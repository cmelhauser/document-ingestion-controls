"""Tests for the Gemini-on-Vertex adapter; no Google Cloud call occurs."""

import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

adapter = importlib.import_module("google_genai_adapter")


def write(path, value):
    path.write_text(json.dumps(value) if not isinstance(value, str) else value)
    return path


def manifest(tmp_path):
    write(tmp_path / "first.pdf", "%PDF-one")
    write(tmp_path / "second.pdf", "%PDF-two")
    write(tmp_path / "first.txt", "Invoice INV-7 total $11")
    return write(
        tmp_path / "manifest.json",
        {
            "pages": [
                {
                    "page_id": "p-1",
                    "page_pdf": "first.pdf",
                    "text_file": "first.txt",
                    "classification_status": "rule_classified",
                    "document_type": "commercial_invoice",
                },
                {"page_id": "p/2", "page_pdf": "second.pdf", "document_type": "unknown"},
            ]
        },
    )


def extracted(document_type="commercial_invoice", handwriting=False, flags=None):
    return {
        "document_type": document_type,
        "has_handwriting": handwriting,
        "handwriting_regions": (
            [
                {
                    "region_id": "note-1",
                    "label": "note",
                    "left": 0.1,
                    "top": 0.1,
                    "right": 0.2,
                    "bottom": 0.2,
                }
            ]
            if handwriting
            else []
        ),
        "handwriting_readings": (
            [
                {
                    "region_id": "note-1",
                    "label": "note",
                    "semantic_type": "note",
                    "content_class": "text",
                    "value": "note",
                    "evidence_text": "note",
                    "model_confidence": 0.76,
                    "financial_amendment": False,
                }
            ]
            if handwriting
            else []
        ),
        "header": [
            {
                "name": "invoice_number",
                "value": "INV-7",
                "source": "printed",
                "model_confidence": 0.87,
            },
            {"name": "total_amount", "value": None, "source": "not_present"},
            "skip",
        ],
        "source_labelled_fields": [],
        "lines": [
            {"line_number": 1, "item_code": "SKU", "description": "Widget"},
            {"line_number": None},
            "skip",
        ],
        "review_flags": flags or [],
    }


class Part:
    @staticmethod
    def from_bytes(**kwargs):
        return {"part": kwargs}


class GenerateContentConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class HttpRetryOptions:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class HttpOptions:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class Types:
    Part = Part
    GenerateContentConfig = GenerateContentConfig
    HttpRetryOptions = HttpRetryOptions
    HttpOptions = HttpOptions


class Response:
    def __init__(self, value=None, parsed=None, raw=None):
        self.text = value
        self.parsed = parsed
        self.raw = raw or {"response_id": "r-1"}

    def model_dump(self, mode="json"):
        return self.raw


class Models:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class Client:
    def __init__(self, responses):
        self.models = Models(responses)


def run(tmp_path, client):
    return adapter.run_adapter(
        manifest(tmp_path),
        tmp_path / "engine.json",
        tmp_path / "adapter.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        "gemini-test",
        client,
        project_id="project-12345",
        types=Types,
        max_retries=0,
    )


