#!/usr/bin/env python3
"""Export every extracted field with the status that governs it.

`canonical_export.py` admits only review-clear documents and has no override.
That rule is correct and this command does not touch it -- but on a corpus where
every document carries an open exception it means nothing reaches a file at all.
On the commission run that is 0 of 716 documents, while 73,878 of the 83,453
extracted fields are already accepted. Those readings exist, they are
provenance-linked, and there was no way to see them outside the artifacts.

**Export the run's current records artifact, not its consensus artifact.**
A consensus artifact is where acceptance *starts*, not where it ends.
`apply_corroboration.py` and `apply_mappings.py` both append acceptances after
it, and on that run the difference was 26,300 accepted fields against 73,878 --
the same 716 documents and the same 83,453 fields, read one step too early. The
consensus artifact answers "what did two vendors agree", which is a real
question and not the one an export asks. Point this at the newest
`records_with_applied_mappings_v1`, or at whatever artifact the run's own lane
coverage names last; `summary.source_artifact` on each record artifact names
the one it was built from, so the chain can be walked forward.

This command writes all of them, and everything else beside them. Every field of
every document appears exactly once, carrying the evidence that accepted it or
the reason it is withheld. Nothing is dropped and nothing is promoted: a
withheld row is a retained reading, not an approved fact, and it is labelled as
one on the row rather than in a footnote.

A status per kind of evidence, because "accepted" hides the distinction that
decides what an operator may do with a row:

* `accepted_vendor_agreement` -- two independent model vendors read the same
  value. This is the only status the canonical load would consider.
* `accepted_independent_corroboration` -- one model read it and an independent
  non-LLM extractor read the same value on the same page. That defeats a
  misread, not a misplacement, so it is not vendor agreement.
* `accepted_same_document` -- the value matches one already settled elsewhere in
  the same document.
* `accepted_extractor_tiebreak` -- vendors split and the non-LLM extractor
  matched one side.
* `accepted_page_review_amendment` -- an authorized amendment from reading the
  page against its image, made only with evidence independent of the reviewer:
  the non-LLM extractor's text of the page or a retained engine reading. The
  value it replaced stays on the field.
* `accepted_approved_mapping` -- two engines read the value under a printed label
  an approved rule maps to this field. The rule places it; no vendor was asked
  which field it belongs in.
* `accepted_document_arithmetic` -- the document's own arithmetic identifies the
  reading: amount, base and rate must agree, and only one candidate makes them.
* `accepted_derived` -- a structural value the pipeline computed, such as a line
  ordinal. Accepted, but it is not a reading of the page and no vendor attested
  it.
* `single_reading` -- exactly one engine returned a value. Not a disagreement:
  there is no second reading to disagree with. Treating it as contested asks a
  question with no answer, and treating it as agreed claims corroboration that
  does not exist.
* `contested` -- engines returned different values. This is a real dispute and
  the candidates are carried on the row.

Every row also carries `confidence`: one word -- `confirmed`, `corroborated`,
`computed`, `derived` or `unconfirmed` -- for a reader who needs to act on the
cell rather than audit it. A pivoted CRM row carries `row_confidence` and
`unconfirmed_fields`, because a row is only as trustworthy as its weakest cell
and an importer showing a row-level mark needs the names behind it.

This is not a canonical export and it clears no gate. `crm_import_package.py`,
`crm_export_jobs.py` and the rest of the load chain read `canonical_export_v1`
and are unaffected by this artifact; nothing here becomes loadable by being
written down. What it does is make the run's own evidence readable outside the
pipeline, per field, with the provenance needed to decide about it.
"""

import argparse
import collections
import csv
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import entity_resolve
from cli_help import apply_shared_help
from document_value import separator_convention_unclear
from extraction_schema import HEADER_FIELDS, LINE_FIELDS
from page_review import PROPOSALS_ARTIFACT_TYPE
from page_review import money_value as printed_figure
from run_io import empty_output_path, load_manifest
from runtime_config import load_project_env

ARTIFACT_TYPE = "field_extract_v1"
LINE_FIELD = re.compile(r"^lines\[(\d+)\]\.(.+)$")
CORROBORATED_BY = "corroborated_by_independent_extractor"

# Every way a field can be accepted, keyed by the `accepted_by` its lane writes.
#
# This is a registry rather than a chain of comparisons because the failure it
# replaces was silent. `field_status` used to test for one `accepted_by` and
# return `accepted_vendor_agreement` for everything else, so the three lanes
# added after it -- document arithmetic, same-document settlement, and the
# non-LLM extractor tiebreak -- each reported their acceptances as two model
# vendors agreeing. On the commission run that overstated 3,566 fields, in the
# one direction a client must never be misled: toward more evidence than exists.
#
# A lane that writes an `accepted_by` absent from this table is named for that
# rather than folded into the strongest bucket. Registering a new lane is one
# line here; forgetting to costs visibility, not truthfulness.
ACCEPTANCE_STATUS = {
    "agreed_by_independent_vendors": "accepted_vendor_agreement",
    CORROBORATED_BY: "accepted_independent_corroboration",
    "matches_a_settled_value_in_the_same_document": "accepted_same_document",
    "tie_broken_by_independent_extractor": "accepted_extractor_tiebreak",
    "reconciled_by_document_arithmetic": "accepted_document_arithmetic",
    "amended_from_page_review": "accepted_page_review_amendment",
    "approved_source_label_mapping": "accepted_approved_mapping",
}
UNREGISTERED_ACCEPTANCE = "accepted_by_an_unregistered_lane"

# What each status lets a reader claim about the cell, in the words the CRM and
# the review UI show. Several distinct kinds of evidence collapse to
# `corroborated` on purpose: they differ in provenance, which the `status` and
# `accepted_by` columns carry, but not in how far a reader may trust the number.
CONFIDENCE = {
    "accepted_vendor_agreement": "confirmed",
    "accepted_independent_corroboration": "corroborated",
    "accepted_same_document": "corroborated",
    "accepted_extractor_tiebreak": "corroborated",
    # The page read against its image, with the independent extractor's text of
    # the page or a retained engine reading beside the reviewer.
    "accepted_page_review_amendment": "corroborated",
    # Two engines read the value under a label an approved rule maps to this
    # field: the rule places it, and no vendor was asked which field it is.
    "accepted_approved_mapping": "corroborated",
    "accepted_document_arithmetic": "computed",
    "accepted_derived": "derived",
}
UNCONFIRMED = "unconfirmed"

# Derived from the confidence table rather than typed out again. The list used
# to be maintained by hand and had fallen three lanes behind it, so the run's
# own `accepted_fields` total omitted every field those lanes settled.
# `UNREGISTERED_ACCEPTANCE` is deliberately absent: the pipeline accepted it,
# but this command cannot say on what evidence, and a total that counted it
# would be asserting exactly that. It is counted in `fields_by_status` and
# called out in the summary instead of being quietly absorbed either way.
ACCEPTED_STATUSES = tuple(CONFIDENCE)

# The columns a CSV carries, in order. `status` and `document_review_status`
# are both required to decide whether a row may be loaded, so neither is
# optional and neither is at the end where a truncated import would lose it.
COLUMNS = (
    "document_id",
    "confidence",
    "status",
    "document_review_status",
    "field",
    "reading",
    "value",
    "resolved_party",
    "party_key",
    "accepted_by",
    "all_readings",
    "reading_engine_count",
    "document_type",
    "consensus_flag",
    "engine_count",
    "agreeing_engines",
    "missing_from_engines",
    "is_handwritten",
    "source",
    "rule",
    "blocking_review_reasons",
    "source_file",
    "source_page_number",
    "source_sha256",
    "page_pdf",
)


# Fields `entity_resolve.py` treats as a party name. A resolved party belongs on
# these rows and nowhere else: joining every column that happens to match a
# variant string would relabel a description that shares a word with a supplier.
PARTY_FIELDS = frozenset(
    {
        "brand_name",
        "manufacturer_name",
        "supplier_name",
        "vendor_name",
        "customer_name",
        "dealer_name",
        "payer_name",
        "payee_name",
        "seller_name",
        # The firm that specified the product. It resolves like any other
        # counterparty, and on this corpus it is the party the CRM most often
        # had no record of at all.
        "specifier_name",
    }
)


def party_index(entities):
    """Map every name variant a party was resolved from to that party.

    `entity_resolve.py` collapses spelling variants into one party -- 610
    mentions to 33 on one run -- but it writes a party master beside the records
    rather than into them, so an export reading the records alone still shows
    `NORTHGATE`, `NORTHGATE CO LLC` and `Northgate Co.` as three different suppliers.
    Anyone grouping the CSV by brand gets the unresolved answer, which is the
    thing resolving parties exists to prevent.
    """
    index = {}
    for party in (entities or {}).get("parties", []):
        if not isinstance(party, dict):
            continue
        canonical = party.get("canonical_name")
        key = party.get("party_key")
        for variant in [canonical, *(party.get("name_variants") or [])]:
            if variant:
                index.setdefault(str(variant).casefold().strip(), (canonical, key))
    return index


def load_object(path, label):
    """Load a required JSON object."""
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object: {path}")
    return value


def page_provenance(manifest):
    """Index immutable intake provenance by the id consensus actually carries.

    The manifest keys pages on `page_id`; `document_id` is null on it until
    reassembly names one. A consensus document's `document_id` is that
    `page_id`. Joining on `document_id` matches nothing and yields an export
    with every provenance column empty -- which reads as a corpus without
    sources rather than as a join that missed.
    """
    pages = {}
    for page in manifest.get("pages", []):
        page_id = str(page.get("page_id") or "")
        if page_id:
            pages[page_id] = page
    return pages


def blocking_reasons(queue):
    """Map (document, field) and (document, None) to the findings that block it."""
    by_field = {}
    for item in (queue or {}).get("items", []):
        if not isinstance(item, dict):
            continue
        document_id = str(item.get("document_id") or item.get("page_id") or "")
        if not document_id:
            continue
        key = (document_id, str(item.get("field") or ""))
        by_field.setdefault(key, set()).add(str(item.get("reason") or "review_required"))
    return by_field


def field_status(entry):
    """Name what is known about a field, not merely whether it was accepted.

    An acceptance carries how it was earned, and the two ways are not the same
    claim. `apply_corroboration.py` accepts a value one model read where an
    independent non-LLM extractor read the same value on the same page -- 47,578
    fields on the commission run, under a named client decision. That is
    evidence, and it is not vendor agreement: it defeats a misread, not a
    misattribution. Reading only `accepted` reported all of them as two model
    vendors agreeing, which is the one thing the corroboration artifact says of
    itself that it is not.
    """
    flag = str(entry.get("consensus_flag") or "")
    if entry.get("accepted"):
        if flag == "derived_ordinal":
            return "accepted_derived"
        acceptance = entry.get("acceptance")
        accepted_by = acceptance.get("accepted_by") if isinstance(acceptance, dict) else None
        if not accepted_by:
            # Consensus accepts without naming a lane; that is vendor agreement
            # by construction, and it is the only unnamed acceptance there is.
            return "accepted_vendor_agreement"
        return ACCEPTANCE_STATUS.get(str(accepted_by), UNREGISTERED_ACCEPTANCE)
    if flag == "single_engine":
        return "single_reading"
    if flag == "no_consensus":
        return "contested"
    # An unaccepted field whose flag says the engines agreed is neither of the
    # above. Naming it by its flag keeps it visible instead of folding it into a
    # bucket that would misdescribe it.
    return f"withheld_{flag}" if flag else "withheld"


def readings(entry):
    """Return every value an engine actually returned for this field.

    `value` on a consensus field is what consensus *accepted*, so it is null on
    every field it did not accept -- 57,150 of 83,453 on the commission run. The
    readings behind those nulls are on `candidate_values`, and an export that
    showed only `value` reported an empty cell for every one of them. An engine
    read `2022-11-01` off the page and the file said nothing at all.
    """
    candidates = entry.get("candidate_values")
    if isinstance(candidates, (list, tuple, set)):
        found = [item for item in candidates if item is not None]
    elif candidates is not None:
        found = [candidates]
    else:
        found = []
    if not found and entry.get("value") is not None:
        found = [entry["value"]]
    return found


