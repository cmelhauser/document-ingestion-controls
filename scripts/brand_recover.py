#!/usr/bin/env python3
"""Recover the manufacturer from the letterhead the page already printed.

A commission run is keyed on the manufacturer whose statement each page is. On
this corpus 354 of 683 documents carry none -- $5.42M of commission with no
brand against it -- and the reason is instructive: `brand_name` held the client,
because a billing report prints the client as its agent of record and the
nearest company field took it. Refusing the client, which is right, left those
documents with nothing.

The value never left the workspace. The manufacturer is in the letterhead, and
the independent non-LLM extractor retains the whole page rather than the fields
the schema asked for. No provider call.

The vocabulary is the corpus's own. Brands are read from the documents that DO
carry one, so this cannot invent a manufacturer the run never saw, and it needs
no list maintained beside the code. A page naming several, or none, yields an
exception rather than a guess.

Nothing is overwritten. A document that already states a brand keeps it, and a
recovered value is independent-extractor evidence -- never vendor agreement.

The second pass over the commission run found what the first left behind:

* 222 of 716 pages had text only in the extractor's retry response, and the
  first response per page was the one read, so 79 were called unreadable. The
  shared index in `run_io` takes the response that read the page.
* `interior`, the tail of a dozen dealers' names, had joined the vocabulary and
  collided with the real letterhead on 17 pages. A manufacturer prints its name
  at the top of its own pages; `interior` headed one page in 716 and sat lower
  on 69. A name must now head at least two pages.
* A page whose brand held a dealer counted as branded and was skipped. Only a
  name in the vocabulary counts as a manufacturer stated.
* `Lindew World`, `Lindel World` and `Martin / Bramwela` are display
  letterheads read one letter off, and a long name may now match that way.
* HALVOR's monthly statement opens `MONTHLY COMMISSION STATEMENT` with its
  logo as an image on 18 pages, while 37 pages open the same way with HALVOR
  first. A page naming no manufacturer takes the one every page opening the
  same way names, when at least two do.
* A continuation page prints its maker's columns and none of its letterhead:
  Linden World's billing report runs for pages under one heading, and
  Marlow/Bramwell's commission statement repeats its columns without its name.
  A page still unsettled takes the maker every one of its five most alike
  settled pages names, counting only words on five pages or more and at most
  half. Measured leave-one-out over the 641 settled pages, 620 named their own
  maker, one a second spelling of it (`lumen wefy` for `lumen weft`) and 20
  stayed undecided; none named another maker. That test cannot see a maker the
  vocabulary lacks, and one showed at once: Crestline Design's funds-transfer
  advices read like Marlow/Bramwell's bill payments. A page whose top names a
  company by its legal form, other than the client or a known maker, is kept
  as a question naming that company instead.

Known limit: a letterhead written by hand is not in a printed text layer. Three
of the nine pages checked against their images carry the manufacturer in pen and
recover nothing -- they are retained as exceptions, which is the right answer.
Of the pages that did recover, none recovered a wrong manufacturer.
"""

import argparse
import collections
import json
import re
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import entity_resolve
from cli_help import apply_shared_help
from run_io import response_document_text, retained_responses

