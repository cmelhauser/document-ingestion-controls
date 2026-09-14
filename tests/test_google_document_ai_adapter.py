"""Tests for the optional Google Document AI adapter; no network call occurs."""

import importlib
import json
import sys
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

adapter = importlib.import_module("google_document_ai_adapter")


def write(path, value):
    path.write_text(json.dumps(value) if not isinstance(value, str) else value)
    return path


def manifest(tmp_path):
    write(tmp_path / "first.pdf", "%PDF-one")
    write(tmp_path / "second.pdf", "%PDF-two")
    return write(
        tmp_path / "manifest.json",
        {
            "pages": [
                {"page_id": "p/1", "page_pdf": "first.pdf"},
                {"page_id": "p2", "page_pdf": "second.pdf"},
            ]
        },
    )


def response():
    return {
        "document": {
            "text": "Amount Dealer\n12.50 Acme",
            "pages": [
                {
                    "pageNumber": 1,
                    "tokens": [
                        {
                            "layout": {
                                "textAnchor": {
                                    "textSegments": [{"startIndex": "0", "endIndex": "6"}]
                                },
                                "confidence": 0.9,
                                "boundingPoly": {
                                    "normalizedVertices": [{"x": 0.1}],
                                    "vertices": [{"x": 1}],
                                },
                            }
                        }
                    ],
                    "tables": [
                        {
                            "layout": {"textAnchor": {"textSegments": [{"endIndex": "24"}]}},
                            "headerRows": [
                                {
                                    "cells": [
                                        {
                                            "layout": {
                                                "textAnchor": {
                                                    "textSegments": [
                                                        {"startIndex": 0, "endIndex": 6}
                                                    ]
                                                }
                                            }
                                        },
                                        {
                                            "layout": {
                                                "textAnchor": {
                                                    "textSegments": [
                                                        {"startIndex": 7, "endIndex": 13}
                                                    ]
                                                }
                                            }
                                        },
                                    ]
                                }
                            ],
                            "bodyRows": [
                                {
                                    "cells": [
                                        {
                                            "rowSpan": 1,
                                            "colSpan": 1,
                                            "layout": {
                                                "textAnchor": {
                                                    "textSegments": [
                                                        {"startIndex": 14, "endIndex": 19}
                                                    ]
                                                }
                                            },
                                        },
                                        {
                                            "layout": {
                                                "textAnchor": {
                                                    "textSegments": [
                                                        {"startIndex": 20, "endIndex": 24}
                                                    ]
                                                }
                                            }
                                        },
                                    ]
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    }


class HTTPResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.value).encode()


def test_endpoint_payload_and_evidence_helpers(tmp_path):
    assert adapter.safe_label("p/1") == "p_1" and adapter.safe_label("!!!") == "page"
    assert adapter.endpoint("project-1", "us", "processor_1").endswith(
        "processors/processor_1:process"
    )
    assert adapter.endpoint("project", "us", "1234567890123456").endswith(
        "processors/1234567890123456:process"
    )
    assert adapter.endpoint("project", "us", "processor", "9876543210987654").endswith(
        "processorVersions/9876543210987654:process"
    )
    assert adapter.endpoint("project", "us", "processor", "pretrained-ocr-v1.0").endswith(
        "processorVersions/pretrained-ocr-v1.0:process"
    )
    assert "processorVersions/v1" in adapter.endpoint("project", "eu", "processor", "v1")
    with pytest.raises(ValueError, match="project"):
        adapter.endpoint("bad/project", "us", "processor")
    pdf = write(tmp_path / "page.pdf", "%PDF")
    body = adapter.request_body(pdf, "p1", True)
    assert body["rawDocument"]["mimeType"] == "application/pdf"
    assert body["processOptions"]["ocrConfig"]["enableNativePdfParsing"] is True
    assert (
        adapter.text_anchor(
            "hello", {"textSegments": [{"endIndex": 2}, {"startIndex": 2, "endIndex": 5}]}
        )
        == "hello"
    )
    assert adapter.text_anchor("hello", {"textSegments": [{"startIndex": "x"}]}) is None
    evidence = adapter.layout_evidence("hello", {"textAnchor": {"textSegments": [{"endIndex": 2}]}})
    assert evidence["evidence_text"] == "he" and evidence["normalized_vertices"] == []
    tables = adapter.source_tables(response()["document"])
    assert tables[0]["source_headers"] == ["Amount", "Dealer"]
    assert tables[0]["source_rows"][0]["cells"][0]["source_label"] == "Amount"
    assert adapter.source_tables({"text": 1, "pages": ["skip"]}) == []
    assert adapter.source_tables({"text": "", "pages": [{"tables": ["skip"]}]}) == []
    assert adapter.table_cells("x", ["skip"]) == []
    assert adapter.text_anchor("x", None) is None
    assert (
        adapter.text_anchor("x", {"textSegments": ["skip", {"startIndex": 2, "endIndex": 1}]})
        is None
    )


def test_provider_request_and_adapter_retention(tmp_path):
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        return HTTPResponse(response())

    value = adapter.provider_request("https://example.test", {"x": 1}, "token", 2, opener)
    assert value["document"]["text"].startswith("Amount")
    assert calls[0][0].get_header("Authorization") == "Bearer token"
    with pytest.raises(ValueError, match="access token"):
        adapter.provider_request("url", {}, "", 1, opener)
    with pytest.raises(ValueError, match="HTTP 403"):
        adapter.provider_request(
            "https://example.test",
            {},
            "token",
            1,
            lambda *args, **kwargs: (_ for _ in ()).throw(
                HTTPError("url", 403, "no", {}, BytesIO())
            ),
        )
    with pytest.raises(ValueError, match="transport"):
        adapter.provider_request(
            "https://example.test",
            {},
            "token",
            1,
            lambda *args, **kwargs: (_ for _ in ()).throw(URLError("offline")),
        )
    intake = manifest(tmp_path)
    result = adapter.run_adapter(
        intake,
        tmp_path / "out.json",
        tmp_path / "handoff.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        "project",
        "us",
        "processor",
        "token",
        opener=opener,
    )
    assert result == {
        "pages": 2,
        "readings": 2,
        "review_items": 0,
        "provider_error_types": [],
        "engine": "google_document_ai/processor",
    }
    records = json.loads((tmp_path / "out.json").read_text())
    assert records[0]["independent_extractor"] and records[0]["source_tables"]
    handoff = json.loads((tmp_path / "handoff.json").read_text())
    assert (
        handoff["proposal_only"]
        and handoff["credential_reference"] == "GOOGLE_DOCUMENT_AI_ACCESS_TOKEN"
    )
    raw = json.loads((tmp_path / "raw" / "000001_p_1.json").read_text())
    assert raw["request"]["page_id"] == "p/1" and raw["response"]["document"]


def test_failures_and_boundaries(tmp_path):
    adapter.validate_limits(1, 1, 180)
    intake = manifest(tmp_path)
    result = adapter.run_adapter(
        intake,
        tmp_path / "out.json",
        tmp_path / "handoff.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        "project",
        "us",
        "processor",
        "token",
        max_pdf_bytes=100,
        opener=lambda request, timeout: HTTPResponse({}),
    )
    assert result["review_items"] == 2
    records = json.loads((tmp_path / "out.json").read_text())
    assert all(item["review_status"] == "open_exception" for item in records)
    assert json.loads((tmp_path / "raw" / "000002_p2.json").read_text())["response"] == {}
    failed = adapter.run_adapter(
        intake,
        tmp_path / "failed-out.json",
        tmp_path / "failed-handoff.json",
        tmp_path / "failed-exceptions.json",
        tmp_path / "failed-raw",
        "project",
        "us",
        "processor",
        "",
    )
    assert failed["review_items"] == 2
    assert (
        json.loads((tmp_path / "failed-raw" / "000001_p_1.json").read_text())["error_type"]
        == "ValueError"
    )
    with pytest.raises(ValueError, match="configured limit"):
        adapter.run_adapter(
            intake,
            tmp_path / "x.json",
            tmp_path / "y.json",
            tmp_path / "z.json",
            tmp_path / "r",
            "project",
            "us",
            "processor",
            "token",
            max_pages=1,
        )
    with pytest.raises(ValueError, match="positive"):
        adapter.validate_limits(0, 1, 1)
    with pytest.raises(ValueError, match="at most"):
        adapter.validate_limits(1, 1, 301)
    with pytest.raises(ValueError, match="bytes"):
        adapter.validate_limits(1, 0, 1)
    outside = {"page_id": "p", "page_pdf": "../outside.pdf"}
    with pytest.raises(ValueError, match="escapes"):
        adapter.resolve_page(intake, outside, 1)
    with pytest.raises(ValueError, match="relative"):
        adapter.resolve_page(intake, {"page_pdf": str(tmp_path / "first.pdf")}, 1)
    with pytest.raises(ValueError, match="not readable"):
        adapter.resolve_page(intake, {"page_pdf": "missing.pdf"}, 1)
    with pytest.raises(ValueError, match="byte limit"):
        adapter.resolve_page(intake, {"page_pdf": "first.pdf"}, 1)
    with pytest.raises(ValueError, match="exists"):
        adapter.require_new_file(tmp_path / "out.json")
    directory = tmp_path / "raw"
    with pytest.raises(ValueError, match="new or empty"):
        adapter.require_new_directory(directory)
    with pytest.raises(ValueError, match="distinct"):
        adapter.run_adapter(
            intake,
            tmp_path / "same.json",
            tmp_path / "same.json",
            tmp_path / "other.json",
            tmp_path / "newraw",
            "project",
            "us",
            "processor",
            "token",
        )
    assert adapter.failed_record({"page_id": "p"}, "raw", "processor", "v")["engine_version"] == "v"
    with pytest.raises(ValueError, match="document object"):
        adapter.normalized_record(
            {"page_id": "p"}, tmp_path / "first.pdf", "raw", {}, "processor", None
        )
    assert (
        adapter.normalized_record(
            {"page_id": "p"},
            tmp_path / "first.pdf",
            "raw",
            {"document": {"pages": ["skip"]}},
            "processor",
            None,
        )["token_evidence"]
        == []
    )
    adapter.write_raw(tmp_path / "error.json", {}, error="Error")
    assert json.loads((tmp_path / "error.json").read_text())["error_type"] == "Error"


def test_resume_reuses_only_validated_successes_and_checkpoints(tmp_path):
    intake = manifest(tmp_path)
    raw = tmp_path / "raw"
    raw.mkdir()
    page_path = tmp_path / "first.pdf"
    adapter.write_raw(
        raw / "000001_p_1.json",
        {"page_id": "p/1", "page_sha256": adapter.sha256(page_path)},
        response=response(),
    )
    (raw / "000002_p2.json").write_text("bad-json")
    calls = []

    def opener(request, timeout):
        calls.append(request)
        return HTTPResponse(response())

    result = adapter.run_adapter(
        intake,
        tmp_path / "resume-out.json",
        tmp_path / "resume-handoff.json",
        tmp_path / "resume-exceptions.json",
        raw,
        "project",
        "us",
        "processor",
        "token",
        opener=opener,
        resume_raw_dir=raw,
    )
    assert result["pages"] == 2
    assert len(calls) == 1
    assert (raw / "000002_p2__retry1.json").is_file()
    checkpoint = json.loads((tmp_path / "document_ai_checkpoint.json").read_text())
    assert checkpoint["completed_pages"] == 2
    assert json.loads((tmp_path / "resume-out.json").read_text())[0]["page_id"] == "p/1"
    malformed = tmp_path / "malformed.json"
    malformed.write_text("not-json")
    assert adapter.resumable_success(malformed, {"page_id": "p/1"}, page_path) is None
    assert (
        adapter.resumable_success(
            raw / "000001_p_1.json",
            {"page_id": "p/1"},
            tmp_path / "second.pdf",
        )
        is None
    )
    assert adapter.retry_raw_path(raw, 1, {"page_id": "p/1"}).name.endswith("__retry1.json")
    assert adapter.retry_raw_path(raw, 2, {"page_id": "p2"}).name.endswith("__retry2.json")
    retry_raw = tmp_path / "retry-raw"
    retry_raw.mkdir()
    (retry_raw / "000001_p_1.json").write_text("bad-json")
    (retry_raw / "000001_p_1__retry1.json").write_text("reserved")
    assert adapter.retry_raw_path(retry_raw, 1, {"page_id": "p/1"}).name.endswith("__retry2.json")
    assert adapter.retry_raw_path(raw, 3, {"page_id": "p3"}).name.endswith("000003_p3.json")
    with pytest.raises(ValueError, match="not readable"):
        adapter.run_adapter(
            intake,
            tmp_path / "bad-resume-out.json",
            tmp_path / "bad-resume-handoff.json",
            tmp_path / "bad-resume-exceptions.json",
            tmp_path / "new-raw",
            "project",
            "us",
            "processor",
            "token",
            opener=opener,
            resume_raw_dir=tmp_path / "missing-raw",
        )
    assert adapter.failure_type(ValueError("Document AI HTTP 403")) == "document_ai_http_403"
    assert (
        adapter.failure_type(ValueError("Document AI transport failed"))
        == "document_ai_transport_failed"
    )
    assert (
        adapter.failure_type(json.JSONDecodeError("bad", "x", 0))
        == "document_ai_invalid_json_response"
    )
    with pytest.raises(ValueError, match="pages list"):
        adapter.load_manifest(write(tmp_path / "not-pages.json", {}))
    with pytest.raises(ValueError, match="requires"):
        adapter.load_manifest(write(tmp_path / "missing-fields.json", {"pages": [{}]}))


def test_main_enable_and_configuration(monkeypatch, capsys):
    monkeypatch.setattr(adapter, "load_project_env", lambda: None)
    monkeypatch.setattr(adapter, "env_bool", lambda name, default: False)
    monkeypatch.setattr(adapter, "env_value", lambda name, default: default)
    monkeypatch.setattr(adapter, "env_int", lambda name, default: default)
    monkeypatch.setattr(adapter, "env_float", lambda name, default: default)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "google_document_ai_adapter.py",
            "m",
            "--out",
            "o",
            "--adapter-out",
            "a",
            "--exceptions",
            "e",
            "--raw-dir",
            "r",
        ],
    )
    with pytest.raises(SystemExit, match="disabled"):
        adapter.main()
    monkeypatch.setattr(
        adapter,
        "run_adapter",
        lambda *args: {"pages": 1, "readings": 1, "review_items": 0, "provider_error_types": []},
    )
    monkeypatch.setattr(adapter.os, "environ", {"GOOGLE_DOCUMENT_AI_ACCESS_TOKEN": "token"})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "google_document_ai_adapter.py",
            "m",
            "--enable",
            "--project-id",
            "project",
            "--processor-id",
            "processor",
            "--out",
            "o",
            "--adapter-out",
            "a",
            "--exceptions",
            "e",
            "--raw-dir",
            "r",
        ],
    )
    adapter.main()
    assert json.loads(capsys.readouterr().out) == {
        "pages": 1,
        "readings": 1,
        "review_items": 0,
        "provider_error_types": [],
    }

    # Rule 9: a corroboration lane where every page failed corroborates nothing.
    # A real corpus failed 404 on all 18 pages and this command still exited 0,
    # so the run carried on believing it had independent evidence.
    monkeypatch.setattr(
        adapter,
        "run_adapter",
        lambda *args: {
            "pages": 18,
            "readings": 0,
            "review_items": 18,
            "provider_error_types": ["document_ai_http_404"],
        },
    )
    with pytest.raises(SystemExit, match="no independent reading .document_ai_http_404."):
        adapter.main()
    # A lane given nothing to read is the intake's problem, not a silent pass.
    monkeypatch.setattr(
        adapter,
        "run_adapter",
        lambda *args: {"pages": 0, "readings": 0, "review_items": 0, "provider_error_types": []},
    )
    adapter.main()
    assert json.loads(capsys.readouterr().out)["pages"] == 0
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "google_document_ai_adapter.py",
            "m",
            "--enable",
            "--credential-env",
            "bad",
            "--out",
            "o",
            "--adapter-out",
            "a",
            "--exceptions",
            "e",
            "--raw-dir",
            "r",
        ],
    )
    with pytest.raises(SystemExit, match="uppercase"):
        adapter.main()
    monkeypatch.setattr(
        adapter, "run_adapter", lambda *args: (_ for _ in ()).throw(ValueError("bad run"))
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "google_document_ai_adapter.py",
            "m",
            "--enable",
            "--project-id",
            "project",
            "--processor-id",
            "processor",
            "--out",
            "o",
            "--adapter-out",
            "a",
            "--exceptions",
            "e",
            "--raw-dir",
            "r",
        ],
    )
    with pytest.raises(SystemExit, match="bad run"):
        adapter.main()
    monkeypatch.setattr(
        adapter, "env_bool", lambda *args: (_ for _ in ()).throw(ValueError("bad env"))
    )
    with pytest.raises(SystemExit, match="bad env"):
        adapter.main()


