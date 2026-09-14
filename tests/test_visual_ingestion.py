"""Evidence-preserving visual intake, shared by MCP and the application API."""

import base64
import copy
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
import visual_ingestion as visual
from PIL import Image
from visual_ingestion import IngestionStore, ingestion_schema


def picture():
    output = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode()


@pytest.fixture
def store(tmp_path):
    return IngestionStore(tmp_path / "intake", "tenant-a")


def session(store, owner="user-a", pages=1):
    return store.create_session(owner, "session-key", pages)["session_id"]


def upload(store, sid, owner="user-a", page=1):
    return store.upload_page(owner, sid, str(page), page, "image/png", picture())


def proposal(store, sid, owner="user-a"):
    status = store.status(owner, sid)
    return {
        "schema_version": ingestion_schema()["schema_version"],
        "source_set_sha256": status["source_set_sha256"],
        "client": {"name": "fictional-client", "model": "unknown"},
        "records": [
            {
                "record_id": "contact-1",
                "document_type": "unknown",
                "page_numbers": [1],
                "header": {
                    "contact_name": {
                        "value": "Example Person",
                        "raw_text": "Example Person",
                        "source": "printed",
                        "page_number": 1,
                        "box": [0.1, 0.1, 0.9, 0.3],
                    }
                },
                "lines": [],
                "unmapped_fields": [],
                "issues": [],
            }
        ],
        "page_exceptions": [],
    }


def test_schema_and_image_to_record(store):
    sid = session(store)
    source = upload(store, sid)
    assert source["status"] == "retained"
    assert store.get_page("user-a", sid, 1)["data"] == picture()
    payload = proposal(store, sid)
    submitted = store.submit("user-a", sid, "proposal-key", payload)
    assert submitted["status"] == "pending_review"
    assert submitted["canonical_write_permitted"] is False
    assert submitted["record_count"] == 1
    assert store.submit("user-a", sid, "proposal-key", payload) == submitted
    assert store.get_proposal("user-a", sid, submitted["proposal_id"])["proposal"] == payload
    assert store.status("user-a", sid)["proposal_count"] == 1
    changed = copy.deepcopy(payload)
    changed["records"][0]["header"]["contact_name"]["value"] = "Changed"
    with pytest.raises(ValueError, match="Idempotency"):
        store.submit("user-a", sid, "proposal-key", changed)


def test_owner_and_incomplete_source_refusal(store):
    sid = session(store, pages=2)
    upload(store, sid)
    with pytest.raises(ValueError, match="not found"):
        store.status("someone-else", sid)
    result = store.submit("user-a", sid, "incomplete", proposal(store, sid))
    assert result["status"] == "rejected"
    assert "source_pages_incomplete" in {item["reason"] for item in result["findings"]}
    assert store.status("user-a", sid)["proposal_count"] == 1


def test_forged_approval_is_retained_but_rejected(store):
    sid = session(store)
    upload(store, sid)
    payload = proposal(store, sid)
    payload["approved"] = True
    result = store.submit("user-a", sid, "bad", payload)
    assert result["status"] == "rejected"
    assert store.get_proposal("user-a", sid, result["proposal_id"])["proposal"] == payload
    assert result["canonical_write_permitted"] is False


def test_concurrent_retry_and_restart(store):
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: store.create_session("owner", "retry", 1), range(8)))
    assert all(receipt == receipts[0] for receipt in receipts)
    assert (
        IngestionStore(store.root, "tenant-a").status("owner", receipts[0]["session_id"])["status"]
        == "awaiting_pages"
    )
    with pytest.raises(ValueError, match="tenant or version"):
        IngestionStore(store.root, "other-tenant")


def test_snapshot_directory_collision_refused_before_any_write(tmp_path):
    snapshot = tmp_path / "intake.sqlite"
    original = b"immutable approved snapshot"
    snapshot.write_bytes(original)
    for directory in (tmp_path, tmp_path.parent):
        with pytest.raises(ValueError, match="approved snapshot"):
            IngestionStore(directory, "tenant", protected_database=snapshot)
    assert snapshot.read_bytes() == original
    assert (
        IngestionStore(tmp_path / "separate", "tenant", protected_database=snapshot).tenant
        == "tenant"
    )


@pytest.mark.parametrize("value", [None, True, 0, -1, 21, 1.5, "1"])
def test_invalid_page_count(store, value):
    with pytest.raises(ValueError, match="integer"):
        store.create_session("owner", "key", value)


