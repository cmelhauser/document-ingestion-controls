#!/usr/bin/env python3
"""Verify what the export says against what the page prints, page by page.

`accuracy_sample.py` names the pages worth reading; nothing scored them. A
reader's prose is not a measurement, and on this corpus that mattered: reading
by hand found real defects and produced no rate, because "I checked it and it
looked right" cannot be aggregated, re-run, or disagreed with.

So a reviewer here reports **data** -- among it the money the page prints, in
the page's own words -- and this command checks that against the export
mechanically. The reviewer never sees the verdict, and the verdict never
depends on the reviewer's summary.

Two questions are asked of every page, and only one of them is an error either
way round:

* **Lost money** -- a figure the page prints that the export holds nowhere, not
  even on a row some flag excluded. Measured against everything the export
  retains, because a value the export deliberately excluded is not lost.
* **Invented money** -- a figure the export counts that the page never printed.
  Measured against the countable rows only, because a duplicate remittance stub
  the page really does print twice is the flag working, not an invention.

Comparing those two the other way round called every correctly de-duplicated
remittance a defect and every two-pass page a disaster, which is how a check
that is merely strict ends up hiding the failures that matter.

An over-extraction is not a misreading, and the two must not share a number. A
page can emit forty-four line records for eleven printed rows with every
reading correct; on this corpus that is common, and counting those records as
wrong cells moved the reported accuracy by five points without a single digit
being misread.

With `--proposals` the reviews also become something an operator can act on.
A reviewer's reading is one model's reading, so each proposal carries the
evidence beside it: whether the independent extractor's text of the page
prints the figure, and whether a retained engine read the cell that way while
consensus chose otherwise. A correction, a row the page never prints, and a
value the export lost are proposed apart, because they ask different things of
whoever authorizes them.

With `--second-read`, a correction the reviewer alone supports, and a row the
extractor prints as often as the export, are read against another vendor's
reading of the same page -- never the reviewers' own vendor. A correction
becomes `second_vendor` where that reading holds the figure in its column more
often than the export's other lines do. A row becomes
`second_vendor_prints_it_fewer_times` only where the reading holds every other
figure the export counts in that column, as often, and this one fewer times: a
reader that misses a quarter of a page misses rows the page does print, and its
silence is no evidence that a row is absent.

This scores an export against source images. It accepts nothing, clears no
control, and writes no artifact any downstream command reads.
"""

import argparse
import collections
import csv
import functools
import glob
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from cli_help import apply_shared_help
from engine_agreement import cell_value, load_records
from multi_engine_vote import handoff_vendor
from run_io import RETRY_SUFFIX