def joined(value):
    """Render a list column without inventing a value for an absent one.

    Sorted as text. A candidate list can hold objects as well as scalars -- a
    table cell carries its own row context, for one -- and ordering the raw
    values raises on the first mixed list rather than rendering it.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return "; ".join(sorted(str(item) for item in value if item is not None))
    return str(value)


def resolved_party(field, reading, parties):
    """Return the party a reading resolves to, on a party field only."""
    base = field.split("].")[-1] if field.startswith("lines[") else field
    base = base[len("header.") :] if base.startswith("header.") else base
    if base not in PARTY_FIELDS or not reading:
        return "", ""
    canonical, key = parties.get(str(reading).casefold().strip(), ("", ""))
    return canonical or "", key or ""


def rows_for(documents, pages, by_field, parties=None):
    """Emit one row per field of every document, in a stable order."""
    rows = []
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("every record document must be an object")
        document_id = str(document.get("document_id") or "")
        page = pages.get(document_id, {})
        fields = document.get("fields")
        if not isinstance(fields, dict):
            raise ValueError(f"record document carries no field map: {document_id}")
        for name in sorted(fields):
            entry = fields[name]
            if not isinstance(entry, dict):
                continue
            reasons = by_field.get((document_id, name), set())
            found = readings(entry)
            single = str(found[0]) if len(found) == 1 else ""
            party_name, party_key = resolved_party(name, single, parties or {})
            status = field_status(entry)
            rows.append(
                {
                    "document_id": document_id,
                    # How far a reader may trust this cell, in one word. The
                    # status says which lane earned it; this says what that is
                    # worth. Anything not accepted -- including an acceptance
                    # from a lane nobody registered -- reads `unconfirmed`.
                    "confidence": CONFIDENCE.get(status, UNCONFIRMED),
                    "status": status,
                    "document_review_status": str(document.get("review_status") or ""),
                    "field": name,
                    # The single reading a row states, when there is exactly one
                    # to state. A contested field has no single reading and this
                    # is empty on purpose: `all_readings` carries its candidates.
                    "reading": single,
                    "value": "" if entry.get("value") is None else str(entry.get("value")),
                    "resolved_party": party_name,
                    "party_key": party_key,
                    "accepted_by": str((entry.get("acceptance") or {}).get("accepted_by") or ""),
                    "all_readings": joined(found),
                    "reading_engine_count": len(found),
                    "document_type": str(document.get("document_type") or ""),
                    "consensus_flag": str(entry.get("consensus_flag") or ""),
                    "engine_count": entry.get("engine_count"),
                    "agreeing_engines": joined(entry.get("agreeing_engines")),
                    "missing_from_engines": joined(entry.get("missing_from_engines")),
                    "is_handwritten": bool(entry.get("is_handwritten")),
                    "source": str(entry.get("source") or ""),
                    "rule": str(entry.get("rule") or ""),
                    "blocking_review_reasons": joined(reasons),
                    "source_file": str(page.get("source_file") or ""),
                    "source_page_number": page.get("source_page_number"),
                    "source_sha256": str(page.get("source_sha256") or ""),
                    "page_pdf": str(page.get("page_pdf") or ""),
                }
            )
    return rows


def summarize(rows, documents, pages):
    """Report what was exported and what could not be provenance-linked."""
    by_status = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    unlinked = sorted({row["document_id"] for row in rows if not row["source_file"]})
    with_reading = sum(1 for row in rows if row["reading_engine_count"])
    by_confidence = {}
    for row in rows:
        by_confidence[row["confidence"]] = by_confidence.get(row["confidence"], 0) + 1
    unregistered = by_status.get(UNREGISTERED_ACCEPTANCE, 0)
    unregistered_lanes = sorted(
        {row["accepted_by"] for row in rows if row["status"] == UNREGISTERED_ACCEPTANCE}
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "artifact_type": ARTIFACT_TYPE,
        "documents": len(documents),
        "fields": len(rows),
        "fields_by_status": dict(sorted(by_status.items())),
        "accepted_fields": sum(by_status.get(name, 0) for name in ACCEPTED_STATUSES),
        "fields_by_confidence": dict(sorted(by_confidence.items())),
        # A lane whose `accepted_by` is not in `ACCEPTANCE_STATUS`. Zero is the
        # expected reading. Anything else means a lane is writing acceptances
        # this export cannot characterize, and it is named here so the fix is
        # to register it rather than to discover the gap in a client's CRM.
        "fields_accepted_by_an_unregistered_lane": unregistered,
        "unregistered_acceptance_lanes": unregistered_lanes,
        # An unaccepted field is not an unread one. Reporting both counts keeps
        # "no engine returned anything" distinguishable from "returned something
        # consensus did not accept", which the status alone does not separate.
        "fields_with_a_reading": with_reading,
        "fields_no_engine_returned": len(rows) - with_reading,
        "manifest_pages": len(pages),
        # Rule 9. A provenance join that matched nothing looks exactly like a
        # corpus with no sources, so the count of rows that could not be linked
        # is reported rather than left to be inferred from empty columns.
        "documents_without_intake_provenance": len(unlinked),
        "documents_without_intake_provenance_sample": unlinked[:10],
        "canonical_load_permitted": False,
        "findings": [
            "Every extracted field is present exactly once, carrying its own status.",
            "`reading` is what an engine returned; `value` is only what consensus "
            "accepted. A row can carry a reading and no accepted value.",
            "A row that is not accepted is a retained reading, not an approved fact.",
            "This artifact clears no control and is not a canonical load; the CRM load "
            "chain reads canonical_export_v1 and is unaffected by it.",
        ],
    }


# Derived from the schema rather than restated. The hand-kept version named
# seven of the seventeen date fields the schema defines, so `period_start`,
# `period_end` and `payment_date` reached a CRM with no typed companion at all
# -- 362 populated cells that a loader could only take as text. This is the
# second place that list had drifted; `validate_extraction.py` was the first.
DATE_COLUMNS = tuple(
    sorted(
        {
            name
            for name in (*HEADER_FIELDS, *LINE_FIELDS)
            if name.endswith("_date") or name in {"period_start", "period_end"}
        }
        | {"memo_date"}
    )
)
CURRENCY_CODES = ("USD", "CAD", "EUR", "GBP", "AUD", "CHF", "MXN")
MONTH_NAMES = {
    name.lower(): index
    for index, name in enumerate(
        (
            "January February March April May June July August September October November December"
        ).split(),
        start=1,
    )
}
ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
# `2023-0430`: the year-first order with its second hyphen lost. The order is
# ISO's either way, so nothing about it is inferred.
ISO_DATE_RUN_TOGETHER = re.compile(r"^(\d{4})-(\d{2})(\d{2})$")
SLASH_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$")
NAMED_DATE = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$")
# A weekday before a date and a time of day after it, as an email or a web page
# prints them: `Fri, Nov 4, 2022, 9:26 AM`, `Apr 13, 2023 at 10:14:20 AM ET`.
WEEKDAY = re.compile(r"^(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)[a-z]*\.?,?\s+", re.I)
TIME_OF_DAY = re.compile(
    r",?\s+(?:at\s+)?\d{1,2}:\d{2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?(?:\s+[a-z]{2,4})?$", re.I
)
# A month named with a four-digit year and no day: `December 2025`, `Dec-2024`.
MONTH_LABEL = re.compile(r"^([A-Za-z]{3,9})\.?[\s,/-]*(\d{4})$")
# Excel's `mmm-yy`: `December-24`, `Sep-24`. Typed only under an operator's
# authorization, and only with the hyphen -- `May 31` is a day with no year.
MONTH_LABEL_TWO_DIGIT = re.compile(r"^([A-Za-z]{3,9})-(\d{2})$")
# A year no statement was written in. `05/24/0122` and `03/17/202` were read
# with a digit lost or gained, and typing them loads year 122 as a real date.
FOUR_DIGIT_YEAR = 1000
# Columns that hold a count rather than money. They load into NUMERIC target
# columns and take the same `__amount` companion money does.
COUNT_COLUMNS = ("quantity",)
COUNT = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")


# The one shape that settles a decimal comma: exactly two digits after the
# comma, any grouping before it in threes -- `2 199,03`, `14 460,05`, `219,90`.
# No US-format number prints it. It is read as one only on a document whose
# own amounts use that convention, because a cell Excel cut short prints
# `12,34` for `12,345.67` on a page of decimal points.
DECIMAL_COMMA = re.compile(r"^-?(?:\d{1,3}(?:[ \u00a0\u202f.]\d{3})+|\d+),\d{2}$")
DECIMAL_POINT = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d+$")
GROUP_SEPARATOR = re.compile(r"[ \u00a0\u202f.]")


def bare_amount(raw):
    """A money reading without its currency code, symbol or accounting parentheses."""
    text = str(raw or "").strip()
    currency = ""
    for code in CURRENCY_CODES:
        pattern = re.compile(rf"\b{code}\b", re.IGNORECASE)
        if pattern.search(text):
            currency = code
            text = pattern.sub("", text).strip()
            break
    text = text.replace("$", "").replace("\u00a3", "").replace("\u20ac", "").strip()
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    return text, currency, negative


def loadable_amount(raw, convention=""):
    """Return a plain decimal string and any currency code, or empty for both.

    The export's own money cells arrive in whatever the page printed --
    `$6,226.00`, `856.13 USD`, `(662.89)`, `n/a`, `2 199,03`. A loader reading
    those into a numeric column gets a string, a null, or a silent zero, and on
    this corpus a naive parse lost $2.8M to the dollar signs alone.

    This adds a column the loader can read without changing the one the page
    said, and refuses rather than guesses. A decimal comma is read only where
    the document has settled it (`convention` is `decimal_comma`, from
    `separator_convention_by_document`): there `2 199,03` is 2199.03, and any
    other comma is refused, since `1,446` could then be either. Elsewhere a
    decimal-comma signal is refused as before -- `12,34` among decimal points is
    a cut cell, not a French amount -- because inferring a separator convention
    from magnitude is the mistake this repository refuses everywhere else.
    """
    text, currency, negative = bare_amount(raw)
    if not text:
        return "", currency
    if convention == "decimal_comma" and DECIMAL_COMMA.match(text):
        text = GROUP_SEPARATOR.sub("", text).replace(",", ".")
    elif separator_convention_unclear(text) or (convention == "decimal_comma" and "," in text):
        return "", currency
    text = text.replace(",", "")
    try:
        # Parsed to prove it is a number, then discarded. The cleaned text is
        # returned instead of a formatted float: `1,778,056.06` through `%g`
        # becomes `1.77806e+06`, which loses cents and is not a number any
        # importer will read back as money.
        float(text)
    except ValueError:
        return "", currency
    if negative and not text.startswith("-"):
        text = f"-{text}"
    return text, currency


def loadable_count(raw):
    """Return a count as a plain number, or empty for anything that is not one.

    A quantity reaches the target's NUMERIC column as text unless it is typed,
    and on the commission run 2,774 did. `12`, `2.5` and `1,200` are counts;
    `***` is a masked cell and `12 EA` names a unit this does not guess about.
    Both come back empty, and the masked one is then named unrendered.
    """
    text = str(raw or "").strip()
    if not COUNT.match(text):
        return ""
    return text.replace(",", "")


def date_text(raw):
    """A date cell's text without a weekday before it or a time of day after it.

    Neither says anything about the date's order: `Fri, Nov 4, 2022, 9:26 AM`
    is `Nov 4, 2022`, and `7/12/2023 2:44 PM` is as ambiguous as `7/12/2023`.
    """
    text = WEEKDAY.sub("", str(raw or "").strip())
    return TIME_OF_DAY.sub("", text).strip()


def month_number(name):
    """A month's number from its name or the start of it (`Sept`), or None."""
    name = name.lower()
    if name in MONTH_NAMES:
        return MONTH_NAMES[name]
    for full, index in MONTH_NAMES.items():
        if full.startswith(name):
            return index
    return None


