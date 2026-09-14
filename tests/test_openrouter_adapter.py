"""Tests for the isolated OpenRouter Chat Completions adapter."""

from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from scripts import openrouter_adapter  # noqa: E402


def extraction_payload() -> dict:
    return {
        "document_type": "commercial_invoice",
        "has_handwriting": False,
        "handwriting_regions": [],
        "handwriting_readings": [],
        "header": [],
        "lines": [],
        "review_flags": [],
    }


class ChatResponse:
    def __init__(self, content: str):
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]

    def model_dump(self, mode="json"):
        return {"id": "router-response", "mode": mode}


class Chat:
    def __init__(self, response):
        self.completions = self
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class RawClient:
    def __init__(self, response):
        self.chat = Chat(response)


def manifest(tmp_path: Path) -> Path:
    (tmp_path / "page.pdf").write_bytes(b"%PDF-page")
    (tmp_path / "page.txt").write_text("invoice INV-1 total 10", encoding="utf-8")
    value = {
        "pages": [
            {
                "page_id": "p-1",
                "page_pdf": "page.pdf",
                "text_file": "page.txt",
                "classification_status": "unclassified",
                "source_file": "source.pdf",
                "source_page_number": 1,
            }
        ]
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_chat_translation_and_unsupported_part_fails_closed():
    raw = RawClient(ChatResponse(json.dumps(extraction_payload())))
    client = openrouter_adapter._Client(raw)
    schema = {"format": {"name": "schema", "strict": True, "schema": {"type": "object"}}}
    response = client.responses.create(
        model="nemotron",
        instructions="instructions",
        input=[{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        text=schema,
        reasoning={"effort": "medium"},
    )
    assert json.loads(response.output_text)["document_type"] == "commercial_invoice"
    assert raw.chat.calls[0]["response_format"]["json_schema"]["name"] == "schema"
    # A page PDF is now translated rather than refused; the guard that keeps a
    # substituted reader out has moved to the catalogue preflight, which refuses
    # the model before any page is sent. An unknown part type still fails closed.
    with pytest.raises(ValueError, match="cannot submit content part"):
        client.responses.create(
            model="nemotron",
            instructions="instructions",
            input=[{"content": [{"type": "input_audio"}]}],
            text=schema,
            reasoning={},
        )


def test_response_dump_fallbacks():
    class ToDict:
        def to_dict(self):
            return {"source": "dict"}

    class Plain:
        pass

    for response, expected in ((ToDict(), {"source": "dict"}), (Plain(), None)):
        wrapped = openrouter_adapter._Response(
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
                to_dict=response.to_dict if hasattr(response, "to_dict") else None,
            )
        )
        if isinstance(response, Plain):
            assert wrapped.model_dump() is wrapped._response
        else:
            assert wrapped.model_dump() == expected


def test_build_client_validates_separate_key_and_transport(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    client = openrouter_adapter.build_client("OPENROUTER_API_KEY", 8.0, 2)
    assert client.responses
    with pytest.raises(ValueError, match="uppercase"):
        openrouter_adapter.build_client("bad-key", 8.0, 2)
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with pytest.raises(ValueError, match="not set"):
        openrouter_adapter.build_client("OPENROUTER_API_KEY", 8.0, 2)
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
    with pytest.raises(ValueError, match="timeout"):
        openrouter_adapter.build_client("OPENROUTER_API_KEY", 0, 2)
    with pytest.raises(ValueError, match="retries"):
        openrouter_adapter.build_client("OPENROUTER_API_KEY", 8.0, -1)

    original_import = builtins.__import__

    def missing_openai(name, *args, **kwargs):
        if name == "openai":
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_openai)
    with pytest.raises(ValueError, match="OpenAI SDK"):
        openrouter_adapter.build_client("OPENROUTER_API_KEY", 8.0, 2)


def test_run_adapter_reuses_contract_and_retains_openrouter_identity(tmp_path: Path):
    raw = RawClient(ChatResponse(json.dumps(extraction_payload())))
    result = openrouter_adapter.run_adapter(
        manifest(tmp_path),
        tmp_path / "engine.json",
        tmp_path / "adapter.json",
        tmp_path / "exceptions.json",
        tmp_path / "raw",
        "nvidia/nemotron-3-super-120b-a12b:free",
        openrouter_adapter._Client(raw),
        "text",
    )
    record = json.loads((tmp_path / "engine.json").read_text(encoding="utf-8"))[0]
    adapter = json.loads((tmp_path / "adapter.json").read_text(encoding="utf-8"))
    assert result["engine"].startswith("openrouter/")
    assert record["engine"].startswith("openrouter/")
    assert adapter["engine"].startswith("openrouter/")
    assert adapter["provider"] == "openrouter" and adapter["lane"] == "extraction"
    assert record["raw_response_sha256"] and adapter["records_sha256"]
    assert json.loads((tmp_path / "exceptions.json").read_text())["exceptions"][0][
        "reason"
    ].startswith("openrouter_")


CATALOGUE = {
    "data": [
        {"id": "anthropic/claude-opus-5", "architecture": {"input_modalities": ["text", "file"]}},
        {
            "id": "nvidia/nemotron-3-super-120b-a12b:free",
            "architecture": {"input_modalities": ["text"]},
        },
        {"id": "broken", "architecture": None},
        "not-a-dict",
    ]
}


def _catalogue():
    return CATALOGUE


def test_native_file_models_reads_openrouter_own_declaration():
    assert openrouter_adapter.native_file_models(_catalogue) == frozenset(
        {"anthropic/claude-opus-5"}
    )


def test_a_model_without_native_file_input_is_refused():
    """OpenRouter would otherwise OCR the PDF with another vendor and hide it."""
    with pytest.raises(ValueError, match="does not declare native file input"):
        openrouter_adapter.require_native_file_support(
            "nvidia/nemotron-3-super-120b-a12b:free", _catalogue
        )


def test_a_model_with_native_file_input_is_accepted():
    assert (
        openrouter_adapter.require_native_file_support("Anthropic/Claude-Opus-5", _catalogue)
        == "anthropic/claude-opus-5"
    )


def test_a_blank_model_is_refused():
    with pytest.raises(ValueError, match="model must be configured"):
        openrouter_adapter.require_native_file_support("", _catalogue)


def test_an_unreachable_catalogue_fails_closed():
    """Unproven support is the exact case where a reader gets substituted."""

    def unreachable():
        raise OSError("network down")

    with pytest.raises(ValueError, match="could not be read"):
        openrouter_adapter.native_file_models(unreachable)


def test_an_empty_catalogue_is_refused():
    with pytest.raises(ValueError, match="contained no models"):
        openrouter_adapter.native_file_models(lambda: {"data": []})


def test_a_catalogue_with_no_file_capable_model_is_refused():
    with pytest.raises(ValueError, match="declared no models accepting file input"):
        openrouter_adapter.native_file_models(
            lambda: {"data": [{"id": "a/b", "architecture": {"input_modalities": ["text"]}}]}
        )


def test_a_page_pdf_is_sent_as_a_file_part_with_the_reader_pinned():
    captured = {}

    class _Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    openrouter_adapter._Responses(client).create(
        model="anthropic/claude-opus-5",
        instructions="extract",
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "page"},
                    {
                        "type": "input_file",
                        "filename": "p0001.pdf",
                        "file_data": "data:application/pdf;base64,AAA",
                    },
                ],
            }
        ],
        text={"format": {"name": "n", "strict": True, "schema": {}}},
        reasoning=None,
    )
    part = captured["messages"][1]["content"][1]
    assert part["type"] == "file"
    assert part["file"]["filename"] == "p0001.pdf"
    # OpenRouter extensions must travel in extra_body: the OpenAI SDK validates
    # its own parameter list and raises TypeError on an unknown keyword.
    assert "plugins" not in captured
    assert captured["extra_body"]["plugins"] == [
        {"id": "file-parser", "pdf": {"engine": openrouter_adapter.NATIVE_PDF_ENGINE}}
    ]


