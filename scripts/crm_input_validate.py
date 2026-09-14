#!/usr/bin/env python3
"""Validate an exported CRM grain against the canonical schema it loads into.

The export writes what the pages said. This asks a different question: whether
what it wrote can survive the target it is going into. A CSV has no types, so a
column that reaches a loader as text loads as text, and a value that is legal on
a page can still be illegal in the column it lands in.

Five faults, each of which reached a real CRM before this existed:

* A date or money column with **no typed companion**. `DATE_COLUMNS` had drifted
  to seven of the schema's seventeen date fields, so `period_start`, `period_end`
  and `payment_date` arrived as raw text with nothing to cast -- 362 populated
  cells whose only reading was a string.
* A **multi-valued cell in a single-valued column**. `contact.phone` is one
  `VARCHAR(48)`, and a page that prints an office and a direct number puts both
  in one cell. Loading that either truncates it or splits the contact in two.
* A value **longer than its target column**, which a loader truncates silently.
* A **placeholder standing in for an identity**. `payment_reference` reads `00`
  on eighteen documents and `check_number` reads `0` on two with different
  amounts. Keyed on, those collapse distinct payments into one.
* A value whose **shape belongs to another table**: a street address in an item
  description, which is a real reading of a project name and still not a product.
* A value **outside the target's enum**. `review_queued` is a status this
  pipeline writes and `review_status_t` does not admit, so twenty-six line rows
  and five documents fail the load on the type, not on the width.
* A **date whose year is not a year**. `05/24/0122` normalizes into
  `0122-05-24`, which is well-formed ISO, casts without complaint, and lands in
  the second century. Four cells, and the only date fault a format check cannot
  see.
* A value that **references a dimension row nothing creates**. `document_type`
  reads `unknown` on 664 of 683 documents and is a foreign key; the load needs
  that row seeded before the fact will go in.

Both of the last two are read out of the DDL rather than listed here. Every
defect this session began as a hand-kept list drifting from the schema it was
supposed to mirror, so the enum members, the columns carrying an enum, and the
foreign keys are all derived from the same file the load uses.

Nothing is repaired. Each finding names the row, the column, the target it fails,
and what the target expects, because the answer is a mapping decision and not a
rewrite of what the page said.
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help

ARTIFACT_TYPE = "crm_input_validation_v1"
COLUMN_PATTERN = re.compile(
    r"^\s*(?P<name>[a-z_][a-z0-9_]*)\s+(?P<type>[A-Z][A-Z0-9 ]*(?:\(\d+(?:,\s*\d+)?\))?)", re.M
)
TABLE_PATTERN = re.compile(r"CREATE TABLE (?P<table>[a-z_]+)\s*\((?P<body>.*?)\n\);", re.S)
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_MONTH = re.compile(r"^\d{4}-\d{2}$")
# Each typed date companion, and the shape its value takes.
DATE_COMPANIONS = (("__iso", ISO_DATE), ("__month", ISO_MONTH))
# A year under four digits is a short or damaged year field, not a date. The
# corpus read `05/24/0122` and `03/17/202`, and both normalized into perfectly
# well-formed ISO -- `0122-05-24` casts without complaint and lands in the
# second century. This is the one date fault a format check cannot see, and it
# needs no threshold to state: a business document is not from the year 122.
IMPLAUSIBLE_YEAR = 1000
# A page prints an office and a direct number in one cell, or two emails.
PHONE = re.compile(r"\d[\d\-.\s()]{6,}\d")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", re.I)
STREET = re.compile(
    r"\b\d{1,6}\s+[A-Za-z][^,;]{0,30}?\b(?:st|street|ave|avenue|rd|road|blvd|boulevard"
    r"|dr|drive|ln|lane|way|suite|ste|pkwy|parkway|hwy|highway)\b",
    re.I,
)
# An identity that is only zeros, or only punctuation, is a placeholder the page
# printed rather than a key. `entity_resolve.py` refuses the same shape for names.
PLACEHOLDER_KEY = re.compile(r"^[0\W_]+$")
# Only identities that name **one** record. An invoice number or transaction id
# is a reference many lines and many statements legitimately share -- a monthly
# statement restates the same invoice until it is paid -- so collisions there
# are the corpus working, and flagging 417 of them would bury the real ones.
IDENTITY_COLUMNS = ("payment_reference", "check_number")
PRODUCT_COLUMNS = ("product_sku", "item_code", "description", "style", "product_name")
SINGLE_VALUED = {
    "contact_phone": PHONE,
    "contact_email": EMAIL,
    "sales_representative_phone": PHONE,
    "sales_representative_email": EMAIL,
}
# Every canonical country column is CHAR(2). A page prints the country's name,
# and `United States of America (the)` truncates to `Un` in a two-character
# column, so it needs a lookup rather than a cast.
COUNTRY_COLUMN = re.compile(r"(^|_)country(_of_\w+|_code)?$")
ISO_COUNTRY = re.compile(r"^[A-Za-z]{2}$")
# `augment_export.py` writes an inferred value in `<column>__inferred`, beside
# the column it fills, with two columns saying how it was found. The value
# loads into that column; the other two travel with it for an operator, and no
# target column holds them -- checked as values, they reported the method's own
# description of a country as a country name.
INFERRED = "__inferred"
PROVENANCE = ("__inferred_by", "__inferred_evidence")


def declared_scale(declared):
    """Return the decimal places a NUMERIC declares, or None."""
    match = re.match(r"NUMERIC\((\d+),\s*(\d+)\)", declared or "")
    return int(match.group(2)) if match else None


def scales_by_column(targets):
    """Map a bare column name to the tightest decimal scale any target declares."""
    scales = {}
    for qualified, declared in targets.items():
        scale = declared_scale(declared)
        if scale is None:
            continue
        name = qualified.split(".", 1)[1]
        scales[name] = min(scale, scales.get(name, scale))
    return scales


ENUM_PATTERN = re.compile(r"CREATE TYPE (?P<name>\w+) AS ENUM \((?P<body>.*?)\);", re.S)
ENUM_MEMBER = re.compile(r"'([^']+)'")
REFERENCES = re.compile(r"REFERENCES (?P<table>\w+)\((?P<column>\w+)\)")
# The export names a status for the document a row came from; the target calls
# the same thing `review_status` on every table that carries one. This is the
# only rename between the two vocabularies, and it is here rather than derived
# because no file records it -- if it grows a second entry, that is a sign the
# export and the schema have started drifting apart and want reconciling.
COLUMN_ALIASES = {"document_review_status": "review_status"}


def enum_members(ddl_text):
    """Read the canonical DDL into `enum type -> the values it admits`."""
    return {
        match.group("name"): frozenset(ENUM_MEMBER.findall(match.group("body")))
        for match in ENUM_PATTERN.finditer(ddl_text)
    }


def columns_declaring(ddl_text, wanted):
    """Map a bare column name to the enum type its target declares.

    Derived from the DDL for the same reason the date fields are derived from
    the schema: a list kept by hand beside the thing it mirrors goes stale, and
    the drift is invisible until a load fails.
    """
    found = {}
    for table in TABLE_PATTERN.finditer(ddl_text):
        for line in table.group("body").splitlines():
            match = re.match(r"\s+(?P<name>[a-z_][a-z0-9_]*)\s+(?P<type>\w+)", line)
            if match and match.group("type") in wanted:
                found[match.group("name")] = match.group("type")
    return found


def controlled_vocabularies(ddl_text):
    """Name the referenced tables that are seeded lists rather than facts.

    The schema separates them itself: a table the pipeline fills carries a
    `review_status`, because every fact it writes has to be reviewable. The
    small closed lists -- currency, country, unit of measure, charge code,
    document type -- carry none, because nobody reviews what a currency code
    means. So the presence of that column, and not a list kept here, says
    whether a missing row is a seed the load needs or a business key that
    legitimately comes from the client's own system.

    It matters: `job_number` is a foreign key too, and reporting its 699 values
    would bury the six that are actually a prerequisite.
    """
    seeded = set()
    for table in TABLE_PATTERN.finditer(ddl_text):
        if "review_status" not in table.group("body"):
            seeded.add(table.group("table"))
    return seeded


def foreign_keys(ddl_text):
    """Map a bare column name to the seeded dimension its target references."""
    seeded = controlled_vocabularies(ddl_text)
    found = {}
    for table in TABLE_PATTERN.finditer(ddl_text):
        for line in table.group("body").splitlines():
            name = re.match(r"\s+(?P<name>[a-z_][a-z0-9_]*)\s", line)
            reference = REFERENCES.search(line)
            if name and reference and reference.group("table") in seeded:
                found[name.group("name")] = reference.group("table")
    return found


def target_columns(ddl_text):
    """Read the canonical DDL into `table.column -> declared type`."""
    columns = {}
    for table in TABLE_PATTERN.finditer(ddl_text):
        for column in COLUMN_PATTERN.finditer(table.group("body")):
            # A table constraint line starts with an uppercase keyword, which the
            # column pattern's lowercase-initial name cannot match, so those need
            # no separate guard.
            columns[f"{table.group('table')}.{column.group('name')}"] = column.group("type").strip()
    return columns


def declared_width(declared):
    """Return the character limit a VARCHAR declares, or None."""
    match = re.match(r"(?:VAR)?CHAR\((\d+)\)", declared or "")
    return int(match.group(1)) if match else None


def widths_by_column(targets):
    """Map a bare column name to the narrowest width any target declares for it.

    A column reaches several tables under one name; the narrowest is the one
    that truncates first, so it is the one worth warning about.
    """
    widths = {}
    for qualified, declared in targets.items():
        width = declared_width(declared)
        if width is None:
            continue
        name = qualified.split(".", 1)[1]
        widths[name] = min(width, widths.get(name, width))
    return widths


def finding(row, column, reason, value, detail):
    """Construct one finding, naming the row and what its target expects."""
    return {
        "document_id": row.get("document_id", ""),
        "line_index": row.get("line_index", ""),
        "column": column,
        "reason": reason,
        "value": value[:120],
        "expected": detail,
        "disposition": "mapping_decision_required",
    }


def untyped_columns(rows, columns, suffix, kind):
    """Name every column of this kind that reaches the target without a cast."""
    header = rows[0] if rows else {}
    found = []
    for column in columns:
        if column not in header or f"{column}{suffix}" in header:
            continue
        filled = sum(1 for row in rows if (row.get(column) or "").strip())
        if filled:
            found.append(
                {
                    "column": column,
                    "rows": filled,
                    "kind": kind,
                    "reason": "no_typed_companion",
                    "expected": f"a {kind} column the loader can cast",
                    "disposition": "mapping_decision_required",
                }
            )
    return found


def date_year_issues(rows):
    """Name every typed date or month whose year cannot belong to a business document.

    Reported per row, with the raw reading beside the cast value, because the
    fix is a re-reading of the page and not an arithmetic on the year: `202`
    could be 2020, 2021 or 2022 and nothing here can choose between them. A
    month typed beside a reading (`__month`) is held to the same test as a
    date, rather than escaping it for want of the `__iso` suffix.
    """
    findings = []
    for row in rows:
        for column, value in row.items():
            companion = next(
                ((suffix, shape) for suffix, shape in DATE_COMPANIONS if column.endswith(suffix)),
                None,
            )
            if companion is None:
                continue
            suffix, shape = companion
            text = (value or "").strip()
            if not shape.match(text) or int(text[:4]) >= IMPLAUSIBLE_YEAR:
                continue
            source = (row.get(column[: -len(suffix)]) or "").strip()
            findings.append(
                finding(
                    row,
                    column,
                    "date_year_is_not_a_year",
                    text,
                    f"a four-digit year; the page was read as {source!r}, "
                    "and which year that is cannot be settled from the value",
                )
            )
    return findings


def target_name(column):
    """Return the canonical column an exported column loads into."""
    return COLUMN_ALIASES.get(column, column)


def enum_issues(rows, enum_of_column, members):
    """Name every cell holding a value the target's enum does not admit.

    A width check passes `review_queued` and the column still refuses it: the
    type is an enum, and a value outside it fails the load rather than
    truncating quietly. Reported per row, because which rows are affected is the
    thing an operator needs in order to decide what those rows should say.
    """
    findings = []
    for row in rows:
        for column, value in row.items():
            enum = enum_of_column.get(target_name(column))
            text = (value or "").strip()
            if not enum or not text:
                continue
            admitted = members.get(enum, frozenset())
            if text not in admitted:
                findings.append(
                    finding(
                        row,
                        column,
                        "value_outside_the_target_enum",
                        text,
                        f"one of {', '.join(sorted(admitted))}; {enum} admits nothing else",
                    )
                )
    return findings


def unseeded_dimensions(rows, keys):
    """Name every foreign-key value whose dimension row the run never creates.

    The run writes facts and no dimensions, so each distinct value here is a row
    the load must seed first or the foreign key rejects the fact. Reported once
    per distinct value rather than once per row: the decision is per value, and
    664 identical findings would bury the other kinds.
    """
    seen = defaultdict(Counter)
    for row in rows:
        for column, value in row.items():
            table = keys.get(target_name(column))
            text = (value or "").strip()
            if table and text:
                seen[(column, table)][text] += 1
    findings = []
    for (column, table), values in sorted(seen.items()):
        for value, count in sorted(values.items(), key=lambda kv: (-kv[1], kv[0])):
            findings.append(
                {
                    "document_id": "",
                    "line_index": "",
                    "column": column,
                    "reason": "references_a_dimension_row_the_run_never_creates",
                    "value": value[:120],
                    "rows": count,
                    "expected": f"a {table} row with this key, seeded before the fact loads",
                    "disposition": "mapping_decision_required",
                }
            )
    return findings


def validate_rows(rows, widths, scales):
    """Return every row-level finding for one exported grain."""
    findings = []
    identity_seen = defaultdict(lambda: defaultdict(set))
    for row in rows:
        for column, pattern in SINGLE_VALUED.items():
            value = (row.get(column) or "").strip()
            if value and len(pattern.findall(value)) > 1:
                findings.append(
                    finding(
                        row,
                        column,
                        "multiple_values_in_a_single_valued_column",
                        value,
                        "one value; the target column holds one, so give each its own field or row",
                    )
                )
        for column, value in row.items():
            text = (value or "").strip()
            skipped = ("__amount", "__iso", "__currency", "__resolved_party", *PROVENANCE)
            if not text or column.endswith(skipped):
                continue
            # An inferred value loads into the column it fills, so it is held to
            # that column's width.
            width = widths.get(column.removesuffix(INFERRED))
            if width and len(text) > width:
                findings.append(
                    finding(
                        row,
                        column,
                        "longer_than_the_target_column",
                        text,
                        f"at most {width} characters; a loader truncates the rest silently",
                    )
                )
        for column in IDENTITY_COLUMNS:
            text = (row.get(column) or "").strip()
            if text and PLACEHOLDER_KEY.match(text):
                findings.append(
                    finding(
                        row,
                        column,
                        "placeholder_standing_in_for_an_identity",
                        text,
                        "a key that identifies one record; keyed on, this merges distinct ones",
                    )
                )
            elif text:
                identity_seen[column][text].add(row.get("document_id", ""))
        for column, value in row.items():
            text = (value or "").strip()
            if not text or not COUNTRY_COLUMN.search(column) or ISO_COUNTRY.match(text):
                continue
            if column.endswith(PROVENANCE):
                continue
            findings.append(
                finding(
                    row,
                    column,
                    "country_name_where_a_two_letter_code_belongs",
                    text,
                    "a CHAR(2) ISO country code; the name needs a lookup, not a cast",
                )
            )
        for column, scale in scales.items():
            text = (row.get(f"{column}__amount") or "").strip()
            if "." in text and len(text.split(".")[1]) > scale:
                findings.append(
                    finding(
                        row,
                        f"{column}__amount",
                        "more_decimal_places_than_the_target_keeps",
                        text,
                        f"at most {scale} decimal places; the target rounds the rest away",
                    )
                )
        for column in PRODUCT_COLUMNS:
            text = (row.get(column) or "").strip()
            if text and STREET.search(text):
                findings.append(
                    finding(
                        row,
                        column,
                        "address_shaped_value_in_a_product_column",
                        text,
                        "an item code or description; an address belongs to the address table",
                    )
                )
    for column, values in identity_seen.items():
        for value, documents in values.items():
            if len(documents) > 1:
                findings.append(
                    {
                        "document_id": "; ".join(sorted(documents)[:5]),
                        "line_index": "",
                        "column": column,
                        "reason": "one_identity_claimed_by_several_documents",
                        "value": value,
                        "expected": f"a value unique to one record; {len(documents)} claim it",
                        "disposition": "mapping_decision_required",
                    }
                )
    return findings


def validate(grains, targets, ddl_text=""):
    """Validate every supplied grain against the canonical schema."""
    widths, scales = widths_by_column(targets), scales_by_column(targets)
    members = enum_members(ddl_text)
    enum_of_column = columns_declaring(ddl_text, members)
    keys = foreign_keys(ddl_text)
    findings, summary = [], {"grains": {}, "target_columns": len(targets)}
    date_columns = sorted({c.split(".", 1)[1] for c, d in targets.items() if d.startswith("DATE")})
    # NUMERIC covers money and counts alike; the export types money in
    # `__amount`, and a count with no companion still reaches the target as text.
    numeric = sorted({c.split(".", 1)[1] for c, d in targets.items() if d.startswith("NUMERIC")})
    for name, rows in grains.items():
        grain = list(validate_rows(rows, widths, scales))
        grain += enum_issues(rows, enum_of_column, members)
        grain += date_year_issues(rows)
        columns = untyped_columns(rows, date_columns, "__iso", "date")
        columns += untyped_columns(rows, numeric, "__amount", "numeric")
        columns += unseeded_dimensions(rows, keys)
        for entry in columns:
            findings.append({"document_id": "", "line_index": "", **entry})
        findings.extend(grain)
        summary["grains"][name] = {"rows": len(rows), "findings": len(grain) + len(columns)}
    summary["findings"] = len(findings)
    summary["by_reason"] = dict(Counter(item["reason"] for item in findings))
    return findings, summary


def read_grain(path):
    """Read one exported CSV grain."""
    with Path(path).open(newline="") as handle:
        return list(csv.DictReader(handle))


def main():
    parser = argparse.ArgumentParser(
        description="Validate an exported CRM grain against the canonical schema it loads into."
    )
    parser.add_argument(
        "--grain",
        action="append",
        required=True,
        metavar="NAME=PATH",
        help="exported CSV to validate, repeatable (for example lines=.../lines.csv)",
    )
    parser.add_argument("--schema", required=True, help="canonical DDL naming the target columns")
    parser.add_argument("--out", required=True, help="validation report")
    parser.add_argument("--findings-csv", help="the same findings as a CSV an operator can work")
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        grains = {}
        for item in args.grain:
            if "=" not in item:
                raise ValueError(f"--grain expects NAME=PATH, got {item!r}")
            name, path = item.split("=", 1)
            grains[name] = read_grain(path)
        ddl = Path(args.schema).read_text()
        targets = target_columns(ddl)
        if not targets:
            raise ValueError(f"No target columns found in {args.schema}")
        findings, summary = validate(grains, targets, ddl)
    except (OSError, ValueError, csv.Error) as exc:
        sys.exit(f"CRM input validation failed: {exc}")
    summary["generated_at"] = datetime.now(UTC).isoformat()
    summary["notes"] = [
        "Nothing is repaired: each finding is a mapping decision, not a rewrite of a reading.",
        "A finding names the row, the column, and what the target column expects.",
    ]
    Path(args.out).write_text(
        json.dumps(
            {"artifact_type": ARTIFACT_TYPE, "summary": summary, "findings": findings}, indent=2
        )
        + "\n"
    )
    if args.findings_csv:
        columns = [
            "document_id",
            "line_index",
            "column",
            "reason",
            "value",
            "expected",
            "rows",
            "kind",
            "disposition",
        ]
        with Path(args.findings_csv).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(findings)
    if not args.quiet:
        print(f"Target columns: {summary['target_columns']}   findings: {summary['findings']}")
        for reason, count in sorted(summary["by_reason"].items(), key=lambda kv: -kv[1]):
            print(f"  {count:6d}  {reason}")


if __name__ == "__main__":
    main()
