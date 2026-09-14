# Sampling QA Design

The purpose is not to feel confident about the data. It is to produce a
**defensible one-sided statement about monetary overstatement**. The implemented
MUS calculation does not bound understatement and does not prove corpus
completeness. Those limitations must travel with the result; "we spot-checked it
and it looked fine" is not an acceptable substitute.

Both schemes below draw **only from records marked `auto_accepted` whose
arithmetic status is explicitly `proved` or `not_applicable`**. Missing or
failed arithmetic status and every exception sit outside the sampling frame and
are worked exhaustively — see `references/workflow-gates.md`, Phase 3.

## Contents

- [Monetary unit sampling](#monetary-unit-sampling-value-accuracy)
- [Stratified attribute sampling](#stratified-attribute-sampling-field-accuracy)
- [Sample sizing](#sample-sizing)
- [Escalation](#escalation)
- [The golden set](#the-golden-set)
- [The held-out answer key](#the-held-out-answer-key)
- [Reporting](#reporting-the-result)

---

## Monetary unit sampling (value accuracy)

Selection probability proportional to document value. The sample naturally
concentrates on the dollars that matter — a $10,000 invoice is 200× more likely
to be drawn than a $50 one, which is exactly right, because that is where the
risk lives.

### Method

1. **Certainty stratum.** Every document at or above the materiality threshold is
   examined with certainty, not sampled. The planner also moves any item larger
   than the provisional sampling interval into the certainty stratum and
   recalculates the interval, preventing one document from receiving multiple
   selection points. Set materiality in Phase 0.
2. **Systematic PPS selection** over the remainder. Sampling interval
   `I = remaining_population_value / n`. Draw a random start in `[0, I)`, then
   select the document containing each cumulative-value point at `start`,
   `start + I`, `start + 2I`, …
3. **One hash-bound review outcome per selected document.** The
   `sample_review_results_v1` artifact names the plan's
   `sample_plan_sha256` and contains exactly one outcome for every certainty
   and MUS selection. Each outcome requires `document_id`, the exact
   `recorded_value`, `audited_value` for reviewed results, and one of
   `reviewed_correct`, `reviewed_error`, `abstained`, or `failed`. Abstained,
   failed, missing, duplicate, foreign, stale-plan, or contradictory outcomes
   block the accuracy statement. For an
   overstatement, `taint = (recorded_value − audited_value) / recorded_value`.
   Understatements are retained separately and are not projected by this
   one-sided calculation.
4. **Projection.** `projected_overstatement = Σ(positive_taint) × I`, plus the
   actual overstatement observed in the fully examined certainty stratum.
5. **Upper bound.** Add basic precision `= I × confidence_factor` and the
   incremental allowance for each finding. At 95% confidence with zero errors the
   factor is 3.0; each additional finding adds a smaller increment.

`scripts/sampling.py` implements this. It reports projected overstatement, the
95% upper overstatement bound, certainty-stratum overstatement, and observed
understatement separately. If the number of overstatement findings exceeds the
checked-in confidence-factor table, it reports no upper bound and requires a
qualified sampling specialist or 100% review.

### Why not simple random sampling

A random sample of documents treats a $50 freight bill and a $50,000 invoice as
equally important. In a corpus with a long tail of small documents — which is
every corpus of this kind — most of the sample lands on values that could be
wrong by 100% without moving the totals. MUS puts the effort where the dollars
are.

---

## Stratified attribute sampling (field accuracy)

Answers a different question: where to concentrate field-level review. The
checked-in script creates the reproducible selection plan and a zero-finding
rule-of-three bound for each planned stratum. It does not ingest attribute
findings or calculate observed field accuracy; those results belong in a
separate reviewed QA artifact.

### Strata

- Document type
- Year
- `has_handwriting` true / false
- Processing branch (A / B / B-rescan)
- JBIG2-suspect status as an oversampling trigger

### Oversampling

**Handwriting-present documents are oversampled** relative to their population
share:

| Branch | Multiplier | Reason |
|---|---|---|
| A | 4× | Detection recall is stronger; recognition still needs additional review |
| B | 9× | Detection recall is materially lower; unflagged annotations are expected |
| B-rescan | 4× | Improved source quality reduces but does not remove the risk |

JBIG2-suspect strata use at least a 5× multiplier. Vendor-rank,
image-quality/scan-bucket, format-change, attribution-key, and period-edge strata
may be valuable for a client engagement, but the current script does not derive
them. Add them to a client-approved plan and regression tests before claiming
those comparisons were computed.

---

## Sample sizing

Rule of three: with zero errors observed in *n* trials, the upper 95% bound on
the true error rate is approximately `3/n`.

| n | Upper bound on true error rate (zero errors observed) |
|---|---|
| 30 | ~10% |
| 60 | ~5% |
| 100 | ~3% |
| 300 | ~1% |
| 600 | ~0.5% |
| 1000 | ~0.3% |

The rule-of-three figure is a planning/zero-finding attribute bound. The current
script does not calculate a binomial bound when attribute errors are observed.

Size each stratum from the tolerable error rate agreed in Phase 0. Handwriting
strata get the largest *n* — that is where the errors are, so that is where the
statistical resolution should be.

### Practical note

Sample sizes are driven by the tolerable error rate, **not by corpus size**.
n=300 gives the same ~1% bound whether the corpus is 10,000 pages or 500,000.
This surprises clients who expect QA cost to scale linearly with volume, and it
is worth stating early — it is what makes a large backfile tractable.

---

## Escalation

**If the completed MUS upper overstatement bound exceeds tolerance, take the
affected population to 100% review.** Invalid, duplicate, or unmatched findings
and confidence-factor-table exhaustion block the calculation instead of
producing a qualified-looking number.

Not accepted with a caveat. Not noted in a footnote. Fully worked.

This is the pressure valve that makes sampling-only QA defensible: sampling does
not eliminate full review, it *decides where full review is required*. A client
who understands this understands why the approach is sound. A client who thinks
sampling means "we only checked some of it" has been explained the method badly.

Re-sample the stratum after remediation to confirm it now clears.

---

## The golden set

Build once, carefully: **500–1,000 pages**, hand-labelled, spanning

- every document type in the taxonomy
- every distinct vendor format
- both processing branches
- heavy handwriting representation (over-weighted relative to the corpus)
- known-hard cases: poor scans, skewed pages, overlapping stamps, multi-page
  documents with continuation totals

This is permanent regression infrastructure, not a one-time QA artefact.

**Benchmark every pipeline change against it before deployment** — a new OCR
model version, a revised rule, a changed prompt. Without it, an engine upgrade
can silently degrade extraction across five years of data with no signal that
anything changed. That failure is discovered months later, by which point the
provenance of every affected figure is in question.

Version the golden set alongside the pipeline. When labels are corrected, record
why.

---

## The held-out answer key

The golden set above is hand-labelled and expensive, which is why it gets built
once and then quietly skipped whenever a method needs deciding *now*. The answer
key is the cheap complement: ground truth the corpus already contains, extracted
without a human labelling anything.

**Method.** Find cells a source independent of the method under test can settle
outright. On the commission run that was document arithmetic: wherever
`commission_amount`, `commissionable_amount` and `stated_commission_rate` all
appear on a line and two of them are agreed, the third is determined. That
settled 293 cells. Those cells are then *withheld* from the method being scored
-- it never sees the arithmetic -- and the correct value is placed at random
among the candidates so a method that always picks the first, or always amends,
scores at chance rather than by accident.

**Report the baseline next to the score.** With the answer randomly placed, a
strategy of always proposing an amendment scored 53%. A number like "87% correct"
means nothing until the reader knows that guessing scores 53% and not 8%.

**Score before adopting, never after.** This is the whole point, and it is worth
being concrete about what it bought:

| Method | Cells it offered to settle | Accuracy | Outcome |
| --- | --- | --- | --- |
| Multi-vendor vote, 2 vendors | 6,951 | 96.3% | adopted |
| Multi-vendor vote, 3 vendors | subset of the above | 100% | adopted |
| Paid LLM re-reading | 7,368 | 87.1% | rejected -- worse, and $58-116 |
| Corpus-frequency tiebreak | 1,880 | 58.1% | built, measured, deleted |

The corpus-frequency method is the reason this section exists. It resolved 1,880
fields, it ran for free, and every one of its outputs looked plausible in a
spreadsheet: it picked the most common value in the corpus for the field. Scored,
it was barely better than a coin flip, and its failures were confident and
systematic -- it chose `NORTHGATE CO.` as `customer_account_number` because that
string is everywhere. Without a key it would have shipped as 1,880 resolutions
and nobody would have had a way to argue with it.

**A resolution count is not a result.** Any method can report a large number of
fields settled; the only question is what fraction are right, and a method that
cannot be scored cannot be adopted no matter how much it resolves. Retain the key
as an artifact (`controls/adjudication_answer_key_*.json`, keyed
`document_id|field`) so the next method is scored against the same cells rather
than a fresh set chosen after the fact.

**What it does not tell you.** The key is drawn from cells arithmetic can settle,
which are numeric, on well-formed lines, and therefore easier than the corpus
average. Treat the score as an upper bound on a method's performance, not an
estimate of it, and never quote a rate measured on one population as a comparison
against a rate measured on another -- a model scored 65.6% on a general sample
and 43.4% on the pages that actually carried open fields.

---

## Reporting the result

The accuracy statement has four parts. Deliver all four; the third and fourth are
the ones clients actually need and the ones most often omitted.

1. **Projected overstatement**, in dollars, with the one-sided 95% upper bound;
   separately disclose observed understatement and the fact it was not
   projected.
2. **Field-level results** from the separately reviewed attribute sample by
   stratum; do not infer these from the planning artifact alone.
3. **What was escalated** to 100% review and what that review found.
4. **What remains open** — the exception register, quantified.

Pair it with the Completeness Report. Accuracy without completeness is a
half-answer: knowing the captured numbers are right says nothing about the ones
that were never captured.

Never soften these figures to make the deliverable sound better. The honest
accounting of limits is the deliverable's main value — a dataset that claims
perfection is one nobody can audit.

## What a document is worth

The monetary-unit frame needs a value per document. That value comes from
`scripts/document_value.py`, which prefers a printed header total and falls back
to the signed net of the document's own line items when the document states no
total of its own -- which a commission statement never does. The sample plan
reports `value_basis` and `derived_value_documents` so a projection built mostly
on derived values can be read as what it is.

A document with neither is excluded with a reason rather than valued at zero: a
document worth an unknown amount and a document worth nothing are different
facts, and only one of them belongs outside the frame silently.
