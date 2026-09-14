"""Tests for vendor-neutral canonical deployment, load orchestration, and retrieval."""

import importlib
import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

canonical_deploy = importlib.import_module("canonical_deploy")
canonical_load = importlib.import_module("canonical_load")
retrieval_mcp = importlib.import_module("retrieval_mcp")
retrieval_store = importlib.import_module("retrieval_store")


def write(path, value):
    path.write_text(json.dumps(value))
    return path


def invoke(monkeypatch, module, *args):
    monkeypatch.setattr(sys, "argv", [module.__file__, *map(str, args)])
    module.main()


def export(review_status="auto_accepted"):
    return {
        "batch_id": "batch-1",
        "registry_version": "registry-2",
        "tables": {
            "document": [
                {
                    "document_id": "doc-1",
                    "natural_key": "INV-1",
                    "document_type": "commercial_invoice",
                    "source_file": "source.pdf",
                    "source_page_range": "1",
                    "source_sha256": "hash-1",
                    "review_status": review_status,
                    "batch_id": "batch-1",
                }
            ],
            "invoice_header": [
                {
                    "invoice_key": "invoice-1",
                    "source_document_id": "doc-1",
                    "invoice_number": "INV-1",
                    "biller_party_key": "party-1",
                    "payer_party_key": "party-2",
                    "total_amount": {"value": "110.00"},
                    "review_status": review_status,
                    "batch_id": "batch-1",
                }
            ],
            "invoice_line": [
                {
                    "invoice_line_key": "line-1",
                    "invoice_key": "invoice-1",
                    "description": {"value": "Blue widget"},
                    "quantity": 2,
                    "review_status": review_status,
                    "batch_id": "batch-1",
                }
            ],
            "party": [
                {
                    "party_key": "party-1",
                    "natural_key": "acme supply co",
                    "canonical_name": "ACME Supply Co",
                    "normalized_name": "acme supply co",
                    "review_status": review_status,
                    "batch_id": "batch-1",
                },
                {
                    "party_key": "party-2",
                    "natural_key": "northwind ltd",
                    "canonical_name": "Northwind Ltd",
                    "normalized_name": "northwind ltd",
                    "review_status": review_status,
                    "batch_id": "batch-1",
                },
            ],
            "selling_location": [
                {
                    "selling_location_key": "location-1",
                    "natural_key": "boston",
                    "location_name": "Boston",
                    "review_status": review_status,
                    "batch_id": "batch-1",
                }
            ],
            "acknowledgement": [
                {
                    "ack_key": "ack-1",
                    "ack_number": "ACK-1",
                    "selling_location_key": "location-1",
                    "review_status": review_status,
                    "batch_id": "batch-1",
                }
            ],
            "job": [
                {
                    "job_key": "job-1",
                    "job_number": "JOB-1",
                    "ack_key": "ack-1",
                    "review_status": review_status,
                    "batch_id": "batch-1",
                }
            ],
            "attribution": [
                {
                    "attribution_id": "attribution-1",
                    "source_document_id": "doc-1",
                    "target_table": "invoice_header",
                    "target_key": "invoice-1",
                    "amount": "110.00",
                    "ack_number": "ACK-1",
                    "job_number": "JOB-1",
                    "selling_location_key": "location-1",
                    "review_status": review_status,
                    "batch_id": "batch-1",
                }
            ],
        },
    }


def test_canonical_schema_enforces_append_only_amendments_and_manifest_grain():
    schema = (ROOT / "assets" / "canonical_schema.sql").read_text()
    assert "BEFORE UPDATE OR DELETE ON amendment" in schema
    assert "RAISE EXCEPTION 'amendment rows are append-only" in schema
    assert "DO INSTEAD NOTHING" not in schema
    assert "PRIMARY KEY (batch_id, load_step, entity)" in schema


