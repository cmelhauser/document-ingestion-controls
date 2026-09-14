# Cross-checking: corroboration and independent verification

Five lanes exist to check a reading against something other than itself. They
are easy to confuse, because all five "compare things", and using the wrong
one produces a comparison that proves nothing. A sixth, `apply_corroboration.py`,
does not compare anything -- it applies an authorized client decision to what
the second lane found.

| Lane | Compares | Answers |
|---|---|---|
| `google_document_ai_adapter.py` | An independent OCR/table reading of the same page | "Does another engine see the same cells?" |
| `independent_table_reconcile.py` | Mapped source rows vs those independent cells | "Do my mapped rows survive an independent reading?" |
| `independent_corroboration.py` | A single reading, or a disagreement, vs that independent reading of the page | "Is the value this engine read printed anywhere on the page another engine read?" |
| `client_review_cross_record.py` | One review item vs other retained evidence in this run | "Does anything already in this corpus support this item?" |
| `client_review_cross_packet.py` | In-packet proposals vs neighbouring packets | "Does this relationship hold outside the packet that proposed it?" |

**None of them is consensus.** Consensus is `consensus.py` over two genuinely
independent extraction lanes, and nothing on this page substitutes for it. None
of them clears a control, authorizes a fact, or closes a review item.

## Document AI corroboration

Disabled by default; it makes live provider calls.

```bash
python scripts/google_document_ai_adapter.py RUN/pages/ingestion_manifest.json --enable \
  --processor-id PROCESSOR \
  --out RUN/providers/docai.json --adapter-out RUN/providers/docai_handoff.json \
  --exceptions RUN/providers/docai_exceptions.json --raw-dir RUN/providers/raw/docai
```

Use a processor version only when it is a version that belongs to the selected
processor. The trial's configured `FORM_PARSER_PROCESSOR` uses its validated
default version, so leave `--processor-version` blank rather than pinning an
OCR-only or custom-extraction version that the processor does not serve. The
adapter retains the effective processor configuration in its handoff.

It corroborates a **conditional page-local audit** and deterministic
reconciliation. It cannot map a source label to a canonical field, and it cannot
clear a financial or identity fact.

## Table reconciliation

```bash
python scripts/independent_table_reconcile.py RUN/controls/table_rows.json \
  --registry registry/mappings.json --document-ai RUN/providers/docai_handoff.json \
  --out RUN/controls/table_reconcile.json \
  --exceptions RUN/controls/table_reconcile_exceptions.json \
  --adapter-out RUN/controls/table_reconcile_handoff.json
```

`--document-ai` is repeatable; supply every independent handoff you have.

**Read `coverage` before reading the result.** This is the lane where a clean
result most often covers nothing: handoffs supplied, nothing comparable, zero
mismatches reported. The refusal `independent_reconciliation_produced_no_comparison`
exists for exactly that case. If you see a pass, confirm rows and cells were
actually compared.

## Corroborating a single reading

Consensus can only accept a field two independent vendors both read. Where one
engine read it and nothing else did, the finding is `single_engine` -- an
absence, not a disagreement, and no tolerance closes it. This lane asks whether
the value appears in an independent extractor's own reading of the same page.

```bash
python scripts/independent_corroboration.py RUN/controls/consensus_exceptions.json \
  RUN/providers/docai_handoff.json RUN/providers/docai_recovery_handoff.json \
  --out RUN/controls/independent_corroboration.json \
  --exceptions RUN/controls/independent_corroboration_exceptions.json \
  --reference-engine ENGINE
```

**Pass every independent artifact the run retained.** A recovery pass routinely
covers pages the first pass did not; on one run the main handoff carried tables
for 459 pages and the recovery pass for 677, and measuring against either alone
understated corroboration by roughly half.

Matching is **page-scoped**, not cell-scoped, because Document AI merges whole
logical rows into a single cell. That is what makes this lane find evidence
`independent_table_reconcile.py` cannot -- and it is also its limit: presence
proves the value is printed on the page, not that it belongs to that row's
column. It defeats a misread and not a misattribution. Read `occurrences`
before leaning on any single corroboration: a value appearing once is nearly as
located as a cell match, and one appearing twenty times says very little.

This is not vendor agreement, must never be recorded as consensus, and resolves
nothing on its own.

## Applying that corroboration

Corroboration resolves nothing by itself. Accepting a value on it is a separate
step and it runs **only** under a named client decision, because it accepts a
reading on weaker evidence than consensus requires.

```bash
python scripts/apply_corroboration.py RUN/controls/consensus.json \
  RUN/controls/consensus_exceptions.json \
  RUN/controls/independent_corroboration.json \
  --authorization CLIENT_DECISION_ID \
  --out RUN/controls/applied_corroboration.json \
  --records-out RUN/controls/corroborated_records.json \
  --exceptions RUN/controls/corroboration_remaining_exceptions.json
```

