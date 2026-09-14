#!/usr/bin/env python3
"""Create explicitly non-authoritative AI simulated-client review comments.

The command receives a final-review queue and optional hash-bound context,
retains one raw response per card, and writes a companion JSON/CSV/XLSX comment
package.  It is decision support only: it cannot impersonate a client, apply a
decision, clear a protected finding, or permit canonical/CRM staging.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

import xlsxwriter
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

from client_review.llm import build_reviewer_client
from client_review.protection import protection_reasons

LABEL_DEFAULT = "AI client reviewed (simulated; not client authorization)"
DECISIONS = (
    "propose_accept_as_simulated_client",
    "propose_reject_as_simulated_client",
    "needs_human_review",
    "abstain",
)
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["card_id", "rationale", "item_decisions"],
    "properties": {
        "card_id": {"type": "string"},
        "rationale": {"type": "string"},
        "item_decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "item_id",
                    "recommendation",
                    "confidence",
                    "rationale",
                    "evidence_quote",
                    "evidence_refs",
                ],
                "properties": {
                    "item_id": {"type": "string"},
                    "recommendation": {"type": "string", "enum": list(DECISIONS)},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}
INSTRUCTIONS = """You are an AI simulated-client reviewer. You are not the client and
cannot authorize any fact, approval, or release. Review only the supplied card,
review items, and hash-bound context. For every item, return a practical
recommendation, a concise rationale, an exact evidence quote visible in the
packet, and evidence references. Do not invent evidence or claim a control is
cleared. Use needs_human_review or abstain whenever the supplied evidence is
not enough. A simulated accept/reject is a proposal for a real client or
operator to review, never actual authorization."""
REASONING_EFFORTS = {"low", "medium", "high"}
PROVIDERS = {"openai", "google", "openrouter", "anthropic"}
RETRYABLE_EXCEPTION_REASONS = {
    "simulated_review_provider_or_schema_failure",
    "simulated_review_card_too_large",
    "simulated_review_card_cap_reached",
    "source_evidence_missing",
    "simulated_review_evidence_audit_failure",
}


def validate_config(provider, model, effort, retries, backoff, max_backoff):
    """Reject unsafe provider settings before creating output or making a call."""
    if provider not in PROVIDERS:
        raise ValueError("provider must be one of: " + ", ".join(sorted(PROVIDERS)))
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be non-empty")
    if effort not in REASONING_EFFORTS:
        raise ValueError("reasoning effort must be low, medium, or high")
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise ValueError("retries must be a non-negative integer")
    if backoff < 0 or max_backoff < 0 or max_backoff < backoff:
        raise ValueError("retry backoff limits are invalid")


def validate_run_limits(
    max_cards, max_context_bytes, max_context_artifact_bytes, context_reserve_bytes, timeout=None
):
    """Reject review limits that are negative, zero, or mutually inconsistent."""
    if (
        max_cards < 0
        or max_context_bytes < 1
        or max_context_artifact_bytes < 1
        or context_reserve_bytes < 0
        or context_reserve_bytes >= max_context_bytes
        or (timeout is not None and timeout <= 0)
    ):
        raise ValueError("review limits are invalid")


def load_json(path):
    """Load a JSON value from a retained artifact."""
    return json.loads(Path(path).read_text())


def load_queue(path):
    """Load the final-review object and preserve its original queue items."""
    queue = load_json(path)
    if not isinstance(queue, dict) or not isinstance(queue.get("items"), list):
        raise ValueError("final review must contain an items list")
    if not isinstance(queue.get("review_cards"), list):
        raise ValueError("final review must contain a review_cards list")
    if any(not isinstance(item, dict) for item in queue["items"]):
        raise ValueError("final-review items must be objects")
    if any(not isinstance(card, dict) for card in queue["review_cards"]):
        raise ValueError("final-review cards must be objects")
    seen = set()
    for card in queue["review_cards"]:
        indexes = card.get("item_indexes")
        if not isinstance(indexes, list):
            continue
        for index in indexes:
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise ValueError("review card item_indexes must be non-negative integers")
            if index >= len(queue["items"]):
                continue
            if index in seen:
                raise ValueError("review item is assigned to more than one review card")
            seen.add(index)
    return queue


def retry_card_ids(path):
    """Select exact cards eligible for a separate no-clobber recovery overlay."""
    value = load_json(path)
    if not isinstance(value, dict) or not isinstance(value.get("exceptions"), list):
        raise ValueError("retry exception artifact must contain an exceptions list")
    return {
        item["card_id"]
        for item in value["exceptions"]
        if isinstance(item, dict)
        and item.get("reason") in RETRYABLE_EXCEPTION_REASONS
        and isinstance(item.get("card_id"), str)
        and item["card_id"]
    }


def item_id(index):
    """Return the deterministic ID used by final-review client views."""
    return f"review-item-{index}"


def card_items(queue, card):
    """Return card members with deterministic IDs or fail before provider I/O."""
    indexes = card.get("item_indexes")
    if not isinstance(indexes, list) or any(
        isinstance(index, bool) or not isinstance(index, int) or index < 0 for index in indexes
    ):
        raise ValueError("review card item_indexes must be non-negative integer list")
    if not indexes:
        raise ValueError("review card must contain at least one item")
    if len(set(indexes)) != len(indexes):
        raise ValueError("review card item_indexes must not repeat items")
    if any(index >= len(queue["items"]) for index in indexes):
        raise ValueError("review card references invalid item index")
    return [{"item_id": item_id(index), "item": queue["items"][index]} for index in indexes]


def _records(value):
    """Extract source records from common retained artifact shapes."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("documents", "records", "results", "pages", "evidence", "items"):
            if isinstance(value.get(key), list):
                return value[key]
    raise ValueError("source evidence must be a list or contain a recognized record list")


