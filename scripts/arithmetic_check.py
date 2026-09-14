#!/usr/bin/env python3
"""
Phase 3 -- Arithmetic self-proof.

An invoice contains its own answer key. Line extensions should reconcile to line
totals, lines should sum to the subtotal, and subtotal plus tax plus freight plus
accessorials should equal the stated total. If a document does not add up,
something was read wrong -- and we know it, regardless of what the OCR confidence
said.

This is the single most effective check in the pipeline. It catches errors no
confidence score flags, because OCR confidence is well calibrated on glyph shape
and poorly calibrated on field assignment: the engine is sure about the digit it
read, not about which field it belongs to.

It is also the principal safety net in Branch B (grayscale/bilevel scans), where
handwriting detection recall is materially lower. An undetected handwritten
quantity or price override will usually break the arithmetic, and that failure
catches what detection missed.

Where a handwritten amendment exists, the proof runs against the AMENDED figure,
because that is the operative value -- see references/handwriting.md.

Usage:
    python arithmetic_check.py consensus.json --out proofed.json
    python arithmetic_check.py records.json --tolerance 0.02 --out proofed.json
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from document_value import separator_convention_unclear
from runtime_config import env_bool, env_float, load_project_env

DEFAULT_TOLERANCE = 0.01  # per-check absolute tolerance, in currency units
PER_LINE_ROUNDING_ALLOWANCE = 0.01
COMMISSION_DOCUMENT_TYPES = {"commission_statement", "commission_report"}

# A commission line carries its own answer key the way an invoice line does:
# commissionable amount x rate = commission. Two things make it usable as proof
# where a second reader is unavailable -- it needs no other engine, and it
# decides, telling you which of two disagreeing readings is coherent.
#
# The rate's unit is not fixed in the wild. This corpus writes 10% as "10.00" on
# some layouts and 0.75 (meaning 75%) on others, so a number below 1 is
# genuinely ambiguous and magnitude must not settle it -- inferring a unit from
# size is exactly what this pipeline forbids. Arithmetic settles it instead: the
# reading that reconciles is the reading the document used. Measured over 716
# pages, the unit never varied within a page (0 of 337 mixed), so it is
# established once per document from the lines that prove themselves and then
# applied to the rest. A document whose lines disagree about the unit is refused
# rather than resolved.
RATE_UNITS = (("percent", 100.0), ("fraction", 1.0))


def num(entry, default=None):
    """Pull a number out of {value,...}, a bare scalar, or a formatted string."""
    if entry is None:
        return default
    if isinstance(entry, dict):
        entry = entry.get("value")
    if entry is None:
        return default
    if isinstance(entry, bool):
        return default
    if isinstance(entry, (int, float)):
        return float(entry)
    raw = str(entry).strip()
    # A decimal comma or a space between digits is a separator convention this
    # cannot decide. Guessing turns `16 430,72` into 1,643,072.
    if separator_convention_unclear(raw.replace("$", "")):
        return default
    s = raw.replace(",", "").replace("$", "").replace(" ", "")
    if s.startswith("(") and s.endswith(")"):  # accounting negatives
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return default


def text(entry):
    """Pull a string out of {value,...} or a bare scalar, the way ``num`` does.

    A provider record carries ``model_document_type`` as a bare string; consensus
    reconciles it and hands on the same fact as a ``{value, confidence, source}``
    field object. ``str()`` over that object yields the repr of a dict, which
    matches no document type, so 18 commission statements were reported
    ``not_provable`` -- "we tried and could not prove this" -- when the truth was
    ``not_applicable``: invoice arithmetic does not apply to them and
    allocation_policy.py is the lane that proves their formula. The wrong status
    reads as conservative, which is why it survived a full run unquestioned.
    """
    if isinstance(entry, dict):
        entry = entry.get("value")
    return "" if entry is None else str(entry).strip()


def rollup_tolerance(tol, summed_row_count):
    """Scale a rollup tolerance by the rows it sums, never by unrelated rows."""
    return tol + PER_LINE_ROUNDING_ALLOWANCE * max(summed_row_count - 1, 0)


def field_source(entry):
    """Return whether a field was printed or handwritten, defaulting to printed.

    An unmarked field is treated as printed rather than credited with the
    stricter recognition a handwritten reading has to pass.
    """
    if isinstance(entry, dict):
        return entry.get("source", "printed")
    return "printed"


def effective(record, path, default=None):
    """
    Resolve a field to its operative value, applying any handwritten amendment.

    The precedence rule: printed values are the baseline, handwritten values are
    amendments with a later effective time. Both remain stored; this returns the
    one that currently governs.
    """
    amendments = record.get("amendments") or []
    for a in amendments:
        if a.get("target_column") == path or a.get("field") == path:
            v = num(a.get("amended_value"))
            if v is not None:
                return v, a.get("amendment_source", "handwritten")
    header = record.get("header", record)
    entry = header.get(path, record.get(path))
    return num(entry, default), field_source(entry)


def check_lines(record, tol):
    """quantity x unit_price == extended_amount, per line."""
    results, computed_subtotal, missing = [], 0.0, 0
    for i, line in enumerate(record.get("lines") or []):
        # line_number may arrive as a bare scalar or as a {value,...} object
        # depending on whether the record came straight from an engine or via
        # consensus reconstruction.
        raw_ln = line.get("line_number", i + 1)
        if isinstance(raw_ln, dict):
            raw_ln = raw_ln.get("value", i + 1)
        try:
            ln = int(raw_ln)
        except (TypeError, ValueError):
            ln = i + 1
        qty = num(line.get("quantity"))
        price = num(line.get("unit_price"))
        ext = num(line.get("extended_amount"))
        disc = num(line.get("discount"), 0.0) or 0.0

        hw = any(
            field_source(line.get(k)) == "handwritten"
            for k in ("quantity", "unit_price", "extended_amount")
        )

        for a in record.get("amendments") or []:
            if a.get("target_line") == ln:
                col = a.get("target_column")
                v = num(a.get("amended_value"))
                if v is not None:
                    if col == "quantity":
                        qty, hw = v, True
                    elif col == "unit_price":
                        price, hw = v, True
                    elif col == "extended_amount":
                        ext, hw = v, True

        if ext is not None:
            computed_subtotal += ext

        if qty is None or price is None or ext is None:
            missing += 1
            results.append(
                {
                    "line": ln,
                    "check": "line_extension",
                    "status": "not_applicable",
                    "reason": "missing quantity, unit_price, or extended_amount",
                    "is_handwritten": hw,
                }
            )
            continue

        expected = qty * price - disc
        delta = round(ext - expected, 4)
        results.append(
            {
                "line": ln,
                "check": "line_extension",
                "status": "pass" if abs(delta) <= tol else "fail",
                "expected": round(expected, 2),
                "stated": round(ext, 2),
                "delta": delta,
                "is_handwritten": hw,
            }
        )
    return results, (computed_subtotal if record.get("lines") else None), missing


def rate_num(entry, default=None):
    """Read a rate that may carry a percent sign, returning its displayed magnitude.

    ``num`` deliberately refuses ``"10%"``. It is the amount parser, and a
    percent sign changes what a number means -- 10% is 0.1, not 10 -- so a
    parser that silently dropped the sign would hand a hundredfold error to
    every caller reading money. A rate is the one term where the displayed
    magnitude is what the caller wants, because ``commission_rate_unit``
    settles percent-or-fraction from the arithmetic rather than from the
    number.

    Refusing the sign here was not free. On one commission run 2,611 lines
    across 576 documents carried a rate written ``10%``; every one of them read
    as a missing rate, so no line could be tested, no document could establish
    a rate unit, and 650 of 716 documents were reported ``not_provable`` --
    "we tried and could not prove this" -- when the truth was that the terms
    were on the page and the parser could not see one of them. Those documents
    were then refused by the canonical export, which admits only proved or
    inapplicable arithmetic.

    The sign is read as an annotation, not as evidence about the unit: a value
    is still tested against every unit in ``RATE_UNITS`` and still settles the
    document only where exactly one of them reconciles the line.
    """
    raw = entry.get("value") if isinstance(entry, dict) else entry
    if isinstance(raw, str) and raw.strip().endswith("%"):
        return num(raw.strip()[:-1], default)
    return num(entry, default)


def commission_line_terms(line):
    """Return the three amounts a commission line proves itself with."""
    return (
        num(line.get("commissionable_amount")),
        rate_num(line.get("stated_commission_rate")),
        num(line.get("commission_amount")),
    )


def commission_rate_unit(record, tol):
    """Establish the document's rate unit from the lines that reconcile.

    Returns the unit name, or None when no line settles it, or ``"mixed"`` when
    lines disagree -- which is refused rather than resolved, because a document
    that uses two units is not one this check can prove.
    """
    settled = set()
    for line in record.get("lines") or []:
        base, rate, commission = commission_line_terms(line)
        if base is None or rate is None or commission is None:
            continue
        works = {
            name for name, divisor in RATE_UNITS if abs(base * rate / divisor - commission) <= tol
        }
        # A line both readings satisfy distinguishes nothing; only a line that
        # picks one out is evidence about the unit.
        if len(works) == 1:
            settled |= works
    if not settled:
        return None
    if len(settled) > 1:
        return "mixed"
    return settled.pop()


def check_commission_lines(record, tol, unit):
    """commissionable_amount x rate == commission_amount, per line."""
    divisor = dict(RATE_UNITS)[unit]
    results, missing = [], 0
    for index, line in enumerate(record.get("lines") or []):
        raw_line_number = line.get("line_number", index + 1)
        if isinstance(raw_line_number, dict):
            raw_line_number = raw_line_number.get("value", index + 1)
        try:
            line_number = int(raw_line_number)
        except (TypeError, ValueError):
            line_number = index + 1
        base, rate, commission = commission_line_terms(line)
        handwritten = any(
            field_source(line.get(name)) == "handwritten"
            for name in ("commissionable_amount", "stated_commission_rate", "commission_amount")
        )
        if base is None or rate is None or commission is None:
            missing += 1
            results.append(
                {
                    "line": line_number,
                    "check": "commission_extension",
                    "status": "not_applicable",
                    "reason": (
                        "missing commissionable_amount, stated_commission_rate, "
                        "or commission_amount"
                    ),
                    "is_handwritten": handwritten,
                }
            )
            continue
        expected = base * rate / divisor
        delta = round(commission - expected, 4)
        results.append(
            {
                "line": line_number,
                "check": "commission_extension",
                "status": "pass" if abs(delta) <= tol else "fail",
                "expected": round(expected, 2),
                "stated": round(commission, 2),
                "delta": delta,
                "rate_unit": unit,
                "is_handwritten": handwritten,
            }
        )
    return results, missing


def accepted_classifications(path):
    """Read the document types a classification control already accepted.

    An engine handoff carries the intake guess, which is `unknown` wherever the
    intake rules found no high-signal type -- 691 of 716 documents in one run,
    including all 639 the classification control had since accepted as
    commission documents. Without this the arithmetic lane asks a record what it
    is, gets `unknown`, and skips the only proof that document supports. An
    accepted classification is a control's output and outranks both the intake
    guess and a model's unratified proposal.
    """
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("accepted"), list):
        raise ValueError(f"not a classification artifact with an accepted list: {path}")
    return {
        entry.get("document_id"): entry.get("document_type")
        for entry in data["accepted"]
        if entry.get("document_id") and entry.get("document_type")
    }


def check_document(record, tol, defer_unassigned=False, classifications=None):
    """Prove one document against the arithmetic its own source page shows.

    Each rollup is bounded by the rows it actually sums, so a header identity
    keeps the base tolerance however many lines the document carries. A document
    whose header balances while a line could not be read reports
    ``proved_with_unproved_lines``: not self-proved, still in review, and out of
    the sampling frame."""
    doc_id = record.get("document_id", "unknown")
    checks, notes = [], []

    document_type = ""
    accepted = (classifications or {}).get(doc_id)
    for candidate in (accepted, record.get("document_type"), record.get("model_document_type")):
        normalized = text(candidate).casefold()
        if normalized not in {"", "unknown", "none"}:
            document_type = normalized
            break
    if document_type in COMMISSION_DOCUMENT_TYPES:
        # Invoice rollups do not apply here, but a commission line still carries
        # its own answer key, and proving it needs no second engine.
        unit = commission_rate_unit(record, tol)
        if unit is None or unit == "mixed":
            note = (
                "No commission line carried commissionable_amount, "
                "stated_commission_rate, and commission_amount together, so the "
                "rate unit could not be established from the document itself."
                if unit is None
                else "Lines disagree about whether the stated rate is a percent "
                "or a decimal fraction. The unit is not inferred from magnitude, "
                "so this document is refused rather than resolved."
            )
            result = {
                "document_id": doc_id,
                "arithmetic_status": "not_provable",
                "arithmetic_scope": "commission_line_extension",
                "checks_run": 0,
                "checks_failed": 0,
                "largest_discrepancy": 0.0,
                "handwriting_involved": bool(record.get("amendments")),
                "lines_missing_fields": len(record.get("lines") or []),
                "checks": [],
                "rate_unit": unit,
                "notes": [note],
                "disposition": (
                    "Unprovable commission document; route to review rather than "
                    "treating an unproved line as a passing one."
                ),
            }
            for key, value in record.items():
                if key not in result:
                    result[key] = value
            return result
        commission_checks, missing = check_commission_lines(record, tol, unit)
        failed = [c for c in commission_checks if c["status"] == "fail"]
        run = [c for c in commission_checks if c["status"] in ("pass", "fail")]
        largest = max((abs(c["delta"]) for c in run), default=0.0)
        result = {
            "document_id": doc_id,
            "arithmetic_status": "failed" if failed else ("proved" if run else "not_provable"),
            "arithmetic_scope": "commission_line_extension",
            "checks_run": len(run),
            "checks_failed": len(failed),
            "largest_discrepancy": round(largest, 2),
            "handwriting_involved": bool(record.get("amendments")),
            "lines_missing_fields": missing,
            "checks": commission_checks,
            "rate_unit": unit,
            "notes": [
                f"Commission lines proved against commissionable_amount x rate, with the "
                f"rate read as a {unit} because that is the reading the document's own "
                f"lines reconcile under. Invoice rollups remain not applicable; "
                f"use allocation_policy.py for any approved sales-credit formula."
            ],
            "disposition": (
                "Commission line arithmetic failed; treat the failing lines as exceptions."
                if failed
                else "Commission line arithmetic proved from the document itself."
            ),
        }
        for key, value in record.items():
            if key not in result:
                result[key] = value
        return result

    line_results, computed_subtotal, missing_lines = check_lines(record, tol)
    checks.extend(line_results)

    line_count = len(record.get("lines") or [])
    # Rounding allowance accrues per summed row, so each rollup check scales with
    # the rows it actually sums. Header-only identities sum no rows and keep the
    # base tolerance; applying a line-count allowance to them would loosen the
    # strongest control on exactly the longest documents.
    line_rollup_tol = rollup_tolerance(tol, line_count)
    accessorial_rollup_tol = rollup_tolerance(tol, len(record.get("accessorials") or []))

    subtotal, subtotal_src = effective(record, "subtotal")
    tax, _ = effective(record, "tax_amount", 0.0)
    freight, _ = effective(record, "freight_amount", 0.0)
    access, _ = effective(record, "accessorial_total", 0.0)
    discount, _ = effective(record, "discount_amount", 0.0)
    total, total_src = effective(record, "total_amount")
    amount_due, _ = effective(record, "amount_due")
    credits, _ = effective(record, "credits_applied", 0.0)

    # Lines -> subtotal
    if computed_subtotal is not None and subtotal is not None:
        delta = round(subtotal - computed_subtotal, 4)
        checks.append(
            {
                "check": "lines_sum_to_subtotal",
                "status": "pass" if abs(delta) <= line_rollup_tol else "fail",
                "expected": round(computed_subtotal, 2),
                "stated": round(subtotal, 2),
                "delta": delta,
                "tolerance_used": round(line_rollup_tol, 4),
            }
        )
    elif subtotal is None:
        notes.append("no subtotal present; lines-to-subtotal check skipped")
    else:
        notes.append("no line items extracted; header-only proof")

    # Subtotal + charges -> total
    if subtotal is not None and total is not None:
        expected = subtotal + (tax or 0) + (freight or 0) + (access or 0) - (discount or 0)
        delta = round(total - expected, 4)
        checks.append(
            {
                "check": "subtotal_plus_charges_equals_total",
                "status": "pass" if abs(delta) <= tol else "fail",
                "expected": round(expected, 2),
                "stated": round(total, 2),
                "delta": delta,
                "tolerance_used": round(tol, 4),
                "components": {
                    "subtotal": subtotal,
                    "tax": tax,
                    "freight": freight,
                    "accessorials": access,
                    "discount": discount,
                },
            }
        )
    elif total is None:
        notes.append("no total_amount present; document total unprovable")

    # Total - credits -> amount due
    if total is not None and amount_due is not None:
        expected = total - (credits or 0)
        delta = round(amount_due - expected, 4)
        checks.append(
            {
                "check": "total_less_credits_equals_amount_due",
                "status": "pass" if abs(delta) <= tol else "fail",
                "expected": round(expected, 2),
                "stated": round(amount_due, 2),
                "delta": delta,
            }
        )

    # Accessorial rollup
    acc_lines = record.get("accessorials") or []
    if acc_lines and access is not None:
        acc_sum = sum(num(a.get("charge_amount"), 0.0) or 0.0 for a in acc_lines)
        delta = round(access - acc_sum, 4)
        checks.append(
            {
                "check": "accessorial_lines_sum_to_total",
                "status": "pass" if abs(delta) <= accessorial_rollup_tol else "fail",
                "expected": round(acc_sum, 2),
                "stated": round(access, 2),
                "delta": delta,
                "tolerance_used": round(accessorial_rollup_tol, 4),
            }
        )

    applied = [c for c in checks if c["status"] in ("pass", "fail")]
    failures = [c for c in applied if c["status"] == "fail"]
    hw_involved = any(c.get("is_handwritten") for c in checks) or bool(record.get("amendments"))

    if not applied:
        if defer_unassigned and not record.get("document_group_id"):
            status = "deferred_reassembly"
            notes.append(
                "arithmetic proof deferred because this page is not assigned to a "
                "reviewed document group; do not treat page-local absence of totals "
                "as a document-level arithmetic failure"
            )
        else:
            status = "not_provable"
            notes.append(
                "no arithmetic relationship could be tested -- treat as an "
                "exception; an unprovable document is not a passing one"
            )
    elif failures:
        status = "failed"
    elif missing_lines:
        # Header identities can tie while individual lines were unreadable. That
        # is not a self-proved document: the rows the subtotal claims to sum were
        # never read. Keep it out of the sampled population and in review.
        status = "proved_with_unproved_lines"
        notes.append(
            f"{missing_lines} line(s) lacked quantity, unit_price, or extended_amount; "
            "header arithmetic cannot prove rows that were never read"
        )
    else:
        status = "proved"

    worst = max((abs(c.get("delta", 0)) for c in failures), default=0.0)

    result = {
        "document_id": doc_id,
        "arithmetic_status": status,
        "checks_run": len(applied),
        "checks_failed": len(failures),
        "largest_discrepancy": round(worst, 2),
        "handwriting_involved": hw_involved,
        "lines_missing_fields": missing_lines,
        "checks": checks,
        "notes": notes,
    }

    if status == "failed":
        result["disposition"] = (
            "Exception. Route to human review regardless of OCR confidence. "
            + (
                "A handwritten amendment is involved -- verify the annotation "
                "reading first; it is the likeliest source."
                if hw_involved
                else "No handwriting involved -- likely a misread digit or a field "
                "assigned to the wrong column."
            )
        )
    elif status == "not_provable":
        result["disposition"] = (
            "Exception. Document could not prove itself; extraction is too incomplete to validate."
        )
    elif status == "deferred_reassembly":
        result["disposition"] = (
            "Review-required proposal. Page-level proof is deferred until "
            "evidence-backed document reassembly exists."
        )
    elif status == "proved_with_unproved_lines":
        result["disposition"] = (
            "Exception. Header arithmetic holds but at least one line could not be "
            "read; the document is not self-proved and is not sample-eligible."
        )
    else:
        result["disposition"] = "Passes self-proof. Eligible for the sampled population."

    # Carry the record forward. Downstream scripts (attribution, completeness,
    # sampling) consume the record shape, so a proof result that drops it breaks
    # the pipeline chain.
    for key, val in record.items():
        if key not in result:
            result[key] = val
    return result


def relabel_not_provable(results, authorization):
    """Count every unprovable document as not applicable, under a named authorization.

    `not_provable` means a document carries no testable arithmetic relationship:
    on the commission run, 392 documents whose lines never print a base, a rate
    and a commission together. Whether that blocks is a policy decision rather
    than a finding, so it is the operator's, and it is made by name. The status
    the document reached on its own readings stays in `prior_arithmetic_status`
    with the authorization beside it, so the relabel always reads back to its
    signature. A failed or partly proved document is never touched.
    """
    relabelled = 0
    for result in results:
        if result.get("arithmetic_status") == "not_provable":
            result["prior_arithmetic_status"] = "not_provable"
            result["arithmetic_status"] = "not_applicable"
            result["arithmetic_status_authorization"] = authorization
            relabelled += 1
    return relabelled


def main():
    load_project_env()
    ap = argparse.ArgumentParser(description="Prove documents against their own arithmetic.")
    ap.add_argument("input", help="JSON: a record, a list of records, or consensus.json")
    ap.add_argument("--out", default="proofed.json")
    ap.add_argument(
        "--tolerance",
        type=float,
        default=None,
        help=f"absolute tolerance per check (default {DEFAULT_TOLERANCE})",
    )
    ap.add_argument(
        "--classification",
        help=(
            "Accepted classification artifact. An engine handoff carries the intake "
            "guess, which is 'unknown' wherever intake found no high-signal type, so "
            "without this a commission document is never recognised as one and the "
            "only arithmetic it supports is never run."
        ),
    )
    ap.add_argument(
        "--failures-only",
        action="store_true",
        help="Emit only records that failed a check. The retained result set is unchanged.",
    )
    ap.add_argument(
        "--defer-unassigned-pages",
        action="store_true",
        # `store_true` alone yields False, never None, which left the documented
        # ARITHMETIC_DEFER_UNASSIGNED_PAGES branch below permanently unreachable.
        default=None,
        help="defer page-level proof until evidence-backed document reassembly exists",
    )
    ap.add_argument(
        "--not-provable-as-not-applicable",
        metavar="AUTHORIZATION",
        help=(
            "Count every not_provable document as not_applicable, under the named "
            "operator authorization. Each keeps prior_arithmetic_status and the "
            "authorization's name; a failed or partly proved document is not touched."
        ),
    )
    ap.add_argument("--quiet", action="store_true")
    apply_shared_help(ap)
    args = ap.parse_args()
    tolerance = (
        env_float("ARITHMETIC_TOLERANCE", DEFAULT_TOLERANCE)
        if args.tolerance is None
        else args.tolerance
    )
    defer_unassigned_pages = (
        env_bool("ARITHMETIC_DEFER_UNASSIGNED_PAGES", False)
        if args.defer_unassigned_pages is None
        else args.defer_unassigned_pages
    )

    with open(args.input) as fh:
        data = json.load(fh)

    if isinstance(data, dict) and "documents" in data:
        records = data["documents"]
    elif isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = [data]
    else:
        records = []

    if not records:
        sys.exit("No records found in input.")

    classifications = accepted_classifications(args.classification) if args.classification else {}
    results = [
        check_document(r, tolerance, defer_unassigned_pages, classifications) for r in records
    ]
    authorization = args.not_provable_as_not_applicable
    if authorization is not None and not authorization.strip():
        sys.exit("--not-provable-as-not-applicable needs the authorization's name")
    relabelled = relabel_not_provable(results, authorization.strip()) if authorization else 0
    if args.failures_only:
        results = [r for r in results if r["arithmetic_status"] != "proved"]

    proved = sum(1 for r in results if r["arithmetic_status"] == "proved")
    failed = sum(1 for r in results if r["arithmetic_status"] == "failed")
    unprov = sum(1 for r in results if r["arithmetic_status"] == "not_provable")
    deferred = sum(1 for r in results if r["arithmetic_status"] == "deferred_reassembly")
    not_applicable = sum(1 for r in results if r["arithmetic_status"] == "not_applicable")
    unproved_lines = sum(
        1 for r in results if r["arithmetic_status"] == "proved_with_unproved_lines"
    )
    hw_fail = sum(
        1 for r in results if r["arithmetic_status"] == "failed" and r["handwriting_involved"]
    )
    total_disc = round(sum(r["largest_discrepancy"] for r in results), 2)

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "tolerance": tolerance,
        "documents": len(results),
        "proved": proved,
        "failed": failed,
        "not_provable": unprov,
        "deferred_reassembly": deferred,
        "not_applicable": not_applicable,
        "not_provable_relabelled": relabelled,
        "not_provable_relabel_authorization": authorization.strip() if relabelled else None,
        "proved_with_unproved_lines": unproved_lines,
        "failures_involving_handwriting": hw_fail,
        "sum_of_largest_discrepancies": total_disc,
        "eligible_for_proof": len(results) - deferred - not_applicable,
        "pass_rate_pct": round(100.0 * proved / (len(results) - deferred - not_applicable), 2)
        if len(results) > deferred + not_applicable
        else None,
    }

    findings = []
    if failed:
        findings.append(
            f"{failed} documents failed self-proof and are exceptions regardless of "
            "OCR confidence. Work all of them -- they sit outside the sampling frame."
        )
    if hw_fail:
        findings.append(
            f"{hw_fail} of those failures involve handwriting. Verify the annotation "
            "reading before assuming a printed-field error; on Branch B pages an "
            "arithmetic failure is often the only signal that an undetected "
            "handwritten override exists."
        )
    if unproved_lines:
        findings.append(
            f"{unproved_lines} documents balanced at the header while at least one "
            "line could not be read. Header arithmetic cannot prove rows that were "
            "never extracted; these stay in review and outside the sampling frame."
        )
    if unprov:
        findings.append(
            f"{unprov} documents could not be proved at all -- no testable "
            "arithmetic relationship was extracted. An unprovable document is not a "
            "passing one; treat as an exception."
        )
    if deferred:
        findings.append(
            f"{deferred} pages were not assigned to a reviewed document group; "
            "their page-level arithmetic is deferred rather than counted as a "
            "document-level failure."
        )
    # No document-level check produces `not_applicable` any more: a commission
    # document is proved against its own line identity, and one whose terms were
    # never extracted is `not_provable` -- tried and could not prove -- rather
    # than "nothing to check here". Only an operator's named authorization turns
    # one into the other, and the finding says so.
    if relabelled:
        findings.append(
            f"{relabelled} not_provable documents are counted as not_applicable under "
            f"{authorization.strip()}; each keeps prior_arithmetic_status."
        )
    commission = [r for r in results if r.get("arithmetic_scope") == "commission_line_extension"]
    if commission:
        unresolved = sum(1 for r in commission if r.get("rate_unit") in (None, "mixed"))
        findings.append(
            f"{len(commission)} commission documents were proved against their own line "
            f"identity rather than invoice rollups; {unresolved} could not establish a rate "
            "unit from their own lines and are unproved rather than passing."
        )
    if summary["pass_rate_pct"] is not None and summary["pass_rate_pct"] < 85 and len(results) > 20:
        findings.append(
            f"Pass rate {summary['pass_rate_pct']}% is low. Before working the queue "
            "by hand, check for a systematic cause: a misassigned tax or freight "
            "column, or a vendor whose totals include charges not extracted as "
            "separate fields."
        )
    summary["findings"] = findings or ["All documents passed self-proof."]

    with open(args.out, "w") as fh:
        json.dump({"summary": summary, "documents": results, "results": results}, fh, indent=2)

    if not args.quiet:
        print(f"Documents: {summary['documents']}")
        print(f"  proved       : {proved} ({summary['pass_rate_pct']}%)")
        print(f"  failed       : {failed}")
        print(f"  not provable : {unprov}")
        for f in summary["findings"]:
            print(f"  - {f}")
        print(f"\nWritten to {args.out}")


if __name__ == "__main__":
    main()
