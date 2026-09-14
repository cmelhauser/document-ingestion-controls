#!/usr/bin/env python3
"""Let a document's own arithmetic choose between two readings of a cell.

Consensus accepts a value two vendors agree on. Where they disagree it records
`no_consensus` and the cell stays open -- 9,007 of them on the commission run,
2,551 carrying money. Nothing downstream can settle those, because both readings
are equally attested: one engine said one thing, one said another, and page
presence cannot separate them. `independent_corroboration.py` proves a string is
printed somewhere on the page, which defeats a misread and not a misplacement.

A commission line states its own arithmetic. `commission_amount` is
`commissionable_amount` times `stated_commission_rate`, and where two of the
three are known the third is not a matter of opinion. This lane computes it and
asks one question of the candidate readings: does exactly one of them satisfy
the line? That is evidence from the document, not a model's preference, and it
is the only thing here that can defeat a **misplacement**.

It caught one on the first page it ran. Two engines offered `(662.89)` and
`4,098.49` for one line's commission, and `4,098.49` appeared again as a
candidate on a different line of the same page -- the second engine had read the
table one row off. Neither reading was wrong about the number; one was wrong
about the row. The arithmetic puts each back where it belongs.

Deliberately narrow:

* Only a cell consensus left open is considered. An accepted value is evidence
  and this does not overrule it.
* A sibling supplies a value only when it is accepted, or when it is open with
  exactly one candidate. Two competing siblings would make the expected value a
  choice, and choosing it to justify a choice is circular.
* Exactly one candidate must match. Two matches is an ambiguity, not a
  resolution, and zero matches means the readings disagree with the line -- both
  are reported rather than resolved.
* Resolutions are proposals. `--records-out` applies them, labelled
  `reconciled_by_document_arithmetic`, never `consensus_*`: an arithmetic proof
  is not two vendors agreeing and must never be counted as it.
"""

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from consensus import refresh_views
from run_io import empty_output_path
from runtime_config import load_project_env

ARTIFACT_TYPE = "arithmetic_reconciliation_v1"
ACCEPTED_BY = "reconciled_by_document_arithmetic"
LINE_FIELD = re.compile(r"^lines\[(\d+)\]\.(.+)$")
AMOUNT = "commission_amount"
BASE = "commissionable_amount"
RATE = "stated_commission_rate"
# The tolerance has to scale, because the error being accommodated is rounding
# in the source document and rounding is proportional. A flat two cents is
# 0.0025% of a $799 line: it rejected `799.87` against an expected `799.671` and
# called a rounded total a contradiction. 102 of 144 rejections were that.
#
# Swept against the corpus, resolutions against ambiguities: 0.02% resolved 509
# with 1 ambiguous, 0.05% resolved 527 with 2, and 1% resolved 538 with 7. The
# curve flattens after 0.05% while the window in which a wrong candidate can be
# the only match keeps widening, so the knee is the setting and not the ceiling.
DEFAULT_RELATIVE_TOLERANCE = 0.0005
# A floor for small values, where a proportional tolerance collapses to nothing.
DEFAULT_TOLERANCE = 0.02
# A rate is quoted in whole percent and rounds harder than a currency amount.
DEFAULT_RATE_TOLERANCE = 0.05


def number(value):
    """Parse a money or rate reading, returning None where it cannot be read.

    `(662.89)` is an accounting negative and `-662.89` is the same number. A
    reading this cannot parse is not zero; it is a reading this lane must not
    reason about.
    """
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return None


def offered(cell):
    """Return the value a sibling cell can lend, or None if it cannot lend one.

    An accepted value is evidence. An open cell with exactly one candidate is a
    single reading -- weaker, and named as such on every resolution it supports.
    An open cell with two candidates lends nothing: using one of them to pick
    between the other cell's candidates decides the disagreement by assuming it.
    """
    if not isinstance(cell, dict):
        return None, None
    if cell.get("accepted"):
        return number(cell.get("value")), "accepted"
    candidates = [c for c in (cell.get("candidate_values") or []) if number(c) is not None]
    if len(candidates) == 1:
        return number(candidates[0]), "single_reading"
    return None, None


