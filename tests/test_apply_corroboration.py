"""The one lane that accepts a value on weaker-than-consensus evidence."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import apply_corroboration  # noqa: E402
import independent_corroboration  # noqa: E402

AUTHORIZATION = "client_decision_authorization_05"


def field(value=None, flag="single_engine", accepted=False, candidates=None):
    return {
        "value": value,
        "candidate_values": candidates,
        "confidence": None,
        "source": "printed",
        "consensus_flag": flag,
        "agreeing_engines": [],
        "engine_count": 1,
        "rule": "printed_majority",
        "accepted": accepted,
        "is_handwritten": False,
        "blocking": not accepted,
        "queue_for_review": not accepted,
    }


def records():
    return {
        "summary": {"documents": 1},
        "documents": [
            {
                "document_id": "doc-1",
                "accepted_field_count": 1,
                "fields": {
                    "header.total_amount": field(),
                    "header.seller_name": field("ACME", "consensus_2of2", accepted=True),
                    "lines[0].description": field(candidates=["Flint X", "Stone Y"]),
                    "lines[0].quantity": field(),
                },
                "header": {},
                "lines": [{}],
            }
        ],
    }


def corroboration(occurrences=1):
    return {
        "artifact_type": independent_corroboration.ARTIFACT_TYPE,
        "corroborations": [
            {
                "document_id": "doc-1",
                "field": "header.total_amount",
                "value": "5,193.60",
                "match_mode": "substring",
                "occurrences": occurrences,
            },
            {
                "document_id": "doc-1",
                "field": "header.seller_name",
                "value": "OTHER",
                "match_mode": "substring",
                "occurrences": 1,
            },
            # No value to accept: skipped rather than accepted as null.
            {"document_id": "doc-1", "field": "lines[0].quantity", "occurrences": 1},
        ],
        "tie_breaks": [
            {
                "document_id": "doc-1",
                "field": "lines[0].description",
                "candidates": ["Flint X", "Stone Y"],
                "supported_value": "Flint X",
                "match_mode": "substring",
                "occurrences": 3,
            }
        ],
    }


def exceptions():
    return [
        {"document_id": "doc-1", "field": "header.total_amount", "cause": "partial_disagreement"},
        {"document_id": "doc-1", "field": "lines[0].description", "cause": "no_majority"},
        {"document_id": "doc-1", "field": "header.buyer_name", "cause": "partial_disagreement"},
    ]


def test_an_authorized_acceptance_applies_and_never_claims_agreement():
    artifact, applied, left = apply_corroboration.build(
        records(), exceptions(), corroboration(), AUTHORIZATION
    )
    document = applied["documents"][0]
    total = document["fields"]["header.total_amount"]

    assert total["value"] == "5,193.60" and total["accepted"]
    assert not total["blocking"] and not total["queue_for_review"]
    # Never relabelled as consensus: no second model vendor read this.
    assert total["consensus_flag"] == "single_engine"
    assert total["acceptance"]["accepted_by"] == independent_corroboration.EVIDENCE_KIND
    assert total["acceptance"]["vendor_agreement"] is False
    assert total["acceptance"]["authorization"] == AUTHORIZATION
    assert total["acceptance"]["match_scope"] == "page"
    # Append-only: what it replaced is retained beside it.
    assert total["superseded_consensus"]["value"] is None
    assert total["superseded_consensus"]["consensus_flag"] == "single_engine"

    # A disagreement the evidence broke is accepted and counted separately.
    description = document["fields"]["lines[0].description"]
    assert description["value"] == "Flint X"
    assert description["acceptance"]["kind"] == "independent_evidence_tie_break"
    assert description["superseded_consensus"]["candidate_values"] == ["Flint X", "Stone Y"]

    # Consensus is the stronger evidence and is never overwritten by this.
    assert document["fields"]["header.seller_name"]["value"] == "ACME"
    assert "acceptance" not in document["fields"]["header.seller_name"]
    # A corroboration with no value to accept is skipped, not accepted as null.
    assert "acceptance" not in document["fields"]["lines[0].quantity"]

    assert artifact["summary"]["fields_accepted"] == 2
    assert artifact["summary"]["corroborated_single_readings"] == 1
    assert artifact["summary"]["independent_evidence_tie_breaks"] == 1
    assert artifact["summary"]["consensus_findings_remaining"] == 1
    assert "misattribution" in artifact["limits"] and "misattribution" in applied["limits"]
    assert applied["artifact_type"] == apply_corroboration.RECORDS_ARTIFACT_TYPE
    assert applied["summary"]["corroboration_authorization"] == AUTHORIZATION

    # Only findings this acceptance does not cover are left for the gate.
    assert [item["field"] for item in left] == ["header.buyer_name"]

    # The nested record shape downstream controls read is rebuilt from the fields.
    assert document["header"]["total_amount"]["value"] == "5,193.60"
    assert document["lines"][0]["description"]["value"] == "Flint X"
    assert document["accepted_field_count"] == 3


def test_unique_occurrence_only_narrows_the_acceptance():
    """A value seen twenty times says little about which row it belongs to."""
    artifact, applied, left = apply_corroboration.build(
        records(), exceptions(), corroboration(), AUTHORIZATION, unique_only=True
    )
    # The tie-break value occurs three times and is now out of scope.
    assert artifact["summary"]["fields_accepted"] == 1
    assert artifact["unique_occurrence_only"] is True
    assert applied["documents"][0]["fields"]["lines[0].description"]["value"] is None
    assert len(left) == 2

    # And when every value is seen many times, nothing is accepted at all --
    # rule 9: a control that processed nothing has not passed.
    crowded = corroboration(occurrences=9)
    for entry in crowded["corroborations"] + crowded["tie_breaks"]:
        entry["occurrences"] = 9
    with pytest.raises(ValueError, match="control that processed nothing"):
        apply_corroboration.build(records(), exceptions(), crowded, AUTHORIZATION, unique_only=True)


def test_the_lane_refuses_anything_that_is_not_the_evidence_it_names(tmp_path):
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"artifact_type": "something_else"}))
    with pytest.raises(ValueError, match="not a independent_corroboration_v1 artifact"):
        apply_corroboration.load_corroboration(wrong)
    with pytest.raises(ValueError, match="not a consensus record artifact"):
        apply_corroboration.load_records(wrong)
    with pytest.raises(ValueError, match="not an exception artifact"):
        apply_corroboration.load_exceptions(wrong)

    good = tmp_path / "corroboration.json"
    good.write_text(json.dumps(corroboration()))
    assert apply_corroboration.load_corroboration(good)["artifact_type"]


def test_the_command_writes_three_artifacts_and_refuses_without_a_decision(
    tmp_path, capsys, monkeypatch
):
    paths = {}
    for name, payload in (
        ("records.json", records()),
        ("consensus_exceptions.json", {"summary": {}, "exceptions": exceptions()}),
        ("corroboration.json", corroboration()),
    ):
        paths[name] = tmp_path / name
        paths[name].write_text(json.dumps(payload))

    argv = [
        str(paths["records.json"]),
        str(paths["consensus_exceptions.json"]),
        str(paths["corroboration.json"]),
        "--authorization",
        AUTHORIZATION,
        "--out",
        str(tmp_path / "applied.json"),
        "--records-out",
        str(tmp_path / "accepted_records.json"),
        "--exceptions",
        str(tmp_path / "remaining.json"),
    ]
    assert apply_corroboration.main(argv) == 0
    out = capsys.readouterr().out
    assert "fields accepted    : 2" in out
    assert "not vendor agreement" in out
    written = json.loads((tmp_path / "applied.json").read_text())
    assert written["authorization"] == AUTHORIZATION
    assert len(json.loads((tmp_path / "remaining.json").read_text())["exceptions"]) == 1
    assert json.loads((tmp_path / "accepted_records.json").read_text())["artifact_type"]

    # Accepting on weaker evidence is a policy choice: no decision, no lane.
    with pytest.raises(SystemExit):
        apply_corroboration.main(argv[:3] + argv[5:])

    # A no-clobber path refusal surfaces as a message, not a traceback.
    with pytest.raises(SystemExit, match="Applied corroboration failed"):
        apply_corroboration.main(argv)

    monkeypatch.setattr(sys, "argv", ["apply_corroboration.py", *argv[:3], "--authorization", "x"])
    with pytest.raises(SystemExit):
        apply_corroboration.main()


def test_a_document_with_no_acceptance_is_left_exactly_as_it_was():
    """Most documents are untouched by any one authorization; none is rebuilt for nothing."""
    document = records()["documents"][0]
    before = json.dumps(document, sort_keys=True)
    assert apply_corroboration.apply_document(document, {}, AUTHORIZATION) == []
    assert json.dumps(document, sort_keys=True) == before


def test_quiet_suppresses_only_the_summary(tmp_path, capsys):
    paths = {}
    for name, payload in (
        ("records.json", records()),
        ("consensus_exceptions.json", {"summary": {}, "exceptions": exceptions()}),
        ("corroboration.json", corroboration()),
    ):
        paths[name] = tmp_path / name
        paths[name].write_text(json.dumps(payload))
    assert (
        apply_corroboration.main(
            [
                str(paths["records.json"]),
                str(paths["consensus_exceptions.json"]),
                str(paths["corroboration.json"]),
                "--authorization",
                AUTHORIZATION,
                "--out",
                str(tmp_path / "applied.json"),
                "--records-out",
                str(tmp_path / "records_out.json"),
                "--exceptions",
                str(tmp_path / "remaining.json"),
                "--quiet",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == ""
    # The artifacts are unchanged by quiet; only the console is.
    assert json.loads((tmp_path / "applied.json").read_text())["summary"]["fields_accepted"] == 2
