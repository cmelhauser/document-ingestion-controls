"""Shared read-only CRM export, schema, and standard-report service.

The HTTPS and MCP transports intentionally call this module instead of growing
separate business logic. Every function reads the immutable, review-clear
canonical snapshot embedded by ``retrieval_store.py`` and returns bounded JSON.
It never sends data, mutates a target CRM, or changes an approval state.
"""

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from canonical_load import IDEMPOTENCY_KEYS, LOAD_ORDER, schema_catalog, stable_checksum

MAX_PAGE_SIZE = 200
SNAPSHOT_SCHEMA_VERSION = "approved_crm_snapshot_v1"
TOKEN = re.compile(r"[A-Za-z0-9]+")
FILTER_OPERATORS = {"eq", "ne", "contains", "starts_with", "in", "gte", "lte", "is_null"}
SALES_DIMENSIONS = {
    "customer",
    "company",
    "vendor",
    "selling_location",
    "region",
    "currency",
    "month",
    "quarter",
    "year",
    "ack_number",
    "job_number",
}

REPORT_CATALOG = (
    {
        "name": "account_directory",
        "description": "Approved party masters with roles, addresses, contacts, and source links.",
        "parameters": {},
        "example": {},
    },
    {
        "name": "invoice_register",
        "description": "Invoice header register with customer/vendor names and source citations.",
        "parameters": {"start_date": "optional ISO date", "end_date": "optional ISO date"},
        "example": {"start_date": "2026-01-01", "end_date": "2026-03-31"},
    },
    {
        "name": "receivables_aging",
        "description": "Approved current snapshot balances for invoices dated on or before the as-of date, bucketed from current through 90+ days.",
        "parameters": {"as_of": "required ISO date"},
        "example": {"as_of": "2026-03-31"},
    },
    {
        "name": "sales_by_customer",
        "description": "Invoice value aggregated by approved payer/customer party and currency.",
        "parameters": {"start_date": "optional ISO date", "end_date": "optional ISO date"},
        "example": {"start_date": "2026-01-01", "end_date": "2026-12-31"},
    },
    {
        "name": "sales_by_selling_location",
        "description": "Invoice value aggregated by approved originating selling location and currency.",
        "parameters": {"start_date": "optional ISO date", "end_date": "optional ISO date"},
        "example": {"start_date": "2026-01-01", "end_date": "2026-12-31"},
    },
    {
        "name": "attribution_coverage",
        "description": "Invoice-target attributed versus unattributed approved value by currency.",
        "parameters": {},
        "example": {},
    },
    {
        "name": "shipment_performance",
        "description": "Approved shipment delivery timeliness against committed dates.",
        "parameters": {"start_date": "optional ISO date", "end_date": "optional ISO date"},
        "example": {"start_date": "2026-01-01", "end_date": "2026-12-31"},
    },
)