def match_width(expected, target, tolerance, rate_tolerance, relative):
    """How far a candidate may sit from the line and still satisfy it."""
    if target == RATE:
        return rate_tolerance
    return max(tolerance, relative * abs(expected))


def expected_value(target, amount, base, rate):
    """Solve the line's own relation for whichever cell is open."""
    if target == AMOUNT and base is not None and rate is not None:
        return base * rate / 100.0
    if target == BASE and amount is not None and rate:
        return amount * 100.0 / rate
    if target == RATE and amount is not None and base:
        return amount * 100.0 / base
    return None


def line_cells(document):
    """Group a document's flat `lines[n].field` keys back into lines."""
    lines = {}
    for name, cell in (document.get("fields") or {}).items():
        match = LINE_FIELD.match(name)
        if match and isinstance(cell, dict):
            lines.setdefault(match.group(1), {})[match.group(2)] = (name, cell)
    return lines


def reconcile(
    documents,
    tolerance=DEFAULT_TOLERANCE,
    rate_tolerance=DEFAULT_RATE_TOLERANCE,
    relative=DEFAULT_RELATIVE_TOLERANCE,
):
    """Resolve open cells the line's own arithmetic decides, and report the rest."""
    resolutions, unresolved = [], []
    counts = {"open": 0, "not_checkable": 0, "checkable": 0}
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("every record must be an object")
        for index, cells in sorted(line_cells(document).items()):
            for target in (AMOUNT, BASE, RATE):
                entry = cells.get(target)
                if not entry or entry[1].get("accepted"):
                    continue
                counts["open"] += 1
                siblings = {
                    AMOUNT: offered((cells.get(AMOUNT) or (None, None))[1]),
                    BASE: offered((cells.get(BASE) or (None, None))[1]),
                    RATE: offered((cells.get(RATE) or (None, None))[1]),
                }
                expected = expected_value(
                    target, siblings[AMOUNT][0], siblings[BASE][0], siblings[RATE][0]
                )
                candidates = [
                    c for c in (entry[1].get("candidate_values") or []) if number(c) is not None
                ]
                if expected is None or not candidates:
                    # Rule 9: a cell this could not check is not a cell it
                    # checked and cleared. Counted, and never counted as clean.
                    counts["not_checkable"] += 1
                    continue
                counts["checkable"] += 1
                width = match_width(expected, target, tolerance, rate_tolerance, relative)
                matching = [c for c in candidates if abs(number(c) - expected) <= width]
                record = {
                    "document_id": document.get("document_id"),
                    "line_index": int(index),
                    "field": entry[0],
                    "expected_value": round(expected, 4),
                    "match_width": round(width, 4),
                    "candidate_values": candidates,
                    "matching_candidate_values": matching,
                    "evidence": {
                        name: {"value": value, "from": source}
                        for name, (value, source) in siblings.items()
                        if name != target and value is not None
                    },
                }
                if len(matching) == 1:
                    resolutions.append({**record, "resolved_value": matching[0]})
                else:
                    record["reason"] = (
                        "several_candidates_satisfy_the_line"
                        if matching
                        else "no_candidate_satisfies_the_line"
                    )
                    unresolved.append(record)
    return resolutions, unresolved, counts


def apply_resolutions(documents, resolutions):
    """Append each resolution to its cell, retaining what consensus recorded."""
    index = {(r["document_id"], r["field"]): r for r in resolutions}
    applied = 0
    out = []
    for document in documents:
        fields = dict(document.get("fields") or {})
        touched = False
        for name, cell in list(fields.items()):
            resolution = index.get((document.get("document_id"), name))
            if not resolution or not isinstance(cell, dict):
                continue
            applied += 1
            touched = True
            fields[name] = {
                **cell,
                "value": resolution["resolved_value"],
                "accepted": True,
                # Never `consensus_*`. An arithmetic proof is not two vendors
                # agreeing, and a reader that cannot tell them apart will treat
                # a solved cell as a corroborated reading.
                "acceptance": {
                    "accepted_by": ACCEPTED_BY,
                    "expected_value": resolution["expected_value"],
                    "evidence": resolution["evidence"],
                },
            }
        updated = {**document, "fields": fields}
        if touched:
            # The line view the arithmetic and attribution controls read carries
            # the solved value too, not only the field the export reads.
            refresh_views(updated)
        out.append(updated)
    return out, applied


