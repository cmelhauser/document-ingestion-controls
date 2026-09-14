"""The client is not a counterparty, and a location key is not part of a name.

Two faults that put the same corpus into a CRM wrong. The client is printed on
nearly every page and became the largest account in the master, its own
customer, dealer and brand. And an account printed beside the number that picks
its location loaded twice -- `KD Frost` with 442 mentions and `7144 KD Frost`
with 3 -- so neither row held the account's real volume.
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

lane = importlib.import_module("entity_resolve")
export = importlib.import_module("field_extract_export")


@pytest.mark.parametrize(
    "printed, identifier, account",
    [
        # The number selects which of the account's locations this row is.
        ("7144 KD Frost", "7144", "KD Frost"),
        ("7373 Empall", "7373", "Empall"),
        ("150-NGC", "150", "NGC"),
        ("18313 NORTHGATE C", "18313", "NORTHGATE C"),
        # A street number belongs to the name. These are two Miami buildings.
        ("801 Bayshore Avenue", "", "801 Bayshore Avenue"),
        ("2200 Bayshore Avenue", "", "2200 Bayshore Avenue"),
        # An address with its city attached still starts with a direction.
        (
            "4100 NE 20TH AVE HARBOR POINT, FL 33000",
            "",
            "4100 NE 20TH AVE HARBOR POINT, FL 33000",
        ),
        ("1900 East", "", "1900 East"),
        # Nothing to split.
        ("Empall", "", "Empall"),
        ("3-USA", "", "3-USA"),
        ("", "", ""),
        # Digits with no name behind them are not an account plus a location.
        ("18313 5.25", "", "18313 5.25"),
    ],
)
def test_a_location_key_is_lifted_off_and_a_street_number_is_not(printed, identifier, account):
    assert lane.split_location_identifier(printed) == (identifier, account)


def test_an_account_printed_with_and_without_its_location_key_is_one_account():
    """`KD Frost` and `7144 KD Frost` are one account with one location."""
    records = [
        {
            "document_id": "d1",
            "header": {"customer_name": "KD Frost"},
            "lines": [{"customer_name": "7144 KD Frost"}, {"customer_name": "7144 KD Frost"}],
        }
    ]
    mentions, _ = lane.extract_parties(records)
    groups, _, _ = lane.cluster(mentions, 0.88, 0.80)
    parties = lane.build_parties(mentions, groups)
    assert len(parties) == 1
    party = parties[0]
    assert party["canonical_name"] == "KD Frost"
    assert party["location_identifiers"] == ["7144"]
    # The page's own wording is still carried, key and all.
    assert party["name_variants"] == ["7144 KD Frost", "KD Frost"]


def test_one_account_carries_every_location_it_was_printed_with():
    """`NGC` is one account at 150 and at 900, not two accounts."""
    records = [
        {"document_id": "d1", "header": {"customer_name": "150-NGC"}},
        {"document_id": "d2", "header": {"customer_name": "900-NGC"}},
    ]
    mentions, _ = lane.extract_parties(records)
    groups, _, _ = lane.cluster(mentions, 0.88, 0.80)
    parties = lane.build_parties(mentions, groups)
    assert len(parties) == 1
    assert parties[0]["location_identifiers"] == ["150", "900"]


@pytest.mark.parametrize(
    "printed, is_client",
    [
        # Exactly, once legal suffixes are set aside.
        ("Northgate Co", True),
        ("NORTHGATE", True),
        ("Northgate & Co", True),
        ("NORTHGATE CO.", True),
        # Cut off mid-word by a column too narrow for it.
        ("NORTHGATE C", True),
        # With something appended: a totals row, an annotator's initials.
        ("NORTHGATE CO Total", True),
        ("Northgate Co, LLC JM", True),
        # Behind a location key.
        ("18313 NORTHGATE C", True),
        ("648 Northgate Co, LLC", True),
        # A different company that merely starts the same way.
        ("Northgale Co", False),
        # A blank declaration matches nothing rather than everything.
        ("KD Frost", False),
        ("KD Frost", False),
        ("", False),
    ],
)
def test_the_client_is_recognised_in_every_form_the_pages_print_it(printed, is_client):
    assert lane.is_the_client(printed, ["Northgate Co"]) is is_client


def test_a_blank_client_declaration_matches_nothing():
    """An empty string must not silently refuse every party."""
    assert lane.is_the_client("KD Frost", [""]) is False
    assert lane.is_the_client("Northgate Co", ["", "Northgate Co"]) is True


def test_a_declared_client_is_refused_as_a_counterparty_and_retained():
    """Refused, never dropped: the reading is true and the role is wrong."""
    records = [
        {
            "document_id": "d1",
            "header": {"customer_name": "KD Frost", "agent_name": "NORTHGATE C"},
            "lines": [{"brand_name": "Northgate Co"}],
        }
    ]
    mentions, refused = lane.extract_parties(records, ["Northgate Co"])
    assert [m["raw_name"] for m in mentions] == ["KD Frost"]
    client = [entry for entry in refused if entry["reason"] == "the_client_is_not_a_counterparty"]
    assert sorted(entry["field"] for entry in client) == ["agent_name", "brand_name"]
    assert {entry["document_id"] for entry in client} == {"d1"}
    # Undeclared, the client resolves like anyone else -- which is the fault.
    mentions, refused = lane.extract_parties(records)
    assert len(mentions) == 3
    assert not [e for e in refused if e["reason"] == "the_client_is_not_a_counterparty"]


def test_the_loose_form_keeps_the_suffix_the_truncation_falls_inside():
    """`normalize_name` drops `co`, and that is where the page cut the name."""
    assert lane.normalize_name("Northgate Co LLC") == "northgate"
    assert lane.loose_name("Northgate Co LLC") == "northgate co llc"
    assert lane.loose_name("Northgate & Co.") == "northgate and co"
    assert lane.loose_name(None) == ""


def test_an_undeclared_client_is_reported_rather_than_assumed_absent(tmp_path, capsys):
    """A control that never ran must not read as a control that passed."""
    records = tmp_path / "records.json"
    records.write_text(json.dumps([{"document_id": "d1", "header": {"customer_name": "KD Frost"}}]))
    out, log = tmp_path / "parties.json", tmp_path / "merges.json"
    sys.argv = ["entity_resolve.py", str(records), "--out", str(out), "--log", str(log), "--quiet"]
    lane.main()
    summary = json.loads(out.read_text())["summary"]
    assert summary["mentions_refused_as_the_client"] == 0
    assert summary["client_names_declared"] == []
    assert any("No --client-name was declared" in f for f in summary["findings"])


def test_the_refused_client_mentions_are_counted_and_reported(tmp_path):
    """The count is the evidence the check ran and what it caught."""
    records = tmp_path / "records.json"
    records.write_text(
        json.dumps(
            [
                {
                    "document_id": "d1",
                    "header": {"customer_name": "KD Frost", "agent_name": "NORTHGATE C"},
                    "lines": [{"brand_name": "Northgate Co"}],
                }
            ]
        )
    )
    out, log = tmp_path / "parties.json", tmp_path / "merges.json"
    sys.argv = [
        "entity_resolve.py",
        str(records),
        "--out",
        str(out),
        "--log",
        str(log),
        "--client-name",
        "Northgate Co",
        "--quiet",
    ]
    lane.main()
    written = json.loads(out.read_text())
    summary = written["summary"]
    assert summary["mentions_refused_as_the_client"] == 2
    assert summary["client_names_declared"] == ["Northgate Co"]
    assert any("named the client and were refused" in f for f in summary["findings"])
    # Refused, not dropped: each keeps the field and document it was read from.
    assert sorted(e["field"] for e in written["refused_as_the_client"]) == [
        "agent_name",
        "brand_name",
    ]
    # The client is gone from the master and the counterparty is not.
    assert [p["canonical_name"] for p in written["parties"]] == ["KD Frost"]


def test_a_declared_client_that_matches_nothing_is_reported(tmp_path):
    """A check that matched nothing is a finding, not a pass.

    A client named on every page and refused zero times means the declared
    spelling is not the one the pages print.
    """
    records = tmp_path / "records.json"
    records.write_text(json.dumps([{"document_id": "d1", "header": {"customer_name": "KD Frost"}}]))
    out, log = tmp_path / "parties.json", tmp_path / "merges.json"
    sys.argv = [
        "entity_resolve.py",
        str(records),
        "--out",
        str(out),
        "--log",
        str(log),
        "--client-name",
        "Misspelled Co",
        "--quiet",
    ]
    lane.main()
    summary = json.loads(out.read_text())["summary"]
    assert summary["mentions_refused_as_the_client"] == 0
    assert any("matched no mention" in f for f in summary["findings"])


def test_a_row_naming_the_client_is_flagged_for_the_mapping_that_reads_it_raw():
    """The resolved party is gone; a mapping reading the raw column is not."""
    records = [
        {"document_id": "d1", "brand_name": "NORTHGATE C", "customer_name": "KD Frost"},
        {"document_id": "d2", "brand_name": "Murbrook", "customer_name": ""},
    ]
    flagged = export.mark_rows_naming_the_client(records, ["Northgate Co"])
    assert flagged[0]["fields_naming_the_client"] == "brand_name"
    assert flagged[1]["fields_naming_the_client"] == ""
    # With no client declared the column exists and is empty, never absent.
    assert all(
        row["fields_naming_the_client"] == ""
        for row in export.mark_rows_naming_the_client(records, [])
    )


@pytest.mark.parametrize(
    "short, long_form, restores",
    [
        # The page stops mid-word with nothing marking it.
        ("Office Surr", "OFFICE SURROUNDINGS & SERVICES", True),
        ("QRC Busin", "QRC BUSINESS INTERIORS", True),
        # Case and punctuation must not turn one name into two.
        ("The Furnis", "THE FURNISHERS UNLIMITED", True),
        ("HOLDEN'S BUSINESS ENV.", "Holden's Business Environments", True),
        ("FURNITURE ADVISORS-FL", "Furniture Advisors - Florida", True),
        # A completion restores words; it does not append an identifier.
        ("KOK Interi", "KOK Interic PO4705", False),
        # A prefix that stops on a word boundary is not a cut-off word.
        ("Supply Co", "Supply Co Holdings", False),
        # Two different parties, not one truncated.
        ("Empall", "Empall Office", False),
        ("KD Frost", "Purdy", False),
    ],
)
def test_a_completion_restores_words_rather_than_adding_a_code(short, long_form, restores):
    assert lane.restores_the_same_words(short, long_form) is restores


def test_an_account_is_named_by_its_complete_reading_not_its_stump():
    """14 accounts were named for their own truncation on the commission run.

    `is_truncated` sees only an ellipsis, and this corpus mostly cuts a name
    with nothing marking it, so frequency made the stump canonical while the
    whole spelling sat in the same party's variants.
    """
    variants = ["Office Surr", "Office Surr", "Office Sur", "OFFICE SURROUNDINGS & SERVICES"]
    assert lane.choose_survivor(variants) == "OFFICE SURROUNDINGS & SERVICES"
    assert lane.stumps_among(variants) == {"Office Surr", "Office Sur"}


def test_one_answer_printed_two_ways_is_not_an_ambiguous_completion():
    """`&` and `and` are the same word, and the guard must not read a conflict.

    Treating them as two candidate completions left the account named
    `Office Surr` with both full spellings sitting beside it.
    """
    variants = [
        "Office Surr",
        "OFFICE SURROUNDINGS & SERVICES",
        "Office Surroundings and Services",
    ]
    assert "Office Surr" in lane.stumps_among(variants)
    # Genuinely different completions are still refused.
    assert lane.stumps_among(["Supply Co Ma", "Supply Co Marine", "Supply Co Machining"]) == set()


def test_a_party_with_only_stumps_still_gets_a_name():
    """Excluding every candidate must not leave the party nameless."""
    assert lane.choose_survivor(["Acme Corp"]) == "Acme Corp"
    assert lane.choose_survivor(["Ace", "Ace"]) == "Ace"


def test_a_heading_a_role_or_a_placeholder_is_not_a_party():
    """`COMPANY` heads a report's first column and was read as the dealer.

    `INSTALL REP` names a role, and `N/A`, `None` and `TBD` name nobody; a
    total's label and a signed territory split were read as dealers too. Each
    became a resolved party, which is a CRM account. A name that only contains
    one of those words is still a name, and so is one that only ends in a colon.
    """
    records = [
        {"document_id": "d1", "header": {"dealer_name": "COMPANY"}},
        {"document_id": "d2", "header": {"specifier_name": "INSTALL REP"}},
        {"document_id": "d3", "header": {"brand_name": "N/A"}},
        {"document_id": "d4", "header": {"brand_name": "None"}},
        {"document_id": "d5", "header": {"brand_name": "TBD"}},
        {"document_id": "d6", "header": {"dealer_name": "LEBNER+COMPANY"}},
        {"document_id": "d7", "header": {"dealer_name": "Total Commissions to date:"}},
        {"document_id": "d8", "header": {"customer_name": "Total :"}},
        {"document_id": "d9", "header": {"dealer_name": "-70% Specify Territory"}},
        {"document_id": "d10", "header": {"brand_name": "DEALER OF"}},
        {"document_id": "d11", "header": {"customer_name": "THE COVE:"}},
        {"document_id": "d12", "header": {"customer_name": "Total Wine & More"}},
    ]
    mentions, refused = lane.extract_parties(records)
    assert [m["raw_name"] for m in mentions] == ["LEBNER+COMPANY", "THE COVE:", "Total Wine & More"]
    assert [r["raw_name"] for r in refused] == [
        "COMPANY",
        "INSTALL REP",
        "N/A",
        "None",
        "TBD",
        "Total Commissions to date:",
        "Total :",
        "-70% Specify Territory",
        "DEALER OF",
    ]
    assert {r["reason"] for r in refused} == {"party_name_is_a_label_or_placeholder"}


def test_a_street_address_is_not_a_party_and_a_location_key_is_still_lifted():
    """The client's own address was read as a payee, and a building as a customer."""
    records = [
        {
            "document_id": "d1",
            "header": {"payee_name": "4100 NE 20TH AVE HARBOR POINT, FL 33000"},
        },
        {"document_id": "d2", "header": {"customer_name": "801 Bayshore Avenue"}},
        {"document_id": "d3", "header": {"customer_name": "7144 KD Frost"}},
        {"document_id": "d4", "header": {"customer_name": "1900 East"}},
    ]
    mentions, refused = lane.extract_parties(records)
    assert [m["raw_name"] for m in mentions] == ["7144 KD Frost", "1900 East"]
    assert [r["raw_name"] for r in refused] == [
        "4100 NE 20TH AVE HARBOR POINT, FL 33000",
        "801 Bayshore Avenue",
    ]
    assert {r["reason"] for r in refused} == {"party_name_is_a_street_address"}
    assert lane.is_a_street_address("801 Bayshore Avenue")
    assert not lane.is_a_street_address("7144 KD Frost")
    assert not lane.is_a_street_address("")


