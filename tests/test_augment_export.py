"""Check the augmentation lane against the shapes this corpus actually prints."""

import collections
import csv
import json

import augment_export as lane
import pytest

CLIENT = ("Northgate Co",)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "13419 Workfields - Ft Meyers, FL",
            {"location_number": "13419", "city": "Ft Meyers", "state": "FL"},
        ),
        ("Workfields Inc - Jacksonville", {"branch": "Jacksonville"}),
        ("Office Quarters Inc - NY", {"state": "NY"}),
        ("Empall Office - FLORIDA", {"state": "FL"}),
        ("CBI - SOUTH FL", {"branch": "SOUTH FL", "state": "FL"}),
        ("Assorted Services - Bermuda Global", {"branch": "Bermuda Global"}),
        ("Acme Systems - COOI", {}),
        ("Acme Systems - Re", {}),
        ("SYSTEMS SUPPLY - LA", {}),
        ("SYSTEMS SUPPLY-LA", {}),
        ("CORPORATE QUARTERS-FL", {"state": "FL"}),
        ("ONE WORKFLOOR (CA)", {"state": "CA"}),
        ("W B PINE / NY", {"state": "NY"}),
        ("MARLOW/BRAMWELL IN", {}),
        ("Acme - Paris, XX", {}),
        ("Acme Holdings", {}),
        ("2200 Bayshore Avenue", {}),
    ],
)
def test_a_name_says_where_a_party_is_only_when_it_says_so_plainly(name, expected):
    """A state set off by punctuation, a city with its state, a branch after a dash.

    `MARLOW/BRAMWELL IN` is `INC` cut short, not Indiana, and a street number is
    part of an address, not a location key.
    """
    assert lane.place_in_name(name) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 310-555-0150 x 4008", [("", "+13105550150;ext=4008")]),
        ("(954) 555-0148", [("", "+19545550148")]),
        (
            "O. 1 310-555-0150 x 4008; D. 1 323-555-0174 x",
            [("office", "+13105550150;ext=4008"), ("direct", "+13235550174")],
        ),
        ("ask reception", []),
    ],
)
def test_phone_numbers_come_back_in_e164_with_their_kind(text, expected):
    """One number per entry, the extension kept, the printed label named."""
    assert lane.phone_numbers(text) == expected


def test_the_engines_type_is_read_whether_or_not_it_is_wrapped():
    """The record artifact keeps the type as a reading with a value, or bare."""
    assert lane.engine_type({"model_document_type": {"value": "commission_report"}}) == (
        "commission_report"
    )
    assert lane.engine_type({"model_document_type": "remittance_advice"}) == "remittance_advice"
    assert lane.engine_type({}) == ""


def test_a_project_a_party_field_holds_is_copied_beside_it_as_an_inference():
    """The flag keeps a project out of the accounts; this gives it a project column."""
    tally = collections.Counter()
    flagged = line(
        "pA",
        description="Argus Glass Fronts",
        dealer_name="Argus Glass Fronts",
        party_field_holds_the_project="dealer_name",
    )
    printed = line("pB", project_name="Tower", party_field_holds_the_project="dealer_name")
    emptied = line("pC", party_field_holds_the_project="customer_name")
    plain = line("pD", dealer_name="OFDC")
    lane.fill_projects_from_party_fields([flagged, printed, emptied, plain], tally)

    assert flagged["project_name__inferred"] == "Argus Glass Fronts"
    assert flagged["project_name__inferred_by"] == "party_field_holds_the_project"
    assert "job or project 12951" in flagged["project_name__inferred_evidence"]
    # The reading is not moved: the party field still says what the page says.
    assert flagged["dealer_name"] == "Argus Glass Fronts"
    # A printed project, an empty party field and an unflagged line fill nothing.
    assert all("project_name__inferred" not in row for row in (printed, emptied, plain))
    assert tally == {("party_field_holds_the_project", "project_name"): 1}


def line(document, **cells):
    """A line row keyed to one recurring job and amount."""
    return {
        "document_id": f"run__{document}",
        "job_number": "12951",
        "commission_amount__amount": "100.00",
        **cells,
    }


