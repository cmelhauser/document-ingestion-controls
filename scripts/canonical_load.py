#!/usr/bin/env python3
"""Validate and order a vendor-neutral canonical export for idempotent CRM loading."""

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "assets" / "canonical_schema.sql"

LOAD_ORDER = (
    "currency",
    "unit_of_measure",
    "country",
    "charge_code",
    "document_type",
    "selling_location",
    "document",
    "party",
    "party_name_variant",
    "address",
    "contact",
    "party_role",
    "carrier",
    "item",
    "lane",
    "acknowledgement",
    "job",
    "shipment",
    "invoice_header",
    "invoice_line",
    "invoice_accessorial",
    "payment",
    "payment_application",
    "attribution",
    "handwriting_region",
    "amendment",
    "exception_event",
    "financial_event",
)
IDEMPOTENCY_KEYS = {
    "currency": ("currency_code",),
    "unit_of_measure": ("uom_code",),
    "country": ("country_code",),
    "charge_code": ("charge_code",),
    "document_type": ("document_type",),
    "selling_location": ("selling_location_key",),
    "document": ("document_id",),
    "party": ("party_key",),
    "party_name_variant": ("party_key", "raw_name"),
    "address": ("address_key",),
    "contact": ("contact_key",),
    "party_role": ("party_key", "role"),
    "carrier": ("carrier_key",),
    "item": ("item_key",),
    "lane": ("lane_key",),
    "acknowledgement": ("ack_key",),
    "job": ("job_key",),
    "shipment": ("shipment_key",),
    "invoice_header": ("invoice_key",),
    "invoice_line": ("invoice_line_key",),
    "invoice_accessorial": ("accessorial_key",),
    "payment": ("payment_key",),
    "payment_application": ("application_key",),
    "attribution": ("attribution_id",),
    "handwriting_region": ("region_id",),
    "amendment": ("amendment_id",),
    "exception_event": ("exception_id",),
    "financial_event": ("event_id",),
}
REVIEW_STATUSES = {"auto_accepted", "sampled_verified", "exception_resolved"}
BATCHED_TABLES = set(LOAD_ORDER) - {
    "currency",
    "unit_of_measure",
    "country",
    "charge_code",
    "document_type",
    "party_name_variant",
    "party_role",
}
PROVENANCE_RULES = {
    "currency": {"mode": "source_independent", "links": ()},
    "unit_of_measure": {"mode": "source_independent", "links": ()},
    "country": {"mode": "source_independent", "links": ()},
    "charge_code": {"mode": "source_independent", "links": ()},
    "document_type": {"mode": "source_independent", "links": ()},
    "selling_location": {"mode": "source_independent", "links": ()},
    "document": {
        "mode": "source_document",
        "required_fields": ("source_file", "source_page_range", "source_sha256"),
        "links": (),
    },
    "party": {
        "mode": "source_independent",
        "links": (("source_document_id", "document", "document_id", False),),
    },
    "party_name_variant": {
        "mode": "source_independent",
        "links": (("party_key", "party", "party_key", True),),
    },
    "address": {
        "mode": "linked",
        "links": (
            ("party_key", "party", "party_key", True),
            ("source_document_id", "document", "document_id", False),
        ),
    },
    "contact": {
        "mode": "linked",
        "links": (
            ("party_key", "party", "party_key", True),
            ("source_document_id", "document", "document_id", False),
        ),
    },
    "party_role": {
        "mode": "source_independent",
        "links": (("party_key", "party", "party_key", True),),
    },
    "carrier": {
        "mode": "source_independent",
        "links": (("party_key", "party", "party_key", False),),
    },
    "item": {"mode": "source_independent", "links": ()},
    "lane": {"mode": "source_independent", "links": ()},
    "acknowledgement": {
        "mode": "source_independent",
        "links": (("source_document_id", "document", "document_id", False),),
    },
    "job": {
        "mode": "source_independent",
        "links": (("source_document_id", "document", "document_id", False),),
    },
    "shipment": {
        "mode": "linked",
        "links": (("source_document_id", "document", "document_id", True),),
    },
    "invoice_header": {
        "mode": "linked",
        "links": (("source_document_id", "document", "document_id", True),),
    },
    "invoice_line": {
        "mode": "linked",
        "links": (("invoice_key", "invoice_header", "invoice_key", True),),
    },
    "invoice_accessorial": {
        "mode": "linked",
        "links": (("invoice_key", "invoice_header", "invoice_key", True),),
    },
    "payment": {
        "mode": "linked",
        "links": (("source_document_id", "document", "document_id", True),),
    },
    "payment_application": {
        "mode": "linked",
        "links": (
            ("payment_key", "payment", "payment_key", True),
            ("invoice_key", "invoice_header", "invoice_key", True),
        ),
    },
    "attribution": {
        "mode": "linked",
        "links": (("source_document_id", "document", "document_id", True),),
    },
    "handwriting_region": {
        "mode": "linked",
        "links": (("document_id", "document", "document_id", True),),
    },
    "amendment": {
        "mode": "linked",
        "links": (("evidence_document_id", "document", "document_id", True),),
    },
    "exception_event": {
        "mode": "linked",
        "links": (
            ("document_id", "document", "document_id", False),
            ("shipment_key", "shipment", "shipment_key", False),
            ("invoice_key", "invoice_header", "invoice_key", False),
        ),
    },
    "financial_event": {
        "mode": "linked",
        "links": (("source_document_id", "document", "document_id", True),),
    },
}