def loadable_month(raw, two_digit_years=False):
    """The month a cell names without a day, as `YYYY-MM`, or empty.

    `December 2025` and `Dec-2024` name a month, not a day, so they have no ISO
    date -- but a statement's period is a month, and a loader can hold one. On
    the commission run 428 cells named a month this way. A two-digit year stays
    empty: `December-24` is as much the 24th as 2024, and nothing in the cell
    says which. A cell naming a day types as a date instead and leaves this
    empty.

    `two_digit_years` is the operator's answer to that question, never this
    function's: Excel's `mmm-yy` read as a month of this century. On the
    commission run it settled 75 statement periods. A year after this one is
    implausible for a statement and stays empty.
    """
    text = date_text(raw)
    short = MONTH_LABEL_TWO_DIGIT.match(text) if two_digit_years else None
    if short:
        month, year = month_number(short[1]), 2000 + int(short[2])
        if month is None or year > datetime.now(UTC).year:
            return ""
        return f"{year:04d}-{month:02d}"
    label = MONTH_LABEL.match(text)
    if not label:
        return ""
    month, year = month_number(label[1]), int(label[2])
    if month is None or year < FOUR_DIGIT_YEAR:
        return ""
    return f"{year:04d}-{month:02d}"


def loadable_date(raw, order=None):
    """Return an ISO date, or empty where the order cannot be known.

    `2024-05-31` and `May 31, 2024` each state their own order. `05/06/2024`
    does not, and `validate_extraction.py` already refuses it for that reason --
    guessing between May 6th and June 5th is exactly the inference this
    repository declines. `01/08/20...` is truncated on the page itself, and
    `December-24` names no day. All three come back empty rather than invented.
    So does a year below 1000, which is a reading gone wrong, not a date. A
    weekday before the date and a time after it are set aside first, and
    `2023-0430` is the ISO order with a hyphen lost.
    """
    text = date_text(raw)
    if not text:
        return ""
    iso = ISO_DATE.match(text) or ISO_DATE_RUN_TOGETHER.match(text)
    if iso:
        if int(iso[1]) < FOUR_DIGIT_YEAR:
            return ""
        try:
            return datetime(int(iso[1]), int(iso[2]), int(iso[3])).date().isoformat()
        except ValueError:
            return ""
    named = NAMED_DATE.match(text)
    if named:
        month = month_number(named[1])
        if month is not None:
            if int(named[3]) < FOUR_DIGIT_YEAR:
                return ""
            try:
                return datetime(int(named[3]), month, int(named[2])).date().isoformat()
            except ValueError:
                return ""
    slashed = SLASH_DATE.match(text)
    if slashed and order:
        first, second, year = int(slashed[1]), int(slashed[2]), int(slashed[3])
        year = year + 2000 if year < 100 else year
        if year < FOUR_DIGIT_YEAR:
            return ""
        month, day = (first, second) if order == "month_first" else (second, first)
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            return ""
    return ""


ITEM_IDENTITY_COLUMNS = ("description", "item_code", "style", "product_name")
# What a page names its money against when it prints no product column. An
# invoice says what was sold; a commission statement says which job it was sold
# into, and many print no description at all. Reading identity only from the
# product columns called eight correctly-read lines on one statement page
# `money with no item named`, and did the same to every money row on 96
# documents -- so a reader told to drop flagged rows lost $3.9M of commission
# that the pages state plainly. The schema's own definitions are the warrant:
# a job number is "a job or work-order identifier", a project name is "the name
# of the job or site a line was sold into".
JOB_IDENTITY_COLUMNS = (
    "job_number",
    "project_number",
    "project_name",
    "purchase_order_number",
)
# Either kind of identity answers `what is this money for`, which is the only
# question `amount_without_line_identity` asks.
LINE_IDENTITY_COLUMNS = (*ITEM_IDENTITY_COLUMNS, *JOB_IDENTITY_COLUMNS)
# The two columns that decide whether a row is a commission line at all. Kept
# narrow on purpose: `amount_without_line_identity` asks whether a row states
# commission money with nothing to attribute it to, and a freight charge or a
# tax figure is not that question.
MONEY_COLUMNS = ("commission_amount", "commissionable_amount")
# Every column a loader will read as money, which is a wider set than the two
# above. `tax_rate` is excluded -- it is a rate, and its unit is the question
# `stated_commission_rate` already shows this repository refusing to guess.
MONEY_VALUE_COLUMNS = (
    *MONEY_COLUMNS,
    "amount_due",
    "applied_amount",
    "balance_due",
    "charge_amount",
    "deposit_amount",
    "discount_amount",
    "discount_taken",
    "duty_amount",
    "extended_amount",
    "freight_amount",
    "invoice_amount",
    "sales_amount",
    "statement_total",
    "subtotal",
    "total_amount",
    "total_paid",
    "unit_price",
)
EVIDENCE_COLUMNS = (
    "row_confidence",
    "duplicate_of_line",
    "amount_without_line_identity",
    "repeats_a_job_and_amount_above",
    "repeats_a_line_in",
    "differs_from_the_same_line_elsewhere",
    "missing_where_the_same_line_carries_it",
    "text_truncated_in_source",
    "completion_proposed",
    "restates_a_total",
    "brand_name_holds_the_specifier",
    "unreadable_in_source",
    "commission_arithmetic",
    "unconfirmed_fields",
    "fields_vendor_agreed",
    "fields_corroborated",
    "fields_computed",
    "fields_derived",
    "fields_single_reading",
    "fields_contested",
    "fields_unattributed",
    "contested_fields",
    "single_reading_fields",
    "corroborated_fields",
)
CONTEXT_COLUMNS = (
    "document_type",
    "document_review_status",
    "source_file",
    "source_page_number",
    "source_sha256",
)


def line_key(field):
    """Split `lines[7].commission_amount` into its index and column."""
    match = LINE_FIELD.match(str(field))
    return (match.group(1), match.group(2)) if match else None


def pivot(
    rows,
    per_line,
    client_names=(),
    refusals=(),
    brand_names=(),
    removed=(),
    two_digit_years=False,
):
    """Reshape field rows into the record grain a CRM import actually loads.

    One row per field is honest and unloadable. This groups them back into the
    records they were read from -- a commission line, or a document header --
    with each cell carrying the engine's reading.

    A pivoted cell cannot show its own status, so every record carries the
    evidence profile of the cells in it: how many were agreed by two vendors,
    how many rest on a single engine, which ones are contested, and by name. A
    row that hid that would present a single unverified reading exactly like a
    corroborated one, which is the whole reason this file needs a shape at all.

    The evidence counters account for every cell in the row. That is checked
    rather than assumed, because the version of this that shipped counted five
    statuses and left the rest out: 2,885 accepted cells on the commission run
    appeared in no column, so a client adding the counters up got a number
    smaller than the row and no way to tell which cells were missing or why.
    """
    records = {}
    for row in rows:
        parsed = line_key(row["field"])
        if per_line:
            if parsed is None:
                continue
            index, column = parsed
            key = (row["document_id"], index)
            identity = {"document_id": row["document_id"], "line_index": index}
        else:
            if not row["field"].startswith("header."):
                continue
            column = row["field"][len("header.") :]
            key = row["document_id"]
            identity = {"document_id": row["document_id"]}
        record = records.setdefault(
            key,
            {
                **identity,
                "fields_vendor_agreed": 0,
                "fields_corroborated": 0,
                "fields_computed": 0,
                "fields_derived": 0,
                "fields_single_reading": 0,
                "fields_contested": 0,
                "fields_unattributed": 0,
                "_contested": [],
                "_unattributed": [],
                "_single": [],
                "_corroborated": [],
                **{name: row[name] for name in CONTEXT_COLUMNS},
            },
        )
        # The value consensus accepted, then the one unambiguous reading, then
        # nothing. Falling back to `all_readings` put the joined candidate list
        # in the cell -- `"1,750.97; 8,918.38"` where consensus had accepted
        # `8,918.38` -- which is not a number, not on the page, and not what any
        # control decided. It reached 48.7% of the line rows on the commission
        # run, and 72.4% of those cells had an accepted value sitting unused in
        # `value`. A contested cell is left empty on purpose: it is named in
        # `contested_fields`, and its candidates stay in the field-grain export.
        record[column] = row["value"] or row["reading"]
        # A pivoted party column carries the reading; the resolved party goes
        # beside it rather than replacing it. Overwriting the cell would hide
        # what the page actually said, and grouping the CSV by the raw column
        # is exactly the mistake party resolution exists to prevent -- so both
        # are present and the resolved one is named for what it is.
        # The key goes beside the name, because a name is not an identity: an
        # importer joining to the master needs the key, and accounts built by
        # matching `__resolved_party` against it matched nothing at all.
        if row["resolved_party"]:
            record[f"{column}__resolved_party"] = row["resolved_party"]
            record[f"{column}__party_key"] = row["party_key"]
        # Counted by confidence rather than by status, so a lane added later
        # lands in the bucket its evidence earns instead of in none of them.
        # The earlier chain tested five status names and fell through for the
        # rest, which left the three newest lanes counted nowhere: a row's
        # evidence columns no longer added up to the cells in the row, and the
        # cells that went missing were the accepted ones.
        confidence = row["confidence"]
        if confidence == "confirmed":
            record["fields_vendor_agreed"] += 1
        elif confidence == "corroborated":
            record["fields_corroborated"] += 1
            record["_corroborated"].append(column)
        elif confidence == "computed":
            record["fields_computed"] += 1
        elif confidence == "derived":
            record["fields_derived"] += 1
        elif row["status"] == "single_reading":
            record["fields_single_reading"] += 1
            record["_single"].append(column)
        elif row["status"] == "contested":
            record["fields_contested"] += 1
            record["_contested"].append(column)
        else:
            # A withheld flag, or an acceptance from a lane nobody registered.
            # Neither can be vouched for, so neither may leave the row reading
            # confirmed -- and a final bucket is what keeps the counters equal
            # to the cells in the row unconditionally rather than only while
            # every status happens to be one of the six above. Expect zero.
            record["fields_unattributed"] += 1
            record["_unattributed"].append(column)
    out = []

    # Line 10 follows line 9, not line 1. Sorting the index as text ordered a
    # commission statement 0, 1, 10, 11, 2 -- which an importer keeping file
    # order writes into the CRM in that order.
    def order(key):
        return (key[0], int(key[1])) if isinstance(key, tuple) else (key, 0)

    for key in sorted(records, key=order):
        record = records[key]
        contested = record.pop("_contested")
        single = record.pop("_single")
        unattributed = record.pop("_unattributed")
        record["contested_fields"] = joined(contested)
        record["single_reading_fields"] = joined(single)
        record["corroborated_fields"] = joined(record.pop("_corroborated"))
        # A row is only as good as its weakest cell. An importer that shows a
        # row-level mark needs one word for the row and the names behind it,
        # because a row with nine corroborated cells and one contested cell is
        # not a confirmed row and must not display as one.
        unconfirmed = sorted({*contested, *single, *unattributed})
        record["row_confidence"] = UNCONFIRMED if unconfirmed else "confirmed"
        record["unconfirmed_fields"] = joined(unconfirmed)
        out.append(record)
    # Each pass labels the rows the one before it produced, and the typed
    # columns have to exist before anything can judge them.
    records = mark_duplicate_lines(out) if per_line else out
    records = mark_truncated_text(records)
    records = mark_subtotal_rows(records)
    records = mark_party_fields_holding_the_project(records)
    records = mark_brand_holding_the_specifier(records)
    records = mark_rows_naming_the_client(records, client_names)
    records = mark_party_fields_not_a_party(records, refusals, removed)
    records = mark_person_fields_not_one_person(records, client_names, brand_names)
    records = add_loadable_columns(
        records,
        date_order_by_document(rows),
        separator_convention_by_document(rows),
        two_digit_years,
    )
    records = add_email_columns(records)
    # Before the sentinel is written: it repeats down a column by design,
    # and a repeated value is what this reads as evidence.
    records = mark_columns_read_one_row_low(records, per_line)
    records = mark_unreadable_in_source(records)
    records = mark_repeated_blocks(records, per_line)
    records = mark_commission_arithmetic(records)
    records = mark_commission_equal_to_its_base(records)
    # After the arithmetic label: the rates it proves are the evidence here.
    records = mark_rate_columns_holding_an_amount(records, per_line)
    # After it too: a line that reconciles has proved its rate is a percent.
    records = add_percent_columns(records)
    # Last, and only for line records: it keys on the typed commission
    # companion, which `add_loadable_columns` writes above. Run before that and
    # every key is empty and the control silently reports nothing.
    return mark_lines_reading_differently_elsewhere(records) if per_line else records


