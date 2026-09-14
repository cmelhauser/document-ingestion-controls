"""Tests for the shared approved-fact CRM API/MCP service layer."""

import importlib
import sqlite3
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

crm = importlib.import_module("crm_service")
store = importlib.import_module("retrieval_store")


def canonical_export():
    batch = "batch-crm"

    def managed(**row):
        return {**row, "batch_id": batch, "review_status": "exception_resolved"}

    return {
        "batch_id": batch,
        "registry_version": "registry-crm-v1",
        "tables": {
            "document": [
                managed(
                    document_id="doc-1",
                    source_file="source.pdf",
                    source_page_range="1-2",
                    source_sha256="source-hash",
                )
            ],
            "party": [
                managed(
                    party_key="vendor-1",
                    natural_key="vendor",
                    canonical_name="Vendor LLC",
                    normalized_name="vendor llc",
                    custom_crm_segment="strategic",
                ),
                managed(
                    party_key="customer-1",
                    natural_key="customer",
                    canonical_name="Customer Inc",
                    normalized_name="customer inc",
                ),
            ],
            "party_role": [
                {"party_key": "vendor-1", "role": "biller"},
                {"party_key": "customer-1", "role": "payer"},
            ],
            "party_name_variant": [
                {"party_key": "customer-1", "raw_name": "Customer Incorporated"}
            ],
            "address": [
                managed(
                    address_key="address-1",
                    party_key="customer-1",
                    raw_address="1 Main St",
                    source_document_id="doc-1",
                )
            ],
            "contact": [
                managed(
                    contact_key="contact-1",
                    party_key="customer-1",
                    name="Sample Contact",
                    source_document_id="doc-1",
                )
            ],
            "selling_location": [
                managed(
                    selling_location_key="location-1",
                    natural_key="boston",
                    location_name="Boston Office",
                    state_province="MA",
                    territory_name="Northeast",
                )
            ],
            "invoice_header": [
                managed(
                    invoice_key="invoice-1",
                    invoice_number="INV-1",
                    invoice_date="2026-01-15",
                    due_date="2026-02-14",
                    biller_party_key="vendor-1",
                    payer_party_key="customer-1",
                    currency_code="USD",
                    total_amount="125.00",
                    amount_due="25.00",
                    ack_number="ACK-1",
                    job_number="JOB-1",
                    selling_location_key="location-1",
                    source_document_id="doc-1",
                )
            ],
            "invoice_line": [
                managed(
                    invoice_line_key="line-1",
                    invoice_key="invoice-1",
                    line_number=1,
                    description="Service",
                    extended_amount="125.00",
                )
            ],
            "attribution": [
                managed(
                    attribution_id="attr-1",
                    target_table="invoice_header",
                    target_key="invoice-1",
                    amount="100.00",
                    reason_code="approved_partial",
                    source_document_id="doc-1",
                )
            ],
            "payment": [
                managed(
                    payment_key="payment-1",
                    natural_key="PAY-1",
                    payer_party_key="customer-1",
                    payee_party_key="vendor-1",
                    total_paid="100.00",
                    source_document_id="doc-1",
                )
            ],
            "shipment": [
                managed(
                    shipment_key="shipment-1",
                    natural_key="BOL-1",
                    ship_date="2026-01-10",
                    committed_date="2026-01-12",
                    delivery_date="2026-01-13",
                    source_document_id="doc-1",
                ),
                managed(
                    shipment_key="shipment-2",
                    natural_key="BOL-2",
                    ship_date="2026-01-20",
                    source_document_id="doc-1",
                ),
            ],
        },
    }


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "crm.sqlite"
    store.build_database(canonical_export(), path)
    return path


