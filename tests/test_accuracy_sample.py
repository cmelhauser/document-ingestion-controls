"""Drawing the pages to read against their own image.

`sampling.py` samples the auto-accepted population, which is right for the
projection it makes and empty here: 716 of 716 documents were excluded, so the
accuracy of the extraction went unmeasured exactly where it mattered. This draws
from the whole population, because accuracy is a question about the documents
that were not accepted.
"""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

lane = importlib.import_module("accuracy_sample")


def documents(count, brand):
    return [
        {"document_id": f"{brand}-{index:03d}", "brand_name": brand, "source_page_number": index}
        for index in range(count)
    ]


def test_one_layout_spelled_several_ways_is_one_stratum():
    """A stratum split across spellings measures nothing.

    It drew 128 pages across 26 "layouts" that were eight: `murbrook` and
    `murb rook`, four spellings of Marlow/Bramwell, five of Lumen Weft.
    """
    canonical = lane.canonical_strata(
        ["Murbrook", "murb rook", "Marlow / Bramwell", "Marlow Bramwell", "Marlow/Bramwell Inc."]
    )
    assert canonical[lane.squash("Murbrook")] == "murbrook"
    assert canonical[lane.squash("murb rook")] == "murbrook"
    # The shortest common core names the group.
    assert canonical[lane.squash("Marlow/Bramwell Inc.")] == "marlowbramwell"
    assert canonical[lane.squash("Marlow / Bramwell")] == "marlowbramwell"


def test_a_document_naming_no_layout_still_belongs_somewhere():
    """104 documents on this corpus name none, and they are not dropped."""
    assert lane.stratum_of({"brand_name": ""}, "brand_name", "(unidentified)") == "(unidentified)"
    assert lane.stratum_of({}, "brand_name", "(unidentified)") == "(unidentified)"
    assert lane.stratum_of({"brand_name": "HALVOR"}, "brand_name", "x") == "halvor"


def test_the_draw_is_random_within_a_stratum_and_reproducible():
    """Not the pages that looked interesting, and no seed to lose.

    26 pages were read by hunting -- the biggest page, the oddest form. That
    finds a new class of defect and cannot say how common one is.
    """
    population = documents(40, "halvor")
    first, _ = lane.draw(population, [], "brand_name", "x", 8, "commission_amount")
    again, _ = lane.draw(population, [], "brand_name", "x", 8, "commission_amount")
    assert [row["document_id"] for row in first] == [row["document_id"] for row in again]
    # The draw is not simply the first eight, nor the last.
    drawn = {row["document_id"] for row in first}
    assert drawn != {d["document_id"] for d in population[:8]}
    assert drawn != {d["document_id"] for d in population[-8:]}
    assert len(drawn) == 8


def test_every_stratum_reports_what_one_read_page_stands_for():
    """A rate quoted without this repeats the mistake the lane exists to stop."""
    population = documents(100, "murbrook") + documents(6, "tallis")
    lines = [
        {"document_id": "murbrook-000", "commission_amount__amount": "500"},
        {"document_id": "tallis-000", "commission_amount__amount": "100"},
        # The unreadable sentinel is not money anyone can weight by.
        {"document_id": "tallis-001", "commission_amount__amount": lane.UNREADABLE_SENTINEL},
    ]
    selected, coverage = lane.draw(population, lines, "brand_name", "x", 8, "commission_amount")
    assert coverage["murbrook"]["documents_per_page_read"] == 12.5
    # A stratum smaller than the draw is read in full and speaks only for itself.
    assert coverage["tallis"]["drawn"] == 6
    assert coverage["tallis"]["documents_per_page_read"] == 1.0
    assert coverage["murbrook"]["value"] == 500.0
    assert coverage["tallis"]["value"] == 100.0
    assert len(selected) == 14


def test_a_page_image_is_named_when_the_run_retained_one(tmp_path):
    """The reader needs the image, not just the id."""
    (tmp_path / "000007_halvor-001.png").write_bytes(b"")
    selected, _ = lane.draw(
        documents(2, "halvor"), [], "brand_name", "x", 8, "commission_amount", str(tmp_path)
    )
    images = {row["document_id"]: row["page_image"] for row in selected}
    assert images["halvor-001"].endswith("000007_halvor-001.png")
    assert images["halvor-000"] == ""
    assert lane.image_for("halvor-001", "") == ""


def test_the_command_line_writes_a_plan_and_a_worksheet(tmp_path, capsys):
    docs = tmp_path / "documents.csv"
    docs.write_text("document_id,brand_name,source_page_number\na,HALVOR,1\nb,HALVOR,2\n")
    lines = tmp_path / "lines.csv"
    lines.write_text("document_id,commission_amount__amount\na,100\nb,50\n")
    out, sheet = tmp_path / "plan.json", tmp_path / "sheet.csv"
    sys.argv = [
        "accuracy_sample.py",
        "--documents",
        str(docs),
        "--lines",
        str(lines),
        "--out",
        str(out),
        "--worksheet",
        str(sheet),
        "--per-stratum",
        "1",
    ]
    lane.main()
    plan = json.loads(out.read_text())
    assert plan["summary"]["population_documents"] == 2
    assert plan["summary"]["pages_drawn"] == 1
    assert plan["summary"]["artifact_type"] == lane.ARTIFACT_TYPE
    assert plan["summary"]["coverage"]["halvor"]["documents_per_page_read"] == 2.0
    assert sheet.read_text().startswith("document_id,stratum")
    assert "Population: 2 documents" in capsys.readouterr().out
    # Without a worksheet, and quiet, it still writes the plan and says nothing.
    out.unlink()
    sys.argv = [
        "accuracy_sample.py",
        "--documents",
        str(docs),
        "--lines",
        str(lines),
        "--out",
        str(out),
        "--per-stratum",
        "1",
        "--quiet",
    ]
    lane.main()
    assert capsys.readouterr().out == ""
    assert json.loads(out.read_text())["summary"]["pages_drawn"] == 1


@pytest.mark.parametrize(
    "argv_extra, message",
    [(["--per-stratum", "0"], "--per-stratum must be at least 1"), ([], "No documents in")],
)
def test_the_command_line_refuses_rather_than_drawing_nothing(tmp_path, argv_extra, message):
    """An empty draw must not read like a completed sample."""
    docs = tmp_path / "documents.csv"
    docs.write_text("document_id,brand_name\n")
    lines = tmp_path / "lines.csv"
    lines.write_text("document_id,commission_amount__amount\n")
    sys.argv = [
        "accuracy_sample.py",
        "--documents",
        str(docs),
        "--lines",
        str(lines),
        "--out",
        str(tmp_path / "o.json"),
        *argv_extra,
    ]
    with pytest.raises(SystemExit) as exit_info:
        lane.main()
    assert message in str(exit_info.value)


def test_money_reads_only_what_is_countable():
    assert lane.money("") == 0
    assert lane.money(lane.UNREADABLE_SENTINEL) == 0
    assert lane.money("not a number") == 0
    assert lane.money("12.50") == 12.5
