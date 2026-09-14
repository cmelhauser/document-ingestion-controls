"""Tests for the no-send CSV/API staging package."""

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

staging = importlib.import_module("csv_api_staging")
canonical = importlib.import_module("canonical_load")


def write(path, value):
    path.write_text(json.dumps(value) if not isinstance(value, str) else value)
    return path


def export():
    return {
        "batch_id": "batch-1",
        "tables": {
            "document": [
                {
                    "document_id": "doc-1",
                    "batch_id": "batch-1",
                    "review_status": "exception_resolved",
                    "source_file": "source.pdf",
                    "source_page_range": "1",
                    "source_sha256": "hash",
                    "evidence": {"page": 1},
                }
            ],
            "party": [
                {
                    "party_key": "party-1",
                    "batch_id": "batch-1",
                    "review_status": "exception_resolved",
                    "source_document_id": "doc-1",
                    "names": ["Acme"],
                }
            ],
        },
    }


def test_creates_checked_no_send_package(tmp_path):
    export_path = write(tmp_path / "export.json", export())
    plan_path = write(tmp_path / "plan.json", canonical.build_plan(export()))
    result = staging.create_package(export_path, plan_path, tmp_path / "stage")
    assert result == {
        "batch_id": "batch-1",
        "findings": [],
        "manifest": "staging_manifest.json",
        "package_kind": "staging",
        "package_content_sha256": result["package_content_sha256"],
        "records": 2,
        "review_context": None,
        "source_export_sha256": result["source_export_sha256"],
        "tables": len(canonical.LOAD_ORDER),
        "verified": True,
    }
    envelope = json.loads((tmp_path / "stage" / "api_load_envelope.json").read_text())
    assert envelope["api_upload_permitted"] is False
    assert envelope["commit_requires_zero_unaccounted_rows"] is True
    assert envelope["schema_version"] == "crm_api_load_envelope_v3"
    assert envelope["authoritative_row_encoding"]["required_for_machine_load"] is True
    manifest = json.loads((tmp_path / "stage" / "staging_manifest.json").read_text())
    document = next(item for item in manifest["files"] if item["table"] == "document")
    assert document["records"] == 1 and (tmp_path / "stage" / document["path"]).is_file()
    assert "page" in (tmp_path / "stage" / document["path"]).read_text()
    assert staging.verify_package(tmp_path / "stage", canonical.build_plan(export()))["verified"]
    with pytest.raises(ValueError, match="new"):
        staging.create_package(export_path, plan_path, tmp_path / "stage")


def test_validates_plan_and_helpers(tmp_path):
    data = export()
    expected = canonical.build_plan(data)
    assert staging.verify_plan(data, expected)["batch_id"] == "batch-1"
    changed = {**expected, "batch_id": "other"}
    with pytest.raises(ValueError, match="does not belong"):
        staging.verify_plan(data, changed)
    changed = {**expected, "steps": []}
    with pytest.raises(ValueError, match="checksums"):
        staging.verify_plan(data, changed)
    legacy = {**expected, "include_open_review": True}
    with pytest.raises(ValueError, match="include_open_review"):
        staging.verify_plan(data, legacy)
    assert staging.csv_value(None) == "" and staging.csv_value({"b": 1}) == '{"b":1}'
    assert staging.csv_value("=1+1") == "'=1+1"
    assert staging.csv_value("-10") == "'-10"
    assert staging.csv_value("'=1+1") == "''=1+1"
    table = tmp_path / "rows.csv"
    assert staging.write_table(table, [{"b": 1, "a": "x"}]) == [
        "a",
        "b",
        *staging.CONTROL_COLUMNS,
    ]
    with pytest.raises(ValueError, match="reserved"):
        staging.write_table(tmp_path / "reserved.csv", [{"__canonical_row_json": "collision"}])
    with pytest.raises(ValueError, match="steps"):
        staging.load_plan(write(tmp_path / "bad.json", {}))
    with pytest.raises(FileExistsError):
        staging.write_table(table, [])
    assert len(staging.sha256(table)) == 64


