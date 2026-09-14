#!/usr/bin/env python3
"""Create a verified no-send package of common CRM import files."""

import argparse
import csv
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from canonical_load import (
    IDEMPOTENCY_KEYS,
    LOAD_ORDER,
    load_export,
    plan_checksum,
    stable_checksum,
)
from cli_help import apply_shared_help
from csv_api_staging import csv_value, load_plan, sha256, verify_plan
from review_export_link import review_context_from_inputs, validate_review_context

CONTROL_COLUMNS = (
    "__canonical_table",
    "__canonical_key_json",
    "__canonical_row_json",
    "__canonical_row_sha256",
)


def _fields(*pairs):
    return tuple({"name": name, "source": source} for name, source in pairs)


OBJECTS = (
    {
        "name": "accounts",
        "source_table": "party",
        "file": "01_accounts.csv",
        "fields": _fields(
            ("external_id", "party_key"),
            ("account_name", "canonical_name"),
            ("account_number", "natural_key"),
            ("normalized_name", "normalized_name"),
            ("tax_id", "tax_id"),
            ("merged_into_external_id", "merged_into"),
            ("source_document_id", "source_document_id"),
            ("review_status", "review_status"),
            ("batch_id", "batch_id"),
        ),
    },
    {
        "name": "account_roles",
        "source_table": "party_role",
        "file": "02_account_roles.csv",
        "fields": _fields(
            ("account_external_id", "party_key"),
            ("role", "role"),
            ("first_seen", "first_seen"),
            ("last_seen", "last_seen"),
            ("document_count", "document_count"),
        ),
    },
    {
        "name": "addresses",
        "source_table": "address",
        "file": "03_addresses.csv",
        "fields": _fields(
            ("external_id", "address_key"),
            ("account_external_id", "party_key"),
            ("address_type", "address_type"),
            ("raw_address", "raw_address"),
            ("street_1", "line1"),
            ("street_2", "line2"),
            ("city", "city"),
            ("state_province", "state_province"),
            ("postal_code", "postal_code"),
            ("country_code", "country_code"),
            ("source_document_id", "source_document_id"),
            ("review_status", "review_status"),
            ("batch_id", "batch_id"),
        ),
    },
    {
        "name": "contacts",
        "source_table": "contact",
        "file": "04_contacts.csv",
        "fields": _fields(
            ("external_id", "contact_key"),
            ("account_external_id", "party_key"),
            ("full_name", "name"),
            ("title", "title"),
            ("email", "email"),
            ("phone", "phone"),
            ("source_document_id", "source_document_id"),
            ("review_status", "review_status"),
            ("batch_id", "batch_id"),
        ),
    },
    {
        "name": "products",
        "source_table": "item",
        "file": "05_products.csv",
        "fields": _fields(
            ("external_id", "item_key"),
            ("product_code", "item_code"),
            ("product_name", "description"),
            ("natural_key", "natural_key"),
            ("hs_code", "hs_code"),
            ("default_uom", "default_uom"),
            ("country_of_origin", "country_of_origin"),
            ("review_status", "review_status"),
            ("batch_id", "batch_id"),
        ),
    },
    {
        "name": "sales_locations",
        "source_table": "selling_location",
        "file": "06_sales_locations.csv",
        "fields": _fields(
            ("external_id", "selling_location_key"),
            ("location_name", "location_name"),
            ("natural_key", "natural_key"),
            ("city", "city"),
            ("state_province", "state_province"),
            ("postal_code", "postal_code"),
            ("country_code", "country_code"),
            ("territory_name", "territory_name"),
            ("effective_from", "effective_from"),
            ("effective_to", "effective_to"),
            ("review_status", "review_status"),
            ("batch_id", "batch_id"),
        ),
    },
    {
        "name": "shipments",
        "source_table": "shipment",
        "file": "07_shipments.csv",
        "fields": _fields(
            ("external_id", "shipment_key"),
            ("shipment_number", "shipment_number"),
            ("natural_key", "natural_key"),
            ("carrier_external_id", "carrier_key"),
            ("lane_external_id", "lane_key"),
            ("shipper_account_external_id", "shipper_party_key"),
            ("consignee_account_external_id", "consignee_party_key"),
            ("ship_date", "ship_date"),
            ("delivery_date", "delivery_date"),
            ("source_document_id", "source_document_id"),
            ("review_status", "review_status"),
            ("batch_id", "batch_id"),
        ),
    },
    {
        "name": "sales_transactions",
        "source_table": "invoice_header",
        "file": "08_sales_transactions.csv",
        "fields": _fields(
            ("external_id", "invoice_key"),
            ("transaction_number", "invoice_number"),
            ("natural_key", "natural_key"),
            ("transaction_date", "invoice_date"),
            ("due_date", "due_date"),
            ("payment_terms", "payment_terms"),
            ("purchase_order_number", "po_number"),
            ("vendor_account_external_id", "biller_party_key"),
            ("customer_account_external_id", "payer_party_key"),
            ("ship_to_account_external_id", "ship_to_party_key"),
            ("shipment_external_id", "shipment_key"),
            ("currency_code", "currency_code"),
            ("subtotal", "subtotal"),
            ("tax_amount", "tax_amount"),
            ("freight_amount", "freight_amount"),
            ("discount_amount", "discount_amount"),
            ("total_amount", "total_amount"),
            ("amount_due", "amount_due"),
            ("ack_number", "ack_number"),
            ("job_number", "job_number"),
            ("sales_location_external_id", "selling_location_key"),
            ("source_document_id", "source_document_id"),
            ("review_status", "review_status"),
            ("batch_id", "batch_id"),
        ),
    },
    {
        "name": "sales_transaction_lines",
        "source_table": "invoice_line",
        "file": "09_sales_transaction_lines.csv",
        "fields": _fields(
            ("external_id", "invoice_line_key"),
            ("transaction_external_id", "invoice_key"),
            ("line_number", "line_number"),
            ("product_external_id", "item_key"),
            ("description", "description"),
            ("quantity", "quantity"),
            ("uom_code", "uom_code"),
            ("unit_price", "unit_price"),
            ("extended_amount", "extended_amount"),
            ("discount", "discount"),
            ("ack_number", "ack_number"),
            ("job_number", "job_number"),
            ("sales_location_external_id", "selling_location_key"),
            ("review_status", "review_status"),
            ("batch_id", "batch_id"),
        ),
    },
    {
        "name": "transaction_charges",
        "source_table": "invoice_accessorial",
        "file": "10_transaction_charges.csv",
        "fields": _fields(
            ("external_id", "accessorial_key"),
            ("transaction_external_id", "invoice_key"),
            ("charge_code", "charge_code"),
            ("description", "raw_description"),
            ("amount", "amount"),
            ("ack_number", "ack_number"),
            ("job_number", "job_number"),
            ("sales_location_external_id", "selling_location_key"),
            ("review_status", "review_status"),
            ("batch_id", "batch_id"),
        ),
    },
    {
        "name": "payments",
        "source_table": "payment",
        "file": "11_payments.csv",
        "fields": _fields(
            ("external_id", "payment_key"),
            ("natural_key", "natural_key"),
            ("payment_date", "payment_date"),
            ("payer_account_external_id", "payer_party_key"),
            ("payee_account_external_id", "payee_party_key"),
            ("payment_method", "payment_method"),
            ("payment_reference", "payment_reference"),
            ("cheque_number", "cheque_number"),
            ("total_paid", "total_paid"),
            ("currency_code", "currency_code"),
            ("source_document_id", "source_document_id"),
            ("review_status", "review_status"),
            ("batch_id", "batch_id"),
        ),
    },
    {
        "name": "payment_applications",
        "source_table": "payment_application",
        "file": "12_payment_applications.csv",
        "fields": _fields(
            ("external_id", "application_key"),
            ("payment_external_id", "payment_key"),
            ("transaction_external_id", "invoice_key"),
            ("amount_applied", "amount_applied"),
            ("discount_taken", "discount_taken"),
            ("adjustment_reason", "adjustment_reason"),
            ("match_method", "match_method"),
            ("review_status", "review_status"),
            ("batch_id", "batch_id"),
        ),
    },
)

