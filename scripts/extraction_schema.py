#!/usr/bin/env python3
"""The provider-neutral extraction schema, prompt, and field normalization.

The document family, field set, and strict output schema describe the business
documents this repository reads. They are not a property of whichever provider
happens to read them, so every adapter imports them from here rather than from
one vendor's module.
"""

import hashlib
import json
import re
from pathlib import Path

from runtime_config import env_value

DOCUMENT_TYPES = (
    "commercial_invoice",
    "sales_quote",
    "estimate",
    "request_for_quote",
    "commission_statement",
    "commission_report",
    "receipt",
    "purchase_order",
    "purchase_requisition",
    "sales_order",
    "order_acknowledgement",
    "order_confirmation",
    "work_order",
    "service_report",
    "timesheet",
    "expense_report",
    "packing_list",
    "bill_of_lading",
    "air_waybill",
    "proof_of_delivery",
    "remittance_advice",
    "payment_confirmation",
    "account_statement",
    "bank_statement",
    "credit_memo",
    "debit_memo",
    "statement",
    "carrier_freight_bill",
    "customs_entry",
    "customs_invoice",
    "certificate",
    "contract",
    "return_authorization",
    "unknown",
)

DOCUMENT_FAMILY_MODES = ("auto", *DOCUMENT_TYPES)

# Record field names the controls read that this schema deliberately does not
# emit. Every one of them is a name that can only reach a record from outside
# extraction -- a client-supplied reference export, an operator amendment, or a
# lane that writes a value back onto the record it resolved.
#
# The registry exists because a control that reads a name nothing emits is
# indistinguishable, in its own output, from a control whose corpus had nothing
# to find. Attribution rank 1 asked for `ack_number` for a full corpus and
# reported 26 documents where the true figure was 137; the selling-location
# hierarchy asked for `branch`, `office`, `letterhead_city` and `salesperson`
# and reported 0 of 716; completeness read `credits_applied` and
# `payment_status` and ran aging closure on permanently empty inputs. Each was
# a name, not a corpus.
#
# The test suite requires every literal name a control looks up to be either a
# field declared below or an entry here, so a lookup that can never match has to
# be written down as a deliberate choice.
NON_SCHEMA_RECORD_ALIASES = {
    "ack_number": "Written back onto every record attribution resolves, and the reference table's own column name.",
    "branch": "Client reference-export column for selling location.",
    "office": "Client reference-export column for selling location.",
    "selling_location": "Client reference-export column, and attribution's own output field.",
    "letterhead_city": "Pre-schema location field; retained for amended records that carry it.",
    "salesperson": "Pre-schema name for `sales_representative_name`.",
    "buyer_city": "Pre-schema name; the schema carries `buyer_address`.",
    "bill_to_city": "Pre-schema name; the schema carries `bill_to_address`.",
    "ship_from_city": "Pre-schema name; the schema carries `origin_city` and `origin_address`.",
    "ship_to_city": "Pre-schema name; the schema carries `ship_to_address`.",
    "credits_applied": "Pre-schema name; the schema carries `credit_amount`.",
    "payment_status": "Pre-schema name; the schema carries `document_status` and `approval_status`.",
    "memo_date": "Pre-schema date fallback; the schema carries `document_date` and `statement_date`.",
    "unresolved": "A disposition reason code read off a register entry, not a document field.",
    "shipment_key": "A graph join key the evidence lane writes, not an extracted field.",
}

