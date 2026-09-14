"""Tests for proposal-only review grouping and handwriting routing."""

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
grouping = importlib.import_module("client_review.grouping")
questions = importlib.import_module("client_review.questions")
queue = importlib.import_module("client_review.queue")


def test_grouping_normalizes_provider_families_and_retains_items():
    data = {
        "items": [
            {
                "document_id": "d1",
                "review_item_id": "review-1",
                "reason": "partial_disagreement",
                "field": "header.total_amount",
            },
            {"document_id": "d2", "reason": "no_majority", "field": "lines[0].description"},
            {
                "document_id": "d3",
                "reason": "google_review_flag:ambiguous",
                "field": "header.buyer_name",
            },
        ]
    }
    consensus = {
        "documents": [{"document_id": "d1", "fields": {"document_type": {"value": "unknown"}}}]
    }
    groups = grouping.build_groups(data, consensus)
    assert sum(group["item_count"] for group in groups) == 3
    assert any(group["finding_family"] == "provider_disagreement" for group in groups)
    assert any(group["finding_family"] == "provider_review_flag_family" for group in groups)
    assert all(group["protected"] for group in groups)
    assert any("review-1" in group["source_item_ids"] for group in groups)
    assert all(group["source_collection"] == "input_items" for group in groups)
    assert all(
        group["client_batch_rule_proposal"]["can_clear_without_item_review"] is False
        for group in groups
    )


def test_grouping_can_write_disabled_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIENT_REVIEW_GROUPING_ENABLED", "false")
    queue_path = tmp_path / "queue.json"
    consensus_path = tmp_path / "consensus.json"
    output = tmp_path / "grouping.json"
    queue_path.write_text(json.dumps({"items": []}))
    consensus_path.write_text(json.dumps({"documents": []}))
    assert (
        grouping.main([str(queue_path), "--consensus", str(consensus_path), "--out", str(output)])
        == 0
    )
    assert json.loads(output.read_text())["enabled"] is False


def test_grouping_uses_configured_default_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLIENT_REVIEW_GROUPING_ENABLED", "true")
    monkeypatch.setenv("CLIENT_REVIEW_GROUPING_OUTPUT_FILE", "configured.json")
    queue_path = tmp_path / "queue.json"
    consensus_path = tmp_path / "consensus.json"
    queue_path.write_text(json.dumps({"items": [{"document_id": "d1", "reason": "no_majority"}]}))
    consensus_path.write_text(json.dumps({"documents": []}))
    assert grouping.main([str(queue_path), "--consensus", str(consensus_path)]) == 0
    assert json.loads((tmp_path / "configured.json").read_text())["source_items"] == 1


def test_handwriting_comment_policy_skips_queue_item(monkeypatch):
    monkeypatch.setenv("HANDWRITING_POLICY", "comment_only")
    assert queue.handwriting_comment_only({"field": "handwriting_readings"}) is True
    assert queue.handwriting_comment_only({"field": "header.total_amount"}) is False


def test_handwriting_strict_policy_keeps_queue_item(monkeypatch):
    monkeypatch.setenv("HANDWRITING_POLICY", "strict")
    assert queue.handwriting_comment_only({"field": "handwriting_readings"}) is False


def test_comment_only_matches_the_field_path_not_the_prose(monkeypatch):
    """A finding is not about handwriting because its explanation says the word.

    On the commission run this dropped findings about `commission_amount`,
    `brand_name`, and `sales_representative_name` whose reason described a
    printed value obscured by a handwritten mark. The finding is about the
    printed value.
    """
    monkeypatch.setenv("HANDWRITING_POLICY", "comment_only")
    obscured = {
        "field": "commission_amount",
        "reason": (
            "audit:A handwritten mark overlaps the printed Total; the printed value "
            "requires visual review."
        ),
    }
    assert queue.handwriting_comment_only(obscured) is False
    for field in ("handwriting", "handwriting_regions", "handwriting_readings.hw_08"):
        assert queue.handwriting_comment_only({"field": field}) is True, field
    assert queue.handwriting_comment_only({"field": "has_handwriting"}) is True
    # The structured signal still suppresses, wherever the reading was recorded.
    assert queue.handwriting_comment_only({"field": "notes", "is_handwritten": True}) is True


def test_comment_only_never_overrides_a_control_that_said_blocking(monkeypatch):
    """Consensus reports these with `is_handwritten: false` and means them to block.

    Its own `comment_only` branch never applied, so suppressing them here is a
    second, different policy applied on top of a control's verdict. That was
    1,068 findings on the commission run.
    """
    monkeypatch.setenv("HANDWRITING_POLICY", "comment_only")
    blocking = {"field": "handwriting_readings", "cause": "no_majority", "blocking": True}
    assert queue.handwriting_comment_only(blocking) is False
    assert queue.handwriting_comment_only({**blocking, "blocking": None}) is True


