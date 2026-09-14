"""Tests for review-link metadata on CRM handoff artifacts."""

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

review_export_link = importlib.import_module("review_export_link")


def test_validate_review_context_accepts_expected_shape():
    review_context = review_export_link.validate_review_context(
        {
            "schema_version": "review_export_link_v1",
            "source_kind": "visual_ingestion_session",
            "session_id": "session-1",
            "source_set_sha256": "a" * 64,
        }
    )
    assert review_context == {
        "schema_version": "review_export_link_v1",
        "source_kind": "visual_ingestion_session",
        "session_id": "session-1",
        "source_set_sha256": "a" * 64,
    }
    assert review_export_link.validate_review_context(None) is None


@pytest.mark.parametrize(
    ("value", "message"),
    (
        ("bad", "object"),
        ({}, "contain only"),
        (
            {
                "schema_version": "wrong",
                "source_kind": "visual_ingestion_session",
                "session_id": "session-1",
                "source_set_sha256": "a" * 64,
            },
            "schema_version",
        ),
        (
            {
                "schema_version": "review_export_link_v1",
                "source_kind": "wrong",
                "session_id": "session-1",
                "source_set_sha256": "a" * 64,
            },
            "source_kind",
        ),
        (
            {
                "schema_version": "review_export_link_v1",
                "source_kind": "visual_ingestion_session",
                "session_id": " session-1 ",
                "source_set_sha256": "a" * 64,
            },
            "session_id",
        ),
        (
            {
                "schema_version": "review_export_link_v1",
                "source_kind": "visual_ingestion_session",
                "session_id": "session-1",
                "source_set_sha256": "bad",
            },
            "source_set_sha256",
        ),
    ),
)
def test_validate_review_context_rejects_invalid_shapes(value, message):
    with pytest.raises(ValueError, match=message):
        review_export_link.validate_review_context(value)


def test_review_context_from_inputs_requires_paired_values():
    assert review_export_link.review_context_from_inputs() is None
    assert review_export_link.review_context_from_inputs("session-1", "b" * 64) == {
        "schema_version": "review_export_link_v1",
        "source_kind": "visual_ingestion_session",
        "session_id": "session-1",
        "source_set_sha256": "b" * 64,
    }
    with pytest.raises(ValueError, match="supplied together"):
        review_export_link.review_context_from_inputs("session-1", None)