def test_a_row_names_the_company_fields_the_party_master_refused():
    """Matched on the document, the header or line, the field and the reading.

    A name a party decision removed is matched on the document and the reading.
    """
    refusals = [
        {
            "document_id": "d1",
            "scope": "header",
            "field": "dealer_name",
            "raw_name": "COMPANY",
            "reason": "party_name_is_a_label_or_placeholder",
        },
        {
            "document_id": "d1",
            "scope": "lines[0]",
            "field": "vendor_name",
            "raw_name": "18313",
            "reason": "party_name_carries_no_letter",
        },
        "not an entry",
    ]
    records = [
        {"document_id": "d1", "dealer_name": "COMPANY", "brand_name": "HALVOR"},
        {"document_id": "d1", "line_index": 0, "vendor_name": "18313"},
        # The same text on another line is not what the resolver refused there.
        {"document_id": "d1", "line_index": 1, "vendor_name": "18313"},
        {"document_id": "d1", "line_index": 2, "vendor_name": ""},
        {"document_id": "d2", "line_index": 0, "specifier_name": "Unknown Specifier"},
        # The same name on a document the decision did not cover is not marked.
        {"document_id": "d3", "line_index": 0, "specifier_name": "Unknown Specifier"},
    ]
    removed = [
        {
            "canonical_name": "Unknown Specifier",
            "name_variants": ["Unknown Specifier", ""],
            "source_document_ids": ["d2"],
        },
        {"canonical_name": "Invoiced"},
        "not a party",
    ]
    marked = export.mark_party_fields_not_a_party(records, refusals, removed)
    assert [record["party_fields_not_a_party"] for record in marked] == [
        "dealer_name: party_name_is_a_label_or_placeholder",
        "vendor_name: party_name_carries_no_letter",
        "",
        "",
        "specifier_name: removed_as_not_a_party_by_decision",
        "",
    ]


