#!/usr/bin/env python3
"""
Phase 3 -- Entity resolution.

Collapses party name and address variants into a deduplicated master.

Small-sounding, and it determines whether the CRM works at all. "ACME Corp",
"Acme Corporation", "ACME CORP." and "Acme Corp - Chicago" are one customer. If
they load as four accounts, every question worth asking -- biggest customers, who
stopped buying, which accounts are growing -- returns a wrong answer, and the
error is not obviously wrong: it looks like four healthy mid-sized accounts
instead of one large one.

Design commitments:

  reversible   every merge is logged with its evidence and can be undone
  conservative ambiguous clusters are flagged for adjudication, not guessed at
  transparent  the survivor is chosen by a stated rule, not by chance ordering

Usage:
    python entity_resolve.py records.json --out parties.json --log merges.json
    python entity_resolve.py records.json --threshold 0.88 --out parties.json
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime

from cli_help import apply_shared_help

# Legal-form suffixes and noise tokens. Stripped for comparison only -- the
# original string is always retained on the surviving record.
# Above this many mentions in one blocking key, pairwise comparison is impractical.
# The block is deferred to review rather than dropped; see resolve().
MAX_BLOCK_PAIRWISE = 400
SUFFIXES = {
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "llc",
    "llp",
    "lp",
    "ltd",
    "limited",
    "plc",
    "gmbh",
    "ag",
    "sa",
    "sas",
    "srl",
    "bv",
    "nv",
    "pty",
    "pte",
    "kk",
    "ab",
    "as",
    "oy",
    "spa",
    "sl",
    "cv",
    "ev",
}
NOISE = {"the", "and", "of", "de", "du", "der", "den"}

ABBREV = {
    "intl": "international",
    "int": "international",
    "mfg": "manufacturing",
    "mfrs": "manufacturers",
    "svcs": "services",
    "svc": "service",
    "distr": "distribution",
    "dist": "distribution",
    "transp": "transport",
    "trans": "transport",
    "logist": "logistics",
    "log": "logistics",
    "ind": "industries",
    "indus": "industries",
    "bros": "brothers",
    "assoc": "associates",
    "natl": "national",
    "nat": "national",
    "amer": "american",
    "am": "american",
    "grp": "group",
    "hldgs": "holdings",
    "hldg": "holdings",
    "ent": "enterprises",
    "sys": "systems",
    "tech": "technologies",
    "whse": "warehouse",
    "frt": "freight",
    "shpg": "shipping",
}

# Branch/location qualifiers. Two records differing only by one of these are the
# same legal party at different sites -- same party, different address.
BRANCH_MARKERS = re.compile(
    r"\b(branch|division|div|plant|warehouse|whse|dc|depot|site|office|"
    r"north|south|east|west|region)\b",
    re.I,
)


# The corpus is an Excel print-to-PDF, so a name wider than its column is cut
# off and marked. The marker is positive evidence that a reading is incomplete.
TRUNCATED_NAME = re.compile(r"(?:\.\.\.|\u2026)\s*$")


def is_truncated(raw):
    """Say whether a name is a reading the source cut off."""
    return bool(TRUNCATED_NAME.search(str(raw or "")))


def collapse_initials(tokens):
    """
    Join runs of single characters. Punctuation stripping turns "L.L.C." into
    "l l c", which then shares no token with "llc" and fails to match -- so a
    dotted legal suffix silently splits one company into two accounts. Same for
    S.A., P.L.C., U.S.A., and initial-based trading names.
    """
    out, run = [], []
    for t in tokens:
        if len(t) == 1 and t.isalpha():
            run.append(t)
        else:
            if run:
                out.append("".join(run))
                run = []
            out.append(t)
    if run:
        out.append("".join(run))
    return out


def normalize_name(raw):
    """Normalize a party name for comparison while preserving the original value."""
    if not raw:
        return ""
    s = str(raw).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    tokens = collapse_initials(s.split())
    kept = []
    for t in tokens:
        t = ABBREV.get(t, t)
        if t in SUFFIXES or t in NOISE:
            continue
        kept.append(t)
    return " ".join(kept) if kept else s


def normalize_address(raw):
    """Normalize an address for comparison while preserving the original value."""
    if not raw:
        return ""
    s = str(raw).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    repl = {
        r"\bstreet\b": "st",
        r"\bavenue\b": "ave",
        r"\broad\b": "rd",
        r"\bdrive\b": "dr",
        r"\bboulevard\b": "blvd",
        r"\bsuite\b": "ste",
        r"\bnorth\b": "n",
        r"\bsouth\b": "s",
        r"\beast\b": "e",
        r"\bwest\b": "w",
        r"\bhighway\b": "hwy",
        r"\bparkway\b": "pkwy",
        r"\bunit\b": "ste",
    }
    for pat, rep in repl.items():
        s = re.sub(pat, rep, s)
    return re.sub(r"\s+", " ", s).strip()


def postal_key(addr):
    """Leading postal-code fragment -- a strong blocking key when present."""
    if not addr:
        return None
    m = re.search(r"\b(\d{5})(?:-\d{4})?\b", str(addr))
    if m:
        return m.group(1)
    m = re.search(r"\b([A-Z]\d[A-Z])\s*\d[A-Z]\d\b", str(addr).upper())
    return m.group(1) if m else None


def token_set_ratio(a, b):
    """Jaccard over token sets. Order-insensitive, which matters because word
    order varies constantly across document formats."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def sequence_ratio(a, b):
    """Character-bigram Dice coefficient. Catches typos and OCR substitutions
    that token comparison misses."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    def bigrams(value):
        return {value[i : i + 2] for i in range(len(value) - 1)}

    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        return 0.0
    return 2 * len(ba & bb) / (len(ba) + len(bb))


def similarity(a, b):
    """Blend of both measures. Token set handles reordering and dropped suffixes;
    bigram handles OCR character damage. Either alone produces obvious misses."""
    if a == b:
        return 1.0
    # Whitespace inside a company name is not information, and treating it as
    # information is not a near miss -- it is a total one. `token_set_ratio`
    # compares token *sets*, so one inserted space makes them disjoint and
    # returns 0.000: `murbrook` against `murb rook` scored 0.320, below even the
    # review band, and one supplier stayed two parties across 125 documents.
    # Identical characters differently spaced are the same name.
    if a.replace(" ", "") == b.replace(" ", ""):
        return 1.0
    return round(0.6 * token_set_ratio(a, b) + 0.4 * sequence_ratio(a, b), 4)


def one_edit_apart(a, b):
    """Report whether two names differ by exactly one character.

    Deliberately narrow. Two edits is a different word; one edit on a name a
    reader saw hundreds of times is the shape OCR damage takes -- `Lumen Weft`
    read once as `Lumen Wefy`, `Linden World` once as `Lindew World`.
    """
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    for index, (left, right) in enumerate(zip(a, b, strict=False)):
        if left == right:
            continue
        # Substitution when the lengths match, otherwise a deletion from the
        # longer name. Either way the rest must be identical.
        return a[index + 1 :] == b[index + 1 :] if len(a) == len(b) else a[index:] == b[index + 1 :]
    return True


def variant_reason(rare, common):
    """Name why a rare spelling is a damaged form of a common one, or None.

    Two shapes, both requiring the frequency gate on top:

    * one character apart, in names of ``MIN_ONE_EDIT_NAME`` characters or
      more -- what OCR does to a letter;
    * every word of the rare name appearing in the common one -- what a reader
      does when it takes `Bramwell` off `Marlow / Bramwell`, or `Weft` off
      `Lumen Weft`. Direction matters: a rare name with a word the common one
      lacks is not a fragment of it. `murbrook ESPACE DE JOUR` is not `murbrook`,
      it is a product line, and merging it would erase that distinction.
    """
    if min(len(rare), len(common)) >= MIN_ONE_EDIT_NAME and one_edit_apart(rare, common):
        return "one_character_variant_of_a_far_more_common_name"
    rare_words, common_words = set(rare.split()), set(common.split())
    if rare_words and rare_words < common_words:
        return FRAGMENT_OF_A_COMMON_NAME
    return None


FRAGMENT_OF_A_COMMON_NAME = "every_word_appears_in_a_far_more_common_name"
# One letter changed in a short name makes another name: at 10:1 on the
# commission run `XTK` went into `XTB, LLC`, a different firm.
MIN_ONE_EDIT_NAME = 6


def absorb_ocr_variants(parties, min_ratio, kept_apart=frozenset()):
    """Absorb a rare one-character variant into the common name it damages.

    This repo's standing rule is to report a one-character difference rather
    than merge it, because merging on a single character is how a genuinely
    separate subsidiary is absorbed into its parent. That rule holds where the
    two names are comparably common. It reads badly at 58 mentions against 1:
    a name seen once, one character from a name seen fifty-eight times, on the
    same corpus, is overwhelmingly the same supplier misread.

    So the merge is gated on evidence rather than on similarity alone -- one
    edit apart *and* a mention ratio at or above ``min_ratio``. Every absorption
    is returned with both counts and the ratio that justified it, and the
    surviving party keeps the absorbed spelling in ``name_variants``, so the
    decision is auditable and reversible. Off unless an operator asks for it:
    the standing rule is the safer default and this is a judgement about one
    corpus.

    A fragment is absorbed only when one other name alone holds every word of
    it. `Design`, `Furniture (` and `Workplace` each sit inside several parties'
    names, and folding one into whichever is most common is a guess about which
    it was cut from: at 10:1 on the commission run that put `The Design` inside
    Cornerwise Design Services. And a pair a person decided about is not this
    rule's to undo. ``kept_apart`` holds the pairs of party keys a decision
    keeps as two -- a branch and its parent among them -- which is why this runs
    after the decisions: run before them, it folded `Office Quarters Inc` into
    its own New York branch and left the decision linking them nothing to link.
    """
    if min_ratio <= 0:
        return parties, []
    words = {party["party_key"]: set(party["normalized_name"].split()) for party in parties}

    def one_name_holds(key):
        """Whether exactly one other party's name carries every word of this one."""
        return sum(1 for other, held in words.items() if other != key and words[key] < held) == 1

    ordered = sorted(parties, key=lambda p: (-p["mention_count"], p["party_key"]))
    absorbed_into, absorptions = {}, []
    for index, rare in enumerate(ordered):
        for common in ordered[:index]:
            if common["party_key"] in absorbed_into:
                continue
            if frozenset((rare["party_key"], common["party_key"])) in kept_apart:
                continue
            if rare["mention_count"] * min_ratio > common["mention_count"]:
                continue
            reason = variant_reason(rare["normalized_name"], common["normalized_name"])
            if reason is None:
                continue
            if reason == FRAGMENT_OF_A_COMMON_NAME and not one_name_holds(rare["party_key"]):
                continue
            absorbed_into[rare["party_key"]] = common["party_key"]
            absorptions.append(
                {
                    "absorbed_party_key": rare["party_key"],
                    "absorbed_name": rare["canonical_name"],
                    "absorbed_mentions": rare["mention_count"],
                    "surviving_party_key": common["party_key"],
                    "surviving_name": common["canonical_name"],
                    "surviving_mentions": common["mention_count"],
                    "mention_ratio": round(
                        common["mention_count"] / max(rare["mention_count"], 1), 1
                    ),
                    "reason": reason,
                }
            )
            common["name_variants"] = sorted(
                set(common["name_variants"]) | set(rare["name_variants"])
            )
            common["variant_count"] = len(common["name_variants"])
            common["mention_count"] += rare["mention_count"]
            common["roles"] = sorted(set(common["roles"]) | set(rare["roles"]))
            common["source_document_count"] += rare["source_document_count"]
            break
    kept = [party for party in parties if party["party_key"] not in absorbed_into]
    return kept, absorptions