HEADER_FIELDS = (
    # Document identity and lifecycle.
    "document_number",
    "document_date",
    "issue_date",
    "effective_date",
    "expiration_date",
    "document_status",
    "invoice_number",
    "invoice_date",
    "bill_date",
    "due_date",
    "quote_number",
    "estimate_number",
    "rfq_number",
    "purchase_requisition_number",
    "purchase_order_number",
    "order_number",
    "sales_order_number",
    "acknowledgement_number",
    "confirmation_number",
    "work_order_number",
    "service_order_number",
    "return_authorization_number",
    "change_order_number",
    "job_number",
    "project_number",
    "project_name",
    "contract_number",
    "claim_number",
    "case_number",
    "reference_number",
    "revision_number",
    "customer_account_number",
    "vendor_account_number",
    "statement_number",
    "statement_date",
    "memo_number",
    "bol_number",
    "pro_number",
    "awb_number",
    "tracking_number",
    "shipment_number",
    "load_number",
    "booking_number",
    "container_number",
    "seal_number",
    # Parties and their visible contact details.
    "seller_name",
    "seller_address",
    "buyer_name",
    "buyer_address",
    "customer_name",
    "customer_address",
    "vendor_name",
    "vendor_address",
    "bill_to_name",
    "bill_to_address",
    "sold_to_name",
    "sold_to_address",
    "ship_to_name",
    "ship_to_address",
    "remit_to_address",
    "remit_to_name",
    "payer_name",
    "payee_name",
    "shipper_name",
    "consignee_name",
    "carrier_name",
    "carrier_scac",
    "broker_name",
    "agent_name",
    "manufacturer_name",
    "warehouse_name",
    "origin_address",
    "destination_address",
    "contact_name",
    "contact_email",
    "contact_phone",
    "sales_representative_name",
    "sales_representative_email",
    "sales_representative_phone",
    "sales_representative_address",
    "prepared_by",
    "requested_by",
    "approved_by",
    "approval_status",
    "approval_reference",
    # Tax, currency, amounts, and settlement.
    "buyer_tax_id",
    "seller_tax_id",
    "vendor_tax_id",
    "customer_tax_id",
    "tax_registration_number",
    "currency",
    "payment_terms",
    "payment_method",
    "payment_reference",
    "check_number",
    "remittance_number",
    "payment_date",
    "deposit_amount",
    "prepaid_amount",
    "total_paid",
    "amount_due",
    "balance_due",
    "credit_amount",
    "debit_amount",
    "fee_amount",
    "surcharge_amount",
    "withholding_tax_amount",
    "exchange_rate",
    "subtotal",
    "tax_amount",
    "tax_rate",
    "freight_amount",
    "accessorial_total",
    "discount_amount",
    "total_amount",
    "statement_total",
    "sales_amount",
    "commissionable_amount",
    "stated_commission_rate",
    "allocation_share",
    "commission_amount",
    "invoice_amount",
    "discount_taken",
    "applied_amount",
    # Dates and operational periods.
    "ship_date",
    "delivery_date",
    "requested_delivery_date",
    "service_date",
    "posting_date",
    "period_start",
    "period_end",
    # Logistics, inventory, and international trade.
    "shipping_method",
    "service_level",
    "equipment_type",
    "freight_class",
    "linehaul_amount",
    "fuel_surcharge",
    "incoterms",
    "terms_of_sale",
    "origin_city",
    "origin_state",
    "origin_postal",
    "destination_city",
    "destination_state",
    "destination_postal",
    "country_of_origin",
    "country_of_destination",
    "warehouse_location",
    "weight",
    "weight_uom",
    "piece_count",
    "package_count",
    "entry_number",
    "port_of_entry",
    "entered_value",
    "duty_amount",
    "return_reason",
    # Commission and sales-reporting fields.
    "dealer_name",
    "brand_name",
    "specifier_name",
)

LINE_FIELDS = (
    "item_code",
    "product_sku",
    "upc",
    "description",
    "quantity",
    "uom",
    "unit_price",
    "extended_amount",
    "discount_amount",
    "tax_amount",
    "tax_rate",
    "tax_code",
    "gl_account_code",
    "cost_center",
    "department",
    "project_number",
    "project_name",
    "job_number",
    "purchase_order_number",
    "sales_order_number",
    "lot_number",
    "serial_number",
    "warehouse_location",
    "country_of_origin",
    "hs_code",
    "weight",
    "weight_uom",
    "charge_code",
    "charge_description",
    "charge_amount",
    "freight_class",
    "equipment_type",
    "linehaul_amount",
    "fuel_surcharge",
    "invoice_amount",
    "discount_taken",
    "applied_amount",
    "entered_value",
    "duty_amount",
    "transaction_id",
    "transaction_date",
    "dealer_name",
    "brand_name",
    "specifier_name",
    "customer_name",
    "sales_amount",
    "commissionable_amount",
    "stated_commission_rate",
    "allocation_share",
    "commission_amount",
)

