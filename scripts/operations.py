#!/usr/bin/env python3
"""Operational safeguards for reproducible, privacy-aware document-ingestion runs.

The script never contacts an OCR provider, stores a secret, or changes source
documents. It validates provider handoff contracts, builds immutable manifests,
records resumable stage state, inventories obvious PII, and creates safe review
exports from an existing final-review JSON artifact.
"""

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import xlsxwriter
from cli_help import apply_shared_help
from llm_usage_report import write_report
from project_metadata import MANIFEST_SCHEMA_VERSION, PROJECT_VERSION
from runtime_config import effective_settings_snapshot

PII_PATTERNS = {
    "email": re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    "us_ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "card_like": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
}
STAGES = ("profile", "intake", "extract", "validate", "review")
REVIEW_FIELDS = (
    "priority",
    "document_id",
    "page_id",
    "region_id",
    "field",
    "reason",
    "review_source",
    "disposition",
)

REVIEW_HEADER_DEFINITIONS = (
    ("priority", "enum", "yes", "Review urgency: critical, high, or normal."),
    ("document_id", "string", "yes", "Stable document or immutable page identifier."),
    ("page_id", "string", "yes", "Immutable page identifier; one retained PDF page."),
    ("region_id", "string", "yes", "Detected-region ID; blank when not region-specific."),
    ("field", "string", "yes", "Canonical field path or review target."),
    ("reason", "string", "yes", "Machine-readable reason; never a silent correction."),
    ("review_source", "string", "yes", "Source collection, such as exceptions or documents."),
    ("disposition", "enum", "yes", "Client-review-required for an open package."),
)
REVIEW_SCHEMA_DEFINITIONS = (
    ("Final review JSON", "summary.schema_version", "string", "yes", "Versioned contract marker."),
    (
        "Final review JSON",
        "summary.generated_at",
        "ISO 8601 datetime",
        "yes",
        "UTC final-gate time.",
    ),
    (
        "Final review JSON",
        "summary.source_artifacts",
        "array[string]",
        "yes",
        "Artifacts supplied to final gate.",
    ),
    (
        "Final review JSON",
        "summary.client_review_items",
        "integer",
        "yes",
        "Items requiring client review.",
    ),
    (
        "Final review JSON",
        "summary.gate_status",
        "enum",
        "yes",
        "clear or blocked pending client review.",
    ),
    ("Final review JSON", "summary.findings", "array[string]", "yes", "Scope and gate statements."),
    (
        "Final review JSON",
        "items",
        "array[ReviewItem]",
        "yes",
        "Exhaustive unresolved review items.",
    ),
    (
        "CSV / Client Review",
        "A:H / header row",
        "eight string columns",
        "yes",
        "Flat JSON item view.",
    ),
    ("Workbook", "Header Definitions", "table", "yes", "Definitions for every review header."),
    ("Workbook", "Schema Definitions", "table", "yes", "JSON and workbook contract definitions."),
    ("Workbook", "Runbook", "table", "yes", "Client review and return instructions."),
)
REVIEW_RUNBOOK = (
    (
        "1. Open JSON",
        "Use the JSON summary as official gate state and item count.",
        "Do not edit source documents.",
    ),
    (
        "2. Filter workbook",
        "Use Client Review filters to work critical items first.",
        "CSV/XLSX are reviewer aids, not canonical.",
    ),
    (
        "3. Return decision",
        "Confirm, correct with cited source, or mark not applicable.",
        "Corrections are amendments; originals remain.",
    ),
    (
        "4. Resolve gate",
        "Return decisions and supporting references to the operator.",
        "Only regenerated clear JSON permits handoff.",
    ),
)


