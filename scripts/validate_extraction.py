#!/usr/bin/env python3
"""Phase 3: deterministic field validation that preserves records and queues faults.

Validation is intentionally independent of OCR confidence. It catches impossible
dates, malformed currency/identifiers, missing provenance, and implausible
numeric values before a record can be represented as ready for downstream use.
"""

import argparse
import json
import re
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from extraction_schema import HEADER_FIELDS, LINE_FIELDS

DEFAULT_CURRENCIES = {"AUD", "CAD", "EUR", "GBP", "JPY", "MXN", "USD"}
# Derived from the schema rather than restated, because a hand-kept list drifts:
# this one had fallen six fields behind and left `transaction_date` -- the field
# most likely to be handed an identifier -- unvalidated. `memo_date` is a
# pre-schema alias retained for amended records that still carry it.
DATE_FIELDS = frozenset(
    {"memo_date"}
    | {
        name
        for name in (*HEADER_FIELDS, *LINE_FIELDS)
        if name.endswith("_date") or name in {"period_start", "period_end"}
    }
)
# This corpus is an Excel print-to-PDF. A column narrower than its contents
# renders as `####`, collapses into scientific notation, or is cut off with an
# ellipsis, and the date is then absent from the page rather than misread from
# it -- a source defect no re-extraction can recover.
NOT_RENDERED = re.compile(r"^#+$|^[+-]?\d+(?:\.\d+)?[Ee][+-]?\d+$|(?:\.\.\.|\u2026)$")
MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
    "|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec"
)
# Anchored at the start only, so a date carrying a clock time is still read as
# the date it leads with.
ISO_SLASH = re.compile(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}")
SLASH_DATE = re.compile(r"(\d{1,2})[/-](\d{1,2})[/-]\d{2,4}")
WEEKDAY = r"(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?,?\s+)?"
# Either order, because a spelled month cannot be misread whichever side the
# day sits on.
NAMED_DATE = re.compile(
    rf"{WEEKDAY}(?:\d{{1,2}}[ -](?:{MONTHS})\.?[ -]\d{{2,4}}"
    rf"|(?:{MONTHS})\.?[ -]\d{{1,2}},?[ -]\d{{2,4}})",
    re.I,
)
TIME_TAIL = re.compile(
    r"^[,\s]*(?:at\s+)?\d{1,2}:\d{2}(?::\d{2})?\s*(?:[APap]\.?[Mm]\.?)?\s*[A-Za-z]{0,4}$"
)
# A month, a month and year, or a year alone: a real date reading that cannot
# name a day. Only actual month names count, so `TBD` stays a non-date.
PARTIAL_DATE = re.compile(
    rf"^(?:(?:{MONTHS})\.?(?:[ -]\d{{2,4}})?|\d{{1,2}}[/-]\d{{2,4}}|\d{{4}})$", re.I
)
NONNEGATIVE_FIELDS = {
    "quantity",
    "unit_price",
    "extended_amount",
    "subtotal",
    "tax_amount",
    "freight_amount",
    "accessorial_total",
    "discount_amount",
    "total_amount",
    "amount_due",
    "total_paid",
    "weight",
    "piece_count",
    "volume",
}
IDENTIFIER_FIELDS = {
    "invoice_number",
    "po_number",
    "purchase_order_number",
    "ack_number",
    "job_number",
    "pro_number",
    "bol_number",
    "awb_number",
    "memo_number",
    "cheque_number",
}


def scalar(value):
    """Read a field object without replacing the original object."""
    return value.get("value") if isinstance(value, dict) else value


def numeric(value):
    """Parse common business numeric notation without accepting booleans."""
    value = scalar(value)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("$", "")
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    try:
        return float(text)
    except ValueError:
        return None


def date_value(value):
    """Accept ISO dates only; ambiguous dates require client review."""
    value = scalar(value)
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def date_text(value):
    """Read a date cell as trimmed text, or None when there is nothing to judge."""
    plain = scalar(value)
    if plain is None:
        return None
    return str(plain).strip() or None