def test_every_method_fills_beside_the_cell_and_refuses_what_it_cannot_support():
    """Each method once, and each reason a cell is left alone.

    The same job and amount on three statements: one customer on two of them
    fills the third; two different dealers fill nothing; the client is never
    carried across; a specifier on one statement alone is not enough.
    """
    documents = [
        {
            "document_id": "run__pA",
            "document_type": "unknown",
            "brand_name": "Northgate Co",
            "country_of_destination": "United States of America (the)",
            "contact_phone": "O. 1 310-555-0150 x 4008; D. 1 323-555-0174 x",
            "sales_representative_phone": "",
        },
        {
            "document_id": "run__pB",
            "document_type": "unknown",
            "brand_name": "murbrook",
            "country_of_destination": "Atlantis",
            "contact_phone": "O. 305-555-1212; O. 305-555-1313",
        },
        {
            "document_id": "run__pC",
            "document_type": "purchase_order",
            "brand_name": "",
            "country_of_destination": "US",
            "contact_phone": "ask reception",
            "sales_representative_phone": "507.555.0135",
        },
    ]
    lines = [
        line(
            "pA",
            customer_name="Acme",
            dealer_name="X",
            specifier_name="Z",
            brand_name="Northgate Co",
            description="Desk",
            transaction_date="01/02/2022",
            document_type="",
        ),
        line(
            "pB",
            customer_name="Acme",
            dealer_name="Y",
            brand_name="Northgate Co",
            description="Table",
            transaction_date="01/02/2022",
        ),
        line(
            "pC",
            dealer_name="13419 Workfields - Ft Meyers, FL",
            customer_name="",
            brand_name="",
            description="",
            transaction_date="",
        ),
        {"document_id": "run__pA", "job_number": "", "brand_name": "", "dealer_name": ""},
        {"document_id": "run__pB", "dealer_name": "648 Northgate Co, LLC"},
        {
            "document_id": "run__pB",
            "customer_name": "Univ of Bayside Founders Village - Lighting",
            "party_field_holds_the_project": "customer_name",
        },
        {
            "document_id": "run__pD",
            "job_number": "999",
            "commission_amount__amount": "5.00",
            "customer_name": "Solo",
        },
        {
            "document_id": "run__pE",
            "job_number": "999",
            "commission_amount__amount": "5.00",
            "customer_name": "",
        },
    ]
    engines = {"run__pA": "commission_report", "run__pB": "commission_report"}
    kinds = {"pA": "commission_statement", "pB": "open_order_report"}
    external = (
        {
            lane.loose_name("13419 Workfields - Ft Meyers, FL"): {
                "identity": "Workfields, Inc.",
                "city": "Orlando",
                "state": "FL",
                "website": "workfields.com",
                "source": "https://www.workfields.com/locations/orlando-fl",
                "confidence": "confirmed",
            }
        },
        "2026-09-11",
    )
    summary = lane.augment(lines, documents, engines, kinds, external, CLIENT)

    first, second, third = documents
    assert first["document_type__inferred"] == "commission_statement"
    assert first["document_type__inferred_by"] == "two_vendors_agree"
    assert "document_type__inferred" not in second
    assert lines[0]["document_type__inferred"] == "commission_statement"
    assert lines[3]["document_type__inferred"] == "commission_statement"
    assert "document_type__inferred" not in lines[1]

    assert lines[2]["customer_name__inferred"] == "Acme"
    assert lines[2]["customer_name__inferred_evidence"].startswith("2 statements")
    assert "description__inferred" not in lines[2]
    assert "transaction_date__inferred" not in lines[2]
    assert "brand_name__inferred" not in lines[2]
    assert "specifier_name__inferred" not in lines[1]
    assert "customer_name__inferred" not in lines[7]
    assert summary["left_empty_where_statements_disagree"] == 1

    assert "brand_name__inferred" not in lines[3]
    assert lines[2]["dealer_name_location_number__inferred"] == "13419"
    assert lines[2]["dealer_name_state__inferred"] == "FL"
    assert "dealer_name_location_number__inferred" not in lines[4]
    assert "customer_name_branch__inferred" not in lines[5]

    assert first["country_of_destination__inferred"] == "US"
    assert summary["countries_left_unmapped"] == {"Atlantis": 1}
    assert "country_of_destination__inferred" not in third

    assert first["contact_phone_office__inferred"] == "+13105550150;ext=4008"
    assert first["contact_phone_direct__inferred"] == "+13235550174"
    assert second["contact_phone_office_2__inferred"] == "+13055551313"
    assert third["sales_representative_phone__inferred"] == "+15075550135"
    assert summary["phones_left_unparsed"] == 1

    assert lines[2]["dealer_name_identity__inferred"] == (
        "Workfields, Inc. · Orlando, FL · workfields.com"
    )
    assert "confirmed, retrieved 2026-09-11" in lines[2]["dealer_name_identity__inferred_evidence"]
    assert summary["cells_inferred"] == sum(summary["by_method"].values())