def test_canonical_deployment_helpers_execute_and_cli(monkeypatch, tmp_path, capsys):
    schema = tmp_path / "schema.sql"
    schema.write_text(
        "BEGIN;\n"
        + "\n".join(
            f"CREATE TABLE {name} (\n    sample_id UUID\n);"
            for name in sorted(canonical_deploy.REQUIRED_TABLES)
        )
        + "\nCREATE VIEW v_x AS SELECT 1;\nCOMMIT;\n"
    )
    assert canonical_deploy.sha256(schema) == canonical_deploy.sha256(schema)
    assert set(canonical_deploy.schema_objects(schema)) == canonical_deploy.REQUIRED_TABLES | {
        "v_x"
    }
    assert canonical_deploy.deployment_plan(schema, "DATABASE_URL")["dialect"] == "postgresql"
    with pytest.raises(FileNotFoundError):
        canonical_deploy.schema_objects(tmp_path / "missing.sql")
    with pytest.raises(ValueError, match="missing required"):
        canonical_deploy.schema_objects(
            write(tmp_path / "invalid.sql", "BEGIN; CREATE TABLE other (); COMMIT;")
        )
    duplicate = schema.read_text().replace(
        "CREATE TABLE document (\n    sample_id UUID\n);",
        "CREATE TABLE document (\n    sample_id UUID,\n    sample_id TEXT\n);",
    )
    duplicate_path = tmp_path / "duplicate.sql"
    duplicate_path.write_text(duplicate)
    with pytest.raises(ValueError, match="duplicate columns"):
        canonical_deploy.schema_objects(duplicate_path)
    no_transaction = tmp_path / "no-transaction.sql"
    no_transaction.write_text(schema.read_text().replace("BEGIN;", "").replace("COMMIT;", ""))
    with pytest.raises(ValueError, match="explicit transaction"):
        canonical_deploy.schema_objects(no_transaction)
    with pytest.raises(ValueError, match="uppercase"):
        canonical_deploy.deployment_plan(schema, "bad")
    with pytest.raises(ValueError, match="not set"):
        canonical_deploy.execute(schema, "DATABASE_URL")
    monkeypatch.setenv("DATABASE_URL", "postgres://secret")

    class Result:
        returncode = 0
        stderr = ""

    calls = []
    monkeypatch.setattr(
        canonical_deploy.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)) or Result(),
    )
    canonical_deploy.execute(schema, "DATABASE_URL")
    assert calls[0][0][0][0] == "psql"
    assert "postgres://secret" not in calls[0][0][0]
    assert calls[0][1]["env"]["PGDATABASE"] == "postgres://secret"
    planned_hash = canonical_deploy.sha256(schema)
    schema.write_text(schema.read_text() + "\n-- changed after planning\n")
    with pytest.raises(ValueError, match="changed after"):
        canonical_deploy.execute(schema, "DATABASE_URL", planned_hash)
    schema.write_text(schema.read_text().replace("\n-- changed after planning\n", ""))
    monkeypatch.setattr(
        canonical_deploy.subprocess,
        "run",
        lambda *_args, **_kwargs: type("Failed", (), {"returncode": 1, "stderr": "bad"})(),
    )
    with pytest.raises(ValueError, match="psql deployment failed"):
        canonical_deploy.execute(schema, "DATABASE_URL")
    out = tmp_path / "plan.json"
    invoke(monkeypatch, canonical_deploy, "--schema", schema, "--out", out)
    assert json.loads(out.read_text())["credential_reference"] == "CANONICAL_DATABASE_URL"
    assert "executed" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="Output already exists"):
        invoke(monkeypatch, canonical_deploy, "--schema", schema, "--out", out)
    monkeypatch.setattr(canonical_deploy.subprocess, "run", lambda *_args, **_kwargs: Result())
    invoke(
        monkeypatch,
        canonical_deploy,
        "--schema",
        schema,
        "--out",
        tmp_path / "executed.json",
        "--database-url-env",
        "DATABASE_URL",
        "--execute",
    )


