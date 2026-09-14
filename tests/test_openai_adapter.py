"""Behavioral tests for the optional OpenAI Responses adapter; no network calls occur."""

import builtins
import hashlib
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

openai_adapter = importlib.import_module("openai_adapter")
extraction_schema = importlib.import_module("extraction_schema")
llm_response = importlib.import_module("llm_response")
run_io = importlib.import_module("run_io")


def write(path, content):
    path.write_text(content)
    return path


def manifest(tmp_path):
    first = write(tmp_path / "first.pdf", "%PDF-first")
    second = write(tmp_path / "second.pdf", "%PDF-second")
    text = write(tmp_path / "first.txt", "Invoice INV-7 total $11")
    value = {
        "pages": [
            {
                "page_id": "p-1",
                "page_pdf": first.name,
                "text_file": text.name,
                "classification_status": "rule_classified",
                "document_type": "commercial_invoice",
                "source_file": "client.pdf",
                "source_page_number": 1,
            },
            {
                "page_id": "p/2",
                "page_pdf": second.name,
                "classification_status": "unclassified",
                "source_file": "client.pdf",
                "source_page_number": 2,
            },
        ]
    }
    return write(tmp_path / "ingestion_manifest.json", json.dumps(value))


def extracted(document_type="commercial_invoice", handwriting=False, flags=None):
    return {
        "document_type": document_type,
        "has_handwriting": handwriting,
        "handwriting_regions": (
            [
                {
                    "region_id": "margin-note-1",
                    "label": "margin note",
                    "left": 0.1,
                    "top": 0.1,
                    "right": 0.2,
                    "bottom": 0.2,
                    "evidence_text": "note",
                }
            ]
            if handwriting
            else []
        ),
        "handwriting_readings": (
            [
                {
                    "region_id": "margin-note-1",
                    "label": "margin note",
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
                "evidence_text": "INV-7",
                "model_confidence": 0.87,
            },
            {
                "name": "seller_address",
                "value": "1 Harbor Way, Boston, MA 02110",
                "source": "printed",
                "evidence_text": "1 Harbor Way, Boston, MA 02110",
            },
            {"name": "total_amount", "value": None, "source": "not_present", "evidence_text": None},
            "ignored header",
        ],
        "lines": [
            "ignored line",
            {
                "line_number": 1,
                "item_code": "SKU",
                "description": "Widget",
                "quantity": "1",
                "uom": None,
                "unit_price": None,
                "extended_amount": "11",
            },
            {
                "line_number": None,
                "item_code": None,
                "description": None,
                "quantity": None,
                "uom": None,
                "unit_price": None,
                "extended_amount": None,
            },
        ],
        "review_flags": flags or [],
    }


class Response:
    def __init__(self, payload, raw=None):
        self.output_text = payload
        self.raw = raw or {"provider_id": "r-1"}

    def model_dump(self, mode="json"):
        return self.raw


class Responses:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Client:
    def __init__(self, responses):
        self.responses = Responses(responses)


def run(tmp_path, client, mode="auto", max_retries=2):
    return openai_adapter.run_adapter(
        manifest(tmp_path),
        tmp_path / "engine.json",
        tmp_path / "adapter.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        "test-model",
        client,
        mode,
        max_retries=max_retries,
    )


def test_adapter_routes_text_and_pdf_with_provenance_and_review(tmp_path):
    client = Client(
        [
            Response(json.dumps(extracted())),
            Response(
                json.dumps(
                    extracted(
                        "receipt",
                        handwriting=True,
                        flags=[
                            {"field": "total_amount", "reason": "blurred", "evidence_text": "$11"}
                        ],
                    )
                )
            ),
        ]
    )
    result = run(tmp_path, client)
    records = json.loads((tmp_path / "engine.json").read_text())
    exceptions = json.loads((tmp_path / "exceptions.json").read_text())["exceptions"]
    assert result["pages"] == 2 and result["review_items"] == 4
    assert records[0]["document_type"] == "commercial_invoice"
    assert records[1]["model_document_type"] == "receipt"
    assert records[0]["openai_input_mode"] == "text" and records[1]["openai_input_mode"] == "pdf"
    assert records[0]["header"]["invoice_number"]["value"] == "INV-7"
    assert records[0]["header"]["invoice_number"]["model_confidence"] == 0.87
    assert records[0]["model_confidences"][0] == {
        "field": "header.invoice_number",
        "value": 0.87,
    }
    assert records[0]["header"]["seller_address"]["value"] == "1 Harbor Way, Boston, MA 02110"
    assert records[1]["handwriting_regions"][0]["label"] == "margin note"
    assert records[1]["handwriting_readings"][0]["value"] == "note"
    assert len(records[0]["lines"]) == 1
    assert {item["reason"] for item in exceptions} == {
        "openai_review_flag:blurred",
        "openai_document_type_proposal",
        "openai_handwriting_detected_requires_independent_htr",
        "openai_handwriting_region_proposal_requires_independent_htr",
    }
    first_content = client.responses.calls[0]["input"][0]["content"]
    second_content = client.responses.calls[1]["input"][0]["content"]
    assert len(first_content) == 2 and "Retained native text" in first_content[1]["text"]
    assert second_content[1]["type"] == "input_file"
    raw = json.loads((tmp_path / "raw" / "000001_p-1.json").read_text())
    assert raw["request"]["input_mode"] == "text" and raw["request"]["reasoning_effort"] == "medium"
    assert raw["response"]["provider_id"] == "r-1"
    adapter = json.loads((tmp_path / "adapter.json").read_text())
    assert adapter["schema_version"] == "independent_extraction_handoff_v1"
    assert adapter["provider"] == "openai" and adapter["lane"] == "extraction"
    assert records[0]["raw_response_sha256"]
    assert adapter["records_sha256"]
    assert adapter["credential_reference"] == "OPENAI_API_KEY"
    assert adapter["model_configuration"] == {"model": "test-model", "reasoning_effort": "medium"}
    assert client.responses.calls[0]["reasoning"] == {"effort": "medium"}
    assert adapter["run_limits"] == {
        "max_pages": 500,
        "max_pdf_bytes": 10_000_000,
        "max_text_chars": 100_000,
    }
    assert adapter["transport"] == {
        "timeout_seconds": 120.0,
        "max_retries": 2,
        "retry_backoff_seconds": 1.0,
    }


def test_adapter_contract_preserves_visible_contact_and_sales_representative_fields(tmp_path):
    assert {
        "contact_name",
        "contact_email",
        "contact_phone",
        "sales_representative_name",
        "sales_representative_email",
        "sales_representative_phone",
        "sales_representative_address",
        "acknowledgement_number",
        "payment_reference",
        "tracking_number",
        "carrier_name",
    }.issubset(extraction_schema.HEADER_FIELDS)
    assert (
        "sales_representative_name"
        in extraction_schema.field_schema()["properties"]["name"]["enum"]
    )
    assert "source_labelled_fields" in extraction_schema.EXTRACTION_SCHEMA["required"]
    response = extracted()
    response["header"].extend(
        [
            {
                "name": "sales_representative_name",
                "value": "Alex Example",
                "source": "printed",
                "evidence_text": "Sales Rep: Alex Example",
                "model_confidence": 0.8,
            },
            {
                "name": "contact_email",
                "value": "alex@example.test",
                "source": "printed",
                "evidence_text": "alex@example.test",
                "model_confidence": 0.8,
            },
        ]
    )
    response["source_labelled_fields"] = [
        {
            "source_label": "Certificate Reference",
            "value": "CERT-7",
            "source": "printed",
            "evidence_text": "Certificate Reference: CERT-7",
            "model_confidence": 0.8,
        }
    ]
    page = tmp_path / "page.pdf"
    page.write_bytes(b"%PDF-test")
    raw = tmp_path / "raw.json"
    raw.write_text("{}")
    record = openai_adapter.normalized_record(
        {"page_id": "p", "source_file": "source.pdf", "source_page_number": 1},
        response,
        page,
        raw,
        "model",
        "text",
    )
    assert record["header"]["sales_representative_name"]["value"] == "Alex Example"
    assert record["header"]["contact_email"]["value"] == "alex@example.test"
    extension = next(iter(record["source_labelled_fields"].values()))
    assert extension["source_label"]["value"] == "Certificate Reference"
    assert extension["observed_value"]["value"] == "CERT-7"


def test_source_labelled_extension_rejects_malformed_or_unusable_values():
    assert (
        extraction_schema.normalized_source_labelled_fields(
            {
                "source_labelled_fields": [
                    "not-an-object",
                    {"source_label": " "},
                    {"source_label": "Bad source", "value": "x", "source": "untrusted"},
                ]
            },
            [],
        )
        == {}
    )


def test_adapter_preserves_provider_and_schema_failures(tmp_path):
    client = Client([Response("not-json", {"provider_id": "schema"}), RuntimeError("network")])
    result = run(tmp_path, client, "pdf", max_retries=0)
    records = json.loads((tmp_path / "engine.json").read_text())
    exceptions = json.loads((tmp_path / "exceptions.json").read_text())["exceptions"]
    assert result["review_items"] == 2 and all(
        r["review_status"] == "open_exception" for r in records
    )
    assert records[0]["openai_input_mode"] == "pdf"
    assert (
        json.loads((tmp_path / "raw" / "000001_p-1.json").read_text())["response"]["provider_id"]
        == "schema"
    )
    assert (
        json.loads((tmp_path / "raw" / "000002_p_2.json").read_text())["error_type"]
        == "RuntimeError"
    )
    assert {item["reason"] for item in exceptions} == {"openai_provider_or_schema_failure"}
    assert records[0]["raw_response"] != records[1]["raw_response"]


def test_adapter_reuses_content_addressed_cache(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    manifest_path = manifest(source)
    cache = tmp_path / "cache"
    openai_adapter.run_adapter(
        manifest_path,
        source / "first-engine.json",
        source / "first-adapter.json",
        source / "first-exceptions.json",
        source / "first-raw",
        "test-model",
        Client([Response(json.dumps(extracted())), Response(json.dumps(extracted()))]),
        "pdf",
        cache_dir=cache,
        max_retries=0,
    )
    openai_adapter.run_adapter(
        manifest_path,
        source / "second-engine.json",
        source / "second-adapter.json",
        source / "second-exceptions.json",
        source / "second-raw",
        "test-model",
        Client([]),
        "pdf",
        cache_dir=cache,
        max_retries=0,
    )
    assert len(json.loads((source / "second-engine.json").read_text())) == 2


def test_adapter_helpers_and_boundaries(monkeypatch, tmp_path):
    assert "handwriting_readings" in extraction_schema.EXTRACTION_SCHEMA["required"]
    monkeypatch.delenv("LLM_DOCUMENT_FAMILY", raising=False)
    assert extraction_schema.extraction_instructions().startswith(extraction_schema.INSTRUCTIONS)
    monkeypatch.setenv("LLM_DOCUMENT_FAMILY", "commission_statement")
    assert "commission_statement" in extraction_schema.extraction_instructions()
    monkeypatch.setenv("LLM_DOCUMENT_FAMILY", "unsupported_family")
    with pytest.raises(ValueError, match="LLM_DOCUMENT_FAMILY"):
        extraction_schema.extraction_instructions()
    page = {"page_id": "page/a", "page_pdf": "missing.pdf"}
    assert run_io.safe_label("page/a") == "page_a"
    assert run_io.safe_label("!!!") == "page"
    assert extraction_schema.extracted_field({"value": None, "source": "printed"}) is None
    assert extraction_schema.extracted_field({"value": "x", "source": "not_present"}) is None
    assert (
        extraction_schema.extracted_field({"value": "x", "source": "handwritten"})["source"]
        == "handwritten"
    )
    assert llm_response.choose_input_mode(page, "text", "pdf") == "pdf"
    assert llm_response.choose_input_mode(page, "text", "text") == "text"
    with pytest.raises(ValueError, match="native text"):
        llm_response.choose_input_mode(page, None, "text")
    with pytest.raises(ValueError, match="input mode"):
        llm_response.choose_input_mode(page, "x", "other")
    assert (
        llm_response.choose_input_mode(
            {**page, "classification_status": "rule_classified"}, "x", "auto"
        )
        == "text"
    )
    assert llm_response.choose_input_mode(page, "x", "auto") == "pdf"
    absent = {**page, "text_file": "none.txt"}
    assert run_io.resolve_text(tmp_path / "m.json", absent, 100) is None
    empty = write(tmp_path / "empty.txt", "  ")
    assert run_io.resolve_text(tmp_path / "m.json", {**page, "text_file": empty.name}, 100) is None
    text = write(tmp_path / "text.txt", "hello")
    assert (
        run_io.resolve_text(tmp_path / "m.json", {**page, "text_file": text.name}, 100) == "hello"
    )
    with pytest.raises(ValueError, match="character limit"):
        run_io.resolve_text(tmp_path / "m.json", {**page, "text_file": text.name}, 1)
    with pytest.raises(ValueError, match="must be relative"):
        run_io.resolve_manifest_file(tmp_path / "m.json", str(text))
    with pytest.raises(ValueError, match="escapes"):
        run_io.resolve_manifest_file(tmp_path / "m.json", "../outside.txt")
    with pytest.raises(ValueError, match="not readable"):
        run_io.resolve_page(tmp_path / "m.json", page, 100)
    with pytest.raises(ValueError, match="pages list"):
        run_io.load_manifest(write(tmp_path / "bad.json", "[]"))
    with pytest.raises(ValueError, match="requires"):
        run_io.load_manifest(write(tmp_path / "bad-pages.json", '{"pages":[{}]}'))
    good = write(tmp_path / "p.pdf", "%PDF")
    assert run_io.resolve_page(tmp_path / "m.json", {**page, "page_pdf": good.name}, 100) == good
    with pytest.raises(ValueError, match="byte limit"):
        run_io.resolve_page(tmp_path / "m.json", {**page, "page_pdf": good.name}, 1)
    assert len(run_io.sha256(good)) == 64
    out = tmp_path / "out.json"
    assert run_io.empty_output_path(out) == out
    write(out, "x")
    with pytest.raises(ValueError, match="already exists"):
        run_io.empty_output_path(out)
    raw = tmp_path / "raw"
    assert run_io.empty_output_directory(raw) == raw
    write(raw / "x", "x")
    with pytest.raises(ValueError, match="new or empty"):
        run_io.empty_output_directory(raw)
    write(tmp_path / "not-dir", "x")
    with pytest.raises(ValueError, match="new or empty"):
        run_io.empty_output_directory(tmp_path / "not-dir")

    class NoArgumentDump:
        def model_dump(self):
            return {"fallback": True}

    class Dictionary:
        def to_dict(self):
            return {"dictionary": True}

    assert llm_response.response_payload(NoArgumentDump()) == {"fallback": True}
    assert llm_response.response_payload(Dictionary()) == {"dictionary": True}
    assert llm_response.response_payload("raw") == "raw"
    with pytest.raises(ValueError, match="structured output text"):
        llm_response.response_json(object())
    with pytest.raises(ValueError, match="object"):
        llm_response.response_json(Response("[]"))
    with pytest.raises(ValueError, match="uppercase"):
        openai_adapter.build_client("bad")
    monkeypatch.delenv("MISSING_OPENAI", raising=False)
    with pytest.raises(ValueError, match="not set"):
        openai_adapter.build_client("MISSING_OPENAI")
    with pytest.raises(ValueError, match="timeout"):
        openai_adapter.build_client("MISSING_OPENAI", 0)
    with pytest.raises(ValueError, match="retries"):
        openai_adapter.build_client("MISSING_OPENAI", max_retries=-1)
    monkeypatch.setenv("MISSING_OPENAI", "test")
    real_import = builtins.__import__

    def missing_openai(name, *args, **kwargs):
        if name == "openai":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_openai)
    with pytest.raises(ValueError, match="not installed"):
        openai_adapter.build_client("MISSING_OPENAI")
    monkeypatch.setattr(builtins, "__import__", real_import)
    client = openai_adapter.build_client("MISSING_OPENAI", 15, 1)
    assert client.api_key == "test" and client.max_retries == 1
    client.close()
    for values, message in [
        ((0, 1, 1), "max pages"),
        ((1, 0, 1), "max PDF"),
        ((1, 1, 0), "max text"),
    ]:
        with pytest.raises(ValueError, match=message):
            extraction_schema.validate_run_limits(*values)
    openai_adapter.validate_reasoning_effort("none")
    openai_adapter.validate_reasoning_effort("max")
    region_schema = extraction_schema.EXTRACTION_SCHEMA["properties"]["handwriting_regions"][
        "items"
    ]
    assert "region_id" in region_schema["required"]
    assert region_schema["properties"]["region_id"]["minLength"] == 1
    with pytest.raises(ValueError, match="reasoning effort"):
        openai_adapter.validate_reasoning_effort("adaptive")


def test_adapter_limits_and_cli_and_model_exception_disagreement(monkeypatch, tmp_path, capsys):
    page = {"page_id": "p", "document_type": "receipt", "classification_status": "rule_classified"}
    record = {
        "provider_review_flags": [],
        "model_document_type": "commercial_invoice",
        "has_handwriting": False,
    }
    assert openai_adapter.model_exceptions(page, record)[0]["reason"].endswith(
        "disagrees_with_intake_rule"
    )
    manifest_path = manifest(tmp_path)
    with pytest.raises(ValueError, match="configured limit"):
        openai_adapter.run_adapter(
            manifest_path,
            tmp_path / "engine-limit.json",
            tmp_path / "adapter-limit.json",
            tmp_path / "exceptions-limit.json",
            tmp_path / "raw-limit",
            "test-model",
            Client([]),
            max_pages=1,
        )
    with pytest.raises(ValueError, match="must be distinct"):
        openai_adapter.run_adapter(
            manifest_path,
            tmp_path / "same.json",
            tmp_path / "same.json",
            tmp_path / "exceptions-same.json",
            tmp_path / "raw-same",
            "test-model",
            Client([]),
        )
    monkeypatch.setattr(openai_adapter, "build_client", lambda *_: object())
    called = {}

    def fake_run(*args):
        called["args"] = args
        return {"pages": 1}

    monkeypatch.setattr(openai_adapter, "run_adapter", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            openai_adapter.__file__,
            "m.json",
            "--out",
            "o",
            "--adapter-out",
            "a",
            "--exceptions",
            "e",
            "--raw-dir",
            "r",
            "--input-mode",
            "pdf",
            "--max-pages",
            "7",
            "--timeout-seconds",
            "8",
            "--max-retries",
            "1",
            "--reasoning-effort",
            "high",
        ],
    )
    openai_adapter.main()
    assert called["args"][7:] == ("pdf", 7, 10_000_000, 100_000, 8.0, 1, "high", "OPENAI_API_KEY")
    assert json.loads(capsys.readouterr().out)["pages"] == 1
    monkeypatch.setattr(
        openai_adapter, "build_client", lambda *_: (_ for _ in ()).throw(ValueError("bad"))
    )
    with pytest.raises(SystemExit, match="OpenAI adapter failed"):
        openai_adapter.main()


