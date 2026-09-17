"""Tests for the OAuth-protected remote Streamable HTTP MCP boundary."""

import http.client
import importlib
import json
import sys
import threading
import time
import urllib.error
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

remote = importlib.import_module("retrieval_remote_mcp")
store = importlib.import_module("retrieval_store")
canonical = importlib.import_module("canonical_load")
RESOURCE_ORIGIN = "https://crm.example.com"
RESOURCE = f"{RESOURCE_ORIGIN}/mcp"
PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
)


def approved_export():
    return {
        "batch_id": "remote-batch",
        "tables": {
            "document": [
                {
                    "document_id": "doc-1",
                    "source_file": "source.pdf",
                    "source_page_range": "1",
                    "source_sha256": "a" * 64,
                    "batch_id": "remote-batch",
                    "review_status": "exception_resolved",
                }
            ],
            "party": [
                {
                    "party_key": "party-1",
                    "canonical_name": "Example Company",
                    "batch_id": "remote-batch",
                    "source_document_id": "doc-1",
                    "review_status": "exception_resolved",
                }
            ],
        },
    }


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def package_sources(tmp_path):
    export = approved_export()
    return (
        write(tmp_path / "canonical-export.json", export),
        write(tmp_path / "canonical-plan.json", canonical.build_plan(export)),
    )


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "retrieval.sqlite"
    store.build_database(approved_export(), path)
    return path


class Response:
    def __init__(self, payload, declared=None):
        self.payload = payload
        self.headers = {}
        if declared is not None:
            self.headers["Content-Length"] = str(declared)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size):
        return self.payload[:size]


def claims(**updates):
    value = {
        "active": True,
        "exp": time.time() + 300,
        "aud": RESOURCE,
        "sub": "user-1",
        "tenant_id": "tenant-1",
        "roles": ["analyst"],
        "scope": "crm:read crm:export",
    }
    value.update(updates)
    return value


def introspector():
    return remote.OAuthIntrospector(
        "https://id.example.com/introspect",
        "client",
        "secret",
        RESOURCE,
        "tenant_id",
        "tenant-1",
        "roles",
        {"analyst"},
    )


def test_oauth_introspection_success_and_request_shape(monkeypatch):
    captured = []

    def urlopen(request, timeout):
        captured.append((request, timeout))
        payload = json.dumps(claims()).encode()
        return Response(payload, len(payload))

    monkeypatch.setattr(remote.urllib.request, "urlopen", urlopen)
    principal = introspector().authenticate({"Authorization": "Bearer token-value"})
    assert principal.subject == "user-1"
    assert principal.audit_subject == remote.hashlib.sha256(b"user-1").hexdigest()
    assert principal.scopes == frozenset({"crm:read", "crm:export"})
    request, timeout = captured[0]
    assert request.full_url == "https://id.example.com/introspect" and timeout == 10.0
    assert request.data == b"token=token-value"
    assert request.headers["Authorization"].startswith("Basic ")


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        ({"active": False}, "inactive"),
        (claims(exp=True), "expiry"),
        (claims(exp=0), "expiry"),
        (claims(aud="https://other.example.com"), "audience"),
        (claims(sub=""), "subject"),
        (claims(tenant_id="other"), "tenant"),
        (claims(roles=["guest"]), "role"),
    ),
)
def test_oauth_rejects_invalid_claims(monkeypatch, payload, message):
    # Parametrized values are constructed during collection. The full quality
    # suite can exceed the five-minute token lifetime before this module runs,
    # so refresh only fixtures whose expiry is meant to remain valid.
    if payload.get("active") is True and isinstance(payload.get("exp"), float):
        payload = {**payload, "exp": time.time() + 300}
    monkeypatch.setattr(
        remote.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: Response(json.dumps(payload).encode()),
    )
    with pytest.raises(PermissionError, match=message):
        introspector().authenticate({"Authorization": "Bearer token"})


