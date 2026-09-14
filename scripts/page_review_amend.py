#!/usr/bin/env python3
"""Amend what the page review proved, under an operator authorization.

`page_review.py --proposals` grades every correction a reviewer found by the
independent evidence beside it. A proposal is not a change: the reviewer is one
model reading a page image, and the repository accepts nothing on that alone.
This is the separate, authorized step that turns the proposals an independent
source backs into amendments.

It runs only under a named authorization, and only for corrections whose
evidence stands apart from the reviewer: the independent extractor's text of
the page prints the figure, a retained engine read the cell that way, or both
-- or another vendor's reading of the page holds it (`second_vendor`, graded by
`page_review.py --second-read`). A correction the reviewer alone supports
cannot be selected at all -- it needs that second read, not an amendment.

A review reads one export, and a correction names the value it saw there. A
cell that holds neither that value nor the page's any longer has been changed
since -- by a re-read, a re-file or another amendment -- and on a re-read page
the line index may now name a different row. Such a correction is refused
rather than written over whatever the cell holds now.

An amendment is append-only. The field takes the page's value and is accepted
by `amended_from_page_review`; the value it replaced, the lane that had
accepted it, and every candidate reading are kept beside it in
`superseded_consensus`. Unlike corroboration, which never outranks consensus,
this does overwrite an accepted value: a misread the engines agreed on, or a
figure taken from the wrong column, is exactly what reading the page catches,
and the evidence here is the page itself plus a source independent of the
reviewer.

A correction is refused when the line's own arithmetic -- base x rate is the
commission -- held before it and would not after. The independent evidence
proves a figure is printed on the page, not on which line. On the commission
run 14 lines that reconciled were amended into lines that did not, a
neighbour's figure or a sign the arithmetic contradicts, and the page-level
score could not see it because the same figures were still on the page.

Nothing here changes a document's review status or clears a control.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from consensus import refresh_views
from field_extract_export import commission_reconciles, loadable_amount
from page_review import PROPOSALS_ARTIFACT_TYPE, money_value
from run_io import empty_output_path

ARTIFACT_TYPE = "page_review_amendments_v1"
ACCEPTED_BY = "amended_from_page_review"
# The grades whose evidence stands apart from the reviewer. `reviewer_only` is
# deliberately absent, so no flag can select it; `second_vendor` is a reviewer
# correction another vendor's reading of the page bears out.
INDEPENDENT_GRADES = ("extractor_text_and_engine", "extractor_text", "engine", "second_vendor")
# The three terms of a line's own arithmetic, judged as the export judges them.
ARITHMETIC_COLUMNS = ("commissionable_amount", "stated_commission_rate", "commission_amount")


def load_json(path, what):
    """A JSON object from disk, refusing anything that is not one."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"not a {what}: {path}")
    return data


def field_path(line_index, column):
    """The record field a correction names."""
    return f"header.{column}" if line_index == "header" else f"lines[{line_index}].{column}"


def amend_field(field, correction, authorization):
    """The field the page review proved, keeping what it replaced."""
    prior = field or {}
    acceptance = prior.get("acceptance")
    return {
        **prior,
        "value": correction["page_value"],
        "source": prior.get("source", "printed"),
        "accepted": True,
        "blocking": False,
        "queue_for_review": False,
        "acceptance": {
            "accepted_by": ACCEPTED_BY,
            "evidence": correction["evidence"],
            "retained_readings": correction.get("retained_readings") or [],
            "authorization": authorization,
            # A reviewer and an independent source agreeing on the page is not
            # two extraction vendors agreeing, and must never read as one.
            "vendor_agreement": False,
        },
        "superseded_consensus": {
            "value": prior.get("value"),
            "consensus_flag": prior.get("consensus_flag"),
            "accepted_by": acceptance.get("accepted_by") if isinstance(acceptance, dict) else None,
            "candidate_values": prior.get("candidate_values"),
        },
    }


def line_arithmetic(fields, line_index, overrides=None):
    """Whether a line's base x rate is its commission, judged as the export will."""
    values = {
        column: (overrides or {}).get(
            column, (fields.get(field_path(line_index, column)) or {}).get("value")
        )
        for column in ARITHMETIC_COLUMNS
    }
    return commission_reconciles(
        {
            "commissionable_amount__amount": loadable_amount(values["commissionable_amount"])[0],
            "commission_amount__amount": loadable_amount(values["commission_amount"])[0],
            "stated_commission_rate": values["stated_commission_rate"],
        }
    )


def changed_since_the_review(correction, document):
    """Whether the cell now holds neither the value the reviewer saw nor the page's.

    A correction naming no export value cannot be checked, and is left to the
    other guards.
    """
    if "export_value" not in correction:
        return False
    path = field_path(correction["line_index"], correction["column"])
    now = money_value(((document.get("fields") or {}).get(path) or {}).get("value"))
    return now not in (
        money_value(correction["export_value"]),
        money_value(correction["page_value"]),
    )