def test_canonical_load_validation_plan_and_cli(monkeypatch, tmp_path, capsys):
    data = export()
    path = write(tmp_path / "export.json", data)
    assert canonical_load.load_export(path) == data
    assert canonical_load.stable_checksum({"a": 1}) == canonical_load.stable_checksum({"a": 1})
    first_plan = canonical_load.build_plan(data)
    second_plan = canonical_load.build_plan(data)
    assert canonical_load.plan_checksum(first_plan) == canonical_load.plan_checksum(second_plan)
    for value, message in [([], "tables"), ({"tables": {}}, "batch_id")]:
        with pytest.raises(ValueError, match=message):
            canonical_load.load_export(write(tmp_path / f"bad-{len(str(value))}.json", value))
    canonical_load.validate_rows("document", data["tables"]["document"], "batch-1")
    canonical_load.validate_rows("currency", [{"currency_code": "USD"}], "batch-1")
    canonical_load.validate_rows(
        "party_role", [{"party_key": "party-1", "role": "payer"}], "batch-1"
    )
    for table, rows, message in [
        ("bad", [], "Unsupported"),
        ("document", {}, "Unsupported"),
        ("document", ["bad"], "objects"),
        ("document", [{"document_id": 1, "batch_id": "batch-1"}], "document_id"),
        ("document", [{"document_id": "x", "batch_id": "wrong"}], "batch_id"),
        ("carrier", [{"carrier_key": "x", "batch_id": "batch-1"}], "review_status"),
        ("party_role", [{"party_key": "x"}], "role"),
        (
            "document",
            [{"document_id": "x", "batch_id": "batch-1", "review_status": "open_exception"}],
            "open_exception",
        ),
    ]:
        with pytest.raises(ValueError, match=message):
            canonical_load.validate_rows(table, rows, "batch-1")
    with pytest.raises(ValueError, match="duplicate idempotency"):
        canonical_load.validate_rows(
            "currency", [{"currency_code": "USD"}, {"currency_code": "USD"}], "batch-1"
        )
    for table, row in (
        ("currency", {"currency_code": "USD", "review_status": "open_exception"}),
        (
            "party_role",
            {"party_key": "party-1", "role": "payer", "review_status": "open_exception"},
        ),
    ):
        rejected = export()
        rejected["tables"][table] = [row]
        with pytest.raises(ValueError, match="open_exception"):
            canonical_load.build_plan(rejected)
    plan = canonical_load.build_plan(data)
    assert plan["steps"][0]["table"] == "currency" and plan["steps"][-1]["records"] == 0
    assert plan["steps"][6]["table"] == "document"
    assert plan["steps"][6]["idempotency_keys"] == ["document_id"]
    assert plan["steps"][11]["idempotency_keys"] == ["party_key", "role"]
    assert plan["steps"][23]["table"] == "attribution"
    with pytest.raises(ValueError, match="Unsupported canonical tables"):
        canonical_load.build_plan({**data, "tables": {**data["tables"], "bad": []}})
    out = tmp_path / "load-plan.json"
    invoke(monkeypatch, canonical_load, path, "--out", out)
    assert json.loads(out.read_text())["batch_id"] == "batch-1"
    assert "steps" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="Output already exists"):
        invoke(monkeypatch, canonical_load, path, "--out", out)


def test_schema_catalog_rejects_empty_and_incomplete_ddl(tmp_path):
    empty_table = tmp_path / "empty-table.sql"
    empty_table.write_text("CREATE TABLE document (\n  CONSTRAINT valid CHECK (1 = 1)\n);\n")
    with pytest.raises(ValueError, match="declares no columns"):
        canonical_load.schema_catalog(empty_table)

    incomplete = tmp_path / "incomplete.sql"
    incomplete.write_text("CREATE TABLE document (\n  document_id text\n);\n")
    with pytest.raises(ValueError, match="missing load tables"):
        canonical_load.schema_catalog(incomplete)


def test_canonical_load_rejects_open_review_and_legacy_bypass(monkeypatch, tmp_path):
    open_export = export("open_exception")
    with pytest.raises(ValueError, match="open_exception"):
        canonical_load.build_plan(open_export)

    source = write(tmp_path / "open-export.json", open_export)
    with pytest.raises(SystemExit) as error:
        invoke(
            monkeypatch,
            canonical_load,
            source,
            "--out",
            tmp_path / "legacy-plan.json",
            "--include-open-review",
        )
    assert error.value.code == 2


@pytest.mark.parametrize("missing_field", ("source_file", "source_page_range", "source_sha256"))
def test_canonical_load_rejects_review_clear_document_without_provenance(missing_field):
    data = export()
    del data["tables"]["document"][0][missing_field]
    with pytest.raises(ValueError, match=missing_field):
        canonical_load.build_plan(data)


