#!/usr/bin/env python3
"""Read supplied handwriting regions with Google Cloud Vision handwriting OCR.

Every immutable intake page is rendered once and submitted to Cloud Vision
``DOCUMENT_TEXT_DETECTION``.  The handwriting language hint makes this a
handwriting-capable recognizer, while separately supplied region geometry
decides which words are candidate handwriting.  The result is one proposal-only
HTR vote for ``handwriting_review.py``; it never validates itself, overwrites a
printed value, or treats provider confidence as independent evidence.
"""

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import reauthorize_google
from cli_help import apply_shared_help
from google_document_ai_adapter import (
    atomic_json_write,
    load_manifest,
    require_new_directory,
    require_new_file,
    resolve_page,
    safe_label,
    sha256,
)
from llm_runtime import retry_call, retryable_error
from runtime_config import env_bool, env_float, env_int, env_value, load_project_env

PROJECT_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
NUMERIC_READING = re.compile(r"[\s$€£¥+()\-.,/0-9]+")


class CloudVisionError(ValueError):
    """Sanitized Cloud Vision transport error with retry classification."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def digest_bytes(path):
    """Return the SHA-256 of a rendered page image."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def retained_reference(directory, path):
    """Return a run-local reference without leaking an operator filesystem path."""
    return f"{Path(directory).name}/{Path(path).name}"


def endpoint(project_id, location):
    """Return the current Cloud Vision handwriting OCR REST endpoint."""
    if not isinstance(project_id, str) or not PROJECT_IDENTIFIER.fullmatch(project_id):
        raise ValueError("project ID contains unsupported characters")
    if location == "global":
        return "https://vision.googleapis.com/v1/images:annotate"
    if location not in {"us", "eu"}:
        raise ValueError("location must be global, us, or eu")
    return (
        f"https://{location}-vision.googleapis.com/v1/projects/{project_id}"
        f"/locations/{location}/images:annotate"
    )


def request_body(image_path, language_hints):
    """Build one handwriting-optimized document-text request."""
    request = {
        "image": {"content": base64.b64encode(Path(image_path).read_bytes()).decode("ascii")},
        "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
    }
    if language_hints:
        request["imageContext"] = {"languageHints": list(language_hints)}
    return {"requests": [request]}