def test_oauth_rejects_malformed_or_failed_introspection(monkeypatch):
    auth = introspector()
    with pytest.raises(PermissionError, match="malformed"):
        auth.authenticate({})
    monkeypatch.setattr(
        remote.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(urllib.error.URLError("down")),
    )
    with pytest.raises(PermissionError, match="failed"):
        auth.authenticate({"Authorization": "Bearer token"})
    monkeypatch.setattr(remote.urllib.request, "urlopen", lambda *_a, **_k: Response(b"{"))
    with pytest.raises(PermissionError, match="invalid JSON"):
        auth.authenticate({"Authorization": "Bearer token"})
    monkeypatch.setattr(
        remote.urllib.request,
        "urlopen",
        lambda *_a, **_k: Response(b"{}", remote.MAX_INTROSPECTION_BYTES + 1),
    )
    with pytest.raises(PermissionError, match="too large"):
        auth.authenticate({"Authorization": "Bearer token"})
    monkeypatch.setattr(
        remote.urllib.request,
        "urlopen",
        lambda *_a, **_k: Response(b"{}", "invalid"),
    )
    with pytest.raises(PermissionError, match="content length"):
        auth.authenticate({"Authorization": "Bearer token"})
    monkeypatch.setattr(
        remote.urllib.request,
        "urlopen",
        lambda *_a, **_k: Response(b"x" * (remote.MAX_INTROSPECTION_BYTES + 1)),
    )
    with pytest.raises(PermissionError, match="too large"):
        auth.authenticate({"Authorization": "Bearer token"})


@pytest.mark.parametrize(
    "args",
    (
        (
            "http://id.example.com/introspect",
            "client",
            "secret",
            RESOURCE,
            "tenant_id",
            "tenant",
            "roles",
            {"role"},
        ),
        (
            "https://id.example.com/introspect",
            "",
            "secret",
            RESOURCE,
            "tenant_id",
            "tenant",
            "roles",
            {"role"},
        ),
        (
            "https://id.example.com/introspect",
            "client",
            "secret",
            "http://crm.example.com",
            "tenant_id",
            "tenant",
            "roles",
            {"role"},
        ),
        (
            "https://id.example.com/introspect",
            "client",
            "secret",
            RESOURCE,
            "",
            "tenant",
            "roles",
            {"role"},
        ),
    ),
)
def test_oauth_configuration_fails_closed(args):
    with pytest.raises(ValueError):
        remote.OAuthIntrospector(*args)


def test_helpers_rate_limit_audit_and_metadata(tmp_path):
    assert remote._split("a,b c") == {"a", "b", "c"}
    assert remote._claim_values(["a", "b"]) == {"a", "b"}
    assert remote._claim_values([1]) == set()
    assert remote.required_scope({"params": {"name": "export_crm_table"}}) == "crm:export"
    assert remote.required_scope([]) == "crm:read"
    assert remote._origin_allowed("", frozenset())
    assert not remote._origin_allowed("https://bad", {"https://good"})
    metadata = remote.protected_resource_metadata(f"{RESOURCE}/", "https://id.example.com/")
    assert metadata["resource"] == RESOURCE
    assert metadata["resource_documentation"] == f"{RESOURCE_ORIGIN}/docs"
    legacy = rpc("tools/list")
    remote._validate_modern_headers({}, legacy)
    modern = rpc(
        "tools/call",
        {
            "name": "get_crm_capabilities",
            "arguments": {},
            "_meta": {"io.modelcontextprotocol/protocolVersion": remote.MODERN_PROTOCOL_VERSION},
        },
    )
    modern_headers = {
        "MCP-Protocol-Version": remote.MODERN_PROTOCOL_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "get_crm_capabilities",
    }
    remote._validate_modern_headers(modern_headers, modern)
    for headers, request_value, message in (
        ({"MCP-Protocol-Version": remote.MODERN_PROTOCOL_VERSION}, legacy, "params._meta"),
        ({**modern_headers, "Mcp-Method": "tools/list"}, modern, "Mcp-Method"),
        ({**modern_headers, "Mcp-Name": "other"}, modern, "Mcp-Name"),
    ):
        with pytest.raises(ValueError, match=message):
            remote._validate_modern_headers(headers, request_value)
    limiter = remote.RateLimiter(1)
    assert limiter.allow("client", now=1)
    assert not limiter.allow("client", now=2)
    assert limiter.allow("client", now=62)
    with pytest.raises(ValueError, match="positive"):
        remote.RateLimiter(0)
    log = remote.AuditLog(tmp_path / "logs" / "audit.jsonl")
    log.write({"ok": True})
    assert json.loads(log.path.read_text()) == {"ok": True}
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(ValueError, match="file"):
        remote.AuditLog(directory)


