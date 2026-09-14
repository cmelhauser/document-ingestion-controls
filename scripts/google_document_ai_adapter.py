#!/usr/bin/env python3
"""Google Document AI OCR/table-evidence adapter with retained page provenance.

This is an optional, bounded, proposal-only integration.  It obtains an
independent OCR/table reading of each immutable PDF page, keeps the provider's
complete response, and deliberately emits no canonical field decisions.  Its
visible text, token, and table-cell evidence can corroborate a separately
extracted source row; financial and identity facts still require deterministic
reconciliation and final client review.
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
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import reauthorize_google
from cli_help import apply_shared_help
from llm_runtime import retry_call, retryable_error
from runtime_config import env_bool, env_float, env_int, env_value, load_project_env

IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,127}")
PROCESSOR_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
PROCESSOR_VERSION_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def sha256(path):
    """Hash a retained page to connect provider output to immutable evidence."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(path):
    """Read the shared intake contract without accepting incomplete pages."""
    data = json.loads(Path(path).read_text())
    pages = data.get("pages") if isinstance(data, dict) else None
    if not isinstance(pages, list) or not all(isinstance(item, dict) for item in pages):
        raise ValueError("Manifest must contain a pages list")
    if not all(item.get("page_id") and item.get("page_pdf") for item in pages):
        raise ValueError("Every manifest page requires page_id and page_pdf")
    return data


