"""Tests for carrying an operator-authorized client decision into classification."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

amend = importlib.import_module("classification_amend")


def write(path, value):
    path.write_text(json.dumps(value))
    return path


def classification(accepted=("doc-kept",)):
    return {
        "artifact_type": "classification_consensus_v1",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "summary": {"documents": 4, "accepted": len(accepted), "unresolved": 4 - len(accepted)},
        "accepted": [
            {
                "document_id": document_id,
                "page_id": document_id,
                "document_type": "commission_report",
                "agreeing_vendors": ["openai", "x-ai"],
                "evidence": "independent_model_vendor_agreement",
            }
            for document_id in accepted
        ],
    }


def patch(patch_id="patch-1", family="commission_statement", documents=("doc-a", "doc-b")):
    return {
        "patch_id": patch_id,
        "change_type": "template_registry_rule",
        "append_only_payload": {
            "template_registry_id": "registry-1",
            "template_id": "observed-1",
            "layout_signature": {"document_family": family, "ordered_headers": ["total"]},
        },
        "impact": {"document_ids": list(documents), "review_item_ids": []},
    }


def authorization(patches=None, status="operator_authorized", authorized=("patch-1",)):
    return {
        "authorization_id": "authorization-1",
        "authorization_status": status,
        "operator_id": "Operator Name",
        "authorized_patch_ids": list(authorized),
        "deferred_patch_ids": [],
        "compiled_plan": {"patches": list(patches if patches is not None else [patch()])},
    }


def test_authorized_client_decisions_are_appended_with_their_own_evidence():
    """A client decision fills a gap consensus left, and says that it did."""
    resolved, result = amend.amend(classification(), authorization())
    assert resolved == {"doc-a": "commission_statement", "doc-b": "commission_statement"}
    assert result["summary"] == {
        "documents": 4,
        "accepted": 3,
        "unresolved": 1,
        "client_decided": 2,
        "superseded_prior_client_decisions": 0,
        "reaffirmed_prior_client_decisions": 0,
        # No records were supplied, so the check did not run. That is reported
        # as "not checked" rather than as zero contradictions found.
        "content_contradiction_checked": False,
        "withheld_contradicted_by_content": None,
    }
    added = [item for item in result["accepted"] if item["document_id"] != "doc-kept"]
    assert [item["document_id"] for item in added] == ["doc-a", "doc-b"]
    for item in added:
        # Never presentable as vendor agreement.
        assert item["evidence"] == "operator_authorized_client_decision"
        assert item["agreeing_vendors"] == []
        assert item["document_type"] == "commission_statement"
        assert item["authorization_id"] == "authorization-1"
        assert item["authorized_by"] == "Operator Name"
    # The retained input is echoed, not replaced.
    assert result["accepted"][0]["evidence"] == "independent_model_vendor_agreement"
    assert result["amended_from"] == "2026-01-01T00:00:00+00:00"
    assert result["client_decision_authorization_id"] == "authorization-1"


def test_a_client_decision_cannot_replace_vendor_agreement():
    """Consensus outranks a client assertion, so an overlap is refused, not merged."""
    with pytest.raises(ValueError, match="does not outrank it"):
        amend.amend(classification(), authorization(patches=[patch(documents=("doc-kept",))]))


def test_only_an_operator_authorized_plan_reaches_classification():
    with pytest.raises(ValueError, match="operator-authorized"):
        amend.amend(classification(), authorization(status="compiled"))
    with pytest.raises(ValueError, match="names no authorized patches"):
        amend.amend(classification(), authorization(authorized=()))
    with pytest.raises(ValueError, match="must be a JSON object"):
        amend.amend(classification(), [])
    with pytest.raises(ValueError, match="does not carry a compiled plan"):
        data = authorization()
        del data["compiled_plan"]
        amend.amend(classification(), data)


def test_only_authorized_applicable_patches_are_read():
    """A deferred patch, or a change type this control cannot apply, contributes nothing."""
    other = {**patch(patch_id="patch-2"), "change_type": "source_quality_action"}
    with pytest.raises(ValueError, match="no authorized patches this control can apply"):
        amend.amend(classification(), authorization(patches=[other], authorized=("patch-2",)))
    deferred = patch(patch_id="patch-9")
    with pytest.raises(ValueError, match="no authorized patches this control can apply"):
        amend.amend(classification(), authorization(patches=[deferred], authorized=("patch-1",)))


def test_a_confirmed_document_type_applies_without_a_layout_fingerprint():
    """The decision a client can actually make must have a consumer.

    A client confirming what a document *is* answers a different question from a
    client approving a layout, and carries no fingerprint. Requiring a
    layout_signature of it made the only client-answerable decision in the pack
    unappliable: it compiled, the authorization bound, and the apply step refused
    after the operator had already signed.
    """
    typed = {
        "patch_id": "patch-dt",
        "change_type": "document_type_rule",
        "append_only_payload": {
            "field": "document_type",
            "scope": "document",
            "proposed_value": "commission_report",
        },
        "impact": {
            "document_ids": ["doc-1"],
            "review_item_ids": ["r-1"],
            "fields": ["document_type"],
        },
    }
    resolved, result = amend.amend(
        classification(), authorization(patches=[typed], authorized=("patch-dt",))
    )
    assert resolved == {"doc-1": "commission_report"}
    accepted = {entry["document_id"]: entry for entry in result["accepted"]}
    assert accepted["doc-1"]["document_type"] == "commission_report"
    assert accepted["doc-1"]["evidence"] == amend.CLIENT_EVIDENCE

    # A patch of that type with nothing in proposed_value names no family, and
    # saying so beats writing an empty document type onto every document.
    empty = {
        **typed,
        "append_only_payload": {**typed["append_only_payload"], "proposed_value": "  "},
    }
    with pytest.raises(ValueError, match="proposed_value naming the family"):
        amend.amend(classification(), authorization(patches=[empty], authorized=("patch-dt",)))


def test_a_repeated_document_is_idempotent_but_a_conflict_is_refused():
    same = [patch(), patch(patch_id="patch-2", documents=("doc-a",))]
    _, result = amend.amend(
        classification(), authorization(same, authorized=("patch-1", "patch-2"))
    )
    assert [item["document_id"] for item in result["accepted"]] == ["doc-kept", "doc-a", "doc-b"]
    conflicting = [patch(), patch(patch_id="patch-2", family="invoice", documents=("doc-a",))]
    with pytest.raises(ValueError, match="disagree on a document's family"):
        amend.amend(classification(), authorization(conflicting, authorized=("patch-1", "patch-2")))


def test_malformed_patches_and_artifacts_are_refused(tmp_path):
    with pytest.raises(ValueError, match="append-only payload"):
        broken = patch()
        del broken["append_only_payload"]
        amend.amend(classification(), authorization([broken]))
    with pytest.raises(ValueError, match="layout_signature"):
        broken = patch()
        del broken["append_only_payload"]["layout_signature"]
        amend.amend(classification(), authorization([broken]))
    with pytest.raises(ValueError, match="document_family"):
        broken = patch()
        broken["append_only_payload"]["layout_signature"] = {"document_family": " "}
        amend.amend(classification(), authorization([broken]))
    with pytest.raises(ValueError, match="impact document list"):
        broken = patch()
        del broken["impact"]
        amend.amend(classification(), authorization([broken]))
    with pytest.raises(ValueError, match="not a classification_consensus_v1"):
        amend.load_classification(write(tmp_path / "other.json", {"artifact_type": "x"}))
    with pytest.raises(ValueError, match="requires an accepted list"):
        amend.load_classification(
            write(tmp_path / "no-list.json", {"artifact_type": "classification_consensus_v1"})
        )


def test_summary_without_a_document_count_leaves_unresolved_alone():
    source = classification()
    del source["summary"]["documents"]
    _, result = amend.amend(source, authorization())
    assert result["summary"]["accepted"] == 3
    assert result["summary"]["unresolved"] == 3


def test_cli_writes_a_new_artifact_and_reports_refusals(tmp_path, capsys):
    source = write(tmp_path / "classification.json", classification())
    auth = write(tmp_path / "authorization.json", authorization())
    out = tmp_path / "amended.json"
    assert amend.main([str(source), str(auth), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["summary"]["client_decided"] == 2
    assert "Client-decided classifications appended: 2" in capsys.readouterr().out

    quiet = tmp_path / "quiet.json"
    assert amend.main([str(source), str(auth), "--out", str(quiet), "--quiet"]) == 0
    assert capsys.readouterr().out == ""

    with pytest.raises(SystemExit, match="Classification amendment failed"):
        amend.main([str(source), str(auth), "--out", str(out)])


def exceptions_artifact():
    return {
        "summary": {"count": 3},
        "exceptions": [
            {
                "document_id": "doc-a",
                "field": "document_type",
                "reason": "no engine proposed a document type for this page",
                "review_source": "classification_consensus",
            },
            {
                "document_id": "doc-other",
                "field": "document_type",
                "reason": "no engine proposed a document type for this page",
                "review_source": "classification_consensus",
            },
            {
                "document_id": "doc-b",
                "field": "document_type",
                "reason": "vendors disagree",
                "client_review_required": False,
            },
        ],
    }


def test_a_resolved_exception_is_marked_answered_and_kept():
    """The gate excludes it; the artifact still carries it."""
    resolved, _ = amend.amend(classification(), authorization())
    amended, answered = amend.resolve_exceptions(exceptions_artifact(), resolved, authorization())
    assert answered == 1
    by_doc = {item["document_id"]: item for item in amended["exceptions"]}
    # Retained, not deleted.
    assert len(amended["exceptions"]) == 3
    assert by_doc["doc-a"]["client_review_required"] is False
    assert by_doc["doc-a"]["resolution"] == "operator_authorized_client_decision"
    assert by_doc["doc-a"]["resolved_document_type"] == "commission_statement"
    assert by_doc["doc-a"]["authorization_id"] == "authorization-1"
    assert by_doc["doc-a"]["reason"].startswith("no engine proposed")
    # An unrelated document is untouched.
    assert "resolution" not in by_doc["doc-other"]
    # Already answered, so not counted twice.
    assert "resolution" not in by_doc["doc-b"]
    assert amended["summary"] == {
        "count": 3,
        "resolved_by_client_decision": 1,
        "still_client_review_required": 1,
    }


def test_a_malformed_exception_artifact_is_refused():
    with pytest.raises(ValueError, match="requires an exceptions list"):
        amend.resolve_exceptions({}, {}, authorization())
    with pytest.raises(ValueError, match="must be an object"):
        amend.resolve_exceptions({"exceptions": ["x"]}, {}, authorization())


def test_cli_amends_exceptions_together_with_the_classification(tmp_path, capsys):
    source = write(tmp_path / "classification.json", classification())
    auth = write(tmp_path / "authorization.json", authorization())
    exc = write(tmp_path / "exceptions.json", exceptions_artifact())
    out, exc_out = tmp_path / "amended.json", tmp_path / "exceptions-out.json"
    assert (
        amend.main(
            [
                str(source),
                str(auth),
                "--out",
                str(out),
                "--exceptions",
                str(exc),
                "--exceptions-out",
                str(exc_out),
            ]
        )
        == 0
    )
    assert json.loads(exc_out.read_text())["summary"]["resolved_by_client_decision"] == 1
    assert "exceptions marked answered: 1" in capsys.readouterr().out

    with pytest.raises(SystemExit, match="used together"):
        amend.main(
            [str(source), str(auth), "--out", str(tmp_path / "x.json"), "--exceptions", str(exc)]
        )


def _counts(**documents):
    return dict(documents)


def test_a_group_answer_does_not_type_the_documents_it_was_not_about():
    """One answer covered thirty documents; three of them carried commission lines.

    The client was shown a group of 30 and answered "Blank Page. Can be
    ignored." Twenty-two were flagged `near_blank`. Three carried 46, 129 and
    190 populated fields, two of them commission lines with amounts -- $386.64
    on $3,866.40, and $1,239.68. Typing those from that answer would have put
    "Blank Page. Can be ignored." on documents carrying money, and nothing in
    the amend step looked.

    The member is withheld, never retyped and never guessed at: it stays an open
    question, which is what Rule 10 asks for when a value is implausible.
    """
    documents = tuple(f"doc-{index}" for index in range(6))
    counts = _counts(**{document: 5 for document in documents})
    counts["doc-5"] = 190
    resolved, result = amend.amend(
        classification(accepted=()),
        authorization(patches=[patch(documents=documents)]),
        counts,
    )
    assert "doc-5" not in resolved
    assert set(resolved) == set(documents[:5])
    assert result["summary"]["withheld_contradicted_by_content"] == 1
    assert result["summary"]["content_contradiction_checked"] is True
    withheld = result["withheld_contradicted_by_content"][0]
    assert withheld["document_id"] == "doc-5"
    assert withheld["populated_fields"] == 190
    assert withheld["proposed_document_type"] == "commission_statement"
    assert withheld["reason"] == "group_answer_contradicted_by_document_content"


def test_only_the_richer_side_is_withheld():
    """A sparse document is not contradicted by a content-bearing answer.

    Reading fewer fields off a payment record does not make it something other
    than a payment record. Withholding both tails flagged 33 of one real group's
    128 members and would have made the check noise. The hazard is one-sided:
    an answer given about empty documents applied to a full one.
    """
    documents = tuple(f"doc-{index}" for index in range(6))
    counts = _counts(**{document: 100 for document in documents})
    counts["doc-5"] = 4
    resolved, result = amend.amend(
        classification(accepted=()),
        authorization(patches=[patch(documents=documents)]),
        counts,
    )
    assert set(resolved) == set(documents)
    assert result["summary"]["withheld_contradicted_by_content"] == 0


def test_a_group_too_small_or_too_empty_to_have_a_median_is_not_judged():
    """With one member there is no group to be unlike, and no comparison to make."""
    assert amend.contradicted_by_content(["doc-a"], {"doc-a": 900}, 4.0) == ([], None)
    # Every member empty: the median is zero and every ratio is infinite, which
    # would withhold the whole group on no evidence at all.
    assert amend.contradicted_by_content(["doc-a", "doc-b"], {"doc-a": 0, "doc-b": 0}, 4.0) == (
        [],
        0,
    )


def test_records_that_name_no_document_are_an_unread_control(tmp_path):
    """Rule 9: a check handed nothing has not passed, and must not read as clean."""
    empty = tmp_path / "records.json"
    empty.write_text(json.dumps({"documents": []}))
    assert amend.populated_field_counts([empty]) == {}
    malformed = tmp_path / "bad.json"
    malformed.write_text(json.dumps({"documents": "not a list"}))
    with pytest.raises(ValueError, match="carries no document list"):
        amend.populated_field_counts([malformed])


def test_the_content_check_runs_and_reports_through_the_command(tmp_path, capsys):
    """The operator must be able to read, off the command's own output, whether it looked."""
    documents = tuple(f"doc-{index}" for index in range(6))
    source = write(tmp_path / "c.json", classification(accepted=()))
    auth = write(tmp_path / "a.json", authorization(patches=[patch(documents=documents)]))
    records = write(
        tmp_path / "r.json",
        {
            "documents": [
                {"document_id": document, "fields": {f"f{n}": "v" for n in range(5)}}
                for document in documents[:5]
            ]
            + [
                {"document_id": "doc-5", "fields": {f"f{n}": "v" for n in range(190)}},
                # Neither of these can be counted, and neither is a document
                # with no content -- they are simply not measurable here.
                {"document_id": "doc-6"},
                "not an object",
            ]
        },
    )
    out = tmp_path / "out.json"
    assert amend.main([str(source), str(auth), "--out", str(out), "--records", str(records)]) == 0
    printed = capsys.readouterr().out
    assert "withheld, contradicted by content: 1" in printed
    written = json.loads(out.read_text())
    assert [entry["document_id"] for entry in written["withheld_contradicted_by_content"]] == [
        "doc-5"
    ]
    assert "doc-5" not in {entry["document_id"] for entry in written["accepted"]}

    # Without records the command says it did not look, rather than reporting zero.
    silent = tmp_path / "silent.json"
    assert amend.main([str(source), str(auth), "--out", str(silent)]) == 0
    assert "content contradiction: NOT CHECKED" in capsys.readouterr().out

    # A factor of zero would withhold every document in every group.
    with pytest.raises(SystemExit, match="outlier-factor must be greater than zero"):
        amend.main(
            [str(source), str(auth), "--out", str(tmp_path / "z.json"), "--outlier-factor", "0"]
        )
    # Records naming no document are an unread control, not a clean one.
    empty = write(tmp_path / "empty.json", {"documents": []})
    with pytest.raises(SystemExit, match="no identified documents to check"):
        amend.main(
            [str(source), str(auth), "--out", str(tmp_path / "e.json"), "--records", str(empty)]
        )