def test_comment_only_never_suppresses_a_lane_failure(monkeypatch):
    """`google_handwriting_region_unreadable` is the reader failing, not a reading.

    It names no field because there is no field: the region could not be read at
    all. 838 of these were dropped from the gate on the commission run.
    """
    monkeypatch.setenv("HANDWRITING_POLICY", "comment_only")
    unreadable = {
        "document_id": "doc-1",
        "region_id": "hw-04",
        "reason": "google_handwriting_region_unreadable",
        "disposition": "client_review_required",
    }
    assert queue.handwriting_comment_only(unreadable) is False


def test_grouping_normalizers_cover_families_and_protected_rules():
    assert grouping.normalize_token("") == "unknown"
    assert grouping.template_family({"reason": "commission issue"}, {}) == "commission_report"
    assert (
        grouping.template_family({"document_type": "commission_statement"}, {})
        == "commission_statement"
    )
    assert grouping.template_family({"reason": "invoice issue"}, {}) == "invoice_like"
    assert grouping.template_family({"reason": "statement issue"}, {}) == "statement_like"
    assert grouping.template_family({"reason": "other"}, {}) == "unclassified_document_family"
    assert (
        grouping.template_family({"document_id": "d"}, {"d": "Purchase Order"}) == "purchase_order"
    )
    assert grouping.field_family("lines[0].description") == "line_description"
    assert grouping.field_family("lines[0].quantity") == "line_quantity"
    assert grouping.field_family("lines[0].amount") == "line_amount"
    assert grouping.field_family("lines[0].other") == "line_fields"
    assert grouping.field_family("handwriting_readings") == "handwriting_comment"
    assert grouping.field_family("header.total") == "header"
    assert grouping.field_family("tax") == "totals"
    assert grouping.field_family("") == "document_level"
    reasons = [
        "document_status_open_exception",
        "document_type_proposal",
        "provider_extraction_exception",
        "schema mismatch",
        "arithmetic_failed",
        "adjudication_not_unique",
        "review_flag:ambiguous",
        "invalid_numeric_value",
        "other reason",
    ]
    assert [grouping.disagreement_family(reason) for reason in reasons] == [
        "document_wrapper",
        "document_type_proposal",
        "provider_extraction_failure",
        "provider_or_schema_exception",
        "arithmetic_or_reassembly",
        "adjudication_ambiguity",
        "provider_review_flag_family",
        "validation_exception_family",
        "other_reason",
    ]
    assert grouping.protected({"reason": "arithmetic_failed"}) is True
    assert grouping.protected({"reason": "formatting_review"}) is False
    assert grouping.protected({"reason": "ordinary"}) is True