# What a canonical field means, stated once and given to every engine.
#
# The schema named 50 line fields and defined none of them. Two independent
# vendors reading the same page then filed the same printed value under
# different names: on an 18-page corpus 397 of 1008 consensus exceptions -- 39%
# -- were the identical string in a different field, and 145 of those were one
# engine's `sales_amount` against the other's `commissionable_amount`.
#
# That is not a disagreement about what the page says, and consensus cannot tell
# it apart from one. It also silently disabled a downstream control: arithmetic
# proved 0 of 18 documents because no engine pair agreed on where the commission
# base lived, so no testable triple survived into the accepted record.
#
# These definitions are the repository's existing contract written down, not a
# new one. `commissionable_amount` is already the denominator of
# `effective_commission_rate` in references/allocation-policy.md, and
# allocation_policy.py already reads it as the base. Naming that in the schema
# removes an ambiguity in the question; it normalizes no value, merges no
# reading, and clears no exception. Two engines that disagree about a digit still
# disagree after this.
#
# Only fields that actually collided on a real corpus are defined. A field whose
# name is unambiguous is left alone rather than given filler prose that would
# dilute the ones that matter.
#
# One earlier attempt is recorded here because its failure is the reason the rest
# is shaped this way. The glossary also told the engines to "put a value in
# exactly one field rather than repeating it across several". It worked, and made
# things worse: both engines stopped emitting a printed identifier into every
# plausible field, each kept a *different* one, and the cross-field cases that
# consensus could at least see turned into values that simply appeared absent to
# the other engine -- 297 of them became 467, and the overall exception rate
# barely moved. The same clause also captured `line_number`, which one engine
# then filled with the row's invoice number instead of its position. Defining a
# field is worth doing; instructing an engine on how to allocate across fields it
# was not given a definition for is not.
FIELD_DEFINITIONS = {
    "stated_commission_rate": (
        "The commission rate this line states, copied as printed -- `10%`, "
        "`10.00`, `8`. A column headed Commission Split, Split, or Rep Split is "
        "not this field however numeric it looks: a split says how a commission "
        "is divided between parties, and the rate says what was applied to the "
        "base. Reading a split of `1.00` or `0.20` as a one-percent or "
        "fifth-of-a-percent rate understates the commission tenfold, and a "
        "consumer recomputing base x rate then disagrees with the printed "
        "commission on every line. Put a split under its own printed label in "
        "source_labelled_fields and leave this null."
    ),
    "allocation_share": (
        "The portion of a line credited to this statement's recipient, copied "
        "as printed -- a shipment or territory percentage such as `100.0%`. It "
        "is not the commission rate and does not multiply the commissionable "
        "amount to give the commission."
    ),
    "extended_amount": ("Quantity times unit price for this line, as printed. Never compute it."),
    "invoice_amount": (
        "The total of the invoice this line refers to, as printed. It is not "
        "the line's own amount unless the document prints one invoice per line."
    ),
    "quantity": (
        "The number of units this line covers, as printed. It is not a line "
        "position or a count for the whole document, and a page that prints no "
        "quantity has none rather than one."
    ),
    "unit_price": (
        "The price of one unit on this line, as printed. It is not the line's "
        "extended amount, which covers every unit, and neither is ever computed "
        "by dividing the other."
    ),
    "purchase_order_number": (
        "The buyer's purchase order number printed for this line. It is the "
        "buyer's own reference, not the seller's sales order, acknowledgement "
        "or job number."
    ),
    "sales_order_number": (
        "The seller's own sales order number printed for this line, which is "
        "not the buyer's purchase order number."
    ),
    "product_sku": (
        "The vendor's item, model or SKU code printed for this line. It names "
        "the product, not the order: a job, purchase order or acknowledgement "
        "number belongs in its own field."
    ),
    "charge_code": (
        "A code the document prints to classify the charge on this line, such "
        "as a condition or billing type. It is not the product's own item or "
        "SKU code."
    ),
    "charge_description": (
        "The document's own wording for what a separately stated charge "
        "covers, such as freight or handling. It is not the description of the "
        "goods on the line."
    ),
    "charge_amount": (
        "The amount of a separately stated charge on this line, such as "
        "freight or handling. It is not part of the line's extended amount "
        "unless the document says so."
    ),
    "discount_taken": (
        "A discount the document states was actually taken on this line, as "
        "printed. It is not a discount merely offered, and it is never computed "
        "from the difference between two other amounts."
    ),
    "tax_rate": (
        "The tax rate this line states, copied as printed. It is not the "
        "commission rate, and a line printing a tax amount without a rate has "
        "no rate to record."
    ),
    # Not an identifier. One engine read the clause above as licence to put the
    # row's invoice number here, which silently renumbers every line in the run.
    "line_number": (
        "This row's position in the table, counting from 1 at the first row. "
        "Never an invoice, order, job, item, or project number printed in the "
        "row -- each of those has its own field."
    ),
    # The money cluster. These are three different amounts and the difference
    # decides what arithmetic can be proved.
    "sales_amount": (
        "Gross sale or invoice amount for this transaction, as printed. Use this "
        "only when the page shows a sale amount that is distinct from the base "
        "the commission was computed on; if one printed amount serves as both, "
        "put it in commissionable_amount and leave this null."
    ),
    "commissionable_amount": (
        "The base amount the commission was computed from -- the number that, "
        "multiplied by the commission rate, yields commission_amount. On a "
        "commission statement showing one sale amount and one commission, this "
        "is that sale amount. This is the denominator of the effective rate, so "
        "a page whose commission has a visible base must fill this field. A "
        "column that sits under a Commission heading is never this field, "
        "however far left it appears: a commission split into categories is "
        "still commission, and a report that pays commission without printing "
        "the base it was computed from has no commissionable amount at all. "
        "Leave this null rather than promoting a commission column into it."
    ),
    "commission_amount": (
        "The commission earned on this line, as printed. Preserve an accounting "
        "negative exactly as shown: parentheses or a leading minus mean a "
        "chargeback, and dropping the sign turns a deduction into revenue. "
        "Where a Commission heading spans several columns -- a breakdown by "
        "product category, such as furniture, leather, fabric or fixed, "
        "followed by a total -- this field is the total column, not the first "
        "category column. Put each category component in "
        "source_labelled_fields under its own printed label."
    ),
    "applied_amount": (
        "An amount applied against an existing balance or advance, not the "
        "commission itself. Use commission_amount for commission earned."
    ),
    # The identifier cluster. One printed number is one field, not three.
    "transaction_id": (
        "The identifier of the underlying transaction -- the invoice, order, or "
        "document number this line settles. On a commission statement, the "
        "printed invoice number belongs here."
    ),
    "item_code": (
        "A product, part, or SKU identifier for the goods on this line. Not an "
        "invoice, order, job, or project number."
    ),
    "job_number": (
        "A job or work-order identifier, used only when the page prints a job "
        "reference separate from the transaction and project numbers."
    ),
    "project_number": (
        "A project identifier, used only when the page prints a project "
        "reference separate from the job and transaction numbers."
    ),
    "project_name": (
        "The name of the job or site a line was sold into -- `One Bayfront "
        "Suite 540`, `Meridiana Corporation Center`, `Norcross Tampa Office`. A "
        "commission statement heads this column `Project` and prints it beside "
        "the job number. It is not a party: with no field of its own it landed "
        "in `dealer_name` on 314 lines across 68 documents, where each project "
        "would have loaded as a company the client sells through. A building or "
        "a suite is never a dealer, a customer or a brand."
    ),
    # The party cluster. A name printed once fills one role.
    "description": (
        "The printed description of what this line is for. When the only text in "
        "the row is a party name, put it in the matching party field and leave "
        "this null rather than duplicating the name here."
    ),
    "customer_name": ("The end customer or account the transaction was for, as printed."),
    "dealer_name": (
        "The dealer, agency, or representative earning the commission -- the "
        "party the statement is addressed to, not the end customer."
    ),
    "brand_name": (
        "The manufacturer or brand whose product the commission is paid on -- "
        "the party issuing the statement. A column headed SPECIFIER, DESIGNER "
        "or ARCHITECT names the firm that chose the product, not the brand that "
        "makes it: that belongs in specifier_name. Neither is this the dealer "
        "who sold the line nor the customer who bought it, so a value you have "
        "already put in dealer_name or customer_name does not also belong here."
    ),
    "specifier_name": (
        "The design firm, architect or specifier the document names -- a column "
        "headed SPECIFIER, DESIGNER or ARCHITECT. This party chose the product; "
        "it is not the manufacturer whose brand it carries, not the dealer who "
        "sold it, and not the customer who bought it, even when the same "
        "company appears in another role elsewhere on the page. Copy the "
        "printed name, including a printed placeholder such as `Unknown "
        "Specifier`, rather than leaving the field empty or moving the value "
        "into brand_name."
    ),
    "transaction_date": (
        "The date printed for the underlying transaction, such as an invoice "
        "date. Copy the printed date without reordering its parts; if the day "
        "and month order is not certain from the page, add a review flag rather "
        "than choosing one."
    ),
}

