---
title: "Business Document Ingestion, Extraction & Analytics Program"
subtitle: "Self-Contained Operating Plan and Vendor-Neutral Handoff Specification — Release 1.0"
author: "Christopher Melhauser and theonlymuffinbot"
license: "The Unlicense"
credit: "Christopher Melhauser; theonlymuffinbot"
date: "September 3, 2026"
version: "1.0.0"
---

# 0. Document Purpose and Scope

## 0.0.1 Authorship and AI collaboration context

Courtesy credit for this manuscript is given to Christopher Melhauser and
`theonlymuffinbot`. In this context, `theonlymuffinbot` describes collaborative
AI tooling. This describes the tooling collaboration and does not assign legal
authorship or ownership to an AI system; legal attribution remains with human
contributors.

## 0.1 What this document is

This is a self-contained operating plan for converting retained business documents —
invoices, receipts, purchase orders, memos, remittances, statements, and
shipping records where present — into a validated, reconciled financial and
operational dataset suitable for an agreed business-system handoff.

ACKs, jobs, commission statements, shipment records, black-and-white scans,
sales-origin analysis, and email are illustrative configuration examples only.
They are not assumed client facts, required fields, or delivery commitments.
Replace them with the engagement's approved document families, relationship
keys, controls, and business questions during Phase 0.

## 0.2 Delivery package

This plan is self-contained for client delivery: it defines the operating gates, evidence-preservation rules, quality controls, data model, output contract, and remaining integration limits. The current delivery package preserves source PDFs and run artifacts and supplies the final review package as canonical JSON plus CSV, formatted XLSX workbook, and HTML reviewer views. `references/artifact-contracts.md` supplies the definitive review interface; `references/extraction-schema.md` and `references/derived-field-schema.md` supply the field dictionaries. The `examples/` folder is explicitly fictional field-shape material, not an operational review package.

## 0.2.1 Delivery workflow and optional AI integration

**Source PDFs, references, and operating constraints** → **preserve/profile** → **immutable
page intake** → **independent proposals and consensus** → **arithmetic,
validation, attribution, completeness, and sampling** → **exhaustive final
client review** → **analytics and agreed load handoff**.

Optional OpenAI, Gemini-on-Vertex, OpenRouter, Document AI, HTR,
semantic-discovery, address, and LLM-adjudication lanes
feed retained evidence or proposals into this same path. None may bypass the
deterministic controls, close an exception, or supply client approval.

The selected run-level LLM adapter does not control the final decision path. It runs only after immutable intake, retains raw provider evidence and a page hash, and contributes one structured proposal to consensus. Provider uncertainty, type proposals, handwriting flags, and failures enter the review gate; no adapter can approve a record or close an exception. Primary and secondary consensus lanes are invoked separately and count as independent only when their providers are genuinely independent.

The separately disabled LLM adjudication lane is intentionally narrower. It may query only explicit nonfinancial, printed candidates after clear deterministic validation and exact independent-extractor agreement. `LLM_ADJUDICATION_MIN_CONFIDENCE` defaults to `0.99` and can be adjusted in `.env` only through `1.0`; it cannot override financial, address, handwriting, reassembly, or disagreement controls. Every result is an audit-marked amendment proposal or exception and always enters the final client-review gate.

## 0.2.2 Semantic schema discovery, entity history, and future CRM/RAG delivery

New source layouts are governed by a versioned canonical vocabulary and a source-alias registry. `scripts/schema_discovery.py` produces strict LLM suggestions for canonical field, semantic type, alternatives, confidence, rationale, and supplied evidence only. It cannot create a canonical field, alter a party or entity master, or approve a mapping. Each new alias/template requires explicit client approval; the registry update creates a new effective-dated snapshot. Familiar sources presenting changed headers, dealer or brand names, city clues, or relationship evidence return to review. Names, addresses, and relationships are temporal evidence, not overwritten labels.

Use address evidence from the source page and the separate address-validation stage first. Google Places (New), when enabled, is only a bounded candidate-discovery source for observed dealer/brand names and city hints. It never establishes entity identity, selects a city/address, or bypasses review. The approved registry and validated provenance-linked records constitute a vendor-neutral CRM staging interface. The implemented local retrieval database indexes approved canonical facts and preserves document/page/source-hash and mapping-version citations. A specific CRM connector, remote vector service, or ChatGPT connection remains deferred until the client selects its security, retention, and access model.

## 0.2.3 Flexible allocation policy

Commission and sales-credit logic is a versioned policy layer, not a globally hard-coded percentage or a column rename. `allocation_policy.py` calculates effective commission rate from retained source amounts and separately records stated rates, shares, and formula proof. A client-approved rule may use any approved source dimension---brand, dealer, product, client, location, or a later schema-approved layer---and an effective-date window. The most-specific active rule applies; missing/ambiguous rules, unknown layers, invalid dates, and formula mismatches remain review work.

For a new report layout, strict LLM output proposes allocation roles and a policy card only. The client edits only the generated decision return file to approve/defer, specify dimensions and dates, and make an explicit approved override. The next registry snapshot preserves the prior policy history. The LLM cannot create sales credit or alter a historical result. See `references/allocation-policy.md`.

The implemented control layer is ready to operate when client records, reference exports, and approved provider configurations are received. It does not claim that OCR/HTR vendor calls, handwriting-region detection, broad automatic classification, or CRM loading have been deployed; those require client-approved integrations and a representative golden-set evaluation.

## 0.3 Capability status and future steps

This plan retains the 1.0.0 control baseline and also describes implemented
development additions in this checkout. See `HANDOFF.md` for merge/deployment
state; an **Unreleased** capability is not a claim of production acceptance.

| Capability | Status | Engagement next step |
|---|---|---|
| Immutable intake, proposal-only extraction, consensus, arithmetic, validation, review packages, and JSON graph overlays | Implemented control layer | Select the applicable source records and run configuration. |
| Configured-primary/configured-buddy relationship inference and cross-packet verification | Implemented proposal workflow | Set an approved provider budget; review retained proposals and exceptions. |
| Reference schema, attribution keys, analytic dimensions, and allocation policy | Configurable implemented controls | Approve the relationship key, field mappings, and policy for the engagement. |
| Approved-fact MCP/API, account cards, governed queries, sales slices, seven standard reports and remote CSV/XLSX downloads | Implemented shared service and transports | Build an approved non-empty snapshot; accept the selected TLS/OAuth/client deployment. |
| Schema-guided PNG/JPEG intake and local source-only export | Implemented optional proposal pilot | Authorize image/model data flow; verify real transfer, receipts, source package and normal pipeline re-entry. |
| Authoritative GL, payment, reference, or authorization evidence | External operating control | Supply it when available; otherwise retain an unavailable-control exception. |
| Additional OCR/HTR vendors, calibrated detector/template deployment, email ingestion, target CRM connector, hosted retrieval, and production accuracy claims | Future integration work | Define vendor, security, retention, evaluation, and acceptance criteria before implementation. |

Nothing in this template turns an inference or a missing client control into an
approved fact. Future steps remain explicitly future until their integration,
test evidence, and client acceptance are recorded.

### Optional visual intake and remaining adapter work

The current MCP servers can add eleven operations for schema discovery, session
discovery, original-page upload/read, proposal submission, reviewer grants,
review summaries, and status/proposal retrieval. The remote OAuth service
exposes equivalent JSON API routes. Intake is disabled unless `--ingestion-dir`
is supplied. Local discovery grows from 12 to 23 tools; remote discovery grows
from 14 to at most 25, filtered by scopes. The TLS/bearer GET API and default
local launcher remain unchanged. The combined servers still require an approved
snapshot and separate activation authorization.

The connected vision-capable assistant reads retained images; the server makes
no additional model call. Originals, source references, unknown fields,
uncertainties and all rejected/corrected proposals remain append-only in a
separate journal. A valid submission is pending review, not an approved record.
No native capture client or chat-attachment relay is included. Client upload
and vision support must be demonstrated rather than inferred from MCP access.

`visual_ingestion_export.py` snapshots an existing session read-only into a new
source package, preserves every original attempt/proposal/rejection, and only
for complete usable pages prepares a session-named image PDF with a provenance
map. Verify against the separately retained receipt, inspect the PDF, and use
normal profiling/intake. Missing or failed pages block progression. Every
source-package exception belongs in final review. The package is neither a
consensus handoff nor an approved CRM import.

The [visual contract](../references/visual-ingestion.md) and
[technical manual](TECHNICAL_DOCUMENTATION.md) define flags, sizes and recovery.
Governed create/update, shared review, saved custom reports, safe general joins
and broader analytics remain in the [vendor-neutral roadmap](../references/business-data-platform-roadmap.md).
They do not depend on Salesforce. The existing no-send common-object package
already prepares twelve CRM import files; live target adapters still require
tenant-specific mapping, sandbox evidence and separate authorization.

## 0.4 What is in scope

Ingestion and normalization; document reassembly, classification, and field
extraction; handwriting capture and linkage; **attribution of every monetary
amount to a configured business reference**; origin resolution; validation, entity resolution, and
reconciliation; completeness controls covering all purchases and payments;
analytics; canonical data model and CRM load sequence. Email integration is in
scope as a deferred option.

## 0.5 What is out of scope

Construction of the CRM application itself. This program terminates at a
documented, validated, load-ready dataset plus the load order and integrity rules
the CRM must honour.

## 0.6 Stated outcome

1. A **complete, auditable financial record** of every purchase and payment
   evidenced in the corpus, each figure traceable to a specific source page.
2. **Every monetary amount attributed** to a configured business reference, or explicitly
   registered as unattributable with a stated reason and a dollar value.
3. A **quantified statement of accuracy and completeness** — a measured error
   rate with confidence bounds and an explicit enumeration of what is missing.
4. A **canonical dataset**, deduplicated and entity-resolved, ready for CRM load
   in a defined order.
