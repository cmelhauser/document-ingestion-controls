"""Tests for letting a line's own arithmetic choose between two readings."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

lane = importlib.import_module("arithmetic_reconcile")


def cell(value=None, accepted=False, candidates=None):
    return {
        "value": value,
        "accepted": accepted,
        "candidate_values": list(candidates) if candidates is not None else None,
        "consensus_flag": "consensus_2of2" if accepted else "no_consensus",
    }


def line(index, **cells):
    return {f"lines[{index}].{name}": entry for name, entry in cells.items()}


def document(document_id="p1", **fields):
    return {"document_id": document_id, "fields": dict(fields)}


def write(path, value):
    path.write_text(json.dumps(value))
    return str(path)


def test_a_solved_cell_reaches_the_line_view_as_well_as_the_field():
    """The arithmetic and attribution controls read the line view, not the field map."""
    doc = document(**line(0, commission_amount=cell(candidates=["100.00", "662.89"])))
    doc["lines"] = [{"commission_amount": {"value": None}, "description": {"value": "Chair"}}]
    resolution = {
        "document_id": "p1",
        "field": "lines[0].commission_amount",
        "resolved_value": "100.00",
        "expected_value": 100.0,
        "evidence": "base x rate",
    }
    [updated], applied = lane.apply_resolutions([doc], [resolution])
    assert applied == 1
    assert updated["lines"][0]["commission_amount"]["value"] == "100.00"
    assert updated["lines"][0]["description"] == {"value": "Chair"}
    [untouched], _ = lane.apply_resolutions([document("p9")], [resolution])
    assert "lines" not in untouched


def test_an_accounting_negative_is_the_number_it_means():
    assert lane.number("(662.89)") == -662.89
    assert lane.number("4,098.49") == 4098.49
    assert lane.number("$1,750.97") == 1750.97
    assert lane.number("10%") == 10.0
    # A reading this cannot parse is not zero. It is one this lane must not
    # reason about, and None is how it says so.
    assert lane.number("n/a") is None
    assert lane.number(None) is None


def test_a_sibling_with_two_candidates_lends_nothing():
    """Using one of its candidates to pick between the other cell's decides the
    disagreement by assuming it."""
    assert lane.offered(cell("10.00", accepted=True)) == (10.0, "accepted")
    assert lane.offered(cell(candidates=["10.00"])) == (10.0, "single_reading")
    assert lane.offered(cell(candidates=["10.00", "11.00"])) == (None, None)
    assert lane.offered(cell(candidates=[])) == (None, None)
    assert lane.offered("not a cell") == (None, None)


def test_the_relation_solves_for_whichever_cell_is_open():
    assert lane.expected_value(lane.AMOUNT, None, 1000.0, 10.0) == 100.0
    assert lane.expected_value(lane.BASE, 100.0, None, 10.0) == 1000.0
    assert lane.expected_value(lane.RATE, 100.0, 1000.0, None) == 10.0
    # A missing sibling, or one that would divide by zero, yields no expectation.
    assert lane.expected_value(lane.AMOUNT, None, None, 10.0) is None
    assert lane.expected_value(lane.BASE, 100.0, None, 0) is None
    assert lane.expected_value(lane.RATE, 100.0, 0, None) is None
    assert lane.expected_value("something_else", 1.0, 2.0, 3.0) is None


def test_the_tolerance_is_proportional_because_rounding_is():
    """A flat two cents is 0.0025% of a $799 line.

    It rejected `799.87` against an expected `799.671` and called a rounded
    total a contradiction -- 102 of 144 rejections on the commission run.
    """
    wide = lane.match_width(799.671, lane.AMOUNT, 0.02, 0.05, 0.0005)
    assert wide == pytest.approx(0.3998, abs=1e-4)
    assert abs(799.87 - 799.671) <= wide
    # The floor still governs a small value, where a proportion collapses.
    assert lane.match_width(1.0, lane.AMOUNT, 0.02, 0.05, 0.0005) == 0.02
    # A rate is quoted in whole percent and keeps its own absolute width.
    assert lane.match_width(10.0, lane.RATE, 0.02, 0.05, 0.0005) == 0.05


def test_the_arithmetic_picks_the_reading_that_satisfies_the_line():
    """The case this was built for: one engine read the table a row off.

    Two engines offered `(662.89)` and `4,098.49` for one line's commission, and
    `4,098.49` was also a candidate on a different line of the same page.
    Neither was wrong about the number; one was wrong about the row.
    """
    documents = [
        document(
            **line(
                2,
                commission_amount=cell(candidates=["(662.89)", "4,098.49"]),
                commissionable_amount=cell("40,984.90", accepted=True),
                stated_commission_rate=cell("10", accepted=True),
            )
        )
    ]
    resolutions, unresolved, counts = lane.reconcile(documents)
    assert unresolved == []
    assert counts == {"open": 1, "checkable": 1, "not_checkable": 0}
    assert resolutions[0]["resolved_value"] == "4,098.49"
    assert resolutions[0]["expected_value"] == 4098.49
    assert resolutions[0]["evidence"] == {
        "commissionable_amount": {"value": 40984.9, "from": "accepted"},
        "stated_commission_rate": {"value": 10.0, "from": "accepted"},
    }


def test_two_matches_is_an_ambiguity_and_none_is_a_contradiction():
    """Both are reported. Neither is resolved, and neither is silence."""
    ambiguous = [
        document(
            **line(
                0,
                commission_amount=cell(candidates=["100.00", "100.001"]),
                commissionable_amount=cell("1000.00", accepted=True),
                stated_commission_rate=cell("10", accepted=True),
            )
        )
    ]
    resolutions, unresolved, _ = lane.reconcile(ambiguous)
    assert resolutions == []
    assert unresolved[0]["reason"] == "several_candidates_satisfy_the_line"
    contradicted = [
        document(
            **line(
                0,
                commission_amount=cell(candidates=["500.00"]),
                commissionable_amount=cell("1000.00", accepted=True),
                stated_commission_rate=cell("10", accepted=True),
            )
        )
    ]
    resolutions, unresolved, _ = lane.reconcile(contradicted)
    assert resolutions == []
    assert unresolved[0]["reason"] == "no_candidate_satisfies_the_line"


def test_a_cell_it_could_not_check_is_not_a_cell_it_cleared():
    """Rule 9. Without this the two are indistinguishable in the summary."""
    documents = [
        document(
            # No siblings to compute an expectation from.
            **line(0, commission_amount=cell(candidates=["1.00", "2.00"])),
            # An accepted cell is evidence and is not overruled.
            **line(1, commission_amount=cell("9.99", accepted=True)),
        )
    ]
    resolutions, unresolved, counts = lane.reconcile(documents)
    assert (resolutions, unresolved) == ([], [])
    assert counts == {"open": 1, "checkable": 0, "not_checkable": 1}


def test_a_cell_with_no_parsable_candidate_is_not_checkable():
    documents = [
        document(
            **line(
                0,
                commission_amount=cell(candidates=["n/a"]),
                commissionable_amount=cell("1000.00", accepted=True),
                stated_commission_rate=cell("10", accepted=True),
            )
        )
    ]
    _, _, counts = lane.reconcile(documents)
    assert counts["not_checkable"] == 1


def test_line_grouping_ignores_what_is_not_a_line_cell():
    grouped = lane.line_cells(
        document(**{"header.total": cell("1"), "lines[3].commission_amount": cell("2")})
    )
    assert list(grouped) == ["3"]
    assert lane.line_cells({"fields": {"lines[0].x": "not an object"}}) == {}


def test_a_record_that_is_not_an_object_is_refused():
    with pytest.raises(ValueError, match="every record must be an object"):
        lane.reconcile(["not a document"])


def test_applying_a_resolution_says_how_it_was_earned():
    """Never `consensus_*`: an arithmetic proof is not two vendors agreeing."""
    documents = [
        document(
            **line(
                0,
                commission_amount=cell(candidates=["100.00", "500.00"]),
                commissionable_amount=cell("1000.00", accepted=True),
                stated_commission_rate=cell("10", accepted=True),
            )
        ),
        document("p2", **{"header.total": "not an object"}),
    ]
    resolutions, _, _ = lane.reconcile(documents)
    amended, applied = lane.apply_resolutions(documents, resolutions)
    assert applied == 1
    cellback = amended[0]["fields"]["lines[0].commission_amount"]
    assert cellback["value"] == "100.00" and cellback["accepted"] is True
    assert cellback["acceptance"]["accepted_by"] == "reconciled_by_document_arithmetic"
    assert cellback["acceptance"]["expected_value"] == 100.0
    # The consensus flag is retained: what consensus recorded is still readable.
    assert cellback["consensus_flag"] == "no_consensus"


def test_the_command_writes_a_proposal_and_optionally_applies_it(tmp_path, capsys):
    records = {
        "documents": [
            document(
                **line(
                    0,
                    commission_amount=cell(candidates=["100.00", "500.00"]),
                    commissionable_amount=cell("1000.00", accepted=True),
                    stated_commission_rate=cell("10", accepted=True),
                )
            )
        ]
    }
    assert (
        lane.main(
            [
                write(tmp_path / "r.json", records),
                "--out",
                str(tmp_path / "out.json"),
                "--exceptions",
                str(tmp_path / "exc.json"),
                "--records-out",
                str(tmp_path / "amended.json"),
            ]
        )
        == 0
    )
    result = json.loads((tmp_path / "out.json").read_text())
    assert result["summary"]["resolved"] == 1
    assert result["summary"]["relative_tolerance"] == lane.DEFAULT_RELATIVE_TOLERANCE
    assert json.loads((tmp_path / "exc.json").read_text())["summary"]["count"] == 0
    amended = json.loads((tmp_path / "amended.json").read_text())
    assert amended["documents"][0]["fields"]["lines[0].commission_amount"]["accepted"] is True
    printed = capsys.readouterr().out
    assert "resolved by the arithmetic : 1" in printed
    assert "applied to records         : 1" in printed
    assert "Not vendor agreement" in printed


def test_malformed_input_and_tolerances_are_refused(tmp_path):
    records = write(tmp_path / "r.json", {"documents": []})
    with pytest.raises(SystemExit, match="tolerances must be greater than zero"):
        lane.main(
            [
                records,
                "--out",
                str(tmp_path / "a.json"),
                "--exceptions",
                str(tmp_path / "b.json"),
                "--tolerance",
                "0",
            ]
        )
    with pytest.raises(SystemExit, match="carries no document list"):
        lane.main(
            [
                write(tmp_path / "bad.json", {"documents": "not a list"}),
                "--out",
                str(tmp_path / "c.json"),
                "--exceptions",
                str(tmp_path / "d.json"),
            ]
        )


def test_quiet_prints_nothing(tmp_path, capsys):
    lane.main(
        [
            write(tmp_path / "r.json", {"documents": [document()]}),
            "--out",
            str(tmp_path / "o.json"),
            "--exceptions",
            str(tmp_path / "e.json"),
            "--quiet",
        ]
    )
    assert capsys.readouterr().out == ""


def test_a_proposal_run_reports_no_application(tmp_path, capsys):
    """Without --records-out nothing is applied, and the report does not claim it was."""
    lane.main(
        [
            write(tmp_path / "r.json", {"documents": [document()]}),
            "--out",
            str(tmp_path / "o.json"),
            "--exceptions",
            str(tmp_path / "e.json"),
        ]
    )
    printed = capsys.readouterr().out
    assert "Open line cells: 0" in printed
    assert "applied to records" not in printed