SOURCES = ("printed", "handwritten", "not_present")


def nullable_string():
    """Return the nullable-string schema, spelled so every transport keeps the null.

    `anyOf` rather than a type list because a dropped nullability flag turns
    every empty cell into the text `null` and every one of those into a claimed
    reading. A schema translated for one transport is re-verified on another.
    """
    return {"anyOf": [{"type": "string"}, {"type": "null"}]}


def field_schema():
    """Return the JSON schema for one extracted field and its provenance."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "value", "source", "evidence_text", "model_confidence"],
        "properties": {
            "name": {"type": "string", "enum": list(HEADER_FIELDS)},
            "value": nullable_string(),
            "source": {"type": "string", "enum": list(SOURCES)},
            "evidence_text": nullable_string(),
            "model_confidence": {
                "anyOf": [
                    {"type": "number", "minimum": 0, "maximum": 1},
                    {"type": "null"},
                ]
            },
        },
    }


def line_schema():
    """Return the JSON schema for one extracted line item."""
    properties = {
        "line_number": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "line_model_confidence": {
            "anyOf": [
                {"type": "number", "minimum": 0, "maximum": 1},
                {"type": "null"},
            ]
        },
    }
    for name in LINE_FIELDS:
        properties[name] = nullable_string()
    # A definition travels with the field itself, which is the strongest signal
    # the structured-output APIs offer. Applied over the properties rather than
    # over LINE_FIELDS, because line_number is a property and not a member of it
    # -- and line_number is exactly the field that needed defining.
    for name, text in FIELD_DEFINITIONS.items():
        if name in properties:
            properties[name]["description"] = text
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


EXTRACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "document_type",
        "has_handwriting",
        "handwriting_regions",
        "handwriting_readings",
        "header",
        "source_labelled_fields",
        "lines",
        "review_flags",
    ],
    "properties": {
        "document_type": {"type": "string", "enum": list(DOCUMENT_TYPES)},
        "has_handwriting": {"type": "boolean"},
        "handwriting_regions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "region_id",
                    "label",
                    "left",
                    "top",
                    "right",
                    "bottom",
                    "evidence_text",
                ],
                "properties": {
                    "region_id": {"type": "string", "minLength": 1},
                    "label": {"type": "string"},
                    "left": {"type": "number", "minimum": 0, "maximum": 1},
                    "top": {"type": "number", "minimum": 0, "maximum": 1},
                    "right": {"type": "number", "minimum": 0, "maximum": 1},
                    "bottom": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence_text": nullable_string(),
                },
            },
        },
        "handwriting_readings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "region_id",
                    "label",
                    "semantic_type",
                    "content_class",
                    "value",
                    "evidence_text",
                    "model_confidence",
                    "financial_amendment",
                ],
                "properties": {
                    "region_id": {"type": "string", "minLength": 1},
                    "label": {"type": "string"},
                    "semantic_type": {"type": "string"},
                    "content_class": {"type": "string", "enum": ["numeric", "text", "signature"]},
                    "value": nullable_string(),
                    "evidence_text": nullable_string(),
                    "model_confidence": {
                        "anyOf": [
                            {"type": "number", "minimum": 0, "maximum": 1},
                            {"type": "null"},
                        ]
                    },
                    "financial_amendment": {"type": "boolean"},
                },
            },
        },
        "header": {"type": "array", "items": field_schema()},
        "source_labelled_fields": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "source_label",
                    "value",
                    "source",
                    "evidence_text",
                    "model_confidence",
                ],
                "properties": {
                    "source_label": {"type": "string", "minLength": 1},
                    "value": nullable_string(),
                    "source": {"type": "string", "enum": list(SOURCES)},
                    "evidence_text": nullable_string(),
                    "model_confidence": {
                        "anyOf": [
                            {"type": "number", "minimum": 0, "maximum": 1},
                            {"type": "null"},
                        ]
                    },
                },
            },
        },
        "lines": {"type": "array", "items": line_schema()},
        "review_flags": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["field", "reason", "evidence_text"],
                "properties": {
                    "field": {"type": "string"},
                    "reason": {"type": "string"},
                    "evidence_text": nullable_string(),
                },
            },
        },
    },
}

INSTRUCTIONS = """Extract only what is visible on the supplied single-page business document.
Return null for an unavailable value and add a review flag for ambiguous or conflicting evidence.
For monetary documents, capture every visible arithmetic input and output: line quantity,
unit price, extended amount, subtotal, tax, freight, discount, total, and amount due.
Do not infer a missing arithmetic value or calculate a replacement; preserve the printed value
and flag uncertainty instead.
Do not infer missing values, perform arithmetic, merge pages, or treat handwriting as an approved amendment.
First classify the page into the closest supported document type. Extract only fields that are
applicable to that document family; do not emit invoice, purchase-order, statement, or remittance
fields merely because the schema permits them. Commission statements and commission reports are
supported document families: use statement_number, statement_date, period_start, period_end,
dealer_name, brand_name, customer_name, sales_amount, commissionable_amount,
stated_commission_rate, allocation_share, commission_amount, statement_total, and the matching
transaction-level fields when those labels are visibly present. Do not rename commissionable
amounts as invoice subtotal, commission amounts as invoice total, or dealer/brand roles as buyer
or seller roles. Where one Commission heading spans several money columns, read the total column
as commission_amount and treat the others as components under their printed labels; do not read
any of them as the commissionable base. A page that pays commission without printing the amount
it was computed from has no commissionable_amount, and leaving it null is the correct reading. Do not apply invoice arithmetic to a commission report; preserve stated formulas
and flag only an explicit report inconsistency. If a page is a different report or unsupported
layout, keep the family as unknown and preserve visible values only in the fields that clearly
apply, with a review flag describing the limitation.
When a printed label or unambiguous labeled block identifies a contact or sales representative,
capture the visible name, email, phone, and address in the matching contact_* or
sales_representative_* header field. Do not infer a person, their role, or an address from an
unlabeled name, signature, letterhead, or email domain; preserve such evidence only as a
review flag or handwriting reading.
For every visible, labeled business value that does not fit a named header or line field,
emit one source_labelled_fields entry with the exact printed label and its visible value. This
extension channel preserves unfamiliar tax, sales, shipping, payment, approval, certificate,
or industry-specific concepts for later schema review; it is not a canonical mapping and does
not authorize an inferred label or value.
When a PDF image is supplied, enumerate every visible handwritten or annotated region using normalized page coordinates (0--1), and transcribe each legible region in `handwriting_readings` using the same stable `region_id`. Return null for an illegible value, preserve the visible evidence text when possible, and classify signatures as signatures rather than data. Mark financial changes with `financial_amendment=true`. Return empty region and reading lists if none are visible or input is text-only. These are review-only readings for independent handwriting reconciliation, not final decisions."""


# A corpus context is prompt text, and prompt text competes with the page for the
# model's attention. This ceiling keeps the operator's notes a glossary rather
# than a second document; a run that needs more than this is describing the
# corpus, not conditioning a reading of it.
MAX_CORPUS_CONTEXT_BYTES = 24_000

CORPUS_CONTEXT_PREAMBLE = """
The operator supplies the corpus notes below. They record what this document set
has already been observed to contain, so that a printed label or layout you would
otherwise fail to place is recognised. They are reasoning-only context and hold no
authority over the page in front of you:
- Never emit a value these notes supply. Every value you return must be visible on
  this page.