@contextmanager
def _connect(database):
    """Open a query-only snapshot and normalize unusable database failures."""
    try:
        database_uri = Path(database).resolve(strict=True).as_uri()
        connection = sqlite3.connect(f"{database_uri}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
    except (OSError, TypeError, sqlite3.Error) as exc:
        raise ValueError("CRM snapshot database is unavailable or invalid") from exc
    try:
        yield connection
    except sqlite3.Error as exc:
        raise ValueError("CRM snapshot database is unavailable or invalid") from exc
    finally:
        connection.close()


def _json_value(raw, label):
    """Decode one retained JSON value and fail closed on snapshot corruption."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(
            f"CRM snapshot contains invalid {label} JSON; rebuild the snapshot"
        ) from exc


def _metadata(connection):
    try:
        rows = connection.execute("SELECT key, value_json FROM crm_metadata").fetchall()
    except sqlite3.Error as exc:
        raise ValueError(
            "CRM snapshot is unavailable; rebuild the retrieval database from the canonical export"
        ) from exc
    metadata = {
        row["key"]: _json_value(row["value_json"], f"metadata {row['key']}") for row in rows
    }
    required = {
        "snapshot_schema_version",
        "batch_id",
        "source_export_sha256",
        "load_plan_sha256",
        "table_manifest",
    }
    if (
        metadata.get("snapshot_schema_version") != SNAPSHOT_SCHEMA_VERSION
        or not required <= set(metadata)
        or not isinstance(metadata.get("batch_id"), str)
        or not metadata["batch_id"]
        or not isinstance(metadata.get("table_manifest"), dict)
        or set(metadata["table_manifest"]) != set(LOAD_ORDER)
    ):
        raise ValueError(
            "CRM snapshot metadata is incomplete or incompatible; rebuild the snapshot"
        )
    return metadata


def _records(connection, table, metadata=None):
    if table not in LOAD_ORDER:
        raise ValueError(f"Unsupported CRM table: {table}")
    try:
        rows = connection.execute(
            "SELECT row_json FROM crm_record WHERE table_name = ? ORDER BY ordinal", (table,)
        ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError("CRM snapshot records are unavailable") from exc
    records = [_json_value(row["row_json"], f"{table} row") for row in rows]
    if not all(isinstance(row, dict) for row in records):
        raise ValueError(f"CRM snapshot contains a non-object {table} row; rebuild the snapshot")
    metadata = _metadata(connection) if metadata is None else metadata
    expected = metadata["table_manifest"].get(table)
    actual = {"records": len(records), "rows_sha256": stable_checksum(records)}
    if expected != actual:
        raise ValueError(f"CRM snapshot {table} integrity check failed; rebuild the snapshot")
    return records


def _scalar(value):
    return value.get("value") if isinstance(value, dict) and "value" in value else value


def _decimal(value, default="0"):
    value = _scalar(value)
    if value in (None, ""):
        return Decimal(default)
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"CRM numeric value is invalid: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"CRM numeric value is invalid: {value!r}")
    return result


def _iso_date(value, field, required=False):
    if value in (None, "") and not required:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date") from exc


def _date_in_range(value, start, end):
    if value in (None, ""):
        return start is None and end is None
    parsed = _iso_date(str(value), "record date", required=True)
    return (start is None or parsed >= start) and (end is None or parsed <= end)


def _date_parameters(parameters):
    start = _iso_date(parameters.get("start_date"), "start_date")
    end = _iso_date(parameters.get("end_date"), "end_date")
    if start and end and end < start:
        raise ValueError("end_date must not precede start_date")
    unknown = set(parameters) - {"start_date", "end_date"}
    if unknown:
        raise ValueError(f"Unsupported report parameters: {sorted(unknown)}")
    return start, end


def capabilities(database):
    """Describe the identical API/MCP approved-fact CRM surface."""
    summary = export_summary(database)
    return {
        "schema_version": "crm_capabilities_v1",
        "read_only": True,
        "approved_facts_only": True,
        "network_upload_permitted": False,
        "batch_id": summary["batch_id"],
        "functions": [
            "search approved business documents",
            "get an approved business document with citation",
            "summarize the CRM export snapshot",
            "inspect canonical CRM schema and table availability",
            "search across every approved CRM record",
            "retrieve any record by its idempotency key",
            "filter, project, and sort approved records",
            "export a checksummed, paginated canonical table",
            "retrieve a joined account, address, and contact card with currency-separated totals",
            "slice currency-partitioned sales by company, vendor, region, location, period, ACK, or job",
            "list and run deterministic standard reports",
        ],
        "api_routes": [
            "GET /health",
            "GET /v1/capabilities",
            "GET /v1/documents?query=...&limit=...",
            "GET /v1/documents/{document_id}",
            "GET /v1/crm/summary",
            "GET /v1/crm/schema[?table=...]",
            "GET /v1/crm/search?query=...[&table=...]",
            "GET /v1/crm/record/{table}?key=...",
            "GET /v1/crm/query/{table}?filter.FIELD__OP=...&select=...&sort=...",
            "GET /v1/crm/export/{table}?limit=...&offset=...&filter.FIELD=...",
            "GET /v1/crm/accounts/{party_key}",
            "GET /v1/crm/analysis/sales?group_by=...",
            "GET /v1/crm/reports",
            "GET /v1/crm/reports/{report_name}?...",
        ],
        "mcp_tools": [
            "search_business_documents",
            "get_business_document",
            "get_crm_capabilities",
            "get_crm_export_summary",
            "get_crm_schema",
            "search_crm_records",
            "get_crm_record",
            "query_crm_records",
            "export_crm_table",
            "get_account_card",
            "analyze_sales",
            "run_crm_report",
        ],
        "standard_reports": [item["name"] for item in REPORT_CATALOG],
    }


def export_summary(database):
    """Return reconciliation metadata for the immutable approved CRM snapshot."""
    with _connect(database) as connection:
        metadata = _metadata(connection)
        counts = {table: len(_records(connection, table, metadata)) for table in LOAD_ORDER}
    total = sum(counts.values())
    return {
        "schema_version": "crm_export_summary_v1",
        "snapshot_schema_version": metadata.get("snapshot_schema_version"),
        "batch_id": metadata.get("batch_id"),
        "registry_version": metadata.get("registry_version"),
        "source_export_sha256": metadata.get("source_export_sha256"),
        "load_plan_sha256": metadata.get("load_plan_sha256"),
        "table_manifest": metadata.get("table_manifest"),
        "snapshot_integrity_verified": True,
        "tables": counts,
        "tables_available": sum(1 for count in counts.values() if count),
        "total_records": total,
        "empty": total == 0,
        "review_clear_only": True,
        "reconciliation_required": True,
        "network_upload_permitted": False,
    }


def schema(database, table=None):
    """Return declared columns, observed extensions, keys, and available counts."""
    if table is not None and table not in LOAD_ORDER:
        raise ValueError(f"Unsupported CRM table: {table}")
    declared = schema_catalog()
    with _connect(database) as connection:
        metadata = _metadata(connection)
        selected = [table] if table else list(LOAD_ORDER)
        tables = []
        for name in selected:
            rows = _records(connection, name, metadata)
            extensions = sorted({key for row in rows for key in row} - set(declared[name]))
            tables.append(
                {
                    "table": name,
                    "records": len(rows),
                    "idempotency_keys": list(IDEMPOTENCY_KEYS[name]),
                    "declared_columns": declared[name],
                    "observed_extension_columns": extensions,
                }
            )
    return {
        "schema_version": "crm_schema_catalog_v1",
        "batch_id": metadata.get("batch_id"),
        "tables": tables,
    }


def export_table(database, table, limit=100, offset=0, filters=None):
    """Export one stable page with keys and checksums suitable for reconciliation."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be an integer from 1 through {MAX_PAGE_SIZE}")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    filters = {} if filters is None else filters
    if not isinstance(filters, dict) or len(filters) > 10:
        raise ValueError("filters must be an object with at most 10 exact matches")
    if table not in LOAD_ORDER:
        raise ValueError(f"Unsupported CRM table: {table}")
    with _connect(database) as connection:
        metadata = _metadata(connection)
        rows = _records(connection, table, metadata)
    allowed_columns = set(schema_catalog()[table]) | {key for row in rows for key in row}
    for key, expected in filters.items():
        if key not in allowed_columns:
            raise ValueError(f"Unsupported filter column for {table}: {key}")
        if isinstance(expected, (dict, list)):
            raise ValueError("filter values must be scalar")
        rows = [row for row in rows if str(_scalar(row.get(key))) == str(expected)]
    page = rows[offset : offset + limit]
    next_offset = offset + len(page) if offset + len(page) < len(rows) else None
    payload = {
        "schema_version": "crm_table_page_v1",
        "batch_id": metadata.get("batch_id"),
        "source_export_sha256": metadata.get("source_export_sha256"),
        "table": table,
        "idempotency_keys": list(IDEMPOTENCY_KEYS[table]),
        "filters": filters,
        "total_records": len(rows),
        "offset": offset,
        "limit": limit,
        "returned_records": len(page),
        "next_offset": next_offset,
        "rows": page,
    }
    payload["page_sha256"] = stable_checksum(payload)
    return payload