TEXT_COLUMNS = ("description", "customer_name", "dealer_name", "brand_name", "item_code")


def truncation_candidates(values):
    """Map each cut-off reading to the completions the corpus itself offers.

    Some of this corpus is a wide spreadsheet printed to a narrow page: the
    cells are cut mid-word and, unlike the `...` case, nothing marks it.
    `NORTHGATE C` appears 219 times where the party is `NORTHGATE CO LLC`, and an
    unresolved truncation becomes its own CRM account. There is no marker to
    key on, so the evidence is the corpus: a reading that is a strict prefix of
    a longer reading in the same column, broken mid-word, was cut off.
    """
    by_length = sorted(values, key=len, reverse=True)
    candidates = {}
    for value in values:
        if len(value) < 4 or not value[-1].isalnum():
            continue
        longer = [
            other
            for other in by_length
            if len(other) > len(value) and other.lower().startswith(value.lower())
        ]
        # A longer reading that continues at a word break is a longer name, not
        # this one completed: `Ponte Verra` beside `Ponte Verra; as per ...` is
        # two readings, while `NORTHGATE C` beside `NORTHGATE CO LLC` is one cut
        # short. The completion must continue with a letter: a prefix of a
        # number is not a truncation, and `AP0494` beside `AP04944` is two
        # item codes.
        longer = [other for other in longer if other[len(value)].isalpha()]
        if not longer:
            continue
        # Keep only the maximal completions; one of them is an answer, several
        # are a question.
        maximal = [
            other
            for other in longer
            if not any(o != other and o.lower().startswith(other.lower()) for o in longer)
        ]
        candidates[value] = maximal
    return candidates


# What a document calls a total. These rows are real readings of real printed
# lines, but they restate amounts the lines above already carry.
SUBTOTAL_LABEL = re.compile(
    r"\b(?:p\.?\s*o\.?\s*total|sub-?total|grand\s+total|purchase\s+order\s+totals?"
    r"|total\s+commissions?(?:\s+to\s+date)?|total\s+to\s+date)\b",
    re.I,
)
# A total row need not name its total. A cell that is only `Total`, that opens
# on it with a separator (`Total :`, `Total - Acoustics`), or that closes on it
# (`Northgate Co LLC Total`) is a total by where the word sits. The same shape is
# also a category written into a data row -- `Total - Other` on two sales
# orders -- so it counts only on a row that names no line of its own. A cell
# that merely begins with the word (`Total Station Cabinet`) is a name.
TOTAL_BY_POSITION = re.compile(r"^totals?\s*(?:[-–—:]|$)|\btotals?\s*:?$", re.I)
SUBTOTAL_TEXT_COLUMNS = ("description", "item_code", "style", "product_name")
# A total row's first cell is often blank, and its label then lands in a party
# column. There it also counts only on a row naming no line: one statement
# pasted the next row's `Total Commissions to date:` onto a real job's line.
SUBTOTAL_PARTY_COLUMNS = ("customer_name", "dealer_name")
# What names a line apart from a total's label: a job, an order, or an item.
TOTAL_ROW_IDENTITY_COLUMNS = (*LINE_IDENTITY_COLUMNS, "sales_order_number")


# What an item is identified by, in the order a natural key prefers. A code
# identifies a product; a description only names one.
ITEM_KEY_COLUMNS = ("product_sku", "item_code", "style", "product_name")
# Shapes that are not product identities however often they appear in the
# column. A territory code, a line item number and a project address each
# reached `item_code` on the commission run, and each would have loaded as a
# distinct product.
#
# The bare number was first labelled a count, which was wrong and sent the
# reader looking for a quantity. Pages 240 and 284 settle it: that form prints a
# column headed `Item` holding SAP line item numbers -- 10, 20, 30, then 21, 22
# for the next order -- beside a separate `Order Position`, and prints no
# quantity column at all. The product code is the `Material` column, which is on
# the page and is not in this cell.
NOT_AN_ITEM = (
    (
        "an address",
        re.compile(
            r"\b\d{1,6}\s+[A-Za-z][^,;]{0,30}?\b(?:st|street|ave|avenue|rd|road|blvd|boulevard"
            r"|dr|drive|ln|lane|way|suite|ste|pkwy|parkway|hwy|highway)\b",
            re.I,
        ),
    ),
    ("a territory code", re.compile(r"^\d{2,3}-[A-Z]{2,4}$")),
    ("a line item number", re.compile(r"^\d{1,3}$")),
)


def item_identity(record):
    """Return the first column that identifies this line's product, and its value."""
    for column in ITEM_KEY_COLUMNS:
        value = str(record.get(column) or "").strip()
        if value:
            return column, value
    return "", ""


def not_an_item(value):
    """Name the shape that disqualifies a reading as a product identity, or empty."""
    for name, pattern in NOT_AN_ITEM:
        if pattern.search(value) if name == "an address" else pattern.match(value):
            return name
    return ""


def item_dimension(records):
    """Collapse repeated products into one row each, and say what is not a product.

    The line grain repeats a product on every line that sells it -- 2,728 item
    codes over 880 distinct values on the commission run -- and loading that
    grain as products creates one product per line. This groups them by the
    identity the line states, counts the lines behind each, and carries the
    descriptions seen with it rather than choosing between them.

    Nothing is dropped: a reading whose shape belongs to another column is kept
    as its own row and labelled, because it is a true reading of the page and
    the fix is a mapping decision.
    """
    grouped = {}
    for record in records:
        column, value = item_identity(record)
        if not value:
            continue
        entry = grouped.setdefault(
            value.casefold(),
            {
                "natural_key": value,
                "identified_by": column,
                "item_code": "",
                "descriptions": [],
                "line_rows": 0,
                "documents": set(),
                "not_a_product": not_an_item(value),
            },
        )
        entry["line_rows"] += 1
        entry["documents"].add(str(record.get("document_id") or ""))
        entry["item_code"] = entry["item_code"] or str(record.get("item_code") or "").strip()
        description = str(record.get("description") or "").strip()
        if description and description not in entry["descriptions"]:
            entry["descriptions"].append(description)
    items = []
    for entry in grouped.values():
        items.append(
            {
                "natural_key": entry["natural_key"],
                "identified_by": entry["identified_by"],
                "item_code": entry["item_code"],
                "description": entry["descriptions"][0] if entry["descriptions"] else "",
                "other_descriptions": joined(entry["descriptions"][1:]),
                "description_count": len(entry["descriptions"]),
                "line_rows": entry["line_rows"],
                "source_document_count": len(entry["documents"]),
                "not_a_product": entry["not_a_product"],
            }
        )
    items.sort(key=lambda item: (-item["line_rows"], item["natural_key"].casefold()))
    return items


def mark_brand_holding_the_specifier(records):
    """Flag a row whose brand is really the firm that specified the product.

    The schema named no specifier field for most of this corpus's life, so the
    design firm was written into the nearest company-name field. Where the two
    now read the same, `brand_name` is holding a specifier: `NORCROSS TAMPA` and
    `BSQ - FLORIDA` are architects, not manufacturers. The reading stays -- it
    is what the page says -- and the row says which field it belongs in.
    """
    for record in records:
        brand = str(record.get("brand_name") or "").strip().casefold()
        specifier = str(record.get("specifier_name") or "").strip().casefold()
        record["brand_name_holds_the_specifier"] = "yes" if brand and brand == specifier else ""
    return records


def mark_rows_naming_the_client(records, client_names):
    """Flag a row whose company field names the client rather than a counterparty.

    The client is printed on nearly every page -- as a statement's addressee, as
    the agent of record, in a letterhead -- and lands in whichever company field
    the form puts it nearest. On this corpus that is 891 line rows whose
    `brand_name` reads the client, which would make the rep firm its own
    furniture brand and its own account.

    `entity_resolve.py` already refuses those as counterparties, so no resolved
    party is created. This is for the mapping that reads the raw column anyway:
    the reading stays, because the page does say it, and the row says the value
    is the client. The names come from the party master's own summary rather
    than a second flag, so the two lanes cannot disagree about who the client is.
    """
    # A field named on both grains -- `dealer_name` is a header field and a line
    # field -- must be considered once, or the row reports it twice.
    fields = sorted({name for name in (*HEADER_FIELDS, *LINE_FIELDS) if name.endswith("_name")})
    for record in records:
        named = sorted(
            field
            for field in fields
            if str(record.get(field) or "").strip()
            and entity_resolve.is_the_client(record.get(field), client_names)
        )
        record["fields_naming_the_client"] = "; ".join(named)
    return records


# The grades under which the page review's `the page never prints this row` is
# carried on the row: the independent extractor prints the row's figure fewer
# times than the export counts it, the row carries no money to count, or a
# second vendor's reading holds every other figure in the column and this one
# fewer times. Where the extractor prints it as often, two sources disagree and
# nothing is labelled.
ROW_NOT_PRINTED_GRADES = (
    "extractor_prints_it_fewer_times",
    "no_money_on_row",
    "second_vendor_prints_it_fewer_times",
)


def label_rows_not_printed(records, proposals):
    """Label -- never drop -- each line the page review says the page does not print.

    A proposal names a line by its index in the export the reviewer read. A line
    that no longer holds the figure the review named has changed since -- a
    re-read or an amendment -- and the index may now name another row, so it is
    counted and left unlabelled rather than labelled blind.
    """
    by_line = {
        (str(record["document_id"]).rsplit("__", 1)[-1], str(record["line_index"])): record
        for record in records
    }
    for record in records:
        record["page_review_row_not_printed"] = ""
    outcome = collections.Counter()
    for proposal in proposals.get("rows_not_printed") or []:
        grade = proposal.get("evidence")
        if grade not in ROW_NOT_PRINTED_GRADES:
            continue
        record = by_line.get((str(proposal.get("page")), str(proposal.get("line_index"))))
        if record is None:
            outcome["no_such_line"] += 1
        elif printed_figure(record.get(proposal.get("column"))) != printed_figure(
            proposal.get("value")
        ):
            outcome["row_changed_since_the_review"] += 1
        else:
            record["page_review_row_not_printed"] = grade
            outcome[grade] += 1
    return records, dict(sorted(outcome.items()))