def kept_apart_by(applied):
    """The pairs of party keys a person decided stay two: kept apart, or a branch."""
    pairs = set()
    for decision in applied:
        if decision["decision"] == "different_parties":
            pairs.add(frozenset(decision["party_keys"]))
        elif decision["decision"] == "branch_of":
            pairs.add(frozenset((decision["parent_party_key"], decision["branch_party_key"])))
    return frozenset(pairs)


def blocking_keys(norm_name, addr):
    """
    Cheap keys that restrict comparison to plausible candidates. Without
    blocking this is O(n^2) and unusable past a few thousand parties.
    """
    keys = set()
    toks = norm_name.split()
    if toks:
        keys.add(toks[0][:6])
        if len(toks) > 1:
            keys.add("".join(t[0] for t in toks[:4]))
    # A key that survives an inserted space. Without it the scores above never
    # get a chance: `murbrook` blocked on {'murbro'} and `murb rook` on
    # {'mf', 'mura'}, so the pair was never compared at any threshold.
    squashed = norm_name.replace(" ", "")
    if squashed:
        keys.add(squashed[:6])
    pk = postal_key(addr)
    if pk:
        keys.add(f"pc:{pk}")
    return keys or {"_unblocked"}


def load_records(data):
    """
    Accept any stage's output. Every script in the chain emits its records under
    a slightly different key; a loader that only knows one of them silently
    reads zero records and reports "nothing found" instead of failing loudly.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("documents", "results", "attributions", "records"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return []


def nameable(value):
    """Report whether a value can be a party name at all.

    A name has to contain a letter. Account numbers, row codes and a bare `0`
    reach these fields when an engine files a value under the wrong column, and
    without this each one becomes its own resolved party -- and then its own CRM
    account, with its own concentration and retention figures. On one run that
    produced `7144`, `25556` and `0` as three suppliers.
    """
    return any(character.isalpha() for character in str(value))


# A page prints an account beside the number that says which of its locations
# this row belongs to: `7144 KD Frost`, `7373 Empall`, `150-NGC`. The number is a
# location key, not part of the name, and leaving it attached splits one account
# in two -- `KD Frost` carries 442 mentions and `7144 KD Frost` another 3. It is
# lifted off before the name is compared, so both land in one account and the
# identifiers survive on the party as the set of locations the corpus showed.
LOCATION_IDENTIFIER = re.compile(r"^(?P<identifier>\d{2,6})\s*[-\s]\s*(?P<name>\D.*)$")
# A street number is not a location key. `801 Bayshore Avenue` and `2200
# Bayshore Avenue` are two Miami buildings, `4100 NE 20TH AVE ...` is an address
# with its city attached, and in each the digits belong to the name.
STREET_WORD = re.compile(
    r"\b(?:st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|ln|lane|way|pkwy"
    r"|parkway|hwy|highway|cir|circle|ct|court|pl|place|ter|terrace|suite|ste)\b",
    re.I,
)
DIRECTION_FIRST = re.compile(r"^(?:n|s|e|w|ne|nw|se|sw|north|south|east|west)\b", re.I)
# A column's heading, a role, or what a form prints when there is no party,
# read as the name that belongs under it. `COMPANY` heads the first column of
# Marlow/Bramwell's weekly order report and was read as the dealer on 11 lines;
# `INSTALL REP` names a role; `None`, `N/A` and `TBD` name nobody. Each became a
# resolved party, which is a CRM account. Compared on the loose form, so case
# and punctuation do not matter, and only whole names: `LEBNER+COMPANY` stays.
NOT_A_PARTY_NAME = frozenset(
    {"company", "dealer of", "install rep", "none", "n a", "na", "tbd", "unknown", "various"}
)
# A total row's label and a charge line's adjustment, read into a party column.
# `Total Commissions to date:` and `Total :` label a statement's closing row;
# `-70% Specify Territory` is a territory split Marlow/Bramwell prints as a
# line of its own. Each resolved to a dealer. A total word before a colon makes
# a label and a signed percentage makes an adjustment; a colon alone does not,
# so `THE COVE:` is still a customer, and `Total Wine & More` is still a name.
TOTAL_LABEL = re.compile(r"^(?:grand\s+|sub\s*)?totals?\b[^:]*:\s*$", re.I)
SIGNED_PERCENT_FIRST = re.compile(r"^[-+\u2212]\s*\d+(?:\.\d+)?\s*%")
# A street number followed by a street word is a place, whatever field it sits
# in. `4100 NE 20TH AVE HARBOR POINT, FL 33000` is the client's own address,
# read as a payee; `801 Bayshore Avenue` is a building a line was sold into.
STREET_NUMBER_FIRST = re.compile(r"^\d{1,6}\s")


def is_a_label(name):
    """Whether a party reading is a heading, a placeholder, a total's label or an adjustment."""
    text = str(name or "").strip()
    return bool(
        loose_name(text) in NOT_A_PARTY_NAME
        or TOTAL_LABEL.match(text)
        or SIGNED_PERCENT_FIRST.match(text)
    )