ARTIFACT_TYPE = "page_review_v1"
# Every key a reviewer must return. A file missing one is reported as a schema
# error rather than counted, because a review that silently omitted its money
# list would otherwise reconcile with anything.
REQUIRED_KEYS = (
    "page",
    "document_kind",
    "page_rows",
    "money_cells_checked",
    "money_cells_wrong",
    "page_money",
    "money_column",
)
# Flags that say a row is not a line of this document. A page total must
# exclude these; every other flag on the row is a judgement or a confidence,
# and excluding those returned a quarter of one run's money.
NOT_A_LINE_OF_THIS_DOCUMENT = (
    "duplicate_of_line",
    "restates_a_total",
    "repeats_an_earlier_block",
    "amount_without_line_identity",
    "repeats_a_job_and_amount_above",
)
# Header columns that can hold a document's whole money when it prints no
# lines. A remittance states one figure and the export puts it here.
HEADER_MONEY_WORDS = ("amount", "total", "paid", "commission")
TYPED_SUFFIXES = ("__amount", "__iso", "__currency")
# A currency code beside a figure, as `57.06 USD` or `USD 57.06`.
CURRENCY_CODE = re.compile(r"^[A-Z]{3}\s+|\s+[A-Z]{3}$")
PROPOSALS_ARTIFACT_TYPE = "page_review_proposals_v1"
# The columns a correction may be proposed for. A reviewer's value for a text
# column is prose as often as it is a reading -- "the Customer column prints
# Office Fixture Group" -- so those stay findings in the reviews.
MONEY_COLUMNS = (
    "commission_amount",
    "commissionable_amount",
    "sales_amount",
    "invoice_amount",
    "extended_amount",
    "total_amount",
    "applied_amount",
    "deposit_amount",
    "amount_due",
    "total_paid",
    "unit_price",
)
# How a reviewer says the export carries a row the page never prints.
NOT_PRINTED = re.compile(
    r"not printed|not on the page|never prints|does not print|doesn't print|no such row"
    r"|second pass|spurious|duplicat|repeat|re-walk|subtotal|not a (?:data )?row"
    r"|heading row|page never",
    re.I,
)
# A number as the independent extractor's text of a page prints it.
PRINTED_NUMBER = re.compile(r"\(?-?\$?\s?\d[\d,]*(?:\.\d+)?\)?")
# A reviewer's figure with a note after it: `799.67 (1,404.13 is job 13046's)`.
# Only a token that looks like money counts -- cents or a thousands separator --
# so `2 rows: 100.00 and 200.00` never reads as 2.
LEADING_MONEY = re.compile(
    r"^\s*(\(?-?\$?(?:\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+\.\d{2})\)?)(?=\s|$)"
)
# What another vendor's reading of the page can make of a proposal. The reviewer
# is one model reading an image, so a correction nothing else backs waits for
# another vendor to read the same figure; and a row the reviewer says the page
# never prints, which the extractor prints as often as the export, waits for
# another vendor to print it fewer times.
SECOND_VENDOR = "second_vendor"
SECOND_VENDOR_FEWER = "second_vendor_prints_it_fewer_times"


def reviewer_value(said):
    """The figure a reviewer reports for a cell, with any note after it set aside.

    Reviewers wrote a shifted row's fix as `799.67 (1,404.13 is job 13046's)`.
    Reading only a bare number skipped that half of the fix, and amending the
    other half alone counted 1,404.13 twice on one page.
    """
    value = money_value(said)
    if value is not None:
        return value
    match = LEADING_MONEY.match(str(said or ""))
    return money_value(match.group(1)) if match else None


def money_value(raw):
    """Read a printed money string as a number, or None if it is not one.

    Parentheses mean negative on these pages, and the thousands separator,
    currency sign and currency code are decoration. Refusing `57.06 USD` made
    a page whose every figure the export held look as if it held none. A value
    that will not parse is not money and is refused rather than guessed at.
    """
    text = CURRENCY_CODE.sub("", str(raw or "").strip()).replace(",", "").replace("$", "")
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    try:
        value = Decimal(text.strip("()"))
    except InvalidOperation:
        return None
    return -value if negative else value


@functools.lru_cache(maxsize=8)
def rows_by_page(path):
    """Every row of a delivery CSV, grouped by the page it belongs to.

    Read once per file. Re-reading nine thousand line rows for each of seven
    hundred pages made a whole-corpus summary take minutes.
    """
    grouped = collections.defaultdict(list)
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            grouped[row["document_id"].rsplit("__", 1)[-1]].append(row)
    return grouped


def document_rows(path, page):
    """Every row of a delivery CSV belonging to one page."""
    return list(rows_by_page(path).get(page, []))


def excluded(row):
    """Whether a flag already says a row is not a line of this document."""
    return any(str(row.get(flag) or "").strip() for flag in NOT_A_LINE_OF_THIS_DOCUMENT)


def line_money(rows, column, countable_only):
    """The money one column holds across a page's line rows."""
    out = []
    for row in rows:
        if countable_only and excluded(row):
            continue
        value = money_value(row.get(column))
        if value is not None:
            out.append(value)
    return out


def header_columns(rows, column):
    """The header columns that can hold a page's money.

    The page's own money column when the header carries it; otherwise every
    column whose name says money.
    """
    names = {name for row in rows for name in row}
    if column in names:
        return [column]
    return sorted(
        name
        for name in names
        if not name.endswith(TYPED_SUFFIXES) and any(word in name for word in HEADER_MONEY_WORDS)
    )