def test_a_text_only_request_sends_no_parser_plugin():
    captured = {}

    class _Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    openrouter_adapter._Responses(client).create(
        model="nvidia/x",
        instructions="extract",
        input=[{"role": "user", "content": [{"type": "input_text", "text": "page"}]}],
        text={"format": {"name": "n", "strict": True, "schema": {}}},
        reasoning=None,
    )
    assert "extra_body" not in captured


def test_an_unsupported_content_part_is_refused():
    client = SimpleNamespace(chat=SimpleNamespace(completions=None))
    with pytest.raises(ValueError, match="cannot submit content part"):
        openrouter_adapter._Responses(client).create(
            model="m",
            instructions="i",
            input=[{"role": "user", "content": [{"type": "input_audio"}]}],
            text={"format": {"name": "n", "strict": True, "schema": {}}},
            reasoning=None,
        )


def test_text_mode_never_consults_the_catalogue(tmp_path):
    def forbidden():
        raise AssertionError("text mode must not need the catalogue")

    with pytest.raises((FileNotFoundError, ValueError, OSError)):
        openrouter_adapter.run_adapter(
            str(tmp_path / "m.json"),
            "o",
            "a",
            "e",
            "r",
            "nvidia/x",
            None,
            "text",
            catalogue_fetch=forbidden,
        )


