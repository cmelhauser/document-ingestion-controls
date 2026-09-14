#!/usr/bin/env python3
"""Qualify a candidate engine against a reference engine, by value not by count.

A second reader exists to corroborate. Whether it can is a question about the
values it returns, and row counts do not answer it.

One run made that concrete and expensive. A candidate matched the reference
engine's row count almost exactly -- 67 to 67, 54 to 54, 172 rows against 171
over four sample pages -- and on that evidence a 716-page corpus was read. The
values told a different story: the candidate had failed to read 42% of the
reference engine's values anywhere on the page, and placed the right value in
the right field 37.5% of the time. It misread digits inside financial amounts,
which is worse than reading nothing: a wrong amount does not abstain, it
disagrees, and every disagreement it manufactures costs a person an
adjudication.

So this command reports two rates that a row count hides:

* **value recall** -- how much of the reference engine's content the candidate
  read *anywhere* on the page. This is the reading question, asked without
  regard to where the candidate filed what it read.
* **field-exact agreement** -- the right value, in the right field, on the same
  row. This is the structure question, and it can only be lower than recall.

Read them together. High recall with low field-exact means the engine can read
the page but not the table, and its output will collide with the reference
engine on field identity rather than on content. Low recall means it cannot
read the page, and no amount of prompting downstream will recover what it never
saw.

Run this against a handful of matched pages *before* committing a corpus, and
compare only pages both engines actually read: a rate over a different
population is not a comparison. The output is a measurement, not a decision --
it clears no control and authorizes no lane.
"""

import argparse
import json
import sys
from pathlib import Path

from cli_help import apply_shared_help
from extraction_schema import literal_null
from run_io import empty_output_path
from runtime_config import load_project_env


def cell_value(cell):
    """Read one normalized cell, treating an empty reading as empty."""
    if isinstance(cell, dict):
        cell = cell.get("value")
    if cell is None or literal_null(cell):
        return None
    text = str(cell).strip()
    return text or None


def page_values(record):
    """Return every line cell of one page as (row index, field, value)."""
    cells = []
    for index, line in enumerate(record.get("lines") or []):
        for field, cell in line.items():
            if field == "line_number":
                continue
            value = cell_value(cell)
            if value is not None:
                cells.append((index, field, value))
    return cells


def load_records(path):
    """Read a provider handoff and index its records by page."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise ValueError(f"not a provider handoff with a records list: {path}")
    return data, {
        record.get("page_id"): record for record in data["records"] if record.get("page_id")
    }


def compare_page(reference, candidate):
    """Compare one page: what was read at all, and what was filed correctly."""
    ref_cells = page_values(reference)
    cand_cells = page_values(candidate)
    # Recall ignores placement: did this value reach the output at all?
    cand_values = {value for _, _, value in cand_cells}
    recalled = sum(1 for _, _, value in ref_cells if value in cand_values)
    # Field-exact requires the same row and field, so it also tests structure.
    ref_by_slot = {(index, field): value for index, field, value in ref_cells}
    cand_by_slot = {(index, field): value for index, field, value in cand_cells}
    slots = set(ref_by_slot) | set(cand_by_slot)
    exact = sum(1 for slot in slots if ref_by_slot.get(slot) == cand_by_slot.get(slot))
    return {
        "page_id": reference.get("page_id"),
        "reference_rows": len(reference.get("lines") or []),
        "candidate_rows": len(candidate.get("lines") or []),
        "reference_values": len(ref_cells),
        "recalled_values": recalled,
        "compared_slots": len(slots),
        "field_exact": exact,
    }


def compare(reference_handoff, candidate_handoff):
    """Compare two handoffs over the pages both engines actually read."""
    ref_data, ref_pages = load_records(reference_handoff)
    cand_data, cand_pages = load_records(candidate_handoff)
    shared = sorted(set(ref_pages) & set(cand_pages))
    if not shared:
        raise ValueError("the two handoffs share no page; there is nothing to compare")
    pages = [compare_page(ref_pages[page], cand_pages[page]) for page in shared]
    totals = {
        key: sum(page[key] for page in pages)
        for key in (
            "reference_rows",
            "candidate_rows",
            "reference_values",
            "recalled_values",
            "compared_slots",
            "field_exact",
        )
    }
    return {
        "artifact_type": "engine_agreement_v1",
        "reference_engine": ref_data.get("engine"),
        "candidate_engine": cand_data.get("engine"),
        "pages_compared": len(pages),
        "reference_pages": len(ref_pages),
        "candidate_pages": len(cand_pages),
        "summary": {
            **totals,
            "row_count_ratio_pct": _rate(totals["candidate_rows"], totals["reference_rows"]),
            # The rate a row count cannot show.
            "value_recall_pct": _rate(totals["recalled_values"], totals["reference_values"]),
            "field_exact_pct": _rate(totals["field_exact"], totals["compared_slots"]),
        },
        "pages": pages,
        "interpretation": (
            "Row-count ratio is not corroboration. Read value_recall_pct for whether the "
            "candidate read the page, and field_exact_pct for whether it filed what it read. "
            "This measurement clears no control and authorizes no lane."
        ),
    }


def _rate(part, whole):
    """Return a percentage, or None where there is nothing to divide."""
    return round(part / whole * 100, 1) if whole else None


def main(argv=None):
    """Report what a candidate engine actually read, next to a reference engine."""
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", help="Retained handoff of the engine already trusted.")
    parser.add_argument("candidate", help="Retained handoff of the engine being qualified.")
    parser.add_argument("--out", help="New agreement artifact path.")
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args(argv)
    try:
        result = compare(args.reference, args.candidate)
        if args.out:
            empty_output_path(Path(args.out)).write_text(json.dumps(result, indent=2) + "\n")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Engine agreement failed: {exc}")
    if not args.quiet:
        summary = result["summary"]
        print(f"reference : {result['reference_engine']}")
        print(f"candidate : {result['candidate_engine']}")
        print(f"pages compared: {result['pages_compared']}")
        print(
            f"  rows            : {summary['candidate_rows']} vs {summary['reference_rows']} "
            f"({summary['row_count_ratio_pct']}%) <- not corroboration"
        )
        print(
            f"  value recall    : {summary['recalled_values']}/{summary['reference_values']} "
            f"({summary['value_recall_pct']}%)"
        )
        print(
            f"  field-exact     : {summary['field_exact']}/{summary['compared_slots']} "
            f"({summary['field_exact_pct']}%)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
