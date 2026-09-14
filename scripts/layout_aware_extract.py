#!/usr/bin/env python3
"""Extract review-only rows into a supplied client-layout vocabulary.

Each invocation submits exactly one immutable intake page.  This makes lengthy
vision requests resumable under constrained worker time limits while retaining
the raw provider response and never turning a proposed row into a client
approved or canonical record.
"""

import argparse
import base64
import json
import re
import sys
from pathlib import Path

from cli_help import apply_shared_help
from llm_provider import build_client
from llm_response import REASONING_EFFORTS, choose_input_mode, response_json, response_payload
from llm_runtime import estimate_tokens, retry_call, retryable_error
from run_io import (
    empty_output_directory,
    empty_output_path,
    load_manifest,
    resolve_page,
    resolve_text,
    sha256,
)
from runtime_config import env_float, env_int, env_value, llm_model, load_project_env


def nullable_string():
    """Return the strict schema used for one visible text cell."""
    return {"anyOf": [{"type": "string"}, {"type": "null"}]}


def load_layout(path):
    """Read a non-secret, explicit visual-layout contract."""
    data = json.loads(Path(path).read_text())
    columns = data.get("columns") if isinstance(data, dict) else None
    valid = (
        isinstance(data.get("layout_id"), str)
        and data["layout_id"].strip()
        and isinstance(columns, list)
        and columns
        and all(
            isinstance(item, dict)
            and isinstance(item.get("key"), str)
            and re.fullmatch(r"[a-z][a-z0-9_]*", item["key"])
            and isinstance(item.get("display_label"), str)
            and item["display_label"].strip()
            for item in columns
        )
    )
    if not valid:
        raise ValueError("Layout requires layout_id and non-empty key/display_label columns")
    keys = [item["key"] for item in columns]
    if len(keys) != len(set(keys)):
        raise ValueError("Layout column keys must be unique")
    return data


def selected_page(manifest, page_id):
    """Select exactly one immutable page for a resumable provider request."""
    matches = [item for item in manifest["pages"] if item.get("page_id") == page_id]
    if len(matches) != 1:
        raise ValueError("page_id must identify exactly one page in the intake manifest")
    return matches[0]


def extraction_schema(layout):
    """Build a strict row schema from the reviewed layout contract."""
    properties = {column["key"]: nullable_string() for column in layout["columns"]}
    properties.update(
        {
            "row_number": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            "source_evidence": {"type": "string"},
            "has_handwriting": {"type": "boolean"},
            "review_flags": {"type": "array", "items": {"type": "string"}},
            "unmapped_visible_values": {"type": "array", "items": {"type": "string"}},
        }
    )
    row = {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["rows", "page_notes"],
        "properties": {
            "rows": {"type": "array", "items": row},
            "page_notes": {"type": "array", "items": {"type": "string"}},
        },
    }


def instructions(layout):
    """State the evidence and review boundary in every provider prompt."""
    labels = "; ".join(f"{item['key']} = {item['display_label']}" for item in layout["columns"])
    return (
        "Extract visible table rows from one photographed business page into the supplied "
        "layout columns. The layout is a display target, not proof of a canonical mapping. "
        f"Columns: {labels}. Return null for unavailable cells, preserve visible spelling and "
        "numerals, put unsafe mappings in unmapped_visible_values, and add review flags for "
        "handwriting, ambiguity, row boundaries, or image quality. Do not infer, correct, "
        "approve, aggregate, or create canonical facts."
    )