def test_a_lane_whose_stages_share_one_raw_directory_can_run_its_documented_sequence(tmp_path):
    """`empty_output_directory` is right when a command owns its raw directory.

    It is wrong for a lane whose stages run in sequence into one directory. The
    documented five-step table sequence gives `profile`, `rows`, `audit`,
    `mappings`, and `assemble` the same `RUN/tables/raw`, so the first stage
    populated it and every later stage was refused with `Raw response directory
    must be new or empty`. The published workflow could not be run as written.
    """
    raw = tmp_path / "tables" / "raw"
    assert run_io.shared_output_directory(raw, "profile_response.json") == raw
    (raw / "profile_response.json").write_text("{}")

    # A later stage of the same lane proceeds: it reserves only its own files.
    assert run_io.shared_output_directory(raw, "rows_response.json") == raw
    (raw / "rows_response.json").write_text("{}")
    assert run_io.shared_output_directory(raw, "audit_response.json") == raw

    # The guarantee that matters is intact: rerunning a stage over its own
    # retained response is refused, and the message names the file.
    with pytest.raises(ValueError, match="Refusing to overwrite retained raw response"):
        run_io.shared_output_directory(raw, "rows_response.json")
    # A stage reserving several names is refused if any one of them exists.
    with pytest.raises(ValueError, match="Refusing to overwrite retained raw response"):
        run_io.shared_output_directory(raw, "audit_response.json", "profile_response.json")

    # A path that is not a directory is still refused rather than replaced.
    plain = tmp_path / "a-file"
    plain.write_text("x")
    with pytest.raises(ValueError, match="must be a directory"):
        run_io.shared_output_directory(plain)

    # Reserving nothing still creates the directory, which is what a lane with a
    # single unnamed response needs.
    fresh = tmp_path / "fresh"
    assert run_io.shared_output_directory(fresh).is_dir()