ARTIFACT_TYPE = "brand_recovery_v1"
RECOVERED_SOURCE = "printed"
RECOVERED_EVIDENCE = "recovered_from_independent_extractor_layout"
EXTRACTOR_ENGINE = "google/document_ai"
# A letterhead sits at the top of the page. Reading further down finds a brand
# named in a line item and attributes the whole statement to it.
LETTERHEAD_CHARS = 600
# The very top, where a manufacturer prints its own name. When a letterhead
# names two, the one here is the page's own; the other is further down.
TOP_CHARS = 120
# The shortest name worth matching. Below this a brand collides with ordinary
# words in a letterhead.
MIN_BRAND = 5
# A manufacturer heads its own pages. A dealer's name reaches the top of one
# page by accident of layout, not two.
MIN_HEADED_PAGES = 2
# A name this long read one letter off is still that name: `lindewworld` is
# `lindenworld`. Shorter, one letter makes another word.
MIN_ONE_LETTER_OFF = 10
# How much of a page's opening, as letters with the manufacturer taken out,
# identifies its layout, and how many pages must share it to name its maker.
OPENING_LETTERS = 28
MIN_OPENING_PAGES = 2
# A continuation page prints its maker's columns and none of its letterhead. It
# is read against the settled pages by the words they share, counting only
# words that can describe a layout: on at least five pages, since a project or
# an amount is on one, and on at most half, since `total` is on every maker's.
WORD = re.compile(r"[a-z][a-z0-9]{2,}")
MIN_WORD_PAGES = 5
MAX_WORD_SHARE = 0.5
# The most alike settled pages must all name one maker, and the closest must
# share this much of its layout words with the page.
NEIGHBOURS = 5
MIN_LIKENESS = 0.3
# Words only one maker's settled pages print, on `MIN_WORD_PAGES` of them or
# more: Linden World's SAP condition types ZCO1 and ZCO2, Marlow/Bramwell's
# Furn, Lea, Masq and Fixed columns. A page printing this many, every one of
# them one maker's, is that maker's page.
MIN_SIGNATURE_WORDS = 3
# A page from a maker the vocabulary lacks reads like whichever known maker's
# pages share its form: Crestline Design's funds-transfer advices read like
# Marlow/Bramwell's bill payments. A top line naming a company by its legal
# form marks such a page, and it stays a question rather than being read.
COMPANY = re.compile(
    r"^[A-Za-z][A-Za-z0-9&.,' /-]*?[A-Za-z][ ,]+(?:inc|llc|ltd|corp|corporation|company|co)\.?$",
    re.I,
)
# Shapes that reach `brand_name` and are not manufacturers. A market or
# territory code -- `3-USA`, `150-NGC` -- is the commonest here, and taking it
# as a brand would attribute a page to a region.
NOT_A_BRAND = re.compile(r"^\d{1,3}\s*-\s*[A-Za-z]{2,4}$")


def nameable(value):
    """Report whether a value can be a manufacturer name at all."""
    return any(character.isalpha() for character in str(value))


def field_text(value):
    """Read a field that may be a plain value or a provenance-carrying object."""
    if isinstance(value, dict):
        value = value.get("value")
    return str(value).strip() if value is not None else ""


def manufacturer_spelling(name, vocabulary):
    """The corpus's spelling of the manufacturer a stated name is, or None.

    A stated name may be cut (`MARLOW/BRAMWELL IN`), carry a suffix the
    vocabulary lacks (`marlow/bramwell inc.`), or be read one letter off.
    """
    key = _squash(name)
    matches = collections.Counter()
    for known, count in vocabulary.items():
        other = _squash(known)
        contained = len(key) >= MIN_BRAND and (key in other or other in key)
        near = min(len(key), len(other)) >= MIN_ONE_LETTER_OFF and entity_resolve.one_edit_apart(
            key, other
        )
        if key == other or contained or near:
            matches[known] = count
    return max(matches, key=lambda known: (matches[known], known)) if matches else None


def states_a_manufacturer(record, client_names, vocabulary=None):
    """Say whether a record already names a manufacturer worth keeping.

    Not the same question as "is `brand_name` populated". The field holding the
    client is the case this lane exists for -- a billing report prints the
    client as its agent of record -- and skipping those as already-branded left
    160 documents unrecovered on the first run. A territory code is no better an
    answer, and given the vocabulary, neither is a dealer: a page stating
    `EMPALL OFFICE, INC.` as its brand has not named its manufacturer.
    """
    name = document_brand(record)
    if not name or not nameable(name) or NOT_A_BRAND.match(name):
        return False
    if entity_resolve.is_the_client(name, client_names):
        return False
    return vocabulary is None or manufacturer_spelling(name, vocabulary) is not None


