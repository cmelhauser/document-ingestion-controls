"""Tests for checkpointed layout-aware extraction; no network calls occur."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

layout_extract = importlib.import_module("layout_aware_extract")


def write(path, value):
    path.write_text(json.dumps(value) if not isinstance(value, str) else value)
    return path


def layout(tmp_path):
    return write(
        tmp_path / "layout.json",
        {
            "layout_id": "client-layout-v1",
            "columns": [
                {"key": "issuer_brand", "display_label": "Issuer Brand"},
                {"key": "project", "display_label": "Project"},
            ],
        },
    )


def manifest(tmp_path):
    write(tmp_path / "page.pdf", "%PDF-page")
    return write(
        tmp_path / "ingestion_manifest.json",
        {"pages": [{"page_id": "page-1", "page_pdf": "page.pdf"}]},
    )


def parsed(flags=None):
    return {
        "rows": [
            {
                "issuer_brand": "Brand A",
                "project": None,
                "row_number": 1,
                "source_evidence": "Brand A",
                "has_handwriting": False,
                "review_flags": flags or [],
                "unmapped_visible_values": [],
            }
        ],
        "page_notes": ["low contrast"],
    }


class Response:
    def __init__(self, payload):
        self.output_text = json.dumps(payload)

    def model_dump(self, mode="json"):
        return {"id": "response-1"}


class Responses:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class Client:
    def __init__(self, responses):
        self.responses = Responses(responses)


def test_layout_and_schema_boundaries(tmp_path):
    value = layout_extract.load_layout(layout(tmp_path))
    assert value["columns"][0]["key"] == "issuer_brand"
    assert (
        layout_extract.extraction_schema(value)["properties"]["rows"]["items"]["properties"][
            "project"
        ]["anyOf"][1]["type"]
        == "null"
    )
    assert "Issuer Brand" in layout_extract.instructions(value)
    assert layout_extract.review_item({"page_id": "p"}, "field", "reason")["priority"] == "high"
    with pytest.raises(ValueError, match="Layout requires"):
        layout_extract.load_layout(write(tmp_path / "bad.json", {"columns": []}))
    with pytest.raises(ValueError, match="unique"):
        layout_extract.load_layout(
            write(
                tmp_path / "duplicate.json",
                {
                    "layout_id": "x",
                    "columns": [
                        {"key": "same", "display_label": "A"},
                        {"key": "same", "display_label": "B"},
                    ],
                },
            )
        )
    with pytest.raises(ValueError, match="exactly one"):
        layout_extract.selected_page({"pages": []}, "missing")


def test_page_extracts_review_only_rows_and_retains_raw_response(tmp_path):
    client = Client([Response(parsed(["unclear row boundary"]))])
    result = layout_extract.run_page(
        manifest(tmp_path),
        layout(tmp_path),
        "page-1",
        tmp_path / "page-result.json",
        tmp_path / "raw",
        "test-model",
        client,
        "medium",
        10_000_000,
        "OPENAI_API_KEY",
        max_retries=0,
    )
    record = result["record"]
    assert record["row_proposals"][0]["issuer_brand"] == "Brand A"
    assert record["requires_independent_consensus"] is True
    assert [item["reason"] for item in result["review_items"]] == [
        "layout_aware_rows_require_client_review",
        "layout_aware:unclear row boundary",
    ]
    raw = json.loads((tmp_path / "raw" / "response.json").read_text())
    assert raw["response"]["id"] == "response-1"
    assert client.responses.calls[0]["input"][0]["content"][1]["type"] == "input_file"


def test_page_preserves_provider_failure_as_review_work(tmp_path):
    result = layout_extract.run_page(
        manifest(tmp_path),
        layout(tmp_path),
        "page-1",
        tmp_path / "failure.json",
        tmp_path / "raw-failure",
        "test-model",
        Client([RuntimeError("offline")]),
        "medium",
        10_000_000,
        "OPENAI_API_KEY",
        max_retries=0,
    )
    assert result["record"]["review_status"] == "open_exception"
    assert result["review_items"][0]["reason"] == "layout_aware_provider_or_schema_failure"
    assert (
        json.loads((tmp_path / "raw-failure" / "response.json").read_text())["error_type"]
        == "RuntimeError"
    )


def test_page_request_supports_native_text():
    class Client:
        def __init__(self):
            self.responses = self
            self.call = None

        def create(self, **kwargs):
            self.call = kwargs
            return Response(parsed())

    client = Client()
    layout_extract.page_request(
        client,
        Path("unused.pdf"),
        {"layout_id": "x", "columns": [{"key": "name", "display_label": "Name"}]},
        "model",
        "medium",
        "text",
        "visible text",
    )
    assert client.call["input"][0]["content"][0]["type"] == "input_text"


def test_combine_requires_new_distinct_outputs_and_retains_every_page(tmp_path):
    first = {
        "record": {"engine": "openai/test", "document_id": "one"},
        "review_items": [],
        "credential_reference": "OPENAI_API_KEY",
    }
    second = {
        "record": {"engine": "openai/test", "document_id": "two"},
        "review_items": [layout_extract.review_item({"page_id": "two"}, "rows", "review")],
        "credential_reference": "OPENAI_API_KEY",
    }
    first_path, second_path = (
        write(tmp_path / "one.json", first),
        write(tmp_path / "two.json", second),
    )
    result = layout_extract.combine(
        layout(tmp_path),
        [first_path, second_path],
        tmp_path / "all.json",
        tmp_path / "exceptions.json",
        tmp_path / "adapter.json",
    )
    assert result == {"records": 2, "review_items": 1}
    assert len(json.loads((tmp_path / "all.json").read_text())) == 2
    assert json.loads((tmp_path / "adapter.json").read_text())["proposal_only"] is True
    with pytest.raises(ValueError, match="distinct and new"):
        layout_extract.combine(
            layout(tmp_path),
            [first_path],
            tmp_path / "all.json",
            tmp_path / "new.json",
            tmp_path / "new-adapter.json",
        )


def test_main_runs_each_checkpointed_command_and_reports_invalid_limits(monkeypatch, capsys):
    monkeypatch.setattr(layout_extract, "load_project_env", lambda: None)
    monkeypatch.setattr(layout_extract, "combine", lambda *args: {"records": 1, "review_items": 2})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "layout_aware_extract.py",
            "combine",
            "layout.json",
            "input.json",
            "--out",
            "out.json",
            "--exceptions",
            "exc.json",
            "--adapter-out",
            "adapter.json",
        ],
    )
    layout_extract.main()
    assert json.loads(capsys.readouterr().out) == {"records": 1, "review_items": 2}

    captured = {}
    monkeypatch.setattr(layout_extract, "build_client", lambda *args: "client")

    def fake_run(*args):
        captured["args"] = args
        return {"review_items": ["x"]}

    monkeypatch.setattr(layout_extract, "run_page", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "layout_aware_extract.py",
            "page",
            "manifest.json",
            "layout.json",
            "--page-id",
            "p",
            "--out",
            "out.json",
            "--raw-dir",
            "raw",
        ],
    )
    layout_extract.main()
    assert json.loads(capsys.readouterr().out) == {"review_items": 1}
    assert captured["args"][6] == "client"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "layout_aware_extract.py",
            "page",
            "manifest.json",
            "layout.json",
            "--page-id",
            "p",
            "--out",
            "out.json",
            "--raw-dir",
            "raw",
            "--max-pdf-bytes",
            "0",
        ],
    )
    with pytest.raises(SystemExit, match="max PDF bytes"):
        layout_extract.main()


def test_page_keeps_raw_response_when_schema_decode_fails(tmp_path):
    result = layout_extract.run_page(
        manifest(tmp_path),
        layout(tmp_path),
        "page-1",
        tmp_path / "schema-failure.json",
        tmp_path / "raw-schema-failure",
        "test-model",
        Client([Response("not-an-object")]),
        "medium",
        10_000_000,
        "OPENAI_API_KEY",
    )
    assert result["record"]["review_status"] == "open_exception"
    assert "response" in json.loads((tmp_path / "raw-schema-failure" / "response.json").read_text())
