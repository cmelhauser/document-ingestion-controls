"""Tests for the explicitly enabled read-only HTTPS retrieval transport."""

import importlib
import sys
from io import BytesIO
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

https = importlib.import_module("retrieval_https")
store = importlib.import_module("retrieval_store")


def database(tmp_path):
    export = {
        "batch_id": "batch",
        "tables": {
            "document": [
                {
                    "document_id": "doc",
                    "batch_id": "batch",
                    "source_file": "source.pdf",
                    "source_page_range": "1",
                    "source_sha256": "hash",
                    "review_status": "exception_resolved",
                }
            ],
            "party": [
                {
                    "party_key": "party",
                    "natural_key": "sample party",
                    "canonical_name": "Sample Party",
                    "normalized_name": "sample party",
                    "batch_id": "batch",
                    "review_status": "exception_resolved",
                }
            ],
        },
    }
    path = tmp_path / "retrieval.sqlite"
    store.build_database(export, path)
    return path


def test_route_is_authenticated_and_read_only(tmp_path):
    db = database(tmp_path)
    assert https.authorized({"Authorization": "Bearer token"}, "token")
    assert not https.authorized({}, "token")
    assert https.route("/health", {}, db, "token")[0] == 401
    assert https.route("/health", {"Authorization": "Bearer token"}, db, "token")[1]["read_only"]
    assert https.route("/v1/capabilities", {"Authorization": "Bearer token"}, db, "token")[0] == 200
    assert (
        https.route("/v1/crm/summary", {"Authorization": "Bearer token"}, db, "token")[1][
            "total_records"
        ]
        == 2
    )
    assert (
        https.route(
            "/v1/crm/schema?table=document", {"Authorization": "Bearer token"}, db, "token"
        )[1]["tables"][0]["table"]
        == "document"
    )
    assert (
        https.route(
            "/v1/crm/export/document?limit=1", {"Authorization": "Bearer token"}, db, "token"
        )[1]["rows"][0]["document_id"]
        == "doc"
    )
    assert https.route(
        "/v1/crm/search?query=Sample", {"Authorization": "Bearer token"}, db, "token"
    )[1]["results"]
    assert (
        https.route(
            "/v1/crm/record/party?key=party", {"Authorization": "Bearer token"}, db, "token"
        )[1]["record"]["canonical_name"]
        == "Sample Party"
    )
    assert (
        https.route(
            "/v1/crm/query/party?filter.canonical_name__contains=party&select=canonical_name",
            {"Authorization": "Bearer token"},
            db,
            "token",
        )[1]["returned_records"]
        == 1
    )
    assert (
        https.route("/v1/crm/accounts/party", {"Authorization": "Bearer token"}, db, "token")[1][
            "party"
        ]["party_key"]
        == "party"
    )
    assert (
        https.route(
            "/v1/crm/analysis/sales?group_by=region", {"Authorization": "Bearer token"}, db, "token"
        )[0]
        == 200
    )
    assert https.route("/v1/crm/reports", {"Authorization": "Bearer token"}, db, "token")[0] == 200
    assert (
        https.route(
            "/v1/crm/reports/account_directory", {"Authorization": "Bearer token"}, db, "token"
        )[0]
        == 200
    )
    assert https.route("/v1/documents", {"Authorization": "Bearer token"}, db, "token")[0] == 400
    assert (
        https.route(
            "/v1/documents?query=doc&limit=x", {"Authorization": "Bearer token"}, db, "token"
        )[0]
        == 400
    )
    assert (
        https.route(
            "/v1/documents?query=doc&limit=21", {"Authorization": "Bearer token"}, db, "token"
        )[0]
        == 400
    )
    status, payload = https.route(
        "/v1/documents?query=doc&limit=1", {"Authorization": "Bearer token"}, db, "token"
    )
    assert status == 200 and payload["results"][0]["document_id"] == "doc"
    assert (
        https.route("/v1/documents/doc", {"Authorization": "Bearer token"}, db, "token")[0] == 200
    )
    assert (
        https.route("/v1/documents/missing", {"Authorization": "Bearer token"}, db, "token")[0]
        == 404
    )
    assert (
        https.route("/v1/documents/a/b", {"Authorization": "Bearer token"}, db, "token")[0] == 404
    )
    assert https.route("/other", {"Authorization": "Bearer token"}, db, "token")[0] == 404


@pytest.mark.parametrize(
    "path",
    (
        "/v1/crm/schema?table=document&table=party",
        "/v1/crm/schema?extra=1",
        "/health?extra=1",
        "/v1/capabilities?extra=1",
        "/v1/crm/summary?extra=1",
        "/v1/crm/schema?table=bad",
        "/v1/crm/search",
        "/v1/crm/search?query=x&extra=1",
        "/v1/crm/search?query=&limit=20",
        "/v1/crm/record/party",
        "/v1/crm/record/party?key=missing",
        "/v1/crm/query/party?limit=bad",
        "/v1/crm/query/party?limit=1&limit=2",
        "/v1/crm/query/party?bad=x",
        "/v1/crm/query/party?filter.party_key__is_null=maybe",
        "/v1/crm/query/party?filter.missing=x",
        "/v1/crm/accounts/missing",
        "/v1/crm/analysis/sales?start_date=1&start_date=2",
        "/v1/crm/analysis/sales?group_by=bad",
        "/v1/crm/reports/account_directory?x=1&x=2",
        "/v1/crm/reports?x=1",
        "/v1/crm/reports/unknown",
        "/v1/crm/export/document?limit=1&limit=2",
        "/v1/crm/export/document?limit=bad",
        "/v1/crm/export/document?extra=x",
        "/v1/crm/export/bad",
        "/v1/documents?query=doc&query=other",
        "/v1/documents?query=&limit=1",
        "/v1/documents?query=doc&extra=1",
        "/v1/documents/doc?extra=1",
    ),
)
def test_crm_routes_map_invalid_queries_to_bounded_errors(tmp_path, path):
    assert (
        https.route(path, {"Authorization": "Bearer token"}, database(tmp_path), "token")[0] == 400
    )