def document_brand(record):
    """Return the brand a record already states: its header, the field map, or a line.

    A header whose engines disagreed carries no value, while the field map the
    export pivots still carries the reading it prints -- on Marlow/Bramwell's
    order reports, the client (`Northgate Co LLC` against `Marlow / Bramwell`).
    Reading the header alone called those pages branded by their lines and left
    the client in the documents' brand column.
    """
    header = record.get("header", record)
    name = field_text(header.get("brand_name")) if isinstance(header, dict) else ""
    if name:
        return name
    fields = record.get("fields")
    if isinstance(fields, dict):
        scoped = "header.brand_name" if isinstance(record.get("header"), dict) else "brand_name"
        name = field_text(fields.get(scoped))
        if name:
            return name
    for line in record.get("lines") or []:
        if isinstance(line, dict):
            name = field_text(line.get("brand_name"))
            if name:
                return name
    return ""


# Roles a manufacturer does not also occupy. Narrower than it first looks: on a
# commission statement the manufacturer legitimately IS the vendor, the payee
# and the seller, so excluding those removed the real manufacturers and left a
# vocabulary of nine. What a manufacturer is never is the dealer it sells
# through, the customer, the firm that specified the job, or the agent of record.
OTHER_ROLES = (
    "dealer_name",
    "customer_name",
    "specifier_name",
    "agent_name",
    "ship_to_name",
    "bill_to_name",
    "sold_to_name",
)


def role_counts(records):
    """Count how often the corpus names each party as a brand, and as anything else.

    `brand_name` is the field this corpus mis-files into most -- a dealer, a
    specifier and the client have all landed there -- so it cannot be trusted on
    its own. Nor can a flat exclusion: excluding any name ever seen in another
    role deleted HALVOR, Marlow/Bramwell and Lumen Weft from the vocabulary over
    a handful of mis-filed pages, because one bad page is enough to disqualify a
    real manufacturer.

    So the corpus decides by weight of its own evidence rather than by a rule
    kept here: a name the pages call a brand more often than they call it
    anything else is a manufacturer. No threshold to tune -- the comparison is
    between two counts the corpus supplies.
    """
    brand, other = collections.Counter(), collections.Counter()
    for record in records:
        holders = [record.get("header", record), *(record.get("lines") or [])]
        for holder in holders:
            if not isinstance(holder, dict):
                continue
            name = field_text(holder.get("brand_name"))
            if name:
                brand[_squash(name)] += 1
            for role in OTHER_ROLES:
                name = field_text(holder.get(role))
                if name:
                    other[_squash(name)] += 1
    return brand, other


def brand_vocabulary(records, client_names=(), tops=None):
    """Collect the manufacturers this corpus names, from the pages that name one.

    Derived rather than listed, for the reason every hand-kept list in this
    repository has gone stale: a vocabulary maintained beside the code stops
    matching the corpus it describes. A run that meets a new manufacturer learns
    it from the pages that name it plainly.

    Refused: the client, matched the way `entity_resolve` matches it, because a
    billing report prints the client as its agent of record and that is how
    `brand_name` came to hold it; a market or territory code, which is not a
    name; any name the pages call something other than a brand more often
    than they call it a brand; and, given the tops of the retained pages, a name
    heading fewer than two of them.
    """
    brand, other = role_counts(records)
    counts = collections.Counter()
    for record in records:
        name = document_brand(record)
        if not name or not nameable(name) or NOT_A_BRAND.match(name):
            continue
        if entity_resolve.is_the_client(name, client_names):
            continue
        key = _squash(name)
        if brand[key] <= other[key]:
            continue
        if len(name) >= MIN_BRAND:
            counts[name.casefold()] += 1
    if tops is None:
        return counts
    return collections.Counter(
        {
            name: count
            for name, count in counts.items()
            if sum(1 for top in tops.values() if _squash(name) in top) >= MIN_HEADED_PAGES
        }
    )


def _squash(text):
    """Compare names without case, spacing or punctuation."""
    return re.sub(r"[^a-z0-9]", "", str(text).casefold())