def test_capabilities_summary_schema_and_checksummed_export(database):
    capabilities = crm.capabilities(database)
    assert capabilities["read_only"] and not capabilities["network_upload_permitted"]
    assert set(capabilities["standard_reports"]) == {item["name"] for item in crm.REPORT_CATALOG}
    summary = crm.export_summary(database)
    assert summary["batch_id"] == "batch-crm"
    assert summary["total_records"] == 15 and summary["tables"]["document"] == 1
    assert summary["tables_available"] == 12 and not summary["empty"]

    schema = crm.schema(database)
    party = next(item for item in schema["tables"] if item["table"] == "party")
    assert party["idempotency_keys"] == ["party_key"]
    assert party["observed_extension_columns"] == ["custom_crm_segment"]
    one = crm.schema(database, "invoice_header")
    assert one["tables"][0]["records"] == 1

    page = crm.export_table(database, "party", limit=1, filters={"custom_crm_segment": "strategic"})
    assert page["returned_records"] == 1 and page["rows"][0]["party_key"] == "vendor-1"
    assert len(page["page_sha256"]) == 64 and page["next_offset"] is None
    first = crm.export_table(database, "party", limit=1)
    second = crm.export_table(database, "party", limit=1, offset=1)
    assert first["next_offset"] == 1 and second["next_offset"] is None
    assert crm.report_catalog()["reports"][0]["name"] == "account_directory"


def test_search_exact_lookup_flexible_query_account_card_and_sales_analysis(database):
    search = crm.search_records(database, "Customer Incorporated")
    assert search["results"][0]["table"] == "party_name_variant"
    punctuation = crm.search_records(database, "!!!")
    assert punctuation["results"] == [] and len(punctuation["search_sha256"]) == 64
    assert (
        crm.get_record(database, "party", "customer-1")["record"]["canonical_name"]
        == "Customer Inc"
    )
    composite = crm.get_record(database, "party_role", ["customer-1", "payer"])
    assert composite["record"]["role"] == "payer"

    query = crm.query_records(
        database,
        "party",
        filters={"canonical_name": {"operator": "contains", "value": "customer"}},
        select=["canonical_name"],
        sort=["-canonical_name"],
        limit=1,
    )
    assert query["rows"] == [{"party_key": "customer-1", "canonical_name": "Customer Inc"}]
    assert len(query["query_sha256"]) == 64
    assert (
        crm.query_records(
            database,
            "invoice_header",
            filters={
                "total_amount": {"operator": "gte", "value": "100"},
                "amount_due": {"operator": "lte", "value": "25"},
                "invoice_number": {"operator": "starts_with", "value": "INV"},
                "currency_code": {"operator": "in", "value": ["USD", "CAD"]},
                "job_number": {"operator": "ne", "value": "OTHER"},
                "po_number": {"operator": "is_null", "value": True},
            },
        )["total_records"]
        == 1
    )

    card = crm.account_card(database, party_key="customer-1")
    assert card["addresses"][0]["raw_address"] == "1 Main St"
    assert card["contacts"][0]["name"] == "Sample Contact"
    assert card["summary"] == {
        "invoice_count": 1,
        "invoice_value": "125.00",
        "amount_due": "25.00",
        "payment_count": 1,
        "payment_value": "100.00",
        "invoice_value_by_currency": {"USD": "125.00"},
        "amount_due_by_currency": {"USD": "25.00"},
        "payment_value_by_currency": {"Unassigned": "100.00"},
        "cross_currency_totals_suppressed": False,
    }
    assert crm.account_card(database, name="customer inc")["party"]["party_key"] == "customer-1"

    sales = crm.analyze_sales(
        database,
        ["company", "region", "quarter"],
        {
            "start_date": "2026-01-01",
            "end_date": "2026-03-31",
            "company": "Customer Inc",
            "region": "Northeast",
            "currency": "USD",
            "minimum_total": "100",
            "maximum_total": "200",
        },
    )
    assert sales["rows"] == [
        {
            "company": "Customer Inc",
            "region": "Northeast",
            "quarter": "2026-Q1",
            "currency": "USD",
            "invoice_count": 1,
            "total_amount": "125.00",
            "amount_due": "25.00",
            "average_invoice_amount": "125.00",
            "source_document_ids": ["doc-1"],
        }
    ]