def page_request(client, page_path, layout, model, reasoning_effort, input_mode, native_text=None):
    """Submit one retained PDF page with a strict layout-aware proposal schema."""
    if input_mode == "text":
        content = [{"type": "input_text", "text": f"Retained native text:\n{native_text}"}]
    else:
        encoded = base64.b64encode(Path(page_path).read_bytes()).decode("ascii")
        content = [
            {"type": "input_text", "text": "Extract this immutable retained page."},
            {
                "type": "input_file",
                "filename": Path(page_path).name,
                "file_data": f"data:application/pdf;base64,{encoded}",
            },
        ]
    return client.responses.create(
        model=model,
        reasoning={"effort": reasoning_effort},
        instructions=instructions(layout),
        input=[
            {
                "role": "user",
                "content": content,
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "layout_aware_business_rows",
                "strict": True,
                "schema": extraction_schema(layout),
            }
        },
    )


def write_raw(path, request, response=None, error=None):
    """Keep original provider output or failure provenance outside normalized rows."""
    payload = {"request": request}
    if response is not None:
        payload["response"] = response
    if error is not None:
        payload["error_type"] = error
    Path(path).write_text(json.dumps(payload, indent=2, default=str) + "\n")


def review_item(page, field, reason):
    """Create the standard review record without interpreting a proposal as fact."""
    return {
        "priority": "high",
        "document_id": page["page_id"],
        "page_id": page["page_id"],
        "region_id": "",
        "field": field,
        "reason": reason,
        "review_source": "layout_aware_extraction",
        "disposition": "client_review_required",
    }


def run_page(
    manifest_path,
    layout_path,
    page_id,
    out_path,
    raw_dir,
    model,
    client,
    effort,
    max_pdf_bytes,
    credential_env,
    max_retries=2,
    retry_backoff_seconds=1.0,
):
    """Produce one no-clobber page proposal so long runs resume safely."""
    manifest = load_manifest(manifest_path)
    layout = load_layout(layout_path)
    page = selected_page(manifest, page_id)
    out_path = empty_output_path(out_path)
    raw_dir = empty_output_directory(raw_dir)
    raw_path = raw_dir / "response.json"
    try:
        page_path = resolve_page(manifest_path, page, max_pdf_bytes)
        native_text = resolve_text(manifest_path, page, 100_000)
        input_mode = choose_input_mode(page, native_text, "auto")
        request = {
            "model": model,
            "reasoning_effort": effort,
            "page_id": page_id,
            "page_sha256": sha256(page_path),
            "layout_id": layout["layout_id"],
            "input_mode": input_mode,
        }
        response = retry_call(
            lambda: page_request(client, page_path, layout, model, effort, input_mode, native_text),
            max_retries,
            retry_backoff_seconds,
            jitter=True,
            max_backoff_seconds=30.0,
            retryable=retryable_error,
            request_tokens=estimate_tokens(request),
        )
        write_raw(raw_path, request, response=response_payload(response))
        parsed = response_json(response)
        record = {
            "engine": f"openai/{model}",
            "engine_version": model,
            "document_id": page_id,
            "page_id": page_id,
            "page_pdf": page["page_pdf"],
            "page_sha256": sha256(page_path),
            "layout_id": layout["layout_id"],
            "layout_columns": layout["columns"],
            "llm_input_mode": input_mode,
            "row_proposals": parsed["rows"],
            "page_notes": parsed["page_notes"],
            "raw_response": str(raw_path),
            "review_status": "client_review_required",
            "requires_independent_consensus": True,
        }
        findings = [review_item(page, "layout_rows", "layout_aware_rows_require_client_review")]
        for row in parsed["rows"]:
            for flag in row["review_flags"]:
                findings.append(review_item(page, "layout_row", f"layout_aware:{flag}"))
    except Exception as exc:
        if not raw_path.exists():
            write_raw(raw_path, {"model": model, "page_id": page_id}, error=type(exc).__name__)
        record = {
            "engine": f"openai/{model}",
            "document_id": page_id,
            "page_id": page_id,
            "layout_id": layout["layout_id"],
            "row_proposals": [],
            "raw_response": str(raw_path),
            "review_status": "open_exception",
            "requires_independent_consensus": True,
        }
        findings = [review_item(page, "layout_rows", "layout_aware_provider_or_schema_failure")]
    result = {"record": record, "review_items": findings, "credential_reference": credential_env}
    out_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def combine(layout_path, inputs, out_path, exceptions_path, adapter_path):
    """Combine independently checkpointed page outputs into one adapter handoff."""
    layout = load_layout(layout_path)
    paths = [Path(value) for value in (out_path, exceptions_path, adapter_path)]
    if len({path.resolve() for path in paths}) != 3 or any(path.exists() for path in paths):
        raise ValueError("Combined outputs must be distinct and new")
    packets = [json.loads(Path(value).read_text()) for value in inputs]
    records = [item["record"] for item in packets]
    findings = [finding for item in packets for finding in item["review_items"]]
    adapter = {
        "engine": records[0]["engine"] if records else "layout_aware_extraction/none",
        "records": records,
        "layout_id": layout["layout_id"],
        "proposal_only": True,
        "requires_independent_consensus": True,
    }
    Path(out_path).write_text(json.dumps(records, indent=2) + "\n")
    Path(exceptions_path).write_text(
        json.dumps({"summary": {"count": len(findings)}, "exceptions": findings}, indent=2) + "\n"
    )
    Path(adapter_path).write_text(json.dumps(adapter, indent=2) + "\n")
    return {"records": len(records), "review_items": len(findings)}


