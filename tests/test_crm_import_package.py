"""Tests for the no-send common CRM import package."""

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

crm_import = importlib.import_module("crm_import_package")
canonical = importlib.import_module("canonical_load")


def write(path, value):
    path.write_text(json.dumps(value) if not isinstance(value, str) else value)
    return path


def export():
    clear = {"batch_id": "batch-1", "review_status": "exception_resolved"}
    return {
        "batch_id": "batch-1",
        "registry_version": "registry-1",
        "tables": {
            "document": [
                {
                    "document_id": "doc-1",
                    "source_file": "source.pdf",
                    "source_page_range": "1",
                    "source_sha256": "hash",
                    **clear,
                }
            ],
            "party": [
                {
                    "party_key": "party-1",
                    "natural_key": "acme",
                    "canonical_name": "=ACME",
                    "normalized_name": "acme",
                    "source_document_id": "doc-1",
                    **clear,
                }
            ],
            "party_role": [{"party_key": "party-1", "role": "payer"}],
            "address": [
                {
                    "address_key": "address-1",
                    "party_key": "party-1",
                    "raw_address": "1 Main",
                    "line1": "1 Main",
                    "city": "Boston",
                    "source_document_id": "doc-1",
                    **clear,
                }
            ],
            "contact": [
                {
                    "contact_key": "contact-1",
                    "party_key": "party-1",
                    "name": "Ada",
                    "email": "ada@example.com",
                    "source_document_id": "doc-1",
                    **clear,
                }
            ],
            "item": [
                {
                    "item_key": "item-1",
                    "natural_key": "widget",
                    "item_code": "W1",
                    "description": "Widget",
                    **clear,
                }
            ],
            "selling_location": [
                {
                    "selling_location_key": "location-1",
                    "natural_key": "boston",
                    "location_name": "Boston",
                    **clear,
                }
            ],
            "shipment": [
                {
                    "shipment_key": "shipment-1",
                    "natural_key": "ship-1",
                    "source_document_id": "doc-1",
                    **clear,
                }
            ],
            "invoice_header": [
                {
                    "invoice_key": "invoice-1",
                    "invoice_number": "INV-1",
                    "payer_party_key": "party-1",
                    "biller_party_key": "party-1",
                    "shipment_key": "shipment-1",
                    "total_amount": "100.00",
                    "source_document_id": "doc-1",
                    **clear,
                }
            ],
            "invoice_line": [
                {
                    "invoice_line_key": "line-1",
                    "invoice_key": "invoice-1",
                    "line_number": 1,
                    "item_key": "item-1",
                    "description": "Widget",
                    "extended_amount": "100.00",
                    **clear,
                }
            ],
            "invoice_accessorial": [
                {
                    "accessorial_key": "charge-1",
                    "invoice_key": "invoice-1",
                    "charge_code": "FUEL",
                    "amount": "5.00",
                    **clear,
                }
            ],
            "payment": [
                {
                    "payment_key": "payment-1",
                    "natural_key": "pay-1",
                    "payer_party_key": "party-1",
                    "total_paid": "100.00",
                    "source_document_id": "doc-1",
                    **clear,
                }
            ],
            "payment_application": [
                {
                    "application_key": "application-1",
                    "payment_key": "payment-1",
                    "invoice_key": "invoice-1",
                    "amount_applied": "100.00",
                    **clear,
                }
            ],
        },
    }


def build(tmp_path, data=None, name="crm-import"):
    data = data or export()
    plan = canonical.build_plan(data)
    out = tmp_path / name
    result = crm_import.create_package(
        write(tmp_path / f"{name}-export.json", data),
        write(tmp_path / f"{name}-plan.json", plan),
        out,
    )
    return data, plan, out, result


def test_builds_complete_verified_no_send_package(tmp_path):
    data, plan, out, result = build(tmp_path)
    assert result["verified"] is True and result["files"] == len(crm_import.OBJECTS)
    assert result["records"] == 12 and not result["findings"]
    manifest = json.loads((out / "crm_import_manifest.json").read_text())
    assert manifest["live_write_permitted"] is False
    assert manifest["target_mapping_is_approved"] is False
    assert len(manifest["canonical_coverage"]) == len(canonical.LOAD_ORDER)
    assert sum(item["records"] for item in manifest["files"]) == 12
    with (out / "01_accounts.csv").open(newline="") as stream:
        account = next(csv.DictReader(stream))
    assert account["account_name"] == "'=ACME"
    assert json.loads(account["__canonical_key_json"]) == ["party-1"]
    assert json.loads(account["__canonical_row_json"])["canonical_name"] == "=ACME"
    mappings = list(csv.DictReader((out / "field_mapping_template.csv").open(newline="")))
    assert len(mappings) == manifest["field_mapping_rows"]
    assert {row["mapping_status"] for row in mappings} == {
        "proposal_validate_against_tenant_metadata"
    }
    write_plan = json.loads((out / "crm_write_plan.json").read_text())
    assert write_plan["live_write_permitted"] is False
    assert write_plan["stages"][-1]["name"] == "acceptance_or_rollback"
    sources = json.loads((out / "official_import_sources.json").read_text())
    assert {item["vendor"] for item in sources["sources"]} == {
        "Salesforce",
        "HubSpot",
        "Microsoft Dynamics 365 / Dataverse",
        "Zoho CRM",
    }
    assert crm_import.verify_package(out, plan, data) == {
        "files": 12,
        "records": 12,
        "verified": True,
    }
    with pytest.raises(ValueError, match="must be new"):
        crm_import.create_package(
            tmp_path / "crm-import-export.json", tmp_path / "crm-import-plan.json", out
        )


