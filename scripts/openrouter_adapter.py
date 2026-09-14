#!/usr/bin/env python3
"""OpenRouter Chat Completions adapter with the repository's strict record contract.

OpenRouter is deliberately a separate provider boundary. It uses the OpenAI
SDK only as an HTTP client pointed at OpenRouter's endpoint; it does not share
OpenAI credentials, model settings, or provider identity.

A retained page PDF is submitted only to a model that reads files **natively**,
and the ``file-parser`` engine is pinned to ``native`` so OpenRouter never
substitutes a reader. Left to its default, OpenRouter OCRs a PDF with a separate
vendor's engine (``mistral-ocr``) and hands the text to the routed model. That
would make the recorded vendor the *reasoning* model while a different vendor
actually read the glyphs, and two lanes on different models could silently share
one OCR pass — corroboration that is not corroboration. Support is verified
against OpenRouter's own model catalogue before any page is sent, and an
unverifiable catalogue fails closed rather than assuming support.
"""

import json
import os
import re
import urllib.error
import urllib.request

from llm_response import DOCUMENT_INPUT_MODES
from runtime_config import SOURCE_READ_BY_MODEL

try:  # Support both ``python scripts/...`` and ``from scripts import ...``.
    from . import openai_adapter
except ImportError:  # pragma: no cover - exercised by the CLI import path.
    import openai_adapter


MODELS_ENDPOINT = "https://openrouter.ai/api/v1/models"
FILE_MODALITY = "file"
IMAGE_MODALITY = "image"
NATIVE_PDF_ENGINE = "native"
CATALOGUE_TIMEOUT_SECONDS = 30.0
PROVIDER_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def validate_provider_slugs(providers):
    """Normalize the operator's allow-list of providers that may serve a request.

    OpenRouter picks the serving provider itself, and the record names only the
    model. A degraded provider returns a truncated stream, and downstream that is
    indistinguishable from a model that could not read the page: one measured
    qualification lost 9 of 20 pages that way, all of them routed to a single
    provider sitting at 12% uptime, and the candidate looked like it had failed
    to read. Naming the permitted providers makes that choice explicit.

    The list of real slugs stays OpenRouter's. It already refuses an unknown
    provider and answers with the providers actually serving the model, so this
    validates shape only rather than keeping a second copy of that list here for
    the two to drift apart.
    """
    if providers is None:
        return ()
    if isinstance(providers, str):
        providers = providers.split(",")
    slugs = tuple(name for name in (str(item).strip() for item in providers) if name)
    for slug in slugs:
        if not PROVIDER_SLUG.fullmatch(slug):
            raise ValueError(
                f"provider must be a lowercase OpenRouter provider slug: {slug!r}; "
                "OpenRouter names the providers serving a model when one is unknown"
            )
    return slugs


def _fetch_catalogue(url=MODELS_ENDPOINT, timeout=CATALOGUE_TIMEOUT_SECONDS):
    """Read OpenRouter's public model catalogue."""
    request = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310 - fixed https endpoint
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https endpoint
        return json.loads(response.read().decode("utf-8"))