@pytest.mark.parametrize(
    ("call", "message"),
    (
        (lambda db: crm.schema(db, "bad"), "Unsupported"),
        (lambda db: crm.export_table(db, "bad"), "Unsupported"),
        (lambda db: crm.export_table(db, "party", limit=0), "limit"),
        (lambda db: crm.export_table(db, "party", offset=-1), "offset"),
        (lambda db: crm.export_table(db, "party", filters=[]), "filters"),
        (
            lambda db: crm.export_table(db, "party", filters={str(i): i for i in range(11)}),
            "filters",
        ),
        (lambda db: crm.export_table(db, "party", filters={"missing": "x"}), "filter column"),
        (lambda db: crm.export_table(db, "party", filters={"party_key": []}), "scalar"),
        (lambda db: crm.search_records(db, ""), "non-empty"),
        (lambda db: crm.search_records(db, "x", tables=[]), "non-empty"),
        (lambda db: crm.search_records(db, "x", tables=["bad"]), "unsupported"),
        (lambda db: crm.search_records(db, "x", limit=0), "limit"),
        (lambda db: crm.get_record(db, "party", "missing"), "not found"),
        (lambda db: crm.get_record(db, "party_role", "one"), "2 non-empty"),
        (lambda db: crm.query_records(db, "party", filters=[]), "filters"),
        (lambda db: crm.query_records(db, "party", select="name"), "select"),
        (lambda db: crm.query_records(db, "party", sort=["a", "b", "c", "d"]), "sort"),
        (lambda db: crm.query_records(db, "party", select=["missing"]), "Unsupported fields"),
        (
            lambda db: crm.query_records(
                db, "party", filters={"party_key": {"operator": "bad", "value": "x"}}
            ),
            "operator",
        ),
        (
            lambda db: crm.query_records(
                db, "party", filters={"party_key": {"operator": "in", "value": "x"}}
            ),
            "list",
        ),
        (
            lambda db: crm.query_records(
                db, "party", filters={"party_key": {"operator": "is_null", "value": "x"}}
            ),
            "Boolean",
        ),
        (lambda db: crm.account_card(db), "exactly one"),
        (lambda db: crm.account_card(db, party_key=" "), "non-empty"),
        (lambda db: crm.account_card(db, party_key="x"), "not found"),
        (lambda db: crm.analyze_sales(db, ["bad"]), "group_by"),
        (lambda db: crm.analyze_sales(db, filters=[]), "must be an object"),
        (lambda db: crm.analyze_sales(db, filters={"bad": "x"}), "Unsupported sales"),
        (
            lambda db: crm.analyze_sales(db, filters={"company": ["Customer Inc"]}),
            "must be scalar",
        ),
        (
            lambda db: crm.analyze_sales(
                db, filters={"start_date": "2026-02-01", "end_date": "2026-01-01"}
            ),
            "must not precede",
        ),
        (
            lambda db: crm.analyze_sales(
                db, filters={"minimum_total": "200", "maximum_total": "100"}
            ),
            "maximum_total",
        ),
    ),
)
def test_schema_and_export_validation(database, call, message):
    with pytest.raises(ValueError, match=message):
        call(database)


def test_all_standard_reports_are_source_bound_and_deterministic(database):
    accounts = crm.run_report(database, "account_directory")
    customer = next(row for row in accounts["rows"] if row["party_key"] == "customer-1")
    assert customer["roles"] == ["payer"] and customer["address_count"] == 1

    register = crm.run_report(
        database,
        "invoice_register",
        {"start_date": "2026-01-01", "end_date": "2026-01-31"},
    )
    assert register["rows"][0]["source_sha256"] == "source-hash"
    assert register["rows"][0]["line_count"] == 1

    aging = crm.run_report(database, "receivables_aging", {"as_of": "2026-03-31"})
    assert aging["rows"][0]["aging_bucket"] == "31-60"
    assert aging["rows"][0]["days_past_due"] == 45

    customer_sales = crm.run_report(database, "sales_by_customer")
    assert customer_sales["rows"][0]["payer"] == "Customer Inc"
    assert customer_sales["rows"][0]["total_amount"] == "125.00"

    location_sales = crm.run_report(database, "sales_by_selling_location")
    assert location_sales["rows"][0]["selling_location"] == "Boston Office"

    coverage = crm.run_report(database, "attribution_coverage")
    assert coverage["rows"][0]["coverage_percent"] == "80.0"
    assert coverage["rows"][0]["unattributed_value"] == "25.00"

    shipment = crm.run_report(database, "shipment_performance")
    assert [row["delivery_status"] for row in shipment["rows"]] == ["late", "not_measurable"]
    assert all(
        len(result["report_sha256"]) == 64
        for result in (
            accounts,
            register,
            aging,
            customer_sales,
            location_sales,
            coverage,
            shipment,
        )
    )


