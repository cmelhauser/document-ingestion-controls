#!/usr/bin/env python3
"""Bring a record artifact's fields and its header and line views into step.

A record carries each reading twice: flat in `fields`, which the export reads,
and nested in `header`, `lines` and `accessorials`, which attribution,
arithmetic, valuation, entity resolution and the canonical export read. Lanes
that wrote one and not the other left the two apart. On the commission run
`multi_engine_vote.py` and `arithmetic_reconcile.py` had put 4,099 accepted
values in the fields alone, so those controls decided without them, and
`apply_mappings.py` had put 393 approved mappings in the header alone, so the
export left every one of them out.

This carries each approved mapping a view holds into its empty field, accepted
as the mapping lane now writes it, and then brings every view up to its fields.
Nothing is overwritten: a field that holds a value keeps it, and a view entry no
field names is kept and reported. With `--check` it writes no records and fails
when any document is out of step, so a run can gate on it.

It changes no reading, no review status and no control.
"""

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from apply_mappings import mapped_field
from cli_help import apply_shared_help
from consensus import refresh_views, view_cells, views_out_of_step
from run_io import empty_output_path

ARTIFACT_TYPE = "records_in_step_report_v1"
ROW_INDEX = re.compile(r"\[\d+\]")


def column(path):
    """A field path with its row index dropped, for counting by column."""
    return ROW_INDEX.sub("[]", path)


def held_mapping(cell):
    """Whether a view cell holds an approved mapping's value."""
    return (
        isinstance(cell, dict)
        and bool(cell.get("mapped_from_source_label"))
        and cell.get("value") not in (None, "")
    )


def measure(document):
    """The cells behind their fields, the mappings only a view holds, and the rest."""
    gap = views_out_of_step(document)
    cells = dict(view_cells(document))
    mappings = [path for path in gap["view_only"] if held_mapping(cells.get(path))]
    others = [path for path in gap["view_only"] if path not in mappings]
    return gap["behind"], mappings, others


def acceptance_of(field):
    """What accepted a field, or how consensus left it."""
    acceptance = field.get("acceptance") if isinstance(field.get("acceptance"), dict) else {}
    if acceptance.get("accepted_by"):
        return str(acceptance["accepted_by"])
    return (
        "consensus" if field.get("accepted") else str(field.get("consensus_flag") or "unaccepted")
    )


def carry_mappings(document, paths):
    """Carry each approved mapping a view alone holds into its empty field."""
    fields = document.setdefault("fields", {})
    cells = dict(view_cells(document))
    for path in paths:
        cell = cells[path]
        prior = fields.get(path) if isinstance(fields.get(path), dict) else {}
        promotion = {
            "value": cell["value"],
            "source_label": cell["mapped_from_source_label"],
            "registry_rule_id": cell.get("registry_rule_id"),
            "approval_authority": cell.get("approval_authority"),
            "agreeing_engines": cell.get("agreeing_engines"),
            "consensus_flag": cell.get("consensus_flag"),
        }
        fields[path] = {**mapped_field(promotion, prior), "carried_from_view": path}


def reconcile(documents, write):
    """Measure every document, and bring it into step when `write`."""
    behind_by, mappings_by, kept_by = Counter(), Counter(), Counter()
    out_of_step = []
    for document in documents:
        behind, mappings, others = measure(document)
        fields = document.get("fields") or {}
        behind_by.update(acceptance_of(fields[path]) for path in behind)
        mappings_by.update(column(path) for path in mappings)
        kept_by.update(column(path) for path in others)
        if behind or mappings:
            out_of_step.append(
                {
                    "document_id": document.get("document_id"),
                    "behind": len(behind),
                    "mappings": len(mappings),
                }
            )
        if write and (behind or mappings):
            carry_mappings(document, mappings)
            refresh_views(document)
    return {
        "artifact_type": ARTIFACT_TYPE,
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "brought_into_step" if write else "check",
        "documents": len(documents),
        "documents_out_of_step": len(out_of_step),
        "cells_brought_into_step": sum(behind_by.values()),
        "behind_by_acceptance": dict(behind_by.most_common()),
        "mappings_carried_into_fields": sum(mappings_by.values()),
        "mappings_by_column": dict(mappings_by.most_common()),
        "view_entries_kept": dict(kept_by.most_common()),
        "out_of_step": out_of_step,
        "findings": [
            "Counts are of what was out of step before this ran; in check mode nothing is changed.",
            "A field that held a value keeps it. A view entry no field names -- a "
            "lane's flag -- is kept, and counted in view_entries_kept.",
            "No reading, review status or control changes.",
        ],
    }


def build_parser():
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("records", help="Record artifact whose fields and views are compared.")
    parser.add_argument(
        "--out", help="The record artifact with its fields and views in step (a new file)."
    )
    parser.add_argument(
        "--report", help="What was out of step, and what was carried or kept (a new file)."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Write no records; exit 1 when any document is out of step.",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress the printed summary.")
    apply_shared_help(parser)
    return parser


def main(argv=None):
    """Bring the records into step, or check whether they are."""
    args = build_parser().parse_args(argv)
    try:
        if args.check == bool(args.out):
            raise ValueError("give --out and --report to bring records into step, or --check")
        if args.out and not args.report:
            raise ValueError("--out needs --report")
        for path in (args.out, args.report):
            if path:
                empty_output_path(path)
        records = json.loads(Path(args.records).read_text(encoding="utf-8"))
        documents = records.get("documents") if isinstance(records, dict) else None
        if not isinstance(documents, list) or not documents:
            raise ValueError(f"not a record artifact with documents: {args.records}")
        report = reconcile(documents, write=not args.check)
        report["source_artifact"] = args.records
        if args.out:
            records.setdefault("summary", {})["records_in_step"] = {
                key: report[key]
                for key in (
                    "documents_out_of_step",
                    "cells_brought_into_step",
                    "mappings_carried_into_fields",
                )
            }
            Path(args.out).write_text(json.dumps(records), encoding="utf-8")
        if args.report:
            Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    except (OSError, ValueError) as exc:
        sys.exit(f"Records in step failed: {exc}")
    if not args.quiet:
        verb = "out of step" if args.check else "brought into step"
        print(f"documents {verb}: {report['documents_out_of_step']} of {report['documents']}")
        print(f"  view cells behind their fields: {report['cells_brought_into_step']}")
        print(f"  approved mappings only a view held: {report['mappings_carried_into_fields']}")
        print(f"  view entries no field names, kept: {sum(report['view_entries_kept'].values())}")
    return 1 if args.check and report["documents_out_of_step"] else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
