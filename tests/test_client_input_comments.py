"""Tests for general client-input comment parsing and reasoning-only context."""

import csv
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

comments = importlib.import_module("client_input_comments")


def test_plain_json_and_delimited_comment_parsing(tmp_path):
    text = tmp_path / "comments.md"
    text.write_text("First note\n\nSecond note\n")
    parsed = comments.parse_comments(text)
    assert [item["client_comment"] for item in parsed] == ["First note", "Second note"]
    source_json = tmp_path / "comments.json"
    source_json.write_text(
        json.dumps({"comments": ["one", {"comment": "two", "scope": "invoice"}]})
    )
    parsed = comments.parse_comments(source_json)
    assert parsed[1]["client_comment"] == "two" and parsed[1]["source_fields"]["scope"] == "invoice"
    source_csv = tmp_path / "comments.csv"
    with source_csv.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["document_id", "note"])
        writer.writeheader()
        writer.writerow({"document_id": "d1", "note": "check address"})
    assert comments.parse_comments(source_csv)[0]["document_id"] == "d1"
    # A spreadsheet with no recognised comment column is what a client usually
    # sends: notes typed into cells. Every non-empty row is a comment, and the
    # first row is one of them rather than a discarded header.
    freeform = tmp_path / "freeform.csv"
    freeform.write_text(
        "Notes from Julie,,,\n"
        '"The ""Job #"" column is our ACK number",,\n'
        ",,,\n"
        "Nomad Residences goes to Wynwood,,,\n"
    )
    parsed_freeform = comments.parse_comments(freeform)
    assert [item["client_comment"] for item in parsed_freeform] == [
        "Notes from Julie",
        'The "Job #" column is our ACK number',
        "Nomad Residences goes to Wynwood",
    ]
    # Every original cell stays recoverable beside the joined comment.
    multi = tmp_path / "multi.csv"
    multi.write_text("12497,rate should be 10%\n")
    item = comments.parse_comments(multi)[0]
    assert item["client_comment"] == "12497 rate should be 10%"
    assert item["source_fields"]["column_1"] == "12497"
    assert item["source_fields"]["column_2"] == "rate should be 10%"
    assert item["source_fields"]["source_row"] == 1
    # A file of nothing but empty rows, and a file with no rows at all, both
    # refuse rather than contributing an empty context.
    for name, body in (("blank.csv", ",,\n,,\n"), ("nothing.csv", "")):
        empty = tmp_path / name
        empty.write_text(body)
        with pytest.raises(ValueError, match="at least one comment"):
            comments.parse_comments(empty)
    # "notes" is accepted alongside "note" as a headed comment column.
    plural = tmp_path / "plural.csv"
    plural.write_text("document_id,notes\nd9,check the total\n")
    assert comments.parse_comments(plural)[0]["client_comment"] == "check the total"


def test_invalid_and_empty_comment_inputs(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("\n")
    with pytest.raises(ValueError, match="at least one"):
        comments.parse_comments(empty)
    unsupported = tmp_path / "comments.xlsx"
    unsupported.write_bytes(b"x")
    with pytest.raises(ValueError, match="Unsupported"):
        comments.parse_comments(unsupported)
    malformed = tmp_path / "bad.json"
    malformed.write_text(json.dumps({"comments": {}}))
    with pytest.raises(ValueError, match="list"):
        comments.parse_comments(malformed)
    blank = tmp_path / "blank.json"
    blank.write_text(json.dumps([{"comment": ""}]))
    with pytest.raises(ValueError, match="non-empty"):
        comments.parse_comments(blank)
    non_object = tmp_path / "non-object.json"
    non_object.write_text(json.dumps([1]))
    with pytest.raises(ValueError, match="text or an object"):
        comments.parse_comments(non_object)


def test_build_run_and_no_clobber(monkeypatch, tmp_path, capsys):
    source = tmp_path / "comments.txt"
    source.write_text("Review the handwritten delivery date\n")
    out = tmp_path / "context.json"
    result = comments.run([source], out)
    assert result["artifact_type"] == "client_review_context_v1"
    assert result["reasoning_only"] is True
    assert result["independent_consensus_input"] is False
    assert result["client_comments"][0]["evidence_role"].startswith("untrusted")
    assert result["source"]["comment_files"][0]["sha256"] == comments.digest(source)
    with pytest.raises(FileExistsError, match="overwrite"):
        comments.run([source], out)
    monkeypatch.setattr(
        sys, "argv", ["client_input_comments.py", str(source), "--out", str(tmp_path / "cli.json")]
    )
    comments.main()
    assert "reasoning-only" in capsys.readouterr().out
    monkeypatch.setattr(comments, "run", lambda *a: (_ for _ in ()).throw(ValueError("bad input")))
    monkeypatch.setattr(sys, "argv", ["client_input_comments.py", str(source), "--out", "x"])
    with pytest.raises(SystemExit, match="bad input"):
        comments.main()


def _minimal_pdf(path, lines):
    """Write a small real PDF whose text layer holds one comment per line."""
    text = "BT /F1 11 Tf 54 720 Td 16 TL\n" + "".join(f"({line}) Tj T*\n" for line in lines) + "ET"
    stream = text.encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    buf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(buf))
        buf += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(buf)
    buf += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        buf += f"{offset:010d} 00000 n \n".encode()
    buf += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(bytes(buf))
    return path


def test_pdf_client_comments_are_preserved_verbatim(tmp_path):
    """Clients send comments as whatever they already have, including a PDF."""
    lines = ["Job 12497 was cancelled.", "Ponte Verra and Ponte Gaida are one project."]
    path = _minimal_pdf(tmp_path / "notes.pdf", lines)
    parsed = comments.parse_comments(path)
    assert [item["client_comment"] for item in parsed] == lines


def test_a_pdf_with_no_text_layer_is_refused(tmp_path):
    """A scanned page is document evidence, not reasoning-only context.

    Returning an empty context would let a lane run with nothing while reporting
    that client context was supplied.
    """
    path = _minimal_pdf(tmp_path / "blank.pdf", [])
    with pytest.raises(ValueError, match="at least one comment"):
        comments.parse_comments(path)


def test_pdf_and_delimited_comments_combine_into_one_context(tmp_path):
    csv_path = tmp_path / "notes.csv"
    csv_path.write_text("client_comment\nThe dealer is Northgate Co.\n", encoding="utf-8")
    pdf_path = _minimal_pdf(tmp_path / "notes.pdf", ["Rows marked pending are unshipped."])
    context = comments.build_context([csv_path, pdf_path])
    assert context["summary"]["comment_files"] == 2
    assert context["summary"]["comments"] == 2
    assert context["reasoning_only"] is True
    assert context["independent_consensus_input"] is False
    assert {entry["name"] for entry in context["source"]["comment_files"]} == {
        "notes.csv",
        "notes.pdf",
    }


def test_a_missing_pdf_reader_names_the_alternatives(tmp_path, monkeypatch):
    path = _minimal_pdf(tmp_path / "notes.pdf", ["A comment."])
    monkeypatch.setattr(comments.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="pdftotext is required"):
        comments.parse_comments(path)


def test_an_unreadable_pdf_is_refused(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf at all")
    with pytest.raises(ValueError, match="Could not read PDF client comments"):
        comments.parse_comments(path)