def mark_party_fields_holding_the_project(records):
    """Flag a line whose company field is really the job the line was sold into.

    A commission statement heads the column `Project` and prints a building or a
    suite there -- `One Bayfront Suite 540`, `Meridiana Corporation Center`. The
    schema named no project field for most of this corpus's life, so the value
    went to the nearest company-name field and 314 lines across 68 documents
    carried a project as their dealer. Loaded, each one becomes a company the
    client sells through.

    The evidence is on the row: the company field repeats the row's own
    description, and the row carries a job or project number, which is what a
    project has and a dealer does not. Both readings are true readings of the
    page, so neither is moved and neither is dropped -- 59 of the 69 names also
    appear as a dealer on rows where they are not the description, and refusing
    them outright would delete real dealers on ambiguous evidence.
    `project_name` now exists for the value; this says which rows are waiting
    for it.
    """
    fields = ("dealer_name", "customer_name", "brand_name")
    for record in records:
        description = str(record.get("description") or "").strip().casefold()
        # A job number and a project number are the same evidence. Some
        # statements print a `Project #` and no job number at all, and requiring
        # the job alone missed every project on them.
        job = str(record.get("job_number") or record.get("project_number") or "").strip()
        named = []
        if description and job:
            named = sorted(
                field
                for field in fields
                if str(record.get(field) or "").strip().casefold() == description
            )
        record["party_field_holds_the_project"] = "; ".join(named)
    return records


# The fields that name a person. `entity_resolve.py` leaves them out of the party
# master on purpose, so nothing there says whether a reading is a person at all.
PERSON_FIELDS = ("sales_representative_name", "contact_name")
# Words that name a role, a form or a report rather than someone.
ROLE_WORDS = frozenset(
    {
        "rep",
        "reps",
        "install",
        "sales",
        "team",
        "department",
        "dept",
        "manager",
        "created",
        "report",
    }
)
SEVERAL_PEOPLE = re.compile(r";|\band\b|&", re.I)
EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def mark_party_fields_not_a_party(records, refusals, removed=()):
    """Say which company fields hold a reading the party master refused.

    `entity_resolve.py` refuses a reading that is not a party -- a number with
    no letter, a column heading, a placeholder, a total's label, an adjustment,
    a street address -- and keeps it as an exception rather than resolving it. The row still carries the
    reading, because the page does say it, and a mapping that builds accounts
    from the raw column builds one from each: on the commission run a CRM showed
    `COMPANY` and `4100 NE 20TH AVE HARBOR POINT, FL 33000` as accounts.
    This names the field and the reason on the row. Matched on the document,
    the header or line the resolver read, the field and the reading, so a line
    that happens to print the same text elsewhere is not marked. The client has
    its own flag, `fields_naming_the_client`, and is not repeated here.

    A party decision removes a name the rules cannot see -- `Unknown
    Specifier`, printed on 84 lines, is a placeholder with two words in it --
    and the master keeps it under `removed_as_not_a_party`. The decision is
    about the name, not the field, so it is matched on the document and the
    reading alone.
    """
    refused = {
        (
            entry.get("document_id"),
            entry.get("scope"),
            entry.get("field"),
            str(entry.get("raw_name") or "").strip(),
        ): entry.get("reason", "")
        for entry in refusals
        if isinstance(entry, dict)
    }
    decided = {
        (document_id, entity_resolve.loose_name(name))
        for party in removed
        if isinstance(party, dict)
        for document_id in party.get("source_document_ids") or []
        for name in [party.get("canonical_name"), *(party.get("name_variants") or [])]
        if name
    }
    for record in records:
        scope = f"lines[{record['line_index']}]" if "line_index" in record else "header"
        named = []
        for field in sorted(name for name in record if name.endswith("_name")):
            value = str(record.get(field) or "").strip()
            reason = refused.get((record["document_id"], scope, field, value))
            if not reason and (record["document_id"], entity_resolve.loose_name(value)) in decided:
                reason = "removed_as_not_a_party_by_decision"
            if value and reason:
                named.append(f"{field}: {reason}")
        record["party_fields_not_a_party"] = "; ".join(named)
    return records


def person_reading_problem(value, client_names, brands):
    """Why a reading in a person field is not one person's name, or `""`."""
    if SEVERAL_PEOPLE.search(value):
        return "names several people"
    if any(character.isdigit() for character in value):
        return "carries a number"
    if entity_resolve.is_the_client(value, client_names):
        return "names the client"
    words = entity_resolve.loose_name(value).split()
    if len(words) < 2:
        return "a single word"
    if any(word in ROLE_WORDS for word in words):
        return "names a role or a report"
    name = entity_resolve.normalize_name(value)
    # One changed letter is a misreading: `Lindew World` is Linden World, and the
    # word-based score halves on it.
    if any(
        entity_resolve.one_edit_apart(name, brand) or entity_resolve.similarity(name, brand) >= 0.86
        for brand in brands
    ):
        return "names a company"
    return ""


def mark_person_fields_not_one_person(records, client_names, brand_names):
    """Say which person fields hold something other than one person's name.

    A sales representative's field on this corpus held a HALVOR territory code
    (`150-NGC`), a report's name and form date from its footer (`COMMREP`,
    `Sept 97`), a role (`INSTALL REP`), a brand (`Marlow / Bramwell`), the
    client, and two people in one cell. A CRM that makes a contact of each
    reading made 37 contacts on the commission run, most of them not people.
    The reading stays; the row names the field and why. A brand is recognised
    against the party master's brands only, so a person printed in a dealer
    field elsewhere is still read as a person here.
    """
    brands = {entity_resolve.normalize_name(name) for name in brand_names} - {""}
    for record in records:
        named = []
        for field in PERSON_FIELDS:
            value = str(record.get(field) or "").strip()
            problem = person_reading_problem(value, client_names, brands) if value else ""
            if problem:
                named.append(f"{field}: {problem}")
        record["person_fields_not_one_person"] = "; ".join(named)
    return records


def add_email_columns(records):
    """Put a usable address beside every email column, and nothing when it is not one.

    Six HALVOR pages print the company's website where a contact's email goes,
    and the reading was carried as the email. The reading stays; the companion
    is empty, so a loader that uses it never writes a website into an email.
    """
    for record in records:
        for column in [name for name in list(record) if name.endswith("_email")]:
            value = str(record.get(column) or "").strip()
            record[f"{column}__email"] = value if EMAIL_SHAPE.match(value) else ""
    return records


def is_a_total_label(cell):
    """Whether a cell reads as a total's label, by its words or where they sit."""
    return bool(SUBTOTAL_LABEL.search(cell) or TOTAL_BY_POSITION.search(cell))


def names_its_own_line(record):
    """Whether a row names a job, an order or an item beyond a total's label."""
    for column in TOTAL_ROW_IDENTITY_COLUMNS:
        cell = str(record.get(column) or "").strip()
        if cell and not is_a_total_label(cell):
            return True
    return False


def mark_subtotal_rows(records):
    """Flag a line that restates a total the lines above it already carry.

    A statement prints `P.O. Total:` and `Total Commissions to date:` as rows
    of the same table, so they extract as lines and carry a commission like any
    other. Summing the line grain then counts that money twice: on this corpus
    204 such rows carry $429,484.61, and only seven of them tripped any other
    flag. The row is a true reading and stays; what it needs is a label saying
    a total must exclude it.

    Each cell is read on its own, because a label is recognised by where the
    word `Total` sits in it. Reading the named phrases alone left a statement's
    bare `Total :` row counted as one more line: its grand total of $148,247.29,
    on a page whose own lines come to $25,415.42. A label known only by where
    the word sits, or found in a party column, counts on a row naming no line
    of its own: the same shapes turn up as a category or a stray label on a
    real line, and flagging that line would hide money the page prints.
    """
    for record in records:
        labels = [str(record.get(column) or "").strip() for column in SUBTOTAL_TEXT_COLUMNS]
        parties = [str(record.get(column) or "").strip() for column in SUBTOTAL_PARTY_COLUMNS]
        named = any(SUBTOTAL_LABEL.search(cell) for cell in labels)
        placed = any(is_a_total_label(cell) for cell in labels + parties)
        labelled = named or (placed and not names_its_own_line(record))
        record["restates_a_total"] = "yes" if labelled else ""
    return records


def mark_truncated_text(records):
    """Flag readings the source cut off, and propose the one completion it offers.

    Nothing is rewritten. A truncated reading is what the page shows, so it
    stays; the completion is a proposal carried beside it, and it is only made
    where the corpus offers exactly one.
    """
    for column in TEXT_COLUMNS:
        values = {
            str(record.get(column) or "").strip()
            for record in records
            if str(record.get(column) or "").strip()
        }
        candidates = truncation_candidates(values)
        for record in records:
            value = str(record.get(column) or "").strip()
            found = candidates.get(value) if value else None
            if not found:
                continue
            record["text_truncated_in_source"] = column
            record["completion_proposed"] = found[0] if len(found) == 1 else ""
    for record in records:
        record.setdefault("text_truncated_in_source", "")
        record.setdefault("completion_proposed", "")
    return records


def document_date_order(values):
    """Settle a document's slash-date order from its own dates, or refuse.

    `05/06/2024` is May 6th or June 5th and the page does not say which, so
    `validate_extraction.py` refuses it outright. But a document that also
    carries `11/16/2022` has proved its own order: 16 is no month. That is the
    document stating the convention rather than anyone inferring it from
    magnitude, and it is the same move `commission_rate_unit` makes when it
    settles percent-or-fraction from the lines that reconcile.

    362 of the 403 documents carrying slash dates settle it this way, with no
    document contradicting itself. A document that cannot settle it keeps its
    dates unparsed rather than guessing.
    """
    day_first = month_first = False
    for value in values:
        parts = SLASH_DATE.match(date_text(value))
        if not parts:
            continue
        if int(parts[1]) > 12:
            day_first = True
        if int(parts[2]) > 12:
            month_first = True
    if day_first and month_first:
        # The document uses both orders, or a reading is wrong. Either way it
        # has not settled anything.
        return None
    if month_first:
        return "month_first"
    return "day_first" if day_first else None


def date_order_by_document(rows):
    """Settle each document's date order once, from every date it states.

    Scoped to the document rather than to the grain being written. A header
    date is usually the only date on its row, so a header pivot settles almost
    nothing on its own -- 115 header dates stayed unparsed while the same
    document's line dates had already proved the order.
    """
    dates = collections.defaultdict(list)
    for row in rows:
        column = row["field"].rsplit(".", 1)[-1]
        if column in DATE_COLUMNS:
            value = row["value"] or row["reading"]
            if value:
                dates[row["document_id"]].append(value)
    return {name: document_date_order(values) for name, values in dates.items()}


def separator_convention(values):
    """Settle a document's decimal separator from its own amounts, or refuse.

    The same move `document_date_order` makes: the document states its
    convention rather than anyone inferring it. murbrook prints its Canadian
    statements `2 199,03` and `14 460,05` and never a decimal point, so every
    comma on them is a decimal comma. A document printing both, or neither,
    settles nothing and keeps the standing refusal.
    """
    comma = point = False
    for value in values:
        text = bare_amount(value)[0]
        comma = comma or bool(DECIMAL_COMMA.match(text))
        point = point or bool(DECIMAL_POINT.match(text))
    return "decimal_comma" if comma and not point else ""


def separator_convention_by_document(rows):
    """Settle each document's decimal separator once, from every amount it states."""
    amounts = collections.defaultdict(list)
    for row in rows:
        if row["field"].rsplit(".", 1)[-1] in MONEY_VALUE_COLUMNS:
            value = row["value"] or row["reading"]
            if value:
                amounts[row["document_id"]].append(value)
    return {name: separator_convention(values) for name, values in amounts.items()}


# The operator's sentinel for a cell the source never rendered. A CRM cannot be
# asked to notice an empty column, and it must not be handed a plausible zero:
# an out-of-range constant is wrong loudly rather than quietly.
UNREADABLE_SENTINEL = "-99999"
# `####` is Excel's column-too-narrow marker, scientific notation is a number
# that overflowed its column, and a trailing ellipsis is a value cut off. A run
# of asterisks masks a value the same way -- `***` stood in three quantity cells
# -- while a single `*` is a footnote mark and is left alone. All of these mean
# the page does not carry the value, however well it was read.
UNREADABLE_IN_SOURCE = re.compile(
    r"^#+$|^\*{2,}$|^[+-]?\d+(?:\.\d+)?[Ee][+-]?\d+$|(?:\.\.\.|\u2026)$"
)


