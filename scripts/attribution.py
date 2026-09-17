#!/usr/bin/env python3
"""
Phase 3A -- Attribution.

Ties every dollar to an ACK, job, or project number, and reports what it could
not tie.

This is a phase, not a field. Many documents in a business corpus never state
the configured attribution key -- a carrier freight bill, for example, carries
a PRO number and a BOL reference rather than a job number. The key has to be
derived through the document graph, which is why this runs after extraction and
entity resolution rather than alongside them.

Resolution runs an eight-rank hierarchy. The first method that resolves wins, and
the METHOD IS RECORDED: an attribution set that is 80% rank 6 is a different
artefact from one that is 80% rank 1, and the client needs to be able to see
which they have.

The gate is not a high attribution rate. It is that no dollar is unaccounted for
silently -- everything is either attributed or in the unattributable register
with a reason code and a dollar value. "97% attributed" fails unless the other 3%
is enumerated.

Usage:
    python attribution.py proofed.json --reference acks.csv --out attributed.json
    python attribution.py proofed.json --reference acks.csv \
        --ack-pattern 'ACK-\\d{4}-\\d{4}' --out attributed.json
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
from document_value import LINE_VALUE_FIELDS, basis_summary, document_value, numeric
from side_channel_input import normalized_columns, rejection

# Rank 6 (customer + date + amount) matches only within this window and this
# relative amount tolerance, and only when the match is unique.
DATE_WINDOW_DAYS = 45
AMOUNT_TOLERANCE_PCT = 0.02

# Inheritance beyond this depth is flagged. Compounding low-confidence
# inferences produces attributions that look authoritative and are not.
MAX_INHERITANCE_DEPTH = 2

RANK_CONFIDENCE = {
    1: 0.99,  # explicit printed key
    2: 0.95,  # explicit handwritten key
    3: 0.92,  # PO cross-reference
    4: 0.82,  # inherited via shipment
    5: 0.78,  # inherited via invoice
    6: 0.60,  # customer + date + amount, unique match only
    7: 0.45,  # email evidence
    8: 1.00,  # manual assignment -- confidence in the human, not the inference
}

# Document-level keys that carry an attribution, in the order rank 1 tries them.
# These are ``extraction_schema.HEADER_FIELDS`` names: a key the extractor does
# not emit under one of these names is invisible to rank 1 no matter how well it
# was read. ``ack_number`` stays last because this lane writes it back onto every
# record it resolves (``header.ack_number``), so a re-run must still find it.
EXPLICIT_KEY_FIELDS = (
    "acknowledgement_number",
    "job_number",
    "project_number",
    "sales_order_number",
    "work_order_number",
    "contract_number",
    "ack_number",
)

# Header names that would carry an attribution key if this engagement used them.
# Used only to report what rank 1 did not consult; it never resolves anything.
KEY_FIELD_NAME = re.compile(
    r"(?:^|_)(?:ack|acknowledgement|job|project|order|contract|work)_?(?:number|no)$"
)

# Fields a later rank already reads. Naming them here keeps the blind-field
# report about fields nothing consults, rather than about the design.
PO_FIELDS = ("po_number", "purchase_order_number")

# Header fields that state a selling location outright, schema names first.
# `warehouse_location` is the only one `extraction_schema` declares; `branch`,
# `office` and `selling_location` are kept because a client-supplied reference
# export or an amended record may carry them, but no extractor can emit them.
#
# The hierarchy below spent a corpus asking only for those three and for
# `letterhead_city` and `salesperson`, none of which is a schema field, and
# reported selling location unresolved for all 716 documents. `salesperson` is
# `sales_representative_name` in the schema, and the corpus carried it on 112.
SELLING_LOCATION_FIELDS = (
    "warehouse_location",
    "branch",
    "office",
    "selling_location",
)

RANK_METHOD = {
    1: "explicit_printed",
    2: "explicit_handwritten",
    3: "po_cross_reference",
    4: "inherited_via_shipment",
    5: "inherited_via_invoice",
    6: "customer_date_amount",
    7: "email_evidence",
    8: "manual_assignment",
}

# Reason codes that represent answers rather than failures. Only 'unresolved'
# is a failure; the rest are dispositions agreed in Phase 0. A payment record
# reports money received or paid against commission already reported, and names
# no job to credit; a blank page prints nothing to attribute. Both answer the
# question rather than fail it -- but only where the page itself shows it: on the
# commission run the client typed 92 unattributed documents payments and 29
# blank, and their images bore out 45 and 5.
DISPOSITION_CODES = {
    "internal_transfer",
    "rebate",
    "rebilled_freight",
    "pre_system",
    "closed_job_credit",
    "overhead",
    "payment_record",
    "blank_page",
}

# The crediting rule, applied only when an operator passes it. A document whose
# lines name several jobs has an allocation question, and this is one answer to
# it: each line carrying money is credited to the key that line prints. Nothing is
# chosen among the keys and no share is estimated, so a document is credited only
# when every one of its money lines prints a key the corpus formats accept.
LINE_CREDIT_CODE = "credited_by_line"


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
    """Read a document-level field from the record or its header.

    Line items are deliberately not searched. A key on the lines is a fact about
    a line, and a document whose lines name several is not missing an
    attribution -- it has several, which is an allocation question.
    """
    header = rec.get("header", {})
    for src in (rec, header):
        if key in src:
            v = src[key]
            return v.get("value") if isinstance(v, dict) else v
    return default


def get_source(rec, key):
    """Return whether a field was printed or handwritten, defaulting to printed.

    This is what separates rank 1 from rank 2, so an unmarked field is treated
    as printed rather than credited with the stricter handwriting recognition it
    never went through.
    """
    header = rec.get("header", {})
    for src in (rec, header):
        if key in src and isinstance(src[key], dict):
            return src[key].get("source", "printed")
    return "printed"


def parse_date(s):
    """Parse a reference date without inferring an order from the value."""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s[: len(fmt) + 4], fmt).date()
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            return None
    return None


def empty_reference(ack_pattern=None, job_pattern=None):
    """The reference shape used when the client supplied no table.

    Format validation is unavailable here -- ``validate_key`` accepts anything
    with ``no_reference_available`` -- so ranks 3 and 6 resolve nothing and an
    OCR error in a key goes uncaught. That is the cost of running without the
    table, and it is stated in the artifact's findings rather than inferred.
    """
    return {
        "table": {},
        "by_po": {},
        "by_customer": defaultdict(list),
        "formats": Counter(),
        "ack_pattern": re.compile(ack_pattern) if ack_pattern else None,
        "job_pattern": re.compile(job_pattern) if job_pattern else None,
        "rows_ingested": 0,
        "rejected_rows": [],
    }


def load_reference(path, ack_pattern, job_pattern):
    """
    Reference table: the ACK/job master built before any resolution is attempted.
    CSV or JSON with ack_number (or job_number), date, customer, amount.
    """
    path = os.fspath(path)
    rows = []
    if path.lower().endswith(".json"):
        with open(path) as fh:
            data = json.load(fh)
        rows = data.get("acks", data) if isinstance(data, dict) else data
    else:
        with open(path, newline="") as fh:
            rows = list(csv.DictReader(fh))

    table, by_po, by_customer = {}, {}, defaultdict(list)
    formats = Counter()
    rejected = []

    for index, r in enumerate(rows):
        low = normalized_columns(r)
        key = (
            low.get("ack_number")
            or low.get("ack")
            or low.get("job_number")
            or low.get("job")
            or low.get("number")
        )
        if not key:
            # The document side of this phase registers every unattributed dollar
            # with a reason. A reference row silently dropped here collapses the
            # attribution rate with nothing in the output explaining why, so an
            # unrecognized key column is registered rather than skipped.
            rejected.append(
                {
                    **rejection(index, "reference_row_has_no_recognized_key_column"),
                    # The client's own headings, not the normalized lookup keys:
                    # an operator diagnosing this has to find the column in the
                    # file they were sent.
                    "available_columns": sorted(str(name) for name in r),
                }
            )
            continue
        key = str(key).strip()
        entry = {
            "key": key,
            "kind": "ack" if (low.get("ack_number") or low.get("ack")) else "job",
            "date": parse_date(low.get("date") or low.get("ack_date") or low.get("order_date")),
            "customer": str(low.get("customer") or low.get("party") or "").strip(),
            "amount": num(low.get("amount") or low.get("value") or low.get("total")),
            "po_number": str(low.get("po_number") or low.get("po") or "").strip(),
            "selling_location": str(
                low.get("selling_location") or low.get("branch") or low.get("office") or ""
            ).strip(),
        }
        table[key] = entry
        if entry["po_number"]:
            by_po[entry["po_number"]] = key
        if entry["customer"]:
            by_customer[entry["customer"].lower()].append(entry)
        formats[re.sub(r"\d", "#", key)] += 1

    return {
        "table": table,
        "by_po": by_po,
        "by_customer": by_customer,
        "formats": formats,
        "rows_ingested": len(table),
        "rejected_rows": rejected,
        "ack_pattern": re.compile(ack_pattern) if ack_pattern else None,
        "job_pattern": re.compile(job_pattern) if job_pattern else None,
    }


def validate_key(key, ref):
    """
    Format validation is the cheapest error detection available here. A
    five-character ACK in a corpus where every other ACK is six characters is an
    OCR error, and the check costs nothing.
    """
    if not key:
        return False, "empty"
    key = str(key).strip()
    if key in ref["table"]:
        return True, "in_reference_table"
    for pat, name in ((ref["ack_pattern"], "ack_pattern"), (ref["job_pattern"], "job_pattern")):
        if pat and pat.fullmatch(key):
            return True, f"matches_{name}_not_in_reference"
    shape = re.sub(r"\d", "#", key)
    if ref["formats"] and shape in ref["formats"]:
        return True, "matches_corpus_format_not_in_reference"
    if ref["formats"]:
        return False, f"format_unrecognized (shape {shape})"
    return True, "no_reference_available"


def build_index(records):
    """Index records so inheritance can traverse the document graph."""
    idx = {
        "by_shipment": defaultdict(list),
        "by_invoice_number": {},
        "by_po": defaultdict(list),
        "by_document": {},
    }
    for rec in records:
        doc_id = rec.get("document_id", "unknown")
        idx["by_document"][doc_id] = rec
        for k in ("bol_number", "pro_number", "awb_number", "shipment_key"):
            v = get(rec, k)
            if v:
                idx["by_shipment"][str(v).strip()].append(doc_id)
        inv = get(rec, "invoice_number")
        if inv:
            idx["by_invoice_number"][str(inv).strip()] = doc_id
        po = next((get(rec, field) for field in PO_FIELDS if get(rec, field)), None)
        if po:
            idx["by_po"][str(po).strip()].append(doc_id)
    return idx


def resolve_explicit(rec, ref, key_fields=EXPLICIT_KEY_FIELDS):
    """Ranks 1 and 2: the key is stated on the document.

    The field names here have to be the ones the extractor actually emits.
    This lane spent a full corpus looking for ``ack_number``, which is not a
    field in ``extraction_schema.HEADER_FIELDS`` and never has been: the schema
    calls it ``acknowledgement_number``. Every document that printed its
    acknowledgement number was read correctly, accepted, and then registered as
    unresolved, because rank 1 asked for a name nothing writes. A key field
    added to the schema and not added here fails exactly this silently, so the
    default list is checked against the schema by the test suite.
    """
    for field in key_fields:
        val = get(rec, field)
        if not val or not str(val).strip():
            continue
        key = str(val).strip()
        valid, note = validate_key(key, ref)
        src = get_source(rec, field)
        rank = 2 if src == "handwritten" else 1
        if valid:
            return {"key": key, "rank": rank, "note": note, "field": field}
        # An invalid key is not silently dropped -- a rejected format is a
        # finding, usually an OCR error on a key nothing else will check.
        return {
            "key": None,
            "rank": None,
            "rejected_key": key,
            "note": f"rejected: {note}",
            "field": field,
        }
    return None


# How rank 1 records a key it found on the lines rather than the header. The
# rank is the same -- the key is printed -- but the method is not, and an
# attribution set is read by its method mix.
LINE_METHOD = "explicit_printed_on_lines"


def line_value(line, field):
    """One line's reading of a field, however it is wrapped."""
    cell = line.get(field) if isinstance(line, dict) else None
    value = cell.get("value") if isinstance(cell, dict) else cell
    return str(value).strip() if value not in (None, "") else ""