def test_the_catalogue_fetch_reads_the_public_endpoint(monkeypatch):
    """The default fetch must actually talk to OpenRouter, not a stub."""
    seen = {}

    class _Body:
        def read(self):
            return b'{"data": [{"id": "a/b", "architecture": {"input_modalities": ["file"]}}]}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return _Body()

    monkeypatch.setattr(openrouter_adapter.urllib.request, "urlopen", fake_urlopen)
    assert openrouter_adapter._fetch_catalogue()["data"][0]["id"] == "a/b"
    assert seen["url"] == openrouter_adapter.MODELS_ENDPOINT
    assert seen["timeout"] == openrouter_adapter.CATALOGUE_TIMEOUT_SECONDS


def test_a_file_capable_entry_without_an_id_is_skipped():
    catalogue = {
        "data": [
            {"id": "", "architecture": {"input_modalities": ["file"]}},
            {"architecture": {"input_modalities": ["file"]}},
            {"id": "real/model", "architecture": {"input_modalities": ["file"]}},
        ]
    }
    assert openrouter_adapter.native_file_models(lambda: catalogue) == frozenset({"real/model"})


def test_a_verified_model_proceeds_to_the_shared_loop(monkeypatch):
    """A supported model must pass the preflight and reach the extraction loop."""
    called = {}

    def fake_run_adapter(*args, **kwargs):
        called["provider_name"] = kwargs.get("provider_name")
        called["args"] = args
        return {"pages": 0}

    monkeypatch.setattr(openrouter_adapter.openai_adapter, "run_adapter", fake_run_adapter)
    result = openrouter_adapter.run_adapter(
        "m",
        "o",
        "a",
        "e",
        "r",
        "anthropic/claude-opus-5",
        None,
        "auto",
        catalogue_fetch=_catalogue,
    )
    assert result == {"pages": 0}
    assert called["provider_name"] == "openrouter"


def _capture_client():
    captured = {}

    class _Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])

    return SimpleNamespace(chat=SimpleNamespace(completions=_Completions())), captured


def _create(client, reasoning):
    openrouter_adapter._Responses(client).create(
        model="m",
        instructions="i",
        input=[{"role": "user", "content": [{"type": "input_text", "text": "t"}]}],
        text={"format": {"name": "n", "strict": True, "schema": {}}},
        reasoning=reasoning,
    )


def test_reasoning_effort_reaches_the_request():
    """It was accepted and dropped, so the documented setting changed nothing."""
    client, captured = _capture_client()
    _create(client, {"effort": "high"})
    assert captured["reasoning_effort"] == "high"


def test_reasoning_effort_none_sends_no_level():
    client, captured = _capture_client()
    _create(client, {"effort": "none"})
    assert "reasoning_effort" not in captured


def test_absent_reasoning_sends_no_level():
    client, captured = _capture_client()
    _create(client, None)
    assert "reasoning_effort" not in captured


def test_a_vision_only_model_is_qualified_on_image_input_not_file_input():
    """The catalogue answers per modality, so a lane asks about the one it will use.

    Every remaining file-native vendor on this router is already a lane in this
    corpus or is served only by an endpoint whose terms require permitting
    training on the submitted page. The vendors that read documents without
    accepting a PDF are the only untapped independent readers left, and refusing
    them for lacking `file` would be refusing the wrong question.
    """
    catalogue = {
        "data": [
            {"id": "qwen/vision", "architecture": {"input_modalities": ["text", "image"]}},
            {"id": "vendor/reader", "architecture": {"input_modalities": ["text", "file"]}},
        ]
    }
    fetch = lambda: catalogue  # noqa: E731 - a one-line stub reads better inline
    assert openrouter_adapter.native_input_models("image", fetch) == frozenset({"qwen/vision"})
    assert (
        openrouter_adapter.require_native_input_support("qwen/vision", "image", fetch)
        == "qwen/vision"
    )
    with pytest.raises(ValueError, match="does not declare image input"):
        openrouter_adapter.require_native_input_support("vendor/reader", "image", fetch)
    with pytest.raises(ValueError, match="does not declare native file input"):
        openrouter_adapter.require_native_input_support("qwen/vision", "file", fetch)
    with pytest.raises(ValueError, match="must be configured"):
        openrouter_adapter.require_native_input_support("", "image", fetch)
    with pytest.raises(ValueError, match="no models accepting image input"):
        openrouter_adapter.native_input_models(
            "image", lambda: {"data": [{"id": "a/b", "architecture": {"input_modalities": []}}]}
        )