@pytest.mark.parametrize(
    "path",
    (
        "/v1/crm/record/",
        "/v1/crm/record/party/extra?key=x",
        "/v1/crm/query/",
        "/v1/crm/query/party/extra",
        "/v1/crm/accounts/",
        "/v1/crm/accounts/party/extra",
        "/v1/crm/reports/",
        "/v1/crm/reports/name/extra",
        "/v1/crm/export/",
        "/v1/crm/export/document/extra",
    ),
)
def test_crm_routes_reject_malformed_resource_paths(tmp_path, path):
    assert (
        https.route(path, {"Authorization": "Bearer token"}, database(tmp_path), "token")[0] == 404
    )


def test_crm_routes_map_snapshot_failures_and_valid_boolean_filter(tmp_path):
    headers = {"Authorization": "Bearer token"}
    missing = tmp_path / "missing.sqlite"
    assert https.route("/v1/capabilities", headers, missing, "token")[0] == 400
    assert https.route("/v1/crm/summary", headers, missing, "token")[0] == 400
    assert https.route("/v1/documents/doc", headers, missing, "token")[0] == 400
    assert (
        https.route(
            "/v1/crm/query/party?filter.party_key__is_null=true",
            headers,
            database(tmp_path),
            "token",
        )[0]
        == 200
    )


def test_server_boundaries_and_main(monkeypatch, tmp_path):
    db = database(tmp_path)
    with pytest.raises(ValueError, match="certificate"):
        https.build_server(db, "127.0.0.1", 1, "missing", "missing", "token")
    certificate, key = tmp_path / "cert", tmp_path / "key"
    certificate.write_text("cert")
    key.write_text("key")
    with pytest.raises(ValueError, match="database"):
        https.build_server("missing", "127.0.0.1", 1, certificate, key, "token")
    with pytest.raises(ValueError, match="bearer"):
        https.build_server(db, "127.0.0.1", 1, certificate, key, "")
    with pytest.raises(ValueError, match="port"):
        https.build_server(db, "127.0.0.1", 0, certificate, key, "token")

    class Socket:
        pass

    class Server:
        socket = Socket()

        def __init__(self, *_):
            self.socket = Socket()

    class Context:
        minimum_version = None

        def load_cert_chain(self, cert, private):
            assert cert == str(certificate) and private == str(key)

        def wrap_socket(self, socket, server_side):
            assert server_side
            return socket

    monkeypatch.setattr(https, "ThreadingHTTPServer", Server)
    monkeypatch.setattr(https.ssl, "SSLContext", lambda *_: Context())
    assert isinstance(
        https.build_server(str(db), "127.0.0.1", 1, str(certificate), str(key), "token"), Server
    )
    monkeypatch.setattr(https, "load_project_env", lambda: None)
    monkeypatch.setattr(https, "env_bool", lambda *_: False)
    monkeypatch.setattr(https, "env_value", lambda _name, default: default)
    monkeypatch.setattr(
        sys, "argv", ["retrieval_https.py", "db", "--certificate", "cert", "--private-key", "key"]
    )
    with pytest.raises(SystemExit, match="disabled"):
        https.main()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "retrieval_https.py",
            "db",
            "--enable",
            "--credential-env",
            "bad",
            "--certificate",
            "cert",
            "--private-key",
            "key",
        ],
    )
    with pytest.raises(SystemExit, match="uppercase"):
        https.main()
    monkeypatch.setattr(https, "env_bool", lambda *_: (_ for _ in ()).throw(ValueError("bad env")))
    with pytest.raises(SystemExit, match="bad env"):
        https.main()


def test_handler_and_enabled_main(monkeypatch, tmp_path):
    db = database(tmp_path)
    handler_type = https.handler(db, "token")
    request = object.__new__(handler_type)
    request.path, request.headers, request.wfile = (
        "/health",
        {"Authorization": "Bearer token"},
        BytesIO(),
    )
    headers, statuses = [], []
    request.send_response = statuses.append
    request.send_header = lambda *args: headers.append(args)
    request.end_headers = lambda: None
    request.do_GET()
    assert statuses == [200] and ("Cache-Control", "no-store") in headers
    request.send_error = lambda *args: statuses.append(args[0])
    request.do_POST()
    assert statuses[-1] == 405
    request.do_PUT()
    request.do_PATCH()
    request.do_DELETE()
    assert statuses[-3:] == [405, 405, 405]
    assert request.log_message("ignored") is None

    class Server:
        def __init__(self):
            self.closed = False

        def serve_forever(self):
            return

        def server_close(self):
            self.closed = True

    server = Server()
    monkeypatch.setattr(https, "load_project_env", lambda: None)
    monkeypatch.setattr(https, "env_bool", lambda *_: False)
    monkeypatch.setattr(https, "env_value", lambda _name, default: default)
    monkeypatch.setattr(https, "build_server", lambda *args: server)
    monkeypatch.setattr(https.os, "environ", {"RETRIEVAL_HTTPS_BEARER_TOKEN": "token"})
    monkeypatch.setattr(
        sys,
        "argv",
        ["retrieval_https.py", "db", "--enable", "--certificate", "cert", "--private-key", "key"],
    )
    https.main()
    assert server.closed
    monkeypatch.setattr(
        https, "build_server", lambda *args: (_ for _ in ()).throw(ValueError("bad server"))
    )
    with pytest.raises(SystemExit, match="bad server"):
        https.main()