def is_a_street_address(name):
    """Whether a party reading is a street address rather than a name."""
    text = str(name or "").strip()
    return bool(STREET_NUMBER_FIRST.match(text) and STREET_WORD.search(text))


def split_location_identifier(raw):
    """Separate a leading location key from the account name printed with it.

    Returns `(identifier, account_name)`, and `("", the name unchanged)` when the
    leading digits are part of the name rather than a key.
    """
    text = str(raw or "").strip()
    match = LOCATION_IDENTIFIER.match(text)
    if not match:
        return "", text
    name = match.group("name").strip()
    if STREET_WORD.search(name) or DIRECTION_FIRST.match(name) or not nameable(name):
        return "", text
    return match.group("identifier"), name


def loose_name(raw):
    """Normalize a name for comparison while KEEPING its legal suffix.

    `normalize_name` drops `co`, `llc` and their kin, which is right for
    grouping and wrong for recognising a truncation: this corpus cuts
    `NORTHGATE CO LLC` down to `NORTHGATE C`, and the cut lands inside the very
    suffix normalization removes. Compared against the stripped form, the
    remainder stops looking like a prefix at all.
    """
    text = re.sub(r"[^\w\s]", " ", str(raw or "").lower().replace("&", " and "))
    return re.sub(r"\s+", " ", text).strip()


def is_the_client(name, client_names):
    """Say whether this reading names the client whose records these are.

    The client is on nearly every page -- as a statement's addressee, as the
    agent of record, in the letterhead -- and none of those make it a
    counterparty. Left in, it becomes the largest account in the CRM and turns
    up as its own customer, its own dealer and its own brand.

    Three ways a page names it, all of which appeared on this corpus:

    * exactly, once legal suffixes are set aside -- `Northgate & Co`, `NORTHGATE CO.`
    * cut off mid-word -- `NORTHGATE C` for `NORTHGATE CO LLC`. The continuation has
      to be a letter, for the reason `completed_by` gives.
    * with something appended -- `NORTHGATE CO Total` is a totals row and
      `Northgate Co, LLC JM` carries an annotator's initials. The client's name
      has to end on a token boundary, so `Northgate Corp` is not the client.
    """
    _, account = split_location_identifier(name)
    strict, loose = normalize_name(account), loose_name(account)
    for client in client_names:
        target_strict, target_loose = normalize_name(client), loose_name(client)
        # A blank declaration names nobody. It is skipped rather than answered,
        # so one empty entry in the list cannot decide the whole question.
        if not target_loose:
            continue
        # Equality is tested on the stripped form only. Two names whose loose
        # forms match have identical stripped forms as well, so a second
        # equality test here would be unreachable rather than lenient.
        if strict and strict == target_strict:
            return True
        if (
            len(loose) >= MIN_PREFIX_NAME
            and target_loose.startswith(loose)
            and target_loose[len(loose)].isalpha()
        ):
            return True
        if loose.startswith(f"{target_loose} "):
            return True
    return False


PARTY_ROLE_FIELDS = [
    ("seller_name", "seller_address", "biller"),
    ("buyer_name", "buyer_address", "payer"),
    ("ship_to_name", "ship_to_address", "consignee"),
    ("shipper_name", "origin_address", "shipper"),
    ("consignee_name", "destination_address", "consignee"),
    ("vendor_name", "vendor_address", "biller"),
    ("carrier_name", None, "carrier"),
    ("payer_name", None, "payer"),
    ("payee_name", None, "biller"),
    # A brand is a party here. In a manufacturers' representative
    # engagement the supplier is the counterparty that owes the commission,
    # and its name arrives spelled several ways: one corpus held the same
    # supplier as `murb rook` on 90 documents and `murbrook` on 35, and
    # another as `Marlow / Bramwell` on 84 and `MARLOW/BRAMWELL INC.` on 21.
    # Left unresolved, every figure grouped by brand is wrong, and the field
    # was outside this control entirely because a brand did not look like a
    # party.
    ("brand_name", None, "brand"),
    ("manufacturer_name", None, "brand"),
    ("supplier_name", None, "brand"),
    # The counterparty this corpus actually names. These nine fields were
    # outside the control while the list held only the nine above, so a
    # commission statement -- which prints its customer on every line and
    # never in the header -- resolved one mention per document instead of
    # thousands, and 836 spellings of a few hundred customers reached the
    # CRM unresolved.
    ("customer_name", "customer_address", "customer"),
    ("sold_to_name", "sold_to_address", "customer"),
    ("bill_to_name", "bill_to_address", "customer"),
    ("dealer_name", None, "dealer"),
    ("remit_to_name", "remit_to_address", "biller"),
    ("broker_name", None, "broker"),
    ("agent_name", None, "agent"),
    ("warehouse_name", None, "warehouse"),
    # The firm that specified the product. It is a counterparty in its own
    # right -- who specified a line drives attribution on a commission corpus
    # -- and it is not the brand, the dealer or the customer.
    ("specifier_name", None, "specifier"),
]
# Deliberately absent: `contact_name` and `sales_representative_name`. Those
# name people, and resolving a person into the organization master puts a
# named individual inside a customer account.