The decision being applied is this, and it must be on the record before the lane
runs: *a value one engine read, which an independent non-LLM extractor also read
from that same page, may be accepted without a second model vendor — accepting
that this defeats misreads and not misattribution.*

`--unique-occurrence-only` is the narrower and safer form: it accepts only
values appearing exactly once on their page, which is nearly as located as a
cell match.

Every accepted field keeps an `acceptance` block naming the authorization and
`vendor_agreement: false`, and a `superseded_consensus` block holding what it
replaced. Nothing is overwritten: a field consensus already accepted is left
alone, because consensus is the stronger evidence. Hand the gate the lane's
`--exceptions` output in place of the consensus exception artifact; that is
where the reduction shows up.

## Cross-record search

Disabled by default. It searches producer-bound source evidence, or review-clear
canonical document facts, for support for a review item.

```bash
python scripts/client_review_cross_record.py RUN/review/final_queue.json --enable \
  --context RUN/client/context.json \
  --out RUN/review/cross_record.json --exceptions RUN/review/cross_record_exceptions.json \
  --raw-dir RUN/review/raw/cross_record
```

Every match must bind to a hash and a page. `--auto-accept-llm-proposals` with
`--auto-accept-threshold` may mark eligible proposals accepted — protected
findings are never eligible, and the threshold is a workload control, not a
confidence claim.

## Cross-packet verification

Disabled by default, and it runs **after** the in-packet iterative artifacts and
the deterministic graph exist. It does two separable things:

1. **Discovery** — finds links for documents that stayed unresolved in-packet.
2. **Verification** — independently checks proposals that *were* resolved
   in-packet against retained documents sharing a source-visible identifier.

```bash
python scripts/client_review_cross_packet.py RUN/client/context.json \
  --records RUN/controls/consensus.json \
  --primary RUN/relationships/primary.json --buddy RUN/relationships/buddy.json \
  --graph RUN/graph/graph.json \
  --out RUN/relationships/cross_packet.json \
  --exceptions RUN/relationships/cross_packet_exceptions.json \
  --raw-dir RUN/relationships/raw/cross_packet
```

Rules that decide most questions here:

- The configured primary and buddy providers must resolve to **two different
  model vendors**, checked before any request is made. Provider *names* are not
  enough: `openrouter` serving `openai/gpt-oss-120b` is OpenAI.
- A verification may flag `close_needs_review`, `conflict`, or `unsupported`. It
  **never rewrites** the proposal it checked. The prior result stands beside the
  new finding.
- Follow-up passes may inspect only **newly exposed unresolved neighbourhoods**,
  and stop when a completed pass adds no new source-backed, buddy-confirmed
  relationship. That is the documented convergence threshold, not a budget.
- Cap-limited or identifier-less coverage is deferred **explicitly**. Silence is
  not coverage.
- `--retry-exceptions` selects only retained provider-failed neighbourhoods, as a
  new overlay.

Rebuild the graph as a **new overlay** afterwards; do not edit the existing one.

## Qualifying a second reader

A second reader exists to corroborate, so the question is what values it
returns. Row counts do not answer it, and this run paid to learn that: a
candidate matched the reference engine's rows almost exactly -- 172 against 171
over four sample pages, 67 to 67 and 54 to 54 on dense ones -- and on that
evidence a 716-page corpus was read. Measured by value, the same lane had failed
to read 42% of the reference engine's values *anywhere* on the page and filed
the right value in the right field 34% of the time. It misread digits inside
financial amounts, which is worse than abstaining: a wrong amount does not stay
silent, it disagrees, and every disagreement it manufactures costs a person an
adjudication.

Run `engine_agreement.py` on a handful of matched pages before committing a
corpus:

```
python scripts/engine_agreement.py REFERENCE_HANDOFF CANDIDATE_HANDOFF --out AGREEMENT
```

It reports three rates and only two of them mean anything:

| rate | reads as |
|---|---|
| row-count ratio | shape only. Never corroboration, and shown so it can be dismissed. |
| **value recall** | did the candidate read the page at all, wherever it filed what it read |
| **field-exact** | the right value, in the right field, on the same row |

High recall with low field-exact means the engine reads the page but not the
table; it will collide with the reference engine on field identity rather than
on content. Low recall means it cannot read the page, and nothing downstream
recovers what it never saw. Compare only pages both engines actually read and
only pages read the same way -- a rate over a different population, or against
an engine given the page image while the other got extracted text, is not a
comparison.

Measured on this corpus against `gpt-5.6-luna`, every candidate on the same 40
pages so the rates are comparable:

| candidate | value recall | field-exact | page set |
|---|---|---|---|
| `google/gemini-2.5-flash` | 59.9% | 26.4% | general 40 |
| `google/gemini-2.5-flash-lite` | 46.9% | 25.5% | general 40 |
| `mistralai/codestral-2508` | 43.4% | 22.3% | the 40 hardest |
| `x-ai/grok-4.20` | 28.3% | 15.8% | general 40 |
| `mistralai/codestral-2508` | 65.6% | 27.4% | general 50 |

