#!/usr/bin/env python3
"""Run a bounded LLM review of final-review cards without closing evidence.

The reviewer is a decision-support lane, not a consensus voter.  It receives
the complete card plus selected pipeline artifacts, returns structured review
proposals and dependency edges, and leaves the original queue untouched.  A
deterministic reducer may collapse only explicitly covered, non-protected
items in the client-facing view; every item remains in ``all_items``.
"""

import argparse
import hashlib
import json
import random
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from cli_help import apply_shared_help
from llm_provider import build_client
from llm_response import response_json, response_payload
from llm_runtime import (
    ProviderQuotaExhausted,
    estimate_tokens,
    fail_fast_on_provider_quota,
    open_provider_quota_circuit,
    parallel_map,
    provider_quota_circuit_open,
    provider_quota_error,
    request_throttle,
    reset_provider_quota_circuits,
)
from run_io import empty_output_directory, empty_output_path
from runtime_config import (
    env_bool,
    env_float,
    env_int,
    env_value,
    lane_model,
    lane_provider,
    load_project_env,
    provider_credential_env,
)

from client_review.protection import protected

REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
RETRYABLE_ERROR_NAMES = {
    "APITimeoutError",
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "RateLimitError",
    "TimeoutError",
    "WriteTimeout",
}
DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "card_id",
        "overall_decision",
        "confidence",
        "rationale",
        "item_decisions",
        "dependencies",
    ],
    "properties": {
        "card_id": {"type": "string"},
        "overall_decision": {
            "type": "string",
            "enum": ["retain_review", "propose_reduction", "needs_more_evidence"],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string"},
        "item_decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "item_id",
                    "decision",
                    "confidence",
                    "rationale",
                    "proposed_update",
                ],
                "properties": {
                    "item_id": {"type": "string"},
                    "decision": {
                        "type": "string",
                        "enum": ["retain_review", "propose_resolution", "covered_by_other_item"],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                    "proposed_update": {
                        "type": ["object", "null"],
                        "additionalProperties": False,
                        "required": [
                            "field",
                            "original_value",
                            "proposed_value",
                            "update_type",
                            "evidence",
                            "rationale",
                        ],
                        "properties": {
                            "field": {"type": "string"},
                            "original_value": {"type": ["string", "null"]},
                            "proposed_value": {"type": ["string", "null"]},
                            "update_type": {
                                "type": "string",
                                "enum": [
                                    "correction",
                                    "completion",
                                    "normalization",
                                    "none",
                                ],
                            },
                            "evidence": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                    },
                },
            },
        },
        "dependencies": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["dependent_item_id", "blocking_item_id", "relationship", "rationale"],
                "properties": {
                    "dependent_item_id": {"type": "string"},
                    "blocking_item_id": {"type": "string"},
                    "relationship": {
                        "type": "string",
                        "enum": [
                            "same_document_context",
                            "arithmetic_recheck",
                            "mapping_context",
                            "duplicate_cause",
                        ],
                    },
                    "rationale": {"type": "string"},
                },
            },
        },
    },
}
INSTRUCTIONS = """You are an evidence-bound client-review assistant. Review the supplied
document card and all supplied pipeline evidence. Do not invent values, approve
financial or identity facts, clear handwriting, arithmetic, provider, or
reassembly exceptions, or delete review items. You may propose that a low-risk
item is covered by another item only when the dependency is explicit and the
blocking item must be reviewed first. For each item, include proposed_update:
use null when no evidence-backed update is justified; otherwise include the
field, original value, proposed value, update type, exact supporting evidence,
and rationale. A proposal is not an approval. Return strict JSON matching the
schema."""
FINAL_RUN_PROTECTED_INSTRUCTIONS = """For arithmetic and reassembly findings only, you may
propose a source-backed next-step amendment or page-grouping candidate. It remains a
protected, append-only proposal: do not mark the finding resolved, do not approve a
financial fact, and do not remove it from final review."""


