"""Decide what one document is worth, and say which evidence that came from.

Attribution, sampling, and completeness each need a document's monetary value,
and each read a printed header total. That is right for an invoice and wrong for
anything whose money lives in its lines. On a real commission-statement corpus
``header.total_amount`` was absent from 16 of 18 documents -- not rejected by
consensus, never read by either engine, because the documents do not carry one --
so attribution reported $17,120.50 as the corpus total against $335,822.26 of
accepted line commission, and sampling found no monetary-unit frame at all.

Sampling refusing was the honest symptom. Attribution's number was the dangerous
one, because a figure that is 5% of the money still looks like an answer.

This module keeps that decision in one place and makes the basis explicit. A
printed total is stronger evidence than a total this pipeline computed, so the
printed one always wins and the derived one is labelled wherever it is used. When
neither exists the document has no value here: it is excluded with a reason
rather than counted as zero, because a document worth an unknown amount and a
document worth nothing are different facts.
"""

from __future__ import annotations

import re
from typing import Any

# Header fields that state a document's own total, most specific first.
HEADER_VALUE_FIELDS = ("total_amount", "amount")
# Line fields that carry money. Only one is summed per line -- the first present
# -- because a line's commission and the commissionable base it was computed from
# are the same money counted twice.
LINE_VALUE_FIELDS = ("commission_amount", "line_total", "amount", "extended_amount")
PRINTED_HEADER_TOTAL = "printed_header_total"
SUMMED_LINE_ITEMS = "summed_line_items"
UNAVAILABLE = "unavailable"


# `16 430,72` is sixteen thousand four hundred and thirty. Stripping the space
# and the comma the way a US-format reading does makes it 1,643,072 -- a hundred
# times the money. Two documents in one corpus were written this way and one of
# them became the largest value in the run at $4.25 million against a median of
# $2,604.
#
# Neither convention can be established from the string alone, and rule 10 is
# explicit that a unit is never inferred from magnitude. So a string carrying
# either signal is refused rather than guessed: a comma followed by one or two
# digits at the end is a decimal comma, and whitespace between two digits is a
# group separator no US-format number uses.
SEPARATOR_CONVENTION_UNCLEAR = re.compile(r"\d[\s\u00a0\u202f]\d|,\d{1,2}$")


def separator_convention_unclear(text: str) -> bool:
    """Say whether a numeric string's thousands/decimal convention is undecidable."""
    return bool(SEPARATOR_CONVENTION_UNCLEAR.search(text.strip()))


def numeric(entry: Any, default: float = 0.0) -> float:
    """Parse a monetary value without inferring a unit or separator convention."""
    if isinstance(entry, dict):
        entry = entry.get("value")
    if entry is None:
        return default
    if isinstance(entry, (int, float)) and not isinstance(entry, bool):
        return float(entry)
    raw = str(entry).strip()
    if separator_convention_unclear(raw.replace("$", "").replace("\u00a3", "")):
        return default
    text = raw.replace(",", "").replace("$", "").replace(" ", "")
    if text.startswith("(") and text.endswith(")"):
        # An accounting negative. Reading it as positive turns a chargeback into
        # revenue, which is the wrong direction for every control downstream.
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return default


def header_field(record: dict[str, Any], key: str) -> Any:
    """Read a header field whether it sits at the record root or under header."""
    header = record.get("header")
    for source in (record, header if isinstance(header, dict) else {}):
        if isinstance(source, dict) and key in source:
            value = source[key]
            return value.get("value") if isinstance(value, dict) else value
    return None


def printed_total(record: dict[str, Any]) -> float | None:
    """Return the document's own stated total, or None when it states none."""
    for key in HEADER_VALUE_FIELDS:
        raw = header_field(record, key)
        if raw in (None, ""):
            continue
        value = numeric(raw, default=0.0)
        if value:
            return value
    return None


def line_total(record: dict[str, Any]) -> tuple[float | None, int]:
    """Sum one monetary field per line, returning the net and the lines used.

    The net is signed on purpose. A statement carrying a chargeback is worth its
    net, and summing absolute values would report money that was never earned.
    """
    lines = record.get("lines")
    if not isinstance(lines, list):
        return None, 0
    total, used = 0.0, 0
    for line in lines:
        if not isinstance(line, dict):
            continue
        for key in LINE_VALUE_FIELDS:
            if key not in line:
                continue
            value = numeric(line[key], default=0.0)
            if value:
                total += value
                used += 1
            break
    return (total, used) if used else (None, 0)


def document_value(record: dict[str, Any]) -> dict[str, Any]:
    """Return one document's value with the evidence it rests on.

    ``value`` is the absolute magnitude, which is what a monetary-unit sample
    frames on; ``signed_value`` keeps the direction for anything reporting net
    position. ``basis`` names the evidence and is meant to be carried into the
    artifact, not consumed and discarded.
    """
    printed = printed_total(record)
    if printed is not None:
        return {
            "value": abs(printed),
            "signed_value": printed,
            "basis": PRINTED_HEADER_TOTAL,
            "lines_summed": 0,
            "derived": False,
        }
    summed, used = line_total(record)
    if summed is not None:
        return {
            "value": abs(summed),
            "signed_value": summed,
            "basis": SUMMED_LINE_ITEMS,
            "lines_summed": used,
            # Flagged wherever it is used: this pipeline computed it, the
            # document did not state it.
            "derived": True,
        }
    return {
        "value": 0.0,
        "signed_value": 0.0,
        "basis": UNAVAILABLE,
        "lines_summed": 0,
        "derived": False,
    }


def basis_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Report how a population was valued, so a reader can weigh it."""
    counts: dict[str, int] = {PRINTED_HEADER_TOTAL: 0, SUMMED_LINE_ITEMS: 0, UNAVAILABLE: 0}
    derived_value = 0.0
    printed_value = 0.0
    for record in records:
        resolved = document_value(record)
        counts[resolved["basis"]] += 1
        if resolved["basis"] == SUMMED_LINE_ITEMS:
            derived_value += resolved["value"]
        elif resolved["basis"] == PRINTED_HEADER_TOTAL:
            printed_value += resolved["value"]
    return {
        "documents_valued_from_printed_total": counts[PRINTED_HEADER_TOTAL],
        "documents_valued_from_summed_lines": counts[SUMMED_LINE_ITEMS],
        "documents_with_no_monetary_value": counts[UNAVAILABLE],
        "printed_total_value": round(printed_value, 2),
        "derived_line_value": round(derived_value, 2),
        "interpretation": (
            "A derived value is the net of this document's own line items, summed "
            "by this pipeline because the document states no total of its own. It "
            "is weaker evidence than a printed total and is labelled wherever it "
            "is used. A document stating no total and carrying no line money has "
            "no value here and is excluded with a reason rather than counted as "
            "zero."
        ),
    }
