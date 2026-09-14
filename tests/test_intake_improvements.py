"""Tests for the conservative reassembly and bounded-adjudication controls."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

adjudicate = importlib.import_module("adjudicate")
reassemble_pages = importlib.import_module("reassemble_pages")


def write_json(path, value):
    path.write_text(json.dumps(value))
    return path


def invoke(monkeypatch, module, *args):
    monkeypatch.setattr(sys, "argv", [module.__file__, *map(str, args)])
    module.main()


def intake_record(number, text_file=None, page_pdf=None, doc_type="commercial_invoice"):
    return {
        "page_id": f"p{number}",
        "source_page_number": number,
        "text_file": text_file,
        "page_pdf": page_pdf,
        "document_type": doc_type,
    }


def test_reassembly_helpers_cover_evidence_duplicates_and_outcomes(tmp_path):
    root = tmp_path / "intake"
    (root / "text").mkdir(parents=True)
    (root / "pages").mkdir()
    (root / "text" / "one.txt").write_text("5527088322\nInvoice number\nPAGE 1 OF 2")
    (root / "text" / "two.txt").write_text("Invoice number\n5527088322\nPAGE 2 OF 2")
    (root / "pages" / "one.pdf").write_bytes(b"same")
    (root / "pages" / "two.pdf").write_bytes(b"same")
    records = [
        intake_record(1, "text/one.txt", "pages/one.pdf"),
        intake_record(2, "text/two.txt", "pages/two.pdf"),
    ]
    assert reassemble_pages.page_evidence(records[0], root)["identifiers"] == ["5527088322"]
    assert len(reassemble_pages.sha256(root / "pages/one.pdf")) == 64
    (root / "text" / "copy.txt").write_text((root / "text" / "one.txt").read_text())
    (root / "pages" / "copy.pdf").write_bytes(b"same")
    duplicates = reassemble_pages.duplicate_candidates(
        [records[0], intake_record(3, "text/copy.txt", "pages/copy.pdf")], root
    )
    assert duplicates[0]["duplicate_of"] == "p1"
    groups, exceptions, unassigned = reassemble_pages.reassemble(records, root)
    assert groups[0]["source_page_range"] == "1-2" and not exceptions and not unassigned
    incomplete = [records[0]]
    assert (
        reassemble_pages.reassemble(incomplete, root)[1][0]["reason"] == "incomplete_page_sequence"
    )
    bad = [records[0], {**records[1], "document_type": "packing_list"}]
    assert (
        reassemble_pages.reassemble(bad, root)[1][0]["reason"] == "conflicting_reassembly_evidence"
    )
    assert reassemble_pages.duplicate_candidates([intake_record(3)], root) == []
    assert (
        reassemble_pages.duplicate_candidates(
            [intake_record(3, "text/not-found.txt", "pages/not-found.pdf")], root
        )
        == []
    )
    (root / "text" / "three.txt").write_text("Invoice number 998877")
    (root / "text" / "four.txt").write_text("Invoice no 998877")
    unordered = [
        intake_record(3, "text/three.txt", doc_type="commercial_invoice"),
        intake_record(4, "text/four.txt", doc_type="commercial_invoice"),
    ]
    broad_groups, broad_exceptions, broad_unassigned = reassemble_pages.reassemble(
        unordered, root, True
    )
    assert broad_groups[0]["evidence"]["method"] == "shared_identifier_unordered"
    assert not broad_exceptions and not broad_unassigned
    assert reassemble_pages.broad_reassembly([intake_record(5, doc_type=None)], root, set(), 0) == (
        [],
        [],
    )
    (root / "text" / "four.txt").write_text("Invoice no 998877 PO number PO-77")
    assert (
        reassemble_pages.reassemble(unordered, root, True)[1][0]["reason"]
        == "ambiguous_unordered_identifier_evidence"
    )
    assert reassemble_pages.page_evidence(intake_record(4, "text/missing.txt"), root)["text"] == ""
    assert reassemble_pages.page_evidence(intake_record(5), root)["marker"] is None
    manifest = write_json(root / "ingestion_manifest.json", {"pages": records})
    assert reassemble_pages.load_manifest(manifest)["pages"] == records
    with pytest.raises(ValueError, match="pages list"):
        reassemble_pages.load_manifest(write_json(root / "bad.json", []))
    with pytest.raises(ValueError, match="requires page_id"):
        reassemble_pages.load_manifest(write_json(root / "missing-page.json", {"pages": [{}]}))
    with pytest.raises(ValueError, match="unique"):
        reassemble_pages.load_manifest(
            write_json(root / "duplicate-page.json", {"pages": [records[0], records[0]]})
        )
    with pytest.raises(ValueError, match="must be relative"):
        reassemble_pages.page_evidence(
            {**records[0], "text_file": str(root / "text/one.txt")}, root
        )
    with pytest.raises(ValueError, match="escapes"):
        reassemble_pages.duplicate_candidates([{**records[0], "page_pdf": "../outside.pdf"}], root)


def test_reassembly_cli_success_quiet_and_errors(monkeypatch, tmp_path, capsys):
    root = tmp_path / "intake"
    root.mkdir()
    manifest = write_json(root / "ingestion_manifest.json", {"pages": []})
    out, exc = tmp_path / "groups.json", tmp_path / "exceptions.json"
    invoke(monkeypatch, reassemble_pages, manifest, "--out", out, "--exceptions", exc)
    assert json.loads(out.read_text())["summary"]["pages"] == 0
    assert "Proposed groups:" in capsys.readouterr().out
    quiet_out, quiet_exc = tmp_path / "quiet.json", tmp_path / "quiet-exc.json"
    invoke(
        monkeypatch,
        reassemble_pages,
        manifest,
        "--out",
        quiet_out,
        "--exceptions",
        quiet_exc,
        "--quiet",
        "--broad-unordered-proposals",
    )
    assert capsys.readouterr().out == ""
    with pytest.raises(SystemExit, match="Reassembly failed"):
        invoke(
            monkeypatch, reassemble_pages, root / "missing.json", "--out", out, "--exceptions", exc
        )


def test_adjudication_helpers_and_cli_paths(monkeypatch, tmp_path, capsys):
    assert adjudicate.number({"value": "$2"}) == 2 and adjudicate.number("bad") is None
    assert adjudicate.value_at({"header": {"x": {"value": 3}}}, "header.x") == 3
    assert adjudicate.value_at({}, "header.x") is None
    assert (
        adjudicate.expected_total(
            {
                "header": {
                    "subtotal": 10,
                    "tax_amount": 1,
                    "freight_amount": 2,
                    "accessorial_total": None,
                    "discount_amount": 3,
                }
            }
        )
        == 10
    )
    assert adjudicate.expected_total({"header": {}}) is None
    sources = [
        {"document_id": "d1", "header": {"total_amount": "11"}},
        {"document_id": "d1", "header": {"total_amount": "12"}},
    ]
    assert adjudicate.candidates("d1", sources, "header.total_amount") == ["11", "12"]
    docs = [
        {"document_id": "accepted", "fields": {"header.total_amount": {"accepted": True}}},
        {
            "document_id": "d1",
            "header": {"subtotal": 10, "tax_amount": 1},
            "fields": {"header.total_amount": {"accepted": False, "value": None}},
        },
        {
            "document_id": "missing",
            "header": {"subtotal": 1},
            "fields": {"header.total_amount": {"accepted": False, "value": None}},
        },
    ]
    amendments, exceptions, skipped = adjudicate.adjudicate(docs, sources, 2)
    assert amendments[0]["proposed_value"] == "11"
    # The arithmetic computed a total and no engine read it. That is not an
    # ambiguity between candidate values; there are no candidate values.
    assert exceptions[0]["reason"] == "no_candidate_matches_the_document_arithmetic"
    assert skipped == {"field_absent": 0, "already_accepted": 1}
    list_path = write_json(tmp_path / "list.json", sources)
    dict_path = write_json(tmp_path / "dict.json", {"records": sources})
    assert adjudicate.records_from(list_path) == sources
    assert adjudicate.records_from(dict_path) == sources
    with pytest.raises(ValueError, match="record list"):
        adjudicate.records_from(write_json(tmp_path / "bad.json", {}))
    consensus_path = write_json(tmp_path / "consensus.json", {"documents": docs})
    out, exc = tmp_path / "amendments.json", tmp_path / "exceptions.json"
    invoke(
        monkeypatch,
        adjudicate,
        consensus_path,
        list_path,
        dict_path,
        "--out",
        out,
        "--exceptions",
        exc,
        "--max-passes",
        "1",
    )
    assert json.loads(out.read_text())["summary"]["proposed_amendments"] == 1
    assert "Proposed amendments:" in capsys.readouterr().out
    quiet_out, quiet_exc = (
        tmp_path / "quiet-amendments.json",
        tmp_path / "quiet-adjudication-exceptions.json",
    )
    invoke(
        monkeypatch,
        adjudicate,
        consensus_path,
        list_path,
        "--out",
        quiet_out,
        "--exceptions",
        quiet_exc,
        "--quiet",
    )
    assert capsys.readouterr().out == ""
    with pytest.raises(SystemExit, match="Adjudication failed"):
        invoke(
            monkeypatch,
            adjudicate,
            tmp_path / "missing.json",
            list_path,
            "--out",
            out,
            "--exceptions",
            exc,
            "--quiet",
        )
    with pytest.raises(SystemExit, match="Proofed input must contain"):
        invoke(
            monkeypatch,
            adjudicate,
            write_json(tmp_path / "bad-consensus.json", {}),
            list_path,
            "--out",
            out,
            "--exceptions",
            exc,
            "--quiet",
        )


def test_a_document_without_the_adjudicated_field_is_not_a_finding():
    """638 of 716 documents were queued as disputes about a field they lack.

    `header.total_amount` exists on 78 documents of that corpus. Adjudicating
    the other 638 asked a client to choose between candidate readings of a total
    their document never carried, and recorded the request as a review item that
    blocked the document from export.
    """
    docs = [
        {"document_id": "no-field", "fields": {"header.brand_name": {"accepted": False}}},
        {"document_id": "no-fields-at-all"},
    ]
    amendments, exceptions, skipped = adjudicate.adjudicate(docs, [], 2)
    assert (amendments, exceptions) == ([], [])
    assert skipped["field_absent"] == 2


def test_arithmetic_that_could_not_run_is_not_arithmetic_that_found_nothing():
    """Rule 9, on the control that queued the most documents in the run.

    `expected_total` needs `subtotal`, which is present on 1 of 716 documents
    and valued on none. It returned None for every document, and every document
    was then recorded `adjudication_not_unique` -- a claim that the control ran
    and could not pick a unique value. It had not picked anything.
    """
    docs = [
        {
            "document_id": "d1",
            "header": {"total_paid": "500.00"},
            "fields": {"header.total_amount": {"accepted": False}},
        }
    ]
    sources = [{"document_id": "d1", "header": {"total_amount": "500.00"}}]
    _, exceptions, _ = adjudicate.adjudicate(docs, sources, 2)
    assert exceptions[0]["reason"] == "arithmetic_inputs_absent"
    assert exceptions[0]["expected_total"] is None
    # What it could not consult, by name, rather than an empty result.
    assert exceptions[0]["unconsulted_key_fields"] == list(adjudicate.ARITHMETIC_INPUTS)
    # The reading is still carried: one engine read a total and nothing could
    # check it, which is the finding.
    assert exceptions[0]["candidate_values"] == ["500.00"]


def test_two_candidates_matching_the_arithmetic_is_the_real_ambiguity():
    """The one case the original message actually described."""
    docs = [
        {
            "document_id": "d1",
            "header": {"subtotal": 10, "tax_amount": 1},
            "fields": {"header.total_amount": {"accepted": False}},
        }
    ]
    sources = [
        {"document_id": "d1", "header": {"total_amount": "11"}},
        {"document_id": "d1", "header": {"total_amount": "11.00"}},
    ]
    amendments, exceptions, _ = adjudicate.adjudicate(docs, sources, 2)
    assert amendments == []
    assert exceptions[0]["reason"] == "adjudication_not_unique"
    assert exceptions[0]["matching_candidate_values"] == ["11", "11.00"]


def test_a_header_that_is_not_an_object_is_read_as_absent_inputs():
    assert adjudicate.arithmetic({"header": "not an object"}) == (
        None,
        list(adjudicate.ARITHMETIC_INPUTS),
    )