def test_the_reading_vendor_decides_the_schema_dialect_not_the_transport():
    """A router does not change whose API validates the schema.

    OpenRouter hands a `google/...` slug to Google, which refuses the shared
    schema for having too much branching -- the same refusal the Vertex lane
    hit. So the vendor behind the slug picks the dialect.
    """
    # The OpenAI-compatible shape drops Vertex's `nullable` flag, so this
    # adapter must carry nullability in the type itself.
    translated = extraction_schema.vertex_response_schema(
        extraction_schema.EXTRACTION_SCHEMA, null_style=extraction_schema.NULL_STYLE_UNION
    )
    for provider, model in [
        ("openrouter", "google/gemini-2.5-flash"),
        ("openrouter", "google/gemini-2.5-pro"),
        # An alias slug resolves to the same vendor, or it would slip through.
        ("openrouter", "~google/gemini-2.5-pro"),
        ("google", "gemini-2.5-pro"),
    ]:
        assert openai_adapter.request_schema(provider, model) == translated, (provider, model)
    for provider, model in [
        ("openai", "gpt-5.6"),
        ("openrouter", "x-ai/grok-4.20"),
        ("openrouter", "mistralai/codestral-2508"),
        ("anthropic", "claude-haiku-4-5"),
        # A router slug that promises no vendor is not treated as Google.
        ("openrouter", "openrouter/auto"),
        (None, None),
    ]:
        assert (
            openai_adapter.request_schema(provider, model) == extraction_schema.EXTRACTION_SCHEMA
        ), (
            provider,
            model,
        )


