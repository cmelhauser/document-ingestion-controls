"""Tests for exporting every extracted field with the status that governs it."""

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

lane = importlib.import_module("field_extract_export")


def write(path, value):
    path.write_text(json.dumps(value))
    return str(path)


def field(value="12.00", flag="consensus_2of2", accepted=True, **extra):
    return {
        "value": value,
        "candidate_values": None,
        "consensus_flag": flag,
        "agreeing_engines": ["openai", "x-ai"],
        "engine_count": 2,
        "rule": "printed_majority",
        "accepted": accepted,
        "is_handwritten": False,
        "source": "printed",
        **extra,
    }


def consensus(documents=None):
    return {
        "documents": documents
        if documents is not None
        else [
            {
                "document_id": "p1",
                "review_status": "open_exception",
                "document_type": "commission_statement",
                "fields": {
                    "header.total_amount": field(),
                    "header.payment_date": field(
                        value=None,
                        flag="single_engine",
                        accepted=False,
                        candidate_values=["2022-11-01"],
                    ),
                    "lines[0].ordinal": field(value="1", flag="derived_ordinal"),
                    "header.dealer_name": field(
                        value=None,
                        flag="no_consensus",
                        accepted=False,
                        candidate_values=["Acme", "Acme Ltd"],
                    ),
                },
            }
        ]
    }


def manifest():
    return {
        "pages": [
            {
                "page_id": "p1",
                "page_pdf": "pages/000001__p1.pdf",
                "source_file": "source/book.pdf",
                "source_page_number": 1,
                "source_sha256": "abc",
                "document_id": None,
            }
        ]
    }


def queue():
    return {
        "items": [
            {"document_id": "p1", "field": "header.dealer_name", "reason": "no_majority"},
            {"page_id": "p1", "field": "header.dealer_name", "reason": "adjudication_not_unique"},
            {"document_id": "", "field": "x", "reason": "dropped_no_document"},
            "not an object",
        ]
    }


def run(tmp_path, extra=(), documents=None, pages=None):
    """Run the command over a small corpus, returning the parsed artifact."""
    payload = manifest() if pages is None else {"pages": pages}
    argv = [
        write(tmp_path / "c.json", consensus(documents)),
        "--manifest",
        write(tmp_path / "m.json", payload),
        "--out",
        str(tmp_path / "out.json"),
        *extra,
    ]
    assert lane.main(argv) == 0
    return json.loads((tmp_path / "out.json").read_text())


def test_every_field_is_exported_once_with_the_status_that_governs_it(tmp_path):
    """The canonical export admits 0 documents here; these rows are the evidence."""
    result = run(tmp_path, ["--final-review", write(tmp_path / "q.json", queue())])
    assert result["summary"]["fields"] == 4
    assert result["summary"]["fields_by_status"] == {
        "accepted_derived": 1,
        "accepted_vendor_agreement": 1,
        "contested": 1,
        "single_reading": 1,
    }
    assert result["summary"]["accepted_fields"] == 2
    assert result["summary"]["canonical_load_permitted"] is False
    assert result["summary"]["final_review_queue_consulted"] is True
    by_field = {row["field"]: row for row in result["rows"]}
    # Rows are ordered by field name, so an importer reading them in order gets a
    # stable file across runs rather than whatever order the dict happened to hold.
    assert [row["field"] for row in result["rows"]] == sorted(by_field)
    contested = by_field["header.dealer_name"]
    assert contested["status"] == "contested"
    # Both queue spellings of the document key reach the same row; a finding
    # recorded against page_id is not a finding about a different document.
    assert contested["blocking_review_reasons"] == "adjudication_not_unique; no_majority"
    assert contested["source_file"] == "source/book.pdf"
    assert contested["source_sha256"] == "abc"
    single = by_field["header.payment_date"]
    assert single["status"] == "single_reading"
    # Consensus did not accept it, so there is no accepted value -- but an engine
    # read one, and the row states it. An export showing only `value` reported an
    # empty cell for 57,150 fields that carry a reading.
    assert single["value"] == ""
    assert single["reading"] == "2022-11-01"
    assert single["reading_engine_count"] == 1
    assert result["summary"]["fields_with_a_reading"] == 4
    assert result["summary"]["fields_no_engine_returned"] == 0


def test_a_withheld_row_says_it_is_not_an_approved_fact(tmp_path):
    """The distinction has to survive being opened in a spreadsheet."""
    result = run(tmp_path)
    assert any("not an approved fact" in finding for finding in result["summary"]["findings"])
    assert any("clears no control" in finding for finding in result["summary"]["findings"])
    for row in result["rows"]:
        assert row["document_review_status"] == "open_exception"


def test_without_the_queue_the_blocking_column_is_reported_unconsulted(tmp_path):
    """An empty column and a corpus with no findings look identical in a CSV."""
    result = run(tmp_path)
    assert result["summary"]["final_review_queue_consulted"] is False
    assert all(row["blocking_review_reasons"] == "" for row in result["rows"])


def test_provenance_joins_on_the_id_the_manifest_actually_keys(tmp_path):
    """The manifest keys pages on page_id; document_id is null until reassembly.

    Joining on document_id matches nothing and produces an export whose every
    provenance column is empty -- which reads as a corpus without sources rather
    than as a join that missed. The count is reported so it cannot pass silently.
    """
    result = run(tmp_path, pages=[{"page_id": "other", "page_pdf": "pages/other.pdf"}])
    assert result["summary"]["documents_without_intake_provenance"] == 1
    assert result["summary"]["documents_without_intake_provenance_sample"] == ["p1"]
    assert all(row["source_file"] == "" for row in result["rows"])


def test_a_page_without_an_id_is_not_indexed(tmp_path):
    assert lane.page_provenance({"pages": [{"page_id": "", "x": 1}, {"page_id": "a"}]}) == {
        "a": {"page_id": "a"}
    }


def test_an_unaccepted_field_is_named_by_its_flag_rather_than_bucketed():
    """A withheld field whose engines agreed is neither single-reading nor contested."""
    assert lane.field_status(field(accepted=False)) == "withheld_consensus_2of2"
    assert lane.field_status(field(flag="", accepted=False)) == "withheld"
    assert lane.field_status(field(flag="derived_ordinal")) == "accepted_derived"


def test_a_mixed_candidate_list_renders_instead_of_raising():
    """A table cell candidate is an object; ordering raw values raises on it."""
    assert lane.joined([{"b": 1}, "a", None]) == "a; {'b': 1}"
    assert lane.joined(None) == ""
    assert lane.joined(7) == "7"


def test_the_csvs_carry_every_row_and_the_accepted_subset(tmp_path):
    run(
        tmp_path,
        [
            "--csv",
            str(tmp_path / "all.csv"),
            "--accepted-csv",
            str(tmp_path / "accepted.csv"),
        ],
    )
    everything = list(csv.DictReader((tmp_path / "all.csv").open()))
    accepted = list(csv.DictReader((tmp_path / "accepted.csv").open()))
    assert len(everything) == 4
    assert {row["status"] for row in accepted} == {
        "accepted_vendor_agreement",
        "accepted_derived",
    }
    assert list(everything[0]) == list(lane.COLUMNS)


def test_the_help_names_every_status_the_accepted_csv_carries(capsys):
    """The help called the accepted rows two vendors agreeing; most were not.

    On the commission run's export 43 vendor agreement was 35,545 of the 88,320
    accepted rows. The option's help is built from the registry that selects the
    rows, and the module description lists each status by hand, so both are held
    to the registry here.
    """
    with pytest.raises(SystemExit):
        lane.main(["--help"])
    text = " ".join(capsys.readouterr().out.split())
    option = text[
        text.index("Also write only the accepted rows") : text.index("--lines-csv LINES_CSV Also")
    ]
    for status in lane.ACCEPTED_STATUSES:
        assert status in option
        assert f"`{status}`" in lane.__doc__
    assert "two independent vendors agreed" not in option
    assert "still not a canonical load" in option


def test_malformed_inputs_are_refused(tmp_path):
    with pytest.raises(SystemExit, match="must contain a JSON object"):
        lane.main(
            [
                write(tmp_path / "c.json", ["not an object"]),
                "--manifest",
                write(tmp_path / "m.json", manifest()),
                "--out",
                str(tmp_path / "o.json"),
            ]
        )
    with pytest.raises(SystemExit, match="carries no document list"):
        lane.main(
            [
                write(tmp_path / "c2.json", {"documents": "not a list"}),
                "--manifest",
                write(tmp_path / "m2.json", manifest()),
                "--out",
                str(tmp_path / "o2.json"),
            ]
        )
    with pytest.raises(SystemExit, match="carries no fields to export"):
        lane.main(
            [
                write(tmp_path / "c3.json", {"documents": []}),
                "--manifest",
                write(tmp_path / "m3.json", manifest()),
                "--out",
                str(tmp_path / "o3.json"),
            ]
        )


def test_a_document_or_field_map_that_is_not_an_object_is_refused():
    with pytest.raises(ValueError, match="must be an object"):
        lane.rows_for(["not a document"], {}, {})
    with pytest.raises(ValueError, match="carries no field map"):
        lane.rows_for([{"document_id": "p1"}], {}, {})


def test_a_field_entry_that_is_not_an_object_is_skipped():
    rows = lane.rows_for(
        [{"document_id": "p1", "fields": {"a": "not an object", "b": field()}}], {}, {}
    )
    assert [row["field"] for row in rows] == ["b"]


def test_the_command_reports_what_it_wrote(tmp_path, capsys):
    lane.main(
        [
            write(tmp_path / "c.json", consensus()),
            "--manifest",
            write(tmp_path / "m.json", {"pages": [{"page_id": "z", "page_pdf": "z.pdf"}]}),
            "--out",
            str(tmp_path / "o.json"),
        ]
    )
    output = capsys.readouterr().out
    assert "Fields exported: 4 over 1 documents" in output
    assert "accepted total: 2" in output
    assert "blocking findings: NOT CONSULTED" in output
    assert "documents without intake provenance: 1" in output
    assert "canonical load permitted: no" in output


def test_quiet_prints_nothing(tmp_path, capsys):
    run(tmp_path, ["--quiet"])
    assert capsys.readouterr().out == ""


def test_a_contested_row_states_no_single_reading_but_carries_its_candidates(tmp_path):
    """Two engines read different values; there is no one reading to state."""
    result = run(tmp_path)
    row = next(r for r in result["rows"] if r["field"] == "header.dealer_name")
    assert row["status"] == "contested"
    assert row["reading"] == ""
    assert row["all_readings"] == "Acme; Acme Ltd"
    assert row["reading_engine_count"] == 2