def page_text(path):
    """Return the page text a retained extractor response carries.

    Raises on a response that is not JSON; returns empty for one that parsed and
    carries no text, which is a page the extractor returned nothing for.
    """
    return response_document_text(json.loads(Path(path).read_text()))


def brands_in(head, vocabulary):
    """Return each distinct manufacturer the letterhead names, best spelling first.

    Two collapses, and both were needed. Spellings that squash to one key --
    `murb rook` and `murbrook`, `Marlow / Bramwell` and `Marlow Bramwell` -- are
    one manufacturer; before they were grouped, 100 pages reported a letterhead
    naming several brands and recovered nothing. And a key contained in another
    -- `bramwell` inside `marlowbramwell` -- is the shorter form of the same
    name, so only the longer survives.

    The spelling returned is the one the corpus prints most often, which is the
    same authority `entity_resolve` uses to pick a canonical name.
    """
    return sorted(name for _, name, _ in letterhead_brands(head, vocabulary, one_letter_off=False))


def one_letter_off_at(head, key):
    """Where the letterhead prints a name one letter off, or -1."""
    for start in range(len(head)):
        for width in (len(key) - 1, len(key), len(key) + 1):
            window = head[start : start + width]
            if len(window) == width and entity_resolve.one_edit_apart(window, key):
                return start
    return -1


def letterhead_brands(head, vocabulary, one_letter_off=True):
    """Each manufacturer the letterhead names: where, which spelling, and whether exactly.

    A name of `MIN_ONE_LETTER_OFF` characters or more may match one letter off,
    because a display face is where the extractor misreads a letter: Linden
    World's letterhead arrives as `Lindew World` and `Lindel World`.
    """
    found = {}
    for name, count in vocabulary.items():
        key = _squash(name)
        position, exact = head.find(key), True
        if position < 0 and one_letter_off and len(key) >= MIN_ONE_LETTER_OFF:
            position, exact = one_letter_off_at(head, key), False
        if position < 0:
            continue
        held = found.get(key)
        if held is None or (count, name) > (held[3], held[1]):
            found[key] = (position, name, exact, count)
    keys = [key for key in found if not any(other != key and key in other for other in found)]
    return sorted(found[key][:3] for key in keys)


def settle(matches):
    """The one manufacturer a letterhead names, or None when it cannot say.

    A letterhead naming two has named its own maker at the top and the other
    further down -- a dealer, a line item -- so the only name at the top wins.
    Two at the top, or none of several there, stay a question.
    """
    if len(matches) == 1:
        return matches[0]
    at_top = [match for match in matches if match[0] < TOP_CHARS]
    return at_top[0] if len(at_top) == 1 else None


def opening(text, vocabulary):
    """A page's opening as letters, with any manufacturer's name taken out."""
    letters = re.sub(r"[^a-z]", "", text[:TOP_CHARS].casefold())
    for name in sorted(vocabulary, key=len, reverse=True):
        key = re.sub(r"[^a-z]", "", name.casefold())
        if len(key) >= MIN_BRAND:
            letters = letters.replace(key, "")
    return letters[:OPENING_LETTERS]


def single_manufacturer(spellings):
    """The one manufacturer a count of spellings names, or None when it names several.

    Spellings of one maker differ in suffix -- `marlow / bramwell`,
    `marlow/bramwell inc.` -- so they agree when every key contains the
    shortest; the spelling returned is the commonest.
    """
    keys = sorted({_squash(name) for name in spellings}, key=len)
    if keys and all(keys[0] in key for key in keys):
        return max(spellings, key=lambda name: (spellings[name], name))
    return None


def companies_named_at_top(text, vocabulary, client_names=()):
    """Companies the top of a page names by legal form, other than the client or a known maker."""
    named = []
    for line in text[:TOP_CHARS].splitlines():
        line = line.strip()
        if not COMPANY.match(line):
            continue
        if entity_resolve.is_the_client(line, client_names) or manufacturer_spelling(
            line, vocabulary
        ):
            continue
        named.append(line)
    return named