class FakeIntrospector:
    def __init__(self, error=None, scopes=("crm:read", "crm:export")):
        self.error = error
        self.scopes = scopes

    def authenticate(self, _headers):
        if self.error:
            raise PermissionError(self.error)
        return remote.Principal(
            "user-1", "tenant-1", frozenset({"analyst"}), frozenset(self.scopes)
        )


@contextmanager
def running_handler(
    database,
    tmp_path,
    *,
    auth=None,
    limit=100,
    origins=(),
    ingestion=None,
    canonical_export=None,
    canonical_load_plan=None,
    platform=None,
):
    audit = remote.AuditLog(tmp_path / f"audit-{time.time_ns()}.jsonl")
    exports = remote.ExportJobs(
        database,
        tmp_path / f"exports-{time.time_ns()}",
        RESOURCE_ORIGIN,
        canonical_export=canonical_export,
        canonical_load_plan=canonical_load_plan,
    )
    request_handler = remote.handler(
        database,
        auth or FakeIntrospector(),
        exports,
        RESOURCE,
        "https://id.example.com",
        audit,
        remote.RateLimiter(limit),
        10_000,
        frozenset(origins),
        ingestion=ingestion,
        platform=platform,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), request_handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, audit
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def request(server, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    raw = response.read()
    result = response.status, dict(response.headers), json.loads(raw) if raw else None
    connection.close()
    return result


def raw_request(server, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    result = response.status, dict(response.headers), response.read()
    connection.close()
    return result


def rpc(method, params=None, request_id=1):
    value = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        value["params"] = params
    return value


def test_remote_http_discovery_tool_call_notification_and_audit(database, tmp_path):
    with running_handler(database, tmp_path) as (server, audit):
        status, _, metadata = request(server, "GET", "/.well-known/oauth-protected-resource")
        assert status == 200 and metadata["scopes_supported"] == ["crm:read", "crm:export"]
        assert request(server, "GET", "/.well-known/oauth-protected-resource/mcp")[2] == metadata
        assert request(server, "GET", "/health")[2]["read_only"]
        assert request(server, "GET", "/docs")[2]["endpoint"].endswith("/mcp")
        assert request(server, "GET", "/mcp")[0] == 405
        assert request(server, "GET", "/missing")[0] == 404
        payload = json.dumps(rpc("tools/list"))
        status, headers, listed = request(
            server,
            "POST",
            "/mcp",
            payload,
            {"Content-Type": "application/json", "Authorization": "Bearer token"},
        )
        assert status == 200 and len(listed["result"]["tools"]) == 14
        create_tool = next(
            tool for tool in listed["result"]["tools"] if tool["name"] == "create_crm_export"
        )
        assert create_tool["annotations"] == {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        }
        assert headers["MCP-Protocol-Version"] == remote.LEGACY_PROTOCOL_VERSIONS[-1]
        modern_payload = json.dumps(
            rpc(
                "tools/list",
                {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": remote.MODERN_PROTOCOL_VERSION
                    }
                },
            )
        )
        modern_status, modern_headers, modern_listed = request(
            server,
            "POST",
            "/mcp",
            modern_payload,
            {
                "Content-Type": "application/json",
                "Authorization": "Bearer token",
                "MCP-Protocol-Version": remote.MODERN_PROTOCOL_VERSION,
                "Mcp-Method": "tools/list",
            },
        )
        assert modern_status == 200
        assert modern_headers["MCP-Protocol-Version"] == remote.MODERN_PROTOCOL_VERSION
        assert modern_listed["result"]["resultType"] == "complete"
        assert modern_listed["result"]["cacheScope"] == "private"
        assert modern_listed["result"]["ttlMs"] == 0
        call = json.dumps(rpc("tools/call", {"name": "get_crm_export_summary", "arguments": {}}))
        assert (
            request(
                server,
                "POST",
                "/mcp",
                call,
                {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer token",
                    "MCP-Protocol-Version": "2025-06-18",
                },
            )[1]["MCP-Protocol-Version"]
            == "2025-06-18"
        )
        create = json.dumps(
            rpc(
                "tools/call",
                {"name": "create_crm_export", "arguments": {"table": "party"}},
            )
        )
        created = request(
            server,
            "POST",
            "/mcp",
            create,
            {"Content-Type": "application/json", "Authorization": "Bearer token"},
        )[2]["result"]["structuredContent"]["result"]
        status_call = json.dumps(
            rpc(
                "tools/call",
                {
                    "name": "get_crm_export_job",
                    "arguments": {"job_id": created["job_id"]},
                },
            )
        )
        assert (
            request(
                server,
                "POST",
                "/mcp",
                status_call,
                {"Content-Type": "application/json", "Authorization": "Bearer token"},
            )[2]["result"]["structuredContent"]["result"]["status"]
            == "completed"
        )
        download_path = created["download_url"].removeprefix(RESOURCE_ORIGIN)
        download_status, download_headers, download = raw_request(server, "GET", download_path)
        assert download_status == 200 and download.startswith(b"PK")
        assert download_headers["X-Content-SHA256"] == created["file_sha256"]
        assert raw_request(server, "GET", f"/downloads/{created['job_id']}?token=bad")[0] == 403
        notification = json.dumps(rpc("notifications/initialized", request_id=None))
        assert (
            request(
                server,
                "POST",
                "/mcp",
                notification,
                {"Content-Type": "application/json", "Authorization": "Bearer token"},
            )[0]
            == 202
        )
        assert request(server, "POST", "/other", "{}")[0] == 404
        assert request(server, "DELETE", "/mcp")[0] == 405
        assert request(server, "PUT", "/mcp")[0] == 405
        assert request(server, "PATCH", "/mcp")[0] == 405
    events = [json.loads(line) for line in audit.path.read_text().splitlines()]
    assert events[0]["method"] == "tools/list" and events[0]["success"]
    assert any(event["tool"] == "get_crm_export_summary" for event in events)
    assert any(event["method"] == "downloads/get" and event["success"] for event in events)


def test_remote_review_ui_routes_appear_only_with_ingestion(database, tmp_path):
    store = importlib.import_module("visual_ingestion").IngestionStore(
        tmp_path / "intake", "tenant-1"
    )
    with running_handler(database, tmp_path, ingestion=store) as (server, _):
        status, headers, page = raw_request(server, "GET", "/ingestion/review")
        assert status == 200
        assert headers["Content-Type"] == "text/html; charset=utf-8"
        assert b"Visual Intake Upload and Review" in page
        status, headers, script = raw_request(server, "GET", "/ingestion/review.js")
        assert status == 200
        assert headers["Content-Type"] == "text/javascript; charset=utf-8"
        assert b"grant_ingestion_session_reviewer" in script
        docs = request(server, "GET", "/docs")[2]
        assert docs["ingestion_review_ui"] == "/ingestion/review"
    with running_handler(database, tmp_path) as (server, _):
        assert raw_request(server, "GET", "/ingestion/review")[0] == 404
        assert raw_request(server, "GET", "/ingestion/review.js")[0] == 404


def test_remote_package_export_api(database, tmp_path):
    export_path, plan_path = package_sources(tmp_path)
    with running_handler(
        database,
        tmp_path,
        canonical_export=export_path,
        canonical_load_plan=plan_path,
    ) as (server, _):
        created = request(
            server,
            "POST",
            "/api/crm-export/create",
            json.dumps({"package": "staging"}),
            {"Content-Type": "application/json", "Authorization": "Bearer token"},
        )[2]
        assert created["format"] == "zip"
        assert created["subject"]["kind"] == "package"
        assert created["package_summary"]["package_kind"] == "staging"
        status = request(
            server,
            "POST",
            "/api/crm-export/status",
            json.dumps({"job_id": created["job_id"]}),
            {"Content-Type": "application/json", "Authorization": "Bearer token"},
        )[2]
        assert status["status"] == "completed"


def test_remote_package_export_review_context_must_match_session(database, tmp_path):
    export_path, plan_path = package_sources(tmp_path)
    ingestion = importlib.import_module("visual_ingestion").IngestionStore(
        tmp_path / "intake", "tenant-1"
    )
    session = ingestion.create_session("user-1", "session", 1)
    ingestion.upload_page(
        "user-1",
        session["session_id"],
        "page-1",
        1,
        "image/png",
        PNG_1X1_BASE64,
    )
    with running_handler(
        database,
        tmp_path,
        ingestion=ingestion,
        canonical_export=export_path,
        canonical_load_plan=plan_path,
    ) as (server, _):
        status, _, body = request(
            server,
            "POST",
            "/api/crm-export/create",
            json.dumps(
                {
                    "package": "staging",
                    "review_context": {
                        "schema_version": "review_export_link_v1",
                        "source_kind": "visual_ingestion_session",
                        "session_id": session["session_id"],
                        "source_set_sha256": "b" * 64,
                    },
                }
            ),
            {"Content-Type": "application/json", "Authorization": "Bearer token"},
        )
        assert status == 400
        assert body["error"] == "review context does not match the current visual intake session"


def test_remote_package_export_review_context_can_match_session(database, tmp_path):
    export_path, plan_path = package_sources(tmp_path)
    ingestion = importlib.import_module("visual_ingestion").IngestionStore(
        tmp_path / "intake", "tenant-1"
    )
    session = ingestion.create_session("user-1", "session", 1)
    ingestion.upload_page(
        "user-1",
        session["session_id"],
        "page-1",
        1,
        "image/png",
        PNG_1X1_BASE64,
    )
    review_context = {
        "schema_version": "review_export_link_v1",
        "source_kind": "visual_ingestion_session",
        "session_id": session["session_id"],
        "source_set_sha256": ingestion.status("user-1", session["session_id"])["source_set_sha256"],
    }
    with running_handler(
        database,
        tmp_path,
        ingestion=ingestion,
        canonical_export=export_path,
        canonical_load_plan=plan_path,
    ) as (server, _):
        created = request(
            server,
            "POST",
            "/api/crm-export/create",
            json.dumps({"package": "staging", "review_context": review_context}),
            {"Content-Type": "application/json", "Authorization": "Bearer token"},
        )[2]
        assert created["format"] == "zip"
        assert created["subject"]["arguments"]["review_context"] == review_context


def test_remote_http_security_and_request_failures(database, tmp_path):
    with running_handler(database, tmp_path, origins={"https://allowed"}) as (server, _):
        base = {"Content-Type": "application/json", "Authorization": "Bearer token"}
        assert request(server, "POST", "/mcp", "{}", {**base, "Origin": "https://bad"})[0] == 403
        assert request(server, "GET", "/mcp", headers={"Origin": "https://bad"})[0] == 403
        assert request(server, "POST", "/mcp", "{}", {"Authorization": "Bearer token"})[0] == 415
        assert request(server, "POST", "/mcp", "{", base)[0] == 400
        assert (
            request(
                server,
                "POST",
                "/mcp",
                "{}",
                {**base, "MCP-Protocol-Version": "unsupported"},
            )[0]
            == 400
        )
        modern = json.dumps(
            rpc(
                "tools/list",
                {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": remote.MODERN_PROTOCOL_VERSION
                    }
                },
            )
        )
        assert (
            request(
                server,
                "POST",
                "/mcp",
                modern,
                {**base, "MCP-Protocol-Version": remote.MODERN_PROTOCOL_VERSION},
            )[0]
            == 400
        )
        assert request(server, "POST", "/mcp", "{}", {**base, "Content-Length": "nope"})[0] == 411
        assert request(server, "POST", "/mcp", None, {**base, "Content-Length": "0"})[0] == 413
        assert request(server, "POST", "/mcp", "{}", {**base, "Content-Length": "10001"})[0] == 413
        for name, arguments in (
            ("create_crm_export", []),
            ("create_crm_export", {"table": "party", "unknown": True}),
            ("create_crm_export", {"table": "missing"}),
            ("get_crm_export_job", {}),
        ):
            result = request(
                server,
                "POST",
                "/mcp",
                json.dumps(rpc("tools/call", {"name": name, "arguments": arguments})),
                base,
            )[2]
            assert result["error"]["code"] == -32602
            assert isinstance(result["error"]["message"], str)
    with running_handler(database, tmp_path, auth=FakeIntrospector("bad token")) as (server, _):
        status, headers, _ = request(
            server,
            "POST",
            "/mcp",
            "{}",
            {"Content-Type": "application/json", "Authorization": "Bearer token"},
        )
        assert status == 401 and "resource_metadata" in headers["WWW-Authenticate"]
    with running_handler(database, tmp_path, auth=FakeIntrospector(scopes=())) as (server, _):
        denied = request(
            server,
            "POST",
            "/mcp",
            json.dumps(rpc("tools/call", {"name": "create_crm_export", "arguments": {}})),
            {"Content-Type": "application/json", "Authorization": "Bearer token"},
        )
        assert denied[0] == 403 and 'scope="crm:export"' in denied[1]["WWW-Authenticate"]
    with running_handler(database, tmp_path, limit=1) as (server, _):
        headers = {"Content-Type": "application/json", "Authorization": "Bearer token"}
        assert request(server, "POST", "/mcp", "{}", headers)[0] == 200
        assert request(server, "POST", "/mcp", "{}", headers)[0] == 429
    with running_handler(database, tmp_path, limit=1) as (server, _):
        assert raw_request(server, "GET", "/downloads/not-a-job?token=x")[0] == 404
        assert raw_request(server, "GET", "/downloads/not-a-job?token=x")[0] == 429


