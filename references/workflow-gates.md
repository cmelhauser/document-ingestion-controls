# Workflow Gates

Entry and exit criteria per phase. A gate is not a milestone — do not advance
past one whose exit condition is unmet. Say what is blocking instead.

Optional schema-guided image intake is a separate pre-pipeline source boundary,
not a new phase or an approval gate. Follow [Visual Intake](visual-ingestion.md)
to retain originals/proposals, export a new source package, pin its receipt hash,
and visually verify its declared complete PDF before normal profiling/intake.
Blocked preparation cannot advance, and all package exceptions enter exhaustive
final review. Host-model readings are not automatically extraction or consensus
inputs. The combined server still needs an approved retrieval snapshot and
separate deployment/source-flow authority; that snapshot's clearance does not
extend to newly submitted images.

## Contents

- [Phase 0 — Discovery](#phase-0--discovery)
- [Phase 0.5 — Scan Profiling](#phase-05--scan-profiling)
- [Phase 1 — Ingestion](#phase-1--ingestion)
- [Phase 2 — Classification](#phase-2--classification)
- [Phase 3 — Extraction](#phase-3--extraction)
- [Phase 3H — Handwriting](#phase-3h--handwriting)
- [Phase 3A — Attribution](#phase-3a--attribution)
- [Phase 4 — Completeness](#phase-4--completeness)
- [Phase 4Q — Sampling QA](#phase-4q--sampling-qa)
- [Phase 5 — Analytics](#phase-5--analytics)
- [Phase 6 — CRM Handoff](#phase-6--crm-handoff)
- [Phase 7 — Email Corpus](#phase-7--email-corpus)
- [Parallelization](#parallelization)

---

## Phase 0 — Discovery

**Entry:** client engagement begins.

Run as a working session. The output is one page, not a report.

### Capture

**Inventory.** File count, total page count, scan DPI as reported, colour depth
as reported (to be verified in 0.5, not trusted), single- or double-sided
capture, presence of any native text layer.

**Document taxonomy.** Which types actually exist. Do not assume the list —
confirm against the sample. Candidates: commercial invoice, packing list, bill of
lading, air waybill, carrier freight bill, customs entry (CBP 7501 or
equivalent), proof of delivery, credit memo, debit memo, remittance advice,
purchase order, statement of account.

**Systems of record.** Accounting platform, TMS, carrier portals. Establishes
which figures are authoritative when extraction disagrees with the books, and
supplies the Phase 4 reconciliation targets.

**Retention, privacy, residency.** What may leave the client network, permitted
cloud regions, statutory retention on originals.

**Decision list.** The questions the analytics must answer, written as literal
sentences. This defines "finished". Without it, analytics scope has no boundary
and the project has no completion criterion.

### Thresholds to agree now

| Parameter | Why it matters |
|---|---|
| Materiality threshold | Sets the MUS certainty stratum in 4Q |
| Tolerable error rate per field class | Sets sample sizes |
| GL variance tolerance | Sets the Phase 4 gate |
| Handwritten-amendment review threshold | Recommend $0 — i.e. all of them |

**Exit:** constraint document written; 50-page random calibration sample drawn.

---

## Phase 0.5 — Scan Profiling

**Entry:** read access to the corpus.

Runs against the **full corpus**, not the sample. About an hour of compute. Use
`scripts/scan_profile.py`.

### Why it runs before anything else

It resolves the colour-depth unknown empirically. Asking the client is
unreliable: scanning practice changes over five years and the corpus is normally
mixed. The result determines the handwriting strategy, the QA sample sizing, and
the rescan decision — i.e. most of the cost.

### Buckets

| Bucket | Definition | Routing |
|---|---|---|
| C | True colour, measurable chroma | Branch A |
| G | Grayscale 8-bit, no chroma | Branch B |
| B | Bilevel 1-bit (CCITT G4, JBIG2) | Branch B, degraded |

### Two traps

**False colour.** 24-bit RGB files containing only grey pixels — a scanner set to
colour photographing a photocopy. Bit depth alone misclassifies these; the
saturation histogram is the operative test. Route to Branch B despite the
apparent qualification for A.

**JBIG2.** Lossy JBIG2 substitutes visually similar glyph patches across a
document and can alter digits with no visible artefact. Mark all numerics from
those pages suspect regardless of OCR confidence and escalate the stratum in QA.

**Exit:** colour map produced; branch routing table set; rescan cost estimated if
originals exist; client has made the rescan decision against real numbers.

---

## Phase 1 — Ingestion

**Entry:** Phase 0 taxonomy agreed.

1. **Retain and burst.** `ingest_pages.py` creates a byte-verified source copy
   and exactly one immutable, zero-padded PDF master per page. Any deskew,
   despeckle, rotation, grayscale, or binarization is a retained sibling created
   after intake; no transformation writes back to source.
2. **Text-layer detection.** Run `pdftotext` per page and retain its text beside
   the PDF. The implemented optional OpenAI lane uses native text only on safely
   rule-classified pages; every other page remains PDF input. Other OCR vendors
   require their own approved adapter and routing test.
3. **Document reassembly proposals.** The hardest step and the most commonly
   underestimated. Implemented proposals use complete consecutive page-marker
   sequences; the explicit broad mode creates review-required same-type,
   unique-identifier candidates without claiming order. Evidence signals are:
   1. OCR-recovered "Page X of Y" markers
   2. Invoice / BOL / AWB number continuity across consecutive pages
   3. Header-page classifier (page 1 is structurally distinct from page 2)
   4. Blank-page or separator-sheet detection

   Pages that cannot be confidently assigned go to an unassigned pool and are
   worked manually. **Never force-fit.**
4. **Deduplication.** The implemented control records exact retained-artifact
   duplicates only. Perceptual image or normalized-text near-duplicate matching
   requires golden-set calibration before production use. Multi-year batches
   routinely contain repeated documents, so unresolved candidates remain review
   work rather than being silently removed.
5. **Provenance.** Every record carries source file, page range, engine, engine
   version, run timestamp, per-field confidence. No exceptions.

**Exit:** every page retained and assigned to a document proposal or to the
unassigned pool; duplicate/proposal registers produced; any requested image
normalization exists only as a sibling artifact.

---

## Phase 2 — Classification

**Entry:** documents reassembled.

Two levels: **document type**, then **party role** (which party is shipper,
consignee, biller, payer).

Start with deterministic rules on form numbers, carrier letterhead tokens, and
distinctive header strings. Measure against the calibration sample. Train a
statistical classifier only if rules plateau below the Phase 0 target.

Anything below the confidence threshold routes to human review. A misclassified
document produces a *structurally* wrong extraction, not a merely inaccurate one
— which is why low-confidence classifications are never silently accepted.

When the deterministic rules classify little or nothing, the escalation is
correct but it is not the end of the phase. Both extraction engines propose a
document type of their own, and `classification_consensus.py` accepts one only
where two genuinely independent model vendors already agreed. Run it after
extraction and feed the result to `template_observations.py --classifications`:
without it a corpus the rules could not classify groups into a single "template"
that is not a layout, and every lane reading a template fingerprint inherits
that.

**Exit:** every document typed and role-assigned, or explicitly escalated. An
escalated population left unworked is a stated limitation, not a pass: a family
that stays unresolved propagates into template observation, drift, and every
mapping proposal keyed to a template fingerprint.

---

## Phase 3 — Extraction

**Entry:** documents classified.

Field schemas per type: `references/extraction-schema.md`.

1. **Machine double-entry** — two independent engines, third as tiebreaker. Run
   `scripts/consensus.py`. Independence is resolved to the model vendor behind
   any router, so two lanes reaching the same weights by different transports are
   refused as one reading. Field grouping is complete-linkage over a deterministic
   order: the order of the handoff arguments cannot change the accepted value, and
   a reading that sits within tolerance of two groups withholds consensus.
2. **Arithmetic self-proof** — run `scripts/arithmetic_check.py`. Failure is an
   exception regardless of confidence. Each rollup check is bounded by the rows it
   actually sums, so a header identity keeps the base tolerance no matter how many
   lines the document has. A document whose header balances while a line could not
   be read reports `proved_with_unproved_lines`: it is not self-proved, stays in
   review, and leaves the sampling frame.
3. **Validation rules** — dates within the operating window; currency and UOM
   against a controlled vocabulary; invoice numbers against the detected
   per-vendor format; tax rates within plausible jurisdictional ranges.
4. **Entity resolution** — run `scripts/entity_resolve.py`. Present ambiguous
   clusters for adjudication rather than guessing. A blocking key above the
   pairwise-comparison cap is deferred to review as an explicit item rather than
   dropped, because the largest blocks are the party names most worth resolving.

### Exception policy

Sampling governs the clean population only. Every record failing consensus,
arithmetic, validation, or reconciliation is worked by a human, 100% of the time.
Typically 5–15% of documents — affordable, and where the error mass sits.

An approved mapping that is never applied changes nothing. `schema_discovery.py
discover` proposes, `registry-update` approves, and `apply_mappings.py` carries an
approved rule into the controlled field on the document record. Attribution,
completeness, and the inferred controls read those records rather than the
registry, so a run that stops at approval leaves every mapped value invisible to
them, and a source label the engines could not place stays outside the controlled
vocabulary no matter how many rules were approved.

**Exit:** every field either consensus-accepted with arithmetic proof, or in the
exception queue with a stated cause. `proved_with_unproved_lines` is an exception
state, not a proof. Every approved mapping rule has been applied, or the values it
governs are named as still unmapped.

---

## Phase 3H — Handwriting

**Entry:** branch routing known from 0.5.

See `references/handwriting.md` for the full subsystem. The gate condition:

When enabled, `google_handwriting_ocr.py` runs Google Cloud Vision handwriting
OCR on every retained page and binds returned words only to regions supplied by
a separate detector/extraction artifact. A page with no supplied region is
still recorded as OCR-complete; it is not declared handwriting-free. Retain the
rendered page, full raw response, normalized word geometry, page hash, and every
provider/unreadable exception. Treat the result as one HTR provider-group vote.
It cannot validate itself, and another Google model or role is not independent.

**Exit:** extraction-lane annotations are retained with page/region provenance as
non-blocking comments. If the optional HTR amendment lane is invoked, every
detected annotation is independently reconciled and either accepted at the
branch threshold or escalated; every amendment is stored beside — never over —
its printed original, and financial amendments remain client-review work.

---

## Phase 3A — Attribution

**Entry:** applicable financial records passed extraction/validation and the
client supplied the approved reference hierarchy or accepted that no reference
exists.

Run `scripts/attribution.py` against the client-approved ACK, job, project,
order, claim, case, or equivalent reference table. Preserve the selected match
rank and evidence. Every unresolved monetary amount is written to the
unattributable register with its reason and amount; no residual is plugged or
silently assigned. If commission or sales credit applies, run the separate
append-only allocation-policy control and use only explicit client-approved
rules for derived credit.

**Exit:** every in-scope dollar has an approved reference or an unattributable
register entry; coverage and unattributed amount are reported; all policy
proposals and conflicts are in final client review.

When client reference, ledger, or payment exports are unavailable, run
`inferred_controls.py` as a separate proposal lane. It may produce deterministic
document rollups and explicit-source payment/attribution candidates, but its
outputs are marked non-authoritative and cannot clear Phase 4. Preserve its
manifest and exceptions beside the run; replace or supersede them only with
client-approved authoritative exports.

Before post-review reasoning, run `client_review_context.py` for returned
workbook responses or `client_input_comments.py` for general JSON, CSV, TSV,
TXT, or Markdown comments. Both preserve every comment in a hash-bound
`client_review_context_v1`; `table_comprehension_corpus.py --client-context`
may include that context in all forward and refinement packets. If enabled,
`client_review_inference.py` may inspect that sample with bounded Google Gemini
Pro requests. Its context is explicitly excluded from independent consensus;
every candidate must cite a source document and remains client-review work.
Comments may guide terminology, priorities, and questions but are never source
evidence, authorization, or control clearance.

---

## Phase 4 — Completeness

**Entry:** extraction complete; GL export available.

All documents normalize to a ledger-shaped event stream:

```
event_id, event_type, party_id, amount, currency, effective_date, source_document_id
event_type ∈ {purchase, payment, credit, adjustment, accrual}
```

Non-financial documents (BOL, packing list) still register with a null amount so
the shipment lifecycle stays traceable.

### Matching

**Three-way match:** PO ↔ receipt (packing list or POD, including handwritten
received quantities) ↔ invoice.

**Open-item matching:** invoice ↔ payment ↔ remittance advice, supporting partial
payments and one-payment-to-many-invoices. Handwritten cheque numbers are
frequently the only available link — a first-class input, not an afterthought.

Every match carries a method: `exact_reference` | `amount_date_party` | `fuzzy` |
`manual` | `unmatched`.

### Controls

Run `scripts/completeness.py`. Sequence gaps, calendar gaps, GL variance by
period and vendor, aging closure.

**Report variance. Never plug it.** If the books say $2.4M and the documents
account for $2.1M, there is $300K of paperwork missing and that is the finding.

This applies to the GL input itself. A GL row that cannot be bucketed to an ISO
year-month, or whose amount cannot be parsed, is retained in
`rejected_gl_rows` — never dropped. Dropping it would shrink the authoritative
total and therefore the reported variance, plugging the gap in the one direction
this rule forbids. While any row is rejected the baseline is incomplete, no
period is declared within tolerance, and `gl_rows_rejected` blocks the gate.

`inference_coverage` enumerates every vendor series and vendor-month that was not
analyzed for gaps, with its reason. "No gaps found" does not cover that
population. It is reported as a stated limitation rather than a gate block,
because a corpus too small to infer from is a client scoping decision, not a
control failure.

**Exit:** Completeness Report has `gate_status: clear`: GL and payment/remittance
inputs were supplied, every GL row was ingested, GL comparison is measurable and
within tolerance, aging closure has no unresolved/partial/ambiguous application,
and no unexplained sequence, calendar, or date gaps remain. A client explanation is retained as an
amendment/input to a rerun; it does not rewrite a blocked report to clear.

---

## Phase 4Q — Sampling QA

**Entry:** Phase 4 gate passed.

See `references/qa-sampling.md`. Run `scripts/sampling.py`.

An optional `ai_simulated_client_review.py` draft-comment package may help an
operator prepare the final-review discussion, but it is not a Phase 4 or 4Q
control. Its comments explicitly state that they are simulated and not client
authorization; they cannot clear a review item, substitute for a client
decision, or permit the Phase 5/6 entry conditions.

**Exit:** a completed one-sided dollar-denominated upper overstatement bound is
within tolerance, or the affected population was escalated to 100% review and
reworked. Invalid, duplicate, off-sample, or confidence-factor-table-exhausting
findings remain blocked. Attribute-sample results and a client-authorized golden
set are reported separately; understatement and completeness are not inferred
from the MUS bound.

---

## Phase 5 — Analytics

**Entry:** 4 and 4Q gates passed. Do not build analytics on unreconciled data —
it produces confident wrong answers that are expensive to retract.

Scope strictly against the Phase 0 decision list. Definitions in
`references/analytics.md`.

**Exit:** every question on the decision list answered, each output carrying its
completeness figures.

---

## Phase 6 — CRM Handoff

**Entry:** final review is clear for the named artifact set; attribution,
completeness, and sampling gates have passed or carry explicit client acceptance;
the approved canonical export is built.

Deliverables:

1. Canonical dataset, materialized
2. ERD and full data dictionary
3. Load order specification with dependency graph
4. Integrity rules the CRM application must enforce
5. Completeness Report as at handoff
6. Accuracy statement with confidence bounds, by field and stratum
7. Open exception and unattributable registers, quantified
8. Explicit PostgreSQL deployment plan, ordered/checksummed idempotent load
   plan, and citation-preserving local retrieval store
9. Target-specific mapping, credential boundary, row/checksum reconciliation,
   and rollback procedure when a client CRM adapter is in scope

**Exit:** the vendor-neutral package is accepted and every row remains traceable
to source. A target system is not declared loaded until its separately authorized
adapter proves idempotency, integrity, rejected-row handling, and reconciliation.

---

## Phase 7 — Email Corpus

**Entry:** Phases 0–6 evidence identifiers are stable; the client separately
approves mailbox scope, access, privacy, retention, and coverage dates.

Email is a deferred corroboration lane governed by
`references/email-integration.md`. Attachments and messages are retained as new
sources with their own hashes and provenance. They may support an amendment,
attribution, missing-payment evidence, location evidence, or contacts, but never
replace the scanned source or close a discrepancy silently.

**Exit:** mailbox coverage is stated, every used message/attachment is retained
and linked, discrepancies are reviewed, and all email-derived metrics identify
their narrower time window.

---

## Parallelization

Everything from Phase 2 onward is branch-agnostic. The colour-depth unknown does
not block the critical path.

| Track | Dependency | May start |
|---|---|---|
| Scan profiling (0.5) | None | On corpus access |
| Ingestion (1) | Phase 0 taxonomy | After Phase 0 |
| Classification + extraction (2–3) | Calibration sample | After Phase 0 |
| Handwriting (3H) | Branch determination | Interface now; strategies after 0.5 |
| Data model + load spec (6) | Phase 0 taxonomy | After Phase 0 — branch-agnostic |
| Analytics definitions (5) | Decision list | After Phase 0 — branch-agnostic |

Write all handwriting detectors against one interface returning
`handwriting_regions[]`. Ink separation, stroke morphology, layout delta, and the
VLM pass are pluggable strategies behind it. Required regardless of the 0.5
outcome, because a mixed corpus exercises both branches in the same run.

## The gate is only as complete as its inputs

`final_review_queue.py` is handed artifacts; it does not discover them. A finding
in an artifact you did not pass is absent from the queue, absent from the
root-cause grouping that reads the queue, and absent from the client pack built
from it.

Running the gate while a review-bearing lane is unfinished therefore produces a
queue and a pack that are **provisional**, even though nothing in them is wrong.
Either finish the outstanding lanes first, or record explicitly that the queue was
built on a partial set and must be rebuilt.

`review_grouping.py` consumes the final queue, so it runs **after** the gate, not
before it. Ordering it earlier is a sequencing error that the argument list will
not catch, because the queue path is just a positional argument.
## A restarted run needs its generation declared

Lane coverage credits a lane from any retained artifact matching its markers or
filenames. On a run that was restarted, several attempts match, so a lane can be
credited by an attempt the operator discarded -- while the artifact the run
actually relies on may not exist at all. That is a false positive in the one
report whose job is to say what did not run.

`run_generation.py` records the decision as an artifact: the authoritative path
per lane, and the reason each other attempt was superseded.

```bash
python scripts/run_generation.py template RUN --out RUN/generation.json
# declare each candidate authoritative or superseded, with a reason
python scripts/run_generation.py verify RUN --manifest RUN/generation.json
python scripts/run_lane_coverage.py RUN --generation RUN/generation.json
```

`verify` reports the three failures a hand-written declaration is prone to: a
declared artifact that does not exist, an undeclared artifact that would still
credit a lane, and a path declared both authoritative and superseded. It exits
non-zero on the first and third.

A lane left undeclared is unrestricted, so the declaration is incremental:
declare the lanes that were restarted and the rest behave as before. Declaring a
generation approves nothing -- it records which reading the run stands behind,
not that the reading is correct.

This is also what makes a delivery defensible to someone other than the operator
who ran it. `client_delivery_package.py` takes explicitly named inputs and
refuses restricted material even when named, so a superseded artifact cannot
reach a client folder by accident; the declaration is what lets a reader
determine which reading the package states without reconstructing it from the
authorization amendments.