def layout_words(texts):
    """Each page's words that can describe a layout, by document."""
    words = {doc_id: set(WORD.findall(text.casefold())) for doc_id, text in texts.items()}
    pages = collections.Counter(word for found in words.values() for word in found)
    keep = {
        word
        for word, count in pages.items()
        if MIN_WORD_PAGES <= count <= MAX_WORD_SHARE * len(words)
    }
    return {doc_id: found & keep for doc_id, found in words.items()}


def maker_key(name, vocabulary):
    """One key per maker however the corpus spells it: the shortest known name related to it.

    `marlow / bramwell`, `marlow/bramwell inc.` and `bramwell` are one maker,
    so each is keyed `bramwell`, the name the others contain.
    """
    key = _squash(name)
    related = [
        other for other in map(_squash, vocabulary) if other and (other in key or key in other)
    ]
    return min(related, key=lambda other: (len(other), other)) if related else key


def signatures(settled, vocabulary):
    """Each layout word only one maker's settled pages print, on enough of them, to that maker."""
    seen = collections.defaultdict(collections.Counter)
    for name, words in settled.values():
        maker = maker_key(name, vocabulary)
        for word in words:
            seen[word][maker] += 1
    return {
        word: next(iter(makers))
        for word, makers in seen.items()
        if len(makers) == 1 and sum(makers.values()) >= MIN_WORD_PAGES
    }


def signed_maker(words, signature):
    """The one maker whose own words a page prints, and those words; None when none or several."""
    makers = {signature[word] for word in words if word in signature}
    if len(makers) != 1:
        return None, []
    maker = makers.pop()
    own = sorted(word for word in words if signature.get(word) == maker)
    return (maker, own) if len(own) >= MIN_SIGNATURE_WORDS else (None, [])


def likeness(words, other):
    """The share of two pages' layout words they have in common."""
    either = words | other
    return len(words & other) / len(either) if either else 0.0


def nearest_maker(words, settled):
    """The one maker the most alike settled pages name, and those pages.

    None when fewer than `NEIGHBOURS` pages are settled, when the closest
    shares under `MIN_LIKENESS` of its words, or when the nearest name more
    than one maker.
    """
    ranked = sorted(
        ((likeness(words, other), doc_id, name) for doc_id, (name, other) in settled.items()),
        reverse=True,
    )[:NEIGHBOURS]
    if len(ranked) < NEIGHBOURS or ranked[0][0] < MIN_LIKENESS:
        return None, ranked
    return single_manufacturer(collections.Counter(name for _, _, name in ranked)), ranked


def recovered_field(name, evidence, displaced=""):
    """Build the field object a recovered reading is carried in.

    Deliberately **not** accepted. One extractor read this and no model read it
    at all, which is weaker than the corroboration status pairing a model with
    an extractor, so it travels as a single reading and earns a place in a CRM
    only once someone confirms it.
    """
    return {
        "value": name,
        "candidate_values": [name],
        "source": RECOVERED_SOURCE,
        "consensus_flag": "single_engine",
        "agreeing_engines": [EXTRACTOR_ENGINE],
        "engine_count": 1,
        "rule": RECOVERED_EVIDENCE,
        "accepted": False,
        "is_handwritten": False,
        "blocking": False,
        "queue_for_review": False,
        "evidence_text": evidence,
        "recovered_by": RECOVERED_EVIDENCE,
        # What this reading stands in front of. Where `brand_name` held the
        # client, the amendment has to carry the displaced reading rather than
        # erase it: the page really did print that name in that column, and
        # which column it belongs in is the finding.
        "displaced_reading": displaced,
    }


def attach(record, name, evidence, method):
    """Put a recovered brand on a record, keeping the reading it stands in front of."""
    field = recovered_field(name, evidence, document_brand(record))
    field["recovery_method"] = method
    header = record.get("header")
    scoped = isinstance(header, dict)
    if scoped:
        header["brand_name"] = field
    else:
        record["brand_name"] = field
    # The export pivots the flat field map, and that map scopes a header
    # field as `header.brand_name`. Writing a bare `brand_name` key adds one
    # nothing reads: the recovered brand reached the field grain and never
    # the document grain, and the count of documents missing a manufacturer
    # did not move at all.
    fields = record.get("fields")
    if isinstance(fields, dict):
        fields["header.brand_name" if scoped else "brand_name"] = dict(field)