- Where a note and the page disagree, the page wins. Extract what the page shows
  and add a review flag describing the difference.
- A note naming a label is not a claim that this page carries it. If it is absent,
  return null rather than supplying it from the notes.
- The notes never authorize arithmetic, a merged page, an inferred unit, date
  order, or country, or a handwriting amendment.

Operator corpus notes follow.
"""


def corpus_context():
    """Return the operator's hash-bound corpus context, or ``None``.

    Extraction reads one page at a time with no memory of the corpus, so a label
    printed on hundreds of pages is rediscovered -- or missed -- page by page.
    This channel lets an operator state, once, what the corpus has already been
    shown to contain. It is deliberately the same text for every lane:
    conditioning one engine and not the other would leave two readings that are
    no longer independent, and ``consensus`` refuses that pair.

    The returned mapping carries the text and its hash. The hash reaches the
    provider handoff so a later reader can tell which reading was conditioned and
    by exactly what.
    """
    configured = env_value("LLM_CORPUS_CONTEXT", "").strip()
    if not configured:
        return None
    path = Path(configured)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"LLM_CORPUS_CONTEXT is set but unreadable: {exc}") from exc
    encoded = text.encode("utf-8")
    if not text.strip():
        # Rule 9 in prompt form: an empty context file is a misconfiguration, not
        # a decision to run without context. Unsetting the variable is how an
        # operator says "no context"; an empty file says nothing at all.
        raise ValueError(f"LLM_CORPUS_CONTEXT names an empty file: {path}")
    if len(encoded) > MAX_CORPUS_CONTEXT_BYTES:
        raise ValueError(
            f"corpus context is {len(encoded)} bytes, above the "
            f"{MAX_CORPUS_CONTEXT_BYTES}-byte ceiling: {path}"
        )
    return {
        "file": path.name,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "bytes": len(encoded),
        "text": text,
    }


def schema_fingerprint():
    """Return a content hash of the schema every adapter sends.

    The response cache keys on the schema's *name*, which does not change when
    the schema does. Adding a line field, tightening a type, or defining a field
    would then replay responses produced against the previous schema, and the run
    would report a fresh reading it did not take. Hashing the content closes that:
    a changed schema is a changed key, and the old entries are simply never hit.
    """
    encoded = json.dumps(EXTRACTION_SCHEMA, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def field_glossary():
    """Render the field definitions as prompt text.

    The line schema carries these on the fields themselves, but the header is a
    name/value array over an enum and has nowhere to hang a per-field
    description. The same definitions are stated here so a header
    `dealer_name` and a line `dealer_name` mean one thing, from one source.
    """
    lines = [
        "These canonical fields are easy to confuse, so each is defined once. "
        "Use the definition, not the field name, to decide where a printed value "
        "belongs. A field with no definition below keeps whatever the rest of "
        "these instructions already say about it:"
    ]
    lines += [f"- {name}: {text}" for name, text in sorted(FIELD_DEFINITIONS.items())]
    return "\n".join(lines)


def extraction_instructions():
    """Return the shared prompt: instructions, glossary, family hint, corpus notes.

    Every adapter renders the prompt through this one function, so a corpus
    context reaches each provider as identical text. The response cache keys on
    the rendered prompt, so adding or changing a context is a new cache key and
    never replays a reading taken under different conditioning.
    """
    family = env_value("LLM_DOCUMENT_FAMILY", "auto")
    if family not in DOCUMENT_FAMILY_MODES:
        raise ValueError("LLM_DOCUMENT_FAMILY must be auto or one of: " + ", ".join(DOCUMENT_TYPES))
    prompt = f"{INSTRUCTIONS}\n{field_glossary()}"
    if family != "auto":
        prompt += (
            f" The operator selected document family {family!r}; use it as a routing hint, "
            "but retain a review flag if the visible page conflicts with that family."
        )
    context = corpus_context()
    if context is None:
        return prompt
    return f"{prompt}\n{CORPUS_CONTEXT_PREAMBLE}\n{context['text']}"


def extracted_field(item):
    """Make a canonical field object without treating a provider self-score as proof."""
    value = item.get("value")
    source = item.get("source")
    # `literal_null` covers a header field the same way it covers a line cell:
    # an empty reading spelled as text is still an empty reading.
    if value is None or literal_null(value) or source not in SOURCES or source == "not_present":
        return None
    model_confidence = item.get("model_confidence")
    if not isinstance(model_confidence, (int, float)) or isinstance(model_confidence, bool):
        model_confidence = None
    return {
        "value": value,
        "confidence": None,
        "model_confidence": model_confidence,
        "source": source,
    }


def normalized_source_labelled_fields(parsed, model_confidences):
    """Preserve unfamiliar printed labels as source-cited extension proposals."""
    result, occurrences = {}, {}
    for item in parsed.get("source_labelled_fields", []):
        if not isinstance(item, dict) or not isinstance(item.get("source_label"), str):
            continue
        label = item["source_label"].strip()
        field = extracted_field(item)
        if not label or field is None:
            continue
        normalized_label = re.sub(r"\s+", " ", label.casefold())
        occurrence = occurrences.get(normalized_label, 0)
        occurrences[normalized_label] = occurrence + 1
        key = f"field_{hashlib.sha256(normalized_label.encode()).hexdigest()[:16]}_{occurrence + 1}"
        result[key] = {
            "source_label": {
                "value": label,
                "confidence": None,
                "model_confidence": field["model_confidence"],
                "source": field["source"],
            },
            "observed_value": field,
        }
        model_confidences.append(
            {
                "field": f"source_labelled_fields.{key}.observed_value",
                "value": field["model_confidence"],
            }
        )
    return result


def validate_run_limits(max_pages, max_pdf_bytes, max_text_chars):
    """Reject unsafe or nonsensical client-approved submission limits."""
    if not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError("max pages must be a positive integer")
    if not isinstance(max_pdf_bytes, int) or max_pdf_bytes < 1:
        raise ValueError("max PDF bytes must be a positive integer")
    if not isinstance(max_text_chars, int) or max_text_chars < 1:
        raise ValueError("max text characters must be a positive integer")


# Vertex will not serve a response schema whose structure branches too widely.
# The served refusal names its own causes exactly:
#
#   "The specified schema produces a constraint that has too much branching for
#    serving. Typical causes of this error are objects with lots of optional
#    properties, enums with too many values, or any_of a large number of
#    alternative types."
#
# EXTRACTION_SCHEMA meets two of the three: a 158-value enum over the header
# field names, and 61 nullable `anyOf` pairs. Bisecting the enum ceiling against
# the served endpoint accepted 40 values and refused 200, so this limit is
# measured against Vertex rather than chosen.
VERTEX_ENUM_LIMIT = 40

# Google validates the same schema behind two transports that do not speak the
# same dialect of "or null", and getting this wrong is silent rather than loud.
#
# Vertex's native API understands `nullable: True`. OpenRouter's
# OpenAI-compatible bridge does not: it drops the flag, leaves `type: "string"`
# standing alone, and the model then satisfies a strict string by writing the
# four characters `null`. Nothing raises. The lane returns the right number of
# rows, every absent cell holds a string that is not None, and every one of them
# reaches consensus as a value the engine claims to have read.
#
# Measured on one run: Vertex direct produced 0 literal `null` strings; the same
# schema through OpenRouter produced 222,190 of them across 716 pages.
NULL_STYLE_FLAG = "nullable_flag"
NULL_STYLE_UNION = "type_union"

# The signature of that failure, so a lane can recognise it rather than pass it
# on. No business field in this schema legitimately holds the text "null".
LITERAL_NULL_TEXT = "null"

# Vertex's schema dialect is narrower than JSON Schema: it spells "or null" as a
# `nullable` flag and has no vocabulary for these two keywords at all.
VERTEX_UNSUPPORTED_KEYWORDS = ("additionalProperties", "minLength")

# A relaxed enum survives only as the vocabulary written into its description,
# so that description must carry every permitted value. Truncating it would drop
# names silently and leave the model unable to emit them -- the translation
# refuses instead. The bound is generous: the widest enum in EXTRACTION_SCHEMA
# spells out in 2,552 characters.
VERTEX_DESCRIPTION_LIMIT = 8000


def literal_null(value):
    """Say whether a value is the text "null" a mis-dialected schema produces.

    Treated as absent rather than as a reading: a cell the model marked empty is
    empty however the transport made it spell that. Counted as well as dropped,
    because a lane full of them has a broken schema dialect and must not report
    a clean run.
    """
    return isinstance(value, str) and value.strip().casefold() == LITERAL_NULL_TEXT


def vertex_response_schema(node, enum_limit=VERTEX_ENUM_LIMIT, null_style=NULL_STYLE_FLAG):
    """Translate a JSON Schema into the narrower dialect Vertex will serve.

    Four transformations. Three preserve the constraint exactly:

    * `additionalProperties` and `minLength` are dropped, because Vertex has no
      equivalent and refuses a schema carrying them;
    * a nullable `anyOf` pair collapses onto its single non-null branch, spelled
      the way `null_style` says the reading transport understands it --
      `nullable: True` for Vertex's native API, or a `type` union for an
      OpenAI-compatible bridge, which drops the flag and would otherwise leave a
      strict string the model satisfies by writing the text "null";
    * a `required` list is sorted, so an equal schema hashes equally. What the
      object demands is untouched -- a property is never added to the list or
      removed from it.

    The fourth deliberately relaxes the constraint: an enum longer than
    `enum_limit` loses its `enum` and carries the permitted values in its
    description instead. The model is then *asked* for one of those names rather
    than *held* to them, so an out-of-vocabulary value becomes possible on this
    provider. `normalized_record` already discards a header name outside
    `HEADER_FIELDS`, so the failure mode is a dropped field rather than a
    corrupted one -- but enforcement is genuinely lost, and only here: the
    OpenAI lane still sends the full enum, so the two engines are not held to
    the same vocabulary by the schema.
    """
    if isinstance(node, list):
        return [vertex_response_schema(item, enum_limit, null_style) for item in node]
    if not isinstance(node, dict):
        return node
    node = {key: value for key, value in node.items() if key not in VERTEX_UNSUPPORTED_KEYWORDS}
    if "anyOf" in node:
        return _vertex_nullable(node, enum_limit, null_style)
    translated = {
        key: vertex_response_schema(value, enum_limit, null_style) for key, value in node.items()
    }
    if isinstance(translated.get("required"), list):
        translated["required"] = sorted(translated["required"])
    return _vertex_relax_enum(translated, enum_limit)


def _vertex_nullable(node, enum_limit, null_style):
    """Rewrite a JSON Schema union over null as Vertex's `nullable` flag."""
    options = [
        item
        for item in node["anyOf"]
        if not (isinstance(item, dict) and item.get("type") == "null")
    ]
    rest = {
        key: vertex_response_schema(value, enum_limit, null_style)
        for key, value in node.items()
        if key != "anyOf"
    }
    if not options:
        raise ValueError("a response schema cannot offer null as a field's only type")
    if len(options) == 1:
        single = {**vertex_response_schema(options[0], enum_limit, null_style), **rest}
        return _spell_nullable(single, null_style)
    united = {**rest, "anyOf": vertex_response_schema(options, enum_limit, null_style)}
    return _spell_nullable(united, null_style)