def test_adapter_routes_text_and_pdf_with_review_and_provenance(tmp_path):
    first_response = extracted()
    first_response["source_labelled_fields"] = [
        {
            "source_label": "Territory",
            "value": "Northeast",
            "source": "printed",
            "evidence_text": "Territory: Northeast",
            "model_confidence": 0.87,
        }
    ]
    client = Client(
        [
            Response(parsed=first_response),
            Response(
                json.dumps(extracted("receipt", True, [{"field": "total", "reason": "blurred"}]))
            ),
        ]
    )
    result = run(tmp_path, client)
    records = json.loads((tmp_path / "engine.json").read_text())
    exceptions = json.loads((tmp_path / "exceptions.json").read_text())["exceptions"]
    assert result == {"pages": 2, "review_items": 4, "engine": "google_genai_vertex/gemini-test"}
    assert records[0]["google_genai_input_mode"] == "text"
    assert records[1]["google_genai_input_mode"] == "pdf"
    assert records[0]["header"]["invoice_number"]["value"] == "INV-7"
    assert records[0]["header"]["invoice_number"]["model_confidence"] == 0.87
    extension = next(iter(records[0]["source_labelled_fields"].values()))
    assert extension["source_label"]["value"] == "Territory"
    assert extension["observed_value"]["value"] == "Northeast"
    assert records[0]["model_confidences"][0] == {
        "field": "header.invoice_number",
        "value": 0.87,
    }
    assert records[1]["handwriting_regions"][0]["label"] == "note"
    assert records[1]["handwriting_readings"][0]["value"] == "note"
    assert len(records[0]["lines"]) == 1
    assert {item["reason"] for item in exceptions} == {
        "google_genai_review_flag:blurred",
        "google_genai_document_type_proposal",
        "google_genai_handwriting_detected_requires_independent_htr",
        "google_genai_handwriting_region_proposal_requires_independent_htr",
    }
    first, second = client.models.calls
    assert first["contents"][1].startswith("Retained native text")
    assert second["contents"][1]["part"]["mime_type"] == "application/pdf"
    assert first["config"].kwargs["response_json_schema"] == adapter.vertex_response_schema(
        adapter.EXTRACTION_SCHEMA
    )
    handoff = json.loads((tmp_path / "adapter.json").read_text())
    assert handoff["schema_version"] == "independent_extraction_handoff_v1"
    assert handoff["provider"] == "google" and handoff["lane"] == "extraction"
    assert records[0]["raw_response_sha256"] and handoff["records_sha256"]
    assert handoff["authentication"] == "application_default_credentials"
    assert handoff["model_configuration"]["project_id"] == "project-12345"
    # Unset here, but the key must exist: `consensus.py` compares lanes on this
    # field, and an absent one is not the same claim as a declared absence.
    assert handoff["corpus_context"] is None
    raw = json.loads((tmp_path / "raw" / "000001_p-1.json").read_text())
    assert raw["request"]["input_mode"] == "text" and raw["response"]["response_id"] == "r-1"


def test_adapter_retains_schema_and_provider_failures(tmp_path):
    client = Client([Response("[]"), RuntimeError("offline")])
    result = adapter.run_adapter(
        manifest(tmp_path),
        tmp_path / "engine.json",
        tmp_path / "adapter.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        "gemini-test",
        client,
        input_mode="pdf",
        project_id="project-12345",
        types=Types,
        max_retries=0,
    )
    assert result["review_items"] == 2
    records = json.loads((tmp_path / "engine.json").read_text())
    assert all(record["review_status"] == "open_exception" for record in records)
    raw = json.loads((tmp_path / "raw" / "000002_p_2.json").read_text())
    assert raw["error_type"] == "RuntimeError"


def test_adapter_reuses_content_addressed_cache(tmp_path):
    cache = tmp_path / "cache"
    client = Client([Response(json.dumps(extracted())), Response(json.dumps(extracted()))])
    adapter.run_adapter(
        manifest(tmp_path),
        tmp_path / "first-engine.json",
        tmp_path / "first-adapter.json",
        tmp_path / "first-exceptions.json",
        tmp_path / "first-raw",
        "gemini-test",
        client,
        input_mode="pdf",
        project_id="project-12345",
        types=Types,
        max_retries=0,
        cache_dir=cache,
    )
    adapter.run_adapter(
        manifest(tmp_path),
        tmp_path / "second-engine.json",
        tmp_path / "second-adapter.json",
        tmp_path / "second-exceptions.json",
        tmp_path / "second-raw",
        "gemini-test",
        Client([]),
        input_mode="pdf",
        project_id="project-12345",
        types=Types,
        max_retries=0,
        cache_dir=cache,
    )
    assert len(json.loads((tmp_path / "second-engine.json").read_text())) == 2


