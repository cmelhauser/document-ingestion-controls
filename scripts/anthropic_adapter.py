#!/usr/bin/env python3
"""Anthropic Messages adapter with the repository's strict record contract.

Anthropic is a separate provider boundary in the same sense as OpenAI and
Google: its own credential, its own model settings, and its own vendor identity
for consensus independence. A lane configured here is genuinely independent of an
OpenAI or a Google lane, and `model_vendor` resolves it to ``anthropic`` without
any routing to see through.

Two details of the Messages API shape this adapter, and both are deliberate:

**Structured output comes from a tool, not from prose.** The Messages API has no
``response_format`` with a strict JSON schema. It does have tool use, where the
tool's ``input_schema`` is a JSON Schema the model must satisfy, and the returned
``tool_use`` block carries the validated object. That is the strongest guarantee
the API offers, so the shared extraction schema becomes a single tool and the
model is required to call it.

**A retained page PDF is submitted as a document block**, so the model reads the
glyphs itself. That matters for corroboration: a lane whose recorded vendor did
not actually read the page is not independent evidence, and routing a PDF through
a separate OCR pass would make the recorded vendor the reasoning model while some
other engine read the page.

``max_tokens`` is required by this API and has no server default, so it is an
explicit setting rather than a constant buried here: a cap that silently truncates
a long page's extraction would look like a model that missed rows.
"""

import json
import os
import re

import openai_adapter
from llm_response import strip_code_fence
from runtime_config import env_int, env_value

# The tool the model is required to call. Its input is the structured record.
STRUCTURED_TOOL_DESCRIPTION = (
    "Return the extracted record. Every field must satisfy the supplied schema; "
    "omit anything the page does not show rather than inventing it."
)
# Anthropic requires an explicit output ceiling. A page of dense table rows needs
# real headroom, and a truncated response is indistinguishable from a model that
# read fewer rows than the page holds.
DEFAULT_MAX_OUTPUT_TOKENS = 16384
# Thinking is configured two different ways depending on the model, and asking
# the wrong way is a hard 400 rather than a silent downgrade. Claude 4.5 and
# earlier take a token budget (`thinking.type: "enabled"`); 4.7 and later reject
# that and take `thinking.type: "adaptive"` with `output_config.effort`. Sonnet 5
# failed all 18 pages of a real corpus on the budget form before this existed.
THINKING_BUDGETS = {
    "none": 0,
    "low": 1024,
    "medium": 4096,
    "high": 8192,
    "xhigh": 16384,
    "max": 24576,
}
# This repository's effort names mapped onto the ones `output_config.effort`
# takes. The finer levels collapse upward rather than being dropped.
ADAPTIVE_EFFORTS = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}
# The version at which the request shape changes, per Anthropic's own migration
# note: "Claude Opus 4.7, Claude Opus 4.8, Claude Opus 5, Claude Sonnet 5 ...
# where type: 'enabled' returns a 400 error".
ADAPTIVE_FROM_VERSION = 4.7
MODEL_VERSION = re.compile(r"claude-[a-z]+-(\d+)(?:-(\d+))?")


def model_version(model):
    """Return the model's version as a number, or None when it cannot be read.

    A slug this cannot parse is not guessed at: the caller falls back to the
    explicitly configured thinking mode rather than picking one.
    """
    match = MODEL_VERSION.search(str(model or "").casefold())
    if not match:
        return None
    major, minor = match.group(1), match.group(2)
    return float(f"{major}.{minor}") if minor else float(major)


def thinking_mode(model, configured=""):
    """Return which thinking contract this model accepts."""
    configured = str(configured or "").strip().casefold()
    if configured in {"adaptive", "budget", "none"}:
        return configured
    version = model_version(model)
    if version is None:
        # An unrecognised slug gets the forward-looking form, which is what every
        # currently released model takes.
        return "adaptive"
    return "adaptive" if version >= ADAPTIVE_FROM_VERSION else "budget"


def _document_block(part):
    """Return the Messages document block for one retained page PDF."""
    data = part.get("file_data", "")
    # The shared request carries a data URL; the Messages API wants the payload.
    if isinstance(data, str) and data.startswith("data:"):
        data = data.split(",", 1)[-1]
    return {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": data},
    }


def _messages(input_items):
    """Translate the shared request's content parts into Messages content blocks."""
    messages = []
    for item in input_items:
        content = []
        for part in item.get("content", []):
            kind = part.get("type")
            if kind == "input_text":
                content.append({"type": "text", "text": part.get("text", "")})
            elif kind == "input_file":
                content.append(_document_block(part))
            else:
                raise ValueError(f"Anthropic adapter cannot submit content part {kind!r}")
        messages.append({"role": item.get("role", "user"), "content": content})
    return messages