def test_canonical_load_rejects_child_with_broken_provenance_chain():
    data = export()
    data["tables"]["invoice_line"][0]["invoice_key"] = "missing-invoice"
    with pytest.raises(ValueError, match="invoice_key"):
        canonical_load.build_plan(data)

    missing_parent = export()
    del missing_parent["tables"]["invoice_line"][0]["invoice_key"]
    with pytest.raises(ValueError, match="requires provenance parent invoice_key"):
        canonical_load.build_plan(missing_parent)

    malformed_parent = export()
    malformed_parent["tables"]["invoice_line"][0]["invoice_key"] = 1
    with pytest.raises(ValueError, match="must be a non-empty string"):
        canonical_load.build_plan(malformed_parent)

    source_independent_parent = export()
    source_independent_parent["tables"]["party"] = [
        {
            "party_key": "party-1",
            "batch_id": "batch-1",
            "review_status": "auto_accepted",
        }
    ]
    source_independent_parent["tables"]["address"] = [
        {
            "address_key": "address-1",
            "party_key": "party-1",
            "batch_id": "batch-1",
            "review_status": "auto_accepted",
        }
    ]
    with pytest.raises(ValueError, match="provenance-valid document"):
        canonical_load.build_plan(source_independent_parent)


def test_retrieval_store_build_query_helpers_and_cli(monkeypatch, tmp_path, capsys):
    data = export()
    source = write(tmp_path / "export.json", data)
    assert retrieval_store.load_export(source) == data
    with pytest.raises(ValueError, match="tables"):
        retrieval_store.load_export(write(tmp_path / "bad.json", []))
    with pytest.raises(ValueError, match="batch_id"):
        retrieval_store.load_export(write(tmp_path / "no-batch.json", {"tables": {}}))
    assert retrieval_store.value({"value": "x"}) == "x" and retrieval_store.value("x") == "x"
    assert retrieval_store.allowed({"review_status": "auto_accepted"})
    assert not retrieval_store.allowed({"review_status": "open_exception"})
    assert not retrieval_store.allowed({})
    chunks = retrieval_store.document_chunks(data)
    assert chunks[0]["citation"]["registry_version"] == "registry-2"
    content = json.loads(chunks[0]["content"])
    assert content["attributions"][0]["job"]["job_number"] == "JOB-1"
    assert content["attributions"][0]["selling_location"]["location_name"] == "Boston"
    # An invoice names its parties only by key. Without this join no chunk holds
    # a party name and the runbook's own "unpaid ACME invoices" example matches
    # nothing; a party still under review stays out, key and name alike.
    assert content["invoices"][0]["biller"] == "ACME Supply Co"
    assert content["invoices"][0]["payer"] == "Northwind Ltd"
    party_open = export()
    party_open["tables"]["party"][1]["review_status"] = "open_exception"
    open_invoice = json.loads(retrieval_store.document_chunks(party_open)[0]["content"])
    assert "payer" not in open_invoice["invoices"][0]
    missing_links = export()
    attribution = missing_links["tables"]["attribution"][0]
    attribution["ack_number"] = "UNKNOWN"
    attribution["job_number"] = "UNKNOWN"
    attribution["selling_location_key"] = "UNKNOWN"
    assert (
        json.loads(retrieval_store.document_chunks(missing_links)[0]["content"])["attributions"][0]
        .keys()
        .isdisjoint({"acknowledgement", "job", "selling_location"})
    )
    assert retrieval_store.document_chunks(export("open_exception")) == []
    missing_provenance = export()
    del missing_provenance["tables"]["document"][0]["source_sha256"]
    with pytest.raises(ValueError, match="source_sha256"):
        retrieval_store.document_chunks(missing_provenance)
    invoice_open = export()
    invoice_open["tables"]["invoice_header"][0]["review_status"] = "open_exception"
    assert json.loads(retrieval_store.document_chunks(invoice_open)[0]["content"])["invoices"] == []
    line_open = export()
    line_open["tables"]["invoice_line"][0]["review_status"] = "open_exception"
    assert (
        json.loads(retrieval_store.document_chunks(line_open)[0]["content"])["invoices"][0]["lines"]
        == []
    )
    with pytest.raises(ValueError, match="document_id"):
        retrieval_store.document_chunks({"tables": {"document": [{}]}})
    database = tmp_path / "retrieval.sqlite"
    assert retrieval_store.build_database(data, database) == {
        "chunks": 1,
        "crm_records": 9,
        "crm_tables": 28,
        "factual_rows_only": True,
    }
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM crm_record").fetchone()[0] == 9
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM crm_record WHERE length(row_sha256) = 64"
            ).fetchone()[0]
            == 9
        )
        metadata = dict(connection.execute("SELECT key, value_json FROM crm_metadata"))
        assert "table_manifest" in metadata
    with pytest.raises(ValueError, match="already exists"):
        retrieval_store.build_database(data, database)
    assert retrieval_store.fts_query("blue widget!") == "blue AND widget"
    assert retrieval_store.fts_query("!!!") == ""
    assert retrieval_store.fts_query("SAMPLE-1001") == "SAMPLE AND 1001"
    assert retrieval_store.search(database, "blue")[0]["document_id"] == "doc-1"
    assert retrieval_store.search(database, "!!!") == []
    assert retrieval_store.document(database, "doc-1")["citation"]["source_file"] == "source.pdf"
    assert retrieval_store.document(database, "none") is None
    with pytest.raises(ValueError, match="limit"):
        retrieval_store.search(database, "blue", 0)
    with pytest.raises(ValueError, match="limit"):
        retrieval_store.search(database, "blue", True)
    with pytest.raises(ValueError, match="question must be a string"):
        retrieval_store.search(database, 1)
    missing_database = tmp_path / "empty.sqlite"
    with pytest.raises(ValueError, match="unavailable or invalid"):
        retrieval_store.search(missing_database, "blue")
    with pytest.raises(ValueError, match="unavailable or invalid"):
        retrieval_store.document(missing_database, "doc-1")
    assert not missing_database.exists()
    query_db = tmp_path / "cli.sqlite"
    invoke(monkeypatch, retrieval_store, "build", source, "--out", query_db)
    assert "chunks" in capsys.readouterr().out
    invoke(monkeypatch, retrieval_store, "query", query_db, "blue", "--limit", "1")
    assert "doc-1" in capsys.readouterr().out
    invoke(monkeypatch, retrieval_store, "get", query_db, "doc-1")
    assert "citation" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="Retrieval store failed"):
        invoke(monkeypatch, retrieval_store, "query", query_db, "blue", "--limit", "0")