def test_the_sent_dialect_takes_part_in_the_cache_key():
    """A response shaped by one dialect must not replay for the other."""
    google = openai_adapter.request_schema("openrouter", "google/gemini-2.5-flash")
    other = openai_adapter.request_schema("openrouter", "x-ai/grok-4.20")
    assert extraction_schema.schema_sha256(google) != extraction_schema.schema_sha256(other)


def test_a_cell_spelled_null_is_dropped_and_counted_not_silently_lost():
    """Dropping alone is how 222,190 cells vanished before anyone noticed."""
    parsed = {
        "document_type": "commercial_invoice",
        "header": [
            {
                "name": "invoice_number",
                "value": "null",
                "source": "printed",
                "evidence_text": None,
                "model_confidence": 0.9,
            }
        ],
        "lines": [{"line_number": 1, "description": "LOUNGE C", "item_code": "null"}],
        "review_flags": [],
    }
    record = openai_adapter.normalized_record(
        {"page_id": "p-1", "document_type": "commercial_invoice"},
        parsed,
        __file__,
        __file__,
        "model-a",
        "pdf",
    )
    # The text is not a reading, and the header field is refused the same way.
    assert record["header"] == {}
    assert "item_code" not in record["lines"][0]
    assert record["lines"][0]["description"]["value"] == "LOUNGE C"
    assert record["cells_spelled_null"] == 1


