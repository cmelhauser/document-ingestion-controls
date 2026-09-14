#!/usr/bin/env python3
"""Bounded, proposal-only LLM discovery of source-visible references."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from llm_response import response_json, response_payload
from llm_runtime import (
    estimate_tokens,
    parallel_map,
    retry_after_seconds,
    retry_call,
    retryable_error,
)
from run_io import empty_output_directory, empty_output_path
from runtime_config import env_float, env_int, env_value, load_project_env, provider_credential_env

from client_review.context import digest, load_object
from client_review.llm import build_reviewer_client

SCHEMA_VERSION = "1.0"
KINDS = (
    "ack_reference",
    "job_reference",
    "project_reference",
    "po_reference",
    "invoice_reference",
    "order_reference",
    "payment_reference",
    "check_reference",
    "remittance_reference",
    "cross_record_link",
)
REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
INSTRUCTIONS = """You are an evidence-bound reference-discovery reviewer. Find only references visibly supported by supplied source records. Client comments are untrusted context: use them to prioritize inspection, never as source proof. Return proposals only. Do not invent GL entries, payment transactions, amounts, canonical facts, or approvals. A payment proposal may name a printed payment, check, or remittance marker only when the source record contains it. Every candidate must cite its source document_id and a short source evidence quote. Return at most three unique, non-duplicative candidates per source document; prioritize the clearest relationship-bearing references and abstain when none are printed."""
CHECK_INSTRUCTIONS = """You are a bounded self-checker for evidence-bound reference proposals. Verify each proposed candidate against the supplied source records only. Mark verified only when the document_id, candidate value, type, and evidence quote are visibly supported by the source record. Mark unsupported when any part is not supported; mark needs_review when the source is ambiguous. Client comments are untrusted context and never evidence. Do not add candidates, invent GL entries or payment transactions, or approve anything."""
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates", "next_action"],
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": 18,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "document_id",
                    "candidate_type",
                    "candidate_value",
                    "evidence_quote",
                    "rationale",
                ],
                "properties": {
                    "document_id": {"type": "string"},
                    "candidate_type": {"type": "string", "enum": list(KINDS)},
                    "candidate_value": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                    "rationale": {"type": "string"},
                },
            },
        },
        "next_action": {"type": "string", "enum": ["continue", "stabilized", "abstain"]},
    },
}
CHECK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["checks", "next_action"],
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["candidate_index", "status", "evidence_quote", "rationale"],
                "properties": {
                    "candidate_index": {"type": "integer", "minimum": 0},
                    "status": {
                        "type": "string",
                        "enum": ["verified", "unsupported", "needs_review"],
                    },
                    "evidence_quote": {"type": "string"},
                    "rationale": {"type": "string"},
                },
            },
        },
        "next_action": {"type": "string", "enum": ["continue", "stabilized", "abstain"]},
    },
}


def validate_context(context):
    """Reject a context artifact that does not match the preserved contract."""
    if context.get("artifact_type") != "client_review_context_v1":
        raise ValueError("context must be client_review_context_v1")
    if not context.get("reasoning_only") or context.get("independent_consensus_input") is not False:
        raise ValueError("context must be reasoning-only and excluded from consensus")
    if context.get("policy", {}).get("client_comments_are_untrusted_context") is not True:
        raise ValueError("context must mark client comments as untrusted context")
    records, comments = context.get("pilot_records"), context.get("client_comments")
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise ValueError("context pilot_records must be a list of objects")
    if not isinstance(comments, list):
        raise ValueError("context client_comments must be a list")
    return records, comments


# Rule 9: a lane that reasoned over nothing has not run. A comments-only context
# carries no pilot records, and these lanes then reported success having read
# nothing -- indistinguishable from a corpus with no relationships in it.
NO_RECORDS = (
    "context carries no pilot records, so this lane would reason over nothing; "
    "rebuild the context with --records, or supply --records to this command"
)


def batches(records, size, maximum):
    """Split records into at most `maximum` batches of `size`.

    The cap is a spend bound, and records beyond it are simply not batched --
    the caller retains them as an explicit exception rather than losing them.
    """
    if not isinstance(size, int) or size < 1 or not isinstance(maximum, int) or maximum < 1:
        raise ValueError("batch size and max batches must be positive")
    return [
        records[start : start + size] for start in range(0, min(len(records), size * maximum), size)
    ]


def adaptive_batches(records, comments, size, maximum, max_context_bytes):
    """Yield batches bounded by serialized packet size, not record count alone."""
    if not isinstance(size, int) or size < 1 or not isinstance(maximum, int) or maximum < 1:
        raise ValueError("batch size and max batches must be positive")
    selected, emitted = [], 0
    for record in records:
        candidate = selected + [record]
        try:
            packet(candidate, comments, emitted + 1, max_context_bytes)
            fits = True
        except ValueError as exc:
            if "max-context-bytes" not in str(exc):
                raise
            fits = False
        if selected and (len(candidate) > size or not fits):
            yield selected
            emitted += 1
            if emitted >= maximum:
                return
            selected = [record]
        elif not fits:
            # Let run() record this source item as an explicit exception.
            yield [record]
            emitted += 1
            if emitted >= maximum:
                return
            selected = []
        else:
            selected = candidate
    if selected and emitted < maximum:
        yield selected


def compact_record(record):
    """Keep source-visible values while excluding bulky engine diagnostics."""
    keys = ("value", "source", "candidate_values", "review_reason", "evidence")

    def project(value):
        """Project a retained record down to the fields the packet may carry."""
        if not isinstance(value, dict):
            return value
        result = {}
        for key in keys:
            if key not in value:
                continue
            item = value[key]
            if key == "candidate_values" and isinstance(item, list):
                result[key] = [str(entry)[:120] for entry in item[:3]]
            elif isinstance(item, str):
                result[key] = item[:240]
            else:
                result[key] = str(item)[:240]
        return result

    compact = {
        key: record[key]
        for key in ("document_id", "document_type", "model_document_type", "review_status")
        if key in record
    }
    compact["header"] = {key: project(value) for key, value in record.get("header", {}).items()}
    field_items = list(record.get("fields", {}).items())
    keywords = (
        "ack",
        "job",
        "project",
        "po",
        "purchase",
        "invoice",
        "order",
        "payment",
        "check",
        "remit",
        "reference",
        "transaction",
    )
    prioritized = [
        item for item in field_items if any(word in item[0].casefold() for word in keywords)
    ]
    remaining = [item for item in field_items if item not in prioritized]
    compact["fields"] = {key: project(value) for key, value in (prioritized + remaining)[:40]}
    compact["lines"] = [
        {key: project(value) for key, value in line.items()}
        for line in record.get("lines", [])[:10]
        if isinstance(line, dict)
    ]
    return compact


def packet(records, comments, batch_number, max_bytes):
    """Build one bounded discovery packet from the supplied documents."""
    value = {
        "lane": "client_review_reference_discovery",
        "batch": batch_number,
        "instructions": INSTRUCTIONS,
        "client_context": comments,
        "source_records": [compact_record(record) for record in records],
    }
    if len(json.dumps(value, sort_keys=True).encode()) > max_bytes:
        raise ValueError("inference packet exceeds max-context-bytes")
    return value


def verification_packet(records, comments, candidates, batch_number, round_number, max_bytes):
    """Build the packet the verification pass receives."""
    value = {
        "lane": "client_review_reference_discovery_self_check",
        "batch": batch_number,
        "round": round_number,
        "instructions": CHECK_INSTRUCTIONS,
        "client_context": comments,
        "source_records": [compact_record(record) for record in records],
        "proposals": candidates,
    }
    if len(json.dumps(value, sort_keys=True).encode()) > max_bytes:
        raise ValueError("self-check packet exceeds max-context-bytes")
    return value


def reduce_candidates(decision, allowed_ids):
    """Reduce a provider response to source-citing candidates, dropping the rest."""
    if not isinstance(decision, dict) or not isinstance(decision.get("candidates"), list):
        raise ValueError("LLM response must contain candidates")
    candidates, rejected = [], []
    for candidate in decision["candidates"]:
        valid = (
            isinstance(candidate, dict)
            and candidate.get("document_id") in allowed_ids
            and candidate.get("candidate_type") in KINDS
            and bool(str(candidate.get("candidate_value", "")).strip())
            and bool(str(candidate.get("evidence_quote", "")).strip())
        )
        if valid:
            candidates.append(
                {
                    **candidate,
                    "proposal_only": True,
                    "source_evidence_required": True,
                    "production_approval_permitted": False,
                }
            )
        else:
            rejected.append(
                {"candidate": candidate, "reason": "candidate_failed_evidence_contract"}
            )
    return candidates, rejected


def reduce_self_check(decision, candidates):
    """Reduce a same-model re-reading, which refines a proposal but is not independence."""
    if not isinstance(decision, dict) or not isinstance(decision.get("checks"), list):
        raise ValueError("self-check response must contain checks")
    verified = []
    rejected = []
    for check in decision["checks"]:
        index = check.get("candidate_index") if isinstance(check, dict) else None
        status = check.get("status") if isinstance(check, dict) else None
        if not isinstance(index, int) or not 0 <= index < len(candidates):
            rejected.append({"check": check, "reason": "self_check_index_invalid"})
        elif status == "verified" and str(check.get("evidence_quote", "")).strip():
            verified.append(candidates[index])
        else:
            rejected.append({"check": check, "reason": "self_check_not_verified"})
    return verified, rejected


def run(
    context_path,
    out_path,
    exceptions_path,
    raw_dir,
    client,
    model,
    provider,
    *,
    batch_size=10,
    max_batches=8,
    max_context_bytes=120000,
    reasoning_effort="medium",
    max_retries=2,
    backoff_seconds=1.0,
    max_backoff_seconds=30.0,
    self_check_rounds=0,
    workers=1,
):
    """Run bounded reference and payment-marker discovery over client context.

    Every candidate must cite a source document and remains client-review work.
    The context is reasoning-only and is excluded from independent consensus."""
    if not isinstance(self_check_rounds, int) or self_check_rounds < 0:
        raise ValueError("self-check rounds must be non-negative")
    context_path = Path(context_path)
    records, comments = validate_context(load_object(context_path, "client review context"))
    if not records:
        raise ValueError(NO_RECORDS)
    out_path, exceptions_path = empty_output_path(out_path), empty_output_path(exceptions_path)
    raw_dir = empty_output_directory(raw_dir)
    candidates, exceptions, results = [], [], []

    def discover_batch(entry):
        """Discover one batch and return its outcome without touching shared lists."""
        batch_number, selected = entry
        retry_log = []
        try:
            request = packet(selected, comments, batch_number, max_context_bytes)
            response = retry_call(
                lambda request=request: client.responses.create(
                    model=model,
                    reasoning={"effort": reasoning_effort},
                    instructions=INSTRUCTIONS,
                    input=[
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": json.dumps(request)}],
                        }
                    ],
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": "reference_discovery",
                            "strict": True,
                            "schema": SCHEMA,
                        }
                    },
                ),
                max_retries,
                backoff_seconds,
                max_backoff_seconds=max_backoff_seconds,
                jitter=True,
                retryable=retryable_error,
                on_retry=lambda attempt, error, delay, retry_log=retry_log: retry_log.append(
                    {
                        "attempt": attempt,
                        "error_type": type(error).__name__,
                        "status_code": getattr(error, "status_code", None),
                        "retry_after_seconds": retry_after_seconds(error),
                        "delay_seconds": delay,
                    }
                ),
                provider=provider,
                request_tokens=estimate_tokens(request),
            )
            found, rejected = reduce_candidates(
                response_json(response), {item.get("document_id") for item in selected}
            )
            raw_path = raw_dir / f"batch-{batch_number:03d}.json"
            raw_path.write_text(
                json.dumps(
                    {"request": request, "response": response_payload(response)},
                    indent=2,
                    default=str,
                )
                + "\n"
            )
            self_check_rejections = []
            for round_number in range(1, self_check_rounds + 1):
                check_retry_log = []
                check_request = verification_packet(
                    selected, comments, found, batch_number, round_number, max_context_bytes
                )
                check_response = retry_call(
                    lambda check_request=check_request: client.responses.create(
                        model=model,
                        reasoning={"effort": reasoning_effort},
                        instructions=CHECK_INSTRUCTIONS,
                        input=[
                            {
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": json.dumps(check_request)}
                                ],
                            }
                        ],
                        text={
                            "format": {
                                "type": "json_schema",
                                "name": "reference_discovery_self_check",
                                "strict": True,
                                "schema": CHECK_SCHEMA,
                            }
                        },
                    ),
                    max_retries,
                    backoff_seconds,
                    max_backoff_seconds=max_backoff_seconds,
                    jitter=True,
                    retryable=retryable_error,
                    on_retry=lambda attempt, error, delay, check_retry_log=check_retry_log: (
                        check_retry_log.append(
                            {
                                "attempt": attempt,
                                "error_type": type(error).__name__,
                                "status_code": getattr(error, "status_code", None),
                                "retry_after_seconds": retry_after_seconds(error),
                                "delay_seconds": delay,
                            }
                        )
                    ),
                    provider=provider,
                    request_tokens=estimate_tokens(check_request),
                )
                found, rejected_check = reduce_self_check(response_json(check_response), found)
                self_check_rejections.extend(rejected_check)
                check_path = (
                    raw_dir / f"batch-{batch_number:03d}-self-check-{round_number:03d}.json"
                )
                check_path.write_text(
                    json.dumps(
                        {"request": check_request, "response": response_payload(check_response)},
                        indent=2,
                        default=str,
                    )
                    + "\n"
                )
            return {
                "candidates": found,
                "exception": None,
                "result": {
                    "batch": batch_number,
                    "document_count": len(selected),
                    "candidate_count": len(found),
                    "rejected_count": len(rejected),
                    "rejected": rejected + self_check_rejections,
                    "raw_response": str(raw_path),
                    "retry_log": retry_log,
                    "self_check_rounds": self_check_rounds,
                },
            }
        except Exception as exc:
            return {
                "candidates": [],
                "result": None,
                "exception": {
                    "batch": batch_number,
                    "document_count": len(selected),
                    "reason": "reference_discovery_provider_or_schema_failure",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                    "retry_log": retry_log,
                    "disposition": "client_review_required",
                },
            }

    # `parallel_map` preserves input order, so candidates and per-batch results
    # land in batch order at any worker count.
    planned = list(
        enumerate(
            adaptive_batches(records, comments, batch_size, max_batches, max_context_bytes), 1
        )
    )
    for outcome in parallel_map(planned, discover_batch, workers):
        candidates.extend(outcome["candidates"])
        if outcome["result"] is not None:
            results.append(outcome["result"])
        if outcome["exception"] is not None:
            exceptions.append(outcome["exception"])
    output = {
        "artifact_type": "client_review_reference_discovery_v1",
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "proposal_only": True,
        "production_approval_permitted": False,
        "gate_status": "blocked_pending_client_review",
        "provider": provider,
        "model": model,
        "self_check_rounds": self_check_rounds,
        "context_sha256": digest(context_path),
        "summary": {
            "batches": len(results) + len(exceptions),
            "successful_batches": len(results),
            "failed_batches": len(exceptions),
            "candidates": len(candidates),
            "rejected_candidates": sum(item["rejected_count"] for item in results),
        },
        "candidates": candidates,
        "batches": results,
    }
    out_path.write_text(json.dumps(output, indent=2) + "\n")
    exceptions_path.write_text(
        json.dumps(
            {
                "artifact_type": "client_review_reference_discovery_exceptions_v1",
                "proposal_only": True,
                "exceptions": exceptions,
            },
            indent=2,
        )
        + "\n"
    )
    return output


def main():
    parser = argparse.ArgumentParser(
        description="Run bounded proposal-only LLM reference discovery with client context."
    )
    parser.add_argument("context")
    parser.add_argument("--out", required=True)
    parser.add_argument("--exceptions", required=True)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="batches discovered concurrently; output order is unchanged by this setting",
    )
    parser.add_argument("--provider", choices=("openai", "google", "openrouter", "anthropic"))
    parser.add_argument("--model")
    parser.add_argument("--batch-size", type=int, help="Documents sent per request.")
    parser.add_argument("--max-batches", type=int, help="Maximum batches this invocation may send.")
    parser.add_argument("--max-context-bytes", type=int)
    parser.add_argument("--reasoning-effort", choices=REASONING_EFFORTS)
    parser.add_argument(
        "--self-check-rounds",
        type=int,
        help="Same-model re-reading rounds. This improves a proposal and is never independent consensus.",
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    load_project_env()
    enabled = (
        args.enable or env_value("CLIENT_REVIEW_CONTEXT_LLM_ENABLED", "false").casefold() == "true"
    )
    if not enabled:
        empty_output_path(args.out).write_text(
            json.dumps(
                {
                    "artifact_type": "client_review_reference_discovery_v1",
                    "summary": {"enabled": False},
                    "candidates": [],
                },
                indent=2,
            )
            + "\n"
        )
        empty_output_path(args.exceptions).write_text(
            json.dumps({"exceptions": []}, indent=2) + "\n"
        )
        empty_output_directory(args.raw_dir)
        print("Client-review reference discovery disabled")
        return
    provider = args.provider or env_value("CLIENT_REVIEW_CONTEXT_LLM_PROVIDER", "google")
    model = args.model or env_value("CLIENT_REVIEW_CONTEXT_LLM_MODEL", "gemini-2.5-pro")
    client = build_reviewer_client(
        provider,
        env_value("CLIENT_REVIEW_CONTEXT_LLM_CREDENTIAL_ENV", provider_credential_env(provider)),
        env_float("CLIENT_REVIEW_CONTEXT_LLM_TIMEOUT_SECONDS", 45.0),
        env_int("CLIENT_REVIEW_CONTEXT_LLM_MAX_RETRIES", 1),
    )
    reasoning_effort = args.reasoning_effort or env_value(
        "CLIENT_REVIEW_CONTEXT_LLM_REASONING_EFFORT", "medium"
    )
    if reasoning_effort not in REASONING_EFFORTS:
        raise SystemExit(f"unsupported reasoning effort: {reasoning_effort}")
    try:
        result = run(
            args.context,
            args.out,
            args.exceptions,
            args.raw_dir,
            client,
            model,
            provider,
            batch_size=args.batch_size or env_int("CLIENT_REVIEW_CONTEXT_LLM_BATCH_SIZE", 6),
            max_batches=args.max_batches or env_int("CLIENT_REVIEW_CONTEXT_LLM_MAX_BATCHES", 12),
            max_context_bytes=args.max_context_bytes
            or env_int("CLIENT_REVIEW_CONTEXT_LLM_MAX_CONTEXT_BYTES", 40000),
            reasoning_effort=reasoning_effort,
            max_retries=env_int("CLIENT_REVIEW_CONTEXT_LLM_MAX_RETRIES", 2),
            backoff_seconds=env_float("CLIENT_REVIEW_CONTEXT_LLM_RETRY_BACKOFF_SECONDS", 1.0),
            max_backoff_seconds=env_float("CLIENT_REVIEW_CONTEXT_LLM_MAX_BACKOFF_SECONDS", 30.0),
            self_check_rounds=args.self_check_rounds
            if args.self_check_rounds is not None
            else env_int("CLIENT_REVIEW_CONTEXT_LLM_SELF_CHECK_ROUNDS", 1),
            workers=args.workers or env_int("CLIENT_REVIEW_CONTEXT_LLM_MAX_WORKERS", 8),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        # The repository convention: a refusal names itself, rather than
        # presenting an operator with a traceback to interpret.
        raise SystemExit(f"Client-review reference discovery failed: {exc}") from exc
    print(
        f"Reference discovery batches: {result['summary']['batches']}; candidates: {result['summary']['candidates']}"
    )


if __name__ == "__main__":
    main()