def client_typed(document_id="doc-a", document_type="commission_statement"):
    """An acceptance that came from an earlier client decision, not from vendors."""
    return {
        "document_id": document_id,
        "page_id": document_id,
        "document_type": document_type,
        "agreeing_vendors": [],
        "evidence": "operator_authorized_client_decision",
        "authorization_id": "authorization-round-1",
    }


def test_a_client_may_correct_the_answer_they_gave_last_round():
    """The refusal that guards vendor agreement was also blocking the correction path.

    Nothing about a prior client decision is vendor agreement: `agreeing_vendors`
    is empty and the evidence names the operator who authorized it. Refusing the
    later answer with "consensus already accepted a type" told an operator
    something untrue about their own audit trail, and left a client's correction
    with no way through a chain built on append-only amendment.
    """
    source = classification()
    source["accepted"].append(client_typed())
    patches = [patch(family="payment_record", documents=("doc-a", "doc-b"))]
    with pytest.raises(ValueError, match="already carry a type from an earlier client decision"):
        amend.amend(source, authorization(patches))
    _, result = amend.amend(source, authorization(patches), supersede_prior_client_decisions=True)
    assert result["superseded_prior_client_decisions"] == [
        {
            "document_id": "doc-a",
            "previous_document_type": "commission_statement",
            "previous_authorization_id": "authorization-round-1",
            "document_type": "payment_record",
            "authorization_id": "authorization-1",
            "source_patch_id": "patch-1",
        }
    ]
    assert result["summary"]["superseded_prior_client_decisions"] == 1
    # Append-only: the earlier acceptance is still there, unedited, and the
    # correction that replaces it names the authorization it supersedes.
    kept = [item for item in result["accepted"] if item["document_id"] == "doc-a"]
    assert [item["document_type"] for item in kept] == ["commission_statement", "payment_record"]
    assert kept[0] == client_typed()
    assert kept[1]["supersedes_authorization_id"] == "authorization-round-1"
    # Two entries for one document is one typed document, not two.
    assert result["summary"]["accepted"] == 3
    assert result["summary"]["unresolved"] == 1