5. A **standing analytics layer** answering the client's decision questions,
   including revenue by originating city and by job.
6. A **reusable pipeline** and a permanent regression test set.

## 0.7 Governing principles

**Nothing is silently discarded.** Every input page produces an output record,
even if that record is "unreadable, escalated."

**Original values are never overwritten.** Corrections are stored as amendments
alongside originals, with source attribution and effective time.

**Documents prove themselves arithmetically.** A confidence score is not
evidence. Internal arithmetic consistency is. This principle carries
disproportionate weight in every financial-document engagement — see §1.2.

**Completeness is reported on the face of every analysis.** Never plug a variance
to make totals agree.

**Every dollar carries an attribution or an explicit reason it has none.**
Silently unattributed revenue defeats the purpose of the exercise.

---

# 1. Engagement Configuration and Open Variables

## 1.1 Example: black-and-white scan corpus

The following is an illustrative configuration for a black-and-white corpus.
For each client, Phase 0.5 measures the actual colour depth, bit depth, and
scan quality; it does not assume this example or choose a processing path before
that evidence exists.

### Direct consequences

| Capability | Status | Consequence |
|---|---|---|
| Ink/colour separation for handwriting detection | **Unavailable** | Pen and print are the same colour. Annotation detection falls back to stroke shape and layout deviation. |
| Stamp and seal detection | **Unavailable by colour** | "PAID" stamps, approval stamps, and received-date stamps no longer separate from print. Planned template matching requires client calibration (§3.5). |
| Highlighter and markup detection | **Lost** | Highlighted regions render as grey wash or, on bilevel scans, erase the underlying text entirely. Affected regions are flagged as damaged, not silently read. |
| Handwriting recognition accuracy | **Degraded** | Lower stroke fidelity, particularly on 1-bit scans. |
| Printed-text OCR | **Largely unaffected** | Print OCR is designed for bilevel input. |
| Classification, matching, reconciliation, attribution | **Unaffected** | Structural, not tonal. |

### What this changes in the plan

For a black-and-white corpus, use these compensating controls:

1. **Use at least two genuinely independent extraction providers; target three
   where the client risk assessment requires triple entry.** Release 1.0
   supplies the consensus contract and optional OpenAI, Gemini-on-Vertex,
   OpenRouter, and Document AI evidence adapters, but the client must select and
   accept the independent production providers. Single-engine error rates rise
   materially on bilevel input.
2. **Multi-binarization ensemble** on any 8-bit grayscale pages (§3.4). Each
   variant is retained comparison evidence; readings from one vendor remain
   correlated and are not separate independent engines.
3. **Arithmetic self-proof is promoted from a validation rule to the primary
   error-detection mechanism** (§1.2).
4. **Handwriting is assumed present until proven absent.** Proposal/detector
   output sets processing priority, never eligibility; local detection still
   requires client calibration.
5. **QA oversampling on handwriting-bearing strata rises to 8–10×**.

### The one remaining question about the scans

The corpus is black-and-white, but **8-bit grayscale and 1-bit bilevel are very
different inputs** and the difference is not visible to the eye at normal zoom.
Grayscale retains stroke intensity information that stroke-morphology detection
depends on; bilevel has discarded it irreversibly.

Phase 0.5 measures the split. If a material share is bilevel, and grayscale or
colour masters still exist anywhere — original paper, an earlier scan generation,
a scanner's retained output folder — re-deriving those pages from the better
source is by far the cheapest accuracy improvement available in this project.

## 1.2 Why arithmetic proof now carries the program

With ink separation gone, some handwritten annotations **will** go undetected.
That is a certainty, not a risk to be mitigated to zero.

The recovery mechanism is arithmetic. A handwritten quantity correction or price
override that the detector missed will, in most cases, break the document's
internal arithmetic — the printed lines will no longer sum to the printed total
once the real quantity is what governs. The failure surfaces the annotation that
detection missed.

This is why the self-proof rule remains non-negotiable whenever handwritten
financial changes may be present. **Arithmetic proof is the backstop when
recognition is unavailable, deferred, or intentionally out of scope.**

## 1.3 Example configuration parameters

| Parameter | Value |
|---|---|
| Scan colour depth | Example: black-and-white source corpus; Phase 0.5 records the measured grayscale/bilevel split per page |
| QA model | Sampling-only on the clean population; 100% review of all exceptions |
| Efficiency posture | Robustness prioritised over cost |
| Financial coverage | All purchases and payments documented and reconciled |
| Handwriting | Example: present; recognition deferred; capture-and-link retained (§7) |
| Dollar attribution | Example: every dollar ties to an approved relationship key such as ACK, job, project, order, or PO |
| Sales origin | Example analytic dimension; replace with approved reporting dimensions |
| Email corpus | Optional future phase if separately authorized |
| Corpus period | Engagement-defined |
| Corpus ordering | Unordered; pages not grouped into documents |

## 1.4 Parameters requiring client input

- Total page count across all scan files
- **ACK and job-number format conventions**, including any changes over the
  five-year period
- **Definition of "sales origin city"** — see §8.4, which explains why this must
  be settled explicitly
- Materiality threshold for monetary-unit sampling
- Tolerable error rate per field class
- GL variance tolerance
- Availability of grayscale or colour masters for any bilevel pages
- Email export format, date range, and mailbox scope (if Phase 7 proceeds)
- Data residency and PII handling constraints

---

# 2. Phase 0 — Discovery

Working session with the client. Output is a one-page constraint document.

## 2.1 Inventory

File count, total page count, scan DPI, single- or double-sided capture, presence
of any native text layer.

## 2.2 Document taxonomy

Confirm which types exist. Do not assume the list. Candidates: commercial
invoice, packing list, bill of lading, air waybill, carrier freight bill, customs
entry, proof of delivery, credit memo, debit memo, remittance advice, purchase
order, **acknowledgement (ACK)**, statement of account.

The acknowledgement document type is load-bearing when it is a configured relationship key. If ACKs
exist as their own document class in the corpus, they are the authoritative
source for the attribution key and are processed first.

## 2.3 Attribution conventions

An essential discovery item when acknowledgement or order references are in scope.

- What does an ACK number look like? Fixed prefix, length, check digit?
- What does a job or project number look like?
- Did either convention change during the measured period? Format changes are
  the most common cause of silent attribution failure.
- Which is authoritative when a document carries both?
- Are there dollars that legitimately have neither — internal transfers,
  rebates, rebilled freight, credits? These need a defined disposition rather
  than showing up as failures.
- Where does the number physically appear on each document type? Printed field,
  stamped, or handwritten?

The last question determines how much of §7 is required. If ACK numbers are
routinely handwritten, handwriting capture is not optional regardless of the
general descoping instruction.

## 2.4 Systems of record

Accounting platform, TMS, order-entry or quoting system, carrier portals.

**An order-entry or quoting system is material**: when an authoritative digital
list of relationship keys, dates, parties, and amounts exists, it becomes the
attribution reference table and makes §8 more reliable than deriving everything
from documents alone.

## 2.5 Retention, privacy, residency

What may leave the client network, permitted cloud regions, statutory retention
on originals. Extend to mailbox scope if Phase 7 is anticipated.

## 2.6 Decision list

The questions the analytics must answer, as literal sentences. Defines
"finished." Include the city-of-sale question explicitly, because its phrasing
determines the §8.4 resolution hierarchy.

## 2.7 Deliverables

Constraint document; 50-page random calibration sample; ACK/job format
specification.

---

# 3. Phase 0.5 — Scan Profiling

Runs against the **full corpus**, not the sample. About an hour of compute.

The purpose is to measure the source evidence before selecting a processing path. The
profiling now characterises **how degraded the black-and-white corpus is** and
routes pages to the appropriate enhancement path.

## 3.1 Probe

Per page: bit depth, effective DPI, compression codec, ink coverage ratio,
estimated blur, contrast range, skew residual, speckle density, scanner make and
model from XMP or producer string.

## 3.2 Primary output: the grayscale/bilevel split

| Bucket | Definition | Handling |
|---|---|---|
| **G** | 8-bit grayscale | Full stroke-morphology detection available; multi-binarization ensemble applies |
| **B1** | 1-bit bilevel, lossless (CCITT G4) | Stroke intensity discarded; morphology degraded but workable |
| **B2** | 1-bit bilevel, lossy (JBIG2) | As B1, plus digit-substitution hazard (§3.3) |

Bucket G is materially better input than B1 or B2 and should be preserved as
grayscale through the pipeline rather than binarized early. Binarizing a
grayscale page and discarding the original throws away the information that
stroke detection needs, and it is a one-way operation.

## 3.3 JBIG2 hazard

JBIG2 lossy compression substitutes visually similar glyph patches across a
document and can alter digits with no visible artefact. This risk is elevated in
an engagement when the corpus includes bilevel-eligible pages.

All numerics from JBIG2 pages are marked suspect regardless of OCR confidence.
Those strata are escalated in QA sampling. If a material share of the corpus is
JBIG2 and any better-quality source exists, re-deriving those pages is the single
highest-return remediation available.

## 3.4 Image quality scoring and planned remediation routing

The implemented profiler records the measurable PDF/image characteristics and
scan branch. The following calibrated quality score and remediation router is a
target integration that requires the client golden set:

| Score | Path |
|---|---|
| Good | Standard pipeline |
| Marginal | Enhancement pass — deskew, despeckle, contrast normalization, and for grayscale, multi-binarization ensemble |
| Poor | Enhancement plus mandatory QA sampling inclusion regardless of stratum quotas |

The implemented **multi-binarization ensemble** (grayscale pages only) produces three binarized
variants using global thresholding, adaptive local thresholding, and a
stroke-preserving method. OCR each variant independently. Each variant becomes an
additional consensus voter. On degraded scans this recovers characters that any
single threshold loses, and it costs only compute.

**Upscaling** for pages below 300 DPI remains planned before any calibrated handwriting-region processing.
Sub-300 DPI is where handwritten digit recognition fails hardest.

