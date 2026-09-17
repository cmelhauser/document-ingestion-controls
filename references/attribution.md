# Attribution

Tying every monetary amount to a configured business reference (for example an
ACK, job, project, order, claim, or case number), and every sale to an
originating location.

Where this is required, it is a **phase, not a field**. The most common way the
requirement fails is being treated as one more thing to extract.

## Contents

- [Why it needs its own phase](#why-it-needs-its-own-phase)
- [The reference table](#the-reference-table)
- [Resolution hierarchy](#resolution-hierarchy)
- [Inheritance rules](#inheritance-rules)
- [Legitimate unattributables](#legitimate-unattributables)
- [The coverage gate](#the-coverage-gate)
- [Sales origin](#sales-origin)
- [Interaction with handwriting](#interaction-with-handwriting)
- [Interaction with QA](#interaction-with-qa)

---

## Why it needs its own phase

Many documents in a business corpus never state the attribution key.
A carrier freight bill carries a PRO number and a BOL reference; it has no idea
what job it belongs to. A remittance advice references invoice numbers, not
projects. A customs entry references an entry number and an importer.

The key has to be **derived through the document graph** — freight bill → shipment
→ invoice → ACK — which means extraction and entity resolution have to finish
first. A field-extraction approach captures the easy half and silently drops the
rest, and "silently" is the operative word: nothing in the pipeline fails, the
totals still reconcile, and the client discovers the gap when they try to run
per-job profitability.

Hence: own phase, own hierarchy, own coverage metric, own gate.

---

## The reference table

Built **before any resolution is attempted**. Sources in descending authority:

1. **The client's order-entry or quoting system export.** A digital list of ACK
   numbers with dates, customers, and amounts. By a wide margin the best input —
   ask for it in Phase 0 and ask again if the first answer is vague. It converts
   attribution from inference into lookup and can reduce the cost of this phase
   by an order of magnitude.
2. **Acknowledgement documents in the corpus**, extracted in Phase 3. Where ACKs
   exist as a document class, classify and process them first.
3. **ACK and job numbers appearing on any other document type.** On a corpus
   with no acknowledgement class at all, this is the only in-corpus source, and
   it is real: the commission corpus carried 3,313 accepted `job_number` cells,
   1,658 `sales_order_number`, and 1,461 `project_number` without a single ACK
   document. Note what a table built this way can and cannot do. It learns the
   format, the date range, the customer, and the expected value, which is what
   ranks 3 and 6 need. It cannot validate a rank 1 key, because the key and the
   table came from the same reading — that check would be circular.

### What to ask the client for

One file, four columns, no formatting requirements:

| Column | Also accepted | Why |
|---|---|---|
| `ack_number` | `job_number`, `ack`, `job`, `number` | The key. Without it the row is registered as rejected, not dropped. |
| `date` | `ack_date`, `order_date` | Rank 6's ±45-day window. |
| `customer` | `party` | Rank 6's match, and the format-per-period check. |
| `amount` | `value`, `total` | Rank 6's ±2% tolerance. |

`po_number` (or `po`) and `selling_location` (`branch`, `office`) are optional
and each unlock a rank: `po_number` is the whole of rank 3, and
`selling_location` resolves the selling-location hierarchy that otherwise falls
through to unresolved. CSV or JSON; the loader normalizes column names and
registers every row it cannot key.

**Ask for the whole system, not the corpus's date range.** A key that resolves a
document is worth more than a tidy file, and a row that matches nothing costs
nothing.

**Before asking, check what the corpus already answers.** Run
`attribution.py` with no `--reference` first and read `unconsulted_key_fields`
and `attribution_by_method`. A low rate caused by a field name is not a missing
table, and the client cannot fix it.

The table gives every known key a validated format, a date range, a customer, and
an expected value. Every subsequent resolution is checked against it, which is
what makes a derived attribution auditable rather than a guess.

### Format learning

Learn the format per period, not once. Numbering conventions change — a prefix
gets added, a year segment appears, the width grows from four digits to five.
Format changes are the single most common cause of silent attribution failure,
because every key after the change fails validation and gets dumped into manual
assignment without anyone asking why the volume spiked.

Ask in Phase 0 whether the convention changed. Then check the answer against the
data anyway.

---

## Resolution hierarchy

Applied in order. First method that resolves wins. **The method is recorded on
the record** — the mix of methods across the corpus is itself a quality metric,
and an attribution that is 80% rank 6 is a different artefact from one that is
80% rank 1.

| Rank | Method | Confidence | Notes |
|---|---|---|---|
| 1 | Explicit printed ACK/job number on the document, or on every line that prints one | Highest | Validated against reference table |
| 2 | Explicit handwritten ACK/job number | High | Requires 3-of-3 recognition |
| 3 | PO number cross-reference | High | PO → ACK via reference table |
| 4 | Invoice-to-shipment inheritance | Medium | Shipment attributed; invoice inherits |
| 5 | Payment-to-invoice inheritance | Medium | Invoice attributed; payment inherits |
| 6 | Customer + date window + amount match | Medium-low | **Unique match only** |
| 7 | Email evidence | Low | Phase 7. Corroborating, never overriding |
| 8 | Manual assignment | Recorded | Assigner and date logged |
| — | **Unattributable** | — | Explicit disposition, reason code, dollar value |

### A key every line prints

`get` reads document-level fields only, and on purpose: a key on the lines is a
fact about a line, and a document whose lines name several keys has an
allocation question rather than a missing attribution. A document whose lines
all name **one** key, and whose header names none, has no such question -- the
key is printed, once per line. `resolve_from_lines` credits it at rank 1 under
its own method, `explicit_printed_on_lines`, after the header and before a PO
cross-reference, and holds the key to the same format check a header key gets.
On the commission run 56 documents that print their sales-order or project
number only on their lines resolved this way, having been registered
unresolved.

### Rank 1 reads the field the extractor emits

The header names rank 1 tries are `attribution.EXPLICIT_KEY_FIELDS`, and every
one of them except the write-back alias is an `extraction_schema.HEADER_FIELDS`
name. This is not a formality. The lane shipped asking for `ack_number`, which
the schema has never had — it calls the field `acknowledgement_number` — so on a
716-document corpus every page that printed its acknowledgement number was read
correctly, corroborated, accepted, and then registered as unresolved. The
attribution rate said 26 documents; the true figure was 137.

A blind rank and an absent key look identical in an attribution rate, so the
artifact separates them: `explicit_key_fields` records what rank 1 asked for,
and `unconsulted_key_fields` counts every populated key-shaped header field no
rank reads. Use `--key-field` when this engagement's key has another name; add
it to `EXPLICIT_KEY_FIELDS` when the schema gains a key field.

### Selling location reads the schema's own field names

The hierarchy in `resolve_selling_location` shipped asking for `branch`,
`office`, `selling_location`, `letterhead_city` and `salesperson`. Not one of
those is an `extraction_schema.HEADER_FIELDS` name, so no extractor can emit
any of them, and the control reported selling location unresolved for all 716
documents of the commission corpus while saying nothing about why.

The schema's names are `warehouse_location` for an explicit location and
`sales_representative_name` for the representative — which that corpus carried
on 112 documents. Reading it moves those 112 from a silent `unresolved` to
`salesperson_needs_territory_table`, which is a request the client can act on.
The location still does not resolve, because a territory table is genuinely
required; what changed is that the artifact now says so.

The pre-schema names are kept, because a client reference export or an operator
amendment can legitimately carry them. They are registered in
`extraction_schema.NON_SCHEMA_RECORD_ALIASES` with the reason, and the test
suite requires every field name any control looks up to be either a schema field
or a registered alias — a lookup that can never match is otherwise
indistinguishable from a corpus that had nothing to find.

### A key on the line items is not a document attribution

Where a document states no header key but its line items each name a job, the
document is not missing an attribution — it has several. On the commission
corpus, 359 documents carried keys only on their lines, and 316 of those named
between two and a hundred distinct jobs. Those are allocation questions
(`allocation_policy.py`), and collapsing them to one document-level key would
invent an attribution the page does not make. Only a document whose lines are
unanimous is a document-level attribution, and it is still rank 8 until someone
decides that unanimity counts.

### The crediting rule

When the client gives a crediting rule for those statements,
`attribution.py --credit-by-line AUTHORIZATION` applies it. Each line carrying
money is credited to the key that line prints. The document is registered
`credited_by_line` -- an answer, not a failure -- with each line's key, key field
and amount, and the authorization.

A line carries money when the first line value field it carries
(`commission_amount`, `line_total`, `amount`, `extended_amount`) is not zero,
which is the reading `document_value` uses. A line printing two kinds of key is
credited to the first in rank 1's order.

The rule chooses nothing and estimates nothing. A document with a money line
that prints no key, or a key the corpus formats refuse, stays `unresolved`, and
a credited document still carries no document-level key.

### Rank 6 discipline

Customer + date + amount matching resolves only on a **unique** match. If two
open jobs for the same customer in the same week share an amount, that is not a
match — it is two candidates, and it goes to rank 8. Accepting the first
candidate produces a plausible, wrong attribution that nothing downstream will
catch.

---

## Inheritance rules

Ranks 4 and 5 are what make the requirement achievable, and they are worth
understanding as a mechanism rather than a fallback.

A carrier freight bill states no job number. But it references a BOL. That BOL
belongs to a shipment. That shipment was invoiced. That invoice carried a printed
ACK number. The freight bill therefore belongs to that ACK, and the chain is
recorded:

```
freight_bill FB-88213
  → shipment BOL-4471          (matched on BOL reference, exact)
  → invoice INV-2231           (matched on shipment key)
  → ACK-2022-0847              (rank 1, explicit printed)
attribution_method: inherited_via_shipment
attribution_confidence: 0.82
evidence_chain: [FB-88213, BOL-4471, INV-2231]
```

Store the chain, not just the answer. When an attribution is later disputed —
and on a five-year backfile some will be — the chain is what makes the
disagreement resolvable in minutes rather than by re-deriving from scratch.

**Inheritance is one-directional and depth-limited.** Do not inherit through more
than two hops without flagging it, and never inherit from a record whose own
attribution came from rank 6 or below. Compounding low-confidence inferences
produces attributions that look authoritative and are not.

---

## Legitimate unattributables

Some dollars genuinely have no job number. Agree the reason codes in Phase 0 so
they are **dispositions rather than failures**, and so the coverage report
distinguishes "we could not find it" from "there isn't one."

Typical codes:

| Code | Meaning |
|---|---|
| `internal_transfer` | Intercompany movement, no external job |
| `rebate` | Volume rebate or credit not tied to a specific job |
| `rebilled_freight` | Pass-through freight billed to a customer |
| `pre_system` | Predates the ACK numbering scheme |
| `closed_job_credit` | Credit issued against a job already closed and archived |
| `overhead` | General operating cost, deliberately unallocated |
| `payment_record` | A payment or remittance: money received against commission already reported, naming no job to credit |
| `blank_page` | The page prints nothing to attribute |
| `unresolved` | Genuinely could not determine — the honest residual |

Assign `payment_record` and `blank_page` only where the page image shows it. A
client's typing is a proposal: on the commission run the client typed 92
unattributed documents payments and 29 blank, and the images bore out 45 and 5
-- the rest were commission reports, handwriting, or faded pages still printing
tables.

`unresolved` is the only one that represents a failure. The rest are answers.
Reporting them separately is what keeps the coverage number meaningful.

---

## The coverage gate

| Metric | Requirement |
|---|---|
| Dollars attributed or explicitly registered | 100% |
| Attribution by method | Reported — rank mix is a quality indicator |
| `unresolved` dollars | Enumerated with document ids and values |
| Selling location resolved | Reported, unresolved quantified |

**"97% attributed" is not a passing result** unless the remaining 3% is
enumerated with reasons and a dollar figure. The gate is not a high rate. It is
that no dollar is unaccounted for silently — because the first question anyone
asks about a 97% figure is what the other 3% is, and "we don't know" is the wrong
answer to have prepared.

---

## Sales origin

"What city did the sale come from" has four defensible meanings that produce
different numbers. Settle it in Phase 0; discovering the disagreement after the
analysis is presented is expensive and damages confidence in everything else.

| Interpretation | Meaning | Typical source |
|---|---|---|
| **Selling location** | Which office or branch booked the sale | Letterhead, issuing office, ACK prefix, salesperson |
| Ship-from origin | Where goods physically departed | BOL origin, warehouse address |
| Customer location | Where the customer is based | Bill-to address |
| Delivery destination | Where goods went | Ship-to address, POD |

Most businesses asking this mean **selling location** — it is a territory or
branch performance question. It is also the hardest of the four to source,
because it is frequently not on the document at all.

### Resolution hierarchy for selling location

1. Explicit branch or office field on the document
2. Letterhead or footer address — recoverable by template matching against a
   small library of the client's letterhead variants, which works even on
   black-and-white scans
3. **ACK number prefix**, where the numbering scheme encodes location. Common,
   and cheap when true — check for it in Phase 0
4. Salesperson or rep name, mapped to a territory table
5. Customer's assigned territory from the reference table
6. Email evidence (Phase 7)
7. Unresolved — explicit, with dollar value

**Capture all four interpretations** where the data supports it, in separate
fields. It costs little and means the client can change their mind about which
one they meant without reprocessing five years of documents. They frequently do.

### Reporting

Every geographic figure carries its resolution-method mix. A city total that is
60% inferred from ACK prefix and 40% explicitly stated is a materially different
quality of number from one that is fully explicit, and a reader making a
territory decision needs to know which they are looking at.

---

## Interaction with handwriting

If the client has descoped handwriting, check this specifically before agreeing:
**are ACK or job numbers routinely handwritten onto documents?**

On shipping paperwork they very often are — added at order entry or at receiving,
after the document was printed. If handwriting is discarded wholesale and the
attribution keys are among the discarded content, the attribution requirement
cannot be met at all.

The two instructions are in direct tension and attribution is the harder
requirement. The resolution is in `references/handwriting.md`: descope
recognition generally, retain it for regions that look like attribution keys.
Raise this as a scoping question rather than discovering it in Phase 3A.

---

## Interaction with QA

**Oversample ACK and job number fields 5×** in the attribute sample.

The reason is specific: a wrong ACK number does not produce a wrong total. It
produces a *correct total filed against the wrong job*. Every arithmetic check in
the pipeline passes. Every reconciliation to the GL passes. The error is
invisible to the entire validation architecture, and sampling is the only
mechanism that catches it.

This is worth explaining to a client who questions the oversampling rate, because
it is not intuitive — the fields that need the most checking here are not the
ones carrying the money.
