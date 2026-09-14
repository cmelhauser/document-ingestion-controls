#!/usr/bin/env python3
"""Create bounded, source-backed primary/buddy proposals for control exceptions.

The lane is deliberately generic: it receives records and exception artifacts,
never modifies either, and emits append-only proposals for reassembly,
validation, or attribution follow-up.  Its outputs cannot clear a gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from cli_help import apply_shared_help
from llm_response import response_json, response_payload
from llm_runtime import (
    adaptive_byte_batches,
    estimate_tokens,
    parallel_map,
    reset_provider_quota_circuits,
    retry_call,
    retryable_error,
)
from run_io import empty_output_directory, empty_output_path
from runtime_config import (
    env_bool,
    env_float,
    env_int,
    env_value,
    load_project_env,
    provider_credential_env,
)

from client_review.inference import compact_record, validate_context
from client_review.iterative import validate_reviewer_roles
from client_review.llm import build_reviewer_client

KINDS = ("reassembly_grouping", "validation_amendment", "attribution_link")
PRIMARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["proposals"],
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "document_id",
                    "kind",
                    "field",
                    "proposed_value",
                    "related_document_ids",
                    "evidence_quote",
                    "rationale",
                ],
                "properties": {
                    "document_id": {"type": "string"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "field": {"type": "string"},
                    "proposed_value": {"type": ["string", "null"]},
                    "related_document_ids": {"type": "array", "items": {"type": "string"}},
                    "evidence_quote": {"type": "string"},
                    "rationale": {"type": "string"},
                },
            },
        }
    },
}
BUDDY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decisions"],
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["proposal_id", "status", "rationale"],
                "properties": {
                    "proposal_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["confirmed", "close_needs_review", "conflict", "unsupported"],
                    },
                    "rationale": {"type": "string"},
                },
            },
        }
    },
}
PRIMARY_INSTRUCTIONS = """Review only supplied source records and named control exceptions. The supplied client-review context is reasoning-only and can identify a question or priority, but is never source evidence. Propose reassembly grouping, validation amendment, or attribution link only when visible source evidence supports it. Never invent values, approve a fact, clear a control, or use client comments as evidence. Return no proposal when unsupported."""
BUDDY_INSTRUCTIONS = """Independently verify each primary proposal against the same source records and exceptions. Confirm only source-supported proposals. A confirmed result remains an append-only proposal and never clears arithmetic, validation, attribution, completeness, or final review."""


def load(path):
    """Read a JSON object input, refusing a list or scalar."""
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError("JSON input must be an object")
    return value


def records_from(path):
    """Read retained records from a record artifact or a bare list of records."""
    value = load(path)
    records = value.get("documents") or value.get("records") or value.get("results")
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise ValueError("records input must contain a documents, records, or results list")
    indexed = {}
    for item in records:
        document_id = str(item.get("document_id") or "").strip()
        if not document_id:
            continue
        if document_id in indexed:
            raise ValueError(f"duplicate document_id: {document_id}")
        indexed[document_id] = {**item, "document_id": document_id}
    return indexed


def reasoning_context(path):
    """Bind preserved client context to the packet as reasoning-only material."""
    if not path:
        return None
    value = load(path)
    if value.get("artifact_type") != "client_review_context_v1":
        raise ValueError("client context must be client_review_context_v1")
    if not value.get("reasoning_only") or value.get("independent_consensus_input"):
        raise ValueError("client context must be reasoning-only and excluded from consensus")
    _, comments = validate_context(value)
    return {
        "artifact_type": value["artifact_type"],
        "reasoning_only": True,
        "independent_consensus_input": False,
        "policy": value.get("policy", {}),
        "client_comments": comments,
        "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
    }


def exception_items(paths):
    """Read every finding out of a supplied exception or register artifact."""
    values = []
    for path in paths:
        data = load(path)
        items = next(
            (
                data[key]
                for key in ("exceptions", "reassembly_exceptions", "items", "register", "results")
                if key in data
            ),
            None,
        )
        if items is None:
            raise ValueError("exception input must contain a recognized exception list")
        if not isinstance(items, list):
            raise ValueError("exception input must contain a list")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("exception input list items must be objects")
            retry_findings = item.get("retry_findings")
            if retry_findings is not None:
                if not isinstance(retry_findings, list) or any(
                    not isinstance(finding, dict) for finding in retry_findings
                ):
                    raise ValueError("retry_findings must be a list of objects")
                values.extend(retry_findings)
                continue
            source_ids = item.get("source_document_ids")
            if not item.get("document_id") and isinstance(source_ids, list):
                values.extend(
                    {**item, "document_id": document_id, "source_document_ids": [document_id]}
                    for document_id in source_ids
                    if str(document_id or "")
                )
            else:
                values.append(item)
    return values


def kind_for(item):
    """Return the finding kind a supplied exception belongs to."""
    text = " ".join(
        str(item.get(key, ""))
        for key in ("reason", "reason_code", "field", "status", "arithmetic_status", "disposition")
    ).casefold()
    if "reassembly" in text or "unassigned" in text or "group" in text:
        return "reassembly_grouping"
    if "attribut" in text or "ack" in text or "job" in text or "reference" in text:
        return "attribution_link"
    return "validation_amendment"


def partition_findings(records, findings):
    """Split supplied findings into resolvable items and explicitly retained ones."""
    grouped = {}
    unmatched = []
    for index, finding in enumerate(findings):
        ids = finding.get("source_document_ids") or [finding.get("document_id")]
        ids = list(dict.fromkeys(str(value or "").strip() for value in ids))
        ids = [value for value in ids if value]
        missing = [value for value in ids if value not in records]
        if not ids or missing:
            unmatched.append(
                {
                    "exception_id": f"exception-{index:06d}",
                    "source_document_ids": ids,
                    "missing_source_document_ids": missing,
                    "finding": finding,
                }
            )
            continue
        source = [records[value] for value in ids]
        key = tuple(sorted(record["document_id"] for record in source))
        item = grouped.setdefault(
            key,
            {
                "exception_id": f"exception-{index:06d}",
                "exception_ids": [],
                "kinds": [],
                "findings": [],
                "source_records": source,
            },
        )
        item["exception_ids"].append(f"exception-{index:06d}")
        item["kinds"].append(kind_for(finding))
        item["findings"].append(finding)
    return list(grouped.values()), unmatched


def selected(records, findings):
    """Return only the findings this lane will work, discarding the partition's remainder."""
    return partition_findings(records, findings)[0]