def proves_date_order(values):
    """Say whether the document's own slash dates settle day-first or month-first.

    A part above twelve can only be a day, so one such part fixes the order for
    every slash date on the page. The order is never inferred from anything
    else: a document that does not prove its own order keeps the ambiguity as a
    finding rather than having a convention chosen for it.
    """
    day_first = month_first = False
    for path, value in values.items():
        if path.rsplit(".", 1)[-1] not in DATE_FIELDS:
            continue
        text = date_text(value)
        match = SLASH_DATE.match(text) if text else None
        if match:
            day_first = day_first or int(match.group(1)) > 12
            month_first = month_first or int(match.group(2)) > 12
    return day_first != month_first


def date_reading(text):
    """Split a date cell into the date it leads with and whatever trails it."""
    for pattern in (ISO_SLASH, NAMED_DATE, SLASH_DATE):
        match = pattern.match(text)
        if match:
            return match, text[match.end() :].strip()
    return None, text


def date_issues(document_id, path, value, order_proved):
    """Return what a date cell earns, separating unreadable from merely ambiguous.

    A cell the source never rendered, a cell holding an identifier, a cell that
    names no day, and a real date in an unsettled order are four different
    faults with four different answers; reporting them under one reason hid the
    first three behind the volume of the last.
    """
    text = date_text(value)
    if text is None or date_value(value) is not None:
        return []
    if NOT_RENDERED.search(text):
        return [validation_issue(document_id, path, "date_not_rendered_in_source", value)]
    match, tail = date_reading(text)
    if match and (not tail or TIME_TAIL.match(tail)):
        ambiguous = match.re is SLASH_DATE and not order_proved
        if not ambiguous:
            return []
        return [validation_issue(document_id, path, "invalid_or_ambiguous_iso_date", value)]
    if PARTIAL_DATE.match(text):
        return [validation_issue(document_id, path, "date_lacks_a_day", value)]
    return [validation_issue(document_id, path, "date_field_holds_a_non_date", value)]


def flattened_values(record, prefix=""):
    """Yield dotted field paths for header and line objects."""
    for key, value in record.items():
        if key in {"fields", "amendments", "handwriting_regions"}:
            continue
        path = f"{prefix}{key}"
        if isinstance(value, dict) and "value" not in value:
            yield from flattened_values(value, f"{path}.")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    yield from flattened_values(item, f"{path}[{index}].")
        else:
            yield path, value


def validation_issue(document_id, field, reason, value=None):
    """Construct a stable exception shape for the final review queue."""
    return {
        "document_id": document_id,
        "field": field,
        "reason": reason,
        "value": scalar(value),
        "disposition": "client_review_required",
    }


# Fields that name the counterparty a commission is owed by. The engagement's
# own party has no business in one of them.
SUPPLIER_FIELDS = ("brand_name", "manufacturer_name", "supplier_name", "vendor_name")


def engagement_party_in_supplier_field(document_id, path, field, value, engagement_party):
    """Flag the engagement's own name sitting in a supplier field.

    A representative agency appears on every page of its own commission
    statements, and an extractor with no notion of whose engagement this is will
    read that name into `brand_name` as readily as the manufacturer's. On the
    commission corpus that put the agency into the supplier field of 119
    documents, where it silently became the largest "brand" in the dataset and
    every figure grouped by brand was wrong.

    Flagged, never removed: the extractor read what was on the page, and which
    name is the supplier is the client's statement rather than this control's
    inference.
    """
    if not engagement_party or field not in SUPPLIER_FIELDS:
        return None
    plain = scalar(value)
    if plain is None:
        return None
    if normalized_party(str(plain)) not in engagement_party:
        return None
    return validation_issue(document_id, path, "engagement_party_named_as_supplier", value)


def normalized_party(name):
    """Compare party names without punctuation, case, or corporate suffixes."""
    import re as _re

    text = _re.sub(r"[^a-z0-9 ]+", " ", str(name).casefold())
    words = [w for w in text.split() if w not in {"inc", "llc", "ltd", "co", "corp", "company"}]
    return " ".join(words)


