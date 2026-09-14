"""A record artifact's fields and views are brought into step, and checked."""

import json

import pytest
import records_in_step as lane


def records():
    """Three documents: an agreed value the views lack, a mapping the fields lack, and one in step."""
    return {
        "summary": {},
        "documents": [
            {
                "document_id": "run__p1",
                "fields": {
                    "lines[0].job_number": {
                        "value": "12951",
                        "accepted": True,
                        "acceptance": {"accepted_by": "agreed_by_independent_vendors"},
                    },
                    "lines[0].amount": {"value": "5.00", "accepted": True},
                },
                "lines": [{"job_number": {"value": None}, "amount": {"value": "4.00"}}],
            },
            {
                "document_id": "run__p2",
                "fields": {"header.reference_number": {"value": None, "consensus_flag": "absent"}},
                "header": {
                    "reference_number": {
                        "value": "R-77",
                        "source": "printed",
                        "mapped_from_source_label": "REF #",
                        "registry_rule_id": "rule-9",
                        "approval_authority": "client",
                        "agreeing_engines": ["google/model", "openai/model"],
                        "consensus_flag": "consensus_2of2",
                    },
                    "flag": "yes",
                },
            },
            {
                "document_id": "run__p3",
                "fields": {"header.total_amount": {"value": "1.00", "consensus_flag": "flag"}},
                "header": {"total_amount": {"value": "1.00"}},
            },
        ],
    }


def test_a_document_is_brought_into_step_without_overwriting_either_side():
    data = records()
    report = lane.reconcile(data["documents"], write=True)
    first, second, third = data["documents"]
    assert first["lines"][0]["job_number"]["value"] == "12951"
    assert first["lines"][0]["amount"]["value"] == "5.00"
    field = second["fields"]["header.reference_number"]
    assert (field["value"], field["accepted"]) == ("R-77", True)
    assert field["acceptance"]["accepted_by"] == "approved_source_label_mapping"
    assert field["carried_from_view"] == "header.reference_number"
    assert field["consensus_flag"] == "consensus_2of2"
    assert second["header"]["flag"] == "yes"
    assert third["header"] == {"total_amount": {"value": "1.00"}}
    assert report["documents_out_of_step"] == 2
    assert report["cells_brought_into_step"] == 2
    assert report["behind_by_acceptance"] == {"agreed_by_independent_vendors": 1, "consensus": 1}
    assert report["mappings_by_column"] == {"header.reference_number": 1}
    assert report["view_entries_kept"] == {"header.flag": 1}
    assert [row["document_id"] for row in report["out_of_step"]] == ["run__p1", "run__p2"]
    # Once in step, nothing is left to do.
    again = lane.reconcile(data["documents"], write=False)
    assert again["documents_out_of_step"] == 0 and again["mode"] == "check"


def test_how_a_field_was_accepted_is_named_for_every_kind():
    assert lane.acceptance_of({"acceptance": {"accepted_by": "x"}}) == "x"
    assert lane.acceptance_of({"accepted": True}) == "consensus"
    assert lane.acceptance_of({"consensus_flag": "single_engine"}) == "single_engine"
    assert lane.acceptance_of({}) == "unaccepted"


def write(path, value):
    path.write_text(json.dumps(value))
    return str(path)


def test_main_writes_new_files_and_check_fails_while_anything_is_out_of_step(tmp_path, capsys):
    source = write(tmp_path / "records.json", records())
    assert lane.main([source, "--check"]) == 1
    printed = capsys.readouterr().out
    assert "documents out of step: 2 of 3" in printed
    assert "approved mappings only a view held: 1" in printed
    out, report = tmp_path / "out.json", tmp_path / "report.json"
    assert lane.main([source, "--out", str(out), "--report", str(report)]) == 0
    assert "documents brought into step: 2 of 3" in capsys.readouterr().out
    written = json.loads(out.read_text())
    assert written["summary"]["records_in_step"]["mappings_carried_into_fields"] == 1
    assert json.loads(report.read_text())["source_artifact"] == source
    checked = tmp_path / "checked.json"
    assert lane.main([str(out), "--check", "--report", str(checked), "--quiet"]) == 0
    assert capsys.readouterr().out == ""
    assert json.loads(checked.read_text())["documents_out_of_step"] == 0
    with pytest.raises(SystemExit, match="Records in step failed"):
        lane.main([source, "--out", str(out), "--report", str(tmp_path / "other.json")])


@pytest.mark.parametrize(
    ("argv", "body", "message"),
    [
        ([], {"documents": [{"document_id": "x"}]}, "or --check"),
        (["--check", "--out", "o.json"], {"documents": [{"document_id": "x"}]}, "or --check"),
        (["--out", "o.json"], {"documents": [{"document_id": "x"}]}, "needs --report"),
        (["--check"], {"documents": []}, "not a record artifact"),
        (["--check"], [1, 2], "not a record artifact"),
    ],
)
def test_a_run_that_cannot_read_records_or_was_asked_nothing_fails(tmp_path, argv, body, message):
    source = write(tmp_path / "records.json", body)
    with pytest.raises(SystemExit, match=message):
        lane.main([source, *[str(tmp_path / a) if a.endswith(".json") else a for a in argv]])