def extract_parties(records, client_names=()):
    """Pull party mentions out of extracted document records."""
    mentions, rejected = [], []
    for rec in records:
        doc_id = rec.get("document_id", "unknown")
        # Header and lines both. A party named only on the lines was invisible
        # to this lane, which is the same defect as a field it never looked at.
        holders = [("header", rec.get("header", rec))]
        lines = rec.get("lines")
        if isinstance(lines, list):
            holders += [
                (f"lines[{index}]", line)
                for index, line in enumerate(lines)
                if isinstance(line, dict)
            ]
        for scope, holder in holders:
            party_mentions_from(holder, scope, doc_id, mentions, rejected, client_names)
    return mentions, rejected


def party_mentions_from(holder, scope, doc_id, mentions, rejected, client_names=()):
    """Collect party mentions from one header or line object."""

    def val(key):
        value = holder.get(key)
        return value.get("value") if isinstance(value, dict) else value

    for name_f, addr_f, role in PARTY_ROLE_FIELDS:
        name = val(name_f)
        if not name or not str(name).strip():
            continue
        if not nameable(name):
            # A value with no letter in it is not a party name. On one run
            # `7144`, `25556` and `0` became three CRM accounts because a
            # field that should hold a supplier held an account number, and
            # every one of them resolved to a party of its own. Retained as
            # an exception, never resolved: the reading is real and the
            # field it landed in is wrong, which is a mapping finding rather
            # than a party.
            rejected.append(
                {
                    "raw_name": str(name).strip(),
                    "role": role,
                    "document_id": doc_id,
                    "field": name_f,
                    "scope": scope,
                    "reason": "party_name_carries_no_letter",
                }
            )
            continue
        if is_the_client(name, client_names):
            # The client is the party these records belong to, not a party they
            # transacted with. It is printed on nearly every page -- as the
            # addressee of a commission statement, as the agent of record in a
            # billing report, in the letterhead -- and on this corpus that put
            # it in thirteen role fields across 2,333 cells, making it the
            # largest account in the CRM and its own customer, dealer and
            # brand. Retained as an exception rather than dropped: the reading
            # is a true reading of the page, and which party is the client is a
            # fact about the engagement rather than about the document.
            rejected.append(
                {
                    "raw_name": str(name).strip(),
                    "role": role,
                    "document_id": doc_id,
                    "field": name_f,
                    "scope": scope,
                    "reason": "the_client_is_not_a_counterparty",
                }
            )
            continue
        if is_a_label(name) or is_a_street_address(name):
            # A heading, a placeholder, a total's label, an adjustment or an
            # address is a true reading of the page filed under a party field.
            # Retained with its reason, never resolved, for the same reason a
            # name with no letter is: the field is wrong, not the reading.
            rejected.append(
                {
                    "raw_name": str(name).strip(),
                    "role": role,
                    "document_id": doc_id,
                    "field": name_f,
                    "scope": scope,
                    "reason": (
                        "party_name_is_a_street_address"
                        if is_a_street_address(name)
                        else "party_name_is_a_label_or_placeholder"
                    ),
                }
            )
            continue
        addr = val(addr_f) if addr_f else None
        identifier, account = split_location_identifier(str(name).strip())
        mentions.append(
            {
                # The reading exactly as the page printed it, identifier and all.
                "raw_name": str(name).strip(),
                # The same reading with the location key lifted off, which is
                # what the name is compared and grouped on.
                "account_name": account,
                "location_identifier": identifier,
                "raw_address": str(addr).strip() if addr else None,
                "role": role,
                "document_id": doc_id,
                "field": name_f,
                "scope": scope,
            }
        )


MIN_PREFIX_NAME = 4


def completed_by(names):
    """Map each cut-off name to the one longer name that completes it.

    Part of this corpus is a spreadsheet printed too narrow, so a party arrives
    cut mid-word with nothing marking it: `wb latha` for `wb latham`, `holden`
    for `holdens business environments`, `merrow off` for `merrow office
    products`. Similarity scoring cannot rescue those -- a third of the name is
    simply absent -- so each loads as its own account.

    The evidence is containment, not resemblance: a name that is a strict
    prefix of a longer name and stops inside a *word* was cut. The completion
    must continue with a letter, because a prefix of a number is not a
    truncation -- `Supply Co 40` and `Supply Co 400` are two accounts. A name
    completed by several longer names is left alone, because choosing between
    them is a question rather than an answer.
    """
    by_length = sorted(names, key=len, reverse=True)
    completions = {}
    for name in names:
        if len(name) < MIN_PREFIX_NAME:
            continue
        longer = [
            other
            for other in by_length
            if len(other) > len(name) and other.startswith(name) and other[len(name)].isalpha()
        ]
        maximal = [
            other for other in longer if not any(o != other and o.startswith(other) for o in longer)
        ]
        if len(maximal) == 1:
            completions[name] = maximal[0]
    return completions


