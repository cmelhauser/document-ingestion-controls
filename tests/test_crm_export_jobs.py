"""Tests for bounded downloadable CRM export jobs."""

import csv
import hashlib
import importlib
import os
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

jobs_module = importlib.import_module("crm_export_jobs")
canonical = importlib.import_module("canonical_load")
store = importlib.import_module("retrieval_store")


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "retrieval.sqlite"
    store.build_database(
        {
            "batch_id": "export-batch",
            "tables": {
                "document": [
                    {
                        "document_id": "doc-1",
                        "source_file": "source.pdf",
                        "source_page_range": "1",
                        "source_sha256": "a" * 64,
                        "batch_id": "export-batch",
                        "review_status": "exception_resolved",
                    },
                    {
                        "document_id": "doc-2",
                        "source_file": "source-2.pdf",
                        "source_page_range": "1",
                        "source_sha256": "b" * 64,
                        "batch_id": "export-batch",
                        "review_status": "exception_resolved",
                    },
                ],
                "party": [
                    {
                        "party_key": "party-1",
                        "canonical_name": "=FORMULA",
                        "batch_id": "export-batch",
                        "source_document_id": "doc-1",
                        "review_status": "exception_resolved",
                    }
                ],
            },
        },
        path,
    )
    return path


def service(database, tmp_path, **kwargs):
    return jobs_module.ExportJobs(
        database, tmp_path / "exports", "https://crm.example.com", **kwargs
    )


def write(path, value):
    path.write_text(jobs_module.json.dumps(value), encoding="utf-8")
    return path


def package_sources(tmp_path):
    export = {
        "batch_id": "export-batch",
        "tables": {
            "document": [
                {
                    "document_id": "doc-1",
                    "source_file": "source.pdf",
                    "source_page_range": "1",
                    "source_sha256": "a" * 64,
                    "batch_id": "export-batch",
                    "review_status": "exception_resolved",
                },
                {
                    "document_id": "doc-2",
                    "source_file": "source-2.pdf",
                    "source_page_range": "1",
                    "source_sha256": "b" * 64,
                    "batch_id": "export-batch",
                    "review_status": "exception_resolved",
                },
            ],
            "party": [
                {
                    "party_key": "party-1",
                    "batch_id": "export-batch",
                    "review_status": "exception_resolved",
                    "source_document_id": "doc-1",
                    "canonical_name": "=FORMULA",
                }
            ],
        },
    }
    return (
        write(tmp_path / "canonical-export.json", export),
        write(tmp_path / "canonical-plan.json", canonical.build_plan(export)),
    )


def test_create_xlsx_status_and_download(database, tmp_path):
    jobs = service(database, tmp_path)
    created = jobs.create("owner-1", table="party")
    assert created["status"] == "completed" and created["rows"] == 1
    assert "download_token_sha256" not in created and "owner_sha256" not in created
    assert created["download_url"].startswith("https://crm.example.com/downloads/")
    status = jobs.status("owner-1", created["job_id"])
    assert status["file_sha256"] == created["file_sha256"]
    assert "owner_sha256" not in status and "download_token_sha256" not in status
    token = created["download_url"].split("token=", 1)[1]
    artifact, manifest = jobs.download(created["job_id"], token)
    assert zipfile.is_zipfile(artifact)
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == manifest["file_sha256"]
    with pytest.raises(ValueError, match="not found"):
        jobs.status("other-owner", created["job_id"])
    with pytest.raises(PermissionError, match="invalid"):
        jobs.download(created["job_id"], None)
    with pytest.raises(PermissionError, match="expired"):
        jobs.download(created["job_id"], token, now=manifest["expires_at_epoch"])
    artifact.write_bytes(b"changed")
    with pytest.raises(ValueError, match="integrity"):
        jobs.download(created["job_id"], token)


