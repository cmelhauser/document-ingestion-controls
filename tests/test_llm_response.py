"""Tests for shared provider-response decoding."""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

llm_response = importlib.import_module("llm_response")


def test_a_json_code_fence_is_removed_before_decoding():
    """Anthropic through OpenRouter honours the schema and still fences the JSON.

    A strict decoder that cannot see past the fence fails the whole lane on a
    response that is otherwise exactly what was requested.
    """
    fenced = SimpleNamespace(output_text='```json\n{"document_type": "invoice"}\n```')
    assert llm_response.response_json(fenced) == {"document_type": "invoice"}


def test_a_bare_fence_is_removed():
    assert llm_response.strip_code_fence('```\n{"a": 1}\n```') == '{"a": 1}'


def test_unfenced_output_is_unchanged():
    assert llm_response.strip_code_fence('  {"a": 1}  ') == '{"a": 1}'


def test_a_non_json_fence_is_left_alone():
    """Only a bare or JSON-labelled fence is a wrapper worth unwrapping."""
    text = "```python\nprint(1)\n```"
    assert llm_response.strip_code_fence(text) == text


def test_an_unterminated_fence_still_decodes():
    assert llm_response.strip_code_fence('```json\n{"a": 1}') == '{"a": 1}'


def test_a_fence_marker_without_a_newline_is_left_alone():
    assert llm_response.strip_code_fence("```") == "```"


def test_a_fenced_non_object_is_still_refused():
    fenced = SimpleNamespace(output_text="```json\n[1, 2]\n```")
    with pytest.raises(ValueError, match="must be an object"):
        llm_response.response_json(fenced)


def test_a_payload_without_header_or_lines_is_refused():
    """A provider can return valid JSON that ignores the schema's structure.

    Normalising that produced an empty record that consensus counted as a present
    engine, so a lane contributing nothing read as disagreement instead of as the
    provider failure it was.
    """
    openai_adapter = importlib.import_module("openai_adapter")
    with pytest.raises(ValueError, match="header .*or lines list"):
        openai_adapter.require_contract_shape({"document_type": "invoice"}, "openrouter")


def test_a_payload_with_only_a_header_list_is_accepted():
    openai_adapter = importlib.import_module("openai_adapter")
    assert openai_adapter.require_contract_shape({"header": []}, "openai") is None


def test_a_payload_with_only_a_lines_list_is_accepted():
    openai_adapter = importlib.import_module("openai_adapter")
    assert openai_adapter.require_contract_shape({"lines": []}, "openai") is None


def test_image_is_a_mode_a_provider_must_declare_it_reads():
    """An adapter that never translated an image part must refuse the mode.

    Silently accepting it would send a page PDF to a vision-only vendor and file
    the empty answer that comes back as a reading.
    """
    with pytest.raises(ValueError, match="not supported by this provider adapter"):
        llm_response.choose_input_mode({"page_id": "p"}, None, "image")
    assert (
        llm_response.choose_input_mode(
            {"page_id": "p"}, None, "image", (*llm_response.DOCUMENT_INPUT_MODES, "image")
        )
        == "image"
    )


def test_auto_never_reaches_for_the_image_lane():
    """Rendering is a deliberate choice about who reads the glyphs, not a fallback."""
    page = {"page_id": "p", "classification_status": "unclassified"}
    assert llm_response.choose_input_mode(page, None, "auto") == "pdf"