def cluster(mentions, threshold, review_band):
    """Group party records into reversible clusters with an explicit review band.

    A blocking key above the pairwise-comparison cap is deferred to review as an
    explicit item rather than dropped, because the largest blocks are the party
    names most worth resolving."""
    for m in mentions:
        m["norm_name"] = normalize_name(m.get("account_name") or m["raw_name"])
        m["norm_address"] = normalize_address(m["raw_address"])
        m["postal"] = postal_key(m["raw_address"])

    # Compare distinct readings, not repetitions of them. A corpus prints the
    # same customer on thousands of lines, and comparing every repetition
    # against every other pushes precisely the highest-cardinality names past
    # the pairwise cap -- deferring the parties most worth resolving. One
    # representative per reading is compared; the rest are united with it below,
    # so the grouping is unchanged apart from being reachable at all.
    readings = defaultdict(list)
    for i, m in enumerate(mentions):
        readings[(m["norm_name"], m["norm_address"], m["postal"])].append(i)

    buckets = defaultdict(list)
    for group in readings.values():
        m = mentions[group[0]]
        for k in blocking_keys(m["norm_name"], m["raw_address"]):
            buckets[k].append(group[0])

    parent = list(range(len(mentions)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    evidence, ambiguous = [], []
    seen_pairs = set()

    # United on an identical normalized reading, and recorded once per reading
    # rather than once per pair: the merge stays auditable and reversible
    # without writing a line for every repetition of the same customer.
    # Unite a name the page cut off with the one name that completes it. This
    # runs before the pairwise pass because similarity cannot see it: `wb latha`
    # and `wb latham` score well, but `holden` and `holdens business
    # environments` do not, and both are one party.
    first_by_name = {}
    for group in readings.values():
        first_by_name.setdefault(mentions[group[0]]["norm_name"], group[0])
    for cut, whole in completed_by(set(first_by_name)).items():
        union(first_by_name[cut], first_by_name[whole])
        evidence.append(
            {
                "a": mentions[first_by_name[cut]]["raw_name"],
                "b": mentions[first_by_name[whole]]["raw_name"],
                "name_score": None,
                "address_score": None,
                "same_postal": False,
                "decision": "merged",
                "reason": "name_cut_off_mid_word_and_completed_by_one_other",
            }
        )

    for group in readings.values():
        if len(group) < 2:
            continue
        for member in group[1:]:
            union(group[0], member)
        evidence.append(
            {
                "a": mentions[group[0]]["raw_name"],
                "b": mentions[group[1]]["raw_name"],
                "name_score": 1.0,
                "address_score": None,
                "same_postal": bool(mentions[group[0]]["postal"]),
                "decision": "merged",
                "reason": "identical_normalized_reading",
                "mention_count": len(group),
            }
        )

    for block_key, idxs in sorted(buckets.items(), key=lambda item: str(item[0])):
        if len(idxs) > MAX_BLOCK_PAIRWISE:
            # An exhaustive O(n^2) comparison of a very large block is impractical,
            # but the excluded population is the highest-cardinality party names --
            # the ones most worth resolving. Skipping them silently left the
            # un-merged variants looking like distinct vendors downstream, so the
            # deferral is registered as review work instead of disappearing.
            ambiguous.append(
                {
                    "a": None,
                    "b": None,
                    "blocking_key": str(block_key),
                    "mention_count": len(idxs),
                    "name_score": None,
                    "address_score": None,
                    "same_postal": False,
                    "decision": "pending_adjudication",
                    "reason": (
                        f"blocking key holds {len(idxs)} mentions, above the "
                        f"{MAX_BLOCK_PAIRWISE} pairwise-comparison cap; this block was "
                        "not resolved and its variants remain separate parties"
                    ),
                }
            )
            continue
        for ii in range(len(idxs)):
            for jj in range(ii + 1, len(idxs)):
                a, b = idxs[ii], idxs[jj]
                pair = (min(a, b), max(a, b))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                ma, mb = mentions[a], mentions[b]
                score = similarity(ma["norm_name"], mb["norm_name"])
                if score < review_band:
                    continue

                addr_score = None
                if ma["norm_address"] and mb["norm_address"]:
                    addr_score = similarity(ma["norm_address"], mb["norm_address"])
                same_postal = ma["postal"] and ma["postal"] == mb["postal"]

                # Matching postal code is strong corroboration; a differing one
                # is weak evidence against, since a company has many sites.
                effective_score = score
                if same_postal:
                    effective_score = min(1.0, score + 0.05)

                branchy = bool(
                    BRANCH_MARKERS.search(ma["raw_name"]) or BRANCH_MARKERS.search(mb["raw_name"])
                )

                if effective_score >= threshold:
                    union(a, b)
                    evidence.append(
                        {
                            "a": ma["raw_name"],
                            "b": mb["raw_name"],
                            "name_score": score,
                            "address_score": addr_score,
                            "same_postal": bool(same_postal),
                            "decision": "merged",
                            "reason": "branch_variant_of_same_party"
                            if branchy
                            else "name_similarity_above_threshold",
                        }
                    )
                else:
                    ambiguous.append(
                        {
                            "a": ma["raw_name"],
                            "b": mb["raw_name"],
                            "name_score": score,
                            "address_score": addr_score,
                            "same_postal": bool(same_postal),
                            "decision": "pending_adjudication",
                            "reason": (
                                f"score {score} sits between review band "
                                f"{review_band} and merge threshold {threshold}"
                            ),
                        }
                    )

    groups = defaultdict(list)
    for i in range(len(mentions)):
        groups[find(i)].append(i)
    return groups, evidence, ambiguous


def restores_the_same_words(short, long_form):
    """Say whether the longer name is the shorter one finished, not extended.

    A truncation restores the rest of words that were already starting. It does
    not introduce a code: `KOK Interi` and `KOK Interic PO4705` differ by a
    purchase-order number, and naming an account after that is worse than naming
    it after the stump. So a completion whose added text carries a digit is
    refused -- a name and a name-plus-an-identifier are not the same reading.

    Compared on the loose form rather than the raw text, because the page prints
    the same party as `The Furnis` in one column and `THE FURNISHERS UNLIMITED`
    in another, and as `HOLDEN'S BUSINESS ENV.` with a full stop the longer
    spelling does not have. Case and punctuation each turn a completion into two
    unrelated names under a raw prefix test.
    """
    a, b = loose_name(short), loose_name(long_form)
    if len(b) <= len(a) or not b.startswith(a) or not b[len(a)].isalpha():
        return False
    return not any(character.isdigit() for character in b[len(a) :])


def stumps_among(names):
    """Return the names that one longer name in the same party finishes.

    The same containment evidence `completed_by` uses for clustering, applied to
    the choice of canonical name so the two cannot disagree. A name several
    different longer names could finish is left alone, because choosing between
    them is a question rather than an answer.

    Candidates are counted as different only when they normalize differently.
    `OFFICE SURROUNDINGS & SERVICES` and `Office Surroundings and Services` are
    one answer printed two ways, and treating them as a disagreement left the
    account named `Office Surr`.
    """
    stumps = set()
    for name in names:
        if len(name) < MIN_PREFIX_NAME:
            continue
        longer = [other for other in names if restores_the_same_words(name, other)]
        maximal = [
            other
            for other in longer
            if not any(o != other and restores_the_same_words(other, o) for o in longer)
        ]
        if len({normalize_name(other) for other in maximal}) == 1:
            stumps.add(name)
    return stumps


def choose_survivor(variants, declared=None):
    """
    Survivor rule, stated so it can be audited:
      0. a name the operator declared for this party, if one matches it
      1. most frequently occurring raw form
      2. tie-break on longest string (usually the most complete legal name)
      3. tie-break alphabetically for determinism across runs

    Rule 0 exists because frequency is an unreliable chooser and nothing
    internal to the data can replace it. A supplier appeared as `murb rook` on
    147 documents against 136 spellings of `Murbrook`, so frequency made an OCR
    artefact the canonical name. The obvious repair -- prefer the spelling
    without the internal space -- is worse: `Lumen Weft` appears 321 times
    against 11 of `LumenWeft`, and `Marlow / Bramwell` 671 times against 67 of
    `Marlow/Bramwell`, so that rule would corrupt two names to fix one.

    A space wrongly inserted and a space wrongly dropped are both OCR damage and
    look identical from inside the corpus. So the code does not guess: it keeps
    frequency, and `spacing_variants` reports every cluster where the question
    arises, for an operator to settle with `--canonical-name`.
    """
    counts = defaultdict(int)
    for v in variants:
        counts[v] += 1
    if declared:
        for name in declared:
            if any(_squashed(name) == _squashed(v) for v in counts):
                return name
    # A cut-off reading never becomes the canonical name while a complete one is
    # present, however often the truncated form was printed: the corpus is an
    # Excel print-to-PDF, and a name wider than its column is evidence about the
    # reading rather than about the party.
    #
    # There are two kinds of cut and `is_truncated` sees only the first. Some
    # readings carry an ellipsis. Most do not -- the page simply stops, and
    # `Office Surr`, `The Furnis` and `QRC Busin` are printed looking exactly
    # like whole names. Frequency then makes the stump canonical: 14 accounts on
    # the commission run were named for their own truncation while the complete
    # spelling sat in the same party's variants, so a CRM would have shown
    # `Office Surr` for `OFFICE SURROUNDINGS & SERVICES`.
    #
    # `completed_by` already knows how to see an unmarked cut -- by containment
    # rather than resemblance, and declining to answer when several longer names
    # could be the completion. Reusing it here keeps the survivor rule and the
    # clustering rule from disagreeing about which readings are stumps.
    stumps = stumps_among(list(counts))
    whole = {v: n for v, n in counts.items() if not is_truncated(v) and v not in stumps}
    return sorted((whole or counts).items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))[0][0]


def spacing_variants(variants):
    """Return the spellings of one name that differ only by internal whitespace.

    Their existence means the canonical name was chosen by a count between forms
    that OCR damage produced, which is not a decision this code can make well.
    An empty result means the question does not arise for this party.
    """
    grouped = defaultdict(set)
    for name in variants:
        grouped[_squashed(name)].add(name)
    return sorted(
        name
        for spellings in grouped.values()
        if len({" " in spelling.strip() for spelling in spellings}) > 1
        for name in spellings
    )


def _squashed(name):
    """The comparison form: no whitespace, no case."""
    return "".join(str(name).split()).casefold()


def build_parties(mentions, groups, declared=None):
    """Collect distinct party records from the supplied extraction records."""
    parties = []
    for gid, idxs in sorted(groups.items()):
        members = [mentions[i] for i in idxs]
        raw_names = [m["raw_name"] for m in members]
        # The survivor is chosen among the names with their location key lifted
        # off, so an account does not end up called `7373 Empall`. Every printed
        # reading is still carried in `name_variants`.
        survivor = choose_survivor(
            [m.get("account_name") or m["raw_name"] for m in members], declared
        )
        variants = sorted(set(raw_names))
        identifiers = sorted(
            {m["location_identifier"] for m in members if m.get("location_identifier")}
        )
        roles = sorted({m["role"] for m in members})
        addresses = sorted({m["raw_address"] for m in members if m["raw_address"]})
        docs = sorted({m["document_id"] for m in members})

        parties.append(
            {
                "party_key": f"PTY-{gid:06d}",
                "canonical_name": survivor,
                "normalized_name": normalize_name(survivor),
                "name_variants": variants,
                "variant_count": len(variants),
                "roles": roles,
                # The location keys this account was printed with. One account
                # with several is one account with several locations -- `NGC`
                # carries 150 and 900 -- and the key is what picks the address
                # for a given row, so it belongs beside the account and not
                # inside its name.
                "location_identifiers": identifiers,
                "addresses": addresses,
                "mention_count": len(members),
                "source_document_ids": docs[:50],
                "source_document_count": len(docs),
                # Spellings of this name that differ only by internal
                # whitespace. When present, the canonical name above was picked
                # by a count between forms OCR damage produced, and an operator
                # should settle it with --canonical-name.
                "spacing_variants": spacing_variants(variants),
                "canonical_name_declared": bool(
                    declared and any(_squashed(survivor) == _squashed(name) for name in declared)
                ),
                "needs_review": len(variants) > 6 or len(roles) > 2,
            }
        )
    parties.sort(key=lambda p: -p["mention_count"])
    return parties


# Applied in this order, whatever order the file gives: a branch must hang from
# the party that survives its merges, not from one a later merge absorbs.
PARTY_DECISIONS = ("same_party", "not_a_party", "branch_of", "different_parties")


def load_party_decisions(path):
    """Read a person's decisions about named pairs, refusing an unauthorized file.

    Similarity can only propose. The pairs it holds between the review band and
    the threshold need knowledge the corpus does not carry -- a public record,
    a branch printed elsewhere, the client's own ruling -- and 44 of them sat
    there on the commission run. A decision names the authorization it rests
    on, so a merge a person made is never mistaken for one a score made.
    """
    with open(path) as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not str(data.get("authorization") or "").strip():
        sys.exit(f"{path}: party decisions must name the authorization they rest on")
    decisions = data.get("decisions")
    if not isinstance(decisions, list):
        sys.exit(f"{path}: 'decisions' must be a list")
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict) or decision.get("decision") not in PARTY_DECISIONS:
            sys.exit(f"{path}: decision {index} must be one of {', '.join(PARTY_DECISIONS)}")
        if not all(str(decision.get(side) or "").strip() for side in ("a", "b")):
            sys.exit(f"{path}: decision {index} must name both 'a' and 'b'")
    return data["authorization"], decisions


