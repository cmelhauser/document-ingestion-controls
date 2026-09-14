#!/usr/bin/env python3
"""Fill what the export leaves empty from what the run already knows, labelled.

The export carries what the pages were read as, and much of it is empty: most
line rows name no brand though their own document's letterhead does, a job
printed on eighteen statements carries its customer on fifteen of them, and a
dealer's branch sits inside its name. None of that needs a new reading. It needs
the run's own evidence carried across -- and carried visibly, because a value
the page never printed in that cell is not a reading of it.

So every filled value goes in columns of its own beside the cell it fills --
`<column>__inferred`, `<column>__inferred_by`, `<column>__inferred_evidence` --
and never over what the page says. A loader that wants only readings ignores
them; one that accepts inference loads them labelled. The methods, in the order
they run, and the first to fill a cell keeps it:

* `two_vendors_agree` -- a document's type, where the engines' reading of it
  and the page review name the same kind. The only method resting on two
  vendors.
* `document_letterhead` -- a line's brand from its own document, never the
  client's name.
* `same_line_on_other_statements` -- a cell the same job and amount carries on
  at least two other statements, where every statement carrying it agrees.
  Where they disagree the cell stays empty: a majority is the corpus repeating
  itself, not a reading.
* `same_key_on_other_lines` -- a line's customer or dealer from the lines of
  the same maker printing its purchase order, sales order, job, project or
  transaction number, where every such line names one party the master keyed.
  It is scored first: each line printing its party is hidden in turn, and a
  field the method names correctly on fewer than 95% of them fills nothing.

An inferred party carries the master's key of the readings it came from, in
`<column>__inferred_party_key`, so it joins its account as a reading does.
* `printed_in_the_name` -- a location number, a state code, or a branch the
  party's own name prints. A bare trailing word is a branch label, never
  claimed as a city: `Univ of Bayside Founders Village - Lighting` has the same
  shape as `Workfields Inc - Jacksonville`.
* `iso_3166_1_alpha2` -- the code for a country name the ISO short-name table
  holds. A name it does not hold is reported, never guessed.
* `e164` -- a North American phone number in E.164, one number per column,
  its extension kept and its labelled kind (office, direct, mobile) named.
* `public_source` -- who a party is, from an operator-compiled file of public
  sources, each fact carrying its URL and how firmly it matched.

With `--parties` it also builds the two dimensions a CRM loads beside the
grains, from the filled rows:

* `--out-accounts` -- one row per resolved party the grains name as a party,
  from `<field>__party_key` and never the raw reading, leaving out the
  client's own fields, a field holding the job and a reading the resolver
  refused.
* `--out-contacts` -- one row per person the person fields name. Spellings of
  one person join on the same letters, a middle initial, or the same email; an
  email or phone is kept only where an email naming the person is printed with
  the name, and a company only where that email's domain names one of the
  document's parties or the client.

It accepts nothing, clears no control, and writes new files only.
"""

import argparse
import collections
import csv
import glob
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import field_extract_export as fe
from cli_help import apply_shared_help
from entity_resolve import is_the_client, loose_name, normalize_name, split_location_identifier
from run_io import empty_output_path, retained_response_tokens, retained_responses

ARTIFACT_TYPE = "export_augmentation_v1"
INFERRED = "__inferred"
BY = "__inferred_by"
EVIDENCE = "__inferred_evidence"
# The master's key for an inferred party, carried from the readings behind it.
INFERRED_PARTY_KEY = "__inferred_party_key"
# Columns that are derivatives of a reading rather than readings to fill.
DERIVED = (
    "__amount",
    "__iso",
    "__month",
    "__currency",
    "__resolved_party",
    "__party_key",
    INFERRED,
    BY,
    EVIDENCE,
    INFERRED_PARTY_KEY,
)
# Name columns that do not name a party, so a place in them is not a branch.
NOT_A_PARTY = ("brand_name", "project_name")
UNTYPED = ("", "unknown")

# How the engines name a document, and the page review's kind each agrees
# with. `commission_report` and `commission_statement` are two vendors' words
# for one kind; an engine's `commission_report` against the review's
# `open_order_report` is a disagreement and fills nothing.
ENGINE_KIND = {
    "commission_statement": "commission_statement",
    "commission_report": "commission_statement",
    "payment_confirmation": "remittance",
    "remittance_advice": "remittance",
}
KIND_TYPE = {"commission_statement": "commission_statement", "remittance": "remittance_advice"}

US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DC DE FL GA HI IA ID IL IN KS KY LA MA MD ME MI MN MO MS MT "
    "NC ND NE NH NJ NM NV NY OH OK OR PA PR RI SC SD TN TX UT VA VT WA WI WV WY".split()
)
# A state code that names a city as often as a state. `SYSTEMS SUPPLY - LA` is
# Los Angeles as readily as Louisiana, and picking one would be a guess.
AMBIGUOUS_STATE_CODES = frozenset({"LA"})
# A state a name spells out after its dash: `Empall Office - FLORIDA`.
STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "puerto rico": "PR", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}  # fmt: skip
# `Workfields - Ft Meyers, FL`: a place the name prints with its state.
CITY_AND_STATE = re.compile(r"\s[-–]\s+([A-Za-z][A-Za-z.' ]*?),\s*([A-Z]{2})\s*$")
# `Workfields Inc - Jacksonville`: the branch a name prints after a spaced dash.
BRANCH = re.compile(r"\s[-–]\s+([A-Za-z][A-Za-z.' ]*[A-Za-z.])\s*$")
# `CORPORATE QUARTERS-FL`, `ONE WORKFLOOR (CA)`, `W B PINE / NY`: a state code
# set off by punctuation. A bare space is not enough -- `MARLOW/BRAMWELL IN` is
# `INC` cut short, not Indiana.
STATE_CODE = re.compile(r"[-,/(.]\s*([A-Z]{2})\)?\s*$")

# ISO 3166-1 short names as this corpus's geography prints them, keyed by
# `loose_name`. A name missing here is reported rather than guessed at.
ISO_ALPHA2 = {
    "united states of america the": "US",
    "united states of america": "US",
    "united states": "US",
    "usa": "US",
    "u s a": "US",
    "u s": "US",
    "puerto rico": "PR",
    "canada": "CA",
    "mexico": "MX",
}

NANP = re.compile(
    r"(?:\+?1[\s.-]*)?\(?([2-9]\d{2})\)?[\s.-]*([2-9]\d{2})[\s.-]*(\d{4})"
    r"(?:\s*(?:x|ext\.?|extension)\s*(\d{1,6}))?",
    re.I,
)
# `O. 1 310-555-0150 x 4008; D. 1 323-555-0174`: a kind named before a number.
PHONE_LABEL = re.compile(r"\s*([ODMC])\.\s")
PHONE_KINDS = {"O": "office", "D": "direct", "M": "mobile", "C": "mobile"}


def cell(row, column):
    """One cell's text, trimmed."""
    return str(row.get(column) or "").strip()


def page_of(document_id):
    """The page id a document id ends in."""
    return str(document_id).rsplit("__", 1)[-1]


def columns_like(row, test):
    """The reading columns of a row that pass `test`, listed before any is filled."""
    return [name for name in row if not name.endswith(DERIVED) and test(name)]


def infer(row, column, value, method, evidence, tally, party_key=""):
    """Set one inferred value beside a cell, unless an earlier method already did.

    A party carries the master's key of the readings it came from, so a loader
    joins it to its account the way it joins a reading, and still sees that it
    was inferred.
    """
    if row.get(column + INFERRED):
        return
    row[column + INFERRED] = value
    row[column + BY] = method
    row[column + EVIDENCE] = evidence
    if party_key:
        row[column + INFERRED_PARTY_KEY] = party_key
    tally[(method, column)] += 1