@pytest.mark.parametrize("value", [None, "", " " * 5, "x" * 201, 1])
def test_invalid_request_identity(store, value):
    with pytest.raises(ValueError, match="text"):
        store.create_session("owner", value, 1)


def test_image_refusals_and_retained_bytes(store, monkeypatch):
    sid = session(store)
    for key, mime, data in [
        ("unsupported", "image/heic", "bm90LWFuLWltYWdl"),
        ("corrupt", "image/png", "bm90LWFuLWltYWdl"),
        ("mismatch", "image/jpeg", picture()),
    ]:
        result = store.upload_page("user-a", sid, key, 1, mime, data)
        assert result["status"] == "rejected"
    with pytest.raises(ValueError, match="Retained page not found"):
        store.get_page("user-a", sid, 1)
    with pytest.raises(ValueError, match="base64"):
        store.upload_page("user-a", sid, "bad-base64", 1, "image/png", "@@")
    monkeypatch.setattr(visual, "MAX_IMAGE_BYTES", 1)
    with pytest.raises(ValueError, match="byte limit"):
        store.upload_page("user-a", sid, "too-big", 1, "image/png", "YWJj")
    monkeypatch.setattr(visual, "MAX_IMAGE_BYTES", 10000)
    monkeypatch.setattr(visual, "MAX_PIXELS", 1)
    assert (
        store.upload_page("user-a", sid, "pixels", 1, "image/png", picture())["status"]
        == "rejected"
    )
    monkeypatch.setattr(visual, "MAX_PIXELS", 10000)
    upload(store, sid)
    assert store.upload_page("user-a", sid, "duplicate-slot", 1, "image/png", picture())[
        "findings"
    ] == ["page_slot_already_retained"]
    assert store.upload_page("user-a", sid, "extra-page", 2, "image/png", picture())[
        "findings"
    ] == ["page_outside_declared_source_set"]
    with closing(sqlite3.connect(store.path)) as connection:
        raw = connection.execute("SELECT data FROM entries WHERE request_key='corrupt'").fetchone()[
            0
        ]
        assert raw == b"not-an-image"