def main():
    """Parse one-page and combine subcommands without retaining credentials."""
    load_project_env()
    parser = argparse.ArgumentParser(description="Create review-only rows for a supplied layout.")
    sub = parser.add_subparsers(dest="command", required=True)
    page = sub.add_parser("page")
    page.add_argument("manifest")
    page.add_argument("layout")
    page.add_argument(
        "--page-id", required=True, help="Identifier of the retained page this invocation reads."
    )
    page.add_argument("--out", required=True)
    page.add_argument("--raw-dir", required=True)
    page.add_argument("--model", default=llm_model())
    page.add_argument(
        "--reasoning-effort",
        choices=REASONING_EFFORTS,
        default=env_value("OPENAI_REASONING_EFFORT", "medium"),
    )
    page.add_argument(
        "--credential-env", default=env_value("OPENAI_CREDENTIAL_ENV", "OPENAI_API_KEY")
    )
    page.add_argument(
        "--max-pdf-bytes", type=int, default=env_int("OPENAI_MAX_PDF_BYTES", 10_000_000)
    )
    page.add_argument(
        "--timeout-seconds", type=float, default=env_float("OPENAI_TIMEOUT_SECONDS", 120.0)
    )
    page.add_argument("--max-retries", type=int, default=env_int("OPENAI_MAX_RETRIES", 2))
    page.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=env_float("OPENAI_RETRY_BACKOFF_SECONDS", 1.0),
    )
    merged = sub.add_parser("combine")
    merged.add_argument("layout")
    merged.add_argument(
        "inputs", nargs="+", help="Per-page review-only layout artifacts to combine."
    )
    merged.add_argument("--out", required=True)
    merged.add_argument("--exceptions", required=True)
    merged.add_argument("--adapter-out", required=True)
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        if args.command == "combine":
            result = combine(args.layout, args.inputs, args.out, args.exceptions, args.adapter_out)
        else:
            if args.max_pdf_bytes < 1:
                raise ValueError("max PDF bytes must be positive")
            client = build_client(args.credential_env, args.timeout_seconds, args.max_retries)
            result = run_page(
                args.manifest,
                args.layout,
                args.page_id,
                args.out,
                args.raw_dir,
                args.model,
                client,
                args.reasoning_effort,
                args.max_pdf_bytes,
                args.credential_env,
                args.max_retries,
                args.retry_backoff_seconds,
            )
        print(
            json.dumps(
                result
                if args.command == "combine"
                else {"review_items": len(result["review_items"])}
            )
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Layout-aware extraction failed: {exc}")


if __name__ == "__main__":
    main()
