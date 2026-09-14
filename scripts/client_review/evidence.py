"""Resolve and render the source page behind a client question.

A question without its page is an assertion. The reviewer is being asked whether
a value is right, and the only way to answer is to look at what the document
actually says -- so every answerable question carries the page it came from,
rendered from the immutable one-page master rather than described in prose.

Rendering is JPEG on purpose. The instruction PDF embeds the bytes directly as a
DCTDecode image, so the page travels into the document without being decoded and
re-encoded, and a review pack of fifty pages stays a file someone can email.

Nothing here is allowed to invent evidence. A page that cannot be located or
cannot be rendered produces an explicit exception and a question with no example,
never a question with a substitute page.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

RENDER_TIMEOUT_SECONDS = 120


def page_index(manifest_path: Path) -> dict[str, Path]:
    """Map every page identifier to its retained one-page PDF.

    Paths in an intake manifest are recorded relative to the intake root, which
    is the directory the manifest itself sits in -- intake writes its one-page
    masters to ``pages/`` beneath that, so a manifest at ``RUN/pages/`` refers to
    ``RUN/pages/pages/``. Resolving against the manifest's own location rather
    than the working directory lets a pack be built from anywhere.
    """
    manifest_path = Path(manifest_path)
    data = json.loads(manifest_path.read_text())
    pages = data.get("pages") if isinstance(data, dict) else None
    if not isinstance(pages, list) or not pages:
        raise ValueError(f"{manifest_path} must contain a non-empty pages list")
    root = manifest_path.resolve().parent
    index: dict[str, Path] = {}
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_id = page.get("page_id")
        page_pdf = page.get("page_pdf")
        if not isinstance(page_id, str) or not isinstance(page_pdf, str):
            continue
        candidate = Path(page_pdf)
        index[page_id] = candidate if candidate.is_absolute() else root / candidate
    if not index:
        raise ValueError(f"{manifest_path} contains no page_id to page_pdf mapping")
    return index


def render_page(page_path: Path, image_path: Path, dpi: int, runner=subprocess.run) -> Path:
    """Render one immutable page PDF to a retained JPEG."""
    image_path = Path(image_path)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = image_path.with_suffix("")
    result = runner(  # noqa: S603 - fixed argv, no shell
        [  # noqa: S607 - poppler is a documented install dependency
            "pdftoppm",
            "-f",
            "1",
            "-l",
            "1",
            "-singlefile",
            "-r",
            str(dpi),
            "-jpeg",
            str(page_path),
            str(prefix),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=RENDER_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise ValueError(f"pdftoppm failed: {(result.stderr or '').strip() or result.returncode}")
    if not image_path.is_file():
        raise ValueError(f"pdftoppm did not produce {image_path.name}")
    return image_path


NEAR_BLANK = "near_blank"


def uninformative_pages(scan_profile: Path | None, manifest: Path | None) -> frozenset[str]:
    """Return the pages that cannot answer a question about a document's content.

    `scan_profile.py` already flags `near_blank` on every page whose ink coverage
    is under its floor, and nothing read it. A blank page therefore reached a
    client as the worked example for a question about layout, they answered
    about the blank page, and that answer was applied to 285 documents of which
    only four were blank.

    The profile records a page index; the manifest maps that index to the page
    identifier the review lane uses. Without either, nothing is excluded -- a
    missing profile makes for a worse pack, never a wrong one.
    """
    if scan_profile is None or manifest is None:
        return frozenset()
    try:
        profile = json.loads(Path(scan_profile).read_text(encoding="utf-8"))
        entries = json.loads(Path(manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    flagged = {
        page.get("page_index")
        for page in (profile.get("pages") or [])
        if isinstance(page, dict) and NEAR_BLANK in (page.get("quality_problems") or [])
    }
    if not flagged:
        return frozenset()
    pages = entries.get("pages") or entries.get("records") or []
    found = set()
    for entry in pages:
        if not isinstance(entry, dict):
            continue
        number = entry.get("source_page_number")
        if isinstance(number, int) and (number - 1) in flagged:
            identifier = entry.get("page_id") or entry.get("document_id")
            if identifier:
                found.add(str(identifier))
    return frozenset(found)


def each_example(question: dict[str, Any]) -> list[dict[str, Any]]:
    """Every worked example on a question, the singular one included.

    A question shows several examples because one was not enough: a group of 285
    documents was represented by its first page, that page was blank, and the
    client answered "Blank Page. Can be ignored." for the whole group. The
    singular `example` is retained so nothing downstream that reads it breaks.
    """
    candidates = [question.get("example")] + list(question.get("examples") or [])
    seen, ordered = set(), []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        # Deduplicated by page, not by object identity: the singular `example`
        # and the first entry of `examples` describe the same page as two
        # separate dicts, and identity would render it twice.
        key = item.get("page_id") or item.get("document_id") or ""
        if key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered


def attach_examples(
    questions: list[dict[str, Any]],
    manifest_path: Path | None,
    images_dir: Path,
    dpi: int = 150,
    max_image_bytes: int = 4_000_000,
    renderer=render_page,
) -> list[dict[str, Any]]:
    """Render one example page per question and report every page it could not get.

    A page rendered larger than the configured bound is retained as an exception
    rather than silently downscaled, matching how every other size limit in this
    pipeline behaves: an oversized render is a fact about the source, and quietly
    shrinking it would hide a scan that needs attention.
    """
    if manifest_path is None:
        for question in questions:
            for example in each_example(question):
                example["page_image"] = None
                example["page_image_status"] = "not_requested"
        return []
    index = page_index(Path(manifest_path))
    images_dir = Path(images_dir)
    exceptions: list[dict[str, Any]] = []
    rendered: dict[str, Any] = {}
    for question in questions:
        for example in each_example(question):
            page_id = example.get("page_id") or example.get("document_id") or ""
            if page_id in rendered:
                example["page_image"] = rendered[page_id]
                example["page_image_status"] = "rendered"
                continue
            page_path = index.get(page_id)
            if page_path is None or not page_path.is_file():
                example["page_image"] = None
                example["page_image_status"] = "page_not_found"
                exceptions.append(
                    {
                        "priority": "high",
                        "document_id": example.get("document_id", ""),
                        "page_id": page_id,
                        "region_id": "",
                        "field": question["question_id"],
                        "reason": f"example page not found for {page_id or 'an unnamed page'}",
                        "review_source": "client_review_lane",
                        "disposition": "client_review_required",
                    }
                )
                continue
            image_path = images_dir / f"{page_id}.jpg"
            try:
                if not image_path.is_file():
                    renderer(page_path, image_path, dpi)
                size = image_path.stat().st_size
                if size > max_image_bytes:
                    raise ValueError(
                        f"rendered page is {size} bytes, above the {max_image_bytes}-byte bound"
                    )
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                example["page_image"] = None
                example["page_image_status"] = "render_failed"
                exceptions.append(
                    {
                        "priority": "high",
                        "document_id": example.get("document_id", ""),
                        "page_id": page_id,
                        "region_id": "",
                        "field": question["question_id"],
                        "reason": f"example page could not be rendered: {error}",
                        "review_source": "client_review_lane",
                        "disposition": "client_review_required",
                    }
                )
                continue
            rendered[page_id] = str(image_path)
            example["page_image"] = str(image_path)
            example["page_image_status"] = "rendered"
    return exceptions