@pytest.mark.parametrize(
    ("name", "parameters", "message"),
    (
        ("unknown", {}, "Unsupported CRM report"),
        ("account_directory", [], "parameters must be an object"),
        ("account_directory", {"extra": "x"}, "Unsupported report parameters"),
        ("attribution_coverage", {"extra": "x"}, "Unsupported report parameters"),
        ("invoice_register", {"extra": "x"}, "Unsupported report parameters"),
        ("invoice_register", {"start_date": "bad"}, "ISO date"),
        (
            "invoice_register",
            {"start_date": "2026-02-01", "end_date": "2026-01-01"},
            "must not precede",
        ),
        ("receivables_aging", {}, "as_of"),
        ("receivables_aging", {"as_of": "2026-01-01", "extra": "x"}, "Unsupported"),
        ("shipment_performance", {"start_date": 1}, "ISO date"),
    ),
)
def test_report_validation(database, name, parameters, message):
    with pytest.raises(ValueError, match=message):
        crm.run_report(database, name, parameters)


def test_invalid_snapshot_and_invalid_numeric_are_explicit(tmp_path):
    with pytest.raises(ValueError, match="unavailable or invalid"):
        crm.export_summary(tmp_path / "missing.sqlite")

    legacy = tmp_path / "legacy.sqlite"
    sqlite3.connect(legacy).close()
    with pytest.raises(ValueError, match="rebuild"):
        crm.export_summary(legacy)

    invalid = canonical_export()
    invalid["tables"]["invoice_header"][0]["total_amount"] = "not-a-number"
    database = tmp_path / "invalid.sqlite"
    store.build_database(invalid, database)
    with pytest.raises(ValueError, match="numeric value"):
        crm.run_report(database, "invoice_register")

    corrupt = tmp_path / "corrupt.sqlite"
    store.build_database(canonical_export(), corrupt)
    with sqlite3.connect(corrupt) as connection:
        connection.execute(
            "UPDATE crm_record SET row_json = '{' WHERE table_name = 'party' AND ordinal = 0"
        )
    with pytest.raises(ValueError, match="invalid party row JSON"):
        crm.get_record(corrupt, "party", "vendor-1")


def test_snapshot_row_and_index_failures_are_bounded(monkeypatch, tmp_path):
    non_object = tmp_path / "non-object.sqlite"
    store.build_database(canonical_export(), non_object)
    with sqlite3.connect(non_object) as connection:
        connection.execute(
            "UPDATE crm_record SET row_json = '[]' WHERE table_name = 'party' AND ordinal = 0"
        )
    with pytest.raises(ValueError, match="non-object party row"):
        crm.export_table(non_object, "party")

    bad_hash = tmp_path / "bad-hash.sqlite"
    store.build_database(canonical_export(), bad_hash)
    with sqlite3.connect(bad_hash) as connection:
        connection.execute(
            "UPDATE crm_record SET row_sha256 = 'wrong' WHERE table_name = 'party' AND ordinal = 0"
        )
    with pytest.raises(ValueError, match="record integrity check failed"):
        crm.get_record(bad_hash, "party", "vendor-1")
    with pytest.raises(ValueError, match="record integrity check failed"):
        crm.search_records(bad_hash, "Vendor", tables=["party"])

    database = tmp_path / "query-errors.sqlite"
    store.build_database(canonical_export(), database)

    class BrokenQueryConnection:
        def __init__(self, connection, failing_fragment):
            self.connection = connection
            self.failing_fragment = failing_fragment

        def execute(self, statement, *args):
            if self.failing_fragment in statement:
                raise sqlite3.OperationalError("simulated missing index")
            return self.connection.execute(statement, *args)

    def broken_connect(failing_fragment):
        @contextmanager
        def connect(_database):
            connection = sqlite3.connect(database)
            connection.row_factory = sqlite3.Row
            try:
                yield BrokenQueryConnection(connection, failing_fragment)
            finally:
                connection.close()

        return connect

    monkeypatch.setattr(crm, "_connect", broken_connect("idempotency_key_json = ?"))
    with pytest.raises(ValueError, match="records are unavailable"):
        crm.get_record(database, "party", "vendor-1")
    monkeypatch.setattr(crm, "_connect", broken_connect("FROM crm_record_fts"))
    with pytest.raises(ValueError, match="search index is unavailable"):
        crm.search_records(database, "Vendor", tables=["party"])