def _spell_nullable(node, null_style):
    """Mark a node nullable the way the reading transport actually understands."""
    if null_style == NULL_STYLE_UNION and isinstance(node.get("type"), str):
        return {**node, "type": [node["type"], "null"]}
    # A union of alternatives has no single `type` to widen, so it keeps the
    # flag; and Vertex's native API wants the flag in every case.
    return {**node, "nullable": True}


def _vertex_relax_enum(node, enum_limit):
    """Move an over-long enum into the description, losing its enforcement."""
    values = node.get("enum")
    if not isinstance(values, list) or len(values) <= enum_limit:
        return node
    relaxed = {key: value for key, value in node.items() if key != "enum"}
    vocabulary = ", ".join(str(value) for value in values)
    described = f"{relaxed.get('description', '')} One of: {vocabulary}".strip()
    if len(described) > VERTEX_DESCRIPTION_LIMIT:
        # Silently cutting the list would leave the model unable to name the
        # values it never saw, and nothing downstream would show the loss.
        raise ValueError(
            f"relaxed enum vocabulary is {len(described)} characters, over the "
            f"{VERTEX_DESCRIPTION_LIMIT}-character description bound; it cannot "
            "be carried without dropping permitted values"
        )
    relaxed["description"] = described
    return relaxed


def relaxed_enum_paths(node, enum_limit=VERTEX_ENUM_LIMIT, path=""):
    """Name every field whose enum the translation relaxes, for the handoff.

    The weakening above is invisible in the sent schema -- the field simply
    looks like a string. Recording the paths keeps it auditable.
    """
    if isinstance(node, list):
        found = []
        for index, item in enumerate(node):
            found.extend(relaxed_enum_paths(item, enum_limit, f"{path}[{index}]"))
        return found
    if not isinstance(node, dict):
        return []
    found = []
    if isinstance(node.get("enum"), list) and len(node["enum"]) > enum_limit:
        found.append(path or "/")
    for key, value in node.items():
        if key != "enum":
            found.extend(relaxed_enum_paths(value, enum_limit, f"{path}/{key}"))
    return found


