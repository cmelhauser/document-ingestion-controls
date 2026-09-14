#!/usr/bin/env python3
"""Create audit-marked LLM amendment proposals without approving client data.

The command deliberately separates eligibility from model judgment.  It queries
the LLM only for a narrow, evidence-linked candidate that has already passed
deterministic validation and matches an independent extractor.  A successful
model result is still an amendment proposal; it never changes source evidence,
clears an exception, or marks a record client approved.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from cli_help import apply_shared_help
from llm_provider import build_client
from llm_response import REASONING_EFFORTS, response_json, response_payload
from run_io import empty_output_directory, empty_output_path, load_manifest, resolve_page, sha256
from runtime_config import env_bool, env_float, env_int, env_value, llm_model, load_project_env

SAFE_FIELDS = {
    "seller_name",
    "buyer_name",
    "ship_to_name",
    "vendor_name",
    "carrier_name",
    "shipper_name",
    "consignee_name",
    "payer_name",
    "payee_name",
    "payment_terms",
    "service_level",
    "equipment_type",
    "description",
    "uom",
    "condition_notes",
}
FINANCIAL_TOKENS = (
    "amount",
    "price",
    "total",
    "subtotal",
    "tax",
    "freight",
    "discount",
    "duty",
    "fee",
    "value",
    "quantity",
    "weight",
    "volume",
    "cost",
    "rate",
    "currency",
    "payment",
)
ADDRESS_TOKENS = ("address", "city", "state", "region", "postal", "zip", "country", "geocode")
REASSEMBLY_TOKENS = ("page_range", "page_group", "document_group", "reassembly")

DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "confidence", "rationale", "evidence_text"],
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["propose_amendment", "retain_original", "needs_client_review"],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string"},
        "evidence_text": {"type": "string"},
    },
}

INSTRUCTIONS = """You are an evidence-bound business-document adjudicator.
Review the one retained source page and the supplied candidate packet. The packet
already passed deterministic validation and has one independent extractor that
agrees with the candidate. Do not invent a value, alter the candidate, make a
financial or address decision, interpret handwriting, or group pages. Return
`propose_amendment` only when the visible page supports the exact candidate value
and the supplied evidence. Otherwise return `retain_original` or
`needs_client_review`. This is an audit-marked amendment proposal, never a
client approval."""


def scalar(value):
    """Return an evidence value while accepting the standard field wrapper."""
    return value.get("value") if isinstance(value, dict) else value


def normalized(value):
    """Compare scalar candidates without treating presentation differences as disagreement."""
    value = scalar(value)
    if value is None:
        return None
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def field_has_token(field, tokens):
    """Identify protected field families from their canonical field name."""
    value = str(field).casefold()
    return any(token in value for token in tokens)


def candidate_id(candidate):
    """Build a stable non-secret identifier when the source omitted one."""
    supplied = candidate.get("candidate_id") or candidate.get("amendment_id")
    if isinstance(supplied, str) and supplied.strip():
        return supplied.strip()
    payload = "\x1f".join(
        str(candidate.get(key, ""))
        for key in ("document_id", "page_id", "field", "original_value", "candidate_value")
    )
    return "llm-candidate-" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def load_candidates(path):
    """Load explicit candidates and retain deterministic amendments as blockers.

    ``adjudicate.py`` emits an ``amendments`` array, not the richer candidate
    contract this LLM lane is allowed to query. Accepting that envelope here
    prevents a schema mismatch from dropping the findings; the marker is
    rejected by ``eligibility_reason`` before any provider call.
    """
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError(
            "Candidate artifact must be an object containing a candidates or amendments list"
        )
    candidates = data.get("candidates")
    amendments = data.get("amendments")
    if candidates is None and amendments is None:
        raise ValueError(
            "Candidate artifact must be an object containing a candidates or amendments list"
        )
    if candidates is not None and (
        not isinstance(candidates, list) or not all(isinstance(item, dict) for item in candidates)
    ):
        raise ValueError("Candidate artifact candidates must be a list of objects")
    if amendments is not None and (
        not isinstance(amendments, list) or not all(isinstance(item, dict) for item in amendments)
    ):
        raise ValueError("Candidate artifact amendments must be a list of objects")
    normalized_amendments = [
        {**item, "_source_input_kind": "adjudicate_amendment", "_source_amendment": item}
        for item in (amendments or [])
    ]
    return (candidates or []) + normalized_amendments


def validate_limits(max_candidates, min_confidence, sampling_rate):
    """Enforce the configured cost, precision, and continuous-QA boundaries."""
    if not isinstance(max_candidates, int) or max_candidates < 1:
        raise ValueError("max candidates must be a positive integer")
    if not isinstance(min_confidence, (int, float)) or not 0.99 <= min_confidence <= 1:
        raise ValueError("minimum confidence must be from 0.99 through 1")
    if not isinstance(sampling_rate, (int, float)) or not 0 < sampling_rate <= 1:
        raise ValueError("sampling rate must be greater than 0 and at most 1")


def eligibility_reason(candidate):
    """Return a fail-closed reason unless every policy control permits model review."""
    if candidate.get("_source_input_kind") == "adjudicate_amendment":
        return "llm_adjudication_amendment_requires_explicit_candidate_evidence"
    required = (
        "document_id",
        "page_id",
        "field",
        "original_value",
        "candidate_value",
        "original_source",
        "evidence_text",
        "deterministic_validation_status",
        "independent_extractor",
        "has_handwriting",
        "is_reassembly",
        "has_disagreement",
    )
    if any(key not in candidate for key in required):
        return "llm_adjudication_candidate_missing_required_evidence"
    if not isinstance(candidate["field"], str) or not candidate["field"].strip():
        return "llm_adjudication_candidate_missing_required_evidence"
    if not isinstance(candidate["evidence_text"], str) or not candidate["evidence_text"].strip():
        return "llm_adjudication_candidate_missing_required_evidence"
    if any(
        not isinstance(candidate[key], bool)
        for key in ("has_handwriting", "is_reassembly", "has_disagreement")
    ):
        return "llm_adjudication_candidate_missing_required_evidence"
    if (
        candidate["has_handwriting"]
        or str(candidate["original_source"]).casefold() == "handwritten"
    ):
        return "llm_adjudication_handwriting_requires_client_review"
    if candidate["is_reassembly"] or field_has_token(candidate["field"], REASSEMBLY_TOKENS):
        return "llm_adjudication_reassembly_requires_client_review"
    if candidate["has_disagreement"]:
        return "llm_adjudication_disagreement_requires_client_review"
    if field_has_token(candidate["field"], FINANCIAL_TOKENS):
        return "llm_adjudication_financial_field_requires_client_review"
    if candidate.get("is_address") is True or field_has_token(candidate["field"], ADDRESS_TOKENS):
        return "llm_adjudication_address_requires_client_review"
    if candidate["field"] not in SAFE_FIELDS:
        return "llm_adjudication_field_not_in_initial_safe_scope"
    if str(candidate["deterministic_validation_status"]).casefold() not in {"clear", "passed"}:
        return "llm_adjudication_deterministic_validation_not_clear"
    independent = candidate["independent_extractor"]
    if not isinstance(independent, dict) or not isinstance(independent.get("engine"), str):
        return "llm_adjudication_independent_extractor_missing"
    if normalized(candidate["candidate_value"]) is None or normalized(
        candidate["candidate_value"]
    ) != normalized(independent.get("value")):
        return "llm_adjudication_independent_extractor_disagrees"
    if normalized(candidate["original_value"]) == normalized(candidate["candidate_value"]):
        return "llm_adjudication_no_amendment_candidate"
    return None


def page_request(client, page_path, candidate, model, reasoning_effort):
    """Submit one immutable page and an evidence packet for a bounded decision."""
    import base64

    encoded = base64.b64encode(Path(page_path).read_bytes()).decode("ascii")
    packet = {
        "candidate_id": candidate_id(candidate),
        "document_id": candidate["document_id"],
        "page_id": candidate["page_id"],
        "field": candidate["field"],
        "original_value": candidate["original_value"],
        "candidate_value": candidate["candidate_value"],
        "original_source": candidate["original_source"],
        "evidence_text": candidate["evidence_text"],
        "independent_extractor": candidate["independent_extractor"],
        "deterministic_validation_status": candidate["deterministic_validation_status"],
    }
    return client.responses.create(
        model=model,
        reasoning={"effort": reasoning_effort},
        instructions=INSTRUCTIONS,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Candidate evidence packet:\n" + json.dumps(packet),
                    },
                    {
                        "type": "input_file",
                        "filename": Path(page_path).name,
                        "file_data": f"data:application/pdf;base64,{encoded}",
                    },
                ],
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "business_document_llm_adjudication",
                "strict": True,
                "schema": DECISION_SCHEMA,
            }
        },
    )


def raw_name(index, candidate):
    """Produce a safe retained-response filename without changing the candidate identifier."""
    label = re.sub(r"[^a-zA-Z0-9_.-]+", "_", candidate_id(candidate)).strip("_") or "candidate"
    return f"{index:06d}_{label}.json"


def write_raw(path, request, response=None, error=None):
    """Retain the request provenance and raw provider response or failure category."""
    payload = {"request": request}
    if response is not None:
        payload["response"] = response
    if error is not None:
        payload["error_type"] = error
    Path(path).write_text(json.dumps(payload, indent=2, default=str) + "\n")


def sampled(candidate, sampling_rate):
    """Select a deterministic continuous-QA sample without a mutable random seed."""
    bucket = int(hashlib.sha256(candidate_id(candidate).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return bucket < sampling_rate


def exception(candidate, reason, **details):
    """Create an explicit review item while preserving all original candidate data."""
    item = {
        "candidate_id": candidate_id(candidate),
        "document_id": candidate.get("document_id"),
        "page_id": candidate.get("page_id"),
        "field": candidate.get("field"),
        "reason": reason,
        "disposition": "client_review_required",
        **details,
    }
    if candidate.get("_source_input_kind") == "adjudicate_amendment":
        item["source_amendment"] = candidate.get("_source_amendment")
    return item


def amendment(
    candidate, decision, raw_path, model, reasoning_effort, min_confidence, sampling_rate
):
    """Create the only success outcome: a review-required LLM amendment proposal."""
    response_hash = sha256(raw_path)
    return {
        "amendment_id": "llm-amendment-" + candidate_id(candidate),
        "candidate_id": candidate_id(candidate),
        "document_id": candidate["document_id"],
        "page_id": candidate["page_id"],
        "field": candidate["field"],
        "original_value": candidate["original_value"],
        "candidate_value": candidate["candidate_value"],
        "original_source": candidate["original_source"],
        "reason": "llm_adjudication_eligible_high_confidence",
        "decision": "llm_generated_amendment_proposal",
        "decision_source": "llm",
        "amendment_source": "llm",
        "client_review_required": True,
        "disposition": "llm_generated_amendment_requires_client_review",
        "sampling_required": sampled(candidate, sampling_rate),
        "audit": {
            "model": model,
            "reasoning_effort": reasoning_effort,
            "prompt_version": "llm-adjudication-v1",
            "raw_response": str(raw_path),
            "raw_response_sha256": response_hash,
            "model_decision": decision["decision"],
            "model_confidence": decision["confidence"],
            "minimum_confidence": min_confidence,
            "model_rationale": decision["rationale"],
            "model_evidence_text": decision["evidence_text"],
            "deterministic_validation_status": candidate["deterministic_validation_status"],
            "independent_extractor": candidate["independent_extractor"],
        },
    }


def run_adjudication(
    candidates_path,
    manifest_path,
    out_path,
    exceptions_path,
    handoff_path,
    raw_dir,
    model,
    client,
    min_confidence=0.99,
    sampling_rate=1.0,
    max_candidates=500,
    max_pdf_bytes=10_000_000,
    timeout_seconds=120.0,
    max_retries=2,
    reasoning_effort="medium",
    credential_env="OPENAI_API_KEY",
):
    """Evaluate eligible candidates and retain an outcome for every input candidate."""
    candidates = load_candidates(candidates_path)
    manifest = load_manifest(manifest_path)
    validate_limits(max_candidates, min_confidence, sampling_rate)
    if len(candidates) > max_candidates:
        raise ValueError(
            f"Candidate count exceeds configured limit: {len(candidates)} > {max_candidates}"
        )
    if max_pdf_bytes < 1:
        raise ValueError("max PDF bytes must be a positive integer")
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise ValueError("timeout seconds must be positive")
    if not isinstance(max_retries, int) or max_retries < 0:
        raise ValueError("max retries must be a non-negative integer")
    if reasoning_effort not in REASONING_EFFORTS:
        raise ValueError("reasoning effort is not supported")
    page_index = {page["page_id"]: page for page in manifest["pages"]}
    targets = [Path(value).resolve() for value in (out_path, exceptions_path, handoff_path)]
    if len(set(targets)) != len(targets):
        raise ValueError("Output, exceptions, and handoff paths must be distinct")
    out_path = empty_output_path(out_path)
    exceptions_path = empty_output_path(exceptions_path)
    handoff_path = empty_output_path(handoff_path)
    raw_dir = empty_output_directory(raw_dir)
    proposals, exceptions, decisions = [], [], []
    for index, candidate in enumerate(candidates, start=1):
        reason = eligibility_reason(candidate)
        if reason:
            item = exception(candidate, reason)
            exceptions.append(item)
            decisions.append({**item, "decision_source": "system_eligibility"})
            continue
        page = page_index.get(candidate["page_id"])
        if page is None:
            item = exception(candidate, "llm_adjudication_page_not_in_manifest")
            exceptions.append(item)
            decisions.append({**item, "decision_source": "system_eligibility"})
            continue
        raw_path = raw_dir / raw_name(index, candidate)
        try:
            page_path = resolve_page(manifest_path, page, max_pdf_bytes)
            request = {
                "model": model,
                "reasoning_effort": reasoning_effort,
                "candidate_id": candidate_id(candidate),
                "page_id": candidate["page_id"],
                "page_sha256": sha256(page_path),
            }
            response = page_request(client, page_path, candidate, model, reasoning_effort)
            write_raw(raw_path, request, response=response_payload(response))
            decision = response_json(response)
            confidence = decision.get("confidence")
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
                raise ValueError("LLM confidence must be numeric")
            if decision.get("decision") == "propose_amendment" and confidence >= min_confidence:
                item = amendment(
                    candidate,
                    decision,
                    raw_path,
                    model,
                    reasoning_effort,
                    min_confidence,
                    sampling_rate,
                )
                proposals.append(item)
                decisions.append(item)
            else:
                reason = (
                    "llm_adjudication_model_confidence_below_threshold"
                    if decision.get("decision") == "propose_amendment"
                    else "llm_adjudication_model_requires_client_review"
                )
                item = exception(
                    candidate,
                    reason,
                    model_decision=decision.get("decision"),
                    model_confidence=confidence,
                    raw_response=str(raw_path),
                )
                exceptions.append(item)
                decisions.append({**item, "decision_source": "llm"})
        except Exception as exc:  # Every failed provider attempt remains review work.
            if not raw_path.exists():
                write_raw(
                    raw_path,
                    {
                        "model": model,
                        "reasoning_effort": reasoning_effort,
                        "candidate_id": candidate_id(candidate),
                        "page_id": candidate["page_id"],
                    },
                    error=type(exc).__name__,
                )
            item = exception(
                candidate,
                "llm_adjudication_provider_or_schema_failure",
                error_type=type(exc).__name__,
                raw_response=str(raw_path),
            )
            exceptions.append(item)
            decisions.append({**item, "decision_source": "llm"})
    summary = {
        "schema_version": "1.0",
        "candidate_count": len(candidates),
        "llm_queries_sent": sum(1 for item in decisions if item.get("decision_source") == "llm"),
        "llm_generated_amendment_proposals": len(proposals),
        "client_review_items": len(exceptions) + len(proposals),
        "decision_mode": "amendment_proposal_only",
        "client_approval_permitted": False,
        "minimum_confidence": min_confidence,
        "sampling_rate": sampling_rate,
        "findings": [
            "No source or extracted value was replaced.",
            "LLM-generated amendment proposals always require final client review.",
            "Financial fields, address changes, handwriting, reassembly, and disagreements remain client review.",
        ],
    }
    out_path.write_text(
        json.dumps(
            {"summary": summary, "amendment_proposals": proposals, "decisions": decisions}, indent=2
        )
        + "\n"
    )
    exceptions_path.write_text(
        json.dumps({"summary": {"count": len(exceptions)}, "exceptions": exceptions}, indent=2)
        + "\n"
    )
    handoff_path.write_text(
        json.dumps(
            {
                "adapter_type": "llm_adjudication",
                "engine": f"openai/{model}",
                "candidate_count": len(candidates),
                "model_configuration": {"model": model, "reasoning_effort": reasoning_effort},
                "credential_reference": credential_env,
                "raw_response_directory": str(raw_dir),
                "raw_response_retention_required": True,
                "policy": {
                    "decision_mode": "amendment_proposal_only",
                    "client_approval_permitted": False,
                    "minimum_confidence": min_confidence,
                    "sampling_rate": sampling_rate,
                    "candidate_count": len(candidates),
                    "maximum_candidates": max_candidates,
                    "max_pdf_bytes": max_pdf_bytes,
                    "timeout_seconds": timeout_seconds,
                    "max_retries": max_retries,
                },
            },
            indent=2,
        )
        + "\n"
    )
    return summary


def main():
    try:
        load_project_env()
        defaults = {
            "enabled": env_bool("LLM_ADJUDICATION_ENABLED", False),
            "model": llm_model(),
            "reasoning_effort": env_value(
                "LLM_ADJUDICATION_REASONING_EFFORT", env_value("OPENAI_REASONING_EFFORT", "medium")
            ),
            "credential_env": env_value(
                "LLM_ADJUDICATION_CREDENTIAL_ENV",
                env_value("OPENAI_CREDENTIAL_ENV", "OPENAI_API_KEY"),
            ),
            "min_confidence": env_float("LLM_ADJUDICATION_MIN_CONFIDENCE", 0.99),
            "sampling_rate": env_float("LLM_ADJUDICATION_SAMPLING_RATE", 1.0),
            "max_candidates": env_int("LLM_ADJUDICATION_MAX_CANDIDATES", 500),
            "max_pdf_bytes": env_int(
                "LLM_ADJUDICATION_MAX_PDF_BYTES", env_int("OPENAI_MAX_PDF_BYTES", 10_000_000)
            ),
            "timeout_seconds": env_float(
                "LLM_ADJUDICATION_TIMEOUT_SECONDS", env_float("OPENAI_TIMEOUT_SECONDS", 120.0)
            ),
            "max_retries": env_int(
                "LLM_ADJUDICATION_MAX_RETRIES", env_int("OPENAI_MAX_RETRIES", 2)
            ),
        }
    except ValueError as exc:
        sys.exit(f"LLM adjudication failed: {exc}")
    parser = argparse.ArgumentParser(
        description="Create audit-marked LLM amendment proposals without client approval."
    )
    parser.add_argument("candidates", help="explicit evidence-linked adjudication candidate JSON")
    parser.add_argument("manifest", help="immutable intake ingestion_manifest.json")
    parser.add_argument("--out", required=True, help="new LLM adjudication artifact")
    parser.add_argument("--exceptions", required=True, help="new client-review exception artifact")
    parser.add_argument("--handoff-out", required=True, help="new non-secret LLM handoff artifact")
    parser.add_argument(
        "--raw-dir", required=True, help="new or empty retained raw-response directory"
    )
    parser.add_argument("--enable", action="store_true", default=defaults["enabled"])
    parser.add_argument("--model", default=defaults["model"])
    parser.add_argument(
        "--reasoning-effort", choices=REASONING_EFFORTS, default=defaults["reasoning_effort"]
    )
    parser.add_argument("--credential-env", default=defaults["credential_env"])
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=defaults["min_confidence"],
        help="Minimum candidate confidence considered. Retained as provenance; it never clears a control.",
    )
    parser.add_argument(
        "--sampling-rate",
        type=float,
        default=defaults["sampling_rate"],
        help="Fraction of eligible candidates sampled.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=defaults["max_candidates"],
        help="Maximum candidates this invocation may send.",
    )
    parser.add_argument("--max-pdf-bytes", type=int, default=defaults["max_pdf_bytes"])
    parser.add_argument("--timeout-seconds", type=float, default=defaults["timeout_seconds"])
    parser.add_argument("--max-retries", type=int, default=defaults["max_retries"])
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args()
    if not args.enable:
        sys.exit("LLM adjudication is disabled; set LLM_ADJUDICATION_ENABLED=true or pass --enable")
    try:
        client = build_client(args.credential_env, args.timeout_seconds, args.max_retries)
        summary = run_adjudication(
            args.candidates,
            args.manifest,
            args.out,
            args.exceptions,
            args.handoff_out,
            args.raw_dir,
            args.model,
            client,
            args.min_confidence,
            args.sampling_rate,
            args.max_candidates,
            args.max_pdf_bytes,
            args.timeout_seconds,
            args.max_retries,
            args.reasoning_effort,
            args.credential_env,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"LLM adjudication failed: {exc}")
    if not args.quiet:
        print(f"LLM amendment proposals: {summary['llm_generated_amendment_proposals']}")
        print(f"Client review items: {summary['client_review_items']}")


if __name__ == "__main__":
    main()