def _available_columns(table, rows):
    return set(schema_catalog()[table]) | {key for row in rows for key in row}


def _comparable(value):
    value = _scalar(value)
    try:
        return 0, Decimal(str(value))
    except (InvalidOperation, ValueError):
        return 1, "" if value is None else str(value).casefold()


def _matches(value, condition):
    if not isinstance(condition, dict):
        condition = {"operator": "eq", "value": condition}
    if set(condition) - {"operator", "value"}:
        raise ValueError("filter conditions accept only operator and value")
    operator = condition.get("operator", "eq")
    if operator not in FILTER_OPERATORS:
        raise ValueError(f"Unsupported filter operator: {operator}")
    expected = condition.get("value")
    actual = _scalar(value)
    if operator == "is_null":
        if not isinstance(expected, bool):
            raise ValueError("is_null filter value must be Boolean")
        return (actual in (None, "")) is expected
    if operator == "in":
        if not isinstance(expected, list) or len(expected) > 100:
            raise ValueError("in filter value must be a list of at most 100 scalars")
        return any(_matches(actual, item) for item in expected)
    if isinstance(expected, (dict, list)):
        raise ValueError("filter value must be scalar")
    if operator == "contains":
        return str(expected).casefold() in ("" if actual is None else str(actual).casefold())
    if operator == "starts_with":
        return ("" if actual is None else str(actual).casefold()).startswith(
            str(expected).casefold()
        )
    if operator == "eq":
        return (
            actual is expected
            if actual is None or expected is None
            else str(actual) == str(expected)
        )
    if operator == "ne":
        return not _matches(actual, {"operator": "eq", "value": expected})
    left, right = _comparable(actual), _comparable(expected)
    if left[0] != right[0]:
        return False
    return left[1] >= right[1] if operator == "gte" else left[1] <= right[1]