def schema_catalog(schema_path=SCHEMA_PATH):
    """Return declared canonical columns from the authoritative PostgreSQL DDL.

    CSV staging, the local CRM snapshot, API, MCP, and schema workbook all need
    the same field inventory. Reading the checked-in DDL keeps those surfaces
    from acquiring separate, stale hand-maintained column lists.
    """
    source = Path(schema_path).read_text()
    catalog = {}
    for match in re.finditer(
        r"CREATE\s+TABLE\s+([a-z_][a-z0-9_]*)\s*\((.*?)\n\);",
        source,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        table, body = match.group(1), match.group(2)
        columns = []
        for raw_line in body.splitlines():
            line = raw_line.split("--", 1)[0].strip()
            if not line:
                continue
            token = line.split(None, 1)[0].rstrip(",").lower()
            if token in {"constraint", "primary", "unique", "foreign", "check"}:
                continue
            if re.fullmatch(r"[a-z_][a-z0-9_]*", token):
                columns.append(token)
        if not columns:
            raise ValueError(f"Canonical schema table {table} declares no columns")
        catalog[table] = columns
    missing = set(LOAD_ORDER) - set(catalog)
    if missing:
        raise ValueError(f"Canonical schema is missing load tables: {sorted(missing)}")
    return catalog


def stable_checksum(value):
    """Fingerprint rows with a stable JSON representation for reconciliation."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def plan_checksum(plan):
    """Hash the semantic load plan while excluding its informational timestamp."""
    return stable_checksum({key: value for key, value in plan.items() if key != "generated_at"})


def load_export(path):
    """Load the controlled canonical-export envelope without accepting bare lists."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("tables"), dict):
        raise ValueError("Canonical export must contain a tables object")
    if not isinstance(data.get("batch_id"), str) or not data["batch_id"]:
        raise ValueError("Canonical export requires batch_id")
    return data


def validate_rows(table, rows, batch_id):
    """Require typed rows, primary IDs, batch lineage, and review-safe export rows."""
    if table not in LOAD_ORDER or not isinstance(rows, list):
        raise ValueError(f"Unsupported canonical table: {table}")
    seen_keys = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{table} rows must be objects")
        for key in IDEMPOTENCY_KEYS[table]:
            if not isinstance(row.get(key), str) or not row[key]:
                raise ValueError(f"{table} row requires {key}")
        idempotency_key = tuple(row[key] for key in IDEMPOTENCY_KEYS[table])
        if idempotency_key in seen_keys:
            raise ValueError(f"{table} contains duplicate idempotency key: {idempotency_key}")
        seen_keys.add(idempotency_key)
        if table in BATCHED_TABLES and row.get("batch_id") != batch_id:
            raise ValueError(f"{table} row batch_id must match export batch_id")
        if (table in BATCHED_TABLES or "review_status" in row) and row.get(
            "review_status"
        ) not in REVIEW_STATUSES:
            raise ValueError(
                f"{table} row review_status {row.get('review_status')!r} is not review-clear"
            )


def validate_provenance(tables):
    """Require every factual row to reach retained source evidence through declared links."""
    indexes = {}
    for _table, rule in PROVENANCE_RULES.items():
        for _field, parent_table, parent_key, _required in rule.get("links", ()):
            index_key = (parent_table, parent_key)
            if index_key not in indexes:
                indexes[index_key] = {
                    row[parent_key]: row
                    for row in tables.get(parent_table, [])
                    if isinstance(row, dict) and isinstance(row.get(parent_key), str)
                }

    def resolves(table, row):
        """Report whether a canonical row resolves to review-clear, provenance-linked evidence."""
        rule = PROVENANCE_RULES[table]
        if rule["mode"] == "source_document":
            for field in rule["required_fields"]:
                if not isinstance(row.get(field), str) or not row[field].strip():
                    raise ValueError(f"document row requires non-empty {field}")
            return True

        reaches_document = False
        for field, parent_table, parent_key, required in rule["links"]:
            value = row.get(field)
            if value in (None, ""):
                if required:
                    raise ValueError(f"{table} row requires provenance parent {field}")
                continue
            if not isinstance(value, str):
                raise ValueError(f"{table} provenance parent {field} must be a non-empty string")
            parent = indexes[(parent_table, parent_key)].get(value)
            if parent is None:
                raise ValueError(
                    f"{table} row {field}={value!r} does not resolve to {parent_table}.{parent_key}"
                )
            reaches_document = resolves(parent_table, parent) or reaches_document
        if rule["mode"] == "linked" and not reaches_document:
            raise ValueError(f"{table} row does not resolve to a provenance-valid document")
        return reaches_document

    for table in LOAD_ORDER:
        for row in tables.get(table, []):
            resolves(table, row)


def build_plan(export):
    """Create ordered, checksummed steps that an industry CRM adapter can consume."""
    tables = export["tables"]
    unknown = set(tables) - set(LOAD_ORDER)
    if unknown:
        raise ValueError(f"Unsupported canonical tables: {sorted(unknown)}")
    steps = []
    for index, table in enumerate(LOAD_ORDER, start=1):
        rows = tables.get(table, [])
        validate_rows(table, rows, export["batch_id"])
        steps.append(
            {
                "step": index,
                "table": table,
                "records": len(rows),
                "checksum": stable_checksum(rows),
                "idempotency_keys": list(IDEMPOTENCY_KEYS[table]),
            }
        )
    validate_provenance(tables)
    total = sum(step["records"] for step in steps)
    # Rule 9: a control that processed nothing has not passed. An export whose
    # rows were all withheld still validates -- every empty table is trivially
    # valid -- and produced a 28-step plan that reads exactly like a real one.
    # An operator reconciling that plan would be reconciling nothing.
    findings = []
    if not total:
        findings.append(
            "This plan orders 0 rows. The export carried no review-clear, "
            "provenance-linked row, so there is nothing to load; read the export's "
            "own exception artifact for which control withheld each document."
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "batch_id": export["batch_id"],
        "registry_version": export.get("registry_version"),
        "source_export_sha256": stable_checksum(export),
        "load_mode": "idempotent_upsert_by_natural_or_primary_key",
        "steps": steps,
        "total_records": total,
        "findings": findings,
        "reconciliation_required": True,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Create a vendor-neutral canonical CRM load orchestration plan."
    )
    parser.add_argument("canonical_export")
    parser.add_argument("--out", required=True)
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        output = Path(args.out)
        if output.exists():
            raise ValueError(f"Output already exists: {output}")
        plan = build_plan(load_export(args.canonical_export))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(plan, indent=2) + "\n")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Canonical load planning failed: {exc}")
    print(
        json.dumps(
            {
                "batch_id": plan["batch_id"],
                "steps": len(plan["steps"]),
                "records": plan["total_records"],
            },
            sort_keys=True,
        )
    )
    for finding in plan["findings"]:
        print(f"  - {finding}")


if __name__ == "__main__":
    main()