def test_a_field_no_engine_returned_is_counted_as_such(tmp_path):
    """Rule 9: an unaccepted field and an unread one are different results."""
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "fields": {
                "header.total_amount": field(value=None, accepted=False, flag="single_engine"),
                "header.tax": field(value="1.00"),
            },
        }
    ]
    result = run(tmp_path, documents=documents)
    assert result["summary"]["fields_with_a_reading"] == 1
    assert result["summary"]["fields_no_engine_returned"] == 1
    empty = next(r for r in result["rows"] if r["field"] == "header.total_amount")
    assert empty["reading"] == "" and empty["all_readings"] == ""


def test_a_scalar_candidate_list_is_read_as_one_reading():
    """Not every lane writes candidates as a list; one value is one reading."""
    assert lane.readings({"candidate_values": "7", "value": None}) == ["7"]
    assert lane.readings({"candidate_values": None, "value": "9"}) == ["9"]
    assert lane.readings({"candidate_values": [None], "value": None}) == []


def test_an_approved_mapping_is_its_own_status_and_never_vendor_agreement():
    """A rule placing a value is not two vendors agreeing on which field it is."""
    entry = field("142437", acceptance={"accepted_by": "approved_source_label_mapping"})
    assert lane.field_status(entry) == "accepted_approved_mapping"
    assert lane.CONFIDENCE["accepted_approved_mapping"] == "corroborated"
    assert "accepted_approved_mapping" in lane.ACCEPTED_STATUSES


def test_the_pivot_returns_records_a_crm_import_can_load(tmp_path):
    """One row per field is honest and unloadable; this is the record grain."""
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "document_type": "commission_statement",
            "fields": {
                "header.dealer_name": field("Northgate Co"),
                "lines[0].commission_amount": field("10.00"),
                "lines[0].description": field(
                    value=None,
                    flag="no_consensus",
                    accepted=False,
                    candidate_values=["Chair", "Chairs"],
                ),
                "lines[10].commission_amount": field(
                    value=None,
                    flag="single_engine",
                    accepted=False,
                    candidate_values=["12.00"],
                ),
                "lines[2].commission_amount": field("11.00"),
                # Accepted, but derived rather than read, so it counts toward
                # none of the three evidence tallies.
                "lines[0].ordinal": field("1", flag="derived_ordinal"),
            },
        }
    ]
    run(
        tmp_path,
        ["--lines-csv", str(tmp_path / "lines.csv"), "--documents-csv", str(tmp_path / "docs.csv")],
        documents=documents,
    )
    lines = list(csv.DictReader((tmp_path / "lines.csv").open()))
    # Line 10 follows line 2. Ordering the index as text writes a statement into
    # the CRM as 0, 10, 2.
    assert [row["line_index"] for row in lines] == ["0", "2", "10"]
    first = lines[0]
    assert first["commission_amount"] == "10.00"
    # A contested cell states no value. It used to carry the joined candidate
    # list, which put `"1,750.97; 8,918.38"` in a commission_amount column on
    # the real corpus -- text where the loader expects money. The candidates are
    # not lost: the row names the field in `contested_fields` and the
    # field-grain export carries them in `all_readings`.
    assert first["description"] == ""
    assert first["contested_fields"] == "description"
    assert first["fields_contested"] == "1"
    assert first["contested_fields"] == "description"
    assert first["fields_vendor_agreed"] == "1"
    assert first["ordinal"] == "1"
    assert first["source_file"] == "source/book.pdf"
    last = lines[-1]
    assert last["commission_amount"] == "12.00"
    assert last["single_reading_fields"] == "commission_amount"
    documents_csv = list(csv.DictReader((tmp_path / "docs.csv").open()))
    assert len(documents_csv) == 1
    assert documents_csv[0]["dealer_name"] == "Northgate Co"
    # Identity first, evidence last: a truncated import must not lose either.
    assert list(documents_csv[0])[0] == "document_id"
    assert list(documents_csv[0])[-len(lane.CONTEXT_COLUMNS) - 1] == lane.EVIDENCE_COLUMNS[-1]


def test_a_pivot_with_no_records_is_refused_rather_than_written_empty(tmp_path):
    """An empty CSV reads as a corpus with no lines, which is a different claim."""
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "fields": {"document_type": field()},
        }
    ]
    with pytest.raises(SystemExit, match="no records to pivot"):
        run(tmp_path, ["--lines-csv", str(tmp_path / "l.csv")], documents=documents)


def test_line_keys_are_recognised_only_where_they_are_lines():
    assert lane.line_key("lines[7].commission_amount") == ("7", "commission_amount")
    assert lane.line_key("header.total") is None


def corroborated(value="99.00", flag="single_engine"):
    """An acceptance earned by an independent extractor, not by two vendors."""
    return field(
        value=value,
        flag=flag,
        accepted=True,
        candidate_values=[value],
        acceptance={
            "accepted_by": "corroborated_by_independent_extractor",
            "occurrences": 1,
        },
    )


def test_corroboration_is_not_reported_as_vendor_agreement(tmp_path):
    """The one thing the corroboration artifact says of itself that it is not.

    `apply_corroboration.py` accepts a value one model read where an independent
    non-LLM extractor read the same value on that page -- 47,578 fields on the
    commission run under a named client decision. Reading only `accepted` labels
    every one of them as two model vendors agreeing. It defeats a misread, not a
    misattribution, and the distinction decides what a row may be used for.
    """
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "fields": {
                "header.total_amount": field("12.00"),
                "header.freight": corroborated(),
                "lines[0].amount": corroborated("5.00", flag="no_consensus"),
            },
        }
    ]
    result = run(tmp_path, documents=documents)
    assert result["summary"]["fields_by_status"] == {
        "accepted_vendor_agreement": 1,
        "accepted_independent_corroboration": 2,
    }
    # Accepted, and counted as accepted -- but never as the other thing.
    assert result["summary"]["accepted_fields"] == 3
    by_field = {row["field"]: row for row in result["rows"]}
    assert by_field["header.freight"]["accepted_by"] == "corroborated_by_independent_extractor"
    assert by_field["header.total_amount"]["accepted_by"] == ""
    # A no_consensus field accepted on corroboration is not contested any more,
    # and is not vendor agreement either.
    assert by_field["lines[0].amount"]["status"] == "accepted_independent_corroboration"


def test_a_pivoted_row_counts_corroboration_in_its_own_column(tmp_path):
    """A row that merged the two would let a page-presence match read as agreement."""
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "fields": {
                "lines[0].commission_amount": field("10.00"),
                "lines[0].job_number": corroborated("J-1"),
            },
        }
    ]
    run(tmp_path, ["--lines-csv", str(tmp_path / "lines.csv")], documents=documents)
    row = list(csv.DictReader((tmp_path / "lines.csv").open()))[0]
    assert row["fields_vendor_agreed"] == "1"
    assert row["fields_corroborated"] == "1"
    assert row["corroborated_fields"] == "job_number"


def test_an_acceptance_block_that_is_not_an_object_is_not_read_as_corroboration():
    assert lane.field_status(field(accepted=True, acceptance="corroborated")) == (
        "accepted_vendor_agreement"
    )


def test_a_page_review_amendment_is_counted_under_its_own_name():
    """Registered, so it is counted; named, so it never reads as vendor agreement."""
    amended = field(accepted=True, acceptance={"accepted_by": "amended_from_page_review"})
    status = lane.field_status(amended)
    assert status == "accepted_page_review_amendment"
    assert lane.CONFIDENCE[status] == "corroborated"
    assert status in lane.ACCEPTED_STATUSES


def entities():
    """A party master of the shape `entity_resolve.py` writes."""
    return {
        "parties": [
            {
                "party_key": "PTY-000004",
                "canonical_name": "Northgate Co.",
                # A blank variant is skipped rather than indexed: an empty key
                # would match every field this export could not read.
                "name_variants": ["NORTHGATE", "", None, "NORTHGATE CO LLC", "Northgate Co."],
            },
            "not a party",
        ]
    }


def test_a_resolved_party_reaches_the_row_beside_the_reading(tmp_path):
    """`entity_resolve.py` writes its master beside the records, not into them.

    So an export reading the records alone still shows `NORTHGATE`,
    `NORTHGATE CO LLC` and `Northgate Co.` as three suppliers, and anyone grouping
    the CSV by brand gets the unresolved answer -- the thing party resolution
    exists to prevent. On the commission run the document rows carried 69 raw
    brand strings resolving to 12 parties.
    """
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "fields": {
                "header.brand_name": field("NORTHGATE CO LLC"),
                "header.total_amount": field("10.00"),
                "lines[0].description": field("NORTHGATE"),
            },
        }
    ]
    result = run(
        tmp_path,
        ["--parties", write(tmp_path / "e.json", entities())],
        documents=documents,
    )
    by = {row["field"]: row for row in result["rows"]}
    assert by["header.brand_name"]["resolved_party"] == "Northgate Co."
    assert by["header.brand_name"]["party_key"] == "PTY-000004"
    # The reading is not overwritten: what the page said is still on the row.
    assert by["header.brand_name"]["reading"] == "NORTHGATE CO LLC"
    # Only party fields resolve. A description that happens to match a variant
    # is not a supplier, and relabelling it would invent an attribution.
    assert by["lines[0].description"]["resolved_party"] == ""
    assert by["header.total_amount"]["resolved_party"] == ""
    assert result["summary"]["party_master_consulted"] is True
    assert result["summary"]["rows_with_a_resolved_party"] == 1
    assert result["summary"]["distinct_resolved_parties"] == 1


def test_without_a_party_master_the_column_is_reported_unconsulted(tmp_path):
    """An empty column and a corpus whose names resolve to nothing look alike."""
    result = run(tmp_path)
    assert result["summary"]["party_master_consulted"] is False
    assert result["summary"]["rows_with_a_resolved_party"] == 0
    assert all(row["resolved_party"] == "" for row in result["rows"])


def test_a_pivoted_party_column_carries_both_the_reading_and_the_party(tmp_path):
    """Grouping a CRM import by the raw column is the mistake being prevented."""
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "fields": {
                "lines[0].dealer_name": field("NORTHGATE"),
                "lines[0].commission_amount": field("10.00"),
            },
        }
    ]
    run(
        tmp_path,
        [
            "--parties",
            write(tmp_path / "e.json", entities()),
            "--lines-csv",
            str(tmp_path / "lines.csv"),
        ],
        documents=documents,
    )
    row = list(csv.DictReader((tmp_path / "lines.csv").open()))[0]
    assert row["dealer_name"] == "NORTHGATE"
    assert row["dealer_name__resolved_party"] == "Northgate Co."
    assert "commission_amount__resolved_party" not in row
    # The master's key beside the name, because a name is not an identity.
    master = json.loads((tmp_path / "e.json").read_text())
    key = next(p["party_key"] for p in master["parties"] if p["canonical_name"] == "Northgate Co.")
    assert row["dealer_name__party_key"] == key
    assert "commission_amount__party_key" not in row


def accepted_by(lane_name, value="7.00", flag="no_consensus"):
    """An acceptance earned by a named lane other than vendor agreement."""
    return field(
        value=value,
        flag=flag,
        accepted=True,
        candidate_values=[value],
        acceptance={"accepted_by": lane_name, "occurrences": 1},
    )