def test_helpers_limits_sdk_and_cli(monkeypatch, tmp_path, capsys):
    adapter.validate_vertex_configuration("project-12345", "us-central1", 1, 0)
    for values, message in [
        (("bad", "us-central1", 1, 0), "project"),
        (("project-12345", "us", 1, 0), "location"),
        (("project-12345", "us-central1", 0, 0), "timeout"),
        (("project-12345", "us-central1", 1, -1), "retries"),
    ]:
        with pytest.raises(ValueError, match=message):
            adapter.validate_vertex_configuration(*values)

    class GenAI:
        called = None

        @staticmethod
        def Client(**kwargs):
            GenAI.called = kwargs
            return kwargs

    client = adapter.build_client("project-12345", "us-central1", 2, 3, (GenAI, Types))
    assert client["vertexai"] and client["http_options"].kwargs["timeout"] == 2000
    assert client["http_options"].kwargs["retry_options"].kwargs["attempts"] == 4
    real_sdk = adapter.load_sdk()
    assert real_sdk[0].Client and real_sdk[1].GenerateContentConfig

    class NoArgumentDump:
        def model_dump(self):
            return {"fallback": True}

    class JsonResponse:
        def to_json(self):
            return '{"ok": true}'

    assert adapter.response_payload(JsonResponse()) == {"ok": True}
    assert adapter.response_payload(NoArgumentDump()) == {"fallback": True}
    assert adapter.response_payload("raw") == "raw"
    with pytest.raises(ValueError, match="structured output"):
        adapter.response_json(object())
    with pytest.raises(ValueError, match="object"):
        adapter.response_json(Response("[]"))
    assert adapter.response_json(Response(parsed={"ok": True})) == {"ok": True}
    disagreement = adapter.model_exceptions(
        {"page_id": "p", "document_type": "receipt", "classification_status": "rule_classified"},
        {
            "provider_review_flags": [],
            "model_document_type": "commercial_invoice",
            "has_handwriting": False,
        },
    )
    assert disagreement[0]["reason"].endswith("disagrees_with_intake_rule")
    with pytest.raises(ValueError, match="project ID"):
        adapter.run_adapter("x", "o", "a", "e", "r", "m", Client([]), project_id="", types=Types)
    intake = manifest(tmp_path)
    with pytest.raises(ValueError, match="configured limit"):
        adapter.run_adapter(
            intake,
            tmp_path / "o",
            tmp_path / "a",
            tmp_path / "e",
            tmp_path / "r",
            "m",
            Client([]),
            max_pages=1,
            project_id="project-12345",
            types=Types,
        )
    with pytest.raises(ValueError, match="must be distinct"):
        adapter.run_adapter(
            intake,
            tmp_path / "same",
            tmp_path / "same",
            tmp_path / "e2",
            tmp_path / "r2",
            "m",
            Client([]),
            project_id="project-12345",
            types=Types,
        )
    monkeypatch.setattr(adapter, "load_sdk", lambda: (GenAI, Types))
    bad_manifest = write(tmp_path / "missing.json", "[]")
    with pytest.raises(ValueError, match="pages list"):
        adapter.run_adapter(
            bad_manifest,
            tmp_path / "out-other",
            tmp_path / "adapter-other",
            tmp_path / "exceptions-other",
            tmp_path / "raw-other",
            "m",
            Client([]),
            project_id="project-12345",
        )

    monkeypatch.setattr(adapter, "load_project_env", lambda: None)
    monkeypatch.setenv("GOOGLE_VERTEX_AI_MODEL", "gemini-2.5-flash")
    monkeypatch.setattr(adapter, "load_sdk", lambda: (GenAI, Types))
    monkeypatch.setattr(adapter, "build_client", lambda *args: "client")
    called = {}

    def fake_run(*args, **kwargs):
        called["args"] = args
        called["kwargs"] = kwargs
        return {"pages": 1}

    monkeypatch.setattr(adapter, "run_adapter", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            adapter.__file__,
            "m",
            "--out",
            "o",
            "--adapter-out",
            "a",
            "--exceptions",
            "e",
            "--raw-dir",
            "r",
            "--project-id",
            "project-12345",
        ],
    )
    adapter.main()
    assert called["args"][5] == "gemini-2.5-flash"
    # The whole model ceiling by default, so a wide page is not truncated away.
    assert called["kwargs"]["max_output_tokens"] == 65536
    assert json.loads(capsys.readouterr().out) == {"pages": 1}
    monkeypatch.setattr(
        adapter, "build_client", lambda *args: (_ for _ in ()).throw(ValueError("bad"))
    )
    with pytest.raises(SystemExit, match="Google Gen AI adapter failed"):
        adapter.main()