## 3.5 Stamp recovery

Colour separation would have found stamps trivially. The planned compensating
control is template matching against a client-approved library of recurring
"PAID", received-date, and approval stamps. It is not implemented in release
1.0 and must be calibrated before it can affect payment status.

This matters specifically for payment matching. A "PAID" stamp with a
handwritten date is frequently the only evidence that an invoice was settled, and
losing it inflates the apparent open-receivables balance in §9.

## 3.6 Decision gate

Outputs: bucket counts, quality distribution, JBIG2 page count, DPI distribution,
and a costed remediation recommendation if better-quality masters exist.

---

# 4. Phase 1 — Ingestion

## 4.1 Retain, burst, and prepare variants

The implemented intake retains a byte-verified source PDF and creates one
immutable PDF master per page. The implemented preparation ensemble then creates
a grayscale master, contrast-enhanced sibling, and three complementary
binarized siblings without changing any PDF master. Scan-specific upscaling,
deskew, despeckle, and auto-rotation remain calibrated integration work and must
create retained variants rather than replace evidence.

## 4.2 Text-layer detection

`pdftotext` is retained per page. In the implemented optional OpenAI lane,
rule-classified pages may use native text and all other pages use their one-page
PDF. Routing for any other OCR provider requires its own approved adapter and
golden-set test.

## 4.3 Document reassembly

The implemented control proposes a group only from a complete consecutive
`PAGE X OF Y` sequence with an agreeing type and identifier. Its explicit broad
mode proposes, but does not order, same-type/unique-ID candidates. The target
signal hierarchy is:

1. OCR-recovered "Page X of Y" markers
2. Invoice, BOL, AWB, **ACK, or job number** continuity across consecutive pages
3. Header-page classifier
4. Blank-page or separator-sheet detection

Signal 2 is stronger when attribution keys are extracted
as first-class fields, ACK and job numbers become reassembly evidence as well as
attribution evidence.

Pages that cannot be confidently assigned go to an unassigned pool and are worked
manually. Never force-fit.

## 4.4 OCR engine integration target

Release 1.0 includes the optional OpenAI proposal voter, vendor-neutral OCR/HTR
contracts, and a separately enabled Google Document AI OCR/table-evidence
adapter. Document AI retains raw page response and table/cell evidence but makes
no canonical decision; it is a conditional buddy-audit/reconciliation input.
The target production configuration uses independently tested readers after
client selection and golden-set benchmarking. Bilevel input raises single-engine
error rates enough that a third independent reading should be routine, not a
last-resort tiebreaker.

When provider adapters and the retained variant ensemble are independently
calibrated, one page may contribute multiple readings. Variants from one engine
are correlated evidence and must not be misrepresented as independent vendors.

## 4.5 Deduplication

Release 1.0 records exact retained-artifact duplicates. Perceptual-image and
normalized-text near-duplicate matching remain a calibrated proposal-only
extension; candidates may never be silently deleted.

## 4.6 Provenance

Every record carries source file, page range, engine, engine version, binarization
variant, run timestamp, per-field confidence, and quality score.

---

# 5. Phase 2 — Classification

Two levels: document type, then party role.

Deterministic rules on form numbers, letterhead tokens, and header strings first;
measure against the calibration sample; train a classifier only if rules plateau
below target.

**Acknowledgements are classified first and processed first** where they exist as
a document class, because they populate the attribution reference table that
Phase 3A depends on.

Below-threshold classifications route to human review. A misclassified document
produces a structurally wrong extraction, not a merely inaccurate one.

---

# 6. Phase 3 — Extraction

## 6.1 Field schema

Per document type. Header fields are cheap; line items are 3–5× the effort and
are the primary cost driver.

**Two new mandatory header fields on every financial document type:**
`ack_number` and `job_number`. These are extracted with the same rigour as
monetary fields, because §8 makes them load-bearing.

## 6.2 Independent extraction and consensus

| Condition | Disposition | Flag |
|---|---|---|
| Independent primary and secondary readings agree | Consensus proposal; applicable deterministic controls still run | `consensus_confirmed` |
| Independent readings differ or a provider fails | Explicit exception; never silently accepted | `no_consensus` |
| A later independent corroboration is available | Retain it as additional evidence; it does not erase prior disagreement | `consensus_corroborated` |

Where the multi-binarization ensemble applies, variant readings are additional
evidence within one provider's result rather than independent proof. A field
where binarization variants disagree is a signal that the page is degraded at
that location, and it is flagged even if the providers agree overall.

Provider budgets are set per engagement. Independent evidence is preserved
because a wrong figure can propagate through a long-lived financial history.

## 6.3 Arithmetic self-proof

Line extensions reconcile to line totals; lines sum to subtotal; subtotal + tax +
freight + accessorials = stated total, to the cent.

A document failing its own arithmetic is an exception regardless of confidence.
It can also be the primary means of discovering handwritten
overrides that detection missed (§1.2).

## 6.4 Additional validation

Dates within the operating window; currency and UOM against controlled
vocabularies; invoice numbers against detected per-vendor format patterns; **ACK
and job numbers against the format specification from §2.3**; tax rates within
plausible ranges.

Format validation on attribution keys is the cheapest error detection in the
project. A five-character ACK number in a corpus where every other ACK is six
characters is an OCR error, and the check costs nothing.

Raw addresses are retained as evidence and locally split into CRM components: address line 1, address line 2, city, state or region, postal code, and ISO country code. An approved run may then explicitly call Google Address Validation with a non-secret API key and a maximum of 5,000 provider requests. It records a separate standardized-address, deliverability, and geocode proposal only when the verdict is complete and premise-level without component uncertainty. Provider failures, including unsupported country coverage, caps, incomplete verdicts, and conflicts stay in the final-review queue; no source value is overwritten. Address Validation itself supplies the geocode evidence, so this path does not call the separate Geocoding API.

## 6.5 Exception policy

Sampling governs the clean population only. Every record failing consensus,
arithmetic, validation, or reconciliation is worked by a human, 100% of the time.
Expect this to run at the upper end of the typical 5–15% band given bilevel
input.

## 6.6 Entity resolution

Party names and addresses normalized and fuzzy-matched into a deduplicated master
with defined surviving-record rules. "ACME Corp", "Acme Corporation", and "ACME
CORP." must collapse to one account or the CRM is worthless on delivery.

Every merge decision is logged and reversible. Ambiguous clusters are presented
for adjudication, not guessed at.

---

# 7. Phase 3H — Handwriting Capture Track

## 7.1 Scope decision and its boundary

The client has directed that handwriting may be ignored. That direction is
accepted for **recognition** — full handwritten text recognition with 3-of-3
consensus is descoped, along with its manual review burden.

It cannot be accepted for **capture**, for two specific reasons:

**First, the attribution requirement.** §2.3 asks where ACK and job numbers
physically appear. On shipping paperwork these are very often handwritten —
written on at the point of order entry or at receiving. If handwriting is
discarded entirely and ACK numbers are among the discarded content, the
attribution requirement in §8 cannot be met at all. The two instructions are in
direct tension, and attribution is the harder requirement.

**Second, arithmetic integrity.** Handwritten quantity and price corrections
change what a document actually says. Ignoring them does not make the printed
figure correct; it makes it wrong and unflagged. Arithmetic proof will surface
these regardless (§1.2), so the work of dealing with them is not avoided by
descoping — only the ability to resolve them cheaply is lost.

### The resulting scope

| Activity | Status |
|---|---|
| Detect handwritten regions | **Retained** — cheap, and required for the flag |
| Crop and store region images | **Retained** — the artefact the CRM links to |
| Read ACK / job numbers from handwriting | **Retained** — mandated by §8 |
| Read quantities/amounts where arithmetic fails | **Retained** — triggered by failure, not by default |
| Read cheque numbers and payment marks | **Retained** — needed for §9 aging closure |
| Read all other handwritten content | **Descoped** — captured as an image, flagged, not transcribed |
| 3-of-3 consensus on all handwritten numerics | **Narrowed** — applies only to the retained categories |
| Manual review of every handwritten amendment | **Narrowed** — applies only where a dollar figure or attribution key is affected |

This preserves the client's cost saving — the bulk of handwriting is delivery
notes, initials, and marginalia that genuinely does not need transcription —
while protecting the two things that would otherwise break.

## 7.2 Detection without colour: target architecture

Three signals in combination, since ink separation is unavailable.

**Stroke morphology** — planned local stroke-width variance, baseline instability, slant
irregularity, curvature entropy. Primary detector. Degraded on bilevel relative
to grayscale, which is why the §3.2 bucket split matters.

**Layout delta** — planned registration against a learned, client-approved form
template; ink outside the printed field grid or overlapping printed glyphs is
annotation. Strong on standardized forms, weak on free-format documents.

**Full-page vision-language model proposal** — the implemented optional PDF-mode
OpenAI lane may enumerate likely handwritten regions with bounding boxes. It is
proposal-only and cannot substitute for a calibrated detector or independent HTR.

Detection recall will be materially below what colour scans would give. Detector
output sets processing priority, never eligibility.

## 7.3 Capture and CRM linkage contract

The canonical contract supports a record for every detected region regardless of
whether it is transcribed:

```
handwriting_region:
  document_id, page, bounding_box, region_image_path,
  detector, detector_confidence,
  annotation_type_guess, transcribed (bool),
  transcribed_text (nullable), recognition_agreement (nullable)
```

The target CRM adapter stores and links the cropped region image.
The practical effect: a user viewing an invoice sees a flag that the document
carries handwriting, and can click through to the actual cropped image of it
without anyone having transcribed it. This satisfies the client's instruction to
"capture it somehow and log it" at near-zero marginal cost, and it means the
decision to transcribe can be revisited later without reprocessing the corpus.

## 7.4 Targeted recognition triggers

Transcription runs only when triggered:

