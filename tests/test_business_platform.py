"""Client-configured analytics and governed record-change contracts."""

import copy
import io
import json
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import business_analytics as analytics
import business_platform_deploy as deploy
import business_platform_server as server_launcher
import business_record_changes as changes
import client_platform_config as platform_config
import client_portal
import pytest
import retrieval_mcp
import retrieval_remote_mcp as remote
import retrieval_store
import yaml
from business_platform_tools import PlatformService, required_scope, tool_definitions
from client_platform_config import fingerprint, load, public_summary, validate
from test_crm_service import canonical_export
from test_retrieval_remote_mcp import FakeIntrospector, raw_request, rpc, running_handler
from test_retrieval_remote_mcp import request as http_request

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config(tmp_path):
    value = load(ROOT / "config" / "client-platform.example.yaml")
    value["deployment"]["snapshot"] = str(tmp_path / "crm.sqlite")
    value["deployment"]["change_dir"] = str(tmp_path / "changes")
    value["deployment"]["export_dir"] = str(tmp_path / "exports")
    value["deployment"]["audit_log"] = str(tmp_path / "audit.jsonl")
    value["deployment"]["intake_dir"] = str(tmp_path / "intake")
    value["record_maintenance"]["adapter"]["output_directory"] = str(tmp_path / "target-staging")
    retrieval_store.build_database(canonical_export(), value["deployment"]["snapshot"])
    return value


def request(operation="amend", expected=None):
    return {
        "schema_version": changes.VERSION,
        "operation": operation,
        "object": "account",
        "record_key": "customer-1" if operation == "amend" else "customer-2",
        "expected_record_sha256": expected,
        "values": {"canonical_name": "Updated Customer"},
        "clear_fields": [],
        "reason": "Client-authorized correction",
        "assertion_type": "user_assertion",
        "source_references": [],
    }


def test_example_config_and_public_summary(config):
    summary = public_summary(config)
    assert summary["schema_version"] == "business_data_platform_v1"
    assert summary["objects"] == ["account", "contact"]
    assert len(summary["configuration_sha256"]) == 64 == len(fingerprint(config))
    assert len(tool_definitions()) == 8
    assert required_scope("query_business_analytics") == "analytics:read"


def test_config_loader_refusals(tmp_path, config):
    missing = tmp_path / "missing.yaml"
    with pytest.raises(ValueError, match="regular"):
        load(missing)
    link = tmp_path / "link.yaml"
    link.symlink_to(ROOT / "config" / "client-platform.example.yaml")
    with pytest.raises(ValueError, match="regular"):
        load(link)
    large = tmp_path / "large.yaml"
    large.write_text("x" * (1_000_001))
    with pytest.raises(ValueError, match="byte limit"):
        load(large)
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(": bad")
    with pytest.raises(ValueError, match="invalid YAML"):
        load(invalid)
    secret = copy.deepcopy(config)
    secret["record_maintenance"]["adapter"]["api_key"] = "never"
    with pytest.raises(ValueError, match="unsupported fields"):
        validate(secret)
    config["record_maintenance"]["adapter"]["headers"] = {"access_token": "never"}
    with pytest.raises(ValueError, match="secret material"):
        validate(config)


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda c: c.update(schema_version="old"), "schema_version"),
        (lambda c: c.update(extra=True), "unsupported"),
        (lambda c: c["client"].update(tenant_id=""), "tenant_id"),
        (lambda c: c["client"].update(fiscal_year_start_month=13), "fiscal"),
        (lambda c: c["deployment"].update(port=True), "port"),
        (lambda c: c["deployment"].update(allowed_roles=[]), "allowed_roles"),
        (lambda c: c["record_maintenance"].update(enabled="yes"), "switches"),
        (lambda c: c["record_maintenance"]["adapter"].update(kind="unknown"), "file or http"),
        (lambda c: c["analytics"].update(enabled="yes"), "Boolean"),
        (lambda c: c["analytics"].update(saved_reports=[]), "saved_reports"),
    ],
)
def test_config_contract_refusals(config, mutate, message):
    mutate(config)
    with pytest.raises(ValueError, match=message):
        validate(config)


def test_typed_analytics_and_saved_reports(config):
    model = analytics.semantic_model(config)
    assert not model["arbitrary_sql_permitted"]
    plan = {
        "dataset": "invoice_sales",
        "dimensions": ["invoice_date", "customer", "region"],
        "date_grains": {"invoice_date": "month"},
        "metrics": ["sales", "invoice_count"],
        "filters": {"region": "MA"},
        "sort": ["invoice_date"],
        "limit": 10,
    }
    result = analytics.query(config["deployment"]["snapshot"], config, plan)
    assert result["status"] == "complete"
    assert result["effective_dimensions"][-1] == "currency"
    assert result["coverage"] == {
        "input_rows": 1,
        "selected_rows": 1,
        "grouped_rows": 1,
        "groups_after_having": 1,
        "exception_groups": 0,
    }
    assert result["rows"][0] == {
        "invoice_date": "2026-01",
        "customer": "Customer Inc",
        "region": "MA",
        "currency": "USD",
        "sales": "125.00",
        "invoice_count": "1",
        "source_rows": 1,
    }
    saved = analytics.run_saved(config["deployment"]["snapshot"], config, "sales_by_customer")
    assert saved["saved_report"] == "sales_by_customer"
    assert len(saved["result_sha256"]) == 64


def test_analytics_date_grains_and_filters(config):
    base = {
        "dataset": "invoice_sales",
        "dimensions": ["invoice_date"],
        "metrics": ["invoice_count"],
    }
    expected = {
        "day": "2026-01-15",
        "quarter": "2026-Q1",
        "year": "2026",
        "fiscal_quarter": "FY2026-Q1",
        "fiscal_year": "FY2026",
    }
    for grain, value in expected.items():
        plan = {**base, "date_grains": {"invoice_date": grain}}
        assert (
            analytics.query(config["deployment"]["snapshot"], config, plan)["rows"][0][
                "invoice_date"
            ]
            == value
        )
    for condition in (
        {"operator": "in", "value": ["2026-01-15"]},
        {"operator": "gte", "value": "2026-01-01"},
        {"operator": "lte", "value": "2026-12-31"},
    ):
        result = analytics.query(
            config["deployment"]["snapshot"],
            config,
            {**base, "filters": {"invoice_date": condition}},
        )
        assert result["coverage"]["selected_rows"] == 1
    contains = analytics.query(
        config["deployment"]["snapshot"],
        config,
        {
            "dataset": "invoice_sales",
            "dimensions": ["customer"],
            "metrics": ["invoice_count"],
            "filters": {"customer": {"operator": "contains", "value": "customer"}},
        },
    )
    assert contains["coverage"]["selected_rows"] == 1


def test_analytics_having_totals_and_numeric_sort(config):
    export = canonical_export()
    second = copy.deepcopy(export["tables"]["invoice_header"][0])
    second.update(
        invoice_key="invoice-2", invoice_number="INV-2", total_amount="9.00", ack_number="ACK-2"
    )
    export["tables"]["invoice_header"].append(second)
    database = Path(config["deployment"]["snapshot"]).with_name("sort.sqlite")
    retrieval_store.build_database(export, database)
    result = analytics.query(
        database,
        config,
        {
            "dataset": "invoice_sales",
            "dimensions": ["ack_number"],
            "metrics": ["sales"],
            "having": {"sales": {"operator": "gte", "value": "0"}},
            "include_totals": True,
            "sort": ["-sales"],
        },
    )
    assert result["rows"][0]["sales"] == "125.00"
    assert result["totals"] == {
        "partition_dimensions": ["currency"],
        "rows": [{"currency": "USD", "source_rows": 2, "sales": "134.00"}],
    }


@pytest.mark.parametrize(
    "plan,message",
    [
        ({"dataset": "missing", "metrics": ["x"]}, "unsupported dataset"),
        ({"dataset": "invoice_sales", "metrics": []}, "non-empty list"),
        ({"dataset": "invoice_sales", "metrics": ["missing"]}, "unsupported fields"),
        (
            {"dataset": "invoice_sales", "metrics": ["invoice_count"], "filters": {"missing": 1}},
            "filters",
        ),
        ({"dataset": "invoice_sales", "metrics": ["invoice_count"], "sort": ["missing"]}, "sort"),
        ({"dataset": "invoice_sales", "metrics": ["invoice_count"], "limit": 0}, "limit"),
        ({"dataset": "invoice_sales", "metrics": ["invoice_count"], "offset": True}, "offset"),
    ],
)
def test_analytics_plan_refusals(config, plan, message):
    with pytest.raises(ValueError, match=message):
        analytics.query(config["deployment"]["snapshot"], config, plan)