def resolve_from_lines(rec, ref, key_fields=EXPLICIT_KEY_FIELDS):
    """Rank 1, read from the lines: every line that names a key names the same one.

    `get` ignores the lines on purpose, because a document whose lines name
    several keys has an allocation question, not a missing attribution. A
    document whose lines all name one key, and whose header names none, has no
    such question: the key is printed on the page, once per line. On the
    commission run 67 documents printed their sales-order or project number
    only on their lines and were registered unresolved.
    """
    for field in key_fields:
        values = {line_value(line, field) for line in rec.get("lines") or []} - {""}
        if not values:
            continue
        if len(values) > 1:
            return None
        key = values.pop()
        valid, note = validate_key(key, ref)
        if valid:
            return {"key": key, "rank": 1, "note": note, "field": field, "method": LINE_METHOD}
        return {
            "key": None,
            "rank": None,
            "rejected_key": key,
            "note": f"rejected: {note}",
            "field": field,
        }
    return None


def credit_by_line(rec, ref, key_fields=EXPLICIT_KEY_FIELDS):
    """The crediting rule: each line carrying money, credited to the key it prints.

    Returns one credit per money line -- its index, key, the field the key was
    read from, and its amount -- or None when the document has no money line, a
    money line prints no key, or a key fails the corpus formats. A money line is
    one `document_value` would count: the first line value field it carries,
    when that value is not zero. A line printing two kinds of key is credited to
    the first in the configured order, the order rank 1 reads them in.
    """
    credits = []
    for index, line in enumerate(rec.get("lines") or []):
        if not isinstance(line, dict):
            continue
        value_field = next((field for field in LINE_VALUE_FIELDS if field in line), None)
        amount = numeric(line[value_field]) if value_field else 0.0
        if not amount:
            continue
        printed = next(
            ((field, line_value(line, field)) for field in key_fields if line_value(line, field)),
            None,
        )
        if printed is None:
            return None
        valid, _note = validate_key(printed[1], ref)
        if not valid:
            return None
        credits.append(
            {"line": index, "key": printed[1], "key_field": printed[0], "amount": round(amount, 2)}
        )
    return credits or None


