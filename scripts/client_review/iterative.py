#!/usr/bin/env python3
"""Configurable independent-primary/buddy iterative reference proposals.

Every output is an immutable, provenance-bound proposal.  Iterations may refine
the proposal set, but never authorize canonical facts or clear GL/payment gates.
"""

import argparse
import json
import re
from collections import defaultdict
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
from runtime_config import (
    LLM_PROVIDERS,
    UNRESOLVED_MODEL_VENDOR,
    env_float,
    env_int,
    env_value,
    load_project_env,
    model_vendor,
    provider_credential_env,
)

from client_review.context import digest, load_object, source_records
from client_review.inference import (
    INSTRUCTIONS,
    NO_RECORDS,
    REASONING_EFFORTS,
    SCHEMA,
    compact_record,
    reduce_candidates,
    validate_context,
)
from client_review.llm import build_reviewer_client

BUDDY_INSTRUCTIONS = """Independently check the primary reviewer's source-visible reference proposals against the supplied records. Confirm only when the document, reference type, value, and quote are supported. Mark close_needs_review for plausible formatting differences, conflict for material disagreement, and unsupported when the source does not support it. Never invent GL entries, payments, approvals, or facts. Return proposal-only decisions."""
ADAPTIVE_PACKET_OVERHEAD_BYTES = 40_000
BUDDY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decisions"],
    "properties": {
        "decisions": {
            "type": "array",
            "maxItems": 18,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["candidate_id", "status", "rationale"],
                "properties": {
                    "candidate_id": {"type": "string"},
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
RAW_PACKET_PATTERN = re.compile(
    r"^iteration-(?P<iteration>\d+)-(?P<role>primary|buddy)-(?P<batch>\d+)(?:-retry-(?P<retry>\d+))?\.json$"
)


def key(candidate):
    """Return the identity that decides whether a later pass found anything new."""
    return "|".join(
        str(candidate.get(k, "")) for k in ("document_id", "candidate_type", "candidate_value")
    )


def compact(candidate):
    """Truncate a candidate to the fields and lengths a packet can carry."""
    return {
        k: str(candidate.get(k, ""))[:500]
        for k in ("document_id", "candidate_type", "candidate_value", "evidence_quote")
    }


def buddy_packet(records, comments, proposals, iteration, batch, max_bytes):
    """Build the packet the buddy reviewer receives for one primary proposal set."""
    packet = {
        "lane": "client_review_iterative_buddy",
        "iteration": iteration,
        "batch": batch,
        "instructions": BUDDY_INSTRUCTIONS,
        "client_context": comments,
        "source_records": [compact_record(item) for item in records],
        "primary_candidates": [
            {"candidate_id": key(item), "candidate": compact(item)} for item in proposals
        ],
    }
    if len(json.dumps(packet, sort_keys=True).encode()) > max_bytes:
        raise ValueError("buddy packet exceeds max-context-bytes")
    return packet


def reduce_buddy(value, proposals):
    """Reduce a buddy response to confirmed, rejected, and unsupported proposals."""
    if not isinstance(value, dict) or not isinstance(value.get("decisions"), list):
        raise ValueError("buddy response must contain decisions")
    allowed = {key(item): item for item in proposals}
    accepted, rejected = [], []
    for decision in value["decisions"]:
        if not isinstance(decision, dict) or decision.get("candidate_id") not in allowed:
            rejected.append({"decision": decision, "reason": "buddy_contract_failure"})
            continue
        status = decision.get("status")
        if status not in {"confirmed", "close_needs_review", "conflict", "unsupported"}:
            rejected.append({"decision": decision, "reason": "buddy_status_invalid"})
            continue
        if status in {"confirmed", "close_needs_review"}:
            accepted.append(
                {
                    **allowed[decision["candidate_id"]],
                    "buddy_status": status,
                    "buddy_rationale": decision.get("rationale", ""),
                    "proposal_only": True,
                    "production_approval_permitted": False,
                }
            )
        else:
            rejected.append({"decision": decision, "reason": f"buddy_{status}"})
    return accepted, rejected


def new_material_candidates(candidates, known_candidate_ids):
    """Return newly confirmed, source-backed relationship proposals only.

    A repeated candidate, or one that the independent buddy did not confirm,
    cannot justify another full-corpus iteration.  The caller retains every
    proposal separately; this helper controls cost only and never clears work.
    """
    return [
        candidate
        for candidate in candidates
        if candidate.get("buddy_status") == "confirmed"
        and key(candidate) not in known_candidate_ids
    ]


def validate_reviewer_roles(primary_provider, buddy_provider, primary_model, buddy_model):
    """Require two configured, genuinely independent provider roles.

    Independence is resolved to the model vendor, exactly as ``consensus.py``
    resolves it. Comparing provider names alone would accept an OpenAI primary
    confirmed by a router serving ``openai/...``: two names, one set of weights,
    and a buddy that cannot disagree for any reason the primary would not have
    reached itself. A routed slug with no resolvable vendor is refused rather
    than treated as its own group.
    """
    if primary_provider not in LLM_PROVIDERS or buddy_provider not in LLM_PROVIDERS:
        raise ValueError("primary and buddy providers must be supported providers")
    if not primary_model or not buddy_model:
        raise ValueError("primary and buddy models must be configured")
    primary_vendor = model_vendor(primary_provider, primary_model)
    buddy_vendor = model_vendor(buddy_provider, buddy_model)
    for vendor, model in ((primary_vendor, primary_model), (buddy_vendor, buddy_model)):
        if vendor == UNRESOLVED_MODEL_VENDOR:
            raise ValueError(
                f"routed model vendor cannot be resolved from {model!r}; "
                "a vendor-prefixed model slug is required for independence"
            )
    if primary_vendor == buddy_vendor:
        raise ValueError(
            f"primary and buddy providers must be genuinely independent; both resolve to "
            f"model vendor {primary_vendor!r} and a router does not create independence"
        )


def raw_output_value(payload, required_key):
    """Decode one retained provider response without trusting its SDK shape."""
    response = payload.get("response") if isinstance(payload, dict) else None
    pending, texts = [response], []
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            output_text = value.get("output_text")
            if isinstance(output_text, str):
                texts.append(output_text)
            text = value.get("text")
            if isinstance(text, str):
                texts.append(text)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    for text in texts:
        try:
            decoded = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(decoded, dict) and isinstance(decoded.get(required_key), list):
            return decoded
    raise ValueError(f"retained response does not contain a valid {required_key} payload")


def retained_packets(raw_dir):
    """Return the latest retained primary/buddy response for each logical packet."""
    grouped = defaultdict(list)
    for path in Path(raw_dir).glob("iteration-*-*.json"):
        match = RAW_PACKET_PATTERN.fullmatch(path.name)
        if not match:
            continue
        groups = match.groupdict()
        grouped[(int(groups["iteration"]), groups["role"], int(groups["batch"]))].append(
            (int(groups["retry"] or 0), path)
        )
    packets = {}
    for identity, attempts in grouped.items():
        _, path = max(attempts)
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            packets[identity] = {"path": path, "error": f"invalid retained raw artifact: {exc}"}
        else:
            packets[identity] = {"path": path, "payload": payload}
    return packets


def finalize_existing(
    context_path,
    primary_out,
    buddy_out,
    exceptions_out,
    raw_dir,
    primary_model,
    buddy_model,
    *,
    primary_provider,
    buddy_provider,
    requested_iterations,
    reasoning_effort="medium",
    convergence_min_new_candidates=1,
):
    """Create no-clobber final artifacts solely from completed retained packets.

    This is intentionally a recovery/finalization path, not a resume: it makes
    no provider call and records every malformed or unpaired packet as an
    explicit exception. It is safe after an operator stops a later iteration.
    """
    if requested_iterations < 1 or requested_iterations > 5:
        raise ValueError("iterations must be between 1 and 5")
    if convergence_min_new_candidates < 1:
        raise ValueError("convergence minimum must be positive")
    validate_reviewer_roles(primary_provider, buddy_provider, primary_model, buddy_model)
    context = load_object(context_path, "client review context")
    validate_context(context)
    primary_out, buddy_out, exceptions_out = (
        empty_output_path(primary_out),
        empty_output_path(buddy_out),
        empty_output_path(exceptions_out),
    )
    packets = retained_packets(raw_dir)
    all_primary, all_buddy, exceptions, rounds = [], [], [], []
    known_material_candidate_ids = set()
    for iteration in range(1, requested_iterations + 1):
        batches = sorted(
            batch for item, role, batch in packets if item == iteration and role == "primary"
        )
        if not batches:
            break
        iteration_primary, iteration_buddy, batches_meta = [], [], []
        for batch in batches:
            primary = packets[(iteration, "primary", batch)]
            source_ids = []
            payload = primary.get("payload")
            if isinstance(payload, dict):
                source_ids = [
                    str(record.get("document_id", ""))
                    for record in payload.get("request", {}).get("source_records", [])
                    if isinstance(record, dict)
                ]
            try:
                proposals, rejected = reduce_candidates(
                    raw_output_value(payload, "candidates"), set(source_ids)
                )
                buddy = packets.get((iteration, "buddy", batch))
                if not buddy:
                    raise ValueError("missing retained buddy packet")
                accepted, rejected_buddy = reduce_buddy(
                    raw_output_value(buddy.get("payload"), "decisions"), proposals
                )
            except (TypeError, ValueError) as exc:
                exceptions.append(
                    {
                        "iteration": iteration,
                        "batch": batch,
                        "source_document_ids": source_ids,
                        "reason": "retained_packet_finalization_failure",
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:500],
                        "disposition": "client_review_required",
                    }
                )
                continue
            iteration_primary.extend(proposals)
            iteration_buddy.extend(accepted)
            batches_meta.append(
                {
                    "batch": batch,
                    "primary_count": len(proposals),
                    "primary_rejected": len(rejected),
                    "buddy_count": len(accepted),
                    "buddy_rejected": len(rejected_buddy),
                    "finalized_from_retained_raw": True,
                }
            )
        all_primary.extend(iteration_primary)
        all_buddy.extend(iteration_buddy)
        material_candidates = new_material_candidates(iteration_buddy, known_material_candidate_ids)
        known_material_candidate_ids.update(key(candidate) for candidate in material_candidates)
        rounds.append(
            {
                "iteration": iteration,
                "primary_count": len(iteration_primary),
                "buddy_count": len(iteration_buddy),
                "new_material_candidate_count": len(material_candidates),
                "new_material_candidate_ids": [key(candidate) for candidate in material_candidates],
                "batches": batches_meta,
            }
        )
    termination_reason = "operator_stopped_after_completed_iterations"
    primary_out.write_text(
        json.dumps(
            {
                "artifact_type": "client_review_iterative_primary_v1",
                "proposal_only": True,
                "reconstructed_from_retained_raw": True,
                "provider": primary_provider,
                "model": primary_model,
                "reasoning_effort": reasoning_effort,
                "context_sha256": digest(context_path),
                "requested_iterations": requested_iterations,
                "completed_iterations": len(rounds),
                "termination_reason": termination_reason,
                "convergence_min_new_candidates": convergence_min_new_candidates,
                "iterations": rounds,
                "candidates": all_primary,
            },
            indent=2,
        )
        + "\n"
    )
    buddy_out.write_text(
        json.dumps(
            {
                "artifact_type": "client_review_iterative_buddy_v1",
                "proposal_only": True,
                "production_approval_permitted": False,
                "reconstructed_from_retained_raw": True,
                "provider": buddy_provider,
                "model": buddy_model,
                "reasoning_effort": reasoning_effort,
                "primary_sha256": digest(primary_out),
                "requested_iterations": requested_iterations,
                "completed_iterations": len(rounds),
                "termination_reason": termination_reason,
                "convergence_min_new_candidates": convergence_min_new_candidates,
                "iterations": rounds,
                "candidates": all_buddy,
            },
            indent=2,
        )
        + "\n"
    )
    exceptions_out.write_text(
        json.dumps(
            {
                "artifact_type": "client_review_iterative_exceptions_v1",
                "proposal_only": True,
                "reconstructed_from_retained_raw": True,
                "exceptions": exceptions,
            },
            indent=2,
        )
        + "\n"
    )
    return {
        "primary": len(all_primary),
        "buddy": len(all_buddy),
        "exceptions": len(exceptions),
        "requested_iterations": requested_iterations,
        "completed_iterations": len(rounds),
        "termination_reason": termination_reason,
    }


def adaptive_batches(records, comments, batch_size, max_bytes, max_batches):
    """Yield byte-bounded batches instead of assuming a document has fixed size."""
    if batch_size < 1 or max_batches < 0:
        raise ValueError("batch-size must be positive and max-batches non-negative")
    selected, emitted = [], 0
    source_budget = max(1, max_bytes - ADAPTIVE_PACKET_OVERHEAD_BYTES)
    for record in records:
        candidate = selected + [record]
        probe = {
            "client_context": comments,
            "source_records": [compact_record(item) for item in candidate],
        }
        too_large = len(json.dumps(probe, sort_keys=True).encode()) > source_budget
        if selected and (len(candidate) > batch_size or too_large):
            yield selected
            emitted += 1
            if max_batches and emitted >= max_batches:
                return
            selected = [record]
        elif too_large:
            # Yield it so the caller records a batch-scoped exception rather than
            # aborting the entire corpus; no provider call is made for it.
            yield [record]
            emitted += 1
            if max_batches and emitted >= max_batches:
                return
            selected = []
        else:
            selected = candidate
    if selected and (not max_batches or emitted < max_batches):
        yield selected


def call(
    client,
    model,
    instructions,
    schema,
    request,
    provider,
    reasoning_effort,
    retries,
    backoff,
    max_backoff,
    raw_path,
):
    """Send one packet to a configured provider and retain its raw response."""
    retry_log = []
    attempts = 0

    def request_response():
        """Issue one bounded provider request under the shared retry runtime."""
        nonlocal attempts
        attempts += 1
        response = client.responses.create(
            model=model,
            reasoning={"effort": reasoning_effort},
            instructions=instructions,
            input=[
                {"role": "user", "content": [{"type": "input_text", "text": json.dumps(request)}]}
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema["name"],
                    "strict": True,
                    "schema": schema["schema"],
                }
            },
        )
        attempt_path = (
            raw_path
            if attempts == 1
            else raw_path.with_stem(f"{raw_path.stem}-retry-{attempts - 1:02d}")
        )
        attempt_path.write_text(
            json.dumps(
                {"request": request, "response": response_payload(response)},
                indent=2,
                default=str,
            )
            + "\n"
        )
        return response_json(response)

    response = retry_call(
        request_response,
        retries,
        backoff,
        max_backoff_seconds=max_backoff,
        jitter=True,
        retryable=lambda error: (
            retryable_error(error) or isinstance(error, (ValueError, json.JSONDecodeError))
        ),
        on_retry=lambda attempt, error, delay: retry_log.append(
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
    return response, retry_log


def run(
    context_path,
    primary_out,
    buddy_out,
    exceptions_out,
    raw_dir,
    primary_client,
    buddy_client,
    primary_model,
    buddy_model,
    *,
    iterations=1,
    batch_size=10,
    max_batches=0,
    max_context_bytes=120000,
    records_path=None,
    reasoning_effort="medium",
    retries=2,
    backoff=1.0,
    max_backoff=30.0,
    workers=1,
    convergence_min_new_candidates=1,
    primary_provider,
    buddy_provider,
):
    """Run bounded primary/buddy relationship passes over one source-bound packet.

    Stops when a completed pass adds no new source-backed, buddy-confirmed
    relationship, and never exceeds the configured iteration cap. An exhausted
    provider batch retains its exact source document IDs so a separate
    no-clobber run can recover it."""
    if iterations < 1 or iterations > 5:
        raise ValueError("iterations must be between 1 and 5")
    if reasoning_effort not in REASONING_EFFORTS:
        raise ValueError(f"unsupported reasoning effort: {reasoning_effort}")
    if convergence_min_new_candidates < 1:
        raise ValueError("convergence minimum must be positive")
    validate_reviewer_roles(primary_provider, buddy_provider, primary_model, buddy_model)
    context = load_object(context_path, "client review context")
    pilot_records, comments = validate_context(context)
    records = source_records(records_path) if records_path else pilot_records
    if not records:
        raise ValueError(NO_RECORDS)
    primary_out, buddy_out, exceptions_out = (
        empty_output_path(primary_out),
        empty_output_path(buddy_out),
        empty_output_path(exceptions_out),
    )
    raw_dir = empty_output_directory(raw_dir)
    all_primary, all_buddy, exceptions, rounds = [], [], [], []
    prior, known_material_candidate_ids = [], set()
    termination_reason = "max_iterations_reached"
    for iteration in range(1, iterations + 1):
        iteration_primary, iteration_buddy, batches_meta = [], [], []

        def run_batch(entry, iteration=iteration, prior=prior):
            """Run one batch's primary and buddy calls within this pass.

            `iteration` and `prior` are bound as defaults rather than closed over:
            the pass they belong to is the only pass they are ever correct for.
            """
            number, selected = entry
            batch_primary, batch_buddy = [], []
            try:
                request = {
                    "lane": "client_review_iterative_primary",
                    "iteration": iteration,
                    "batch": number,
                    "instructions": INSTRUCTIONS,
                    "client_context": comments,
                    "source_records": [compact_record(item) for item in selected],
                    "prior_proposals": [
                        compact(item)
                        for item in prior
                        if item.get("document_id") in {r.get("document_id") for r in selected}
                    ],
                }
                if len(json.dumps(request).encode()) > max_context_bytes:
                    raise ValueError("primary packet exceeds max-context-bytes")
                response, primary_retry = call(
                    primary_client,
                    primary_model,
                    INSTRUCTIONS,
                    {"name": "reference_discovery", "schema": SCHEMA},
                    request,
                    primary_provider,
                    reasoning_effort,
                    retries,
                    backoff,
                    max_backoff,
                    raw_dir / f"iteration-{iteration:02d}-primary-{number:03d}.json",
                )
                proposals, rejected = reduce_candidates(
                    response, {r.get("document_id") for r in selected}
                )
                batch_primary.extend(proposals)
                packet = buddy_packet(
                    selected, comments, proposals, iteration, number, max_context_bytes
                )
                buddy_response, buddy_retry = call(
                    buddy_client,
                    buddy_model,
                    BUDDY_INSTRUCTIONS,
                    {"name": "reference_buddy_check", "schema": BUDDY_SCHEMA},
                    packet,
                    buddy_provider,
                    reasoning_effort,
                    retries,
                    backoff,
                    max_backoff,
                    raw_dir / f"iteration-{iteration:02d}-buddy-{number:03d}.json",
                )
                accepted, rejected_buddy = reduce_buddy(buddy_response, proposals)
                batch_buddy.extend(accepted)
                batch_meta = {
                    "batch": number,
                    "primary_count": len(proposals),
                    "primary_rejected": len(rejected),
                    "buddy_count": len(accepted),
                    "buddy_rejected": len(rejected_buddy),
                    "primary_retry_log": primary_retry,
                    "buddy_retry_log": buddy_retry,
                }
                return {
                    "primary": batch_primary,
                    "buddy": batch_buddy,
                    "meta": batch_meta,
                    "exception": None,
                }
            except Exception as exc:
                source_document_ids = [str(record.get("document_id", "")) for record in selected]
                (
                    raw_dir / f"iteration-{iteration:02d}-batch-{number:03d}-exception.json"
                ).write_text(
                    json.dumps(
                        {
                            "iteration": iteration,
                            "batch": number,
                            "source_document_ids": source_document_ids,
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:500],
                        },
                        indent=2,
                    )
                    + "\n"
                )
                return {
                    "primary": batch_primary,
                    "buddy": batch_buddy,
                    "meta": None,
                    "exception": {
                        "iteration": iteration,
                        "batch": number,
                        "source_document_ids": source_document_ids,
                        "reason": "iterative_primary_or_buddy_failure",
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:500],
                        "disposition": "client_review_required",
                    },
                }

        # Passes stay sequential -- each reads `prior` from the last -- but batches
        # inside a pass are independent. `parallel_map` preserves input order, so a
        # pass's proposals are assembled in batch order at any worker count.
        planned = list(
            enumerate(
                adaptive_batches(records, comments, batch_size, max_context_bytes, max_batches),
                1,
            )
        )
        for outcome in parallel_map(planned, run_batch, workers):
            iteration_primary.extend(outcome["primary"])
            iteration_buddy.extend(outcome["buddy"])
            if outcome["meta"] is not None:
                batches_meta.append(outcome["meta"])
            if outcome["exception"] is not None:
                exceptions.append(outcome["exception"])
        all_primary.extend(iteration_primary)
        all_buddy.extend(iteration_buddy)
        prior = iteration_buddy or iteration_primary
        material_candidates = new_material_candidates(iteration_buddy, known_material_candidate_ids)
        known_material_candidate_ids.update(key(candidate) for candidate in material_candidates)
        rounds.append(
            {
                "iteration": iteration,
                "primary_count": len(iteration_primary),
                "buddy_count": len(iteration_buddy),
                "new_material_candidate_count": len(material_candidates),
                "new_material_candidate_ids": [key(candidate) for candidate in material_candidates],
                "batches": batches_meta,
            }
        )
        if iteration < iterations and len(material_candidates) < convergence_min_new_candidates:
            termination_reason = "converged_no_new_material_relationships"
            break
    primary_out.write_text(
        json.dumps(
            {
                "artifact_type": "client_review_iterative_primary_v1",
                "proposal_only": True,
                "provider": primary_provider,
                "model": primary_model,
                "reasoning_effort": reasoning_effort,
                "context_sha256": digest(context_path),
                "requested_iterations": iterations,
                "completed_iterations": len(rounds),
                "termination_reason": termination_reason,
                "convergence_min_new_candidates": convergence_min_new_candidates,
                "iterations": rounds,
                "candidates": all_primary,
            },
            indent=2,
        )
        + "\n"
    )
    buddy_out.write_text(
        json.dumps(
            {
                "artifact_type": "client_review_iterative_buddy_v1",
                "proposal_only": True,
                "production_approval_permitted": False,
                "provider": buddy_provider,
                "model": buddy_model,
                "reasoning_effort": reasoning_effort,
                "primary_sha256": digest(primary_out),
                "requested_iterations": iterations,
                "completed_iterations": len(rounds),
                "termination_reason": termination_reason,
                "convergence_min_new_candidates": convergence_min_new_candidates,
                "iterations": rounds,
                "candidates": all_buddy,
            },
            indent=2,
        )
        + "\n"
    )
    exceptions_out.write_text(
        json.dumps(
            {
                "artifact_type": "client_review_iterative_exceptions_v1",
                "proposal_only": True,
                "exceptions": exceptions,
            },
            indent=2,
        )
        + "\n"
    )
    return {
        "primary": len(all_primary),
        "buddy": len(all_buddy),
        "exceptions": len(exceptions),
        "requested_iterations": iterations,
        "completed_iterations": len(rounds),
        "termination_reason": termination_reason,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Run configurable independent-primary/buddy iterative proposals."
    )
    parser.add_argument("context")
    parser.add_argument(
        "--primary-out",
        required=True,
        help="Destination path for the primary lane's retained relationship proposals.",
    )
    parser.add_argument(
        "--buddy-out",
        required=True,
        help="Destination path for the buddy lane's retained confirmations.",
    )
    parser.add_argument("--exceptions", required=True)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--iterations", type=int, default=None)
    parser.add_argument(
        "--convergence-min-new-candidates",
        type=int,
        default=None,
        help="Stop after a completed pass yields fewer than this many new source-backed, buddy-confirmed relationships.",
    )
    parser.add_argument(
        "--finalize-existing",
        action="store_true",
        help="write final artifacts from completed retained raw packets without provider calls",
    )
    parser.add_argument("--reasoning-effort", choices=REASONING_EFFORTS)
    parser.add_argument(
        "--records", help="full consensus/proofed JSON corpus; overrides pilot records"
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    load_project_env()
    iterations = args.iterations or env_int("CLIENT_REVIEW_CONTEXT_LLM_MAX_ITERATIONS", 1)
    convergence_minimum = args.convergence_min_new_candidates or env_int(
        "CLIENT_REVIEW_CONTEXT_LLM_CONVERGENCE_MIN_NEW_CANDIDATES", 1
    )
    primary_provider = env_value("CLIENT_REVIEW_CONTEXT_LLM_PROVIDER", "")
    buddy_provider = env_value("CLIENT_REVIEW_CONTEXT_LLM_BUDDY_PROVIDER", "")
    primary_model = env_value("CLIENT_REVIEW_CONTEXT_LLM_MODEL", "")
    buddy_model = env_value("CLIENT_REVIEW_CONTEXT_LLM_BUDDY_MODEL", "")
    reasoning_effort = args.reasoning_effort or env_value(
        "CLIENT_REVIEW_CONTEXT_LLM_REASONING_EFFORT", "medium"
    )
    if args.finalize_existing:
        result = finalize_existing(
            args.context,
            args.primary_out,
            args.buddy_out,
            args.exceptions,
            args.raw_dir,
            primary_model,
            buddy_model,
            primary_provider=primary_provider,
            buddy_provider=buddy_provider,
            requested_iterations=iterations,
            reasoning_effort=reasoning_effort,
            convergence_min_new_candidates=convergence_minimum,
        )
        print(json.dumps(result, sort_keys=True))
        return
    validate_reviewer_roles(primary_provider, buddy_provider, primary_model, buddy_model)
    primary = build_reviewer_client(
        primary_provider,
        env_value(
            "CLIENT_REVIEW_CONTEXT_LLM_CREDENTIAL_ENV",
            provider_credential_env(primary_provider),
        ),
        env_float("CLIENT_REVIEW_CONTEXT_LLM_TIMEOUT_SECONDS", 45.0),
        env_int("CLIENT_REVIEW_CONTEXT_LLM_MAX_RETRIES", 1),
    )
    buddy = build_reviewer_client(
        buddy_provider,
        env_value(
            "CLIENT_REVIEW_CONTEXT_LLM_BUDDY_CREDENTIAL_ENV",
            provider_credential_env(buddy_provider),
        ),
        env_float("CLIENT_REVIEW_CONTEXT_LLM_BUDDY_TIMEOUT_SECONDS", 45.0),
        env_int("CLIENT_REVIEW_CONTEXT_LLM_MAX_RETRIES", 1),
    )
    result = run(
        args.context,
        args.primary_out,
        args.buddy_out,
        args.exceptions,
        args.raw_dir,
        primary,
        buddy,
        primary_model,
        buddy_model,
        iterations=iterations,
        batch_size=env_int("CLIENT_REVIEW_CONTEXT_LLM_BATCH_SIZE", 6),
        max_batches=env_int("CLIENT_REVIEW_CONTEXT_LLM_ITERATIVE_MAX_BATCHES", 0),
        max_context_bytes=env_int("CLIENT_REVIEW_CONTEXT_LLM_MAX_CONTEXT_BYTES", 40000),
        records_path=args.records,
        reasoning_effort=reasoning_effort,
        retries=env_int("CLIENT_REVIEW_CONTEXT_LLM_MAX_RETRIES", 2),
        backoff=env_float("CLIENT_REVIEW_CONTEXT_LLM_RETRY_BACKOFF_SECONDS", 1.0),
        max_backoff=env_float("CLIENT_REVIEW_CONTEXT_LLM_MAX_BACKOFF_SECONDS", 30.0),
        workers=env_int("CLIENT_REVIEW_CONTEXT_LLM_MAX_WORKERS", 8),
        convergence_min_new_candidates=convergence_minimum,
        primary_provider=primary_provider,
        buddy_provider=buddy_provider,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