def test_build_server_validation_and_main(monkeypatch, database, tmp_path):
    missing = tmp_path / "missing"
    audit = remote.AuditLog(tmp_path / "audit.jsonl")
    exports = remote.ExportJobs(database, tmp_path / "exports", RESOURCE_ORIGIN)
    with pytest.raises(ValueError, match="database"):
        remote.build_server(
            missing,
            "127.0.0.1",
            1,
            missing,
            missing,
            introspector(),
            exports,
            RESOURCE,
            "https://id.example.com",
            audit,
        )
    with pytest.raises(ValueError, match="certificate"):
        remote.build_server(
            database,
            "127.0.0.1",
            1,
            missing,
            missing,
            introspector(),
            exports,
            RESOURCE,
            "https://id.example.com",
            audit,
        )
    for port, size, message in ((0, 1, "port"), (True, 1, "port"), (1, 0, "request bytes")):
        with pytest.raises(ValueError, match=message):
            remote.build_server(
                database,
                "127.0.0.1",
                port,
                database,
                database,
                introspector(),
                exports,
                RESOURCE,
                "https://id.example.com",
                audit,
                max_request_bytes=size,
            )
    for auth, auth_object, export_object, message in (
        ("http://id.example.com", introspector(), exports, "authorization server"),
        (
            "https://id.example.com",
            remote.OAuthIntrospector(
                "https://id.example.com/introspect",
                "client",
                "secret",
                "https://other.example.com",
                "tenant_id",
                "tenant-1",
                "roles",
                {"analyst"},
            ),
            exports,
            "introspector resource",
        ),
        (
            "https://id.example.com",
            introspector(),
            remote.ExportJobs(database, tmp_path / "other", "https://download.example.com"),
            "download base URL",
        ),
    ):
        with pytest.raises(ValueError, match=message):
            remote.build_server(
                database,
                "127.0.0.1",
                9443,
                database,
                database,
                auth_object,
                export_object,
                RESOURCE,
                auth,
                audit,
            )
    for invalid_resource in (
        RESOURCE_ORIGIN,
        "http://crm.example.com/mcp",
        f"{RESOURCE}?query=yes",
        "https://user@crm.example.com/mcp",
    ):
        with pytest.raises(ValueError, match="MCP resource"):
            remote.build_server(
                database,
                "127.0.0.1",
                9443,
                database,
                database,
                introspector(),
                exports,
                invalid_resource,
                "https://id.example.com",
                audit,
            )

    class SocketServer:
        socket = object()

    class Context:
        def __init__(self, protocol):
            assert protocol == remote.ssl.PROTOCOL_TLS_SERVER
            self.minimum_version = None

        def load_cert_chain(self, certificate, private_key):
            assert certificate == database and private_key == database

        def wrap_socket(self, socket, server_side):
            assert server_side and socket is SocketServer.socket
            return "tls-socket"

    socket_server = SocketServer()
    monkeypatch.setattr(remote, "ThreadingHTTPServer", lambda *_args: socket_server)
    monkeypatch.setattr(remote.ssl, "SSLContext", Context)
    built = remote.build_server(
        database,
        "127.0.0.1",
        9443,
        database,
        database,
        introspector(),
        exports,
        RESOURCE,
        "https://id.example.com",
        audit,
    )
    assert built is socket_server and built.socket == "tls-socket"
    from visual_ingestion import IngestionStore

    intake = IngestionStore(tmp_path / "intake", "tenant-1")
    intake.tenant = "other-tenant"
    with pytest.raises(ValueError, match="Intake tenant"):
        remote.build_server(
            database,
            "127.0.0.1",
            9443,
            database,
            database,
            introspector(),
            exports,
            RESOURCE,
            "https://id.example.com",
            audit,
            ingestion=intake,
        )
    intake.tenant = "tenant-1"
    socket_server.socket = SocketServer.socket
    remote.build_server(
        database,
        "127.0.0.1",
        9443,
        database,
        database,
        introspector(),
        exports,
        RESOURCE,
        "https://id.example.com",
        audit,
        ingestion=intake,
    )

    monkeypatch.setattr(remote, "load_project_env", lambda: None)
    monkeypatch.setattr(sys, "argv", ["retrieval_remote_mcp.py", str(database)])
    with pytest.raises(SystemExit) as exit_info:
        remote.main()
    assert exit_info.value.code == 2

    class Server:
        def __init__(self):
            self.served = False
            self.closed = False

        def serve_forever(self):
            self.served = True

        def server_close(self):
            self.closed = True

    fake = Server()
    monkeypatch.setenv("RETRIEVAL_REMOTE_MCP_CLIENT_SECRET", "secret")
    base_argv = [
        "retrieval_remote_mcp.py",
        str(database),
        "--certificate",
        str(database),
        "--private-key",
        str(database),
        "--resource",
        RESOURCE,
        "--authorization-server",
        "https://id.example.com",
        "--introspection-endpoint",
        "https://id.example.com/introspect",
        "--client-id",
        "client",
        "--tenant",
        "tenant-1",
        "--allow-role",
        "analyst",
        "--audit-log",
        str(tmp_path / "main-audit.jsonl"),
        "--export-dir",
        str(tmp_path / "exports"),
        "--download-base-url",
        RESOURCE_ORIGIN,
    ]
    monkeypatch.setattr(sys, "argv", base_argv)
    with pytest.raises(SystemExit, match="disabled"):
        remote.main()
    monkeypatch.setattr(sys, "argv", [*base_argv, "--client-secret-env", "bad-name", "--enable"])
    with pytest.raises(SystemExit, match="uppercase"):
        remote.main()
    monkeypatch.setattr(
        remote,
        "build_server",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad build")),
    )
    monkeypatch.setattr(sys, "argv", [*base_argv, "--enable"])
    with pytest.raises(SystemExit, match="bad build"):
        remote.main()
    monkeypatch.setattr(remote, "build_server", lambda *_args, **_kwargs: fake)
    monkeypatch.setattr(sys, "argv", [*base_argv, "--enable"])
    remote.main()
    assert fake.served and fake.closed
    monkeypatch.setattr(sys, "argv", [*base_argv, "--enable", "--ingestion-dir", str(intake.root)])
    remote.main()


def test_asset_bytes_refuses_missing_file(tmp_path):
    with pytest.raises(ValueError, match="Missing required asset"):
        remote._asset_bytes(tmp_path / "missing.js")