1. Region classified as likely containing an ACK or job number, by position and
   character pattern
2. Document failed arithmetic self-proof and a handwritten region overlaps a
   quantity, price, or total field
3. Region overlaps a payment block, or a "PAID" stamp was matched nearby
4. Region falls within a QA sample selection

The target integration uses dedicated independent HTR engines in parallel,
full-page context rather than isolated crops, 3-of-3 agreement required for
numerics. The strictness is retained where it is applied; only the scope of
application is narrowed.

## 7.5 Precedence rule

Unchanged and non-negotiable:

> **Printed values are the baseline. Handwritten values are amendments carrying a
> later effective time. Both are stored.**

The canonical layer exposes the amended value as current and the printed value as
`original_value`, with `amendment_source = 'handwritten'`. Auditable and
reversible.

## 7.6 Signatures

Never transcribed as data. Recorded as `signed` (boolean), region bounding box,
and approver identity only where separately legible from a printed name line.
Reading cursive signatures as names produces confident nonsense that contaminates
entity resolution.

---

# 8. Phase 3A — Attribution

New phase. Every dollar in the dataset ties to an ACK or job/project number, and
every sale carries an originating city. This is a hard gate, not an enrichment.

## 8.1 Why this is a separate phase

Attribution is not a field extraction problem. Roughly half the documents in a
corpus of this kind will not state the attribution key directly — a carrier
freight bill carries a PRO number and a BOL reference, not a job number. The key
has to be **derived through the document graph**, which requires extraction and
entity resolution to be complete first.

Treating it as just another extracted field is the most common way this
requirement fails. It has its own phase, its own resolution hierarchy, its own
coverage metric, and its own gate.

## 8.2 Attribution reference table

Built first, before any resolution is attempted. Sources in order of authority:

1. Client's order-entry or quoting system export, if one exists — a digital list
   of ACK numbers with dates, customers, and amounts. This is by far the best
   input and §2.4 asks for it specifically.
2. Acknowledgement documents in the corpus, extracted in Phase 3.
3. ACK and job numbers appearing on any other document type.

The reference table gives every known ACK and job number a validated format, a
date range, a customer, and an expected value. Every subsequent resolution is
checked against it, which converts attribution from guesswork into lookup.

## 8.3 Resolution hierarchy

Applied in order. The first method that resolves wins, and **the method is
recorded on the record**.

| Rank | Method | Confidence | Notes |
|---|---|---|---|
| 1 | Explicit printed ACK or job number on the document | Highest | Validated against reference table |
| 2 | Explicit handwritten ACK or job number | High | Requires 3-of-3 recognition per §7.4 |
| 3 | Purchase order number cross-reference | High | PO → ACK via reference table |
| 4 | Invoice-to-shipment linkage | Medium | Shipment already attributed; invoice inherits |
| 5 | Payment-to-invoice linkage | Medium | Invoice already attributed; payment inherits |
| 6 | Customer + date-window + amount match against reference table | Medium-low | Requires a unique match; ambiguous matches are not resolved |
| 7 | Email evidence | Low | Phase 7 only. Corroborating, never overriding |
| 8 | Manual assignment | Recorded | Assigner and date logged |
| — | **Unattributable** | — | Explicit disposition with a stated reason and dollar value |

Ranks 4 and 5 are inheritance rules and they are what make the requirement
achievable. A carrier freight bill that states no job number inherits one from
the shipment it bills for, which inherited it from the invoice, which stated it
explicitly. The chain is recorded so the inference is auditable.

## 8.4 Sales origin city

The client requires knowing what city a sale came from. **This phrase has at
least four defensible meanings and they produce different numbers.** Settle it in
Phase 0 rather than discovering the disagreement after the analysis is
presented.

| Interpretation | Meaning | Typical source |
|---|---|---|
| Selling location | Which of the client's offices or branches booked the sale | Letterhead, issuing office, ACK prefix, salesperson |
| Ship-from origin | Where the goods physically departed | BOL origin, warehouse address |
| Customer location | Where the customer is based | Bill-to address |
| Delivery destination | Where the goods went | Ship-to address, POD |

In most businesses asking this question, the intended meaning is **selling
location** — a territory or branch performance question. It is also the hardest
of the four to source, because it is frequently not stated on the document at
all and has to be inferred from letterhead, ACK number prefix, or salesperson
assignment.

### Resolution hierarchy for selling location

1. Explicit branch or office field on the document
2. Letterhead or footer address on the issuing document — recoverable by template
   matching against a small library of the client's letterhead variants
3. ACK number prefix, where the numbering scheme encodes location (common; check
   in §2.3)
4. Salesperson or rep name, mapped to a territory table
5. Customer's assigned territory from the reference table
6. Email evidence (Phase 7)
7. Unresolved — explicit, with dollar value

All four interpretations are captured where the data supports it, stored as
separate fields. This costs little and means the client can change their mind
about which one they meant without reprocessing.

## 8.5 Attribution coverage gate

The exit condition for this phase.

| Metric | Requirement |
|---|---|
| Dollars attributed to ACK or job number | 100% attributed **or** explicitly registered unattributable |
| Attribution by method | Reported — the mix of ranks 1–8 is a quality indicator in itself |
| Dollars with resolved selling location | Reported, with unresolved quantified |
| Unattributable register | Every entry has a reason code and a dollar value |

An attribution rate of "97%" is not a passing result unless the remaining 3% is
enumerated with reasons and a dollar figure. The gate is not high attribution; it
is that **no dollar is unaccounted for silently.**

Reason codes for legitimate unattributables — internal transfer, rebate, rebilled
freight, pre-system-implementation, credit against a closed job — are agreed in
Phase 0 so they are dispositions rather than failures.

---

# 9. Phase 4 — Completeness and Data Quality Gate

Completeness is a harder requirement than accuracy. A missing document is
invisible; a wrong one is at least detectable.

## 9.1 Financial event stream

All documents normalize to a ledger-shaped stream:

```
event_id, event_type, party_id, amount, currency, effective_date,
source_document_id, ack_number, job_number, selling_location
```

`event_type in {purchase, payment, credit, adjustment, accrual}`

Attribution keys travel on the event, not just on the source document, so every
downstream aggregation can be cut by job without a join back through the document
graph.

Non-financial documents register with a null amount so the shipment lifecycle
stays complete.

## 9.2 Matching

**Three-way match:** PO <-> receipt (packing list or POD) <-> invoice.

**Open-item matching:** invoice <-> payment <-> remittance advice, supporting partial
payments and one-payment-to-many-invoices.

**ACK-to-invoice match:** every ACK in the reference table should resolve to one
or more invoices. ACKs with no invoice are either open jobs or missing documents
— the distinction matters and is reported.

Every match carries a method and confidence.

## 9.3 Completeness controls

1. **Sequence continuity** — invoice numbering per vendor; all gaps enumerated.
   When acknowledgements are in scope, apply the control to **ACK number sequences**, which are typically
   client-issued and therefore far more reliably sequential than vendor invoice
   numbers. A gap in the client's own ACK sequence is strong evidence of a
   missing job.
2. **Calendar continuity** — document counts and amounts by vendor by month.
3. **Period reconciliation** — extracted totals vs GL by period and vendor.
   Variance reported, never plugged.
4. **Bank/cash reconciliation** — payment events vs bank statement totals.
5. **Aging closure** — every invoice terminates in a payment, credit, write-off,
   or explicit open status.
6. **Attribution closure** — every dollar attributed or explicitly registered
   (§8.5).

## 9.4 Completeness Report

A standing deliverable regenerated on every run: total documents and value;
unmatched count and value by cause; sequence gaps including ACK gaps; calendar
gaps; GL variance; **attribution coverage and the unattributable register**;
exception queue volume and clearance.

Every analytic output carries these figures on its face.

## 9.5 Gate

Does not proceed to analytics until the one-sided MUS bound is completed and
within tolerance (or the affected population was fully reworked), the
Completeness Report has `gate_status: clear`, separately reviewed attribute
results are accepted, and the §8.5 attribution gate is met.

---

# 10. Phase 4Q — Sampling QA

Two schemes in parallel, both drawing only from records marked `auto_accepted`
whose arithmetic status is explicitly `proved` or `not_applicable`.

## 10.1 Monetary unit sampling

Selection probability proportional to document value. Everything above the
materiality threshold, plus any item larger than the provisional systematic
interval, is examined with certainty. Produces a one-sided projected
overstatement and dollar-denominated 95% upper overstatement bound. Observed
understatement and completeness are reported separately.

## 10.2 Stratified attribute sampling

The checked-in script creates a reproducible document-selection plan by document
type × year × processing branch × `has_handwriting`; JBIG2 status is a separate
oversampling trigger. It reports a zero-finding rule-of-three planning bound per
stratum, not observed field accuracy. Vendor rank, image-quality/scan-bucket,
period-edge, and ACK/job field strata require an engagement-specific extension
and validation before they can be reported.

**Oversampling:**

| Stratum | Multiplier |
|---|---|
| Handwriting-bearing Branch A | 4× |
| Handwriting-bearing Branch B | 9× |
| Handwriting-bearing B-rescan | 4× |
| JBIG2-suspect stratum | at least 5× |

Attribution keys are a recommended engagement-specific oversample because a wrong
ACK number does not produce a wrong total. The current generic planner does not
derive a field-level ACK/job stratum, so an operator must not claim it did.

## 10.3 Sizing