def test_a_lane_whose_cells_are_mostly_that_text_has_a_broken_dialect():
    """A transport that drops nullability must not report a clean run."""
    clean = [{"cells_spelled_null": 0, "lines": [{"line_number": 1, "a": 1, "b": 2}]}]
    finding, count = extraction_schema.spelled_null_finding(clean)
    assert finding is None and count == 0

    broken = [{"cells_spelled_null": 48, "lines": [{"line_number": 1, "a": 1}]}]
    finding, count = extraction_schema.spelled_null_finding(broken)
    assert count == 48
    assert finding["reason"] == "response_schema_nullability_not_honoured"
    assert finding["blocking"] is True
    assert finding["share"] > extraction_schema.SPELLED_NULL_SHARE_LIMIT

    # Nothing emitted at all divides by nothing rather than raising.
    assert extraction_schema.spelled_null_finding([]) == (None, 0)


def spelled_null_page():
    """What a transport that drops the schema's nullability actually returns."""
    return {
        "document_type": "commercial_invoice",
        "has_handwriting": False,
        "handwriting_regions": [],
        "handwriting_readings": [],
        "header": [],
        "source_labelled_fields": [],
        "lines": [
            {
                "line_number": 1,
                "description": "LOUNGE C",
                **{name: "null" for name in list(extraction_schema.LINE_FIELDS)[:20]},
            }
        ],
        "review_flags": [],
    }