def _structured_output(response, tool_name):
    """Return the structured object the model produced, as JSON text.

    The tool input is the contract. A model that answered in prose instead has
    not satisfied the schema, and saying so is better than decoding whatever text
    came back and hoping it matches.
    """
    text_blocks = []
    for block in getattr(response, "content", None) or []:
        kind = getattr(block, "type", None)
        if kind == "tool_use" and getattr(block, "name", None) == tool_name:
            return json.dumps(getattr(block, "input", None))
        if kind == "text":
            text_blocks.append(getattr(block, "text", "") or "")
    joined = "\n".join(part for part in text_blocks if part).strip()
    if not joined:
        raise ValueError("Anthropic response contained neither the required tool call nor any text")
    # A model that returned the object as text rather than through the tool is
    # still checked against the schema by the caller; only obvious fencing is
    # removed here.
    return strip_code_fence(joined)


class _Response:
    """Expose a Messages response through the Responses adapter contract."""

    def __init__(self, response, tool_name):
        self._response = response
        self.output_text = _structured_output(response, tool_name)

    def model_dump(self, mode="json"):
        """Retain the complete SDK response when the installed SDK supports it."""
        if callable(getattr(self._response, "model_dump", None)):
            return self._response.model_dump(mode=mode)
        if callable(getattr(self._response, "to_dict", None)):
            return self._response.to_dict()
        return self._response


class _Responses:
    """Translate the shared structured extraction request to Messages."""

    def __init__(self, client, max_output_tokens, mode=""):
        self.client = client
        self.max_output_tokens = max_output_tokens
        self.thinking_mode = mode

    def create(self, *, model, instructions, input, text, reasoning):
        """Translate one structured extraction request to a Messages call."""
        schema = text["format"]
        tool_name = schema["name"]
        request = {
            "model": model,
            "max_tokens": self.max_output_tokens,
            "system": instructions,
            "messages": _messages(input),
            "tools": [
                {
                    "name": tool_name,
                    "description": STRUCTURED_TOOL_DESCRIPTION,
                    "input_schema": schema["schema"],
                }
            ],
        }
        effort = (reasoning or {}).get("effort") if isinstance(reasoning, dict) else reasoning
        effort = str(effort or "none").strip().casefold()
        mode = thinking_mode(model, self.thinking_mode)
        thinking = None
        if effort != "none" and mode != "none":
            if mode == "adaptive":
                thinking = {"type": "adaptive"}
                request["output_config"] = {"effort": ADAPTIVE_EFFORTS.get(effort, "high")}
            else:
                budget = THINKING_BUDGETS.get(effort, 0)
                # The budget must leave room for the answer inside max_tokens.
                if budget and budget < self.max_output_tokens:
                    thinking = {"type": "enabled", "budget_tokens": budget}
        if thinking is not None:
            # Thinking and a forced tool choice are mutually exclusive, so a lane
            # that asked to think gets `auto` and the tool is located in the
            # response rather than guaranteed by the request.
            request["thinking"] = thinking
            request["tool_choice"] = {"type": "auto"}
        else:
            request["tool_choice"] = {"type": "tool", "name": tool_name}
        return _Response(self.client.messages.create(**request), tool_name)


class _Client:
    """Small compatibility shell keeping Anthropic provider calls walled off."""

    def __init__(self, client, max_output_tokens, mode=""):
        self.responses = _Responses(client, max_output_tokens, mode)


def build_client(credential_env, timeout_seconds=120.0, max_retries=2):
    """Build an Anthropic client with its own credential and bounded transport."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", credential_env):
        raise ValueError("credential environment variable must be uppercase with underscores")
    if timeout_seconds <= 0 or max_retries < 0:
        raise ValueError("timeout seconds must be positive and retries non-negative")
    key = os.environ.get(credential_env)
    if not key:
        raise ValueError(f"Required credential environment variable is not set: {credential_env}")
    try:
        from anthropic import Anthropic
    except ImportError as exc:  # pragma: no cover - dependency is declared and locked
        raise ValueError("Anthropic SDK is required for the Anthropic client") from exc
    max_output_tokens = env_int("ANTHROPIC_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS)
    if max_output_tokens < 1:
        raise ValueError("ANTHROPIC_MAX_OUTPUT_TOKENS must be a positive integer")
    options = {"api_key": key, "timeout": timeout_seconds, "max_retries": max_retries}
    base_url = env_value("ANTHROPIC_BASE_URL", "")
    if base_url:
        options["base_url"] = base_url
    # An identity-linked API key is scoped to a workspace and the API refuses a
    # request that does not name one. The workspace id is an identifier rather
    # than a secret, so it is an ordinary setting; a key that is not
    # identity-linked ignores the header.
    workspace = env_value("ANTHROPIC_WORKSPACE_ID", "")
    if workspace:
        options["default_headers"] = {"anthropic-workspace-id": workspace}
    return _Client(
        Anthropic(**options), max_output_tokens, env_value("ANTHROPIC_THINKING_MODE", "")
    )


def run_adapter(*args, **kwargs):
    """Reuse the shared evidence-preserving loop with Anthropic identity.

    Only the provider identity and the client differ; every retention, retry,
    exception, and manifest-ordering guarantee belongs to the shared loop and is
    not reimplemented per provider.
    """
    return openai_adapter.run_adapter(*args, provider_name="anthropic", **kwargs)