def test_each_lane_is_named_by_the_evidence_it_actually_earned(tmp_path):
    """The three lanes added after `field_status` all reported as vendor agreement.

    `field_status` tested for one `accepted_by` and returned
    `accepted_vendor_agreement` for every other acceptance. Document arithmetic,
    same-document settlement and the extractor tiebreak were all added after it,
    so each one's acceptances were reported as two independent model vendors
    reading the same value. On the commission run that overstated 3,566 fields
    -- 38,446 claimed against 34,880 real -- in the only direction that matters,
    toward more evidence than exists.
    """
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "fields": {
                "header.total_amount": field("12.00"),
                "header.freight": corroborated(),
                "header.commission_amount": accepted_by("reconciled_by_document_arithmetic"),
                "header.invoice_number": accepted_by(
                    "matches_a_settled_value_in_the_same_document"
                ),
                "header.customer_name": accepted_by("tie_broken_by_independent_extractor"),
                "header.payer_name": accepted_by("agreed_by_independent_vendors"),
            },
        }
    ]
    result = run(tmp_path, documents=documents)
    assert result["summary"]["fields_by_status"] == {
        "accepted_vendor_agreement": 2,
        "accepted_independent_corroboration": 1,
        "accepted_document_arithmetic": 1,
        "accepted_same_document": 1,
        "accepted_extractor_tiebreak": 1,
    }
    # Every one of them is accepted; none of them is the same claim.
    assert result["summary"]["accepted_fields"] == 6


def test_a_row_says_how_far_it_may_be_trusted_in_one_word(tmp_path):
    """`confidence` is what a CRM cell shows; `status` is what an auditor reads."""
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "fields": {
                "header.total_amount": field("12.00"),
                "header.freight": corroborated(),
                "header.commission_amount": accepted_by("reconciled_by_document_arithmetic"),
                "lines[0].ordinal": field(value="1", flag="derived_ordinal"),
                "header.dealer_name": field(
                    value=None, flag="no_consensus", accepted=False, candidate_values=["a", "b"]
                ),
            },
        }
    ]
    result = run(tmp_path, documents=documents)
    by_field = {row["field"]: row["confidence"] for row in result["rows"]}
    assert by_field["header.total_amount"] == "confirmed"
    assert by_field["header.freight"] == "corroborated"
    assert by_field["header.commission_amount"] == "computed"
    assert by_field["lines[0].ordinal"] == "derived"
    assert by_field["header.dealer_name"] == "unconfirmed"
    assert result["summary"]["fields_by_confidence"] == {
        "confirmed": 1,
        "corroborated": 1,
        "computed": 1,
        "derived": 1,
        "unconfirmed": 1,
    }


def test_an_acceptance_from_an_unregistered_lane_is_named_not_promoted(tmp_path):
    """Rule 2: never silently normalize a record into a passing status.

    A lane added without registering its `accepted_by` used to inherit the
    strongest status in the file. Naming it costs a reader nothing and makes the
    omission fixable; promoting it makes the omission invisible and wrong.
    """
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "fields": {"header.total_amount": accepted_by("settled_by_a_lane_nobody_registered")},
        }
    ]
    result = run(tmp_path, documents=documents)
    row = result["rows"][0]
    assert row["status"] == "accepted_by_an_unregistered_lane"
    assert row["confidence"] == "unconfirmed"
    # Accepted by the pipeline, but this command cannot say on what evidence, so
    # it is not counted into a total that would assert exactly that.
    assert result["summary"]["accepted_fields"] == 0
    assert result["summary"]["fields_accepted_by_an_unregistered_lane"] == 1
    assert result["summary"]["unregistered_acceptance_lanes"] == [
        "settled_by_a_lane_nobody_registered"
    ]


def test_a_pivoted_row_is_only_as_confirmed_as_its_weakest_cell(tmp_path):
    """Nine corroborated cells and one contested cell is not a confirmed row."""
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "fields": {
                "lines[0].commission_amount": field("10.00"),
                "lines[0].job_number": corroborated("J-1"),
                "lines[1].commission_amount": field("11.00"),
                "lines[1].description": field(
                    value=None, flag="single_engine", accepted=False, candidate_values=["widget"]
                ),
            },
        }
    ]
    run(tmp_path, ["--lines-csv", str(tmp_path / "lines.csv")], documents=documents)
    rows = list(csv.DictReader((tmp_path / "lines.csv").open()))
    assert rows[0]["row_confidence"] == "confirmed"
    assert rows[0]["unconfirmed_fields"] == ""
    assert rows[1]["row_confidence"] == "unconfirmed"
    assert rows[1]["unconfirmed_fields"] == "description"


def test_the_evidence_counters_account_for_every_cell_in_the_row(tmp_path):
    """A counter set that does not add up hides whichever cells it dropped.

    The shipped export counted five statuses and fell through for the rest, so
    2,885 accepted cells on the commission run appeared in no column at all. A
    client adding the columns up got a number smaller than the row, with nothing
    naming the difference.
    """
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "fields": {
                "lines[0].commission_amount": field("10.00"),
                "lines[0].job_number": corroborated("J-1"),
                "lines[0].rate": accepted_by("reconciled_by_document_arithmetic"),
                "lines[0].invoice_number": accepted_by(
                    "matches_a_settled_value_in_the_same_document"
                ),
                "lines[0].customer_name": accepted_by("tie_broken_by_independent_extractor"),
                "lines[0].ordinal": field(value="2", flag="derived_ordinal"),
                "lines[0].description": field(
                    value=None, flag="single_engine", accepted=False, candidate_values=["widget"]
                ),
                "lines[0].dealer_name": field(
                    value=None, flag="no_consensus", accepted=False, candidate_values=["a", "b"]
                ),
            },
        }
    ]
    run(tmp_path, ["--lines-csv", str(tmp_path / "lines.csv")], documents=documents)
    row = list(csv.DictReader((tmp_path / "lines.csv").open()))[0]
    counters = (
        "fields_vendor_agreed",
        "fields_corroborated",
        "fields_computed",
        "fields_derived",
        "fields_single_reading",
        "fields_contested",
    )
    assert sum(int(row[name]) for name in counters) == 8
    assert row["fields_corroborated"] == "3"
    assert row["fields_computed"] == "1"
    assert row["fields_derived"] == "1"
    assert sorted(row["corroborated_fields"].split("; ")) == [
        "customer_name",
        "invoice_number",
        "job_number",
    ]


def test_a_cell_no_column_describes_still_lands_in_one(tmp_path):
    """The counters equal the cells in the row unconditionally, not usually.

    A withheld flag and an acceptance from an unregistered lane are both cells
    the export cannot vouch for. Neither may leave the row reading `confirmed`,
    and neither may go uncounted -- a counter set that silently drops a category
    is the same failure as a status that silently promotes one.
    """
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "fields": {
                "lines[0].commission_amount": field("10.00"),
                "lines[0].job_number": accepted_by("settled_by_a_lane_nobody_registered"),
                "lines[0].item_code": field(
                    value=None, flag="provider_error", accepted=False, candidate_values=["X-1"]
                ),
            },
        }
    ]
    run(tmp_path, ["--lines-csv", str(tmp_path / "lines.csv")], documents=documents)
    row = list(csv.DictReader((tmp_path / "lines.csv").open()))[0]
    assert row["fields_unattributed"] == "2"
    assert (
        sum(
            int(row[name])
            for name in (
                "fields_vendor_agreed",
                "fields_corroborated",
                "fields_computed",
                "fields_derived",
                "fields_single_reading",
                "fields_contested",
                "fields_unattributed",
            )
        )
        == 3
    )
    assert row["row_confidence"] == "unconfirmed"
    assert row["unconfirmed_fields"] == "item_code; job_number"


def test_the_pivot_carries_the_value_consensus_accepted(tmp_path):
    """An accepted value with two candidate readings must reach the cell.

    `reading` is empty whenever a field has more than one candidate, even where
    consensus settled which one it accepted. The pivot fell back to the joined
    candidate list in that case, so the cell said `"1,750.97; 8,918.38"` where
    consensus had accepted `8,918.38`. On the commission run that hit 8,004
    cells -- 72.4% of every joined cell in the export -- each one discarding a
    value the pipeline had already decided.
    """
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "document_type": "commission_statement",
            "fields": {
                "lines[0].commission_amount": field(
                    "8918.38", candidate_values=["1750.97", "8918.38"]
                ),
                "header.dealer_name": field(
                    "Northgate Co", candidate_values=["Northgate Co", "ESPACE DE JOUR"]
                ),
            },
        }
    ]
    run(
        tmp_path,
        ["--lines-csv", str(tmp_path / "lines.csv"), "--documents-csv", str(tmp_path / "docs.csv")],
        documents=documents,
    )
    line = list(csv.DictReader((tmp_path / "lines.csv").open()))[0]
    assert line["commission_amount"] == "8918.38"
    assert "; " not in line["commission_amount"]
    assert line["fields_contested"] == "0"
    document = list(csv.DictReader((tmp_path / "docs.csv").open()))[0]
    assert document["dealer_name"] == "Northgate Co"


def test_a_line_repeating_an_earlier_line_says_which_one(tmp_path, capsys):
    """An engine padded eight real lines out to a hundred by repeating one.

    The repeats differ only in the ordinal, which is assigned rather than read,
    so comparing whole rows found nothing and a CRM would have loaded the
    document's commission twelve times over.
    """
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "document_type": "commission_statement",
            "fields": {
                "lines[0].commission_amount": field("183.38"),
                "lines[1].commission_amount": field("183.38"),
                "lines[2].commission_amount": field("183.38"),
                "lines[3].commission_amount": field("92.48"),
            },
        }
    ]
    run(
        tmp_path,
        ["--lines-csv", str(tmp_path / "lines.csv")],
        documents=documents,
    )
    rows = list(csv.DictReader((tmp_path / "lines.csv").open()))
    assert [row["duplicate_of_line"] for row in rows] == ["", "0", "0", ""]
    # Nothing is removed: the repeats keep their place and their evidence.
    assert [row["commission_amount"] for row in rows] == ["183.38", "183.38", "183.38", "92.48"]
    assert "line rows repeating an earlier line: 2 of 4" in capsys.readouterr().out


def test_the_document_grain_has_no_duplicate_column_to_fill(tmp_path):
    """One row per document cannot repeat a line, so the column stays empty."""
    run(tmp_path, ["--documents-csv", str(tmp_path / "docs.csv")])
    rows = list(csv.DictReader((tmp_path / "docs.csv").open()))
    assert all(row["duplicate_of_line"] == "" for row in rows)