def test_a_lane_that_lost_its_nullability_is_an_exception_not_a_clean_run(tmp_path):
    """The lane returns rows, every empty cell holds text, and nothing raises."""
    page = json.dumps(spelled_null_page())
    result = run(tmp_path, Client([Response(page), Response(page)]))
    written = json.loads((tmp_path / "exceptions.json").read_text())
    assert written["summary"]["cells_spelled_null"] > 0
    blocking = [
        item
        for item in written["exceptions"]
        if item.get("reason") == "response_schema_nullability_not_honoured"
    ]
    assert len(blocking) == 1
    assert blocking[0]["blocking"] is True
    assert blocking[0]["share"] > extraction_schema.SPELLED_NULL_SHARE_LIMIT
    # The run still completed and retained every page; it simply cannot be
    # reported as clean.
    assert result["pages"] == 2
    records = json.loads((tmp_path / "engine.json").read_text())
    assert all(record["cells_spelled_null"] > 0 for record in records)


def test_a_render_resolution_outside_the_readable_range_is_refused():
    """DPI decides what the model can read, so it is validated, not trusted."""
    assert openai_adapter.validate_image_dpi(200) == 200
    for bad in (71, 601):
        with pytest.raises(ValueError, match="image DPI must be between"):
            openai_adapter.validate_image_dpi(bad)
    for bad in (True, 200.0, "200"):
        with pytest.raises(ValueError, match="image DPI must be an integer"):
            openai_adapter.validate_image_dpi(bad)


