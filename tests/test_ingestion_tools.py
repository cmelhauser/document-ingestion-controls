"""The same intake operations over stdio MCP and authenticated JSON HTTP."""

import hashlib
import io
import json
import sqlite3
import sys

import ingestion_tools
import pytest
import retrieval_mcp
import retrieval_remote_mcp as remote
import retrieval_store
from test_retrieval_remote_mcp import (
    FakeIntrospector,
    approved_export,
    request,
    rpc,
    running_handler,
)
from test_visual_ingestion import picture, proposal
from visual_ingestion import IngestionStore


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "retrieval.sqlite"
    retrieval_store.build_database(approved_export(), path)
    return path


def test_local_tools_and_stdio(tmp_path, monkeypatch):
    store = IngestionStore(tmp_path / "intake", "local")
    for method in ("initialize", "server/discover"):
        assert (
            "visual intake"
            in retrieval_mcp.handle(rpc(method), None, store)["result"]["instructions"]
        )
    result = retrieval_mcp.handle(rpc("tools/list"), None, store)
    assert len(result["result"]["tools"]) == 23

    def call(name, args):
        return retrieval_mcp.handle(
            rpc("tools/call", {"name": name, "arguments": args}), None, store, "user-a"
        )

    schema = call("get_ingestion_schema", {})["result"]["structuredContent"]["result"]
    assert schema["canonical_write_permitted"] is False
    sid = call("create_ingestion_session", {"idempotency_key": "session", "expected_pages": 1})[
        "result"
    ]["structuredContent"]["result"]["session_id"]
    args = {
        "session_id": sid,
        "idempotency_key": "page",
        "page_number": 1,
        "mime_type": "image/png",
        "data_base64": picture(),
    }
    assert (
        call("upload_ingestion_page", args)["result"]["structuredContent"]["result"]["status"]
        == "retained"
    )
    result = call("get_ingestion_page", {"session_id": sid, "page_number": 1})["result"]
    assert result["content"][1] == {"type": "image", "mimeType": "image/png", "data": picture()}
    assert "data" not in result["structuredContent"]["result"]
    payload = proposal(store, sid)
    value = call(
        "submit_record_proposal",
        {"session_id": sid, "idempotency_key": "proposal", "proposal": payload},
    )["result"]["structuredContent"]["result"]
    grant = call(
        "grant_ingestion_session_reviewer",
        {
            "session_id": sid,
            "idempotency_key": "grant",
            "reviewer_subject": "reviewer-a",
        },
    )["result"]["structuredContent"]["result"]
    assert grant["status"] == "active"
    sessions = call("list_ingestion_sessions", {})["result"]["structuredContent"]["result"]
    assert sessions["sessions"][0]["session_id"] == sid
    reviewers = call("list_ingestion_session_reviewers", {"session_id": sid})["result"][
        "structuredContent"
    ]["result"]
    assert reviewers["reviewer_grants"][0]["reviewer_subject"] == "reviewer-a"
    review = call("get_ingestion_review_summary", {"session_id": sid})["result"][
        "structuredContent"
    ]["result"]
    assert review["proposal_count"] == 1
    assert (
        call("get_record_proposal", {"session_id": sid, "proposal_id": value["proposal_id"]})[
            "result"
        ]["structuredContent"]["result"]["proposal"]
        == payload
    )
    assert (
        call("get_ingestion_status", {"session_id": sid})["result"]["structuredContent"]["result"][
            "proposal_count"
        ]
        == 1
    )
    assert "error" in call("get_ingestion_schema", {"extra": 1})
    assert "error" in call("missing", {})
    with pytest.raises(ValueError, match="Unknown ingestion"):
        ingestion_tools.invoke(store, "user-a", "unknown", {})
    output = io.StringIO()
    retrieval_mcp.serve(
        None, io.StringIO(json.dumps(rpc("tools/list")) + "\n"), output, ingestion=store
    )
    assert len(json.loads(output.getvalue())["result"]["tools"]) == 23
    calls = []
    monkeypatch.setattr(retrieval_mcp, "serve", lambda path, **kwargs: calls.append((path, kwargs)))
    monkeypatch.setattr(
        sys, "argv", ["retrieval_mcp.py", "fictional.sqlite", "--ingestion-dir", str(store.root)]
    )
    retrieval_mcp.main()
    assert calls[0][1]["ingestion"].tenant == "local"