def test_money_with_nothing_to_attribute_it_to_is_named(tmp_path, capsys):
    """A row can carry a commission and say nothing about what earned it.

    On the commission run 1,369 line rows -- 31.1% of all the commission money
    in the export -- state an amount with no description, item code, style or
    product name. On the page checked by hand they were the report's own
    "P.O. Total" rows, which a CRM would load as extra commission lines and
    count the money twice. Only 63 of them prove arithmetically as the sum of
    the lines above, so the flag names what is observed -- money with no item
    identity -- rather than asserting a subtotal it cannot demonstrate.
    """
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "document_type": "commission_statement",
            "fields": {
                "lines[0].description": field("MEDINAH"),
                "lines[0].commission_amount": field("2007.14"),
                "lines[1].commission_amount": field("8731.50"),
            },
        }
    ]
    run(tmp_path, ["--lines-csv", str(tmp_path / "lines.csv")], documents=documents)
    rows = list(csv.DictReader((tmp_path / "lines.csv").open()))
    assert [row["amount_without_line_identity"] for row in rows] == ["", "true"]
    assert "line rows stating money but naming no item: 1 of 2" in capsys.readouterr().out


def test_a_job_number_names_what_the_money_is_for(tmp_path, capsys):
    """A commission statement prints no product column and is not anonymous.

    Eight correct lines on one statement page were called `money with no item
    named` because the page heads its rows `Job` and `Project` rather than
    `Description`. Honouring the flag dropped the page's whole $39,300.
    """
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "document_type": "commission_statement",
            "fields": {
                "lines[0].job_number": field("13122"),
                "lines[0].commission_amount": field("2252.50"),
                "lines[1].project_name": field("Meridiana Corporation Center"),
                "lines[1].commission_amount": field("1562.41"),
                "lines[2].commission_amount": field("162132.29"),
            },
        }
    ]
    run(tmp_path, ["--lines-csv", str(tmp_path / "lines.csv")], documents=documents)
    rows = list(csv.DictReader((tmp_path / "lines.csv").open()))
    assert [row["amount_without_line_identity"] for row in rows] == ["", "", "true"]


def _recurring(job, amount, extra, documents=("p1", "p2", "p3")):
    """The same job and commission on several statements, one per document."""
    out = []
    for name in documents:
        fields = {
            "lines[0].job_number": field(job),
            "lines[0].commission_amount": field(amount),
        }
        for column, value in extra.get(name, {}).items():
            fields[f"lines[0].{column}"] = field(value)
        out.append(
            {
                "document_id": name,
                "review_status": "open_exception",
                "document_type": "commission_statement",
                "fields": fields,
            }
        )
    return out


def test_the_odd_reading_of_a_recurring_line_is_named(tmp_path):
    """These statements repeat a line until it is paid, which is free evidence.

    Nothing here changes a value. A project name no arithmetic can test is
    printed the same way every month, so a reading that differs from the rest
    is worth a reviewer's eye.
    """
    documents = _recurring(
        "13166",
        "13559.45",
        {
            "p1": {"description": "Meridiana Suite 160"},
            "p2": {"description": "Meridiana Suite 160"},
            "p3": {"description": "Meridiana Suite 610"},
        },
    )
    run(tmp_path, ["--lines-csv", str(tmp_path / "lines.csv")], documents=documents)
    rows = list(csv.DictReader((tmp_path / "lines.csv").open()))
    assert [row["differs_from_the_same_line_elsewhere"] for row in rows] == [
        "",
        "",
        "description",
    ]


def test_a_reading_lost_on_one_statement_is_named_separately(tmp_path):
    """An empty cell where the others carry a value is a loss, not a reading.

    A `PROJECT CANCELLED` stamp printed on every statement survived on most of
    them and left no trace on the rest, where a cancelled project reads as a
    live one. That failure hides, so it is named in its own column.
    """
    documents = _recurring(
        "12951",
        "28950.92",
        {
            "p1": {"description": "Hopfield Brewers PROJECT CANCELLED"},
            "p2": {"description": "Hopfield Brewers PROJECT CANCELLED"},
            "p3": {},
        },
    )
    run(tmp_path, ["--lines-csv", str(tmp_path / "lines.csv")], documents=documents)
    rows = list(csv.DictReader((tmp_path / "lines.csv").open()))
    assert [row["missing_where_the_same_line_carries_it"] for row in rows] == [
        "",
        "",
        "description",
    ]
    assert [row["differs_from_the_same_line_elsewhere"] for row in rows] == ["", "", ""]


def test_a_zero_commission_is_not_a_line_key(tmp_path):
    """Hundreds of unrelated lines state 0.00 and are not the same line.

    Keyed on the job and a zero, they grouped together and every one of them
    was reported as disagreeing with the others.
    """
    documents = _recurring(
        "13096",
        "0.00",
        {
            "p1": {"description": "Coastline Bank Miami"},
            "p2": {"description": "Cumming Group 545 Wyn"},
            "p3": {"description": "Green Security Clearwater"},
        },
    )
    run(tmp_path, ["--lines-csv", str(tmp_path / "lines.csv")], documents=documents)
    rows = list(csv.DictReader((tmp_path / "lines.csv").open()))
    assert [row["differs_from_the_same_line_elsewhere"] for row in rows] == ["", "", ""]


def test_two_documents_leave_no_majority_to_name(tmp_path):
    """With one reading each way, naming an odd one out is a coin toss."""
    documents = _recurring(
        "13166",
        "13559.45",
        {"p1": {"description": "Meridiana Suite 160"}, "p2": {"description": "Meridiana 160"}},
        documents=("p1", "p2"),
    )
    run(tmp_path, ["--lines-csv", str(tmp_path / "lines.csv")], documents=documents)
    rows = list(csv.DictReader((tmp_path / "lines.csv").open()))
    assert [row["differs_from_the_same_line_elsewhere"] for row in rows] == ["", ""]


def test_a_second_pass_restating_a_job_is_named(tmp_path, capsys):
    """The same job and amount twice is a repeat; the same job twice is not.

    A page read in two passes emits each line again with different columns
    filled, so no two rows are equal and `duplicate_of_line` clears them all.
    """
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "document_type": "commission_statement",
            "fields": {
                "lines[0].job_number": field("13166"),
                "lines[0].customer_name": field("Berman Pollard Grant"),
                "lines[0].commission_amount": field("13559.45"),
                "lines[1].job_number": field("13166"),
                "lines[1].dealer_name": field("Berman Pollard Grant Advisors"),
                "lines[1].commission_amount": field("13559.45"),
                "lines[2].job_number": field("13166"),
                "lines[2].commission_amount": field("214.00"),
            },
        }
    ]
    run(tmp_path, ["--lines-csv", str(tmp_path / "lines.csv")], documents=documents)
    rows = list(csv.DictReader((tmp_path / "lines.csv").open()))
    assert [row["repeats_a_job_and_amount_above"] for row in rows] == ["", "0", ""]
    assert [row["duplicate_of_line"] for row in rows] == ["", "", ""]
    assert (
        "line rows restating a job and amount already above them: 1 of 3" in capsys.readouterr().out
    )


@pytest.mark.parametrize(
    ("raw", "amount", "currency"),
    [
        ("$6,226.00", "6226.00", ""),
        ("856.13 USD", "856.13", "USD"),
        ("(662.89)", "-662.89", ""),
        ("1,778,056.06", "1778056.06", ""),
        ("-2204.80", "-2204.80", ""),
        # A decimal comma the document has not settled is refused, not guessed.
        ("2 199,03", "", ""),
        ("n/a", "", ""),
        ("", "", ""),
    ],
)
def test_the_loadable_amount_refuses_what_it_cannot_decide(raw, amount, currency):
    """A money column an importer can read, without changing what the page said."""
    assert lane.loadable_amount(raw) == (amount, currency)


@pytest.mark.parametrize(
    ("raw", "convention", "amount"),
    [
        ("2 199,03", "decimal_comma", "2199.03"),
        ("14 460,05", "decimal_comma", "14460.05"),
        ("180 105,23", "decimal_comma", "180105.23"),
        ("219,90", "decimal_comma", "219.90"),
        ("0,00", "decimal_comma", "0.00"),
        ("(1.446,01)", "decimal_comma", "-1446.01"),
        # On a document of decimal commas, three digits after one could be either.
        ("1,446", "decimal_comma", ""),
        ("1446", "decimal_comma", "1446"),
        ("12 5", "decimal_comma", ""),
        # Among decimal points `12,34` is a cut cell, not a French amount.
        ("12,34", "", ""),
        ("1,446", "", "1446"),
    ],
)
def test_a_decimal_comma_is_read_only_where_the_document_settles_it(raw, convention, amount):
    """murbrook's Canadian statements print `2 199,03` and no decimal point at all."""
    assert lane.loadable_amount(raw, convention)[0] == amount


def test_a_document_settles_its_decimal_separator_from_its_own_amounts():
    """Decimal commas and no decimal point settle it; both, or neither, settle nothing."""
    assert lane.separator_convention(["2 199,03", "219,90 CAD", "0"]) == "decimal_comma"
    assert lane.separator_convention(["2 199,03", "$1,250.00"]) == ""
    assert lane.separator_convention(["1,446", "12"]) == ""
    rows = [
        {"document_id": "fr", "field": "lines[0].commission_amount", "value": "1 446,01"},
        {
            "document_id": "fr",
            "field": "lines[1].commission_amount",
            "value": "",
            "reading": "219,90",
        },
        {"document_id": "fr", "field": "lines[2].commission_amount", "value": "", "reading": ""},
        {"document_id": "fr", "field": "lines[0].description", "value": "12.50 units"},
        {"document_id": "us", "field": "header.statement_total", "value": "$6,226.00"},
    ]
    assert lane.separator_convention_by_document(rows) == {"fr": "decimal_comma", "us": ""}


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"stated_commission_rate": "10%", "commission_arithmetic": ""}, "10"),
        ({"stated_commission_rate": "-9 %", "commission_arithmetic": "does_not_reconcile"}, "-9"),
        ({"stated_commission_rate": "10.00", "commission_arithmetic": "reconciles"}, "10.00"),
        ({"stated_commission_rate": "1.00", "commission_arithmetic": "does_not_reconcile"}, ""),
        ({"stated_commission_rate": "0.10", "commission_arithmetic": ""}, ""),
        ({"stated_commission_rate": "n/a", "commission_arithmetic": "reconciles"}, ""),
        ({"tax_rate": "7.00", "commission_arithmetic": "reconciles"}, ""),
        ({"tax_rate": "7%"}, "7"),
    ],
)
def test_a_rate_is_typed_as_a_percent_only_when_its_unit_is_proven(record, expected):
    """A `%` proves the unit, and so does a line whose own base x rate reconciles."""
    column = next(name for name in record if name.endswith("_rate"))
    lane.add_percent_columns([record])
    assert record[f"{column}__percent"] == expected


