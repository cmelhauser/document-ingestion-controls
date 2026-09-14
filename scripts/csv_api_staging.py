#!/usr/bin/env python3
"""Create a no-send CSV/API staging package from a verified canonical load plan."""

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

from canonical_load import build_plan, load_export, plan_checksum, schema_catalog, stable_checksum
from cli_help import apply_shared_help
from review_export_link import review_context_from_inputs, validate_review_context

CONTROL_COLUMNS = ("__canonical_row_json", "__canonical_row_sha256")


def sha256(path):
    """Return the checksum used to reconcile generated staging artifacts."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_plan(path):
    """Read a canonical-load plan without accepting an unstructured object."""
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict) or not isinstance(value.get("steps"), list):
        raise ValueError("Canonical load plan requires steps list")
    return value


def verify_plan(export, supplied):
    """Require the plan's ordered checksums to match this exact canonical export."""
    if "include_open_review" in supplied:
        raise ValueError("Canonical load plan contains unsupported include_open_review behavior")
    expected = build_plan(export)
    fields = ("batch_id", "source_export_sha256", "load_mode")
    if any(supplied.get(field) != expected.get(field) for field in fields):
        raise ValueError("Canonical load plan does not belong to this export")
    supplied_steps = [
        {key: step.get(key) for key in ("step", "table", "records", "checksum", "idempotency_keys")}
        for step in supplied["steps"]
        if isinstance(step, dict)
    ]
    expected_steps = [
        {key: step.get(key) for key in ("step", "table", "records", "checksum", "idempotency_keys")}
        for step in expected["steps"]
    ]
    if supplied_steps != expected_steps:
        raise ValueError("Canonical load plan checksums or order do not match this export")
    return expected


def csv_value(value):
    """Serialize values and neutralize spreadsheet-formula execution in CSV viewers.

    The envelope tells machine receivers to remove one leading security
    apostrophe. JSON remains authoritative, so this presentation-layer escape
    never changes the canonical record.
    """
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        rendered = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    else:
        rendered = str(value)
    return (
        f"'{rendered}" if rendered.startswith(("'", "=", "+", "-", "@", "\t", "\r")) else rendered
    )


def write_table(path, rows, declared_headings=None):
    """Write one stable CSV table, retaining every source column observed in the rows."""
    declared_headings = list(declared_headings or [])
    observed = {key for row in rows if isinstance(row, dict) for key in row}
    if observed & set(CONTROL_COLUMNS):
        raise ValueError("Canonical rows use a reserved staging control column")
    business_headings = declared_headings + sorted(observed - set(declared_headings))
    headings = [*business_headings, *CONTROL_COLUMNS]
    with Path(path).open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headings, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            canonical_json = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)
            writer.writerow(
                {
                    **{key: csv_value(row.get(key)) for key in business_headings},
                    "__canonical_row_json": canonical_json,
                    "__canonical_row_sha256": stable_checksum(row),
                }
            )
    return headings


def operation_contract(step, relative_csv, batch_id):
    """Build one exact, URL-safe receiver operation from a validated load step."""
    encoded_batch = quote(batch_id, safe="")
    table = step["table"]
    base = f"/v1/staging/batches/{encoded_batch}/tables/{table}"
    return {
        "step": step["step"],
        "table": table,
        "method": "idempotent_upsert",
        "relative_csv": relative_csv,
        "records": step["records"],
        "checksum": step["checksum"],
        "idempotency_keys": step["idempotency_keys"],
        "stage_endpoint_template": base,
        "reconcile_endpoint_template": f"{base}/reconciliation",
        "rejected_rows_endpoint_template": f"{base}/rejections",
        "reconciliation_required": True,
        "halt_on_rejection": True,
    }