def native_input_models(modality, fetch=_fetch_catalogue):
    """Return the model IDs OpenRouter declares as accepting ``modality`` input.

    `architecture.input_modalities` is OpenRouter's own statement about a model,
    so it cannot go stale the way a checked-in allowlist would.
    """
    try:
        catalogue = fetch()
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"OpenRouter model catalogue could not be read ({type(exc).__name__}); "
            "native file support cannot be verified, so no page PDF is submitted"
        ) from exc
    entries = catalogue.get("data") if isinstance(catalogue, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError("OpenRouter model catalogue contained no models")
    supported = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        architecture = entry.get("architecture")
        modalities = (
            architecture.get("input_modalities") if isinstance(architecture, dict) else None
        )
        if isinstance(modalities, list) and modality in modalities:
            model_id = entry.get("id")
            if isinstance(model_id, str) and model_id:
                supported.add(model_id.casefold())
    if not supported:
        raise ValueError(
            f"OpenRouter model catalogue declared no models accepting {modality} input"
        )
    return frozenset(supported)


def native_file_models(fetch=_fetch_catalogue):
    """Return the model IDs OpenRouter declares as accepting native file input."""
    return native_input_models(FILE_MODALITY, fetch)


def require_native_input_support(model, modality, fetch=_fetch_catalogue):
    """Refuse to submit a page to a model that does not declare it reads that input.

    Fails closed. A model absent from the catalogue, or a catalogue that cannot
    be read, means support is unproven — and an unproven reader is exactly the
    case where OpenRouter would quietly substitute another vendor's OCR, or
    would answer about a page it never actually saw.
    """
    slug = str(model or "").strip().casefold()
    if not slug:
        raise ValueError("an OpenRouter model must be configured before submitting a page")
    if slug not in native_input_models(modality, fetch):
        if modality == IMAGE_MODALITY:
            raise ValueError(
                f"OpenRouter model {model!r} does not declare image input; a vision-only lane "
                "cannot read this page. Choose a model whose input_modalities include 'image'."
            )
        raise ValueError(
            f"OpenRouter model {model!r} does not declare native file input; submitting a page "
            "PDF would let OpenRouter parse it with another vendor's OCR and record the wrong "
            "vendor as having read the page. Choose a model whose input_modalities include "
            "'file', run this lane in image input mode, or run it in text input mode."
        )
    return slug


def require_native_file_support(model, fetch=_fetch_catalogue):
    """Refuse to submit a page PDF to a model that does not read files itself."""
    return require_native_input_support(model, FILE_MODALITY, fetch)


class _Response:
    """Expose Chat Completions output through the Responses adapter contract."""

    def __init__(self, response):
        self._response = response
        choice = response.choices[0]
        self.output_text = choice.message.content or ""

    def model_dump(self, mode="json"):
        """Retain the complete SDK response when the installed SDK supports it."""
        if callable(getattr(self._response, "model_dump", None)):
            return self._response.model_dump(mode=mode)
        if callable(getattr(self._response, "to_dict", None)):
            return self._response.to_dict()
        return self._response


class _Responses:
    """Translate the shared structured extraction request to Chat Completions."""

    def __init__(self, client, provider_only=()):
        self.client = client
        self.provider_only = tuple(provider_only)

    def create(self, *, model, instructions, input, text, reasoning):
        """Translate one structured extraction request to Chat Completions."""
        messages, sent_file = [], False
        for item in input:
            content = []
            for part in item.get("content", []):
                kind = part.get("type")
                if kind == "input_text":
                    content.append({"type": "text", "text": part.get("text", "")})
                elif kind == "input_image":
                    # No file-parser plugin and no engine to pin: the routed
                    # model is shown pixels, so no other vendor's OCR can be
                    # interposed between the page and the reader on record.
                    content.append(
                        {"type": "image_url", "image_url": {"url": part.get("image_url", "")}}
                    )
                elif kind == "input_file":
                    sent_file = True
                    content.append(
                        {
                            "type": "file",
                            "file": {
                                "filename": part.get("filename", "page.pdf"),
                                "file_data": part.get("file_data", ""),
                            },
                        }
                    )
                else:
                    raise ValueError(f"OpenRouter adapter cannot submit content part {kind!r}")
            messages.append({"role": item.get("role", "user"), "content": content})
        schema = text["format"]
        request = {
            "model": model,
            "messages": [{"role": "system", "content": instructions}, *messages],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema["name"],
                    "strict": schema["strict"],
                    "schema": schema["schema"],
                },
            },
        }
        extra_body = {}
        if sent_file:
            # Pin the reader. Without this OpenRouter may parse the PDF with
            # another vendor's OCR and pass the text on, which would record the
            # wrong vendor as having read the page.
            #
            # `plugins` is an OpenRouter extension and the OpenAI SDK validates
            # its own parameter list, so passing it directly raises TypeError.
            # `extra_body` is the SDK's supported passthrough for exactly this.
            extra_body["plugins"] = [{"id": "file-parser", "pdf": {"engine": NATIVE_PDF_ENGINE}}]
        if self.provider_only:
            # Fail closed on serving infrastructure: OpenRouter refuses the
            # request when none of the named providers serves the model, rather
            # than quietly substituting one whose uptime this lane never chose.
            extra_body["provider"] = {"only": list(self.provider_only)}
        if extra_body:
            request["extra_body"] = extra_body
        # Reasoning effort was accepted by this signature and then dropped, so the
        # documented OPENROUTER_REASONING_EFFORT setting changed nothing. The SDK
        # accepts reasoning_effort on chat.completions; "none" means the caller
        # asked for no reasoning, so it is omitted rather than sent as a level.
        effort = (reasoning or {}).get("effort") if isinstance(reasoning, dict) else reasoning
        if isinstance(effort, str) and effort and effort != "none":
            request["reasoning_effort"] = effort
        return _Response(self.client.chat.completions.create(**request))


class _Client:
    """Small compatibility shell keeping OpenRouter provider calls walled off."""

    def __init__(self, client, provider_only=()):
        self.responses = _Responses(client, provider_only)


def build_client(credential_env, timeout_seconds=120.0, max_retries=2, provider_only=()):
    """Build an OpenRouter client with separate credentials and bounded transport."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", credential_env):
        raise ValueError("credential environment variable must be uppercase with underscores")
    if timeout_seconds <= 0 or max_retries < 0:
        raise ValueError("timeout seconds must be positive and retries non-negative")
    key = os.environ.get(credential_env)
    if not key:
        raise ValueError(f"Required credential environment variable is not set: {credential_env}")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ValueError("OpenAI SDK is required for the OpenRouter HTTP client") from exc
    base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    client = OpenAI(
        api_key=key, base_url=base_url, timeout=timeout_seconds, max_retries=max_retries
    )
    return _Client(client, validate_provider_slugs(provider_only))


def run_adapter(*args, catalogue_fetch=_fetch_catalogue, **kwargs):
    """Reuse only the shared evidence-preserving loop with OpenRouter identity.

    When the lane may submit a page PDF, native file support is verified once
    here rather than per page: a run that cannot legitimately read its corpus
    should refuse before it has spent anything.
    """
    model = kwargs.get("model", args[5] if len(args) > 5 else None)
    input_mode = kwargs.get("input_mode", args[7] if len(args) > 7 else "auto")
    if input_mode == IMAGE_MODALITY:
        require_native_input_support(model, IMAGE_MODALITY, catalogue_fetch)
    elif input_mode != "text":
        require_native_input_support(model, FILE_MODALITY, catalogue_fetch)
    kwargs.setdefault("supported_input_modes", (*DOCUMENT_INPUT_MODES, IMAGE_MODALITY))
    # The `file-parser` engine is pinned to `native` above, so the routed model
    # reads the page itself and no other vendor's OCR is interposed. Declaring
    # it lets consensus admit two different vendors through this one router
    # without having to take the router's word for their independence.
    kwargs.setdefault("source_read_by", SOURCE_READ_BY_MODEL)
    return openai_adapter.run_adapter(*args, provider_name="openrouter", **kwargs)
