#!/usr/bin/env python3
"""Recover the printed SPECIFIER column from retained independent-extractor layout.

The schema named no specifier field, so on every page that prints one the design
firm was either written into `brand_name` -- `NORCROSS TAMPA` and `BSQ - FLORIDA`
are architects, not manufacturers -- or dropped entirely. Adding the field fixes
the next run. This recovers the column for the run already paid for, without a
provider call, because the value never left the workspace: the independent
non-LLM extractor retains the whole page, not the fields the schema asked for.

The join is a printed key both sides already carry. These forms group lines
under a header reading COMPANY / ACK# / P.O.# / P.O. DATE / SPECIFIER, and the
extracted line carries that group's purchase order number, so the specifier
printed beside a P.O. number belongs to every line quoting it.

Nothing is overwritten and nothing is invented. A line that already states a
specifier keeps it, a page whose header cannot be read yields an exception
rather than a guess, and a recovered value is independent-extractor evidence --
never vendor agreement.
"""

import argparse
import json
import re
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from run_io import retained_responses

RECOVERED_SOURCE = "printed"
RECOVERED_EVIDENCE = "recovered_from_independent_extractor_layout"
EXTRACTOR_ENGINE = "google/document_ai"
PRINTED_DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")
# Column headings and subtotal labels sit in the same text stream as the values.
HEADING = re.compile(
    r"^(p\.?o\.?\s*total|total|invoice|qty|style|job\s*number|number|date|billed"
    r"|commissions|furn|table|lea|fab|masq|fixed|company|ack#|specifier)\b",
    re.I,
)


def firm_like(text):
    """Say whether a reading can be a company name rather than a number.

    The line after a printed date is sometimes an invoice number and date
    (`64397 01/16/2023`) or an amount (`91.09`). A specifier is a name, so it
    is mostly letters.
    """
    letters = sum(character.isalpha() for character in text)
    dense = sum(not character.isspace() for character in text)
    return letters >= 3 and bool(dense) and letters / dense >= 0.5


def extractor_lines(path):
    """Read one retained independent-extractor response as trimmed text lines."""
    try:
        payload = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return []
    document = (payload.get("response") or {}).get("document") or {}
    return [line.strip() for line in (document.get("text") or "").splitlines() if line.strip()]


def specifiers_by_order(lines):
    """Map each printed purchase-order number to the specifier printed with it.

    The group header prints the P.O. number, then its date, then the specifier,
    so the specifier is the line after a date and the order number is the line
    before it.
    """
    found = {}
    for index, line in enumerate(lines):
        if not index or not PRINTED_DATE.match(line) or index + 1 >= len(lines):
            continue
        candidate = lines[index + 1]
        if PRINTED_DATE.match(candidate) or HEADING.match(candidate):
            continue
        if not firm_like(candidate):
            continue
        order = lines[index - 1]
        if PRINTED_DATE.match(order) or HEADING.match(order):
            continue
        found.setdefault(order, candidate)
    return found


def field_text(value):
    """Read a field that may be a plain value or a provenance-carrying object."""
    if isinstance(value, dict):
        value = value.get("value")
    return str(value).strip() if value is not None else ""


def recovered_field(name, evidence):
    """Build the field object a recovered reading is carried in.

    It is deliberately **not** accepted. One extractor read this and no model
    read it at all, which is weaker than the corroboration status that pairs a
    model with an extractor, so it travels as `single_reading` and earns its
    place in a CRM only once someone confirms it.
    """
    return {
        "value": name,
        "candidate_values": [name],
        "source": RECOVERED_SOURCE,
        "consensus_flag": "single_engine",
        "agreeing_engines": [EXTRACTOR_ENGINE],
        "engine_count": 1,
        "rule": RECOVERED_EVIDENCE,
        "accepted": False,
        "is_handwritten": False,
        "blocking": False,
        "queue_for_review": False,
        "evidence_text": evidence,
        "recovered_by": RECOVERED_EVIDENCE,
    }