def test_a_line_takes_its_documents_brand_but_never_the_clients():
    """The letterhead speaks for every line of its document, except as the client."""
    documents = [
        {"document_id": "run__p1", "brand_name": "murbrook"},
        {"document_id": "run__p2", "brand_name": "Northgate Co LLC"},
    ]
    lines = [
        {"document_id": "run__p1", "brand_name": ""},
        {"document_id": "run__p1", "brand_name": "HALVOR"},
        {"document_id": "run__p2", "brand_name": ""},
        {"document_id": "run__p9", "brand_name": ""},
    ]
    lane.augment(lines, documents, {}, {}, ({}, ""), CLIENT)
    assert lines[0]["brand_name__inferred"] == "murbrook"
    assert lines[0]["brand_name__inferred_by"] == "document_letterhead"
    assert "brand_name__inferred" not in lines[1]
    assert "brand_name__inferred" not in lines[2]
    assert "brand_name__inferred" not in lines[3]


def test_the_first_method_to_fill_a_cell_keeps_it():
    """The document's own letterhead outranks what other statements carry."""
    documents = [{"document_id": f"run__p{i}", "brand_name": "murbrook"} for i in range(3)]
    lines = [line(f"p{i}", brand_name="HALVOR") for i in range(2)] + [line("p2", brand_name="")]
    lane.augment(lines, documents, {}, {}, ({}, ""), CLIENT)
    assert lines[2]["brand_name__inferred"] == "murbrook"


def test_a_carried_party_brings_the_key_every_carrier_holds():
    """The same job on three statements: the customer arrives with its account's key.

    One name under two keys is carried as a name and joins no account.
    """
    lines = [
        line("pA", customer_name="Acme", customer_name__party_key="PTY-9"),
        line("pB", customer_name="Acme", customer_name__party_key="PTY-9"),
        line("pC", customer_name=""),
        line("pD", job_number="77777", dealer_name="Beta", dealer_name__party_key="PTY-1"),
        line("pE", job_number="77777", dealer_name="Beta", dealer_name__party_key="PTY-2"),
        line("pF", job_number="77777", dealer_name=""),
    ]
    lane.augment(lines, [], {}, {}, ({}, ""), CLIENT)
    assert lines[2]["customer_name__inferred"] == "Acme"
    assert lines[2]["customer_name__inferred_party_key"] == "PTY-9"
    assert lines[5]["dealer_name__inferred"] == "Beta"
    assert "dealer_name__inferred_party_key" not in lines[5]


def keyed(document, maker="Acme Seating", **cells):
    """A line of one maker, its party fields keyed as the master keys a reading."""
    return {"document_id": f"run__{document}", "brand_name": maker, **cells}


def big_corp(document, **cells):
    """A line printing Big Corp as its dealer, resolved and keyed."""
    return keyed(
        document,
        dealer_name="Big Corp",
        dealer_name__resolved_party="Big Corp",
        dealer_name__party_key="PTY-1",
        **cells,
    )