@pytest.mark.parametrize(
    ("raw", "iso"),
    [
        ("2024-05-31", "2024-05-31"),
        ("May 31, 2024", "2024-05-31"),
        ("Sept 3, 2023", "2023-09-03"),
        # Order is not stated, so it is not inferred: May 6th or June 5th.
        ("05/06/2024", ""),
        # Truncated on the page itself.
        ("01/08/20...", ""),
        ("December-24", ""),
        ("2024-02-31", ""),
        # A month name no calendar has.
        ("Smarch 3, 2024", ""),
        # A named month with a day it does not have.
        ("February 31, 2024", ""),
        # A year no statement was written in: a digit lost or gained in reading.
        ("0122-05-24", ""),
        ("May 3, 0122", ""),
        ("", ""),
        # A weekday before and a time after, as an email prints them.
        ("Fri, Nov 4, 2022, 9:26 AM", "2022-11-04"),
        ("Apr 13, 2023 at 10:14:20 AM ET", "2023-04-13"),
        # The year-first order with its second hyphen lost.
        ("2023-0430", "2023-04-30"),
        # 2026 is no leap year.
        ("2026-02-29", ""),
        # A time of day settles no order.
        ("7/12/2023 2:44 PM", ""),
    ],
)
def test_the_loadable_date_refuses_an_order_it_cannot_know(raw, iso):
    assert lane.loadable_date(raw) == iso


def test_the_crm_grain_carries_loadable_companions(tmp_path):
    """The page's own text stays; a typed column appears beside it."""
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "document_type": "commission_statement",
            "fields": {
                "lines[0].description": field("MEDINAH"),
                "lines[0].commission_amount": field("$1,019.00 USD"),
                "lines[0].transaction_date": field("May 31, 2024"),
            },
        }
    ]
    run(tmp_path, ["--lines-csv", str(tmp_path / "lines.csv")], documents=documents)
    row = list(csv.DictReader((tmp_path / "lines.csv").open()))[0]
    assert row["commission_amount"] == "$1,019.00 USD"
    assert row["commission_amount__amount"] == "1019.00"
    assert row["commission_amount__currency"] == "USD"
    assert row["transaction_date"] == "May 31, 2024"
    assert row["transaction_date__iso"] == "2024-05-31"


def test_a_line_repeating_another_document_names_that_document(tmp_path):
    """These statements are periodic; a project recurs until it is paid."""
    line = {
        "lines[0].description": field("Town Square Suite 820"),
        "lines[0].commission_amount": field("1111.92"),
    }
    documents = [
        {"document_id": "p1", "review_status": "x", "document_type": "c", "fields": dict(line)},
        {"document_id": "p2", "review_status": "x", "document_type": "c", "fields": dict(line)},
    ]
    run(tmp_path, ["--lines-csv", str(tmp_path / "lines.csv")], documents=documents)
    rows = list(csv.DictReader((tmp_path / "lines.csv").open()))
    assert [row["repeats_a_line_in"] for row in rows] == ["", "p1"]


@pytest.mark.parametrize(
    ("values", "order"),
    [
        # 16 is no month, so this document has stated its order.
        (["11/16/2022", "05/06/2024"], "month_first"),
        (["16/11/2022", "05/06/2024"], "day_first"),
        # Nothing above 12 anywhere: the document has settled nothing.
        (["05/06/2024", "01/02/2023"], None),
        # Both orders present. The document contradicts itself; refuse.
        (["11/16/2022", "16/11/2022"], None),
        ([], None),
        (["May 31, 2024"], None),
        # A time printed after the date does not hide the order it states.
        (["9/23/22, 8:50 AM", "05/06/2024"], "month_first"),
    ],
)
def test_a_document_settles_its_own_date_order(values, order):
    """`05/06/2024` is May 6th or June 5th; a sibling date can prove which."""
    assert lane.document_date_order(values) == order


def test_a_settled_order_makes_the_ambiguous_dates_loadable(tmp_path):
    """One unambiguous date releases the rest of the document's dates."""
    documents = [
        {
            "document_id": "p1",
            "review_status": "x",
            "document_type": "c",
            "fields": {
                "lines[0].description": field("MEDINAH"),
                "lines[0].transaction_date": field("11/16/2022"),
                "lines[1].description": field("CONTOLLO"),
                "lines[1].transaction_date": field("05/06/2024"),
            },
        },
        {
            "document_id": "p2",
            "review_status": "x",
            "document_type": "c",
            "fields": {
                "lines[0].description": field("ANZA"),
                "lines[0].transaction_date": field("05/06/2024"),
            },
        },
    ]
    run(tmp_path, ["--lines-csv", str(tmp_path / "lines.csv")], documents=documents)
    rows = list(csv.DictReader((tmp_path / "lines.csv").open()))
    settled = {
        row["document_id"]: row
        for row in rows
        if row["line_index"] != "0" or row["document_id"] == "p2"
    }
    assert [r["transaction_date__iso"] for r in rows if r["document_id"] == "p1"] == [
        "2022-11-16",
        "2024-05-06",
    ]
    # p2 never proved an order, so its date stays unparsed rather than guessed.
    assert settled["p2"]["transaction_date__iso"] == ""


@pytest.mark.parametrize(
    ("raw", "order", "iso"),
    [
        ("11/16/2022", "month_first", "2022-11-16"),
        ("16/11/2022", "day_first", "2022-11-16"),
        # Two-digit years are this century on these statements.
        ("7/15/22", "month_first", "2022-07-15"),
        # A three- or four-digit year below 1000 is a misreading, not a year.
        ("05/24/0122", "month_first", ""),
        ("03/17/202", "month_first", ""),
        # A settled order does not make an impossible date possible.
        ("02/31/2024", "month_first", ""),
        # No order settled: still refused.
        ("05/06/2024", None, ""),
        ("9/23/22, 8:50 AM", "month_first", "2022-09-23"),
    ],
)
def test_a_slash_date_needs_the_order_its_document_settled(raw, order, iso):
    assert lane.loadable_date(raw, order) == iso


@pytest.mark.parametrize(
    ("raw", "month"),
    [
        ("December 2025", "2025-12"),
        ("Dec-2024", "2024-12"),
        ("Sept. 2023", "2023-09"),
        # A two-digit year is as much a day: the 24th, or 2024.
        ("December-24", ""),
        # A day is named, so this is a date, not a month.
        ("May 31, 2024", ""),
        ("Smarch 2024", ""),
        ("Dec 0122", ""),
        ("MONTH ENDED :", ""),
        ("", ""),
    ],
)
def test_a_month_named_without_a_day_types_as_a_month(raw, month):
    assert lane.loadable_month(raw) == month


def test_the_crm_grain_carries_a_month_beside_a_month_label(tmp_path):
    """`December 2025` has no ISO date; its month loads beside the reading."""
    documents = [
        {
            "document_id": "p1",
            "review_status": "x",
            "document_type": "c",
            "fields": {
                "lines[0].description": field("MEDINAH"),
                "lines[0].transaction_date": field("December 2025"),
            },
        }
    ]
    run(tmp_path, ["--lines-csv", str(tmp_path / "lines.csv")], documents=documents)
    row = list(csv.DictReader((tmp_path / "lines.csv").open()))[0]
    assert row["transaction_date"] == "December 2025"
    assert row["transaction_date__iso"] == ""
    assert row["transaction_date__month"] == "2025-12"


@pytest.mark.parametrize(
    ("raw", "month"),
    [
        ("December-24", "2024-12"),
        ("Sep-24", "2024-09"),
        # Only Excel's hyphen: `May 31` is a day with no year.
        ("May 31", ""),
        # A statement is not written in a year still to come.
        ("Dec-99", ""),
        ("Smarch-24", ""),
        # A four-digit year reads as it always did.
        ("Dec-2024", "2024-12"),
    ],
)
def test_an_authorized_two_digit_year_types_as_a_month_of_this_century(raw, month):
    assert lane.loadable_month(raw, two_digit_years=True) == month


def test_the_operator_types_two_digit_year_months_by_name(tmp_path):
    """Only the named authorization settles whether `December-24` is a year."""
    documents = [
        {
            "document_id": "p1",
            "review_status": "x",
            "document_type": "c",
            "fields": {
                "header.period_end": field("December-24"),
                "lines[0].description": field("MEDINAH"),
                "lines[0].transaction_date": field("May 31"),
            },
        }
    ]
    grains = [
        "--documents-csv",
        str(tmp_path / "documents.csv"),
        "--lines-csv",
        str(tmp_path / "lines.csv"),
        "--two-digit-year-months",
        "run-authorization-amendment-55",
    ]
    out = run(tmp_path, grains, documents=documents)
    document = list(csv.DictReader((tmp_path / "documents.csv").open()))[0]
    assert document["period_end"] == "December-24"
    assert document["period_end__month"] == "2024-12"
    line = list(csv.DictReader((tmp_path / "lines.csv").open()))[0]
    assert line["transaction_date__month"] == ""
    assert out["summary"]["two_digit_year_months_authorization"] == (
        "run-authorization-amendment-55"
    )
    assert out["summary"]["document_months_from_two_digit_years"] == 1
    assert out["summary"]["line_months_from_two_digit_years"] == 0


def test_a_two_digit_year_authorization_needs_a_name(tmp_path):
    with pytest.raises(SystemExit, match="authorization's name"):
        run(tmp_path, ["--two-digit-year-months", " "])


def test_a_contested_date_states_nothing_toward_the_order(tmp_path):
    """A date field with no settled value cannot help settle the convention."""
    documents = [
        {
            "document_id": "p1",
            "review_status": "x",
            "document_type": "c",
            "fields": {
                "lines[0].description": field("MEDINAH"),
                "lines[0].transaction_date": field(
                    value=None,
                    flag="no_consensus",
                    accepted=False,
                    candidate_values=["11/16/2022", "16/11/2022"],
                ),
                "lines[1].description": field("CONTOLLO"),
                "lines[1].transaction_date": field("05/06/2024"),
            },
        }
    ]
    run(tmp_path, ["--lines-csv", str(tmp_path / "lines.csv")], documents=documents)
    rows = list(csv.DictReader((tmp_path / "lines.csv").open()))
    assert rows[0]["transaction_date"] == ""
    assert rows[0]["transaction_date__iso"] == ""
    # The only date that stated a value is itself ambiguous, so nothing settled.
    assert rows[1]["transaction_date__iso"] == ""


def test_a_reading_the_source_cut_off_is_flagged_not_rewritten():
    """Part of this corpus is a wide sheet printed narrow and cut mid-word.

    `NORTHGATE C` appears 219 times where the party is `NORTHGATE CO LLC`, with no
    marker to say so. The cut reading is what the page shows and stays; the
    completion is a proposal beside it.
    """
    records = [
        {"customer_name": "NORTHGATE C"},
        {"customer_name": "NORTHGATE CO LLC"},
        {"customer_name": "NORTHGATE CO LLC"},
    ]
    marked = lane.mark_truncated_text(records)
    assert marked[0]["text_truncated_in_source"] == "customer_name"
    assert marked[0]["completion_proposed"] == "NORTHGATE CO LLC"
    # The original reading is preserved, never overwritten.
    assert marked[0]["customer_name"] == "NORTHGATE C"
    assert marked[1]["text_truncated_in_source"] == ""


