"""Full-path tests for reproducibility, privacy, adapter, and review-export controls."""

import importlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

operations = importlib.import_module("operations")


def write(path, content):
    path.write_text(content)
    return path


def invoke(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", [operations.__file__, *map(str, args)])
    operations.main()


def test_manifest_and_resumable_state_paths(monkeypatch, tmp_path):
    artifact = write(tmp_path / "artifact.txt", "evidence")
    manifest = operations.artifact_manifest([artifact], {"a": 1})
    assert manifest["artifacts"][0] == {
        "artifact_id": "artifact-0001",
        "name": "artifact.txt",
        "bytes": 8,
        "sha256": operations.sha256(artifact),
    }
    assert manifest["pipeline_version"] == "1.0.0"
    assert manifest["manifest_schema_version"] == "1.0"
    assert len(manifest["configuration_sha256"]) == 64
    assert len(operations.sha256(artifact)) == 64
    with pytest.raises(ValueError, match="readable"):
        operations.artifact_manifest([tmp_path / "missing"])
    state_path = tmp_path / "state.json"
    state = operations.update_state(state_path, "profile", "hash")
    assert state["completed"] == ["profile"]
    write(state_path, json.dumps(state))
    assert operations.update_state(state_path, "profile", "hash")["completed"] == ["profile"]
    with pytest.raises(ValueError, match="prior stages"):
        operations.update_state(state_path, "extract", "hash")
    with pytest.raises(ValueError, match="different"):
        operations.update_state(state_path, "intake", "other")
    write(state_path, json.dumps({"manifest_hash": "hash", "completed": "bad"}))
    with pytest.raises(ValueError, match="list"):
        operations.update_state(state_path, "intake", "hash")
    with pytest.raises(ValueError, match="Unknown"):
        operations.update_state(state_path, "other", "hash")

    new_path = tmp_path / "new.txt"
    operations.write_new_text(new_path, "first")
    with pytest.raises(FileExistsError):
        operations.write_new_text(new_path, "second")
    operations.replace_text_atomically(new_path, "replacement")
    assert new_path.read_text() == "replacement"
    monkeypatch.setattr(operations.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("stop")))
    with pytest.raises(OSError, match="stop"):
        operations.replace_text_atomically(new_path, "not-written")
    assert new_path.read_text() == "replacement"
    assert not list(tmp_path.glob(".new.txt.*"))
    with pytest.raises(ValueError, match="already exists"):
        operations.require_new_outputs((new_path,))


def test_manifest_can_record_effective_environment_settings(monkeypatch, tmp_path):
    artifact = write(tmp_path / "artifact.txt", "evidence")
    output = tmp_path / "manifest.json"
    invoke(monkeypatch, "manifest", artifact, "--config-from-env", "--out", output)
    result = json.loads(output.read_text())
    assert "effective_runtime_settings" in result