def resolve_po(rec, ref):
    """Rank 3: PO number cross-reference."""
    po = next((get(rec, field) for field in PO_FIELDS if get(rec, field)), None)
    if not po:
        return None
    key = ref["by_po"].get(str(po).strip())
    if key:
        return {"key": key, "rank": 3, "note": f"via PO {str(po).strip()}"}
    return None


def resolve_inherited(rec, resolved, idx, depth=0):
    """
    Ranks 4 and 5: inherit through the document graph.

    Inheritance is one-directional and depth-limited, and never inherits from a
    record whose own attribution came from rank 6 or below -- compounding
    low-confidence inferences produces attributions that look authoritative and
    are not.
    """
    if depth >= MAX_INHERITANCE_DEPTH:
        return None
    doc_id = rec.get("document_id")

    def usable(donor_id):
        """Return a donor good enough to inherit from, or None.

        A rank 6 or weaker donor is refused: inheriting from an inference
        compounds it into an attribution that looks authoritative and is not.
        """
        r = resolved.get(donor_id)
        if not r or not r.get("ack_number"):
            return None
        if r.get("rank") is not None and r["rank"] >= 6:
            return None
        return r

    # Rank 4: via shared shipment reference
    for k in ("bol_number", "pro_number", "awb_number", "shipment_key"):
        v = get(rec, k)
        if not v:
            continue
        for donor_id in idx["by_shipment"].get(str(v).strip(), []):
            if donor_id == doc_id:
                continue
            r = usable(donor_id)
            if r:
                return {
                    "key": r["ack_number"],
                    "rank": 4,
                    "note": f"via shipment ref {str(v).strip()}",
                    "chain": (r.get("evidence_chain") or []) + [donor_id],
                }

    # Rank 5: via referenced invoice
    for k in ("invoice_number", "original_invoice_number", "reference_invoice"):
        v = get(rec, k)
        if not v:
            continue
        donor_id = idx["by_invoice_number"].get(str(v).strip())
        if donor_id and donor_id != doc_id:
            r = usable(donor_id)
            if r:
                return {
                    "key": r["ack_number"],
                    "rank": 5,
                    "note": f"via invoice {str(v).strip()}",
                    "chain": (r.get("evidence_chain") or []) + [donor_id],
                }
    return None


