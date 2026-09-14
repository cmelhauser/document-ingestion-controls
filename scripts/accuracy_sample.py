#!/usr/bin/env python3
"""Draw the pages to read against their own image, and say what they represent.

`sampling.py` draws a monetary-unit sample for projection, and it samples only
the auto-accepted population because anything in the exception queue is worked
in full. That is right for what it measures and it cannot answer this question:
on a corpus where nothing is auto-accepted -- 716 of 716 excluded on this run --
the frame is empty, and the accuracy of the extraction goes unmeasured precisely
where it matters most.

Measuring extraction accuracy is a question about documents that have *not* been
accepted. So this draws from the whole population, and it exists because the
alternative is what happened here: 26 pages read by hunting -- the biggest page,
the oddest form, the family nobody had opened. That is efficient for finding a
new class of defect and worthless for estimating how common one is, and it
yields no accuracy figure at all.

Two properties make the difference. Selection inside a stratum is **random**, so
the pages are not the ones that looked interesting. And it is **reproducible**
from the document ids alone -- the same corpus draws the same sample, with no
seed to record and no way to redraw until the answer improves.

Nothing here scores anything. It names the pages, their source images, and the
weight each stratum carries, so a reader's findings can be weighted back up.
"""

import argparse
import collections
import csv
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from cli_help import apply_shared_help

ARTIFACT_TYPE = "accuracy_sample_v1"
UNREADABLE_SENTINEL = "-99999"


def money(text):
    """Read a typed money companion, ignoring the unreadable sentinel."""
    if str(text).strip() in ("", UNREADABLE_SENTINEL):
        return Decimal(0)
    try:
        return Decimal(str(text))
    except InvalidOperation:
        return Decimal(0)


def draw_order(document_id):
    """Return a stable, arbitrary ordering key for one document.

    A hash of the id rather than a seeded generator: the draw is reproducible
    from the corpus alone, so nobody has to record a seed, and nobody can redraw
    with a different one until the sample reads better.
    """
    return hashlib.sha256(document_id.encode()).hexdigest()


def squash(text):
    """Compare a stratum name without case, spacing or punctuation."""
    return re.sub(r"[^a-z0-9]", "", str(text).casefold())


def canonical_strata(values):
    """Map every spelling of a layout to one stratum name.

    A page layout arrives spelled several ways -- `murbrook` and `murb rook`,
    four spellings of Marlow/Bramwell, five of Lumen Weft -- and a stratum split
    across them measures nothing: it drew 128 pages across 26 "layouts" that are
    eight. Spellings that squash together are one, and a squashed name contained
    in another is the same layout said at greater length, so the shortest common
    core names the group.
    """
    keys = {squash(value) for value in values if squash(value)}
    canonical = {}
    for key in keys:
        cores = sorted((other for other in keys if other in key), key=len)
        canonical[key] = cores[0] if cores else key
    return canonical


def stratum_of(document, field, fallback, canonical=None):
    """Return the stratum a document belongs to."""
    value = str(document.get(field) or "").strip()
    if not value:
        return fallback
    key = squash(value)
    return (canonical or {}).get(key, key) or fallback


def value_by_document(lines, column):
    """Total the named money column per document."""
    totals = collections.Counter()
    for row in lines:
        totals[row.get("document_id", "")] += money(row.get(f"{column}__amount"))
    return totals


def strata(documents, lines, field, fallback, column):
    """Group the population, carrying what each group is worth."""
    totals = value_by_document(lines, column)
    canonical = canonical_strata(document.get(field) for document in documents)
    rows = collections.Counter()
    grouped = collections.defaultdict(list)
    for document in documents:
        name = stratum_of(document, field, fallback, canonical)
        grouped[name].append(document)
        rows[name] += 1
    return {
        name: {
            "documents": sorted(members, key=lambda d: draw_order(d.get("document_id", ""))),
            "document_count": len(members),
            "value": float(sum(totals[d.get("document_id", "")] for d in members)),
        }
        for name, members in grouped.items()
    }


def image_for(document_id, images):
    """Return the retained page image for a document, if the run kept one."""
    if not images:
        return ""
    matches = sorted(Path(images).glob(f"*{document_id}*"))
    return str(matches[0]) if matches else ""