def engine_type(record):
    """What the engines read a document's type as, from the record artifact."""
    raw = record.get("model_document_type")
    value = raw.get("value") if isinstance(raw, dict) else raw
    return str(value or "").strip()


def fill_document_types(documents, lines, engine_types, review_kinds, tally):
    """A document's type where the engines and the page review name the same kind."""
    typed = {}
    for row in documents:
        if cell(row, "document_type") not in UNTYPED:
            continue
        engine = engine_types.get(row["document_id"], "")
        kind = review_kinds.get(page_of(row["document_id"]), "")
        if ENGINE_KIND.get(engine) != kind:
            continue
        typed[row["document_id"]] = KIND_TYPE[kind]
        evidence = f"the engines read {engine}; the page review read {kind}"
        infer(row, "document_type", KIND_TYPE[kind], "two_vendors_agree", evidence, tally)
    for row in lines:
        found = typed.get(row["document_id"])
        if found and cell(row, "document_type") in UNTYPED:
            infer(row, "document_type", found, "two_vendors_agree", "its document's type", tally)


def fill_brands_from_letterhead(lines, documents_by_id, client_names, tally):
    """A line's brand from its own document's letterhead, never the client's name."""
    for row in lines:
        if cell(row, "brand_name"):
            continue
        brand = cell(documents_by_id.get(row["document_id"], {}), "brand_name")
        if brand and not is_the_client(brand, client_names):
            evidence = "the brand its own document prints"
            infer(row, "brand_name", brand, "document_letterhead", evidence, tally)


def compared_fields():
    """The line fields the same job's other statements can speak for.

    Dates are left out. This corpus fills `transaction_date` from six different
    printed columns, so a date carried from another statement may be a
    different date altogether -- and the one independent check available found
    a carried date on its own page only three times in four.
    """
    skipped = ("line_number", "ordinal", *fe.JOB_IDENTITY_COLUMNS)
    return [
        name
        for name in fe.LINE_FIELDS
        if name not in (*skipped, "commission_amount", "commissionable_amount")
        and not name.endswith("_date")
    ]


def fill_from_the_same_line(lines, client_names, tally):
    """Cells the same job and amount carries, unanimously, on other statements.

    Returns how many empty cells were left because the statements disagree.
    """
    groups = collections.defaultdict(list)
    for row in lines:
        key = fe.recurring_line_key(row)
        if key is not None:
            groups[key].append(row)
    disagreed = 0
    for group in groups.values():
        if len({row["document_id"] for row in group}) < fe.MIN_DOCUMENTS_FOR_A_MAJORITY:
            continue
        for name in compared_fields():
            empty = [row for row in group if not cell(row, name)]
            carriers = [row for row in group if cell(row, name)]
            if not empty or not carriers:
                continue
            values = {cell(row, name) for row in carriers}
            if len(values) > 1:
                disagreed += len(empty)
                continue
            statements = {row["document_id"] for row in carriers}
            value = values.pop()
            if len(statements) < 2 or is_the_client(value, client_names):
                continue
            evidence = f"{len(statements)} statements print this job and amount with it"
            party_key = carried_party_key(carriers, name)
            for row in empty:
                infer(row, name, value, "same_line_on_other_statements", evidence, tally, party_key)
    return disagreed


def carried_party_key(carriers, name):
    """The master's key every carrier of a party value holds, or empty.

    Only where each carrier names a party: a reading its row says holds the
    client, the job or a refused name carries no key across, and carriers
    holding different keys carry none.
    """
    keys = set()
    for row in carriers:
        if not names_a_party(row, name):
            return ""
        keys.add(cell(row, name + PARTY_KEY))
    return keys.pop() if len(keys) == 1 else ""


# -- A party from a printed key the line shares with other lines. --

# The printed keys a line can share with another line of the same maker's job.
SHARED_KEY_COLUMNS = (
    "purchase_order_number",
    "sales_order_number",
    "job_number",
    "project_number",
    "transaction_id",
)
# The party fields a shared key speaks for.
SHARED_KEY_FIELDS = ("customer_name", "dealer_name")
# The least a field's held-out accuracy may be for the method to fill it. On the
# commission run it named the printed party on 97.4% of held-out customer lines
# and 96.0% of dealer lines, where naming each maker's most common party did so
# on 28.6% and 30.3%.
MIN_SHARED_KEY_ACCURACY = 0.95
# A key too short, or too generic, to name one job.
MIN_KEY_LENGTH = 4
PLACEHOLDER_KEY = re.compile(r"0+|n/?a|none|stock|tbd", re.IGNORECASE)


def usable_key(value):
    """Whether a printed key can name one job: long enough, and not a placeholder."""
    return len(value) >= MIN_KEY_LENGTH and not PLACEHOLDER_KEY.fullmatch(value)


def line_maker(row):
    """The maker a line belongs to: resolved, read, or taken from its letterhead."""
    return (
        cell(row, "brand_name__resolved_party")
        or cell(row, "brand_name")
        or cell(row, "brand_name" + INFERRED)
    )


def names_a_party(row, field):
    """Whether a line's field holds a party the master keyed.

    Not the client, not the job the line was sold into, and not a reading the
    resolver refused: the row's own flags say which.
    """
    if not cell(row, field + PARTY_KEY):
        return False
    return not any(field in fields_named(row.get(flag)) for flag in NOT_AN_ACCOUNT)


def shared_key_index(lines, field):
    """Each maker's printed keys, and the lines naming a party beside them."""
    index = collections.defaultdict(list)
    for position, row in enumerate(lines):
        if not names_a_party(row, field):
            continue
        for column in SHARED_KEY_COLUMNS:
            value = cell(row, column)
            if usable_key(value):
                index[(line_maker(row), column, value)].append((position, row))
    return index


def party_by_shared_key(row, field, index, skip=None):
    """The one party every key a line shares names, and the lines saying so.

    Returns `(party_key, column, lines)` or `None`, and whether keys disagreed.
    A key whose lines name two parties, or two keys naming different parties,
    settle nothing: a job number is not always one job.
    """
    found = {}
    for column in SHARED_KEY_COLUMNS:
        value = cell(row, column)
        if not usable_key(value):
            continue
        others = [
            other
            for position, other in index.get((line_maker(row), column, value), ())
            if position != skip
        ]
        keys = {cell(other, field + PARTY_KEY) for other in others}
        if len(keys) > 1:
            return None, True
        if keys:
            found[column] = (keys.pop(), others)
    if len({key for key, _ in found.values()}) > 1:
        return None, True
    if not found:
        return None, False
    column, (key, others) = max(found.items(), key=lambda item: len(item[1][1]))
    return (key, column, others), False


def score_shared_keys(lines, field, index):
    """How often the method names the printed party when that party is hidden.

    Each line naming a party is held out in turn and the method asked for it
    from the other lines. The chance baseline names the maker's most common
    party. A method that settles open fields is scored before it is used.
    """
    counts = collections.defaultdict(collections.Counter)
    for row in lines:
        if names_a_party(row, field):
            counts[line_maker(row)][cell(row, field + PARTY_KEY)] += 1
    common = {maker: parties.most_common(1)[0][0] for maker, parties in counts.items()}
    held_out = proposed = correct = chance = 0
    for position, row in enumerate(lines):
        if not names_a_party(row, field):
            continue
        held_out += 1
        found, _ = party_by_shared_key(row, field, index, skip=position)
        if not found:
            continue
        truth = cell(row, field + PARTY_KEY)
        proposed += 1
        correct += found[0] == truth
        chance += common[line_maker(row)] == truth
    return {
        "held_out": held_out,
        "proposed": proposed,
        "correct": correct,
        "accuracy": round(correct / proposed, 4) if proposed else None,
        "chance_baseline": round(chance / proposed, 4) if proposed else None,
    }