def load_source_evidence(paths, artifact_limit):
    """Load immutable source records and assign stable artifact-scoped references."""
    if artifact_limit < 1:
        raise ValueError("source evidence artifact limit must be positive")
    evidence, metadata = [], []
    for path in paths:
        raw = Path(path).read_bytes()
        if len(raw) > artifact_limit:
            raise ValueError(f"source evidence artifact exceeds byte limit: {path}")
        records = _records(json.loads(raw))
        digest = hashlib.sha256(raw).hexdigest()
        metadata.append(
            {"artifact": Path(path).name, "sha256": digest, "record_count": len(records)}
        )
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise ValueError(f"source evidence record {index} in {path} is not an object")
            evidence.append(
                {
                    "ref": f"source:{digest}:{index}",
                    "artifact": Path(path).name,
                    "record_index": index,
                    "record": record,
                }
            )
    return metadata, evidence


def evidence_for_item(item, evidence):
    """Return source records matching an item, preferring document and page identity."""
    document_id, page_id = item.get("document_id"), item.get("page_id")
    exact = [
        entry
        for entry in evidence
        if entry["record"].get("document_id") == document_id
        and page_id is not None
        and entry["record"].get("page_id") == page_id
    ]
    if exact:
        return exact
    by_document = [entry for entry in evidence if entry["record"].get("document_id") == document_id]
    if by_document:
        return by_document
    return [
        entry
        for entry in evidence
        if page_id is not None and entry["record"].get("page_id") == page_id
    ]


def source_text(evidence):
    """Build searchable source text while excluding provenance/control metadata."""
    excluded = {"ref", "artifact", "record_index", "source_evidence", "review_item_id"}

    def values(value):
        """Yield every scalar value in a record, for source-quote verification."""
        if isinstance(value, dict):
            for key, child in value.items():
                if key not in excluded:
                    yield from values(child)
        elif isinstance(value, list):
            for child in value:
                yield from values(child)
        elif value is not None:
            yield str(value)

    return normalize(" ".join(values(evidence)))


def context_summary(paths, artifact_limit, packet_limit):
    """Hash and byte-bound context without treating it as source evidence."""
    if artifact_limit < 1 or packet_limit < 1:
        raise ValueError("context limits must be positive")
    context, used = [], 0
    for path in paths:
        raw = Path(path).read_bytes()
        if len(raw) > artifact_limit:
            raise ValueError(f"context artifact exceeds byte limit: {path}")
        value = json.loads(raw)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        remaining = packet_limit - used
        encoded_bytes = encoded.encode("utf-8")
        clipped_bytes = encoded_bytes[: max(0, remaining)]
        while clipped_bytes:
            try:
                clipped = clipped_bytes.decode("utf-8")
                break
            except UnicodeDecodeError:
                clipped_bytes = clipped_bytes[:-1]
        else:
            clipped = ""
        context.append(
            {
                "artifact": Path(path).name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "reasoning_only": True,
                "json": clipped,
                "included": bool(clipped),
                "omitted_reason": "packet_context_cap_reached" if not clipped else None,
                "truncated": len(clipped_bytes) < len(encoded_bytes),
            }
        )
        used += len(clipped_bytes)
    return context


def packet(card, members, context):
    """Create the exact provider packet; context is informative, never proof."""
    evidence_by_ref = {}
    for entry in members:
        for source in entry.get("source_evidence", []):
            evidence_by_ref.setdefault(source["ref"], source)
    return {
        "lane": "ai_simulated_client_review",
        "authorization_boundary": {
            "simulation_only": True,
            "client_authorization": False,
            "production_approval_permitted": False,
        },
        "card": card,
        "source_evidence": list(evidence_by_ref.values()),
        "review_items": [
            {
                "item_id": entry["item_id"],
                **entry["item"],
                "source_evidence_refs": [
                    source["ref"] for source in entry.get("source_evidence", [])
                ],
            }
            for entry in members
        ],
        "context": [entry for entry in context if entry["included"]],
    }


def largest_item_packet(queue, evidence):
    """Return the bytes the biggest single review item needs, context aside.

    Context is informative and the card is the payload, but the context was
    clipped to the whole ceiling less a fixed 16000-byte reserve and then added
    to every packet. On a real 18-page corpus that reserve was smaller than any
    single card, so 18 of 20 cards overflowed before a request was made: the
    ceiling read as generous and nothing fitted underneath it.

    Reserving what one item actually costs guarantees every packet can carry at
    least one item, which is what lets a split make progress instead of
    retaining the whole card as unreviewable.
    """
    largest = 0
    for card in queue["review_cards"]:
        try:
            members = card_items(queue, card)
        except ValueError:
            # An unreadable card is reported by the run loop, which retains it
            # as an exception; sizing must not fail before that happens.
            continue
        for member in members:
            sized = {**member, "source_evidence": evidence_for_item(member["item"], evidence)}
            largest = max(largest, len(json.dumps(packet(card, [sized], [])).encode()))
    return largest


