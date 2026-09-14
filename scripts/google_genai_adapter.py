#!/usr/bin/env python3
"""Vertex AI Gemini adapter for evidence-preserving, per-page extraction.

This optional provider implementation reads the same immutable intake manifest as
the OpenAI adapter and produces the same consensus-ready proposal contract. It
uses the Google Gen AI SDK with ``vertexai=True`` so inference is billed to the
specified Google Cloud project through Application Default Credentials (ADC).
It never approves a field, changes retained source evidence, or drops a page
when a provider request or response fails.
"""

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path

from cli_help import apply_shared_help
from extraction_schema import (
    EXTRACTION_SCHEMA,
    HEADER_FIELDS,
    LINE_FIELDS,
    VERTEX_ENUM_LIMIT,
    corpus_context,
    extracted_field,
    extraction_instructions,
    literal_null,
    normalized_source_labelled_fields,
    relaxed_enum_paths,
    schema_fingerprint,
    schema_sha256,
    spelled_null_finding,
    validate_run_limits,
    vertex_response_schema,
)
from llm_response import choose_input_mode
from llm_runtime import (
    ResponseCache,
    cache_key,
    estimate_tokens,
    parallel_map,
    retry_call,
    retryable_error,
)
from run_io import (
    empty_output_directory,
    empty_output_path,
    load_manifest,
    resolve_page,
    resolve_text,
    safe_label,
    sha256,
)
from runtime_config import (
    SOURCE_READ_BY_MODEL,
    env_float,
    env_int,
    env_value,
    load_project_env,
    require_google_capability_limits,
)

PROJECT_ID = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")
LOCATION = re.compile(r"[a-z]+(?:-[a-z0-9]+)+")


def validate_vertex_configuration(project_id, location, timeout_seconds, max_retries):
    """Validate non-secret Vertex settings before the SDK creates a client."""
    if not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id):
        raise ValueError("Vertex project ID must be a valid Google Cloud project ID")
    if not isinstance(location, str) or not LOCATION.fullmatch(location):
        raise ValueError("Vertex location must be a region such as us-central1")
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise ValueError("timeout seconds must be positive")
    if not isinstance(max_retries, int) or max_retries < 0:
        raise ValueError("max retries must be a non-negative integer")


def load_sdk():
    """Import the optional SDK only when this provider is selected."""
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
        raise ValueError(
            "Google Gen AI SDK is not installed; install requirements-dev.txt"
        ) from exc
    return genai, types


def build_client(project_id, location, timeout_seconds=180.0, max_retries=4, sdk=None):
    """Create the Vertex-backed SDK client using ADC and bounded transport settings."""
    validate_vertex_configuration(project_id, location, timeout_seconds, max_retries)
    genai, types = sdk or load_sdk()
    return genai.Client(
        vertexai=True,
        project=project_id,
        location=location,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(
                initial_delay=1.0,
                attempts=max_retries + 1,
                http_status_codes=[408, 429, 500, 502, 503, 504],
            ),
            timeout=int(timeout_seconds * 1000),
        ),
    )


class VertexResponsesClient:
    """Small Responses-API compatibility boundary for review-only LLM lanes.

    Optional review lanes pre-date the run-level provider selector and express
    their constrained requests in the OpenAI Responses shape.  Keeping the
    translation here makes the provider selection atomic without changing the
    evidence or review contracts those lanes already enforce.
    """

    def __init__(self, client, types, max_output_tokens=8192):
        self._client = client
        self._types = types
        if not isinstance(max_output_tokens, int) or max_output_tokens < 1:
            raise ValueError("max output tokens must be positive")
        self._max_output_tokens = max_output_tokens
        self.responses = self

    def create(self, *, model, instructions, input, text, reasoning=None):
        """Create the Vertex client after validating every non-secret setting."""
        del reasoning  # Gemini does not expose the OpenAI reasoning-effort control.
        parts = []
        for message in input:
            for item in message.get("content", []):
                if item.get("type") == "input_text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "input_file":
                    encoded = item.get("file_data", "")
                    prefix = "data:application/pdf;base64,"
                    if not encoded.startswith(prefix):
                        raise ValueError("Vertex compatibility client accepts PDF input files only")
                    parts.append(
                        self._types.Part.from_bytes(
                            data=base64.b64decode(encoded[len(prefix) :]),
                            mime_type="application/pdf",
                        )
                    )
                else:
                    raise ValueError(
                        "Vertex compatibility client received unsupported input content"
                    )
        schema = text.get("format", {}).get("schema")
        if not isinstance(schema, dict):
            raise ValueError("Vertex compatibility client requires a JSON schema")
        response = self._client.models.generate_content(
            model=model,
            contents=parts,
            config=self._types.GenerateContentConfig(
                system_instruction=instructions,
                response_mime_type="application/json",
                # Translated: a review lane's schema meets the same Vertex
                # branching limit as the extraction schema does.
                response_json_schema=vertex_response_schema(schema),
                max_output_tokens=self._max_output_tokens,
            ),
        )
        return VertexResponsesResponse(response)