def fill_parties_from_shared_keys(lines, tally):
    """A line's customer or dealer from the lines sharing its printed key.

    Scored first on the lines that print their party, and a field scoring below
    `MIN_SHARED_KEY_ACCURACY` fills nothing. Only readings the master keyed
    speak, never an inference, so one guess never feeds another. Returns each
    field's score, what it filled, and the lines its keys left empty by
    disagreeing.
    """
    scores = {}
    for field in SHARED_KEY_FIELDS:
        index = shared_key_index(lines, field)
        score = score_shared_keys(lines, field, index)
        score.update(filled=0, left_empty_where_keys_disagree=0)
        scores[field] = score
        if score["accuracy"] is None or score["accuracy"] < MIN_SHARED_KEY_ACCURACY:
            continue
        for row in lines:
            if cell(row, field) or row.get(field + INFERRED) or cell(row, field + PARTY_KEY):
                continue
            found, disagreed = party_by_shared_key(row, field, index)
            if disagreed:
                score["left_empty_where_keys_disagree"] += 1
                continue
            if not found:
                continue
            key, column, others = found
            name = cell(others[0], field + "__resolved_party") or cell(others[0], field)
            documents = {other["document_id"] for other in others}
            evidence = (
                f"{column} {cell(row, column)} names this party on {len(others)} lines "
                f"of {len(documents)} documents; held out, the method named the printed "
                f"party on {score['accuracy']:.1%} of {score['proposed']} lines"
            )
            infer(row, field, name, "same_key_on_other_lines", evidence, tally, key)
            score["filled"] += 1
    return scores


def plausible_branch(label):
    """Whether the text after a name's dash could be a branch rather than a code.

    `COOI`, `Re` and `New` came after a dash on this corpus: an internal code
    and two words cut short. A label needs four letters, and a single capitalised
    token that short is a code.
    """
    letters = sum(ch.isalpha() for ch in label)
    return letters >= 4 and not (" " not in label and label.isupper() and letters <= 4)


def place_in_name(name):
    """The location number, city, state or branch a party's name prints plainly."""
    found = {}
    number, rest = split_location_identifier(name)
    if number:
        found["location_number"] = number
    match = CITY_AND_STATE.search(rest)
    if match and match.group(2) in US_STATES:
        found["city"], found["state"] = match.group(1).strip(), match.group(2)
        return found
    match = BRANCH.search(rest)
    if match:
        branch = match.group(1).strip()
        code = STATE_NAMES.get(branch.lower(), "")
        if len(branch) == 2 and branch.upper() in US_STATES:
            code = branch.upper()
        if code:
            if code not in AMBIGUOUS_STATE_CODES:
                found["state"] = code
        elif plausible_branch(branch):
            found["branch"] = branch
            last = branch.split()[-1]
            if " " in branch and last.isupper() and last in US_STATES:
                found["state"] = last
        return found
    match = STATE_CODE.search(rest)
    if match and match.group(1) in US_STATES - AMBIGUOUS_STATE_CODES:
        found["state"] = match.group(1)
    return found


def fill_places(rows, client_names, tally):
    """What a party's own name says about where it is."""
    for row in rows:
        projects = cell(row, "party_field_holds_the_project").split("; ")
        for column in columns_like(row, lambda c: c.endswith("_name") and c not in NOT_A_PARTY):
            name = cell(row, column)
            if not name or column in projects or is_the_client(name, client_names):
                continue
            for part, value in place_in_name(name).items():
                evidence = f"{column} prints “{name}”"
                infer(row, f"{column}_{part}", value, "printed_in_the_name", evidence, tally)


def fill_countries(rows, tally):
    """The ISO 3166-1 alpha-2 code for a country name; returns the names not mapped."""
    unmapped = collections.Counter()
    for row in rows:
        for column in columns_like(row, lambda c: "country" in c):
            value = cell(row, column)
            if not value or re.fullmatch(r"[A-Z]{2}", value):
                continue
            code = ISO_ALPHA2.get(loose_name(value))
            if code:
                evidence = f"the ISO short name “{value}”"
                infer(row, column, code, "iso_3166_1_alpha2", evidence, tally)
            else:
                unmapped[value] += 1
    return unmapped


def phone_numbers(text):
    """Every North American number a cell prints, in E.164, with its labelled kind."""
    found = []
    for segment in text.split(";"):
        label = PHONE_LABEL.match(segment)
        kind = PHONE_KINDS[label.group(1)] if label else ""
        for match in NANP.finditer(segment):
            number = "+1" + "".join(match.group(1, 2, 3))
            if match.group(4):
                number += ";ext=" + match.group(4)
            found.append((kind, number))
    return found


def fill_phones(rows, tally):
    """One E.164 number per column; returns how many cells held no parseable number."""
    unparsed = 0
    for row in rows:
        for column in columns_like(row, lambda c: "phone" in c):
            value = cell(row, column)
            if not value:
                continue
            numbers = phone_numbers(value)
            if not numbers:
                unparsed += 1
                continue
            used = set()
            for index, (kind, number) in enumerate(numbers):
                target = column if len(numbers) == 1 else f"{column}_{kind or index + 1}"
                while target in used:
                    target += "_2"
                used.add(target)
                infer(row, target, number, "e164", f"{column} prints “{value}”", tally)
    return unparsed


def load_external(path):
    """Operator-compiled public-source facts, keyed by each name they cover."""
    if not path:
        return {}, ""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    by_name = {}
    for party in data.get("parties") or []:
        for name in party.get("names") or []:
            by_name[loose_name(name)] = party
    return by_name, str(data.get("retrieved") or "")


def fill_identities(rows, by_name, retrieved, tally):
    """Who a party is, where a public source names it, with the source beside it."""
    for row in rows:
        for column in columns_like(row, lambda c: c.endswith("_name")):
            party = by_name.get(loose_name(cell(row, column)))
            if not party:
                continue
            place = ", ".join(p for p in (party.get("city"), party.get("state")) if p)
            value = " · ".join(p for p in (party.get("identity"), place, party.get("website")) if p)
            rating = party.get("confidence") or "unrated"
            # A fact retrieved after the file was compiled carries its own date.
            when = party.get("retrieved") or retrieved or "undated"
            evidence = f"{party.get('source')} ({rating}, retrieved {when})"
            infer(row, f"{column}_identity", value, "public_source", evidence, tally)


def fill_projects_from_party_fields(lines, tally):
    """Copy a project a party field holds into the project column the line lacks.

    `field_extract_export.py` flags a line whose dealer, customer or brand
    repeats the row's own description while the row prints a job or project
    number: a project heading read into the nearest company field, 490 lines on
    the commission run. The flag keeps the name out of the accounts; this gives
    it the home it was waiting for, beside the reading. The party field is not
    changed.
    """
    for row in lines:
        named = [field for field in cell(row, NOT_AN_ACCOUNT[1]).split("; ") if field]
        if not named or cell(row, "project_name"):
            continue
        value = cell(row, named[0])
        if not value:
            continue
        number = cell(row, "job_number") or cell(row, "project_number")
        evidence = f"{named[0]} repeats the row's description “{value}”, job or project {number}"
        infer(row, "project_name", value, NOT_AN_ACCOUNT[1], evidence, tally)


