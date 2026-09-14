"""Tests for the shared fail-closed review protection taxonomy."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
protection = importlib.import_module("client_review.protection")


@pytest.mark.parametrize(
    "item",
    [
        {},
        {"reason": "novel_future_reason"},
        {"reason": "unknown_provider_document_type_proposal"},
        {"reason": "no_majority"},
        {"reason": "some_provider_failure"},
        {"reason": "formatting_review", "field": "vendor_address"},
        {"reason": "formatting_review", "category": "identity"},
        {"reason": "formatting_review", "review_source": "financial total"},
    ],
)
def test_unknown_explicit_and_sensitive_items_are_protected(item):
    assert protection.protected(item)
    assert protection.protection_reasons(item)


@pytest.mark.parametrize(
    "reason",
    [
        "document_type_proposal",
        "formatting_review",
        "no_high_signal_document_type",
        "openai_document_type_proposal",
        "google_genai_document_type_disagrees_with_intake_rule",
    ],
)
def test_narrow_non_sensitive_reason_allowlist(reason):
    assert protection.protection_reasons({"reason": reason, "field": "formatting_note"}) == ()
    assert not protection.protected({"reason": reason, "field": "formatting_note"})


def test_protection_causes_are_deduplicated_and_deterministic():
    causes = protection.protection_reasons(
        {"reason": "provider_schema_failure", "field": "total_amount"}
    )
    assert causes == (
        "reason_token:failure",
        "reason_token:provider",
        "reason_token:schema",
        "field_token:amount",
        "field_token:total",
    )