def test_vendor_agreement_is_not_superseded_even_when_superseding_is_asked_for():
    """The flag opens one door. It does not open the other."""
    with pytest.raises(ValueError, match="does not outrank it"):
        amend.amend(
            classification(),
            authorization(patches=[patch(documents=("doc-kept",))]),
            supersede_prior_client_decisions=True,
        )


def test_repeating_last_round_s_answer_appends_nothing():
    """A client who confirms what they already said has changed nothing."""
    source = classification()
    source["accepted"].append(client_typed())
    patches = [patch(family="commission_statement", documents=("doc-a", "doc-b"))]
    _, result = amend.amend(source, authorization(patches))
    assert result["summary"]["reaffirmed_prior_client_decisions"] == 1
    assert result["summary"]["client_decided"] == 1
    assert [item["document_id"] for item in result["accepted"]] == [
        "doc-kept",
        "doc-a",
        "doc-b",
    ]


def test_the_refusal_names_the_whole_set_rather_than_the_first_document():
    """An operator decides once about 134 documents, not 134 times about one."""
    source = classification()
    source["accepted"] += [client_typed("doc-a"), client_typed("doc-b")]
    patches = [patch(family="payment_record", documents=("doc-a", "doc-b"))]
    with pytest.raises(ValueError) as caught:
        amend.amend(source, authorization(patches))
    message = str(caught.value)
    assert "2 documents" in message
    assert "authorization-round-1" in message
    assert "commission_statement -> payment_record" in message
    assert "--supersede-prior-client-decisions" in message