def retry_metadata(items):
    """Retain the exact named findings needed for a no-clobber retry."""
    return {
        "source_exception_ids": sorted(
            {exception_id for item in items for exception_id in item["exception_ids"]}
        ),
        "retry_findings": [finding for item in items for finding in item["findings"]],
        "retry_kinds": sorted({kind for item in items for kind in item["kinds"]}),
    }


def proposal_id(value):
    """Return a content-addressed proposal ID.

    Derived from the proposal itself so the same proposal keeps the same ID
    across a retry, and a changed proposal cannot reuse one.
    """
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def visible_source_text(records):
    """Return normalized visible scalar content for deterministic quote checks."""
    scalars = []
    provenance_only = {
        "document_id",
        "document_type",
        "model_document_type",
        "review_reason",
        "review_status",
        "source",
    }

    def collect(value):
        """Collect findings from every supplied exception artifact."""
        if isinstance(value, dict):
            for key, item in value.items():
                if key in provenance_only:
                    continue
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif value is not None:
            scalars.append(str(value))

    collect(records)
    return " ".join(" ".join(value.casefold().split()) for value in scalars)


def proposal_rejection_reason(proposal, allowed, source_text):
    """Return an explicit reason when a primary proposal fails the packet contract."""
    if not isinstance(proposal, dict):
        return "proposal_not_an_object"
    if proposal.get("document_id") not in allowed:
        return "proposal_document_outside_packet"
    if proposal.get("kind") not in KINDS:
        return "proposal_kind_unsupported"
    if not str(proposal.get("field", "")).strip():
        return "proposal_field_missing"
    related = proposal.get("related_document_ids")
    if not isinstance(related, list) or any(value not in allowed for value in related):
        return "proposal_related_document_outside_packet"
    quote = " ".join(str(proposal.get("evidence_quote", "")).casefold().split())
    if not quote:
        return "proposal_evidence_quote_missing"
    if len(quote) < 2:
        return "proposal_evidence_quote_not_in_packet"
    if isinstance(source_text, dict):
        evidence_documents = {proposal["document_id"], *related}
        if not any(quote in source_text.get(document_id, "") for document_id in evidence_documents):
            return "proposal_evidence_quote_not_in_related_source"
    elif quote not in source_text:
        return "proposal_evidence_quote_not_in_packet"
    return None