def test_analytics_retains_invalid_metric_and_join_failures(config, monkeypatch):
    export = canonical_export()
    export["tables"]["invoice_header"][0]["total_amount"] = "bad"
    bad = Path(config["deployment"]["snapshot"]).with_name("bad.sqlite")
    retrieval_store.build_database(export, bad)
    result = analytics.query(bad, config, {"dataset": "invoice_sales", "metrics": ["sales"]})
    assert result["status"] == "completed_with_exceptions"
    assert result["rows"][0]["sales"] is None
    original_records = analytics._records

    def duplicate_records(connection, table):
        rows = original_records(connection, table)
        return [*rows, copy.deepcopy(rows[-1])] if table == "party" else rows

    monkeypatch.setattr(analytics, "_records", duplicate_records)
    with pytest.raises(ValueError, match="fan-out"):
        analytics.query(
            config["deployment"]["snapshot"],
            config,
            {"dataset": "invoice_sales", "metrics": ["invoice_count"]},
        )


def test_record_change_propose_preview_authorize_and_file_apply(config, tmp_path):
    store = changes.ChangeStore(
        config["deployment"]["change_dir"], "sample-tenant", fingerprint(config)
    )
    prior = __import__("crm_service").get_record(
        config["deployment"]["snapshot"], "party", "customer-1"
    )["record"]
    payload = request(expected=changes.stable_checksum(prior))
    receipt = store.propose("submitter", "request-1", config, payload)
    assert store.propose("submitter", "request-1", config, payload) == receipt
    preview = store.preview(
        "submitter", receipt["change_id"], config["deployment"]["snapshot"], config
    )
    assert preview["before"]["canonical_name"] == "Customer Inc"
    assert preview["after"]["canonical_name"] == "Updated Customer"
    expiry = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    secret = "s" * 32
    authorization = changes.authorize(preview, "submitter", "approver", expiry, secret)
    retained_auth = store.record_lifecycle(
        "submitter", "authorization", "auth-1", receipt["change_id"], authorization
    )
    assert retained_auth["kind"] == "authorization"
    applied = changes.apply_change(
        config, preview, authorization, secret, "apply-1", tmp_path / "out"
    )
    assert applied["transport"] == "file"
    assert applied["canonical_snapshot_changed"] is False
    store.record_lifecycle("submitter", "application", "apply-1", receipt["change_id"], applied)
    reconciled = changes.reconcile(config, preview, applied, tmp_path / "out")
    store.record_lifecycle(
        "submitter", "reconciliation", "reconcile-1", receipt["change_id"], reconciled
    )
    assert [item["kind"] for item in store.history("submitter", receipt["change_id"])[1:]] == [
        "authorization",
        "application",
        "reconciliation",
    ]
    assert (
        json.loads((tmp_path / "out" / f"{receipt['change_id']}.json").read_text())["account_name"]
        == "Updated Customer"
    )
    with pytest.raises(FileExistsError):
        changes.apply_change(config, preview, authorization, secret, "apply-1", tmp_path / "out")


def test_analytics_download_job(config, tmp_path):
    jobs = remote.ExportJobs(
        config["deployment"]["snapshot"], tmp_path / "exports", "https://crm.example.com"
    )
    service = PlatformService(config, config["deployment"]["snapshot"], export_jobs=jobs)
    result = service.invoke(
        "owner",
        "create_business_analytics_export",
        {
            "request": {"report": "sales_by_customer"},
            "format": "xlsx",
        },
    )
    assert result["subject"]["kind"] == "analytics"
    assert jobs.status("owner", result["job_id"])["file_sha256"] == result["file_sha256"]


def test_analytics_export_job_contract(config, tmp_path):
    jobs = remote.ExportJobs(
        config["deployment"]["snapshot"], tmp_path / "analytics-exports", "https://crm.example.com"
    )
    good_subject = {"kind": "analytics", "name": "query", "arguments": {}}
    snapshot = {"batch_id": "batch", "source_export_sha256": "a" * 64}
    for kwargs, message in (
        ({"owner": "", "subject": good_subject, "rows": [], "snapshot": snapshot}, "owner"),
        (
            {
                "owner": "owner",
                "subject": good_subject,
                "rows": [],
                "snapshot": snapshot,
                "format": "pdf",
            },
            "format",
        ),
        ({"owner": "owner", "subject": {}, "rows": [], "snapshot": snapshot}, "subject"),
        ({"owner": "owner", "subject": good_subject, "rows": {}, "snapshot": snapshot}, "rows"),
        ({"owner": "owner", "subject": good_subject, "rows": [], "snapshot": {}}, "snapshot"),
    ):
        with pytest.raises(ValueError, match=message):
            jobs.create_rows(**kwargs)
    limited = remote.ExportJobs(
        config["deployment"]["snapshot"],
        tmp_path / "limited-analytics-exports",
        "https://crm.example.com",
        max_rows=1,
    )
    with pytest.raises(ValueError, match="row limit"):
        limited.create_rows("owner", subject=good_subject, rows=[{}, {}], snapshot=snapshot)


def test_record_change_service_and_create(config):
    service = PlatformService(config, config["deployment"]["snapshot"])
    schema = service.invoke("owner", "get_business_object_schema", {})
    assert schema["model_authorization_permitted"] is False
    proposed = service.invoke(
        "owner",
        "propose_business_record_change",
        {"idempotency_key": "new", "request": request("create")},
    )
    preview = service.invoke(
        "owner", "preview_business_record_change", {"change_id": proposed["change_id"]}
    )
    assert preview["before"] is None and preview["target_payload"]["external_id"] == "customer-2"
    retained = service.invoke(
        "owner", "get_business_record_change", {"change_id": proposed["change_id"]}
    )
    assert retained["receipt"] == proposed
    assert service.invoke("owner", "get_business_platform_capabilities", {})[
        "record_changes_are_proposals"
    ]
    assert service.invoke("owner", "get_analytics_semantic_model", {})["datasets"]
    assert service.invoke("owner", "run_saved_business_report", {"report": "sales_by_customer"})[
        "rows"
    ]
    assert service.invoke(
        "owner",
        "query_business_analytics",
        {"plan": {"dataset": "invoice_sales", "metrics": ["invoice_count"]}},
    )["rows"]

    listing = retrieval_mcp.handle(
        rpc("tools/list"), config["deployment"]["snapshot"], platform=service
    )
    assert len(listing["result"]["tools"]) == 20
    result = retrieval_mcp.handle(
        rpc("tools/call", {"name": "get_business_platform_capabilities", "arguments": {}}),
        config["deployment"]["snapshot"],
        platform=service,
    )
    assert result["result"]["structuredContent"]["result"]["analytics_enabled"]