def load_object(path):
    """Load one JSON object and reject malformed context artifacts."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def load_queue(path):
    """Load the canonical final-review queue."""
    data = load_object(path)
    if not isinstance(data.get("items"), list) or not isinstance(data.get("review_cards"), list):
        raise ValueError("review queue must contain items and review_cards lists")
    return data


def item_id(index):
    """Return the stable identifier used inside a reviewer packet."""
    return f"review-item-{index}"


def card_items(queue, card):
    """Resolve card item indexes while preserving the source queue order."""
    items = queue["items"]
    indexes = card.get("item_indexes", [])
    if not isinstance(indexes, list):
        raise ValueError("review card item_indexes must be a list")
    resolved = []
    for index in indexes:
        if not isinstance(index, int) or index < 0 or index >= len(items):
            raise ValueError("review card contains an invalid item index")
        resolved.append({"item_id": item_id(index), "index": index, "item": items[index]})
    return resolved


def context_summary(paths, limit=120000):
    """Retain all context identities while bounding one model packet."""
    result = []
    for path in paths:
        data = json.loads(Path(path).read_text())
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"))
        if len(encoded) > limit:
            encoded = encoded[:limit] + "...TRUNCATED_FOR_PACKET"
        result.append(
            {
                "artifact": str(Path(path)),
                "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                "json": encoded,
            }
        )
    return result


def packet(card, resolved_items, context):
    """Build the complete bounded card packet sent to the reviewer."""
    return {"card": card, "items": resolved_items, "pipeline_context": context}


def reviewer_instructions(final_run_include_protected_arithmetic_reassembly=False):
    """Return the final-run instruction extension without changing protections."""
    return (
        INSTRUCTIONS + "\n\n" + FINAL_RUN_PROTECTED_INSTRUCTIONS
        if final_run_include_protected_arithmetic_reassembly
        else INSTRUCTIONS
    )


def stable_sha256(value):
    """Hash strict JSON without permitting non-finite values."""
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def resume_contract(
    queue_path,
    context,
    model,
    provider,
    max_context_bytes,
    reasoning_effort,
    auto_accept,
    auto_accept_threshold,
    instructions,
):
    """Bind resumable decisions to every decision-affecting input and setting."""
    value = {
        "schema_version": "client_review_resume_v2",
        "queue_sha256": hashlib.sha256(Path(queue_path).read_bytes()).hexdigest(),
        "context_artifacts": [
            {"artifact": item["artifact"], "sha256": item["sha256"]} for item in context
        ],
        "reviewer_provider": provider,
        "reviewer_model": model,
        "max_context_bytes": max_context_bytes,
        "reasoning_effort": reasoning_effort,
        "auto_accept": auto_accept,
        "auto_accept_threshold": auto_accept_threshold,
        "reviewer_instructions_sha256": hashlib.sha256(instructions.encode()).hexdigest(),
    }
    value["resume_contract_sha256"] = stable_sha256(value)
    return value


def validate_resumed_cards(resume_data, expected_contract, queue, context):
    """Return only prior results bound to the unchanged queue, context, and raw evidence."""
    if not isinstance(resume_data, dict) or resume_data.get("resume_contract") != expected_contract:
        raise ValueError("resume contract does not match the current review run")
    cards = resume_data.get("cards")
    if not isinstance(cards, list):
        raise ValueError("resume cards must be a list")
    current = {card.get("card_id"): card for card in queue["review_cards"]}
    indexed = {}
    for item in cards:
        if not isinstance(item, dict) or not isinstance(item.get("card_id"), str):
            raise ValueError("resume cards require card_id values")
        card_id = item["card_id"]
        if card_id in indexed or card_id not in current:
            raise ValueError("resume cards must be unique members of the current queue")
        resolved = card_items(queue, current[card_id])
        expected_request = stable_sha256(packet(current[card_id], resolved, context))
        if item.get("review_request_sha256") != expected_request:
            raise ValueError("resume card request hash does not match current evidence")
        raw = item.get("raw_response")
        raw_sha = item.get("raw_response_sha256")
        raw_path = Path(raw) if isinstance(raw, str) else Path("")
        if (
            not isinstance(raw, str)
            or not raw
            or not raw_path.is_file()
            or not re.fullmatch(r"[0-9a-f]{64}", str(raw_sha or ""))
            or hashlib.sha256(raw_path.read_bytes()).hexdigest() != raw_sha
        ):
            raise ValueError("resume raw response evidence is missing or changed")
        indexed[card_id] = item
    return indexed


def page_request(client, model, reasoning_effort, review_packet, instructions=INSTRUCTIONS):
    """Submit one card for structured review."""
    return client.responses.create(
        model=model,
        reasoning={"effort": reasoning_effort},
        instructions=instructions,
        input=[
            {"role": "user", "content": [{"type": "input_text", "text": json.dumps(review_packet)}]}
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "business_document_client_review",
                "strict": True,
                "schema": DECISION_SCHEMA,
            }
        },
    )


def retry_after_seconds(error):
    """Read a bounded numeric or HTTP-date Retry-After header."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {}) if response is not None else {}
    value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            from email.utils import parsedate_to_datetime

            return max(0.0, parsedate_to_datetime(str(value)).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


def retryable_error(error):
    """Retry transient provider failures, including common SDK status codes."""
    if type(error).__name__ in RETRYABLE_ERROR_NAMES:
        return True
    return getattr(error, "status_code", None) in {408, 409, 425, 429, 500, 502, 503, 504}


def retry_page_request(
    client,
    model,
    reasoning_effort,
    review_packet,
    max_retries,
    backoff_seconds,
    max_backoff_seconds,
    sleep=time.sleep,
    uniform=random.uniform,
    throttle=None,
    instructions=INSTRUCTIONS,
    retry_log=None,
):
    """Retry transient reviewer calls with capped exponential full jitter.

    The caller may supply `retry_log` so the record survives a raise. Returning
    it only on success left every failed card reporting zero attempts -- the one
    case where knowing what was tried actually matters.
    """
    retry_log = [] if retry_log is None else retry_log
    throttle = throttle or request_throttle("client_review")
    provider = getattr(throttle, "provider", "client_review")
    if fail_fast_on_provider_quota() and provider_quota_circuit_open(provider):
        raise ProviderQuotaExhausted(
            f"{provider} provider quota circuit is open; request was not attempted"
        )
    for attempt in range(max_retries + 1):
        try:
            throttle.acquire(request_tokens=estimate_tokens(review_packet))
            return page_request(
                client, model, reasoning_effort, review_packet, instructions
            ), retry_log
        except Exception as error:
            if fail_fast_on_provider_quota() and provider_quota_error(error):
                open_provider_quota_circuit(provider)
                raise ProviderQuotaExhausted(
                    f"{provider} provider quota exhausted; remaining requests deferred"
                ) from error
            if not retryable_error(error) or attempt >= max_retries:
                raise
            exponential = min(max_backoff_seconds, backoff_seconds * (2**attempt))
            requested = retry_after_seconds(error)
            cap = min(max_backoff_seconds, requested) if requested is not None else exponential
            delay = cap if requested is not None else uniform(0.0, cap)
            retry_log.append(
                {
                    "attempt": attempt + 1,
                    "error_type": type(error).__name__,
                    "status_code": getattr(error, "status_code", None),
                    "retry_after_seconds": requested,
                    "delay_seconds": delay,
                }
            )
            sleep(delay)
    raise RuntimeError("retry loop exhausted")  # pragma: no cover


def build_reviewer_client(provider, credential_env, timeout_seconds, max_retries):
    """Build the reviewer provider independently of the extraction-lane setting."""
    if provider == "openai":
        from openai_adapter import build_client as build_openai_client

        return build_openai_client(credential_env, timeout_seconds, max_retries)
    if provider == "google":
        return build_client(credential_env, timeout_seconds, max_retries, provider="google")
    if provider == "openrouter":
        import openrouter_adapter

        return openrouter_adapter.build_client(credential_env, timeout_seconds, max_retries)
    if provider == "anthropic":
        # Every other surface accepts anthropic -- `resolve_reviewer_configuration`
        # validates against it, the error below names it, and the adapter has
        # exposed a matching `build_client` all along. Only this branch was
        # missing, so a lane configured with an anthropic buddy failed at
        # construction with a message listing the provider it had just refused.
        import anthropic_adapter

        return anthropic_adapter.build_client(credential_env, timeout_seconds, max_retries)
    raise ValueError(
        "LLM_CLIENT_REVIEW_PROVIDER must be one of: anthropic, google, openai, openrouter"
    )


def resolve_reviewer_configuration(final_provider=None, final_model=None, legacy_model=None):
    """Resolve final-stage provider/model while inheriting all other settings."""
    provider = final_provider or env_value("LLM_CLIENT_REVIEW_PROVIDER", lane_provider("reasoning"))
    if provider not in {"openai", "google", "openrouter", "anthropic"}:
        raise ValueError("final provider must be one of: anthropic, google, openai, openrouter")
    model = (
        final_model
        or legacy_model
        or env_value("CLIENT_REVIEW_LLM_MODEL", lane_model("reasoning", provider))
    )
    return provider, model


def raw_name(index, card):
    """Create a safe retained raw-response filename."""
    label = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(card.get("card_id", "card"))).strip("_") or "card"
    return f"{index:06d}_{label}.json"


