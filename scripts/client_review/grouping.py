#!/usr/bin/env python3
"""Build proposal-only root-cause groups for a client review queue.

The exhaustive queue remains the source of truth.  This command creates a
smaller decision surface by grouping repeated template, provider-disagreement,
and wrapper findings.  A group is a proposed batch rule, never an approval or
deletion of its underlying evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cli_help import apply_shared_help
from runtime_config import load_project_env

from client_review.protection import protected

# `no_majority` is a genuine disagreement: both engines read the field and
# returned different values, so the client chooses between them. `partial_disagreement`
# is not a disagreement at all -- only one engine read the field, and there is no
# second value to choose. Grouping them together asked 52,528 single-reading items
# "which of the two readings is right?", a question with no answer.
PROVIDER_REASONS = {"no_majority"}
SINGLE_READING_REASONS = {"partial_disagreement"}
WRAPPER_REASONS = {"document_status_open_exception", "register_review_required"}


def load_object(path: Path) -> dict[str, Any]:
    """Load a required JSON object."""
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def normalize_token(value: Any) -> str:
    """Normalize free-text labels into stable group-key tokens."""
    token = re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")
    return token or "unknown"


# What a lane writes when it will not claim a document type. Treated as an
# absent answer everywhere, so a later artifact that does have one can supply it.
UNRESOLVED_DOCUMENT_TYPES = frozenset({"unknown", "none", ""})


def template_family(item: dict[str, Any], document_types: dict[str, Any]) -> str:
    """Return a conservative document-family label without inferring a fact."""
    document_id = str(item.get("document_id") or item.get("page_id") or "")
    for candidate in (
        item.get("document_type"),
        item.get("model_document_type"),
        document_types.get(document_id),
    ):
        if not unresolved(candidate):
            return normalize_token(candidate)
    text = " ".join(
        str(item.get(key, "")) for key in ("reason", "field", "review_source")
    ).casefold()
    if "commission" in text:
        return "commission_report"
    if "invoice" in text:
        return "invoice_like"
    if "statement" in text:
        return "statement_like"
    return "unclassified_document_family"


def field_family(field: Any) -> str:
    """Collapse indexed fields into a stable presentation family."""
    value = str(field or "").casefold()
    if value.startswith("header."):
        return "header"
    if value.startswith("lines["):
        if "description" in value:
            return "line_description"
        if "quantity" in value:
            return "line_quantity"
        if "price" in value or "amount" in value:
            return "line_amount"
        return "line_fields"
    if "handwriting" in value:
        return "handwriting_comment"
    if "total" in value or "subtotal" in value or "tax" in value:
        return "totals"
    return normalize_token(value) if value else "document_level"


def provider_reading_survived(item: Any) -> bool:
    """Report whether a provider-exception item still carries a provider's reading.

    `provider_extraction_exception` does not mean the provider failed. On the
    commission run all 1,319 of them recorded `review_status: review_queued`
    with `provider_review_flags` attached -- a provider that read the page and
    raised a question about one field, such as "no overall statement total is
    visible on this page; the footer identifies it as Page 2 of 6".

    Treated as a failure, those became the question "We could not read these
    pages at all", and 630 documents were put to the client with a request to
    rescan them. Every one had been read: all 630 carry extracted fields and
    accepted values, many over a hundred. The client answered that they are
    "perfectly clear" and to reread them from the source.

    A reading that survived is a review flag. A genuine failure -- no engine
    entry, or one that is not queued for review -- keeps the failure wording.
    """
    engines = item.get("engines") if isinstance(item, dict) else None
    if not isinstance(engines, dict) or not engines:
        return False
    return all(
        isinstance(entry, dict)
        and entry.get("review_status") == "review_queued"
        and entry.get("provider_review_flags")
        for entry in engines.values()
    )


# What a table-audit finding is *about*, in the order the tests are applied.
# Each entry is a family name and the phrases that identify it.
#
# The audit lane writes a sentence per cell, and every sentence is different:
# 11,251 findings on the commission corpus carried 11,242 distinct reason
# strings. Grouping on the reason therefore produced 4,463 families of which
# 4,455 held exactly one item, and a third of the client review queue arrived as
# individual prose to be read one at a time. The wording is unique; the concerns
# are not. Classified by concern the same 11,251 fall into nine groups, and two
# of those nine -- totals rows and handwriting -- are questions this client had
# already answered.
AUDIT_CONCERNS = (
    (
        "audit_totals_or_to_date_row",
        (
            "to date",
            "subtotal",
            "sub-total",
            "bottom total",
            "total row",
            "grand total",
            "total commissions",
            "p.o. total",
            "po total",
        ),
    ),
    ("audit_handwriting_present", ("handwrit", "hand-writ")),
    (
        "audit_value_clipped_or_obscured",
        ("clip", "obscur", "truncat", "cut off", "overlap"),
    ),
    (
        "audit_no_header_or_label",
        ("header", "label", "column heading", "unlabelled", "unlabeled"),
    ),
    (
        "audit_sources_conflict",
        ("conflict", "differs from", "does not match", "discrepan"),
    ),
    (
        "audit_field_association_unclear",
        ("relationship", "which row", "association", "belongs to", "attribut"),
    ),
    (
        "audit_page_populated_contrary_to_concern",
        ("populated", "contrary to", "corroborat"),
    ),
    (
        "audit_agreement_does_not_resolve",
        ("visible agreement", "agreement with the initial concern"),
    ),
    (
        "audit_value_not_visible",
        ("not visible", "no visible", "cannot be located", "illegible"),
    ),
)
AUDIT_PREFIX = "audit:"
AUDIT_DEFAULT = "audit_unclassified"
# A reason token names a machine-generated cause and is short. A sentence is not
# a cause, and keying a group on one makes every distinct wording its own group.
# The audit prefix learned that at 4,455 groups of one; the same fallback is
# still open to every reason that is not audit-prefixed, and it had already
# started: one cause split into five families because the sentence carried the
# vendor and document type inside it -- "independent model vendors disagree on
# the document type: openai payment_confirmation, x-ai remittance_advice".
#
# Measured over this corpus, every genuine family runs to eight words or fewer
# and they carry effectively all of the volume; every longer key was a sentence
# with a single-digit item count. So the bound is set just past the real ones.
MAX_FAMILY_WORDS = 9
UNCLASSIFIED_DEFAULT = "unclassified_finding"


def audit_concern(reason: Any) -> str | None:
    """Classify a table-audit finding by what it is about, not by its wording.

    Returns None for anything that is not an audit finding, so every other
    family keeps its existing route.
    """
    value = str(reason or "")
    if not value.lower().startswith(AUDIT_PREFIX):
        return None
    text = value[len(AUDIT_PREFIX) :].casefold()
    for family, phrases in AUDIT_CONCERNS:
        if any(phrase in text for phrase in phrases):
            return family
    # Never fall through to the sentence itself: that is what produced 4,455
    # groups of one.
    return AUDIT_DEFAULT


def disagreement_family(reason: Any, item: Any = None) -> str:
    """Classify repeated disagreement causes for proposal grouping."""
    value = str(reason or "")
    # An audit finding's family comes from what it says, before any test that
    # merely looks for a word like "provider" somewhere in its prose.
    concern = audit_concern(value)
    if concern is not None:
        return concern
    lowered = value.casefold()
    if value in SINGLE_READING_REASONS or "partial_disagreement" in lowered:
        return "single_reading"
    if value in PROVIDER_REASONS or "no_majority" in lowered:
        return "provider_disagreement"
    if value in WRAPPER_REASONS or "document_status_" in lowered or "register_review" in lowered:
        return "document_wrapper"
    if lowered.endswith("document_type_proposal") or "document_type_proposal" in lowered:
        return "document_type_proposal"
    # One question, asked once per provider. `openai_...` and `openrouter_...`
    # name the engine that disagreed, which is not what the client is being
    # asked -- they are being asked what the document is. Left split, the same
    # question reached the pack twice with generic wording both times.
    if "document_type_disagrees_with_intake_rule" in lowered:
        return "document_type_disagrees_with_intake_rule"
    if "provider_extraction_exception" in lowered:
        # The name says failure; the evidence usually says the opposite.
        if provider_reading_survived(item):
            return "provider_review_flag_family"
        return "provider_extraction_failure"
    if "provider" in lowered or "schema" in lowered:
        return "provider_or_schema_exception"
    if "arithmetic" in lowered or "reassembly" in lowered:
        return "arithmetic_or_reassembly"
    if "adjudication_not_unique" in lowered:
        return "adjudication_ambiguity"
    if "review_flag:" in lowered:
        return "provider_review_flag_family"
    if lowered.startswith(("invalid_", "negative_", "unsupported_currency")):
        return "validation_exception_family"
    # A payment export whose rows carry no document key is not a defect in the
    # export. It usually means a payment settles many documents at once rather
    # than one, and only the client knows which. Without its own family it fell
    # through to a raw reason token and was asked about in generic wording.
    if "payment_row_has_no" in lowered:
        return "payment_settlement_grain"
    token = normalize_token(value)
    # Bounded rather than raw: an unrecognized reason still gets asked about,
    # but it cannot bring its own sentence along as a group key. Questions built
    # from this family carry `generic_wording`, so what the bucket costs shows
    # up in the pack instead of being lost quietly.
    if token.count("_") + 1 > MAX_FAMILY_WORDS:
        return UNCLASSIFIED_DEFAULT
    return token


def document_types(*artifacts: dict[str, Any]) -> dict[str, Any]:
    """Index accepted or proposed document types without changing them.

    Two artifacts answer this question and only one of them was being read.

    The extraction consensus carries ``fields.document_type``, and on a corpus
    the extraction lanes could not agree a type for, that value is the string
    ``"unknown"`` -- 691 of 716 documents on the commission run. That is not a
    missing answer, it is consensus correctly declining to claim one, and it is
    the whole reason ``classification_consensus.py`` exists as a separate lane.
    That lane had accepted 686 of the same documents as commission statements,
    reports, and payment confirmations on two independent model vendors
    agreeing.

    Reading only the first, grouping labelled 691 documents
    ``unclassified_document_family`` and asked the client one undifferentiated
    question about a corpus whose type was already settled.

    Later artifacts win, so pass the classification consensus after the
    extraction consensus. An explicit ``"unknown"`` never overwrites a type an
    earlier artifact resolved.
    """
    result: dict[str, Any] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        for document in artifact.get("documents", []):
            if not isinstance(document, dict):
                continue
            fields = document.get("fields", {})
            field = fields.get("document_type", {}) if isinstance(fields, dict) else {}
            if isinstance(field, dict):
                _record(result, document.get("document_id"), field.get("value"))
        # `classification_consensus_v1` records one accepted type per document,
        # each carrying the vendors that agreed it.
        for accepted in artifact.get("accepted", []):
            if isinstance(accepted, dict):
                _record(result, accepted.get("document_id"), accepted.get("document_type"))
    return result


def _record(result: dict[str, Any], document_id: Any, value: Any) -> None:
    """Keep a resolved type; never let an unresolved one overwrite it."""
    key = str(document_id or "")
    if not key:
        return
    if unresolved(value) and not unresolved(result.get(key)):
        return
    result[key] = value


def unresolved(value: Any) -> bool:
    """Report whether a document type is a real answer or a declined one."""
    return value is None or str(value).casefold() in UNRESOLVED_DOCUMENT_TYPES


def build_groups(
    queue: dict[str, Any],
    consensus: dict[str, Any],
    *classifications: dict[str, Any],
) -> list[dict[str, Any]]:
    """Create stable, evidence-linked presentation groups.

    Classification artifacts are read after the extraction consensus, so a type
    that lane accepted supplies the one extraction declined to claim.
    """
    indexed: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    types = document_types(consensus, *classifications)
    for index, item in enumerate(queue.get("items", [])):
        if not isinstance(item, dict):
            raise ValueError("queue items must be objects")
        family = template_family(item, types)
        disagreement = disagreement_family(item.get("reason"), item)
        indexed[(family, disagreement)].append((index, item))
    groups = []
    for group_number, ((family, disagreement), members) in enumerate(sorted(indexed.items()), 1):
        items = [item for _, item in members]
        document_ids = sorted(
            {str(item.get("document_id") or item.get("page_id") or "") for item in items}
        )
        source_item_ids = sorted(
            {
                str(value)
                for item in items
                for value in ([item.get("review_item_id")] + list(item.get("source_item_ids", [])))
                if value not in (None, "")
            }
        )
        groups.append(
            {
                "group_id": f"decision_group:{group_number:05d}",
                "template_family": family,
                "finding_family": disagreement,
                "field_families": sorted({field_family(item.get("field")) for item in items}),
                "item_count": len(items),
                "document_count": len(document_ids),
                "source_item_indexes": [index for index, _ in members],
                "source_item_ids": source_item_ids,
                "source_collection": "input_items",
                "document_ids": document_ids,
                "representative_reasons": sorted(
                    {str(item.get("reason", "review_required")) for item in items}
                )[:10],
                "protected": any(protected(item) for item in items),
                "all_source_items_retained": True,
                "client_batch_rule_proposal": {
                    "decision_required": True,
                    "approval_scope": "matching_template_and_finding_family",
                    "can_clear_without_item_review": False,
                    "retains_all_source_items": True,
                },
            }
        )
    return groups


def main(argv: list[str] | None = None) -> int:
    """Write the proposal-only grouping artifact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queue", type=Path)
    parser.add_argument("--consensus", type=Path, required=True)
    parser.add_argument(
        "--classifications",
        type=Path,
        action="append",
        default=None,
        metavar="ARTIFACT",
        help=(
            "Repeatable classification_consensus_v1 artifact. The extraction consensus writes "
            "'unknown' where its lanes could not agree a document type, which is most of a "
            "corpus this lane was built for; without this the groups are labelled "
            "unclassified_document_family even where classification accepted a type."
        ),
    )
    parser.add_argument("--out", type=Path, default=None)
    apply_shared_help(parser)
    args = parser.parse_args(argv)
    load_project_env()
    output = args.out or Path(
        os.environ.get("CLIENT_REVIEW_GROUPING_OUTPUT_FILE", "client_review_grouping.json")
    )
    if os.environ.get("CLIENT_REVIEW_GROUPING_ENABLED", "false").casefold() != "true":
        payload = {
            "schema_version": "1.0",
            "enabled": False,
            "production_approval_permitted": False,
        }
    else:
        queue = load_object(args.queue)
        consensus = load_object(args.consensus)
        classifications = [load_object(path) for path in (args.classifications or [])]
        groups = build_groups(queue, consensus, *classifications)
        payload = {
            "schema_version": "1.0",
            "generated_at": datetime.now(UTC).isoformat(),
            "grouping_method": "data_derived_unique_template_and_finding_family",
            "requested_group_count": None,
            "source_queue": str(args.queue),
            "source_consensus": str(args.consensus),
            "source_classifications": [str(path) for path in (args.classifications or [])],
            "documents_with_a_resolved_type": sum(
                1
                for value in document_types(consensus, *classifications).values()
                if not unresolved(value)
            ),
            "source_items": len(queue.get("items", [])),
            "decision_groups": groups,
            "decision_group_count": len(groups),
            "group_count_derived_from_data": True,
            "all_source_items_retained": True,
            "production_approval_permitted": False,
            "gate_status": "blocked_pending_client_review",
            "findings": [
                "Groups are presentation-only proposals; client approval is required before any batch rule is applied.",
                "Protected findings remain item-level review work.",
            ],
        }
    output.write_text(json.dumps(payload, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