OFFICIAL_SOURCES = (
    {
        "vendor": "Salesforce",
        "url": "https://help.salesforce.com/s/articleView?id=sf.essentials_import_checklist.htm&language=en_US&type=5",
        "use": "Required account/contact/lead fields and split address components.",
    },
    {
        "vendor": "HubSpot",
        "url": "https://knowledge.hubspot.com/import-and-export/set-up-your-import-file",
        "use": "UTF-8 spreadsheet structure, object identifiers, and association requirements.",
    },
    {
        "vendor": "Microsoft Dynamics 365 / Dataverse",
        "url": "https://learn.microsoft.com/en-us/dynamics365/guidance/implementation-guide/data-management-product-specific-ce",
        "use": "Import formats, templates, alternate keys, and lookup resolution.",
    },
    {
        "vendor": "Zoho CRM",
        "url": "https://help.zoho.com/portal/en/kb/crm/faqs/data-administration/import/articles/faqs-import",
        "use": "Supported files, mandatory fields, duplicate checks, and relation mapping.",
    },
)

VENDOR_SUGGESTIONS = {
    "accounts": {
        "external_id": (
            "External ID (custom)",
            "Custom unique property",
            "Alternate key",
            "Record ID/custom unique field",
        ),
        "account_name": ("Account Name", "Company name", "Account Name", "Account Name"),
        "account_number": ("Account Number", "Custom property", "Account Number", "Account Number"),
    },
    "contacts": {
        "external_id": (
            "External ID (custom)",
            "Custom unique property",
            "Alternate key",
            "Record ID/custom unique field",
        ),
        "account_external_id": (
            "Account relationship external ID",
            "Associated company unique ID",
            "Parent customer alternate key",
            "Account relation key",
        ),
        "full_name": (
            "Last Name / split after review",
            "First name + Last name / split after review",
            "Full Name / split after review",
            "Last Name / split after review",
        ),
        "email": ("Email", "Email", "Email", "Email"),
        "phone": ("Phone", "Phone number", "Business Phone", "Phone"),
    },
    "products": {
        "external_id": (
            "Product external ID (custom)",
            "Custom unique property",
            "Product alternate key",
            "Product code/custom unique field",
        ),
        "product_code": ("Product Code", "SKU", "Product ID", "Product Code"),
        "product_name": ("Product Name", "Name", "Name", "Product Name"),
    },
}


