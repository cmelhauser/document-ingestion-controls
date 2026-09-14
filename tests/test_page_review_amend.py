"""Check the page-review amendment lane against what it must and must not change."""

import json

import page_review_amend as lane
import pytest


def records():
    """One document: an accepted misread, a correct cell, an unaccepted header."""
    return {
        "summary": {},
        "documents": [
            {
                "document_id": "run__p1",
                "fields": {
                    "lines[0].commission_amount": {
                        "value": "799.87",
                        "candidate_values": ["799.87"],
                        "consensus_flag": "single_engine",
                        "accepted": True,
                        "acceptance": {"accepted_by": "reconciled_by_document_arithmetic"},
                    },
                    "lines[1].commission_amount": {"value": "40.00", "accepted": True},
                    "header.commissionable_amount": {
                        "value": "993,673.65",
                        "accepted": False,
                        "consensus_flag": "single_engine",
                    },
                },
                "header": {},
                "lines": [],
            }
        ],
    }


def proposals(*corrections):
    """A proposals artifact holding these corrections."""
    return {"artifact_type": "page_review_proposals_v1", "corrections": list(corrections)}


def correction(line, column, page_value, evidence="extractor_text", page="p1"):
    """One correction as `page_review.py --proposals` writes it."""
    return {
        "page": page,
        "line_index": line,
        "column": column,
        "page_value": page_value,
        "evidence": evidence,
        "retained_readings": ["799.87", "799.67"],
    }


def test_a_proved_correction_supersedes_the_accepted_value_and_keeps_it():
    """Job 13075's commission prints 799.67; two passes agreed on 799.87.

    Arithmetic had even accepted the misread. The page review, with a retained
    engine reading and the independent extractor's text beside it, replaces it,
    and the replaced value stays on the field.
    """
    recs = records()
    fix = correction("0", "commission_amount", "799.67", "extractor_text_and_engine")
    applied, _ = lane.amend(recs, proposals(fix), "auth-1")
    document = recs["documents"][0]
    field = document["fields"]["lines[0].commission_amount"]
    assert (field["value"], field["accepted"]) == ("799.67", True)
    assert field["acceptance"]["accepted_by"] == "amended_from_page_review"
    assert field["acceptance"]["vendor_agreement"] is False
    assert field["superseded_consensus"]["value"] == "799.87"
    assert field["superseded_consensus"]["accepted_by"] == "reconciled_by_document_arithmetic"
    assert document["lines"][0]["commission_amount"]["value"] == "799.67"
    assert document["accepted_field_count"] == 2
    assert applied == [
        {
            "document_id": "run__p1",
            "field": "lines[0].commission_amount",
            "from": "799.87",
            "to": "799.67",
            "evidence": "extractor_text_and_engine",
        }
    ]


def test_a_header_cell_and_a_cell_the_export_never_had_are_both_amended():
    """A header misread with an inserted digit, and a lost value on a line."""
    recs = records()
    applied, _ = lane.amend(
        recs,
        proposals(
            correction("header", "commissionable_amount", "93673.65"),
            correction("2", "commission_amount", "13885.00", "engine"),
        ),
        "auth-1",
    )
    document = recs["documents"][0]
    assert document["header"]["commissionable_amount"]["value"] == "93673.65"
    assert document["fields"]["lines[2].commission_amount"]["superseded_consensus"]["value"] is None
    assert [a["from"] for a in applied] == ["993,673.65", None]


def test_what_is_not_proved_or_not_needed_is_left_alone():
    """The reviewer alone, a cell already right, and a page with no document."""
    recs = records()
    applied, skipped = lane.amend(
        recs,
        proposals(
            correction("0", "commission_amount", "799.67", "reviewer_only"),
            correction("1", "commission_amount", "40.00"),
            correction("0", "commission_amount", "1.00", page="p9"),
        ),
        "auth-1",
    )
    assert applied == []
    assert skipped == {
        "evidence_not_selected": 1,
        "already_reads_the_page": 1,
        "no_such_document": 1,
    }
    assert recs["documents"][0]["fields"]["lines[0].commission_amount"]["value"] == "799.87"
    assert "accepted_field_count" not in recs["documents"][0]