def test_the_render_travels_with_its_own_digest_and_resolution(tmp_path, monkeypatch):
    """A reading is reproducible only against the exact bytes the model was shown."""
    monkeypatch.setattr(
        openai_adapter,
        "render_page",
        lambda page_path, image_path, dpi: Path(image_path).write_bytes(b"\x89PNG-page"),
    )
    data, provenance = openai_adapter.rendered_page_image(tmp_path / "p.pdf", 200, 1_000)
    assert data == b"\x89PNG-page"
    assert provenance["dpi"] == 200
    assert provenance["media_type"] == "image/png"
    assert provenance["bytes"] == len(data)
    assert provenance["sha256"] == hashlib.sha256(data).hexdigest()


def test_an_empty_render_is_a_failure_rather_than_a_blank_page(tmp_path, monkeypatch):
    """A page with nothing on it and a page that never rendered must not look alike."""
    monkeypatch.setattr(
        openai_adapter,
        "render_page",
        lambda page_path, image_path, dpi: Path(image_path).write_bytes(b""),
    )
    with pytest.raises(ValueError, match="Rendered page image is empty"):
        openai_adapter.rendered_page_image(tmp_path / "p.pdf", 200, 1_000)


def test_an_oversized_render_is_refused_rather_than_truncated(tmp_path, monkeypatch):
    """Truncating a page image would submit part of a page as if it were the page."""
    monkeypatch.setattr(
        openai_adapter,
        "render_page",
        lambda page_path, image_path, dpi: Path(image_path).write_bytes(b"x" * 50),
    )
    with pytest.raises(ValueError, match="above the 10 limit"):
        openai_adapter.rendered_page_image(tmp_path / "p.pdf", 200, 10)
    with pytest.raises(ValueError, match="maximum image bytes must be positive"):
        openai_adapter.rendered_page_image(tmp_path / "p.pdf", 200, 0)