def augment(lines, documents, engine_types, review_kinds, external, client_names):
    """Fill both grains in place and say what was filled, by which method."""
    tally = collections.Counter()
    by_id = {row["document_id"]: row for row in documents}
    fill_document_types(documents, lines, engine_types, review_kinds, tally)
    fill_brands_from_letterhead(lines, by_id, client_names, tally)
    disagreed = fill_from_the_same_line(lines, client_names, tally)
    shared_keys = fill_parties_from_shared_keys(lines, tally)
    fill_projects_from_party_fields(lines, tally)
    both = [*documents, *lines]
    fill_places(both, client_names, tally)
    unmapped = fill_countries(both, tally)
    unparsed = fill_phones(both, tally)
    fill_identities(both, *external, tally)
    by_method, by_column = collections.Counter(), collections.Counter()
    for (method, column), count in tally.items():
        by_method[method] += count
        by_column[column] += count
    return {
        "artifact_type": ARTIFACT_TYPE,
        "generated_at": datetime.now(UTC).isoformat(),
        "cells_inferred": sum(tally.values()),
        "by_method": dict(by_method.most_common()),
        "by_column": dict(by_column.most_common()),
        "left_empty_where_statements_disagree": disagreed,
        "parties_from_shared_keys": shared_keys,
        "countries_left_unmapped": dict(unmapped),
        "phones_left_unparsed": unparsed,
        "findings": [
            "Every value sits beside the cell it fills, never over it: "
            "`<column>__inferred_by` names the method and `<column>__inferred_evidence` "
            "what supports it.",
            "Only `two_vendors_agree` rests on two vendors. Every other method carries "
            "the run's own evidence across, or cites a public source, and none of it is "
            "a reading of the page.",
            "`same_key_on_other_lines` is scored on held-out lines before it fills "
            "anything: `parties_from_shared_keys` gives each field's accuracy beside "
            "its chance baseline.",
            "This accepts nothing and clears no control.",
        ],
    }


def read_rows(path):
    """A delivery CSV's columns, in order, and its rows."""
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def engine_types_from(path):
    """Each document's engine-read type, from the record artifact, if one is given."""
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {record["document_id"]: engine_type(record) for record in data.get("documents") or []}


def review_kinds_from(directory):
    """Each page's kind as the page review read it, if a review directory is given."""
    kinds = {}
    if not directory:
        return kinds
    for path in sorted(glob.glob(str(Path(directory) / "*.json"))):
        try:
            review = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        kinds[str(review.get("page") or "")] = str(review.get("document_kind") or "")
    return kinds


def columns_for(original, rows):
    """The original columns, in order, then every inferred column added."""
    added = sorted({name for row in rows for name in row} - set(original))
    return [*original, *added]


# -- The two dimensions a CRM loads beside the grains: accounts and contacts. --

# The date a document is dated by, in the order a statement states its dates.
DOCUMENT_DATES = (
    "document_date",
    "statement_date",
    "invoice_date",
    "period_end",
    "payment_date",
    "bill_date",
    "ship_date",
)
# The master's key the export writes beside each resolved name. Accounts join on
# it, because the name beside it is not an identity.
PARTY_KEY = "__party_key"
# The row flags naming party fields that do not hold an account.
NOT_AN_ACCOUNT = (
    "fields_naming_the_client",
    "party_field_holds_the_project",
    "party_fields_not_a_party",
)
# A person field, and the email and phone columns printed with it.
PERSON_GROUPS = (
    ("contact_name", "contact_email", "contact_phone"),
    ("sales_representative_name", "sales_representative_email", "sales_representative_phone"),
)
THE_CLIENT = "the client"
# The shortest company name an email domain may be read as naming.
MIN_NAME_IN_DOMAIN = 4
ACCOUNT_COLUMNS = (
    "party_key",
    "canonical_name",
    "natural_key",
    "name_variants",
    "roles",
    "parent_party_key",
    "parent_name",
    "identity",
    "identity_evidence",
    "phone",
    "phone_evidence",
    "addresses_printed",
    "location_identifiers",
    "rows_naming_it",
    "source_document_count",
    "first_document_date",
    "last_document_date",
    "needs_review",
)
CONTACT_COLUMNS = (
    "contact_key",
    "name",
    "name_variants",
    "party_key",
    "company",
    "belongs_to_the_client",
    "email",
    "other_emails",
    "phone",
    "other_phones",
    "company_evidence",
    "company_unproven",
    "merged_on",
    "printed_in",
    "source_document_count",
    "source_document_ids",
)


def fields_named(flag):
    """The field names a row flag lists, as in `dealer_name; customer_name: reason`."""
    return {part.split(":")[0].strip() for part in str(flag or "").split(";") if part.strip()}


def document_dates(documents):
    """Each document's date: the first of its own date columns it types."""
    dates = {}
    for row in documents:
        found = [cell(row, f"{column}__iso") for column in DOCUMENT_DATES]
        found = [value for value in found if value]
        if found:
            dates[row["document_id"]] = found[0]
    return dates


def public_source_for(party, by_name):
    """The public-source entry covering any of a party's spellings, or none."""
    for name in (party["canonical_name"], *(party.get("name_variants") or [])):
        entry = by_name.get(loose_name(name))
        if entry:
            return entry
    return {}


def account_rows(lines, documents, parties, by_name):
    """One account per resolved party the grains name as a party.

    Built from `<field>__party_key`, never from the raw reading, and never
    from a field the row itself says holds no account: the client's own fields,
    a field holding the job, or a reading the resolver refused. A CRM that built
    its accounts from the raw columns of export 44 made 50 of them, the client
    five times, a column heading and the client's street address among them.
    Each account carries every spelling, the roles it was printed in, the
    parent a branch decision names, the public identity beside its readings,
    and the dates of the documents naming it.
    """
    master = {party["party_key"]: party for party in parties}
    dates = document_dates(documents)
    seen = {}
    for row in [*documents, *lines]:
        skip = set().union(*(fields_named(row.get(flag)) for flag in NOT_AN_ACCOUNT))
        for column in [name for name in row if name.endswith(PARTY_KEY)]:
            field, key = column[: -len(PARTY_KEY)], cell(row, column)
            if key not in master or field in skip:
                continue
            entry = seen.setdefault(
                key,
                {
                    "roles": set(),
                    "documents": set(),
                    "rows": 0,
                    "identities": collections.Counter(),
                },
            )
            entry["roles"].add(field.removesuffix("_name"))
            entry["documents"].add(row["document_id"])
            entry["rows"] += 1
            identity = cell(row, f"{field}_identity{INFERRED}")
            if identity:
                entry["identities"][(identity, cell(row, f"{field}_identity{EVIDENCE}"))] += 1
    accounts = []
    for key, entry in seen.items():
        party = master[key]
        days = sorted(dates[name] for name in entry["documents"] if name in dates)
        identity, evidence = (
            entry["identities"].most_common(1)[0][0] if entry["identities"] else ("", "")
        )
        source = public_source_for(party, by_name)
        accounts.append(
            {
                "party_key": key,
                "canonical_name": party["canonical_name"],
                "natural_key": party["normalized_name"],
                "name_variants": fe.joined(party.get("name_variants")),
                "roles": fe.joined(entry["roles"]),
                "parent_party_key": party.get("parent_party_key", ""),
                "parent_name": party.get("parent_name", ""),
                "identity": identity,
                "identity_evidence": evidence,
                "phone": source.get("phone", ""),
                "phone_evidence": source.get("source", "") if source.get("phone") else "",
                "addresses_printed": fe.joined(party.get("addresses")),
                "location_identifiers": fe.joined(party.get("location_identifiers")),
                "rows_naming_it": entry["rows"],
                "source_document_count": len(entry["documents"]),
                "first_document_date": days[0] if days else "",
                "last_document_date": days[-1] if days else "",
                "needs_review": "yes" if party.get("needs_review") else "",
            }
        )
    accounts.sort(key=lambda account: (-account["rows_naming_it"], account["party_key"]))
    return accounts


def squashed(name):
    """A company name as letters and digits alone, to compare with an email domain."""
    return re.sub(r"[^a-z0-9]", "", normalize_name(name))