def schema_sha256(schema):
    """Fingerprint a schema by content, so a changed schema cannot replay."""
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


# Above this share of emitted cells, the text "null" is a broken schema
# dialect rather than a page that happened to be empty.
SPELLED_NULL_SHARE_LIMIT = 0.05


def spelled_null_finding(records, limit=SPELLED_NULL_SHARE_LIMIT):
    """Refuse to report a clean run when the transport ate the nullability.

    A schema whose nullability a transport drops leaves the model satisfying a
    strict string by writing the text "null". Every such cell is dropped rather
    than recorded as a reading -- but dropping alone is silent, and silence is
    how this defect reached 222,190 cells across 716 pages before anyone saw it.
    A lane whose emitted cells are largely this text has a broken dialect, not
    empty pages, and must say so.
    """
    spelled = sum(record.get("cells_spelled_null", 0) or 0 for record in records)
    kept = sum(len(line) - 1 for record in records for line in (record.get("lines") or []))
    total = spelled + kept
    if not total or spelled / total <= limit:
        return None, spelled
    return {
        "reason": "response_schema_nullability_not_honoured",
        "detail": (
            f"{spelled} of {total} emitted line cells carried the text 'null' rather than "
            "JSON null. The response schema's nullability was not honoured by this "
            "transport, so the reading cannot be trusted as extraction evidence."
        ),
        "spelled_null_cells": spelled,
        "emitted_cells": total,
        "share": round(spelled / total, 4),
        "disposition": "client_review_required",
        "blocking": True,
    }, spelled
