#!/usr/bin/env python3
"""Create deterministic, review-required deduplication views of LLM display rows."""

import argparse
import csv
import json
import re
import unicodedata
from pathlib import Path

from cli_help import apply_shared_help


def normalize(value):
    """Normalize visible text for exact duplicate candidate comparison only."""
    if value is None:
        return ""
    value = unicodedata.normalize("NFKC", str(value)).casefold()
    return re.sub(r"\s+", " ", value).strip()


def load_layout(path):
    """Read a layout's columns, refusing one whose columns are not fully labelled."""
    data = json.loads(Path(path).read_text())
    columns = data.get("columns", [])
    if not columns or any(not item.get("key") or not item.get("display_label") for item in columns):
        raise ValueError("layout requires non-empty key and display_label columns")
    return columns


def deduplicate(proposals, columns):
    """Return first-seen visible rows and exact duplicate candidates."""
    labels = [column["display_label"] for column in columns]
    seen = {}
    unique = []
    duplicates = []
    for page in proposals:
        page_id = page.get("page_id")
        for index, row in enumerate(page.get("row_proposals", []), start=1):
            values = {
                label: row.get(column["key"]) for label, column in zip(labels, columns, strict=True)
            }
            key = tuple(normalize(values[label]) for label in labels)
            entry = {
                "page_id": page_id,
                "row_index": index,
                "row_number": row.get("row_number") or index,
                "values": values,
            }
            if key in seen:
                representative = seen[key]
                duplicates.append(
                    {
                        "representative_page_id": representative["page_id"],
                        "representative_row_index": representative["row_index"],
                        "representative_row_number": representative["row_number"],
                        "duplicate_page_id": page_id,
                        "duplicate_row_index": index,
                        "duplicate_row_number": entry["row_number"],
                        "match_basis": "exact normalized match across all display columns",
                        "review_disposition": "client_review_required",
                    }
                )
            else:
                seen[key] = entry
                unique.append(values)
    return labels, unique, duplicates


def write_csv(path, fieldnames, rows):
    """Write a CSV to a fresh path, refusing to overwrite an existing one."""
    path = Path(path)
    if path.exists():
        raise ValueError(f"refusing to overwrite existing output: {path}")
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "proposals", help="Row proposals to split into unique and exact-duplicate views."
    )
    parser.add_argument("layout")
    parser.add_argument(
        "--unique-out",
        required=True,
        help="Destination path for the deterministic unique-row view.",
    )
    parser.add_argument(
        "--duplicates-out",
        required=True,
        help="Destination path for the exact-duplicate view. Duplicates are retained, never dropped.",
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    columns = load_layout(args.layout)
    proposals = json.loads(Path(args.proposals).read_text())
    labels, unique, duplicates = deduplicate(proposals, columns)
    write_csv(args.unique_out, labels, unique)
    write_csv(
        args.duplicates_out,
        [
            "representative_page_id",
            "representative_row_index",
            "representative_row_number",
            "duplicate_page_id",
            "duplicate_row_index",
            "duplicate_row_number",
            "match_basis",
            "review_disposition",
        ],
        duplicates,
    )
    print(json.dumps({"unique_rows": len(unique), "duplicate_candidates": len(duplicates)}))


if __name__ == "__main__":
    main()
