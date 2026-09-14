#!/usr/bin/env python3
"""Run a bounded, full-dataset review agent over retained pipeline artifacts.

This is a proposal-only reasoning lane.  It builds a complete inventory and
cross-record evidence index from every supplied JSON artifact, then asks the
configured OpenAI or Vertex-compatible reviewer to reason over the dataset in
bounded iterations.  The deterministic reducer preserves all evidence and
keeps handwriting in a secondary-comments channel: handwriting may explain
context, but it is never a primary fact, decision input, blocker, or
auto-acceptance source in this lane.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
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
from runtime_config import (
    env_bool,
    env_float,
    env_int,
    env_value,
    load_project_env,
    provider_credential_env,
)

from client_review.llm import build_reviewer_client, resolve_reviewer_configuration
from client_review.protection import protected

AGENT_SCHEMA_VERSION = "1.0"
MAX_REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
HANDWRITING_WORDS = ("handwriting", "handwritten", "htr", "annotation")

AGENT_INSTRUCTIONS = """You are a bounded full-dataset review agent. Use the complete supplied
artifact inventory, cross-record evidence index, validated facts, outstanding
review items, contradictions, and prior iteration results to find mistakes and
improve the dataset-level review. Do not invent evidence, overwrite source
values, approve production facts, or treat repetition as proof. Every proposal
must cite evidence references and preserve the original value.

Handwriting is secondary context only in this agent. Extract or mention it when
it helps explain a record, but never use it as a primary fact, never use it to
make a decision, never make it a blocker, and never auto-accept anything from
it. Put handwriting observations only in handwriting_comments with
relevance=secondary_context_only. Protected financial, identity, arithmetic,
provider, reassembly, and missing-field matters remain client review.

Reason across the whole dataset, including validated and outstanding records.
Identify process-level patterns that could improve prompts, mappings,
validation, routing, or extraction. Those are proposals only. Return strict
JSON matching the supplied schema. Abstain when evidence conflicts or is
insufficient."""

DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "hypotheses",
        "item_updates",
        "process_findings",
        "handwriting_comments",
        "next_action",
        "confidence",
    ],
    "properties": {
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "hypothesis_id",
                    "statement",
                    "status",
                    "evidence_refs",
                    "affected_item_ids",
                    "proposed_process_change",
                ],
                "properties": {
                    "hypothesis_id": {"type": "string"},
                    "statement": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["supported", "contradicted", "unresolved"],
                    },
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "affected_item_ids": {"type": "array", "items": {"type": "string"}},
                    "proposed_process_change": {"type": "string"},
                },
            },
        },
        "item_updates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "review_item_id",
                    "decision",
                    "confidence",
                    "rationale",
                    "evidence_refs",
                    "proposed_update",
                    "dependency_item_ids",
                ],
                "properties": {
                    "review_item_id": {"type": "string"},
                    "decision": {
                        "type": "string",
                        "enum": ["retain_review", "propose_resolution", "needs_more_evidence"],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
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
                                "enum": ["correction", "completion", "normalization", "none"],
                            },
                            "evidence": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                    },
                    "dependency_item_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "process_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["finding_id", "pattern", "evidence_refs", "proposed_change"],
                "properties": {
                    "finding_id": {"type": "string"},
                    "pattern": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "proposed_change": {"type": "string"},
                },
            },
        },
        "handwriting_comments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["review_item_id", "comment", "evidence_refs", "relevance"],
                "properties": {
                    "review_item_id": {"type": "string"},
                    "comment": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "relevance": {"type": "string", "enum": ["secondary_context_only"]},
                },
            },
        },
        "next_action": {"type": "string", "enum": ["iterate", "stabilized", "abstain"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


def sha256(path):
    """Hash one retained artifact."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path):
    """Load either a JSON object or array from a supplied artifact."""
    value = json.loads(Path(path).read_text())
    if not isinstance(value, (dict, list)):
        raise ValueError(f"{path} must contain a JSON object or array")
    return value


def is_handwriting(value):
    """Identify handwriting-related evidence without making it decisive."""
    text = json.dumps(value, sort_keys=True).casefold()
    return any(word in text for word in HANDWRITING_WORDS)


def item_reason(value):
    """Return a review reason from a candidate object."""
    return str(value.get("reason", value.get("review_reason", ""))).casefold()