def card_parts(card, members, context, max_context_bytes):
    """Split one card into packets that each fit the provider byte budget.

    A card is a document's worth of findings and its source evidence, so a
    busy document can exceed any single-request budget. Discarding the whole
    card then loses every finding on that document, including the ones that
    would have fitted -- on a real 18-page corpus 18 of 20 cards were discarded
    this way and the lane returned no comment at all.

    CLAUDE.md requires the shared byte-adaptive packet utility here: pack whole
    members up to the exact budget, and retain only a member that cannot fit
    even alone as an explicit exception. Every packet still carries the same
    card and the same context, so each one is independently source-bound and
    the card_id check downstream is unchanged.
    """

    def packet_bytes(candidate):
        return max_context_bytes - len(json.dumps(packet(card, candidate, context)).encode())

    return adaptive_byte_batches(members, packet_bytes, max(1, len(members)))


def normalize(value):
    """Casefold and collapse whitespace for comparison only.

    The normalized form is never retained in place of the value: it exists to
    compare two readings, not to replace either.
    """
    return " ".join(str(value or "").casefold().split())


def _pointer_token(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def source_scalar_locations(value, pointer=""):
    """Yield JSON pointers and scalar text without joining unrelated fields."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield from source_scalar_locations(child, f"{pointer}/{_pointer_token(key)}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from source_scalar_locations(child, f"{pointer}/{index}")
    elif value is not None:
        yield pointer or "/", normalize(value)


def item_evidence_validation(decision, evidence_by_item):
    """Bind a quote and every reference to the decision's own source records."""
    item_sources = evidence_by_item.get(decision.get("item_id"), [])
    sources_by_ref = {source["ref"]: source for source in item_sources}
    refs = decision.get("evidence_refs")
    if any(ref not in sources_by_ref for ref in refs or []):
        return "decision_evidence_ref_outside_item", []
    quote = normalize(decision.get("evidence_quote"))
    locations = []
    for ref in refs or []:
        for pointer, scalar in source_scalar_locations(sources_by_ref[ref]["record"]):
            if quote and quote in scalar:
                locations.append({"evidence_ref": ref, "json_pointer": pointer})
    if not locations:
        return "decision_evidence_quote_not_in_cited_source", []
    return None, locations


def rejection_reason(
    decision, allowed_ids, searchable_text=None, allowed_refs=None, evidence_by_item=None
):
    """Validate an item comment against the exact source-bound packet."""
    if not isinstance(decision, dict):
        return "decision_not_object"
    if decision.get("item_id") not in allowed_ids:
        return "decision_item_outside_card"
    if decision.get("recommendation") not in DECISIONS:
        return "decision_recommendation_invalid"
    confidence = decision.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        return "decision_confidence_invalid"
    if not normalize(decision.get("rationale")):
        return "decision_rationale_missing"
    quote = normalize(decision.get("evidence_quote"))
    if len(quote) < 2 or quote not in searchable_text:
        return "decision_evidence_quote_not_in_packet"
    refs = decision.get("evidence_refs")
    if (
        not isinstance(refs, list)
        or not refs
        or any(not isinstance(ref, str) or not ref.strip() for ref in refs)
    ):
        return "decision_evidence_refs_invalid"
    if any(ref not in allowed_refs for ref in refs):
        return "decision_evidence_ref_outside_packet"
    if evidence_by_item is not None:
        reason, _ = item_evidence_validation(decision, evidence_by_item)
        if reason:
            return reason
    return None


def comment_text(label, recommendation, rationale, quote):
    """Build the visible reviewer comment with a non-authority warning."""
    return f"{label}. Proposed simulated decision: {recommendation}. {rationale} Evidence: {quote}"


def reduce_card(members, model_decisions, label, raw_sha256):
    """Retain one comment for every card item, preserving invalid output separately."""
    allowed = {entry["item_id"] for entry in members}
    source_records = [record for entry in members for record in entry["source_evidence"]]
    searchable_text = source_text(source_records)
    allowed_refs = {record["ref"] for record in source_records}
    evidence_by_item = {entry["item_id"]: entry["source_evidence"] for entry in members}
    if not isinstance(model_decisions, list):
        raise ValueError("provider item_decisions must be a list")
    model_by_item, duplicate_ids, provided_ids, rejected = {}, set(), set(), []
    for decision in model_decisions:
        reason = rejection_reason(
            decision,
            allowed,
            searchable_text,
            allowed_refs,
            evidence_by_item=evidence_by_item,
        )
        if reason:
            if isinstance(decision, dict) and decision.get("item_id") in allowed:
                provided_ids.add(decision["item_id"])
            rejected.append(
                {
                    "review_item_id": decision.get("item_id")
                    if isinstance(decision, dict)
                    else None,
                    "reason": reason,
                    "decision": decision,
                }
            )
            continue
        if decision["item_id"] in model_by_item:
            duplicate_ids.add(decision["item_id"])
            rejected.append(
                {
                    "review_item_id": decision["item_id"],
                    "reason": "duplicate_model_decision",
                    "decision": decision,
                }
            )
            continue
        model_by_item[decision["item_id"]] = decision
    comments = []
    for entry in members:
        original = entry["item"]
        decision = model_by_item.get(entry["item_id"])
        reason = (
            "duplicate_model_decision"
            if entry["item_id"] in duplicate_ids
            else (
                None
                if decision
                else (
                    "invalid_model_decision"
                    if entry["item_id"] in provided_ids
                    else "decision_missing"
                )
            )
        )
        if reason:
            if reason != "invalid_model_decision":
                rejected.append(
                    {"review_item_id": entry["item_id"], "reason": reason, "decision": decision}
                )
            decision = {
                "recommendation": "needs_human_review",
                "confidence": None,
                "rationale": "No valid AI simulated-client decision was retained.",
                "evidence_quote": "",
                "evidence_refs": [],
            }
            status = (
                "duplicate_model_decision"
                if reason == "duplicate_model_decision"
                else "invalid_or_missing_model_decision"
            )
        else:
            status = "simulated_client_proposal"
        _, evidence_locations = (
            item_evidence_validation(decision, evidence_by_item) if not reason else (reason, [])
        )
        reasons = protection_reasons(original)
        recommendation = decision["recommendation"]
        if reasons:
            status = "protected_review_required"
            recommendation = "needs_human_review"
        comments.append(
            {
                "review_item_id": entry["item_id"],
                "document_id": original.get("document_id"),
                "page_id": original.get("page_id"),
                "field": original.get("field"),
                "reason": original.get("reason") or original.get("cause"),
                "ai_review_label": label,
                "simulated_client_recommendation": recommendation,
                "model_recommendation": decision["recommendation"],
                "confidence": decision["confidence"],
                "rationale": decision["rationale"],
                "evidence_quote": decision["evidence_quote"],
                "evidence_refs": decision["evidence_refs"],
                "evidence_locations": evidence_locations,
                "protection_reasons": list(reasons),
                "simulation_status": status,
                "client_authorization": False,
                "production_approval_permitted": False,
                "raw_response_sha256": raw_sha256,
                "comment": comment_text(
                    label, recommendation, decision["rationale"], decision["evidence_quote"]
                ),
            }
        )
    return comments, rejected


def request(client, provider, model, effort, body, retries, backoff, max_backoff):
    """Use the shared bounded retry/circuit/throttle runtime."""
    return retry_call(
        lambda: client.responses.create(
            model=model,
            reasoning={"effort": effort},
            instructions=INSTRUCTIONS,
            input=[{"role": "user", "content": [{"type": "input_text", "text": json.dumps(body)}]}],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "ai_simulated_client_review",
                    "strict": True,
                    "schema": SCHEMA,
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


def raw_name(index, card, part_number=1, part_count=1):
    """Return the file name for one card's retained raw response."""
    label = "".join(
        char if char.isalnum() or char in "_.-" else "_"
        for char in str(card.get("card_id", "card"))
    )
    # An unsplit card keeps the name it has always had; only a card that needed
    # more than one packet grows a part suffix, so each retained raw response
    # still maps to exactly one request.
    suffix = f".part{part_number:02d}" if part_count > 1 else ""
    return f"{index:06d}_{label.strip('_') or 'card'}{suffix}.json"


def atomic_json_write(path, value):
    """Atomically replace resumable state without weakening output no-clobber rules."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def configuration_record(
    provider,
    model,
    effort,
    retries,
    backoff,
    max_backoff,
    max_cards,
    max_context_bytes,
    max_context_artifact_bytes,
    context_reserve_bytes,
    extra,
):
    """Return the complete non-secret resolved configuration for reproducibility."""
    record = {
        "provider": provider,
        "model": model,
        "reasoning_effort": effort,
        "max_retries": retries,
        "retry_backoff_seconds": backoff,
        "max_backoff_seconds": max_backoff,
        "max_cards": max_cards,
        "max_context_bytes": max_context_bytes,
        "max_context_artifact_bytes": max_context_artifact_bytes,
        "context_reserve_bytes": context_reserve_bytes,
    }
    record.update(extra)
    return record


def checkpoint_identity(queue_path, evidence_metadata, context_metadata, configuration):
    """Bind resumable state to immutable input and resolved configuration."""
    return {
        "final_review_sha256": hashlib.sha256(Path(queue_path).read_bytes()).hexdigest(),
        "source_evidence": evidence_metadata,
        "context": [
            {key: value for key, value in item.items() if key != "json"}
            for item in context_metadata
        ],
        "configuration_sha256": hashlib.sha256(
            json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def checkpoint_payload(
    identity, completed, comments, rejected, exceptions, batches, status, completed_parts=()
):
    """Build the atomic checkpoint binding the queue, evidence, and resolved settings."""
    return {
        "artifact_type": "ai_simulated_client_review_checkpoint_v1",
        **identity,
        "status": status,
        "completed_card_indexes": sorted(completed),
        # A card that needs several packets can be interrupted between them, so
        # completion is also recorded per packet. Without it a resume re-sends a
        # packet whose raw response is already retained, and overwrites it.
        "completed_packet_parts": sorted(completed_parts),
        "comments": comments,
        "rejected_model_decisions": rejected,
        "exceptions": exceptions,
        "batches": batches,
    }


def write_views(comments, csv_path, xlsx_path):
    """Write reviewer views from the authoritative JSON comment rows."""

    def safe_cell(value):
        value = json.dumps(value) if isinstance(value, list) else value
        if isinstance(value, str) and value[:1] in "=+-@":
            return "'" + value
        return value

    columns = [
        "review_item_id",
        "document_id",
        "page_id",
        "field",
        "reason",
        "ai_review_label",
        "simulated_client_recommendation",
        "model_recommendation",
        "confidence",
        "simulation_status",
        "comment",
        "evidence_quote",
        "evidence_refs",
        "evidence_locations",
        "protection_reasons",
        "raw_response_sha256",
        "client_authorization",
        "production_approval_permitted",
    ]
    csv_path, xlsx_path = Path(csv_path), Path(xlsx_path)
    csv_handle = tempfile.NamedTemporaryFile(
        mode="w", newline="", encoding="utf-8", dir=csv_path.parent, delete=False
    )
    csv_temp = Path(csv_handle.name)
    xlsx_handle = tempfile.NamedTemporaryFile(dir=xlsx_path.parent, suffix=".xlsx", delete=False)
    xlsx_temp = Path(xlsx_handle.name)
    xlsx_handle.close()
    try:
        with csv_handle as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for comment in comments:
                writer.writerow({key: safe_cell(comment.get(key)) for key in columns})
        workbook = xlsxwriter.Workbook(
            str(xlsx_temp), {"strings_to_formulas": False, "strings_to_urls": False}
        )
        sheet = workbook.add_worksheet("AI Simulated Comments")
        header = workbook.add_format({"bold": True, "bg_color": "#D9EAF7", "text_wrap": True})
        wrap = workbook.add_format({"text_wrap": True, "valign": "top"})
        for column, name in enumerate(columns):
            sheet.write(0, column, name, header)
            sheet.set_column(column, column, min(max(len(name) + 2, 14), 36))
        for row, comment in enumerate(comments, 1):
            for column, name in enumerate(columns):
                sheet.write(row, column, safe_cell(comment.get(name)), wrap)
            lengths = [len(str(safe_cell(comment.get(name)) or "")) for name in columns]
            sheet.set_row(row, min(390, max(72, 15 * math.ceil(max(lengths, default=1) / 70))))
        sheet.freeze_panes(1, 0)
        sheet.autofilter(0, 0, len(comments), len(columns) - 1)
        boundary = workbook.add_worksheet("Boundary")
        boundary.set_column(0, 0, 34)
        boundary.set_column(1, 1, 100)
        rows = [
            (
                "Status",
                "AI simulated-client review only; this workbook is not client authorization.",
            ),
            (
                "Does not do",
                "It does not approve facts, clear review items, authorize changes, or permit canonical/CRM staging.",
            ),
            (
                "Required next step",
                "A real client or authorized operator must review and explicitly authorize any separate append-only change.",
            ),
        ]
        for row, (key, value) in enumerate(rows):
            boundary.write(row, 0, key, header)
            boundary.write(row, 1, value, wrap)
            boundary.set_row(row, 36)
        workbook.close()
        os.replace(csv_temp, csv_path)
        os.replace(xlsx_temp, xlsx_path)
    finally:
        csv_temp.unlink(missing_ok=True)
        xlsx_temp.unlink(missing_ok=True)


def run(
    queue_path,
    context_paths,
    out_path,
    exceptions_path,
    raw_dir,
    csv_path,
    xlsx_path,
    client,
    *,
    provider,
    model,
    effort,
    retries,
    backoff,
    max_backoff,
    max_cards,
    max_context_bytes,
    max_context_artifact_bytes,
    label,
    evidence_paths=(),
    context_reserve_bytes=16000,
    resume=False,
    resolved_configuration=None,
    only_card_ids=None,
    workers=1,
):
    """Run the non-authoritative reviewer, retaining all comments and failures."""
    if not label or "not client authorization" not in label.casefold():
        raise ValueError("comment label must explicitly state not client authorization")
    if context_reserve_bytes >= max_context_bytes:
        context_reserve_bytes = max(0, max_context_bytes // 8)
    validate_run_limits(
        max_cards, max_context_bytes, max_context_artifact_bytes, context_reserve_bytes
    )
    validate_config(provider, model, effort, retries, backoff, max_backoff)
    reset_provider_quota_circuits()
    queue = load_queue(queue_path)
    evidence_metadata, evidence = load_source_evidence(evidence_paths, max_context_artifact_bytes)
    # The configured reserve is a floor, not the whole story: what the card side
    # of the packet actually needs is measured from this queue's own items.
    reserve = min(
        max(context_reserve_bytes, largest_item_packet(queue, evidence)), max_context_bytes - 1
    )
    context_budget = max_context_bytes - reserve
    context = context_summary(context_paths, max_context_artifact_bytes, context_budget)
    configuration = resolved_configuration or configuration_record(
        provider,
        model,
        effort,
        retries,
        backoff,
        max_backoff,
        max_cards,
        max_context_bytes,
        max_context_artifact_bytes,
        context_reserve_bytes,
        {"comment_label": label},
    )
    identity = checkpoint_identity(queue_path, evidence_metadata, context, configuration)
    cards = queue["review_cards"]
    scoped_card_ids = set(only_card_ids) if only_card_ids is not None else None
    queue_card_ids = {card.get("card_id") for card in cards if isinstance(card.get("card_id"), str)}
    if scoped_card_ids is not None and not scoped_card_ids <= queue_card_ids:
        raise ValueError("retry scope contains card IDs outside the final-review queue")
    outputs = [out_path, exceptions_path, csv_path, xlsx_path]
    targets = [empty_output_path(path) for path in outputs]
    out_path, exceptions_path, csv_path, xlsx_path = targets
    raw_dir = Path(raw_dir)
    checkpoint_path = raw_dir / "checkpoint.json"
    if resume:
        if not checkpoint_path.is_file():
            raise ValueError("resume requires a retained checkpoint.json")
        checkpoint = load_json(checkpoint_path)
        if any(checkpoint.get(key) != value for key, value in identity.items()):
            raise ValueError("resume checkpoint does not match input evidence and configuration")
        comments = list(checkpoint.get("comments", []))
        rejected = list(checkpoint.get("rejected_model_decisions", []))
        exceptions = list(checkpoint.get("exceptions", []))
        batches = list(checkpoint.get("batches", []))
        completed = set(checkpoint.get("completed_card_indexes", []))
        completed_parts = set(checkpoint.get("completed_packet_parts", []))
    else:
        raw_dir = empty_output_directory(raw_dir)
        comments, rejected, exceptions, batches, completed = [], [], [], [], set()
        completed_parts = set()
        atomic_json_write(
            checkpoint_path,
            checkpoint_payload(
                identity, completed, comments, rejected, exceptions, batches, "running"
            ),
        )

    state_lock = Lock()

    def save_checkpoint(status="running"):
        atomic_json_write(
            checkpoint_path,
            checkpoint_payload(
                identity,
                completed,
                comments,
                rejected,
                exceptions,
                batches,
                status,
                completed_parts,
            ),
        )

    def review_card(entry):
        """Review one card; every shared mutation is taken under the state lock.

        Shared state is what the resume checkpoint serialises, so it has to be
        written as work finishes. That order is completion order, which is not
        the order the artifact may record -- so the card's own results are also
        collected here and reassembled in card order once the map is done.
        """
        index, card = entry
        mine = {"comments": [], "rejected": [], "batches": [], "exceptions": []}
        if scoped_card_ids is not None and card.get("card_id") not in scoped_card_ids:
            return None
        if index in completed:
            return None
        try:
            members = card_items(queue, card)
        except ValueError as exc:
            failure = {"card_index": index, "reason": "invalid_review_card", "error": str(exc)}
            mine["exceptions"].append(failure)
            with state_lock:
                exceptions.append(failure)
                completed.add(index)
                save_checkpoint()
            return mine
        if max_cards and index >= max_cards:
            capped = {
                "card_id": card.get("card_id"),
                "reason": "simulated_review_card_cap_reached",
                "review_item_ids": [entry["item_id"] for entry in members],
            }
            mine["exceptions"].append(capped)
            with state_lock:
                exceptions.append(capped)
                completed.add(index)
                save_checkpoint()
            return mine
        for member in members:
            member["source_evidence"] = evidence_for_item(member["item"], evidence)
        missing_source = [entry["item_id"] for entry in members if not entry["source_evidence"]]
        if missing_source:
            exceptions.append(
                {
                    "card_id": card.get("card_id"),
                    "reason": "source_evidence_missing",
                    "review_item_ids": missing_source,
                }
            )
            members = [entry for entry in members if entry["item_id"] not in missing_source]
            if not members:
                with state_lock:
                    completed.add(index)
                    save_checkpoint()
                return mine
        parts, oversized, _ = card_parts(card, members, context, max_context_bytes)
        if oversized:
            oversize_failure = {
                "card_id": card.get("card_id"),
                "reason": "simulated_review_card_too_large",
                "review_item_ids": [entry["item_id"] for entry in oversized],
                "packet_parts": len(parts),
            }
            mine["exceptions"].append(oversize_failure)
            with state_lock:
                exceptions.append(oversize_failure)
        for part_number, part in enumerate(parts, start=1):
            part_key = f"{index}:{part_number}"
            if part_key in completed_parts:
                continue
            raw_path = None
            body = packet(card, part, context)
            try:
                response = request(
                    client, provider, model, effort, body, retries, backoff, max_backoff
                )
                raw_path = raw_dir / raw_name(index, card, part_number, len(parts))
                raw_payload = {"request": body, "response": response_payload(response)}
                raw_path.write_text(json.dumps(raw_payload, indent=2, default=str) + "\n")
                raw_sha256 = hashlib.sha256(raw_path.read_bytes()).hexdigest()
                result = response_json(response)
                if result.get("card_id") != card.get("card_id"):
                    raise ValueError("provider response card_id does not match packet")
                card_comments, card_rejected = reduce_card(
                    part, result.get("item_decisions", []), label, raw_sha256
                )
                batch_record = {
                    "card_id": card.get("card_id"),
                    "packet_part": part_number,
                    "packet_parts": len(parts),
                    "raw_response": raw_path.name,
                    "comments": len(card_comments),
                }
                mine["comments"].extend(card_comments)
                mine["rejected"].extend(card_rejected)
                mine["batches"].append(batch_record)
                with state_lock:
                    comments.extend(card_comments)
                    rejected.extend(card_rejected)
                    batches.append(batch_record)
            except Exception as exc:
                failure = {
                    "card_id": card.get("card_id"),
                    "reason": "simulated_review_provider_or_schema_failure",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                    "review_item_ids": [entry["item_id"] for entry in part],
                    "packet_part": part_number,
                    "packet_parts": len(parts),
                }
                if raw_path is not None and raw_path.exists():
                    failure["raw_response"] = raw_path.name
                    failure["raw_response_sha256"] = hashlib.sha256(
                        raw_path.read_bytes()
                    ).hexdigest()
                mine["exceptions"].append(failure)
                with state_lock:
                    exceptions.append(failure)
            # A part that has had its attempt is finished, whether it answered or
            # failed, exactly as a whole card used to be. Recording it before the
            # next request is what keeps a resumed run from re-sending a packet
            # whose raw response is already retained.
            with state_lock:
                completed_parts.add(part_key)
                save_checkpoint()
        with state_lock:
            completed.add(index)
            save_checkpoint()
        return mine

    # `parallel_map` preserves input order, and the lock keeps each checkpoint a
    # consistent snapshot: a card is only ever recorded complete together with the
    # comments it produced, so a resumed run never skips work it has not retained.
    # Anything already in these lists was restored from a checkpoint and belongs
    # ahead of whatever this pass produces.
    resumed = {
        "comments": list(comments),
        "rejected": list(rejected),
        "batches": list(batches),
        "exceptions": list(exceptions),
    }
    outcomes = parallel_map(list(enumerate(cards)), review_card, workers)
    # The lists were written in completion order so each checkpoint stayed a
    # consistent snapshot. The artifact must not record that order, so rebuild them
    # from the per-card outcomes, which `parallel_map` returns in card order.
    for name, collected in (
        ("comments", comments),
        ("rejected", rejected),
        ("batches", batches),
        ("exceptions", exceptions),
    ):
        collected[:] = resumed[name] + [
            item for outcome in outcomes if outcome is not None for item in outcome[name]
        ]
    accounted = {comment["review_item_id"] for comment in comments}
    excepted = {
        review_item_id
        for exception in exceptions
        for review_item_id in exception.get("review_item_ids", [])
    }
    scoped_item_indexes = (
        set(range(len(queue["items"])))
        if scoped_card_ids is None
        else {
            index
            for card in cards
            if card.get("card_id") in scoped_card_ids
            for index in card.get("item_indexes", [])
            if isinstance(index, int)
            and not isinstance(index, bool)
            and 0 <= index < len(queue["items"])
        }
    )
    missing = [
        item_id(index)
        for index in sorted(scoped_item_indexes)
        if item_id(index) not in accounted | excepted
    ]
    if missing:
        exceptions.append(
            {"reason": "simulated_review_items_not_covered_by_cards", "review_item_ids": missing}
        )
    output = {
        "artifact_type": "ai_simulated_client_review_v1",
        "run_scope": "retry_overlay" if scoped_card_ids is not None else "full_queue",
        "generated_at": datetime.now(UTC).isoformat(),
        "simulation_only": True,
        "client_authorization": False,
        "production_approval_permitted": False,
        "gate_status": "blocked_pending_actual_client_or_operator_review",
        "comment_label": label,
        "provider": provider,
        "model": model,
        "reasoning_effort": effort,
        "resolved_configuration": configuration,
        "configuration_sha256": identity["configuration_sha256"],
        "final_review_sha256": hashlib.sha256(Path(queue_path).read_bytes()).hexdigest(),
        "context": [
            {key: value for key, value in item.items() if key != "json"} for item in context
        ],
        "source_evidence": evidence_metadata,
        "summary": {
            "input_items": len(queue["items"]),
            "input_cards": len(cards),
            "scoped_cards": len(scoped_card_ids) if scoped_card_ids is not None else len(cards),
            "scoped_items": len(scoped_item_indexes),
            "comments": len(comments),
            "rejected_model_decisions": len(rejected),
            "exceptions": len(exceptions),
            "authorized_items": 0,
            "applied_changes": 0,
        },
        "comments": comments,
        "rejected_model_decisions": rejected,
        "batches": batches,
    }
    atomic_json_write(out_path, output)
    atomic_json_write(
        exceptions_path,
        {
            "artifact_type": "ai_simulated_client_review_exceptions_v1",
            "simulation_only": True,
            "exceptions": exceptions,
        },
    )
    write_views(comments, csv_path, xlsx_path)
    save_checkpoint("complete")
    return output


def disabled(out_path, exceptions_path, raw_dir, csv_path, xlsx_path):
    """Emit explicit no-call artifacts when the lane is disabled."""
    out_path, exceptions_path, csv_path, xlsx_path = [
        empty_output_path(path) for path in (out_path, exceptions_path, csv_path, xlsx_path)
    ]
    empty_output_directory(raw_dir)
    output = {
        "artifact_type": "ai_simulated_client_review_v1",
        "simulation_only": True,
        "summary": {"enabled": False},
        "comments": [],
    }
    out_path.write_text(json.dumps(output, indent=2) + "\n")
    exceptions_path.write_text(
        json.dumps(
            {"artifact_type": "ai_simulated_client_review_exceptions_v1", "exceptions": []},
            indent=2,
        )
        + "\n"
    )
    write_views([], csv_path, xlsx_path)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("final_review", help="final_client_review.json input")
    parser.add_argument(
        "--context", action="append", default=[], help="optional reasoning-only JSON artifact"
    )
    parser.add_argument(
        "--evidence",
        action="append",
        default=[],
        help="source evidence JSON artifact; required for provider-backed review",
    )
    parser.add_argument("--out", required=True, help="new authoritative comment JSON")
    parser.add_argument("--exceptions-out", required=True, help="new explicit exception JSON")
    parser.add_argument("--raw-dir", required=True, help="new raw provider response directory")
    parser.add_argument("--comments-csv", required=True, help="new companion CSV comment view")
    parser.add_argument("--comments-xlsx", required=True, help="new companion XLSX comment view")
    parser.add_argument("--enable", action="store_true", help="explicitly allow provider calls")
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="cards reviewed concurrently; output order is unchanged by this setting",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="resume an interrupted run from the hash-bound checkpoint in --raw-dir",
    )
    parser.add_argument(
        "--retry-exceptions",
        help="create a new no-clobber overlay for recoverable cards in a prior exception artifact",
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    load_project_env()
    if not (args.enable or env_bool("AI_SIMULATED_CLIENT_REVIEW_ENABLED", False)):
        disabled(args.out, args.exceptions_out, args.raw_dir, args.comments_csv, args.comments_xlsx)
        return
    provider = env_value("AI_SIMULATED_CLIENT_REVIEW_PROVIDER", "openai")
    if provider not in PROVIDERS:
        raise ValueError(
            "AI_SIMULATED_CLIENT_REVIEW_PROVIDER must be one of: " + ", ".join(sorted(PROVIDERS))
        )
    model = env_value("AI_SIMULATED_CLIENT_REVIEW_MODEL", "gpt-5.6-luna")
    effort = env_value("AI_SIMULATED_CLIENT_REVIEW_REASONING_EFFORT", "high")
    retries = env_int("AI_SIMULATED_CLIENT_REVIEW_MAX_RETRIES", 2)
    timeout = env_float("AI_SIMULATED_CLIENT_REVIEW_TIMEOUT_SECONDS", 300.0)
    backoff = env_float("AI_SIMULATED_CLIENT_REVIEW_RETRY_BACKOFF_SECONDS", 5.0)
    max_backoff = env_float("AI_SIMULATED_CLIENT_REVIEW_MAX_BACKOFF_SECONDS", 180.0)
    max_cards = env_int("AI_SIMULATED_CLIENT_REVIEW_MAX_CARDS", 0)
    # Measured, not guessed. On a real 18-page corpus one card's packet ran 47KB
    # to 155KB and one review item's ran up to 81KB, and the previous 120000
    # default still rejected 18 of 20 cards -- the context was clipped to the
    # whole ceiling less a fixed 16000-byte reserve first, so almost nothing was
    # left for the card. A ceiling below what a real card needs turns an enabled
    # lane into a silent no-op.
    #
    # Both halves of that are now fixed: the reserve is measured from the
    # queue's own items, and a card the budget cannot hold is split by the
    # shared byte-adaptive packet utility rather than discarded. At this ceiling
    # the same corpus packs into one packet per card with nothing oversized.
    max_context_bytes = env_int("AI_SIMULATED_CLIENT_REVIEW_MAX_CONTEXT_BYTES", 2000000)
    artifact_bytes = env_int("AI_SIMULATED_CLIENT_REVIEW_MAX_CONTEXT_ARTIFACT_BYTES", 10000000)
    reserve_bytes = env_int("AI_SIMULATED_CLIENT_REVIEW_CONTEXT_RESERVE_BYTES", 16000)
    if reserve_bytes >= max_context_bytes:
        reserve_bytes = max(0, max_context_bytes // 8)
    credential_env = env_value(
        "AI_SIMULATED_CLIENT_REVIEW_CREDENTIAL_ENV", provider_credential_env(provider)
    )
    label = env_value("AI_SIMULATED_CLIENT_REVIEW_COMMENT_LABEL", LABEL_DEFAULT)
    validate_config(provider, model, effort, retries, backoff, max_backoff)
    validate_run_limits(max_cards, max_context_bytes, artifact_bytes, reserve_bytes, timeout)
    resolved_configuration = configuration_record(
        provider,
        model,
        effort,
        retries,
        backoff,
        max_backoff,
        max_cards,
        max_context_bytes,
        artifact_bytes,
        reserve_bytes,
        {
            "timeout_seconds": timeout,
            "credential_environment_variable": credential_env,
            "fail_fast_on_provider_quota": env_bool("LLM_FAIL_FAST_ON_PROVIDER_QUOTA", False),
            "comment_label": label,
            "retry_exception_sha256": args.retry_exceptions
            and hashlib.sha256(Path(args.retry_exceptions).read_bytes()).hexdigest(),
        },
    )
    only_card_ids = retry_card_ids(args.retry_exceptions) if args.retry_exceptions else None
    if args.retry_exceptions and not only_card_ids:
        raise ValueError("retry exception artifact contains no recoverable card exceptions")
    output = run(
        args.final_review,
        args.context,
        args.out,
        args.exceptions_out,
        args.raw_dir,
        args.comments_csv,
        args.comments_xlsx,
        build_reviewer_client(
            provider,
            credential_env,
            timeout,
            retries,
        ),
        provider=provider,
        model=model,
        effort=effort,
        retries=retries,
        backoff=backoff,
        max_backoff=max_backoff,
        max_cards=max_cards,
        max_context_bytes=max_context_bytes,
        max_context_artifact_bytes=artifact_bytes,
        label=label,
        evidence_paths=args.evidence,
        context_reserve_bytes=reserve_bytes,
        resume=args.resume,
        resolved_configuration=resolved_configuration,
        only_card_ids=only_card_ids,
        workers=args.workers or env_int("AI_SIMULATED_CLIENT_REVIEW_MAX_WORKERS", 8),
    )
    print(json.dumps(output["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