class VertexResponsesResponse:
    """Expose the narrow response interface consumed by existing audit lanes."""

    def __init__(self, response):
        self._response = response
        self.output_text = response.text

    def model_dump(self, *args, **kwargs):
        """Return the retained raw payload, matching the SDK method audit lanes call.

        The retained raw response is the authority over any configuration file,
        so this returns the provider's own payload rather than a reshaped view
        of it.
        """
        del args, kwargs
        return response_payload(self._response)


def build_responses_client(
    project_id, location, timeout_seconds=180.0, max_retries=4, max_output_tokens=8192
):
    """Create a Vertex client usable by every run-level LLM review lane."""
    client = build_client(project_id, location, timeout_seconds, max_retries)
    _, types = load_sdk()
    return VertexResponsesClient(client, types, max_output_tokens)


def response_payload(response):
    """Serialize the complete provider response for retained raw evidence."""
    if hasattr(response, "model_dump"):
        try:
            return response.model_dump(mode="json")
        except TypeError:
            return response.model_dump()
    if hasattr(response, "to_json"):
        payload = response.to_json()
        return json.loads(payload) if isinstance(payload, str) else payload
    return response


def response_json(response):
    """Decode structured output and reject non-object provider responses."""
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, dict):
        return parsed
    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Google Gen AI response did not contain structured output text")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Google Gen AI structured output must be an object")
    return data


def page_request(
    client,
    types,
    page_path,
    model,
    input_mode,
    native_text,
    response_schema,
    max_output_tokens=None,
):
    """Submit either retained text or an immutable one-page PDF to Gemini."""
    contents = ["Extract this one retained business-document page."]
    if input_mode == "text":
        contents.append(f"Retained native text:\n{native_text}")
    else:
        contents.append(
            types.Part.from_bytes(data=Path(page_path).read_bytes(), mime_type="application/pdf")
        )
    config = {
        "system_instruction": extraction_instructions(),
        "response_mime_type": "application/json",
        "response_json_schema": response_schema,
    }
    if max_output_tokens is not None:
        config["max_output_tokens"] = max_output_tokens
    return client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(**config),
    )


def normalized_record(page, parsed, page_path, raw_path, model, input_mode):
    """Convert a Gemini proposal to the shared engine-record contract."""
    header = {}
    model_confidences = []
    spelled_null = 0
    for item in parsed.get("header", []):
        if isinstance(item, dict) and item.get("name") in HEADER_FIELDS:
            field = extracted_field(item)
            if field is not None:
                header[item["name"]] = field
                model_confidences.append(
                    {
                        "field": f"header.{item['name']}",
                        "value": field["model_confidence"],
                    }
                )
    lines = []
    for item in parsed.get("lines", []):
        if not isinstance(item, dict):
            continue
        line = {"line_number": item.get("line_number")}
        for name in LINE_FIELDS:
            value = item.get(name)
            # A cell the model marked empty is empty however the transport made
            # it spell that. Left standing, the text "null" is not None and
            # enters consensus as a value this engine claims to have read.
            if literal_null(value):
                # Counted as well as dropped. Dropping alone would turn a broken
                # schema dialect into a quiet loss of cells, which is the exact
                # shape of the defect this guards against.
                spelled_null += 1
                continue
            if value is not None:
                line[name] = {"value": value, "confidence": None, "source": "printed"}
        if any(value is not None for key, value in line.items() if key != "line_number"):
            lines.append(line)
            model_confidences.append(
                {
                    "field": f"lines[{len(lines) - 1}]",
                    "value": item.get("line_model_confidence"),
                }
            )
    flags = [item for item in parsed.get("review_flags", []) if isinstance(item, dict)]
    return {
        "engine": f"google_genai_vertex/{model}",
        "engine_version": model,
        "document_id": page["page_id"],
        "page_id": page["page_id"],
        "source_file": page.get("source_file"),
        "source_page_range": page.get("source_page_range"),
        "source_page_number": page.get("source_page_number"),
        "page_pdf": page.get("page_pdf"),
        "page_sha256": sha256(page_path),
        "raw_response": str(raw_path),
        "raw_response_sha256": sha256(raw_path),
        "document_type": page.get("document_type") or "unknown",
        "model_document_type": parsed.get("document_type", "unknown"),
        "google_genai_input_mode": input_mode,
        "has_handwriting": bool(parsed.get("has_handwriting")),
        "handwriting_regions": [
            item for item in parsed.get("handwriting_regions", []) if isinstance(item, dict)
        ],
        "handwriting_readings": [
            item for item in parsed.get("handwriting_readings", []) if isinstance(item, dict)
        ],
        "header": header,
        "lines": lines,
        "source_labelled_fields": normalized_source_labelled_fields(parsed, model_confidences),
        "model_confidences": model_confidences,
        "provider_review_flags": flags,
        "review_status": "review_queued"
        if flags or parsed.get("has_handwriting")
        else "pending_consensus",
        # How many cells this page spelled as the text "null" rather than
        # returning as JSON null. A response schema whose nullability the
        # transport dropped produces these in bulk.
        "cells_spelled_null": spelled_null,
        "requires_independent_consensus": True,
    }


