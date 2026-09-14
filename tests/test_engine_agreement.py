"""Tests for qualifying a candidate engine by value rather than by row count."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

agreement = importlib.import_module("engine_agreement")


def cell(value):
    return {"value": value, "confidence": None, "source": "printed"}


def handoff(path, engine, pages):
    """Write a provider handoff whose pages carry the given line cells."""
    records = [
        {
            "page_id": page_id,
            "lines": [{"line_number": index + 1, **row} for index, row in enumerate(rows)],
        }
        for page_id, rows in pages.items()
    ]
    path.write_text(json.dumps({"engine": engine, "records": records}))
    return path


def test_matching_row_counts_do_not_make_a_candidate_agree(tmp_path):
    """The failure this command exists to catch: right shape, wrong content."""
    reference = handoff(
        tmp_path / "ref.json",
        "openai/model-a",
        {"p1": [{"item_code": cell("BU1437"), "sales_amount": cell("5,193.60")}]},
    )
    candidate = handoff(
        tmp_path / "cand.json",
        "openrouter/google/model-b",
        # Same row count. Neither value read correctly.
        {"p1": [{"item_code": cell("LOUNGE C"), "sales_amount": cell("5,193.80")}]},
    )
    result = agreement.compare(reference, candidate)
    summary = result["summary"]
    assert summary["row_count_ratio_pct"] == 100.0
    assert summary["value_recall_pct"] == 0.0
    assert summary["field_exact_pct"] == 0.0
    assert result["reference_engine"] == "openai/model-a"
    assert result["candidate_engine"] == "openrouter/google/model-b"


def test_reading_the_page_but_not_the_table_shows_as_recall_above_field_exact():
    """A value filed under the wrong name was still read; the rates separate that."""
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        reference = handoff(
            tmp / "ref.json",
            "openai/model-a",
            {"p1": [{"item_code": cell("BU1437"), "description": cell("LOUNGE C")}]},
        )
        candidate = handoff(
            tmp / "cand.json",
            "openrouter/google/model-b",
            # Both values present, one in the wrong field.
            {"p1": [{"item_code": cell("LOUNGE C"), "description": cell("BU1437")}]},
        )
        summary = agreement.compare(reference, candidate)["summary"]
        assert summary["value_recall_pct"] == 100.0
        assert summary["field_exact_pct"] == 0.0


def test_an_empty_reading_spelled_as_text_is_not_counted_as_a_value():
    """A mis-dialected schema returns the text "null"; it is absence, not content."""
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        reference = handoff(
            tmp / "ref.json", "openai/model-a", {"p1": [{"item_code": cell("BU1437")}]}
        )
        candidate = handoff(
            tmp / "cand.json",
            "openrouter/google/model-b",
            {"p1": [{"item_code": cell("BU1437"), "description": cell("null"), "upc": cell("  ")}]},
        )
        summary = agreement.compare(reference, candidate)["summary"]
        assert summary["value_recall_pct"] == 100.0
        # The "null" text and the blank add no slot to compare.
        assert summary["compared_slots"] == 1
        assert summary["field_exact_pct"] == 100.0


def test_a_bare_cell_value_is_read_like_a_normalized_one():
    """Handoffs carry {"value": ...} cells, but a bare value must not crash a check."""
    assert agreement.cell_value("BU1437") == "BU1437"
    assert agreement.cell_value({"value": "BU1437"}) == "BU1437"
    assert agreement.cell_value("null") is None
    assert agreement.cell_value(None) is None
    assert agreement.cell_value({"value": None}) is None
    assert agreement.cell_value("   ") is None
    assert agreement.cell_value(42) == "42"


def test_only_pages_both_engines_read_are_compared():
    """A rate over a different population is not a comparison."""
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        reference = handoff(
            tmp / "ref.json",
            "openai/model-a",
            {"p1": [{"item_code": cell("A")}], "p2": [{"item_code": cell("B")}]},
        )
        candidate = handoff(
            tmp / "cand.json",
            "openrouter/google/model-b",
            {"p1": [{"item_code": cell("A")}], "p3": [{"item_code": cell("Z")}]},
        )
        result = agreement.compare(reference, candidate)
        assert result["pages_compared"] == 1
        assert result["reference_pages"] == 2 and result["candidate_pages"] == 2
        assert result["summary"]["value_recall_pct"] == 100.0


def test_a_page_with_nothing_to_compare_divides_by_nothing():
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        reference = handoff(tmp / "ref.json", "openai/model-a", {"p1": []})
        candidate = handoff(tmp / "cand.json", "openrouter/google/model-b", {"p1": []})
        summary = agreement.compare(reference, candidate)["summary"]
        assert summary["value_recall_pct"] is None
        assert summary["field_exact_pct"] is None
        assert summary["row_count_ratio_pct"] is None


def test_malformed_and_disjoint_handoffs_are_refused(tmp_path):
    (tmp_path / "bad.json").write_text(json.dumps({"engine": "x"}))
    with pytest.raises(ValueError, match="records list"):
        agreement.load_records(tmp_path / "bad.json")
    reference = handoff(tmp_path / "ref.json", "a", {"p1": [{"item_code": cell("A")}]})
    candidate = handoff(tmp_path / "cand.json", "b", {"p9": [{"item_code": cell("A")}]})
    with pytest.raises(ValueError, match="share no page"):
        agreement.compare(reference, candidate)
    # A record without a page_id cannot be matched and is not indexed.
    (tmp_path / "nopage.json").write_text(json.dumps({"engine": "c", "records": [{"lines": []}]}))
    _, pages = agreement.load_records(tmp_path / "nopage.json")
    assert pages == {}


def test_cli_reports_both_rates_and_writes_an_artifact(tmp_path, capsys):
    reference = handoff(
        tmp_path / "ref.json", "openai/model-a", {"p1": [{"item_code": cell("BU1437")}]}
    )
    candidate = handoff(
        tmp_path / "cand.json", "openrouter/google/model-b", {"p1": [{"item_code": cell("WRONG")}]}
    )
    out = tmp_path / "agreement.json"
    assert agreement.main([str(reference), str(candidate), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "not corroboration" in printed
    assert "value recall" in printed
    written = json.loads(out.read_text())
    assert written["artifact_type"] == "engine_agreement_v1"
    assert written["summary"]["value_recall_pct"] == 0.0

    assert agreement.main([str(reference), str(candidate), "--quiet"]) == 0
    assert capsys.readouterr().out == ""

    with pytest.raises(SystemExit, match="Engine agreement failed"):
        agreement.main([str(reference), str(tmp_path / "missing.json")])