def query_records(
    database,
    table,
    filters=None,
    select=None,
    sort=None,
    limit=100,
    offset=0,
):
    """Filter, project, and sort one approved table with a bounded query grammar."""
    if table not in LOAD_ORDER:
        raise ValueError(f"Unsupported CRM table: {table}")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be an integer from 1 through {MAX_PAGE_SIZE}")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    filters = {} if filters is None else filters
    select = [] if select is None else select
    sort = [] if sort is None else sort
    if not isinstance(filters, dict) or len(filters) > 10:
        raise ValueError("filters must be an object with at most 10 conditions")
    if (
        not isinstance(select, list)
        or len(select) > 50
        or not all(isinstance(field, str) for field in select)
    ):
        raise ValueError("select must be a list of at most 50 field names")
    if (
        not isinstance(sort, list)
        or len(sort) > 3
        or not all(isinstance(field, str) for field in sort)
    ):
        raise ValueError("sort must be a list of at most 3 field names")
    with _connect(database) as connection:
        metadata = _metadata(connection)
        rows = _records(connection, table)
    columns = _available_columns(table, rows)
    requested = set(filters) | set(select) | {field.removeprefix("-") for field in sort}
    unknown = requested - columns
    if unknown:
        raise ValueError(f"Unsupported fields for {table}: {sorted(unknown)}")
    for field, condition in filters.items():
        rows = [row for row in rows if _matches(row.get(field), condition)]
    for field in reversed(sort):
        descending = field.startswith("-")
        name = field.removeprefix("-")
        rows.sort(key=lambda row: _comparable(row.get(name)), reverse=descending)
    total = len(rows)
    rows = rows[offset : offset + limit]
    if select:
        fields = list(dict.fromkeys([*IDEMPOTENCY_KEYS[table], *select]))
        rows = [{field: row.get(field) for field in fields} for row in rows]
    payload = {
        "schema_version": "crm_record_query_v1",
        "batch_id": metadata.get("batch_id"),
        "source_export_sha256": metadata.get("source_export_sha256"),
        "table": table,
        "filters": filters,
        "select": select,
        "sort": sort,
        "total_records": total,
        "offset": offset,
        "limit": limit,
        "returned_records": len(rows),
        "next_offset": offset + len(rows) if offset + len(rows) < total else None,
        "rows": rows,
    }
    payload["query_sha256"] = stable_checksum(payload)
    return payload


def get_record(database, table, key):
    """Retrieve any canonical row by its single or composite idempotency key."""
    if table not in LOAD_ORDER:
        raise ValueError(f"Unsupported CRM table: {table}")
    keys = [key] if len(IDEMPOTENCY_KEYS[table]) == 1 and not isinstance(key, list) else key
    if (
        not isinstance(keys, list)
        or len(keys) != len(IDEMPOTENCY_KEYS[table])
        or any(not isinstance(value, str) or not value for value in keys)
    ):
        raise ValueError(
            f"key must supply {len(IDEMPOTENCY_KEYS[table])} non-empty string value(s)"
        )
    encoded = json.dumps(keys, sort_keys=True, separators=(",", ":"))
    with _connect(database) as connection:
        metadata = _metadata(connection)
        _records(connection, table, metadata)
        try:
            row = connection.execute(
                "SELECT row_json, row_sha256 FROM crm_record "
                "WHERE table_name = ? AND idempotency_key_json = ?",
                (table, encoded),
            ).fetchone()
        except sqlite3.Error as exc:
            raise ValueError("CRM snapshot records are unavailable") from exc
    if row is None:
        raise ValueError("CRM record was not found")
    payload = {
        "schema_version": "crm_record_v1",
        "batch_id": metadata.get("batch_id"),
        "source_export_sha256": metadata.get("source_export_sha256"),
        "table": table,
        "idempotency_keys": list(IDEMPOTENCY_KEYS[table]),
        "key": keys,
        "record": _json_value(row["row_json"], f"{table} row"),
    }
    if (
        not isinstance(payload["record"], dict)
        or stable_checksum(payload["record"]) != row["row_sha256"]
    ):
        raise ValueError(
            f"CRM snapshot {table} record integrity check failed; rebuild the snapshot"
        )
    payload["record_sha256"] = stable_checksum(payload)
    return payload