def test_stdio_deep_json_refused_without_stopping_server(monkeypatch):
    nesting = sys.getrecursionlimit() + 100
    malformed = "[" * nesting + "0" + "]" * nesting
    output = io.StringIO()
    retrieval_mcp.serve(
        None, io.StringIO(malformed + "\n" + json.dumps(rpc("tools/list")) + "\n"), output
    )
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    # CPython's C decoder and Python recursion ceilings need not be identical.
    assert responses[0]["error"]["code"] in {-32600, -32700}
    assert len(responses[1]["result"]["tools"]) == 12
    original_loads = json.loads

    def decoder(value):
        if value.strip() == "decoder_recursion":
            raise RecursionError("fixture decoder ceiling")
        return original_loads(value)

    monkeypatch.setattr(retrieval_mcp.json, "loads", decoder)
    output = io.StringIO()
    retrieval_mcp.serve(
        None, io.StringIO("decoder_recursion\n" + json.dumps(rpc("tools/list")) + "\n"), output
    )
    responses = [original_loads(line) for line in output.getvalue().splitlines()]
    assert responses[0]["error"]["code"] == -32700
    assert len(responses[1]["result"]["tools"]) == 12


def test_remote_and_api_same_owner_receipts(database, tmp_path, monkeypatch):
    original_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    store = IngestionStore(tmp_path / "intake", "tenant-1")
    auth = FakeIntrospector(scopes=("ingestion:read", "ingestion:submit"))
    headers = {"Content-Type": "application/json"}
    with running_handler(database, tmp_path, auth=auth, ingestion=store) as (server, audit):

        def api(name, args):
            return request(server, "POST", f"/api/ingestion/{name}", json.dumps(args), headers)

        status, _, metadata = request(server, "GET", "/.well-known/oauth-protected-resource")
        assert status == 200 and "ingestion:submit" in metadata["scopes_supported"]
        assert request(server, "GET", "/health")[2]["read_only"] is False
        assert request(server, "GET", "/docs")[2]["canonical_read_only"] is True
        tools = request(server, "POST", "/mcp", json.dumps(rpc("tools/list")), headers)[2][
            "result"
        ]["tools"]
        assert {tool["name"] for tool in tools} == set(ingestion_tools.OPERATIONS)
        sid = api("create_ingestion_session", {"expected_pages": 1, "idempotency_key": "session"})[
            2
        ]["session_id"]
        args = {
            "session_id": sid,
            "idempotency_key": "page",
            "page_number": 1,
            "mime_type": "image/png",
            "data_base64": picture(),
        }
        upload = api("upload_ingestion_page", args)[2]
        replay = request(
            server,
            "POST",
            "/mcp",
            json.dumps(rpc("tools/call", {"name": "upload_ingestion_page", "arguments": args})),
            headers,
        )[2]
        assert replay["result"]["structuredContent"]["result"] == upload
        assert (
            api("get_ingestion_page", {"session_id": sid, "page_number": 1})[2]["data"] == picture()
        )
        assert api("get_ingestion_schema", {})[2]["proposal_only"]
        payload = proposal(store, sid, owner="user-1")
        submitted = api(
            "submit_record_proposal",
            {"session_id": sid, "idempotency_key": "proposal", "proposal": payload},
        )[2]
        assert submitted["status"] == "pending_review"
        grant = api(
            "grant_ingestion_session_reviewer",
            {
                "session_id": sid,
                "idempotency_key": "grant",
                "reviewer_subject": "reviewer-a",
            },
        )[2]
        assert grant["status"] == "active"
        assert api("list_ingestion_sessions", {})[2]["sessions"][0]["session_id"] == sid
        assert (
            api("list_ingestion_session_reviewers", {"session_id": sid})[2]["reviewer_grants"][0][
                "reviewer_subject"
            ]
            == "reviewer-a"
        )
        review_summary = api("get_ingestion_review_summary", {"session_id": sid})[2]
        assert review_summary["proposal_count"] == 1
        assert (
            api(
                "get_record_proposal", {"session_id": sid, "proposal_id": submitted["proposal_id"]}
            )[2]["proposal"]
            == payload
        )
        rejected_payload = dict(payload)
        rejected_payload["approved"] = True
        rejected = api(
            "submit_record_proposal",
            {"session_id": sid, "idempotency_key": "rejected", "proposal": rejected_payload},
        )[2]
        assert rejected["status"] == "rejected" and rejected["findings"]
        assert api("get_ingestion_status", {"session_id": "other-owner"})[0] == 400
        assert api("get_ingestion_status", [])[0] == 400
        assert (
            request(
                server,
                "POST",
                "/mcp",
                json.dumps(rpc("tools/call", {"name": "get_crm_schema"})),
                headers,
            )[0]
            == 403
        )
        assert request(server, "POST", "/api/ingestion/unknown", "{}", headers)[0] == 404
        # A reader may inspect only their own retained sources, never upload.
        auth.scopes = frozenset({"ingestion:read"})
        assert api("upload_ingestion_page", args)[0] == 403
        monkeypatch.setattr(
            auth,
            "authenticate",
            lambda _headers: remote.Principal(
                "reviewer-a", "tenant-1", frozenset({"analyst"}), auth.scopes
            ),
        )
        assert api("get_ingestion_status", {"session_id": sid})[2]["proposal_count"] == 2
        assert (
            api("get_ingestion_page", {"session_id": sid, "page_number": 1})[2]["status"]
            == "retained"
        )
        assert (
            api("get_ingestion_review_summary", {"session_id": sid})[2]["access_role"] == "reviewer"
        )
        assert api("list_ingestion_sessions", {})[2]["sessions"][0]["access_role"] == "reviewer"
        assert (
            api(
                "grant_ingestion_session_reviewer",
                {"session_id": sid, "idempotency_key": "x", "reviewer_subject": "new"},
            )[0]
            == 403
        )
        assert api("list_ingestion_session_reviewers", {"session_id": sid})[0] == 400
        assert (
            api(
                "get_record_proposal", {"session_id": sid, "proposal_id": submitted["proposal_id"]}
            )[2]["proposal"]
            == payload
        )
    assert picture() not in audit.path.read_text()
    assert "Example Person" not in audit.path.read_text()
    assert hashlib.sha256(database.read_bytes()).hexdigest() == original_hash