def resolve_fuzzy(rec, ref):
    """
    Rank 6: customer + date window + amount.

    Resolves on a UNIQUE match only. Two open jobs for the same customer in the
    same week sharing an amount is not a match -- it is two candidates, and
    accepting the first produces a plausible wrong attribution nothing
    downstream will catch.
    """
    customer = get(rec, "buyer_name") or get(rec, "payer_name") or get(rec, "seller_name")
    if not customer:
        return None
    date = parse_date(get(rec, "invoice_date") or get(rec, "bill_date"))
    amount = document_value(rec)["value"]
    if not date or amount <= 0:
        return None

    candidates = []
    for entry in ref["by_customer"].get(str(customer).strip().lower(), []):
        if not entry["date"]:
            continue
        if abs((entry["date"] - date).days) > DATE_WINDOW_DAYS:
            continue
        ref_amt = abs(entry["amount"])
        if ref_amt <= 0:
            continue
        if abs(ref_amt - amount) / ref_amt > AMOUNT_TOLERANCE_PCT:
            continue
        candidates.append(entry)

    if len(candidates) == 1:
        return {"key": candidates[0]["key"], "rank": 6, "note": "unique customer+date+amount match"}
    if len(candidates) > 1:
        return {
            "key": None,
            "rank": None,
            "note": f"ambiguous: {len(candidates)} candidates -- "
            "escalate to manual rather than picking one",
            "candidates": [c["key"] for c in candidates[:10]],
        }
    return None