def require_new_file(path):
    """Refuse to replace a retained result artifact."""
    path = Path(path)
    if path.exists():
        raise ValueError(f"Output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def require_new_directory(path):
    """Permit only a fresh or empty raw-response directory."""
    path = Path(path)
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError(f"Raw response directory must be new or empty: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_json_write(path, payload):
    """Write a checkpoint atomically so interruption cannot leave it half-written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def safe_label(value):
    """Create a stable filename without changing the retained page identifier."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_") or "page"


def resolve_page(manifest_path, page, max_pdf_bytes):
    """Resolve a manifest-relative one-page PDF and enforce its byte cap."""
    root = Path(manifest_path).resolve().parent
    candidate = Path(page["page_pdf"])
    if candidate.is_absolute():
        raise ValueError("Manifest artifact path must be relative")
    path = (root / candidate).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("Manifest artifact path escapes the manifest directory") from exc
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise ValueError(f"Page PDF is not readable: {path}")
    if path.stat().st_size > max_pdf_bytes:
        raise ValueError(f"Page PDF exceeds configured byte limit: {path.name}")
    return path


def valid_identifier(label, value, pattern=IDENTIFIER):
    """Reject malformed resource identifiers before constructing an endpoint."""
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{label} must contain letters, digits, underscores, or hyphens")
    return value


def endpoint(project_id, location, processor_id, processor_version=None):
    """Build the documented regional synchronous-process endpoint."""
    project_id = valid_identifier("project ID", project_id)
    location = valid_identifier("location", location)
    processor_id = valid_identifier("processor ID", processor_id, PROCESSOR_IDENTIFIER)
    resource = f"projects/{project_id}/locations/{location}/processors/{processor_id}"
    if processor_version:
        resource += (
            "/processorVersions/"
            f"{valid_identifier('processor version', processor_version, PROCESSOR_VERSION_IDENTIFIER)}"
        )
    return f"https://{location}-documentai.googleapis.com/v1/{resource}:process"


def request_body(page_path, page_id, native_pdf_parsing):
    """Encode one retained PDF as the Document AI RawDocument request body."""
    body = {
        "rawDocument": {
            "content": base64.b64encode(Path(page_path).read_bytes()).decode("ascii"),
            "mimeType": "application/pdf",
            "displayName": str(page_id),
        },
        "skipHumanReview": True,
    }
    if native_pdf_parsing:
        body["processOptions"] = {"ocrConfig": {"enableNativePdfParsing": True}}
    return body


def provider_request(url, body, access_token, timeout_seconds, opener=urlopen):
    """Issue one bounded REST request; the caller retains both successes and failures."""
    if not access_token:
        raise ValueError("Required Google Document AI access token is not set")
    request = Request(  # noqa: S310 - fixed https endpoint constant, never a caller-supplied URL
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with opener(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise ValueError(f"Document AI HTTP {exc.code}") from exc
    except URLError as exc:
        raise ValueError("Document AI transport failed") from exc


def request_with_refreshed_credential(url, body, token_source, timeout_seconds, opener=urlopen):
    """Re-mint an expired short-lived token once rather than losing the page.

    An ADC token lives about an hour and a corpus pass runs longer, so a token
    minted at start-up dies mid-run and every page after it returns HTTP 401.
    That is not a transient fault and bounded retry cannot help it, which is why
    401 is absent from the retryable set -- but a *fresh* token can. The first
    401 refreshes the credential and reissues the request exactly once. A second
    401 is a genuine credential failure and stays an explicit exception.
    """
    try:
        return provider_request(
            url, body, reauthorize_google.token_value(token_source), timeout_seconds, opener
        )
    except ValueError as exc:
        if str(exc) != "Document AI HTTP 401" or not hasattr(token_source, "refresh"):
            raise
        token_source.refresh()
        return provider_request(
            url, body, reauthorize_google.token_value(token_source), timeout_seconds, opener
        )


def failure_type(exc):
    """Return a safe provider-failure category without retaining error text."""
    message = str(exc)
    if re.fullmatch(r"Document AI HTTP [1-5][0-9]{2}", message):
        return message.lower().replace(" ", "_")
    if message == "Document AI transport failed":
        return "document_ai_transport_failed"
    if isinstance(exc, json.JSONDecodeError):
        return "document_ai_invalid_json_response"
    return type(exc).__name__


def text_anchor(document_text, anchor):
    """Recover visible evidence exactly from Document AI's document-text anchors."""
    if not isinstance(anchor, dict) or not isinstance(anchor.get("textSegments"), list):
        return None
    values = []
    for segment in anchor["textSegments"]:
        if not isinstance(segment, dict):
            continue
        try:
            start = int(segment.get("startIndex", 0))
            end = int(segment.get("endIndex", 0))
        except (TypeError, ValueError):
            continue
        if 0 <= start <= end <= len(document_text):
            values.append(document_text[start:end])
    value = "".join(values)
    return value if value else None


def layout_evidence(document_text, layout):
    """Retain text-anchor and page-coordinate evidence without semantic inference."""
    layout = layout if isinstance(layout, dict) else {}
    polygon = layout.get("boundingPoly") if isinstance(layout.get("boundingPoly"), dict) else {}
    return {
        "evidence_text": text_anchor(document_text, layout.get("textAnchor")),
        "confidence": layout.get("confidence")
        if isinstance(layout.get("confidence"), (int, float))
        else None,
        "normalized_vertices": polygon.get("normalizedVertices", []),
        "vertices": polygon.get("vertices", []),
    }


def table_cells(document_text, cells):
    """Normalize provider table cells into evidence-backed visible source cells."""
    result = []
    for index, cell in enumerate(cells if isinstance(cells, list) else []):
        if not isinstance(cell, dict):
            continue
        evidence = layout_evidence(document_text, cell.get("layout"))
        result.append(
            {
                "column_index": index,
                "row_span": cell.get("rowSpan", 1),
                "column_span": cell.get("colSpan", 1),
                **evidence,
            }
        )
    return result


def source_tables(document):
    """Expose page tables as source-native rows; no canonical labels are invented."""
    document_text = document.get("text", "") if isinstance(document.get("text"), str) else ""
    pages = document.get("pages") if isinstance(document.get("pages"), list) else []
    result = []
    for page_index, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            continue
        for table_index, table in enumerate(page.get("tables", []), start=1):
            if not isinstance(table, dict):
                continue
            headers = []
            for header_row in table.get("headerRows", []):
                headers.extend(
                    table_cells(
                        document_text,
                        header_row.get("cells") if isinstance(header_row, dict) else [],
                    )
                )
            source_headers = [
                cell.get("evidence_text") or f"column_{index + 1}"
                for index, cell in enumerate(headers)
            ]
            rows = []
            for row_index, body_row in enumerate(table.get("bodyRows", []), start=1):
                cells = table_cells(
                    document_text, body_row.get("cells") if isinstance(body_row, dict) else []
                )
                rows.append(
                    {
                        "source_row_number": row_index,
                        "cells": [
                            {
                                "source_label": source_headers[index]
                                if index < len(source_headers)
                                else f"column_{index + 1}",
                                **cell,
                            }
                            for index, cell in enumerate(cells)
                        ],
                    }
                )
            result.append(
                {
                    "provider_page_number": page.get("pageNumber", page_index),
                    "source_table_number": table_index,
                    "table_evidence": layout_evidence(document_text, table.get("layout")),
                    "source_headers": source_headers,
                    "source_rows": rows,
                }
            )
    return result


def normalized_record(page, page_path, raw_path, response, processor_id, processor_version):
    """Build an independent OCR/table reading, explicitly not a field decision."""
    document = response.get("document") if isinstance(response, dict) else None
    if not isinstance(document, dict):
        raise ValueError("Document AI response does not contain a document object")
    document_text = document.get("text", "") if isinstance(document.get("text"), str) else ""
    tokens = []
    for page_data in document.get("pages", []) if isinstance(document.get("pages"), list) else []:
        if isinstance(page_data, dict):
            tokens.extend(
                layout_evidence(document_text, item.get("layout"))
                for item in page_data.get("tokens", [])
                if isinstance(item, dict)
            )
    version = processor_version or "default"
    return {
        "engine": f"google_document_ai/{processor_id}",
        "engine_version": version,
        "independence_group": "google_document_ai",
        "independent_extractor": True,
        "document_id": page["page_id"],
        "page_id": page["page_id"],
        "source_file": page.get("source_file"),
        "source_page_number": page.get("source_page_number"),
        "page_pdf": page.get("page_pdf"),
        "page_sha256": sha256(page_path),
        "raw_response": str(raw_path),
        "document_text": document_text,
        "token_evidence": tokens,
        "source_tables": source_tables(document),
        "header": {},
        "lines": [],
        "review_status": "pending_deterministic_reconciliation",
        "proposal_only": True,
        "requires_deterministic_reconciliation": True,
        "requires_final_client_review_for_financial_or_identity_facts": True,
    }


def failed_record(page, raw_path, processor_id, processor_version):
    """Keep a failed retained page visible to the final review process."""
    return {
        "engine": f"google_document_ai/{processor_id}",
        "engine_version": processor_version or "default",
        "independence_group": "google_document_ai",
        "independent_extractor": True,
        "document_id": page["page_id"],
        "page_id": page["page_id"],
        "page_pdf": page.get("page_pdf"),
        "raw_response": str(raw_path),
        "header": {},
        "lines": [],
        "review_status": "open_exception",
        "proposal_only": True,
    }


def write_raw(path, request, response=None, error=None):
    """Preserve the non-secret request metadata and original result/failure type."""
    payload = {"request": request}
    if response is not None:
        payload["response"] = response
    if error is not None:
        payload["error_type"] = error
    atomic_json_write(path, payload)


def resumable_success(raw_path, page, page_path):
    """Return a retained successful record only when its evidence hash matches."""
    try:
        payload = json.loads(Path(raw_path).read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    request = payload.get("request") if isinstance(payload, dict) else None
    response = payload.get("response") if isinstance(payload, dict) else None
    if (
        not isinstance(request, dict)
        or request.get("page_id") != page.get("page_id")
        or request.get("page_sha256") != sha256(page_path)
        or not isinstance(response, dict)
        or not isinstance(response.get("document"), dict)
        or payload.get("error_type") is not None
    ):
        return None
    return response


def retry_raw_path(raw_dir, index, page):
    """Choose a new raw path when an interrupted attempt left a partial file."""
    base = raw_dir / f"{index:06d}_{safe_label(page['page_id'])}.json"
    if not base.exists():
        return base
    suffix = 1
    while True:
        candidate = base.with_name(f"{base.stem}__retry{suffix}{base.suffix}")
        if not candidate.exists():
            return candidate
        suffix += 1


def validate_limits(max_pages, max_pdf_bytes, timeout_seconds):
    """Fail closed on unreasonable resource or transport controls."""
    if not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError("max pages must be a positive integer")
    if not isinstance(max_pdf_bytes, int) or max_pdf_bytes < 1:
        raise ValueError("max PDF bytes must be a positive integer")
    if not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 300:
        raise ValueError("timeout seconds must be greater than zero and at most 300")


def run_adapter(
    manifest_path,
    out_path,
    adapter_path,
    exceptions_path,
    raw_dir,
    project_id,
    location,
    processor_id,
    access_token,
    processor_version=None,
    max_pages=500,
    max_pdf_bytes=10_000_000,
    timeout_seconds=120.0,
    native_pdf_parsing=False,
    credential_env="GOOGLE_DOCUMENT_AI_ACCESS_TOKEN",
    opener=urlopen,
    resume_raw_dir=None,
):
    """Process a bounded manifest, recording every provider result or failure."""
    manifest = load_manifest(manifest_path)
    validate_limits(max_pages, max_pdf_bytes, timeout_seconds)
    service_url = endpoint(project_id, location, processor_id, processor_version)
    if len(manifest["pages"]) > max_pages:
        raise ValueError(
            f"Manifest pages exceed configured limit: {len(manifest['pages'])} > {max_pages}"
        )
    targets = [Path(item).resolve() for item in (out_path, adapter_path, exceptions_path)]
    if len(set(targets)) != len(targets):
        raise ValueError("Output, adapter, and exception paths must be distinct")
    out_path, adapter_path, exceptions_path = (
        require_new_file(item) for item in (out_path, adapter_path, exceptions_path)
    )
    if resume_raw_dir is not None:
        raw_dir = Path(resume_raw_dir)
        if not raw_dir.is_dir():
            raise ValueError(f"Resume raw response directory is not readable: {raw_dir}")
    else:
        raw_dir = require_new_directory(raw_dir)
    records, exceptions = [], []
    checkpoint_path = raw_dir.parent / "document_ai_checkpoint.json"
    for index, page in enumerate(manifest["pages"], start=1):
        raw_path = raw_dir / f"{index:06d}_{safe_label(page['page_id'])}.json"
        try:
            page_path = resolve_page(manifest_path, page, max_pdf_bytes)
            if resume_raw_dir is not None and raw_path.is_file():
                cached_response = resumable_success(raw_path, page, page_path)
                if cached_response is not None:
                    records.append(
                        normalized_record(
                            page,
                            page_path,
                            raw_path,
                            cached_response,
                            processor_id,
                            processor_version,
                        )
                    )
                    atomic_json_write(
                        checkpoint_path,
                        {
                            "schema_version": "1.0",
                            "manifest_page_count": len(manifest["pages"]),
                            "completed_pages": len(records),
                            "reused_pages": sum(
                                1 for item in records if item.get("raw_response") == str(raw_path)
                            ),
                            "provider_failures": len(exceptions),
                        },
                    )
                    continue
                raw_path = retry_raw_path(raw_dir, index, page)
            body = request_body(page_path, page["page_id"], native_pdf_parsing)
            request = {
                "endpoint": service_url,
                "page_id": page["page_id"],
                "page_sha256": sha256(page_path),
            }
            response = retry_call(
                lambda body=body: request_with_refreshed_credential(
                    service_url, body, access_token, timeout_seconds, opener
                ),
                3,
                1.0,
                jitter=True,
                max_backoff_seconds=30.0,
                retryable=lambda error: (
                    retryable_error(error)
                    or str(error)
                    in {
                        "Document AI HTTP 408",
                        "Document AI HTTP 429",
                        "Document AI HTTP 500",
                        "Document AI HTTP 502",
                        "Document AI HTTP 503",
                        "Document AI HTTP 504",
                        "Document AI transport failed",
                    }
                ),
            )
            write_raw(raw_path, request, response=response)
            records.append(
                normalized_record(
                    page, page_path, raw_path, response, processor_id, processor_version
                )
            )
        except Exception as exc:  # A failed page must be review work, never a missing result.
            if not raw_path.exists():
                write_raw(
                    raw_path,
                    {"endpoint": service_url, "page_id": page["page_id"]},
                    error=failure_type(exc),
                )
            records.append(failed_record(page, raw_path, processor_id, processor_version))
            exceptions.append(
                {
                    "document_id": page["page_id"],
                    "page_id": page["page_id"],
                    "reason": "google_document_ai_provider_or_schema_failure",
                    # The generic reason cannot tell a misconfigured processor
                    # from a corrupt page. A whole corpus failed 404 while the
                    # exception file said only "provider or schema failure",
                    # so the concrete cause is retained beside it.
                    "provider_error_type": failure_type(exc),
                    "disposition": "client_review_required",
                }
            )
        finally:
            atomic_json_write(
                checkpoint_path,
                {
                    "schema_version": "1.0",
                    "manifest_page_count": len(manifest["pages"]),
                    "completed_pages": len(records),
                    "reused_pages": sum(
                        1 for item in records if "__retry" not in str(item.get("raw_response", ""))
                    ),
                    "provider_failures": len(exceptions),
                },
            )
    out_path.write_text(json.dumps(records, indent=2) + "\n")
    adapter = {
        "engine": f"google_document_ai/{processor_id}",
        "provider": "google_document_ai",
        "processor_configuration": {
            "project_id": project_id,
            "location": location,
            "processor_id": processor_id,
            "processor_version": processor_version or "default",
        },
        "records": records,
        "credential_reference": credential_env,
        "raw_response_directory": str(raw_dir),
        "raw_response_retention_required": True,
        "proposal_only": True,
        "independent_extractor": True,
        "requires_deterministic_reconciliation": True,
        "requires_final_client_review_for_financial_or_identity_facts": True,
        "run_limits": {"max_pages": max_pages, "max_pdf_bytes": max_pdf_bytes},
        "transport": {"timeout_seconds": timeout_seconds, "max_retries": 0},
    }
    adapter_path.write_text(json.dumps(adapter, indent=2) + "\n")
    exceptions_path.write_text(
        json.dumps({"summary": {"count": len(exceptions)}, "exceptions": exceptions}, indent=2)
        + "\n"
    )
    return {
        "pages": len(records),
        # Rule 9: a page that failed is retained as review work, not as a
        # reading. Reporting only "pages" let a corpus where every page failed
        # read like a completed corroboration lane.
        "readings": sum(1 for item in records if item.get("review_status") != "open_exception"),
        "review_items": len(exceptions),
        "provider_error_types": sorted(
            {
                str(item["provider_error_type"])
                for item in exceptions
                if item.get("provider_error_type")
            }
        ),
        "engine": adapter["engine"],
    }


def main():
    try:
        load_project_env()
        defaults = {
            "enabled": env_bool("GOOGLE_DOCUMENT_AI_ENABLED", False),
            "project_id": env_value("GOOGLE_DOCUMENT_AI_PROJECT_ID", ""),
            "location": env_value("GOOGLE_DOCUMENT_AI_LOCATION", "us"),
            "processor_id": env_value("GOOGLE_DOCUMENT_AI_PROCESSOR_ID", ""),
            "processor_version": env_value("GOOGLE_DOCUMENT_AI_PROCESSOR_VERSION", "") or None,
            "credential_env": env_value(
                "GOOGLE_DOCUMENT_AI_CREDENTIAL_ENV", "GOOGLE_DOCUMENT_AI_ACCESS_TOKEN"
            ),
            "max_pages": env_int("GOOGLE_DOCUMENT_AI_MAX_PAGES", 1000),
            "max_pdf_bytes": env_int("GOOGLE_DOCUMENT_AI_MAX_PDF_BYTES", 10_000_000),
            "timeout_seconds": env_float("GOOGLE_DOCUMENT_AI_TIMEOUT_SECONDS", 180.0),
            "native_pdf_parsing": env_bool("GOOGLE_DOCUMENT_AI_NATIVE_PDF_PARSING", False),
        }
    except ValueError as exc:
        sys.exit(f"Google Document AI adapter failed: {exc}")
    parser = argparse.ArgumentParser(
        description="Retain independent Google Document AI OCR/table evidence for intake pages."
    )
    parser.add_argument("manifest")
    parser.add_argument("--out", required=True)
    parser.add_argument("--adapter-out", required=True)
    parser.add_argument("--exceptions", required=True)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument(
        "--resume-raw-dir",
        default=None,
        help="Reuse validated successful raw responses from an interrupted prior attempt.",
    )
    parser.add_argument(
        "--enable",
        action="store_true",
        default=defaults["enabled"],
        help="explicitly permit this proposal-only live provider call",
    )
    parser.add_argument("--project-id", default=defaults["project_id"])
    parser.add_argument("--location", default=defaults["location"])
    parser.add_argument(
        "--processor-id",
        default=defaults["processor_id"],
        help="Document AI processor that serves the request.",
    )
    parser.add_argument(
        "--processor-version",
        default=defaults["processor_version"],
        help="Pinned processor version, retained so a reading can be reproduced.",
    )
    parser.add_argument("--credential-env", default=defaults["credential_env"])
    parser.add_argument("--max-pages", type=int, default=defaults["max_pages"])
    parser.add_argument("--max-pdf-bytes", type=int, default=defaults["max_pdf_bytes"])
    parser.add_argument("--timeout-seconds", type=float, default=defaults["timeout_seconds"])
    parser.add_argument(
        "--native-pdf-parsing",
        action="store_true",
        default=defaults["native_pdf_parsing"],
        help="Use the processor's native PDF parsing instead of rasterized input.",
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    if not args.enable:
        sys.exit(
            "Google Document AI adapter failed: disabled; set GOOGLE_DOCUMENT_AI_ENABLED=true or pass --enable"
        )
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", args.credential_env):
        sys.exit(
            "Google Document AI adapter failed: credential environment variable must be uppercase with underscores"
        )
    try:
        run_kwargs = {"resume_raw_dir": args.resume_raw_dir} if args.resume_raw_dir else {}
        result = run_adapter(
            args.manifest,
            args.out,
            args.adapter_out,
            args.exceptions,
            args.raw_dir,
            args.project_id,
            args.location,
            args.processor_id,
            reauthorize_google.RefreshingToken(args.credential_env),
            args.processor_version,
            args.max_pages,
            args.max_pdf_bytes,
            args.timeout_seconds,
            args.native_pdf_parsing,
            args.credential_env,
            **run_kwargs,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Google Document AI adapter failed: {exc}")
    if result["pages"] and not result["readings"]:
        # A corroboration lane that read nothing corroborates nothing. Exiting
        # zero here let a run continue believing it had independent evidence.
        causes = ", ".join(result["provider_error_types"]) or "no cause recorded"
        sys.exit(
            "Google Document AI adapter failed: every one of "
            f"{result['pages']} pages failed, so this lane produced no independent "
            f"reading ({causes}). Retained exceptions and raw responses name each page; "
            "fix the processor configuration or disable the lane deliberately."
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