def evidence_kind(value):
    """Classify evidence by its safety role."""
    reason = item_reason(value)
    if reason and protected(value):
        return "protected_outstanding"
    if "review_item_id" in value or value.get("client_review_required") is True:
        return "outstanding_or_decision"
    if reason and any(
        word in reason for word in ("review", "exception", "disagreement", "unresolved")
    ):
        return "outstanding_or_decision"
    if any(key in value for key in ("reviewer_decision", "decision", "exceptions")):
        return "outstanding_or_decision"
    if value.get("review_status") in {"auto_accepted", "sampled_verified", "exception_resolved"}:
        return "validated"
    return "source_or_derived"


def walk_objects(value, path="$"):
    """Yield every nested JSON object with a stable JSON-path reference."""
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from walk_objects(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_objects(child, f"{path}[{index}]")


def identity(value):
    """Return a useful cross-record identity when one is present."""
    for key in ("record_id", "document_id", "source_record_id", "page_id", "id"):
        if value.get(key) not in (None, ""):
            return str(value[key])
    return None


def field_name(value):
    """Return the best available field name for cross-record grouping."""
    for key in ("field", "source_field", "name", "semantic_type"):
        if value.get(key) not in (None, ""):
            return str(value[key])
    return None


def field_value(value):
    """Return a scalar value used only for deterministic signal discovery."""
    for key in ("value", "proposed_value", "candidate_value", "original_value"):
        candidate = value.get(key)
        if isinstance(candidate, dict):
            candidate = candidate.get("value")
        if candidate not in (None, "") and isinstance(candidate, (str, int, float, bool)):
            return str(candidate)
    return None


def build_evidence_index(paths):
    """Build a complete inventory plus all usable cross-record evidence."""
    artifacts = []
    evidence = []
    groups = defaultdict(list)
    handwriting = []
    for path in paths:
        source = str(Path(path))
        data = load_json(path)
        objects = list(walk_objects(data))
        artifacts.append(
            {
                "path": source,
                "sha256": sha256(path),
                "bytes": Path(path).stat().st_size,
                "objects": len(objects),
                "top_level_type": type(data).__name__,
            }
        )
        for ref, value in objects:
            kind = evidence_kind(value)
            record_id = identity(value)
            field = field_name(value)
            scalar = field_value(value)
            entry = {
                "ref": f"{source}::{ref}",
                "artifact": source,
                "kind": kind,
                "review_item_id": value.get("review_item_id"),
                "record_id": record_id,
                "field": field,
                "value": scalar,
                "has_handwriting_context": is_handwriting(value),
                "object": value,
            }
            if record_id or field or kind != "source_or_derived":
                evidence.append(entry)
            if record_id and field and scalar is not None:
                groups[(field.casefold(), scalar.casefold())].append(entry["ref"])
            if is_handwriting(value):
                handwriting.append(entry["ref"])
    contradictions = []
    by_field_record = defaultdict(set)
    for entry in evidence:
        if entry["record_id"] and entry["field"] and entry["value"] is not None:
            by_field_record[(entry["record_id"], entry["field"].casefold())].add(entry["value"])
    for (record_id, field), values in sorted(by_field_record.items()):
        if len(values) > 1:
            contradictions.append(
                {"record_id": record_id, "field": field, "values": sorted(values)}
            )
    counts = Counter(entry["kind"] for entry in evidence)
    return {
        "schema_version": AGENT_SCHEMA_VERSION,
        "artifacts": artifacts,
        "evidence": evidence,
        "cross_record_signals": [
            {"field": key[0], "value": key[1], "evidence_refs": refs, "count": len(refs)}
            for key, refs in sorted(groups.items())
            if len(refs) > 1
        ],
        "contradictions": contradictions,
        "handwriting_context_refs": sorted(set(handwriting)),
        "counts": dict(counts),
        "all_artifacts_indexed": True,
        "all_source_values_retained": True,
    }


def review_item_ids(index):
    """Return stable IDs for outstanding review objects."""
    return sorted(
        entry.get("review_item_id") or entry["ref"]
        for entry in index["evidence"]
        if entry["kind"] in {"protected_outstanding", "outstanding_or_decision"}
    )


def compact_evidence(entry):
    """Create a small LLM view while the complete object stays in the index."""
    source = entry.get("object", {})
    summary = {
        key: source[key]
        for key in (
            "document_id",
            "page_id",
            "record_id",
            "field",
            "source_field",
            "reason",
            "review_reason",
            "review_status",
            "value",
            "original_value",
            "proposed_value",
            "client_review_required",
            "evidence_refs",
        )
        if key in source
    }
    return {
        "ref": entry["ref"],
        "kind": entry["kind"],
        "review_item_id": entry.get("review_item_id"),
        "record_id": entry.get("record_id"),
        "field": entry.get("field"),
        "value": entry.get("value"),
        "has_handwriting_context": entry.get("has_handwriting_context", False),
        "object_summary": summary,
    }


def index_slice(index, entries, slice_number):
    """Build one bounded analysis slice with only related global signals."""
    refs = {entry["ref"] for entry in entries}
    records = {entry.get("record_id") for entry in entries if entry.get("record_id")}
    signals = [
        signal
        for signal in index["cross_record_signals"]
        if refs.intersection(signal.get("evidence_refs", []))
    ]
    contradictions = [
        contradiction
        for contradiction in index["contradictions"]
        if contradiction.get("record_id") in records
    ]
    return {
        "schema_version": index["schema_version"],
        "slice_number": slice_number,
        "slice_evidence_count": len(entries),
        "total_evidence_count": len(index["evidence"]),
        "artifacts": index["artifacts"],
        "evidence": [compact_evidence(entry) for entry in entries],
        "cross_record_signals": signals,
        "contradictions": contradictions,
        "handwriting_context_refs": [
            ref for ref in index["handwriting_context_refs"] if ref in refs
        ],
        "counts": dict(Counter(entry["kind"] for entry in entries)),
        "all_artifacts_indexed": True,
        "all_source_values_retained": True,
    }


def analysis_slices(index, max_bytes, max_evidence_per_slice=500):
    """Partition evidence into bounded, complete slices for provider calls."""
    if max_bytes < 1:
        raise ValueError("agent slice context bytes must be positive")
    if not isinstance(max_evidence_per_slice, int) or max_evidence_per_slice < 1:
        raise ValueError("agent max evidence per slice must be positive")
    if not index["evidence"]:
        return [index_slice(index, [], 1)]
    slices = []

    def append_partition(entries):
        """Append a bounded partition, splitting only when its serialized form is large."""
        candidate = index_slice(index, entries, len(slices) + 1)
        encoded_size = len(json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode())
        if len(entries) <= max_evidence_per_slice and encoded_size <= max_bytes:
            slices.append(candidate)
            return
        if len(entries) == 1:
            raise ValueError("agent evidence slice exceeds max-slice-context-bytes")
        midpoint = len(entries) // 2
        append_partition(entries[:midpoint])
        append_partition(entries[midpoint:])

    for start in range(0, len(index["evidence"]), max_evidence_per_slice):
        append_partition(index["evidence"][start : start + max_evidence_per_slice])
    return slices


def slice_packet(slice_index_value, prior, iteration, max_bytes):
    """Build and size-check one full-dataset slice request."""
    packet = {
        "iteration": iteration,
        "skill": AGENT_INSTRUCTIONS,
        "analysis_scope": "complete_dataset_partition; all source evidence remains in the retained index",
        "index": slice_index_value,
        "prior": prior,
    }
    if len(json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()) > max_bytes:
        raise ValueError("full dataset agent slice packet exceeds max-context-bytes")
    return packet


def empty_reduction():
    """Return the stable empty reduction shape used for partial runs."""
    return {
        "item_updates": [],
        "handwriting_comments": [],
        "hypotheses": [],
        "process_findings": [],
        "next_action": "abstain",
        "confidence": None,
        "all_review_items_retained": True,
        "production_approval_permitted": False,
    }


def merge_reductions(target, reduction):
    """Combine independent slice proposals without treating them as approvals."""
    for key in ("item_updates", "handwriting_comments", "hypotheses", "process_findings"):
        target[key].extend(reduction.get(key, []))
    target["next_action"] = reduction.get("next_action", target["next_action"])
    if reduction.get("confidence") is not None:
        target["confidence"] = reduction["confidence"]
    return target


def valid_update(update, valid_refs, evidence_refs=(), expected_field=None):
    """Accept only an evidence-linked proposal with a complete update."""
    if not isinstance(update, dict):
        return False
    required = ("field", "original_value", "proposed_value", "update_type", "evidence", "rationale")
    if not all(isinstance(update.get(key), str) and update[key].strip() for key in required):
        return False
    if update.get("update_type") not in {"correction", "completion", "normalization"}:
        return False
    if expected_field and update.get("field") != expected_field:
        return False
    return isinstance(evidence_refs, list) and any(ref in valid_refs for ref in evidence_refs)


def reduce_decision(decision, index):
    """Apply safety rules while retaining all agent output as proposals."""
    refs = {entry["ref"] for entry in index["evidence"]}
    item_ids = set(review_item_ids(index))
    item_fields = {
        str(entry["review_item_id"]): entry.get("field")
        for entry in index["evidence"]
        if entry.get("review_item_id") and entry.get("field")
    }
    updates, handwriting_comments, hypotheses, process_findings = [], [], [], []
    for update in decision.get("item_updates", []):
        entry = dict(update)
        entry["agent_auto_accept_status"] = "disabled"
        entry["primary_decision_permitted"] = False
        if entry.get("review_item_id") not in item_ids:
            entry["reducer_status"] = "unknown_review_item"
        elif any(word in str(entry.get("rationale", "")).casefold() for word in HANDWRITING_WORDS):
            entry["reducer_status"] = "handwriting_secondary_only"
        elif entry.get("decision") == "propose_resolution" and valid_update(
            entry.get("proposed_update"),
            refs,
            entry.get("evidence_refs"),
            item_fields.get(str(entry.get("review_item_id"))),
        ):
            entry["reducer_status"] = "proposal_requires_existing_review_policy"
        else:
            entry["reducer_status"] = "retained_review"
        updates.append(entry)
    for comment in decision.get("handwriting_comments", []):
        entry = dict(comment)
        entry["relevance"] = "secondary_context_only"
        entry["primary_decision_permitted"] = False
        entry["blocking"] = False
        handwriting_comments.append(entry)
    for hypothesis in decision.get("hypotheses", []):
        entry = dict(hypothesis)
        entry["proposal_only"] = True
        hypotheses.append(entry)
    for finding in decision.get("process_findings", []):
        entry = dict(finding)
        entry["proposal_only"] = True
        process_findings.append(entry)
    return {
        "item_updates": updates,
        "handwriting_comments": handwriting_comments,
        "hypotheses": hypotheses,
        "process_findings": process_findings,
        "next_action": decision.get("next_action", "abstain"),
        "confidence": decision.get("confidence"),
        "all_review_items_retained": True,
        "production_approval_permitted": False,
    }


def stable_signature(reduction):
    """Create a deterministic convergence signature for iterative passes."""
    return hashlib.sha256(
        json.dumps(reduction, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def request_agent(client, model, reasoning_effort, packet):
    """Ask either OpenAI Responses or the Vertex compatibility client."""
    return client.responses.create(
        model=model,
        reasoning={"effort": reasoning_effort},
        instructions=AGENT_INSTRUCTIONS,
        input=[{"role": "user", "content": [{"type": "input_text", "text": json.dumps(packet)}]}],
        text={
            "format": {
                "type": "json_schema",
                "name": "business_document_full_dataset_review_agent",
                "strict": True,
                "schema": DECISION_SCHEMA,
            }
        },
    )


def run_agent(
    context_paths,
    out_path,
    exceptions_path,
    raw_dir,
    model,
    client,
    *,
    max_iterations=3,
    workers=1,
    max_context_bytes=8000000,
    max_slice_context_bytes=120000,
    max_evidence_per_slice=500,
    reasoning_effort="medium",
    max_retries=4,
    retry_backoff_seconds=1.0,
    max_backoff_seconds=120.0,
    provider=None,
):
    """Run global dataset reasoning with bounded convergence and fail-closed output."""
    if not context_paths:
        raise ValueError("at least one context artifact is required")
    if not isinstance(max_iterations, int) or max_iterations < 1:
        raise ValueError("max iterations must be positive")
    if reasoning_effort not in MAX_REASONING_EFFORTS:
        raise ValueError("unsupported reasoning effort")
    if max_context_bytes < 1:
        raise ValueError("max context bytes must be positive")
    if max_slice_context_bytes < 1:
        raise ValueError("max slice context bytes must be positive")
    index = build_evidence_index(context_paths)
    out_path = empty_output_path(out_path)
    exceptions_path = empty_output_path(exceptions_path)
    raw_dir = empty_output_directory(raw_dir)
    slice_limit = min(max_context_bytes, max_slice_context_bytes)
    slices = analysis_slices(index, slice_limit, max_evidence_per_slice)
    iterations = []
    exceptions = []
    aggregate = empty_reduction()

    def review_slice(entry):
        """Run one slice's iterations and return them without merging shared state."""
        slice_number, slice_index_value = entry
        prior = {}
        signatures = set()
        slice_iterations, slice_exceptions, slice_reductions = [], [], []
        for iteration in range(
            1, max_iterations + 1
        ):  # pragma: no branch - max_iterations is validated positive
            retry_log = []
            try:
                packet = slice_packet(slice_index_value, prior, iteration, slice_limit)
                response = retry_call(
                    lambda packet=packet: request_agent(client, model, reasoning_effort, packet),
                    max_retries,
                    retry_backoff_seconds,
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
                    provider=provider or "client_review",
                    request_tokens=estimate_tokens(packet),
                )
                decision = response_json(response)
                reduction = reduce_decision(decision, slice_index_value)
                slice_reductions.append(reduction)
                signature = stable_signature(reduction)
                raw_path = raw_dir / f"slice-{slice_number:04d}-iteration-{iteration:03d}.json"
                raw_path.write_text(
                    json.dumps(
                        {"request": packet, "response": response_payload(response)},
                        indent=2,
                        default=str,
                    )
                    + "\n"
                )
                iteration_result = {
                    "slice": slice_number,
                    "iteration": iteration,
                    "raw_response": str(raw_path),
                    "retry_log": retry_log,
                    "decision": decision,
                    "reduction": reduction,
                    "signature": signature,
                }
                slice_iterations.append(iteration_result)
                if signature in signatures or decision.get("next_action") in {
                    "stabilized",
                    "abstain",
                }:
                    break
                signatures.add(signature)
                prior = reduction
            except Exception as exc:
                slice_exceptions.append(
                    {
                        "slice": slice_number,
                        "iteration": iteration,
                        "reason": "full_dataset_agent_provider_or_schema_failure",
                        "error_type": type(exc).__name__,
                        "retry_attempts": len(retry_log),
                        "retry_log": retry_log,
                        "disposition": "client_review_required",
                    }
                )
                break
        return {
            "iterations": slice_iterations,
            "exceptions": slice_exceptions,
            "reductions": slice_reductions,
        }

    # Slices are independent, but `merge_reductions` is not commutative -- it
    # extends lists and overwrites next_action/confidence with whatever merged
    # last. So each slice collects its own reductions and they are merged here in
    # slice order, which is the order a sequential run would have merged them.
    for outcome in parallel_map(list(enumerate(slices, 1)), review_slice, workers):
        for reduction in outcome["reductions"]:
            merge_reductions(aggregate, reduction)
        iterations.extend(outcome["iterations"])
        exceptions.extend(outcome["exceptions"])
    final = aggregate if iterations else reduce_decision({}, index)
    summary = {
        "schema_version": AGENT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "agent_provider": provider,
        "agent_model": model,
        "iterations": len(iterations),
        "analysis_status": (
            "failed" if not iterations and exceptions else "partial" if exceptions else "completed"
        ),
        "max_iterations": max_iterations,
        "slices": len(slices),
        "successful_slices": len({item["slice"] for item in iterations}),
        "failed_slices": len({item["slice"] for item in exceptions}),
        "indexed_artifacts": len(index["artifacts"]),
        "indexed_evidence_objects": len(index["evidence"]),
        "cross_record_signals": len(index["cross_record_signals"]),
        "contradictions": len(index["contradictions"]),
        "handwriting_comments": len(final["handwriting_comments"]),
        "process_findings": len(final["process_findings"]),
        "provider_exceptions": len(exceptions),
        "partial_results_retained": True,
        "request_limits": {
            "max_context_bytes": max_context_bytes,
            "max_slice_context_bytes": max_slice_context_bytes,
            "max_evidence_per_slice": max_evidence_per_slice,
        },
        "all_data_inventory_retained": True,
        "all_source_values_retained": True,
        "handwriting_policy": "secondary_context_only_nonblocking_never_primary",
        "production_approval_permitted": False,
        "gate_status": "blocked_pending_client_review",
        "retry_policy": {
            "max_retries": max_retries,
            "backoff_seconds": retry_backoff_seconds,
            "max_backoff_seconds": max_backoff_seconds,
            "jitter": "full_uniform",
        },
        "retry_telemetry": {
            "retry_attempts": sum(len(item.get("retry_log", [])) for item in iterations),
            "rate_limit_attempts": sum(
                1
                for item in iterations
                for event in item.get("retry_log", [])
                if event.get("status_code") == 429 or event.get("error_type") == "RateLimitError"
            ),
            "total_retry_delay_seconds": sum(
                float(event.get("delay_seconds") or 0)
                for item in iterations
                for event in item.get("retry_log", [])
            ),
            "exhausted_iterations": len(exceptions),
        },
    }
    out_path.write_text(
        json.dumps(
            {
                "summary": summary,
                "index": index,
                "iterations": iterations,
                "final_reduction": final,
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
    """CLI entrypoint for the full-dataset agent."""
    parser = argparse.ArgumentParser(description="Run the proposal-only full-dataset review agent.")
    parser.add_argument("--context", action="append", default=[])
    parser.add_argument("--out", required=True)
    parser.add_argument("--exceptions", required=True)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument(
        "--final-provider", choices=("openai", "google", "openrouter", "anthropic"), default=None
    )
    parser.add_argument("--final-model", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--iterations", type=int, default=None)
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="slices analysed concurrently; output order is unchanged by this setting",
    )
    parser.add_argument("--max-context-bytes", type=int, default=None)
    parser.add_argument(
        "--max-slice-context-bytes",
        type=int,
        default=None,
        help="Maximum UTF-8 bytes of context in one analysis slice.",
    )
    parser.add_argument(
        "--max-evidence-per-slice",
        type=int,
        default=None,
        help="Maximum evidence entries included in one analysis slice.",
    )
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument("--retry-backoff-seconds", type=float, default=None)
    parser.add_argument("--max-backoff-seconds", type=float, default=None)
    parser.add_argument("--reasoning-effort", choices=MAX_REASONING_EFFORTS, default=None)
    apply_shared_help(parser)
    args = parser.parse_args()
    load_project_env()
    enabled = args.enable or env_bool("CLIENT_REVIEW_LLM_ENABLED", False)
    if not enabled:
        empty_output_path(args.out).write_text(
            json.dumps(
                {"summary": {"enabled": False, "analysis_status": "disabled"}, "iterations": []},
                indent=2,
            )
            + "\n"
        )
        empty_output_path(args.exceptions).write_text(
            json.dumps({"summary": {"count": 0}, "exceptions": []}, indent=2) + "\n"
        )
        empty_output_directory(args.raw_dir)
        print("Full-dataset review agent disabled")
        return
    provider, model = resolve_reviewer_configuration(
        args.final_provider, args.final_model, args.model
    )
    client = build_reviewer_client(
        provider,
        env_value("CLIENT_REVIEW_LLM_CREDENTIAL_ENV", provider_credential_env(provider)),
        120.0,
        args.max_retries if args.max_retries is not None else 4,
    )
    summary = run_agent(
        args.context,
        args.out,
        args.exceptions,
        args.raw_dir,
        model,
        client,
        max_iterations=args.iterations or env_int("CLIENT_REVIEW_LLM_AGENT_ITERATIONS", 3),
        workers=args.workers or env_int("CLIENT_REVIEW_LLM_AGENT_MAX_WORKERS", 8),
        max_context_bytes=args.max_context_bytes
        or env_int("CLIENT_REVIEW_LLM_AGENT_MAX_CONTEXT_BYTES", 8000000),
        max_slice_context_bytes=args.max_slice_context_bytes
        or env_int("CLIENT_REVIEW_LLM_AGENT_MAX_SLICE_CONTEXT_BYTES", 120000),
        max_evidence_per_slice=args.max_evidence_per_slice
        or env_int("CLIENT_REVIEW_LLM_AGENT_MAX_EVIDENCE_PER_SLICE", 500),
        reasoning_effort=args.reasoning_effort
        or env_value("CLIENT_REVIEW_LLM_REASONING_EFFORT", "medium"),
        max_retries=args.max_retries
        if args.max_retries is not None
        else env_int("CLIENT_REVIEW_LLM_MAX_RETRIES", 4),
        retry_backoff_seconds=args.retry_backoff_seconds
        if args.retry_backoff_seconds is not None
        else env_float("CLIENT_REVIEW_LLM_RETRY_BACKOFF_SECONDS", 1.0),
        max_backoff_seconds=args.max_backoff_seconds
        if args.max_backoff_seconds is not None
        else env_float("CLIENT_REVIEW_LLM_MAX_BACKOFF_SECONDS", 120.0),
        provider=provider,
    )
    print(
        f"Full-dataset review agent iterations: {summary['iterations']}; evidence objects: {summary['indexed_evidence_objects']}"
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Full-dataset review agent failed: {exc}")