def test_a_page_image_is_sent_as_pixels_with_no_parser_plugin():
    """Pixels cannot be silently re-read by a third party's OCR.

    The file lane has to pin an engine and take the router's word for who read
    the glyphs. An image lane needs neither: whatever the routed model reports,
    it reported from this image.
    """
    captured = {}

    class _Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    openrouter_adapter._Responses(client).create(
        model="qwen/vision",
        instructions="i",
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "read"},
                    {"type": "input_image", "image_url": "data:image/png;base64,AAA"},
                ],
            }
        ],
        text={"format": {"name": "n", "strict": True, "schema": {}}},
        reasoning={"effort": "medium"},
    )
    parts = captured["messages"][1]["content"]
    assert parts[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}
    assert "extra_body" not in captured


def test_image_mode_qualifies_the_model_on_image_input_before_spending(tmp_path):
    """The lane asks the catalogue about the modality it will actually send.

    Verified once here rather than per page: a run that cannot legitimately read
    its corpus should refuse before it has spent anything.
    """
    asked = []

    def catalogue():
        asked.append(True)
        return {"data": [{"id": "qwen/vision", "architecture": {"input_modalities": ["image"]}}]}

    with pytest.raises(ValueError, match="does not declare image input"):
        openrouter_adapter.run_adapter(
            str(tmp_path / "m.json"),
            "o",
            "a",
            "e",
            "r",
            "anthropic/claude-opus-5",
            None,
            "image",
            catalogue_fetch=catalogue,
        )
    with pytest.raises((FileNotFoundError, ValueError, OSError)):
        openrouter_adapter.run_adapter(
            str(tmp_path / "m.json"),
            "o",
            "a",
            "e",
            "r",
            "qwen/vision",
            None,
            "image",
            catalogue_fetch=catalogue,
        )
    assert asked


def test_provider_slugs_are_shape_checked_without_copying_openrouter_s_list():
    assert openrouter_adapter.validate_provider_slugs("coreweave, modal ,sail-research") == (
        "coreweave",
        "modal",
        "sail-research",
    )
    assert openrouter_adapter.validate_provider_slugs(None) == ()
    assert openrouter_adapter.validate_provider_slugs("") == ()
    assert openrouter_adapter.validate_provider_slugs(["coreweave"]) == ("coreweave",)
    # Display names, quantization tags and spaces are not routing slugs. The set
    # of real slugs stays OpenRouter's, which names them when one is unknown.
    for rejected in ("CoreWeave", "coreweave/fp8", "co reweave", "-x", "x-"):
        with pytest.raises(ValueError, match="lowercase OpenRouter provider slug"):
            openrouter_adapter.validate_provider_slugs(rejected)


def test_a_named_provider_allow_list_travels_with_the_request():
    captured = {}

    class _Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    openrouter_adapter._Responses(client, ("coreweave", "modal")).create(
        model="z-ai/glm-5.3-flash",
        instructions="extract",
        input=[{"role": "user", "content": [{"type": "input_text", "text": "page"}]}],
        text={"format": {"name": "n", "strict": True, "schema": {}}},
        reasoning=None,
    )
    assert captured["extra_body"]["provider"] == {"only": ["coreweave", "modal"]}


def test_routing_does_not_displace_the_pinned_pdf_reader():
    """Both extensions share one extra_body, so neither may overwrite the other.

    The reader pin is what keeps another vendor's OCR out of a file-native
    reading, and provider routing is what keeps a degraded host out of the lane.
    A page PDF sent to a pinned provider needs both.
    """
    captured = {}

    class _Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    openrouter_adapter._Responses(client, ("coreweave",)).create(
        model="meta/muse-spark-1.3",
        instructions="extract",
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_file",
                        "filename": "p0001.pdf",
                        "file_data": "data:application/pdf;base64,AAA",
                    }
                ],
            }
        ],
        text={"format": {"name": "n", "strict": True, "schema": {}}},
        reasoning=None,
    )
    assert captured["extra_body"]["plugins"] == [
        {"id": "file-parser", "pdf": {"engine": openrouter_adapter.NATIVE_PDF_ENGINE}}
    ]
    assert captured["extra_body"]["provider"] == {"only": ["coreweave"]}


def test_an_unrouted_lane_sends_no_provider_preference(monkeypatch):
    captured = {}

    class _Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    openrouter_adapter._Responses(client).create(
        model="z-ai/glm-5.3-flash",
        instructions="extract",
        input=[{"role": "user", "content": [{"type": "input_text", "text": "page"}]}],
        text={"format": {"name": "n", "strict": True, "schema": {}}},
        reasoning=None,
    )
    assert "extra_body" not in captured
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    built = openrouter_adapter.build_client("OPENROUTER_API_KEY", 8.0, 2, "coreweave,modal")
    assert built.responses.provider_only == ("coreweave", "modal")
    with pytest.raises(ValueError, match="lowercase OpenRouter provider slug"):
        openrouter_adapter.build_client("OPENROUTER_API_KEY", 8.0, 2, "CoreWeave")