def unreadable(raw):
    """Say whether a reading is a marker the source printed in place of a value."""
    return bool(UNREADABLE_IN_SOURCE.search(str(raw or "").strip()))


def mark_unreadable_in_source(records):
    """Name every column whose reading the source never rendered.

    The sentinel goes in the typed money companion, where a number is expected
    and `-99999` is unmistakable. It is deliberately not written into a date
    companion: a date column typed as a date cannot hold `-99999`, and writing
    it there would trade a detectable gap for an unparseable one. Those columns
    are named here instead, which is what a loader needs either way.
    """
    for record in records:
        found = [
            column
            for column in record
            if not column.endswith(("__amount", "__iso", "__currency"))
            and unreadable(record.get(column))
        ]
        for column in found:
            companion = f"{column}__amount"
            if companion in record:
                record[companion] = UNREADABLE_SENTINEL
        record["unreadable_in_source"] = joined(sorted(found))
    return records


def num(raw):
    """Read one of the typed companion columns as a number, or None."""
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


# Two rows is the shortest run that is evidence rather than coincidence. One
# cell equal to a neighbouring column's previous value happens by chance on a
# page of round numbers; four in a row does not.
MIN_SHIFTED_RUN = 2


def money_value(text):
    """Return a non-zero, readable amount from a typed companion, or None.

    Zero is excluded deliberately: a column of zeros matches a column of zeros
    at every offset, and that says nothing about whether either was read low.
    So is the unreadable sentinel, which repeats down a column by design.
    """
    if str(text).strip() == UNREADABLE_SENTINEL:
        return None
    value = num(text)
    return value or None


def shifted_cells(rows, columns):
    """Name every cell holding the value a sibling column carried one row up.

    A table read one row low is the quietest way money goes wrong here. The
    number in the cell is real -- it is printed on the page -- so no format
    check, no range check and no arithmetic check on that row can see it. Only
    the neighbours can: a cell equal to what a *different* money column held on
    the row above, while disagreeing with that column on its own row, and doing
    it on consecutive rows, is a column the extractor tracked one line low.

    Found on 17 documents of this corpus. On one of them the page prints a
    single money column, `Adjusted Net Sale`, and no commission at all -- yet
    fourteen rows carry a commission, each of them the previous row's net sale,
    with the page's own subtotal on the row below them. That is $800,557 of
    commission the pages never stated.
    """
    flagged = collections.defaultdict(set)
    for column in columns:
        for other in columns:
            if other == column:
                continue
            run = []
            for index in range(1, len(rows)):
                value = money_value(rows[index].get(f"{column}__amount"))
                above = money_value(rows[index - 1].get(f"{other}__amount"))
                here = money_value(rows[index].get(f"{other}__amount"))
                if value is not None and above is not None and here is not None:
                    if value == above and value != here:
                        run.append(index)
                        continue
                if len(run) >= MIN_SHIFTED_RUN:
                    for hit in run:
                        flagged[hit].add((column, other))
                run = []
            if len(run) >= MIN_SHIFTED_RUN:
                for hit in run:
                    flagged[hit].add((column, other))
    return flagged


def mark_columns_read_one_row_low(records, per_line):
    """Flag each line whose money column carries the row above it.

    Nothing is moved. The value is a true reading of the page and which row it
    belongs to is a question this cannot answer on its own -- the run says the
    column drifted, not where it started. What the row gets is the sentence an
    operator needs to go and look.
    """
    columns = sorted(set(MONEY_VALUE_COLUMNS))
    grouped = collections.defaultdict(list)
    for record in records:
        record["column_read_one_row_low"] = ""
        if per_line:
            grouped[record.get("document_id")].append(record)
    for rows in grouped.values():
        rows.sort(key=lambda row: int(row.get("line_index") or 0))
        for index, pairs in shifted_cells(rows, columns).items():
            rows[index]["column_read_one_row_low"] = "; ".join(
                f"{column} carries the previous row's {other}" for column, other in sorted(pairs)
            )
    return records


def commission_reconciles(record):
    """Check a line against its own arithmetic: base x rate must be the commission.

    A rate is printed rounded, so `2.3751%` appears as `2.38%` and an exact
    comparison calls a correct line wrong; the test is whether the true ratio
    rounds to what the document printed. A row missing any of the three terms
    proves nothing and is left unjudged rather than called a failure.
    """
    base = num(record.get("commissionable_amount__amount"))
    commission = num(record.get("commission_amount__amount"))
    printed = str(record.get("stated_commission_rate") or "").strip().rstrip("%")
    rate = num(printed.replace(",", ""))
    if None in (base, rate, commission) or not base or not rate:
        return ""
    if abs(base * rate / 100 - commission) <= max(0.02, abs(commission) * 0.001):
        return "reconciles"
    places = len(printed.split(".")[1]) if "." in printed else 0
    actual = commission / base * 100
    if abs(round(actual, places) - round(rate, places)) <= 10 ** -max(places, 2):
        return "reconciles"
    return "does_not_reconcile"


def rates_stated_by(rows):
    """Return the commission rates a document proves on its own reconciling rows.

    Taken from the document rather than from a list kept here. A rate table in
    the code would be a guess about this corpus that goes stale the moment
    another one arrives, and the document already says which rates it uses.
    """
    rates = set()
    for row in rows:
        if row.get("commission_arithmetic") != "reconciles":
            continue
        rate = num(str(row.get("stated_commission_rate") or "").rstrip("%"))
        if rate and 0 < rate <= 100:
            rates.add(rate)
    return rates


def mark_rate_columns_holding_an_amount(records, per_line):
    """Flag a rate cell that is really the commission, with the rate lost.

    One page shifted its money left by a column: the commission landed in
    `stated_commission_rate` and the rate itself fell off the row. Eleven lines
    read as rates of 182.93 and 961.65 percent, and $5,060 of commission was
    invisible to every total because the column it belongs in is empty.

    Proved against the document's own rates, not an assumption about them: the
    value equals this line's commissionable amount times a rate that this same
    document states on rows whose arithmetic reconciles. Nothing is moved --
    which column the rate fell out of is not something this can answer.
    """
    grouped = collections.defaultdict(list)
    for record in records:
        record["rate_column_holds_an_amount"] = ""
        if per_line:
            grouped[record.get("document_id")].append(record)
    for rows in grouped.values():
        rates = rates_stated_by(rows)
        if not rates:
            continue
        for record in rows:
            if record.get("commission_amount__amount"):
                continue
            value = num(str(record.get("stated_commission_rate") or "").rstrip("%"))
            base = num(record.get("commissionable_amount__amount"))
            if not value or not base:
                continue
            if any(abs(base * rate / 100 - value) <= 0.01 for rate in rates):
                record["rate_column_holds_an_amount"] = (
                    f"{value} is this line's commission, not a rate; "
                    "the rate the page states is not on the row"
                )
    return records


MIN_REPEATED_BLOCK = 3
BLOCK_MATCH_RATIO = 0.8


def block_signature(record):
    """The part of a line that identifies it when its labels are missing."""
    return (
        record.get("commission_amount__amount") or "",
        record.get("commissionable_amount__amount") or "",
        record.get("extended_amount__amount") or "",
        str(record.get("description") or "")[:12],
    )