def test_a_shared_key_names_a_party_only_where_every_key_agrees_and_it_scores():
    """Lines of one maker printing one job name one dealer; a line missing it takes it.

    Scored first: each line printing its party is hidden and asked for, and
    comes back right. A line whose two keys name different parties takes
    neither, another maker's line takes nothing, the client never speaks for a
    line, and a placeholder is no key.
    """
    lines = [
        big_corp("p1", job_number="J1001"),
        big_corp("p2", job_number="J1001"),
        big_corp("p2", job_number="J1001", purchase_order_number="PO5555"),
        keyed("p3", job_number="J1001", dealer_name=""),
        keyed(
            "p4",
            purchase_order_number="PO7777",
            dealer_name="Other Co",
            dealer_name__party_key="PTY-4",
        ),
        keyed("p5", job_number="J1001", purchase_order_number="PO7777", dealer_name=""),
        keyed("p6", maker="Another Maker", job_number="J1001", dealer_name=""),
        keyed(
            "p7",
            job_number="J8888",
            dealer_name="Northgate Co",
            dealer_name__party_key="PTY-C",
            fields_naming_the_client="dealer_name",
        ),
        keyed("p8", job_number="J8888", dealer_name=""),
        keyed("p9", job_number="N/A", dealer_name=""),
    ]
    summary = lane.augment(lines, [], {}, {}, ({}, ""), CLIENT)
    filled = lines[3]
    assert filled["dealer_name__inferred"] == "Big Corp"
    assert filled["dealer_name__inferred_by"] == "same_key_on_other_lines"
    assert filled["dealer_name__inferred_party_key"] == "PTY-1"
    assert filled["dealer_name__inferred_evidence"].startswith(
        "job_number J1001 names this party on 3 lines of 2 documents"
    )
    for row in lines[5:]:
        assert "dealer_name__inferred" not in row
    score = summary["parties_from_shared_keys"]["dealer_name"]
    assert (score["held_out"], score["proposed"], score["accuracy"]) == (4, 3, 1.0)
    assert score["filled"] == 1
    assert score["left_empty_where_keys_disagree"] == 1
    assert summary["parties_from_shared_keys"]["customer_name"]["accuracy"] is None


def test_a_shared_key_scoring_below_the_bar_fills_nothing():
    """One job naming two dealers: hidden, each line names the other, and is wrong."""
    lines = [
        keyed("p1", job_number="J3003", dealer_name="Alpha", dealer_name__party_key="PTY-5"),
        keyed("p2", job_number="J3003", dealer_name="Omega", dealer_name__party_key="PTY-6"),
        keyed("p3", job_number="J4004", dealer_name="Solo", dealer_name__party_key="PTY-7"),
        keyed("p4", job_number="J4004", dealer_name="Solo", dealer_name__party_key="PTY-7"),
        keyed("p5", job_number="J4004", dealer_name=""),
    ]
    summary = lane.augment(lines, [], {}, {}, ({}, ""), CLIENT)
    score = summary["parties_from_shared_keys"]["dealer_name"]
    assert (score["proposed"], score["correct"], score["accuracy"]) == (4, 2, 0.5)
    assert score["chance_baseline"] == 0.5
    assert score["filled"] == 0
    assert "dealer_name__inferred" not in lines[4]


def test_a_key_whose_lines_name_two_parties_proposes_nothing():
    """Two dealers under one job: hidden, each line sees both, and names neither."""
    lines = [
        keyed("p1", job_number="J5005", dealer_name="Alpha", dealer_name__party_key="PTY-8"),
        keyed("p2", job_number="J5005", dealer_name="Alpha", dealer_name__party_key="PTY-8"),
        keyed("p3", job_number="J5005", dealer_name="Omega", dealer_name__party_key="PTY-9"),
        keyed("p4", job_number="J5005", dealer_name="Omega", dealer_name__party_key="PTY-9"),
    ]
    summary = lane.augment(lines, [], {}, {}, ({}, ""), CLIENT)
    score = summary["parties_from_shared_keys"]["dealer_name"]
    assert (score["held_out"], score["proposed"], score["accuracy"]) == (4, 0, None)