def letters(name):
    """A person's name as its letters alone: `JANA HALVORSEN` is `Jana Halvorsen`."""
    return re.sub(r"[^a-z]", "", name.casefold())


def email_names_the_person(email, name):
    """Whether an address's local part carries one of the person's names."""
    local = email.split("@")[0].casefold()
    return any(len(word) >= 3 and word in local for word in loose_name(name).split())


def company_of(email, candidates):
    """The one company an email's domain names among the candidates, or None.

    `mara.castell@norvena.example` names Norvena and `jana@northgate.example` the
    client: the domain's first label is the company's name, or starts with it,
    as `halvorfurniture` does HALVOR. Only the document's own parties and the
    client are candidates, and a domain naming two of them names neither.
    """
    label = re.sub(r"[^a-z0-9]", "", email.split("@")[-1].split(".")[0].casefold())
    named = {
        key
        for key, name in candidates
        if len(squashed(name)) >= MIN_NAME_IN_DOMAIN and label.startswith(squashed(name))
    }
    return named.pop() if len(named) == 1 else None


def person_readings(documents, master, client_names):
    """Every person a person field names, with the email and phone printed beside them.

    A field the row flags as not one person is left out. An email is kept only
    when its local part names this person, and a phone only beside such an
    email: seven murbrook pages print murbrook's own number, 450-555-0132, in
    the contact block beside the client's rep, and one page prints the rep's
    email beside someone else's name.
    """
    readings = []
    for row in documents:
        refused = fields_named(row.get("person_fields_not_one_person"))
        candidates = [(THE_CLIENT, name) for name in client_names]
        for column in [name for name in row if name.endswith(PARTY_KEY)]:
            party = master.get(cell(row, column))
            if party:
                candidates += [
                    (party["party_key"], name)
                    for name in (party["canonical_name"], *(party.get("name_variants") or []))
                ]
        for field, email_column, phone_column in PERSON_GROUPS:
            name = cell(row, field)
            if not name or field in refused:
                continue
            email = cell(row, f"{email_column}__email")
            if not email_names_the_person(email, name):
                email = ""
            phones = {
                cell(row, column)
                for column in row
                if email
                and column.startswith(phone_column)
                and column.endswith(INFERRED)
                and cell(row, column[: -len(INFERRED)] + BY) == "e164"
            }
            readings.append(
                {
                    "name": name,
                    "field": field,
                    "document_id": row["document_id"],
                    "email": email,
                    "phones": sorted(phones - {""}),
                    "company": company_of(email, candidates) if email else None,
                }
            )
    return readings


def without_middle_initial(name):
    """`JANA K HALVORSEN` as the letters of `Jana Halvorsen`, or None."""
    words = loose_name(name).split()
    if len(words) == 3 and len(words[1]) == 1:
        return letters(words[0] + words[2])
    return None


def one_person_groups(readings):
    """Group the readings that are one person, and say what joined each group.

    Three kinds of evidence, each narrower than similarity: the same letters
    (`JANA HALVORSEN`, `Jana Halvorsen`), a middle initial the other spelling
    lacks (`JANA K HALVORSEN`), and the same email printed with names it
    names (`Jana Holvorsen`, a misreading, beside jana@northgate.example). One
    letter apart is not evidence on its own, because two people can be.
    """
    parent = list(range(len(readings)))

    def root(index):
        while parent[index] != index:
            index = parent[index]
        return index

    first, joins = {}, []
    for index, reading in enumerate(readings):
        keys = [(("name", letters(reading["name"])), "the same letters")]
        initial = without_middle_initial(reading["name"])
        if initial:
            keys.append((("name", initial), "a middle initial"))
        if reading["email"]:
            keys.append((("email", reading["email"].casefold()), "the same email"))
        for key, reason in keys:
            if key not in first:
                first[key] = (index, reason)
                continue
            other, registered = first[key]
            parent[root(index)] = root(other)
            if readings[other]["name"] != reading["name"]:
                joins.append((other, registered if reason == "the same letters" else reason))
    groups = collections.defaultdict(list)
    for index, reading in enumerate(readings):
        groups[root(index)].append(reading)
    merged = collections.defaultdict(set)
    for other, reason in joins:
        merged[root(other)].add(reason)
    return [(members, merged[key]) for key, members in groups.items()]


def unproven_company(members, companies):
    """Why a contact carries no company, or empty when it carries one."""
    if len(companies) == 1:
        return ""
    if companies:
        return "its emails name different companies"
    if any(reading["email"] for reading in members):
        return "its email's domain names none of the document's companies"
    if all(reading["field"] == "sales_representative_name" for reading in members):
        return (
            "printed only in a rep field, with no email; "
            "who the rep-field people are waits on the client"
        )
    return "no email naming this person is printed with the name"


def contact_rows(documents, parties, client_names):
    """One contact per person the person fields name, and how many readings made them."""
    master = {party["party_key"]: party for party in parties}
    readings = person_readings(documents, master, client_names)
    contacts = []
    for members, merged_on in one_person_groups(readings):
        spellings = collections.Counter(reading["name"] for reading in members)
        emails = list(dict.fromkeys(reading["email"] for reading in members if reading["email"]))
        phones = collections.Counter(phone for reading in members for phone in reading["phones"])
        companies = {reading["company"] for reading in members if reading["company"]}
        company = next(iter(companies)) if len(companies) == 1 else None
        proof = next((r["email"] for r in members if company and r["company"] == company), "")
        domain = proof.split("@")[-1]
        named = client_names[0] if company == THE_CLIENT and client_names else ""
        if company and company != THE_CLIENT:
            named = master[company]["canonical_name"]
        contacts.append(
            {
                "name": spellings.most_common(1)[0][0],
                "name_variants": fe.joined(list(spellings)),
                "party_key": company if company and company != THE_CLIENT else "",
                "company": named,
                "belongs_to_the_client": "yes" if company == THE_CLIENT else "",
                "email": emails[0] if emails else "",
                "other_emails": fe.joined(emails[1:]),
                "phone": phones.most_common(1)[0][0] if phones else "",
                "other_phones": fe.joined([phone for phone, _ in phones.most_common()[1:]]),
                "company_evidence": f"the email domain {domain} names {named}" if company else "",
                "company_unproven": unproven_company(members, companies),
                "merged_on": fe.joined(merged_on),
                "printed_in": fe.joined({reading["field"] for reading in members}),
                "source_document_count": len({reading["document_id"] for reading in members}),
                "source_document_ids": fe.joined({reading["document_id"] for reading in members}),
            }
        )
    contacts.sort(key=lambda contact: contact["name"].casefold())
    for number, contact in enumerate(contacts, start=1):
        contact["contact_key"] = f"CON-{number:04d}"
    return contacts, len(readings)


# -- Products: only what a manufacturer's own pages print as a product. --

# Rows that are not a line of their document. A product is never read off one.
NOT_A_LINE = (
    "duplicate_of_line",
    "restates_a_total",
    "repeats_an_earlier_block",
    "repeats_a_job_and_amount_above",
)
DEFAULT_CODE_FROM = ("item_code", "product_sku", "description")
DEFAULT_NAME_FROM = ("description", "item_code")
PRODUCT_FIELDS = (
    "code",
    "key",
    "name",
    "variant",
    "brand",
    "brand_key",
    "rule",
    "printed_row_code",
    "printed_row_check",
)
# A digit an extractor reads where a code prints a letter.
LOOKALIKE_LETTERS = {"0": "O", "1": "I"}
# The printed amounts a line's row is found by, when its manufacturer's rule
# checks the code against the printed row.
ROW_AMOUNTS = ("commissionable_amount", "extended_amount", "sales_amount", "commission_amount")
PRODUCT_COLUMNS = (
    "product_key",
    "brand",
    "brand_party_key",
    "code",
    "other_codes",
    "name",
    "other_names",
    "variants",
    "line_rows",
    "source_document_count",
    "first_document_date",
    "last_document_date",
    "evidence",
)