def recover(records, responses, client_names=()):
    """Carry every record forward, attaching the brand its letterhead printed.

    Three passes. The first reads each letterhead and learns, from every page
    whose maker is settled, how that maker's pages open. The second gives a
    page that names no maker the one every page opening the same way names.
    The third reads a page still unsettled against the settled pages, and gives
    it the maker every one of the most alike names.
    """
    texts, unreadable = {}, set()
    for record in records:
        doc_id = record.get("document_id", "")
        path = responses.get(doc_id)
        if path is None:
            continue
        try:
            texts[doc_id] = page_text(path)
        except (OSError, ValueError):
            unreadable.add(doc_id)
    tops = {doc_id: _squash(text[:TOP_CHARS]) for doc_id, text in texts.items()}
    # With no page read at all, nothing can say which names head pages, and the
    # vocabulary is the records' own.
    vocabulary = brand_vocabulary(records, client_names, tops or None)
    out, exceptions, pending = [], [], []
    openings = collections.defaultdict(collections.Counter)
    settled = {}
    attached = 0
    for record in records:
        record = deepcopy(record)
        out.append(record)
        doc_id = record.get("document_id", "")
        text = texts.get(doc_id, "")
        if states_a_manufacturer(record, client_names, vocabulary):
            name = manufacturer_spelling(document_brand(record), vocabulary)
            if text and name:
                openings[opening(text, vocabulary)][name] += 1
                settled[doc_id] = name
            continue
        if doc_id in unreadable:
            exceptions.append({"document_id": doc_id, "reason": "extractor_layout_unreadable"})
            continue
        if doc_id not in texts:
            exceptions.append({"document_id": doc_id, "reason": "no_retained_extractor_layout"})
            continue
        if not text.strip():
            exceptions.append({"document_id": doc_id, "reason": "no_text_in_the_retained_response"})
            continue
        matches = letterhead_brands(_squash(text[:LETTERHEAD_CHARS]), vocabulary)
        chosen = settle(matches)
        if chosen is None and matches:
            exceptions.append(
                {
                    "document_id": doc_id,
                    "reason": "letterhead_names_several_brands",
                    "candidates": sorted(name for _, name, _ in matches),
                }
            )
            continue
        if chosen is None:
            pending.append((record, text))
            continue
        _, name, exact = chosen
        attach(
            record,
            name,
            "brand printed in the page letterhead"
            if exact
            else "brand printed in the page letterhead, read one letter off",
            "letterhead" if exact else "letterhead_one_letter_off",
        )
        openings[opening(text, vocabulary)][name] += 1
        settled[doc_id] = name
        attached += 1
    unsettled = []
    for record, text in pending:
        key = opening(text, vocabulary)
        seen = openings.get(key, collections.Counter())
        name = (
            single_manufacturer(seen) if key and sum(seen.values()) >= MIN_OPENING_PAGES else None
        )
        if name is None:
            unsettled.append(record)
            continue
        attach(
            record,
            name,
            f"the page opens as {sum(seen.values())} pages of {name} do",
            "opening",
        )
        settled[record.get("document_id", "")] = name
        attached += 1
    words = layout_words(texts)
    alike_pool = {doc_id: (name, words[doc_id]) for doc_id, name in settled.items()}
    signature = signatures(alike_pool, vocabulary)
    spellings = collections.defaultdict(collections.Counter)
    for name in settled.values():
        spellings[maker_key(name, vocabulary)][name] += 1
    for record in unsettled:
        doc_id = record.get("document_id", "")
        named = companies_named_at_top(texts[doc_id], vocabulary, client_names)
        if named:
            exceptions.append(
                {
                    "document_id": doc_id,
                    "reason": "letterhead_names_a_company_never_called_a_brand",
                    "candidates": named,
                }
            )
            continue
        maker, own = signed_maker(words[doc_id], signature)
        if maker is not None:
            name = spellings[maker].most_common(1)[0][0]
            attach(
                record,
                name,
                f"the page prints {len(own)} words only {name}'s pages print: "
                + ", ".join(own[:8]),
                "layout_signature",
            )
            attached += 1
            continue
        name, alike = nearest_maker(words[doc_id], alike_pool)
        if name is None:
            exceptions.append({"document_id": doc_id, "reason": "no_known_brand_in_the_letterhead"})
            continue
        pages = ", ".join(other.rsplit("__", 1)[-1] for _, other, _ in alike)
        attach(
            record,
            name,
            f"the page's layout words are most like {len(alike)} pages of {name} "
            f"({pages}), sharing {alike[0][0]:.0%} with the closest",
            "layout_likeness",
        )
        attached += 1
    return out, exceptions, attached, vocabulary