def test_responses_compatibility_client_translates_text_and_pdf(monkeypatch):
    response = Response('{"ok": true}')
    client = Client([response])
    compat = adapter.VertexResponsesClient(client, Types)
    result = compat.responses.create(
        model="gemini-test",
        instructions="stay grounded",
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "packet"},
                    {
                        "type": "input_file",
                        "filename": "page.pdf",
                        "file_data": "data:application/pdf;base64," + "JVBERg==",
                    },
                ],
            }
        ],
        text={"format": {"schema": {"type": "object"}}},
        reasoning={"effort": "medium"},
    )
    assert result.output_text == '{"ok": true}'
    assert result.model_dump() == {"response_id": "r-1"}
    call = client.models.calls[0]
    assert call["contents"][0] == "packet"
    assert call["contents"][1]["part"]["data"] == b"%PDF"
    assert call["config"].kwargs["system_instruction"] == "stay grounded"
    assert call["config"].kwargs["max_output_tokens"] == 8192
    with pytest.raises(ValueError, match="max output"):
        adapter.VertexResponsesClient(client, Types, 0)
    with pytest.raises(ValueError, match="PDF"):
        compat.create(
            model="m",
            instructions="i",
            input=[{"content": [{"type": "input_file", "file_data": "bad"}]}],
            text={"format": {"schema": {}}},
        )
    with pytest.raises(ValueError, match="JSON schema"):
        compat.create(model="m", instructions="i", input=[], text={})
    with pytest.raises(ValueError, match="unsupported"):
        compat.create(
            model="m",
            instructions="i",
            input=[{"content": [{"type": "image"}]}],
            text={"format": {"schema": {}}},
        )

    monkeypatch.setattr(adapter, "build_client", lambda *args: client)
    monkeypatch.setattr(adapter, "load_sdk", lambda: (object(), Types))
    assert isinstance(
        adapter.build_responses_client("project-12345", "us-central1"),
        adapter.VertexResponsesClient,
    )
    monkeypatch.setattr(
        adapter, "load_project_env", lambda: (_ for _ in ()).throw(ValueError("bad env"))
    )
    with pytest.raises(SystemExit, match="Google Gen AI adapter failed"):
        adapter.main()


def test_translation_removes_the_keywords_vertex_refuses():
    source = {
        "type": "object",
        "additionalProperties": False,
        "required": ["b", "a"],
        "properties": {
            "a": {"type": "string", "minLength": 1},
            "b": {"type": "object", "additionalProperties": False, "properties": {}},
        },
    }
    assert adapter.vertex_response_schema(source) == {
        "type": "object",
        # Sorted, so an equal schema hashes equally -- membership is untouched.
        "required": ["a", "b"],
        "properties": {"a": {"type": "string"}, "b": {"type": "object", "properties": {}}},
    }


def test_a_nullable_union_becomes_the_flag_vertex_understands():
    source = {
        "properties": {
            "one": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "kept"},
            "many": {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]},
            "nested": {
                "anyOf": [
                    {"type": "object", "additionalProperties": False, "properties": {}},
                    {"type": "null"},
                ]
            },
        }
    }
    assert adapter.vertex_response_schema(source)["properties"] == {
        "one": {"type": "string", "description": "kept", "nullable": True},
        # More than one real branch stays a union; only the null arm is folded in.
        "many": {"anyOf": [{"type": "string"}, {"type": "integer"}], "nullable": True},
        "nested": {"type": "object", "properties": {}, "nullable": True},
    }


def test_a_union_of_nothing_but_null_is_refused():
    with pytest.raises(ValueError, match="only type"):
        adapter.vertex_response_schema({"anyOf": [{"type": "null"}]})


def test_an_over_long_enum_is_relaxed_and_a_short_one_is_kept():
    short = {"type": "string", "enum": ["a", "b"]}
    assert adapter.vertex_response_schema(short, enum_limit=2) == short
    relaxed = adapter.vertex_response_schema(
        {"type": "string", "description": "Field.", "enum": ["a", "b", "c"]}, enum_limit=2
    )
    # The constraint is gone; the vocabulary survives only as guidance.
    assert "enum" not in relaxed
    assert relaxed == {"type": "string", "description": "Field. One of: a, b, c"}