@pytest.mark.parametrize(
    "reading, problem",
    [
        ("Mara Castell", ""),
        ("JANA K HALVORSEN", ""),
        ("CAROLINA RENNER; JANA HALVORSEN", "names several people"),
        ("150-NGC", "carries a number"),
        ("Sept 97", "carries a number"),
        ("Northgate Co. - Jana Halvorsen", "names the client"),
        ("COMMREP", "a single word"),
        ("INSTALL REP", "names a role or a report"),
        ("Marlow / Bramwell", "names a company"),
        ("Lindew World", "names a company"),
    ],
)
def test_a_person_field_says_when_it_does_not_hold_one_person(reading, problem):
    """A sales representative's field held codes, a footer, a role, a brand and the client."""
    records = [{"document_id": "d1", "sales_representative_name": reading}, {"document_id": "d2"}]
    marked = export.mark_person_fields_not_one_person(
        records, ["Northgate Co"], ["Marlow / Bramwell", "Linden World", ""]
    )
    assert marked[0]["person_fields_not_one_person"] == (
        f"sales_representative_name: {problem}" if problem else ""
    )
    assert marked[1]["person_fields_not_one_person"] == ""


def test_an_email_column_gets_a_usable_address_or_nothing():
    """A website printed where an email goes is carried, never typed as an email."""
    records = [
        {
            "document_id": "d1",
            "contact_email": "mara.castell@norvena.example",
            "sales_representative_email": "www.HALVORfurniture.example",
        },
        {"document_id": "d2"},
    ]
    marked = export.add_email_columns(records)
    assert marked[0]["contact_email__email"] == "mara.castell@norvena.example"
    assert marked[0]["sales_representative_email__email"] == ""
    assert marked[0]["sales_representative_email"] == "www.HALVORfurniture.example"
    assert "contact_email__email" not in marked[1]