def recovery_methods(records):
    """How many brands each method recovered, read off the recovered fields."""
    methods = collections.Counter()
    for record in records:
        holder = record.get("header") if isinstance(record.get("header"), dict) else record
        field = holder.get("brand_name")
        if isinstance(field, dict) and field.get("recovery_method"):
            methods[field["recovery_method"]] += 1
    return dict(methods.most_common())


def responses_by_document(directory):
    """Index the retained extractor responses by the document they cover.

    The shared index: a page whose first response came back without text is
    read from its retry, where the first-file index read an empty page.
    """
    return retained_responses(directory)


def records_from(path):
    """Read a record artifact, a list, or a single record."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("documents"), list):
            return data["documents"]
        if "document_id" in data:
            return [data]
    raise ValueError("Input must be a record, a list of records, or contain documents")


def main():
    parser = argparse.ArgumentParser(
        description="Recover the manufacturer from the letterhead the page printed."
    )
    parser.add_argument("input", help="records JSON to carry forward")
    parser.add_argument(
        "--extractor-raw",
        required=True,
        help="directory of retained independent-extractor responses",
    )
    parser.add_argument("--out", required=True, help="records with recovered brands")
    parser.add_argument("--exceptions", required=True, help="pages the recovery could not settle")
    parser.add_argument(
        "--client-name",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "the party whose records these are, repeatable. It is not a "
            "manufacturer, and on a billing report that prints it as the agent "
            "of record it is exactly what `brand_name` wrongly held."
        ),
    )
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        records = records_from(args.input)
        responses = responses_by_document(args.extractor_raw)
        out, exceptions, attached, vocabulary = recover(records, responses, args.client_name)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Brand recovery failed: {exc}")
    methods = recovery_methods(out)
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "artifact_type": ARTIFACT_TYPE,
        "documents": len(out),
        "documents_missing_a_brand": attached + len(exceptions),
        "brands_recovered": attached,
        "brands_recovered_by": methods,
        "exceptions": len(exceptions),
        "brand_vocabulary": len(vocabulary),
        "vocabulary": sorted(vocabulary),
        "findings": [
            "A recovered brand is independent-extractor evidence, never vendor agreement.",
            "It is retained unaccepted: one extractor read it and no model read it at all.",
            "The vocabulary is the corpus's own, so no manufacturer is invented.",
        ],
    }
    Path(args.out).write_text(json.dumps({"summary": summary, "documents": out}, indent=2) + "\n")
    Path(args.exceptions).write_text(
        json.dumps({"summary": summary, "exceptions": exceptions}, indent=2) + "\n"
    )
    if not args.quiet:
        print(f"Documents: {len(out)}   brands recovered: {attached}")
        for method, count in methods.items():
            print(f"    {count:5d}  by {method.replace('_', ' ')}")
        print(f"  vocabulary drawn from the corpus: {len(vocabulary)} manufacturers")
        print(f"  exceptions retained: {len(exceptions)}")
        for reason, count in collections.Counter(e["reason"] for e in exceptions).most_common():
            print(f"    {count:5d}  {reason}")


if __name__ == "__main__":
    main()