def header_money(rows, column=""):
    """The distinct money a document row holds, for a page that prints no lines.

    A remittance or deposit advice has no line items. Reading only the line
    rows called four such pages a disagreement when the export had every figure
    exactly right.

    Given the page's money column, only that column is read. A header also
    states a base, an amount due and a total paid -- figures the page prints
    but a list of its data-row money never names -- and sweeping them all in
    called correct headers an invention.
    """
    names = header_columns(rows, column)
    out = []
    for row in rows:
        for name in names:
            value = money_value(row.get(name))
            if value is not None and value not in out:
                out.append(value)
    return out


def reconcile(review, lines_path, documents_path):
    """Name the money one page lost and the money it invented.

    Returns a pair of sorted lists. Both empty means the export carries exactly
    what the page prints, once the flags have had their say.

    A header may restate the page's printed total: that is where a total
    belongs. A line row may not -- a grand total carried as one more line is
    the page's money counted twice, which is what the flags exist to stop.
    """
    page = review["page"]
    column = review["money_column"]
    printed = collections.Counter(
        v for v in (money_value(x) for x in review["page_money"]) if v is not None
    )
    lines = document_rows(lines_path, page) if column else []
    header = document_rows(documents_path, page) if column else []
    retained = collections.Counter(
        line_money(lines, column, countable_only=False) or header_money(header)
    )
    countable = collections.Counter(line_money(lines, column, countable_only=True))
    allowed = printed
    if not countable:
        countable = collections.Counter(header_money(header, column))
        total = money_value(review.get("page_total"))
        allowed = printed + collections.Counter([total] if total is not None else [])
    lost = sorted((printed - retained).elements())
    invented = sorted((countable - allowed).elements())
    return lost, invented


def schema_error(review):
    """Say why a review cannot be counted, or nothing if it can."""
    missing = sorted(set(REQUIRED_KEYS) - set(review))
    if missing:
        return f"missing keys: {missing}"
    return ""