def failed_record(page, raw_path, model, input_mode=None):
    """Retain a failed page as an exception record rather than omitting it."""
    return {
        "engine": f"google_genai_vertex/{model}",
        "engine_version": model,
        "document_id": page["page_id"],
        "page_id": page["page_id"],
        "source_file": page.get("source_file"),
        "source_page_range": page.get("source_page_range"),
        "source_page_number": page.get("source_page_number"),
        "page_pdf": page.get("page_pdf"),
        "page_sha256": page.get("page_sha256"),
        "raw_response": str(raw_path),
        "raw_response_sha256": sha256(raw_path),
        "document_type": "unknown",
        "google_genai_input_mode": input_mode,
        "header": {},
        "lines": [],
        "handwriting_readings": [],
        "model_confidences": [],
        "provider_review_flags": [{"field": "page", "reason": "provider_request_failed"}],
        "review_status": "open_exception",
        "requires_independent_consensus": True,
    }


def model_exceptions(page, record):
    """Route all Gemini uncertainty, type proposals, and handwriting to review."""
    items = []
    for flag in record["provider_review_flags"]:
        items.append(
            {
                "document_id": page["page_id"],
                "page_id": page["page_id"],
                "field": flag.get("field"),
                "reason": f"google_genai_review_flag:{flag.get('reason', 'unspecified')}",
                "evidence_text": flag.get("evidence_text"),
                "disposition": "client_review_required",
            }
        )
    candidate = record["model_document_type"]
    intake_type = page.get("document_type")
    if page.get("classification_status") != "rule_classified":
        items.append(
            {
                "document_id": page["page_id"],
                "page_id": page["page_id"],
                "field": "document_type",
                "candidate_value": candidate,
                "reason": "google_genai_document_type_proposal",
                "disposition": "client_review_required",
            }
        )
    elif candidate not in ("unknown", intake_type):
        items.append(
            {
                "document_id": page["page_id"],
                "page_id": page["page_id"],
                "field": "document_type",
                "intake_value": intake_type,
                "candidate_value": candidate,
                "reason": "google_genai_document_type_disagrees_with_intake_rule",
                "disposition": "client_review_required",
            }
        )
    if record.get("handwriting_regions", []):
        items.append(
            {
                "document_id": page["page_id"],
                "page_id": page["page_id"],
                "field": "handwriting_regions",
                "reason": "google_genai_handwriting_region_proposal_requires_independent_htr",
                "disposition": "client_review_required",
                "blocking": False,
                "client_review_required": False,
            }
        )
    if record["has_handwriting"]:
        items.append(
            {
                "document_id": page["page_id"],
                "page_id": page["page_id"],
                "field": "page",
                "reason": "google_genai_handwriting_detected_requires_independent_htr",
                "disposition": "client_review_required",
                "blocking": False,
                "client_review_required": False,
            }
        )
    return items


def write_raw(path, request, response=None, error=None):
    """Retain non-secret request provenance and the complete result or error type."""
    payload = {"request": request}
    if response is not None:
        payload["response"] = response
    if error is not None:
        payload["error_type"] = error
    Path(path).write_text(json.dumps(payload, indent=2, default=str) + "\n")