def canonical_key(table, row):
    """Return the exact canonical idempotency key as a JSON list."""
    return [row[key] for key in IDEMPOTENCY_KEYS[table]]


def generic_rows(spec, export):
    """Map every source-table row exactly once without changing canonical values."""
    table = spec["source_table"]
    rows = []
    for source in export["tables"].get(table, []):
        rows.append(
            {
                **{field["name"]: source.get(field["source"]) for field in spec["fields"]},
                "__canonical_table": table,
                "__canonical_key_json": json.dumps(
                    canonical_key(table, source), separators=(",", ":")
                ),
                "__canonical_row_json": json.dumps(
                    source, sort_keys=True, separators=(",", ":"), allow_nan=False
                ),
                "__canonical_row_sha256": stable_checksum(source),
            }
        )
    return rows


def write_rows(path, spec, rows):
    """Write stable UTF-8 CSV headings and formula-safe presentation values."""
    headings = [*(field["name"] for field in spec["fields"]), *CONTROL_COLUMNS]
    with Path(path).open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=headings, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_value(value) for key, value in row.items()})
    return headings


def mapping_rows():
    """Return a complete target-mapping worksheet with proposal-only presets."""
    rows = []
    for spec in OBJECTS:
        suggestions = VENDOR_SUGGESTIONS.get(spec["name"], {})
        for field in spec["fields"]:
            vendor = suggestions.get(field["name"], ("Custom field/object",) * 4)
            rows.append(
                {
                    "generic_object": spec["name"],
                    "generic_field": field["name"],
                    "canonical_source": f"{spec['source_table']}.{field['source']}",
                    "target_object": "",
                    "target_field": "",
                    "target_field_type": "",
                    "required_for_create": "confirm_in_tenant",
                    "unique_or_alternate_key": "confirm_in_tenant",
                    "salesforce_suggestion": vendor[0],
                    "hubspot_suggestion": vendor[1],
                    "dynamics_suggestion": vendor[2],
                    "zoho_suggestion": vendor[3],
                    "mapping_status": "proposal_validate_against_tenant_metadata",
                }
            )
    return rows


def write_mapping(path):
    """Write the target-owner completion worksheet."""
    rows = mapping_rows()
    headings = list(rows[0])
    with Path(path).open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=headings)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows), headings