def test_empty_package_has_declared_headers_and_corruption_is_detected(tmp_path):
    empty = {"batch_id": "empty", "tables": {}}
    export_path = write(tmp_path / "empty.json", empty)
    plan = canonical.build_plan(empty)
    stage = tmp_path / "empty-stage"
    result = staging.create_package(export_path, write(tmp_path / "empty-plan.json", plan), stage)
    assert result["records"] == 0 and result["findings"]
    manifest = json.loads((stage / "staging_manifest.json").read_text())
    document = next(item for item in manifest["files"] if item["table"] == "document")
    assert (stage / document["path"]).read_text().startswith("document_id,")
    (stage / document["path"]).write_text("tampered\n")
    with pytest.raises(ValueError, match="checksum"):
        staging.verify_package(stage, plan)


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("manifest-source", "manifest source export"),
        ("envelope-source", "envelope source export"),
        ("load-plan-checksum", "load plan checksum"),
        ("missing-step", "every load step"),
        ("duplicate-path", "duplicate file paths"),
        ("file-entry-type", "file entries must be objects"),
        ("unsafe-path", "unsafe file path"),
        ("shape", "file shape"),
        ("step-checksum", "load-step topology"),
        ("record-total", "record totals"),
        ("content-checksum", "package content checksum"),
    ),
)
def test_package_verification_rejects_each_reconciliation_mismatch(tmp_path, case, message):
    root = tmp_path / case
    root.mkdir()
    data = export()
    plan = canonical.build_plan(data)
    stage = root / "stage"
    staging.create_package(
        write(root / "export.json", data),
        write(root / "plan.json", plan),
        stage,
    )
    manifest_path = stage / "staging_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    envelope_path = stage / manifest["api_envelope"]
    envelope = json.loads(envelope_path.read_text())
    if case == "manifest-source":
        manifest["source_export_sha256"] = "wrong"
    elif case == "envelope-source":
        envelope["source_export_sha256"] = "wrong"
    elif case == "load-plan-checksum":
        manifest["load_plan_sha256"] = "wrong"
    elif case == "missing-step":
        manifest["files"].pop()
    elif case == "duplicate-path":
        manifest["files"][1]["path"] = manifest["files"][0]["path"]
    elif case == "file-entry-type":
        manifest["files"][0] = None
    elif case == "unsafe-path":
        manifest["files"][0]["path"] = "../outside.csv"
    elif case == "shape":
        manifest["files"][0]["columns"] = ["wrong"]
    elif case == "step-checksum":
        manifest["files"][0]["canonical_rows_sha256"] = "wrong"
    elif case == "record-total":
        manifest["records"] += 1
    else:
        manifest["package_content_sha256"] = "wrong"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    envelope_path.write_text(json.dumps(envelope, sort_keys=True))

    with pytest.raises(ValueError, match=message):
        staging.verify_package(stage, plan)


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("missing-controls", "missing authoritative row controls"),
        ("invalid-row-json", "canonical row JSON is invalid"),
        ("row-checksum", "canonical row checksum mismatch"),
        ("load-step-checksum", "load-step checksum mismatch"),
    ),
)
def test_package_verification_reconstructs_authoritative_rows(tmp_path, case, message):
    data = export()
    plan = canonical.build_plan(data)
    stage = tmp_path / case
    staging.create_package(
        write(tmp_path / f"{case}-export.json", data),
        write(tmp_path / f"{case}-plan.json", plan),
        stage,
    )
    manifest_path = stage / "staging_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    item = next(entry for entry in manifest["files"] if entry["table"] == "document")
    path = stage / item["path"]
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        headings = list(reader.fieldnames or [])
        rows = list(reader)

    if case == "missing-controls":
        headings[-1] = "__wrong_control"
        rows[0]["__wrong_control"] = rows[0].pop("__canonical_row_sha256")
    elif case == "invalid-row-json":
        rows[0]["__canonical_row_json"] = "{"
    elif case == "row-checksum":
        rows[0]["__canonical_row_sha256"] = "wrong"
    else:
        canonical_row = json.loads(rows[0]["__canonical_row_json"])
        canonical_row["source_file"] = "changed.pdf"
        rows[0]["source_file"] = staging.csv_value(canonical_row["source_file"])
        rows[0]["__canonical_row_json"] = json.dumps(
            canonical_row, sort_keys=True, separators=(",", ":")
        )
        rows[0]["__canonical_row_sha256"] = canonical.stable_checksum(canonical_row)

    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headings, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    item["columns"] = headings
    item["bytes"] = path.stat().st_size
    item["sha256"] = staging.sha256(path)
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match=message):
        staging.verify_package(stage, plan)


def test_package_failure_never_publishes_partial_directory(monkeypatch, tmp_path):
    export_path = write(tmp_path / "export.json", export())
    plan_path = write(tmp_path / "plan.json", canonical.build_plan(export()))
    original = staging.write_table
    calls = []

    def fail_second(path, rows, headings=None):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("simulated write failure")
        return original(path, rows, headings)

    monkeypatch.setattr(staging, "write_table", fail_second)
    target = tmp_path / "stage"
    with pytest.raises(OSError, match="simulated"):
        staging.create_package(export_path, plan_path, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".stage.*"))