def run_adapter(
    manifest_path,
    out_path,
    adapter_path,
    exceptions_path,
    raw_dir,
    model,
    client,
    input_mode="auto",
    max_pages=500,
    max_pdf_bytes=10_000_000,
    max_text_chars=100_000,
    project_id=None,
    location="us-central1",
    timeout_seconds=180.0,
    max_retries=4,
    credential_env="GOOGLE_APPLICATION_CREDENTIALS",
    types=None,
    max_workers=1,
    cache_dir=None,
    retry_backoff_seconds=1.0,
    max_backoff_seconds=120.0,
    lane="extraction",
    max_output_tokens=None,
):
    """Extract all retained pages while recording every success or failure."""
    if not project_id:
        raise ValueError("Vertex project ID is required")
    validate_vertex_configuration(project_id, location, timeout_seconds, max_retries)
    validate_run_limits(max_pages, max_pdf_bytes, max_text_chars)
    if types is None:
        _, types = load_sdk()
    manifest = load_manifest(manifest_path)
    if len(manifest["pages"]) > max_pages:
        raise ValueError(
            f"Manifest pages exceed configured limit: {len(manifest['pages'])} > {max_pages}"
        )
    target_paths = [Path(value).resolve() for value in (out_path, adapter_path, exceptions_path)]
    if len(set(target_paths)) != len(target_paths):
        raise ValueError("Output, adapter, and exception paths must be distinct")
    out_path = empty_output_path(out_path)
    adapter_path = empty_output_path(adapter_path)
    exceptions_path = empty_output_path(exceptions_path)
    raw_dir = empty_output_directory(raw_dir)
    cache = ResponseCache(cache_dir)
    # One translation for the whole run: the schema does not vary by page, and
    # its fingerprint has to enter the cache key below.
    response_schema = vertex_response_schema(EXTRACTION_SCHEMA)
    response_schema_sha256 = schema_sha256(response_schema)
    relaxed_enums = relaxed_enum_paths(EXTRACTION_SCHEMA)

    def process_page(item):
        """Read one retained page and return its record, retaining every failure."""
        page_index, page = item
        raw_path = raw_dir / f"{page_index:06d}_{safe_label(page['page_id'])}.json"
        selected_mode = None
        try:
            page_path = resolve_page(manifest_path, page, max_pdf_bytes)
            native_text = resolve_text(manifest_path, page, max_text_chars)
            selected_mode = choose_input_mode(page, native_text, input_mode)
            request = {
                "model": model,
                "input_mode": selected_mode,
                "page_id": page["page_id"],
                "page_sha256": sha256(page_path),
            }
            key = cache_key(
                {
                    "provider": "google",
                    "schema": "business_document_page_extraction",
                    # The schema's content, not just its name: a changed schema
                    # must not replay a response shaped by the previous one.
                    "schema_sha256": schema_fingerprint(),
                    # The translated schema is what shaped the response, so a
                    # changed translation must not replay the previous one
                    # either.
                    "vertex_schema_sha256": response_schema_sha256,
                    "prompt": extraction_instructions(),
                    **request,
                }
            )
            cached = cache.read(key)
            # A cache hit replays a retained response rather than calling the
            # provider. The retained raw artifact must say which of the two it is,
            # or an auditor cannot tell a genuine second reading from a replay.
            request["cache_hit"] = bool(cached)
            request["cache_key"] = key
            request["cache_dir"] = str(cache.directory) if cache.directory else None
            if cached:
                request["cache_written_at"] = cached.get("written_at")
                parsed, payload = cached["parsed"], cached["payload"]
            else:
                response = retry_call(
                    lambda: page_request(
                        client,
                        types,
                        page_path,
                        model,
                        selected_mode,
                        native_text,
                        response_schema,
                        max_output_tokens,
                    ),
                    max_retries,
                    retry_backoff_seconds,
                    jitter=True,
                    max_backoff_seconds=max_backoff_seconds,
                    retryable=retryable_error,
                    provider="google",
                    request_tokens=estimate_tokens(
                        {
                            "instructions": extraction_instructions(),
                            "native_text": native_text or "",
                        }
                        if selected_mode == "text"
                        else {"instructions": extraction_instructions(), "page_id": page["page_id"]}
                    ),
                )
                payload = response_payload(response)
                write_raw(raw_path, request, response=payload)
                parsed = response_json(response)
                cache.write(key, {"parsed": parsed, "payload": payload})
            if cached:
                write_raw(raw_path, request, response=payload)
            record = normalized_record(page, parsed, page_path, raw_path, model, selected_mode)
            return record, model_exceptions(page, record)
        except Exception as exc:  # Provider failures must not silently omit a page.
            if not raw_path.exists():
                write_raw(
                    raw_path,
                    {"model": model, "input_mode": selected_mode, "page_id": page["page_id"]},
                    error=type(exc).__name__,
                )
            return failed_record(page, raw_path, model, selected_mode), [
                {
                    "document_id": page["page_id"],
                    "page_id": page["page_id"],
                    "reason": "google_genai_provider_or_schema_failure",
                    "disposition": "client_review_required",
                }
            ]

    results = parallel_map(list(enumerate(manifest["pages"], start=1)), process_page, max_workers)
    records = [result[0] for result in results]
    exceptions = [item for result in results for item in result[1]]
    out_path.write_text(json.dumps(records, indent=2) + "\n")
    # The conditioning is already in the prompt -- `extraction_instructions()`
    # appends it, and it is part of this lane's cache key. What was missing was
    # saying so on the handoff, and `consensus.py` compares lanes on this field:
    # a conditioned Vertex reading declared `None` was refused as conditioned
    # differently from its own pair. The lane had done the right thing and could
    # not prove it.
    context = corpus_context()
    adapter = {
        "schema_version": "independent_extraction_handoff_v1",
        "adapter_type": "ocr",
        "engine": f"google_genai_vertex/{model}",
        "provider": "google",
        "model": model,
        "lane": lane,
        "independence_group": "google",
        # Gemini is handed the retained page itself; nothing reads it first.
        "source_read_by": SOURCE_READ_BY_MODEL,
        "records_sha256": hashlib.sha256(
            json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest(),
        "corpus_context": (
            None if context is None else {k: v for k, v in context.items() if k != "text"}
        ),
        "model_configuration": {
            "provider": "vertex_ai",
            "model": model,
            "project_id": project_id,
            "location": location,
            "max_output_tokens": max_output_tokens,
        },
        # Vertex is served a translated schema. Naming the relaxed fields keeps
        # the one weakened constraint auditable instead of invisible.
        "response_schema": {
            "dialect": "vertex_structured_output",
            "translated_from_sha256": schema_fingerprint(),
            "sent_sha256": response_schema_sha256,
            "enum_limit": VERTEX_ENUM_LIMIT,
            "relaxed_enum_paths": relaxed_enums,
        },
        "records": records,
        "credential_reference": credential_env,
        "authentication": "application_default_credentials",
        "raw_response_directory": str(raw_dir),
        "raw_response_retention_required": True,
        "run_limits": {
            "max_pages": max_pages,
            "max_pdf_bytes": max_pdf_bytes,
            "max_text_chars": max_text_chars,
        },
        "transport": {
            "timeout_seconds": timeout_seconds,
            "max_retries": max_retries,
            "retry_backoff_seconds": retry_backoff_seconds,
        },
        "execution": {
            "max_workers": max_workers,
            "cache_dir": str(cache.directory) if cache.directory else None,
        },
    }
    adapter_path.write_text(json.dumps(adapter, indent=2) + "\n")
    # A lane whose cells are largely the text "null" has a broken schema
    # dialect, not empty pages, and must not report a clean run.
    nullability, spelled_null = spelled_null_finding(records)
    if nullability is not None:
        exceptions.append(nullability)
    exceptions_path.write_text(
        json.dumps(
            {
                "summary": {"count": len(exceptions), "cells_spelled_null": spelled_null},
                "exceptions": exceptions,
            },
            indent=2,
        )
        + "\n"
    )
    return {"pages": len(records), "review_items": len(exceptions), "engine": adapter["engine"]}


def main():
    try:
        load_project_env()
        require_google_capability_limits()
        defaults = {
            "model": env_value("GOOGLE_VERTEX_AI_MODEL", "gemini-2.5-pro"),
            "project_id": env_value("GOOGLE_VERTEX_AI_PROJECT_ID", ""),
            "location": env_value("GOOGLE_VERTEX_AI_LOCATION", "us-central1"),
            "credential_env": env_value(
                "GOOGLE_VERTEX_AI_CREDENTIAL_ENV", "GOOGLE_APPLICATION_CREDENTIALS"
            ),
            "max_pages": env_int("GOOGLE_VERTEX_AI_MAX_PAGES", 1000),
            "max_pdf_bytes": env_int("GOOGLE_VERTEX_AI_MAX_PDF_BYTES", 10_000_000),
            "max_text_chars": env_int("GOOGLE_VERTEX_AI_MAX_TEXT_CHARS", 100_000),
            "timeout_seconds": env_float("GOOGLE_VERTEX_AI_TIMEOUT_SECONDS", 180.0),
            "max_retries": env_int("GOOGLE_VERTEX_AI_MAX_RETRIES", 4),
            "input_mode": env_value("GOOGLE_VERTEX_AI_INPUT_MODE", "auto"),
            # A commission page can carry fifty columns over forty rows, and the
            # schema requires every one of them on every row. Ask for the
            # model's whole output ceiling: a truncated response is a lost page,
            # not a shorter one.
            "max_output_tokens": env_int("GOOGLE_VERTEX_AI_MODEL_MAX_OUTPUT_TOKENS", 65536),
            "max_workers": env_int("GOOGLE_VERTEX_AI_MAX_WORKERS", 8),
            "cache_dir": env_value("LLM_CACHE_DIR", ".llm-cache"),
            "retry_backoff_seconds": env_float("GOOGLE_VERTEX_AI_RETRY_BACKOFF_SECONDS", 1.0),
            "max_backoff_seconds": env_float("GOOGLE_VERTEX_AI_MAX_BACKOFF_SECONDS", 120.0),
        }
    except ValueError as exc:
        sys.exit(f"Google Gen AI adapter failed: {exc}")
    parser = argparse.ArgumentParser(
        description="Extract immutable intake pages with Gemini on Vertex AI."
    )
    parser.add_argument(
        "manifest", help="ingestion_manifest.json created by scripts/ingest_pages.py"
    )
    parser.add_argument("--out", required=True, help="new normalized per-engine record list")
    parser.add_argument("--adapter-out", required=True, help="new provider handoff object")
    parser.add_argument("--exceptions", required=True, help="new provider/schema exception queue")
    parser.add_argument("--raw-dir", required=True, help="new or empty raw-response directory")
    parser.add_argument("--model", default=defaults["model"], help="client-approved Gemini model")
    parser.add_argument("--project-id", default=defaults["project_id"])
    parser.add_argument("--location", default=defaults["location"])
    parser.add_argument("--credential-env", default=defaults["credential_env"])
    parser.add_argument("--max-pages", type=int, default=defaults["max_pages"])
    parser.add_argument("--max-pdf-bytes", type=int, default=defaults["max_pdf_bytes"])
    parser.add_argument("--max-text-chars", type=int, default=defaults["max_text_chars"])
    parser.add_argument("--timeout-seconds", type=float, default=defaults["timeout_seconds"])
    parser.add_argument("--max-retries", type=int, default=defaults["max_retries"])
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=defaults["max_output_tokens"],
        help="Output ceiling for one page; a wide table truncates below the model maximum.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=defaults["max_workers"],
        help="Pages read concurrently. A dense page takes minutes, so 1 is hours per hundred.",
    )
    parser.add_argument(
        "--cache-dir",
        default=defaults["cache_dir"],
        help=(
            "Directory of retained responses replayed instead of calling the provider. "
            "Clear it for a genuinely fresh reading."
        ),
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=defaults["retry_backoff_seconds"],
        help="Initial delay before a retried page request.",
    )
    parser.add_argument(
        "--max-backoff-seconds",
        type=float,
        default=defaults["max_backoff_seconds"],
        help="Ceiling on that delay, so a failing page cannot stall the lane indefinitely.",
    )
    parser.add_argument(
        "--input-mode",
        choices=("auto", "text", "pdf"),
        default=defaults["input_mode"],
        help="Submit native page text, the page PDF, or auto to use text only on safely rule-classified pages.",
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        sdk = load_sdk()
        result = run_adapter(
            args.manifest,
            args.out,
            args.adapter_out,
            args.exceptions,
            args.raw_dir,
            args.model,
            build_client(
                args.project_id, args.location, args.timeout_seconds, args.max_retries, sdk
            ),
            args.input_mode,
            args.max_pages,
            args.max_pdf_bytes,
            args.max_text_chars,
            args.project_id,
            args.location,
            args.timeout_seconds,
            args.max_retries,
            args.credential_env,
            sdk[1],
            max_workers=args.max_workers,
            cache_dir=args.cache_dir or None,
            retry_backoff_seconds=args.retry_backoff_seconds,
            max_backoff_seconds=args.max_backoff_seconds,
            max_output_tokens=args.max_output_tokens,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Google Gen AI adapter failed: {exc}")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
