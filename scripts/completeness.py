#!/usr/bin/env python3
"""
Phase 4 -- Completeness controls.

Accuracy asks whether the numbers we captured are right. Completeness asks
whether all of them were captured. Completeness is the harder problem, because a
document that was never scanned leaves no trace at all -- there is nothing to
find, only an absence to infer.

Four inferences:

  sequence gaps   invoice numbering per vendor. 1001, 1002, 1004 means 1003
                  exists somewhere and we do not have it.

  calendar gaps   documents by vendor by month. A supplier used every month with
                  nothing in March 2023 means a missing scan batch, not a quiet
                  quarter.

  GL variance     extracted totals against the accounting system, by period and
                  vendor. If the books say $2.4M and the documents account for
                  $2.1M, there is $300K of paperwork missing. REPORT IT. Never
                  adjust figures to close the gap -- a plugged variance converts
                  a known unknown into an invisible error.

  aging closure   every invoice ends in a payment, credit, write-off, or explicit
                  open status. Anything that just stops is quantified.

Output is the Completeness Report, which travels with the data. Every chart built
on this dataset states the completeness figures it rests on.

Usage:
    python completeness.py proofed.json --gl gl_export.csv --out completeness.json
    python completeness.py proofed.json --payments payments.json --out completeness.json
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime

from cli_help import apply_shared_help
from document_value import document_value
from runtime_config import env_bool, env_value, load_project_env
from side_channel_input import first_populated, normalized_columns, rejection

# A vendor needs at least this many invoices before sequence inference is
# meaningful. Below it, apparent gaps are usually just sparse usage.
MIN_FOR_SEQUENCE = 5
MIN_MONTHS_FOR_CALENDAR = 4

# Gaps larger than this in a single run usually mean the vendor uses a shared
# sequence across customers, not that thousands of documents are missing.
MAX_PLAUSIBLE_GAP = 500
DATE_ORDERS = ("month_first", "day_first", "iso_only")


def num(entry, default=0.0):
    """Parse a monetary value without inferring a unit or separator convention."""
    if entry is None:
        return default
    if isinstance(entry, dict):
        entry = entry.get("value")
    if entry is None:
        return default
    if isinstance(entry, (int, float)) and not isinstance(entry, bool):
        return float(entry)
    s = str(entry).strip().replace(",", "").replace("$", "").replace(" ", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return default


def get(rec, key, default=None):
    """Read a document-level field from the record or its header."""
    header = rec.get("header", {})
    for src in (rec, header):
        if key in src:
            v = src[key]
            return v.get("value") if isinstance(v, dict) else v
    return default


def split_invoice_number(raw):
    """
    Split into (prefix, numeric, width). A vendor using INV-0042 and INV-0043 has
    prefix 'INV-', numeric 42/43, width 4. Comparing raw strings misses gaps.
    """
    if raw is None:
        return None, None, None
    s = str(raw).strip()
    m = re.search(r"^(.*?)(\d+)$", s)
    if not m:
        return s, None, None
    return m.group(1), int(m.group(2)), len(m.group(2))


# A month written as a word carries no ambiguity to resolve. "May 31, 2024" is
# 2024-05 under every convention on earth, which is why it is read before the
# date-order policy is consulted rather than being governed by it: that policy
# exists for `05/03/2023`, where two readings genuinely compete.
#
# Every date on a real 18-page commission corpus was printed this way, and the
# parser handled only numeric forms -- so all 18 documents were reported as
# having no parseable date and excluded from every calendar and GL check.
MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
# Matches a spelled month beside a four-digit year in either order, so both
# "May 31, 2024" and "31 May 2024" resolve, and so does a bare "May 2024".
NAMED_MONTH = re.compile(
    r"(?:(?P<before>\d{4})\D{1,3})?(?P<name>[A-Za-z]{3,9})\.?\b\D{0,3}"
    r"(?:\d{1,2}\D{1,3})?(?P<after>\d{4})?"
)


def named_month(text):
    """Return YYYY-MM for a date whose month is a word, or None.

    A month name this does not recognise returns None rather than a guess, and a
    string with no four-digit year returns None rather than assuming a year.
    """
    match = NAMED_MONTH.search(str(text))
    if not match:
        return None
    month = MONTH_NAMES.get(match.group("name")[:3].casefold())
    year = match.group("after") or match.group("before")
    if month is None or not year:
        return None
    return f"{year}-{month:02d}"


def date_order():
    """Return the configured reading for an ambiguous numeric date."""
    value = env_value("COMPLETENESS_DATE_ORDER", "month_first")
    if value not in DATE_ORDERS:
        raise ValueError("COMPLETENESS_DATE_ORDER must be one of: " + ", ".join(DATE_ORDERS))
    return value


def month_of(date_str, order=None):
    """Bucket a date to YYYY-MM under an explicit, configured date order.

    ``05/03/2023`` is May in the United States and March nearly everywhere else.
    The historical behavior was month-first with no declaration, which silently
    re-bucketed a non-US export. The reading is now a stated policy:
    ``month_first`` keeps the historical behavior, ``day_first`` reads the other
    convention, and ``iso_only`` refuses to guess.
    """
    if not date_str:
        return None
    s = str(date_str).strip()
    m = re.match(r"(\d{4})[-/](\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    # Before the ambiguity policy, because a named month has no ambiguity --
    # including under iso_only, which refuses to *guess* rather than refusing to
    # read an unambiguous date.
    spelled = named_month(s)
    if spelled:
        return spelled
    order = date_order() if order is None else order
    if order == "iso_only":
        return None
    m = re.match(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)
    if m:
        month = m.group(2) if order == "day_first" else m.group(1)
        if not 1 <= int(month) <= 12:
            return None
        return f"{m.group(3)}-{int(month):02d}"
    return None


def month_range(start, end):
    """Return every ISO year-month between two dates, inclusive."""
    sy, sm = (int(x) for x in start.split("-"))
    ey, em = (int(x) for x in end.split("-"))
    out = []
    y, mo = sy, sm
    while (y, mo) <= (ey, em):
        out.append(f"{y}-{mo:02d}")
        mo += 1
        if mo > 12:
            mo, y = 1, y + 1
    return out


def sequence_gaps(docs, coverage=None):
    """Infer missing invoice numbers, recording every series left unanalyzed.

    "No sequence gaps found" and "no sequence analysis was possible" are different
    answers, and the report used to be unable to tell them apart. Series are also
    grouped by ``(vendor, prefix)`` rather than by zero-padding width, so a
    rollover from INV-999 to INV-1000 stays one series instead of splitting into
    two that may each fall below the analysis threshold and vanish.
    """
    coverage = [] if coverage is None else coverage
    by_series = defaultdict(list)
    for d in docs:
        vendor = d["vendor"]
        prefix, n, width = split_invoice_number(d["invoice_number"])
        if n is None:
            coverage.append(
                {
                    "vendor": vendor,
                    "series_prefix": prefix,
                    "documents": 1,
                    "reason": "invoice_number_has_no_numeric_suffix",
                    "disposition": "client_review_required",
                }
            )
            continue
        by_series[(vendor, prefix)].append((n, d, width))

    results = []
    for (vendor, prefix), entries in sorted(by_series.items(), key=lambda kv: str(kv[0])):
        width = max(item[2] or 0 for item in entries) or None
        if len(entries) < MIN_FOR_SEQUENCE:
            coverage.append(
                {
                    "vendor": vendor,
                    "series_prefix": prefix,
                    "documents": len(entries),
                    "reason": "series_below_minimum_for_sequence_inference",
                    "minimum_required": MIN_FOR_SEQUENCE,
                    "disposition": "client_review_required",
                }
            )
            continue
        nums = sorted({n for n, _, _ in entries})
        lo, hi = nums[0], nums[-1]
        present = set(nums)
        missing = [n for n in range(lo, hi + 1) if n not in present]
        if not missing:
            continue

        span = hi - lo + 1
        implausible = len(missing) > MAX_PLAUSIBLE_GAP or len(missing) > span * 0.8

        runs, start, prev = [], None, None
        for n in missing:
            if start is None:
                start = prev = n
            elif n == prev + 1:
                prev = n
            else:
                runs.append((start, prev))
                start = prev = n
        # ``missing`` is non-empty here, so the loop always initializes a run.
        # Appending directly makes that invariant explicit and removes a dead
        # conditional from this control calculation.
        runs.append((start, prev))

        def fmt(number, prefix=prefix, width=width):
            suffix = f"{number:0{width}d}" if width else str(number)
            return f"{prefix or ''}{suffix}"

        results.append(
            {
                "vendor": vendor,
                "series_prefix": prefix,
                "observed_range": [fmt(lo), fmt(hi)],
                "present": len(nums),
                "missing_count": len(missing),
                "missing_pct_of_range": round(100.0 * len(missing) / span, 1),
                "missing_runs_truncated": max(len(runs) - 50, 0),
                "missing_runs": [
                    {"from": fmt(a), "to": fmt(b), "count": b - a + 1} for a, b in runs[:50]
                ],
                "likely_shared_sequence": implausible,
                "interpretation": (
                    "Gap density suggests this vendor's numbering is shared across "
                    "their whole customer base, not dedicated to this client. Treat "
                    "as inconclusive rather than as missing documents."
                    if implausible
                    else "Documents in these ranges exist and are not in the corpus. "
                    "Check for unscanned batches."
                ),
            }
        )
    results.sort(key=lambda r: -r["missing_count"])
    return results


def calendar_gaps(docs, coverage=None):
    """Infer empty months, recording every vendor left unanalyzed."""
    coverage = [] if coverage is None else coverage
    by_vendor = defaultdict(lambda: defaultdict(lambda: {"count": 0, "value": 0.0}))
    undated = Counter()
    for d in docs:
        m = d["month"]
        if not m:
            undated[d["vendor"]] += 1
            continue
        cell = by_vendor[d["vendor"]][m]
        cell["count"] += 1
        cell["value"] += d["amount"]
    for vendor, count in sorted(undated.items()):
        coverage.append(
            {
                "vendor": vendor,
                "documents": count,
                "reason": "document_date_not_parseable",
                "disposition": "client_review_required",
            }
        )

    results = []
    for vendor, months in sorted(by_vendor.items()):
        keys = sorted(months)
        if len(keys) < MIN_MONTHS_FOR_CALENDAR:
            coverage.append(
                {
                    "vendor": vendor,
                    "months_observed": len(keys),
                    "reason": "vendor_below_minimum_months_for_calendar_inference",
                    "minimum_required": MIN_MONTHS_FOR_CALENDAR,
                    "disposition": "client_review_required",
                }
            )
            continue
        full = month_range(keys[0], keys[-1])
        empty = [m for m in full if m not in months]
        counts = sorted(months[m]["count"] for m in keys)
        median = counts[len(counts) // 2]
        thin = [m for m in keys if median >= 4 and months[m]["count"] <= max(1, median * 0.25)]

        if not empty and not thin:
            continue
        results.append(
            {
                "vendor": vendor,
                "active_period": [keys[0], keys[-1]],
                "months_observed": len(keys),
                "months_in_period": len(full),
                "missing_months": empty,
                "thin_months": thin,
                "median_docs_per_month": median,
                "interpretation": (
                    f"{len(empty)} month(s) with no documents inside an otherwise "
                    "continuous relationship. Probable unscanned batch -- verify before "
                    "reading this as a lapse in trading. This is the most common source "
                    "of false churn findings."
                    if empty
                    else "Months present but unusually thin relative to this vendor's own "
                    "baseline. Possible partial batch."
                ),
            }
        )
    results.sort(key=lambda r: -(len(r["missing_months"])))
    return results


def iso_month_prefix(value):
    """Accept only a literal ISO ``YYYY-MM`` prefix as a period bucket."""
    match = re.match(r"(\d{4})-(\d{2})", str(value or "").strip())
    return (
        f"{match.group(1)}-{match.group(2)}" if match and 1 <= int(match.group(2)) <= 12 else None
    )


def load_payments(path):
    """Read a payment or remittance export as CSV or JSON.

    The GL export beside this one has always accepted both, while payments
    accepted JSON alone and met a client's CSV remittance with a raw decoder
    traceback. A payment export is whatever the client's system emits, and CSV is
    what it usually emits.
    """
    path = os.fspath(path)
    if path.lower().endswith(".json"):
        with open(path) as fh:
            data = json.load(fh)
        rows = data.get("payments", data) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise ValueError("payments JSON must be a list or an object with a payments list")
        return rows
    with open(path, newline="") as fh:
        rows = [normalized_columns(row) for row in csv.DictReader(fh)]
    if not rows:
        raise ValueError(f"payments export contains no rows: {path}")
    return rows


def load_gl(path):
    """
    Accepts CSV with columns period, vendor (optional), amount.
    Also accepts JSON [{period, vendor, amount}, ...].

    Returns ``(buckets, rejected_rows)``. Every row that could not be bucketed or
    whose amount could not be parsed is retained with its raw content and a
    reason code; no caller may discard it.
    """
    path = os.fspath(path)
    rows = []
    if path.lower().endswith(".json"):
        with open(path) as fh:
            data = json.load(fh)
        rows = data.get("entries", data) if isinstance(data, dict) else data
    else:
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                rows.append(r)

    out = defaultdict(lambda: defaultdict(float))
    rejected = []
    for index, r in enumerate(rows):
        lower = normalized_columns(r)
        period = lower.get("period") or lower.get("month") or lower.get("date") or ""
        # The historical fallback accepted any 7-character prefix, so "Q1-2026"
        # became a month bucket and inflated the baseline with a period that does
        # not exist. Only an ISO year-month prefix is a period.
        m = month_of(period) or iso_month_prefix(period)
        raw_amount = first_populated(lower.get("amount"), lower.get("total"))
        amount = num(raw_amount, None)
        # A dropped or zero-coerced GL row shrinks the authoritative total, which
        # shrinks the reported variance. That is the one direction this control
        # must never move on its own -- retain the row as an explicit rejection.
        reason = (
            "gl_row_period_unparseable"
            if not m
            else "gl_row_amount_unparseable"
            if amount is None
            else None
        )
        if reason:
            rejected.append(rejection(index, reason, raw_period=period, raw_amount=raw_amount))
            continue
        vendor = str(
            lower.get("vendor") or lower.get("party") or lower.get("account") or "ALL"
        ).strip()
        out[m][vendor] += amount
    return out, rejected


def gl_variance(docs, gl, tolerance_pct, rejected_rows=()):
    """Compare document totals with the authoritative ledger, by period and vendor.

    Reports variance and never plugs it. While any GL row is rejected the
    baseline is incomplete, so no period is declared within tolerance."""
    extracted = defaultdict(lambda: defaultdict(float))
    for d in docs:
        if d["month"]:
            extracted[d["month"]][d["vendor"]] += d["amount"]

    periods = sorted(set(gl) | set(extracted))
    by_period, vendor_rows = [], []
    total_gl = total_ex = 0.0

    for p in periods:
        gl_total = sum(gl.get(p, {}).values())
        ex_total = sum(extracted.get(p, {}).values())
        total_gl += gl_total
        total_ex += ex_total
        var = ex_total - gl_total
        pct = (100.0 * var / gl_total) if gl_total else None
        by_period.append(
            {
                "period": p,
                "gl_total": round(gl_total, 2),
                "extracted_total": round(ex_total, 2),
                "variance": round(var, 2),
                "variance_pct": round(pct, 2) if pct is not None else None,
                "within_tolerance": (abs(pct) <= tolerance_pct) if pct is not None else None,
            }
        )

        gl_vendors = gl.get(p, {})
        if set(gl_vendors) - {"ALL"}:
            for vendor in sorted(set(gl_vendors) | set(extracted.get(p, {}))):
                if vendor == "ALL":
                    continue
                g = gl_vendors.get(vendor, 0.0)
                e = extracted.get(p, {}).get(vendor, 0.0)
                if abs(e - g) < 0.01:
                    continue
                vendor_rows.append(
                    {
                        "period": p,
                        "vendor": vendor,
                        "gl_total": round(g, 2),
                        "extracted_total": round(e, 2),
                        "variance": round(e - g, 2),
                    }
                )

    rejected_rows = list(rejected_rows)
    if rejected_rows:
        # The GL baseline is incomplete, so no period can be declared within
        # tolerance against it.
        for row in by_period:
            row["within_tolerance"] = None
    overall_var = total_ex - total_gl
    return {
        "status": "completed_with_rejected_gl_rows" if rejected_rows else "completed",
        "periods_compared": len(periods),
        "gl_total": round(total_gl, 2),
        "extracted_total": round(total_ex, 2),
        "overall_variance": round(overall_var, 2),
        "overall_variance_pct": round(100.0 * overall_var / total_gl, 2) if total_gl else None,
        "tolerance_pct": tolerance_pct,
        "periods_outside_tolerance": [
            b["period"] for b in by_period if b["within_tolerance"] is False
        ],
        "gl_rows_rejected": len(rejected_rows),
        "rejected_gl_rows": rejected_rows,
        "by_period": by_period,
        "largest_vendor_variances": sorted(vendor_rows, key=lambda r: -abs(r["variance"]))[:50],
        "rule": (
            "Variance is reported, never plugged. A rejected GL row is not "
            "silently excluded from the baseline: while any row is rejected the "
            "GL total is incomplete and no period is declared within tolerance. "
            "A difference means "
            "documents are missing or extraction is incomplete -- both are "
            "findings. Adjusting figures to make totals agree destroys the "
            "only signal that says so."
        ),
    }


def aging_closure(docs, payments):
    """
    Every invoice must terminate in a payment, credit, write-off, or an explicit
    open status. Anything that just stops is quantified, not ignored.
    """
    invoice_keys = defaultdict(list)
    for doc in docs:
        invoice = str(doc.get("invoice_number") or "").strip()
        if invoice:
            invoice_keys[invoice].append(
                (
                    str(doc.get("vendor") or "").strip().casefold(),
                    invoice,
                    str(doc.get("currency") or "").strip().upper(),
                )
            )

    applied = defaultdict(float)
    ambiguous_applications = []
    unusable_applications = []

    def apply_payment_record(record, parent=None):
        """Apply payment and remittance evidence to open items and report closure."""
        invoice = str(record.get("invoice_number") or "").strip()
        if not invoice:
            # Rule 2: a payment row this control cannot key is not a row it may
            # forget. Dropping it silently reports an invoice as open while the
            # client's own export says it was paid -- an understatement of
            # closure produced by the control rather than by the evidence.
            unusable_applications.append(
                {
                    "reason": "payment_row_has_no_invoice_number",
                    "available_columns": sorted(str(key) for key in record),
                    "raw_record": {str(key): str(value) for key, value in record.items()},
                    "disposition": "client_review_required",
                }
            )
            return
        parent = parent or {}
        vendor = (
            str(
                record.get("vendor")
                or record.get("vendor_name")
                or record.get("payee")
                or parent.get("vendor")
                or parent.get("vendor_name")
                or parent.get("payee")
                or ""
            )
            .strip()
            .casefold()
        )
        currency = str(record.get("currency") or parent.get("currency") or "").strip().upper()
        candidates = [
            key
            for key in invoice_keys.get(invoice, [])
            if (not vendor or key[0] == vendor) and (not currency or key[2] == currency)
        ]
        if len(candidates) != 1:
            ambiguous_applications.append(
                {
                    "invoice_number": invoice,
                    "vendor": vendor,
                    "currency": currency,
                    "candidate_count": len(candidates),
                }
            )
            return
        applied[candidates[0]] += num(
            record.get("amount_applied") or record.get("total_paid") or record.get("amount")
        )

    for p in payments:
        for line in p.get("applied") or p.get("applications") or []:
            apply_payment_record(line, p)
        if not (p.get("applied") or p.get("applications")):
            apply_payment_record(p)

    open_items, closed, partial = [], 0, []
    for d in docs:
        if d["doc_type"] not in (
            "commercial_invoice",
            "carrier_freight_bill",
            "invoice",
            "unknown",
        ):
            continue
        inv = str(d["invoice_number"] or "").strip()
        amount = d["amount"]
        if amount <= 0:
            continue
        key = (
            str(d.get("vendor") or "").strip().casefold(),
            inv,
            str(d.get("currency") or "").strip().upper(),
        )
        paid = applied.get(key, 0.0) + d["credits"]
        status = d["explicit_status"]

        if status in ("paid", "written_off", "credited", "open", "disputed"):
            closed += 1
            continue
        if abs(paid - amount) <= 0.01:
            closed += 1
        elif 0 < paid < amount:
            partial.append(
                {
                    "invoice_number": inv,
                    "vendor": d["vendor"],
                    "invoiced": round(amount, 2),
                    "applied": round(paid, 2),
                    "balance": round(amount - paid, 2),
                }
            )
        else:
            open_items.append(
                {
                    "invoice_number": inv,
                    "vendor": d["vendor"],
                    "date": d["date"],
                    "amount": round(amount, 2),
                }
            )

    open_items.sort(key=lambda x: -x["amount"])
    partial.sort(key=lambda x: -x["balance"])
    return {
        "status": "completed",
        "invoices_examined": closed + len(partial) + len(open_items),
        "closed": closed,
        "partially_applied": len(partial),
        "unresolved": len(open_items),
        "unresolved_value": round(sum(i["amount"] for i in open_items), 2),
        "partial_balance_value": round(sum(p["balance"] for p in partial), 2),
        "ambiguous_payment_applications": ambiguous_applications,
        "ambiguous_payment_application_count": len(ambiguous_applications),
        "unusable_payment_applications": unusable_applications,
        "unusable_payment_application_count": len(unusable_applications),
        "largest_unresolved": open_items[:50],
        "largest_partial": partial[:50],
        "interpretation": (
            "Unresolved items are invoices with no payment, credit, write-off, or "
            "explicit open status. Each is either a missing remittance document or "
            "a genuinely open receivable -- distinguishing the two usually requires "
            "the bank statement. Handwritten cheque numbers on the invoice itself "
            "close a large share of these; check that Phase 3H extracted them."
        ),
    }


def extract_docs(records):
    """Collect the document-level events the completeness controls compare against."""
    docs = []
    for rec in records:
        # A commission statement names its issuer in brand_name and dates itself
        # in statement_date; neither chain below looked at either. On a real
        # 18-page commission corpus that produced one vendor -- "UNKNOWN" -- and
        # 18 documents excluded from every calendar and GL check for having no
        # parseable date, while `statement_date: "May 31, 2024"` sat in each
        # header. The invoice vocabulary is kept first so nothing about an
        # invoice changes; the commission fields are consulted only when it is
        # exhausted.
        vendor = (
            get(rec, "seller_name")
            or get(rec, "vendor_name")
            or get(rec, "carrier_name")
            or get(rec, "payee_name")
            # The brand issues the statement and pays the commission, so it is
            # the series a missing-statement gap is measured against. The dealer
            # receives it and is the same party on every document here.
            or get(rec, "brand_name")
            or "UNKNOWN"
        )
        date = (
            get(rec, "invoice_date")
            or get(rec, "bill_date")
            or get(rec, "memo_date")
            or get(rec, "document_date")
            or get(rec, "statement_date")
            # Falls back to the period it covers when the statement is undated.
            or get(rec, "period_end")
        )
        docs.append(
            {
                "document_id": rec.get("document_id", "unknown"),
                "doc_type": rec.get("document_type", "unknown"),
                "vendor": str(vendor).strip(),
                "invoice_number": get(rec, "invoice_number") or get(rec, "pro_number"),
                "date": date,
                "month": month_of(date),
                "amount": document_value(rec)["value"],
                "currency": str(get(rec, "currency") or "").strip().upper(),
                # `credits_applied` and `payment_status` are not
                # `extraction_schema.HEADER_FIELDS` names, so no extractor emits
                # them and both of these were permanently 0 and "" -- aging
                # closure ran on empty inputs for a whole corpus. The schema
                # names come first; the originals stay for a client-amended
                # record that carries them.
                "credits": abs(num(get(rec, "credit_amount") or get(rec, "credits_applied"))),
                "explicit_status": str(
                    get(rec, "payment_status")
                    or get(rec, "document_status")
                    or get(rec, "approval_status")
                    or ""
                )
                .strip()
                .lower(),
            }
        )
    return docs


def main():
    # Without this the documented COMPLETENESS_* settings would be readable only
    # from the process environment, never from the project `.env`.
    load_project_env()
    ap = argparse.ArgumentParser(description="Produce the Completeness Report.")
    ap.add_argument("input", help="proofed.json, consensus.json, or a record list")
    ap.add_argument("--gl", default=None, help="GL export: CSV or JSON")
    ap.add_argument(
        "--payments",
        default=None,
        help="Client payment or remittance export, CSV or JSON. A row that cannot be "
        "keyed to an invoice is registered as unusable, never dropped.",
    )
    ap.add_argument("--out", default="completeness.json")
    ap.add_argument(
        "--gl-tolerance-pct",
        type=float,
        default=2.0,
        help="per-period variance tolerance (default 2%%)",
    )
    ap.add_argument("--quiet", action="store_true")
    apply_shared_help(ap)
    args = ap.parse_args()

    with open(args.input) as fh:
        data = json.load(fh)
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = next(
            (
                data[k]
                for k in ("documents", "results", "attributions", "records")
                if isinstance(data.get(k), list)
            ),
            None,
        ) or [data]
    else:
        records = []
    if not records:
        sys.exit("No records found in input.")

    docs = extract_docs(records)
    payments = []
    if args.payments:
        try:
            payments = load_payments(args.payments)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            sys.exit(f"Completeness failed reading payments: {exc}")

    sequence_coverage, calendar_coverage = [], []
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "corpus": {
            "documents": len(docs),
            "total_value": round(sum(d["amount"] for d in docs), 2),
            "vendors": len({d["vendor"] for d in docs}),
            "date_range": [
                min((d["month"] for d in docs if d["month"]), default=None),
                max((d["month"] for d in docs if d["month"]), default=None),
            ],
            "documents_without_date": sum(1 for d in docs if not d["month"]),
        },
        "sequence_gaps": sequence_gaps(docs, sequence_coverage),
        "calendar_gaps": calendar_gaps(docs, calendar_coverage),
        "inference_coverage": {
            "sequence_not_analyzed": sequence_coverage,
            "calendar_not_analyzed": calendar_coverage,
        },
    }

    if args.gl:
        gl_buckets, gl_rejected = load_gl(args.gl)
        report["gl_variance"] = gl_variance(docs, gl_buckets, args.gl_tolerance_pct, gl_rejected)
    else:
        report["gl_variance"] = {
            "status": "not_run",
            "note": (
                "No GL export supplied. Without a reconciliation target, "
                "completeness can be asserted but not measured -- the corpus "
                "can only be compared against itself. Obtain the GL export "
                "before the Phase 4 gate."
            ),
        }

    report["aging_closure"] = (
        aging_closure(docs, payments)
        if payments
        else {
            "status": "not_run",
            "note": (
                "No payment or remittance data supplied. Aging closure cannot be "
                "assessed, so unresolved receivables cannot be distinguished from "
                "missing remittance documents."
            ),
        }
    )

    findings = []
    seq_missing = sum(
        s["missing_count"] for s in report["sequence_gaps"] if not s["likely_shared_sequence"]
    )
    if seq_missing:
        findings.append(
            f"{seq_missing} documents are implied by invoice numbering but absent "
            f"from the corpus, across {len([s for s in report['sequence_gaps'] if not s['likely_shared_sequence']])} vendor series."
        )
    not_analyzed = len(report["inference_coverage"]["sequence_not_analyzed"]) + len(
        report["inference_coverage"]["calendar_not_analyzed"]
    )
    if not_analyzed:
        findings.append(
            f"{not_analyzed} vendor series or vendor-months were not analyzed for gaps "
            "(too few documents, an unparseable date, or an invoice number with no "
            "numeric suffix). 'No gaps found' does not cover this population; it is "
            "enumerated in inference_coverage."
        )
    cal_missing = sum(len(c["missing_months"]) for c in report["calendar_gaps"])
    if cal_missing:
        findings.append(
            f"{cal_missing} vendor-months are empty inside otherwise continuous "
            "trading relationships -- probable unscanned batches. Cross-check every "
            "churn finding in Phase 5 against this list before presenting it."
        )
    gv = report["gl_variance"]
    if gv.get("overall_variance") is not None:
        findings.append(
            f"GL variance: extracted ${gv['extracted_total']:,.2f} vs ledger "
            f"${gv['gl_total']:,.2f}, a difference of ${gv['overall_variance']:,.2f} "
            f"({gv['overall_variance_pct']}%). Report this figure on the face of "
            "every analysis. Do not plug it."
        )
        if gv["periods_outside_tolerance"]:
            findings.append(
                f"{len(gv['periods_outside_tolerance'])} periods exceed the "
                f"{args.gl_tolerance_pct}% tolerance: "
                f"{', '.join(gv['periods_outside_tolerance'][:12])}. The Phase 4 "
                "gate is not met until these are explained or accepted with cause."
            )
    ac = report["aging_closure"]
    if ac.get("unresolved"):
        findings.append(
            f"{ac['unresolved']} invoices worth ${ac['unresolved_value']:,.2f} "
            "terminate in nothing -- no payment, credit, write-off, or explicit open "
            "status."
        )
    if ac.get("partially_applied"):
        findings.append(
            f"{ac['partially_applied']} invoices have partially applied payments with "
            f"${ac['partial_balance_value']:,.2f} still unresolved."
        )
    if ac.get("ambiguous_payment_application_count"):
        findings.append(
            f"{ac['ambiguous_payment_application_count']} payment applications could not "
            "be matched uniquely by vendor, invoice number, and currency."
        )
    if report["corpus"]["documents_without_date"]:
        findings.append(
            f"{report['corpus']['documents_without_date']} documents have no "
            "parseable date and are excluded from calendar and GL analysis. They "
            "are not counted as complete."
        )

    gate_reasons = []
    if gv.get("status") not in ("completed", "completed_with_rejected_gl_rows"):
        gate_reasons.append("gl_reconciliation_not_run")
    elif not gv.get("periods_compared") or any(
        row.get("within_tolerance") is None for row in gv.get("by_period", [])
    ):
        gate_reasons.append("gl_reconciliation_not_measurable")
    if gv.get("gl_rows_rejected"):
        gate_reasons.append("gl_rows_rejected")
    # Whether a population too small to infer from should block delivery is a
    # client scoping decision, so it is an explicit opt-in rather than a default.
    if env_bool("COMPLETENESS_BLOCK_ON_UNANALYZED_POPULATION", False) and (
        report["inference_coverage"]["sequence_not_analyzed"]
        or report["inference_coverage"]["calendar_not_analyzed"]
    ):
        gate_reasons.append("inference_population_not_analyzed")
    if gv.get("periods_outside_tolerance"):
        gate_reasons.append("gl_periods_outside_tolerance")
    if ac.get("status") != "completed":
        gate_reasons.append("aging_closure_not_run")
    if ac.get("unresolved") or ac.get("partially_applied"):
        gate_reasons.append("aging_closure_unresolved")
    if ac.get("ambiguous_payment_application_count"):
        gate_reasons.append("payment_application_ambiguous")
    if seq_missing:
        gate_reasons.append("sequence_gaps")
    if cal_missing:
        gate_reasons.append("calendar_gaps")
    if report["corpus"]["documents_without_date"]:
        gate_reasons.append("documents_without_parseable_date")
    report["gate_reasons"] = gate_reasons
    report["findings"] = findings or ["No completeness issues detected."]
    report["gate_status"] = "blocked" if gate_reasons else "clear"

    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=2)

    if not args.quiet:
        c = report["corpus"]
        print(
            f"Documents: {c['documents']}   Value: ${c['total_value']:,.2f}   "
            f"Vendors: {c['vendors']}"
        )
        print(f"Period: {c['date_range'][0]} to {c['date_range'][1]}")
        print(f"Gate status: {report['gate_status'].upper()}")
        for f in report["findings"]:
            print(f"  - {f}")
        print(f"\nWritten to {args.out}")


if __name__ == "__main__":
    main()
