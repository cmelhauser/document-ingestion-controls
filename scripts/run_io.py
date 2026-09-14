#!/usr/bin/env python3
"""Provider-neutral run I/O: manifests, retained pages, hashes, and no-clobber paths.

These primitives enforce the "new output directory for every run" invariant and
resolve manifest-relative evidence. They are shared by every lane, so they live
outside any one vendor's adapter: a Google or OpenRouter run must not import the
OpenAI adapter to find out whether it is about to overwrite retained evidence.
"""

import hashlib
import json
import re
from pathlib import Path


def sha256(path):
    """Hash an immutable retained page for provider provenance."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(path):
    """Read an intake manifest with enough immutable page provenance to process it."""
    data = json.loads(Path(path).read_text())
    pages = data.get("pages") if isinstance(data, dict) else None
    if not isinstance(pages, list) or not all(isinstance(page, dict) for page in pages):
        raise ValueError("Manifest must contain a pages list")
    if not all(page.get("page_id") and page.get("page_pdf") for page in pages):
        raise ValueError("Every manifest page requires page_id and page_pdf")
    return data


def empty_output_path(path):
    """Refuse to overwrite an earlier extraction artifact."""
    path = Path(path)
    if path.exists():
        raise ValueError(f"Output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def empty_output_directory(path):
    """Create a new raw-response directory without replacing retained evidence."""
    path = Path(path)
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError(f"Raw response directory must be new or empty: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def shared_output_directory(path, *reserved_names):
    """Create a raw-response directory shared by several stages of one lane.

    ``empty_output_directory`` refuses any directory that is not empty, which is
    right when a command owns its raw directory outright. It is wrong for a lane
    whose stages run in sequence into one directory: the first stage populates it
    and every later stage is then refused.

    The documented five-step table sequence does exactly that -- ``profile``,
    ``rows``, ``audit``, ``mappings``, and ``assemble`` are all given
    ``RUN/tables/raw`` -- so it failed at step two, every time, with
    ``Raw response directory must be new or empty``. The published workflow could
    not be run as written.

    The guarantee that matters is that a retained raw response is never
    overwritten, and each stage writes its own ``{role}_response.json``. So the
    check is scoped to the files this stage will actually write: a rerun of the
    same stage is still refused, while a later stage of the same lane proceeds.
    """
    path = Path(path)
    if path.exists() and not path.is_dir():
        raise ValueError(f"Raw response directory must be a directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    for name in reserved_names:
        if (path / name).exists():
            raise ValueError(f"Refusing to overwrite retained raw response: {path / name}")
    return path


def safe_label(value):
    """Create a stable raw-artifact filename while retaining the unmodified ID in JSON."""
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value)).strip("_") or "page"


def resolve_manifest_file(manifest_path, value):
    """Resolve a manifest-relative artifact without allowing path escape or absolute input."""
    root = Path(manifest_path).resolve().parent
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValueError("Manifest artifact path must be relative")
    path = (root / candidate).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("Manifest artifact path escapes the manifest directory") from exc
    return path


def resolve_page(manifest_path, page, max_pdf_bytes):
    """Resolve a retained one-page PDF and enforce the configured submission-size limit."""
    path = resolve_manifest_file(manifest_path, page["page_pdf"])
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise ValueError(f"Page PDF is not readable: {path}")
    if path.stat().st_size > max_pdf_bytes:
        raise ValueError(f"Page PDF exceeds configured byte limit: {path.name}")
    return path


# A lane's retained responses for one page: `000086_run__p0086.json`, and the
# retry it made when the first came back without text, `…__p0086__retry1.json`.
RESPONSE_ORDINAL = re.compile(r"^\d+_")
RETRY_SUFFIX = re.compile(r"__retry\d+$")


def response_document_text(payload):
    """The page text a retained extractor response carries, or empty."""
    response = payload.get("response") if isinstance(payload, dict) else None
    document = response.get("document") if isinstance(response, dict) else None
    text = document.get("text") if isinstance(document, dict) else None
    return text if isinstance(text, str) else ""


def retained_response_text(path):
    """Read one retained extractor response's page text, or empty when it has none."""
    try:
        return response_document_text(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return ""


def retained_responses(directory):
    """Index a lane's retained extractor responses by document, taking one that read the page.

    A lane whose request came back without text retries it and keeps both files.
    On the commission run 222 of 716 pages had text only in the retry, and every
    reader that indexed the first file per page read an empty page for all 222:
    brand recovery called them unreadable, specifier recovery found no
    specifier, and the page review graded their corrections as if the extractor
    had never read them. A retry is the same page, so the index keys it by the
    page and takes the first response that carries text, falling back to the
    first response when none does.
    """
    index = {}
    for path in sorted(Path(directory).glob("*.json")):
        document = RETRY_SUFFIX.sub("", RESPONSE_ORDINAL.sub("", path.stem))
        held = index.get(document)
        if held is None or (not retained_response_text(held) and retained_response_text(path)):
            index[document] = path
    return index


def response_tokens(payload):
    """Each word a retained extractor response read, with where it sits on the page.

    `(text, top, bottom)` in normalized page height, offset by the page's
    position so that words on two pages never share a row. A row join reads
    these: two readings on one printed row overlap vertically.
    """
    response = payload.get("response") if isinstance(payload, dict) else None
    document = response.get("document") if isinstance(response, dict) else None
    if not isinstance(document, dict):
        return []
    text = document.get("text") if isinstance(document.get("text"), str) else ""
    found = []
    for number, page in enumerate(document.get("pages") or []):
        for token in page.get("tokens") or [] if isinstance(page, dict) else []:
            layout = token.get("layout") or {}
            segments = (layout.get("textAnchor") or {}).get("textSegments") or []
            reading = "".join(
                text[int(segment.get("startIndex", 0)) : int(segment.get("endIndex", 0))]
                for segment in segments
            ).strip()
            corners = (layout.get("boundingPoly") or {}).get("normalizedVertices") or []
            if reading and corners:
                heights = [corner.get("y", 0.0) for corner in corners]
                found.append((reading, number + min(heights), number + max(heights)))
    return found


def retained_response_tokens(path):
    """Read one retained extractor response's positioned words, or none when unreadable."""
    try:
        return response_tokens(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


def resolve_text(manifest_path, page, max_text_chars):
    """Return bounded retained native text when the manifest supplies a safe sibling."""
    value = page.get("text_file")
    if not isinstance(value, str) or not value:
        return None
    path = resolve_manifest_file(manifest_path, value)
    if not path.is_file():
        return None
    text = path.read_text(errors="replace").strip()
    if len(text) > max_text_chars:
        raise ValueError(f"Retained native text exceeds configured character limit: {path.name}")
    return text or None