def test_create_csv_report_and_formula_protection(database, tmp_path):
    jobs = service(database, tmp_path)
    table = jobs.create("owner", table="party", format="csv")
    token = table["download_url"].split("token=", 1)[1]
    artifact, _ = jobs.download(table["job_id"], token)
    rows = list(csv.DictReader(artifact.open(encoding="utf-8")))
    assert rows[0]["canonical_name"] == "'=FORMULA"
    report = jobs.create("owner", report="account_directory", format="csv", parameters={})
    assert report["subject"]["kind"] == "report" and report["rows"] == 1
    assert jobs_module._display(None) == ""
    assert jobs_module._display({"b": 2, "a": 1}) == '{"a":1,"b":2}'


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"ttl_seconds": 59}, "TTL"),
        ({"ttl_seconds": True}, "TTL"),
        ({"max_rows": 0}, "rows"),
        ({"max_rows": True}, "rows"),
    ),
)
def test_configuration_validation(database, tmp_path, kwargs, message):
    with pytest.raises(ValueError, match=message):
        service(database, tmp_path, **kwargs)
    with pytest.raises(ValueError, match="database"):
        jobs_module.ExportJobs(tmp_path / "missing", tmp_path / "x", "https://crm.example.com")
    with pytest.raises(ValueError, match="HTTPS"):
        jobs_module.ExportJobs(database, tmp_path / "x", "http://crm.example.com")
    for invalid in (
        "https://crm.example.com/path",
        "https://crm.example.com?query=yes",
        "https://user@crm.example.com",
    ):
        with pytest.raises(ValueError, match="HTTPS origin"):
            jobs_module.ExportJobs(database, tmp_path / "x", invalid)
    insecure = tmp_path / "insecure"
    insecure.mkdir(mode=0o777)
    os.chmod(insecure, 0o755)  # noqa: S103 - intentionally exercise insecure-dir refusal.
    with pytest.raises(ValueError, match="permissions"):
        jobs_module.ExportJobs(database, insecure, "https://crm.example.com")
    real_root = tmp_path / "real-root"
    real_root.mkdir(mode=0o700)
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(ValueError, match="real directory"):
        jobs_module.ExportJobs(database, linked_root, "https://crm.example.com")