def test_a_longer_reading_that_starts_a_new_word_is_not_a_completion():
    """`Ponte Verra` and `Ponte Verra; as per an email` are two readings."""
    marked = lane.mark_truncated_text(
        [{"description": "Ponte Verra"}, {"description": "Ponte Verra; as per an email"}]
    )
    assert all(record["text_truncated_in_source"] == "" for record in marked)
    # Too short to judge, and a value already ending at a break, are both left alone.
    marked = lane.mark_truncated_text([{"description": "AB"}, {"description": "ABCDE"}])
    assert all(record["text_truncated_in_source"] == "" for record in marked)


def test_several_possible_completions_are_flagged_without_a_proposal():
    """One completion is an answer; several are a question for a person."""
    marked = lane.mark_truncated_text(
        [{"item_code": "LOUNGE C"}, {"item_code": "LOUNGE CHAIR"}, {"item_code": "LOUNGE COUCH"}]
    )
    assert marked[0]["text_truncated_in_source"] == "item_code"
    assert marked[0]["completion_proposed"] == ""


def test_a_row_that_restates_a_total_is_labelled():
    """A statement prints its totals as rows of the same table.

    They extract as lines and carry a commission like any other, so summing the
    line grain counts that money twice.
    """
    records = lane.mark_subtotal_rows(
        [
            {"description": "JETTY", "commission_amount": "7.41"},
            {"description": "P.O. Total:", "commission_amount": "58.34"},
            {"description": "Total Commissions to date:", "commission_amount": "2,038.98"},
            {"item_code": "SUBTOTAL", "commission_amount": "10.00"},
            {"description": "Total Station Cabinet", "commission_amount": "5.00"},
        ]
    )
    assert [record["restates_a_total"] for record in records] == ["", "yes", "yes", "yes", ""]
    # The reading itself is untouched; only the label is added.
    assert records[1]["commission_amount"] == "58.34"


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"description": "Total :"}, "yes"),
        ({"description": "TOTAL"}, "yes"),
        ({"description": "Northgate Co LLC Total"}, "yes"),
        ({"description": "Purchase Order Totals — MERROW OFFICE"}, "yes"),
        ({"description": "Purchase Order Totals", "purchase_order_number": "60685-114965"}, "yes"),
        ({"description": "Total - Acoustics"}, "yes"),
        ({"description": "Total - Other", "item_code": "Total - Other"}, "yes"),
        ({"dealer_name": "Acknowledged Total"}, "yes"),
        ({"customer_name": "Total:"}, "yes"),
        ({"description": "Total - Other", "sales_order_number": "SO16712"}, ""),
        (
            {
                "dealer_name": "Total Commissions to date:",
                "job_number": "13075",
                "description": "Atlantic Express San Francisco",
            },
            "",
        ),
        ({"description": "Total Station Cabinet"}, ""),
        ({"customer_name": "Total Wine & More"}, ""),
        ({"description": "Totally Custom Bench"}, ""),
    ],
)
def test_a_bare_total_row_is_labelled_too(record, expected):
    """A total row need not say which total it is.

    A statement closed on a bare `Total :` row carrying its grand total of
    $148,247.29, and the phrase list let it count as one more line. The label is
    known by where the word sits: alone, opening on a separator, or closing the
    cell. A name that only begins with the word is a name.

    Known only by position, or found in a party column, a label counts on a
    row naming no line of its own. `Total - Other` was written into two real
    sales orders as their category, and one statement pasted the next row's
    `Total Commissions to date:` onto a real job's line; flagging either hides
    money the page prints.
    """
    assert lane.mark_subtotal_rows([dict(record)])[0]["restates_a_total"] == expected


def test_a_prefix_of_a_number_is_not_a_truncation():
    """`AP0494` and `AP04944` are two item codes, not one cut short."""
    marked = lane.mark_truncated_text([{"item_code": "AP0494"}, {"item_code": "AP04944"}])
    assert all(record["text_truncated_in_source"] == "" for record in marked)


def test_a_value_the_source_never_rendered_carries_an_out_of_range_sentinel():
    """A CRM cannot notice an empty column, and must not be handed a zero.

    `####` is Excel's column-too-narrow marker. The row is carried; the typed
    money companion says `-99999`, which is wrong loudly rather than quietly.
    """
    records = lane.mark_unreadable_in_source(
        lane.add_loadable_columns(
            [{"document_id": "d", "commission_amount": "########", "transaction_date": "########"}],
            {},
        )
    )
    assert records[0]["commission_amount__amount"] == "-99999"
    assert records[0]["unreadable_in_source"] == "commission_amount; transaction_date"
    # A date column typed as a date cannot hold -99999; naming it is the answer.
    assert records[0]["transaction_date__iso"] == ""
    # The reading the page carried is untouched.
    assert records[0]["commission_amount"] == "########"


def test_scientific_notation_and_a_cut_value_count_as_unrendered():
    """All three are a column too narrow for what it had to show."""
    assert lane.unreadable("########") and lane.unreadable("5.21E+08")
    assert lane.unreadable("01/21/20...") and lane.unreadable("CORPORATE QUARTERS OF…")
    assert not lane.unreadable("1,985.40") and not lane.unreadable("")
    # A run of asterisks masks a value; one asterisk is a footnote mark.
    assert lane.unreadable("***") and not lane.unreadable("*")


def test_a_quantity_is_typed_and_a_masked_one_is_named():
    """2,774 counts reached a NUMERIC column as text; `***` is a masked cell.

    A count takes the same `__amount` companion money does, so a masked one
    carries the same out-of-range sentinel and the reading is left as printed.
    """
    records = lane.mark_unreadable_in_source(
        lane.add_loadable_columns(
            [
                {"document_id": "d", "quantity": "12"},
                {"document_id": "d", "quantity": "1,200"},
                {"document_id": "d", "quantity": "2.5"},
                {"document_id": "d", "quantity": "12 EA"},
                {"document_id": "d", "quantity": "***"},
            ],
            {},
        )
    )
    assert [r["quantity__amount"] for r in records] == ["12", "1200", "2.5", "", "-99999"]
    assert records[4]["unreadable_in_source"] == "quantity"
    assert records[3]["quantity"] == "12 EA"


def test_a_line_is_labelled_by_its_own_arithmetic_and_carried_either_way():
    """Most failures are the document disagreeing with itself, and still real."""

    def row(base, rate, commission):
        return {
            "document_id": "d",
            "commissionable_amount": base,
            "stated_commission_rate": rate,
            "commission_amount": commission,
        }

    records = lane.mark_commission_arithmetic(
        lane.add_loadable_columns(
            [
                row("5,880.00", "10%", "588.00"),
                # Page 6 of the commission run prints this, and it is wrong on
                # the page while every other row on that page is right.
                row("7,646.16", "8%", "2,038.98"),
                # A rate printed rounded to two places is not a failure.
                row("703,376.34", "2.38%", "16,705.19"),
                row("1,000.00", "", "50.00"),
            ],
            {},
        )
    )
    assert [r["commission_arithmetic"] for r in records] == [
        "reconciles",
        "does_not_reconcile",
        "reconciles",
        "",
    ]
    # Carried, not corrected.
    assert records[1]["commission_amount__amount"] == "2038.98"


def test_a_brand_that_is_really_the_specifier_is_labelled():
    """The design firm went into the nearest company-name field for years."""
    records = lane.mark_brand_holding_the_specifier(
        [
            {"brand_name": "BSQ - FLORIDA", "specifier_name": "BSQ - FLORIDA"},
            {"brand_name": "bsq - florida", "specifier_name": "BSQ - FLORIDA"},
            {"brand_name": "MARLOW/BRAMWELL INC.", "specifier_name": "NORCROSS TAMPA"},
            {"brand_name": "", "specifier_name": ""},
        ]
    )
    assert [r["brand_name_holds_the_specifier"] for r in records] == ["yes", "yes", "", ""]
    # The reading itself is what the page says, and stays.
    assert records[0]["brand_name"] == "BSQ - FLORIDA"


def test_repeated_products_collapse_into_one_row_each():
    """The line grain repeats a product on every line that sells it.

    Loaded as products that is one product per line; the dimension groups them
    by the identity the line states and counts the lines behind each.
    """
    records = [
        {"document_id": "d1", "item_code": "BU0597", "description": "LOUNGE C"},
        {"document_id": "d2", "item_code": "bu0597", "description": "LOUNGE CHAIR"},
        {"document_id": "d2", "item_code": "BU0597", "description": "LOUNGE C"},
        {"document_id": "d3", "product_sku": "SO1610"},
        {"document_id": "d3"},
    ]
    items = lane.item_dimension(records)
    assert [item["natural_key"] for item in items] == ["BU0597", "SO1610"]
    first = items[0]
    assert first["line_rows"] == 3 and first["source_document_count"] == 2
    # Both descriptions are carried; neither is chosen over the other.
    assert first["description"] == "LOUNGE C"
    assert first["other_descriptions"] == "LOUNGE CHAIR"
    assert first["description_count"] == 2
    # A line naming no product contributes nothing rather than an empty item.
    assert items[1]["identified_by"] == "product_sku"


def test_a_reading_whose_shape_is_not_a_product_is_labelled_not_dropped():
    """A territory code and a line item number each reached `item_code` on a run."""
    items = lane.item_dimension(
        [
            {"document_id": "d", "item_code": "150-NGC"},
            {"document_id": "d", "item_code": "10"},
            {"document_id": "d", "item_code": "801 Bayshore Avenue"},
            {"document_id": "d", "item_code": "BU0597"},
        ]
    )
    labels = {item["natural_key"]: item["not_a_product"] for item in items}
    assert labels["150-NGC"] == "a territory code"
    # The source form prints this column as `Item`, holding SAP line item
    # numbers beside a separate `Order Position` and no quantity column at all.
    assert labels["10"] == "a line item number"
    assert labels["801 Bayshore Avenue"] == "an address"
    assert labels["BU0597"] == ""
    # Nothing is dropped: every reading keeps a row.
    assert len(items) == 4
    assert lane.item_identity({"style": "MEDINAH"}) == ("style", "MEDINAH")
    assert lane.item_identity({}) == ("", "")


def test_the_cli_writes_the_item_dimension(tmp_path):
    """`--items-csv` writes distinct products, not one row per line."""
    documents = [
        {
            "document_id": "doc-1",
            "fields": {
                "lines[0].item_code": field("BU0597"),
                "lines[0].description": field("LOUNGE C"),
                "lines[1].item_code": field("BU0597"),
                "lines[1].description": field("LOUNGE C"),
                "lines[2].item_code": field("150-NGC"),
            },
        }
    ]
    run(
        tmp_path,
        ["--lines-csv", str(tmp_path / "lines.csv"), "--items-csv", str(tmp_path / "items.csv")],
        documents=documents,
    )
    items = list(csv.DictReader((tmp_path / "items.csv").open()))
    assert [item["natural_key"] for item in items] == ["BU0597", "150-NGC"]
    assert items[0]["line_rows"] == "2", "two lines, one product"
    assert items[1]["not_a_product"] == "a territory code"