def folded(name):
    """A name as its letters and digits, the way two printed spellings are compared."""
    return re.sub(r"[^a-z0-9]", "", str(name or "").casefold())


def load_product_rules(path):
    """An operator's rules for which printed column holds each manufacturer's product.

    The export cannot say. `description` holds a murbrook project, a HALVOR
    project client, a Lumen Weft specifier and an Linden World product name,
    because each of those layouts printed a column the schema had no other
    place for, and two vendors agreeing on the field does not make it the right
    one. A rule per manufacturer, read off its own pages, is what the products
    rest on, so the file names the authorization it rests on and every rule
    carries its evidence.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not str(data.get("authorization") or "").strip():
        raise ValueError(f"{path}: product rules must name the authorization they rest on")
    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError(f"{path}: 'rules' must be a non-empty list")
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict) or not str(rule.get("brand") or "").strip():
            raise ValueError(f"{path}: rule {index} must name a brand")
        if not str(rule.get("evidence") or "").strip():
            raise ValueError(f"{path}: rule {index} must carry its evidence")
        if not rule.get("no_product") and not rule.get("code"):
            raise ValueError(f"{path}: rule {index} must give a code pattern or say no_product")
    return rules


def party_names(parties):
    """Every printed spelling of every party, folded, to the party it resolved to."""
    return {
        folded(name): party["party_key"]
        for party in parties
        for name in (party["canonical_name"], *(party.get("name_variants") or []))
    }


def product_code_in(row, rule, names):
    """The first code column printing the manufacturer's product code, and which column.

    A candidate the rule calls no product, or one naming a party, is passed over
    for the next column rather than ending the search: Marlow/Bramwell's weekly
    report files the substyle `XSH` under `item_code` with the style beside it.
    When every candidate was passed over, the first refusal is returned instead,
    as (candidate, reason, tally key).
    """
    pattern = re.compile(rule["code"])
    not_products = {value.casefold() for value in rule.get("not_products") or []}
    refused = None
    for column in rule.get("code_from") or DEFAULT_CODE_FROM:
        value = cell(row, column)
        words = value.split()
        # Linden World's billing report can fuse the payer into the code's cell --
        # `Workfield BQ1336` -- so a rule may take the cell's last word.
        tail = words[-1] if rule.get("code_may_follow_a_name") and len(words) > 1 else ""
        for candidate in (value, tail):
            if not candidate or not pattern.fullmatch(candidate):
                continue
            if candidate.casefold() in not_products:
                refused = refused or (
                    candidate,
                    "is not a product",
                    "lines_naming_something_other_than_a_product",
                )
            elif folded(candidate) in names:
                # `D FROST COMPANY` sits in Marlow/Bramwell's style position on
                # a few pages: a party the page names, not a style.
                refused = refused or (
                    candidate,
                    "names a party, not a product",
                    "lines_naming_a_party_not_a_product",
                )
            else:
                return candidate, column, None
    return "", "", refused


def product_name_in(row, rule, code, names):
    """The product's printed name: the first name column holding one.

    Never a party, the code itself, or another code: a name reading `ME6551`
    beside the code ME1754 is a second product's code in the name's column.
    """
    if rule.get("name_is_the_code"):
        return code
    pattern = re.compile(rule["code"])
    for column in rule.get("name_from") or DEFAULT_NAME_FROM:
        value = cell(row, column)
        if (
            any(character.isalpha() for character in value)
            and code.casefold() not in value.casefold()
            and folded(value) not in names
            and not pattern.fullmatch(value)
        ):
            return value
    return ""


def document_brand_keys(document, names):
    """The parties a document's brand names: its key, or its printed letters when unkeyed.

    A stated brand the export left unkeyed -- its engines spelled it two ways,
    so no single reading resolved -- is matched by its letters.
    """
    found = (
        cell(document, f"brand_name{PARTY_KEY}"),
        names.get(folded(cell(document, "brand_name")), ""),
    )
    return [key for key in found if key]


def line_brand_keys(row, names):
    """The parties a line's own brand cells name."""
    found = (
        names.get(folded(cell(row, f"brand_name{INFERRED}")), ""),
        cell(row, f"brand_name{PARTY_KEY}"),
        names.get(folded(cell(row, "brand_name")), ""),
    )
    return [key for key in found if key]


def line_brand_key(row, document, names, ruled):
    """The manufacturer a line belongs to: the first its document or line names that has a rule.

    Its document's first, then the line's own, so a document whose brand holds
    a dealer gives way to the manufacturer its line names. With no ruled
    manufacturer named at all, the first party named is returned, to be counted
    as unruled.
    """
    named = [*document_brand_keys(document, names), *line_brand_keys(row, names)]
    return next((key for key in named if key in ruled), named[0] if named else "")


def ruled_makers_on_lines(lines, names, ruled):
    """Per document, the ruled manufacturers its lines name."""
    found = collections.defaultdict(set)
    for row in lines:
        found[row["document_id"]].update(key for key in line_brand_keys(row, names) if key in ruled)
    return found


def page_tokens(directory):
    """A reader of each document's positioned words, from the retained extractor responses."""
    responses = retained_responses(directory)
    if not responses:
        raise ValueError(f"No retained extractor responses under {directory}")

    def tokens_for(document_id):
        path = responses.get(document_id)
        return retained_response_tokens(path) if path else []

    return tokens_for


def printed_amount(value):
    """An amount as its printed digits, so `2,016.00` and `2016.00` compare equal."""
    return re.sub(r"[,$\s]", "", str(value or ""))


def on_one_row(top, bottom, other_top, other_bottom):
    """Whether two readings sit on one printed row: either's middle lies within the other."""
    return (
        other_top <= (top + bottom) / 2 <= other_bottom
        or top <= (other_top + other_bottom) / 2 <= bottom
    )


def printed_row_codes(rows, tokens, rule):
    """The code printed on each line's row, found by the line's own amounts; 1:1 both ways.

    An amount identifies a row only where the page prints it once. A line whose
    row prints several codes, and a code several lines claim, stay unread
    rather than guessed. Returns {position in rows: code}.
    """
    pattern = re.compile(rule["code"])
    codes = []
    for index, (reading, top, bottom) in enumerate(tokens):
        word = reading.strip("()[]{}:;,.|")
        if pattern.fullmatch(word):
            codes.append((index, word, top, bottom))
    amounts = [(printed_amount(reading), top, bottom) for reading, top, bottom in tokens]
    printed = collections.Counter(amount for amount, _, _ in amounts)
    claims = {}
    for position, row in enumerate(rows):
        own = {printed_amount(cell(row, column)) for column in ROW_AMOUNTS} - {""}
        spans = [
            (top, bottom)
            for amount, top, bottom in amounts
            if amount in own and printed[amount] == 1
        ]
        found = {
            (index, word)
            for top, bottom in spans
            for index, word, code_top, code_bottom in codes
            if on_one_row(top, bottom, code_top, code_bottom)
        }
        if len(found) == 1:
            claims[position] = found.pop()
    claimed = collections.Counter(index for index, _ in claims.values())
    return {position: word for position, (index, word) in claims.items() if claimed[index] == 1}