def resolve_selling_location(rec, ref, attribution):
    """
    Selling location, by the hierarchy in references/attribution.md.

    All interpretations are captured where available. Clients change their mind
    about which one they meant, and capturing all of them means they can do that
    without reprocessing.
    """
    out = {
        "selling_location": None,
        "method": None,
        "ship_from": None,
        "customer_location": None,
        "destination": None,
    }

    explicit = next((get(rec, field) for field in SELLING_LOCATION_FIELDS if get(rec, field)), None)
    if explicit:
        out["selling_location"] = str(explicit).strip()
        out["method"] = "explicit_field"
    elif get(rec, "letterhead_city"):
        out["selling_location"] = str(get(rec, "letterhead_city")).strip()
        out["method"] = "letterhead"
    elif attribution.get("ack_number"):
        entry = ref["table"].get(attribution["ack_number"])
        if entry and entry.get("selling_location"):
            out["selling_location"] = entry["selling_location"]
            out["method"] = "reference_table_via_ack"
        else:
            # ACK prefixes frequently encode location. Cheap when true.
            m = re.match(r"^([A-Za-z]{2,4})[-_]", attribution["ack_number"])
            if m:
                out["selling_location"] = m.group(1).upper()
                out["method"] = "ack_prefix_inferred"
    if not out["selling_location"] and (
        get(rec, "sales_representative_name") or get(rec, "salesperson")
    ):
        out["selling_location"] = None
        out["method"] = "salesperson_needs_territory_table"

    out["ship_from"] = (
        get(rec, "origin_city") or get(rec, "origin_address") or get(rec, "ship_from_city")
    )
    out["customer_location"] = (
        get(rec, "buyer_address")
        or get(rec, "bill_to_address")
        or get(rec, "buyer_city")
        or get(rec, "bill_to_city")
    )
    out["destination"] = (
        get(rec, "destination_city")
        or get(rec, "destination_address")
        or get(rec, "ship_to_address")
        or get(rec, "ship_to_city")
    )
    if not out["method"]:
        out["method"] = "unresolved"
    return out