def write_plan(batch_id, source_export_sha256, package_objects):
    """Return the generic write sequence and authorization/reconciliation gates."""
    return {
        "schema_version": "crm_write_plan_v1",
        "mode": "no_send_mapping_and_import_preparation",
        "batch_id": batch_id,
        "source_export_sha256": source_export_sha256,
        "live_write_permitted": False,
        "requires_target_selection": True,
        "requires_tenant_metadata_export": True,
        "requires_completed_mapping_approval": True,
        "requires_separate_write_authorization": True,
        "rules": {
            "idempotency": "Use a tenant-enforced unique external/alternate key for every object.",
            "relationships": "Load parents first and resolve children only through external/alternate keys.",
            "rejections": "Halt on rejected rows; retain the exact rejected row, cause, and canonical key.",
            "reconciliation": "Account for attempted = created + updated + unchanged + rejected for every object.",
            "rollback": "Record created target IDs and before-images for updates; validate rollback in a sandbox.",
            "deletion": "No hard delete is authorized by this package.",
        },
        "stages": [
            {
                "step": 1,
                "name": "target_discovery",
                "requires": ["tenant metadata", "permissions", "limits", "duplicate rules"],
            },
            {
                "step": 2,
                "name": "mapping_approval",
                "requires": [
                    "completed field_mapping_template.csv",
                    "relationship keys",
                    "owner/time-zone/enumeration decisions",
                ],
            },
            {
                "step": 3,
                "name": "sandbox_dry_run",
                "requires": [
                    "target backup/export",
                    "small representative batch",
                    "zero unaccounted rows",
                ],
            },
            {
                "step": 4,
                "name": "authorized_idempotent_upsert",
                "requires": [
                    "separate written authorization",
                    "approved batch/checksums",
                    "captured target job IDs",
                ],
            },
            {
                "step": 5,
                "name": "reconciliation",
                "requires": [
                    "per-object outcomes",
                    "reject file",
                    "relationship checks",
                    "financial/control totals",
                ],
            },
            {
                "step": 6,
                "name": "acceptance_or_rollback",
                "requires": ["client acceptance", "retained manifest", "tested rollback procedure"],
            },
        ],
        "objects": [
            {
                "order": index,
                "generic_object": item["generic_object"],
                "source_table": item["source_table"],
                "file": item["path"],
                "records": item["records"],
                "source_idempotency_keys": list(IDEMPOTENCY_KEYS[item["source_table"]]),
            }
            for index, item in enumerate(package_objects, start=1)
        ],
    }


def _safe_path(value):
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("CRM import manifest contains an unsafe file path")
    return path


