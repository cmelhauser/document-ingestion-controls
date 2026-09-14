#!/usr/bin/env python3
"""Optional cross-record evidence pass for the client-review proposal lane.

This phase searches the supplied retained normalized artifacts for an
evidence-linked answer to an unresolved item on another record. It emits
proposal-only matches; protected categories can never be auto-accepted.
"""

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import pikepdf
from canonical_load import build_plan as build_canonical_plan
from canonical_load import load_export as load_canonical_export
from cli_help import apply_shared_help
from ingest_pages import (
    categorize_text,
    extract_page_text,
    has_text_layer,
    page_output_name,
    schema_label,
)
from llm_runtime import (
    estimate_tokens,
    parallel_map,
    retry_after_seconds,
    retry_call,
    retryable_error,
)
from operations import review_rows
from project_metadata import MANIFEST_SCHEMA_VERSION, PROJECT_VERSION
from review_status import REVIEW_CLEAR
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

from client_review.llm import (
    INSTRUCTIONS,
    context_summary,
    empty_output_directory,
    empty_output_path,
    load_queue,
    valid_proposed_update,
    validate_auto_accept_threshold,
    validate_limits,
)
from client_review.protection import protected

GLOBAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["matches", "rationale"],
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "review_item_id",
                    "source_record_id",
                    "source_field",
                    "decision",
                    "confidence",
                    "proposed_update",
                    "rationale",
                ],
                "properties": {
                    "review_item_id": {"type": "string"},
                    "source_record_id": {"type": "string"},
                    "source_field": {"type": "string"},
                    "decision": {"type": "string", "enum": ["retain_review", "propose_resolution"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
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
                            "original_value": {"type": ["string", "number", "boolean", "null"]},
                            "proposed_value": {"type": ["string", "number", "boolean", "null"]},
                            "update_type": {
                                "type": "string",
                                "enum": ["correction", "completion", "normalization", "none"],
                            },
                            "evidence": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                    },
                    "rationale": {"type": "string"},
                },
            },
        },
        "rationale": {"type": "string"},
    },
}
REVIEW_CLEAR_STATUSES = frozenset(REVIEW_CLEAR)
EVIDENCE_ENVELOPE_SCHEMA_VERSION = "cross_record_evidence_v1"
INTAKE_PRODUCER_SCHEMA_VERSION = "intake_evidence_producer_v1"
CANONICAL_PRODUCER_SCHEMA_VERSION = "canonical_evidence_producer_v1"
DEFAULT_MAX_EVIDENCE_ARTIFACT_BYTES = 10_000_000
DEFAULT_MAX_EVIDENCE_ENTRIES = 10_000
EVIDENCE_ENTRY_FIELDS = {
    "record_id",
    "field",
    "value",
    "evidence",
    "source_sha256",
    "source_page_range",
}
PRODUCER_KINDS = {"intake_manifest", "canonical_export"}
ARTIFACT_REFERENCE_FIELDS = {"path", "sha256"}
PRODUCER_FIELDS = {
    "intake_manifest": {
        "kind",
        "schema_version",
        "path",
        "sha256",
        "operations_manifest",
        "classification_exceptions",
    },
    "canonical_export": {
        "kind",
        "schema_version",
        "path",
        "sha256",
        "operations_manifest",
        "load_plan",
        "final_review",
    },
}
INTAKE_SUMMARY_FIELDS = {
    "generated_at",
    "source_file",
    "source_original_name",
    "source_sha256",
    "pages",
    "page_output_directory",
    "output_order",
    "native_text_pages",
    "rule_classified_pages",
    "exception_pages",
    "unassigned_pages",
    "findings",
}
INTAKE_PAGE_FIELDS = {
    "page_id",
    "page_label",
    "output_sort_key",
    "document_id",
    "source_file",
    "source_original_name",
    "source_sha256",
    "source_page_number",
    "source_page_range",
    "page_pdf",
    "text_file",
    "has_text_layer",
    "text_extraction_status",
    "document_type",
    "classification_confidence",
    "classification_status",
    "document_group_id",
    "reassembly_status",
}
INTAKE_EXCEPTION_FIELDS = {
    "page_id",
    "source_file",
    "source_page_number",
    "reason",
    "candidate_document_type",
    "disposition",
}
CANONICAL_DOCUMENT_FIELDS = {
    "document_id",
    "natural_key",
    "document_type",
    "source_file",
    "source_page_range",
    "source_sha256",
    "page_count",
    "engine",
    "engine_version",
    "run_timestamp",
    "branch",
    "scan_dpi",
    "jbig2_suspect",
    "has_handwriting",
    "consensus_flag",
    "arithmetic_status",
    "source_confidence",
    "review_status",
    "duplicate_of",
    "batch_id",
    "created_at",
    "updated_at",
}
ELIGIBLE_FIELDS = {
    "intake_manifest": {"document_type"},
    "canonical_export": {"document_type"},
}
PROVIDER_UPDATE_FIELDS = {
    "confidence",
    "decision",
    "matches",
    "proposed_update",
    "proposed_value",
    "update_type",
}


def global_packet(queue, context, item_indexes=None):
    """Build a bounded packet while preserving the queue's global item IDs."""
    indexes = list(range(len(queue["items"]))) if item_indexes is None else list(item_indexes)
    selected = set(indexes)
    cards = []
    for card in queue["review_cards"]:
        card_indexes = [index for index in card.get("item_indexes", []) if index in selected]
        if card_indexes:
            cards.append({**card, "item_indexes": card_indexes})
    return {
        "review_items": [
            {"review_item_id": f"review-item-{index}", "item": queue["items"][index]}
            for index in indexes
        ],
        "review_cards": cards,
        "pipeline_context": context,
    }


def packet_size_bytes(packet):
    """Return the exact serialized size used by the local packet guard."""
    return len(json.dumps(packet, sort_keys=True, separators=(",", ":")).encode())


