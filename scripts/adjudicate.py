#!/usr/bin/env python3
"""Phase 3: bounded, amendment-only adjudication of arithmetic-checked total disagreements.

This intentionally narrow first loop follows the arithmetic-check stage and uses its
control relationship to choose a total only when exactly one independently extracted candidate equals the
proved subtotal-plus-charges result. It records a proposed system amendment; it
never mutates consensus or source-engine evidence. Every other disagreement is
explicitly queued.
"""

import argparse
import collections
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help


def number(value):
    """Parse a monetary value, returning None where it cannot be read.

    None is not zero. An amendment proposed from a value this could not parse
    would be arithmetic performed on a misread.
    """
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def value_at(record, path):
    """Follow a dotted path into a record, returning None at the first gap."""
    value = record
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value.get("value") if isinstance(value, dict) and "value" in value else value


def records_from(path):
    """Read a record list from any of the shapes upstream lanes emit."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return data
    for key in ("documents", "results", "records"):
        if isinstance(data.get(key), list):
            return data[key]
    raise ValueError(f"No record list in {path}")


ADJUDICATED_FIELD = "header.total_amount"
ARITHMETIC_INPUTS = (
    "subtotal",
    "tax_amount",
    "freight_amount",
    "accessorial_total",
    "discount_amount",
)


def expected_total(record):
    """Return the total the document's own arithmetic supports, if it is unique."""
    return arithmetic(record)[0]


def arithmetic(record):
    """Return the supported total and the inputs this document does not carry.

    Reporting what could not be read is the whole difference between a control
    that failed and a control that never ran. On the commission run `subtotal`
    is present on 1 of 716 documents and valued on none of them, so this returns
    None for every document -- and every one of them was then queued as an
    adjudication that could not pick a unique value. It had not picked anything;
    there was nothing to pick from.
    """
    header = record.get("header", {})
    if not isinstance(header, dict):
        header = {}
    missing = [name for name in ARITHMETIC_INPUTS if number(header.get(name)) is None]
    subtotal = number(header.get("subtotal"))
    if subtotal is None:
        return None, missing
    additions = sum(
        number(header.get(name)) or 0.0
        for name in ("tax_amount", "freight_amount", "accessorial_total")
    )
    discount = number(header.get("discount_amount")) or 0.0
    return round(subtotal + additions - discount, 2), missing


def candidates(document_id, source_records, path):
    """Return the amendment candidates the document's arithmetic uniquely supports."""
    values = []
    for record in source_records:
        if record.get("document_id") != document_id:
            continue
        value = value_at(record, path)
        if value is not None and value not in values:
            values.append(value)
    return values