def test_grouping_rejects_malformed_objects(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("[]")
    try:
        grouping.load_object(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("malformed object was accepted")
    try:
        grouping.build_groups({"items": ["bad"]}, {})
    except ValueError:
        pass
    else:
        raise AssertionError("malformed queue item was accepted")
    assert grouping.document_types(
        {
            "documents": [
                "not-a-document",
                {"document_id": "d1", "fields": []},
                {"document_id": "d2", "fields": {"document_type": "not-an-object"}},
            ]
        }
    ) == {"d1": None}


def test_final_queue_skips_handwriting_documents_and_items(monkeypatch):
    monkeypatch.setenv("HANDWRITING_POLICY", "comment_only")
    data = {
        "exceptions": [{"field": "handwriting_readings", "reason": "comment"}],
        "documents": [{"document_id": "d1", "review_status": "open", "field": "handwriting"}],
    }
    assert queue.review_items(data) == []


def test_the_same_question_is_not_asked_once_per_provider():
    """`openai_...` and `openrouter_...` name the engine, not the question.

    The client is being asked what the document is, not which engine disagreed.
    Split by provider, the same question reached the pack twice, generically
    worded both times.
    """
    families = {
        grouping.disagreement_family(reason)
        for reason in (
            "openai_document_type_disagrees_with_intake_rule",
            "openrouter_document_type_disagrees_with_intake_rule",
        )
    }
    assert families == {"document_type_disagrees_with_intake_rule"}
    assert grouping.disagreement_family("openai_document_type_proposal") == "document_type_proposal"


def test_a_declined_document_type_is_not_a_document_type():
    """`"unknown"` is consensus refusing to claim a type, not an answer."""
    assert grouping.unresolved("unknown")
    assert grouping.unresolved("None")
    assert grouping.unresolved(None)
    assert grouping.unresolved("")
    assert not grouping.unresolved("commission_statement")


def test_classification_supplies_the_type_extraction_declined_to_claim():
    """Two artifacts answer this and only one was being read.

    The extraction consensus writes `"unknown"` where its lanes could not agree,
    which was 691 of 718 documents on the commission run -- the whole reason
    `classification_consensus.py` exists as a separate lane. It had accepted 686
    of those same documents on two independent model vendors agreeing. Reading
    only the first labelled 691 documents `unclassified_document_family` and put
    one undifferentiated question to the client about a corpus whose type was
    already settled.
    """
    consensus = {
        "documents": [
            {"document_id": "d1", "fields": {"document_type": {"value": "unknown"}}},
            {"document_id": "d2", "fields": {"document_type": {"value": "purchase_order"}}},
        ]
    }
    classification = {
        "artifact_type": "classification_consensus_v1",
        "accepted": [
            {"document_id": "d1", "document_type": "commission_statement"},
            {"document_id": "d3", "document_type": "payment_confirmation"},
        ],
    }
    types = grouping.document_types(consensus, classification)
    assert types["d1"] == "commission_statement"
    assert types["d3"] == "payment_confirmation"
    # A later artifact that says nothing about d2 leaves its resolved type alone.
    assert types["d2"] == "purchase_order"

    # And order matters only among resolved answers: an "unknown" arriving later
    # never overwrites a type an earlier artifact settled.
    late_unknown = {"documents": [{"document_id": "d3", "fields": {"document_type": None}}]}
    assert grouping.document_types(classification, late_unknown)["d3"] == "payment_confirmation"


def test_the_family_falls_back_to_text_only_when_no_artifact_knows_the_type():
    """The substring fallback invented families while the real answer sat unread.

    On the commission run it produced 190 `purchase_order`, 142 `invoice_like`
    and 110 `statement_like` groups out of words in reason strings. With
    classification supplied those drop to one each.
    """
    item = {"document_id": "d1", "reason": "invoice_total_mismatch"}
    assert grouping.template_family(item, {}) == "invoice_like"
    assert grouping.template_family(item, {"d1": "unknown"}) == "invoice_like"
    assert grouping.template_family(item, {"d1": "commission_statement"}) == "commission_statement"


def test_build_groups_labels_families_from_the_classification_artifact():
    # A field whose own name says nothing about the document family, so the
    # text fallback cannot supply the answer the artifacts are meant to.
    queue = {
        "items": [
            {"document_id": "d1", "field": "header.total_amount", "reason": "single_engine"},
            {"document_id": "d2", "field": "header.total_amount", "reason": "single_engine"},
        ]
    }
    consensus = {
        "documents": [
            {"document_id": document, "fields": {"document_type": {"value": "unknown"}}}
            for document in ("d1", "d2")
        ]
    }
    classification = {
        "accepted": [
            {"document_id": "d1", "document_type": "commission_statement"},
            {"document_id": "d2", "document_type": "payment_confirmation"},
        ]
    }
    without = grouping.build_groups(queue, consensus)
    assert {group["template_family"] for group in without} == {"unclassified_document_family"}

    with_types = grouping.build_groups(queue, consensus, classification)
    assert {group["template_family"] for group in with_types} == {
        "commission_statement",
        "payment_confirmation",
    }


def test_document_types_ignores_entries_it_cannot_read():
    """Malformed input narrows the answer; it never invents or crashes on one."""
    types = grouping.document_types(
        "not an artifact",
        {"documents": ["not a document", {"fields": "not a mapping"}]},
        {"accepted": ["not an entry", {"document_type": "commission_report"}]},
        {"documents": [{"document_id": "", "fields": {"document_type": {"value": "x"}}}]},
        {"documents": [{"document_id": "d1", "fields": {"document_type": {"value": "invoice"}}}]},
    )
    # The entry with no document_id and the unreadable ones contribute nothing.
    assert types == {"d1": "invoice"}


def test_a_resolved_type_survives_an_artifact_that_declines_to_claim_one():
    """Order among artifacts must not let a later `unknown` erase an answer."""
    resolved = {"accepted": [{"document_id": "d1", "document_type": "commission_report"}]}
    declined = {
        "documents": [{"document_id": "d1", "fields": {"document_type": {"value": "unknown"}}}]
    }
    assert grouping.document_types(resolved, declined) == {"d1": "commission_report"}
    # And an unresolved value still lands when nothing better is known, so the
    # document is present in the index rather than silently missing from it.
    assert grouping.document_types(declined) == {"d1": "unknown"}


def test_a_provider_that_read_the_page_is_not_a_provider_that_failed():
    """`provider_extraction_exception` does not mean the provider failed.

    On the commission run all 1,319 of them recorded `review_status:
    review_queued` with `provider_review_flags` attached -- a provider that read
    the page and raised a question about one field. Treated as failures they
    became "We could not read these pages at all", and 630 documents went to the
    client asking for a rescan. Every one had been read: all 630 carry extracted
    fields and accepted values, many over a hundred. The client replied that the
    pages are "perfectly clear".
    """
    flagged = {
        "document_id": "p0001",
        "field": "page",
        "reason": "provider_extraction_exception",
        "engines": {
            "openai/gpt-5.6-luna": {
                "review_status": "review_queued",
                "provider_review_flags": [{"field": "statement_total", "reason": "not visible"}],
            }
        },
    }
    assert grouping.provider_reading_survived(flagged)
    assert grouping.disagreement_family(flagged["reason"], flagged) == "provider_review_flag_family"


def test_a_genuine_provider_failure_keeps_the_failure_family():
    """A reading that did not survive is still a failure, and still says so."""
    for engines in (
        {},
        None,
        {"openai": {"review_status": "failed"}},
        {"openai": {"review_status": "review_queued"}},
        {"openai": "not a mapping"},
    ):
        item = {"reason": "provider_extraction_exception", "engines": engines}
        assert not grouping.provider_reading_survived(item), engines
        assert grouping.disagreement_family(item["reason"], item) == "provider_extraction_failure"
    # And with no item at all, the conservative reading is failure.
    assert grouping.disagreement_family("provider_extraction_exception") == (
        "provider_extraction_failure"
    )


def test_the_failure_question_does_not_blame_the_client_s_paperwork():
    """It is our system failing, and the first option is to re-read."""
    template = questions.template_for("provider_extraction_failure")
    assert "could not read these pages at all" not in template["headline"]
    assert "not a judgement about your paperwork" in template["what_we_found"]
    assert template["answer_options"][0].startswith("Re-read them")


def test_an_audit_finding_groups_by_its_concern_not_its_sentence():
    """The audit lane writes a sentence per cell and every sentence is different.

    On the commission corpus 11,251 audit findings carried 11,242 distinct
    reason strings, so grouping on the reason produced 4,463 families of which
    4,455 held exactly one item. A third of the client review queue arrived as
    individual prose to be read one at a time. The wording is unique; the
    concerns are not.
    """
    cases = {
        "audit:The Total Commissions to date row repeats the project total.": "audit_totals_or_to_date_row",
        "audit:A handwritten mark overlaps the printed percentage.": "audit_handwriting_present",
        "audit:The value is clipped at the column divider.": "audit_value_clipped_or_obscured",
        "audit:The column carries values under no readable header.": "audit_no_header_or_label",
        "audit:A direct conflict exists between the page and provider OCR.": "audit_sources_conflict",
        "audit:The relationship of the amount to the payment row is unclear.": "audit_field_association_unclear",
        "audit:The retained page visibly contains populated commission columns.": "audit_page_populated_contrary_to_concern",
        "audit:Visible agreement: both readings show the same figure.": "audit_agreement_does_not_resolve",
        "audit:No visible statement total appears anywhere on the page.": "audit_value_not_visible",
        "audit:Something the phrase list has never seen before at all.": "audit_unclassified",
    }
    for reason, family in cases.items():
        assert grouping.audit_concern(reason) == family, reason
        assert grouping.disagreement_family(reason) == family, reason


def test_an_audit_finding_never_falls_through_to_its_own_wording():
    """Falling through is what produced 4,455 groups of one."""
    unique = "audit:" + "a sentence no phrase list anticipates " * 5
    assert grouping.audit_concern(unique) == grouping.AUDIT_DEFAULT
    assert grouping.disagreement_family(unique) == grouping.AUDIT_DEFAULT


def test_only_audit_findings_take_the_audit_route():
    """Every other family keeps its existing classification exactly."""
    assert grouping.audit_concern("single_engine") is None
    assert grouping.audit_concern("") is None
    assert grouping.audit_concern(None) is None
    assert grouping.disagreement_family("no_majority") == "provider_disagreement"
    assert grouping.disagreement_family("arithmetic_not_provable") == "arithmetic_or_reassembly"


def test_the_audit_concern_wins_over_a_word_appearing_in_its_prose():
    """An audit sentence mentioning a provider is not a provider exception.

    6,355 audit findings grouped as `provider_or_schema_exception` purely
    because the word "provider" appeared somewhere in the sentence, which is the
    same substring mistake in a different place.
    """
    reason = "audit:The provider OCR and the retained page both show a to date row."
    assert grouping.disagreement_family(reason) == "audit_totals_or_to_date_row"


def test_every_audit_concern_has_a_question_a_client_can_answer():
    """A new family with no template is asked about generically, which is no question."""
    families = [family for family, _ in grouping.AUDIT_CONCERNS] + [grouping.AUDIT_DEFAULT]
    for family in families:
        template = questions.template_for(family)
        assert template is not questions.DEFAULT_TEMPLATE, family
        assert template["answer_options"], family
        assert template["decision_choice"], family