def printed_rows(to_read, tokens_for):
    """The code printed on each line's row, for lines whose rule checks it, by line."""
    if tokens_for is None:
        return {}
    held = collections.defaultdict(list)
    for row, rule, _ in to_read:
        if rule.get("check_the_printed_row"):
            held[(row["document_id"], rule["brand"])].append((row, rule))
    found = {}
    for (document_id, _), pairs in held.items():
        rows = [row for row, _ in pairs]
        codes = printed_row_codes(rows, tokens_for(document_id), pairs[0][1])
        found.update({id(rows[position]): code for position, code in codes.items()})
    return found


def read_product(row, rule, brand, names, printed, tally):
    """Read one line's product by its rule, checking the code against its printed row.

    Where the row of the line's own amounts prints another code than the
    engines read, the printed row wins: pages 234 and 238, checked against
    their images, print the amount beside the code the join found, with the
    engines' code one row away. The engines' reading stays in its own column,
    and the line's name is left to the product, since the description the
    engines paired with the wrong code may be the wrong row's as well.
    """
    code, column, refused = product_code_in(row, rule, names)
    check = ""
    if printed:
        agrees = code and product_code_key(code, rule) == product_code_key(printed, rule)
        check = "agrees" if agrees else "differs" if code else "recovered"
    found = {"printed_row_code": printed, "printed_row_check": check}
    if check == "differs":
        found.update(
            code=printed,
            rule=f"{rule['brand']}: code printed on the row of the line's amount; "
            f"the engines read {code}",
        )
        tally["codes_taken_from_the_printed_row_over_the_engines"] += 1
    elif check == "recovered":
        found.update(
            code=printed,
            name=product_name_in(row, rule, printed, names),
            rule=f"{rule['brand']}: code printed on the row of the line's amount, "
            "which the engines did not read",
        )
        tally["codes_read_only_from_the_printed_row"] += 1
    elif code:
        found.update(
            code=code,
            name=product_name_in(row, rule, code, names),
            rule=f"{rule['brand']}: code from {column}"
            + ("; the printed row agrees" if check else ""),
        )
        if check:
            tally["codes_the_printed_row_agrees_with"] += 1
    if found.get("code"):
        found.update(
            key=f"{brand}:{product_code_key(found['code'], rule)}",
            variant=next(
                (cell(row, c) for c in rule.get("variant_from") or [] if cell(row, c)), ""
            ),
        )
        tally["lines_with_a_product"] += 1
    elif refused:
        candidate, reason, counted_as = refused
        found["rule"] = f"{rule['brand']}: {candidate} {reason}"
        tally[counted_as] += 1
    else:
        found["rule"] = f"{rule['brand']}: no product code printed on the line"
        tally["lines_printing_no_product_code"] += 1
    for field, value in found.items():
        row[f"product__{field}"] = value


def assign_products(lines, documents, rules, parties, tokens_for=None):
    """Put each line's product beside it by its manufacturer's rule, and say why when none.

    Nothing is read into a product that the rule does not name: a line of a
    manufacturer that lists no products says so, a row that is not a line of
    its document carries none, and a manufacturer with no rule is counted as
    unruled rather than guessed at. Given the retained extractor's positioned
    words (`tokens_for`), a rule that checks the printed row reads each line's
    code off the row its own amounts are printed on.
    """
    names = party_names(parties)
    canonical = {party["party_key"]: party["canonical_name"] for party in parties}
    by_key, unmatched = {}, []
    for rule in rules:
        key = names.get(folded(rule["brand"]))
        if key:
            by_key[key] = rule
        else:
            unmatched.append(rule["brand"])
    by_id = {row["document_id"]: row for row in documents}
    on_lines = ruled_makers_on_lines(lines, names, by_key)
    tally = collections.Counter()
    to_read = []
    for row in lines:
        document = by_id.get(row["document_id"], {})
        brand = line_brand_key(row, document, names, by_key)
        rule = by_key.get(brand)
        found = dict.fromkeys(PRODUCT_FIELDS, "")
        if rule is not None:
            found.update(brand=canonical[brand], brand_key=brand)
        if rule is None:
            tally["lines_whose_manufacturer_has_no_product_rule"] += 1
        elif (
            brand not in document_brand_keys(document, names)
            and len(on_lines[row["document_id"]]) > 1
        ):
            # The client's own monthly report lists each maker's jobs, a line
            # apiece: a summary, not the manufacturer's statement its rule reads.
            found["rule"] = "a page naming several manufacturers is a summary, not one's statement"
            tally["lines_on_a_page_naming_several_manufacturers"] += 1
        elif rule.get("no_product"):
            found["rule"] = f"no product: {rule['no_product']}"
            tally["lines_of_a_manufacturer_listing_no_products"] += 1
        elif any(cell(row, flag) for flag in NOT_A_LINE):
            found["rule"] = "not a line of its document"
            tally["lines_that_are_not_a_line"] += 1
        else:
            to_read.append((row, rule, brand))
        for field, value in found.items():
            row[f"product__{field}"] = value
    printed = printed_rows(to_read, tokens_for)
    for row, rule, brand in to_read:
        read_product(row, rule, brand, names, printed.get(id(row), ""), tally)
    return tally, unmatched


def product_code_key(code, rule):
    """The code a product is keyed on: upper case, and its letter positions read as letters.

    Linden World's codes open with two letters, and the extractors read the
    letter O there as the digit 0 and I as 1 -- `S01610` for SO1610, `S11300`
    for SI1300 -- which split one product in two. The printed reading is kept
    on the line; only the key folds.
    """
    key = code.upper()
    letters = int(rule.get("code_letters") or 0)
    head = "".join(LOOKALIKE_LETTERS.get(character, character) for character in key[:letters])
    return head + key[letters:]


def product_rows(lines, documents):
    """One product per manufacturer and code, from the lines carrying one."""
    dates = document_dates(documents)
    grouped = {}
    for row in lines:
        code = cell(row, "product__code")
        if not code:
            continue
        entry = grouped.setdefault(
            cell(row, "product__key"),
            {
                "brand": cell(row, "product__brand"),
                "brand_key": cell(row, "product__brand_key"),
                "codes": collections.Counter(),
                "names": collections.Counter(),
                "variants": set(),
                "documents": set(),
                "rules": set(),
                "rows": 0,
            },
        )
        entry["codes"][code] += 1
        name = cell(row, "product__name")
        if name:
            entry["names"][name] += 1
        variant = cell(row, "product__variant")
        if variant:
            entry["variants"].add(variant)
        entry["documents"].add(row["document_id"])
        entry["rules"].add(cell(row, "product__rule"))
        entry["rows"] += 1
    products = []
    for key, entry in grouped.items():
        codes = [code for code, _ in entry["codes"].most_common()]
        spellings = [name for name, _ in entry["names"].most_common()]
        days = sorted(dates[name] for name in entry["documents"] if name in dates)
        products.append(
            {
                "product_key": key,
                "brand": entry["brand"],
                "brand_party_key": entry["brand_key"],
                "code": codes[0],
                "other_codes": fe.joined(codes[1:]),
                "name": spellings[0] if spellings else "",
                "other_names": fe.joined(spellings[1:]),
                "variants": fe.joined(entry["variants"]),
                "line_rows": entry["rows"],
                "source_document_count": len(entry["documents"]),
                "first_document_date": days[0] if days else "",
                "last_document_date": days[-1] if days else "",
                "evidence": fe.joined(entry["rules"]),
            }
        )
    products.sort(key=lambda p: (p["brand"].casefold(), -p["line_rows"], p["code"]))
    return products


def load_parties(path):
    """The party master's parties, refusing a file that carries none."""
    parties = json.loads(Path(path).read_text(encoding="utf-8")).get("parties")
    if not isinstance(parties, list):
        raise ValueError(f"{path} carries no parties list")
    return parties