def packet(batch, client_context=None):
    """Build the exception, source, and context packet both reviewers receive."""
    return {
        "lane": "client_review_exception_resolution",
        "instructions": PRIMARY_INSTRUCTIONS,
        "exceptions": [
            {
                "exception_id": item["exception_id"],
                "exception_ids": item["exception_ids"],
                "kinds": sorted(set(item["kinds"])),
                "findings": item["findings"],
            }
            for item in batch
        ],
        "source_records": [
            compact_record(record) for item in batch for record in item["source_records"]
        ],
        "client_review_context": client_context,
    }


def adaptive_batches(work, batch_size, max_batches, max_context_bytes, client_context):
    """Pack whole work items greedily; retain an item that cannot fit alone."""
    if batch_size < 1 or max_batches < 0 or max_context_bytes < 1:
        raise ValueError("batch/context limits must be positive (max batches may be zero)")
    return adaptive_byte_batches(
        work,
        lambda candidate: (
            max_context_bytes - len(json.dumps(packet(candidate, client_context)).encode())
        ),
        batch_size,
        max_batches,
    )


def request(
    client, model, effort, instructions, schema, body, provider, retries, backoff, max_backoff
):
    """Issue one bounded provider request and retain its raw response."""
    return retry_call(
        lambda: client.responses.create(
            model=model,
            reasoning={"effort": effort},
            instructions=instructions,
            input=[{"role": "user", "content": [{"type": "input_text", "text": json.dumps(body)}]}],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "exception_resolution",
                    "strict": True,
                    "schema": schema,
                }
            },
        ),
        retries,
        backoff,
        max_backoff_seconds=max_backoff,
        jitter=True,
        retryable=retryable_error,
        provider=provider,
        request_tokens=estimate_tokens(body),
    )


