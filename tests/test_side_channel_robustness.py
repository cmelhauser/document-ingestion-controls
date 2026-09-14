"""Hold the client-supplied side-channel inputs to what a real export looks like.

A client's export is whatever their finance system prints. These tests are
written from that premise rather than from the shape the loaders happened to
expect: a column called ``Job #``, a remittance in CSV, a payment row keyed on
something other than an invoice number.

Each case here was a defect found by running the loaders against a realistic
file rather than reading them.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

attribution = importlib.import_module("attribution")
completeness = importlib.import_module("completeness")
side_channel = importlib.import_module("side_channel_input")


def test_column_headings_are_matched_as_a_client_prints_them():
    """A printed heading reaches the loader without the operator renaming it."""
    assert side_channel.normalized_column_name("Job #") == "job"
    assert side_channel.normalized_column_name("  ACK_Number  ") == "ack_number"
    assert side_channel.normalized_column_name("Invoice No.") == "invoice_no"

    # A bare noun answers to its number column, and a trailing abbreviation
    # expands -- both are what the heading means.
    assert "job_number" in side_channel.column_lookup_keys("Job #")
    assert "invoice_number" in side_channel.column_lookup_keys("Invoice No.")
    assert "ack_number" in side_channel.column_lookup_keys("ACK Num")

    # Only a heading that says it is a number expands. A plain noun keeps its
    # own name, so a "Customer" column can never answer to "customer_number"
    # and shadow a real identifier column beside it.
    assert side_channel.column_lookup_keys("Ship Date") == ["ship_date"]
    assert side_channel.column_lookup_keys("Amount") == ["amount"]
    assert side_channel.column_lookup_keys("Customer") == ["customer"]
    shadowing = side_channel.normalized_columns(
        {"Customer": "Northgate & Co", "Customer Number": "C-9"}
    )
    assert shadowing["customer"] == "Northgate & Co"
    assert shadowing["customer_number"] == "C-9"

    columns = side_channel.normalized_columns({"Job #": "12497", "Amount": "$1.00"})
    assert columns["job"] == "12497" and columns["job_number"] == "12497"
    assert columns["amount"] == "$1.00"

    # An explicit column wins over one inferred from a bare noun.
    both = side_channel.normalized_columns({"job_number": "explicit", "Job": "bare"})
    assert both["job_number"] == "explicit"


def test_reference_export_accepts_a_real_ack_sheet(tmp_path):
    """``Job #`` used to reject every row of an otherwise perfect export."""
    export = tmp_path / "ack.csv"
    export.write_text(
        "Job #,Project,Customer\n"
        "12497,Ponte Verra,Northgate & Co\n"
        " 12512 ,Town Square,Northgate & Co\n"
        ",Missing job number,Orphan\n"
    )
    reference = attribution.load_reference(export, None, None)
    assert reference["rows_ingested"] == 2
    # The genuinely keyless row is still refused, with its columns retained.
    assert len(reference["rejected_rows"]) == 1
    rejected = reference["rejected_rows"][0]
    assert rejected["reason"] == "reference_row_has_no_recognized_key_column"
    # The client's own heading, so an operator can find it in the file they were sent.
    assert "Job #" in rejected["available_columns"]


def test_payment_export_is_read_as_csv_or_json(tmp_path):
    """A remittance in CSV met a raw JSON decoder traceback."""
    csv_export = tmp_path / "payments.csv"
    csv_export.write_text('Invoice #,Amount Paid\n12497,"$12,497.00"\n')
    rows = completeness.load_payments(csv_export)
    assert rows[0]["invoice_number"] == "12497"

    json_export = tmp_path / "payments.json"
    json_export.write_text(json.dumps({"payments": [{"invoice_number": "1", "amount": 1}]}))
    assert completeness.load_payments(json_export)[0]["invoice_number"] == "1"

    bare_list = tmp_path / "list.json"
    bare_list.write_text(json.dumps([{"invoice_number": "2"}]))
    assert completeness.load_payments(bare_list)[0]["invoice_number"] == "2"

    empty = tmp_path / "empty.csv"
    empty.write_text("Invoice #,Amount Paid\n")
    with pytest.raises(ValueError, match="no rows"):
        completeness.load_payments(empty)

    wrong_shape = tmp_path / "wrong.json"
    wrong_shape.write_text(json.dumps({"unexpected": 1}))
    with pytest.raises(ValueError, match="list or an object"):
        completeness.load_payments(wrong_shape)


def test_a_payment_row_that_cannot_be_keyed_is_registered_not_dropped():
    """Rule 2: dropping one reports an invoice open that the client says was paid."""
    docs = [
        {
            "doc_type": "commercial_invoice",
            "invoice_number": "INV-1",
            "amount": 100.0,
            "credits": 0.0,
            "vendor": "Northgate",
            "currency": "USD",
            "explicit_status": None,
            "month": "2024-05",
        }
    ]
    payments = [
        {"invoice_number": "INV-1", "amount": 100.0, "vendor": "Northgate", "currency": "USD"},
        {"reference": "12497", "amount": 12497.0, "vendor": "Northgate"},
        {"invoice_number": "", "amount": 500.0, "vendor": "Nomad"},
    ]
    report = completeness.aging_closure(docs, payments)
    assert report["unusable_payment_application_count"] == 2
    reasons = {item["reason"] for item in report["unusable_payment_applications"]}
    assert reasons == {"payment_row_has_no_invoice_number"}
    # The raw row is retained so a client can be shown exactly what was skipped.
    raw = report["unusable_payment_applications"][0]["raw_record"]
    assert raw["reference"] == "12497"
    assert "reference" in report["unusable_payment_applications"][0]["available_columns"]
    # The usable row still applied.
    assert report["closed"] == 1


def test_completeness_names_the_payments_file_it_could_not_read(tmp_path, monkeypatch):
    """A client's unreadable export gets a message, not a decoder traceback."""
    records = tmp_path / "consensus.json"
    records.write_text(json.dumps({"summary": {}, "documents": []}))
    broken = tmp_path / "payments.json"
    broken.write_text("{not json")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "completeness.py",
            str(records),
            "--payments",
            str(broken),
            "--out",
            str(tmp_path / "out.json"),
            "--quiet",
        ],
    )
    with pytest.raises(SystemExit, match="Completeness failed reading payments"):
        completeness.main()