def test_a_money_column_read_one_row_low_is_flagged_on_every_row_it_drifted():
    """The quietest way money goes wrong: a real number on the wrong row.

    Page 617 prints one money column, `Adjusted Net Sale`, and no commission at
    all -- yet fourteen rows carried a commission, each of them the previous
    row's net sale. No format, range or arithmetic check on that row can see it,
    because the number really is printed on the page.
    """
    records = [
        {
            "document_id": "d",
            "line_index": 0,
            "sales_amount__amount": "100",
            "commission_amount__amount": "",
        },
        {
            "document_id": "d",
            "line_index": 1,
            "sales_amount__amount": "200",
            "commission_amount__amount": "100",
        },
        {
            "document_id": "d",
            "line_index": 2,
            "sales_amount__amount": "300",
            "commission_amount__amount": "200",
        },
        {
            "document_id": "d",
            "line_index": 3,
            "sales_amount__amount": "400",
            "commission_amount__amount": "300",
        },
    ]
    flagged = lane.mark_columns_read_one_row_low(records, per_line=True)
    assert flagged[0]["column_read_one_row_low"] == ""
    for row in flagged[1:]:
        assert row["column_read_one_row_low"] == (
            "commission_amount carries the previous row's sales_amount"
        )
    # Nothing is moved: the reading the page carries is still the reading.
    assert [row["commission_amount__amount"] for row in flagged] == ["", "100", "200", "300"]


def test_a_drift_that_stops_partway_down_the_table_is_still_flagged():
    """A column can come back into line, and the rows it missed still count."""
    records = [
        {
            "document_id": "d",
            "line_index": 0,
            "sales_amount__amount": "100",
            "commission_amount__amount": "11",
        },
        {
            "document_id": "d",
            "line_index": 1,
            "sales_amount__amount": "200",
            "commission_amount__amount": "100",
        },
        {
            "document_id": "d",
            "line_index": 2,
            "sales_amount__amount": "300",
            "commission_amount__amount": "200",
        },
        {
            "document_id": "d",
            "line_index": 3,
            "sales_amount__amount": "400",
            "commission_amount__amount": "44",
        },
        {
            "document_id": "d",
            "line_index": 4,
            "sales_amount__amount": "500",
            "commission_amount__amount": "55",
        },
    ]
    flagged = lane.mark_columns_read_one_row_low(records, per_line=True)
    assert [bool(row["column_read_one_row_low"]) for row in flagged] == [
        False,
        True,
        True,
        False,
        False,
    ]


def test_a_single_coincidence_is_not_a_shifted_column():
    """One match on a page of round numbers is chance; a run is evidence."""
    records = [
        {
            "document_id": "d",
            "line_index": 0,
            "sales_amount__amount": "100",
            "commission_amount__amount": "9",
        },
        {
            "document_id": "d",
            "line_index": 1,
            "sales_amount__amount": "200",
            "commission_amount__amount": "100",
        },
        {
            "document_id": "d",
            "line_index": 2,
            "sales_amount__amount": "300",
            "commission_amount__amount": "7",
        },
    ]
    flagged = lane.mark_columns_read_one_row_low(records, per_line=True)
    assert all(row["column_read_one_row_low"] == "" for row in flagged)


def test_zeros_and_the_unreadable_sentinel_are_not_evidence_of_a_shift():
    """Both repeat down a column by design, at every offset."""
    zeros = [
        {
            "document_id": "d",
            "line_index": i,
            "sales_amount__amount": "0",
            "commission_amount__amount": "0",
        }
        for i in range(4)
    ]
    assert all(
        row["column_read_one_row_low"] == ""
        for row in lane.mark_columns_read_one_row_low(zeros, True)
    )
    sentinel = [
        {
            "document_id": "d",
            "line_index": i,
            "sales_amount__amount": lane.UNREADABLE_SENTINEL,
            "commission_amount__amount": lane.UNREADABLE_SENTINEL,
        }
        for i in range(4)
    ]
    assert all(
        row["column_read_one_row_low"] == ""
        for row in lane.mark_columns_read_one_row_low(sentinel, True)
    )
    assert lane.money_value("0") is None
    assert lane.money_value("not a number") is None
    assert lane.money_value("12.5") == 12.5


def test_the_document_grain_carries_the_column_and_never_a_finding():
    """One row per document has no row order for a drift to show up in."""
    records = [
        {"document_id": "d", "sales_amount__amount": "100", "commission_amount__amount": "100"}
    ]
    flagged = lane.mark_columns_read_one_row_low(records, per_line=False)
    assert flagged[0]["column_read_one_row_low"] == ""


def test_a_company_field_repeating_the_description_beside_a_job_is_a_project():
    """A building is never a dealer. 314 lines carried one as their dealer.

    The evidence is on the row: the company field repeats the row's own
    description, and the row carries a job number, which is what a project has
    and a trading account does not.
    """
    records = [
        {
            "document_id": "d",
            "description": "One Bayfront Suite 540",
            "dealer_name": "One Bayfront Suite 540",
            "customer_name": "One Bayfront Suite 540",
            "job_number": "12664",
        },
        # A real dealer does not repeat the line's description.
        {
            "document_id": "d",
            "description": "ARMCHAIR",
            "dealer_name": "KD Frost",
            "job_number": "9",
        },
        # Without a job or project number there is no project for it to be.
        {
            "document_id": "d",
            "description": "Auctioneers Suite",
            "dealer_name": "Auctioneers Suite",
            "job_number": "",
        },
        # A project number is the same evidence: some statements print a
        # `Project #` and no job number at all.
        {
            "document_id": "d",
            "description": "Grow Financial West River Branch - Tampa, FL",
            "customer_name": "Grow Financial West River Branch - Tampa, FL",
            "project_number": "2300011071",
        },
    ]
    flagged = lane.mark_party_fields_holding_the_project(records)
    assert flagged[0]["party_field_holds_the_project"] == "customer_name; dealer_name"
    assert flagged[1]["party_field_holds_the_project"] == ""
    assert flagged[2]["party_field_holds_the_project"] == ""
    assert flagged[3]["party_field_holds_the_project"] == "customer_name"
    # Nothing is moved: both readings are true readings of the page.
    assert flagged[0]["dealer_name"] == "One Bayfront Suite 540"


def test_a_rate_cell_holding_the_commission_is_proved_by_the_documents_own_rates():
    """One page shifted its money left: the commission landed in the rate.

    Eleven lines read as rates of 182.93 and 961.65 percent, and the commission
    column was empty, so that money was invisible to every total. Proved against
    the rates this document itself states on rows that reconcile -- a rate table
    in the code would be a guess that goes stale on the next corpus.
    """
    records = [
        # This row proves the document works at 10 percent.
        {
            "document_id": "d",
            "commissionable_amount__amount": "1000",
            "commission_amount__amount": "100",
            "stated_commission_rate": "10.00",
            "commission_arithmetic": "reconciles",
        },
        # 182.93 is not a rate; it is 1829.25 at the 10 percent above.
        {
            "document_id": "d",
            "commissionable_amount__amount": "1829.25",
            "commission_amount__amount": "",
            "stated_commission_rate": "182.93",
            "commission_arithmetic": "",
        },
        # A genuine rate on a row with no commission stays unflagged.
        {
            "document_id": "d",
            "commissionable_amount__amount": "500",
            "commission_amount__amount": "",
            "stated_commission_rate": "10.00",
            "commission_arithmetic": "",
        },
    ]
    records += [
        # A row with nothing to judge: no rate, or no base to apply one to.
        {
            "document_id": "d",
            "commissionable_amount__amount": "",
            "commission_amount__amount": "",
            "stated_commission_rate": "10.00",
            "commission_arithmetic": "",
        },
        # A reconciling row whose "rate" is outside any percentage is not
        # evidence of what this document charges.
        {
            "document_id": "d",
            "commissionable_amount__amount": "2000",
            "commission_amount__amount": "200",
            "stated_commission_rate": "961.65",
            "commission_arithmetic": "reconciles",
        },
    ]
    flagged = lane.mark_rate_columns_holding_an_amount(records, per_line=True)
    assert flagged[0]["rate_column_holds_an_amount"] == ""
    assert "182.93 is this line's commission" in flagged[1]["rate_column_holds_an_amount"]
    assert flagged[2]["rate_column_holds_an_amount"] == ""
    assert flagged[3]["rate_column_holds_an_amount"] == ""
    assert lane.rates_stated_by(records) == {10.0}


def test_a_document_that_proves_no_rate_makes_no_claim_about_its_rate_column():
    """With nothing reconciling, there is no evidence to judge a rate against."""
    records = [
        {
            "document_id": "d",
            "commissionable_amount__amount": "1829.25",
            "commission_amount__amount": "",
            "stated_commission_rate": "182.93",
            "commission_arithmetic": "does_not_reconcile",
        }
    ]
    assert lane.rates_stated_by(records) == set()
    flagged = lane.mark_rate_columns_holding_an_amount(records, per_line=True)
    assert flagged[0]["rate_column_holds_an_amount"] == ""
    # The document grain carries the column and never a finding.
    assert (
        lane.mark_rate_columns_holding_an_amount(records, per_line=False)[0][
            "rate_column_holds_an_amount"
        ]
        == ""
    )


def test_a_table_read_twice_is_flagged_even_when_the_copy_is_emptier():
    """Page 297 prints eight lines totalling $4,966.50; the export carried
    sixteen totalling $9,933.00.

    `mark_duplicate_lines` compares whole rows and the second pass arrives
    *less* complete -- it dropped every sales-order number and every customer --
    so the emptier copy never matched and the money was counted twice.
    """
    rows = [
        {
            "document_id": "d",
            "line_index": 0,
            "commission_amount__amount": "651.00",
            "sales_order_number": "SO31097",
        },
        {
            "document_id": "d",
            "line_index": 1,
            "commission_amount__amount": "708.90",
            "sales_order_number": "SO31097",
        },
        {
            "document_id": "d",
            "line_index": 2,
            "commission_amount__amount": "115.80",
            "sales_order_number": "SO31097",
        },
        # The same table again, with the labels gone.
        {
            "document_id": "d",
            "line_index": 3,
            "commission_amount__amount": "651.00",
            "sales_order_number": "",
        },
        {
            "document_id": "d",
            "line_index": 4,
            "commission_amount__amount": "708.90",
            "sales_order_number": "",
        },
        {
            "document_id": "d",
            "line_index": 5,
            "commission_amount__amount": "115.80",
            "sales_order_number": "",
        },
    ]
    flagged = lane.mark_repeated_blocks(rows, per_line=True)
    assert [bool(r["repeats_an_earlier_block"]) for r in flagged] == [
        False,
        False,
        False,
        True,
        True,
        True,
    ]
    assert "repeats line 0" in flagged[3]["repeats_an_earlier_block"]
    # Nothing is dropped: the copy is a real reading of a page read twice.
    assert flagged[3]["commission_amount__amount"] == "651.00"