def plan(corrections, by_page, grades):
    """Say why each correction that may not be applied may not.

    Two guards feed each other, so they run until neither refuses anything
    more. A figure moved between lines lands only with both halves of the move,
    or it is counted twice. And a line whose own arithmetic reconciles is not
    amended into one that does not: the independent evidence proves a figure is
    printed on the page, not that it is printed on this line. A review that
    aligned its rows one off -- after a row the page printed once and the
    engines read twice -- offers every later line its neighbour's figure, each
    of them really on the page.
    """
    reasons = {}
    for index, correction in enumerate(corrections):
        if correction.get("evidence") not in grades:
            reasons[index] = "evidence_not_selected"
        elif correction.get("page") not in by_page:
            reasons[index] = "no_such_document"
        elif changed_since_the_review(correction, by_page[correction["page"]]):
            reasons[index] = "the_field_changed_since_the_review"
    while True:
        live = [index for index in range(len(corrections)) if index not in reasons]
        lines, overrides = {}, {}
        for index in live:
            correction = corrections[index]
            line = str(correction.get("line_index"))
            lines.setdefault((correction["page"], correction.get("column")), set()).add(line)
            if line != "header":
                overrides.setdefault((correction["page"], line), {})[correction["column"]] = (
                    correction["page_value"]
                )
        broken = {
            key
            for key, values in overrides.items()
            if line_arithmetic(by_page[key[0]].get("fields") or {}, key[1]) == "reconciles"
            and line_arithmetic(by_page[key[0]].get("fields") or {}, key[1], values) != "reconciles"
        }
        refused = {}
        for index in live:
            correction = corrections[index]
            others = set(correction.get("moves_from_lines") or [])
            if others - lines[(correction["page"], correction.get("column"))]:
                refused[index] = "moves_a_figure_whose_other_half_is_not_proved"
            elif (correction["page"], str(correction.get("line_index"))) in broken:
                refused[index] = "breaks_the_line_arithmetic"
        if not refused:
            return reasons
        reasons.update(refused)


def amend(records, proposals, authorization, grades=INDEPENDENT_GRADES):
    """Apply every correction the plan allows; return what was applied and what was not."""
    documents = records.get("documents") or []
    by_page = {str(doc.get("document_id", "")).rsplit("__", 1)[-1]: doc for doc in documents}
    corrections = proposals.get("corrections") or []
    reasons = plan(corrections, by_page, grades)
    applied, skipped, touched = [], Counter(), set()
    for index, correction in enumerate(corrections):
        if index in reasons:
            skipped[reasons[index]] += 1
            continue
        document = by_page[correction["page"]]
        fields = document.setdefault("fields", {})
        path = field_path(correction["line_index"], correction["column"])
        field = fields.get(path)
        if field is not None and money_value(field.get("value")) == money_value(
            correction["page_value"]
        ):
            skipped["already_reads_the_page"] += 1
            continue
        fields[path] = amend_field(field, correction, authorization)
        touched.add(document["document_id"])
        applied.append(
            {
                "document_id": document["document_id"],
                "field": path,
                "from": None if field is None else field.get("value"),
                "to": correction["page_value"],
                "evidence": correction["evidence"],
            }
        )
    for document in documents:
        if document.get("document_id") not in touched:
            continue
        refresh_views(document)
        document["accepted_field_count"] = sum(
            1 for field in document["fields"].values() if field.get("accepted")
        )
    return applied, skipped


def build_report(applied, skipped, authorization, grades):
    """What was amended, on which evidence, and what was left alone and why."""
    return {
        "artifact_type": ARTIFACT_TYPE,
        "generated_at": datetime.now(UTC).isoformat(),
        "authorization": authorization,
        "evidence_selected": list(grades),
        "amendments_applied": len(applied),
        "by_evidence": dict(Counter(a["evidence"] for a in applied).most_common()),
        "skipped": dict(skipped.most_common()),
        "applied": applied,
        "findings": [
            "Every amended field keeps the value it replaced, the lane that had "
            "accepted it and every candidate reading, in `superseded_consensus`.",
            "A correction the reviewer alone supports is never amended; it needs a "
            "second read by a different vendor.",
            "No document's review status changes and no control is cleared.",
        ],
    }


def build_parser():
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("records", help="Record artifact to amend.")
    parser.add_argument("proposals", help="Proposals artifact from page_review.py --proposals.")
    parser.add_argument(
        "--authorization",
        required=True,
        help="Name of the operator authorization the amendments are applied under.",
    )
    parser.add_argument(
        "--evidence",
        action="append",
        choices=INDEPENDENT_GRADES,
        default=None,
        help="Evidence grade to apply; repeatable. Defaults to every grade independent "
        "of the reviewer.",
    )
    parser.add_argument("--out", required=True, help="Amended record artifact (a new file).")
    parser.add_argument("--report", required=True, help="What was amended, and what was not.")
    parser.add_argument("--quiet", action="store_true", help="Suppress the printed summary.")
    apply_shared_help(parser)
    return parser


def main(argv=None):
    """Amend the records the page review proved, and report what changed."""
    args = build_parser().parse_args(argv)
    try:
        for path in (args.out, args.report):
            empty_output_path(path)
        records = load_json(args.records, "record artifact")
        proposals = load_json(args.proposals, "proposals artifact")
        if proposals.get("artifact_type") != PROPOSALS_ARTIFACT_TYPE:
            raise ValueError(f"not a {PROPOSALS_ARTIFACT_TYPE} artifact: {args.proposals}")
        grades = tuple(args.evidence or INDEPENDENT_GRADES)
        applied, skipped = amend(records, proposals, args.authorization, grades)
        report = build_report(applied, skipped, args.authorization, grades)
        records.setdefault("summary", {})["page_review_amendments"] = {
            "authorization": args.authorization,
            "amendments_applied": len(applied),
        }
        Path(args.out).write_text(json.dumps(records), encoding="utf-8")
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    except (OSError, ValueError) as exc:
        sys.exit(f"Page review amendment failed: {exc}")
    if not args.quiet:
        print(f"amendments applied: {report['amendments_applied']}")
        for grade, count in report["by_evidence"].items():
            print(f"  {grade}: {count}")
        for reason, count in report["skipped"].items():
            print(f"  left alone, {reason}: {count}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