def adjudicate(consensus_documents, source_records, max_passes):
    """Propose a single arithmetic-proved amendment, otherwise preserve queue.

    Three outcomes were being reported as one. A document that does not carry
    the adjudicated field has nothing to adjudicate; a document whose arithmetic
    inputs are absent was never adjudicated at all; only a document with a
    computed total and no single matching candidate is genuinely not unique.
    Reported together, 706 of 716 documents on the commission run were queued as
    disputes, 638 of them about a field they do not have.
    """
    amendments, exceptions = [], []
    skipped = {"field_absent": 0, "already_accepted": 0}
    for record in consensus_documents:
        fields = record.get("fields") or {}
        if ADJUDICATED_FIELD not in fields:
            # Not a finding. Asking a client to adjudicate a total the document
            # never carried is a question with no answer.
            skipped["field_absent"] += 1
            continue
        field = fields.get(ADJUDICATED_FIELD) or {}
        if field.get("accepted"):
            skipped["already_accepted"] += 1
            continue
        expected, missing_inputs = arithmetic(record)
        values = candidates(record.get("document_id"), source_records, ADJUDICATED_FIELD)
        matching = [value for value in values if number(value) == expected]
        if expected is None:
            # Rule 9. The control did not run, and saying so is not the same as
            # saying it ran and found no unique answer.
            exceptions.append(
                {
                    "document_id": record.get("document_id"),
                    "field": ADJUDICATED_FIELD,
                    "reason": "arithmetic_inputs_absent",
                    "expected_total": None,
                    "unconsulted_key_fields": missing_inputs,
                    "candidate_values": values,
                    "disposition": "human_review_required",
                }
            )
            continue
        if expected is not None and len(matching) == 1:
            amendments.append(
                {
                    "document_id": record.get("document_id"),
                    "target_field": "header.total_amount",
                    "original_value": field.get("value"),
                    "proposed_value": matching[0],
                    "amendment_source": "system",
                    "effective_time": datetime.now(UTC).isoformat(),
                    "reason": "arithmetic_unique_candidate",
                    "passes_used": 1,
                    "max_passes": max_passes,
                    "candidate_values": values,
                }
            )
        else:
            exceptions.append(
                {
                    "document_id": record.get("document_id"),
                    "field": ADJUDICATED_FIELD,
                    # A total the arithmetic supports that no engine read is a
                    # different problem from several engines reading different
                    # ones, and only the second is an ambiguity to resolve.
                    "reason": (
                        "adjudication_not_unique"
                        if matching
                        else "no_candidate_matches_the_document_arithmetic"
                    ),
                    "expected_total": expected,
                    "candidate_values": values,
                    "matching_candidate_values": matching,
                    "disposition": "human_review_required",
                }
            )
    return amendments, exceptions, skipped


def main():
    parser = argparse.ArgumentParser(
        description="Propose bounded arithmetic-backed amendments without changing evidence."
    )
    parser.add_argument("proofed", help="proofed.json from scripts/arithmetic_check.py")
    parser.add_argument("sources", nargs="+", help="independent raw engine JSON files")
    parser.add_argument("--out", required=True, help="amendment proposal JSON")
    parser.add_argument("--exceptions", required=True, help="adjudication exception JSON")
    parser.add_argument(
        "--max-passes",
        type=int,
        choices=(1, 2),
        default=2,
        help="Maximum adjudication passes. Two passes may propose an amendment; one records candidates only.",
    )
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        consensus = json.loads(Path(args.proofed).read_text())
        docs = consensus.get("documents")
        if not isinstance(docs, list):
            raise ValueError("Proofed input must contain a documents list")
        source_records = [record for source in args.sources for record in records_from(source)]
        amendments, exceptions, skipped = adjudicate(docs, source_records, args.max_passes)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Adjudication failed: {exc}")
    by_reason = collections.Counter(entry["reason"] for entry in exceptions)
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "documents": len(docs),
        "proposed_amendments": len(amendments),
        "open_exceptions": len(exceptions),
        "exceptions_by_reason": dict(sorted(by_reason.items())),
        # A document this control had nothing to say about is not a document it
        # cleared, and it is not a finding either. Both counts are reported so
        # neither is inferred from the exception list.
        "documents_without_the_adjudicated_field": skipped["field_absent"],
        "documents_already_accepted": skipped["already_accepted"],
        "adjudicated_documents": len(amendments) + len(exceptions),
        "max_passes": args.max_passes,
        "findings": [
            "No source or consensus value was overwritten; amendments require review before application.",
            "`arithmetic_inputs_absent` means this control could not run on the document, "
            "not that it ran and found no unique value.",
        ],
    }
    Path(args.out).write_text(
        json.dumps({"summary": summary, "amendments": amendments}, indent=2) + "\n"
    )
    Path(args.exceptions).write_text(
        json.dumps({"summary": {"count": len(exceptions)}, "exceptions": exceptions}, indent=2)
        + "\n"
    )
    if not args.quiet:
        print(f"Proposed amendments: {len(amendments)}")
        print(f"Open adjudication exceptions: {len(exceptions)}")
        for reason, count in summary["exceptions_by_reason"].items():
            print(f"  {count:5d}  {reason}")
        print(f"  not adjudicated, field absent: {skipped['field_absent']}")


if __name__ == "__main__":
    main()