def test_empty_package_is_explicit_and_helpers_are_stable(tmp_path):
    data = {"batch_id": "empty", "tables": {}}
    data, plan, out, result = build(tmp_path, data, "empty")
    assert result["records"] == 0 and result["findings"]
    assert (out / "01_accounts.csv").read_text().startswith("external_id,")
    account = crm_import.OBJECTS[0]
    assert crm_import.generic_rows(account, data) == []
    assert crm_import.canonical_key("party_role", {"party_key": "p", "role": "payer"}) == [
        "p",
        "payer",
    ]
    assert len(crm_import.mapping_rows()) > 40
    assert crm_import.write_plan(
        "batch",
        "hash",
        [{"generic_object": "accounts", "source_table": "party", "path": "x.csv", "records": 0}],
    )["objects"][0]["source_idempotency_keys"] == ["party_key"]
    with pytest.raises(ValueError, match="unsafe"):
        crm_import._safe_path("../outside")
    with pytest.raises(ValueError, match="unsafe"):
        crm_import._safe_path("/outside")
    assert crm_import._safe_path("safe/file.csv") == Path("safe/file.csv")
    assert crm_import.verify_package(out, plan, data)["verified"]


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("schema", "schema mismatch"),
        ("permission", "must not authorize"),
        ("source", "source export checksum"),
        ("plan", "load plan checksum"),
        ("topology", "object topology"),
        ("topology-entry", "object topology"),
        ("coverage-topology", "coverage is incomplete"),
        ("coverage", "coverage does not reconcile"),
        ("mapping", "companion checksum"),
        ("write-plan", "write plan object topology"),
        ("content", "content checksum"),
        ("total", "record totals"),
    ),
)
def test_manifest_and_companion_corruption_fails_closed(tmp_path, case, message):
    data, plan, out, _ = build(tmp_path, name=case)
    manifest_path = out / "crm_import_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if case == "schema":
        manifest["schema_version"] = "bad"
    elif case == "permission":
        manifest["live_write_permitted"] = True
    elif case == "source":
        manifest["source_export_sha256"] = "bad"
    elif case == "plan":
        manifest["load_plan_sha256"] = "bad"
    elif case == "topology":
        manifest["files"] = []
    elif case == "topology-entry":
        manifest["files"][0]["generic_object"] = "wrong"
    elif case == "coverage-topology":
        manifest["canonical_coverage"] = []
    elif case == "coverage":
        manifest["canonical_coverage"][0]["records"] += 1
    elif case == "mapping":
        (out / "field_mapping_template.csv").write_text("changed")
    elif case == "write-plan":
        write_path = out / "crm_write_plan.json"
        write_plan = json.loads(write_path.read_text())
        write_plan["objects"] = []
        write_path.write_text(json.dumps(write_plan))
        manifest["write_plan_sha256"] = crm_import.sha256(write_path)
    elif case == "content":
        manifest["package_content_sha256"] = "bad"
    else:
        manifest["records"] += 1
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=message):
        crm_import.verify_package(out, plan, data)


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("file-path", "unsafe file path"),
        ("file-checksum", "file checksum"),
        ("shape", "file shape"),
        ("controls-json", "controls are invalid"),
        ("controls-key", "controls do not reconcile"),
        ("presentation", "presentation row"),
        ("canonical-checksum", "canonical row checksum"),
    ),
)
def test_data_file_corruption_fails_closed(tmp_path, case, message):
    data, plan, out, _ = build(tmp_path, name=case)
    manifest_path = out / "crm_import_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    item = manifest["files"][0]
    path = out / item["path"]
    if case == "file-path":
        item["path"] = "../outside.csv"
    elif case == "file-checksum":
        path.write_text("tampered")
    elif case == "canonical-checksum":
        item["canonical_rows_sha256"] = "bad"
    else:
        with path.open(newline="") as stream:
            reader = csv.DictReader(stream)
            headings, rows = list(reader.fieldnames or []), list(reader)
        if case == "shape":
            old = headings[0]
            headings[0] = "wrong"
            rows[0]["wrong"] = rows[0].pop(old)
        elif case == "controls-json":
            rows[0]["__canonical_row_json"] = "{"
        elif case == "controls-key":
            rows[0]["__canonical_key_json"] = "[]"
        else:
            rows[0]["account_name"] = "wrong"
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=headings)
            writer.writeheader()
            writer.writerows(rows)
        item["sha256"] = crm_import.sha256(path)
        item["bytes"] = path.stat().st_size
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=message):
        crm_import.verify_package(out, plan, data)


def test_atomic_cleanup_and_cli(monkeypatch, tmp_path, capsys):
    data = export()
    export_path = write(tmp_path / "export.json", data)
    plan_path = write(tmp_path / "plan.json", canonical.build_plan(data))
    original = crm_import.verify_package
    monkeypatch.setattr(
        crm_import, "verify_package", lambda *_: (_ for _ in ()).throw(ValueError("boom"))
    )
    with pytest.raises(ValueError, match="boom"):
        crm_import.create_package(export_path, plan_path, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".failed.*"))
    monkeypatch.setattr(crm_import, "verify_package", original)
    monkeypatch.setattr(
        sys,
        "argv",
        ["crm_import_package.py", str(export_path), str(plan_path), "--out", str(tmp_path / "cli")],
    )
    crm_import.main()
    assert json.loads(capsys.readouterr().out)["verified"] is True
    monkeypatch.setattr(
        sys,
        "argv",
        ["crm_import_package.py", "missing", str(plan_path), "--out", str(tmp_path / "bad")],
    )
    with pytest.raises(SystemExit, match="CRM import packaging failed"):
        crm_import.main()
