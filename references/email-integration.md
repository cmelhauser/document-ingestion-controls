# Email Corpus Integration (Phase 7)

Optional, deferred. Designed so that deferring costs nothing and executing
requires no rework of Phases 0–6.

## Contents

- [Coverage mismatch](#coverage-mismatch-state-it-first)
- [What email is good for](#what-email-is-good-for)
- [What email must not do](#what-email-must-not-do)
- [Screening](#screening-comes-first)
- [Process](#process)
- [Deliverables](#deliverables)
- [What makes deferral free](#what-makes-deferral-free)

---

## Coverage mismatch: state it first

A mailbox archive rarely spans the same period as the document corpus. When the
email archive covers only part of the document period, **email enriches only
its measured overlap window**.

Any metric drawing on email-derived data applies to that window and must say so.
Presenting an email-enriched figure alongside a full-period trend without noting
the coverage break produces a discontinuity at the boundary that reads as a
business change and is nothing of the sort. This has to be handled in the
presentation layer, not just noted in a methodology appendix.

---

## What email is good for

In descending order of value.

### 1. Attachments

The single most valuable thing in a mailbox, and frequently underestimated.

Emails may carry a **digitally generated PDF** of an invoice that the scan corpus
holds only as a degraded image. Where an attachment matches a scanned document,
retain it as a separate source with its own hash and provenance. Compare its
derived values with the scan/OCR proposals; record agreement or a cited
amendment/discrepancy. The attachment never replaces the scan or its OCR record
in place.

On a black-and-white corpus this alone can justify the phase. It also produces a
measurement nothing else can: comparing attachment-derived values against
scan-derived values for the same document **quantifies what the scanning cost**,
in the client's own numbers rather than in general claims about OCR accuracy.

### 2. Attribution gap-filling

Emails carry ACK numbers, job numbers, and PO references in subject lines and
bodies. For dollars stranded at rank 8 or `unresolved` in
`references/attribution.md`, email is often the only remaining evidence.

Subject lines are disproportionately useful here — they tend to carry the
reference number in a stable position across a thread.

### 3. Missing payment evidence

Remittance advices, payment confirmations, and settlement threads close aging
items that the document corpus leaves open. This directly reduces the apparent
open-receivables balance that Phase 4 reports.

### 4. Selling location evidence

Signature blocks carry office addresses. Sender identity maps to territory. This
resolves rank 6 in the selling-location hierarchy, which is otherwise one of the
weaker links.

### 5. Contact enrichment

Names, titles, phone numbers, current email addresses per account. The document
corpus barely contains this and the CRM genuinely needs it. Often the fastest
visible win from the phase.

### 6. Order-to-cash narrative

Quote → ACK → change order → dispute → settlement. The documents record outcomes;
the threads explain them. Useful for investigating anomalies the analytics
surface, less so as a standing data source.

---

## What email must not do

**Email is corroborating evidence. It never overrides a source document.**

An email stating "invoice 2231 is for $5,500" does not correct an invoice that
says $5,507.50. It raises a discrepancy for human resolution. Emails contain
estimates, drafts, stale figures, and people being approximately right — treating
them as authoritative would inject exactly the kind of confident wrong number the
rest of the pipeline exists to prevent.

**The exception:** a PDF attachment is a *document*, not an email. It enters the
document pipeline at Phase 2 and is treated with full authority.

All email-derived values carry `source = 'email'` and are **excluded from the
document-based accuracy statement**, which is computed on documents only. Mixing
them would make the confidence bound uninterpretable.

---

## Screening comes first

Before any content processing, agree in writing:

- Which mailboxes are in scope
- What date range
- Exclusion filters for personal correspondence, HR matters, legal advice, and
  anything privileged
- Retention and residency constraints on the extracted content
- Who reviews flagged-but-uncertain items

Screening **precedes** ingestion. Processing first and filtering afterwards means
the excluded content has already been read, indexed, and copied, which is the
thing the screening was supposed to prevent.

The output of this step is a written scope agreement, not a configuration file.

---

## Process

**Ingest.** PST, MBOX, EML, or Graph API export. Normalize to: message id, thread
id, in-reply-to, sender, recipients, date, subject, body, attachment list.

**Thread reconstruction and deduplication.** Reply chains duplicate body text
extensively — a twelve-message thread may contain the first message twelve times.
Without deduplication the corpus inflates several-fold and reference extraction
returns the same fact dozens of times, which then looks like corroboration and
is not.

**Attachment extraction and matching.** Extract every attachment, hash it, match
against the document corpus by content hash and by reference number. Matched
attachments enter the document pipeline as additional, separately hashed
sources and are processed through Phases 2–3 normally. They may provide a
higher-fidelity comparison view, but they never replace or rewrite the scanned
source.

**Disagreements between attachment-derived and scan-derived values are findings,
not silent overwrites.** Log every one. The aggregate is the scan-quality
measurement described above.

Unmatched attachments are also valuable: an invoice that exists only as an email
attachment and never made it into the scan batch is a **completeness finding**,
and it may explain a sequence gap Phase 4 already flagged.

**Reference extraction.** ACK numbers, job numbers, invoice numbers, PO numbers,
cheque numbers, and amounts — by pattern match against the formats in the
attribution reference table, plus a language-model pass for references stated in
prose ("the Henderson job" rather than "ACK-2022-0847"). The prose pass needs the
party master to resolve informal names.

**Entity linkage.** Sender and recipient domains and addresses matched to the
resolved party master. Extract contacts for CRM load.

**Evidence linkage.** Each extracted reference becomes an `email_evidence` row
linking a message to a document, attribution, or party, carrying the quoted
supporting text and a confidence. The quote matters: an attribution justified by
"see email 4471" is not reviewable, one justified by the sentence that said it is.

---

## Deliverables

1. Email-derived contact list, formatted for CRM load
2. Attribution gap-fill report — coverage before and after, by method
3. Recovered payment evidence and its effect on aging closure
4. **Attachment-versus-scan discrepancy report** — the scan-quality measurement
5. Unmatched-attachment register — documents that exist only in email
6. Revised Completeness Report for the email-covered window, clearly bounded to
   that window

---

## What makes deferral free

Phase 7 can be executed months after handoff without redesigning or repeating
Phases 0–6, provided those phases leave three things in place. The later email
screening, ingestion, provider, review, and storage work still has its own cost:

1. **A `source` field on every value.** Without it, email-derived and
   document-derived data cannot be distinguished after the fact, and the accuracy
   statement becomes uncomputable.
2. **An `attribution` table that accepts new evidence rows without schema
   change.** Attribution is append-and-supersede, not overwrite.
3. **An unattributable register that persists** rather than being cleared at
   handoff. It is the worklist Phase 7 operates against — if it was discarded,
   the gap-filling has no target and the work has to be re-derived.

All three are in the standard design. Confirm they survived into the delivered
schema before telling a client that deferral is free.
