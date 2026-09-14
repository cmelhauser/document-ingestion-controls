#!/usr/bin/env python3
"""OpenAI Responses adapter for evidence-preserving, per-page extraction.

This optional integration consumes an immutable ``ingestion_manifest.json`` and
emits one normalized record per retained page. It stores the raw provider
response separately, never stores credentials, never overwrites source evidence,
and turns every provider or schema failure into an explicit exception item.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import extraction_schema
from cli_help import apply_shared_help
from extraction_schema import (
    EXTRACTION_SCHEMA,
    HEADER_FIELDS,
    LINE_FIELDS,
    NULL_STYLE_UNION,
    extracted_field,
    extraction_instructions,
    literal_null,
    normalized_source_labelled_fields,
    schema_fingerprint,
    schema_sha256,
    spelled_null_finding,
    validate_run_limits,
    vertex_response_schema,
)

# The repository already renders a retained page to PNG for handwriting OCR,
# with the page range, resolution and Poppler argv it takes to do that safely.
# An image-input extraction lane needs the same render for a different reason,
# so it reuses that function rather than keeping a second copy of the argv --
# a second copy is a second place for the page range or the resolution to drift.
from google_handwriting_ocr import render_page
from llm_response import REASONING_EFFORTS, choose_input_mode, response_json, response_payload
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
    llm_model,
    load_project_env,
    model_vendor,
)

IMAGE_MEDIA_TYPE = "image/png"
DEFAULT_IMAGE_DPI = 200
MIN_IMAGE_DPI = 72
MAX_IMAGE_DPI = 600


def validate_image_dpi(dpi):
    """Reject a render resolution that cannot be read or that no vendor accepts."""
    if not isinstance(dpi, int) or isinstance(dpi, bool):
        raise ValueError("image DPI must be an integer")
    if dpi < MIN_IMAGE_DPI or dpi > MAX_IMAGE_DPI:
        raise ValueError(f"image DPI must be between {MIN_IMAGE_DPI} and {MAX_IMAGE_DPI}: {dpi}")
    return dpi


def rendered_page_image(page_path, dpi, max_image_bytes):
    """Return the retained page as PNG bytes, with the provenance of that render.

    The page PDF stays immutable; the render is a derived sibling that exists
    only for the duration of the request. Its resolution and digest travel with
    the record, because a reading is reproducible only against the exact bytes
    the model was shown, and the same page rendered at a different resolution is
    a different reading rather than the same one.

    Colour is preserved. A red parenthesised total is a negative amount on these
    statements, and dropping the channel that says so would be the silent
    normalisation this pipeline refuses everywhere else.
    """
    validate_image_dpi(dpi)
    if max_image_bytes <= 0:
        raise ValueError("maximum image bytes must be positive")
    with tempfile.TemporaryDirectory() as work:
        image_path = Path(work) / "page.png"
        render_page(page_path, image_path, dpi)
        data = image_path.read_bytes()
    if not data:
        # An empty render submitted as an image comes back as a page with
        # nothing on it, which is indistinguishable from a genuinely blank page.
        # A control that read nothing has not read the page.
        raise ValueError(f"Rendered page image is empty for {Path(page_path).name}")
    if len(data) > max_image_bytes:
        raise ValueError(
            f"Rendered page image is {len(data)} bytes, above the {max_image_bytes} limit; "
            "lower --image-dpi rather than sending a truncated page"
        )
    return data, {
        "media_type": IMAGE_MEDIA_TYPE,
        "dpi": dpi,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def page_request(
    client,
    page_path,
    model,
    reasoning_effort,
    input_mode,
    native_text=None,
    provider_name=None,
    page_image=None,
):
    """Submit a retained page as native text, its immutable page PDF, or a page image."""
    content = [{"type": "input_text", "text": "Extract this one retained business-document page."}]
    if input_mode == "text":
        content.append({"type": "input_text", "text": f"Retained native text:\n{native_text}"})
    elif input_mode == "image":
        if not page_image:
            # Never fall through to the PDF branch. A vision-only vendor sent a
            # PDF returns a page it could not read, and silently substituting a
            # different input mode would file that under the wrong provenance.
            raise ValueError("image input mode requires a rendered page image")
        encoded = base64.b64encode(page_image).decode("ascii")
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:{IMAGE_MEDIA_TYPE};base64,{encoded}",
            }
        )
    else:
        encoded = base64.b64encode(Path(page_path).read_bytes()).decode("ascii")
        content.append(
            {
                "type": "input_file",
                "filename": Path(page_path).name,
                "file_data": f"data:application/pdf;base64,{encoded}",
            }
        )
    return client.responses.create(
        model=model,
        reasoning={"effort": reasoning_effort},
        instructions=extraction_instructions(),
        input=[
            {
                "role": "user",
                "content": content,
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "business_document_page_extraction",
                "strict": True,
                "schema": request_schema(provider_name, model),
            }
        },
    )


def request_schema(provider_name, model):
    """Return the response schema in the dialect the reading vendor will serve.

    A router does not change whose API validates the schema. OpenRouter hands a
    `google/...` slug to Google, which refuses the shared schema outright for
    having too much branching -- the same refusal the Vertex lane hit, and the
    reason a Gemini probe through OpenRouter was written off earlier in this
    run as unable to serve the pipeline. So the vendor behind the slug decides
    the dialect, not the transport in front of it.

    `model_vendor` is the same resolver consensus uses, so a schema choice and
    an independence decision can never disagree about who is reading.
    """
    if model_vendor(provider_name, model) == "google":
        # This adapter always speaks the OpenAI Responses/Chat shape, whether it
        # is pointed at OpenAI or at a router. That bridge drops Vertex's
        # `nullable` flag, so nullability has to travel in the type itself or
        # the model answers a strict string with the text "null".
        return vertex_response_schema(EXTRACTION_SCHEMA, null_style=NULL_STYLE_UNION)
    return EXTRACTION_SCHEMA


def require_contract_shape(parsed, provider_name):
    """Refuse a payload that carries neither a header list nor a line list.

    A provider can return well-formed JSON that ignores the schema's structure —
    Anthropic through OpenRouter returns flat top-level keys with no ``header``
    and no ``lines``. Normalising that yields a record with every field empty,
    which is then indistinguishable from a page that genuinely had nothing on it.
    Downstream, consensus counts that engine as present and marks every field
    ``single_engine``, so a lane that contributed nothing reads as disagreement
    rather than as the provider failure it is. Refuse it here so the page becomes
    an explicit exception with its raw response retained.
    """
    header = parsed.get("header")
    lines = parsed.get("lines")
    if isinstance(header, list) or isinstance(lines, list):
        return
    raise ValueError(
        f"{provider_name} returned JSON without the extraction contract's header "
        "or lines list; the schema was not honoured and no record can be built"
    )


def normalized_record(
    page,
    parsed,
    page_path,
    raw_path,
    model,
    input_mode,
    provider_name="openai",
    page_image=None,
):
    """Convert strict model output to the record shape expected by consensus."""
    require_contract_shape(parsed, provider_name)
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
        "engine": f"{provider_name}/{model}",
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
        "openai_input_mode": input_mode,
        # Present only when the page was read as a raster. A reading is
        # reproducible against the exact bytes the model was shown, and at a
        # different DPI it is a different reading, not the same one.
        **({"page_image": page_image} if page_image else {}),
        "has_handwriting": bool(parsed.get("has_handwriting")),
        "handwriting_regions": [
            item for item in parsed.get("handwriting_regions", []) if isinstance(item, dict)
        ],
        "handwriting_readings": [
            item for item in parsed.get("handwriting_readings", []) if isinstance(item, dict)
        ],
        "header": header,
        "source_labelled_fields": normalized_source_labelled_fields(parsed, model_confidences),
        "lines": lines,
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


def failed_record(page, raw_path, model, input_mode=None, provider_name="openai", page_path=None):
    """Preserve failed-page provenance as an explicit record instead of dropping it.

    The page hash is computed from the retained page, exactly as a successful
    record computes it. Reading it from the manifest page record yielded ``None``,
    because intake does not put a ``page_sha256`` there — and a single failed page
    then blocked the whole consensus stage with "invalid page_sha256", which names
    the symptom rather than the provider failure that caused it.
    """
    return {
        "engine": f"{provider_name}/{model}",
        "engine_version": model,
        "document_id": page["page_id"],
        "page_id": page["page_id"],
        "source_file": page.get("source_file"),
        "source_page_range": page.get("source_page_range"),
        "source_page_number": page.get("source_page_number"),
        "page_pdf": page.get("page_pdf"),
        "page_sha256": sha256(page_path) if page_path else page.get("page_sha256"),
        "raw_response": str(raw_path),
        "raw_response_sha256": sha256(raw_path),
        "document_type": "unknown",
        "openai_input_mode": input_mode,
        "header": {},
        "lines": [],
        "handwriting_readings": [],
        "model_confidences": [],
        "provider_review_flags": [{"field": "page", "reason": "provider_request_failed"}],
        "review_status": "open_exception",
        "requires_independent_consensus": True,
    }


def model_exceptions(page, record, provider_name="openai"):
    """Route model uncertainty and category proposals into explicit client review."""
    items = []
    for flag in record["provider_review_flags"]:
        items.append(
            {
                "document_id": page["page_id"],
                "page_id": page["page_id"],
                "field": flag.get("field"),
                "reason": f"{provider_name}_review_flag:{flag.get('reason', 'unspecified')}",
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
                "reason": f"{provider_name}_document_type_proposal",
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
                "reason": f"{provider_name}_document_type_disagrees_with_intake_rule",
                "disposition": "client_review_required",
            }
        )
    if record.get("handwriting_regions", []):
        items.append(
            {
                "document_id": page["page_id"],
                "page_id": page["page_id"],
                "field": "handwriting_regions",
                "reason": f"{provider_name}_handwriting_region_proposal_requires_independent_htr",
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
                "reason": f"{provider_name}_handwriting_detected_requires_independent_htr",
                "disposition": "client_review_required",
                "blocking": False,
                "client_review_required": False,
            }
        )
    return items


PROVIDER_ERROR_MESSAGE_LIMIT = 2000


def write_raw(path, request, response=None, error=None, error_message=None):
    """Retain request provenance and the original provider response or failure.

    The message is retained beside the type. A bare ``error_type`` named the
    exception class and nothing else, so diagnosing a failed lane meant
    reproducing every call by hand; the provider's own words are the evidence
    that makes a retained failure usable. It is truncated because a provider can
    echo a whole request back in an error.
    """
    payload = {"request": request}
    if response is not None:
        payload["response"] = response
    if error is not None:
        payload["error_type"] = error
    if error_message is not None:
        payload["error_message"] = str(error_message)[:PROVIDER_ERROR_MESSAGE_LIMIT]
    Path(path).write_text(json.dumps(payload, indent=2, default=str) + "\n")


def validate_reasoning_effort(reasoning_effort):
    """Reject reasoning levels outside this approved adapter contract."""
    if reasoning_effort not in REASONING_EFFORTS:
        values = ", ".join(REASONING_EFFORTS)
        raise ValueError(f"reasoning effort must be one of: {values}")


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
    timeout_seconds=120.0,
    max_retries=2,
    reasoning_effort="medium",
    credential_env="OPENAI_API_KEY",
    max_workers=1,
    cache_dir=None,
    retry_backoff_seconds=1.0,
    provider_name="openai",
    max_backoff_seconds=120.0,
    lane="extraction",
    source_read_by=SOURCE_READ_BY_MODEL,
    image_dpi=DEFAULT_IMAGE_DPI,
    supported_input_modes=("auto", "text", "pdf"),
):
    """Extract bounded manifest pages, retaining every success or failure as an artifact."""
    manifest = load_manifest(manifest_path)
    validate_run_limits(max_pages, max_pdf_bytes, max_text_chars)
    validate_reasoning_effort(reasoning_effort)
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

    def process_page(item):
        """Read one retained page and return its record, retaining every failure."""
        page_index, page = item
        raw_path = raw_dir / f"{page_index:06d}_{safe_label(page['page_id'])}.json"
        selected_mode = None
        # Bound before the try: resolve_page itself can raise, and an unbound
        # name in the handler would mask the failure it is there to report.
        page_path = None
        page_image_bytes, page_image = None, None
        try:
            page_path = resolve_page(manifest_path, page, max_pdf_bytes)
            native_text = resolve_text(manifest_path, page, max_text_chars)
            selected_mode = choose_input_mode(page, native_text, input_mode, supported_input_modes)
            if selected_mode == "image":
                # Rendered once per page rather than per attempt, so a retry
                # re-sends the same bytes the cache key was computed from.
                page_image_bytes, page_image = rendered_page_image(
                    page_path, image_dpi, max_pdf_bytes
                )
            request = {
                "model": model,
                "reasoning_effort": reasoning_effort,
                "input_mode": selected_mode,
                "page_id": page["page_id"],
                "page_sha256": sha256(page_path),
                # The raster the model was shown is part of what was asked, so
                # it belongs in the cache key: the same page at a different DPI
                # must not replay a response produced from a different image.
                **({"page_image": page_image} if page_image else {}),
            }
            key = cache_key(
                {
                    "provider": provider_name,
                    "schema": "business_document_page_extraction",
                    # The schema's content, not just its name: a changed schema
                    # must not replay a response shaped by the previous one.
                    "schema_sha256": schema_fingerprint(),
                    # The dialect actually sent, which the routed vendor decides:
                    # a Google slug is served a translated schema, and a response
                    # shaped by one dialect must not replay for the other.
                    "sent_schema_sha256": schema_sha256(request_schema(provider_name, model)),
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
                        page_path,
                        model,
                        reasoning_effort,
                        selected_mode,
                        native_text,
                        provider_name,
                        page_image_bytes,
                    ),
                    max_retries,
                    retry_backoff_seconds,
                    jitter=True,
                    max_backoff_seconds=max_backoff_seconds,
                    retryable=retryable_error,
                    provider=provider_name,
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
            record = normalized_record(
                page,
                parsed,
                page_path,
                raw_path,
                model,
                selected_mode,
                provider_name,
                page_image,
            )
            return record, model_exceptions(page, record, provider_name)
        except Exception as exc:  # The provider must never be allowed to drop a retained page.
            if not raw_path.exists():
                write_raw(
                    raw_path,
                    {
                        "model": model,
                        "reasoning_effort": reasoning_effort,
                        "input_mode": selected_mode,
                        "page_id": page["page_id"],
                    },
                    error=type(exc).__name__,
                    error_message=exc,
                )
            return failed_record(page, raw_path, model, selected_mode, provider_name, page_path), [
                {
                    "document_id": page["page_id"],
                    "page_id": page["page_id"],
                    "reason": f"{provider_name}_provider_or_schema_failure",
                    "disposition": "client_review_required",
                }
            ]

    results = parallel_map(list(enumerate(manifest["pages"], start=1)), process_page, max_workers)
    records = [result[0] for result in results]
    exceptions = [item for result in results for item in result[1]]
    out_path.write_text(json.dumps(records, indent=2) + "\n")
    context = extraction_schema.corpus_context()
    adapter = {
        "schema_version": "independent_extraction_handoff_v1",
        "adapter_type": "ocr",
        "engine": f"{provider_name}/{model}",
        "provider": provider_name,
        "model": model,
        "lane": lane,
        "independence_group": provider_name,
        # Who read the source bytes. Consensus needs this to tell two lanes
        # sharing a router apart from two lanes sharing a reading.
        "source_read_by": source_read_by,
        "records_sha256": hashlib.sha256(
            json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest(),
        "model_configuration": {
            "model": model,
            "reasoning_effort": reasoning_effort,
        },
        # The corpus context that conditioned this reading, by name and hash and
        # never by content: the handoff is provenance, not a second copy of the
        # prompt. ``consensus`` compares these across lanes, because two engines
        # given different notes are no longer reading the page independently.
        "corpus_context": (
            None if context is None else {k: v for k, v in context.items() if k != "text"}
        ),
        "records": records,
        "credential_reference": credential_env,
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


def build_client(credential_env, timeout_seconds=120.0, max_retries=2):
    """Load the SDK with bounded transport controls and without exposing credentials."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", credential_env):
        raise ValueError("credential environment variable must be uppercase with underscores")
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise ValueError("timeout seconds must be positive")
    if not isinstance(max_retries, int) or max_retries < 0:
        raise ValueError("max retries must be a non-negative integer")
    key = os.environ.get(credential_env)
    if not key:
        raise ValueError(f"Required credential environment variable is not set: {credential_env}")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ValueError("OpenAI SDK is not installed; install requirements-dev.txt") from exc
    return OpenAI(api_key=key, timeout=timeout_seconds, max_retries=max_retries)