def apply_to_document(record, by_order):
    """Attach recovered specifiers to one document, and report what happened."""
    attached = conflicts = misfiled = 0
    for index, line in enumerate(record.get("lines") or []):
        if not isinstance(line, dict):
            continue
        order = field_text(line.get("purchase_order_number"))
        name = by_order.get(order)
        if not name:
            continue
        if field_text(line.get("specifier_name")):
            conflicts += 1
            continue
        field = recovered_field(name, f"specifier printed for P.O. {order}")
        line["specifier_name"] = field
        # The export reads the flat field map, not the line objects, so a
        # reading attached only to the line never reaches a CSV.
        fields = record.get("fields")
        if isinstance(fields, dict):
            fields[f"lines[{index}].specifier_name"] = dict(field)
        attached += 1
        # The value the schema had nowhere else to put often landed here.
        if field_text(line.get("brand_name")).casefold() == name.casefold():
            line["brand_name_holds_the_specifier"] = True
            misfiled += 1
    return attached, conflicts, misfiled


def recover(records, responses):
    """Carry every record forward, attaching the specifiers the page printed."""
    out, exceptions = [], []
    summary = {
        "documents": 0,
        "with_a_specifier": 0,
        "lines_attached": 0,
        "lines_already_stated": 0,
        "brand_held_the_specifier": 0,
    }
    for record in records:
        copy = deepcopy(record)
        document_id = copy.get("document_id", "unknown")
        summary["documents"] += 1
        path = responses.get(document_id)
        if path is None:
            exceptions.append(
                {
                    "document_id": document_id,
                    "reason": "no_retained_extractor_response",
                    "disposition": "client_review_required",
                }
            )
            out.append(copy)
            continue
        by_order = specifiers_by_order(extractor_lines(path))
        if not by_order:
            out.append(copy)
            continue
        summary["with_a_specifier"] += 1
        attached, conflicts, misfiled = apply_to_document(copy, by_order)
        summary["lines_attached"] += attached
        summary["lines_already_stated"] += conflicts
        summary["brand_held_the_specifier"] += misfiled
        if not attached and by_order:
            exceptions.append(
                {
                    "document_id": document_id,
                    "reason": "specifier_printed_but_no_line_quotes_its_order_number",
                    "value": sorted(by_order.values())[:5],
                    "disposition": "client_review_required",
                }
            )
        copy["specifier_recovery"] = {
            "orders_with_a_specifier": len(by_order),
            "lines_attached": attached,
            "evidence": RECOVERED_EVIDENCE,
        }
        out.append(copy)
    return out, exceptions, summary


def responses_by_document(directory):
    """Index the retained extractor responses by the document they cover.

    The shared index, so a page whose first response came back empty is read
    from its retry: indexing the first file per page left 222 pages of the
    commission run unread here.
    """
    return retained_responses(directory)


def records_from(path):
    """Read a record artifact, a list, or a single record."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("documents"), list):
            return data["documents"]
        if "document_id" in data:
            return [data]
    raise ValueError("Input must be a record, a list of records, or contain documents")


def main():
    parser = argparse.ArgumentParser(
        description="Recover the printed SPECIFIER column from retained extractor layout."
    )
    parser.add_argument("input", help="records JSON to carry forward")
    parser.add_argument(
        "--extractor-raw",
        required=True,
        help="directory of retained independent-extractor responses",
    )
    parser.add_argument("--out", required=True, help="records with recovered specifiers")
    parser.add_argument("--exceptions", required=True, help="pages the recovery could not settle")
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        records = records_from(args.input)
        responses = responses_by_document(args.extractor_raw)
        if not responses:
            raise ValueError(f"No retained extractor responses under {args.extractor_raw}")
        out, exceptions, summary = recover(records, responses)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Specifier recovery failed: {exc}")
    summary["generated_at"] = datetime.now(UTC).isoformat()
    summary["findings"] = [
        "A recovered specifier is independent-extractor evidence, never vendor agreement.",
        "No reading was overwritten; a line already stating a specifier kept it.",
    ]
    Path(args.out).write_text(json.dumps({"summary": summary, "documents": out}, indent=2) + "\n")
    Path(args.exceptions).write_text(
        json.dumps({"summary": {"count": len(exceptions)}, "exceptions": exceptions}, indent=2)
        + "\n"
    )
    if not args.quiet:
        print(
            f"Documents: {summary['documents']}   printing a specifier: {summary['with_a_specifier']}"
        )
        print(f"  lines given a specifier    : {summary['lines_attached']}")
        print(f"  lines that already had one : {summary['lines_already_stated']}")
        print(f"  brand_name held it         : {summary['brand_held_the_specifier']}")
        print(f"  exceptions                 : {len(exceptions)}")


if __name__ == "__main__":
    main()