def test_local_mcp_platform_fallback_and_yaml_main(config, tmp_path, monkeypatch):
    service = PlatformService(config, config["deployment"]["snapshot"])
    assert retrieval_mcp.handle(
        rpc("tools/call", {"name": "get_crm_capabilities", "arguments": {}}),
        config["deployment"]["snapshot"],
        platform=service,
    )["result"]["structuredContent"]["result"]
    source = write_config(tmp_path, config)
    captured = {}
    monkeypatch.setattr(
        retrieval_mcp,
        "serve",
        lambda database, ingestion=None, platform=None: captured.update(
            database=database, ingestion=ingestion, platform=platform
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["retrieval_mcp.py", config["deployment"]["snapshot"], "--client-config", str(source)],
    )
    retrieval_mcp.main()
    assert captured["platform"].config["client"]["tenant_id"] == "sample-tenant"


def test_visual_intake_snapshot_and_read_only_store(config, tmp_path):
    from visual_ingestion import IngestionStore

    store = IngestionStore(tmp_path / "intake", "sample-tenant")
    created = store.create_session("owner", "listing", 1)
    status, rows = store.snapshot("owner", created["session_id"])
    assert status["session_id"] == created["session_id"] and len(rows) == 1
    read_only = IngestionStore(store.root, "sample-tenant", read_only=True)
    assert read_only.status("owner", created["session_id"])["status"] == "awaiting_pages"
    with pytest.raises(ValueError, match="read-only"):
        read_only.create_session("owner", "another", 1)


def test_remote_platform_mcp_and_json_api(config, tmp_path):
    service = PlatformService(config, config["deployment"]["snapshot"])
    auth = FakeIntrospector(scopes=("platform:read", "analytics:read", "records:propose"))
    with running_handler(
        config["deployment"]["snapshot"], tmp_path, auth=auth, platform=service
    ) as (server, _audit):
        status, _headers, raw = http_request(server, "GET", "/.well-known/oauth-protected-resource")
        assert status == 200 and "analytics:read" in raw["scopes_supported"]
        body = json.dumps({"plan": {"dataset": "invoice_sales", "metrics": ["invoice_count"]}})
        status, _headers, raw = http_request(
            server,
            "POST",
            "/api/platform/query_business_analytics",
            body,
            {"Authorization": "Bearer token", "Content-Type": "application/json"},
        )
        assert status == 200 and raw["rows"]
        listing = json.dumps(rpc("tools/list"))
        status, _headers, raw = http_request(
            server,
            "POST",
            "/mcp",
            listing,
            {"Authorization": "Bearer token", "Content-Type": "application/json"},
        )
        names = {item["name"] for item in raw["result"]["tools"]}
        assert {"query_business_analytics", "propose_business_record_change"} <= names
        assert "search_crm_records" not in names
    assert (
        remote.required_scope({"params": {"name": "propose_business_record_change"}})
        == "records:propose"
    )


def test_remote_operator_api_static_portal_and_tenant_guard(config, tmp_path, monkeypatch):
    configured = copy.deepcopy(config)
    configured["record_maintenance"]["separation_of_duties"] = False
    configured["deployment"]["change_dir"] = str(tmp_path / "operator-changes")
    service = PlatformService(
        configured,
        configured["deployment"]["snapshot"],
        authorization_secret="s" * 32,
    )
    proposal = service.invoke(
        "user-1",
        "propose_business_record_change",
        {"idempotency_key": "remote-operator", "request": request("create")},
    )
    auth = FakeIntrospector(
        scopes=(
            "platform:read",
            "analytics:read",
            "records:propose",
            "records:authorize",
            "records:apply",
        )
    )
    from visual_ingestion import IngestionStore

    intake = IngestionStore(tmp_path / "portal-intake", "sample-tenant")
    with running_handler(
        configured["deployment"]["snapshot"],
        tmp_path,
        auth=auth,
        platform=service,
        ingestion=intake,
    ) as (server, _audit):
        for path, marker in (("/portal.css", "text/css"), ("/portal.js", "javascript")):
            status, headers, _raw = raw_request(server, "GET", path)
            assert status == 200 and marker in headers.get("Content-Type", "")
        body = {
            "owner": "user-1",
            "change_id": proposal["change_id"],
            "idempotency_key": "remote-authorize",
            "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "decision": "authorize",
        }
        status, _headers, authorization = http_request(
            server,
            "POST",
            "/api/platform/authorize_business_record_change",
            json.dumps(body),
            {"Authorization": "Bearer token", "Content-Type": "application/json"},
        )
        assert status == 200 and authorization["authorization"]["approver"] == "user-1"
        status, _headers, application = http_request(
            server,
            "POST",
            "/api/platform/apply_business_record_change",
            json.dumps(
                {
                    "owner": "user-1",
                    "change_id": proposal["change_id"],
                    "idempotency_key": "remote-apply",
                    "authorization_sha256": authorization["receipt"]["artifact_sha256"],
                    "execute": False,
                }
            ),
            {"Authorization": "Bearer token", "Content-Type": "application/json"},
        )
        assert status == 200 and application["application"]["transport"] == "file"
        status, _headers, reconciliation = http_request(
            server,
            "POST",
            "/api/platform/reconcile_business_record_change",
            json.dumps(
                {
                    "owner": "user-1",
                    "change_id": proposal["change_id"],
                    "idempotency_key": "remote-reconcile",
                    "application_sha256": application["receipt"]["artifact_sha256"],
                }
            ),
            {"Authorization": "Bearer token", "Content-Type": "application/json"},
        )
        assert status == 200 and reconciliation["reconciliation"]["status"] == "reconciled"
    assert remote.required_scope({"params": {"name": "records:missing"}}) == "crm:read"
    assert (
        remote.required_scope({"params": {"name": "authorize_business_record_change"}})
        == "records:authorize"
    )
    metadata = remote.protected_resource_metadata(
        "https://crm.example.com/mcp", "https://id.example.com", platform=True
    )
    assert {"records:authorize", "records:apply"} <= set(metadata["scopes_supported"])

    class DifferentTenant:
        resource = "https://crm.example.com/mcp"
        tenant = "different"

    exports = remote.ExportJobs(
        configured["deployment"]["snapshot"], tmp_path / "tenant-exports", "https://crm.example.com"
    )
    with pytest.raises(ValueError, match="Platform tenant"):
        remote.build_server(
            configured["deployment"]["snapshot"],
            "127.0.0.1",
            9443,
            configured["deployment"]["snapshot"],
            configured["deployment"]["snapshot"],
            DifferentTenant(),
            exports,
            "https://crm.example.com/mcp",
            "https://id.example.com",
            remote.AuditLog(tmp_path / "tenant-audit.jsonl"),
            platform=service,
        )


def test_record_change_refusals(config):
    store = changes.ChangeStore(
        config["deployment"]["change_dir"], "sample-tenant", fingerprint(config)
    )
    bad = request(expected="0" * 64)
    receipt = store.propose("owner", "bad-version", config, bad)
    with pytest.raises(ValueError, match="stale"):
        store.preview("owner", receipt["change_id"], config["deployment"]["snapshot"], config)
    with pytest.raises(ValueError, match="different input"):
        store.propose("owner", "bad-version", config, {**bad, "reason": "changed"})
    with pytest.raises(ValueError, match="not found"):
        store.proposal("other", receipt["change_id"])
    with pytest.raises(ValueError, match="differ"):
        changes.authorize(
            {"change_id": "x", "preview_sha256": "y", "configuration_sha256": "z"},
            "same",
            "same",
            (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
            "s" * 32,
        )


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_http_json_adapter(config, tmp_path, monkeypatch):
    store = changes.ChangeStore(
        config["deployment"]["change_dir"], "sample-tenant", fingerprint(config)
    )
    prior = __import__("crm_service").get_record(
        config["deployment"]["snapshot"], "party", "customer-1"
    )["record"]
    receipt = store.propose(
        "submitter", "http", config, request(expected=changes.stable_checksum(prior))
    )
    preview = store.preview(
        "submitter", receipt["change_id"], config["deployment"]["snapshot"], config
    )
    auth = changes.authorize(
        preview,
        "submitter",
        "approver",
        (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "s" * 32,
    )
    adapter = config["record_maintenance"]["adapter"]
    adapter.update(
        kind="http_json",
        base_url="https://target.example.com",
        credential_env="TARGET_TOKEN",
        response_id_field="record_id",
    )
    monkeypatch.setenv("TARGET_TOKEN", "private")
    seen = []

    def opener(req, timeout):
        seen.append((req, timeout))
        return Response(b'{"record_id":"external-1"}')

    result = changes.apply_change(config, preview, auth, "s" * 32, "http-apply", tmp_path, opener)
    assert result["external_id"] == "external-1" and seen[0][0].method == "PATCH"
    assert seen[0][0].headers["Authorization"] == "Bearer private"


def test_change_journal_integrity_and_scope(config):
    store = changes.ChangeStore(
        config["deployment"]["change_dir"], "sample-tenant", fingerprint(config)
    )
    receipt = store.propose("owner", "one", config, request("create"))
    with closing(sqlite3.connect(store.path)) as connection, connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM events")
        connection.execute("DROP TRIGGER changes_no_update")
        connection.execute("UPDATE events SET result='{}'")
    with pytest.raises(ValueError, match="integrity"):
        store.proposal("owner", receipt["change_id"])


def test_config_is_yaml_serializable(config):
    assert yaml.safe_load(yaml.safe_dump(config)) == config


def write_config(tmp_path, config, name="client.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def test_config_rejects_ambiguous_and_unsafe_values(tmp_path, config):
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text("schema_version: one\nschema_version: two\n")
    with pytest.raises(ValueError, match="invalid YAML"):
        load(duplicate)

    mutations = [
        (lambda c: c.__setitem__("client", []), "client must be an object"),
        (lambda c: c["client"].pop("display_name"), "missing fields"),
        (lambda c: c["client"].update(timezone="Mars/Olympus"), "IANA timezone"),
        (lambda c: c["deployment"].update(snapshot="relative.sqlite"), "absolute path"),
        (lambda c: c["deployment"].update(client_secret_env="bad-name"), "environment"),
        (lambda c: c["deployment"].update(public_base_url="https://x.test/path"), "origin"),
        (lambda c: c["deployment"].update(allowed_origins=["*"]), "HTTPS origin"),
        (
            lambda c: c["deployment"].update(allowed_roles=["reader", "reader"]),
            "must be unique",
        ),
        (
            lambda c: c["record_maintenance"]["adapter"].update(timeout_seconds=0),
            "timeout_seconds",
        ),
        (
            lambda c: c["record_maintenance"]["adapter"].update(response_id_field=""),
            "response_id_field",
        ),
    ]
    for mutate, message in mutations:
        candidate = copy.deepcopy(config)
        mutate(candidate)
        with pytest.raises(ValueError, match=message):
            validate(candidate)

    nested_secret = copy.deepcopy(config)
    nested_secret["record_maintenance"]["adapter"]["headers"] = [{"private_key": "literal"}]
    with pytest.raises(ValueError, match="headers"):
        validate(nested_secret)


def test_operator_api_completes_governed_file_lifecycle(config):
    service = PlatformService(
        config,
        config["deployment"]["snapshot"],
        authorization_secret="s" * 32,
    )
    proposal = service.invoke(
        "submitter",
        "propose_business_record_change",
        {"idempotency_key": "operator-proposal", "request": request("create")},
    )
    expires = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    authorized = service.invoke_operator(
        "approver",
        "authorize_business_record_change",
        {
            "owner": "submitter",
            "change_id": proposal["change_id"],
            "idempotency_key": "operator-auth",
            "expires_at": expires,
            "decision": "authorize",
        },
    )
    auth_hash = authorized["receipt"]["artifact_sha256"]
    applied = service.invoke_operator(
        "operator",
        "apply_business_record_change",
        {
            "owner": "submitter",
            "change_id": proposal["change_id"],
            "idempotency_key": "operator-apply",
            "authorization_sha256": auth_hash,
            "execute": False,
        },
    )
    assert applied["application"]["transport"] == "file"
    assert not applied["idempotent_replay"]
    replay = service.invoke_operator(
        "operator",
        "apply_business_record_change",
        {
            "owner": "submitter",
            "change_id": proposal["change_id"],
            "idempotency_key": "ignored-on-replay",
            "authorization_sha256": auth_hash,
            "execute": False,
        },
    )
    assert replay["idempotent_replay"]
    reconciled = service.invoke_operator(
        "operator",
        "reconcile_business_record_change",
        {
            "owner": "submitter",
            "change_id": proposal["change_id"],
            "idempotency_key": "operator-reconcile",
            "application_sha256": applied["receipt"]["artifact_sha256"],
        },
    )
    assert reconciled["reconciliation"]["status"] == "reconciled"


def test_operator_api_refusals(config):
    service = PlatformService(config, config["deployment"]["snapshot"])
    with pytest.raises(ValueError, match="Unknown operator"):
        service.invoke_operator("actor", "missing", {})
    with pytest.raises(ValueError, match="unavailable"):
        service.invoke_operator("actor", "authorize_business_record_change", {})
    config["record_maintenance"]["enabled"] = False
    config["deployment"]["change_dir"] = str(
        Path(config["deployment"]["change_dir"]).with_name("disabled-changes")
    )
    disabled = PlatformService(
        config, config["deployment"]["snapshot"], authorization_secret="s" * 32
    )
    with pytest.raises(ValueError, match="disabled"):
        disabled.invoke_operator("actor", "authorize_business_record_change", {})


def test_operator_action_shape_and_http_execution_refusals(config):
    service = PlatformService(
        config, config["deployment"]["snapshot"], authorization_secret="s" * 32
    )
    proposed = service.invoke(
        "submitter",
        "propose_business_record_change",
        {"idempotency_key": "shape-proposal", "request": request("create")},
    )
    with pytest.raises(ValueError, match="requires exactly"):
        service.invoke_operator("approver", "authorize_business_record_change", {})
    base = {
        "owner": "submitter",
        "change_id": proposed["change_id"],
        "idempotency_key": "shape-auth",
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "decision": "no",
    }
    with pytest.raises(ValueError, match="explicit"):
        service.invoke_operator("approver", "authorize_business_record_change", base)
    base["decision"] = "authorize"
    assert (
        service.invoke_operator("approver", "authorize_business_record_change", base)[
            "authorization"
        ]["decision"]
        == "authorize"
    )
    http_config = copy.deepcopy(config)
    http_config["deployment"]["change_dir"] = str(
        Path(config["deployment"]["change_dir"]).with_name("http-changes")
    )
    http_config["record_maintenance"]["adapter"] = {
        "kind": "http_json",
        "base_url": "https://target.example.test",
        "credential_env": "TARGET_TOKEN",
    }
    http_service = PlatformService(
        http_config,
        http_config["deployment"]["snapshot"],
        authorization_secret="s" * 32,
    )
    proposal = http_service.invoke(
        "submitter",
        "propose_business_record_change",
        {"idempotency_key": "http-shape", "request": request("create")},
    )
    authorized = http_service.invoke_operator(
        "approver",
        "authorize_business_record_change",
        {
            "owner": "submitter",
            "change_id": proposal["change_id"],
            "idempotency_key": "http-auth",
            "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "decision": "authorize",
        },
    )
    with pytest.raises(ValueError, match="Boolean"):
        http_service.invoke_operator(
            "operator",
            "apply_business_record_change",
            {
                "owner": "submitter",
                "change_id": proposal["change_id"],
                "idempotency_key": "http-apply",
                "authorization_sha256": authorized["receipt"]["artifact_sha256"],
                "execute": "yes",
            },
        )
    with pytest.raises(ValueError, match="execute=true"):
        http_service.invoke_operator(
            "operator",
            "apply_business_record_change",
            {
                "owner": "submitter",
                "change_id": proposal["change_id"],
                "idempotency_key": "http-apply",
                "authorization_sha256": authorized["receipt"]["artifact_sha256"],
                "execute": False,
            },
        )


def test_deployment_plan_and_assets(config, tmp_path):
    result = deploy.plan(config)
    assert result["endpoints"]["portal"].endswith("/portal")
    assert {"records:authorize", "records:apply"} <= set(result["scopes"])
    assert result["production_accepted"] is False
    out = tmp_path / "plan.json"
    deploy.write_new(out, result)
    assert json.loads(out.read_text())["plan_sha256"] == result["plan_sha256"]
    with pytest.raises(FileExistsError):
        deploy.write_new(out, result)
    for name in (
        ".dockerignore",
        "deploy/business-platform/Dockerfile",
        "deploy/business-platform/compose.yaml",
        "deploy/business-platform/kubernetes.yaml",
        "deploy/business-platform/business-data-platform.service",
    ):
        assert (ROOT / name).is_file()


def test_deployment_plan_disabled_optional_surfaces(config):
    config["deployment"].pop("intake_dir")
    config["record_maintenance"]["enabled"] = False
    result = deploy.plan(config)
    assert result["endpoints"]["portal"] is None
    assert result["endpoints"]["ingestion_api"] is None
    assert (
        config["record_maintenance"]["authorization_secret_env"]
        not in result["secret_environment_variables"]
    )
    config["deployment"]["public_base_url"] = "https://example.com/path"
    with pytest.raises(ValueError, match="origin"):
        deploy.plan(config)


def test_deployment_cli(config, tmp_path, monkeypatch, capsys):
    source = write_config(tmp_path, config)
    out = tmp_path / "deployment.json"
    monkeypatch.setattr(
        sys, "argv", ["business_platform_deploy.py", str(source), "--out", str(out)]
    )
    deploy.main()
    assert json.loads(capsys.readouterr().out)["production_accepted"] is False
    monkeypatch.setattr(
        sys, "argv", ["business_platform_deploy.py", str(source), "--out", str(out)]
    )
    with pytest.raises(SystemExit, match="failed"):
        deploy.main()


class FakeServer:
    def __init__(self):
        self.served = False
        self.closed = False

    def serve_forever(self):
        self.served = True

    def server_close(self):
        self.closed = True


def test_yaml_server_assembly_and_cli(config, tmp_path, monkeypatch):
    monkeypatch.setenv(config["deployment"]["client_secret_env"], "introspection")
    monkeypatch.setenv(config["deployment"]["tls_private_key_env"], "/tls/key")
    monkeypatch.setenv(config["record_maintenance"]["authorization_secret_env"], "s" * 32)
    captured = {}
    fake = FakeServer()

    def build(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return fake

    monkeypatch.setattr(server_launcher, "build_server", build)
    assert server_launcher.build_from_config(config) is fake
    assert captured["kwargs"]["platform"].authorization_secret == "s" * 32
    assert captured["kwargs"]["ingestion"].tenant == "sample-tenant"

    source = write_config(tmp_path, config)
    monkeypatch.setattr(sys, "argv", ["business_platform_server.py", str(source), "--enable"])
    server_launcher.main()
    assert fake.served and fake.closed


def test_yaml_server_refusals(config, tmp_path, monkeypatch):
    monkeypatch.delenv(config["deployment"]["client_secret_env"], raising=False)
    with pytest.raises(ValueError, match="required"):
        server_launcher.build_from_config(config)
    source = write_config(tmp_path, config)
    monkeypatch.setattr(sys, "argv", ["business_platform_server.py", str(source)])
    with pytest.raises(SystemExit, match="disabled"):
        server_launcher.main()
    monkeypatch.setattr(sys, "argv", ["business_platform_server.py", str(source), "--enable"])
    with pytest.raises(SystemExit, match="failed"):
        server_launcher.main()


def test_portal_assets_are_external_and_safe():
    markup = client_portal.html()
    styles = client_portal.css()
    script = client_portal.javascript()
    assert b'src="/portal.js"' in markup and b'href="/portal.css"' in markup
    assert b"<script>" not in markup and b"Authorization:'Bearer '" in script
    assert b"@media" in styles and b"canonical" not in script


def test_config_helper_error_paths(config, tmp_path):
    with pytest.raises(ValueError, match="object"):
        platform_config._object([], "x", set())
    with pytest.raises(ValueError, match="unsupported"):
        platform_config._object({"x": 1}, "x", set())
    with pytest.raises(ValueError, match="missing"):
        platform_config._object({}, "x", {"x"}, {"x"})
    for value in (None, "", "x" * 501):
        with pytest.raises(ValueError, match="non-empty"):
            platform_config._text(value, "x")
    for value in (False, 0, 2):
        with pytest.raises(ValueError, match="integer"):
            platform_config._positive(value, "x", 1)
    platform_config._secret_scan({"token": None, "nested": [{"token_env": "NAME"}]})
    with pytest.raises(ValueError, match="secret material"):
        platform_config._secret_scan({"nested": [{"token": "literal"}]})
    with pytest.raises(ValueError, match="non-empty"):
        platform_config._mapping({}, "x")
    with pytest.raises(ValueError, match="environment"):
        platform_config._environment_name("x-y", "x")
    with pytest.raises(ValueError, match="absolute"):
        platform_config._absolute_path("/a/../b", "x")
    for value in ("http://example.test", "https://example.test/path", "https://u:p@example.test"):
        with pytest.raises(ValueError, match="HTTPS origin"):
            platform_config._https_origin(value, "x")
    candidate = copy.deepcopy(config)
    candidate["deployment"].pop("intake_dir")
    validate(candidate)
    for field in ("authorization_server", "introspection_endpoint"):
        candidate = copy.deepcopy(config)
        candidate["deployment"][field] = "https://user:pass@example.test/#bad"
        with pytest.raises(ValueError, match="HTTPS URL"):
            validate(candidate)
    candidate = copy.deepcopy(config)
    candidate["deployment"]["export_ttl_seconds"] = 59
    with pytest.raises(ValueError, match="at least 60"):
        validate(candidate)
    candidate = copy.deepcopy(config)
    candidate["record_maintenance"]["adapter"] = {
        "kind": "http_json",
        "base_url": "https://target.example.test",
        "credential_env": "TARGET_TOKEN",
    }
    validate(candidate)
    candidate["record_maintenance"]["adapter"]["base_url"] = "http://target.example.test"
    with pytest.raises(ValueError, match="HTTP adapter"):
        validate(candidate)
    candidate = copy.deepcopy(config)
    candidate["record_maintenance"]["adapter"]["headers"] = {"Authorization": "Bearer no"}
    with pytest.raises(ValueError, match="Authorization credentials"):
        validate(candidate)
    broken = tmp_path / "broken.yaml"
    broken.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="invalid YAML"):
        load(broken)


def test_platform_tool_contract_and_refusals(config, tmp_path):
    assert {item["name"] for item in tool_definitions(config, exports=True)} >= {
        "create_business_analytics_export"
    }
    disabled = copy.deepcopy(config)
    disabled["record_maintenance"]["enabled"] = False
    disabled["analytics"]["enabled"] = False
    disabled["deployment"]["change_dir"] = str(tmp_path / "disabled-changes")
    assert [item["name"] for item in tool_definitions(disabled)] == [
        "get_business_platform_capabilities"
    ]
    with pytest.raises(ValueError, match="snapshot"):
        PlatformService(config, tmp_path / "other.sqlite")
    service = PlatformService(config, config["deployment"]["snapshot"])
    with pytest.raises(ValueError, match="Unknown platform"):
        service.invoke("owner", "not-real", {})
    with pytest.raises(ValueError, match="requires exactly"):
        service.invoke("owner", "get_business_platform_capabilities", {"extra": True})
    with pytest.raises(ValueError, match="unavailable"):
        service.invoke(
            "owner",
            "create_business_analytics_export",
            {"request": {"report": "sales_by_customer"}, "format": "csv"},
        )
    jobs = remote.ExportJobs(
        config["deployment"]["snapshot"], tmp_path / "export-jobs", "https://crm.example.com"
    )
    exported = PlatformService(config, config["deployment"]["snapshot"], export_jobs=jobs)
    with pytest.raises(ValueError, match="exactly one"):
        exported.invoke(
            "owner",
            "create_business_analytics_export",
            {"request": {"report": "x", "plan": {}}, "format": "csv"},
        )
    for operation in (
        "get_business_object_schema",
        "propose_business_record_change",
        "preview_business_record_change",
        "get_business_record_change",
    ):
        disabled_service = PlatformService(
            disabled,
            disabled["deployment"]["snapshot"],
        )
        with pytest.raises(ValueError, match="Record maintenance"):
            disabled_service.invoke("owner", operation, {})
    for operation in (
        "get_analytics_semantic_model",
        "query_business_analytics",
        "run_saved_business_report",
        "create_business_analytics_export",
    ):
        disabled_service = PlatformService(
            disabled,
            disabled["deployment"]["snapshot"],
        )
        with pytest.raises(ValueError, match="Analytics"):
            disabled_service.invoke("owner", operation, {})


def test_analytics_helper_and_contract_paths(config, tmp_path, monkeypatch):
    for function, value in ((analytics._object, []), (analytics._name, ""), (analytics._list, [])):
        with pytest.raises(ValueError):
            if function is analytics._object:
                function(value, "x", set())
            elif function is analytics._list:
                function(value, "x", 1)
            else:
                function(value, "x")
    for value in ({"extra": 1}, {}):
        with pytest.raises(ValueError):
            analytics._object(value, "x", set(), {"required"})
    with pytest.raises(ValueError, match="unique"):
        analytics._list(["x", "x"], "x", 2)
    for grain in ("day", "month", "quarter", "year", "fiscal_year", "fiscal_quarter"):
        assert analytics._date_bucket("2025-10-01", grain, 10)
    with pytest.raises(ValueError, match="non-ISO"):
        analytics._date_bucket("not-date", "day", 1)
    assert analytics._typed(None, "date") is None
    assert analytics._typed("2.0", "number") == 2
    assert analytics._typed("2026-01-01", "date").isoformat() == "2026-01-01"
    for value, kind, message in (("nan", "number", "non-finite"), ("bad", "date", "non-ISO")):
        with pytest.raises(ValueError, match=message):
            analytics._typed(value, kind)
    assert analytics._matches(None, {"operator": "is_null", "value": True})
    assert analytics._matches("x", {"operator": "not_null", "value": True})
    assert analytics._matches(None, None)
    assert analytics._matches("x", {"operator": "ne", "value": "y"})
    assert analytics._matches("x", {"operator": "not_in", "value": ["y"]})
    assert analytics._matches("Example", {"operator": "starts_with", "value": "ex"})
    assert analytics._matches("Example", {"operator": "ends_with", "value": "ple"})
    assert analytics._matches("2", {"operator": "gt", "value": "1"}, "number")
    assert analytics._matches("2", {"operator": "lt", "value": "3"}, "number")
    assert analytics._matches("2", {"operator": "between", "value": ["1", "3"]}, "number")
    with pytest.raises(ValueError, match="unsupported"):
        analytics._matches("x", {"operator": "between", "value": ["x"]})
    assert analytics._sort_value(None) == (1, "")
    assert analytics._sort_value("bad", True) == (1, "")
    assert analytics._aggregate([1, 2], "count") == ("2", [])
    assert analytics._aggregate(["a", "a", None], "count_distinct") == ("1", [])
    assert analytics._aggregate(["2", "4"], "average") == ("3", [])
    assert analytics._aggregate(["2", "4"], "minimum") == ("2", [])
    assert analytics._aggregate(["2", "4"], "maximum") == ("4", [])
    assert analytics._aggregate([], "sum") == (None, [])

    settings = copy.deepcopy(config["analytics"])
    mutations = [
        (lambda s: s["datasets"]["invoice_sales"].update(base_table="nope"), "base_table"),
        (lambda s: s["datasets"]["invoice_sales"].update(joins={}), "joins"),
        (
            lambda s: s["datasets"]["invoice_sales"]["joins"][0].update(table="nope"),
            "unsupported table",
        ),
        (
            lambda s: s["datasets"]["invoice_sales"]["joins"][0].update(local_field="nope"),
            "join field",
        ),
        (
            lambda s: s["datasets"]["invoice_sales"]["joins"][0].update(fields={}),
            "fields",
        ),
        (
            lambda s: s["datasets"]["invoice_sales"]["joins"][0].update(
                fields={"invoice_key": "party_key"}
            ),
            "projected",
        ),
        (lambda s: s["datasets"]["invoice_sales"].update(dimensions={}), "dimensions"),
        (lambda s: s["datasets"]["invoice_sales"].update(metrics={}), "metrics"),
        (
            lambda s: s["datasets"]["invoice_sales"]["dimensions"]["customer"].update(type="bad"),
            "dimension",
        ),
        (
            lambda s: s["datasets"]["invoice_sales"]["metrics"]["sales"].update(aggregation="bad"),
            "metric",
        ),
        (
            lambda s: s["datasets"]["invoice_sales"]["metrics"]["sales"].update(
                currency_dimension="bad"
            ),
            "currency",
        ),
    ]
    for mutate, message in mutations:
        candidate = copy.deepcopy(settings)
        mutate(candidate)
        with pytest.raises(ValueError, match=message):
            analytics.validate_semantic_model(candidate)
    for plan in (
        {
            "dataset": "invoice_sales",
            "dimensions": ["customer"],
            "metrics": ["invoice_count"],
            "date_grains": {"customer": "month"},
        },
        {"dataset": "invoice_sales", "metrics": ["invoice_count"], "include_totals": "yes"},
        {"dataset": "invoice_sales", "metrics": ["invoice_count"], "having": {"sales": 1}},
        {"dataset": "invoice_sales", "metrics": ["invoice_count"], "filters": []},
        {"dataset": "invoice_sales", "metrics": ["invoice_count"], "sort": ["-invoice_count"]},
    ):
        if plan.get("sort"):
            continue
        with pytest.raises(ValueError):
            analytics.validate_plan(settings, plan)
    with pytest.raises(ValueError, match="date_grains"):
        analytics.validate_plan(
            settings,
            {"dataset": "invoice_sales", "metrics": ["invoice_count"], "date_grains": []},
        )
    with pytest.raises(ValueError, match="non-numeric"):
        analytics._typed("not-a-number", "number")
    model = analytics.semantic_model(config)
    assert model["query_language"]["aggregates"] == sorted(analytics.AGGREGATES)
    assert model["arbitrary_sql_permitted"] is False
    original_records = analytics._records
    monkeypatch.setattr(analytics, "_records", lambda connection, table: [{}, {}])
    with analytics._connect(config["deployment"]["snapshot"]) as connection:
        with pytest.raises(ValueError, match="input-row"):
            analytics._joined_rows(connection, settings["datasets"]["invoice_sales"], 1)
    monkeypatch.setattr(analytics, "_records", original_records)

    def limited_records(_connection, table):
        return [{"invoice_key": "one"}] if table == "invoice_header" else [{}, {}]

    monkeypatch.setattr(analytics, "_records", limited_records)
    with analytics._connect(config["deployment"]["snapshot"]) as connection:
        with pytest.raises(ValueError, match="join exceeds"):
            analytics._joined_rows(connection, settings["datasets"]["invoice_sales"], 1)
    monkeypatch.setattr(analytics, "_records", original_records)
    assert analytics._aggregate(["NaN"], "sum") == (None, [0])
    empty = analytics.query(
        config["deployment"]["snapshot"],
        config,
        {
            "dataset": "invoice_sales",
            "metrics": ["sales"],
            "filters": {"region": "not-a-region"},
            "include_totals": True,
        },
    )
    assert empty["totals"]["rows"][0]["source_rows"] == 0
    bad_export = canonical_export()
    bad_export["tables"]["invoice_header"][0]["total_amount"] = "bad"
    bad_database = tmp_path / "bad-totals.sqlite"
    retrieval_store.build_database(bad_export, bad_database)
    assert (
        analytics.query(
            bad_database,
            config,
            {"dataset": "invoice_sales", "metrics": ["sales"], "include_totals": True},
        )["coverage"]["exception_groups"]
        == 2
    )
    no_rows = analytics.query(
        config["deployment"]["snapshot"],
        config,
        {"dataset": "invoice_sales", "metrics": ["invoice_count"], "filters": {"region": "no"}},
    )
    assert no_rows["rows"] == []
    with pytest.raises(ValueError, match="Unknown saved"):
        analytics.run_saved(config["deployment"]["snapshot"], config, "missing")


def test_record_change_contract_and_journal_error_paths(config, tmp_path, monkeypatch):
    for value in ({"x": float("nan")}, {"x": {1, 2}}):
        with pytest.raises(ValueError, match="finite JSON"):
            changes._json(value)
    for value in (None, "", "x" * 1001):
        with pytest.raises(ValueError, match="non-empty"):
            changes._text(value, "x")
    with pytest.raises(ValueError, match="complete"):
        changes._object({}, "x", set(), {"x"})
    with pytest.raises(ValueError, match="unsupported"):
        changes._object({"x": 1}, "x", set())
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    assert changes._private_directory(private, "private") == private
    link = tmp_path / "link"
    link.symlink_to(private)
    with pytest.raises(ValueError, match="symlink"):
        changes._private_directory(link, "private")
    exposed = tmp_path / "exposed"
    exposed.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="private"):
        changes._private_directory(exposed, "private")

    catalog_mutations = [
        (lambda c: c["record_maintenance"]["objects"].update({"": {}}), "non-empty"),
        (
            lambda c: c["record_maintenance"]["objects"]["account"].update(canonical_table="nope"),
            "canonical table",
        ),
        (
            lambda c: c["record_maintenance"]["objects"]["account"].update(writable_fields=[]),
            "writable_fields",
        ),
        (
            lambda c: c["record_maintenance"]["objects"]["account"].update(field_mapping={}),
            "must map",
        ),
        (
            lambda c: c["record_maintenance"]["objects"]["account"].update(
                writable_fields=["nope"]
            ),
            "unsupported fields",
        ),
        (
            lambda c: c["record_maintenance"]["objects"]["account"].update(
                field_mapping={"party_key": ""}
            ),
            "target fields",
        ),
        (
            lambda c: c["record_maintenance"]["objects"]["account"].update(target_object=""),
            "target_object",
        ),
        (
            lambda c: c["record_maintenance"]["objects"]["account"].update(target_endpoint="bad"),
            "target_endpoint",
        ),
    ]
    for mutate, message in catalog_mutations:
        candidate = copy.deepcopy(config)
        mutate(candidate)
        with pytest.raises(ValueError, match=message):
            changes.object_catalog(candidate)

    invalid_requests = [
        ({}, "complete"),
        ({**request("create"), "schema_version": "bad"}, "Unsupported"),
        ({**request("create"), "object": "bad"}, "business object"),
        ({**request("create"), "assertion_type": "bad"}, "assertion_type"),
        ({**request("create"), "expected_record_sha256": "x"}, "create"),
        ({**request("amend"), "expected_record_sha256": "x"}, "amend"),
        ({**request("create"), "values": {}, "clear_fields": []}, "distinct"),
        (
            {**request("create"), "values": {"canonical_name": {}}, "clear_fields": []},
            "scalars",
        ),
        (
            {
                **request("create"),
                "assertion_type": "source_backed",
                "source_references": [],
            },
            "source reference",
        ),
    ]
    for candidate, message in invalid_requests:
        with pytest.raises(ValueError, match=message):
            changes.validate_request(config, candidate)
    sourced = request("create")
    sourced["assertion_type"] = "source_backed"
    sourced["source_references"] = [
        {"document_id": "doc", "page": "1", "field": "name", "evidence": "quoted"}
    ]
    assert changes.validate_request(config, sourced) == sourced

    store = changes.ChangeStore(
        config["deployment"]["change_dir"], "sample-tenant", fingerprint(config)
    )
    exposed_database = tmp_path / "exposed-database"
    exposed_database.mkdir(mode=0o700)
    database_path = exposed_database / "changes.sqlite"
    database_path.touch()
    database_path.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        changes.ChangeStore(exposed_database, "tenant", "a" * 64)
    with pytest.raises(ValueError, match="mismatch"):
        changes.ChangeStore(config["deployment"]["change_dir"], "other-tenant", fingerprint(config))
    receipt = store.propose("owner", "journal", config, request("create"))
    with pytest.raises(ValueError, match="lifecycle"):
        store.record_lifecycle("owner", "nope", "x", receipt["change_id"], {})
    with pytest.raises(ValueError, match="bind"):
        store.record_lifecycle("owner", "authorization", "x", receipt["change_id"], {})
    with pytest.raises(ValueError, match="lifecycle"):
        store.lifecycle_artifact("owner", receipt["change_id"], "nope", "x")
    with pytest.raises(ValueError, match="not found"):
        store.lifecycle_artifact("owner", receipt["change_id"], "authorization", "x")
    assert store.application_for_authorization("owner", receipt["change_id"], "x") is None
    with closing(store._connect()) as connection, connection:
        connection.execute("DROP TRIGGER changes_no_delete")
        connection.execute("DROP TRIGGER changes_no_update")
        connection.execute("UPDATE events SET integrity='bad'")
    with pytest.raises(ValueError, match="integrity"):
        store.history("owner", receipt["change_id"])


def test_preview_authorization_and_adapter_failure_paths(config, tmp_path, monkeypatch):
    store = changes.ChangeStore(
        config["deployment"]["change_dir"], "sample-tenant", fingerprint(config)
    )
    create_existing = store.propose(
        "owner", "collision", config, {**request("create"), "record_key": "customer-1"}
    )
    with pytest.raises(ValueError, match="conflicts"):
        store.preview(
            "owner", create_existing["change_id"], config["deployment"]["snapshot"], config
        )
    missing = store.propose(
        "owner",
        "missing",
        config,
        {**request("amend"), "record_key": "does-not-exist", "expected_record_sha256": "0" * 64},
    )
    with pytest.raises(ValueError, match="requires an existing"):
        store.preview("owner", missing["change_id"], config["deployment"]["snapshot"], config)
    prior = __import__("crm_service").get_record(
        config["deployment"]["snapshot"], "party", "customer-1"
    )["record"]
    retained = store.propose(
        "owner", "good", config, request(expected=changes.stable_checksum(prior))
    )
    preview = store.preview(
        "owner", retained["change_id"], config["deployment"]["snapshot"], config
    )
    preview["after"]["tax_id"] = None
    for kwargs, message in (
        ({"expires_at": "bad"}, "offset-bearing"),
        ({"expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}, "future"),
        ({"expires_at": (datetime.now(UTC) + timedelta(days=9)).isoformat()}, "TTL"),
        (
            {"expires_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(), "secret": "x"},
            "32",
        ),
    ):
        with pytest.raises(ValueError, match=message):
            changes.authorize(
                preview,
                "owner",
                "approver",
                kwargs["expires_at"],
                kwargs.get("secret", "s" * 32),
            )
    authorization = changes.authorize(
        preview,
        "owner",
        "approver",
        (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "s" * 32,
    )
    for candidate, message in (
        ({}, "signature"),
        ({**authorization, "signature": "bad"}, "signature"),
        ({**authorization, "decision": "bad"}, "signature"),
    ):
        with pytest.raises(ValueError, match=message):
            changes.verify_authorization(candidate, preview, "s" * 32)
    expired = changes.authorize(
        preview,
        "owner",
        "approver",
        (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
        "s" * 32,
    )
    expired["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    expired["signature"] = (
        __import__("hmac")
        .new(
            ("s" * 32).encode(),
            changes._json(
                {key: value for key, value in expired.items() if key != "signature"}
            ).encode(),
            __import__("hashlib").sha256,
        )
        .hexdigest()
    )
    with pytest.raises(ValueError, match="expired"):
        changes.verify_authorization(expired, preview, "s" * 32)

    http_config = copy.deepcopy(config)
    http_config["record_maintenance"]["adapter"] = {
        "kind": "http_json",
        "base_url": "https://target.example.test",
        "credential_env": "TARGET_TOKEN",
        "response_id_field": "id",
    }
    with pytest.raises(ValueError, match="credential"):
        changes.apply_change(http_config, preview, authorization, "s" * 32, "http", tmp_path)
    monkeypatch.setenv("TARGET_TOKEN", "token")
    with pytest.raises(ValueError, match="failed"):
        changes.apply_change(
            http_config,
            preview,
            authorization,
            "s" * 32,
            "http-failed",
            tmp_path,
            opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("down")),
        )
    monkeypatch.setattr(changes, "MAX_RESPONSE_BYTES", 2)
    with pytest.raises(ValueError, match="byte limit"):
        changes.apply_change(
            http_config,
            preview,
            authorization,
            "s" * 32,
            "http-large",
            tmp_path,
            opener=lambda *_args, **_kwargs: Response(b"{}x"),
        )
    monkeypatch.setattr(changes, "MAX_RESPONSE_BYTES", 1_000_000)
    with pytest.raises(ValueError, match="invalid JSON"):
        changes.apply_change(
            http_config,
            preview,
            authorization,
            "s" * 32,
            "http-json",
            tmp_path,
            opener=lambda *_args, **_kwargs: Response(b"bad"),
        )
    application = changes.apply_change(
        http_config,
        preview,
        authorization,
        "s" * 32,
        "http-list",
        tmp_path,
        opener=lambda *_args, **_kwargs: Response(b"[]"),
    )
    assert application["external_id"] is None
    with pytest.raises(ValueError, match="external ID"):
        changes.reconcile(http_config, preview, application, tmp_path)


def test_remaining_change_reconcile_and_cli_paths(config, tmp_path, monkeypatch, capsys):
    oversized = request("create")
    oversized["values"] = ["not-an-object"]
    with pytest.raises(ValueError, match="bounds"):
        changes.validate_request(config, oversized)
    refs = request("create")
    refs["source_references"] = {}
    with pytest.raises(ValueError, match="source_references"):
        changes.validate_request(config, refs)
    monkeypatch.setattr(changes, "MAX_REQUEST_BYTES", 1)
    with pytest.raises(ValueError, match="byte limit"):
        changes.validate_request(config, request("create"))
    monkeypatch.setattr(changes, "MAX_REQUEST_BYTES", 1_000_000)

    store = changes.ChangeStore(
        config["deployment"]["change_dir"], "sample-tenant", fingerprint(config)
    )
    prior = __import__("crm_service").get_record(
        config["deployment"]["snapshot"], "party", "customer-1"
    )["record"]
    retained = store.propose(
        "owner",
        "remaining",
        config,
        {
            **request(expected=changes.stable_checksum(prior)),
            "clear_fields": ["tax_id"],
        },
    )
    preview = store.preview(
        "owner", retained["change_id"], config["deployment"]["snapshot"], config
    )
    assert preview["after"]["tax_id"] is None
    original_get = changes.get_record
    monkeypatch.setattr(
        changes, "get_record", lambda *_args: (_ for _ in ()).throw(ValueError("database bad"))
    )
    with pytest.raises(ValueError, match="database bad"):
        store.preview("owner", retained["change_id"], config["deployment"]["snapshot"], config)
    monkeypatch.setattr(changes, "get_record", original_get)
    authorization = changes.authorize(
        preview,
        "owner",
        "approver",
        (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "s" * 32,
    )
    retained_auth = store.record_lifecycle(
        "owner", "authorization", "remaining-auth", retained["change_id"], authorization
    )
    with pytest.raises(ValueError, match="not found"):
        store.lifecycle_artifact("owner", retained["change_id"], "authorization", "0" * 64)
    assert (
        store.lifecycle_artifact(
            "owner", retained["change_id"], "authorization", retained_auth["artifact_sha256"]
        )[0]["approver"]
        == "approver"
    )
    other_auth = changes.authorize(
        preview,
        "owner",
        "other-approver",
        (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "s" * 32,
    )
    other_receipt = store.record_lifecycle(
        "owner", "authorization", "other-auth", retained["change_id"], other_auth
    )
    assert (
        store.lifecycle_artifact(
            "owner", retained["change_id"], "authorization", other_receipt["artifact_sha256"]
        )[0]["approver"]
        == "other-approver"
    )
    tampered = {**authorization, "configuration_sha256": "wrong"}
    tampered["signature"] = (
        __import__("hmac")
        .new(
            ("s" * 32).encode(),
            changes._json(
                {key: value for key, value in tampered.items() if key != "signature"}
            ).encode(),
            __import__("hashlib").sha256,
        )
        .hexdigest()
    )
    with pytest.raises(ValueError, match="does not bind"):
        changes.verify_authorization(tampered, preview, "s" * 32)
    missing_expiry = {key: value for key, value in authorization.items() if key != "expires_at"}
    missing_expiry["signature"] = (
        __import__("hmac")
        .new(
            ("s" * 32).encode(),
            changes._json(
                {key: value for key, value in missing_expiry.items() if key != "signature"}
            ).encode(),
            __import__("hashlib").sha256,
        )
        .hexdigest()
    )
    with pytest.raises(ValueError, match="expiry"):
        changes.verify_authorization(missing_expiry, preview, "s" * 32)
    with pytest.raises(ValueError, match="32"):
        changes.verify_authorization(authorization, preview, "short")

    application = changes.apply_change(
        config,
        preview,
        authorization,
        "s" * 32,
        "file-remaining",
        config["record_maintenance"]["adapter"]["output_directory"],
    )
    store.record_lifecycle(
        "owner", "application", "remaining-application", retained["change_id"], application
    )
    assert store.application_for_authorization("owner", retained["change_id"], "different") is None
    target = (
        Path(config["record_maintenance"]["adapter"]["output_directory"])
        / f"{preview['change_id']}.json"
    )
    invalid_application = {**application, "change_id": "other"}
    with pytest.raises(ValueError, match="does not bind"):
        changes.reconcile(config, preview, invalid_application, target.parent)
    target.unlink()
    with pytest.raises(ValueError, match="unavailable"):
        changes.reconcile(config, preview, application, target.parent)
    target.write_text("not-json")
    with pytest.raises(ValueError, match="invalid"):
        changes.reconcile(config, preview, application, target.parent)
    target.write_text("{}")
    assert (
        changes.reconcile(config, preview, application, target.parent)["status"]
        == "blocked_mismatch"
    )

    http_config = copy.deepcopy(config)
    http_config["record_maintenance"]["adapter"] = {
        "kind": "http_json",
        "base_url": "https://target.example.test",
        "credential_env": "TARGET_TOKEN",
    }
    remote_application = {
        "schema_version": "business_record_change_application_v1",
        "change_id": preview["change_id"],
        "preview_sha256": preview["preview_sha256"],
        "authorization_sha256": changes.stable_checksum(authorization),
        "approved_by": "approver",
        "transport": "http_json",
        "external_id": "id / 1",
        "response_sha256": "x",
        "reconciliation_status": "pending",
        "canonical_snapshot_changed": False,
    }
    remote_application["receipt_sha256"] = changes.stable_checksum(remote_application)
    monkeypatch.delenv("TARGET_TOKEN", raising=False)
    with pytest.raises(ValueError, match="credential"):
        changes.reconcile(http_config, preview, remote_application, tmp_path)
    monkeypatch.setenv("TARGET_TOKEN", "token")
    with pytest.raises(ValueError, match="request failed"):
        changes.reconcile(
            http_config,
            preview,
            remote_application,
            tmp_path,
            opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("down")),
        )
    monkeypatch.setattr(changes, "MAX_RESPONSE_BYTES", 2)
    with pytest.raises(ValueError, match="byte limit"):
        changes.reconcile(
            http_config,
            preview,
            remote_application,
            tmp_path,
            opener=lambda *_args, **_kwargs: Response(b"{}x"),
        )
    monkeypatch.setattr(changes, "MAX_RESPONSE_BYTES", 1_000_000)
    with pytest.raises(ValueError, match="invalid JSON"):
        changes.reconcile(
            http_config,
            preview,
            remote_application,
            tmp_path,
            opener=lambda *_args, **_kwargs: Response(b"bad"),
        )
    assert (
        changes.reconcile(
            http_config,
            preview,
            remote_application,
            tmp_path,
            opener=lambda *_args, **_kwargs: Response(b"[]"),
        )["status"]
        == "blocked_mismatch"
    )
    expected_payload = json.dumps(preview["target_payload"]).encode()
    assert (
        changes.reconcile(
            http_config,
            preview,
            remote_application,
            tmp_path,
            opener=lambda *_args, **_kwargs: Response(expected_payload),
        )["status"]
        == "reconciled"
    )

    config_path = write_config(tmp_path, config, "cli.yaml")
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request("create")))
    monkeypatch.setenv(config["record_maintenance"]["authorization_secret_env"], "s" * 32)
    monkeypatch.setattr(
        sys, "argv", ["business_record_changes.py", "--config", str(config_path), "schema"]
    )
    changes.main()
    assert json.loads(capsys.readouterr().out)["schema_version"] == changes.VERSION
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "business_record_changes.py",
            "--config",
            str(config_path),
            "propose",
            "--owner",
            "cli-owner",
            "--request-key",
            "cli-proposal",
            "--request",
            str(request_path),
        ],
    )
    changes.main()
    proposal = json.loads(capsys.readouterr().out)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "business_record_changes.py",
            "--config",
            str(config_path),
            "preview",
            "--owner",
            "cli-owner",
            "--change-id",
            proposal["change_id"],
        ],
    )
    changes.main()
    assert json.loads(capsys.readouterr().out)["change_id"] == proposal["change_id"]
    auth_path = tmp_path / "authorization.json"
    expires = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "business_record_changes.py",
            "--config",
            str(config_path),
            "authorize",
            "--owner",
            "cli-owner",
            "--change-id",
            proposal["change_id"],
            "--approver",
            "cli-approver",
            "--expires-at",
            expires,
            "--request-key",
            "cli-auth",
            "--out",
            str(auth_path),
        ],
    )
    changes.main()
    application_path = tmp_path / "application.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "business_record_changes.py",
            "--config",
            str(config_path),
            "apply",
            "--owner",
            "cli-owner",
            "--change-id",
            proposal["change_id"],
            "--authorization",
            str(auth_path),
            "--request-key",
            "cli-apply",
            "--out",
            str(application_path),
        ],
    )
    changes.main()
    reconciliation_path = tmp_path / "reconciliation.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "business_record_changes.py",
            "--config",
            str(config_path),
            "reconcile",
            "--owner",
            "cli-owner",
            "--change-id",
            proposal["change_id"],
            "--application",
            str(application_path),
            "--request-key",
            "cli-reconcile",
            "--out",
            str(reconciliation_path),
        ],
    )
    changes.main()
    assert json.loads(reconciliation_path.read_text())["status"] == "reconciled"
    with pytest.raises(FileExistsError):
        changes._write_new(reconciliation_path, {})

    http_cli = copy.deepcopy(config)
    http_cli["deployment"]["change_dir"] = str(tmp_path / "http-cli-changes")
    http_cli["record_maintenance"]["adapter"] = {
        "kind": "http_json",
        "base_url": "https://target.example.test",
        "credential_env": "TARGET_TOKEN",
    }
    http_store = changes.ChangeStore(
        http_cli["deployment"]["change_dir"], "sample-tenant", fingerprint(http_cli)
    )
    http_proposal = http_store.propose("http-owner", "http-cli", http_cli, request("create"))
    http_preview = http_store.preview(
        "http-owner", http_proposal["change_id"], http_cli["deployment"]["snapshot"], http_cli
    )
    http_auth = changes.authorize(
        http_preview,
        "http-owner",
        "http-approver",
        (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "s" * 32,
    )
    http_config_path = write_config(tmp_path, http_cli, "http-cli.yaml")
    http_auth_path = tmp_path / "http-authorization.json"
    http_auth_path.write_text(json.dumps(http_auth))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "business_record_changes.py",
            "--config",
            str(http_config_path),
            "apply",
            "--owner",
            "http-owner",
            "--change-id",
            http_proposal["change_id"],
            "--authorization",
            str(http_auth_path),
            "--request-key",
            "http-apply",
            "--out",
            str(tmp_path / "http-application.json"),
        ],
    )
    with pytest.raises(ValueError, match="--execute"):
        changes.main()