def test_the_last_acceptance_for_a_document_is_the_one_read():
    """Entries are appended, so an index that keeps the first one reads a stale type."""
    source = classification()
    source["accepted"] += [
        "not an object",
        {"page_id": "no-document-id"},
        client_typed("doc-a", "commission_statement"),
        client_typed("doc-a", "payment_record"),
    ]
    index = amend.prior_acceptance(source)
    assert index["doc-a"]["document_type"] == "payment_record"
    assert set(index) == {"doc-kept", "doc-a"}


def test_a_content_contradicted_document_never_reaches_the_supersession_question():
    """A member the answer was visibly not about is withheld before it is retyped."""
    source = classification()
    source["accepted"].append(client_typed())
    patches = [patch(family="payment_record", documents=("doc-a", "doc-b", "doc-c"))]
    counts = {"doc-a": 400, "doc-b": 4, "doc-c": 5}
    _, result = amend.amend(source, authorization(patches), counts)
    assert [entry["document_id"] for entry in result["withheld_contradicted_by_content"]] == [
        "doc-a"
    ]
    assert result["summary"]["superseded_prior_client_decisions"] == 0


def test_a_reaffirmed_answer_still_closes_the_question_it_answered():
    """Repeating an answer changes no type, but it is not silence.

    A document whose exception was still standing -- the earlier authorization
    ran without `--exceptions`, say -- would otherwise be asked about again
    after the client had answered it twice.
    """
    source = classification()
    source["accepted"].append(client_typed())
    patches = [patch(family="commission_statement", documents=("doc-a", "doc-b"))]
    resolved, _ = amend.amend(source, authorization(patches))
    assert resolved == {"doc-a": "commission_statement", "doc-b": "commission_statement"}
    exceptions = {
        "exceptions": [
            {"document_id": "doc-a", "reason": "no_independent_agreement"},
            {"document_id": "doc-b", "reason": "no_independent_agreement"},
        ]
    }
    amended, answered = resolve_exceptions_for(exceptions, resolved)
    assert answered == 2
    assert all(entry["client_review_required"] is False for entry in amended["exceptions"])