def api_envelope(batch_id, source_export_sha256, operations):
    """Return the complete closed-world receiver contract for one staging package."""
    encoded_batch = quote(batch_id, safe="")
    return {
        "schema_version": "crm_api_load_envelope_v3",
        "batch_id": batch_id,
        "mode": "csv_api_staging_only",
        "api_upload_permitted": False,
        "source_export_sha256": source_export_sha256,
        "csv_cell_encoding": {
            "name": "excel_formula_escaped_v2",
            "decode": "Remove exactly one leading apostrophe when the cell begins with two apostrophes or when the remaining value begins with =, +, -, @, tab, or carriage return.",
            "null_and_empty_string_note": "Use __canonical_row_json to distinguish null, empty, and absent values.",
        },
        "authoritative_row_encoding": {
            "column": "__canonical_row_json",
            "checksum_column": "__canonical_row_sha256",
            "format": "compact JSON object with sorted keys",
            "required_for_machine_load": True,
        },
        "operations": operations,
        "rollback_endpoint_template": f"/v1/staging/batches/{encoded_batch}/rollback",
        "required_reconciliation_fields": [
            "records_attempted",
            "records_loaded",
            "records_rejected",
            "reject_causes",
            "checksum",
        ],
        "reconciliation_required": True,
        "commit_requires_zero_unaccounted_rows": True,
    }


def verify_package(root, plan):
    """Re-read every generated artifact before the atomic directory publish."""
    root = Path(root)
    manifest = json.loads((root / "staging_manifest.json").read_text())
    envelope = json.loads((root / manifest["api_envelope"]).read_text())
    review_context = validate_review_context(manifest.get("review_context"))
    if manifest.get("source_export_sha256") != plan["source_export_sha256"]:
        raise ValueError("Staging manifest source export checksum mismatch")
    if envelope.get("source_export_sha256") != plan["source_export_sha256"]:
        raise ValueError("API envelope source export checksum mismatch")
    if manifest.get("load_plan_sha256") != plan_checksum(plan):
        raise ValueError("Staging manifest load plan checksum mismatch")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != len(plan["steps"]):
        raise ValueError("Staging manifest does not account for every load step")
    if not all(isinstance(item, dict) for item in files):
        raise ValueError("Staging manifest file entries must be objects")
    if [item.get("table") for item in files] != [step["table"] for step in plan["steps"]]:
        raise ValueError("Staging manifest load-step topology mismatch")
    if len({item.get("path") for item in files}) != len(files):
        raise ValueError("Staging manifest contains duplicate file paths")
    records = 0
    operations = []
    for item, step in zip(files, plan["steps"], strict=True):
        if (
            item.get("step") != step["step"]
            or item.get("records") != step["records"]
            or item.get("canonical_rows_sha256") != step["checksum"]
            or item.get("idempotency_keys") != step["idempotency_keys"]
        ):
            raise ValueError("Staging manifest load-step topology mismatch")
        path = Path(item.get("path", ""))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Staging manifest contains an unsafe file path")
        target = root / path
        if (
            not target.is_file()
            or target.stat().st_size != item.get("bytes")
            or sha256(target) != item.get("sha256")
        ):
            raise ValueError(f"Staging file checksum mismatch: {path}")
        with target.open(newline="") as stream:
            reader = csv.DictReader(stream)
            headings = reader.fieldnames
            rows = list(reader)
        row_count = len(rows)
        if headings != item.get("columns") or row_count != item.get("records"):
            raise ValueError(f"Staging file shape mismatch: {path}")
        if not headings or headings[-2:] != list(CONTROL_COLUMNS):
            raise ValueError(f"Staging file is missing authoritative row controls: {path}")
        business_headings = headings[:-2]
        canonical_rows = []
        for row in rows:
            try:
                canonical = json.loads(row["__canonical_row_json"])
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError(f"Staging canonical row JSON is invalid: {path}") from exc
            if not isinstance(canonical, dict) or stable_checksum(canonical) != row.get(
                "__canonical_row_sha256"
            ):
                raise ValueError(f"Staging canonical row checksum mismatch: {path}")
            if any(row[field] != csv_value(canonical.get(field)) for field in business_headings):
                raise ValueError(f"Staging presentation columns do not match canonical row: {path}")
            canonical_rows.append(canonical)
        if stable_checksum(canonical_rows) != step["checksum"]:
            raise ValueError(f"Staging load-step checksum mismatch: {path}")
        records += row_count
        operations.append(operation_contract(step, str(path), manifest["batch_id"]))
    if records != manifest.get("records") or records != plan["total_records"]:
        raise ValueError("Staging record totals do not reconcile")
    if envelope != api_envelope(manifest["batch_id"], plan["source_export_sha256"], operations):
        raise ValueError("API envelope contract mismatch")
    content_descriptor = {
        "batch_id": manifest["batch_id"],
        "source_export_sha256": manifest["source_export_sha256"],
        "files": files,
        "api_envelope_sha256": sha256(root / manifest["api_envelope"]),
        "review_context": review_context,
    }
    if stable_checksum(content_descriptor) != manifest.get("package_content_sha256"):
        raise ValueError("Staging package content checksum mismatch")
    return {"records": records, "files": len(files), "verified": True}