def test_adapter_privacy_review_helpers_and_errors(tmp_path):
    ocr = {"engine": "provider-v1", "records": [{"document_id": "d1"}]}
    htr = {
        "engine": "provider-v2",
        "annotations": [{"document_id": "d1", "page_id": "p1", "region_id": "r1"}],
    }
    assert operations.validate_adapter(ocr, "ocr", "OCR_TOKEN")["record_count"] == 1
    assert operations.validate_adapter(htr, "htr", "HTR_TOKEN")["raw_response_retention_required"]
    llm_handoff = {
        "adapter_type": "llm_adjudication",
        "engine": "openai/test",
        "candidate_count": 2,
        "policy": {"decision_mode": "amendment_proposal_only", "client_approval_permitted": False},
    }
    assert (
        operations.validate_adapter(llm_handoff, "llm_adjudication", "OPENAI_API_KEY")[
            "record_count"
        ]
        == 2
    )
    allocation_handoff = {
        "adapter_type": "allocation_policy",
        "engine": "openai/test",
        "template_count": 1,
        "policy": {
            "decision_mode": "proposal_only",
            "client_approval_permitted": False,
            "automatic_sales_credit": False,
        },
    }
    assert (
        operations.validate_adapter(allocation_handoff, "allocation_policy", "OPENAI_API_KEY")[
            "record_count"
        ]
        == 1
    )
    schema_handoff = {
        "adapter_type": "schema_discovery",
        "engine": "openai/test",
        "template_count": 1,
        "policy": {
            "decision_mode": "proposal_only",
            "client_approval_permitted": False,
            "automatic_new_field_creation": False,
        },
    }
    assert (
        operations.validate_adapter(schema_handoff, "schema_discovery", "OPENAI_API_KEY")[
            "record_count"
        ]
        == 1
    )
    table_handoff = {
        "adapter_type": "table_comprehension",
        "engine": "openai/test",
        "records": [{"page_id": "p1"}],
        "proposal_only": True,
        "requires_independent_consensus": True,
        "same_model_roles_not_independent": True,
        "policy": {
            "decision_mode": "source_row_proposal_only",
            "client_approval_permitted": False,
            "automatic_canonical_mapping": False,
        },
    }
    assert (
        operations.validate_adapter(table_handoff, "table_comprehension", "OPENAI_API_KEY")[
            "record_count"
        ]
        == 1
    )
    for data, kind, env, message in [
        (ocr, "ocr", "bad", "uppercase"),
        ({**ocr, "token": "no"}, "ocr", "OCR_TOKEN", "credentials"),
        ({"engine": "", "records": []}, "ocr", "OCR_TOKEN", "requires engine"),
        ({"engine": "x", "records": [{}]}, "ocr", "OCR_TOKEN", "evidence"),
        ({"engine": "x", "annotations": [{}]}, "htr", "HTR_TOKEN", "evidence"),
        (
            ocr,
            "other",
            "OCR_TOKEN",
            "ocr, htr, llm_adjudication, schema_discovery, allocation_policy, or table_comprehension",
        ),
        (
            {
                "engine": "x",
                "adapter_type": "llm_adjudication",
                "candidate_count": -1,
                "policy": {},
            },
            "llm_adjudication",
            "OPENAI_API_KEY",
            "evidence",
        ),
    ]:
        with pytest.raises(ValueError, match=message):
            operations.validate_adapter(data, kind, env)
    text = write(tmp_path / "text.txt", "mail a@b.com SSN 111-22-3333 4111 1111 1111 1111")
    findings = operations.privacy_inventory([text])
    assert {item["category"] for item in findings} == {"email", "us_ssn", "card_like"}
    assert all(item["name"] == "text.txt" and "path" not in item for item in findings)
    with pytest.raises(ValueError, match="readable"):
        operations.privacy_inventory([tmp_path / "missing.txt"])
    assert operations.load_json(write(tmp_path / "object.json", "{}")) == {}
    with pytest.raises(ValueError, match="object"):
        operations.load_json(write(tmp_path / "list.json", "[]"))