def main():
    ap = argparse.ArgumentParser(description="Resolve ACK/job attribution for every dollar.")
    ap.add_argument("input", help="proofed.json, consensus.json, or a record list")
    ap.add_argument(
        "--reference",
        default=None,
        help="ACK/job reference table (CSV or JSON). Strongly recommended.",
    )
    ap.add_argument("--out", default="attributed.json")
    ap.add_argument(
        "--register",
        default="unattributable.json",
        help="Destination path for the unattributable register recording every unresolved amount with its reason.",
    )
    ap.add_argument(
        "--key-field",
        action="append",
        default=None,
        metavar="FIELD",
        help=(
            "Header field carrying the attribution key, repeatable and tried in the order given. "
            "Defaults to the schema's own key fields: " + ", ".join(EXPLICIT_KEY_FIELDS) + "."
        ),
    )
    ap.add_argument("--ack-pattern", default=None, help="regex for valid ACK numbers")
    ap.add_argument("--job-pattern", default=None, help="regex for valid job numbers")
    ap.add_argument(
        "--dispositions",
        default=None,
        help="JSON map of document_id -> reason code for known non-job dollars",
    )
    ap.add_argument(
        "--credit-by-line",
        default=None,
        metavar="AUTHORIZATION",
        help=(
            "Apply the operator-authorized crediting rule, naming its authorization. A document no "
            "rank attributes, whose every line carrying money prints a key, is registered "
            f"{LINE_CREDIT_CODE} with each line's key and amount -- an answer, not a failure. A "
            "document with a money line printing no key stays unresolved."
        ),
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

    if args.reference:
        ref = load_reference(args.reference, args.ack_pattern, args.job_pattern)
    else:
        ref = empty_reference(args.ack_pattern, args.job_pattern)

    dispositions = {}
    if args.dispositions:
        with open(args.dispositions) as fh:
            dispositions = json.load(fh)

    key_fields = tuple(args.key_field) if args.key_field else EXPLICIT_KEY_FIELDS

    # A header field that looks like an attribution key, is populated, and is
    # not in the configured list. Rank 1 is blind to it, and a blind rank is
    # indistinguishable from an absent key in the attribution rate alone. This
    # is the check that ``ack_number`` needed and did not have.
    #
    # Taken before resolution, because this lane writes `header.ack_number` back
    # onto every record it resolves. Measured afterwards it reports its own
    # output as a field nobody reads.
    unconsulted = Counter()
    for rec in records:
        header = rec.get("header") or {}
        if not isinstance(header, dict):
            continue
        for name, cell in header.items():
            if name in key_fields or name in PO_FIELDS:
                continue
            if not KEY_FIELD_NAME.search(str(name)):
                continue
            value = cell.get("value") if isinstance(cell, dict) else cell
            if value not in (None, ""):
                unconsulted[name] += 1
    unconsulted = dict(unconsulted.most_common())

    idx = build_index(records)
    resolved, unattributable = {}, []

    # Two passes: explicit and PO first, then inheritance, which needs donors
    # already resolved.
    for rec in records:
        doc_id = rec.get("document_id", "unknown")
        amount = document_value(rec)["value"]
        r = (
            resolve_explicit(rec, ref, key_fields)
            or resolve_from_lines(rec, ref, key_fields)
            or resolve_po(rec, ref)
        )
        if r and r.get("key"):
            resolved[doc_id] = {
                "document_id": doc_id,
                "amount": amount,
                "ack_number": r["key"],
                "rank": r["rank"],
                "attribution_method": r.get("method") or RANK_METHOD[r["rank"]],
                "attribution_confidence": RANK_CONFIDENCE[r["rank"]],
                "note": r.get("note"),
                "evidence_chain": [doc_id],
            }
        elif r and r.get("rejected_key"):
            resolved[doc_id] = {
                "document_id": doc_id,
                "amount": amount,
                "ack_number": None,
                "rank": None,
                "attribution_method": None,
                "rejected_key": r["rejected_key"],
                "note": r["note"],
            }

    for rec in records:
        doc_id = rec.get("document_id", "unknown")
        if resolved.get(doc_id, {}).get("ack_number"):
            continue
        amount = document_value(rec)["value"]
        r = resolve_inherited(rec, resolved, idx) or resolve_fuzzy(rec, ref)
        if r and r.get("key"):
            prior = resolved.get(doc_id, {})
            resolved[doc_id] = {
                "document_id": doc_id,
                "amount": amount,
                "ack_number": r["key"],
                "rank": r["rank"],
                "attribution_method": RANK_METHOD[r["rank"]],
                "attribution_confidence": RANK_CONFIDENCE[r["rank"]],
                "note": r.get("note"),
                "evidence_chain": (r.get("chain") or []) + [doc_id],
                "rejected_key": prior.get("rejected_key"),
            }
        elif r:
            resolved.setdefault(
                doc_id,
                {
                    "document_id": doc_id,
                    "amount": amount,
                    "ack_number": None,
                    "rank": None,
                    "attribution_method": None,
                    "note": r.get("note"),
                    "candidates": r.get("candidates"),
                },
            )

    output = []
    for rec in records:
        doc_id = rec.get("document_id", "unknown")
        amount = document_value(rec)["value"]
        entry = resolved.get(
            doc_id,
            {
                "document_id": doc_id,
                "amount": amount,
                "ack_number": None,
                "rank": None,
                "attribution_method": None,
                "note": None,
            },
        )

        if not entry.get("ack_number"):
            code = dispositions.get(doc_id, "unresolved")
            credits = (
                credit_by_line(rec, ref, key_fields)
                if code == "unresolved" and args.credit_by_line
                else None
            )
            entry["attribution_method"] = LINE_CREDIT_CODE if credits else "unattributable"
            entry["reason_code"] = LINE_CREDIT_CODE if credits else code
            entry["is_failure"] = not credits and code not in DISPOSITION_CODES
            registered = {
                "document_id": doc_id,
                "amount": amount,
                "reason_code": entry["reason_code"],
                "is_failure": entry["is_failure"],
                "rejected_key": entry.get("rejected_key"),
                "candidates": entry.get("candidates"),
                "note": entry.get("note"),
            }
            if credits:
                # The credits ride on the entry and the register alike, with the
                # authorization that applied the rule, so a credited document
                # always reads back to the lines and the signature behind it.
                for target in (entry, registered):
                    target["line_credits"] = credits
                    target["crediting_authorization"] = args.credit_by_line
            unattributable.append(registered)

        entry.update(resolve_selling_location(rec, ref, entry))
        entry["document_type"] = rec.get("document_type", "unknown")
        # Carry the record forward so completeness and sampling can consume this
        # file directly. Attribution keys ride on the record from here on.
        for key, val in rec.items():
            if key not in entry:
                entry[key] = val
        if entry.get("ack_number"):
            hdr = entry.setdefault("header", {})
            if isinstance(hdr, dict):
                hdr["ack_number"] = {
                    "value": entry["ack_number"],
                    "confidence": entry.get("attribution_confidence"),
                    "source": "system",
                }
        output.append(entry)

    total_value = sum(e["amount"] for e in output)
    attributed = [e for e in output if e.get("ack_number")]
    attributed_value = sum(e["amount"] for e in attributed)
    failures = [u for u in unattributable if u["is_failure"]]
    failure_value = sum(u["amount"] for u in failures)
    disposed_value = sum(u["amount"] for u in unattributable if not u["is_failure"])
    credited = [u for u in unattributable if u["reason_code"] == LINE_CREDIT_CODE]

    by_method = Counter(e["attribution_method"] for e in output)
    value_by_method = defaultdict(float)
    for e in output:
        value_by_method[e["attribution_method"]] += e["amount"]

    loc_resolved = [e for e in output if e.get("selling_location")]
    loc_by_method = Counter(e["method"] for e in output if e.get("method"))

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "reference_rows_ingested": ref.get("rows_ingested", 0),
        "reference_rows_rejected": len(ref.get("rejected_rows", [])),
        "rejected_reference_rows": ref.get("rejected_rows", []),
        "documents": len(output),
        "total_value": round(total_value, 2),
        # How the corpus was valued, so a reader can tell a printed total from
        # one this pipeline summed out of the document's own lines.
        "value_basis": basis_summary(records),
        "attributed_documents": len(attributed),
        "attributed_value": round(attributed_value, 2),
        "attributed_value_pct": round(100 * attributed_value / total_value, 2)
        if total_value
        else 0.0,
        "unresolved_documents": len(failures),
        "unresolved_value": round(failure_value, 2),
        "disposed_documents": len(unattributable) - len(failures),
        "disposed_value": round(disposed_value, 2),
        "credited_by_line_documents": len(credited),
        "credited_by_line_value": round(sum(u["amount"] for u in credited), 2),
        "crediting_authorization": args.credit_by_line,
        "attribution_by_method": dict(by_method),
        "value_by_method": {k: round(v, 2) for k, v in value_by_method.items()},
        "selling_location_resolved": len(loc_resolved),
        "selling_location_by_method": dict(loc_by_method),
        # The names rank 1 actually asked for. An operator reading a low
        # attribution rate needs to be able to tell "no document states a key"
        # apart from "this lane looked under the wrong name".
        "explicit_key_fields": list(key_fields),
        "unconsulted_key_fields": unconsulted,
        "gate_status": "clear" if not failures else "blocked",
    }

    findings = []
    if not args.reference:
        findings.append(
            "No reference table supplied. Resolution is limited to explicit keys "
            "on documents -- ranks 3 and 6 are unavailable and nothing can be "
            "format-validated. Ask the client whether their order-entry or "
            "quoting system can export ACK numbers with dates, customers, and "
            "amounts. It is the highest-value single input to this phase."
        )
    if unconsulted:
        findings.append(
            "Documents state a key under a header field rank 1 was not told to "
            "read: "
            + ", ".join(f"{name} ({count})" for name, count in unconsulted.items())
            + ". These are counted as unresolved. Add the field with --key-field "
            "if it carries this engagement's attribution key."
        )
    if failures:
        findings.append(
            f"{len(failures)} documents worth ${failure_value:,.2f} are "
            "unresolved. The gate is not a high attribution rate -- it is that no "
            "dollar is unaccounted for silently. Either resolve these, or assign "
            "each a Phase 0 reason code so it becomes a disposition rather than a "
            "failure."
        )
    rejected = [e for e in output if e.get("rejected_key")]
    if rejected:
        findings.append(
            f"{len(rejected)} documents carry an attribution key that failed "
            "format validation. These are usually OCR errors on a field nothing "
            "else in the pipeline checks -- work them before treating them as "
            "missing."
        )
    ambiguous = [u for u in unattributable if u.get("candidates")]
    if ambiguous:
        findings.append(
            f"{len(ambiguous)} documents matched more than one reference entry on "
            "customer, date, and amount. Deliberately left unresolved -- picking "
            "the first candidate produces a plausible wrong attribution that "
            "nothing downstream will catch."
        )
    low_rank_value = sum(
        v for k, v in value_by_method.items() if k in ("customer_date_amount", "email_evidence")
    )
    if total_value and low_rank_value / total_value > 0.15:
        findings.append(
            f"${low_rank_value:,.2f} ({round(100 * low_rank_value / total_value, 1)}%) "
            "is attributed by inference rather than by a stated key. Report the "
            "method mix alongside any per-job figure -- an attribution set that is "
            "mostly rank 6 is a different artefact from one that is mostly rank 1."
        )
    unloc = len(output) - len(loc_resolved)
    if unloc:
        findings.append(
            f"{unloc} documents have no resolved selling location. If the client "
            "wants revenue by city, confirm which of the four interpretations they "
            "mean -- selling location, ship-from, customer location, or destination "
            "-- before building anything geographic."
        )
    if credited:
        findings.append(
            f"{len(credited)} documents worth ${summary['credited_by_line_value']:,.2f} are "
            f"credited line by line under {args.credit_by_line}: each money line to the key "
            "it prints. None of them carries a document-level key."
        )
    summary["findings"] = findings or ["All dollars attributed."]

    with open(args.out, "w") as fh:
        json.dump(
            {
                "summary": summary,
                "documents": output,
                "attributions": [
                    {
                        k: v
                        for k, v in e.items()
                        if k
                        in (
                            "document_id",
                            "ack_number",
                            "rank",
                            "attribution_method",
                            "attribution_confidence",
                            "reason_code",
                            "is_failure",
                            "line_credits",
                            "evidence_chain",
                            "selling_location",
                            "method",
                            "amount",
                            "note",
                        )
                    }
                    for e in output
                ],
            },
            fh,
            indent=2,
        )
    with open(args.register, "w") as fh:
        json.dump(
            {
                "summary": {
                    "count": len(unattributable),
                    "failures": len(failures),
                    "failure_value": round(failure_value, 2),
                },
                "register": unattributable,
            },
            fh,
            indent=2,
        )

    if not args.quiet:
        print(f"Documents: {summary['documents']}   Value: ${total_value:,.2f}")
        print(
            f"  attributed : {len(attributed)} (${attributed_value:,.2f}, "
            f"{summary['attributed_value_pct']}%)"
        )
        print(f"  disposed   : {summary['disposed_documents']} (${disposed_value:,.2f})")
        print(f"  unresolved : {len(failures)} (${failure_value:,.2f})")
        print(f"  gate       : {summary['gate_status'].upper()}")
        print("  method mix :")
        for m, c in by_method.most_common():
            print(f"      {m}: {c} docs, ${value_by_method[m]:,.2f}")
        for f in summary["findings"]:
            print(f"  - {f}")
        print(f"\nWritten to {args.out}; register to {args.register}")


if __name__ == "__main__":
    main()