def load_reviews(directory):
    """Every review on disk, plus the files that would not parse."""
    reviews, broken = [], []
    for path in sorted(glob.glob(str(Path(directory) / "*.json"))):
        try:
            reviews.append(json.loads(Path(path).read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            broken.append((Path(path).name, f"unparseable: {exc}"))
    return reviews, broken


def packet(lines_path, documents_path, images, page):
    """Everything a reviewer needs to check one page, and nothing else."""
    matches = sorted(glob.glob(str(Path(images) / f"*__{page}.png")))
    return {
        "artifact_type": ARTIFACT_TYPE,
        "page": page,
        "image": matches[0] if matches else "",
        "header": document_rows(documents_path, page),
        "lines": document_rows(lines_path, page),
    }


def accuracy(reviews):
    """Cells checked, cells misread, and rows the page never printed.

    `misread_cells` and `spurious_rows` are reported apart because they are
    different failures: one is a wrong reading, the other is a row that does
    not exist. Reviews written before the split carry only the combined count,
    so that is kept too rather than being silently reinterpreted.
    """
    checked = sum(int(r.get("money_cells_checked", 0)) for r in reviews)
    wrong = sum(int(r.get("money_cells_wrong", 0)) for r in reviews)
    misread = sum(int(r.get("misread_cells", 0)) for r in reviews)
    spurious = sum(int(r.get("spurious_rows", 0)) for r in reviews)
    return {
        "money_cells_checked": checked,
        "money_cells_wrong": wrong,
        "misread_cells": misread,
        "spurious_rows": spurious,
    }


def summarize(reviews, lines_path, documents_path):
    """Roll the reviews into a rate, a reconciliation, and a defect inventory."""
    counts = accuracy(reviews)
    clean, disagree, errors = [], [], []
    for review in reviews:
        why = schema_error(review)
        if why:
            errors.append((review.get("page", "?"), why))
            continue
        lost, invented = reconcile(review, lines_path, documents_path)
        if lost or invented:
            disagree.append(
                {
                    "page": review["page"],
                    "lost": [str(v) for v in lost],
                    "invented": [str(v) for v in invented],
                }
            )
        else:
            clean.append(review["page"])
    kinds = collections.Counter(r.get("document_kind", "?") for r in reviews)
    return {
        "artifact_type": ARTIFACT_TYPE,
        "pages_reviewed": len(reviews),
        "pages_reconciling": len(clean),
        "pages_disagreeing": len(disagree),
        "schema_errors": [{"page": p, "why": w} for p, w in errors],
        "disagreements": disagree,
        "document_kinds": dict(kinds.most_common()),
        **counts,
    }


def extractor_numbers(directory):
    """Every number the independent extractor read on each page, counted.

    Document AI returns a page's whole text, not the fields a schema named, so
    a figure the pipeline dropped or misread is usually still in it. A page
    sent more than once keeps the most occurrences any one response saw. A page
    with no response is absent, which is not a page that printed nothing.
    """
    found = {}
    if not directory:
        return found
    for path in sorted(glob.glob(str(Path(directory) / "*.json"))):
        try:
            response = json.loads(Path(path).read_text(encoding="utf-8"))["response"]
            text = response["document"].get("text") or ""
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            continue
        numbers = collections.Counter(
            v for v in (money_value(t) for t in PRINTED_NUMBER.findall(text)) if v is not None
        )
        # A retry is the same page. Keyed by the name's last segment, every
        # `…__p0594__retry1.json` was filed under a page called `retry1`, and the
        # 222 pages read only in a retry had no extractor evidence at all.
        stem = RETRY_SUFFIX.sub("", Path(path).stem)
        page = found.setdefault(stem.rsplit("__", 1)[-1], collections.Counter())
        for value, count in numbers.items():
            page[value] = max(page[value], count)
    return found


def retained_readings(path):
    """Every reading any engine returned for a field, by page and field name."""
    readings = {}
    if not path:
        return readings
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            values = (money_value(x) for x in (row.get("all_readings") or "").split(";"))
            key = (row["document_id"].rsplit("__", 1)[-1], row["field"])
            readings[key] = [v for v in values if v is not None]
    return readings


def field_name(line, column):
    """The field-grain name of one cell."""
    return f"header.{column}" if line == "header" else f"lines[{line}].{column}"


def correction_evidence(value, readings, printed):
    """Name the evidence beside a reviewer's reading of one cell.

    `extractor_text` means the figure appears somewhere in the independent
    extractor's text of the page -- not necessarily in that cell. `engine`
    means a retained engine read the cell that way and consensus chose
    otherwise, which is the strongest single sign a reading went astray.
    """
    engine = value in readings
    extractor = bool(printed) and printed[value] > 0
    if engine and extractor:
        return "extractor_text_and_engine"
    if extractor:
        return "extractor_text"
    if engine:
        return "engine"
    return "reviewer_only"


def not_printed_evidence(row, column, countable, printed):
    """Whether the extractor bears out a reviewer's `the page never prints this row`.

    The page cannot print a figure fewer times than the export counts it and
    have every one of those rows be real.
    """
    value = money_value(row.get(column))
    if value is None:
        return value, "no_money_on_row"
    if printed is None:
        return value, "no_extractor_response"
    if printed[value] < countable[value]:
        return value, "extractor_prints_it_fewer_times"
    return value, "extractor_prints_it_as_often"


def lost_evidence(value, printed):
    """Whether the extractor saw a figure the export lost, so it can be recovered."""
    if printed is None:
        return "no_extractor_response"
    return "extractor_saw_it" if printed[value] else "extractor_missed_it"


def load_second_read(path, reviewer_vendor):
    """Every figure another vendor's reading holds, by page, grain and column.

    Refused when that vendor wrote the reviews, and when the reading's vendor
    cannot be resolved: neither can stand beside the reviewer as a second read.
    A page the reading returned no line for keeps its header but offers no line
    evidence, because an engine that read none of a table is no witness to
    which rows the table prints.
    """
    reviewer = str(reviewer_vendor or "").strip().casefold()
    if not reviewer:
        raise ValueError("a second read needs --reviewer-vendor, the vendor that wrote the reviews")
    data, records = load_records(path)
    vendor = handoff_vendor(data, path)
    if vendor == reviewer:
        raise ValueError(
            f"{path}: the second read is by {vendor}, the reviewers' own vendor, "
            "so it cannot stand beside them"
        )
    pages, unread = {}, []
    for page_id, record in records.items():
        page = str(page_id).rsplit("__", 1)[-1]
        lines = record.get("lines") or []
        columns = collections.defaultdict(collections.Counter)
        for line in lines:
            for column, cell in line.items():
                value = money_value(cell_value(cell))
                if value is not None:
                    columns[column][value] += 1
        if not lines:
            unread.append(page)
        pages[page] = {
            "lines": dict(columns) if lines else None,
            "header": {
                column: money_value(cell_value(cell))
                for column, cell in (record.get("header") or {}).items()
            },
        }
    return {
        "handoff": str(path),
        "engine": data.get("engine"),
        "vendor": vendor,
        "reviewer_vendor": reviewer,
        "pages": pages,
        "pages_with_no_line": sorted(unread),
    }


def second_read_correction(value, line, column, rows, seen):
    """Whether another vendor's reading holds a reviewer's figure for one cell.

    In the header, the reading's own cell must hold it. On a line, the reading
    must hold it in that column more often than the export's other countable
    lines do, so it is not merely the figure a neighbour already carries.
    """
    if line == "header":
        held = int(seen["header"].get(column) == value)
        return {"holds_it": held, "export_holds_it_elsewhere": 0, "agrees": bool(held)}
    if seen["lines"] is None:
        return None
    held = seen["lines"].get(column, collections.Counter())[value]
    elsewhere = sum(
        1
        for index, row in rows.items()
        if index != line and not excluded(row) and money_value(row.get(column)) == value
    )
    return {"holds_it": held, "export_holds_it_elsewhere": elsewhere, "agrees": held > elsewhere}


def second_read_row(value, column, countable, seen):
    """Whether another vendor's reading bears out `the page never prints this row`.

    Only where it holds every other figure the export counts in the column, as
    often, and this one fewer times.
    """
    if seen["lines"] is None:
        return None
    held = seen["lines"].get(column, collections.Counter())
    complete = all(held[figure] >= count for figure, count in countable.items() if figure != value)
    return {
        "holds_it": held[value],
        "export_counts_it": countable[value],
        "holds_every_other_figure": complete,
        "agrees": complete and held[value] < countable[value],
    }


def regrade(record, verdict, grade):
    """Carry a second read's verdict on a proposal, and its grade where it agrees."""
    if verdict is None:
        return
    record["second_read"] = verdict
    if verdict["agrees"]:
        record["evidence"] = grade


def cell_proposal(page, cell, rows, column, countable, readings, printed, second=None):
    """What one wrong cell proposes: a corrected value, a row to exclude, or nothing.

    `second` is another vendor's reading of this page, when there is one.
    """
    if not isinstance(cell, dict):
        return None
    line = str(cell.get("line_index", "")).strip()
    said = str(cell.get("page", ""))
    if NOT_PRINTED.search(said):
        row = rows.get(line)
        if not column or row is None or excluded(row):
            return None
        value, evidence = not_printed_evidence(row, column, countable, printed)
        record = {
            "page": page,
            "line_index": line,
            "column": column,
            "value": "" if value is None else str(value),
            "evidence": evidence,
            "reviewer": said[:300],
        }
        if second is not None and evidence == "extractor_prints_it_as_often":
            regrade(record, second_read_row(value, column, countable, second), SECOND_VENDOR_FEWER)
        return "rows_not_printed", record
    name, value = str(cell.get("column", "")), reviewer_value(said)
    if name not in MONEY_COLUMNS or value is None or not (line.isdigit() or line == "header"):
        return None
    was = money_value(cell.get("export"))
    held = readings.get((page, field_name(line, name)), [])
    record = {
        "page": page,
        "line_index": line,
        "column": name,
        "export_value": str(cell.get("export", "")),
        "page_value": str(value),
        "difference": str(abs(value - (was or 0))),
        "evidence": correction_evidence(value, held, printed),
        "retained_readings": [str(v) for v in held],
        "moves_from_lines": lines_holding(rows, name, value, line),
    }
    if second is not None and record["evidence"] == "reviewer_only":
        regrade(record, second_read_correction(value, line, name, rows, second), SECOND_VENDOR)
    return "corrections", record


def lines_holding(rows, column, value, line):
    """The other countable lines already holding a correction's figure in its column.

    A shifted row is fixed by two corrections -- the figure onto its own line and
    the wrong figure off the line that holds it. Applied alone, the first counts
    the figure twice. Zero is left out: it repeats on every cancelled line and
    moves no money.
    """
    if not value or not line.isdigit():
        return []
    return sorted(
        (
            index
            for index, row in rows.items()
            if index != line and not excluded(row) and money_value(row.get(column)) == value
        ),
        key=int,
    )


def propose(reviews, lines_path, documents_path, readings, printed, second=None):
    """Turn every review into proposals, each carrying its evidence."""
    found = {"corrections": [], "rows_not_printed": [], "lost_values": []}
    witness = (second or {}).get("pages", {})
    for review in reviews:
        if schema_error(review):
            continue
        page, column = review["page"], review["money_column"]
        seen = printed.get(page)
        rows = {row["line_index"]: row for row in document_rows(lines_path, page)}
        countable = collections.Counter(line_money(rows.values(), column, countable_only=True))
        for cell in review.get("wrong_cells") or []:
            proposal = cell_proposal(
                page, cell, rows, column, countable, readings, seen, witness.get(page)
            )
            if proposal:
                kind, record = proposal
                found[kind].append(record)
        lost, _ = reconcile(review, lines_path, documents_path)
        for value in lost:
            found["lost_values"].append(
                {"page": page, "value": str(value), "evidence": lost_evidence(value, seen)}
            )
    return found


def tally(records, money_key):
    """Count and total each evidence grade of one kind of proposal."""
    grades = {}
    for record in records:
        grade = grades.setdefault(record["evidence"], {"count": 0, "money": Decimal(0)})
        grade["count"] += 1
        grade["money"] += abs(money_value(record[money_key]) or 0)
    return {k: {"count": v["count"], "money": str(v["money"])} for k, v in sorted(grades.items())}


def proposals(reviews, lines_path, documents_path, fields_csv, extractor_raw, second=None):
    """Everything the reviews found that an operator could authorize, graded."""
    printed = extractor_numbers(extractor_raw)
    found = propose(
        reviews, lines_path, documents_path, retained_readings(fields_csv), printed, second
    )
    kinds = (("corrections", "difference"), ("rows_not_printed", "value"), ("lost_values", "value"))
    artifact = {
        "artifact_type": PROPOSALS_ARTIFACT_TYPE,
        "decision_mode": "proposal_only",
        "pages_reviewed": len(reviews),
        "pages_the_extractor_read": len(printed),
        "summary": {kind: tally(found[kind], key) for kind, key in kinds},
        **found,
        "findings": [
            "Proposals only: nothing here changes a record, clears a control, or is "
            "read by any downstream command, and applying any of it needs an operator "
            "authorization.",
            "`extractor_text` means the figure appears somewhere in the independent "
            "extractor's text of the page, not necessarily in that cell.",
            "Only money columns are proposed; a reviewer's value for a text column is "
            "prose as often as a reading, and those stay findings in the reviews.",
        ],
    }
    if second is not None:
        artifact["second_read"] = {
            "handoff": second["handoff"],
            "engine": second["engine"],
            "vendor": second["vendor"],
            "reviewer_vendor": second["reviewer_vendor"],
            "pages_read": len(second["pages"]),
            "pages_with_no_line": second["pages_with_no_line"],
        }
        artifact["findings"].append(
            "`second_vendor` and `second_vendor_prints_it_fewer_times` rest on another "
            "vendor's reading of the page, never the reviewers' own; on a page that reading "
            "returned no line for, only a header correction can be regraded."
        )
    return artifact


def build_parser():
    """The command line: emit a packet, or check the reviews that came back."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lines-csv", required=True, help="Delivery line-grain CSV.")
    parser.add_argument("--documents-csv", required=True, help="Delivery document-grain CSV.")
    parser.add_argument("--images", default="", help="Directory of page images, for --packet.")
    parser.add_argument("--packet", default="", help="Emit the review packet for this page id.")
    parser.add_argument("--reviews", default="", help="Directory of returned review JSON files.")
    parser.add_argument("--out", default="", help="Write the summary here as JSON.")
    parser.add_argument(
        "--proposals",
        default="",
        help="Also write what the reviews propose, graded by evidence, here as JSON.",
    )
    parser.add_argument(
        "--fields-csv",
        default="",
        help="Delivery field-grain CSV, for every reading each engine returned.",
    )
    parser.add_argument(
        "--extractor-raw",
        default="",
        help="Retained independent-extractor responses, for the page's own text.",
    )
    parser.add_argument(
        "--second-read",
        default="",
        help=(
            "Retained provider handoff of another vendor's reading of the reviewed pages. "
            "A correction the reviewer alone supports becomes second_vendor where that "
            "reading holds the figure in its column more often than the export's other "
            "lines; a row the extractor prints as often as the export becomes "
            "second_vendor_prints_it_fewer_times where the reading holds every other "
            "figure in the column as often and this one fewer times. Needs --proposals "
            "and --reviewer-vendor."
        ),
    )
    parser.add_argument(
        "--reviewer-vendor",
        default="",
        help="The model vendor that wrote the reviews; a second read by that vendor is refused.",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress the printed summary.")
    apply_shared_help(parser)
    return parser


def report(summary):
    """Print what a reader needs to decide whether to trust the export."""
    print(f"pages reviewed: {summary['pages_reviewed']}")
    print(f"  reconciling with the export: {summary['pages_reconciling']}")
    print(f"  disagreeing -- needs a second look: {summary['pages_disagreeing']}")
    for row in summary["disagreements"][:20]:
        print(f"     {row['page']}: lost {row['lost'][:6]}; invented {row['invented'][:6]}")
    for row in summary["schema_errors"][:10]:
        print(f"     {row['page']}: {row['why']}")
    checked = summary["money_cells_checked"]
    if checked:
        correct = checked - summary["money_cells_wrong"]
        print(f"  money cells: {checked} checked, {summary['money_cells_wrong']} wrong")
        print(f"    of which misread: {summary['misread_cells']}")
        print(f"    of which rows the page never printed: {summary['spurious_rows']}")
        print(f"  accuracy over every checked cell: {100 * correct / checked:.2f}%")


def report_proposals(artifact):
    """Print how many proposals of each kind each grade of evidence carries."""
    second = artifact.get("second_read")
    if second:
        print(
            f"second read: {second['engine']} ({second['vendor']}) over "
            f"{second['pages_read']} pages, {len(second['pages_with_no_line'])} with no line"
        )
    for kind, grades in artifact["summary"].items():
        print(f"{kind}:")
        for grade, row in grades.items():
            print(f"  {grade}: {row['count']} ({row['money']})")


def main(argv=None):
    """Emit one page's packet, or summarize the reviews that came back."""
    args = build_parser().parse_args(argv)
    try:
        if args.packet:
            print(json.dumps(packet(args.lines_csv, args.documents_csv, args.images, args.packet)))
            return 0
        if not args.reviews:
            sys.exit("Give --packet to emit one page, or --reviews to check what came back.")
        if args.second_read and not args.proposals:
            sys.exit("--second-read regrades proposals; give --proposals as well.")
        second = None
        if args.second_read:
            second = load_second_read(args.second_read, args.reviewer_vendor)
        reviews, broken = load_reviews(args.reviews)
        summary = summarize(reviews, args.lines_csv, args.documents_csv)
        summary["schema_errors"].extend({"page": name, "why": why} for name, why in broken)
        if args.out:
            Path(args.out).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        if not args.quiet:
            report(summary)
        if args.proposals:
            artifact = proposals(
                reviews,
                args.lines_csv,
                args.documents_csv,
                args.fields_csv,
                args.extractor_raw,
                second,
            )
            Path(args.proposals).write_text(json.dumps(artifact, indent=2), encoding="utf-8")
            if not args.quiet:
                report_proposals(artifact)
    except (OSError, ValueError) as exc:
        sys.exit(f"Page review failed: {exc}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