def party_holding(parties, name):
    """The party a printed name resolved to, matched ignoring case and whitespace."""
    wanted = _squashed(name)
    for party in parties:
        if any(_squashed(v) == wanted for v in [party["canonical_name"], *party["name_variants"]]):
            return party
    return None


def fold_party(survivor, absorbed):
    """Fold one party into another, keeping every spelling, key and document."""
    for field in ("name_variants", "roles", "location_identifiers", "addresses"):
        survivor[field] = sorted(set(survivor[field]) | set(absorbed[field]))
    survivor["variant_count"] = len(survivor["name_variants"])
    survivor["mention_count"] += absorbed["mention_count"]
    survivor["source_document_ids"] = sorted(
        set(survivor["source_document_ids"]) | set(absorbed["source_document_ids"])
    )[:50]
    survivor["source_document_count"] += absorbed["source_document_count"]


def apply_party_decisions(parties, decisions, authorization):
    """Apply what a person decided about named pairs; report what matched nothing.

    ``same_party`` folds the less-mentioned party into the other, as an
    absorption does, under the survivor's name unless the decision declares one
    of the printed spellings. ``not_a_party`` removes the parties holding either
    name -- a project or a site printed in a party column -- and retains them.
    ``branch_of`` keeps both accounts and records ``a`` as ``b``'s parent: a
    branch is its own location, so merging it loses which site bought what,
    and leaving it unrelated loses the company's total. ``different_parties``
    keeps two parties apart. Clustering that already joined what a decision
    keeps apart is reported, never undone here: splitting a party needs the
    mentions, and a person should see it first.
    """
    applied, unmatched, removed = [], [], []
    for decision in sorted(decisions, key=lambda d: PARTY_DECISIONS.index(d["decision"])):
        kind, a_name, b_name = decision["decision"], decision["a"], decision["b"]
        a, b = party_holding(parties, a_name), party_holding(parties, b_name)
        record = {
            "a": a_name,
            "b": b_name,
            "decision": kind,
            "authorization": authorization,
            "evidence": decision.get("evidence", ""),
        }
        if kind == "not_a_party":
            gone = {p["party_key"]: p for p in (a, b) if p is not None}
            if not gone:
                unmatched.append({**record, "reason": "neither_name_resolved_to_a_party"})
                continue
            removed.extend({**p, "removed_by": record} for p in gone.values())
            parties = [p for p in parties if p["party_key"] not in gone]
            applied.append({**record, "removed_party_keys": sorted(gone)})
            continue
        if a is None or b is None:
            missing = [name for name, party in ((a_name, a), (b_name, b)) if party is None]
            unmatched.append(
                {**record, "reason": "a_name_resolved_to_no_party", "missing": missing}
            )
            continue
        if a is b:
            if kind == "same_party":
                applied.append(
                    {**record, "party_keys": [a["party_key"]], "already_one_party": True}
                )
            else:
                unmatched.append(
                    {
                        **record,
                        "reason": "clustering_already_joined_them",
                        "party_key": a["party_key"],
                    }
                )
            continue
        if kind == "different_parties":
            applied.append({**record, "party_keys": [a["party_key"], b["party_key"]]})
        elif kind == "branch_of":
            b["parent_party_key"], b["parent_name"] = a["party_key"], a["canonical_name"]
            a["branch_party_keys"] = sorted({*a.get("branch_party_keys", []), b["party_key"]})
            applied.append(
                {**record, "parent_party_key": a["party_key"], "branch_party_key": b["party_key"]}
            )
        else:
            declared = str(decision.get("canonical_name") or "").strip()
            spellings = [
                a["canonical_name"],
                *a["name_variants"],
                b["canonical_name"],
                *b["name_variants"],
            ]
            printed = [s for s in spellings if declared and _squashed(s) == _squashed(declared)]
            if declared and not printed:
                unmatched.append({**record, "reason": "declared_name_is_not_a_printed_spelling"})
                continue
            survivor, absorbed = sorted((a, b), key=lambda p: (-p["mention_count"], p["party_key"]))
            fold_party(survivor, absorbed)
            if printed:
                survivor["canonical_name"] = printed[0]
                survivor["normalized_name"] = normalize_name(printed[0])
                survivor["canonical_name_declared"] = True
            parties = [p for p in parties if p is not absorbed]
            applied.append(
                {
                    **record,
                    "surviving_party_key": survivor["party_key"],
                    "absorbed_party_key": absorbed["party_key"],
                    "canonical_name": survivor["canonical_name"],
                }
            )
    return parties, applied, unmatched, removed