def _context_with_budget(context, total_chars):
    """Retain every artifact identity while allocating a bounded text excerpt."""
    if not context:
        return []
    marker = "...[context excerpt truncated for packet budget]"
    allocation = max(0, total_chars // len(context))
    remainder = max(0, total_chars - allocation * len(context))
    result = []
    for index, entry in enumerate(context):
        copy = dict(entry)
        text = str(entry.get("json", ""))
        allowance = allocation + (1 if index < remainder else 0)
        if len(text) > allowance:
            copy["json"] = text[: max(0, allowance - len(marker))] + marker
            copy["context_truncated"] = True
        else:
            copy["context_truncated"] = False
        result.append(copy)
    return result


def fit_context_to_packet(queue, context, max_packet_bytes):
    """Shrink context excerpts until even a one-item packet fits its hard cap."""
    if not context:
        return context, False
    indexes = range(len(queue["items"]))

    def fits(candidate):
        return all(
            packet_size_bytes(global_packet(queue, candidate, [index])) <= max_packet_bytes
            for index in indexes
        )

    if fits(context):
        return context, False
    compact = _context_with_budget(context, 0)
    if not fits(compact):
        raise ValueError(
            "cross-record queue item exceeds max-packet-bytes even after context omission"
        )
    low, high = 0, sum(len(str(entry.get("json", ""))) for entry in context)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = _context_with_budget(context, middle)
        if fits(candidate):
            low = middle
        else:
            high = middle - 1
    return _context_with_budget(context, low), True


def batch_item_indexes(queue, context, max_packet_bytes, max_items_per_batch):
    """Split queue items into bounded batches without dropping any item."""
    if max_packet_bytes < 1:
        raise ValueError("cross-record max packet bytes must be positive")
    if not isinstance(max_items_per_batch, int) or max_items_per_batch < 1:
        raise ValueError("cross-record max items per batch must be positive")
    batches = []
    current = []
    for index in range(len(queue["items"])):
        candidate = current + [index]
        packet = global_packet(queue, context, candidate)
        if current and (
            len(candidate) > max_items_per_batch or packet_size_bytes(packet) > max_packet_bytes
        ):
            batches.append(current)
            current = [index]
            continue
        current = candidate
    if current:
        batches.append(current)
    return batches


def global_request(client, model, packet):
    """Submit the cross-record packet using its dedicated strict schema."""
    return client.responses.create(
        model=model,
        instructions=(
            INSTRUCTIONS
            + " Search all records for exact evidence-linked matches, but never resolve protected categories."
        ),
        input=[{"role": "user", "content": [{"type": "input_text", "text": json.dumps(packet)}]}],
        text={
            "format": {
                "type": "json_schema",
                "name": "business_document_cross_record_review",
                "strict": True,
                "schema": GLOBAL_SCHEMA,
            }
        },
    )


def _record_identity(value):
    """Return one supported exact identity and distinguish missing from malformed."""
    for key in ("record_id", "document_id", "source_record_id", "page_id", "id"):
        candidate = value.get(key)
        if candidate in (None, ""):
            continue
        if not isinstance(candidate, str) or candidate != candidate.strip():
            return None, "malformed"
        return candidate, None
    return None, "missing"


def _scalar_token(value):
    """Retain JSON scalar type and value for exact evidence matching."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return None


def _eligible_context_evidence(value):
    """Validate one declared tuple before resolving it to its producer artifact."""
    if not isinstance(value, dict):
        return None, "malformed_context_entry"
    if value.get("proposal_only") is True or value.get("status") == "proposal_only":
        return None, "proposal_only_context"
    if PROVIDER_UPDATE_FIELDS.intersection(value):
        return None, "provider_update_context"
    approval_status = value.get("approval_status")
    if value.get("approved") is False or approval_status not in (None, "approved", "authorized"):
        return None, "unapproved_context"
    if (
        "review_item_id" in value
        or value.get("client_review_required") is True
        or "reason" in value
        or "review_reason" in value
        or (
            value.get("review_status") is not None
            and value.get("review_status") not in REVIEW_CLEAR_STATUSES
        )
    ):
        return None, "unresolved_review_context"
    record_id = value.get("record_id")
    if record_id in (None, ""):
        return None, "missing_context_record_identity"
    if not isinstance(record_id, str) or record_id != record_id.strip():
        return None, "malformed_context_record_identity"
    field = value.get("field")
    if not isinstance(field, str) or not field.strip():
        return None, "missing_context_field"
    scalar = _scalar_token(value.get("value"))
    if scalar is None:
        return None, "invalid_context_value"
    evidence = value.get("evidence")
    if not isinstance(evidence, str) or not evidence.strip():
        return None, "missing_context_evidence"
    source_sha256 = value.get("source_sha256")
    if not (
        isinstance(source_sha256, str)
        and len(source_sha256) == 64
        and all(character in "0123456789abcdefABCDEF" for character in source_sha256)
    ):
        return None, "missing_or_invalid_source_sha256"
    page = value.get("source_page_range")
    if isinstance(page, str):
        page = page if page.strip() else None
    elif not isinstance(page, int) or isinstance(page, bool) or page < 1:
        page = None
    if page is None:
        return None, "missing_page_provenance"
    if set(value) != EVIDENCE_ENTRY_FIELDS:
        return None, "unsupported_context_fields"
    return {
        "record_id": record_id,
        "field": field,
        "value": scalar,
        "evidence": evidence,
        "source_sha256": source_sha256,
        "source_page_range": str(page),
    }, None


class EvidencePreflightError(ValueError):
    """Fail local evidence preflight with a stable operator-facing reason."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class ProducerValidationError(ValueError):
    """Retain a precise producer-authentication failure reason."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _relative_file(root, value):
    """Resolve one retained relative file without permitting path escape."""
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return None
    root = Path(root).resolve()
    candidate = (root / value).resolve()
    return candidate if candidate.is_relative_to(root) and candidate.is_file() else None


def _file_identity(path):
    """Return a cache key that changes when the retained file changes."""
    path = Path(path).resolve()
    stat = path.stat()
    return (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _sha256(path, cache=None):
    """Hash one retained artifact once per stable file identity."""
    path = Path(path)
    identity = _file_identity(path)
    hashes = cache.setdefault("hashes", {}) if cache is not None else {}
    if identity in hashes:
        return hashes[identity]
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    if identity != _file_identity(path):
        raise ProducerValidationError("artifact_changed_during_preflight")
    value = digest.hexdigest()
    hashes[identity] = value
    return value


def _cached_sha256(path, cache):
    """Return a previously authenticated digest without another hash call."""
    identity = _file_identity(path)
    hashes = cache.setdefault("hashes", {})
    return hashes[identity] if identity in hashes else _sha256(path, cache)


def _require_artifact_size(path, maximum):
    """Bound every JSON/PDF/text artifact before local evidence preflight."""
    if Path(path).stat().st_size > maximum:
        raise EvidencePreflightError("cross_record_evidence_artifact_bytes_limit_exceeded")


def _load_json_artifact(path, maximum):
    """Read one bounded JSON artifact as an object."""
    _require_artifact_size(path, maximum)
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ProducerValidationError("unrecognized_producer_artifact")
    return value


def _artifact_reference(root, value, cache, maximum):
    """Resolve and hash one exact nested producer artifact reference."""
    if not isinstance(value, dict) or set(value) != ARTIFACT_REFERENCE_FIELDS:
        raise ProducerValidationError("invalid_producer_handoff_reference")
    path = _relative_file(root, value.get("path"))
    if path is None:
        raise ProducerValidationError("invalid_producer_handoff_reference")
    _require_artifact_size(path, maximum)
    if not isinstance(value.get("sha256"), str) or value["sha256"] != _sha256(path, cache):
        raise ProducerValidationError("producer_handoff_hash_mismatch")
    return path


def _pdf_page_count(path, cache):
    """Validate and count one retained PDF once per stable file identity."""
    identity = _file_identity(path)
    metadata = cache.setdefault("pdf_metadata", {})
    if identity in metadata:
        return metadata[identity]["page_count"]
    try:
        with pikepdf.open(path) as document:
            count = len(document.pages)
            text_layers = tuple(has_text_layer(page) for page in document.pages)
    except (OSError, pikepdf.PdfError) as exc:
        raise ProducerValidationError("source_artifact_invalid_pdf") from exc
    if identity != _file_identity(path):
        raise ProducerValidationError("artifact_changed_during_preflight")
    metadata[identity] = {"page_count": count, "text_layers": text_layers}
    return count


def _pdf_has_text_layer(path, page_number, cache):
    """Return the cached producer-native text-layer flag for one source page."""
    _pdf_page_count(path, cache)
    identity = _file_identity(path)
    return cache["pdf_metadata"][identity]["text_layers"][page_number - 1]


def _valid_utc_timestamp(value):
    """Recognize one timezone-aware ISO timestamp normalized to UTC offset zero."""
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == UTC.utcoffset(parsed)


def _page_bounds(page_range):
    """Parse a supported one-based page or ordered page range."""
    parts = page_range.split("-")
    if len(parts) not in (1, 2) or not all(part.isdigit() for part in parts):
        return None
    first = int(parts[0])
    last = int(parts[-1])
    return (first, last) if first >= 1 and last >= first else None


def _source_page_exists(path, page_range, cache=None):
    """Require every claimed one-based source page to exist in the retained PDF."""
    bounds = _page_bounds(page_range)
    if bounds is None:
        return False
    try:
        count = _pdf_page_count(path, cache or {})
    except ProducerValidationError:
        return False
    return bounds[1] <= count


def _validate_operations_manifest(data, required_paths, cache):
    """Bind every required producer artifact to one generated operations manifest."""
    allowed = {
        "manifest_schema_version",
        "pipeline_version",
        "generated_at",
        "artifacts",
        "configuration_sha256",
        "effective_runtime_settings",
    }
    required = allowed - {"effective_runtime_settings"}
    if (
        not required <= set(data) <= allowed
        or data.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION
        or data.get("pipeline_version") != PROJECT_VERSION
        or not _valid_utc_timestamp(data.get("generated_at"))
        or not isinstance(data.get("configuration_sha256"), str)
        or len(data["configuration_sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in data["configuration_sha256"])
        or not isinstance(data.get("artifacts"), list)
        or (
            "effective_runtime_settings" in data
            and not isinstance(data["effective_runtime_settings"], dict)
        )
    ):
        raise ProducerValidationError("unrecognized_operations_manifest")
    artifact_fields = {"artifact_id", "name", "bytes", "sha256"}
    artifacts = data["artifacts"]
    if not all(
        isinstance(item, dict)
        and set(item) == artifact_fields
        and item.get("artifact_id") == f"artifact-{index:04d}"
        and isinstance(item.get("name"), str)
        and bool(item["name"])
        and Path(item["name"]).name == item["name"]
        and isinstance(item.get("bytes"), int)
        and not isinstance(item.get("bytes"), bool)
        and item["bytes"] >= 0
        and isinstance(item.get("sha256"), str)
        and len(item["sha256"]) == 64
        and all(character in "0123456789abcdef" for character in item["sha256"])
        for index, item in enumerate(artifacts, 1)
    ):
        raise ProducerValidationError("unrecognized_operations_manifest")
    for path in required_paths:
        expected = {
            "name": Path(path).name,
            "bytes": Path(path).stat().st_size,
            "sha256": _cached_sha256(path, cache),
        }
        matches = [
            item
            for item in artifacts
            if all(item[key] == expected[key] for key in ("name", "bytes", "sha256"))
        ]
        if len(matches) != 1:
            raise ProducerValidationError("producer_artifact_not_operations_bound")


def _classification_for_page(source, page_number, cache):
    """Re-run the retained deterministic intake classifier once per source page."""
    key = (*_file_identity(source), page_number)
    classifications = cache.setdefault("classifications", {})
    if key not in classifications:
        text, extraction_status = extract_page_text(source, page_number)
        classifications[key] = (text, extraction_status, categorize_text(text))
    return classifications[key]


def _validate_intake_producer(data, exceptions, root, cache, maximum):
    """Validate the complete generated intake state and semantic classification."""
    if set(data) != {"summary", "pages"}:
        raise ProducerValidationError("unrecognized_producer_artifact")
    summary = data.get("summary")
    pages = data.get("pages")
    if not isinstance(summary, dict) or set(summary) != INTAKE_SUMMARY_FIELDS:
        raise ProducerValidationError("unrecognized_producer_artifact")
    if not isinstance(pages, list) or not pages:
        raise ProducerValidationError("unrecognized_producer_artifact")
    if any(not isinstance(page, dict) or set(page) != INTAKE_PAGE_FIELDS for page in pages):
        raise ProducerValidationError("unsupported_intake_manifest_fields")
    if summary.get("pages") != len(pages):
        raise ProducerValidationError("unrecognized_producer_artifact")
    source_original_name = summary.get("source_original_name")
    if (
        not _valid_utc_timestamp(summary.get("generated_at"))
        or not isinstance(source_original_name, str)
        or not source_original_name
        or Path(source_original_name).name != source_original_name
        or summary.get("source_file") != f"source/{source_original_name}"
        or summary.get("page_output_directory") != "pages"
        or summary.get("output_order")
        != "ascending source_page_number; zero-padded output_sort_key"
    ):
        raise ProducerValidationError("unrecognized_producer_artifact")
    source = _relative_file(root, summary.get("source_file"))
    if source is None:
        raise ProducerValidationError("source_artifact_hash_mismatch")
    _require_artifact_size(source, maximum)
    if summary.get("source_sha256") != _sha256(source, cache):
        raise ProducerValidationError("source_artifact_hash_mismatch")
    if _pdf_page_count(source, cache) != len(pages):
        raise ProducerValidationError("source_page_count_mismatch")
    required_paths = [source]
    observed_exceptions = []
    native_text_pages = 0
    rule_classified_pages = 0
    for index, page in enumerate(pages, 1):
        if (
            not isinstance(page.get("page_id"), str)
            or page["page_id"]
            != f"{schema_label(Path(source_original_name).stem, 'source')}__p{index:04d}"
            or page.get("source_file") != summary.get("source_file")
            or page.get("source_original_name") != source_original_name
            or page.get("source_sha256") != summary.get("source_sha256")
            or page.get("source_page_number") != index
            or page.get("source_page_range") != str(index)
            or page.get("output_sort_key") != f"{index:06d}"
            or page.get("document_id") is not None
            or page.get("document_group_id") is not None
            or page.get("reassembly_status") != "unassigned"
        ):
            raise ProducerValidationError("unrecognized_producer_artifact")
        expected_pdf, expected_label = page_output_name(
            Path(summary["source_original_name"]), index, page.get("document_type")
        )
        if (
            page.get("page_label") != expected_label
            or page.get("page_pdf") != f"pages/{expected_pdf}"
        ):
            raise ProducerValidationError("unrecognized_producer_artifact")
        page_pdf = _relative_file(root, page.get("page_pdf"))
        if page_pdf is None:
            raise ProducerValidationError("unrecognized_producer_artifact")
        _require_artifact_size(page_pdf, maximum)
        if _pdf_page_count(page_pdf, cache) != 1:
            raise ProducerValidationError("unrecognized_producer_artifact")
        required_paths.append(page_pdf)
        text, extraction_status, classification = _classification_for_page(source, index, cache)
        document_type, confidence, classification_status = classification
        if (
            page.get("text_extraction_status") != extraction_status
            or page.get("has_text_layer") != _pdf_has_text_layer(source, index, cache)
            or page.get("document_type") != document_type
            or page.get("classification_confidence") != confidence
            or page.get("classification_status") != classification_status
        ):
            raise ProducerValidationError("intake_semantic_evidence_mismatch")
        text_file = page.get("text_file")
        if text:
            expected_text_file = f"text/{index:06d}__{expected_label}.txt"
            if text_file != expected_text_file:
                raise ProducerValidationError("intake_semantic_evidence_mismatch")
            retained_text = _relative_file(root, text_file)
            if retained_text is None:
                raise ProducerValidationError("intake_semantic_evidence_mismatch")
            _require_artifact_size(retained_text, maximum)
            if retained_text.read_text() != text:
                raise ProducerValidationError("intake_semantic_evidence_mismatch")
            required_paths.append(retained_text)
        elif text_file is not None:
            raise ProducerValidationError("intake_semantic_evidence_mismatch")
        native_text_pages += page.get("has_text_layer") is True
        rule_classified_pages += classification_status == "rule_classified"
        if classification_status != "rule_classified":
            observed_exceptions.append(
                {
                    "page_id": page["page_id"],
                    "source_file": page["source_file"],
                    "source_page_number": index,
                    "reason": classification_status,
                    "candidate_document_type": document_type,
                    "disposition": "human_classification_and_reassembly_required",
                }
            )
    expected_summary = {
        "native_text_pages": native_text_pages,
        "rule_classified_pages": rule_classified_pages,
        "exception_pages": len(observed_exceptions),
        "unassigned_pages": len(pages),
    }
    if any(summary.get(key) != value for key, value in expected_summary.items()):
        raise ProducerValidationError("unrecognized_producer_artifact")
    expected_findings = [
        "Every source page was preserved as exactly one PDF in ascending source "
        "page order. Filenames begin with a zero-padded sort key and carry the "
        "page label and conservative document category. No pages were "
        "force-grouped into documents. The byte-identical source PDF is retained "
        "inside the intake run and verified by SHA-256."
    ]
    if observed_exceptions:
        expected_findings.append(
            f"{len(observed_exceptions)} pages require human classification and "
            "reassembly; they remain in the exception queue rather than being guessed."
        )
    if summary.get("findings") != expected_findings:
        raise ProducerValidationError("unrecognized_producer_artifact")
    if (
        not isinstance(exceptions, dict)
        or set(exceptions) != {"summary", "exceptions"}
        or exceptions.get("summary") != {"count": len(observed_exceptions)}
        or exceptions.get("exceptions") != observed_exceptions
        or any(set(item) != INTAKE_EXCEPTION_FIELDS for item in observed_exceptions)
    ):
        raise ProducerValidationError("unrecognized_classification_exceptions")
    sources = {
        (summary["source_file"], summary["source_sha256"]): {
            "path": source,
            "page_count": len(pages),
        }
    }
    return required_paths, sources


def _validate_canonical_producer(export, load_plan, final_review, export_path):
    """Validate the exact canonical plan/final-gate chain and documented row fields."""
    if (
        not {"batch_id", "tables"}
        <= set(export)
        <= {
            "batch_id",
            "registry_version",
            "tables",
        }
    ):
        raise ProducerValidationError("unrecognized_producer_artifact")
    documents = export.get("tables", {}).get("document", [])
    if any(
        not isinstance(document, dict) or not set(document) <= CANONICAL_DOCUMENT_FIELDS
        for document in documents
    ):
        raise ProducerValidationError("unsupported_canonical_document_fields")
    expected_plan = build_canonical_plan(export)
    if (
        set(load_plan) != set(expected_plan)
        or not isinstance(load_plan.get("generated_at"), str)
        or not load_plan["generated_at"]
        or any(
            load_plan.get(key) != value
            for key, value in expected_plan.items()
            if key != "generated_at"
        )
    ):
        raise ProducerValidationError("canonical_load_plan_mismatch")
    if set(final_review) != {"summary", "items", "review_cards"}:
        raise ProducerValidationError("unrecognized_final_review_artifact")
    summary = final_review.get("summary")
    expected_summary_fields = {
        "schema_version",
        "generated_at",
        "source_artifacts",
        "client_review_items",
        "client_review_cards",
        "gate_status",
        "findings",
    }
    if (
        not isinstance(summary, dict)
        or set(summary) != expected_summary_fields
        or summary.get("schema_version") != "1.0"
        or not _valid_utc_timestamp(summary.get("generated_at"))
        or not isinstance(summary.get("source_artifacts"), list)
        or Path(export_path).name not in summary.get("source_artifacts", [])
        or not isinstance(final_review.get("review_cards"), list)
        or summary.get("client_review_cards") != len(final_review["review_cards"])
    ):
        raise ProducerValidationError("unrecognized_final_review_artifact")
    try:
        review_rows(final_review)
    except ValueError as exc:
        raise ProducerValidationError("unrecognized_final_review_artifact") from exc
    if (
        final_review["items"]
        or final_review["review_cards"]
        or final_review["summary"].get("gate_status") != "clear"
    ):
        raise ProducerValidationError("canonical_final_review_not_clear")


def _producer_index(records, identity_field):
    """Build one producer identity index for constant-time entry binding."""
    result = defaultdict(list)
    for index, record in enumerate(records):
        if isinstance(record, dict) and isinstance(record.get(identity_field), str):
            result[record[identity_field]].append((index, record))
    return dict(result)


def _load_producer(envelope_path, producer, cache, maximum):
    """Load a recognized producer only through its strict authenticated artifact chain."""
    if not isinstance(producer, dict):
        return None, "invalid_producer_reference"
    kind = producer.get("kind")
    if kind not in PRODUCER_KINDS:
        return None, "unsupported_producer_kind"
    if set(producer) != PRODUCER_FIELDS[kind]:
        return None, "invalid_producer_reference"
    expected_schema = (
        INTAKE_PRODUCER_SCHEMA_VERSION
        if kind == "intake_manifest"
        else CANONICAL_PRODUCER_SCHEMA_VERSION
    )
    if producer.get("schema_version") != expected_schema:
        return None, "unsupported_producer_schema_version"
    root = Path(envelope_path).parent
    producer_path = _relative_file(root, producer.get("path"))
    if producer_path is None:
        return None, "invalid_producer_reference"
    try:
        _require_artifact_size(producer_path, maximum)
        claimed_sha256 = producer.get("sha256")
        if not isinstance(claimed_sha256, str) or claimed_sha256 != _sha256(producer_path, cache):
            return None, "producer_hash_mismatch"
        operations_path = _artifact_reference(root, producer["operations_manifest"], cache, maximum)
        data = _load_json_artifact(producer_path, maximum)
        required_paths = [producer_path]
        if kind == "intake_manifest":
            exceptions_path = _artifact_reference(
                root, producer["classification_exceptions"], cache, maximum
            )
            exceptions = _load_json_artifact(exceptions_path, maximum)
            intake_paths, authenticated_sources = _validate_intake_producer(
                data, exceptions, root, cache, maximum
            )
            required_paths.extend(intake_paths)
            required_paths.append(exceptions_path)
            records = data["pages"]
            identity_field = "page_id"
            path_prefix = "$.pages"
        else:
            load_plan_path = _artifact_reference(root, producer["load_plan"], cache, maximum)
            final_review_path = _artifact_reference(root, producer["final_review"], cache, maximum)
            export = load_canonical_export(producer_path)
            load_plan = _load_json_artifact(load_plan_path, maximum)
            final_review = _load_json_artifact(final_review_path, maximum)
            _validate_canonical_producer(export, load_plan, final_review, producer_path)
            required_paths.extend((load_plan_path, final_review_path))
            records = export["tables"].get("document", [])
            authenticated_sources = {}
            for record in records:
                source_file = record.get("source_file")
                source_sha256 = record.get("source_sha256")
                source_page_range = record.get("source_page_range")
                if (
                    not isinstance(source_file, str)
                    or not source_file
                    or not isinstance(source_sha256, str)
                    or len(source_sha256) != 64
                    or not isinstance(source_page_range, str)
                ):
                    raise ProducerValidationError("source_artifact_hash_mismatch")
                source = _relative_file(root, source_file)
                if source is None:
                    raise ProducerValidationError("source_artifact_hash_mismatch")
                key = (source_file, source_sha256)
                if key not in authenticated_sources:
                    _require_artifact_size(source, maximum)
                    if _cached_sha256(source, cache) != source_sha256:
                        raise ProducerValidationError("source_artifact_hash_mismatch")
                    authenticated_sources[key] = {
                        "path": source,
                        "page_count": _pdf_page_count(source, cache),
                    }
                bounds = _page_bounds(source_page_range)
                if bounds is None or bounds[1] > authenticated_sources[key]["page_count"]:
                    raise ProducerValidationError("source_page_not_found")
                required_paths.append(source)
            identity_field = "document_id"
            path_prefix = "$.tables.document"
            data = export
        operations = _load_json_artifact(operations_path, maximum)
        _validate_operations_manifest(operations, required_paths, cache)
    except ProducerValidationError as exc:
        return None, exc.reason
    except EvidencePreflightError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None, "unrecognized_producer_artifact"
    return {
        "kind": kind,
        "path": producer_path,
        "sha256": claimed_sha256,
        "data": data,
        "index": _producer_index(records, identity_field),
        "path_prefix": path_prefix,
        "cache": cache,
        "authenticated_sources": authenticated_sources,
    }, None


def _source_binding(entry, producer, source_record, producer_json_path):
    """Verify mapped value, source file/hash/page, and derived evidence locator."""
    if entry["field"] not in ELIGIBLE_FIELDS[producer["kind"]]:
        return None, f"unsupported_{producer['kind']}_evidence_field"
    if (
        producer["kind"] == "intake_manifest"
        and source_record.get("classification_status") != "rule_classified"
    ):
        return None, "intake_semantic_evidence_not_authoritative"
    if source_record.get(entry["field"]) != json.loads(entry["value"]):
        return None, "producer_value_mismatch"
    source_sha256 = source_record.get("source_sha256")
    source_page_range = source_record.get("source_page_range")
    source_file = source_record.get("source_file")
    if (
        source_sha256 != entry["source_sha256"]
        or str(source_page_range) != entry["source_page_range"]
        or not isinstance(source_file, str)
        or not source_file
    ):
        return None, "producer_provenance_mismatch"
    authenticated_source = producer["authenticated_sources"].get((source_file, source_sha256))
    if authenticated_source is None:
        return None, "source_artifact_hash_mismatch"
    bounds = _page_bounds(entry["source_page_range"])
    if bounds is None or bounds[1] > authenticated_source["page_count"]:
        return None, "source_page_not_found"
    expected_evidence = f"{source_file}#page={entry['source_page_range']}#field={entry['field']}"
    if entry["evidence"] != expected_evidence:
        return None, "producer_evidence_mismatch"
    return {
        "producer_kind": producer["kind"],
        "producer_artifact": str(producer["path"]),
        "producer_artifact_sha256": producer["sha256"],
        "producer_json_path": producer_json_path,
        "source_file": source_file,
        "source_sha256": source_sha256,
        "source_page_range": entry["source_page_range"],
    }, None


def _bind_to_producer(entry, producer):
    """Resolve one tuple through the producer index built during preflight."""
    matches = producer["index"].get(entry["record_id"], [])
    if not matches:
        return None, "producer_record_not_found"
    if len(matches) != 1:
        return None, "ambiguous_producer_record"
    index, source_record = matches[0]
    return _source_binding(entry, producer, source_record, f"{producer['path_prefix']}[{index}]")


def _ineligible_context(artifact, artifact_sha256, json_path, reason):
    """Describe one retained context object that cannot serve as evidence."""
    return {
        "retained_context_reference": {
            "artifact": artifact,
            "artifact_sha256": artifact_sha256,
            "json_path": json_path,
        },
        "eligibility_reason": reason,
        "disposition": "not_eligible_as_cross_record_evidence",
    }


def _positive_integer(value, name):
    """Require a positive non-boolean local preflight limit."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _preflight_evidence_envelopes(context_paths, cache, maximum, max_entries):
    """Bound and parse every outer envelope before loading any producer."""
    preflighted = []
    declared_entries = 0
    for path in context_paths:
        context_path = Path(path)
        artifact = str(context_path)
        _require_artifact_size(context_path, maximum)
        artifact_sha256 = _sha256(context_path, cache)
        try:
            value = json.loads(context_path.read_text())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            value = None
        entries = value.get("entries") if isinstance(value, dict) else None
        if isinstance(entries, list):
            declared_entries += len(entries)
            if declared_entries > max_entries:
                raise EvidencePreflightError("cross_record_evidence_entries_limit_exceeded")
        valid = (
            isinstance(value, dict)
            and set(value) == {"schema_version", "producer", "entries"}
            and value.get("schema_version") == EVIDENCE_ENVELOPE_SCHEMA_VERSION
            and isinstance(entries, list)
        )
        preflighted.append(
            {
                "path": context_path,
                "artifact": artifact,
                "artifact_sha256": artifact_sha256,
                "value": value if valid else None,
            }
        )
    return preflighted


def build_retained_evidence_index(
    queue,
    context_paths,
    *,
    max_artifact_bytes=DEFAULT_MAX_EVIDENCE_ARTIFACT_BYTES,
    max_entries=DEFAULT_MAX_EVIDENCE_ENTRIES,
):
    """Index only admissible retained evidence and report every rejected context tuple."""
    del queue
    _positive_integer(max_artifact_bytes, "max evidence artifact bytes")
    _positive_integer(max_entries, "max evidence entries")
    cache = {}
    record_ids = set()
    record_fields = set()
    exact = defaultdict(list)
    context_artifacts = []
    ineligible_context = []
    preflighted = _preflight_evidence_envelopes(
        context_paths,
        cache,
        max_artifact_bytes,
        max_entries,
    )
    for envelope in preflighted:
        context_path = envelope["path"]
        artifact = envelope["artifact"]
        artifact_sha256 = envelope["artifact_sha256"]
        value = envelope["value"]
        eligible_count = 0
        ineligible_count = 0
        if value is None:
            ineligible_context.append(
                _ineligible_context(artifact, artifact_sha256, "$", "invalid_evidence_envelope")
            )
            ineligible_count = 1
            entries = []
            producer = None
            producer_reason = None
        else:
            entries = value["entries"]
            producer, producer_reason = _load_producer(
                context_path,
                value["producer"],
                cache,
                max_artifact_bytes,
            )
            if producer is None:
                ineligible_context.append(
                    _ineligible_context(
                        artifact,
                        artifact_sha256,
                        "$.producer",
                        producer_reason,
                    )
                )
                ineligible_count = 1
        for index, candidate in enumerate(entries):
            json_path = f"$.entries[{index}]"
            entry, reason = _eligible_context_evidence(candidate)
            if producer is None and reason is None:
                reason = "producer_not_authenticated"
            reference = None
            if reason is None:
                reference, reason = _bind_to_producer(entry, producer)
            if entry is None:
                reason = reason or "malformed_context_entry"
            if reason is not None:
                ineligible_count += 1
                ineligible_context.append(
                    _ineligible_context(artifact, artifact_sha256, json_path, reason)
                )
                continue
            eligible_count += 1
            record_ids.add(entry["record_id"])
            record_field = (entry["record_id"], entry["field"])
            record_fields.add(record_field)
            exact[(*record_field, entry["value"], entry["evidence"])].append(
                {
                    "artifact": artifact,
                    "artifact_sha256": artifact_sha256,
                    "json_path": json_path,
                    **reference,
                }
            )
        context_artifacts.append(
            {
                "artifact": artifact,
                "artifact_sha256": artifact_sha256,
                "eligible_evidence": eligible_count,
                "ineligible_evidence": ineligible_count,
            }
        )
    return {
        "record_ids": record_ids,
        "record_fields": record_fields,
        "exact": exact,
        "context_artifacts": context_artifacts,
        "ineligible_context": ineligible_context,
    }


def resolve_retained_evidence(match, proposal, evidence_index):
    """Resolve a provider citation to exactly one retained evidence object."""
    record_id = match.get("source_record_id")
    field = match.get("source_field")
    if isinstance(record_id, str) and record_id and record_id != record_id.strip():
        return None, "malformed_source_record_identity"
    if not isinstance(record_id, str) or not record_id or not isinstance(field, str) or not field:
        return None, "malformed_source_reference"
    if record_id not in evidence_index["record_ids"]:
        return None, "source_record_not_found"
    if (record_id, field) not in evidence_index["record_fields"]:
        return None, "source_field_not_found"
    value = _scalar_token(proposal.get("proposed_value"))
    refs = evidence_index["exact"].get((record_id, field, value, proposal.get("evidence")), [])
    if not refs:
        return None, "retained_evidence_not_found"
    if len(refs) != 1:
        return None, "ambiguous_retained_evidence"
    return refs[0], None


def normalize_matches(matches, queue, evidence_index, threshold, auto_accept):
    """Keep only evidence-linked, valid, non-protected cross-record proposals."""
    valid_ids = {f"review-item-{i}": item for i, item in enumerate(queue["items"])}
    accepted, remaining = [], []
    for match in matches:
        if not isinstance(match, dict):
            remaining.append(
                {
                    "provider_match": match,
                    "proposal_only": True,
                    "auto_accept_status": "disabled",
                    "retained_review_reason": "malformed_match",
                }
            )
            continue
        item = valid_ids.get(match.get("review_item_id"))
        proposal = match.get("proposed_update")
        entry = dict(match)
        target_record_id, target_identity_reason = (
            _record_identity(item) if item is not None else (None, None)
        )
        source_record_id = match.get("source_record_id")
        confidence = match.get("confidence")
        if item is None:
            reason = "unknown_review_item"
        elif target_identity_reason == "missing":
            reason = "missing_target_record_identity"
        elif target_identity_reason == "malformed":
            reason = "malformed_target_record_identity"
        elif protected(item):
            reason = "protected_review_item"
        elif match.get("decision") != "propose_resolution":
            reason = "proposal_not_requested"
        elif not valid_proposed_update(proposal, item):
            reason = "invalid_proposed_update"
        elif (
            isinstance(source_record_id, str)
            and source_record_id
            and source_record_id != source_record_id.strip()
        ):
            reason = "malformed_source_record_identity"
        elif (
            isinstance(source_record_id, str)
            and source_record_id
            and source_record_id == target_record_id
        ):
            reason = "same_record_reference"
        elif isinstance(confidence, bool):
            reason = "boolean_confidence"
        elif not isinstance(confidence, (int, float)):
            reason = "invalid_confidence_type"
        elif not math.isfinite(confidence):
            reason = "non_finite_confidence"
        elif not 0 <= confidence <= 1:
            reason = "confidence_out_of_range"
        elif confidence < threshold:
            reason = "below_auto_accept_threshold"
        else:
            reference, reason = resolve_retained_evidence(match, proposal, evidence_index)
            if reference is not None:
                entry["resolved_evidence_reference"] = reference
        if reason is None and not auto_accept:
            reason = "auto_accept_disabled"
        entry["proposal_only"] = True
        entry["auto_accept_status"] = "auto_accepted" if reason is None else "disabled"
        if reason is None:
            accepted.append(entry)
        else:
            entry["retained_review_reason"] = reason
            remaining.append(entry)
    return accepted, remaining


def load_resumable_batch(raw_path, expected_indexes):
    """Load a successful raw batch only when its retained request is exact."""
    try:
        payload = json.loads(raw_path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    request = payload.get("request") if isinstance(payload, dict) else None
    response = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(request, dict) or not isinstance(response, dict):
        return None
    review_items = request.get("review_items")
    expected_ids = [f"review-item-{index}" for index in expected_indexes]
    if (
        not isinstance(review_items, list)
        or [item.get("review_item_id") for item in review_items if isinstance(item, dict)]
        != expected_ids
    ):
        return None
    if isinstance(response.get("matches"), list):
        return response if isinstance(response.get("rationale"), str) else None
    output = response.get("output")
    if isinstance(output, list):
        for message in reversed(output):
            for content in message.get("content", []) if isinstance(message, dict) else []:
                text = content.get("text") if isinstance(content, dict) else None
                if isinstance(text, str):
                    try:
                        decision = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if (
                        isinstance(decision, dict)
                        and isinstance(decision.get("matches"), list)
                        and isinstance(decision.get("rationale"), str)
                    ):
                        return decision
    return None


def run_cross_record(
    queue_path,
    context_paths,
    out_path,
    exceptions_path,
    raw_dir,
    model,
    client,
    *,
    max_context_bytes=12000,
    max_packet_bytes=200000,
    max_items_per_batch=100,
    max_evidence_artifact_bytes=DEFAULT_MAX_EVIDENCE_ARTIFACT_BYTES,
    max_evidence_entries=DEFAULT_MAX_EVIDENCE_ENTRIES,
    timeout_seconds=120.0,
    max_retries=4,
    retry_backoff_seconds=1.0,
    max_backoff_seconds=120.0,
    auto_accept=False,
    auto_accept_threshold=0.99,
    provider=None,
    resume_raw_dir=None,
    workers=1,
):
    """Run bounded evidence batches without mutating the canonical queue."""
    queue = load_queue(queue_path)
    validate_limits(1, max_context_bytes, timeout_seconds, max_retries, "medium")
    validate_auto_accept_threshold(auto_accept_threshold)
    _positive_integer(max_evidence_artifact_bytes, "max evidence artifact bytes")
    _positive_integer(max_evidence_entries, "max evidence entries")
    out_path = empty_output_path(out_path)
    exceptions_path = empty_output_path(exceptions_path)
    if resume_raw_dir is None:
        raw_dir = empty_output_directory(raw_dir)
    else:
        raw_dir = Path(resume_raw_dir)
        if not raw_dir.is_dir():
            raise ValueError(f"Resume raw directory does not exist: {raw_dir}")
    try:
        evidence_index = build_retained_evidence_index(
            queue,
            context_paths,
            max_artifact_bytes=max_evidence_artifact_bytes,
            max_entries=max_evidence_entries,
        )
        context = context_summary(context_paths, max_context_bytes)
        context, context_truncated = fit_context_to_packet(queue, context, max_packet_bytes)
    except Exception as exc:
        reason = (
            exc.reason
            if isinstance(exc, EvidencePreflightError)
            else "cross_record_packet_preflight_failure"
        )
        exception = {
            "batch": None,
            "item_count": len(queue["items"]),
            "reason": reason,
            "error_type": type(exc).__name__,
            "disposition": "client_review_required",
        }
        summary = {
            "generated_at": datetime.now(UTC).isoformat(),
            "matches": 0,
            "matches_available": False,
            "analysis_status": "failed",
            "auto_accepted_updates": 0,
            "remaining_matches": 0,
            "batches": 0,
            "successful_batches": 0,
            "provider_exceptions": 1,
            "partial_results_retained": True,
            "all_source_items_retained": True,
            "production_approval_permitted": False,
            "request_limits": {
                "context_bytes_per_artifact": max_context_bytes,
                "max_packet_bytes": max_packet_bytes,
                "max_items_per_batch": max_items_per_batch,
                "evidence_artifact_bytes": max_evidence_artifact_bytes,
                "evidence_entries": max_evidence_entries,
            },
        }
        out_path.write_text(
            json.dumps(
                {"summary": summary, "batches": [], "matches": [], "remaining_matches": []},
                indent=2,
            )
            + "\n"
        )
        exceptions_path.write_text(
            json.dumps({"summary": {"count": 1}, "exceptions": [exception]}, indent=2) + "\n"
        )
        return summary
    batches = batch_item_indexes(queue, context, max_packet_bytes, max_items_per_batch)
    all_matches, all_accepted, all_remaining = [], [], []
    exceptions, batch_results, raw_paths = [], [], []

    def search_batch(entry):
        """Search one batch and return its outcome without touching shared lists."""
        batch_number, indexes = entry
        packet = global_packet(queue, context, indexes)
        retry_log = []
        try:
            if packet_size_bytes(packet) > max_packet_bytes:
                raise ValueError("cross-record packet exceeds max-packet-bytes")
            raw_path = raw_dir / f"cross_record-{batch_number:04d}.json"
            response = (
                load_resumable_batch(raw_path, indexes)
                if resume_raw_dir is not None and raw_path.is_file()
                else None
            )
            if response is None:
                response_model = retry_call(
                    lambda packet=packet: global_request(client, model, packet),
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
                raw_response = response_model.model_dump(mode="json")
                response = json.loads(response_model.output_text)
                raw_path.write_text(
                    json.dumps({"request": packet, "response": raw_response}, indent=2) + "\n"
                )
            decision = response
            matches = decision.get("matches", [])
            accepted, remaining = normalize_matches(
                matches,
                queue,
                evidence_index,
                auto_accept_threshold,
                auto_accept,
            )
            return {
                "status": "completed",
                "raw_path": str(raw_path),
                "matches": matches,
                "accepted": accepted,
                "remaining": remaining,
                "batch_result": {
                    "batch": batch_number,
                    "item_count": len(indexes),
                    "matches": len(matches),
                    "status": "completed",
                    "retry_log": retry_log,
                },
            }
        except Exception as exc:
            exception = {
                "batch": batch_number,
                "item_count": len(indexes),
                "review_item_indexes": indexes,
                "reason": "cross_record_provider_or_schema_failure",
                "error_type": type(exc).__name__,
                "retry_log": retry_log,
                "disposition": "client_review_required",
            }
            return {"status": "failed", "exception": exception}

    # `parallel_map` preserves input order, so batch results, matches, and
    # exceptions land in batch order at any worker count.
    for outcome in parallel_map(list(enumerate(batches, 1)), search_batch, workers):
        if outcome["status"] == "completed":
            raw_paths.append(outcome["raw_path"])
            all_matches.extend(outcome["matches"])
            all_accepted.extend(outcome["accepted"])
            all_remaining.extend(outcome["remaining"])
            batch_results.append(outcome["batch_result"])
            continue
        exceptions.append(outcome["exception"])
        batch_results.append({**outcome["exception"], "status": "failed"})
    successful_batches = sum(item["status"] == "completed" for item in batch_results)
    analysis_status = (
        "no_items"
        if not batches
        else "failed"
        if successful_batches == 0
        else "partial"
        if exceptions
        else "completed"
    )
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "matches": len(all_matches),
        "matches_available": successful_batches > 0,
        "analysis_status": analysis_status,
        "auto_accepted_updates": len(all_accepted),
        "remaining_matches": len(all_remaining),
        "batches": len(batches),
        "successful_batches": successful_batches,
        "provider_exceptions": len(exceptions),
        "partial_results_retained": True,
        "context_truncated_to_packet_budget": context_truncated,
        "all_source_items_retained": True,
        "production_approval_permitted": False,
        "request_limits": {
            "context_bytes_per_artifact": max_context_bytes,
            "max_packet_bytes": max_packet_bytes,
            "max_items_per_batch": max_items_per_batch,
            "evidence_artifact_bytes": max_evidence_artifact_bytes,
            "evidence_entries": max_evidence_entries,
        },
        "retry_telemetry": {
            "retry_attempts": sum(len(item.get("retry_log", [])) for item in batch_results),
            "rate_limit_attempts": sum(
                1
                for item in batch_results
                for event in item.get("retry_log", [])
                if event.get("status_code") == 429 or event.get("error_type") == "RateLimitError"
            ),
            "total_retry_delay_seconds": sum(
                float(event.get("delay_seconds") or 0)
                for item in batch_results
                for event in item.get("retry_log", [])
            ),
        },
    }
    payload = {
        "summary": summary,
        "batches": batch_results,
        "matches": all_matches,
        "auto_accepted_updates": all_accepted,
        "remaining_matches": all_remaining,
        "evidence_context": {
            "artifacts": evidence_index["context_artifacts"],
            "eligible_entries": sum(
                artifact["eligible_evidence"] for artifact in evidence_index["context_artifacts"]
            ),
            "ineligible_entries": evidence_index["ineligible_context"],
        },
        "raw_responses": raw_paths,
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n")
    exceptions_path.write_text(
        json.dumps({"summary": {"count": len(exceptions)}, "exceptions": exceptions}, indent=2)
        + "\n"
    )
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Run the optional cross-record client-review evidence pass."
    )
    parser.add_argument("queue")
    parser.add_argument("--context", action="append", default=[])
    parser.add_argument("--out", required=True)
    parser.add_argument("--exceptions", required=True)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument(
        "--resume-raw-dir",
        default=None,
        help="Reuse validated raw batches and retry only missing or invalid batch files.",
    )
    parser.add_argument("--enable", action="store_true")
    parser.add_argument(
        "--final-provider", choices=("openai", "google", "openrouter", "anthropic"), default=None
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=None)
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="batches searched concurrently; output order is unchanged by this setting",
    )
    parser.add_argument("--retry-backoff-seconds", type=float, default=None)
    parser.add_argument("--max-backoff-seconds", type=float, default=None)
    parser.add_argument("--max-context-bytes", type=int, default=None)
    parser.add_argument(
        "--max-packet-bytes",
        type=int,
        default=None,
        help="Maximum UTF-8 bytes in one evidence packet.",
    )
    parser.add_argument(
        "--max-items-per-batch",
        type=int,
        default=None,
        help="Maximum review items sent in one batch.",
    )
    parser.add_argument(
        "--max-evidence-artifact-bytes",
        type=int,
        default=None,
        help="maximum bytes for each evidence/producer/source artifact before provider I/O",
    )
    parser.add_argument(
        "--max-evidence-entries",
        type=int,
        default=None,
        help="maximum total declared entries across supplied evidence envelopes",
    )
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
    apply_shared_help(parser)
    args = parser.parse_args()
    if not args.enable and not env_bool("CLIENT_REVIEW_LLM_CROSS_RECORD_ENABLED", False):
        empty_output_path(args.out).write_text(
            json.dumps(
                {"summary": {"enabled": False, "analysis_status": "disabled"}, "matches": []},
                indent=2,
            )
            + "\n"
        )
        empty_output_path(args.exceptions).write_text(
            json.dumps({"summary": {"count": 0}, "exceptions": []}, indent=2) + "\n"
        )
        empty_output_directory(args.raw_dir)
        return
    load_project_env()
    timeout_seconds = args.timeout_seconds or env_float("CLIENT_REVIEW_LLM_TIMEOUT_SECONDS", 120.0)
    max_retries = (
        args.max_retries
        if args.max_retries is not None
        else env_int("CLIENT_REVIEW_LLM_MAX_RETRIES", 4)
    )
    retry_backoff_seconds = args.retry_backoff_seconds
    if retry_backoff_seconds is None:
        retry_backoff_seconds = env_float("CLIENT_REVIEW_LLM_RETRY_BACKOFF_SECONDS", 1.0)
    max_backoff_seconds = args.max_backoff_seconds
    if max_backoff_seconds is None:
        max_backoff_seconds = env_float("CLIENT_REVIEW_LLM_MAX_BACKOFF_SECONDS", 120.0)
    max_context_bytes = args.max_context_bytes or env_int(
        "CLIENT_REVIEW_LLM_CROSS_RECORD_MAX_CONTEXT_BYTES", 12000
    )
    max_packet_bytes = args.max_packet_bytes or env_int(
        "CLIENT_REVIEW_LLM_CROSS_RECORD_MAX_PACKET_BYTES", 200000
    )
    max_items_per_batch = args.max_items_per_batch or env_int(
        "CLIENT_REVIEW_LLM_CROSS_RECORD_MAX_ITEMS_PER_BATCH", 100
    )
    max_evidence_artifact_bytes = args.max_evidence_artifact_bytes
    if max_evidence_artifact_bytes is None:
        max_evidence_artifact_bytes = env_int(
            "CLIENT_REVIEW_LLM_CROSS_RECORD_MAX_EVIDENCE_ARTIFACT_BYTES",
            DEFAULT_MAX_EVIDENCE_ARTIFACT_BYTES,
        )
    max_evidence_entries = args.max_evidence_entries
    if max_evidence_entries is None:
        max_evidence_entries = env_int(
            "CLIENT_REVIEW_LLM_CROSS_RECORD_MAX_EVIDENCE_ENTRIES",
            DEFAULT_MAX_EVIDENCE_ENTRIES,
        )
    auto_accept_threshold = args.auto_accept_threshold
    if auto_accept_threshold is None:
        auto_accept_threshold = env_float("CLIENT_REVIEW_LLM_AUTO_ACCEPT_THRESHOLD", 0.99)
    auto_accept = args.auto_accept_llm_proposals or env_bool(
        "CLIENT_REVIEW_LLM_AUTO_ACCEPT_PROPOSALS", False
    )
    from client_review.llm import build_reviewer_client

    provider = args.final_provider or env_value(
        "LLM_POST_REVIEW_PROVIDER",
        env_value("LLM_CLIENT_REVIEW_PROVIDER", lane_provider("reasoning")),
    )
    client = build_reviewer_client(
        provider,
        env_value("CLIENT_REVIEW_LLM_CREDENTIAL_ENV", provider_credential_env(provider)),
        timeout_seconds,
        max_retries,
    )
    run_cross_record(
        args.queue,
        args.context,
        args.out,
        args.exceptions,
        args.raw_dir,
        args.model or lane_model("reasoning", provider),
        client,
        max_context_bytes=max_context_bytes,
        max_packet_bytes=max_packet_bytes,
        max_items_per_batch=max_items_per_batch,
        max_evidence_artifact_bytes=max_evidence_artifact_bytes,
        max_evidence_entries=max_evidence_entries,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        max_backoff_seconds=max_backoff_seconds,
        auto_accept=auto_accept,
        auto_accept_threshold=auto_accept_threshold,
        provider=provider,
        resume_raw_dir=args.resume_raw_dir,
        workers=args.workers or env_int("CLIENT_REVIEW_CROSS_RECORD_MAX_WORKERS", 8),
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Cross-record client review failed: {exc}")