def test_every_refusal_is_kept_in_the_master_with_its_reason(tmp_path, monkeypatch):
    """A heading, an address and a number are each kept, counted by the rule that refused them.

    The first version kept only refusals for a missing letter, so the heading and
    address rules refused their readings and left no record of either.
    """
    records = tmp_path / "records.json"
    records.write_text(
        json.dumps(
            [
                {"document_id": "d1", "header": {"dealer_name": "COMPANY"}},
                {"document_id": "d2", "header": {"payee_name": "801 Bayshore Avenue"}},
                {"document_id": "d3", "header": {"brand_name": "7144"}},
                {"document_id": "d4", "header": {"brand_name": "Lumen Weft"}},
            ]
        )
    )
    out, log = tmp_path / "parties.json", tmp_path / "merges.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["entity_resolve.py", str(records), "--out", str(out), "--log", str(log), "--quiet"],
    )
    lane.main()
    written = json.loads(out.read_text())
    assert sorted(entry["reason"] for entry in written["refused_not_a_name"]) == [
        "party_name_carries_no_letter",
        "party_name_is_a_label_or_placeholder",
        "party_name_is_a_street_address",
    ]
    assert written["summary"]["mentions_refused_by_reason"] == {
        "party_name_carries_no_letter": 1,
        "party_name_is_a_label_or_placeholder": 1,
        "party_name_is_a_street_address": 1,
    }
    assert [party["canonical_name"] for party in written["parties"]] == ["Lumen Weft"]