def test_a_vocabulary_too_large_to_carry_is_refused_rather_than_truncated():
    """Cutting the list would leave the model unable to name what it never saw."""
    with pytest.raises(ValueError, match="dropping permitted values"):
        adapter.vertex_response_schema({"type": "string", "enum": ["x" * 400] * 40}, enum_limit=2)


def test_scalars_and_lists_pass_through_untouched():
    assert adapter.vertex_response_schema([{"minLength": 1}, "text", 3, None]) == [
        {},
        "text",
        3,
        None,
    ]


def test_the_extraction_schema_translates_to_something_vertex_will_serve():
    """The measured cause of the refusal, on the schema this lane actually sends."""
    translated = adapter.vertex_response_schema(adapter.EXTRACTION_SCHEMA)
    rendered = json.dumps(translated)
    assert "additionalProperties" not in rendered and "minLength" not in rendered
    assert '"anyOf"' not in rendered
    # Exactly one enum is over the measured ceiling: the 158 header field names.
    assert adapter.relaxed_enum_paths(adapter.EXTRACTION_SCHEMA) == [
        "/properties/header/items/properties/name"
    ]
    name = translated["properties"]["header"]["items"]["properties"]["name"]
    assert "enum" not in name
    # Every permitted name still reaches the model, or the relaxation loses them.
    for field in adapter.HEADER_FIELDS:
        assert field in name["description"]
    # document_type is under the ceiling, so it stays enforced.
    assert len(translated["properties"]["document_type"]["enum"]) == 34


def test_the_extraction_schema_already_requires_every_property_it_declares():
    """So sorting `required` cannot change what any object demands."""

    def check(node):
        if isinstance(node, list):
            for item in node:
                check(item)
            return
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" and isinstance(node.get("properties"), dict):
            assert sorted(node.get("required", [])) == sorted(node["properties"])
        for value in node.values():
            check(value)

    check(adapter.EXTRACTION_SCHEMA)


def test_the_adapter_sends_the_translated_schema_and_records_the_relaxation(tmp_path):
    client = Client([Response(parsed=extracted()), Response(parsed=extracted())])
    adapter.run_adapter(
        manifest(tmp_path),
        tmp_path / "engine.json",
        tmp_path / "adapter.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        "gemini-test",
        client,
        project_id="project-12345",
        types=Types,
        max_retries=0,
        max_output_tokens=65536,
    )
    sent = client.models.calls[0]["config"].kwargs["response_json_schema"]
    assert sent == adapter.vertex_response_schema(adapter.EXTRACTION_SCHEMA)
    assert sent != adapter.EXTRACTION_SCHEMA
    assert client.models.calls[0]["config"].kwargs["max_output_tokens"] == 65536
    handoff = json.loads((tmp_path / "adapter.json").read_text())["response_schema"]
    assert handoff["dialect"] == "vertex_structured_output"
    assert handoff["enum_limit"] == adapter.VERTEX_ENUM_LIMIT
    # The one weakened field is named, not left invisible in a translated schema.
    assert handoff["relaxed_enum_paths"] == ["/properties/header/items/properties/name"]
    assert handoff["sent_sha256"] == adapter.schema_sha256(sent)
    assert handoff["sent_sha256"] != handoff["translated_from_sha256"]


