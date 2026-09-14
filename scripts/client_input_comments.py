#!/usr/bin/env python3
"""Parse general client comment files into hash-bound reasoning-only context."""

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from client_review.context import source_records, stratified_records

COMMENT_COLUMNS = ("client_comment", "comment", "comments", "note", "notes", "text")
SUPPORTED_SUFFIXES = {".csv", ".json", ".md", ".pdf", ".txt", ".tsv"}
# Clients send comments as whatever they already have. A scanned or exported PDF
# is common enough that refusing it pushes an operator into retyping, and a
# retyped comment is no longer the client's verbatim words.
PDF_TEXT_COMMAND = ("pdftotext", "-layout")


def digest(path):
    """Hash an immutable client-supplied comment file."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalized_comment(item, index):
    """Preserve one comment object while exposing its verbatim comment text."""
    if isinstance(item, str):
        text, fields = item, {}
    elif isinstance(item, dict):
        fields = dict(item)
        text = next(
            (
                value
                for key in COMMENT_COLUMNS
                if isinstance((value := item.get(key)), str) and value.strip()
            ),
            "",
        )
    else:
        raise ValueError("Each client comment must be text or an object")
    if not text.strip():
        raise ValueError("Each client comment requires non-empty comment text")
    result = {
        "review_item_id": f"client-input-comment-{index:06d}",
        "client_comment": text,
        "field": "client_comment",
        "client_review_required": True,
        "evidence_role": "untrusted_client_context_not_source_evidence",
    }
    for key in ("document_id", "page_id", "field", "scope"):
        if fields.get(key) not in (None, ""):
            result[key] = fields[key]
    if fields:
        result["source_fields"] = fields
    return result


def json_comments(path):
    """Read a JSON list or an object containing a comments list."""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    items = value.get("comments") if isinstance(value, dict) else value
    if not isinstance(items, list):
        raise ValueError("JSON client comments must be a list or contain a comments list")
    return items


def pdf_comments(path):
    """Read comments from a PDF, one per non-empty line, preserving wording.

    Extraction is text-layer only: a PDF with no text layer yields nothing and is
    refused rather than silently contributing an empty context. Comments are
    reasoning-only context, so a scanned page belongs in the document intake
    where it becomes evidence with provenance, not here.
    """
    if shutil.which(PDF_TEXT_COMMAND[0]) is None:
        raise ValueError(
            "pdftotext is required to read PDF client comments; install poppler "
            "or supply the comments as CSV, TSV, TXT, Markdown, or JSON"
        )
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [*PDF_TEXT_COMMAND, str(path), "-"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise ValueError(f"Could not read PDF client comments: {path.name}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def unstructured_row(row, index):
    """Turn one spreadsheet row into a comment, keeping each cell recoverable.

    A client writing notes in a spreadsheet produces trailing empty cells and no
    useful header. Empty cells are dropped and the rest joined, because the
    comment a person typed across two columns is still one comment; every
    original cell is retained beside it so nothing is lost to the join.
    """
    cells = [cell.strip() for cell in row if isinstance(cell, str) and cell.strip()]
    if not cells:
        return None
    item = {"client_comment": " ".join(cells)}
    for column, cell in enumerate(cells, start=1):
        item[f"column_{column}"] = cell
    item["source_row"] = index
    return item


def delimited_comments(path, delimiter):
    """Read comments from a CSV/TSV, headed or freeform.

    A headed file keeps every source column, so a client who labels a
    ``document_id`` or ``scope`` alongside the comment has that carried through.
    A file with no recognised comment column is treated as what it usually is --
    someone's notes typed into a spreadsheet -- and every non-empty row becomes a
    comment. Refusing that would push an operator into retyping, and a retyped
    comment is no longer the client's words.

    The first row of a freeform file is a comment, not a discarded header.
    """
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.reader(stream, delimiter=delimiter))
    if not rows:
        return []
    header = [str(name).strip().casefold() for name in rows[0]]
    if any(name in COMMENT_COLUMNS for name in header):
        with Path(path).open(newline="", encoding="utf-8-sig") as stream:
            return list(csv.DictReader(stream, delimiter=delimiter))
    return [
        item
        for index, row in enumerate(rows, start=1)
        if (item := unstructured_row(row, index)) is not None
    ]


def parse_comments(path):
    """Parse one supported comments file without changing comment values."""
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported client comments file type: {path.suffix or '[none]'}")
    if path.suffix.lower() == ".json":
        items = json_comments(path)
    elif path.suffix.lower() in {".csv", ".tsv"}:
        items = delimited_comments(path, "," if path.suffix.lower() == ".csv" else "\t")
    elif path.suffix.lower() == ".pdf":
        items = pdf_comments(path)
    else:
        items = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not items:
        raise ValueError("Client comments file must contain at least one comment")
    return [normalized_comment(item, index) for index, item in enumerate(items, start=1)]


def build_context(paths, records=None, records_path=None, maximum=75):
    """Combine comment files append-only and bind each to its source hash.

    The relationship lanes -- reference discovery, iterative proposals, and
    cross-packet verification -- take this artifact as their only input and
    reason over its ``pilot_records``. Built from comments alone that list is
    empty, so on a first run, before any client workbook has come back, those
    lanes had nothing to work on and reported success on nothing. Passing the
    retained records here gives them the same deterministic pilot sample
    ``client_review_context.py`` supplies after a workbook returns.
    """
    all_comments, sources = [], []
    for path in paths:
        parsed = parse_comments(path)
        offset = len(all_comments)
        for index, item in enumerate(parsed, start=1):
            item["review_item_id"] = f"client-input-comment-{offset + index:06d}"
            item["source_file"] = Path(path).name
            item["source_sha256"] = digest(path)
        all_comments.extend(parsed)
        sources.append({"name": Path(path).name, "sha256": digest(path), "comments": len(parsed)})
    selected = stratified_records(records or [], maximum) if records is not None else []
    return {
        "artifact_type": "client_review_context_v1",
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "proposal_only": True,
        "reasoning_only": True,
        "independent_consensus_input": False,
        "production_approval_permitted": False,
        "gate_status": "blocked_pending_client_review",
        "policy": {
            "client_comments_are_untrusted_context": True,
            "source_evidence_must_support_every_proposal": True,
            "comments_cannot_create_gl_or_payment_facts": True,
            "comments_cannot_authorize_or_clear_controls": True,
        },
        "source": {
            "comment_files": sources,
            "records_path": Path(records_path).name if records_path else None,
            "records_sha256": digest(records_path) if records_path else None,
        },
        "summary": {
            "comment_files": len(sources),
            "comments": len(all_comments),
            "pilot_records": len(selected),
            "pilot_max_documents": maximum if records is not None else 0,
        },
        "client_comments": all_comments,
        "pilot_records": selected,
    }


def run(paths, out_path, records_path=None, maximum=75):
    """Write a new reasoning-only context artifact without clobbering prior context."""
    out_path = Path(out_path)
    if out_path.exists():
        raise FileExistsError(f"refusing to overwrite client input comments context: {out_path}")
    records = source_records(records_path) if records_path else None
    context = build_context(paths, records, records_path, maximum)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(context, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return context


def main():
    """Parse one or more general client comment files."""
    parser = argparse.ArgumentParser(
        description="Build hash-bound reasoning-only context from general client comments."
    )
    parser.add_argument(
        "comments",
        nargs="+",
        help="Client comment files: JSON, CSV, TSV, TXT, Markdown, or PDF. Repeatable.",
    )
    parser.add_argument("--out", required=True, help="new client_review_context_v1 JSON")
    parser.add_argument(
        "--records",
        help=(
            "Consensus or proofed JSON supplying the deterministic pilot sample the "
            "relationship lanes reason over. Without it they receive no records."
        ),
    )
    parser.add_argument(
        "--max-documents",
        type=int,
        default=75,
        help="Maximum source documents this invocation may consider.",
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        result = run(args.comments, args.out, args.records, args.max_documents)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Preserved {result['summary']['comments']} client comments as reasoning-only context.")
    print(
        f"  pilot records available to the relationship lanes: {result['summary']['pilot_records']}"
    )


if __name__ == "__main__":
    main()
