# Bounded Adjudication and Intake Improvement Controls

This phase improves first-pass accuracy without allowing the system to hide an
uncertain reading. It operates after intake, consensus, and arithmetic proof.

## Reassembly and duplicate controls

Run `scripts/reassemble_pages.py` against an ingestion manifest. It writes a
new proposal artifact; it never alters source-page provenance or the intake
manifest.

A multi-page group is proposed only when all of the following hold:

1. Consecutive pages carry a complete `PAGE X OF Y` sequence.
2. Every page shares at least one labelled business identifier containing digits.
3. All pages have the same non-null high-signal document type.

Exact retained-artifact duplicate candidates are recorded for review. Near
duplicates are intentionally not excluded automatically.

## Bounded adjudication

Run `scripts/adjudicate.py` after `scripts/arithmetic_check.py`. It currently
handles an intentionally narrow, high-safety case: a rejected `total_amount`
may receive a proposed system amendment only when exactly one independently
extracted candidate equals the subtotal-plus-charges arithmetic result.

- It makes at most two passes.
- It does not modify the raw engine output, consensus output, or source page.
- It emits an amendment proposal with the candidate set and rationale.
- Any non-unique or unprovable result is an open exception for human review.

This is not permission for a model to critique and replace its own answer.
Future rules must require independent evidence such as a PO/reference match,
known-party master, or a cross-document continuity signal.

## Measure before expanding automation

For a client-authorized golden set, report first-pass document-type precision,
reassembly precision, duplicate-review yield, field precision/recall, exception
rate, and wrong-auto-accept rate. Never optimize for hit rate alone: an abstained
record is safer than a confidently wrong record.