def test_the_report_gives_each_fields_score_beside_its_chance(capsys):
    """A scored field prints what it filled and how it scored; an unscored one says so."""
    lane.report(
        {
            "cells_inferred": 0,
            "by_method": {},
            "left_empty_where_statements_disagree": 0,
            "parties_from_shared_keys": {
                "customer_name": {"accuracy": None},
                "dealer_name": {
                    "accuracy": 0.96,
                    "proposed": 1745,
                    "chance_baseline": 0.303,
                    "filled": 788,
                },
            },
        }
    )
    out = capsys.readouterr().out
    assert "customer_name from a shared key: unscored, nothing filled" in out
    assert (
        "dealer_name from a shared key: 788 filled; held out, 96.0% of 1745 against 30.3% by chance"
    ) in out


def test_inputs_that_are_absent_or_unreadable_are_empty_not_fatal(tmp_path):
    """No record artifact, no review directory, no public-source file: nothing to add."""
    assert lane.engine_types_from("") == {}
    assert lane.review_kinds_from("") == {}
    assert lane.load_external("") == ({}, "")
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    (reviews / "p1.json").write_text(json.dumps({"page": "p1", "document_kind": "remittance"}))
    (reviews / "p2.json").write_text("{broken")
    assert lane.review_kinds_from(str(reviews)) == {"p1": "remittance"}
    records = tmp_path / "records.json"
    records.write_text(json.dumps({"documents": [{"document_id": "run__p1"}]}))
    assert lane.engine_types_from(str(records)) == {"run__p1": ""}
    external = tmp_path / "external.json"
    external.write_text(
        json.dumps({"retrieved": "2026-09-11", "parties": [{"names": ["DAX Tampa"]}]})
    )
    by_name, retrieved = lane.load_external(str(external))
    assert lane.loose_name("DAX Tampa") in by_name
    assert retrieved == "2026-09-11"


def write_csv(path, rows):
    """Write rows with the union of their columns."""
    columns = list(dict.fromkeys(name for row in rows for name in row))
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_main_writes_new_files_and_never_overwrites(tmp_path, capsys):
    """New grains beside the old, a summary, and a refusal to clobber either."""
    lines_in, documents_in = tmp_path / "lines.csv", tmp_path / "documents.csv"
    write_csv(lines_in, [{"document_id": "run__p1", "brand_name": ""}])
    write_csv(documents_in, [{"document_id": "run__p1", "brand_name": "murbrook"}])
    out = tmp_path / "out"
    args = [
        "--lines-csv",
        str(lines_in),
        "--documents-csv",
        str(documents_in),
        "--out-lines",
        str(out / "lines.csv"),
        "--out-documents",
        str(out / "documents.csv"),
        "--out",
        str(out / "summary.json"),
        "--client-name",
        "Northgate Co",
    ]
    assert lane.main(args) == 0
    assert "cells inferred: 1" in capsys.readouterr().out
    written = list(csv.DictReader(open(out / "lines.csv", encoding="utf-8")))
    assert written[0]["brand_name__inferred"] == "murbrook"
    assert json.loads((out / "summary.json").read_text())["inputs"]["client_names"] == [
        "Northgate Co"
    ]
    with pytest.raises(SystemExit) as exc:
        lane.main(args)
    assert "Augmentation failed" in str(exc.value)


def test_main_stays_quiet_when_asked(tmp_path, capsys):
    """`--quiet` writes the files and prints nothing."""
    lines_in, documents_in = tmp_path / "lines.csv", tmp_path / "documents.csv"
    write_csv(lines_in, [{"document_id": "run__p1"}])
    write_csv(documents_in, [{"document_id": "run__p1"}])
    lane.main(
        [
            "--lines-csv",
            str(lines_in),
            "--documents-csv",
            str(documents_in),
            "--out-lines",
            str(tmp_path / "a.csv"),
            "--out-documents",
            str(tmp_path / "b.csv"),
            "--out",
            str(tmp_path / "c.json"),
            "--quiet",
        ]
    )
    assert capsys.readouterr().out == ""