def run(
    records_path,
    exception_paths,
    out_path,
    exceptions_path,
    raw_dir,
    primary,
    buddy,
    primary_model,
    buddy_model,
    primary_provider,
    buddy_provider,
    client_context_path=None,
    *,
    batch_size,
    max_batches,
    max_context_bytes,
    effort,
    retries,
    backoff,
    max_backoff,
    workers=1,
):
    """Produce source-cited primary/buddy proposals for every supplied exception.

    Counts every input finding and routes each to a proposal or to an explicit
    retained exception. Missing-source, rejected, capped, oversized, and
    provider-failed work stays in the companion artifacts; the lane never clears
    the control that raised the finding."""
    reset_provider_quota_circuits()
    records = records_from(records_path)
    client_context = reasoning_context(client_context_path)
    findings = exception_items(exception_paths)
    work, unmatched = partition_findings(records, findings)
    if batch_size < 1 or max_batches < 0 or max_context_bytes < 1:
        raise ValueError("batch/context limits must be positive (max batches may be zero)")
    out_path, exceptions_path, raw_dir = (
        empty_output_path(out_path),
        empty_output_path(exceptions_path),
        empty_output_directory(raw_dir),
    )
    accepted = []
    rejected = []
    failures = [
        {
            "exception_id": item["exception_id"],
            "reason": "exception_resolution_source_record_missing",
            "source_document_ids": item["source_document_ids"],
            "missing_source_document_ids": item["missing_source_document_ids"],
            "finding": item["finding"],
            "disposition": "client_review_required",
        }
        for item in unmatched
    ]
    batches = []
    planned, oversize, deferred = adaptive_batches(
        work, batch_size, max_batches, max_context_bytes, client_context
    )

    def resolve_batch(entry):
        """Resolve one batch and return its outcome without touching shared lists."""
        number, batch = entry
        body = packet(batch, client_context)
        batch_rejected = []
        try:
            primary_response = request(
                primary,
                primary_model,
                effort,
                PRIMARY_INSTRUCTIONS,
                PRIMARY_SCHEMA,
                body,
                primary_provider,
                retries,
                backoff,
                max_backoff,
            )
            raw = raw_dir / f"batch-{number:03d}-primary.json"
            raw.write_text(
                json.dumps(
                    {"request": body, "response": response_payload(primary_response)},
                    indent=2,
                    default=str,
                )
                + "\n"
            )
            proposals = response_json(primary_response).get("proposals", [])
            valid = []
            allowed = {r.get("document_id") for x in batch for r in x["source_records"]}
            exception_ids_by_document = {
                document_id: sorted(
                    {
                        exception_id
                        for item in batch
                        if document_id
                        in {record.get("document_id") for record in item["source_records"]}
                        for exception_id in item["exception_ids"]
                    }
                )
                for document_id in allowed
            }
            source_text = {
                document_id: visible_source_text(
                    [
                        record
                        for record in body["source_records"]
                        if record.get("document_id") == document_id
                    ]
                )
                for document_id in allowed
            }
            for proposal in proposals:
                rejection_reason = proposal_rejection_reason(proposal, allowed, source_text)
                if rejection_reason:
                    batch_rejected.append(
                        {
                            "batch": number,
                            "reason": rejection_reason,
                            "proposal": proposal,
                        }
                    )
                    continue
                proposal = {
                    **proposal,
                    "proposal_id": proposal_id(proposal),
                    "source_exception_ids": exception_ids_by_document[proposal["document_id"]],
                    "proposal_only": True,
                    "production_approval_permitted": False,
                }
                valid.append(proposal)
            buddy_body = {
                "lane": "client_review_exception_resolution_buddy",
                "instructions": BUDDY_INSTRUCTIONS,
                "exceptions": body["exceptions"],
                "source_records": body["source_records"],
                "client_review_context": body["client_review_context"],
                "primary_proposals": valid,
            }
            buddy_response = request(
                buddy,
                buddy_model,
                effort,
                BUDDY_INSTRUCTIONS,
                BUDDY_SCHEMA,
                buddy_body,
                buddy_provider,
                retries,
                backoff,
                max_backoff,
            )
            buddy_raw = raw_dir / f"batch-{number:03d}-buddy.json"
            buddy_raw.write_text(
                json.dumps(
                    {"request": buddy_body, "response": response_payload(buddy_response)},
                    indent=2,
                    default=str,
                )
                + "\n"
            )
            decisions = {
                d.get("proposal_id"): d
                for d in response_json(buddy_response).get("decisions", [])
                if isinstance(d, dict)
            }
            merged = [
                {
                    **p,
                    "buddy_status": decisions.get(p["proposal_id"], {}).get(
                        "status", "unsupported"
                    ),
                    "buddy_rationale": decisions.get(p["proposal_id"], {}).get("rationale", ""),
                }
                for p in valid
            ]
            return {
                "rejected": batch_rejected,
                "accepted": merged,
                "batch": {
                    "batch": number,
                    "work_items": len(batch),
                    "proposals": len(merged),
                    "raw_primary": str(raw),
                    "raw_buddy": str(buddy_raw),
                },
                "failure": None,
            }
        except Exception as exc:
            return {
                "rejected": batch_rejected,
                "accepted": [],
                "batch": None,
                "failure": {
                    "batch": number,
                    "reason": "exception_resolution_provider_or_schema_failure",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                    "source_document_ids": [
                        r.get("document_id") for x in batch for r in x["source_records"]
                    ],
                    **retry_metadata(batch),
                    "disposition": "client_review_required",
                },
            }

    # `parallel_map` preserves input order, so proposals, rejections, and
    # failures land in batch order at any worker count.
    for outcome in parallel_map(list(enumerate(planned, 1)), resolve_batch, workers):
        rejected.extend(outcome["rejected"])
        accepted.extend(outcome["accepted"])
        if outcome["batch"] is not None:
            batches.append(outcome["batch"])
        if outcome["failure"] is not None:
            failures.append(outcome["failure"])
    for item in oversize:
        failures.append(
            {
                "reason": "exception_resolution_record_too_large",
                "source_document_ids": [
                    record.get("document_id") for record in item["source_records"]
                ],
                **retry_metadata([item]),
                "work_items": 1,
                "disposition": "client_review_required",
            }
        )
    if deferred:
        failures.append(
            {
                "reason": "exception_resolution_batch_cap_reached",
                "source_document_ids": [
                    record.get("document_id")
                    for item in deferred
                    for record in item["source_records"]
                ],
                **retry_metadata(deferred),
                "work_items": len(deferred),
                "disposition": "client_review_required",
            }
        )
    output = {
        "artifact_type": "client_review_exception_resolution_v1",
        "proposal_only": True,
        "production_approval_permitted": False,
        "gate_status": "blocked_pending_deterministic_controls",
        "provider": primary_provider,
        "model": primary_model,
        "buddy_provider": buddy_provider,
        "buddy_model": buddy_model,
        "records_sha256": hashlib.sha256(Path(records_path).read_bytes()).hexdigest(),
        "exception_sha256": [
            hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in exception_paths
        ],
        "client_review_context_sha256": client_context and client_context["sha256"],
        "summary": {
            "input_findings": len(findings),
            "matched_work_items": len(work),
            "unmatched_findings": len(unmatched),
            "work_items": len(work) + len(unmatched),
            "batches": len(planned),
            "successful_batches": len(batches),
            "failed_batches": sum("batch" in item for item in failures),
            "exceptions": len(failures),
            "proposals": len(accepted),
            "rejected_proposals": len(rejected),
        },
        "proposals": accepted,
        "rejected_proposals": rejected,
        "batches": batches,
    }
    out_path.write_text(json.dumps(output, indent=2) + "\n")
    exceptions_path.write_text(
        json.dumps(
            {
                "artifact_type": "client_review_exception_resolution_exceptions_v1",
                "proposal_only": True,
                "exceptions": failures,
            },
            indent=2,
        )
        + "\n"
    )
    return output