def test_review_export_and_all_cli_commands(monkeypatch, tmp_path, capsys):
    review_item = {
        "priority": "high",
        "document_id": "d1",
        "page_id": "p1",
        "region_id": "",
        "field": "header.total_amount",
        "reason": "<check>",
        "review_source": "exceptions",
        "disposition": "client_review_required",
    }
    review_data = {
        "summary": {
            "schema_version": "1.0",
            "generated_at": "2026-08-13T00:00:00+00:00",
            "source_artifacts": ["exceptions.json"],
            "client_review_items": 1,
            "gate_status": "blocked_pending_client_review",
            "findings": ["fixture"],
        },
        "items": [review_item],
    }
    rows = operations.review_rows(review_data)
    assert rows[0]["reason"] == "<check>" and len(rows) == 1
    with pytest.raises(ValueError, match="summary object and items"):
        operations.review_rows({})
    for bad, message in [
        ({**review_data, "items": ["skip"]}, "must be an object"),
        (
            {**review_data, "summary": {**review_data["summary"], "client_review_items": 2}},
            "count",
        ),
        (
            {
                **review_data,
                "items": [{key: value for key, value in review_item.items() if key != "field"}],
            },
            "missing required fields",
        ),
    ]:
        with pytest.raises(ValueError, match=message):
            operations.review_rows(bad)
    for summary_change, message in [
        ({"schema_version": None}, "schema_version"),
        ({"schema_version": ""}, "schema_version"),
        ({"generated_at": None}, "generated_at"),
        ({"generated_at": ""}, "generated_at"),
        ({"source_artifacts": "bad"}, "source_artifacts"),
        ({"source_artifacts": [1]}, "source_artifacts"),
        ({"findings": "bad"}, "findings"),
        ({"findings": [1]}, "findings"),
        ({"gate_status": "clear"}, "gate status"),
    ]:
        bad = {**review_data, "summary": {**review_data["summary"], **summary_change}}
        with pytest.raises(ValueError, match=message):
            operations.review_rows(bad)
    for item_change, message in [
        ({"priority": "urgent"}, "priority"),
        ({"disposition": "closed"}, "require client review"),
        ({"reason": ""}, "reason and review_source"),
        ({"review_source": ""}, "reason and review_source"),
        ({"page_id": 1}, "fields must be strings"),
    ]:
        bad = {**review_data, "items": [{**review_item, **item_change}]}
        with pytest.raises(ValueError, match=message):
            operations.review_rows(bad)
    clear_data = {
        "summary": {
            **review_data["summary"],
            "client_review_items": 0,
            "gate_status": "clear",
        },
        "items": [],
    }
    assert operations.review_rows(clear_data) == []
    csv_path, html_path, xlsx_path = (
        tmp_path / "review.csv",
        tmp_path / "review.html",
        tmp_path / "review.xlsx",
    )
    operations.write_review_exports(rows, csv_path, html_path, xlsx_path)
    assert "document_id" in csv_path.read_text() and "&lt;check&gt;" in html_path.read_text()
    with zipfile.ZipFile(xlsx_path) as workbook:
        assert "xl/worksheets/sheet1.xml" in workbook.namelist()
        strings = workbook.read("xl/sharedStrings.xml")
        assert all(
            name in strings
            for name in (b"Client Review", b"Header Definitions", b"Schema Definitions", b"Runbook")
        )
        assert b"priority" in strings and b"Client Review Header Definitions" in strings
    operations.write_review_exports(
        [], tmp_path / "empty.csv", tmp_path / "empty.html", tmp_path / "empty.xlsx"
    )
    assert zipfile.is_zipfile(tmp_path / "empty.xlsx")
    artifact = write(tmp_path / "artifact.txt", "x")
    manifest_path = tmp_path / "manifest.json"
    invoke(monkeypatch, "manifest", artifact, "--config", '{"x": 1}', "--out", manifest_path)
    assert json.loads(manifest_path.read_text())["artifacts"]
    state_path = tmp_path / "state.json"
    invoke(monkeypatch, "stage", state_path, "profile", "--manifest-hash", "h")
    assert json.loads(state_path.read_text())["completed"] == ["profile"]
    ocr_path = write(
        tmp_path / "ocr.json", json.dumps({"engine": "x", "records": [{"document_id": "d"}]})
    )
    adapter_path = tmp_path / "adapter.json"
    invoke(
        monkeypatch,
        "adapter",
        ocr_path,
        "--type",
        "ocr",
        "--credential-env",
        "OCR_TOKEN",
        "--out",
        adapter_path,
    )
    assert json.loads(adapter_path.read_text())["engine"] == "x"
    llm_path = write(
        tmp_path / "llm.json",
        json.dumps(
            {
                "adapter_type": "llm_adjudication",
                "engine": "openai/test",
                "candidate_count": 0,
                "policy": {
                    "decision_mode": "amendment_proposal_only",
                    "client_approval_permitted": False,
                },
            }
        ),
    )
    invoke(
        monkeypatch,
        "adapter",
        llm_path,
        "--type",
        "llm_adjudication",
        "--credential-env",
        "OPENAI_API_KEY",
        "--out",
        tmp_path / "llm-adapter.json",
    )
    assert json.loads((tmp_path / "llm-adapter.json").read_text())["record_count"] == 0
    allocation_path = write(
        tmp_path / "allocation.json",
        json.dumps(
            {
                "adapter_type": "allocation_policy",
                "engine": "openai/test",
                "template_count": 0,
                "policy": {
                    "decision_mode": "proposal_only",
                    "client_approval_permitted": False,
                    "automatic_sales_credit": False,
                },
            }
        ),
    )
    invoke(
        monkeypatch,
        "adapter",
        allocation_path,
        "--type",
        "allocation_policy",
        "--credential-env",
        "OPENAI_API_KEY",
        "--out",
        tmp_path / "allocation-adapter.json",
    )
    assert json.loads((tmp_path / "allocation-adapter.json").read_text())["record_count"] == 0
    privacy_path = tmp_path / "privacy.json"
    invoke(monkeypatch, "privacy", artifact, "--out", privacy_path)
    assert json.loads(privacy_path.read_text()) == {"findings": []}
    review_path = write(tmp_path / "review.json", json.dumps(review_data))
    cli_csv = tmp_path / "review-cli.csv"
    cli_html = tmp_path / "review-cli.html"
    cli_xlsx = tmp_path / "review-cli.xlsx"
    invoke(
        monkeypatch,
        "review-export",
        review_path,
        "--csv",
        cli_csv,
        "--html",
        cli_html,
        "--xlsx",
        cli_xlsx,
    )
    assert "review_rows" in capsys.readouterr().out
    usage_path = tmp_path / "llm_usage_report.json"
    assert usage_path.is_file()
    assert json.loads(usage_path.read_text())["current_run"]["tokens"] == {}
    with pytest.raises(SystemExit, match="already exists"):
        invoke(
            monkeypatch,
            "review-export",
            review_path,
            "--csv",
            cli_csv,
            "--html",
            cli_html,
            "--xlsx",
            cli_xlsx,
        )
    with pytest.raises(SystemExit, match="Operations control failed"):
        invoke(monkeypatch, "manifest", artifact, "--config", "bad", "--out", manifest_path)