def test_a_fact_retrieved_later_carries_its_own_date():
    """murbrook was confirmed a day after the public-source file was compiled."""
    rows = [{"document_id": "run__p1", "brand_name": "murbrook"}]
    by_name = {
        lane.loose_name("murbrook"): {
            "identity": "Murbrook",
            "source": "https://murbrook.com/en/contact-us",
            "confidence": "confirmed",
            "retrieved": "2026-09-12",
        }
    }
    lane.fill_identities(rows, by_name, "2026-09-11", collections.Counter())
    assert rows[0]["brand_name_identity__inferred_evidence"].endswith(
        "(confirmed, retrieved 2026-09-12)"
    )


def master_party(key, name, variants=(), **extra):
    """A party as the master writes it."""
    return {
        "party_key": key,
        "canonical_name": name,
        "normalized_name": lane.normalize_name(name),
        "name_variants": [name, *variants],
        **extra,
    }


def test_accounts_come_from_resolved_parties_never_from_a_field_holding_none():
    """The client's fields, a job, a refused reading and an unknown key make no account."""
    parties = [
        master_party(
            "PTY-1", "murbrook", ["murb rook"], addresses=["1253 Rue Dickson"], needs_review=True
        ),
        master_party(
            "PTY-2",
            "Office Quarters Inc - NY",
            parent_party_key="PTY-9",
            parent_name="Office Quarters Inc",
        ),
        master_party("PTY-3", "Univ of Bayside Founders Village"),
    ]
    documents = [
        {
            "document_id": "run__p1",
            "brand_name__party_key": "PTY-1",
            "statement_date__iso": "2023-01-31",
            "brand_name_identity__inferred": "Murbrook · Montréal, QC · murbrook.com",
            "brand_name_identity__inferred_evidence": "https://murbrook.com (confirmed)",
        },
        {
            "document_id": "run__p2",
            "brand_name__party_key": "PTY-1",
            "document_date__iso": "2022-05-01",
            "dealer_name__party_key": "PTY-2",
            "fields_naming_the_client": "dealer_name",
        },
    ]
    lines = [
        {"document_id": "run__p1", "dealer_name__party_key": "PTY-2"},
        {
            "document_id": "run__p1",
            "customer_name__party_key": "PTY-3",
            "party_field_holds_the_project": "customer_name",
        },
        {
            "document_id": "run__p2",
            "specifier_name__party_key": "PTY-2",
            "party_fields_not_a_party": "specifier_name: removed_as_not_a_party_by_decision",
        },
        {"document_id": "run__p3", "dealer_name__party_key": "PTY-404"},
    ]
    by_name = {
        lane.loose_name("murbrook"): {
            "phone": "+14505550132",
            "source": "https://murbrook.com/en/contact-us",
        }
    }
    murbrook, branch = lane.account_rows(lines, documents, parties, by_name)
    assert (murbrook["party_key"], branch["party_key"]) == ("PTY-1", "PTY-2")
    assert (murbrook["rows_naming_it"], murbrook["source_document_count"]) == (2, 2)
    assert (murbrook["first_document_date"], murbrook["last_document_date"]) == (
        "2022-05-01",
        "2023-01-31",
    )
    assert murbrook["identity"].startswith("Murbrook")
    assert murbrook["identity_evidence"] == "https://murbrook.com (confirmed)"
    assert murbrook["phone"] == "+14505550132"
    assert murbrook["phone_evidence"] == "https://murbrook.com/en/contact-us"
    assert murbrook["name_variants"] == "murb rook; murbrook"
    assert (murbrook["roles"], murbrook["needs_review"]) == ("brand", "yes")
    assert (branch["parent_party_key"], branch["roles"]) == ("PTY-9", "dealer")
    assert (branch["phone"], branch["identity"], branch["needs_review"]) == ("", "", "")