def test_the_translated_schema_takes_part_in_the_cache_key(tmp_path, monkeypatch):
    """A changed translation must not replay a response the old one shaped."""
    cache = tmp_path / "cache"
    for run_index in range(2):
        client = Client([Response(parsed=extracted()), Response(parsed=extracted())])
        adapter.run_adapter(
            manifest(tmp_path),
            tmp_path / f"engine-{run_index}.json",
            tmp_path / f"adapter-{run_index}.json",
            tmp_path / f"exceptions-{run_index}.json",
            tmp_path / f"raw-{run_index}",
            "gemini-test",
            client,
            project_id="project-12345",
            types=Types,
            max_retries=0,
            cache_dir=cache,
        )
        # Second run replays: the schema, and so the key, is unchanged.
        assert len(client.models.calls) == (2 if run_index == 0 else 0)
    raw = json.loads((tmp_path / "raw-1" / "000001_p-1.json").read_text())
    assert raw["request"]["cache_hit"] is True
    first_key = raw["request"]["cache_key"]

    # A different translation is a different sent schema, so it must not replay.
    client = Client([Response(parsed=extracted()), Response(parsed=extracted())])
    monkeypatch.setattr(
        adapter, "vertex_response_schema", lambda schema, *args, **kwargs: {"type": "object"}
    )
    adapter.run_adapter(
        manifest(tmp_path),
        tmp_path / "engine-2.json",
        tmp_path / "adapter-2.json",
        tmp_path / "exceptions-2.json",
        tmp_path / "raw-2",
        "gemini-test",
        client,
        project_id="project-12345",
        types=Types,
        max_retries=0,
        cache_dir=cache,
    )
    assert len(client.models.calls) == 2
    replayed = json.loads((tmp_path / "raw-2" / "000001_p-1.json").read_text())
    assert replayed["request"]["cache_hit"] is False
    assert replayed["request"]["cache_key"] != first_key


def test_the_review_compatibility_client_also_translates(tmp_path):
    client = Client([Response("{}")])
    compatibility = adapter.VertexResponsesClient(client, Types, max_output_tokens=8192)
    compatibility.create(
        model="gemini-test",
        instructions="Review.",
        input=[{"content": [{"type": "input_text", "text": "page"}]}],
        text={"format": {"schema": {"type": "object", "additionalProperties": False}}},
    )
    sent = client.models.calls[0]["config"].kwargs["response_json_schema"]
    assert sent == {"type": "object"}


def test_a_vertex_lane_that_lost_its_nullability_is_also_an_exception(tmp_path):
    """The same control, on the adapter the defect was first measured against."""
    page = extracted()
    page["lines"] = [
        {
            "line_number": 1,
            "description": "LOUNGE C",
            **{name: "null" for name in list(adapter.LINE_FIELDS)[:20]},
        }
    ]
    page["header"] = []
    client = Client([Response(parsed=page), Response(parsed=page)])
    adapter.run_adapter(
        manifest(tmp_path),
        tmp_path / "engine.json",
        tmp_path / "adapter.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        "gemini-test",
        client,
        project_id="project-12345",
        types=Types,
        max_retries=0,
    )
    written = json.loads((tmp_path / "exceptions.json").read_text())
    assert written["summary"]["cells_spelled_null"] > 0
    assert [
        item
        for item in written["exceptions"]
        if item.get("reason") == "response_schema_nullability_not_honoured"
    ]
    records = json.loads((tmp_path / "engine.json").read_text())
    # Dropped from the reading, counted on the record.
    assert "item_code" not in (records[0]["lines"][0] if records[0]["lines"] else {})
    assert records[0]["cells_spelled_null"] > 0


def test_a_conditioned_vertex_lane_says_so_on_its_handoff(tmp_path, monkeypatch):
    """The conditioning was already in the prompt; the handoff did not say so.

    `extraction_instructions()` appends the corpus context and it is part of this
    lane's cache key, so a Vertex reading taken with `LLM_CORPUS_CONTEXT` set was
    genuinely conditioned. It recorded `corpus_context: None` regardless, and
    `consensus.py` compares lanes on exactly that field -- so a correctly
    conditioned Vertex lane was refused as conditioned differently from the
    OpenAI lane it was meant to pair with. The lane had done the right thing and
    could not prove it, which costs a re-read of the corpus to discover.
    """
    context = tmp_path / "corpus_context.txt"
    context.write_text("Ack numbers are printed top-right.\n", encoding="utf-8")
    monkeypatch.setenv("LLM_CORPUS_CONTEXT", str(context))
    client = Client([Response(parsed=extracted()), Response(parsed=extracted())])
    run(tmp_path, client)
    handoff = json.loads((tmp_path / "adapter.json").read_text())
    recorded = handoff["corpus_context"]
    assert recorded["file"] == "corpus_context.txt"
    assert recorded["sha256"] == hashlib.sha256(context.read_bytes()).hexdigest()
    # The text itself is not copied into the handoff; the hash is what a later
    # reader compares, and the file is retained under the run.
    assert "text" not in recorded