def test_retrieval_build_failure_leaves_no_partial_database(monkeypatch, tmp_path):
    destination = tmp_path / "retrieval.sqlite"
    monkeypatch.setattr(
        retrieval_store.sqlite3,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("failed")),
    )
    with pytest.raises(sqlite3.OperationalError, match="failed"):
        retrieval_store.build_database(export(), destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".retrieval.sqlite.*.tmp"))


def test_retrieval_build_integrity_and_publish_failures_clean_temporary_files(
    monkeypatch, tmp_path
):
    real_connect = sqlite3.connect

    class IntegrityResult:
        @staticmethod
        def fetchone():
            return ("not-ok",)

    class IntegrityConnection:
        def __init__(self, path):
            self.connection = real_connect(path)

        def execute(self, statement, *args):
            if statement == "PRAGMA integrity_check":
                return IntegrityResult()
            return self.connection.execute(statement, *args)

        def __getattr__(self, name):
            return getattr(self.connection, name)

    integrity_destination = tmp_path / "integrity.sqlite"
    monkeypatch.setattr(
        retrieval_store.sqlite3, "connect", lambda path, **_kwargs: IntegrityConnection(path)
    )
    with pytest.raises(ValueError, match="integrity check failed"):
        retrieval_store.build_database(export(), integrity_destination)
    assert not integrity_destination.exists()
    assert not list(tmp_path.glob(".integrity.sqlite.*.tmp"))

    monkeypatch.setattr(retrieval_store.sqlite3, "connect", real_connect)
    monkeypatch.setattr(
        retrieval_store.os,
        "link",
        lambda *_args: (_ for _ in ()).throw(OSError("simulated publish failure")),
    )
    publish_destination = tmp_path / "publish.sqlite"
    with pytest.raises(OSError, match="publish failure"):
        retrieval_store.build_database(export(), publish_destination)
    assert not publish_destination.exists()
    assert not list(tmp_path.glob(".publish.sqlite.*.tmp"))