def test_a_move_is_applied_only_with_both_halves_proved():
    """A shifted row: 1,404.13 belongs on line 4 but sits on line 10.

    Moving it onto line 4 while line 10's own fix rests on the reviewer alone
    counts it twice, so the move waits. With both halves proved, both land.
    """

    def page():
        field = {"value": "1,404.13", "accepted": True}
        return {
            "documents": [
                {"document_id": "run__p1", "fields": {"lines[10].commission_amount": field}}
            ]
        }

    move = dict(correction("4", "commission_amount", "1404.13", "engine"), moves_from_lines=["10"])
    half = page()
    applied, skipped = lane.amend(
        half,
        proposals(move, correction("10", "commission_amount", "799.67", "reviewer_only")),
        "auth-1",
    )
    assert applied == []
    assert skipped["moves_a_figure_whose_other_half_is_not_proved"] == 1
    whole = page()
    applied, _ = lane.amend(
        whole,
        proposals(move, correction("10", "commission_amount", "799.67", "extractor_text")),
        "auth-1",
    )
    assert sorted(a["field"] for a in applied) == [
        "lines[10].commission_amount",
        "lines[4].commission_amount",
    ]


def arithmetic_page(**cells):
    """A page whose lines carry a base, a rate and a commission."""
    fields = {}
    for line, terms in cells.items():
        for column, value in zip(lane.ARITHMETIC_COLUMNS, terms, strict=True):
            if value is not None:
                fields[f"lines[{line.lstrip('l')}].{column}"] = {"value": value, "accepted": True}
    return {"documents": [{"document_id": "run__p1", "fields": fields}]}


def test_a_line_its_own_arithmetic_proves_is_not_amended_into_one_it_does_not():
    """p0430 printed line 8 once and the engines read it twice.

    The review aligned its rows one off from there, so every later line was
    offered its neighbour's commission -- each really printed on the page, which
    is all the independent evidence proves. 649.35 x 10% = 64.94 held; 110.34
    would not.
    """
    recs = arithmetic_page(l9=("649.35", "10.00", "64.94"), l10=("1103.40", "10.00", "110.34"))
    move = dict(
        correction("9", "commission_amount", "110.34", "extractor_text_and_engine"),
        moves_from_lines=["10"],
    )
    applied, skipped = lane.amend(
        recs, proposals(move, correction("10", "commission_amount", "904.50", "engine")), "auth-1"
    )
    assert applied == []
    assert skipped == {"breaks_the_line_arithmetic": 2}
    assert recs["documents"][0]["fields"]["lines[9].commission_amount"]["value"] == "64.94"


def test_a_correction_that_completes_or_repairs_a_line_is_applied():
    """A commission the export lacked, and one that makes a failing line hold."""
    recs = arithmetic_page(l3=("2,298.59", "8.00", None), l4=("1,000.00", "10.00", "10.00"))
    applied, skipped = lane.amend(
        recs,
        proposals(
            correction("3", "commission_amount", "183.89"),
            correction("4", "commission_amount", "100.00"),
        ),
        "auth-1",
    )
    assert [a["field"] for a in applied] == [
        "lines[3].commission_amount",
        "lines[4].commission_amount",
    ]
    assert skipped == {}


def test_a_move_waits_when_its_other_half_would_break_a_line():
    """Line 10 holds and its fix would break it, so line 4 cannot take its figure."""
    recs = arithmetic_page(l10=("14,041.30", "10.00", "1,404.13"))
    move = dict(correction("4", "commission_amount", "1404.13", "engine"), moves_from_lines=["10"])
    applied, skipped = lane.amend(
        recs, proposals(move, correction("10", "commission_amount", "799.67")), "auth-1"
    )
    assert applied == []
    assert skipped == {
        "breaks_the_line_arithmetic": 1,
        "moves_a_figure_whose_other_half_is_not_proved": 1,
    }


def test_only_grades_independent_of_the_reviewer_can_be_selected():
    """No flag can ask for an amendment the reviewer alone supports."""
    with pytest.raises(SystemExit):
        lane.build_parser().parse_args(
            ["r", "p", "--authorization", "a", "--out", "o", "--report", "x"]
            + ["--evidence", "reviewer_only"]
        )


