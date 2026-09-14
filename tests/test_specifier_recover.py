"""The specifier recovery lane, which reads a column the schema once had no field for.

These tests pin the two things that make recovery legitimate rather than
guesswork: the value comes from a retained reading, and the join is a printed
key both sides already carry.
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

lane = importlib.import_module("specifier_recover")

PAGE = "\n".join(
    [
        "Martin",
        "/ Bramwell",
        "Commissions Due",
        "COMPANY",
        "ACK#",
        "P.O.#",
        "P.O. DATE",
        "SPECIFIER",
        "SAVANNA BUSINESS INT.",
        "137984",
        "2308.00125",
        "09/22/23",
        "Unknown Specifier",
        "1",
        "LAS RONDAS",
        "137984",
        "10,821.60",
        "P.O. Total:",
        "10,821.60",
        "CORNERWISE DESIGN SERVICE 138280",
        "558-26-462886",
        "11/01/23",
        "NORCROSS TAMPA",
        "8",
        "LAS RUTAS",
        "138280",
        "18,864.00",
        # The line after a date is sometimes an invoice and date, or an amount.
        "133664",
        "08/23/2022",
        "64397 01/16/2023",
        "135157",
        "07/21/2022",
        "91.09",
    ]
)


def write_response(path, text):
    """Write a retained extractor response in the shape the lane reads."""
    path.write_text(json.dumps({"request": {}, "response": {"document": {"text": text}}}))
    return path


def test_the_specifier_is_the_name_printed_after_the_order_date():
    found = lane.specifiers_by_order(PAGE.splitlines())
    assert found == {
        "2308.00125": "Unknown Specifier",
        "558-26-462886": "NORCROSS TAMPA",
    }
    # An invoice number and date, and an amount, are not company names.
    assert "133664" not in found and "135157" not in found


def test_a_reading_that_is_mostly_digits_is_not_a_firm():
    assert lane.firm_like("NORCROSS TAMPA") and lane.firm_like("BSQ - FLORIDA")
    assert not lane.firm_like("64397 01/16/2023")
    assert not lane.firm_like("91.09")
    assert not lane.firm_like("") and not lane.firm_like("AB")


def test_recovery_attaches_by_the_order_number_the_line_already_carries(tmp_path):
    raw = tmp_path / "docai"
    raw.mkdir()
    write_response(raw / "000001_corpus__p0001.json", PAGE)
    records = [
        {
            "document_id": "corpus__p0001",
            "lines": [
                {"purchase_order_number": "2308.00125", "brand_name": "Unknown Specifier"},
                {"purchase_order_number": {"value": "558-26-462886"}, "brand_name": "Martin"},
                {"purchase_order_number": "not-on-the-page"},
            ],
        }
    ]
    out, exceptions, summary = lane.recover(records, lane.responses_by_document(raw))
    lines = out[0]["lines"]
    assert lines[0]["specifier_name"]["value"] == "Unknown Specifier"
    assert lines[1]["specifier_name"]["value"] == "NORCROSS TAMPA"
    assert "specifier_name" not in lines[2], "a line quoting no matching order gets nothing"
    # The misfiling this lane exists to expose.
    assert lines[0]["brand_name_holds_the_specifier"] is True
    assert "brand_name_holds_the_specifier" not in lines[1]
    # The recovered value is never passed off as vendor agreement.
    assert lines[0]["specifier_name"]["recovered_by"] == lane.RECOVERED_EVIDENCE
    assert summary["lines_attached"] == 2 and summary["brand_held_the_specifier"] == 1
    assert not exceptions
    # The original reading is preserved.
    assert lines[0]["brand_name"] == "Unknown Specifier"


def test_a_page_read_only_in_its_retry_is_read_from_the_retry(tmp_path):
    """The first response per page came back empty for 222 pages of the run."""
    raw = tmp_path / "docai"
    raw.mkdir()
    write_response(raw / "000001_corpus__p0001.json", "")
    write_response(raw / "000002_corpus__p0001__retry1.json", PAGE)
    records = [{"document_id": "corpus__p0001", "lines": [{"purchase_order_number": "2308.00125"}]}]
    out, exceptions, summary = lane.recover(records, lane.responses_by_document(raw))
    assert out[0]["lines"][0]["specifier_name"]["value"] == "Unknown Specifier"
    assert summary["lines_attached"] == 1 and not exceptions


def test_a_line_that_already_states_a_specifier_keeps_it(tmp_path):
    raw = tmp_path / "docai"
    raw.mkdir()
    write_response(raw / "corpus__p0001.json", PAGE)
    records = [
        {
            "document_id": "corpus__p0001",
            "lines": [
                {"purchase_order_number": "2308.00125", "specifier_name": "Already Read"},
                "not-an-object",
            ],
        }
    ]
    out, _, summary = lane.recover(records, lane.responses_by_document(raw))
    assert out[0]["lines"][0]["specifier_name"] == "Already Read"
    assert summary["lines_already_stated"] == 1 and summary["lines_attached"] == 0


def test_a_page_printing_a_specifier_no_line_claims_is_an_exception(tmp_path):
    raw = tmp_path / "docai"
    raw.mkdir()
    write_response(raw / "corpus__p0001.json", PAGE)
    records = [{"document_id": "corpus__p0001", "lines": [{"purchase_order_number": "elsewhere"}]}]
    _, exceptions, _ = lane.recover(records, lane.responses_by_document(raw))
    assert exceptions[0]["reason"] == "specifier_printed_but_no_line_quotes_its_order_number"


def test_a_document_with_no_retained_response_is_an_exception():
    out, exceptions, _ = lane.recover([{"document_id": "corpus__p0002"}], {})
    assert exceptions[0]["reason"] == "no_retained_extractor_response"
    assert out[0]["document_id"] == "corpus__p0002"


def test_a_page_with_no_specifier_is_carried_unchanged(tmp_path):
    raw = tmp_path / "docai"
    raw.mkdir()
    write_response(raw / "corpus__p0003.json", "Invoice\n10,000.00\n")
    out, exceptions, summary = lane.recover(
        [{"document_id": "corpus__p0003", "lines": [{"purchase_order_number": "x"}]}],
        lane.responses_by_document(raw),
    )
    assert summary["with_a_specifier"] == 0 and not exceptions
    assert "specifier_recovery" not in out[0]


def test_unreadable_and_malformed_inputs_are_refused(tmp_path):
    assert lane.extractor_lines(tmp_path / "absent.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert lane.extractor_lines(bad) == []
    assert lane.field_text(None) == "" and lane.field_text({"value": None}) == ""
    assert lane.records_from(write_json(tmp_path / "one.json", {"document_id": "d"})) == [
        {"document_id": "d"}
    ]
    assert lane.records_from(write_json(tmp_path / "list.json", [{"a": 1}])) == [{"a": 1}]
    with pytest.raises(ValueError, match="Input must"):
        lane.records_from(write_json(tmp_path / "bad2.json", {"nope": 1}))
    # Neither a list nor an object is not a record artifact at all.
    with pytest.raises(ValueError, match="Input must"):
        lane.records_from(write_json(tmp_path / "bad3.json", "just a string"))


def write_json(path, value):
    """Write a JSON fixture and return its path."""
    path.write_text(json.dumps(value))
    return path


def test_the_cli_writes_records_and_exceptions(tmp_path, monkeypatch, capsys):
    raw = tmp_path / "docai"
    raw.mkdir()
    write_response(raw / "corpus__p0001.json", PAGE)
    source = write_json(
        tmp_path / "in.json",
        {
            "documents": [
                {"document_id": "corpus__p0001", "lines": [{"purchase_order_number": "2308.00125"}]}
            ]
        },
    )
    out, exceptions = tmp_path / "out.json", tmp_path / "exc.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "specifier_recover.py",
            str(source),
            "--extractor-raw",
            str(raw),
            "--out",
            str(out),
            "--exceptions",
            str(exceptions),
        ],
    )
    lane.main()
    written = json.loads(out.read_text())
    assert written["documents"][0]["lines"][0]["specifier_name"]["value"] == "Unknown Specifier"
    assert json.loads(exceptions.read_text())["summary"]["count"] == 0
    assert "lines given a specifier" in capsys.readouterr().out
    # An empty extractor directory is refused rather than reported as nothing found.
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "specifier_recover.py",
            str(source),
            "--extractor-raw",
            str(tmp_path / "empty"),
            "--out",
            str(tmp_path / "o2.json"),
            "--exceptions",
            str(tmp_path / "e2.json"),
            "--quiet",
        ],
    )
    with pytest.raises(SystemExit, match="No retained extractor responses"):
        lane.main()


def test_a_header_or_a_second_date_is_never_read_as_the_specifier():
    """The line after a date is often another date, or the next column heading."""
    # Candidate is itself a date, then a heading.
    assert lane.specifiers_by_order(["2308.00125", "09/22/23", "10/01/23"]) == {}
    assert lane.specifiers_by_order(["2308.00125", "09/22/23", "P.O. Total:"]) == {}
    # The line before the date is a heading or a date, so there is no order number.
    assert lane.specifiers_by_order(["SPECIFIER", "09/22/23", "NORCROSS TAMPA"]) == {}
    assert lane.specifiers_by_order(["01/01/23", "09/22/23", "NORCROSS TAMPA"]) == {}
    # A date on the first line has nothing before it.
    assert lane.specifiers_by_order(["09/22/23", "NORCROSS TAMPA"]) == {}
    # A date on the last line has nothing after it.
    assert lane.specifiers_by_order(["2308.00125", "09/22/23"]) == {}


def test_quiet_writes_the_artifacts_and_prints_nothing(tmp_path, monkeypatch, capsys):
    raw = tmp_path / "docai"
    raw.mkdir()
    write_response(raw / "corpus__p0001.json", PAGE)
    source = write_json(
        tmp_path / "in.json",
        [{"document_id": "corpus__p0001", "lines": [{"purchase_order_number": "2308.00125"}]}],
    )
    out = tmp_path / "q.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "specifier_recover.py",
            str(source),
            "--extractor-raw",
            str(raw),
            "--out",
            str(out),
            "--exceptions",
            str(tmp_path / "qe.json"),
            "--quiet",
        ],
    )
    lane.main()
    assert capsys.readouterr().out == ""
    assert json.loads(out.read_text())["summary"]["lines_attached"] == 1


def test_a_recovered_reading_reaches_the_flat_field_map_and_is_not_accepted(tmp_path):
    """The export reads `fields`, so a value attached only to a line is invisible.

    And one extractor reading with no model behind it is a single reading, not
    the corroboration status that pairs a model with an extractor.
    """
    raw = tmp_path / "docai"
    raw.mkdir()
    write_response(raw / "corpus__p0001.json", PAGE)
    records = [
        {
            "document_id": "corpus__p0001",
            "fields": {"lines[0].brand_name": {"value": "Unknown Specifier"}},
            "lines": [{"purchase_order_number": "2308.00125"}],
        }
    ]
    out, _, _ = lane.recover(records, lane.responses_by_document(raw))
    entry = out[0]["fields"]["lines[0].specifier_name"]
    assert entry["value"] == "Unknown Specifier"
    assert entry["accepted"] is False and entry["consensus_flag"] == "single_engine"
    assert entry["agreeing_engines"] == [lane.EXTRACTOR_ENGINE]
    assert entry["rule"] == lane.RECOVERED_EVIDENCE