def test_a_page_that_really_prints_the_same_line_many_times_is_not_a_repeat():
    """One page prints eight identical lines, and that money is real.

    Only a copy that begins after the original ends is a repeated table. A page
    repeating itself matches at small offsets, and treating that as duplication
    would throw away money the page states.
    """
    rows = [
        {
            "document_id": "d",
            "line_index": i,
            "commission_amount__amount": "75.60",
            "description": "Acoustics",
        }
        for i in range(8)
    ]
    flagged = lane.mark_repeated_blocks(rows, per_line=True)
    assert all(r["repeats_an_earlier_block"] == "" for r in flagged)


def test_a_short_or_empty_document_claims_no_repeated_block():
    """Three matched rows is the least that is evidence rather than accident."""
    assert lane.repeated_block_offset([]) is None
    short = [
        {"document_id": "d", "line_index": i, "commission_amount__amount": v}
        for i, v in enumerate(["10", "20", "10", "20"])
    ]
    # Two matched rows is below the floor.
    assert lane.repeated_block_offset(short) is None
    # The document grain carries the column and never a finding.
    assert lane.mark_repeated_blocks(short, per_line=False)[0]["repeats_an_earlier_block"] == ""
    # A row with nothing in any signature column contributes no evidence.
    assert lane.block_signature({}) == ("", "", "", "")
    # A document whose rows simply differ has no second copy of anything.
    distinct = [
        {"document_id": "d", "line_index": i, "commission_amount__amount": str(i * 11 + 1)}
        for i in range(8)
    ]
    assert lane.repeated_block_offset(distinct) is None
    # Nor has one whose later rows carry nothing to compare.
    trailing_blanks = [
        {"document_id": "d", "line_index": i, "commission_amount__amount": v}
        for i, v in enumerate(["10", "20", "30", "40", "", "", "", ""])
    ]
    assert lane.repeated_block_offset(trailing_blanks) is None
    # A document of mostly empty rows offers too few comparable pairs to judge.
    mostly_empty = [
        {"document_id": "d", "line_index": i, "commission_amount__amount": v}
        for i, v in enumerate(["10", "20", "", "", "", "", "", ""])
    ]
    assert lane.repeated_block_offset(mostly_empty) is None


def test_the_commission_no_control_can_test_travels_with_the_rate():
    """A rate over the provable rows alone answers a question nobody asked.

    96.7% of provable rows reconcile on this corpus while 35.8% of the money
    sits on documents where nothing is provable, one of which prints no
    commission column and carries $1.67M. The repository's rule is that a
    control which processed nothing is failed rather than clear; this applies
    it to an accuracy figure.
    """
    records = [
        # A document the check reached: both rows count as tested.
        {
            "document_id": "tested",
            "commission_amount__amount": "100",
            "commission_arithmetic": "reconciles",
        },
        {"document_id": "tested", "commission_amount__amount": "200", "commission_arithmetic": ""},
        # A document stating no base and no rate: nothing is provable on it.
        {"document_id": "blind", "commission_amount__amount": "500", "commission_arithmetic": ""},
        {
            "document_id": "blind",
            "commission_amount__amount": "250.50",
            "commission_arithmetic": "",
        },
        # A cell the page never rendered is not money anyone can count.
        {
            "document_id": "blind",
            "commission_amount__amount": lane.UNREADABLE_SENTINEL,
            "commission_arithmetic": "",
        },
        {"document_id": "blind", "commission_amount__amount": "", "commission_arithmetic": ""},
    ]
    assert lane.commission_no_control_can_test(records) == (750.50, 2)
    # A failing row still counts as tested: the check reached it and said no.
    only_failures = [
        {
            "document_id": "d",
            "commission_amount__amount": "10",
            "commission_arithmetic": "does_not_reconcile",
        }
    ]
    assert lane.commission_no_control_can_test(only_failures) == (0.0, 0)


def test_a_commission_equal_to_its_own_base_is_flagged():
    """A rate of 100% is not a thing; one number reached two columns.

    The HALVOR open order reports print a single money column headed `Adjusted
    Net Sale` and no commission at all, and 88 rows across 16 documents carried
    $3.25M of "commission" that is those net sales copied across. No other
    control sees it: the arithmetic check needs a base and a rate and those
    pages state neither, so every row is `not_provable`, and the value is
    well-formed, in range, and really printed on the page.
    """
    records = [
        {
            "document_id": "d",
            "commission_amount__amount": "11535.75",
            "sales_amount__amount": "11535.75",
        },
        {
            "document_id": "d",
            "commission_amount__amount": "100",
            "commissionable_amount__amount": "1000",
        },
        {"document_id": "d", "commission_amount__amount": "", "sales_amount__amount": "500"},
        # The unreadable sentinel repeats across columns by design.
        {
            "document_id": "d",
            "commission_amount__amount": lane.UNREADABLE_SENTINEL,
            "sales_amount__amount": lane.UNREADABLE_SENTINEL,
        },
        # Zero against zero says nothing either.
        {"document_id": "d", "commission_amount__amount": "0", "sales_amount__amount": "0"},
    ]
    flagged = lane.mark_commission_equal_to_its_base(records)
    assert "equal to sales_amount" in flagged[0]["commission_equals_its_own_base"]
    assert [row["commission_equals_its_own_base"] for row in flagged[1:]] == ["", "", "", ""]
    # Nothing is moved: the number is a true reading of the page.
    assert flagged[0]["commission_amount__amount"] == "11535.75"


def test_the_summary_carries_every_grains_counts(tmp_path):
    """Each grain's counts reach the JSON, and a clobber fails before any CSV is written.

    The JSON was written before the grains were exported, so every count a grain
    added -- rows, duplicates, the new field flags -- was missing from every
    delivered summary.
    """
    result = run(
        tmp_path,
        ["--lines-csv", str(tmp_path / "lines.csv"), "--documents-csv", str(tmp_path / "docs.csv")],
    )
    lines = list(csv.DictReader((tmp_path / "lines.csv").open()))
    assert result["summary"]["line_records"] == len(lines)
    assert result["summary"]["document_records"] == 1
    for grain in ("line", "document"):
        assert result["summary"][f"{grain}_records_with_a_party_field_not_a_party"] == 0
        assert result["summary"][f"{grain}_records_with_a_person_field_not_one_person"] == 0
    again = tmp_path / "again"
    again.mkdir()
    (again / "out.json").write_text("{}")
    argv = [
        write(again / "c.json", consensus()),
        "--manifest",
        write(again / "m.json", manifest()),
        "--out",
        str(again / "out.json"),
        "--lines-csv",
        str(again / "lines.csv"),
    ]
    with pytest.raises(SystemExit, match="Output already exists"):
        lane.main(argv)
    assert not (again / "lines.csv").exists()
    assert (again / "out.json").read_text() == "{}"


def test_a_row_the_page_review_says_the_page_does_not_print_is_labelled_never_dropped(
    tmp_path, capsys
):
    """Every line is kept; a label says which the page review found the page does not print.

    A second vendor's reading bears out line 1, line 2 carries no money, line 3
    now holds a figure the review never saw, and two sources dispute line 0. A
    line the export no longer has is counted, not guessed at.
    """
    documents = [
        {
            "document_id": "p1",
            "review_status": "open_exception",
            "fields": {
                "lines[0].commission_amount": field("50.00"),
                "lines[1].commission_amount": field("50.00"),
                "lines[2].commission_amount": field(
                    value=None, flag="single_engine", accepted=False, candidate_values=[]
                ),
                "lines[3].commission_amount": field("12.00"),
            },
        }
    ]

    def claim(line, value, evidence):
        return {
            "page": "p1",
            "line_index": line,
            "column": "commission_amount",
            "value": value,
            "evidence": evidence,
        }

    proposals = write(
        tmp_path / "proposals.json",
        {
            "artifact_type": "page_review_proposals_v1",
            "rows_not_printed": [
                claim("0", "50.00", "extractor_prints_it_as_often"),
                claim("1", "50.00", "second_vendor_prints_it_fewer_times"),
                claim("2", "", "no_money_on_row"),
                claim("3", "99.00", "extractor_prints_it_fewer_times"),
                claim("7", "1.00", "extractor_prints_it_fewer_times"),
            ],
        },
    )
    result = run(
        tmp_path,
        [
            "--lines-csv",
            str(tmp_path / "lines.csv"),
            "--rows-not-printed",
            proposals,
            "--rows-not-printed-authorization",
            "auth-55",
        ],
        documents=documents,
    )
    lines = list(csv.DictReader((tmp_path / "lines.csv").open()))
    assert [row["page_review_row_not_printed"] for row in lines] == [
        "",
        "second_vendor_prints_it_fewer_times",
        "no_money_on_row",
        "",
    ]
    assert result["summary"]["rows_not_printed_authorization"] == "auth-55"
    assert result["summary"]["rows_not_printed_labels"] == {
        "no_money_on_row": 1,
        "no_such_line": 1,
        "row_changed_since_the_review": 1,
        "second_vendor_prints_it_fewer_times": 1,
    }
    printed = capsys.readouterr().out
    assert "rows the page review says the page does not print: 1 no_money_on_row" in printed


def test_row_labels_are_refused_without_their_authorization_or_a_row_to_label(tmp_path):
    """A label lane with no name behind it, no line grain, the wrong artifact, or nothing to do."""
    good = write(
        tmp_path / "good.json",
        {
            "artifact_type": "page_review_proposals_v1",
            "rows_not_printed": [{"page": "p1", "line_index": "0", "evidence": "no_money_on_row"}],
        },
    )
    other = write(tmp_path / "other.json", {"artifact_type": "something_else"})
    disputed = write(
        tmp_path / "disputed.json",
        {
            "artifact_type": "page_review_proposals_v1",
            "rows_not_printed": [{"evidence": "extractor_prints_it_as_often"}],
        },
    )
    base = [
        write(tmp_path / "c.json", consensus()),
        "--manifest",
        write(tmp_path / "m.json", manifest()),
        "--out",
        str(tmp_path / "out.json"),
    ]
    lines = ["--lines-csv", str(tmp_path / "lines.csv")]
    named = ["--rows-not-printed-authorization", "auth-55"]
    for extra, message in (
        ([*lines, "--rows-not-printed", good], "go together"),
        ([*lines, *named], "go together"),
        ([*lines, "--rows-not-printed", good, "--rows-not-printed-authorization", " "], "together"),
        (["--rows-not-printed", good, *named], "labels the line grain"),
        ([*lines, "--rows-not-printed", other, *named], "not a page_review_proposals_v1"),
        ([*lines, "--rows-not-printed", disputed, *named], "name no row"),
    ):
        with pytest.raises(SystemExit, match=message):
            lane.main([*base, *extra])
    assert not (tmp_path / "out.json").exists()