def test_a_record_artifact_must_be_an_object(tmp_path):
    path = tmp_path / "records.json"
    path.write_text(json.dumps([1, 2]))
    with pytest.raises(ValueError):
        lane.load_json(str(path), "record artifact")


def write(path, data):
    path.write_text(json.dumps(data))
    return str(path)


def test_main_writes_the_amended_records_and_the_report(tmp_path, capsys):
    """New files only, a report of what changed, and no second write over them."""
    recs = write(tmp_path / "records.json", records())
    props = write(
        tmp_path / "proposals.json",
        proposals(
            correction("0", "commission_amount", "799.67"),
            correction("0", "commission_amount", "1.00", "reviewer_only"),
        ),
    )
    out, report = tmp_path / "amended.json", tmp_path / "report.json"
    args = [recs, props, "--authorization", "auth-1", "--out", str(out), "--report", str(report)]
    assert lane.main(args) == 0
    printed = capsys.readouterr().out
    assert "amendments applied: 1" in printed
    assert "left alone, evidence_not_selected: 1" in printed
    assert json.loads(report.read_text())["by_evidence"] == {"extractor_text": 1}
    summary = json.loads(out.read_text())["summary"]["page_review_amendments"]
    assert summary == {"authorization": "auth-1", "amendments_applied": 1}
    with pytest.raises(SystemExit) as exc:
        lane.main(args)
    assert "Page review amendment failed" in str(exc.value)


def test_main_refuses_what_is_not_a_proposals_artifact(tmp_path):
    recs = write(tmp_path / "records.json", records())
    props = write(tmp_path / "proposals.json", {"artifact_type": "something_else"})
    with pytest.raises(SystemExit) as exc:
        lane.main(
            [recs, props, "--authorization", "a"]
            + ["--out", str(tmp_path / "o.json"), "--report", str(tmp_path / "x.json")]
        )
    assert "page_review_proposals_v1" in str(exc.value)


def test_main_stays_quiet_when_asked(tmp_path, capsys):
    recs = write(tmp_path / "records.json", records())
    props = write(tmp_path / "proposals.json", proposals())
    lane.main(
        [recs, props, "--authorization", "a", "--evidence", "engine", "--quiet"]
        + ["--out", str(tmp_path / "o.json"), "--report", str(tmp_path / "x.json")]
    )
    assert capsys.readouterr().out == ""


def test_a_correction_another_vendor_bears_out_is_independent_evidence():
    """The reviewer and a second vendor reading the same figure: selectable, and applied."""
    assert "second_vendor" in lane.INDEPENDENT_GRADES
    applied, _ = lane.amend(
        records(), proposals(correction("0", "commission_amount", "799.67", "second_vendor")), "a"
    )
    assert [(a["field"], a["evidence"]) for a in applied] == [
        ("lines[0].commission_amount", "second_vendor")
    ]


def test_a_cell_changed_since_the_review_is_left_alone():
    """A re-read or another amendment changed the cell after the reviewer read it.

    The cell holds 799.87. A review that saw 500.00 there was about something
    else and is refused. One that saw 799.87 is applied, as is a cell the review
    saw empty and still is; a cell already holding the page's value is already
    right, whatever the review saw.
    """

    def saw(export, *args):
        return dict(correction(*args), export_value=export)

    applied, skipped = lane.amend(
        records(), proposals(saw("500.00", "0", "commission_amount", "799.67")), "a"
    )
    assert (applied, skipped) == ([], {"the_field_changed_since_the_review": 1})
    applied, skipped = lane.amend(
        records(),
        proposals(
            saw("799.87", "0", "commission_amount", "799.67"),
            saw("", "2", "commission_amount", "13885.00", "engine"),
            saw("39.00", "1", "commission_amount", "40.00"),
        ),
        "a",
    )
    assert [a["field"] for a in applied] == [
        "lines[0].commission_amount",
        "lines[2].commission_amount",
    ]
    assert skipped == {"already_reads_the_page": 1}
