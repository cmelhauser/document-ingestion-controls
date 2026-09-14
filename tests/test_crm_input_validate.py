"""Validating an exported CRM grain against the canonical schema it loads into.

These tests pin the distinction the lane exists to make: a value that is legal
on a page can still be illegal in the column it lands in, and a repeated value
is only a fault when the column is supposed to identify one record.
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

lane = importlib.import_module("crm_input_validate")

DDL = """
CREATE TABLE contact (
    contact_key         UUID PRIMARY KEY,
    phone               VARCHAR(48),
    CONSTRAINT uq_contact UNIQUE (contact_key)
);
CREATE TABLE payment (
    payment_key         UUID PRIMARY KEY,
    payment_reference   VARCHAR(64),
    payment_date        DATE,
    total_paid          NUMERIC(18,2)
);
CREATE TABLE item (
    item_key            UUID PRIMARY KEY,
    item_code           VARCHAR(12),
    description         TEXT,
    country_of_origin   CHAR(2)
);
CREATE TABLE invoice_line (
    line_key            UUID PRIMARY KEY,
    unit_price          NUMERIC(18,4)
);
"""


def write(path, rows, columns):
    """Write a CSV grain the lane can read."""
    import csv

    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_the_ddl_is_read_into_columns_and_widths():
    targets = lane.target_columns(DDL)
    assert targets["contact.phone"] == "VARCHAR(48)"
    assert targets["payment.payment_date"] == "DATE"
    # A constraint line is not a column.
    assert not any(name.endswith(".CONSTRAINT") for name in targets)
    widths = lane.widths_by_column(targets)
    assert widths["phone"] == 48 and widths["item_code"] == 12
    assert lane.declared_width("TEXT") is None


def test_a_multi_valued_cell_in_a_single_valued_column_is_reported(tmp_path):
    rows = [
        {
            "document_id": "d1",
            "contact_phone": "O. 1 310-555-0150 x 4008; D. 1 323-555-0174 x 4008",
        },
        {"document_id": "d2", "contact_phone": "1 310-555-0150 x 4008"},
    ]
    grain = {"documents": [dict(r) for r in rows]}
    findings, summary = lane.validate(grain, lane.target_columns(DDL))
    reasons = [f["reason"] for f in findings]
    assert reasons.count("multiple_values_in_a_single_valued_column") == 1
    assert summary["by_reason"]["multiple_values_in_a_single_valued_column"] == 1


def test_a_placeholder_identity_is_separated_from_a_shared_reference():
    rows = [
        {"document_id": "d1", "payment_reference": "00"},
        {"document_id": "d2", "payment_reference": "00"},
        {"document_id": "d3", "payment_reference": "15542"},
        {"document_id": "d4", "payment_reference": "15542"},
        # An invoice reference many documents share is the corpus working.
        {"document_id": "d5", "transaction_id": "64364"},
        {"document_id": "d6", "transaction_id": "64364"},
    ]
    findings, _ = lane.validate({"documents": rows}, lane.target_columns(DDL))
    reasons = [f["reason"] for f in findings]
    assert reasons.count("placeholder_standing_in_for_an_identity") == 2
    assert reasons.count("one_identity_claimed_by_several_documents") == 1
    assert "transaction_id" not in {f["column"] for f in findings}


def test_a_value_longer_than_its_target_and_an_address_in_a_product_column():
    rows = [
        {"document_id": "d1", "item_code": "A" * 20, "description": "801 Bayshore Avenue"},
        {"document_id": "d2", "item_code": "BU0597", "description": "LOUNGE CHAIR"},
    ]
    findings, _ = lane.validate({"lines": rows}, lane.target_columns(DDL))
    reasons = {f["reason"] for f in findings}
    assert "longer_than_the_target_column" in reasons
    assert "address_shaped_value_in_a_product_column" in reasons
    long = [f for f in findings if f["reason"] == "longer_than_the_target_column"][0]
    assert "at most 12" in long["expected"]


def test_a_column_the_loader_cannot_cast_is_named():
    rows = [{"document_id": "d1", "payment_date": "7/13/2023", "total_paid": "$140.00"}]
    findings, _ = lane.validate({"documents": rows}, lane.target_columns(DDL))
    untyped = {f["column"]: f for f in findings if f["reason"] == "no_typed_companion"}
    assert untyped["payment_date"]["kind"] == "date"
    assert untyped["total_paid"]["kind"] == "numeric"
    # With the companions present there is nothing to report.
    typed = [
        {
            "document_id": "d1",
            "payment_date": "7/13/2023",
            "payment_date__iso": "2023-07-13",
            "total_paid": "$140.00",
            "total_paid__amount": "140.00",
        }
    ]
    findings, _ = lane.validate({"documents": typed}, lane.target_columns(DDL))
    assert not [f for f in findings if f["reason"] == "no_typed_companion"]


def test_the_cli_writes_a_report_and_a_findings_csv(tmp_path, monkeypatch, capsys):
    schema = tmp_path / "schema.sql"
    schema.write_text(DDL)
    grain = write(
        tmp_path / "documents.csv",
        [{"document_id": "d1", "payment_reference": "00"}],
        ["document_id", "payment_reference"],
    )
    out, csv_out = tmp_path / "report.json", tmp_path / "findings.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crm_input_validate.py",
            "--grain",
            f"documents={grain}",
            "--schema",
            str(schema),
            "--out",
            str(out),
            "--findings-csv",
            str(csv_out),
        ],
    )
    lane.main()
    report = json.loads(out.read_text())
    assert report["artifact_type"] == lane.ARTIFACT_TYPE
    assert report["summary"]["findings"] == 1
    assert "placeholder_standing_in_for_an_identity" in csv_out.read_text()
    assert "findings:" in capsys.readouterr().out


def test_a_malformed_grain_argument_and_an_empty_schema_are_refused(tmp_path, monkeypatch):
    schema = tmp_path / "schema.sql"
    schema.write_text(DDL)
    grain = write(tmp_path / "d.csv", [{"document_id": "d1"}], ["document_id"])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crm_input_validate.py",
            "--grain",
            "no-equals-sign",
            "--schema",
            str(schema),
            "--out",
            str(tmp_path / "o.json"),
        ],
    )
    with pytest.raises(SystemExit, match="NAME=PATH"):
        lane.main()
    empty = tmp_path / "empty.sql"
    empty.write_text("-- nothing here\n")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crm_input_validate.py",
            "--grain",
            f"documents={grain}",
            "--schema",
            str(empty),
            "--out",
            str(tmp_path / "o2.json"),
            "--quiet",
        ],
    )
    with pytest.raises(SystemExit, match="No target columns"):
        lane.main()


def test_an_empty_column_and_a_unique_identity_report_nothing():
    """A column present but never filled needs no cast, and a key used once is fine."""
    rows = [
        {"document_id": "d1", "payment_date": "", "payment_reference": "15542"},
        {"document_id": "d2", "payment_date": "", "payment_reference": "15999"},
    ]
    findings, summary = lane.validate({"documents": rows}, lane.target_columns(DDL))
    assert findings == [] and summary["findings"] == 0


def test_the_cli_runs_quietly_without_a_findings_csv(tmp_path, monkeypatch, capsys):
    schema = tmp_path / "schema.sql"
    schema.write_text(DDL)
    grain = write(
        tmp_path / "d.csv",
        [{"document_id": "d1", "payment_reference": "15542"}],
        ["document_id", "payment_reference"],
    )
    out = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "crm_input_validate.py",
            "--grain",
            f"documents={grain}",
            "--schema",
            str(schema),
            "--out",
            str(out),
            "--quiet",
        ],
    )
    lane.main()
    assert capsys.readouterr().out == ""
    assert json.loads(out.read_text())["summary"]["findings"] == 0


def test_a_country_name_is_reported_where_a_two_letter_code_belongs():
    """Every canonical country column is CHAR(2), and a name truncates to two letters."""
    rows = [
        {"document_id": "d1", "country_of_destination": "United States of America (the)"},
        {"document_id": "d2", "country_of_origin": "US"},
        {"document_id": "d3", "country_of_origin": ""},
    ]
    findings, _ = lane.validate({"documents": rows}, lane.target_columns(DDL))
    hits = [f for f in findings if f["reason"] == "country_name_where_a_two_letter_code_belongs"]
    assert len(hits) == 1 and hits[0]["column"] == "country_of_destination"
    assert "lookup, not a cast" in hits[0]["expected"]


def test_an_inferred_code_passes_and_its_provenance_is_never_a_target():
    """The code beside a name loads; the columns saying how it was found do not.

    Checked as values, the method's own description of a country --
    `the ISO short name “United States of America (the)”` -- was reported as a
    country name where a code belongs, twice for every row the lane filled.
    """
    rows = [
        {
            "document_id": "d1",
            "country_of_destination": "United States of America (the)",
            "country_of_destination__inferred": "US",
            "country_of_destination__inferred_by": "iso_3166_1_alpha2",
            "country_of_destination__inferred_evidence": (
                "the ISO short name “United States of America (the)”"
            ),
        }
    ]
    findings, _ = lane.validate({"documents": rows}, lane.target_columns(DDL))
    hits = [f for f in findings if f["reason"] == "country_name_where_a_two_letter_code_belongs"]
    assert [f["column"] for f in hits] == ["country_of_destination"]


def test_an_inferred_value_is_held_to_the_width_of_the_column_it_fills():
    """It loads into that column, so a loader truncates it the same way."""
    rows = [
        {
            "document_id": "d1",
            "item_code__inferred": "A" * 20,
            "item_code__inferred_evidence": "3 statements print this job and amount with it" * 5,
        }
    ]
    findings, _ = lane.validate({"lines": rows}, lane.target_columns(DDL))
    long = [f for f in findings if f["reason"] == "longer_than_the_target_column"]
    assert [f["column"] for f in long] == ["item_code__inferred"]
    assert "at most 12" in long[0]["expected"]


def test_a_value_with_more_decimals_than_the_target_keeps_is_reported():
    """The declared scale decides, not an assumption about it.

    `unit_price` is NUMERIC(18,4) here, so four places are kept and five are not.
    """
    targets = lane.target_columns(DDL)
    assert lane.scales_by_column(targets)["unit_price"] == 4
    assert lane.declared_scale("TEXT") is None
    kept, lost = "1.2345", "1.23456"
    for value, expected in ((kept, 0), (lost, 1)):
        findings, _ = lane.validate(
            {"lines": [{"document_id": "d", "unit_price": value, "unit_price__amount": value}]},
            targets,
        )
        assert (
            len([f for f in findings if f["reason"] == "more_decimal_places_than_the_target_keeps"])
            == expected
        )


TYPED_DDL = """
CREATE TYPE review_status_t AS ENUM (
    'auto_accepted', 'open_exception'
);
CREATE TABLE document_type (
    document_type   VARCHAR(48) PRIMARY KEY,
    description     TEXT NOT NULL
);
CREATE TABLE job (
    job_number      TEXT PRIMARY KEY,
    review_status   review_status_t NOT NULL
);
CREATE TABLE document (
    document_id     UUID PRIMARY KEY,
    document_type   VARCHAR(48) REFERENCES document_type(document_type),
    job_number      TEXT REFERENCES job(job_number),
    review_status   review_status_t NOT NULL
);
"""


def test_a_value_the_target_enum_does_not_admit_is_reported():
    """A width check passes `review_queued`; the column still refuses it.

    The pipeline writes that status and `review_status_t` has no such member, so
    twenty-six line rows failed the load on the type rather than on the width.
    """
    rows = [
        {"document_id": "d1", "document_review_status": "review_queued"},
        {"document_id": "d2", "document_review_status": "open_exception"},
        {"document_id": "d3", "document_review_status": ""},
    ]
    findings, summary = lane.validate(
        {"documents": rows}, lane.target_columns(TYPED_DDL), TYPED_DDL
    )
    hits = [f for f in findings if f["reason"] == "value_outside_the_target_enum"]
    assert len(hits) == 1
    assert hits[0]["document_id"] == "d1" and hits[0]["value"] == "review_queued"
    assert "auto_accepted, open_exception" in hits[0]["expected"]
    assert summary["by_reason"]["value_outside_the_target_enum"] == 1


def test_the_enum_and_the_columns_carrying_one_are_read_from_the_schema():
    """Every defect this session began as a list drifting from its schema."""
    members = lane.enum_members(TYPED_DDL)
    assert members["review_status_t"] == frozenset({"auto_accepted", "open_exception"})
    assert lane.columns_declaring(TYPED_DDL, members) == {"review_status": "review_status_t"}
    # The export names the document's status for the row it came from.
    assert lane.target_name("document_review_status") == "review_status"
    assert lane.target_name("item_code") == "item_code"


def test_only_a_seeded_vocabulary_counts_as_a_missing_dimension_row():
    """A job number is a business key; a document type is a list to seed.

    The schema separates them: a table the pipeline fills carries a
    `review_status`, and the small closed lists carry none. Reporting the 699
    job numbers alongside would bury the six rows that are a prerequisite.
    """
    assert lane.controlled_vocabularies(TYPED_DDL) == {"document_type"}
    assert lane.foreign_keys(TYPED_DDL) == {"document_type": "document_type"}
    rows = [
        {"document_id": "d1", "document_type": "unknown", "job_number": "12390"},
        {"document_id": "d2", "document_type": "unknown", "job_number": "12471"},
        {"document_id": "d3", "document_type": "", "job_number": ""},
    ]
    findings, _ = lane.validate({"documents": rows}, lane.target_columns(TYPED_DDL), TYPED_DDL)
    hits = [
        f for f in findings if f["reason"] == "references_a_dimension_row_the_run_never_creates"
    ]
    # One finding per distinct value, carrying how many rows wait on it.
    assert len(hits) == 1
    assert hits[0]["column"] == "document_type" and hits[0]["value"] == "unknown"
    assert hits[0]["rows"] == 2
    assert "seeded before the fact loads" in hits[0]["expected"]


def test_a_year_that_cannot_be_a_year_is_reported():
    """`0122-05-24` is well-formed ISO, casts fine, and is from the year 122.

    The one date fault a format check cannot see. The raw reading travels with
    the finding because `05/24/0122` needs re-reading, not arithmetic -- nothing
    here can decide which year `202` was meant to be.
    """
    rows = [
        {
            "document_id": "d1",
            "transaction_date": "05/24/0122",
            "transaction_date__iso": "0122-05-24",
        },
        {"document_id": "d2", "ship_date": "03/17/202", "ship_date__iso": "0202-03-17"},
        {
            "document_id": "d3",
            "transaction_date": "05/24/2022",
            "transaction_date__iso": "2022-05-24",
        },
        {"document_id": "d4", "transaction_date": "", "transaction_date__iso": ""},
        # A typed month is held to the same test as a typed date.
        {"document_id": "d5", "period_end": "Dec 0122", "period_end__month": "0122-12"},
        {"document_id": "d6", "period_end": "December 2025", "period_end__month": "2025-12"},
    ]
    findings, _ = lane.validate({"lines": rows}, lane.target_columns(DDL))
    hits = [f for f in findings if f["reason"] == "date_year_is_not_a_year"]
    assert [f["document_id"] for f in hits] == ["d1", "d2", "d5"]
    assert hits[0]["value"] == "0122-05-24"
    assert "'05/24/0122'" in hits[0]["expected"]
    assert hits[2]["value"] == "0122-12"
    assert "'Dec 0122'" in hits[2]["expected"]
