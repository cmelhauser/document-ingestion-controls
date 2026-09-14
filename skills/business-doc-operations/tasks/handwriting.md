# Handwriting (Phase 3H)

Handwritten amendments are where the money moves and where OCR is least
reliable. The recommended review threshold is **$0** — every financial
handwritten amendment is client-review work.

The full subsystem is
[`references/handwriting.md`](../../../references/handwriting.md). The branch you
are on comes from Phase 0.5 scan profiling.

Handwriting OCR shares Application Default Credentials with Document AI: one
`python scripts/reauthorize_google.py` run enables both `documentai.googleapis.com`
and `vision.googleapis.com`, and each lane mints its own short-lived token at run
time. There is no token setting in `.env`.

## Detection then reading, in that order

`google_handwriting_ocr.py` binds returned words **only** to regions supplied by
a separate detector or extraction artifact. It does not decide where the
handwriting is. That separation is the control: a reader that chose its own
regions would confirm its own guesses.

```bash
python scripts/google_handwriting_ocr.py MANIFEST --enable \
  --regions RUN/controls/handwriting_regions.json \
  --out RUN/htr/readings.json --adapter-out RUN/htr/handoff.json \
  --exceptions RUN/htr/exceptions.json --raw-dir RUN/htr/raw --images-dir RUN/htr/images
```

`--regions` is repeatable. `--render-dpi` sets the resolution each page is
rendered at before submission, and `--max-image-bytes` bounds the render — an
oversized render is an explicit exception, not a downscaled guess.

Three things it does that matter:

- It runs on **every retained page**, not only pages with regions.
- A page with no supplied region is recorded as **OCR-complete**, not as
  handwriting-free. Those are different claims and only one of them is supported.
- It retains the rendered page, the full raw response, normalized word geometry,
  the page hash, and every provider or unreadable exception.

**It is one Google provider-group vote.** It cannot validate itself, and another
Google model or role is not independent of it.

## Reconciliation

```bash
python scripts/handwriting_review.py RUN/htr/readings.json \
  --out RUN/controls/htr_decisions.json --exceptions RUN/controls/htr_exceptions.json
```

`--max-passes 2` may propose an amendment; `--max-passes 1` records readings
only. Numeric handwritten fields are held to a stricter rule than free text
because the digit confusion pairs — 1/7, 4/9, 3/5/8, 0/6 — are non-recoverable
errors in a financial dataset.

## The financial escalation, and what it needs

`financial_amendment` is **copied from the region you supply**, never derived.
This lane reads where a detector pointed and has no basis to decide what a mark
modifies. If your regions do not set it, it is false everywhere -- and an unset
flag looks exactly like a corpus with no financial handwriting. On a 716-page
run it was false on all 3,319 annotations for that reason, and `semantic_type`
came back `handwritten_text` for every one, so the typed-financial branch never
fired either.

`handwriting_review.py` now escalates an **untyped numeric reading** on its own,
recorded as `financial_basis: untyped_numeric_reading`. That is not a claim that
the mark is an amendment -- nothing establishes what it modifies, which is what
the reconciler reports as `missing_or_conflicting_semantic_type`. It is routed to
review because it could be one and nothing has established that it is not.

**It fires only after the independence gate.** A numeric reading needs three
independence groups in unanimous agreement, text needs two of three. With a
single provider group every reading becomes `insufficient_independent_agreement`
and never reaches the escalation at all. Qualify a second HTR provider group, or
accept that handwriting is review-only for the engagement and say so in the
delivery.

## What consensus does with it

`consensus.py --handwriting-policy strict` gates handwritten fields;
`comment_only` preserves them as non-blocking observations. Choose `strict` when
handwritten values carry money. HTR reconciliation counts provider **independence
groups**, so two Google models cannot validate one another no matter how they are
configured.

## Exit condition

Extraction-lane annotations are retained with page and region provenance as
non-blocking comments. If the amendment lane ran, every detected annotation is
independently reconciled and either accepted at the branch threshold or
escalated. Every amendment is stored **beside** its printed original, never over
it. Financial amendments remain client-review work regardless of how confident
any reading was.