def validate_record(record, currencies, engagement_party=frozenset()):
    """Return independent validation findings; do not mutate record evidence."""
    document_id = record.get("document_id", "unknown")
    issues, values = [], dict(flattened_values(record))
    order_proved = proves_date_order(values)
    for path, value in values.items():
        field = path.rsplit(".", 1)[-1]
        plain = scalar(value)
        if isinstance(value, dict) and value.get("value") is not None and not value.get("source"):
            issues.append(validation_issue(document_id, path, "missing_field_provenance", value))
        party_issue = engagement_party_in_supplier_field(
            document_id, path, field, value, engagement_party
        )
        if party_issue is not None:
            issues.append(party_issue)
        if field in DATE_FIELDS and plain is not None:
            issues.extend(date_issues(document_id, path, value, order_proved))
        if field in NONNEGATIVE_FIELDS and plain is not None:
            parsed = numeric(value)
            if parsed is None:
                issues.append(validation_issue(document_id, path, "invalid_numeric_value", value))
            elif parsed < 0:
                issues.append(
                    validation_issue(document_id, path, "negative_value_requires_review", value)
                )
        if field in IDENTIFIER_FIELDS and plain is not None:
            text = str(plain).strip()
            if len(text) < 3 or not re.search(r"\d", text) or any(char.isspace() for char in text):
                issues.append(
                    validation_issue(document_id, path, "implausible_identifier_format", value)
                )
    currency = scalar(values.get("header.currency", values.get("currency")))
    if currency is not None and str(currency).upper() not in currencies:
        issues.append(validation_issue(document_id, "currency", "unsupported_currency", currency))
    header = record.get("header", record)
    due, invoice = date_value(header.get("due_date")), date_value(header.get("invoice_date"))
    delivery, ship = date_value(header.get("delivery_date")), date_value(header.get("ship_date"))
    if due and invoice and due < invoice:
        issues.append(
            validation_issue(document_id, "header.due_date", "due_date_before_invoice_date")
        )
    if delivery and ship and delivery < ship:
        issues.append(
            validation_issue(document_id, "header.delivery_date", "delivery_date_before_ship_date")
        )
    return issues


def records_from(path):
    """Read one record, a list, or a normal pipeline document artifact."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("documents"), list):
            return data["documents"]
        if "document_id" in data:
            return [data]
    raise ValueError("Input must be a record, a list of records, or contain documents")


def validate(records, currencies, engagement_party=frozenset()):
    """Carry every record forward with a deterministic validation status."""
    validated, exceptions = [], []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Every record must be an object")
        copy = deepcopy(record)
        issues = validate_record(copy, currencies, engagement_party)
        copy["field_validation_status"] = "clear" if not issues else "client_review_required"
        copy["field_validation_findings"] = issues
        validated.append(copy)
        exceptions.extend(issues)
    return validated, exceptions


def main():
    parser = argparse.ArgumentParser(
        description="Validate extracted fields without altering source values."
    )
    parser.add_argument("input", help="consensus/proofed JSON, a record, or a list of records")
    parser.add_argument("--out", required=True, help="records with validation findings")
    parser.add_argument("--exceptions", required=True, help="client-review validation exceptions")
    parser.add_argument(
        "--currencies",
        default=",".join(sorted(DEFAULT_CURRENCIES)),
        help="comma-separated allowed ISO currency codes",
    )
    parser.add_argument(
        "--engagement-party",
        action="append",
        default=None,
        metavar="NAME",
        help=(
            "This engagement's own party name, repeatable. A representative agency appears on "
            "every page of its own commission statements, and an extractor reads that name into "
            "brand_name as readily as the manufacturer's; naming it here routes those to review "
            "instead of letting the agency become the dataset's largest supplier."
        ),
    )
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        currencies = {item.strip().upper() for item in args.currencies.split(",") if item.strip()}
        engagement_party = frozenset(
            normalized_party(name) for name in (args.engagement_party or []) if str(name).strip()
        )
        if not currencies:
            raise ValueError("At least one allowed currency is required")
        records = records_from(args.input)
        validated, exceptions = validate(records, currencies, engagement_party)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Field validation failed: {exc}")
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "documents": len(validated),
        "clear_documents": sum(not item["field_validation_findings"] for item in validated),
        "client_review_items": len(exceptions),
        "findings": [
            "Validation findings do not replace extracted values; every fault is retained for review."
        ],
    }
    Path(args.out).write_text(
        json.dumps({"summary": summary, "documents": validated}, indent=2) + "\n"
    )
    Path(args.exceptions).write_text(
        json.dumps({"summary": {"count": len(exceptions)}, "exceptions": exceptions}, indent=2)
        + "\n"
    )
    if not args.quiet:
        print(f"Validated documents: {len(validated)}")
        print(f"Client review items: {len(exceptions)}")


if __name__ == "__main__":
    main()