def verify_package(root, plan, export):
    """Reconstruct every generated row and verify complete canonical coverage."""
    root = Path(root)
    manifest = json.loads((root / "crm_import_manifest.json").read_text())
    review_context = validate_review_context(manifest.get("review_context"))
    if manifest.get("schema_version") != "crm_import_package_v1":
        raise ValueError("CRM import manifest schema mismatch")
    if manifest.get("live_write_permitted") is not False:
        raise ValueError("CRM import package must not authorize live writes")
    if manifest.get("source_export_sha256") != plan["source_export_sha256"]:
        raise ValueError("CRM import source export checksum mismatch")
    if manifest.get("load_plan_sha256") != plan_checksum(plan):
        raise ValueError("CRM import load plan checksum mismatch")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != len(OBJECTS):
        raise ValueError("CRM import manifest object topology mismatch")
    records = 0
    for item, spec in zip(files, OBJECTS, strict=True):
        if (
            item.get("generic_object") != spec["name"]
            or item.get("source_table") != spec["source_table"]
        ):
            raise ValueError("CRM import manifest object topology mismatch")
        relative = _safe_path(item.get("path", ""))
        target = root / relative
        if (
            not target.is_file()
            or target.stat().st_size != item.get("bytes")
            or sha256(target) != item.get("sha256")
        ):
            raise ValueError(f"CRM import file checksum mismatch: {relative}")
        with target.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            headings, actual = reader.fieldnames, list(reader)
        expected = generic_rows(spec, export)
        expected_headings = [*(field["name"] for field in spec["fields"]), *CONTROL_COLUMNS]
        if (
            headings != expected_headings
            or headings != item.get("columns")
            or len(actual) != item.get("records")
        ):
            raise ValueError(f"CRM import file shape mismatch: {relative}")
        reconstructed = []
        for row, expected_row in zip(actual, expected, strict=True):
            try:
                canonical = json.loads(row["__canonical_row_json"])
                key = json.loads(row["__canonical_key_json"])
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError(f"CRM import canonical controls are invalid: {relative}") from exc
            if (
                not isinstance(canonical, dict)
                or key != canonical_key(spec["source_table"], canonical)
                or stable_checksum(canonical) != row["__canonical_row_sha256"]
            ):
                raise ValueError(f"CRM import canonical controls do not reconcile: {relative}")
            if any(row[name] != csv_value(value) for name, value in expected_row.items()):
                raise ValueError(
                    f"CRM import presentation row does not match canonical source: {relative}"
                )
            reconstructed.append(canonical)
        if stable_checksum(reconstructed) != item.get("canonical_rows_sha256"):
            raise ValueError(f"CRM import canonical row checksum mismatch: {relative}")
        records += len(actual)
    coverage = manifest.get("canonical_coverage")
    if not isinstance(coverage, list) or [item.get("table") for item in coverage] != list(
        LOAD_ORDER
    ):
        raise ValueError("CRM import canonical coverage is incomplete")
    mapped = {spec["source_table"]: spec["name"] for spec in OBJECTS}
    for item in coverage:
        table = item["table"]
        expected_records = len(export["tables"].get(table, []))
        expected_disposition = "mapped_to_file" if table in mapped else "not_in_common_crm_profile"
        if (
            item.get("records") != expected_records
            or item.get("disposition") != expected_disposition
        ):
            raise ValueError("CRM import canonical coverage does not reconcile")
    mapping_path = root / _safe_path(manifest.get("field_mapping_template", ""))
    write_plan_path = root / _safe_path(manifest.get("write_plan", ""))
    sources_path = root / _safe_path(manifest.get("official_sources", ""))
    for path, expected_hash in (
        (mapping_path, manifest.get("field_mapping_sha256")),
        (write_plan_path, manifest.get("write_plan_sha256")),
        (sources_path, manifest.get("official_sources_sha256")),
    ):
        if not path.is_file() or sha256(path) != expected_hash:
            raise ValueError(f"CRM import companion checksum mismatch: {path.name}")
    generated_plan = json.loads(write_plan_path.read_text())
    if generated_plan.get("live_write_permitted") is not False or generated_plan.get("objects") != [
        {
            "order": index,
            "generic_object": item["generic_object"],
            "source_table": item["source_table"],
            "file": item["path"],
            "records": item["records"],
            "source_idempotency_keys": item["source_idempotency_keys"],
        }
        for index, item in enumerate(files, start=1)
    ]:
        raise ValueError("CRM write plan object topology mismatch")
    descriptor = {
        "batch_id": manifest["batch_id"],
        "source_export_sha256": manifest["source_export_sha256"],
        "files": files,
        "field_mapping_sha256": manifest["field_mapping_sha256"],
        "write_plan_sha256": manifest["write_plan_sha256"],
        "official_sources_sha256": manifest["official_sources_sha256"],
        "canonical_coverage": coverage,
        "review_context": review_context,
    }
    if stable_checksum(descriptor) != manifest.get("package_content_sha256"):
        raise ValueError("CRM import package content checksum mismatch")
    if records != manifest.get("records"):
        raise ValueError("CRM import record totals do not reconcile")
    return {"files": len(files), "records": records, "verified": True}