def test_disabled_remote_has_no_intake_api(database, tmp_path):
    assert remote.required_scope({"params": {"name": []}}) == "crm:read"
    with running_handler(database, tmp_path) as (server, _):
        assert (
            request(
                server,
                "POST",
                "/api/ingestion/get_ingestion_schema",
                "{}",
                {"Content-Type": "application/json"},
            )[0]
            == 404
        )
    assert remote.required_scope({"params": {"name": "get_ingestion_page"}}) == "ingestion:read"
    assert (
        remote.required_scope({"params": {"name": "upload_ingestion_page"}}) == "ingestion:submit"
    )


def test_storage_failure_is_safe_tool_error(tmp_path, monkeypatch):
    store = IngestionStore(tmp_path / "intake", "local")
    monkeypatch.setattr(
        store,
        "status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("sensitive path")),
    )
    with pytest.raises(ValueError, match="storage unavailable") as exc:
        ingestion_tools.invoke(store, "owner", "get_ingestion_status", {"session_id": "id"})
    assert "sensitive" not in str(exc.value)


def test_malformed_remote_input_is_bounded_and_not_logged(database, tmp_path):
    store = IngestionStore(tmp_path / "intake", "tenant-1")
    headers = {"Content-Type": "application/json"}
    with running_handler(database, tmp_path, ingestion=store) as (server, audit):
        for method in (["private source content"], "private source content"):
            result = request(server, "POST", "/mcp", json.dumps(rpc(method)), headers)
            assert result[0] == 200 and "error" in result[2]
        result = request(server, "POST", "/mcp", "[" * 2000 + "]" * 2000, headers)
        # Parser recursion limits vary by interpreter and prior suite setup.
        # Either the parser refuses it or MCP rejects the non-object request.
        assert result[0] in (200, 400) and "error" in result[2]
    assert "private source content" not in audit.path.read_text()