def main():
    try:
        load_project_env()
        defaults = {
            "model": llm_model("openai"),
            "reasoning_effort": env_value("OPENAI_REASONING_EFFORT", "medium"),
            "credential_env": env_value("OPENAI_CREDENTIAL_ENV", "OPENAI_API_KEY"),
            "max_pages": env_int("OPENAI_MAX_PAGES", 1000),
            "max_pdf_bytes": env_int("OPENAI_MAX_PDF_BYTES", 10_000_000),
            "max_text_chars": env_int("OPENAI_MAX_TEXT_CHARS", 100_000),
            "timeout_seconds": env_float("OPENAI_TIMEOUT_SECONDS", 120.0),
            "max_retries": env_int("OPENAI_MAX_RETRIES", 2),
            "input_mode": env_value("OPENAI_INPUT_MODE", "auto"),
        }
    except ValueError as exc:
        sys.exit(f"OpenAI adapter failed: {exc}")
    parser = argparse.ArgumentParser(
        description="Extract immutable intake pages with the OpenAI Responses API."
    )
    parser.add_argument(
        "manifest", help="ingestion_manifest.json created by scripts/ingest_pages.py"
    )
    parser.add_argument(
        "--out", required=True, help="new normalized per-engine record list for consensus"
    )
    parser.add_argument(
        "--adapter-out", required=True, help="new provider handoff object for operations.py"
    )
    parser.add_argument("--exceptions", required=True, help="new provider/schema exception queue")
    parser.add_argument(
        "--raw-dir", required=True, help="new or empty directory for raw per-page responses"
    )
    parser.add_argument(
        "--model", default=defaults["model"], help="client-approved OpenAI vision model"
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=REASONING_EFFORTS,
        default=defaults["reasoning_effort"],
        help="explicit model reasoning effort recorded in non-secret run artifacts",
    )
    parser.add_argument("--credential-env", default=defaults["credential_env"])
    parser.add_argument("--max-pages", type=int, default=defaults["max_pages"])
    parser.add_argument("--max-pdf-bytes", type=int, default=defaults["max_pdf_bytes"])
    parser.add_argument("--max-text-chars", type=int, default=defaults["max_text_chars"])
    parser.add_argument("--timeout-seconds", type=float, default=defaults["timeout_seconds"])
    parser.add_argument("--max-retries", type=int, default=defaults["max_retries"])
    parser.add_argument(
        "--input-mode",
        choices=("auto", "text", "pdf"),
        default=defaults["input_mode"],
        help="auto uses native text only for rule-classified pages; otherwise use the retained PDF",
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        result = run_adapter(
            args.manifest,
            args.out,
            args.adapter_out,
            args.exceptions,
            args.raw_dir,
            args.model,
            build_client(args.credential_env, args.timeout_seconds, args.max_retries),
            args.input_mode,
            args.max_pages,
            args.max_pdf_bytes,
            args.max_text_chars,
            args.timeout_seconds,
            args.max_retries,
            args.reasoning_effort,
            args.credential_env,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"OpenAI adapter failed: {exc}")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