def sha256(path):
    """Hash an artifact without changing it."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_manifest(paths, config=None):
    """Create a portable reproducibility manifest without operator filesystem paths."""
    files = []
    for index, raw in enumerate(paths, start=1):
        path = Path(raw)
        if not path.is_file():
            raise ValueError(f"Artifact is not a readable file: {path}")
        files.append(
            {
                "artifact_id": f"artifact-{index:04d}",
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    encoded = json.dumps(config or {}, sort_keys=True, separators=(",", ":")).encode()
    result = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "pipeline_version": PROJECT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "artifacts": files,
        "configuration_sha256": hashlib.sha256(encoded).hexdigest(),
    }
    # Only the explicitly redacted runtime snapshot is retained. Arbitrary
    # --config values remain hash-only so operators cannot accidentally place
    # credentials or client inputs in a portable manifest.
    if isinstance(config, dict) and "effective_runtime_settings" in config:
        result["effective_runtime_settings"] = config["effective_runtime_settings"]
    return result


def write_new_text(path, text):
    """Write a new artifact without replacing an earlier run output."""
    with Path(path).open("x") as stream:
        stream.write(text)


def replace_text_atomically(path, text):
    """Replace resumable state atomically while keeping other outputs immutable."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def require_new_outputs(paths):
    """Reject a multi-file export before any member can overwrite retained evidence."""
    existing = [str(Path(path)) for path in paths if Path(path).exists()]
    if existing:
        raise ValueError(f"Output already exists; start a new run path: {', '.join(existing)}")