def valid_dependencies(decision, item_map):
    """Keep only explicit, in-card, non-self dependency edges."""
    valid = []
    for edge in decision.get("dependencies", []):
        dependent = item_map.get(edge.get("dependent_item_id"))
        blocking = item_map.get(edge.get("blocking_item_id"))
        if dependent is None or blocking is None or dependent == blocking:
            continue
        valid.append(edge)
    return valid


def valid_proposed_update(proposal, item=None):
    """Accept only a complete proposal bound to the reviewed field."""
    if not isinstance(proposal, dict):
        return False
    required = (
        "field",
        "original_value",
        "proposed_value",
        "update_type",
        "evidence",
        "rationale",
    )
    if not all(isinstance(proposal.get(key), str) and proposal[key].strip() for key in required):
        return False
    if proposal["update_type"] not in {"correction", "completion", "normalization"}:
        return False
    expected_field = str((item or {}).get("field", "")).strip()
    return not expected_field or proposal["field"].strip() == expected_field


def validate_auto_accept_threshold(threshold):
    """Require a deliberately high confidence floor for auto-acceptance."""
    if not isinstance(threshold, (int, float)) or not 0.99 <= threshold <= 1.0:
        raise ValueError("auto-accept threshold must be between 0.99 and 1.0")


def reduce_card(resolved_items, decision, auto_accept=False, auto_accept_threshold=0.99):
    """Return a reduced view while retaining every original item and reason."""
    item_map = {entry["item_id"]: entry["item"] for entry in resolved_items}
    decisions = {entry.get("item_id"): entry for entry in decision.get("item_decisions", [])}
    edges = valid_dependencies(decision, item_map)
    blockers = {edge["dependent_item_id"]: edge for edge in edges}
    all_items = []
    visible = []
    covered = []
    accepted_updates = []
    for entry in resolved_items:
        item = dict(entry["item"])
        item["review_item_id"] = entry["item_id"]
        model_decision = decisions.get(entry["item_id"], {})
        item["reviewer_decision"] = model_decision.get("decision", "retain_review")
        item["reviewer_confidence"] = model_decision.get("confidence")
        item["reviewer_rationale"] = model_decision.get(
            "rationale", "No reviewer decision supplied"
        )
        proposal = model_decision.get("proposed_update")
        item["llm_proposed_update"] = proposal if valid_proposed_update(proposal, item) else None
        # The repository's own invariant is that provider confidence is
        # non-decisional provenance. It is retained on the item above as exactly
        # that. The decision rests on the fail-closed protection taxonomy, an
        # explicit reviewer decision, and a proposal bound to the reviewed field
        # with retained evidence -- none of which the model can self-report.
        can_auto_accept = (
            auto_accept
            and not protected(item)
            and model_decision.get("decision") == "propose_resolution"
            and valid_proposed_update(proposal, item)
        )
        # Statements rather than a nested conditional expression, so the branch
        # gate can see each outcome. See docs/DEBUG_FIX_UPGRADE_PLAN.md section 3.1.
        if can_auto_accept:
            item["llm_auto_accept_status"] = "auto_accepted"
        elif not auto_accept:
            item["llm_auto_accept_status"] = "disabled"
        else:
            item["llm_auto_accept_status"] = "not_eligible"
        edge = blockers.get(entry["item_id"])
        target = item_map.get(edge["blocking_item_id"]) if edge else None
        can_cover = (
            edge is not None
            and target is not None
            and not protected(item)
            and model_decision.get("decision") == "covered_by_other_item"
            and decisions.get(edge["blocking_item_id"], {}).get("decision") == "propose_resolution"
        )
        if can_cover:
            item["dependency_status"] = "covered_by_review_item"
            item["covered_by"] = edge["blocking_item_id"]
            covered.append(item)
        elif can_auto_accept:
            item["disposition"] = "llm_auto_accepted_proposal"
            item["client_review_required"] = False
            accepted_updates.append(item)
        else:
            item["dependency_status"] = "independent_review_required"
            visible.append(item)
        all_items.append(item)
    return {
        "all_items": all_items,
        "visible_items": visible,
        "covered_items": covered,
        "auto_accepted_updates": accepted_updates,
        "dependencies": edges,
    }