def create_package(export_path, plan_path, out_dir, review_context=None):
    """Create a new target-neutral package; it never opens a network connection."""
    export, supplied = load_export(export_path), load_plan(plan_path)
    plan = verify_plan(export, supplied)
    review_context = validate_review_context(review_context)
    root = Path(out_dir)
    if root.exists():
        raise ValueError("Staging output directory must be new")
    root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent))
    try:
        files, operations = [], []
        declared = schema_catalog()
        for step in plan["steps"]:
            table, rows = step["table"], export["tables"].get(step["table"], [])
            destination = temporary / f"{step['step']:02d}_{table}.csv"
            headings = write_table(destination, rows, declared[table])
            relative = str(destination.relative_to(temporary))
            files.append(
                {
                    "step": step["step"],
                    "table": table,
                    "path": relative,
                    "sha256": sha256(destination),
                    "bytes": destination.stat().st_size,
                    "records": len(rows),
                    "columns": headings,
                    "canonical_rows_sha256": step["checksum"],
                    "idempotency_keys": step["idempotency_keys"],
                }
            )
            operations.append(operation_contract(step, relative, export["batch_id"]))
        envelope = api_envelope(export["batch_id"], plan["source_export_sha256"], operations)
        envelope_path = temporary / "api_load_envelope.json"
        envelope_path.write_text(json.dumps(envelope, indent=2) + "\n")
        records = sum(item["records"] for item in files)
        findings = (
            []
            if records
            else [
                "This package stages 0 rows. Every CSV has declared schema headings because the "
                "canonical export carried no review-clear row; do not reconcile it as a load."
            ]
        )
        content_descriptor = {
            "batch_id": export["batch_id"],
            "source_export_sha256": plan["source_export_sha256"],
            "files": files,
            "api_envelope_sha256": sha256(envelope_path),
            "review_context": review_context,
        }
        manifest = {
            "schema_version": "crm_staging_manifest_v2",
            "batch_id": export["batch_id"],
            "registry_version": export.get("registry_version"),
            "source_export_sha256": plan["source_export_sha256"],
            "load_plan_sha256": plan_checksum(plan),
            "package_content_sha256": stable_checksum(content_descriptor),
            "files": files,
            "api_envelope": "api_load_envelope.json",
            "api_upload_permitted": False,
            "records": records,
            "findings": findings,
            "reconciliation_required": True,
            "atomic_package_publish": True,
            "review_context": review_context,
        }
        (temporary / "staging_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        verify_package(temporary, plan)
        os.replace(temporary, root)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "batch_id": export["batch_id"],
        "source_export_sha256": plan["source_export_sha256"],
        "tables": len(files),
        "records": records,
        "findings": findings,
        "package_content_sha256": manifest["package_content_sha256"],
        "manifest": "staging_manifest.json",
        "package_kind": "staging",
        "review_context": review_context,
        "verified": True,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Create a checked, no-send CSV/API staging package."
    )
    parser.add_argument("canonical_export")
    parser.add_argument(
        "canonical_load_plan",
        help="Load plan the staging package must match exactly under strict matching.",
    )
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--review-session-id",
        help="Optional visual intake session ID to link in the staging manifest.",
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
        sys.exit(f"CSV/API staging failed: {exc}")


if __name__ == "__main__":
    main()