def load_json(path):
    """Load a JSON object with an explicit schema-boundary error."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("JSON input must be an object")
    return data


def update_state(path, stage, manifest_hash):
    """Record a stage idempotently; a changed manifest must start a new run."""
    if stage not in STAGES:
        raise ValueError(f"Unknown stage: {stage}")
    state = load_json(path) if Path(path).exists() else {"completed": []}
    if state.get("manifest_hash") not in (None, manifest_hash):
        raise ValueError("Run state belongs to a different manifest")
    completed = state.get("completed", [])
    if not isinstance(completed, list):
        raise ValueError("Run state completed must be a list")
    index = STAGES.index(stage)
    required = list(STAGES[:index])
    if any(item not in completed for item in required):
        raise ValueError(f"Stage {stage} requires prior stages: {', '.join(required)}")
    if stage not in completed:
        completed.append(stage)
    return {
        "manifest_hash": manifest_hash,
        "completed": completed,
        "updated_at": datetime.now(UTC).isoformat(),
    }


def validate_adapter(data, adapter_type, credential_env):
    """Validate normalized provider/policy handoffs and prohibit embedded secrets."""
    if not credential_env or not re.fullmatch(r"[A-Z][A-Z0-9_]*", credential_env):
        raise ValueError("credential environment variable must be uppercase with underscores")
    if any(key in data for key in ("api_key", "token", "secret", "password")):
        raise ValueError("Adapter result must not embed credentials")
    engine = data.get("engine")
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError("Adapter result requires engine")
    if adapter_type == "ocr":
        records = data.get("records")
        valid = isinstance(records, list) and all(
            isinstance(item, dict) and item.get("document_id") for item in records
        )
    elif adapter_type == "htr":
        records = data.get("annotations")
        valid = isinstance(records, list) and all(
            isinstance(item, dict)
            and item.get("document_id")
            and item.get("page_id")
            and item.get("region_id")
            for item in records
        )
    elif adapter_type == "schema_discovery":
        policy = data.get("policy")
        records = data.get("template_count")
        valid = (
            data.get("adapter_type") == "schema_discovery"
            and isinstance(policy, dict)
            and policy.get("decision_mode") == "proposal_only"
            and policy.get("client_approval_permitted") is False
            and policy.get("automatic_new_field_creation") is False
            and isinstance(records, int)
            and records >= 0
        )
    elif adapter_type == "allocation_policy":
        policy = data.get("policy")
        records = data.get("template_count")
        valid = (
            data.get("adapter_type") == "allocation_policy"
            and isinstance(policy, dict)
            and policy.get("decision_mode") == "proposal_only"
            and policy.get("client_approval_permitted") is False
            and policy.get("automatic_sales_credit") is False
            and isinstance(records, int)
            and records >= 0
        )
    elif adapter_type == "llm_adjudication":
        policy = data.get("policy")
        records = data.get("candidate_count")
        valid = (
            data.get("adapter_type") == "llm_adjudication"
            and isinstance(policy, dict)
            and policy.get("decision_mode") == "amendment_proposal_only"
            and policy.get("client_approval_permitted") is False
            and isinstance(records, int)
            and records >= 0
        )
    elif adapter_type == "table_comprehension":
        policy = data.get("policy")
        records = data.get("records")
        valid = (
            data.get("adapter_type") == "table_comprehension"
            and isinstance(policy, dict)
            and policy.get("decision_mode") == "source_row_proposal_only"
            and policy.get("client_approval_permitted") is False
            and policy.get("automatic_canonical_mapping") is False
            and data.get("proposal_only") is True
            and data.get("requires_independent_consensus") is True
            and data.get("same_model_roles_not_independent") is True
            and isinstance(records, list)
            and all(isinstance(item, dict) and item.get("page_id") for item in records)
        )
    else:
        raise ValueError(
            "adapter type must be ocr, htr, llm_adjudication, schema_discovery, allocation_policy, or table_comprehension"
        )
    if not valid:
        raise ValueError("Adapter records do not meet the required evidence contract")
    return {
        "engine": engine,
        "adapter_type": adapter_type,
        "credential_reference": credential_env,
        "record_count": records
        if adapter_type in {"llm_adjudication", "schema_discovery", "allocation_policy"}
        else len(records),
        "raw_response_retention_required": True,
    }


def privacy_inventory(paths):
    """Inventory obvious PII in text artifacts; review hits rather than redact originals."""
    findings = []
    for index, raw in enumerate(paths, start=1):
        path = Path(raw)
        if not path.is_file():
            raise ValueError(f"Text artifact is not a readable file: {path}")
        text = path.read_text(errors="replace")
        for label, pattern in PII_PATTERNS.items():
            count = len(pattern.findall(text))
            if count:
                findings.append(
                    {
                        "artifact_id": f"artifact-{index:04d}",
                        "name": path.name,
                        "category": label,
                        "count": count,
                        "disposition": "privacy_review_required",
                    }
                )
    return findings


def review_rows(data):
    """Validate canonical final-review JSON and make stable reviewer rows."""
    summary = data.get("summary")
    items = data.get("items")
    if not isinstance(summary, dict) or not isinstance(items, list):
        raise ValueError("Final review artifact must contain summary object and items list")
    if not isinstance(summary.get("schema_version"), str) or not summary["schema_version"]:
        raise ValueError("Final review summary requires schema_version")
    if not isinstance(summary.get("generated_at"), str) or not summary["generated_at"]:
        raise ValueError("Final review summary requires generated_at")
    if not isinstance(summary.get("source_artifacts"), list) or not all(
        isinstance(item, str) for item in summary["source_artifacts"]
    ):
        raise ValueError("Final review summary requires source_artifacts string list")
    if not isinstance(summary.get("findings"), list) or not all(
        isinstance(item, str) for item in summary["findings"]
    ):
        raise ValueError("Final review summary requires findings string list")
    if summary.get("client_review_items") != len(items):
        raise ValueError("Final review item count does not match items list")
    expected_gate = "blocked_pending_client_review" if items else "clear"
    if summary.get("gate_status") != expected_gate:
        raise ValueError("Final review gate status does not match unresolved item count")
    rows = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Every final review item must be an object")
        missing = [field for field in REVIEW_FIELDS if field not in item]
        if missing:
            raise ValueError(f"Final review item is missing required fields: {', '.join(missing)}")
        if item["priority"] not in {"critical", "high", "normal"}:
            raise ValueError("Final review item has invalid priority")
        if item["disposition"] != "client_review_required":
            raise ValueError("Final review item must require client review")
        if not item["reason"] or not item["review_source"]:
            raise ValueError("Final review item requires reason and review_source")
        if not all(isinstance(item[field], str) for field in REVIEW_FIELDS):
            raise ValueError("Final review item fields must be strings")
        rows.append({key: item[key] for key in REVIEW_FIELDS})
    return rows


def write_reference_sheet(sheet, title_text, headings, rows, widths, title, header, wrap):
    """Write a self-describing worksheet with stable headings and widths."""
    sheet.hide_gridlines(2)
    sheet.merge_range(0, 0, 0, len(headings) - 1, title_text, title)
    sheet.write_row(2, 0, headings, header)
    for row_index, row in enumerate(rows, start=3):
        sheet.write_row(row_index, 0, row, wrap)
    sheet.autofilter(2, 0, 2 + len(rows), len(headings) - 1)
    sheet.freeze_panes(3, 0)
    for column, width in enumerate(widths):
        sheet.set_column(column, column, width)


def write_review_exports(rows, csv_path, html_path, xlsx_path):
    """Write review aids while keeping the supplied JSON package canonical."""
    require_new_outputs((csv_path, html_path, xlsx_path))
    with Path(csv_path).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    cells = "".join(f"<th>{html.escape(field)}</th>" for field in REVIEW_FIELDS)
    body = "".join(
        "<tr>"
        + "".join(f"<td>{html.escape(str(row[field]))}</td>" for field in REVIEW_FIELDS)
        + "</tr>"
        for row in rows
    )
    Path(html_path).write_text(
        f'<!doctype html><meta charset="utf-8"><title>Client review</title><table><thead><tr>{cells}</tr></thead><tbody>{body}</tbody></table>\n'
    )
    workbook = xlsxwriter.Workbook(
        xlsx_path, {"strings_to_formulas": False, "strings_to_urls": False}
    )
    sheet = workbook.add_worksheet("Client Review")
    title = workbook.add_format(
        {"bold": True, "font_size": 16, "font_color": "#FFFFFF", "bg_color": "#1F4E78"}
    )
    note = workbook.add_format({"italic": True, "font_color": "#595959"})
    header = workbook.add_format(
        {"bold": True, "font_color": "#FFFFFF", "bg_color": "#4472C4", "align": "center"}
    )
    wrap = workbook.add_format({"text_wrap": True, "valign": "top"})
    sheet.hide_gridlines(2)
    sheet.merge_range("A1:H1", "Final Client Review", title)
    sheet.merge_range(
        "A2:H2",
        "Review aid only. The supplied final_client_review.json remains the canonical record.",
        note,
    )
    sheet.write_row(3, 0, REVIEW_FIELDS, header)
    for row_index, row in enumerate(rows, start=4):
        sheet.write_row(row_index, 0, [str(row[field]) for field in REVIEW_FIELDS])
    last_row = 3 + len(rows)
    sheet.autofilter(3, 0, last_row, len(REVIEW_FIELDS) - 1)
    sheet.freeze_panes(4, 0)
    for column, width in enumerate((12, 22, 16, 16, 24, 40, 24, 28)):
        sheet.set_column(column, column, width)
    if rows:
        sheet.conditional_format(
            4,
            0,
            last_row,
            0,
            {
                "type": "text",
                "criteria": "containing",
                "value": "high",
                "format": workbook.add_format({"bg_color": "#FCE4D6"}),
            },
        )
    write_reference_sheet(
        workbook.add_worksheet("Header Definitions"),
        "Client Review Header Definitions",
        ("Header", "Type", "Required", "Definition"),
        REVIEW_HEADER_DEFINITIONS,
        (22, 14, 14, 72),
        title,
        header,
        wrap,
    )
    write_reference_sheet(
        workbook.add_worksheet("Schema Definitions"),
        "Artifact Schema Definitions",
        ("Artifact", "Path / sheet", "Type", "Required", "Definition"),
        REVIEW_SCHEMA_DEFINITIONS,
        (21, 31, 21, 13, 66),
        title,
        header,
        wrap,
    )
    write_reference_sheet(
        workbook.add_worksheet("Runbook"),
        "Client Review Runbook",
        ("Step", "Client action", "Evidence rule"),
        REVIEW_RUNBOOK,
        (18, 56, 48),
        title,
        header,
        wrap,
    )
    workbook.close()


def main():
    parser = argparse.ArgumentParser(
        description="Operational safeguards for auditable ingestion runs."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    manifest = sub.add_parser("manifest")
    manifest.add_argument(
        "artifacts", nargs="+", help="Retained artifacts to hash into the manifest."
    )
    manifest.add_argument(
        "--config", default="{}", help="Inline JSON run configuration recorded in the manifest."
    )
    manifest.add_argument(
        "--config-from-env",
        action="store_true",
        help="merge the non-secret effective .env/process settings into the manifest",
    )
    manifest.add_argument("--out", required=True)
    state = sub.add_parser("stage")
    state.add_argument("state", help="Stage-state artifact to update.")
    state.add_argument(
        "stage", choices=STAGES, help="Pipeline stage this state transition records."
    )
    state.add_argument(
        "--manifest-hash",
        required=True,
        help="Hash of the manifest this stage state is bound to. A mismatch is refused.",
    )
    adapter = sub.add_parser("adapter")
    adapter.add_argument("input", help="Provider handoff the adapter contract is built from.")
    adapter.add_argument(
        "--type",
        choices=(
            "ocr",
            "htr",
            "llm_adjudication",
            "schema_discovery",
            "allocation_policy",
            "table_comprehension",
        ),
        required=True,
        help="Adapter contract type to emit.",
    )
    adapter.add_argument("--credential-env", required=True)
    adapter.add_argument("--out", required=True)
    privacy = sub.add_parser("privacy")
    privacy.add_argument("texts", nargs="+", help="Text artifacts scanned for a privacy inventory.")
    privacy.add_argument("--out", required=True)
    export = sub.add_parser("review-export")
    export.add_argument("input", help="Review artifact exported for reviewers.")
    export.add_argument(
        "--csv", required=True, help="Destination path for the CSV reviewer export."
    )
    export.add_argument(
        "--html", required=True, help="Destination path for the HTML reviewer export."
    )
    export.add_argument(
        "--xlsx", required=True, help="Destination path for the XLSX reviewer export."
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        if args.command == "manifest":
            config = json.loads(args.config)
            if args.config_from_env:
                config = {**config, "effective_runtime_settings": effective_settings_snapshot()}
            result = artifact_manifest(args.artifacts, config)
            write_new_text(args.out, json.dumps(result, indent=2) + "\n")
        elif args.command == "stage":
            result = update_state(args.state, args.stage, args.manifest_hash)
            replace_text_atomically(args.state, json.dumps(result, indent=2) + "\n")
        elif args.command == "adapter":
            result = validate_adapter(load_json(args.input), args.type, args.credential_env)
            write_new_text(args.out, json.dumps(result, indent=2) + "\n")
        elif args.command == "privacy":
            result = {"findings": privacy_inventory(args.texts)}
            write_new_text(args.out, json.dumps(result, indent=2) + "\n")
        else:
            rows = review_rows(load_json(args.input))
            write_review_exports(rows, args.csv, args.html, args.xlsx)
            usage_path = Path(args.input).resolve().parent / "llm_usage_report.json"
            write_report(Path(args.input).resolve().parent, usage_path)
            result = {"review_rows": len(rows), "llm_usage_report": str(usage_path)}
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Operations control failed: {exc}")


if __name__ == "__main__":
    main()