def test_account_card_rejects_snapshot_with_missing_source_document_row(tmp_path):
    export = canonical_export()
    database = tmp_path / "missing-source.sqlite"
    store.build_database(export, database)
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM crm_record WHERE table_name = 'document'")

    with pytest.raises(ValueError, match="integrity check failed"):
        crm.account_card(database, party_key="customer-1")


def test_low_level_query_guards_cover_defensive_snapshot_edges(database):
    class BrokenRecords:
        def execute(self, *_args):
            raise sqlite3.OperationalError("missing table")

    with pytest.raises(ValueError, match="Unsupported CRM table"):
        crm._records(BrokenRecords(), "not-a-table")
    with pytest.raises(ValueError, match="records are unavailable"):
        crm._records(BrokenRecords(), "document")
    with pytest.raises(ValueError, match="unavailable or invalid"):
        with crm._connect(database) as connection:
            connection.execute("SELECT * FROM table_that_does_not_exist")

    assert crm._decimal(None) == 0
    for value in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValueError, match="numeric value"):
            crm._decimal(value)
    assert crm._date_in_range(None, None, None)
    assert not crm._date_in_range(None, date(2026, 1, 1), None)
    with pytest.raises(ValueError, match="only operator and value"):
        crm._matches("x", {"operator": "eq", "value": "x", "extra": True})
    with pytest.raises(ValueError, match="must be scalar"):
        crm._matches("x", {"operator": "contains", "value": []})
    assert not crm._matches("alphabetic", {"operator": "gte", "value": 1})
    assert not crm._matches("001", {"operator": "eq", "value": "1"})
    assert crm._matches("001", {"operator": "ne", "value": "1"})

    with pytest.raises(ValueError, match="Unsupported CRM table"):
        crm.query_records(database, "not-a-table")
    with pytest.raises(ValueError, match="limit"):
        crm.query_records(database, "party", limit=0)
    with pytest.raises(ValueError, match="offset"):
        crm.query_records(database, "party", offset=-1)
    with pytest.raises(ValueError, match="limit"):
        crm.query_records(database, "party", limit=True)
    with pytest.raises(ValueError, match="Unsupported CRM table"):
        crm.get_record(database, "not-a-table", "key")


def test_snapshot_index_failures_are_explicit(monkeypatch, database):
    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class BrokenIndexConnection:
        def execute(self, statement, *_args):
            if "crm_metadata" in statement:
                return Result(
                    [
                        {"key": "batch_id", "value_json": '"batch-crm"'},
                        {"key": "source_export_sha256", "value_json": '"hash"'},
                    ]
                )
            raise sqlite3.OperationalError("missing index")

    @contextmanager
    def broken_connect(_database):
        yield BrokenIndexConnection()

    monkeypatch.setattr(crm, "_connect", broken_connect)
    with pytest.raises(ValueError, match="metadata is incomplete"):
        crm.get_record(database, "party", "customer-1")
    with pytest.raises(ValueError, match="metadata is incomplete"):
        crm.search_records(database, "customer")


def test_ambiguous_cards_filters_and_report_exclusions(tmp_path):
    data = canonical_export()
    data["tables"]["party"][0]["canonical_name"] = "Customer Inc"
    database = tmp_path / "edge.sqlite"
    store.build_database(data, database)
    with pytest.raises(ValueError, match="ambiguous"):
        crm.account_card(database, name="Customer Inc")

    assert crm.analyze_sales(database, filters={"minimum_total": "200"})["rows"] == []
    assert crm.analyze_sales(database, filters={"maximum_total": "100"})["rows"] == []
    assert crm.analyze_sales(database, filters={"company": "Other"})["rows"] == []
    assert (
        crm.run_report(
            database, "invoice_register", {"start_date": "2025-01-01", "end_date": "2025-12-31"}
        )["rows"]
        == []
    )
    assert (
        crm.run_report(
            database,
            "shipment_performance",
            {"start_date": "2026-02-01", "end_date": "2026-02-28"},
        )["rows"]
        == []
    )