def resolve_exceptions_for(exceptions, resolved):
    """Call the resolver with this file's standard authorization."""
    return amend.resolve_exceptions(exceptions, resolved, authorization())


def test_a_document_named_by_two_patches_is_counted_once(tmp_path):
    """A repeated document is one answer, whichever branch it takes."""
    source = classification()
    source["accepted"].append(client_typed())
    patches = [
        patch(family="payment_record", documents=("doc-a",)),
        patch(patch_id="patch-2", family="payment_record", documents=("doc-a",)),
    ]
    _, result = amend.amend(
        source,
        authorization(patches, authorized=("patch-1", "patch-2")),
        supersede_prior_client_decisions=True,
    )
    assert result["summary"]["superseded_prior_client_decisions"] == 1
    assert result["summary"]["client_decided"] == 1
    same = [patch(family="commission_statement", documents=("doc-a",))]
    same.append(patch(patch_id="patch-2", family="commission_statement", documents=("doc-a",)))
    _, result = amend.amend(source, authorization(same, authorized=("patch-1", "patch-2")))
    assert result["summary"]["reaffirmed_prior_client_decisions"] == 1


def test_the_command_reports_a_supersession_and_a_reaffirmation(tmp_path, capsys):
    """Both outcomes are visible without reading the artifact."""
    source = classification()
    source["accepted"] += [client_typed("doc-a"), client_typed("doc-b", "payment_record")]
    patches = [patch(family="payment_record", documents=("doc-a", "doc-b"))]
    exit_code = amend.main(
        [
            str(write(tmp_path / "c.json", source)),
            str(write(tmp_path / "a.json", authorization(patches))),
            "--out",
            str(tmp_path / "out.json"),
            "--supersede-prior-client-decisions",
        ]
    )
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "superseded an earlier client decision: 1" in output
    assert "repeated an earlier client decision, nothing appended: 1" in output