def search_records(database, query, tables=None, limit=20):
    """Full-text search every approved canonical record in one indexed snapshot."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be an integer from 1 through {MAX_PAGE_SIZE}")
    tables = list(LOAD_ORDER) if tables is None else tables
    if not isinstance(tables, list) or not tables or len(tables) > len(LOAD_ORDER):
        raise ValueError("tables must be a non-empty list of supported CRM tables")
    if any(table not in LOAD_ORDER for table in tables):
        raise ValueError("tables contain an unsupported CRM table")
    fts = " AND ".join(TOKEN.findall(query))
    with _connect(database) as connection:
        metadata = _metadata(connection)
        for table in tables:
            _records(connection, table, metadata)
        try:
            if not fts:
                rows = []
            else:
                rows = connection.execute(
                    "SELECT r.table_name, r.idempotency_key_json, r.row_json, r.row_sha256 "
                    "FROM crm_record_fts f JOIN crm_record r ON r.record_id = f.rowid "
                    "WHERE crm_record_fts MATCH ? "
                    "AND r.table_name IN (SELECT value FROM json_each(?)) "
                    "ORDER BY bm25(crm_record_fts), r.record_id LIMIT ?",
                    (fts, json.dumps(tables), limit),
                ).fetchall()
        except sqlite3.Error as exc:
            raise ValueError(
                "CRM record search index is unavailable; rebuild the snapshot"
            ) from exc
    results = []
    for row in rows:
        record = _json_value(row["row_json"], f"{row['table_name']} row")
        if not isinstance(record, dict) or stable_checksum(record) != row["row_sha256"]:
            raise ValueError(
                f"CRM snapshot {row['table_name']} record integrity check failed; rebuild the snapshot"
            )
        results.append(
            {
                "table": row["table_name"],
                "key": _json_value(row["idempotency_key_json"], "idempotency key"),
                "record": record,
            }
        )
    payload = {
        "schema_version": "crm_record_search_v1",
        "batch_id": metadata.get("batch_id"),
        "source_export_sha256": metadata.get("source_export_sha256"),
        "query": query,
        "tables": tables,
        "results": results,
    }
    payload["search_sha256"] = stable_checksum(payload)
    return payload


def account_card(database, party_key=None, name=None):
    """Return one joined account card with addresses, contacts, roles, and activity."""
    if (party_key is None) == (name is None):
        raise ValueError("supply exactly one of party_key or name")
    selected = party_key if party_key is not None else name
    if not isinstance(selected, str) or not selected.strip():
        raise ValueError("party_key or name must be a non-empty string")
    with _connect(database) as connection:
        metadata = _metadata(connection)
        tables = _report_source(connection)
    matches = [
        row
        for row in tables["party"]
        if (party_key is not None and row.get("party_key") == party_key)
        or (
            name is not None
            and str(row.get("canonical_name", "")).casefold() == str(name).casefold()
        )
    ]
    if not matches:
        raise ValueError("CRM account was not found")
    if len(matches) > 1:
        raise ValueError("CRM account name is ambiguous; use party_key")
    party = matches[0]
    key = party["party_key"]
    invoices = [
        row
        for row in tables["invoice_header"]
        if key
        in {row.get("biller_party_key"), row.get("payer_party_key"), row.get("ship_to_party_key")}
    ]
    payments = [
        row
        for row in tables["payment"]
        if key in {row.get("payer_party_key"), row.get("payee_party_key")}
    ]
    document_ids = sorted(
        {
            value
            for value in [
                party.get("source_document_id"),
                *(row.get("source_document_id") for row in invoices),
                *(row.get("source_document_id") for row in payments),
            ]
            if value
        }
    )
    documents = {
        row.get("document_id"): row
        for row in tables["document"]
        if row.get("document_id") in document_ids
    }
    missing_document_ids = sorted(set(document_ids) - set(documents))
    payload = {
        "schema_version": "crm_account_card_v1",
        "batch_id": metadata.get("batch_id"),
        "source_export_sha256": metadata.get("source_export_sha256"),
        "party": party,
        "roles": [row for row in tables["party_role"] if row.get("party_key") == key],
        "name_variants": [
            row for row in tables["party_name_variant"] if row.get("party_key") == key
        ],
        "addresses": [row for row in tables["address"] if row.get("party_key") == key],
        "contacts": [row for row in tables["contact"] if row.get("party_key") == key],
        "invoices": invoices,
        "payments": payments,
        "summary": {
            "invoice_count": len(invoices),
            "payment_count": len(payments),
        },
        "source_documents": [
            documents[document_id] for document_id in document_ids if document_id in documents
        ],
        "missing_source_document_ids": missing_document_ids,
    }
    invoice_totals = _currency_totals(invoices, "total_amount")
    due_totals = _currency_totals(invoices, "amount_due")
    payment_totals = _currency_totals(payments, "total_paid")
    payload["summary"].update(
        {
            "invoice_value": _single_currency_total(invoice_totals),
            "amount_due": _single_currency_total(due_totals),
            "payment_value": _single_currency_total(payment_totals),
            "invoice_value_by_currency": invoice_totals,
            "amount_due_by_currency": due_totals,
            "payment_value_by_currency": payment_totals,
            "cross_currency_totals_suppressed": any(
                len(totals) > 1 for totals in (invoice_totals, due_totals, payment_totals)
            ),
        }
    )
    payload["card_sha256"] = stable_checksum(payload)
    return payload


def analyze_sales(database, group_by=None, filters=None):
    """Slice approved invoice value by governed business dimensions."""
    group_by = ["customer"] if group_by is None else group_by
    filters = {} if filters is None else filters
    if (
        not isinstance(group_by, list)
        or len(group_by) > 3
        or not all(isinstance(item, str) and item in SALES_DIMENSIONS for item in group_by)
        or len(group_by) != len(set(group_by))
    ):
        raise ValueError("group_by must contain at most 3 unique supported sales dimensions")
    allowed_filters = {
        "start_date",
        "end_date",
        "customer",
        "company",
        "vendor",
        "region",
        "selling_location",
        "currency",
        "ack_number",
        "job_number",
        "minimum_total",
        "maximum_total",
    }
    if not isinstance(filters, dict):
        raise ValueError("sales filters must be an object")
    unknown_filters = set(filters) - allowed_filters
    if unknown_filters:
        raise ValueError(f"Unsupported sales filters: {sorted(unknown_filters)}")
    if any(
        isinstance(filters.get(name), (dict, list)) for name in SALES_DIMENSIONS if name in filters
    ):
        raise ValueError("sales dimension filters must be scalar")
    start = _iso_date(filters.get("start_date"), "start_date")
    end = _iso_date(filters.get("end_date"), "end_date")
    if start and end and end < start:
        raise ValueError("end_date must not precede start_date")
    minimum = _decimal(filters.get("minimum_total")) if "minimum_total" in filters else None
    maximum = _decimal(filters.get("maximum_total")) if "maximum_total" in filters else None
    if minimum is not None and maximum is not None and maximum < minimum:
        raise ValueError("maximum_total must not be below minimum_total")
    with _connect(database) as connection:
        metadata = _metadata(connection)
        tables = _report_source(connection)
    locations = {row.get("selling_location_key"): row for row in tables["selling_location"]}
    invoices = _invoice_rows(tables, start, end)
    enriched = []
    for row in invoices:
        location = locations.get(row.get("selling_location_key"), {})
        invoice_date = _iso_date(row.get("invoice_date"), "invoice_date")
        dimensions = {
            "customer": row.get("payer") or "Unassigned",
            "company": row.get("payer") or "Unassigned",
            "vendor": row.get("biller") or "Unassigned",
            "selling_location": location.get("location_name") or "Unassigned",
            "region": location.get("territory_name")
            or location.get("state_province")
            or location.get("country_code")
            or "Unassigned",
            "currency": _scalar(row.get("currency_code")) or "Unassigned",
            "month": invoice_date.strftime("%Y-%m") if invoice_date else "Undated",
            "quarter": (
                f"{invoice_date.year}-Q{((invoice_date.month - 1) // 3) + 1}"
                if invoice_date
                else "Undated"
            ),
            "year": str(invoice_date.year) if invoice_date else "Undated",
            "ack_number": row.get("ack_number") or "Unassigned",
            "job_number": row.get("job_number") or "Unassigned",
        }
        total = _decimal(row.get("total_amount"))
        if minimum is not None and total < minimum:
            continue
        if maximum is not None and total > maximum:
            continue
        if any(
            str(dimensions[name]).casefold() != str(expected).casefold()
            for name, expected in filters.items()
            if name in SALES_DIMENSIONS
        ):
            continue
        enriched.append((row, dimensions))
    grouped = {}
    effective_group_by = [*group_by, *([] if "currency" in group_by else ["currency"])]
    for row, dimensions in enriched:
        key = tuple(dimensions[name] for name in effective_group_by)
        entry = grouped.setdefault(
            key,
            {
                **{name: dimensions[name] for name in effective_group_by},
                "invoice_count": 0,
                "total_amount": Decimal("0"),
                "amount_due": Decimal("0"),
                "source_document_ids": set(),
            },
        )
        entry["invoice_count"] += 1
        entry["total_amount"] += _decimal(row.get("total_amount"))
        entry["amount_due"] += _decimal(row.get("amount_due"))
        if row.get("source_document_id"):
            entry["source_document_ids"].add(row["source_document_id"])
    rows = []
    for entry in grouped.values():
        rows.append(
            {
                **{name: entry[name] for name in effective_group_by},
                "invoice_count": entry["invoice_count"],
                "total_amount": str(entry["total_amount"]),
                "amount_due": str(entry["amount_due"]),
                "average_invoice_amount": str(entry["total_amount"] / entry["invoice_count"]),
                "source_document_ids": sorted(entry["source_document_ids"]),
            }
        )
    rows.sort(key=lambda item: tuple(str(item[name]) for name in effective_group_by))
    payload = {
        "schema_version": "crm_sales_analysis_v1",
        "batch_id": metadata.get("batch_id"),
        "source_export_sha256": metadata.get("source_export_sha256"),
        "group_by": group_by,
        "effective_group_by": effective_group_by,
        "currency_partitioned": True,
        "filters": filters,
        "row_count": len(rows),
        "rows": rows,
        "approved_facts_only": True,
    }
    payload["analysis_sha256"] = stable_checksum(payload)
    return payload


def report_catalog():
    """Return the reports this service will run, as its clients see them."""
    return {"schema_version": "crm_report_catalog_v1", "reports": list(REPORT_CATALOG)}


def _report_source(connection):
    metadata = _metadata(connection)
    return {table: _records(connection, table, metadata) for table in LOAD_ORDER}


def _invoice_rows(tables, start=None, end=None):
    parties = {row.get("party_key"): row for row in tables["party"]}
    documents = {row.get("document_id"): row for row in tables["document"]}
    lines = {}
    for line in tables["invoice_line"]:
        lines.setdefault(line.get("invoice_key"), []).append(line)
    output = []
    for invoice in tables["invoice_header"]:
        if not _date_in_range(invoice.get("invoice_date"), start, end):
            continue
        document = documents.get(invoice.get("source_document_id"), {})
        biller = parties.get(invoice.get("biller_party_key"), {})
        payer = parties.get(invoice.get("payer_party_key"), {})
        output.append(
            {
                "invoice_key": invoice.get("invoice_key"),
                "invoice_number": invoice.get("invoice_number"),
                "invoice_date": invoice.get("invoice_date"),
                "due_date": invoice.get("due_date"),
                "biller": biller.get("canonical_name"),
                "payer": payer.get("canonical_name"),
                "currency_code": invoice.get("currency_code"),
                "total_amount": str(_decimal(invoice.get("total_amount"))),
                "amount_due": str(_decimal(invoice.get("amount_due"))),
                "line_count": len(lines.get(invoice.get("invoice_key"), [])),
                "ack_number": invoice.get("ack_number"),
                "job_number": invoice.get("job_number"),
                "selling_location_key": invoice.get("selling_location_key"),
                "review_status": invoice.get("review_status"),
                "source_document_id": invoice.get("source_document_id"),
                "source_file": document.get("source_file"),
                "source_page_range": document.get("source_page_range"),
                "source_sha256": document.get("source_sha256"),
            }
        )
    return output


def _currency_totals(rows, amount_field):
    totals = {}
    for row in rows:
        currency = _scalar(row.get("currency_code")) or "Unassigned"
        totals[currency] = totals.get(currency, Decimal("0")) + _decimal(row.get(amount_field))
    return {currency: str(total) for currency, total in sorted(totals.items())}


def _single_currency_total(totals):
    return next(iter(totals.values())) if len(totals) == 1 else None


def _aggregate(rows, key_field, label_field):
    grouped = {}
    for row in rows:
        currency = _scalar(row.get("currency_code")) or "Unassigned"
        key = (row.get(key_field) or "unassigned", currency)
        entry = grouped.setdefault(
            key,
            {
                key_field: key[0],
                label_field: row.get(label_field) or "Unassigned",
                "currency_code": currency,
                "invoice_count": 0,
                "total_amount": Decimal("0"),
                "amount_due": Decimal("0"),
                "source_document_ids": set(),
            },
        )
        entry["invoice_count"] += 1
        entry["total_amount"] += _decimal(row.get("total_amount"))
        entry["amount_due"] += _decimal(row.get("amount_due"))
        if row.get("source_document_id"):
            entry["source_document_ids"].add(row["source_document_id"])
    return [
        {
            **entry,
            "total_amount": str(entry["total_amount"]),
            "amount_due": str(entry["amount_due"]),
            "source_document_ids": sorted(entry["source_document_ids"]),
        }
        for entry in grouped.values()
    ]


def run_report(database, name, parameters=None):
    """Run one deterministic report and retain source-document links in its rows."""
    parameters = {} if parameters is None else parameters
    if not isinstance(parameters, dict):
        raise ValueError("report parameters must be an object")
    definitions = {item["name"]: item for item in REPORT_CATALOG}
    if name not in definitions:
        raise ValueError(f"Unsupported CRM report: {name}")
    with _connect(database) as connection:
        metadata = _metadata(connection)
        tables = _report_source(connection)
    if name == "account_directory":
        if parameters:
            raise ValueError(f"Unsupported report parameters: {sorted(parameters)}")
        roles, addresses, contacts = {}, {}, {}
        for row in tables["party_role"]:
            roles.setdefault(row.get("party_key"), []).append(row.get("role"))
        for row in tables["address"]:
            addresses.setdefault(row.get("party_key"), []).append(row)
        for row in tables["contact"]:
            contacts.setdefault(row.get("party_key"), []).append(row)
        rows = [
            {
                "party_key": row.get("party_key"),
                "canonical_name": row.get("canonical_name"),
                "roles": sorted(role for role in roles.get(row.get("party_key"), []) if role),
                "address_count": len(addresses.get(row.get("party_key"), [])),
                "contact_count": len(contacts.get(row.get("party_key"), [])),
                "source_document_id": row.get("source_document_id"),
                "review_status": row.get("review_status"),
            }
            for row in tables["party"]
        ]
    elif name == "invoice_register":
        start, end = _date_parameters(parameters)
        rows = _invoice_rows(tables, start, end)
    elif name == "receivables_aging":
        unknown = set(parameters) - {"as_of"}
        if unknown:
            raise ValueError(f"Unsupported report parameters: {sorted(unknown)}")
        as_of = _iso_date(parameters.get("as_of"), "as_of", required=True)
        rows = []
        for invoice in _invoice_rows(tables):
            invoice_date = _iso_date(invoice.get("invoice_date"), "invoice_date")
            if invoice_date and invoice_date > as_of:
                continue
            balance = _decimal(invoice["amount_due"])
            if balance <= 0:
                continue
            due = _iso_date(invoice.get("due_date"), "due_date")
            days = 0 if due is None else (as_of - due).days
            bucket = (
                "current"
                if days <= 0
                else "1-30"
                if days <= 30
                else "31-60"
                if days <= 60
                else "61-90"
                if days <= 90
                else "90+"
            )
            rows.append(
                {**invoice, "as_of": str(as_of), "days_past_due": days, "aging_bucket": bucket}
            )
    elif name == "sales_by_customer":
        start, end = _date_parameters(parameters)
        invoices = _invoice_rows(tables, start, end)
        payer_keys = {
            row.get("invoice_key"): row.get("payer_party_key") for row in tables["invoice_header"]
        }
        for row in invoices:
            row["payer_party_key"] = payer_keys.get(row["invoice_key"])
        rows = _aggregate(invoices, "payer_party_key", "payer")
    elif name == "sales_by_selling_location":
        start, end = _date_parameters(parameters)
        invoices = _invoice_rows(tables, start, end)
        locations = {
            row.get("selling_location_key"): row.get("location_name")
            for row in tables["selling_location"]
        }
        for row in invoices:
            row["selling_location"] = locations.get(row.get("selling_location_key"))
        rows = _aggregate(invoices, "selling_location_key", "selling_location")
    elif name == "attribution_coverage":
        if parameters:
            raise ValueError(f"Unsupported report parameters: {sorted(parameters)}")
        invoice_by_key = {row.get("invoice_key"): row for row in tables["invoice_header"]}
        grouped = {}
        for invoice in tables["invoice_header"]:
            currency = _scalar(invoice.get("currency_code")) or "Unassigned"
            entry = grouped.setdefault(
                currency,
                {
                    "currency_code": currency,
                    "invoice_records": 0,
                    "attribution_records": 0,
                    "invoice_value": Decimal("0"),
                    "attributed_value": Decimal("0"),
                    "source_document_ids": set(),
                },
            )
            entry["invoice_records"] += 1
            entry["invoice_value"] += _decimal(invoice.get("total_amount"))
        for attribution in tables["attribution"]:
            if (
                attribution.get("target_table") != "invoice_header"
                or _scalar(attribution.get("is_failure")) is True
            ):
                continue
            invoice = invoice_by_key.get(attribution.get("target_key"))
            if invoice is None:
                continue
            currency = _scalar(invoice.get("currency_code")) or "Unassigned"
            entry = grouped[currency]
            entry["attribution_records"] += 1
            entry["attributed_value"] += _decimal(attribution.get("amount"))
            if attribution.get("source_document_id"):
                entry["source_document_ids"].add(attribution["source_document_id"])
        rows = []
        for entry in grouped.values():
            total = entry["invoice_value"]
            attributed = entry["attributed_value"]
            rows.append(
                {
                    **{
                        key: value
                        for key, value in entry.items()
                        if key not in {"invoice_value", "attributed_value", "source_document_ids"}
                    },
                    "invoice_value": str(total),
                    "attributed_value": str(attributed),
                    "unattributed_value": str(total - attributed),
                    "coverage_percent": str((attributed / total * 100) if total else Decimal("0")),
                    "source_document_ids": sorted(entry["source_document_ids"]),
                }
            )
        rows.sort(key=lambda row: row["currency_code"])
    else:
        start, end = _date_parameters(parameters)
        rows = []
        for shipment in tables["shipment"]:
            if not _date_in_range(shipment.get("ship_date"), start, end):
                continue
            delivered = _iso_date(shipment.get("delivery_date"), "delivery_date")
            committed = _iso_date(shipment.get("committed_date"), "committed_date")
            status = "not_measurable"
            if delivered and committed:
                status = "on_time" if delivered <= committed else "late"
            rows.append(
                {
                    "shipment_key": shipment.get("shipment_key"),
                    "ship_date": shipment.get("ship_date"),
                    "delivery_date": shipment.get("delivery_date"),
                    "committed_date": shipment.get("committed_date"),
                    "delivery_status": status,
                    "carrier_key": shipment.get("carrier_key"),
                    "lane_key": shipment.get("lane_key"),
                    "source_document_id": shipment.get("source_document_id"),
                    "review_status": shipment.get("review_status"),
                }
            )
    payload = {
        "schema_version": "crm_standard_report_v1",
        "report": name,
        "description": definitions[name]["description"],
        "batch_id": metadata.get("batch_id"),
        "source_export_sha256": metadata.get("source_export_sha256"),
        "parameters": parameters,
        "row_count": len(rows),
        "rows": rows,
        "approved_facts_only": True,
    }
    payload["report_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return payload