def test_contacts_join_one_person_on_evidence_and_take_a_company_only_from_an_email():
    """Jana's three spellings are one contact, and murbrook's main line is not hers."""
    parties = [
        master_party("PTY-1", "NORVENA LLC", ["norvena"]),
        master_party("PTY-2", "murbrook"),
        master_party("PTY-3", "Norvena Studio"),
        master_party("PTY-4", "CBI"),
    ]

    def page(number, **cells):
        return {"document_id": f"run__{number}", **cells}

    e164 = "e164"
    documents = [
        page(
            "p128",
            brand_name__party_key="PTY-1",
            contact_name="Mara Castell",
            contact_email__email="mara.castell@norvena.example",
            contact_phone_office__inferred="+13105550150;ext=4008",
            contact_phone_office__inferred_by=e164,
            contact_phone_direct__inferred="+13235550174;ext=4008",
            contact_phone_direct__inferred_by=e164,
        ),
        page(
            "p131",
            brand_name__party_key="PTY-1",
            contact_name="Mara Castell",
            contact_email__email="mara.castell@norvena.example",
            contact_phone__inferred="+13105550150;ext=4008",
            contact_phone__inferred_by=e164,
        ),
        page(
            "p573",
            brand_name__party_key="PTY-2",
            contact_name="Jana Halvorsen",
            contact_phone__inferred="+14505550132",
            contact_phone__inferred_by=e164,
        ),
        page("p088", contact_name="JANA K HALVORSEN"),
        page(
            "p645",
            contact_name="Jana Holvorsen",
            contact_email__email="jana@northgate.example",
            contact_phone__inferred="+19545550148",
            contact_phone__inferred_by=e164,
        ),
        page("p647", contact_name="Jana Halvorsen", contact_email__email="jana@northgate.example"),
        page("p648", contact_name="Bria O'Donnell", contact_email__email="jana@northgate.example"),
        page("p634", sales_representative_name="CAROLINA RENNER"),
        page(
            "p146",
            sales_representative_name="150-NGC",
            person_fields_not_one_person="sales_representative_name: carries a number",
        ),
        page(
            "p900",
            brand_name__party_key="PTY-3",
            dealer_name__party_key="PTY-4",
            contact_name="Greta Bellamy",
            contact_email__email="greta@norvena.example",
        ),
        page(
            "p901",
            brand_name__party_key="PTY-404",
            contact_name="Kurt Hayward",
            contact_email__email="kurt.hayward@halvorfurniture.example",
        ),
        page(
            "p902",
            brand_name__party_key="PTY-1",
            contact_name="Dana Pemberton",
            contact_email__email="dana.pemberton@norvena.example",
        ),
        page(
            "p903",
            brand_name__party_key="PTY-2",
            contact_name="Dana Pemberton",
            contact_email__email="dana@murbrook.example",
        ),
    ]
    contacts, readings = lane.contact_rows(documents, parties, ["Northgate Co"])
    assert readings == 12
    by_name = {contact["name"]: contact for contact in contacts}
    jana = by_name["Jana Halvorsen"]
    assert jana["contact_key"] == "CON-0005"
    assert jana["name_variants"] == "JANA K HALVORSEN; Jana Halvorsen; Jana Holvorsen"
    assert jana["merged_on"] == "a middle initial; the same email"
    assert (jana["company"], jana["belongs_to_the_client"], jana["party_key"]) == (
        "Northgate Co",
        "yes",
        "",
    )
    assert jana["company_evidence"] == "the email domain northgate.example names Northgate Co"
    assert (jana["phone"], jana["other_phones"]) == ("+19545550148", "")
    assert jana["source_document_count"] == 4
    mara = by_name["Mara Castell"]
    assert (mara["party_key"], mara["company"]) == ("PTY-1", "NORVENA LLC")
    assert mara["phone"] == "+13105550150;ext=4008"
    assert mara["other_phones"] == "+13235550174;ext=4008"
    assert (mara["email"], mara["company_unproven"]) == ("mara.castell@norvena.example", "")
    bria = by_name["Bria O'Donnell"]
    assert (bria["email"], bria["phone"]) == ("", "")
    assert bria["company_unproven"].startswith("no email naming this person")
    assert "who the rep-field people are" in by_name["CAROLINA RENNER"]["company_unproven"]
    assert by_name["Greta Bellamy"]["company_unproven"].startswith("its email's domain names none")
    assert by_name["Kurt Hayward"]["company"] == ""
    dana = by_name["Dana Pemberton"]
    assert dana["company_unproven"] == "its emails name different companies"
    assert dana["other_emails"] == "dana@murbrook.example"
    assert "150-NGC" not in by_name