class _RefreshingStub:
    """A token source that mints a new value each time it is refreshed."""

    def __init__(self):
        self.tokens = ["expired", "fresh"]
        self.refreshes = 0

    def __call__(self):
        return self.tokens[0]

    def refresh(self):
        self.refreshes += 1
        self.tokens.pop(0)
        return self.tokens[0]


def _unauthorized_then(responses):
    """Return an opener that answers 401 until the caller presents a new token."""

    def opener(request, timeout):
        if request.get_header("Authorization") == "Bearer expired":
            raise HTTPError("url", 401, "expired", {}, BytesIO())
        return HTTPResponse(responses())

    return opener


def test_an_expired_token_is_reminted_once_instead_of_losing_the_page():
    """A 60-minute ADC token expired mid-corpus and cost 223 pages to HTTP 401."""
    source = _RefreshingStub()
    value = adapter.request_with_refreshed_credential(
        "https://example.test", {"x": 1}, source, 2, _unauthorized_then(response)
    )
    assert value["document"]["text"].startswith("Amount")
    assert source.refreshes == 1


def test_a_second_unauthorized_stays_an_explicit_failure():
    """A refreshed token that is still refused is a real credential fault."""
    source = _RefreshingStub()
    with pytest.raises(ValueError, match="HTTP 401"):
        adapter.request_with_refreshed_credential(
            "https://example.test",
            {},
            source,
            1,
            lambda *args, **kwargs: (_ for _ in ()).throw(
                HTTPError("url", 401, "no", {}, BytesIO())
            ),
        )
    assert source.refreshes == 1


def test_a_fixed_token_string_is_never_refreshed():
    """A token exported by the run shell has no refresh path and must not gain one."""
    with pytest.raises(ValueError, match="HTTP 401"):
        adapter.request_with_refreshed_credential(
            "https://example.test",
            {},
            "exported",
            1,
            lambda *args, **kwargs: (_ for _ in ()).throw(
                HTTPError("url", 401, "no", {}, BytesIO())
            ),
        )


def test_a_non_credential_failure_is_not_retried_with_a_new_token():
    """Only 401 means expiry; a 403 is a permission fault a new token cannot fix."""
    source = _RefreshingStub()
    with pytest.raises(ValueError, match="HTTP 403"):
        adapter.request_with_refreshed_credential(
            "https://example.test",
            {},
            source,
            1,
            lambda *args, **kwargs: (_ for _ in ()).throw(
                HTTPError("url", 403, "no", {}, BytesIO())
            ),
        )
    assert source.refreshes == 0