def main():
    parser = argparse.ArgumentParser(
        description="Run bounded primary/buddy proposals for reassembly, validation, and attribution exceptions."
    )
    parser.add_argument(
        "records", help="Retained extracted-record artifact the supplied exceptions refer to."
    )
    parser.add_argument("--exceptions", action="append", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--exceptions-out", required=True)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--client-context", help="reasoning-only client_review_context_v1 artifact")
    parser.add_argument("--enable", action="store_true")
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="batches resolved concurrently; output order is unchanged by this setting",
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    load_project_env()
    if not (args.enable or env_bool("CLIENT_REVIEW_EXCEPTION_RESOLUTION_ENABLED", False)):
        out_path, exceptions_path, _ = (
            empty_output_path(args.out),
            empty_output_path(args.exceptions_out),
            empty_output_directory(args.raw_dir),
        )
        out_path.write_text(
            json.dumps(
                {
                    "artifact_type": "client_review_exception_resolution_v1",
                    "summary": {"enabled": False},
                    "proposals": [],
                },
                indent=2,
            )
            + "\n"
        )
        exceptions_path.write_text(json.dumps({"exceptions": []}, indent=2) + "\n")
        return
    pp = env_value("CLIENT_REVIEW_EXCEPTION_RESOLUTION_PRIMARY_PROVIDER", "")
    bp = env_value("CLIENT_REVIEW_EXCEPTION_RESOLUTION_BUDDY_PROVIDER", "")
    pm = env_value("CLIENT_REVIEW_EXCEPTION_RESOLUTION_PRIMARY_MODEL", "")
    bm = env_value("CLIENT_REVIEW_EXCEPTION_RESOLUTION_BUDDY_MODEL", "")
    validate_reviewer_roles(pp, bp, pm, bm)
    retries = env_int("CLIENT_REVIEW_EXCEPTION_RESOLUTION_MAX_RETRIES", 2)
    timeout = env_float("CLIENT_REVIEW_EXCEPTION_RESOLUTION_TIMEOUT_SECONDS", 180.0)
    result = run(
        args.records,
        args.exceptions,
        args.out,
        args.exceptions_out,
        args.raw_dir,
        build_reviewer_client(
            pp,
            env_value(
                "CLIENT_REVIEW_EXCEPTION_RESOLUTION_PRIMARY_CREDENTIAL_ENV",
                provider_credential_env(pp),
            ),
            timeout,
            retries,
        ),
        build_reviewer_client(
            bp,
            env_value(
                "CLIENT_REVIEW_EXCEPTION_RESOLUTION_BUDDY_CREDENTIAL_ENV",
                provider_credential_env(bp),
            ),
            timeout,
            retries,
        ),
        pm,
        bm,
        pp,
        bp,
        args.client_context,
        batch_size=env_int("CLIENT_REVIEW_EXCEPTION_RESOLUTION_BATCH_SIZE", 6),
        max_batches=env_int("CLIENT_REVIEW_EXCEPTION_RESOLUTION_MAX_BATCHES", 0),
        max_context_bytes=env_int("CLIENT_REVIEW_EXCEPTION_RESOLUTION_MAX_CONTEXT_BYTES", 120000),
        effort=env_value("CLIENT_REVIEW_EXCEPTION_RESOLUTION_REASONING_EFFORT", "medium"),
        retries=retries,
        backoff=env_float("CLIENT_REVIEW_EXCEPTION_RESOLUTION_RETRY_BACKOFF_SECONDS", 5.0),
        max_backoff=env_float("CLIENT_REVIEW_EXCEPTION_RESOLUTION_MAX_BACKOFF_SECONDS", 180.0),
        workers=args.workers or env_int("CLIENT_REVIEW_EXCEPTION_RESOLUTION_MAX_WORKERS", 8),
    )
    print(json.dumps(result["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