**Read the page set column before the rates.** Codestral appears twice on
purpose. Over a general 50-page sample it was the strongest candidate measured
at 65.6% recall; over 40 pages selected because they still carried an unaccepted
field it fell to 43.4%. Same engine, same corpus, same reference — a sample
drawn from the pages a pipeline has already settled is easier than the pages it
has not, and a candidate qualified on the easy sample is qualified for work that
is already done. Draw the qualification sample from the population the reader
will actually be asked to read.

Reading the native text layer instead of the page PDF cost `gemini-2.5-flash`
more than half its recall -- 30% and 8% field-exact -- because the extracted text
loses the column grid the table depends on.

Two things that decided a real lane choice here:

**The reader in force was the worst of them.** `x-ai/grok-4.20` returned 28.3%
of the primary's values, and that is the direct cause of 48,534 fields carrying
a single reading: consensus can only agree with a value the second engine
actually returned. A second reader that abstains produces no corroboration and
no disagreement, while looking like a lane that ran. It cost nothing to find --
the measurement is over retained artifacts -- and it had gone unmeasured for the
length of the run.

**Rank candidates by output price, and measure rather than project.** These
pages cost 3.91M prompt tokens against 8.25M completion, so the completion rate
sets the bill. A list-price estimate of $3.69 for `gemini-2.5-flash-lite`
measured at $5.33, because it emits half again as many output tokens as
`gemini-2.5-flash`; `codestral-2508` measured about a fifth of that again. Quote
a measured per-page cost from a probe, never a projection.

The measurement clears no control and authorizes no lane.

### A third vendor is evidence, not a third lane

`consensus.py` admits three consensus lanes -- `consensus_primary`,
`consensus_secondary` and `consensus_tiebreaker` -- and refuses a duplicate.
Until 2026-09-07 only the first two could be *produced*: `llm_adapter.py --lane`
did not offer the third, so a lane the consumer accepted had no command that
could emit one, and the only way to read a corpus with a third vendor was to
label it the secondary -- the lane a later reader trusts to be the second of a
pair. It is offered now, and it has no default provider: a tiebreaker that
quietly resolves to the vendor already reading another lane is one engine
compared with itself.

Where a third vendor is not wanted as a lane, it enters through
`independent_corroboration.py` as evidence, whose output is corroboration by
value presence and explicitly **not** vendor agreement, and needs its own named
client decision before acceptance.

### Conditioning is compared on the handoff, not on the prompt

`consensus.py` refuses a pair whose `corpus_context` hashes differ, and it reads
that field off the handoff. Until 2026-09-07 the Vertex adapter never wrote it.
The reading itself was conditioned -- `extraction_instructions()` appends the
context and it is part of the lane's cache key -- so a correctly conditioned
Vertex lane recorded `None` and was refused as conditioned differently from the
OpenAI lane it was built to pair with. The lane had done the right thing and
could not prove it, and the cost of finding out was a full corpus read.

Two lessons, and the second is the general one:

* Before pairing lanes, compare `corpus_context.sha256` across every handoff.
  They must be equal, and none may be `None` when another is set.
* **A field a control compares on is a field every producer must write.**
  Whenever a consumer gains a check, every adapter that can feed it needs the
  field, or the check silently fails closed on lanes that are actually correct.
  `source_read_by` had the same shape: added to the consumer, absent from
  handoffs written before it existed.

Two lanes may share one router only when both declare `source_read_by: model`.
An absent declaration fails closed, because OpenRouter's default PDF handling
can feed one OCR pass to both models -- they would then inherit one set of
errors and are one reading, not two. A handoff written before that field existed
declares nothing and is refused; the fix is a fresh pass on current code, never
an edit to the retained handoff.

## The question to ask of any cross-check

Not "did it find problems" but **"did it compare anything?"** A cross-check with
no comparisons is the same failure as a control that processed nothing, wearing a
more reassuring result.

## Before you adopt a resolution method, score it

A method that settles open fields must be measured before it is trusted, and the
corpus can usually supply its own ground truth for free. Build a **held-out
answer key** — cells an independent source settles outright, withheld from the
method under test, with the correct value randomly placed so a fixed strategy
scores at chance. Report the baseline beside the score.

On the commission run this cost nothing and decided four methods. It adopted the
multi-vendor vote (96.3% on two vendors, 100% on three), rejected paid LLM
re-reading (87.1%, and $58–116), and deleted a corpus-frequency tiebreak that
offered 1,880 free resolutions at 58.1% — a coin flip whose failures were
confident and systematic, and which would otherwise have shipped because 1,880
resolutions look like progress in a summary.

**A resolution count is not a result.** Full method, limits, and what the key
cannot tell you: `references/qa-sampling.md`, *The held-out answer key*.