Rule of three: zero errors in *n* gives an upper 95% bound of approximately 3/*n*.

| n | Upper bound |
|---|---|
| 60 | ~5% |
| 300 | ~1% |
| 600 | ~0.5% |

Sizing derives from tolerable error rate, not corpus size.

## 10.4 Escalation

If the completed MUS upper overstatement bound exceeds tolerance, the affected
population goes to 100% review and is re-sampled after remediation. Invalid,
duplicate, off-sample, or confidence-factor-table-exhausting findings block the
calculation instead of producing a bound.

## 10.5 Golden set

500–1,000 hand-labelled pages spanning every document type, vendor format,
quality band, both bilevel and grayscale buckets, and heavy handwriting
representation. Permanent regression infrastructure; every pipeline change is
benchmarked against it before deployment.

---

# 11. Phase 5 — Analytics

Scoped against the Phase 0 decision list.

## 11.1 Job and ACK analytics

When configured under §8, this is a primary analytic frame.

- Revenue, cost, and margin by job or ACK
- Job profitability distribution; loss-making job identification
- Cost composition by job — materials, freight, accessorials, duty
- Job cycle time: ACK date → ship date → invoice date → payment date
- Open ACKs with no invoice; invoiced jobs with no payment
- Revenue by job over time; job size distribution

## 11.2 Geographic analytics

- Revenue by selling location, by period
- Growth and decline by location
- Customer concentration within each location
- Lane and destination mix by originating location
- Margin by location

Every geographic figure carries its resolution-method mix. A city total that is
60% inferred from ACK prefix and 40% explicitly stated is a different quality of
number than one that is fully explicit, and the reader needs to know which they
are looking at.

## 11.3 Customer analytics

Revenue by account and period; concentration; first-order date; RFM; churn
signal; cohort retention; product mix.

Churn findings are cross-checked against the calendar-gap report before
presentation. A customer who appears to have stopped ordering may simply have
documents in an unscanned batch — the most common false finding in backfile
analytics.

## 11.4 Vendor and carrier analytics

Spend by carrier; rate variance by lane; on-time delivery from POD dates; claim
and damage rate; vendor concentration; fuel surcharge ratio.

## 11.5 Cost analytics

Freight cost per shipment, per unit weight, per lane; **accessorial leakage** —
detention, demurrage, fuel surcharge, redelivery, storage; landed cost; cost
trend normalized for volume.

Accessorial leakage is usually where recoverable money is found. It requires
accessorials itemized by code at extraction; collapsing them into a single
freight figure makes the analysis impossible without re-extraction.

## 11.6 Financial control analytics

Duplicate invoice detection across the full corpus; price-vs-contract variance;
payment terms adherence; DSO/DPO; early-payment discount capture; unapplied cash;
overbilling via three-way match variance.

## 11.7 Data quality analytics

A permanent operational dashboard: extraction accuracy by field, type, and
quality band; handwriting incidence and capture rate; **attribution coverage by
method**; exception volume and clearance; completeness metrics; entity resolution
status.

---

# 12. Phase 6 — Data Model and CRM Handoff

## 12.1 Layers

**Raw** (immutable engine output, retained permanently) → **staged** (typed,
validated) → **canonical** (deduplicated, entity-resolved, amendments applied) →
**CRM load** (target-shaped).

Any downstream layer rebuilds from raw without re-OCR.

## 12.2 Core entities

`party`, `party_role`, `address`, `contact`, `item`, `carrier`, `lane`,
`shipment`, `document`, `invoice_header`, `invoice_line`, `payment`,
`payment_application`, `amendment`, `exception_event`

**Implemented control:**

| Entity | Purpose |
|---|---|
| `acknowledgement` | The ACK master. Number, date, customer, selling location, value, status |
| `job` | Job or project master where distinct from ACK |
| `attribution` | Links any financial row to an ACK or job, with method, confidence, and evidence chain |
| `selling_location` | Branch/office master with territory mapping |
| `handwriting_region` | Detected annotation regions, cropped images, transcription status |
| `email_message` | Phase 7 only |
| `email_evidence` | Phase 7 only — links an email to a document, attribution, or party |

## 12.3 Common control columns

Every batch-managed business row carries its idempotency key, `batch_id`, and
`review_status`. Evidence-bearing rows also carry a direct source-document key
or explicit decision-evidence link. Static vocabulary and pure association rows
retain their own stable natural/composite keys without inventing page provenance.

Applicable transaction fields include:

- Identity: stable primary/idempotency key and `natural_key` where applicable.
- Source proof: `source_document_id` and `source_page_range`.
- Control: `source_confidence`, `review_status`, and `has_handwriting`.
- Value lineage: `original_value`, `amended_value`, and `amendment_source`.
- Quality band: `image_quality_band`.
- Scan bucket: `scan_bucket`.
- Processing audit: `created_at`, `updated_at`, and `batch_id`.

Financial tables carry `ack_number` and `job_number`. They also carry
`attribution_method` and `selling_location_key`.

## 12.4 Load order

1. Reference data — currency, UOM, charge codes, country, document type, **selling_location**
2. `document` registry — before every source-provenance foreign key
3. `party` → `address` → `contact`
4. `carrier`, `item`, `lane`
5. **`acknowledgement`, `job`**
6. `shipment`
7. `invoice_header`
8. `invoice_line`
9. `payment`, then `payment_application`
10. **`attribution`**
11. **`handwriting_region`**, `amendment`, `exception_event`
12. Phase 7 only: `email_message`, `email_evidence`

ACK and job masters load before any transactional table that references them.
Attribution loads after both sides exist.

## 12.5 Load mechanics

Idempotent upsert by natural key. Referential integrity enforced at load, not
assumed — a failed foreign key halts the load rather than silently dropping the
row. Load manifest per run. Rollback by `batch_id`.

## 12.6 Handoff deliverables

Canonical dataset; ERD and data dictionary; load order specification; integrity
rules; Completeness Report; one-sided MUS overstatement bound and separately
reviewed attribute-sample results;
**attribution coverage report and unattributable register**; open exception
register.

---

# 13. Phase 7 — Email Corpus Integration (Optional, Deferred)

An email corpus may be incorporated as an ancillary step when separately
authorized. It is designed so that deferring it costs nothing and executing it
requires no rework of Phases 0–6.

## 13.1 Scope mismatch, stated up front

An email corpus may cover a shorter or different period than the document
corpus. **Email can enrich only its measured overlap window.** Any analytic
conclusion drawn from email-derived data applies to that window and must state
the coverage limit. Presenting an email-enriched metric alongside a full-period
trend without noting the coverage break produces a false discontinuity that
looks like a business change.

## 13.2 What email is good for

In descending order of likely value:

**1. Attachments.** Emails frequently contain a digitally generated PDF of an
invoice that exists in the scan corpus only as a degraded black-and-white image.
Where an attachment matches a scan, retain it as a separate source with its own
hash and provenance. Compare its derived values to the scan/OCR proposals and
record agreement or a cited amendment/discrepancy; never replace either source.
Given that the historic corpus is bilevel, this is the single most valuable
thing in the mailbox and on its own may justify the phase.

**2. Attribution gap-filling.** Emails carry ACK numbers, job numbers, and PO
references in subject lines and bodies. For dollars stranded at rank 8 or
unattributable in §8.3, email is often the only remaining evidence.

**3. Missing payment evidence.** Remittance advices, payment confirmations, and
"cheque is in the mail" threads close aging items that the document corpus leaves
open.

**4. Selling location evidence.** Signature blocks carry office addresses. Sender
identity maps to territory. This resolves §8.4 rank 6.

**5. Contact enrichment for the CRM.** Names, titles, phone numbers, and current
email addresses per account — data the document corpus barely contains and the
CRM genuinely needs.

**6. Order-to-cash narrative.** Quote → ACK → change orders → dispute → settlement
threads explain anomalies that the documents only record the outcome of.

## 13.3 What email must not be used for

**Email is corroborating evidence. It never overrides a source document.**

An email saying "invoice 2231 is for $5,500" does not correct an invoice that
says $5,507.50. It raises a discrepancy for human resolution. The exception is
§13.2 item 1: a PDF *attachment* is a document, not an email, and is treated as
such.

All email-derived values carry `source = 'email'` and are excluded from the
document-based accuracy statement, which is computed on documents only.

## 13.4 Process

**Screening first.** Before any content processing: define mailbox scope, date
range, and an exclusion filter for personal correspondence, HR matters, legal
advice, and anything privileged. Screening precedes ingestion, not follows it.
The output of this step is a written scope agreement.

**Ingest.** PST, MBOX, EML, or Graph API export. Normalize to a common structure:
message id, thread id, sender, recipients, date, subject, body, attachments.

**Thread reconstruction and deduplication.** Reply chains duplicate body text
extensively; without deduplication the corpus inflates several-fold and reference
extraction returns the same fact dozens of times.

**Attachment extraction and matching.** Every attachment extracted, hashed, and
matched against the document corpus by content and by reference number.
Matched attachments are promoted into the document pipeline as
higher-fidelity replacements, processed through Phases 2–3 normally, and their
extracted values compared against the scan-derived values. **Disagreements are
findings**, not silent overwrites — they measure how much the black-and-white
scanning actually cost.

**Reference extraction.** ACK numbers, job numbers, invoice numbers, PO numbers,
cheque numbers, and amounts, by pattern match against the §8.2 reference table
formats, plus a language-model pass for references stated in prose.

**Entity linkage.** Sender and recipient domains and addresses matched to the
resolved party master from §6.6. Contacts extracted for CRM load.

**Evidence linkage.** Each extracted reference becomes an `email_evidence` row
linking a message to a document, attribution, or party, with the quoted text and
a confidence.

## 13.5 Deliverables

Email-derived contact list for CRM load; attribution gap-fill report with
before/after coverage; recovered payment evidence with its effect on aging
closure; **attachment-versus-scan discrepancy report**; revised Completeness
Report for the email-covered window.

## 13.6 Why this is deferred rather than dropped

Executing Phase 7 later costs nothing extra provided Phases 0–6 leave room for
it. The room required is: `source` fields on every value, an `attribution` table
that accepts new evidence rows without schema change, and an unattributable
register that persists rather than being cleared at handoff. All three are in the
The current design.

---

# 14. Build Sequence and Parallelization

| Track | Dependency | May start |
|---|---|---|
| Scan profiling (0.5) | Corpus access | Immediately |
| Ingestion (1) | Phase 0 taxonomy | After Phase 0 |
| Classification, extraction (2–3) | Calibration sample | After Phase 0 |
| Handwriting capture (3H) | 0.5 quality bands | After 0.5 |
| **Attribution reference table (8.2)** | ACK format spec | **After Phase 0 — independent of scan processing** |
| Attribution resolution (8.3) | Extraction + entity resolution | After Phase 3 |
| Completeness (4) | Attribution, GL export | After 3A |
| Data model and load spec (6) | Phase 0 taxonomy | After Phase 0 |
| Analytics definitions (5) | Decision list | After Phase 0 |
| Email (7) | Screening agreement | Any time after Phase 6 |

The attribution reference table is on the critical path and is buildable
immediately from the client's order-entry export, independent of anything
happening to the scans. Start it early; it de-risks §8 more than any other single
action.

---

# 15. Outputs and Deliverables

## 15.1 Phase deliverables

| Phase | Deliverable |
|---|---|
| 0 | Constraint document; calibration sample; ACK/job format specification |
| 0.5 | Grayscale/bilevel split; quality distribution; JBIG2 register; remediation recommendation |
| 1 | Normalized page store; reassembled document register; deduplication report |
| 2 | Classification rules or model; accuracy report |
| 3 | Extracted field dataset; consensus and arithmetic exception queues |
| 3H | Handwriting region index with cropped images; targeted transcriptions; amendment table |
| 3A | Attribution reference table; attributed event stream; coverage report; unattributable register; selling-location assignment |
| 4 | Completeness Report; reconciliation statements; matched event ledger |
| 4Q | Golden set; sampling plan; one-sided MUS overstatement bound; separate reviewed attribute results |
| 5 | Analytics layer including job and geographic dashboards |
| 6 | Canonical dataset; data dictionary; ERD; load order; load manifests |
| 7 | Email-derived contacts; attribution gap-fill; attachment discrepancy report |

## 15.2 Final output

A single validated corpus supporting four uses:

1. **Financial analysis** — every purchase and payment documented, matched,
   reconciled, with variances quantified.
2. **Job-level analysis** — every dollar tied to an ACK or job number, enabling
   per-reference profitability across the measured period.
3. **Geographic and operational analysis** — revenue by selling location;
   shipment, lane, carrier, and transit performance.
4. **CRM foundation** — deduplicated, entity-resolved, load-ordered, with
   enforced integrity and full traceability from every record to a source page.

## 15.3 What the client can state on completion

- What the data says.
- How accurate it is, with a numeric confidence bound, reported separately for
  the better and worse scan quality bands.
- What is missing, enumerated.
- **Which dollars could not be attributed, and why, with a dollar figure.**
- Where every individual figure came from.

---

# 16. Immediate Next Actions

| # | Action | Owner | Blocks |
|---|---|---|---|
| 1 | Total page count across all scan files | Client | QA sizing and cost model |
| 2 | Corpus access for the Phase 0.5 probe | Client | Quality banding, remediation decision |
| 3 | **Relationship-key format specification, including changes over the period** | Client | Attribution and schema mapping |
| 4 | **Reference-system export, if one exists** | Client | Highest-confidence attribution and relationship validation |
| 5 | **Approved reporting dimensions and business questions** | Client | Analytics and schema design |
| 6 | Confirm whether grayscale or colour masters exist for any pages | Client | Remediation option |
| 7 | Confirm whether relationship keys appear handwritten on documents | Client | §7 scope |
| 8 | Accounting system GL export | Client | Phase 4 reconciliation |
| 9 | Decision list | Client | Phase 5 scope |
| 10 | Email scope and screening agreement | Client | Phase 7 only — not blocking |

Items 3, 4, and 5 materially change the shape of the work. Item 4 can reduce
the cost of attribution substantially when an authoritative export exists; when
it does not, inferred proposals remain clearly labelled and review-bound.

---

# 17. Reference Implementation

A working toolkit implements the load-bearing controls in this plan. It includes
optional, client-approved OpenAI Responses, Gemini-on-Vertex and OpenRouter
page-extraction adapters, Document AI corroboration, Google Cloud Vision
handwriting proposals, and a retained image-comparison ensemble. These are
implemented integrations, not claims of deployed accuracy or automatic approval.
Additional independent OCR/HTR providers, calibrated production detectors,
broad automatic classification, and target-system execution still depend on
client-selected integrations and acceptance. The validation and control layer
determines whether the retained output can proceed; no provider or proposal lane
may bypass it. The optional visual-intake journal and approved-fact MCP/API
surfaces are described separately in §0.3 and §17.1.

## 17.1 Scripts

This is a representative implementation map, not the complete CLI catalogue.
Use `references/command-line-reference.md`, its parser-derived help catalogue,
and the operations skill's lane table for every current command and flag.

| Script | Implements | Plan section |
|---|---|---|
| `scan_profile.py` | Scan buckets, JBIG2 hazards, quality bands, false-colour checks, and rescan evidence | §3 |
| `ingest_pages.py` | Byte-verified source retention, immutable one-page PDFs, relative provenance, native text, conservative type labels, and exceptions | §4-5 |
| `reassemble_pages.py` | Safe manifest-path validation, review-required page groups, unordered candidates, and exact duplicates | §4.3 |
| `preprocess_pages.py` | Grayscale master, enhanced grayscale, and three complementary binarized siblings | §4/6 |
| `openai_adapter.py` | Optional structured extraction/type/handwriting-region proposal voter with raw-response retention | §5-7 |
| `google_document_ai_adapter.py` | Explicitly enabled independent OCR/table evidence with page hash, raw response, token, and table-cell provenance | §4/6 |
| `table_comprehension.py` | Source-native table profile, evidence-linked rows, non-independent audit, exact-registry mapping, and aggregated review/quality output | §6 |
| `table_comprehension_corpus.py` | Sequential multi-manifest layout-context runner with bounded amendment-only refinement | §6 |
| `independent_table_reconcile.py` | Deterministic approved-map comparison of source rows and Document AI cells | §6 |
| `detection_calibration.py` | Golden-set evaluator for handwriting-region and party-role proposals; no automatic deployment | §5/7 |
| `llm_adjudication.py` | Disabled-by-default, nonfinancial printed amendment proposals after independent agreement | §6.3 |
| `schema_discovery.py` | Proposal-only source-label, entity, relationship, and candidate-location discovery | §0/6 |
| `allocation_policy.py` | Client-approved, effective-dated allocation and sales-credit policy application | §8/11 |
| `consensus.py` | Independent multi-engine agreement, per-field flags, and exception routing | §6.2 |
| `arithmetic_check.py` | Line, subtotal, charge, total, and payment arithmetic self-proof | §6.3 |
| `adjudicate.py` | Bounded amendment proposal for a uniquely arithmetic-supported rejected total | §6.3 |
| `validate_extraction.py` | Deterministic provenance, date, currency, identifier, and plausibility checks | §6.3 |
| `address_normalize.py` | Raw-address retention, local CRM components, and optional capped Address Validation evidence | §6.4 |
| `handwriting_review.py` | Strict independent HTR reconciliation with a two-pass cap and amendment-only financial output | §7 |
| `entity_resolve.py` | Party clustering with a reversible merge log and ambiguity review | §6.6 |
| `attribution.py` | Eight-rank reference resolution, evidence chains, location hierarchy, and unattributable register | §8 |
| `completeness.py` | Sequence/calendar gaps, GL variance, aging closure, and explicit gate | §9 |
| `sampling.py` | Monetary-unit and stratified sampling, projection, confidence bound, and escalation | §10 |
| `final_review_queue.py` | Versioned, exhaustive eight-field canonical review package for supplied artifacts | §3-10 |
| `operations.py` | Run manifests/state, adapter contracts, privacy inventory, and strict reviewer exports | Cross-phase |
| `canonical_deploy.py` | Validated non-secret PostgreSQL schema deployment plan and explicit execution | §12 |
| `canonical_load.py` | Ordered, checksummed load plan with review status, batch lineage, and unique idempotency keys | §12 |
| `csv_api_staging.py` | No-send CSV files and API-operation envelope checked against the canonical load plan | §12 |
| `retrieval_store.py` | Approved-fact SQLite store with document, invoice, line, job, attribution, and location context | §12 |
| `retrieval_mcp.py` | Approved document/CRM search, exact lookup, schema, account cards, governed queries, sales and reports over stdio; optional separate proposal intake | §0.3/12 |
| `retrieval_remote_mcp.py` | OAuth Streamable HTTP transport, owner-bound report downloads and optional intake JSON API | §0.3/12 |
| `retrieval_https.py` | TLS/bearer read-only GET business queries; not an MCP endpoint | §12 |
| `visual_ingestion_export.py` | Receipt-verified source-only archive and image-PDF handoff; no proposal promotion | §0.3/4 |
| `crm_import_package.py` | Verified no-send common-object CSVs, mapping template and write plan | §12 |
| Schema-sample workbook builder | Regenerates clearly labelled fictional schema samples | Delivery documentation |

## 17.2 Chain

The operational Python stages use JSON contracts and carry records forward; the
review workbook and local retrieval database are derived delivery views:

```
scan_profile   → profile.json
ingest_pages   → ingestion_manifest.json
               → classification_exceptions.json
reassemble     → reassembly.json
               → reassembly_exceptions.json
preprocess     → preprocessing_manifest.json
openai_adapter → openai_engine.json + openai_adapter.json
               → openai_exceptions.json + openai_raw/
schema_discovery → schema_discovery.json + schema_discovery_exceptions.json
allocation_policy → allocation_results.json + allocation_exceptions.json
consensus      → consensus.json  + exceptions.json
arithmetic     → proofed.json
adjudicate     → amendments.json
               → adjudication_exceptions.json
validate       → validated.json
               → validation_exceptions.json
address_normalize → address_normalized.json
               → address_exceptions.json
handwriting    → handwriting_decisions.json
               → handwriting_client_review.json
entity_resolve → parties.json    + merges.json
attribution    → attributed.json + unattributable.json
completeness   → completeness.json
sampling       → sample_plan.json
               → accuracy_statement.json
preprocess     → preprocessing_manifest.json
operations     → run_manifest.json + run_state.json
               → privacy_inventory.json
final_review   → final_client_review.json
review_export  → final_client_review.csv
               → final_client_review.html
               → final_client_review.xlsx
canonical_load → canonical_load_plan.json
retrieval_store → business_retrieval.sqlite
retrieval_mcp  → approved-fact CRM queries, reports, and lookup
```

## 17.3 Delivery interface and update rule

The schema-sample implementation is `scripts/build_schema_samples_workbook.mjs`.

The canonical final-review package is JSON; CSV, XLSX, and HTML are regenerated
reviewer views. The XLSX workbook has a filterable `Client Review` sheet plus
`Header Definitions`, `Schema Definitions`, and `Runbook` sheets. Update the
canonical JSON or create an amendment, then regenerate outputs; never use a
workbook edit to alter a source value or close a gate. The `examples/` folder
contains only artefacts labelled **SAMPLE - FICTIONAL - NOT CLIENT DATA**. It
demonstrates extracted and derived field shapes, not client evidence,
calibration, or review output.

Address components use the role-prefixed CRM names `*_address_line1`,
`*_address_line2`, `*_city`, `*_state_or_region`, `*_postal_code`, and
`*_country_code`; raw addresses remain immutable evidence and local parsing
does not prove deliverability. When client-approved Google Address Validation
is explicitly enabled with a protected credential and a cap of at most 5,000
calls per run, its deliverability and geocode result is separately retained
evidence; uncertain, failed, exhausted, or conflicting results remain
final-review work. Never put the credential in a handoff or delivered artifact.

Runtime/provider controls are centralized in the Git-ignored root `.env`, copied
from `.env.example`; explicit command-line options and existing environment
values take precedence. `references/runtime-configuration.md` defines every
setting. The selected adapter records its provider/model and supported
reasoning effort, limits, timeout, retries, and non-secret credential reference.
Resolve these from the specific lane; do not infer a model or reasoning default
from another lane or from an old run.

## 17.4 Verification performed

The chain was exercised end to end against a synthetic 60-document corpus
carrying deliberately seeded faults. Results:

| Seeded fault | Detected |
|---|---|
| Broken printed arithmetic | Yes — flagged as exception, handwriting correctly ruled out |
| Handwritten quantity amendment breaking the total | Yes — flagged, handwriting correctly implicated |
| Two-of-three engine disagreement | Yes — accepted with `consensus_2of3`, queued for review |
| Three-way engine disagreement | Yes — hard exception, not auto-resolved |
| Malformed ACK number (`AK-99`) | Yes — format-rejected, then recovered via shipment inheritance |
| Vendor name variants (`ACME Corp` / `ACME Corporation` / `Acme Corp.`) | Yes — merged to one party |
| Dotted legal suffix (`Globex LLC` / `Globex L.L.C.`) | Yes — merged after normalization fix |
| GL inflated 4% against documents | Yes — variance reported, not plugged |
| Bilevel, grayscale, and false-colour pages | Yes — all bucketed correctly |
| Sampling tolerance breach | Yes — escalation to 100% review triggered |

The synthetic test corpus exercised four example resolution ranks (explicit
printed, explicit handwritten, cross-reference, and inherited linkage). This is
test evidence for control behavior, not a claim that any client corpus has
achieved attribution coverage.

## 17.5 Production operations controls

The reference implementation now creates immutable run manifests, hashes non-secret configuration, blocks resumptions against a different manifest, validates vendor-neutral OCR/HTR handoff contracts, avoids embedded credentials, inventories obvious privacy patterns without changing evidence, creates grayscale and an enhanced grayscale and three complementary binarized sibling page variants, and exports the final review package to CSV, a formatted XLSX workbook, and static HTML. CI executes strict coverage, lint, acceptance, public-corpus validation, and LaTeX rendering. These controls improve reproducibility and operating safety; they do not establish an accuracy percentage without a representative golden set.

## 17.6 What remains to build

- Additional independent OCR/HTR vendor adapters and calibrated local handwriting-region detectors
- Deskew, despeckle, rotation, and scan-specific enhancement beyond the retained comparison ensemble
- Calibrated automatic classification and party-role assignment beyond the high-signal rules and proposal-only model lane
- Approved-template layout-delta comparison and a configured stamp library (§3.5)
- Email ingestion, if Phase 7 proceeds (§13)
- A target-specific CRM adapter, remote transport, and load reconciliation against the chosen system (§12)
- Representative golden-set calibration before any numerical production accuracy claim

The control layer being complete first is deliberate. It means every component
added above is measured against working checks from the day it lands, rather than
being trusted until something downstream fails.

---

# Appendix A — Plain-Language Illustrative Scenario

*This appendix restates the plan without technical vocabulary. It is a
non-normative example only: the actual document families, coverage periods,
relationship keys, locations, and optional evidence sources are established in
Phase 0. The implemented controls and explicit future-work boundaries in the
body of this plan govern if this appendix and the technical sections differ.*

## A.1 What you have and what you want

You have a retained collection of business records — invoices, shipping
receipts, delivery slips, or other approved document families — scanned into
PDF files. Some pages may have handwriting or degraded image quality.

You want reliable, analyzable numbers; every monetary amount tied to a
configured business reference where evidence permits; clear origin or other
approved analytic dimensions; and clean party records for an agreed target
system.

You may also have a separately scoped email archive that can be considered
later as corroborating evidence.

## A.2 The core difficulty

Run the scans through ordinary scanning software and you get a spreadsheet full
of numbers. Some are wrong. You will not know which.

That is the real danger. Wrong numbers that look right are worse than no numbers,
because you will act on them. A total off by a digit, a customer split across
three phantom accounts, an invoice scanned twice and counted twice — none of
these announce themselves.

So the plan is built around one question: **how do we know the numbers are
right without relying on unchecked automation?**

## A.3 How we answer that

**We compare genuinely independent readings and deterministic controls.**

Not one system reading each page, but three independent ones, compared. When they
agree, we accept it. When they disagree, it goes on a review list. Originally
this plan called for two systems; black-and-white scans are harder to read
accurately than colour ones, so we have added a third.

**We make every invoice prove itself with arithmetic.**

An invoice contains its own answer key. The line items should add up to the
subtotal; the subtotal plus tax and freight should equal the total. If a document
does not add up, something was read wrong — and we know it, even if the software
was confident.

This has become the most important check in the whole project, for a reason
explained in A.5.

**We hand-check a sample, chosen to matter.**

Not a random sample. A sample weighted toward **dollars**. A $10,000 invoice is
far more likely to be checked than a $50 one, because that is where the risk is.
Everything above a threshold you set gets checked for certain.

The result is not "we spot-checked it and it looked fine." It is a one-sided
95% upper bound on monetary overstatement, paired with separately disclosed
understatement observations and completeness results. That is a number whose
scope a client can evaluate without mistaking it for proof of missing records.

And if any category fails the check — say invoices from one vendor in 2022 turn
out error-prone — we do not accept them with a warning label. **We go back and
check every single one.** Sampling does not replace full review. It tells us
where full review is needed.

## A.4 What black-and-white scanning costs you

This is worth understanding plainly, because it changes several things.

When a page is scanned in colour, blue or black pen ink is visibly a different
colour from printed black text, and finding handwriting is easy. In black and
white, pen and print become the same, and handwriting has to be found by the
*shape* of the writing — which works, but not as reliably.

The same applies to stamps. A red "PAID" stamp is unmistakable in colour. In
black and white it blends into whatever it was stamped over.

What we do about it:

- **Read everything three times instead of twice**, as described above.
- **Where scans are grayscale rather than pure black-and-white**, process each
  page three different ways and treat each as another independent reading. Costs
  only computer time.
- **Look for stamps by shape** instead of colour, using a small library of your
  recurring stamps.
- **Assume handwriting might be on any page**, rather than trusting that we found
  all of it.
- **Lean much harder on the arithmetic check**, which is the subject of A.5.

One thing worth checking: not all black-and-white scans are equal. Some keep grey
shading; some are pure black-and-white with the grey thrown away. The second kind
is meaningfully harder to work with. We will measure which you have in the first
hour. If any pages are the harder kind and you still have the original paper or
an earlier better scan, re-scanning just those pages is the cheapest accuracy
improvement available anywhere in this project.

## A.5 About the handwriting

You have said the handwriting can be ignored. We have accepted that, mostly — but
there are two places where it cannot be, and it is better to say so now than to
discover it in month three.

**First, your job numbers.** You need every dollar tied to an ACK or job number.
On paperwork like this, those numbers are very often *written on by hand* — added
at order entry or at receiving. If we throw away all handwriting and your job
numbers are among it, we cannot meet your main requirement at all. So we do need
to read handwritten job and ACK numbers, even if we read nothing else.

**Second, corrections.** When someone crosses out "40" and writes "38 — two
damaged", the printed 40 is now wrong. Ignoring the handwriting does not make the
printed number right. It makes it wrong, and unflagged. The arithmetic check will
catch most of these anyway — the invoice will stop adding up — so the work does
not actually go away by ignoring it. Only our ability to resolve it quickly does.

**What we are doing instead — and this is what your instruction buys you:**

The target integration will *find* the handwriting and retain a crop, but not
transcribe most of it. Release 1.0 already defines the canonical region/evidence
contract and accepts independent HTR results; calibrated local detection,
cropping, and CRM attachment remain implementation work. Once connected, a user
can see a handwriting flag and its retained crop without anyone typing it out.

The future targeted HTR integration reads handwriting in four situations: it looks like a job or
ACK number; the invoice does not add up and the handwriting is sitting on a
quantity or price; it appears to be a cheque number or payment mark; or it landed
in the quality-check sample.

That preserves nearly all of your cost saving — the bulk of handwriting is
delivery notes and initials that genuinely do not need transcribing — while
protecting the two things that would otherwise break. And because the images are
stored, you can change your mind later and have them transcribed without
re-processing anything.

## A.6 Tying every dollar to a job

This is a bigger job than it sounds, and it gets its own phase.

The problem: roughly half your documents will not have the job number written on
them anywhere. A carrier's freight bill has a tracking number and a shipping
reference, not your job number. So the job number has to be **worked out** by
following the trail from one document to another.

How we work it out, in order of preference:

1. It is printed on the document. Best case.
2. It is handwritten on the document. We read it (see A.5).
3. The purchase order number is on it, and we can look up which job that PO
   belongs to.
4. The document relates to a shipment we have already tied to a job — so it
   inherits the job number.
5. It relates to an invoice we have already tied to a job — same idea.
6. The customer, date, and amount uniquely match one job in your records.
7. An email mentions it (only if we do the email phase).
8. Someone assigns it by hand, and we record who and when.
9. **It genuinely cannot be attributed** — and we record that explicitly, with a
   reason and a dollar amount.

Steps 4 and 5 are what make this achievable. They are how a freight bill that
mentions no job number still ends up correctly filed.

**The most useful thing you can give us:** if your order-entry or quoting system
can export a list of every ACK number with its date, customer, and amount, that
list becomes the master reference and makes this entire phase far cheaper and far
more reliable. This is the single highest-value item on the list of things we need
from you.

**What "done" looks like:** not "97% of dollars attributed." It is that 100% of
dollars are either attributed *or* explicitly listed as unattributable with a
reason and an amount. Ninety-seven percent with the other three percent
unexplained is not a passing result — the missing three percent is exactly what
someone will ask about.

## A.7 Which city the sale came from

You want to know what city each sale came from. Before we build anything, we need
to pin down what that means, because it has four possible meanings and they give
different answers:

- **Which of your offices sold it** — a branch or territory performance question
- **Where the goods shipped from** — a warehouse question
- **Where the customer is** — a market question
- **Where the goods went** — a destination question

Most people asking this question mean the first one. It is also the hardest to
find, because it is often not written on the document at all — it has to be
worked out from the letterhead, from the ACK number prefix if your numbering
encodes location, or from which salesperson handled it.

We will capture all four where the data supports it, so if you decide later that
you meant a different one, nothing has to be redone. But tell us which one you
mean now, because it shapes what we build.

And every city figure will show how it was worked out. A city total that is 60%
guessed from ACK prefixes is a different quality of number than one that is
written on the documents, and you should be able to see which you are looking at.

## A.8 Making sure nothing is missing

Accuracy is whether the numbers we captured are right. **Completeness** is whether
we captured all of them — harder, because a document that was never scanned
leaves no trace.

**Numbering gaps.** Invoices run in sequence. If we have 1001, 1002, and 1004,
then 1003 exists somewhere and we do not have it. We list every gap. We now also
do this on your ACK numbers — and since those are your own numbers, gaps in them
are much stronger evidence than gaps in a supplier's.

**Calendar gaps.** We count documents by supplier by month. If a supplier you use
every month has nothing in March 2023, a batch of scans is probably missing.

**Comparison to your books.** We compare our totals against your accounting
system, period by period. If your books say $2.4 million in 2022 and our
documents account for $2.1 million, there is $300,000 of paperwork we do not
have. **We report that. We never quietly adjust the numbers to make them match.**

**Loose ends.** Every invoice should end somewhere — paid, credited, written off,
or still open. Anything that just stops gets listed.

**Unattributed dollars.** Per A.6.

All of this goes into a report that travels with the data. Every chart you get
shows what portion of the records it is based on. If a chart is built on 94% of
documents, the chart says so.

## A.9 Cleaning up customer names

Small-sounding, and it determines whether the CRM works.

Over time the same customer may appear as "ACME Corp", "Acme Corporation",
"ACME CORP.", and probably "Acme Corp - Chicago". These are one customer. If they
arrive in your new CRM as four accounts, then every question you want to ask —
biggest customers, who stopped buying, which accounts are growing — gives a wrong
answer. You would see four mid-sized accounts instead of one large one.

We merge these into one clean record per real business, keeping a log of every
decision so it can be reviewed and undone.

## A.10 The email option

An email archive is an optional, separately authorized step. Deferring it costs
nothing, and the design leaves room so nothing has to be rebuilt if it is later
approved.

**One caveat first:** email may cover only part of the document period. Any
number that draws on it must state the overlap window — otherwise a chart can
show what looks like a business change when it is only an evidence-coverage
boundary.

**Why it is worth considering, in order:**

**Attachments.** This is the big one. Emails often contain the original, clean,
computer-generated PDF of an invoice that we only have as a degraded
black-and-white scan. Where we find one, the attachment is *better evidence than
the scan* and use it as a separately retained source. For a degraded scan
corpus, this alone may justify the phase. Comparing the clean version to the
scanned version measures the scan-quality effect without overwriting either
source.

**Filling in missing job numbers.** For dollars we could not attribute from
documents alone, an email is often the only remaining evidence.

**Missing payment records.** Remittance notices and payment confirmations close
out invoices that otherwise look unpaid.

**Contact details for the CRM.** Names, titles, phone numbers, current email
addresses — things your paper documents barely contain and your CRM genuinely
needs.

**The story behind the numbers.** Quote, change order, dispute, settlement — the
documents record the outcome; the emails explain it.

**One firm rule:** an email never overrides a document. If an email says an
invoice was for $5,500 and the invoice says $5,507.50, that is a discrepancy for
a person to resolve, not a correction to apply. The exception is an actual PDF
attachment, which is a document, not an email.

**Before we touch any of it**, we agree in writing which mailboxes, what date
range, and what gets filtered out — personal correspondence, HR matters, anything
privileged. That agreement comes first, not after.

## A.11 What you get at the end

**Reliable numbers**, with a precise monetary statement — the one-sided 95%
upper bound on overstatement is X dollars. Observed understatement, missing
documents, and open exceptions are reported separately, along with results by
scan-quality stratum so you can see what the scan quality cost.

**A stated list of what is missing** — numbering gaps, calendar gaps, and the
difference between our totals and your accounting system.

**Every dollar tied to a job or ACK number**, or explicitly listed as
unattributable with a reason and an amount.

**Job-level analysis** — what each job actually made, what it cost, how long it
took from order to payment, and which ones lost money.

**Revenue by city**, showing how each figure was determined.

**Business analysis**, including: which customers generate the most revenue and
how concentrated you are; which customers have quietly stopped ordering; what you
spend by carrier and route; where surcharges and extra fees are accumulating;
which carriers deliver on time; duplicate invoices you may have paid twice;
whether you are being charged your contracted rates; and how your business moves
seasonally.

**Clean data for your CRM**, in the correct loading order, with the rules your
developers need to keep it clean.

**Handwriting captured as images**, attached to the right documents, flagged and
searchable — even though most of it was never transcribed.

**Complete traceability.** Every number, anywhere in the system, traces back to a
specific page of a specific scanned document. If anyone questions a figure, you
can produce the piece of paper it came from.

## A.12 What we need from you to start

1. **How many pages in total?** Determines the size and cost of the quality
   checking.
2. **What do your ACK and job numbers look like?** Format, length, prefixes, and
   whether the convention changed over the measured period.
3. **Can your order-entry system export a list of ACK numbers with dates,
   customers, and amounts?** The most valuable single thing on this list.
4. **Which meaning of "city the sale came from" do you want?** See A.7.
5. **Do your job numbers get handwritten onto documents?** Determines how much
   handwriting work is unavoidable.
6. **Access to the scan files**, for the one-hour quality check.
7. **Do you still have original paper, or better-quality earlier scans?**
   Determines whether targeted re-scanning is an option.
8. **An export from your accounting system**, to reconcile against.
9. **A list of the questions you want answered**, in plain sentences. Defines
   what "finished" means.
10. **Email scope**, only when and if you want the email phase.

## A.13 What has already been built

The checking machinery described above is not a proposal — it exists and has been
tested. The control toolkit implements intake, comparison between independent
readings, arithmetic proof, customer-name resolution, job-number attribution,
completeness checks, sampling, client review packaging, and a vendor-neutral
post-review load and retrieval boundary.

They were tested against a synthetic set of sixty invoices with deliberate
faults planted in them — a broken total, a handwritten correction, disagreeing
readings, a malformed job number, duplicate customer names spelled four ways, and
books inflated by 4% against the paperwork. Every planted fault was caught.

What remains client-specific is integration and authorization: additional
independent reading services, calibrated handwriting and party-role detectors,
scan-specific cleanup, a target CRM adapter, and a representative client-approved
golden set. The repository now includes the evaluator for that golden set, exact
template drift controls, and a proposal-only decision compiler with operator
authorization; those controls do not supply the missing client evidence or apply
production facts. The current reassembly and vision lanes remain review
proposals. That order means every added component is measured against working
controls before production use.

## A.14 The one-paragraph version

We will preserve and compare independent readings of scanned documents, make
every invoice prove itself by checking its
own arithmetic, photograph and flag every piece of handwriting while only
transcribing the parts that carry job numbers or affect the money, work out a job
or ACK number for every single dollar and explicitly list the ones we cannot,
determine which of your offices each sale came from and show how we determined
it, hand-check a dollar-weighted sample and fully review any category that fails,
tell you plainly what is missing rather than hiding it, merge duplicate customer
names into single clean records, and hand you a dataset where every number traces
back to a specific page — with an honest, numeric statement of how accurate and
how complete it is. Your email can be folded in later to fill gaps and, where it
contains clean original PDFs of documents we only have as poor scans, to improve
on the scans themselves.
