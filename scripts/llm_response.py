#!/usr/bin/env python3
"""Provider-neutral response handling and input-mode selection for LLM lanes.

`response_payload` and `response_json` accept any SDK object shape rather than a
single vendor's, which is why they belong here and not in one adapter.
"""

import json

REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")


INPUT_MODES = ("auto", "text", "pdf", "image")
# Fail closed: a provider adapter opts into image reading by passing it in, so a
# mode no one translated cannot reach a provider by inheriting a default.
DOCUMENT_INPUT_MODES = ("auto", "text", "pdf")


def choose_input_mode(page, native_text, requested_mode, supported_modes=DOCUMENT_INPUT_MODES):
    """Use low-cost text only for conservatively classified native-text pages.

    ``auto`` never selects ``image``. Rendering a page costs a local raster and
    submits pixels instead of the retained PDF, which is a deliberate choice
    about who reads the glyphs rather than a fallback to reach for silently.

    ``supported_modes`` fails closed. A provider whose request translation has
    not been verified for a mode must refuse it, because the alternative is an
    adapter quietly sending a PDF to a vision-only vendor and recording the
    empty answer as a reading.
    """
    if requested_mode not in INPUT_MODES:
        raise ValueError("input mode must be auto, text, pdf, or image")
    if requested_mode not in supported_modes:
        raise ValueError(
            f"input mode {requested_mode!r} is not supported by this provider adapter; "
            f"supported modes are: {', '.join(supported_modes)}"
        )
    if requested_mode in ("pdf", "image"):
        return requested_mode
    if requested_mode == "text":
        if not native_text:
            raise ValueError(f"Retained native text is not readable for page {page['page_id']}")
        return "text"
    if native_text and page.get("classification_status") == "rule_classified":
        return "text"
    return "pdf"


def response_payload(response):
    """Serialize SDK responses for raw retention without requiring a specific SDK object type."""
    if hasattr(response, "model_dump"):
        try:
            return response.model_dump(mode="json")
        except TypeError:
            return response.model_dump()
    if hasattr(response, "to_dict"):
        return response.to_dict()
    return response


def strip_code_fence(text):
    """Return structured output with a surrounding Markdown fence removed.

    Some providers honour a strict JSON schema in the content and still wrap it
    in ```` ```json ```` — Anthropic through OpenRouter does. A strict decoder
    that cannot see past the fence fails the whole lane on a response that is
    otherwise exactly what was asked for, so the fence is removed before
    decoding. Nothing else is altered: the retained raw response keeps the
    original bytes, and the JSON inside is still validated against the schema.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped[3:]
    newline = body.find("\n")
    if newline == -1:
        return stripped
    language = body[:newline].strip()
    # Only a bare fence or an explicitly JSON-labelled one; anything else is not
    # a wrapper this function should be guessing about.
    if language and language.casefold() not in {"json", "json5"}:
        return stripped
    body = body[newline + 1 :]
    closing = body.rfind("```")
    return (body[:closing] if closing != -1 else body).strip()


def response_json(response):
    """Decode a strict Structured Outputs response and reject non-object outputs."""
    output_text = getattr(response, "output_text", None)
    if not isinstance(output_text, str) or not output_text.strip():
        raise ValueError("provider response did not contain structured output text")
    data = json.loads(strip_code_fence(output_text))
    if not isinstance(data, dict):
        raise ValueError("provider structured output must be an object")
    return data
