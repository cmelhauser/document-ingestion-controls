"""Shared fail-closed classification for protected review work.

The review pipeline emits reason strings from several independent lanes.  Keep
the safety decision in one place so grouping, proposal reduction, and dataset
review cannot drift into different interpretations of the same finding.
"""

from __future__ import annotations

from typing import Any

SAFE_REASON_CODES = {
    "document_type_proposal",
    "formatting_review",
    "google_genai_document_type_disagrees_with_intake_rule",
    "google_genai_document_type_proposal",
    "no_high_signal_document_type",
    "openai_document_type_disagrees_with_intake_rule",
    "openai_document_type_proposal",
    "openrouter_document_type_disagrees_with_intake_rule",
    "openrouter_document_type_proposal",
}
PROTECTED_REASON_CODES = {
    "no_majority",
    "partial_disagreement",
    "upstream_gate_blocked",
}
PROTECTED_TOKENS = (
    "adjudication",
    "arithmetic",
    "conflict",
    "exception",
    "failure",
    "financial",
    "handwriting",
    "htr",
    "identity",
    "jbig2",
    "missing",
    "no_consensus",
    "no_extractable_text",
    "provider",
    "reassembly",
    "review_flag",
    "schema",
    "signature",
    "unresolved",
)
PROTECTED_FIELD_TOKENS = (
    "account",
    "address",
    "amount",
    "buyer",
    "currency",
    "customer",
    "entity",
    "handwriting",
    "identity",
    "party",
    "price",
    "seller",
    "subtotal",
    "tax",
    "total",
    "vendor",
)


def protection_reasons(item: dict[str, Any]) -> tuple[str, ...]:
    """Return deterministic protection causes; unknown reasons fail closed."""
    reason = str(item.get("reason") or item.get("cause") or "").casefold().strip()
    field_text = " ".join(
        str(item.get(key, "")) for key in ("field", "category", "review_source")
    ).casefold()
    causes: list[str] = []
    if reason in PROTECTED_REASON_CODES:
        causes.append(f"reason:{reason}")
    causes.extend(f"reason_token:{token}" for token in PROTECTED_TOKENS if token in reason)
    causes.extend(f"field_token:{token}" for token in PROTECTED_FIELD_TOKENS if token in field_text)
    if causes:
        return tuple(dict.fromkeys(causes))
    if reason in SAFE_REASON_CODES:
        return ()
    return ("unclassified_reason",)


def protected(item: dict[str, Any]) -> bool:
    """Return whether an item must remain outside every reduction path."""
    return bool(protection_reasons(item))