def validate_limits(
    max_cards, max_context_bytes, timeout_seconds, max_retries, reasoning_effort, workers=1
):
    """Validate bounded reviewer execution settings."""
    if not isinstance(max_cards, int) or max_cards < 1:
        raise ValueError("max cards must be positive")
    if not isinstance(max_context_bytes, int) or max_context_bytes < 1:
        raise ValueError("max context bytes must be positive")
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise ValueError("timeout seconds must be positive")
    if not isinstance(max_retries, int) or max_retries < 0:
        raise ValueError("max retries must be non-negative")
    if reasoning_effort not in REASONING_EFFORTS:
        raise ValueError("reasoning effort is not supported")
    if not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")


def validate_retry_limits(backoff_seconds, max_backoff_seconds):
    """Validate retry delay controls before a provider invocation."""
    if not isinstance(backoff_seconds, (int, float)) or backoff_seconds < 0:
        raise ValueError("retry backoff seconds must be non-negative")
    if not isinstance(max_backoff_seconds, (int, float)) or max_backoff_seconds <= 0:
        raise ValueError("maximum retry backoff seconds must be positive")
    if backoff_seconds > max_backoff_seconds:
        raise ValueError("retry backoff seconds cannot exceed maximum retry backoff")


def run_review(
    queue_path,
    context_paths,
    out_path,
    exceptions_path,
    raw_dir,
    model,
    client,
    *,
    max_cards=1000,
    max_context_bytes=20000,
    timeout_seconds=120.0,
    max_retries=4,
    retry_backoff_seconds=1.0,
    max_backoff_seconds=120.0,
    reasoning_effort="medium",
    auto_accept=False,
    auto_accept_threshold=0.99,
    provider=None,
    resume_from=None,
    workers=1,
    final_run_include_protected_arithmetic_reassembly=False,
):
    """Review cards, fail closed on provider errors, and write immutable outputs."""
    reset_provider_quota_circuits()
    queue = load_queue(queue_path)
    validate_limits(
        max_cards, max_context_bytes, timeout_seconds, max_retries, reasoning_effort, workers
    )
    validate_retry_limits(retry_backoff_seconds, max_backoff_seconds)
    validate_auto_accept_threshold(auto_accept_threshold)
    instructions = reviewer_instructions(final_run_include_protected_arithmetic_reassembly)
    cards = queue["review_cards"]
    if len(cards) > max_cards:
        raise ValueError(f"card count exceeds configured limit: {len(cards)} > {max_cards}")
    context = context_summary(context_paths, max_context_bytes)
    contract = resume_contract(
        queue_path,
        context,
        model,
        provider,
        max_context_bytes,
        reasoning_effort,
        auto_accept,
        auto_accept_threshold,
        instructions,
    )
    results, exceptions, reductions, accepted_updates = [], [], [], []
    resumed_cards = {}
    if resume_from:
        resume_data = json.loads(Path(resume_from).read_text())
        resumed_cards = validate_resumed_cards(resume_data, contract, queue, context)
        results.extend(resumed_cards.values())
        for item in results:
            reduction = item.get("reduction", {})
            reductions.extend(reduction.get("covered_items", []))
            accepted_updates.extend(reduction.get("auto_accepted_updates", []))
    out_path = empty_output_path(out_path)
    exceptions_path = empty_output_path(exceptions_path)
    raw_dir = empty_output_directory(raw_dir)
    throttle = request_throttle(provider or "client_review")
    quota_circuit = Event()

    def review_card(entry):
        """Review one card and return its outcome without touching shared state."""
        index, card = entry
        if card.get("card_id") in resumed_cards:
            return None
        if quota_circuit.is_set():
            return (
                "circuit_skipped",
                {
                    "card_id": card.get("card_id"),
                    "reason": "client_review_rate_limit_circuit_open",
                    "disposition": "client_review_required",
                },
            )
        retry_log = []
        try:
            resolved = card_items(queue, card)
            response, retry_log = retry_page_request(
                client,
                model,
                reasoning_effort,
                packet(card, resolved, context),
                max_retries,
                retry_backoff_seconds,
                max_backoff_seconds,
                throttle=throttle,
                instructions=instructions,
                retry_log=retry_log,
            )
            raw_path = raw_dir / raw_name(index, card)
            raw_path.write_text(
                json.dumps(
                    {
                        "request": packet(card, resolved, context),
                        "response": response_payload(response),
                    },
                    indent=2,
                    default=str,
                )
                + "\n"
            )
            decision = response_json(response)
            if decision.get("card_id") != card.get("card_id"):
                raise ValueError("reviewer card_id does not match request")
            reduced = reduce_card(resolved, decision, auto_accept, auto_accept_threshold)
            return (
                "result",
                {
                    "card_id": card.get("card_id"),
                    "decision": decision,
                    "reduction": reduced,
                    "raw_response": str(raw_path),
                    "raw_response_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                    "review_request_sha256": stable_sha256(packet(card, resolved, context)),
                    "retry_log": retry_log,
                },
            )
        except Exception as exc:
            rate_limit_failure = fail_fast_on_provider_quota() and (
                isinstance(exc, ProviderQuotaExhausted) or provider_quota_error(exc)
            )
            if rate_limit_failure:
                quota_circuit.set()
            return (
                "exception",
                {
                    "card_id": card.get("card_id"),
                    "reason": "client_review_llm_provider_or_schema_failure",
                    "error_type": type(exc).__name__,
                    "retry_attempts": len(retry_log),
                    "retry_log": retry_log,
                    "rate_limit_failure": rate_limit_failure,
                    "disposition": "client_review_required",
                },
            )

    # `parallel_map` preserves input order, so the retained artifact is byte-identical
    # whatever the worker count: a card's position in the output never depends on how
    # quickly it returned. At one worker it is the same sequential pass as before.
    circuit_skipped_cards = 0
    for outcome in parallel_map(list(enumerate(cards, 1)), review_card, workers):
        if outcome is None:
            continue
        kind, payload = outcome
        if kind == "result":
            results.append(payload)
            reductions.extend(payload["reduction"]["covered_items"])
            accepted_updates.extend(payload["reduction"]["auto_accepted_updates"])
            continue
        if kind == "circuit_skipped":
            circuit_skipped_cards += 1
        exceptions.append(payload)
    retry_logs = [item.get("retry_log", []) for item in results]
    retry_logs.extend(item.get("retry_log", []) for item in exceptions)
    retry_events = [event for log in retry_logs for event in log]
    summary = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "reviewer_provider": provider,
        "reviewer_model": model,
        "reviewer_cards": len(cards),
        "workers": workers,
        "successful_cards": len(results),
        "provider_exceptions": len(exceptions),
        "covered_items": len(reductions),
        "auto_accepted_updates": sum(
            len(card["reduction"]["auto_accepted_updates"]) for card in results
        ),
        "auto_accept_policy": {
            "enabled": auto_accept,
            "threshold": auto_accept_threshold,
            "protected_categories_never_auto_accepted": True,
            "final_run_include_protected_arithmetic_reassembly": final_run_include_protected_arithmetic_reassembly,
        },
        "retry_policy": {
            "max_retries": max_retries,
            "backoff_seconds": retry_backoff_seconds,
            "max_backoff_seconds": max_backoff_seconds,
            "jitter": "full_uniform",
        },
        "retry_telemetry": {
            "retry_attempts": len(retry_events),
            "rate_limit_attempts": sum(
                1
                for event in retry_events
                if event.get("status_code") == 429 or event.get("error_type") == "RateLimitError"
            ),
            "total_retry_delay_seconds": sum(
                float(event.get("delay_seconds") or 0) for event in retry_events
            ),
            "exhausted_cards": len(exceptions),
            "circuit_breaker_skipped_cards": circuit_skipped_cards,
            "resumed_from": str(resume_from) if resume_from else None,
        },
        "all_source_items_retained": True,
        "production_approval_permitted": False,
        "gate_status": "blocked_pending_client_review",
        "findings": [
            "Reviewer output is proposal-only; the original queue and source evidence are never rewritten."
        ],
    }
    out_path.write_text(
        json.dumps(
            {
                "summary": summary,
                "source_queue": str(queue_path),
                "context_artifacts": [str(path) for path in context_paths],
                "resume_contract": contract,
                "cards": results,
                "accepted_updates": accepted_updates,
            },
            indent=2,
        )
        + "\n"
    )
    exceptions_path.write_text(
        json.dumps({"summary": {"count": len(exceptions)}, "exceptions": exceptions}, indent=2)
        + "\n"
    )
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Run a bounded, proposal-only LLM client-review lane."
    )
    parser.add_argument("queue")
    parser.add_argument("--context", action="append", default=[])
    parser.add_argument("--out", required=True)
    parser.add_argument("--exceptions", required=True)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--final-provider", choices=("openai", "google", "openrouter", "anthropic"), default=None
    )
    parser.add_argument("--final-model", default=None)
    parser.add_argument(
        "--max-cards", type=int, default=None, help="Maximum review cards this invocation may send."
    )
    parser.add_argument("--max-context-bytes", type=int, default=None)
    parser.add_argument("--timeout-seconds", type=float, default=None)
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument("--retry-backoff-seconds", type=float, default=None)
    parser.add_argument("--max-backoff-seconds", type=float, default=None)
    parser.add_argument(
        "--auto-accept-llm-proposals",
        action="store_true",
        help="Allow proposals at or above the threshold to be marked auto-accepted. Protected findings are never eligible.",
    )
    parser.add_argument(
        "--auto-accept-threshold",
        type=float,
        default=None,
        help="Proposal score at or above which auto-acceptance may apply. Provider confidence is not a decision term.",
    )
    parser.add_argument(
        "--final-run-include-protected-arithmetic-reassembly",
        action="store_true",
        help="Allow source-backed proposals for protected arithmetic/reassembly findings; they remain review-required.",
    )
    parser.add_argument("--reasoning-effort", default=None, choices=REASONING_EFFORTS)
    parser.add_argument(
        "--resume-from", default=None, help="prior reviewer output; retry only unresolved cards"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="cards reviewed concurrently; output order is unchanged by this setting",
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        load_project_env()
        enabled = args.enable or env_bool("CLIENT_REVIEW_LLM_ENABLED", False)
        if not enabled:
            out = empty_output_path(args.out)
            exceptions = empty_output_path(args.exceptions)
            empty_output_directory(args.raw_dir)
            payload = {
                "summary": {
                    "schema_version": "1.0",
                    "enabled": False,
                    "production_approval_permitted": False,
                },
                "cards": [],
            }
            out.write_text(json.dumps(payload, indent=2) + "\n")
            exceptions.write_text(
                json.dumps({"summary": {"count": 0}, "exceptions": []}, indent=2) + "\n"
            )
            print("Client-review LLM disabled")
            return
        provider, model = resolve_reviewer_configuration(
            args.final_provider, args.final_model, args.model
        )
        client = build_reviewer_client(
            provider,
            env_value("CLIENT_REVIEW_LLM_CREDENTIAL_ENV", provider_credential_env(provider)),
            args.timeout_seconds or env_float("CLIENT_REVIEW_LLM_TIMEOUT_SECONDS", 120.0),
            args.max_retries
            if args.max_retries is not None
            else env_int("CLIENT_REVIEW_LLM_MAX_RETRIES", 4),
        )
        summary = run_review(
            args.queue,
            args.context,
            args.out,
            args.exceptions,
            args.raw_dir,
            model,
            client,
            max_cards=args.max_cards or env_int("CLIENT_REVIEW_LLM_MAX_CARDS", 1000),
            max_context_bytes=args.max_context_bytes
            or env_int("CLIENT_REVIEW_LLM_MAX_CONTEXT_BYTES", 20000),
            timeout_seconds=args.timeout_seconds
            or env_float("CLIENT_REVIEW_LLM_TIMEOUT_SECONDS", 120.0),
            max_retries=args.max_retries
            if args.max_retries is not None
            else env_int("CLIENT_REVIEW_LLM_MAX_RETRIES", 4),
            retry_backoff_seconds=(
                args.retry_backoff_seconds
                if args.retry_backoff_seconds is not None
                else env_float("CLIENT_REVIEW_LLM_RETRY_BACKOFF_SECONDS", 1.0)
            ),
            max_backoff_seconds=(
                args.max_backoff_seconds
                if args.max_backoff_seconds is not None
                else env_float("CLIENT_REVIEW_LLM_MAX_BACKOFF_SECONDS", 120.0)
            ),
            auto_accept=args.auto_accept_llm_proposals
            or env_bool("CLIENT_REVIEW_LLM_AUTO_ACCEPT_PROPOSALS", False),
            auto_accept_threshold=(
                args.auto_accept_threshold
                if args.auto_accept_threshold is not None
                else env_float("CLIENT_REVIEW_LLM_AUTO_ACCEPT_THRESHOLD", 0.99)
            ),
            reasoning_effort=args.reasoning_effort
            or env_value("CLIENT_REVIEW_LLM_REASONING_EFFORT", "medium"),
            provider=provider,
            resume_from=args.resume_from,
            workers=args.workers or env_int("CLIENT_REVIEW_LLM_MAX_WORKERS", 8),
            final_run_include_protected_arithmetic_reassembly=(
                args.final_run_include_protected_arithmetic_reassembly
                or env_bool(
                    "CLIENT_REVIEW_LLM_FINAL_RUN_INCLUDE_PROTECTED_ARITHMETIC_REASSEMBLY", False
                )
            ),
        )
        print(
            f"Client-review LLM cards: {summary['successful_cards']}/{summary['reviewer_cards']}; covered items: {summary['covered_items']}"
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Client-review LLM failed: {exc}")


if __name__ == "__main__":
    main()