def test_retrieval_read_failures_are_bounded(tmp_path):
    invalid_citation = tmp_path / "invalid-citation.sqlite"
    retrieval_store.build_database(export(), invalid_citation)
    with sqlite3.connect(invalid_citation) as connection:
        connection.execute("UPDATE retrieval_chunk SET citation_json = '{'")
    with pytest.raises(ValueError, match="citation JSON is invalid"):
        retrieval_store.search(invalid_citation, "blue")
    with pytest.raises(ValueError, match="citation JSON is invalid"):
        retrieval_store.document(invalid_citation, "doc-1")

    broken_search = tmp_path / "broken-search.sqlite"
    retrieval_store.build_database(export(), broken_search)
    with sqlite3.connect(broken_search) as connection:
        connection.execute("DROP TABLE retrieval_fts")
    with pytest.raises(ValueError, match="Retrieval query failed"):
        retrieval_store.search(broken_search, "blue")

    broken_document = tmp_path / "broken-document.sqlite"
    retrieval_store.build_database(export(), broken_document)
    with sqlite3.connect(broken_document) as connection:
        connection.execute("DROP TABLE retrieval_chunk")
    with pytest.raises(ValueError, match="Retrieval query failed"):
        retrieval_store.document(broken_document, "doc-1")


def test_mcp_launcher_and_portable_config():
    config = json.loads((ROOT / "mcp" / "retrieval.mcp.json.example").read_text())
    args = config["mcpServers"]["business-document-retrieval"]["args"]
    assert args == [
        "<REPOSITORY_ROOT>/scripts/run_retrieval_mcp.sh",
        "<RETRIEVAL_DATABASE_PATH>",
    ]
    launcher = ROOT / "scripts" / "run_retrieval_mcp.sh"
    assert (
        launcher.stat().st_mode & 0o111 and "BUSINESS_DOCUMENT_RETRIEVAL_DB" in launcher.read_text()
    )