def settle_pending(ambiguous, decisions):
    """Take each pending pair a decision names out of adjudication, keeping it."""
    decided = {frozenset((_squashed(d["a"]), _squashed(d["b"]))): d["decision"] for d in decisions}
    pending, settled = [], []
    for entry in ambiguous:
        key = frozenset((_squashed(entry.get("a") or ""), _squashed(entry.get("b") or "")))
        if entry.get("a") and key in decided:
            settled.append({**entry, "decision": "decided", "party_decision": decided[key]})
        else:
            pending.append(entry)
    return pending, settled


def main():
    ap = argparse.ArgumentParser(description="Resolve party names into a deduplicated master.")
    ap.add_argument("input", help="JSON records, or consensus.json")
    ap.add_argument("--out", default="parties.json")
    ap.add_argument("--log", default="merges.json", help="reversible merge log")
    ap.add_argument(
        "--threshold",
        type=float,
        default=0.86,
        help="merge at or above this similarity (default 0.86)",
    )
    ap.add_argument(
        "--review-band",
        type=float,
        default=0.72,
        help="flag for adjudication at or above this (default 0.72)",
    )
    ap.add_argument(
        "--canonical-name",
        action="append",
        default=None,
        metavar="NAME",
        help=(
            "Declare the canonical spelling for a party, repeatable. Matched to a cluster "
            "ignoring case and whitespace, so it need not reproduce a variant exactly. "
            "Spelling authority is the engagement's, not a frequency count's: one supplier "
            "appeared as 'murb rook' on 90 documents and 'Murbrook' on 35."
        ),
    )
    ap.add_argument(
        "--absorb-ocr-variants",
        type=float,
        default=0.0,
        metavar="RATIO",
        help=(
            "Absorb a party whose name is one character from a far more common one, "
            "or whose every word only that one name also carries, when the common "
            "name has at least RATIO times its mentions. Runs after --party-decisions "
            "and never joins a pair a decision keeps apart, or a branch to its parent. "
            "Off by default: the standing rule reports a one-character difference "
            "rather than merging it, because merging on a single character is how a "
            "separate subsidiary is absorbed into its parent. Every absorption is "
            "retained with both counts and the ratio that justified it."
        ),
    )
    ap.add_argument(
        "--client-name",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "the party whose records these are, repeatable. It is printed on "
            "nearly every page -- as a statement's addressee, as the agent of "
            "record, in the letterhead -- and none of that makes it a "
            "counterparty. Left unset, the client becomes the largest account "
            "in the resolved master and appears as its own customer, dealer and "
            "brand. Matched on the normalized name and on a truncation of it, so "
            "one spelling covers the cut-off forms the corpus prints. Every "
            "refusal is retained as an exception with the field and document it "
            "came from, never dropped."
        ),
    )
    ap.add_argument(
        "--party-decisions",
        metavar="PATH",
        help=(
            "JSON naming the authorization it rests on and a list of decisions, each "
            "{decision, a, b, evidence, canonical_name}: same_party folds one party into "
            "the other, branch_of keeps b as its own account under parent a, "
            "different_parties keeps them apart, not_a_party removes a project or site "
            "printed in a party column and retains it. A decided pair leaves pending "
            "adjudication with its score kept; a decision naming no party is reported."
        ),
    )
    ap.add_argument("--quiet", action="store_true")
    apply_shared_help(ap)
    args = ap.parse_args()

    if args.review_band >= args.threshold:
        sys.exit("--review-band must be below --threshold")
    authorization, decisions = (
        load_party_decisions(args.party_decisions) if args.party_decisions else ("", [])
    )

    with open(args.input) as fh:
        data = json.load(fh)
    records = load_records(data)

    mentions, refused = extract_parties(records, args.client_name)
    # Two kinds of refusal with nothing in common but the word: a reading that is
    # not a name is a mapping fault, and the client is a fact about the
    # engagement. Reported and retained apart, so neither hides inside the
    # other's count. The first list takes every refusal but the client's, so a
    # rule added later cannot fall out of both: the heading and address rules
    # did, and their refusals went unrecorded.
    rejected = [e for e in refused if e["reason"] != "the_client_is_not_a_counterparty"]
    client_mentions = [e for e in refused if e["reason"] == "the_client_is_not_a_counterparty"]
    if not mentions:
        summary = {
            "generated_at": datetime.now(UTC).isoformat(),
            "merge_threshold": args.threshold,
            "review_band": args.review_band,
            "party_mentions": 0,
            "resolved_parties": 0,
            "mentions_collapsed": 0,
            "parties_with_multiple_variants": 0,
            "merge_decisions": 0,
            "pending_adjudication": 1,
            "parties_needing_review": 0,
            "findings": [
                "No party mentions were extracted; retain this as review work and "
                "verify whether the source layout lacks party names or extraction missed them."
            ],
        }
        with open(args.out, "w") as fh:
            json.dump({"summary": summary, "parties": []}, fh, indent=2)
        with open(args.log, "w") as fh:
            json.dump(
                {
                    "summary": {"merges": 0, "pending": 1, "reversible": True},
                    "merges": [],
                    "pending_adjudication": [
                        {
                            "reason": "no_party_mentions",
                            "disposition": "client_review_required",
                        }
                    ],
                },
                fh,
                indent=2,
            )
        if not args.quiet:
            print("Mentions: 0  ->  Parties: 0")
            print("  - No party mentions were extracted; queued for client review.")
        return

    groups, evidence, ambiguous = cluster(mentions, args.threshold, args.review_band)
    parties = build_parties(mentions, groups, args.canonical_name)

    collapsed = len(mentions) - len(parties)
    multi = sum(1 for p in parties if p["variant_count"] > 1)

    parties, decided, undecidable, not_parties = apply_party_decisions(
        parties, decisions, authorization
    )
    # After the decisions, which it must not undo: a frequency rule is weaker
    # evidence than a person who looked at the pair.
    parties, absorptions = absorb_ocr_variants(
        parties, args.absorb_ocr_variants, kept_apart_by(decided)
    )
    ambiguous, settled = settle_pending(ambiguous, decisions)
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "merge_threshold": args.threshold,
        "review_band": args.review_band,
        "party_mentions": len(mentions),
        "resolved_parties": len(parties),
        "mentions_collapsed": collapsed,
        "parties_with_multiple_variants": multi,
        "merge_decisions": len(evidence),
        "pending_adjudication": len(ambiguous),
        "parties_needing_review": sum(1 for p in parties if p["needs_review"]),
        # Rule 9: a value refused as a name is a finding, not a silence. Zero
        # here means every mention was nameable; the list says which were not.
        "mentions_refused_not_a_name": len(rejected),
        # Rule 9 once more: zero here with `--client-name` set means the client
        # was never named in a role field; zero without it means the check
        # never ran, and the client is somewhere in the master.
        "mentions_refused_as_the_client": len(client_mentions),
        # Which rule refused each mention, so a count that moves says why.
        "mentions_refused_by_reason": {
            reason: sum(1 for entry in rejected if entry["reason"] == reason)
            for reason in sorted({entry["reason"] for entry in rejected})
        },
        "client_names_declared": list(args.client_name),
        # Rule 9 again: a zero here means the check ran and absorbed nothing.
        # `absorb_ocr_variants_ratio` of 0 means it never ran at all.
        "absorb_ocr_variants_ratio": args.absorb_ocr_variants,
        "ocr_variants_absorbed": len(absorptions),
        # Rule 9: a decision file that matched nothing is reported, and an
        # empty authorization with zero supplied means none was given.
        "party_decisions_authorization": authorization,
        "party_decisions_supplied": len(decisions),
        "party_decisions_applied": len(decided),
        "party_decisions_unmatched": len(undecidable),
        "parties_removed_as_not_a_party": len(not_parties),
        "pending_settled_by_decision": len(settled),
    }

    findings = []
    if collapsed:
        findings.append(
            f"{len(mentions)} mentions collapsed to {len(parties)} parties. Without "
            "this step those would have loaded as separate CRM accounts and every "
            "concentration and retention metric would be wrong."
        )
    if ambiguous:
        findings.append(
            f"{len(ambiguous)} pairs sit between the review band and the merge "
            "threshold. These are NOT merged -- present them for human "
            "adjudication rather than guessing. Merging them silently is how a "
            "genuinely separate subsidiary gets absorbed into its parent."
        )
    if summary["parties_needing_review"]:
        findings.append(
            f"{summary['parties_needing_review']} resolved parties have unusually "
            "many name variants or role assignments. Verify these are one entity "
            "and not an over-merge."
        )
    if absorptions:
        findings.append(
            f"{len(absorptions)} parties were absorbed into a name one character "
            "away and far more common. Each is retained with both mention counts "
            "and the ratio that justified it; reverse any that is a real separate "
            "entity rather than a misreading."
        )
    if rejected:
        fields = sorted({entry["field"] for entry in rejected})
        findings.append(
            f"{len(rejected)} mentions were refused as names -- a number with no letter, "
            "a heading, placeholder, total's label or adjustment, or a street address -- "
            f"from {', '.join(fields)}. "
            "Each would otherwise have resolved to a party of its own. A reading in the "
            "wrong field is a mapping finding, not a supplier."
        )
    if client_mentions:
        fields = sorted({entry["field"] for entry in client_mentions})
        findings.append(
            f"{len(client_mentions)} mentions named the client and were refused as "
            f"counterparties, from {', '.join(fields)}. The client is printed on nearly "
            "every page and is not a party these records transacted with; left in, it "
            "becomes the largest account in the master and its own customer and brand."
        )
    elif not args.client_name:
        findings.append(
            "No --client-name was declared, so the party whose records these are was "
            "not excluded. Verify the master does not contain the client as an account "
            "before any of it reaches a CRM."
        )
    else:
        # Rule 9: a check that matched nothing is reported, not passed over. A
        # client named on every page and refused zero times means the spelling
        # given here does not match the spelling the pages print.
        findings.append(
            f"{', '.join(args.client_name)} was declared as the client and matched no "
            "mention. Either the client is genuinely absent from these role fields, or "
            "the declared spelling does not match the one the pages print -- check the "
            "resolved master for it before trusting the zero."
        )
    if decided:
        findings.append(
            f"{len(decided)} party decisions were applied under {authorization}, settling "
            f"{len(settled)} pending pairs. Each is retained with its evidence; a merge "
            "made by a person is recorded apart from one made by a score."
        )
    if undecidable:
        findings.append(
            f"{len(undecidable)} party decisions were not applied: a name resolved to no "
            "party, a declared name is not a printed spelling, or clustering had already "
            "joined a pair the decision keeps apart. Each is retained with its reason."
        )
    summary["findings"] = findings or ["No party variants detected."]

    with open(args.out, "w") as fh:
        json.dump(
            {
                "summary": summary,
                "parties": parties,
                "refused_not_a_name": rejected,
                "refused_as_the_client": client_mentions,
                "ocr_variants_absorbed": absorptions,
                "party_decisions_applied": decided,
                "party_decisions_unmatched": undecidable,
                "removed_as_not_a_party": not_parties,
            },
            fh,
            indent=2,
        )
    with open(args.log, "w") as fh:
        json.dump(
            {
                "summary": {
                    "merges": len(evidence),
                    "pending": len(ambiguous),
                    "settled_by_decision": len(settled),
                    "reversible": True,
                },
                "merges": evidence,
                "pending_adjudication": ambiguous,
                "settled_by_decision": settled,
            },
            fh,
            indent=2,
        )

    if not args.quiet:
        print(f"Mentions: {len(mentions)}  ->  Parties: {len(parties)}")
        print(f"  merges recorded     : {len(evidence)}")
        print(f"  pending adjudication: {len(ambiguous)}")
        for f in summary["findings"]:
            print(f"  - {f}")
        print(f"\nWritten to {args.out}; merge log to {args.log}")


if __name__ == "__main__":
    main()
