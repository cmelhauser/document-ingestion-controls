"""Hold the shared document-value basis to what the documents actually carry.

Attribution, sampling, and completeness each valued a document by a printed
header total. A commission statement does not carry one, so on a real corpus
attribution reported $17,120.50 against $335,822.26 of accepted line commission.
These tests are written from the document's shape rather than the schema's
expectation.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

document_value = importlib.import_module("document_value")


def test_a_printed_total_is_stronger_evidence_than_a_derived_one():
    """A document that states its own total is valued at what it states."""
    record = {
        "header": {"total_amount": {"value": "5,000.00"}},
        "lines": [{"commission_amount": {"value": "999999.00"}}],
    }
    resolved = document_value.document_value(record)
    assert resolved["value"] == 5000.0
    assert resolved["basis"] == document_value.PRINTED_HEADER_TOTAL
    assert resolved["derived"] is False

    # The total may sit at the record root rather than under header.
    assert document_value.document_value({"total_amount": "42"})["value"] == 42.0
    # "amount" stands in when no total_amount is present.
    assert document_value.document_value({"header": {"amount": "7"}})["value"] == 7.0


def test_a_document_with_no_total_is_valued_from_its_own_lines():
    """The money in a commission statement lives per line, and it is signed."""
    record = {
        "lines": [
            {"commission_amount": {"value": "1,000.00"}},
            # An accounting negative. Reading it as positive would turn a
            # chargeback into revenue.
            {"commission_amount": {"value": "(100.00)"}},
        ]
    }
    resolved = document_value.document_value(record)
    assert resolved["signed_value"] == 900.0
    assert resolved["value"] == 900.0
    assert resolved["basis"] == document_value.SUMMED_LINE_ITEMS
    assert resolved["derived"] is True
    assert resolved["lines_summed"] == 2

    # A net that goes negative is still a magnitude for a monetary-unit frame,
    # and the direction is kept for anything reporting net position.
    negative = document_value.document_value(
        {"lines": [{"commission_amount": "100"}, {"commission_amount": "(400)"}]}
    )
    assert negative["signed_value"] == -300.0 and negative["value"] == 300.0

    # Only one monetary field per line is summed: a commission and the base it
    # was computed from are the same money counted twice.
    once = document_value.document_value({"lines": [{"commission_amount": "10", "amount": "500"}]})
    assert once["value"] == 10.0 and once["lines_summed"] == 1

    # A later monetary field stands in when the first is absent.
    fallback = document_value.document_value({"lines": [{"line_total": "250"}]})
    assert fallback["value"] == 250.0 and fallback["lines_summed"] == 1

    # A line carrying no money at all contributes nothing and is not counted,
    # and neither is one whose amount is zero.
    mixed = document_value.document_value(
        {
            "lines": [
                {"description": "carried forward"},
                {"commission_amount": "0.00"},
                {"commission_amount": "75"},
            ]
        }
    )
    assert mixed["value"] == 75.0 and mixed["lines_summed"] == 1

    # Every line empty leaves the document unvalued rather than worth zero.
    empty = document_value.document_value({"lines": [{"commission_amount": "0"}]})
    assert empty["basis"] == document_value.UNAVAILABLE


def test_a_document_worth_nothing_is_not_a_document_worth_zero():
    """No stated total and no line money is unavailable, not zero."""
    for record in ({}, {"lines": []}, {"lines": "not-a-list"}, {"lines": ["not-an-object"]}):
        resolved = document_value.document_value(record)
        assert resolved["basis"] == document_value.UNAVAILABLE
        assert resolved["value"] == 0.0

    # A total that parses to zero is not a total; the lines are consulted.
    zeroed = document_value.document_value(
        {"header": {"total_amount": "0.00"}, "lines": [{"commission_amount": "25"}]}
    )
    assert zeroed["basis"] == document_value.SUMMED_LINE_ITEMS and zeroed["value"] == 25.0

    # An unparseable total falls through rather than being read as zero.
    unparseable = document_value.document_value(
        {"header": {"total_amount": "see attached"}, "lines": [{"commission_amount": "5"}]}
    )
    assert unparseable["basis"] == document_value.SUMMED_LINE_ITEMS


def test_monetary_parsing_keeps_the_direction_a_document_meant():
    """Currency, separators, and accounting negatives, without inferring a unit."""
    assert document_value.numeric("(662.89)") == -662.89
    assert document_value.numeric("$1,234.56") == 1234.56
    assert document_value.numeric({"value": "-5"}) == -5.0
    assert document_value.numeric(7) == 7.0
    assert document_value.numeric(None) == 0.0
    assert document_value.numeric(True) == 0.0
    assert document_value.numeric("not a number") == 0.0
    assert document_value.numeric("not a number", default=None) is None
    assert document_value.header_field({"header": "not-a-dict"}, "total_amount") is None


def test_the_basis_of_a_population_is_reported_not_averaged_away():
    """A reader can tell how much of a corpus total this pipeline computed."""
    records = [
        {"header": {"total_amount": "100"}},
        {"lines": [{"commission_amount": "40"}]},
        {"lines": [{"commission_amount": "60"}]},
        {},
    ]
    summary = document_value.basis_summary(records)
    assert summary["documents_valued_from_printed_total"] == 1
    assert summary["documents_valued_from_summed_lines"] == 2
    assert summary["documents_with_no_monetary_value"] == 1
    assert summary["printed_total_value"] == 100.0
    assert summary["derived_line_value"] == 100.0
    assert "weaker evidence than a printed total" in summary["interpretation"]


def test_a_separator_convention_it_cannot_decide_is_refused_not_guessed():
    """`16 430,72` is sixteen thousand four hundred thirty, not 1.6 million.

    Two documents in one corpus were written this way. Stripping the space and
    the comma the way a US-format reading does made one of them the largest
    value in the run at $4.25 million against a corpus median of $2,604 --
    exactly a hundred times the money. Rule 10: never infer a unit or a
    convention from magnitude.
    """
    assert document_value.separator_convention_unclear("16 430,72")
    assert document_value.separator_convention_unclear("0,00")
    assert document_value.separator_convention_unclear("12,34")
    # US formatting is decidable and still parses.
    assert not document_value.separator_convention_unclear("1,234.56")
    assert not document_value.separator_convention_unclear("1,234")
    assert not document_value.separator_convention_unclear("(15)")

    assert document_value.numeric("16 430,72", default=None) is None
    assert document_value.numeric("5 624,84", default=None) is None
    assert document_value.numeric("$1,234.50") == 1234.5
    assert document_value.numeric("(15)") == -15.0
    # The guard runs before the currency strip, so a currency mark cannot make a
    # decidable string undecidable.
    assert not document_value.separator_convention_unclear("$1,234.50")