def main(argv=None):
    """Write the reconciliation proposal, and optionally the amended records."""
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", help="Retained record artifact carrying line cells.")
    parser.add_argument("--out", required=True, help="New reconciliation artifact path.")
    parser.add_argument("--exceptions", required=True, help="New unresolved-cell artifact path.")
    parser.add_argument(
        "--records-out",
        help=(
            "Apply the resolutions to a new record artifact. Each amended cell is "
            "labelled reconciled_by_document_arithmetic and never consensus_*."
        ),
    )
    parser.add_argument(
        "--relative-tolerance",
        type=float,
        default=DEFAULT_RELATIVE_TOLERANCE,
        help=(
            "Fraction of the expected value a candidate may differ by. Rounding in a "
            "source document is proportional, so the tolerance is too; a flat amount "
            "reads a rounded total on a large line as a contradiction."
        ),
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help=(
            "Absolute floor for the match width, in currency units. It governs small "
            "values, where a proportional tolerance collapses to nothing."
        ),
    )
    parser.add_argument(
        "--rate-tolerance",
        type=float,
        default=DEFAULT_RATE_TOLERANCE,
        help=(
            "Match width for a commission rate, in percentage points. A rate is quoted "
            "in whole percent and rounds harder than a currency amount, so it keeps an "
            "absolute width rather than a proportional one."
        ),
    )
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args(argv)
    try:
        if args.tolerance <= 0 or args.rate_tolerance <= 0 or args.relative_tolerance < 0:
            raise ValueError("tolerances must be greater than zero")
        data = json.loads(Path(args.records).read_text())
        documents = data.get("documents") if isinstance(data, dict) else None
        if not isinstance(documents, list):
            raise ValueError("record artifact carries no document list")
        resolutions, unresolved, counts = reconcile(
            documents, args.tolerance, args.rate_tolerance, args.relative_tolerance
        )
        summary = {
            "generated_at": datetime.now(UTC).isoformat(),
            "artifact_type": ARTIFACT_TYPE,
            "documents": len(documents),
            "open_line_cells": counts["open"],
            "checkable": counts["checkable"],
            "not_checkable": counts["not_checkable"],
            "resolved": len(resolutions),
            "unresolved": len(unresolved),
            "tolerance": args.tolerance,
            "rate_tolerance": args.rate_tolerance,
            "relative_tolerance": args.relative_tolerance,
            "findings": [
                "A resolution is the document's own arithmetic, not vendor agreement, "
                "and is labelled so it can never be counted as one.",
                "A cell with no computable expected value is reported as not checkable "
                "rather than as checked and clear.",
            ],
        }
        empty_output_path(Path(args.out)).write_text(
            json.dumps({"summary": summary, "resolutions": resolutions}, indent=2) + "\n"
        )
        empty_output_path(Path(args.exceptions)).write_text(
            json.dumps({"summary": {"count": len(unresolved)}, "exceptions": unresolved}, indent=2)
            + "\n"
        )
        applied = None
        if args.records_out:
            amended, applied = apply_resolutions(documents, resolutions)
            empty_output_path(Path(args.records_out)).write_text(
                json.dumps({**data, "documents": amended}, indent=2) + "\n"
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Arithmetic reconciliation failed: {exc}")
    if not args.quiet:
        print(f"Open line cells: {counts['open']}")
        print(f"  checkable against the line : {counts['checkable']}")
        print(f"  not checkable              : {counts['not_checkable']}")
        print(f"  resolved by the arithmetic : {len(resolutions)}")
        print(f"  candidates disagree with it: {len(unresolved)}")
        if applied is not None:
            print(f"  applied to records         : {applied}")
        print("Not vendor agreement; labelled reconciled_by_document_arithmetic.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