def test_aggregate_missing_source_links_and_paid_invoice_are_retained(tmp_path):
    assert (
        crm._aggregate(
            [
                {
                    "account": "a",
                    "label": "A",
                    "currency_code": "USD",
                    "total_amount": "1",
                    "amount_due": "0",
                }
            ],
            "account",
            "label",
        )[0]["source_document_ids"]
        == []
    )
    data = canonical_export()
    data["tables"]["invoice_header"][0]["amount_due"] = "0"
    database = tmp_path / "paid.sqlite"
    store.build_database(data, database)
    assert crm.run_report(database, "receivables_aging", {"as_of": "2026-03-31"})["rows"] == []


def test_currency_partitioning_attribution_scope_and_as_of_boundary(tmp_path):
    data = canonical_export()
    managed = lambda **row: {  # noqa: E731 - compact fixture helper.
        **row,
        "batch_id": data["batch_id"],
        "review_status": "exception_resolved",
    }
    data["tables"]["invoice_header"].extend(
        [
            managed(
                invoice_key="invoice-2",
                invoice_number="INV-2",
                invoice_date="2026-02-01",
                due_date="2026-03-01",
                biller_party_key="vendor-1",
                payer_party_key="customer-1",
                currency_code="CAD",
                total_amount="75.00",
                amount_due="75.00",
                source_document_id="doc-1",
            ),
            managed(
                invoice_key="invoice-future",
                invoice_number="INV-FUTURE",
                invoice_date="2027-01-01",
                due_date="2027-02-01",
                biller_party_key="vendor-1",
                payer_party_key="customer-1",
                currency_code="USD",
                total_amount="50.00",
                amount_due="50.00",
                source_document_id="doc-1",
            ),
        ]
    )
    data["tables"]["attribution"].extend(
        [
            managed(
                attribution_id="attr-2",
                target_table="invoice_header",
                target_key="invoice-2",
                amount="25.00",
                source_document_id="doc-1",
            ),
            managed(
                attribution_id="attr-payment",
                target_table="payment",
                target_key="payment-1",
                amount="999.00",
                source_document_id="doc-1",
            ),
        ]
    )
    database = tmp_path / "currency.sqlite"
    store.build_database(data, database)

    sales = crm.analyze_sales(database, ["customer"])
    assert [(row["currency"], row["total_amount"]) for row in sales["rows"]] == [
        ("CAD", "75.00"),
        ("USD", "175.00"),
    ]
    card = crm.account_card(database, party_key="customer-1")
    assert card["summary"]["invoice_value"] is None
    assert card["summary"]["cross_currency_totals_suppressed"] is True
    coverage = crm.run_report(database, "attribution_coverage")["rows"]
    assert [(row["currency_code"], row["attributed_value"]) for row in coverage] == [
        ("CAD", "25.00"),
        ("USD", "100.00"),
    ]
    aging = crm.run_report(database, "receivables_aging", {"as_of": "2026-03-31"})
    assert {row["invoice_key"] for row in aging["rows"]} == {"invoice-1", "invoice-2"}


def test_sales_and_attribution_retain_missing_source_and_unresolved_links(monkeypatch, database):
    tables = {table: [] for table in crm.LOAD_ORDER}
    tables["party"] = [
        {"party_key": "customer-1", "canonical_name": "Customer Inc"},
        {"party_key": "vendor-1", "canonical_name": "Vendor LLC"},
    ]
    tables["invoice_header"] = [
        {
            "invoice_key": "invoice-1",
            "invoice_date": "2026-01-15",
            "payer_party_key": "customer-1",
            "biller_party_key": "vendor-1",
            "currency_code": "USD",
            "total_amount": "10.00",
            "amount_due": "4.00",
        }
    ]
    tables["attribution"] = [
        {
            "target_table": "invoice_header",
            "target_key": "missing-invoice",
            "amount": "100.00",
        },
        {
            "target_table": "invoice_header",
            "target_key": "invoice-1",
            "amount": "6.00",
        },
    ]
    monkeypatch.setattr(crm, "_report_source", lambda _connection: tables)

    sales = crm.analyze_sales(database, ["customer"])
    assert sales["rows"][0]["source_document_ids"] == []
    coverage = crm.run_report(database, "attribution_coverage")["rows"]
    assert coverage[0]["attributed_value"] == "6.00"
    assert coverage[0]["source_document_ids"] == []