def keys_not_in_the_master(rows, parties):
    """Party keys the grains carry that the master does not hold.

    Accounts join on the key, so a master other than the one the export
    resolved against joins nothing -- and an empty accounts file reads as a
    corpus with no accounts. The mismatch is counted and reported instead.
    """
    known = {party["party_key"] for party in parties}
    carried = {cell(row, column) for row in rows for column in row if column.endswith(PARTY_KEY)}
    return sorted(carried - known - {""})


def dimension_summary(accounts, contacts, readings, unknown_keys):
    """What the two dimensions hold, for the augmentation summary."""
    return {
        "accounts": len(accounts),
        "party_keys_not_in_the_master": len(unknown_keys),
        "person_readings": readings,
        "contacts": len(contacts),
        "contacts_with_a_company": sum(1 for contact in contacts if contact["company"]),
        "contacts_belonging_to_the_client": sum(
            1 for contact in contacts if contact["belongs_to_the_client"]
        ),
    }


def build_parser():
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lines-csv", required=True, help="Delivery line-grain CSV to fill.")
    parser.add_argument("--documents-csv", required=True, help="Delivery document-grain CSV.")
    parser.add_argument("--out-lines", required=True, help="New line-grain CSV with inferences.")
    parser.add_argument("--out-documents", required=True, help="New document-grain CSV.")
    parser.add_argument("--out", required=True, help="Summary JSON of what was filled, and how.")
    parser.add_argument(
        "--records", default="", help="Record artifact, for the engines' document types."
    )
    parser.add_argument(
        "--reviews", default="", help="Page-review directory, for each page's reviewed kind."
    )
    parser.add_argument(
        "--external",
        default="",
        help="Operator-compiled public-source party facts (party_public_sources_v1).",
    )
    parser.add_argument(
        "--client-name",
        action="append",
        default=[],
        help="The client whose records these are; never filled in as a party. Repeatable.",
    )
    parser.add_argument(
        "--parties",
        default="",
        help=(
            "Party master from entity_resolve.py. With it the summary counts the accounts and "
            "contacts the grains name, and --out-accounts and --out-contacts can be written."
        ),
    )
    parser.add_argument(
        "--out-accounts",
        default="",
        help=(
            "New accounts CSV: one row per resolved party the grains name as a party -- never "
            "the client, a field holding the job, or a reading the resolver refused."
        ),
    )
    parser.add_argument(
        "--out-contacts",
        default="",
        help=(
            "New contacts CSV: one row per person the person fields name, one person's "
            "spellings merged on evidence, and a company only where an email naming the "
            "person proves it."
        ),
    )
    parser.add_argument(
        "--product-rules",
        default="",
        help=(
            "Operator rules naming, per manufacturer, the printed column holding its product "
            "code and name, or that it lists no products (product_rules_v1). Needs --parties. "
            "Each line then carries product__code, product__name, product__variant, "
            "product__brand, product__brand_key and product__rule."
        ),
    )
    parser.add_argument(
        "--out-products",
        default="",
        help=(
            "New products CSV: one row per manufacturer and printed product code, from the "
            "lines a product rule reads -- never a project, a total, a label or a party."
        ),
    )
    parser.add_argument(
        "--extractor-raw",
        default="",
        help=(
            "Directory of retained independent-extractor responses. With a product rule that "
            "checks the printed row, each line's code is read off the row its own amounts are "
            "printed on, and product__printed_row_check says whether the engines' code agrees. "
            "Needs --product-rules."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress the printed summary.")
    apply_shared_help(parser)
    return parser


def report(summary):
    """Print what was filled, by method, and the dimensions counted."""
    print(f"cells inferred: {summary['cells_inferred']}")
    for method, count in summary["by_method"].items():
        print(f"  {method}: {count}")
    left = summary["left_empty_where_statements_disagree"]
    print(f"  left empty where statements disagree: {left}")
    for field, score in summary.get("parties_from_shared_keys", {}).items():
        if score["accuracy"] is None:
            print(f"  {field} from a shared key: unscored, nothing filled")
            continue
        print(
            f"  {field} from a shared key: {score['filled']} filled; held out, "
            f"{score['accuracy']:.1%} of {score['proposed']} against "
            f"{score['chance_baseline']:.1%} by chance"
        )
    if "accounts" in summary:
        print(
            f"accounts: {summary['accounts']}; contacts: {summary['contacts']} from "
            f"{summary['person_readings']} readings, {summary['contacts_with_a_company']} "
            "with a company"
        )
        if summary["party_keys_not_in_the_master"]:
            print(
                f"  party keys the master does not hold: {summary['party_keys_not_in_the_master']}"
                " -- was --parties the master the export resolved against?"
            )
    if "products" in summary:
        products = summary["products"]
        print(f"products: {products['products']}")
        for reason, count in products["lines"].items():
            print(f"  {reason.replace('_', ' ')}: {count}")
        if products["rules_naming_no_party"]:
            print(f"  product rules naming no party: {products['rules_naming_no_party']}")


def main(argv=None):
    """Read the grains, fill them, and write new files beside them."""
    args = build_parser().parse_args(argv)
    try:
        if (args.out_accounts or args.out_contacts or args.product_rules) and not args.parties:
            raise ValueError("--out-accounts, --out-contacts and --product-rules need --parties")
        if (args.out_products or args.extractor_raw) and not args.product_rules:
            raise ValueError("--out-products and --extractor-raw need --product-rules")
        outputs = (args.out_lines, args.out_documents, args.out)
        for path in (*outputs, args.out_accounts, args.out_contacts, args.out_products):
            if path:
                empty_output_path(path)
        line_columns, lines = read_rows(args.lines_csv)
        document_columns, documents = read_rows(args.documents_csv)
        # Read before anything is written, so a bad master or rule file fails the run whole.
        parties = load_parties(args.parties) if args.parties else None
        rules = load_product_rules(args.product_rules) if args.product_rules else None
        tokens_for = page_tokens(args.extractor_raw) if args.extractor_raw else None
        external = load_external(args.external)
        summary = augment(
            lines,
            documents,
            engine_types_from(args.records),
            review_kinds_from(args.reviews),
            external,
            args.client_name,
        )
        summary["inputs"] = {
            "lines_csv": args.lines_csv,
            "documents_csv": args.documents_csv,
            "records": args.records,
            "reviews": args.reviews,
            "external": args.external,
            "parties": args.parties,
            "client_names": args.client_name,
            "product_rules": args.product_rules,
            "extractor_raw": args.extractor_raw,
        }
        if rules is not None:
            # After the fill, so a line whose brand was inferred is read by its rule.
            tally, unmatched = assign_products(lines, documents, rules, parties, tokens_for)
            products = product_rows(lines, documents)
            summary["products"] = {
                "products": len(products),
                "lines": tally,
                "rules_naming_no_party": unmatched,
            }
            if args.out_products:
                fe.write_csv(args.out_products, products, PRODUCT_COLUMNS)
        fe.write_csv(args.out_lines, lines, columns_for(line_columns, lines))
        fe.write_csv(args.out_documents, documents, columns_for(document_columns, documents))
        if parties is not None:
            # Built after the fill, so an account carries the identity placed beside
            # its readings and a contact the phone typed beside its email.
            accounts = account_rows(lines, documents, parties, external[0])
            contacts, readings = contact_rows(documents, parties, args.client_name)
            unknown = keys_not_in_the_master([*lines, *documents], parties)
            summary.update(dimension_summary(accounts, contacts, readings, unknown))
            for path, rows, columns in (
                (args.out_accounts, accounts, ACCOUNT_COLUMNS),
                (args.out_contacts, contacts, CONTACT_COLUMNS),
            ):
                if path:
                    fe.write_csv(path, rows, columns)
        Path(args.out).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except (OSError, ValueError) as exc:
        sys.exit(f"Augmentation failed: {exc}")
    if not args.quiet:
        report(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