def test_retrieval_mcp_protocol_and_stdio(monkeypatch, tmp_path):
    database = tmp_path / "retrieval.sqlite"
    retrieval_store.build_database(export(), database)

    def rpc(method, params=None, request_id=1):
        request = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            request["params"] = params
        return request

    assert retrieval_mcp.response(1, {"ok": True})["result"]["ok"]
    assert retrieval_mcp.response(1, error="bad")["error"]["code"] == -32602
    assert retrieval_mcp.response(1, [], modern=True)["result"] == []
    error = retrieval_mcp.response(1, error="bad", code=-32022, data={"supported": []})
    assert error["error"]["data"] == {"supported": []}
    assert retrieval_mcp.tool_result([])["content"][0]["type"] == "text"
    assert retrieval_mcp.handle([], database)["error"]["code"] == -32600
    assert (
        retrieval_mcp.handle({"jsonrpc": "1.0", "method": "tools/list"}, database)["error"]["code"]
        == -32600
    )
    assert retrieval_mcp.handle({"jsonrpc": "2.0"}, database)["error"]["code"] == -32600
    assert retrieval_mcp.handle(rpc("notifications/initialized", request_id=None), database) is None

    discover = retrieval_mcp.handle(
        rpc(
            "server/discover",
            {"_meta": {retrieval_mcp.PROTOCOL_VERSION_META_KEY: "2026-07-28"}},
        ),
        database,
    )["result"]
    assert discover["supportedVersions"] == ["2026-07-28"]
    assert discover["resultType"] == "complete"
    assert discover["_meta"][retrieval_mcp.SERVER_INFO_META_KEY]["version"] == "1.0.0"

    initialize = retrieval_mcp.handle(
        rpc("initialize", {"protocolVersion": "2025-06-18"}), database
    )["result"]
    assert initialize["protocolVersion"] == "2025-06-18"
    assert initialize["serverInfo"]["version"] == "1.0.0"
    assert retrieval_mcp.handle(rpc("initialize"), database)["result"]["protocolVersion"] == (
        "2025-11-25"
    )
    assert len(retrieval_mcp.handle(rpc("tools/list"), database)["result"]["tools"]) == 12
    assert all(tool["annotations"]["readOnlyHint"] for tool in retrieval_mcp.TOOLS)
    modern_meta = {"_meta": {retrieval_mcp.PROTOCOL_VERSION_META_KEY: "2026-07-28"}}
    modern_list = retrieval_mcp.handle(rpc("tools/list", modern_meta), database)["result"]
    assert modern_list["_meta"][retrieval_mcp.SERVER_INFO_META_KEY]["name"]
    unsupported = retrieval_mcp.handle(
        rpc(
            "tools/list",
            {"_meta": {retrieval_mcp.PROTOCOL_VERSION_META_KEY: "2099-01-01"}},
        ),
        database,
    )["error"]
    assert unsupported["code"] == -32022 and unsupported["data"]["requested"] == "2099-01-01"
    assert retrieval_mcp.request_protocol_version({"params": []}) is None
    assert retrieval_mcp.request_protocol_version({"params": {"_meta": []}}) is None
    assert retrieval_mcp.handle(rpc("other"), database)["error"]["code"] == -32601
    assert "requires" in retrieval_mcp.handle(rpc("tools/call"), database)["error"]["message"]
    assert (
        "arguments"
        in retrieval_mcp.handle(rpc("tools/call", {"arguments": []}), database)["error"]["message"]
    )
    search_request = rpc(
        "tools/call",
        {
            "name": "search_business_documents",
            "arguments": {"query": "blue"},
            **modern_meta,
        },
    )
    search_result = retrieval_mcp.handle(search_request, database)["result"]
    assert "doc-1" in search_result["content"][0]["text"]
    assert search_result["_meta"][retrieval_mcp.SERVER_INFO_META_KEY]
    for name, arguments, message in [
        ("search_business_documents", {}, "query"),
        ("get_business_document", {}, "document_id"),
        ("get_business_document", {"document_id": "missing"}, "not found"),
        ("unknown", {}, "Unknown"),
    ]:
        request = rpc("tools/call", {"name": name, "arguments": arguments})
        assert message in retrieval_mcp.handle(request, database)["error"]["message"]
    get_request = rpc(
        "tools/call", {"name": "get_business_document", "arguments": {"document_id": "doc-1"}}
    )
    assert (
        "source.pdf" in retrieval_mcp.handle(get_request, database)["result"]["content"][0]["text"]
    )
    for name, arguments, expected in [
        ("get_crm_capabilities", {}, "standard_reports"),
        ("get_crm_export_summary", {}, "total_records"),
        ("get_crm_schema", {"table": "invoice_header"}, "idempotency_keys"),
        ("export_crm_table", {"table": "invoice_header", "limit": 1}, "page_sha256"),
        ("search_crm_records", {"query": "ACME"}, "party"),
        ("get_crm_record", {"table": "party", "key": "party-1"}, "record_sha256"),
        (
            "query_crm_records",
            {"table": "party", "filters": {"canonical_name": "ACME Supply Co"}},
            "query_sha256",
        ),
        ("get_account_card", {"party_key": "party-1"}, "card_sha256"),
        ("analyze_sales", {"group_by": ["vendor"]}, "analysis_sha256"),
        ("run_crm_report", {"report": "invoice_register"}, "report_sha256"),
    ]:
        result = retrieval_mcp.handle(
            rpc("tools/call", {"name": name, "arguments": arguments}), database
        )["result"]
        assert expected in result["content"][0]["text"]
    invalid = retrieval_mcp.handle(
        rpc("tools/call", {"name": "get_crm_capabilities", "arguments": {"extra": 1}}),
        database,
    )
    assert "Unknown tool arguments" in invalid["error"]["message"]
    for name, arguments, message in [
        ("search_business_documents", {"query": 1}, "query must be a string"),
        ("get_business_document", {"document_id": 1}, "document_id must be a string"),
        ("get_crm_schema", {"table": 1}, "table must be a string"),
        ("export_crm_table", {"table": 1}, "table must be a string"),
        ("get_crm_record", {"table": 1, "key": "x"}, "table must be a string"),
        ("query_crm_records", {"table": 1}, "table must be a string"),
        ("run_crm_report", {"report": 1}, "report must be a string"),
    ]:
        result = retrieval_mcp.handle(
            rpc("tools/call", {"name": name, "arguments": arguments}), database
        )
        assert message in result["error"]["message"]
    output = io.StringIO()
    retrieval_mcp.serve(
        database, io.StringIO("\nnot-json\n" + json.dumps(search_request) + "\n"), output
    )
    retrieval_mcp.serve(
        database,
        io.StringIO(json.dumps(rpc("notifications/initialized", request_id=None)) + "\n"),
        output,
    )
    first_error = [json.loads(line) for line in output.getvalue().splitlines()][0]["error"]
    assert first_error == {"code": -32700, "message": "Invalid JSON"}
    calls = []
    monkeypatch.setattr(retrieval_mcp, "serve", lambda path, **_kwargs: calls.append(path))
    invoke(monkeypatch, retrieval_mcp, database)
    assert calls == [str(database)]