def create_package(export_path, plan_path, out_dir, review_context=None):
    """Build, self-verify, and atomically publish a new no-send import package."""
    export, supplied = load_export(export_path), load_plan(plan_path)
    plan = verify_plan(export, supplied)
    review_context = validate_review_context(review_context)
    root = Path(out_dir)
    if root.exists():
        raise ValueError("CRM import output directory must be new")
    root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent))
    try:
        files = []
        for spec in OBJECTS:
            rows = generic_rows(spec, export)
            destination = temporary / spec["file"]
            columns = write_rows(destination, spec, rows)
            files.append(
                {
                    "generic_object": spec["name"],
                    "source_table": spec["source_table"],
                    "path": spec["file"],
                    "records": len(rows),
                    "columns": columns,
                    "source_idempotency_keys": list(IDEMPOTENCY_KEYS[spec["source_table"]]),
                    "canonical_rows_sha256": stable_checksum(
                        export["tables"].get(spec["source_table"], [])
                    ),
                    "sha256": sha256(destination),
                    "bytes": destination.stat().st_size,
                }
            )
        mapping_path = temporary / "field_mapping_template.csv"
        mapping_count, _ = write_mapping(mapping_path)
        plan_path_out = temporary / "crm_write_plan.json"
        plan_path_out.write_text(
            json.dumps(
                write_plan(export["batch_id"], plan["source_export_sha256"], files), indent=2
            )
            + "\n"
        )
        sources_path = temporary / "official_import_sources.json"
        sources_path.write_text(
            json.dumps(
                {"schema_version": "crm_import_sources_v1", "sources": OFFICIAL_SOURCES}, indent=2
            )
            + "\n"
        )
        mapped = {spec["source_table"]: spec["name"] for spec in OBJECTS}
        coverage = [
            {
                "table": table,
                "records": len(export["tables"].get(table, [])),
                "disposition": "mapped_to_file" if table in mapped else "not_in_common_crm_profile",
                "generic_object": mapped.get(table),
            }
            for table in LOAD_ORDER
        ]
        records = sum(item["records"] for item in files)
        findings = (
            []
            if records
            else [
                "The approved canonical export contains zero rows in the common CRM profile; do not treat headings-only files as a completed import."
            ]
        )
        descriptor = {
            "batch_id": export["batch_id"],
            "source_export_sha256": plan["source_export_sha256"],
            "files": files,
            "field_mapping_sha256": sha256(mapping_path),
            "write_plan_sha256": sha256(plan_path_out),
            "official_sources_sha256": sha256(sources_path),
            "canonical_coverage": coverage,
            "review_context": review_context,
        }
        manifest = {
            "schema_version": "crm_import_package_v1",
            "mode": "no_send_common_crm_import_files",
            "batch_id": export["batch_id"],
            "registry_version": export.get("registry_version"),
            "source_export_sha256": plan["source_export_sha256"],
            "load_plan_sha256": plan_checksum(plan),
            "package_content_sha256": stable_checksum(descriptor),
            "files": files,
            "records": records,
            "field_mapping_template": mapping_path.name,
            "field_mapping_rows": mapping_count,
            "field_mapping_sha256": descriptor["field_mapping_sha256"],
            "write_plan": plan_path_out.name,
            "write_plan_sha256": descriptor["write_plan_sha256"],
            "official_sources": sources_path.name,
            "official_sources_sha256": descriptor["official_sources_sha256"],
            "canonical_coverage": coverage,
            "findings": findings,
            "live_write_permitted": False,
            "target_mapping_is_approved": False,
            "reconciliation_required": True,
            "atomic_package_publish": True,
            "review_context": review_context,
        }
        (temporary / "crm_import_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        verify_package(temporary, plan, export)
        os.replace(temporary, root)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "batch_id": export["batch_id"],
        "source_export_sha256": plan["source_export_sha256"],
        "files": len(files),
        "records": records,
        "findings": findings,
        "package_content_sha256": manifest["package_content_sha256"],
        "manifest": "crm_import_manifest.json",
        "package_kind": "common_import",
        "review_context": review_context,
        "verified": True,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Create verified no-send common CRM import files and a write plan."
    )
    parser.add_argument("canonical_export", help="Approved canonical export JSON to reshape.")
    parser.add_argument(
        "canonical_load_plan", help="Exact strict canonical load plan for the export."
    )
    parser.add_argument("--out", required=True, help="New output directory for the import package.")
    parser.add_argument(
        "--review-session-id",
        help="Optional visual intake session ID to link in the import manifest.",
    )
    parser.add_argument(
        "--review-source-set-sha256",
        help="Optional visual intake source_set_sha256 paired with --review-session-id.",
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        review_context = review_context_from_inputs(
            args.review_session_id, args.review_source_set_sha256
        )
        print(
            json.dumps(
                create_package(
                    args.canonical_export,
                    args.canonical_load_plan,
                    args.out,
                    review_context=review_context,
                ),
                sort_keys=True,
            )
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"CRM import packaging failed: {exc}")


if __name__ == "__main__":
    main()