def test_create_and_manifest_validation(database, tmp_path):
    jobs = service(database, tmp_path, max_rows=1)
    for kwargs in ({}, {"table": "party", "report": "account_directory"}):
        with pytest.raises(ValueError, match="exactly one"):
            jobs.create("owner", **kwargs)
    with pytest.raises(ValueError, match="format"):
        jobs.create("owner", table="party", format="pdf")
    with pytest.raises(ValueError, match="objects"):
        jobs.create("owner", table="party", filters=[])
    with pytest.raises(ValueError, match="owner"):
        jobs.create("", table="party")
    with pytest.raises(ValueError, match="Unsupported CRM table"):
        jobs.create("owner", table="missing")
    with pytest.raises(ValueError, match="Unsupported CRM report"):
        jobs.create("owner", report="missing")
    assert not jobs_module.re_full_uuid(None)
    assert not jobs_module.re_full_uuid("not-a-uuid")
    with pytest.raises(ValueError, match="not found"):
        jobs.status("owner", "not-a-uuid")
    job_id = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(ValueError, match="not found"):
        jobs.status("owner", job_id)
    directory = jobs.root / job_id
    directory.mkdir()
    (directory / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        jobs.status("owner", job_id)
    (directory / "manifest.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="not found"):
        jobs.status("owner", job_id)


def test_row_limit(database, tmp_path):
    jobs = service(database, tmp_path, max_rows=1)
    with pytest.raises(ValueError, match="row limit"):
        jobs.create("owner", table="document", filters={})


def test_create_zip_package_job_with_review_link(database, tmp_path):
    export_path, plan_path = package_sources(tmp_path)
    jobs = service(
        database,
        tmp_path,
        canonical_export=export_path,
        canonical_load_plan=plan_path,
    )
    created = jobs.create(
        "owner",
        package="staging",
        review_context={
            "schema_version": "review_export_link_v1",
            "source_kind": "visual_ingestion_session",
            "session_id": "session-1",
            "source_set_sha256": "b" * 64,
        },
    )
    assert created["format"] == "zip"
    assert created["subject"]["kind"] == "package"
    assert created["package_summary"]["package_kind"] == "staging"
    assert created["package_summary"]["manifest"] == "staging_manifest.json"
    token = created["download_url"].split("token=", 1)[1]
    artifact, manifest = jobs.download(created["job_id"], token)
    assert zipfile.is_zipfile(artifact)
    assert manifest["package_summary"]["verified"] is True
    with zipfile.ZipFile(artifact) as archive:
        names = set(archive.namelist())
    assert "staging_manifest.json" in names and "api_load_envelope.json" in names


def test_package_job_requires_matching_package_sources(database, tmp_path):
    export_path, plan_path = package_sources(tmp_path)
    jobs = service(database, tmp_path)
    with pytest.raises(ValueError, match="not enabled"):
        jobs.create("owner", package="staging")
    with pytest.raises(ValueError, match="supplied together"):
        service(database, tmp_path, canonical_export=export_path)
    wrong_export = {
        "batch_id": "other-batch",
        "tables": {"document": [], "party": []},
    }
    wrong_export_path = write(tmp_path / "wrong-export.json", wrong_export)
    wrong_plan_path = write(tmp_path / "wrong-plan.json", canonical.build_plan(wrong_export))
    with pytest.raises(ValueError, match="served retrieval snapshot"):
        service(
            database,
            tmp_path / "other",
            canonical_export=wrong_export_path,
            canonical_load_plan=wrong_plan_path,
        )
    configured = service(
        database,
        tmp_path / "configured",
        canonical_export=export_path,
        canonical_load_plan=plan_path,
    )
    created = configured.create("owner", package="staging")
    assert created["format"] == "zip"
    explicit = configured.create("owner", package="staging", format="zip")
    assert explicit["format"] == "zip"
    with pytest.raises(ValueError, match="package format must be zip"):
        configured.create("owner", package="staging", format="csv")
    with pytest.raises(ValueError, match="do not accept filters or parameters"):
        configured.create("owner", package="staging", filters={"x": 1})
    with pytest.raises(ValueError, match="do not accept filters or parameters"):
        configured.create("owner", package="staging", parameters={"x": 1})
    with pytest.raises(ValueError, match="package must be staging or common_import"):
        configured.create("owner", package="other")
    with pytest.raises(ValueError, match="readable files"):
        service(
            database,
            tmp_path / "missing-package-sources",
            canonical_export=tmp_path / "missing-export.json",
            canonical_load_plan=tmp_path / "missing-plan.json",
        )


def test_common_import_package_job_and_manifest_validation(database, tmp_path):
    export_path, plan_path = package_sources(tmp_path)
    jobs = service(
        database,
        tmp_path,
        canonical_export=export_path,
        canonical_load_plan=plan_path,
    )
    created = jobs.create("owner", package="common_import")
    assert created["package_summary"]["package_kind"] == "common_import"
    token = created["download_url"].split("token=", 1)[1]
    artifact, manifest = jobs.download(created["job_id"], token)
    assert zipfile.is_zipfile(artifact)
    manifest_path = jobs.root / created["job_id"] / "manifest.json"
    changed = jobs_module.json.loads(manifest_path.read_text())
    changed["package_summary"]["package_content_sha256"] = "bad"
    manifest_path.write_text(jobs_module.json.dumps(changed))
    with pytest.raises(ValueError, match="manifest is invalid"):
        jobs.status("owner", created["job_id"])


def test_zip_directory_skips_directories(tmp_path):
    root = tmp_path / "tree"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "value.txt").write_text("hello", encoding="utf-8")
    artifact = tmp_path / "tree.zip"
    jobs_module._zip_directory(root, artifact)
    with zipfile.ZipFile(artifact) as archive:
        assert archive.namelist() == ["nested/value.txt"]


def test_paged_table_and_report_row_limits(monkeypatch, database, tmp_path):
    source_hash = jobs_module.export_summary(database)["source_export_sha256"]
    pages = iter(
        [
            {
                "rows": [{"id": "1"}],
                "next_offset": 1,
                "source_export_sha256": source_hash,
            },
            {
                "rows": [{"id": "2"}],
                "next_offset": None,
                "source_export_sha256": source_hash,
            },
        ]
    )
    monkeypatch.setattr(jobs_module, "export_table", lambda *_args: next(pages))
    assert jobs_module._table_rows(database, "party", {}, 2, source_hash) == [
        {"id": "1"},
        {"id": "2"},
    ]
    monkeypatch.setattr(
        jobs_module,
        "export_table",
        lambda *_args: {
            "rows": [],
            "next_offset": None,
            "source_export_sha256": "0" * 64,
        },
    )
    with pytest.raises(ValueError, match="snapshot changed"):
        jobs_module._table_rows(database, "party", {}, 2, source_hash)
    jobs = service(database, tmp_path, max_rows=1)
    monkeypatch.setattr(
        jobs_module,
        "run_report",
        lambda *_args: {"rows": [{}, {}], "source_export_sha256": source_hash},
    )
    with pytest.raises(ValueError, match="row limit"):
        jobs.create("owner", report="account_directory")
    monkeypatch.setattr(
        jobs_module,
        "run_report",
        lambda *_args: {"rows": [], "source_export_sha256": "0" * 64},
    )
    with pytest.raises(ValueError, match="snapshot changed"):
        jobs.create("owner", report="account_directory")


def test_failed_creation_cleans_partial_job_and_manifest_tampering_fails_closed(
    monkeypatch, database, tmp_path
):
    jobs = service(database, tmp_path)
    monkeypatch.setattr(
        jobs_module, "_write_csv", lambda *_args: (_ for _ in ()).throw(OSError("x"))
    )
    with pytest.raises(OSError):
        jobs.create("owner", table="party", format="csv")
    assert list(jobs.root.iterdir()) == []

    monkeypatch.undo()
    created = jobs.create("owner", table="party", format="csv")
    manifest_path = jobs.root / created["job_id"] / "manifest.json"
    manifest = jobs_module.json.loads(manifest_path.read_text())
    for field, bad_value in (
        ("file", "../outside.csv"),
        ("owner_sha256", "bad"),
        ("expires_at_epoch", True),
    ):
        changed = {**manifest, field: bad_value}
        manifest_path.write_text(jobs_module.json.dumps(changed))
        with pytest.raises(ValueError, match="manifest is invalid"):
            jobs.status("owner", created["job_id"])
    manifest_path.write_text(jobs_module.json.dumps({**manifest, "unexpected": True}))
    with pytest.raises(ValueError, match="manifest is invalid"):
        jobs.status("owner", created["job_id"])


def test_xlsx_limits_and_streaming_hash(tmp_path):
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"abc")
    assert jobs_module._sha256_file(artifact) == hashlib.sha256(b"abc").hexdigest()
    with pytest.raises(ValueError, match="column limit"):
        jobs_module._write_xlsx(
            tmp_path / "wide.xlsx",
            [],
            [f"column-{index}" for index in range(jobs_module.MAX_XLSX_COLUMNS + 1)],
            {},
        )
    with pytest.raises(ValueError, match="character limit"):
        jobs_module._write_xlsx(
            tmp_path / "long.xlsx",
            [{"value": "x" * (jobs_module.MAX_XLSX_CELL_CHARACTERS + 1)}],
            ["value"],
            {},
        )
    empty = tmp_path / "empty.xlsx"
    jobs_module._write_xlsx(empty, [], [], {"status": "empty"})
    assert zipfile.is_zipfile(empty)