def repeated_block_offset(rows):
    """Return where a document's own table starts over, or None.

    An engine that reads a page twice returns the table twice, and the copy
    arrives *less* complete than the original -- on one page the second pass
    dropped every sales-order number and every customer. `mark_duplicate_lines`
    compares whole rows, so the emptier copy does not match and the money is
    counted twice: page 297 prints eight lines totalling $4,966.50 and the
    export carried sixteen totalling $9,933.00.

    Only a copy that starts after the original ends is a repeated table. A page
    that genuinely prints the same line eight times -- and one here does --
    matches itself at small offsets, and treating that as a duplicate would
    throw away money the page really states.
    """
    count = len(rows)
    for offset in range((count + 1) // 2, count - MIN_REPEATED_BLOCK + 1):
        pairs = [
            (block_signature(a), block_signature(b))
            for a, b in zip(rows, rows[offset:], strict=False)
            if any(block_signature(a)) or any(block_signature(b))
        ]
        if len(pairs) < MIN_REPEATED_BLOCK:
            continue
        same = sum(1 for a, b in pairs if a == b and any(a))
        if same < MIN_REPEATED_BLOCK or same / len(pairs) < BLOCK_MATCH_RATIO:
            continue
        # A table has more than one kind of row. A page that prints the same
        # line eight times -- and one here does -- matches itself at half its
        # own length, and calling that a second table would delete money the
        # page really states. Variety in the original block is what tells a
        # table read twice from a line printed twice.
        if len({a for a, _ in pairs if any(a)}) < 2:
            continue
        return offset
    return None


def mark_repeated_blocks(records, per_line):
    """Flag every line belonging to a second copy of the document's own table.

    Nothing is dropped. The copy is a real reading of the page -- the page was
    simply read twice -- and which of the two copies to keep is a decision for
    whoever loads it. What the rows get is the sentence that stops a total
    counting the page twice.
    """
    grouped = collections.defaultdict(list)
    for record in records:
        record["repeats_an_earlier_block"] = ""
        if per_line:
            grouped[record.get("document_id")].append(record)
    for rows in grouped.values():
        rows.sort(key=lambda row: int(row.get("line_index") or 0))
        offset = repeated_block_offset(rows)
        if offset is None:
            continue
        for index, record in enumerate(rows[offset:]):
            record["repeats_an_earlier_block"] = (
                f"this line repeats line {index} of the same document; "
                "the table was read twice and a total must exclude this block"
            )
    return records


# The amounts a commission could plausibly be computed on, widest first.
COMMISSION_BASES = (
    "commissionable_amount",
    "invoice_amount",
    "sales_amount",
    "extended_amount",
    "charge_amount",
)


def mark_commission_equal_to_its_base(records):
    """Flag a line whose commission is the amount it is a commission on.

    A commission equal to its own base is a rate of 100%, which is not a thing.
    It means one number was read into two columns, and it happens where the page
    prints no commission at all: the HALVOR open order reports have a single
    money column headed `Adjusted Net Sale` and a `Total Net Sale` at the foot,
    and 88 rows across 16 documents carried $3.25M of "commission" that is those
    net sales copied across.

    Invisible to every other control. The arithmetic check needs a base and a
    rate and these pages state neither, so the rows are `not_provable`; the
    value is well-formed, in range, and really is printed on the page. Only the
    equality gives it away.
    """
    for record in records:
        commission = num(record.get("commission_amount__amount"))
        record["commission_equals_its_own_base"] = ""
        if not commission or str(record.get("commission_amount__amount")) == UNREADABLE_SENTINEL:
            continue
        for base in COMMISSION_BASES:
            value = num(record.get(f"{base}__amount"))
            if value is not None and value == commission:
                record["commission_equals_its_own_base"] = (
                    f"equal to {base}; a commission is not 100% of what it is a "
                    "commission on, so one number reached two columns"
                )
                break
    return records


def commission_no_control_can_test(records):
    """Return the commission money on documents no arithmetic check can reach.

    `commission_arithmetic` needs a base and a rate on the row. Where a page
    states neither, every line is `not_provable`, and a rate quoted over the
    provable rows alone answers a question nobody asked: on this corpus 96.7% of
    provable rows reconcile, while 35.8% of the money is on documents where
    nothing is provable at all. One of them prints no commission column and
    carries $1.67M of commission.

    The repository's own rule is that a control which processed nothing is
    failed rather than clear. This is that rule applied to an accuracy figure:
    the denominator travels with the rate.
    """
    tested = collections.defaultdict(bool)
    for record in records:
        if record.get("commission_arithmetic") in ("reconciles", "does_not_reconcile"):
            tested[record.get("document_id")] = True
    total = 0.0
    rows = 0
    for record in records:
        if tested[record.get("document_id")]:
            continue
        amount = num(record.get("commission_amount__amount"))
        if amount and str(record.get("commission_amount__amount")) != UNREADABLE_SENTINEL:
            total += amount
            rows += 1
    return round(total, 2), rows


def mark_commission_arithmetic(records):
    """Label each line with what its own arithmetic says, and carry it either way.

    Most failures here are the document disagreeing with itself -- page 6 of the
    commission run prints `7,646.16 x 8% = 2,038.98`, which is wrong on the page
    while every other row on it is right. That is a real reading of a real page
    and it is carried, labelled, so the CRM can see the problem rather than
    inherit it silently.
    """
    for record in records:
        record["commission_arithmetic"] = commission_reconciles(record)
    return records


def add_loadable_columns(records, order, conventions=None, two_digit_years=False):
    """Put a typed column beside every money and date column, never over one."""
    conventions = conventions or {}
    for record in records:
        for column in [name for name in list(record) if name in MONEY_VALUE_COLUMNS]:
            amount, currency = loadable_amount(
                record[column], conventions.get(record["document_id"], "")
            )
            record[f"{column}__amount"] = amount
            if currency:
                record[f"{column}__currency"] = currency
        for column in [name for name in list(record) if name in COUNT_COLUMNS]:
            record[f"{column}__amount"] = loadable_count(record[column])
        for column in [name for name in list(record) if name in DATE_COLUMNS]:
            record[f"{column}__iso"] = loadable_date(
                record[column], order.get(record["document_id"])
            )
            record[f"{column}__month"] = loadable_month(record[column], two_digit_years)
    return records


# Rates a loader reads as a percent. `exchange_rate` is a factor, not one.
PERCENT_COLUMNS = ("stated_commission_rate", "tax_rate")
PLAIN_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


def loadable_percent(raw, proven):
    """The percent a rate cell states, when its unit is certain, or empty."""
    text = str(raw or "").strip().replace("−", "-")
    printed = text.endswith("%")
    number = text[:-1].strip() if printed else text
    if not PLAIN_NUMBER.match(number) or not (printed or proven):
        return ""
    return number


def add_percent_columns(records):
    """Put the percent a rate states beside it, only where its unit is proven.

    A commission rate is printed `10%`, `10.00` and `1.00`, and a number alone
    does not say whether it is a percent or a fraction: deciding by its size is
    inferring a unit from magnitude. A `%` on the page says so, and so does the
    line's own arithmetic -- `commission_reconciles` tests base x rate / 100
    against the commission, so a line that reconciles has proved its rate a
    percent. `tax_rate` has no arithmetic here and is typed only with its `%`.
    `<column>__percent` holds the number the page printed; its name carries the
    unit, and nothing is converted to a fraction.
    """
    for record in records:
        for column in [name for name in list(record) if name in PERCENT_COLUMNS]:
            proven = (
                column == "stated_commission_rate"
                and record.get("commission_arithmetic") == "reconciles"
            )
            record[f"{column}__percent"] = loadable_percent(record[column], proven)
    return records


def mark_duplicate_lines(records):
    """Name the earlier line each row repeats, without removing either.

    An engine padded one page of eight lines out to a hundred, repeating a
    single row ninety-two times with nothing changing but the ordinal. 584 line
    rows across 106 documents on the commission run are repeats of a row
    already in the same document. Loaded as they stand they multiply that
    document's commission by twelve.

    Nothing is deleted: a repeat is a reading the pipeline retained, and it may
    be a real one -- a statement can legitimately bill the same item twice. The
    row keeps its place and its evidence, and points at the line it duplicates
    so an importer can exclude it deliberately rather than discovering the
    total is wrong afterwards.

    The ordinal is excluded from the comparison. It is assigned rather than
    read, so it differs on every row and made every duplicate look distinct.
    """
    ignore = {
        "line_index",
        "line_number",
        "ordinal",
        "duplicate_of_line",
        "amount_without_line_identity",
    }
    seen = {}
    for record in records:
        signature = (
            record["document_id"],
            tuple(
                sorted(
                    (name, str(value))
                    for name, value in record.items()
                    if name not in ignore and name not in EVIDENCE_COLUMNS
                )
            ),
        )
        first = seen.get(signature)
        record["duplicate_of_line"] = "" if first is None else str(first)
        if first is None:
            seen[signature] = record["line_index"]
        record["amount_without_line_identity"] = (
            "true" if carries_money(record) and not names_an_item(record) else ""
        )
    return mark_repeats_across_documents(mark_repeated_job_amounts(records))


def mark_repeated_job_amounts(records):
    """Name the earlier line of this document already stating this job's money.

    `duplicate_of_line` compares every column, so it catches only a row emitted
    twice unchanged. A page read in two passes emits the same job and the same
    commission twice with different columns filled -- the customer on one pass,
    the dealer on the other -- and those rows are not equal anywhere, so the
    duplicate check clears them and a total counts the commission twice.

    Until the job columns counted as identity this was hidden: the second
    emission usually carries no description, so `amount_without_line_identity`
    excluded it for the wrong reason. Widening identity without this check
    would have started counting that money twice.

    A repeat must match both the job and the amount. The same job legitimately
    carries several lines at different amounts, and the same amount recurs
    across different jobs, so neither alone names a repeat.
    """
    for record in records:
        record["repeats_a_job_and_amount_above"] = ""
    seen = {}
    for record in records:
        job = next(
            (
                str(record.get(name) or "").strip()
                for name in JOB_IDENTITY_COLUMNS
                if str(record.get(name) or "").strip()
            ),
            "",
        )
        amount = str(record.get("commission_amount") or "").strip()
        if not job or not amount:
            continue
        signature = (record["document_id"], job, amount)
        first = seen.get(signature)
        if first is None:
            seen[signature] = record["line_index"]
            continue
        record["repeats_a_job_and_amount_above"] = str(first)
    return records


def mark_repeats_across_documents(records):
    """Name another document already carrying this exact line.

    These statements are periodic, and a project stays on them until it is
    paid, so the same line legitimately recurs month after month. That is not
    an error in the page and nothing here removes it -- but a CRM that loads
    every statement's lines counts the same commission once per statement. On
    this corpus 3,272 of the 7,605 unflagged rows repeat a line in another
    document, carrying $3.8M, 36.8% of the money.

    The row names the first document the line appeared in, so an importer can
    decide deliberately whether it is loading statements or projects.

    The signature spans every identity column, job as well as product. Matching
    on the product columns alone would have made two description-less rows
    carrying the same amount look like the same line once the job columns
    started counting as identity, which on these statements is a common
    coincidence rather than a repeat.
    """
    first = {}
    for record in records:
        if not names_an_item(record) or not carries_money(record):
            record["repeats_a_line_in"] = ""
            continue
        signature = tuple(
            str(record.get(name) or "") for name in (*LINE_IDENTITY_COLUMNS, *MONEY_COLUMNS)
        )
        origin = first.get(signature)
        if origin is None or origin == record["document_id"]:
            first.setdefault(signature, record["document_id"])
            record["repeats_a_line_in"] = ""
            continue
        record["repeats_a_line_in"] = origin
    return records


# A line must appear on at least this many documents before the corpus is asked
# what it usually says. Two readings that differ leave no majority to name, and
# calling one of them the odd one out would be a coin toss presented as a
# finding.
MIN_DOCUMENTS_FOR_A_MAJORITY = 3


def recurring_line_key(record):
    """Key a line by the job it names and the commission it states, or nothing.

    These statements are periodic: the same job stays on them until it is paid,
    printing the same figures each month. That repetition is free evidence
    about fields no arithmetic can test -- a project name, a customer, a date --
    and it is the only evidence this corpus offers for them.

    A zero commission is refused as a key. Hundreds of distinct lines state
    0.00, and grouping them together compared readings of different lines and
    reported every one of them as disagreeing. The commissionable amount stands
    in for it, because the lines that state no commission are exactly the ones
    worth checking: a cancelled project prints its full commissionable amount
    beside a 0% rate, and the stamp saying so is what goes missing.
    """
    job = next(
        (
            str(record.get(name) or "").strip()
            for name in JOB_IDENTITY_COLUMNS
            if str(record.get(name) or "").strip()
        ),
        "",
    )
    amount = money_value(record.get("commission_amount__amount")) or money_value(
        record.get("commissionable_amount__amount")
    )
    if not job or amount is None:
        return None
    return (job, str(amount))


def mark_lines_reading_differently_elsewhere(records):
    """Name the fields where this row disagrees with the same line elsewhere.

    Nothing here changes a value or decides which reading is right. It reports
    that the same printed line was read two ways, so a reviewer can look. The
    majority is not a vendor agreeing and not an acceptance: it is the corpus
    repeating itself, which is weaker evidence than either and stronger than
    the nothing these fields have now.

    An empty cell where the majority carries a value is named separately. It is
    a different failure -- the reading was lost rather than misread -- and it is
    the one that hides: a `PROJECT CANCELLED` stamp printed on twenty
    statements survived on twelve, landed in a neighbouring column on one, and
    left no trace on seven, where a cancelled project reads as a live one.

    Fields are taken from the schema rather than listed here, so a field added
    to the schema is compared without this function being touched.
    """
    compared = [
        name
        for name in LINE_FIELDS
        if name
        not in (
            "line_number",
            "ordinal",
            *JOB_IDENTITY_COLUMNS,
            "commission_amount",
            "commissionable_amount",
        )
    ]
    groups = collections.defaultdict(list)
    for record in records:
        key = recurring_line_key(record)
        if key is not None:
            groups[key].append(record)
    for record in records:
        record["differs_from_the_same_line_elsewhere"] = ""
        record["missing_where_the_same_line_carries_it"] = ""
    for group in groups.values():
        if len({record["document_id"] for record in group}) < MIN_DOCUMENTS_FOR_A_MAJORITY:
            continue
        for name in compared:
            counts = collections.Counter(str(record.get(name) or "").strip() for record in group)
            if len(counts) < 2:
                continue
            # Sorting before `max` keeps the winner stable when two readings tie,
            # so the same corpus reports the same finding on every run.
            usual = max(sorted(counts), key=counts.__getitem__)
            for record in group:
                value = str(record.get(name) or "").strip()
                if value == usual:
                    continue
                column = (
                    "missing_where_the_same_line_carries_it"
                    if not value and usual
                    else "differs_from_the_same_line_elsewhere"
                )
                named = [part for part in record[column].split("; ") if part]
                named.append(name)
                record[column] = "; ".join(sorted(set(named)))
    return records


def names_an_item(record):
    """Whether the row says what the money is for.

    A product column and a job column answer the same question. Only the
    product columns were read here until a statement page that prints no
    description at all had all eight of its correct lines called anonymous.
    """
    return any(str(record.get(name) or "").strip() for name in LINE_IDENTITY_COLUMNS)


def carries_money(record):
    """Whether the row states a commission or commissionable amount."""
    return any(str(record.get(name) or "").strip() for name in MONEY_COLUMNS)


def pivot_columns(records, identity):
    """Order pivoted columns so identity and evidence never fall off the end."""
    fixed = set(identity) | set(EVIDENCE_COLUMNS) | set(CONTEXT_COLUMNS)
    values = sorted({name for record in records for name in record if name not in fixed})
    return [*identity, *values, *EVIDENCE_COLUMNS, *CONTEXT_COLUMNS]


def write_csv(path, rows, columns=COLUMNS):
    """Write the rows a spreadsheet or staging table can read."""
    with empty_output_path(Path(path)).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(columns), restval="")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main(argv=None):
    """Write the complete field-level extract, and optionally its CSVs."""
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "records",
        help=(
            "The run's current retained record artifact -- normally the newest "
            "records_with_applied_mappings_v1. A consensus artifact is accepted and is "
            "usually the wrong input: acceptances are appended after it."
        ),
    )
    parser.add_argument("--manifest", required=True, help="intake ingestion_manifest.json")
    parser.add_argument(
        "--final-review",
        help=(
            "Final review queue JSON. Without it the blocking_review_reasons column is "
            "empty for every row, which is indistinguishable from a corpus with no "
            "findings; the summary reports whether it was supplied."
        ),
    )
    parser.add_argument("--out", required=True, help="New field-extract artifact path.")
    parser.add_argument("--csv", help="Also write every row as CSV at this path.")
    parser.add_argument(
        "--accepted-csv",
        # Built from the registry that selects the rows, so it names every lane
        # that can accept one. Typed out, it called the whole subset two vendors
        # agreeing, which on the commission run's export 43 was 35,545 of 88,320.
        help=(
            "Also write only the accepted rows as CSV: every row whose status is one of "
            f"{', '.join(ACCEPTED_STATUSES)}. Only accepted_vendor_agreement means two "
            "independent vendors read the same value; each other status names the "
            "different evidence that accepted its row, and a row accepted by an "
            "unregistered lane is left out. It is still not a canonical load."
        ),
    )
    parser.add_argument(
        "--lines-csv",
        help=(
            "Also write one row per commission line, pivoted back into the record grain a "
            "CRM import loads. Each row carries how many of its cells two vendors agreed, "
            "how many rest on one engine, and which are contested, by name."
        ),
    )
    parser.add_argument(
        "--documents-csv",
        help="Also write one row per document from its header fields, with the same evidence profile.",
    )
    parser.add_argument(
        "--items-csv",
        help=(
            "Also write the distinct products the line grain names, one row each. "
            "Loading the line grain as products creates one product per line; this "
            "collapses the repeats and labels a reading whose shape is not a product."
        ),
    )
    parser.add_argument(
        "--parties",
        help=(
            "Party master from entity_resolve.py. Without it the resolved_party column "
            "is empty on every row, which reads as a corpus whose names resolve to "
            "nothing; the summary reports whether it was supplied."
        ),
    )
    parser.add_argument(
        "--two-digit-year-months",
        metavar="AUTHORIZATION",
        help=(
            "Type a month printed with a two-digit year (`December-24`, Excel's "
            "mmm-yy) as that month of 20YY in <column>__month, under the named "
            "operator authorization. Without it such a cell stays untyped, because "
            "nothing in the cell says whether 24 is a year or a day."
        ),
    )
    parser.add_argument(
        "--rows-not-printed",
        metavar="PROPOSALS",
        help=(
            "Proposals from page_review.py --proposals. Each line the page review says "
            "the page does not print is labelled in page_review_row_not_printed with its "
            "evidence -- the extractor prints its figure fewer times, the row carries no "
            "money, or a second vendor's reading holds every other figure and this one "
            "fewer times -- and is never dropped. A line no longer holding the figure the "
            "review named is counted and left unlabelled. Needs --lines-csv and "
            "--rows-not-printed-authorization."
        ),
    )
    parser.add_argument(
        "--rows-not-printed-authorization",
        metavar="AUTHORIZATION",
        help="The operator authorization the row labels are carried under.",
    )
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args(argv)
    try:
        if args.two_digit_year_months is not None and not args.two_digit_year_months.strip():
            raise ValueError("--two-digit-year-months needs the authorization's name")
        if bool(args.rows_not_printed) != bool((args.rows_not_printed_authorization or "").strip()):
            raise ValueError("--rows-not-printed and --rows-not-printed-authorization go together")
        row_proposals = None
        if args.rows_not_printed:
            if not args.lines_csv:
                raise ValueError("--rows-not-printed labels the line grain; give --lines-csv")
            row_proposals = load_object(args.rows_not_printed, "page review proposals")
            if row_proposals.get("artifact_type") != PROPOSALS_ARTIFACT_TYPE:
                raise ValueError(
                    f"not a {PROPOSALS_ARTIFACT_TYPE} artifact: {args.rows_not_printed}"
                )
            if not any(
                proposal.get("evidence") in ROW_NOT_PRINTED_GRADES
                for proposal in row_proposals.get("rows_not_printed") or []
            ):
                raise ValueError(
                    "the proposals name no row at a grade that is labelled; "
                    "a label lane that reads nothing has not run"
                )
        records = load_object(args.records, "records")
        documents = records.get("documents")
        if not isinstance(documents, list):
            raise ValueError("record artifact carries no document list")
        manifest = load_manifest(args.manifest)
        pages = page_provenance(manifest)
        queue = load_object(args.final_review, "final review queue") if args.final_review else None
        entities = load_object(args.parties, "party master") if args.parties else None
        parties = party_index(entities)
        # Who the client is was settled when the party master was resolved. Read
        # it from there rather than adding a second flag here, so the export and
        # the resolver cannot disagree about it.
        client_names = list((entities or {}).get("summary", {}).get("client_names_declared", []))
        # What the resolver refused, and why, so a row can say which of its
        # company fields hold something that is not a party; and the brands,
        # so a person field that names one says so.
        refusals = list((entities or {}).get("refused_not_a_name", []))
        removed = list((entities or {}).get("removed_as_not_a_party", []))
        brand_names = [
            name
            for party in (entities or {}).get("parties", [])
            if isinstance(party, dict) and "brand" in (party.get("roles") or [])
            for name in [party.get("canonical_name"), *(party.get("name_variants") or [])]
            if name
        ]
        rows = rows_for(documents, pages, blocking_reasons(queue), parties)
        if not rows:
            raise ValueError("record artifact carries no fields to export")
        summary = summarize(rows, documents, pages)
        summary["final_review_queue_consulted"] = bool(args.final_review)
        summary["party_master_consulted"] = bool(args.parties)
        summary["two_digit_year_months_authorization"] = args.two_digit_year_months
        summary["rows_not_printed_authorization"] = args.rows_not_printed_authorization
        summary["rows_with_a_resolved_party"] = sum(1 for row in rows if row["resolved_party"])
        summary["distinct_resolved_parties"] = len(
            {row["party_key"] for row in rows if row["party_key"]}
        )
        # The path is claimed first, so a run that would clobber fails before any
        # CSV is written; the file is written last, so its summary carries the
        # counts each grain adds below. Written first, it carried none of them.
        out_path = empty_output_path(Path(args.out))
        if args.csv:
            write_csv(args.csv, rows)
        if args.accepted_csv:
            write_csv(
                args.accepted_csv,
                [row for row in rows if row["status"] in ACCEPTED_STATUSES],
            )
        for path, per_line, identity in (
            (args.lines_csv, True, ("document_id", "line_index")),
            (args.documents_csv, False, ("document_id",)),
        ):
            if not path:
                continue
            records = pivot(
                rows,
                per_line,
                client_names,
                refusals,
                brand_names,
                removed,
                two_digit_years=bool(args.two_digit_year_months),
            )
            if not records:
                raise ValueError(f"no records to pivot for {path}")
            if per_line and row_proposals is not None:
                records, summary["rows_not_printed_labels"] = label_rows_not_printed(
                    records, row_proposals
                )
            write_csv(path, records, pivot_columns(records, identity))
            summary["line_records" if per_line else "document_records"] = len(records)
            grain = "line" if per_line else "document"
            summary[f"{grain}_records_with_a_party_field_not_a_party"] = sum(
                1 for record in records if record["party_fields_not_a_party"]
            )
            summary[f"{grain}_records_with_a_person_field_not_one_person"] = sum(
                1 for record in records if record["person_fields_not_one_person"]
            )
            summary[f"{grain}_months_from_two_digit_years"] = sum(
                1
                for record in records
                for column in DATE_COLUMNS
                if record.get(f"{column}__month")
                and MONTH_LABEL_TWO_DIGIT.match(date_text(record.get(column)))
            )
            if per_line and args.items_csv:
                items = item_dimension(records)
                write_csv(args.items_csv, items, list(items[0]) if items else ["natural_key"])
                summary["item_records"] = len(items)
                summary["item_records_not_a_product"] = sum(
                    1 for item in items if item["not_a_product"]
                )
            if per_line:
                summary["duplicate_line_records"] = sum(
                    1 for record in records if record["duplicate_of_line"] != ""
                )
                summary["line_records_without_item_identity"] = sum(
                    1 for record in records if record["amount_without_line_identity"]
                )
                summary["line_records_repeating_a_job_amount"] = sum(
                    1 for record in records if record["repeats_a_job_and_amount_above"]
                )
                summary["line_records_reading_differently_elsewhere"] = sum(
                    1 for record in records if record["differs_from_the_same_line_elsewhere"]
                )
                summary["line_records_missing_a_field_the_same_line_carries"] = sum(
                    1 for record in records if record["missing_where_the_same_line_carries_it"]
                )
                untestable, untestable_rows = commission_no_control_can_test(records)
                summary["commission_no_control_can_test"] = untestable
                summary["line_records_no_control_can_test"] = untestable_rows
        payload = {"schema_version": "1.0", "summary": summary, "rows": rows}
        out_path.write_text(json.dumps(payload, indent=2) + "\n")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Field extract export failed: {exc}")
    if not args.quiet:
        print(f"Fields exported: {summary['fields']} over {summary['documents']} documents")
        for name, count in summary["fields_by_status"].items():
            print(f"  {count:7d}  {name}")
        print(f"  accepted total: {summary['accepted_fields']}")
        print(f"  carrying a reading: {summary['fields_with_a_reading']}")
        print(f"  no engine returned a value: {summary['fields_no_engine_returned']}")
        if summary.get("duplicate_line_records"):
            print(
                f"  line rows repeating an earlier line: "
                f"{summary['duplicate_line_records']} of {summary['line_records']} "
                "-- see duplicate_of_line; loading them multiplies the document's total"
            )
        if summary.get("line_records_without_item_identity"):
            print(
                f"  line rows stating money but naming no item: "
                f"{summary['line_records_without_item_identity']} of {summary['line_records']} "
                "-- see amount_without_line_identity; some are the page's own subtotals"
            )
        if summary.get("line_records_repeating_a_job_amount"):
            print(
                f"  line rows restating a job and amount already above them: "
                f"{summary['line_records_repeating_a_job_amount']} of "
                f"{summary['line_records']} -- see repeats_a_job_and_amount_above; "
                "a page read in two passes emits each line twice"
            )
        if summary.get("line_records_reading_differently_elsewhere"):
            print(
                f"  line rows the same line reads differently on another document: "
                f"{summary['line_records_reading_differently_elsewhere']} of "
                f"{summary['line_records']}, and "
                f"{summary['line_records_missing_a_field_the_same_line_carries']} missing a "
                "field the others carry -- see differs_from_the_same_line_elsewhere; these "
                "are fields no arithmetic can test, and the corpus is the only witness"
            )
        if summary.get("commission_no_control_can_test"):
            print(
                f"  commission no arithmetic check can test: "
                f"{summary['commission_no_control_can_test']:,.2f} over "
                f"{summary['line_records_no_control_can_test']} rows -- these documents state "
                "no base or no rate, so any reconciliation rate excludes them"
            )
        if summary.get("rows_not_printed_labels"):
            print(
                "  rows the page review says the page does not print: "
                + ", ".join(
                    f"{count} {kind}" for kind, count in summary["rows_not_printed_labels"].items()
                )
            )
        if not summary["final_review_queue_consulted"]:
            print("  blocking findings: NOT CONSULTED (no --final-review supplied)")
        if summary["party_master_consulted"]:
            print(
                f"  rows resolved to a party: {summary['rows_with_a_resolved_party']} "
                f"across {summary['distinct_resolved_parties']} parties"
            )
        else:
            print("  party resolution: NOT CONSULTED (no --parties supplied)")
        if summary["documents_without_intake_provenance"]:
            print(
                "  documents without intake provenance: "
                f"{summary['documents_without_intake_provenance']}"
            )
        print("  canonical load permitted: no")
    return 0


if __name__ == "__main__":
    sys.exit(main())