def test_main_writes_accounts_and_contacts_only_from_a_party_master(tmp_path, capsys):
    """Asked for without a master it refuses; with one it writes what was asked."""
    lines_in, documents_in = tmp_path / "lines.csv", tmp_path / "documents.csv"
    write_csv(lines_in, [{"document_id": "run__p1", "dealer_name__party_key": "PTY-1"}])
    write_csv(
        documents_in,
        [
            {
                "document_id": "run__p1",
                "brand_name__party_key": "PTY-2",
                "contact_name": "Kurt Hayward",
                "contact_email__email": "kurt.hayward@halvorfurniture.example",
            }
        ],
    )
    master = tmp_path / "parties.json"
    master.write_text(
        json.dumps(
            {"parties": [master_party("PTY-1", "KD Frost"), master_party("PTY-2", "HALVOR")]}
        )
    )
    base = ["--lines-csv", str(lines_in), "--documents-csv", str(documents_in)]

    def outputs(name):
        return [
            "--out-lines",
            str(tmp_path / f"{name}_lines.csv"),
            "--out-documents",
            str(tmp_path / f"{name}_documents.csv"),
            "--out",
            str(tmp_path / f"{name}.json"),
        ]

    with pytest.raises(SystemExit) as exc:
        lane.main([*base, *outputs("a"), "--out-accounts", str(tmp_path / "a_accounts.csv")])
    assert "need --parties" in str(exc.value)
    assert not (tmp_path / "a_lines.csv").exists()
    both = ["--out-accounts", str(tmp_path / "b_accounts.csv")]
    both += ["--out-contacts", str(tmp_path / "b_contacts.csv")]
    assert lane.main([*base, *outputs("b"), "--parties", str(master), *both]) == 0
    printed = capsys.readouterr().out
    assert "accounts: 2; contacts: 1 from 1 readings, 1 with a company" in printed
    assert "party keys the master does not hold" not in printed
    accounts = list(csv.DictReader(open(tmp_path / "b_accounts.csv", encoding="utf-8")))
    contacts = list(csv.DictReader(open(tmp_path / "b_contacts.csv", encoding="utf-8")))
    assert [account["canonical_name"] for account in accounts] == ["KD Frost", "HALVOR"]
    assert (contacts[0]["name"], contacts[0]["company"]) == ("Kurt Hayward", "HALVOR")
    summary = json.loads((tmp_path / "b.json").read_text())
    assert (summary["inputs"]["parties"], summary["accounts"]) == (str(master), 2)
    only = ["--parties", str(master), "--out-accounts", str(tmp_path / "c_accounts.csv")]
    lane.main([*base, *outputs("c"), *only, "--quiet"])
    assert (tmp_path / "c_accounts.csv").exists()
    assert not (tmp_path / "c_contacts.csv").exists()
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"parties": None}))
    with pytest.raises(SystemExit) as exc:
        lane.main([*base, *outputs("d"), "--parties", str(broken)])
    assert "carries no parties list" in str(exc.value)
    assert not (tmp_path / "d_lines.csv").exists()
    # A master other than the one the export resolved against joins nothing,
    # and says so rather than writing an empty accounts file as if it were one.
    stale = tmp_path / "stale_lines.csv"
    write_csv(stale, [{"document_id": "run__p1", "customer_name__party_key": "PTY-404"}])
    stale_base = ["--lines-csv", str(stale), "--documents-csv", str(documents_in)]
    assert lane.main([*stale_base, *outputs("e"), "--parties", str(master)]) == 0
    assert "party keys the master does not hold: 1" in capsys.readouterr().out
    assert json.loads((tmp_path / "e.json").read_text())["party_keys_not_in_the_master"] == 1