def test_verification_rejects_duplicate_topology_envelope_and_row_tampering(tmp_path):
    data = export()
    plan = canonical.build_plan(data)
    stage = tmp_path / "stage"
    staging.create_package(
        write(tmp_path / "export.json", data), write(tmp_path / "plan.json", plan), stage
    )
    manifest_path = stage / "staging_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    original = json.loads(manifest_path.read_text())

    manifest["files"][1] = dict(manifest["files"][0])
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="topology|duplicate"):
        staging.verify_package(stage, plan)

    manifest_path.write_text(json.dumps(original))
    envelope_path = stage / original["api_envelope"]
    envelope = json.loads(envelope_path.read_text())
    envelope["operations"][0]["halt_on_rejection"] = False
    envelope_path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match="envelope contract"):
        staging.verify_package(stage, plan)

    envelope_path.write_text(
        json.dumps(
            staging.api_envelope(
                data["batch_id"],
                plan["source_export_sha256"],
                [
                    staging.operation_contract(step, item["path"], data["batch_id"])
                    for step, item in zip(plan["steps"], original["files"], strict=True)
                ],
            )
        )
    )
    target_item = next(item for item in original["files"] if item["table"] == "document")
    target = stage / target_item["path"]
    text = target.read_text().replace("source.pdf", "changed.pdf", 1)
    target.write_text(text)
    target_item["sha256"] = staging.sha256(target)
    target_item["bytes"] = target.stat().st_size
    original["files"] = [
        target_item if item["table"] == "document" else item for item in original["files"]
    ]
    descriptor = {
        "batch_id": original["batch_id"],
        "source_export_sha256": original["source_export_sha256"],
        "files": original["files"],
        "api_envelope_sha256": staging.sha256(envelope_path),
    }
    original["package_content_sha256"] = canonical.stable_checksum(descriptor)
    manifest_path.write_text(json.dumps(original))
    with pytest.raises(ValueError, match="presentation columns"):
        staging.verify_package(stage, plan)


def test_receiver_endpoint_templates_percent_encode_batch_identity(tmp_path):
    data = export()
    data["batch_id"] = "batch/with spaces"
    for rows in data["tables"].values():
        for row in rows:
            row["batch_id"] = data["batch_id"]
    plan = canonical.build_plan(data)
    stage = tmp_path / "encoded"
    staging.create_package(
        write(tmp_path / "encoded-export.json", data),
        write(tmp_path / "encoded-plan.json", plan),
        stage,
    )
    envelope = json.loads((stage / "api_load_envelope.json").read_text())
    assert "/batch%2Fwith%20spaces/" in envelope["operations"][0]["stage_endpoint_template"]


def test_staging_rejects_open_review_rows_and_legacy_plan_flag(tmp_path):
    safe = export()
    open_export = export()
    open_export["tables"]["document"][0]["review_status"] = "open_exception"
    export_path = write(tmp_path / "open-export.json", open_export)
    safe_plan = canonical.build_plan(safe)

    with pytest.raises(ValueError, match="open_exception"):
        staging.create_package(
            export_path,
            write(tmp_path / "plan.json", safe_plan),
            tmp_path / "stage-without-flag",
        )

    legacy_plan = {**safe_plan, "include_open_review": True}
    with pytest.raises(ValueError, match="include_open_review"):
        staging.create_package(
            export_path,
            write(tmp_path / "legacy-plan.json", legacy_plan),
            tmp_path / "stage-with-flag",
        )


@pytest.mark.parametrize(
    ("table", "row"),
    (
        ("currency", {"currency_code": "USD", "review_status": "open_exception"}),
        (
            "party_role",
            {"party_key": "party-1", "role": "payer", "review_status": "open_exception"},
        ),
    ),
)
def test_staging_rejects_open_review_static_and_association_rows(tmp_path, table, row):
    safe = export()
    supplied_plan = canonical.build_plan(safe)
    unsafe = export()
    unsafe["tables"][table] = [row]

    with pytest.raises(ValueError, match="open_exception"):
        staging.create_package(
            write(tmp_path / f"{table}-export.json", unsafe),
            write(tmp_path / f"{table}-plan.json", supplied_plan),
            tmp_path / f"{table}-stage",
        )


def test_main(monkeypatch, capsys):
    monkeypatch.setattr(staging, "create_package", lambda *args, review_context=None: {"tables": 1})
    monkeypatch.setattr(sys, "argv", ["csv_api_staging.py", "export", "plan", "--out", "out"])
    staging.main()
    assert json.loads(capsys.readouterr().out) == {"tables": 1}
    monkeypatch.setattr(
        staging,
        "create_package",
        lambda *args, review_context=None: (_ for _ in ()).throw(ValueError("bad")),
    )
    with pytest.raises(SystemExit, match="failed"):
        staging.main()