def test_image_mode_submits_the_render_and_never_falls_back_to_the_pdf(tmp_path):
    """A vision-only vendor sent a PDF answers about a page it could not read."""
    captured = {}

    class Client:
        class responses:  # noqa: N801 - mirrors the SDK attribute name
            @staticmethod
            def create(**kwargs):
                captured.update(kwargs)
                return SimpleNamespace(output_text="{}")

    openai_adapter.page_request(
        Client, tmp_path / "p.pdf", "m", "medium", "image", None, "openrouter", b"\x89PNG"
    )
    parts = captured["input"][0]["content"]
    assert [part["type"] for part in parts] == ["input_text", "input_image"]
    assert parts[1]["image_url"].startswith("data:image/png;base64,")
    with pytest.raises(ValueError, match="requires a rendered page image"):
        openai_adapter.page_request(
            Client, tmp_path / "p.pdf", "m", "medium", "image", None, "openrouter", None
        )


def test_the_image_lane_renders_each_page_and_records_what_it_showed(tmp_path, monkeypatch):
    """The render is provenance, not a detail: it is what the model actually saw.

    A page read as an image is reproducible only against the exact bytes that
    were submitted, so the resolution and digest travel on the record rather
    than being recomputed later from a PDF nobody sent.
    """
    rendered = []

    def fake_render(page_path, image_path, dpi):
        rendered.append((Path(page_path).name, dpi))
        Path(image_path).write_bytes(b"\x89PNG" + Path(page_path).name.encode())

    monkeypatch.setattr(openai_adapter, "render_page", fake_render)
    client = Client([Response(json.dumps(extracted())), Response(json.dumps(extracted()))])
    openai_adapter.run_adapter(
        manifest(tmp_path),
        tmp_path / "engine.json",
        tmp_path / "adapter.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        "test-model",
        client,
        "image",
        supported_input_modes=("auto", "text", "pdf", "image"),
        image_dpi=150,
    )
    # Both pages render, including the one a text layer would have diverted to
    # the cheap lane: an explicit image mode is a decision, not a preference.
    assert rendered == [("first.pdf", 150), ("second.pdf", 150)]
    records = json.loads((tmp_path / "engine.json").read_text())
    for record in records:
        assert record["openai_input_mode"] == "image"
        assert record["page_image"]["dpi"] == 150
        assert record["page_image"]["media_type"] == "image/png"
        assert len(record["page_image"]["sha256"]) == 64
    # Two different pages were shown, so two different digests were recorded.
    assert records[0]["page_image"]["sha256"] != records[1]["page_image"]["sha256"]


def test_a_provider_that_never_translated_an_image_refuses_the_mode(tmp_path):
    """Fail closed rather than send a PDF to a vendor that cannot open one."""
    client = Client([Response(json.dumps(extracted()))])
    openai_adapter.run_adapter(
        manifest(tmp_path),
        tmp_path / "engine.json",
        tmp_path / "adapter.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        "test-model",
        client,
        "image",
    )
    records = json.loads((tmp_path / "engine.json").read_text())
    assert [record["review_status"] for record in records] == ["open_exception"] * 2
    assert "page_image" not in records[0]