def draw(documents, lines, field, fallback, per_stratum, column, images=""):
    """Select the pages to read, and report what each stratum represents."""
    grouped = strata(documents, lines, field, fallback, column)
    selected, coverage = [], {}
    for name, group in sorted(grouped.items()):
        taken = group["documents"][:per_stratum]
        coverage[name] = {
            "documents": group["document_count"],
            "value": round(group["value"], 2),
            "drawn": len(taken),
            # What one read page stands for. A finding on a page here speaks for
            # this many documents, and quoting a rate without it repeats exactly
            # the mistake this lane exists to stop.
            "documents_per_page_read": round(group["document_count"] / len(taken), 1)
            if taken
            else None,
        }
        for document in taken:
            doc_id = document.get("document_id", "")
            selected.append(
                {
                    "document_id": doc_id,
                    "stratum": name,
                    "source_page_number": document.get("source_page_number", ""),
                    "source_file": document.get("source_file", ""),
                    "page_image": image_for(doc_id, images),
                    "line_rows": sum(1 for row in lines if row.get("document_id") == doc_id),
                }
            )
    return selected, coverage


def read_csv(path):
    """Read one exported grain."""
    with Path(path).open(newline="") as handle:
        return list(csv.DictReader(handle))


def main():
    parser = argparse.ArgumentParser(
        description="Draw the pages to read against their own image, with the weight each carries."
    )
    parser.add_argument("--documents", required=True, help="exported document grain")
    parser.add_argument("--lines", required=True, help="exported line grain")
    parser.add_argument(
        "--stratify-by",
        default="brand_name",
        help=(
            "the document field that separates one page layout from another. "
            "Every defect found by reading on this corpus was layout-specific, "
            "so a sample that does not stratify by layout measures the wrong thing."
        ),
    )
    parser.add_argument(
        "--unstratified-label",
        default="(unidentified)",
        help="the stratum documents with no value in that field belong to",
    )
    parser.add_argument("--per-stratum", type=int, default=8, help="pages to draw per stratum")
    parser.add_argument(
        "--value-column",
        default="commission_amount",
        help="the money column each stratum's weight is measured in",
    )
    parser.add_argument("--images", default="", help="directory of retained page images")
    parser.add_argument("--out", required=True, help="the sample plan")
    parser.add_argument("--worksheet", help="the same pages as a CSV a reader can work")
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args()
    if args.per_stratum < 1:
        sys.exit("--per-stratum must be at least 1")
    try:
        documents = read_csv(args.documents)
        lines = read_csv(args.lines)
        if not documents:
            raise ValueError(f"No documents in {args.documents}")
        selected, coverage = draw(
            documents,
            lines,
            args.stratify_by,
            args.unstratified_label,
            args.per_stratum,
            args.value_column,
            args.images,
        )
    except (OSError, ValueError, csv.Error) as exc:
        sys.exit(f"Accuracy sample failed: {exc}")
    drawn_value = sum(entry["value"] for entry in coverage.values())
    summary = {
        "artifact_type": ARTIFACT_TYPE,
        "generated_at": datetime.now(UTC).isoformat(),
        "population_documents": len(documents),
        "pages_drawn": len(selected),
        "share_of_documents": round(len(selected) / len(documents), 4),
        "strata": len(coverage),
        "value_represented": round(drawn_value, 2),
        "stratify_by": args.stratify_by,
        "coverage": coverage,
        "findings": [
            "Selection inside a stratum is random and reproducible from the "
            "document ids, so these are not the pages that looked interesting.",
            "Nothing here is scored. A reader's findings weight up by "
            "`documents_per_page_read`, which is what one page stands for.",
            "Every document is in the frame. Accuracy is a question about the "
            "documents that were not accepted, which is most of them.",
        ],
    }
    Path(args.out).write_text(json.dumps({"summary": summary, "sample": selected}, indent=2) + "\n")
    if args.worksheet:
        columns = [
            "document_id",
            "stratum",
            "source_page_number",
            "line_rows",
            "page_image",
            "source_file",
        ]
        with Path(args.worksheet).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(selected)
    if not args.quiet:
        print(
            f"Population: {len(documents)} documents   drawn: {len(selected)} "
            f"({summary['share_of_documents']:.1%})"
        )
        for name, entry in sorted(coverage.items(), key=lambda kv: -kv[1]["value"]):
            print(
                f"  {name[:22]:24s} {entry['drawn']:>3} of {entry['documents']:>4} "
                f"  1 page speaks for {entry['documents_per_page_read']:>5}   "
                f"{entry['value']:>14,.0f}"
            )


if __name__ == "__main__":
    main()