def test_private_store_and_tampering(tmp_path, store):
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="0700"):
        IngestionStore(public, "tenant")
    link = tmp_path / "link"
    link.symlink_to(store.root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        IngestionStore(link, "tenant-a")
    store.path.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        IngestionStore(store.root, "tenant-a")
    store.path.chmod(0o600)
    sid = session(store)
    with closing(sqlite3.connect(store.path)) as connection, connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE entries SET result='{}'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM entries")
        connection.execute("DROP TRIGGER no_update")
        connection.execute("UPDATE entries SET result='{}'")
    with pytest.raises(ValueError, match="integrity"):
        store.status("user-a", sid)


def test_quota_and_non_json(store, monkeypatch):
    sid = session(store)
    upload(store, sid)
    for bad in [float("nan"), {"a": object()}]:
        with pytest.raises(ValueError, match="finite"):
            store.submit("user-a", sid, "bad", bad)
    monkeypatch.setattr(visual, "MAX_PROPOSAL_BYTES", 1)
    with pytest.raises(ValueError, match="byte limit"):
        store.submit("user-a", sid, "big", {"too": "big"})
    monkeypatch.setattr(visual, "MAX_ENTRIES", 1)
    with pytest.raises(ValueError, match="storage quota"):
        store.create_session("user-a", "another", 1)
    monkeypatch.setattr(visual, "MAX_ENTRIES", 10000)
    monkeypatch.setattr(visual, "MAX_STORE_BYTES", 1)
    with pytest.raises(ValueError, match="storage quota"):
        store.create_session("user-a", "another", 1)
    monkeypatch.setattr(visual, "MAX_STORE_BYTES", 10000000)
    monkeypatch.setattr(visual, "MAX_SESSION_ENTRIES", 1)
    with pytest.raises(ValueError, match="session quota"):
        store.upload_page("user-a", sid, "limit", 1, "image/png", picture())


def replace_at(value, path, replacement):
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement


@pytest.mark.parametrize(
    "path,replacement",
    [
        (("schema_version",), "old"),
        (("source_set_sha256",), "wrong"),
        (("client",), {}),
        (("client", "model"), ""),
        (("records",), {}),
        (("records", 0), {}),
        (("records", 0, "record_id"), ""),
        (("records", 0, "document_type"), "invented"),
        (("records", 0, "page_numbers"), []),
        (("records", 0, "page_numbers"), [1, 1]),
        (("records", 0, "page_numbers"), [2]),
        (("records", 0, "header"), {"approved": {}}),
        (("records", 0, "header", "contact_name", "value"), 123),
        (("records", 0, "header", "contact_name", "raw_text"), ""),
        (("records", 0, "header", "contact_name", "source"), "system"),
        (("records", 0, "header", "contact_name", "page_number"), 2),
        (("records", 0, "header", "contact_name", "box"), []),
        (("records", 0, "header", "contact_name", "box"), [True, 0, 1, 1]),
        (("records", 0, "header", "contact_name", "box"), [-1, 0, 1, 1]),
        (("records", 0, "header", "contact_name", "box"), [10**400, 0, 1, 1]),
        (("records", 0, "header", "contact_name", "box"), [0.9, 0, 0.1, 1]),
        (("records", 0, "header", "contact_name", "box"), [0, 0.9, 1, 0.1]),
        (("records", 0, "header", "contact_name", "box"), "box"),
        (("records", 0, "header"), {}),
        (("records", 0, "lines"), [{"line_number": 1, "fields": {}}]),
        (("records", 0, "unmapped_fields"), [{"source_label": "unknown"}]),
        (("records", 0, "issues"), [""]),
        (("page_exceptions",), [{"page_number": 1}]),
    ],
)
def test_invalid_proposals_retained(store, path, replacement):
    sid = session(store)
    upload(store, sid)
    payload = proposal(store, sid)
    replace_at(payload, path, replacement)
    result = store.submit("user-a", sid, "bad", payload)
    assert result["status"] == "rejected"
    assert result["findings"]
    assert store.get_proposal("user-a", sid, result["proposal_id"])["proposal"] == payload


def test_multi_page_lines_unknown_fields_and_exceptions(store):
    sid = session(store, pages=2)
    upload(store, sid)
    upload(store, sid, page=2)
    payload = proposal(store, sid)
    field = payload["records"][0]["header"]["contact_name"]
    field["value"] = None
    field["source"] = "handwritten"
    payload["records"][0]["unmapped_fields"] = [
        {"source_label": "New label", "observation": copy.deepcopy(field)}
    ]
    payload["records"][0]["lines"] = [
        {"line_number": 1, "fields": {"description": copy.deepcopy(field)}}
    ]
    payload["records"][0]["issues"] = ["unreadable name"]
    payload["page_exceptions"] = [{"page_number": 2, "reason": "unreadable page"}]
    result = store.submit("user-a", sid, "complete", payload)
    assert result["status"] == "pending_review"
    payload["records"][0]["lines"] *= 2
    assert store.submit("user-a", sid, "duplicate-line", payload)["status"] == "rejected"
    payload["records"][0]["lines"] = []
    payload["records"] *= 2
    assert store.submit("user-a", sid, "duplicate-record", payload)["status"] == "rejected"
    payload["records"] = []
    payload["page_exceptions"] *= 2
    result = store.submit("user-a", sid, "empty", payload)
    assert {item["reason"] for item in result["findings"]} >= {
        "invalid_page_exception",
        "no_records_extracted",
        "page_not_accounted_for",
    }
    with pytest.raises(ValueError, match="Proposal not found"):
        store.get_proposal("user-a", sid, "absent")
    assert store.submit("user-a", sid, "non-object", [1])["status"] == "rejected"
    assert json.loads(visual.encoded(ingestion_schema()))["proposal_schema"]["$defs"]


def test_session_listing_reviewer_access_and_review_summary(store):
    sid = session(store, pages=2)
    upload(store, sid, page=1)
    upload(store, sid, page=2)
    first = proposal(store, sid)
    first["page_exceptions"] = [{"page_number": 2, "reason": "unclear"}]
    first_receipt = store.submit("user-a", sid, "proposal-1", first)
    second = copy.deepcopy(first)
    second["records"][0]["header"]["contact_name"]["value"] = "Updated Person"
    second["records"][0]["issues"] = ["verify spelling"]
    second["page_exceptions"] = []
    second_receipt = store.submit("user-a", sid, "proposal-2", second)
    grant = store.grant_reviewer("user-a", sid, "grant-1", "reviewer-a")
    assert grant["reviewer_subject"] == "reviewer-a"

    owner_sessions = store.list_sessions("user-a")["sessions"]
    assert owner_sessions[0]["session_id"] == sid
    assert owner_sessions[0]["access_role"] == "owner"
    assert owner_sessions[0]["proposal_count"] == 2

    reviewer_sessions = store.list_sessions("reviewer-a")["sessions"]
    assert reviewer_sessions == [
        {**reviewer_sessions[0], "session_id": sid, "access_role": "reviewer"}
    ]
    assert reviewer_sessions[0]["granted_to_requester"] is True

    assert store.status("reviewer-a", sid)["proposal_count"] == 2
    assert store.get_page("reviewer-a", sid, 1)["status"] == "retained"
    assert store.get_proposal("reviewer-a", sid, second_receipt["proposal_id"])["proposal"][
        "records"
    ][0]["issues"] == ["verify spelling"]
    summary = store.review_summary("reviewer-a", sid)
    assert summary["access_role"] == "reviewer"
    assert summary["proposal_count"] == 2
    assert summary["proposals"][0]["proposal_id"] == first_receipt["proposal_id"]
    assert summary["proposals"][1]["proposal_id"] == second_receipt["proposal_id"]
    assert summary["proposals"][1]["changes_since_previous"]["changed_record_ids"] == ["contact-1"]
    assert summary["proposals"][1]["changes_since_previous"]["removed_page_exception_numbers"] == [
        2
    ]
    assert summary["reviewer_grants"][0]["grant_id"] == grant["grant_id"]
    with pytest.raises(ValueError, match="Session not found"):
        store.snapshot("reviewer-a", sid)
    with pytest.raises(ValueError, match="Session not found"):
        store.list_reviewer_grants("reviewer-a", sid)


def test_reviewer_grant_failures_and_duplicate_refusal(store):
    sid = session(store)
    upload(store, sid)
    with pytest.raises(ValueError, match="session owner"):
        store.grant_reviewer("user-a", sid, "self", "user-a")
    grant = store.grant_reviewer("user-a", sid, "grant", "reviewer-a")
    assert grant["status"] == "active"
    assert store.grant_reviewer("user-a", sid, "grant", "reviewer-a") == grant
    with pytest.raises(ValueError, match="already granted"):
        store.grant_reviewer("user-a", sid, "grant-2", "reviewer-a")
    with pytest.raises(ValueError, match="Session not found"):
        store.grant_reviewer("other", sid, "grant-3", "reviewer-b")
    with pytest.raises(ValueError, match="Session not found"):
        store.status("reviewer-b", sid)


def test_review_summary_handles_malformed_retained_proposals_and_hidden_sessions(store):
    sid = session(store)
    upload(store, sid)
    store.submit("user-a", sid, "bad-non-object", [1])
    invalid = {
        "schema_version": ingestion_schema()["schema_version"],
        "source_set_sha256": store.status("user-a", sid)["source_set_sha256"],
        "client": [],
        "records": [None, {"record_id": "", "document_type": 1, "page_numbers": [True]}],
        "page_exceptions": [None, {"page_number": True}],
    }
    store.submit("user-a", sid, "bad-shape", invalid)
    summary = store.review_summary("user-a", sid)
    assert summary["proposal_count"] == 2
    assert summary["proposals"][0]["client"] is None
    assert summary["proposals"][0]["record_ids"] == []
    assert summary["proposals"][1]["document_types"] == []
    assert summary["proposals"][1]["record_page_numbers"] == []
    assert summary["proposals"][1]["page_exception_numbers"] == [True]

    hidden = session(store, owner="other-owner")
    upload(store, hidden, owner="other-owner")
    other_visible = session(store, owner="other-owner")
    upload(store, other_visible, owner="other-owner")
    store.grant_reviewer("other-owner", other_visible, "grant-someone-else", "reviewer-b")
    store.grant_reviewer("other-owner", other_visible, "grant-user-a", "user-a")
    assert {item["session_id"] for item in store.list_sessions("user-a")["sessions"]} == {
        sid,
        other_visible,
    }


def test_list_sessions_skips_inaccessible_sessions_without_building_listing(store, monkeypatch):
    hidden = session(store, owner="other-owner")
    upload(store, hidden, owner="other-owner")
    calls = []
    original = store._session_listing

    def record_listing(connection, session_row, accessor, access_role):
        calls.append((session_row["id"], accessor, access_role))
        return original(connection, session_row, accessor, access_role)

    monkeypatch.setattr(store, "_session_listing", record_listing)
    assert store.list_sessions("user-a") == {"sessions": []}
    assert calls == []