def provider_request(url, body, access_token, project_id, timeout_seconds, opener=urlopen):
    """Call Cloud Vision without retaining a token or provider error body."""
    if not access_token:
        raise ValueError("Required Google handwriting OCR access token is not set")
    request = Request(  # noqa: S310 - fixed https endpoint constant, never a caller-supplied URL
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=utf-8",
            "X-goog-user-project": project_id,
        },
        method="POST",
    )
    try:
        with opener(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise CloudVisionError(f"Cloud Vision HTTP {exc.code}", exc.code) from exc
    except URLError as exc:
        raise CloudVisionError("Cloud Vision transport failed", 503) from exc


def request_with_refreshed_credential(
    url, body, token_source, project_id, timeout_seconds, opener=urlopen
):
    """Re-mint an expired short-lived token once rather than losing the page.

    Cloud Vision shares Application Default Credentials with Document AI, so it
    shares the expiry: a token minted at start-up dies about an hour in, and a
    whole-corpus render-and-read pass runs longer than that. A 401 is not
    transient, so bounded retry cannot recover it, and this lane has no resume
    path -- every page after the expiry would have to be paid for again. The
    first 401 refreshes the credential and reissues the request exactly once; a
    second 401 is a genuine credential failure and stays an explicit exception.
    """
    try:
        return provider_request(
            url,
            body,
            reauthorize_google.token_value(token_source),
            project_id,
            timeout_seconds,
            opener,
        )
    except CloudVisionError as exc:
        if exc.status_code != 401 or not hasattr(token_source, "refresh"):
            raise
        token_source.refresh()
        return provider_request(
            url,
            body,
            reauthorize_google.token_value(token_source),
            project_id,
            timeout_seconds,
            opener,
        )


def failure_type(exc):
    """Return only a non-secret failure category."""
    message = str(exc)
    if re.fullmatch(r"Cloud Vision HTTP [1-5][0-9]{2}", message):
        return message.casefold().replace(" ", "_")
    if message == "Cloud Vision transport failed":
        return "cloud_vision_transport_failed"
    if isinstance(exc, json.JSONDecodeError):
        return "cloud_vision_invalid_json_response"
    return type(exc).__name__


def render_page(page_path, image_path, dpi):
    """Render an immutable one-page PDF to a retained PNG without shell expansion."""
    image_path = Path(image_path)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = image_path.with_suffix("")
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [  # noqa: S607 - poppler is a documented install dependency
            "pdftoppm",
            "-f",
            "1",
            "-l",
            "1",
            "-singlefile",
            "-r",
            str(dpi),
            "-png",
            str(page_path),
            str(prefix),
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode or not image_path.is_file():
        raise ValueError("Unable to render retained page for Google handwriting OCR")


def word_text(word):
    """Join only visible symbol text from a Vision word."""
    return "".join(
        symbol.get("text", "")
        for symbol in word.get("symbols", [])
        if isinstance(symbol, dict) and isinstance(symbol.get("text"), str)
    )


def normalized_box(polygon, width, height):
    """Convert a Vision pixel polygon to a normalized axis-aligned box."""
    vertices = polygon.get("vertices", []) if isinstance(polygon, dict) else []
    points = [
        (float(item.get("x", 0)), float(item.get("y", 0)))
        for item in vertices
        if isinstance(item, dict)
    ]
    if not points or width <= 0 or height <= 0:
        return None
    return {
        "left": min(item[0] for item in points) / width,
        "top": min(item[1] for item in points) / height,
        "right": max(item[0] for item in points) / width,
        "bottom": max(item[1] for item in points) / height,
    }


def word_evidence(response):
    """Flatten Vision's structural hierarchy into ordered, source-boxed words."""
    annotation = response.get("fullTextAnnotation") if isinstance(response, dict) else None
    pages = annotation.get("pages", []) if isinstance(annotation, dict) else []
    values = []
    for provider_page, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            continue
        width, height = page.get("width", 0), page.get("height", 0)
        for block_index, block in enumerate(page.get("blocks", []), start=1):
            if not isinstance(block, dict):
                continue
            for paragraph_index, paragraph in enumerate(block.get("paragraphs", []), start=1):
                if not isinstance(paragraph, dict):
                    continue
                for word_index, word in enumerate(paragraph.get("words", []), start=1):
                    if not isinstance(word, dict):
                        continue
                    text = word_text(word)
                    box = normalized_box(word.get("boundingBox"), width, height)
                    if text and box:
                        values.append(
                            {
                                "text": text,
                                "confidence": word.get("confidence")
                                if isinstance(word.get("confidence"), (int, float))
                                else None,
                                "box": box,
                                "provider_page_number": provider_page,
                                "block_number": block_index,
                                "paragraph_number": paragraph_index,
                                "word_number": word_index,
                            }
                        )
    return values


def valid_box(value):
    """Validate normalized handwriting-region geometry."""
    if not isinstance(value, dict) or set(("left", "top", "right", "bottom")) - set(value):
        return False
    coordinates = [value[key] for key in ("left", "top", "right", "bottom")]
    return all(
        isinstance(item, (int, float)) and not isinstance(item, bool) for item in coordinates
    ) and (0 <= value["left"] < value["right"] <= 1 and 0 <= value["top"] < value["bottom"] <= 1)


def region_records(value):
    """Yield page/region pairs from extraction records or profile packets."""
    if isinstance(value, list):
        records = value
    elif isinstance(value, dict) and isinstance(value.get("records"), list):
        records = value["records"]
    elif isinstance(value, dict):
        records = [value]
    else:
        raise ValueError("Handwriting region artifact must be an object or list")
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Handwriting region records must be objects")
        page_id = record.get("page_id") or record.get("document_id")
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else record
        regions = payload.get("handwriting_regions", [])
        if not isinstance(regions, list):
            raise ValueError("handwriting_regions must be a list")
        for region in regions:
            yield page_id, region


def load_regions(paths):
    """Load separately detected regions without treating their readings as proof.

    ``--regions`` is repeatable because two independent detectors proposing the
    same page is the point: that is what makes a reading corroborated rather than
    asserted. But a region_id is only meaningful inside the artifact that issued
    it, and every engine numbers its own regions from one. Requiring
    ``(page_id, region_id)`` to be unique across all supplied artifacts therefore
    refused the entire run the first time two detectors both said ``hw_1`` --
    which is to say, always. The documented multi-detector usage could not run.

    Uniqueness still matters: a reading has to map back to exactly one region.
    So the constraint is scoped to where the identifier is actually issued --
    ``(page_id, detector, region_id)`` -- and a genuine repeat within one
    detector is still refused. Where two detectors collide on an id, both are
    qualified by detector so the emitted ids stay unique per page; where they do
    not, the id is passed through untouched and a single-detector run is
    unchanged.
    """
    loaded, seen = [], set()
    for path in paths or []:
        detector = Path(path).stem
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        for page_id, region in region_records(value):
            if not page_id or not isinstance(region, dict) or not region.get("region_id"):
                raise ValueError("Each handwriting region requires page_id and region_id")
            box = region.get("box") or {
                key: region.get(key) for key in ("left", "top", "right", "bottom")
            }
            if not valid_box(box):
                raise ValueError("Each handwriting region requires a normalized box")
            key = (str(page_id), detector, str(region["region_id"]))
            if key in seen:
                raise ValueError(
                    f"duplicate region_id for page within {detector}: {region['region_id']}"
                )
            seen.add(key)
            loaded.append((str(page_id), detector, {**region, "box": box}))

    # Which (page, region_id) pairs more than one detector laid claim to.
    contested = {
        (page_id, str(region["region_id"]))
        for page_id, detector, region in loaded
        if any(
            other_detector != detector and str(other["region_id"]) == str(region["region_id"])
            for other_page, other_detector, other in loaded
            if other_page == page_id
        )
    }

    result = {}
    for page_id, detector, region in loaded:
        region_id = str(region["region_id"])
        result.setdefault(page_id, []).append(
            {
                **region,
                # Kept exactly as the detector issued it, so a reading can still
                # be traced back to the region that engine proposed.
                "detector": detector,
                "detector_region_id": region_id,
                "region_id": (
                    f"{detector}:{region_id}" if (page_id, region_id) in contested else region_id
                ),
            }
        )
    return result


def center_in(word_box, region_box):
    """Return whether a word center falls inside a supplied region."""
    x = (word_box["left"] + word_box["right"]) / 2
    y = (word_box["top"] + word_box["bottom"]) / 2
    return (
        region_box["left"] <= x <= region_box["right"]
        and region_box["top"] <= y <= region_box["bottom"]
    )


def infer_content_class(value):
    """Type only an unambiguously numeric-looking reading; never infer semantics."""
    return "numeric" if value and NUMERIC_READING.fullmatch(value) else "text"


def bind_regions(page_id, regions, words):
    """Bind ordered Google words to detector-supplied region IDs."""
    annotations, exceptions = [], []
    for region in regions:
        selected = [item for item in words if center_in(item["box"], region["box"])]
        value = " ".join(item["text"] for item in selected).strip() or None
        semantic_type = str(region.get("semantic_type") or "handwritten_text")
        content_class = str(region.get("content_class") or infer_content_class(value))
        annotation = {
            "document_id": str(region.get("document_id") or page_id),
            "page_id": page_id,
            "region_id": str(region["region_id"]),
            "semantic_type": semantic_type,
            "content_class": content_class,
            "value": value,
            "confidence": min(
                (item["confidence"] for item in selected if item["confidence"] is not None),
                default=None,
            ),
            "iteration": 1,
            # Copied from the supplied region, never derived here: this lane
            # reads what a detector pointed at and has no basis to decide what a
            # mark modifies. That makes an unset flag indistinguishable from a
            # corpus with no financial handwriting, and on a 716-page run it was
            # false on all 3,319 annotations because no region artifact set it --
            # so the $0-threshold control for financial handwritten amendments
            # could not fire at all. The companion flag says which of the two
            # this is, and `content_class` carries the reading's own shape for
            # `handwriting_review.py` to escalate on.
            "financial_amendment": bool(region.get("financial_amendment", False)),
            "financial_amendment_supplied": "financial_amendment" in region,
            "box": region["box"],
            "word_evidence": selected,
            "proposal_only": True,
        }
        annotations.append(annotation)
        if value is None:
            exceptions.append(
                {
                    "document_id": annotation["document_id"],
                    "page_id": page_id,
                    "region_id": annotation["region_id"],
                    "reason": "google_handwriting_region_unreadable",
                    "disposition": "client_review_required",
                }
            )
    return annotations, exceptions


def validate_limits(max_pages, max_pdf_bytes, max_image_bytes, timeout_seconds, max_retries, dpi):
    """Validate resource controls before creating retained artifacts."""
    if any(
        not isinstance(item, int) or isinstance(item, bool) or item < 1
        for item in (max_pages, max_pdf_bytes, max_image_bytes)
    ):
        raise ValueError("max pages, max PDF bytes, and max image bytes must be positive integers")
    if not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 300:
        raise ValueError("timeout seconds must be greater than zero and at most 300")
    if not isinstance(dpi, int) or not 72 <= dpi <= 600:
        raise ValueError("render DPI must be from 72 through 600")
    if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
        raise ValueError("max retries must be a non-negative integer")


def run_lane(
    manifest_path,
    out_path,
    adapter_path,
    exceptions_path,
    raw_dir,
    images_dir,
    project_id,
    location,
    access_token,
    *,
    region_paths=None,
    language_hints=("en-t-i0-handwrit",),
    max_pages=1000,
    max_pdf_bytes=10_000_000,
    max_image_bytes=20_000_000,
    timeout_seconds=180.0,
    max_retries=2,
    dpi=300,
    credential_env="GOOGLE_HANDWRITING_OCR_ACCESS_TOKEN",
    renderer=render_page,
    opener=urlopen,
):
    """OCR every page and retain one explicit result or exception for each."""
    manifest = load_manifest(manifest_path)
    validate_limits(max_pages, max_pdf_bytes, max_image_bytes, timeout_seconds, max_retries, dpi)
    if len(manifest["pages"]) > max_pages:
        raise ValueError(
            f"Manifest pages exceed configured limit: {len(manifest['pages'])} > {max_pages}"
        )
    targets = [Path(item).resolve() for item in (out_path, adapter_path, exceptions_path)]
    if len(set(targets)) != len(targets):
        raise ValueError("Output, adapter, and exception paths must be distinct")
    out_path, adapter_path, exceptions_path = (require_new_file(item) for item in targets)
    raw_dir = require_new_directory(raw_dir)
    images_dir = require_new_directory(images_dir)
    regions = load_regions(region_paths)
    region_sources = [
        {"name": Path(path).name, "sha256": digest_bytes(path)} for path in region_paths or []
    ]
    service_url = endpoint(project_id, location)
    engine = "google_cloud_vision_handwriting/v1"
    page_records, annotations, exceptions = [], [], []
    for index, page in enumerate(manifest["pages"], start=1):
        page_id = str(page["page_id"])
        raw_path = raw_dir / f"{index:06d}_{safe_label(page_id)}.json"
        image_path = images_dir / f"{index:06d}_{safe_label(page_id)}.png"
        request_meta = {"page_id": page_id, "feature": "DOCUMENT_TEXT_DETECTION"}
        try:
            page_path = resolve_page(manifest_path, page, max_pdf_bytes)
            renderer(page_path, image_path, dpi)
            if image_path.stat().st_size > max_image_bytes:
                raise ValueError("Rendered page exceeds configured image byte limit")
            body = request_body(image_path, language_hints)
            request_meta.update(
                page_sha256=sha256(page_path),
                image_sha256=digest_bytes(image_path),
                language_hints=list(language_hints),
            )
            response = retry_call(
                lambda request_body_value=body: request_with_refreshed_credential(
                    service_url,
                    request_body_value,
                    access_token,
                    project_id,
                    timeout_seconds,
                    opener,
                ),
                max_retries,
                retryable=retryable_error,
                jitter=True,
                provider="google_cloud_vision",
            )
            response_items = response.get("responses") if isinstance(response, dict) else None
            response_item = (
                response_items[0]
                if isinstance(response_items, list) and len(response_items) == 1
                else None
            )
            if not isinstance(response_item, dict):
                raise ValueError("Cloud Vision response does not contain one image response")
            if response_item.get("error"):
                raise ValueError("Cloud Vision returned an image annotation error")
            atomic_json_write(raw_path, {"request": request_meta, "response": response})
            words = word_evidence(response_item)
            page_annotations, page_exceptions = bind_regions(
                page_id, regions.get(page_id, []), words
            )
            for item in page_annotations:
                item["raw_response"] = retained_reference(raw_dir, raw_path)
                item["page_sha256"] = request_meta["page_sha256"]
            annotations.extend(page_annotations)
            exceptions.extend(page_exceptions)
            page_records.append(
                {
                    "engine": engine,
                    "independence_group": "google",
                    "document_id": page_id,
                    "page_id": page_id,
                    "source_page_number": page.get("source_page_number"),
                    "page_pdf": page.get("page_pdf"),
                    "page_sha256": request_meta["page_sha256"],
                    "rendered_image": retained_reference(images_dir, image_path),
                    "rendered_image_sha256": request_meta["image_sha256"],
                    "raw_response": retained_reference(raw_dir, raw_path),
                    "page_status": "ocr_complete_regions_bound"
                    if regions.get(page_id)
                    else "ocr_complete_no_supplied_regions",
                    "full_text": response_item.get("fullTextAnnotation", {}).get("text", ""),
                    "word_evidence": words,
                    "region_count": len(regions.get(page_id, [])),
                    "annotation_count": len(page_annotations),
                    "proposal_only": True,
                }
            )
        except Exception as exc:
            category = failure_type(exc)
            if not raw_path.exists():
                atomic_json_write(raw_path, {"request": request_meta, "error_type": category})
            page_records.append(
                {
                    "engine": engine,
                    "independence_group": "google",
                    "document_id": page_id,
                    "page_id": page_id,
                    "page_pdf": page.get("page_pdf"),
                    "raw_response": retained_reference(raw_dir, raw_path),
                    "page_status": "open_exception",
                    "failure_type": category,
                    "proposal_only": True,
                }
            )
            exceptions.append(
                {
                    "document_id": page_id,
                    "page_id": page_id,
                    "reason": category,
                    "disposition": "separate_no_clobber_retry_required"
                    if retryable_error(exc)
                    else "client_review_required",
                }
            )
    generated_at = datetime.now(UTC).isoformat()
    out_path.write_text(
        json.dumps(
            {
                "artifact_type": "google_cloud_vision_handwriting_htr_v1",
                "schema_version": "1.0",
                "generated_at": generated_at,
                "engine": engine,
                "independence_group": "google",
                "proposal_only": True,
                "annotations": annotations,
            },
            indent=2,
        )
        + "\n"
    )
    adapter_path.write_text(
        json.dumps(
            {
                "adapter_type": "htr",
                "provider": "google_cloud_vision",
                "engine": engine,
                "independence_group": "google",
                "feature": "DOCUMENT_TEXT_DETECTION",
                "handwriting_language_hints": list(language_hints),
                "handwriting_region_sources": region_sources,
                "raw_response_directory": Path(raw_dir).name,
                "rendered_image_directory": Path(images_dir).name,
                "credential_reference": credential_env,
                "project_id": project_id,
                "location": location,
                "records": page_records,
                "proposal_only": True,
                "independent_validation_required": True,
                "provider_confidence_is_not_validation": True,
                "run_limits": {
                    "max_pages": max_pages,
                    "max_pdf_bytes": max_pdf_bytes,
                    "max_image_bytes": max_image_bytes,
                    "render_dpi": dpi,
                },
                "transport": {"timeout_seconds": timeout_seconds, "max_retries": max_retries},
            },
            indent=2,
        )
        + "\n"
    )
    exceptions_path.write_text(
        json.dumps({"summary": {"count": len(exceptions)}, "exceptions": exceptions}, indent=2)
        + "\n"
    )
    return {
        "pages": len(page_records),
        "annotations": len(annotations),
        "review_items": len(exceptions),
    }


def language_hints(value):
    """Parse a comma-separated hint list; blank enables provider auto-detection."""
    return tuple(item.strip() for item in value.split(",") if item.strip())


def main():
    """Run the disabled-by-default Google handwriting OCR lane."""
    try:
        load_project_env()
        defaults = {
            "enabled": env_bool("GOOGLE_HANDWRITING_OCR_ENABLED", False),
            "project_id": env_value("GOOGLE_HANDWRITING_OCR_PROJECT_ID", ""),
            "location": env_value("GOOGLE_HANDWRITING_OCR_LOCATION", "us"),
            "credential_env": env_value(
                "GOOGLE_HANDWRITING_OCR_CREDENTIAL_ENV", "GOOGLE_HANDWRITING_OCR_ACCESS_TOKEN"
            ),
            "language_hints": env_value(
                "GOOGLE_HANDWRITING_OCR_LANGUAGE_HINTS", "en-t-i0-handwrit"
            ),
            "max_pages": env_int("GOOGLE_HANDWRITING_OCR_MAX_PAGES", 1000),
            "max_pdf_bytes": env_int("GOOGLE_HANDWRITING_OCR_MAX_PDF_BYTES", 10_000_000),
            "max_image_bytes": env_int("GOOGLE_HANDWRITING_OCR_MAX_IMAGE_BYTES", 20_000_000),
            "timeout_seconds": env_float("GOOGLE_HANDWRITING_OCR_TIMEOUT_SECONDS", 180.0),
            "max_retries": env_int("GOOGLE_HANDWRITING_OCR_MAX_RETRIES", 2),
            "dpi": env_int("GOOGLE_HANDWRITING_OCR_RENDER_DPI", 300),
        }
    except ValueError as exc:
        sys.exit(f"Google handwriting OCR failed: {exc}")
    parser = argparse.ArgumentParser(
        description="Read detector-supplied handwriting regions with Google Cloud Vision OCR."
    )
    parser.add_argument("manifest", help="immutable intake manifest")
    parser.add_argument(
        "--regions", action="append", default=[], help="handwriting-region artifact; repeatable"
    )
    parser.add_argument("--out", required=True, help="new HTR annotation JSON")
    parser.add_argument("--adapter-out", required=True, help="new non-secret adapter handoff")
    parser.add_argument("--exceptions", required=True, help="new exception JSON")
    parser.add_argument("--raw-dir", required=True, help="new raw-response directory")
    parser.add_argument("--images-dir", required=True, help="new retained rendered-image directory")
    parser.add_argument("--enable", action="store_true", default=defaults["enabled"])
    parser.add_argument("--project-id", default=defaults["project_id"])
    parser.add_argument("--location", choices=("global", "us", "eu"), default=defaults["location"])
    parser.add_argument("--credential-env", default=defaults["credential_env"])
    parser.add_argument(
        "--language-hints",
        default=defaults["language_hints"],
        help="Comma-separated language hints passed to the OCR request.",
    )
    parser.add_argument("--max-pages", type=int, default=defaults["max_pages"])
    parser.add_argument("--max-pdf-bytes", type=int, default=defaults["max_pdf_bytes"])
    parser.add_argument(
        "--max-image-bytes",
        type=int,
        default=defaults["max_image_bytes"],
        help="Maximum bytes for one rendered page image. A larger render is an explicit exception.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=defaults["timeout_seconds"])
    parser.add_argument("--max-retries", type=int, default=defaults["max_retries"])
    parser.add_argument(
        "--render-dpi",
        type=int,
        default=defaults["dpi"],
        help="Resolution used to render each retained page before submission.",
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    if not args.enable:
        sys.exit(
            "Google handwriting OCR failed: disabled; set GOOGLE_HANDWRITING_OCR_ENABLED=true or pass --enable"
        )
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", args.credential_env):
        sys.exit(
            "Google handwriting OCR failed: credential environment variable must be uppercase with underscores"
        )
    try:
        result = run_lane(
            args.manifest,
            args.out,
            args.adapter_out,
            args.exceptions,
            args.raw_dir,
            args.images_dir,
            args.project_id,
            args.location,
            reauthorize_google.RefreshingToken(args.credential_env),
            region_paths=args.regions,
            language_hints=language_hints(args.language_hints),
            max_pages=args.max_pages,
            max_pdf_bytes=args.max_pdf_bytes,
            max_image_bytes=args.max_image_bytes,
            timeout_seconds=args.timeout_seconds,
            max_retries=args.max_retries,
            dpi=args.render_dpi,
            credential_env=args.credential_env,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Google handwriting OCR failed: {exc}")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
