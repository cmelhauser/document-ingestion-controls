"""Validate optional review-link metadata for CRM handoff artifacts."""

import re

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def validate_review_context(review_context):
    """Return a normalized review-link object or None.

    The current contract links a generated Phase 6 package back to a reviewed
    visual-intake session without claiming that the package was derived directly
    from an intake proposal.
    """
    if review_context is None:
        return None
    if not isinstance(review_context, dict):
        raise ValueError("review context must be an object")
    if set(review_context) != {
        "schema_version",
        "source_kind",
        "session_id",
        "source_set_sha256",
    }:
        raise ValueError(
            "review context must contain only schema_version, source_kind, session_id, and source_set_sha256"
        )
    if review_context["schema_version"] != "review_export_link_v1":
        raise ValueError("review context schema_version must be review_export_link_v1")
    if review_context["source_kind"] != "visual_ingestion_session":
        raise ValueError("review context source_kind must be visual_ingestion_session")
    session_id = review_context["session_id"]
    if not isinstance(session_id, str) or not session_id or session_id != session_id.strip():
        raise ValueError(
            "review context session_id must be a non-empty string without surrounding whitespace"
        )
    source_set_sha256 = review_context["source_set_sha256"]
    if not isinstance(source_set_sha256, str) or not SHA256_PATTERN.fullmatch(source_set_sha256):
        raise ValueError(
            "review context source_set_sha256 must be a 64-character lowercase hexadecimal SHA-256"
        )
    return {
        "schema_version": "review_export_link_v1",
        "source_kind": "visual_ingestion_session",
        "session_id": session_id,
        "source_set_sha256": source_set_sha256,
    }


def review_context_from_inputs(session_id=None, source_set_sha256=None):
    """Build a validated review-link object from paired optional inputs."""
    if session_id is None and source_set_sha256 is None:
        return None
    if not isinstance(session_id, str) or not isinstance(source_set_sha256, str):
        raise ValueError("review session_id and source_set_sha256 must be supplied together")
    return validate_review_context(
        {
            "schema_version": "review_export_link_v1",
            "source_kind": "visual_ingestion_session",
            "session_id": session_id,
            "source_set_sha256": source_set_sha256,
        }
    )
