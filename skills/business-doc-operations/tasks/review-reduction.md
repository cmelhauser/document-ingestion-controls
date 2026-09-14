# Reducing the review queue

Everything here is **proposal-only** and every path retains the underlying queue
items. The goal is to give a reviewer less to read, never to make findings go
away.

All paths use `scripts/client_review/protection.py`: an unrecognized reason **fails
closed**, protected categories cannot carry forward, and a proposed update must
target the reviewed field and cite retained evidence.

## What can never be reduced

Arithmetic, provider/schema, reassembly, handwriting, identity, review-flag,
missing-field, and unresolved-evidence findings. No threshold, grouping, batch
decision, or model output clears one. If something appears to have cleared one,
stop and read the protection reasons — that is a bug, not a shortcut.

## Build the client context first

Card review and the full-dataset agent both take `--context`, and so do three of
the relationship lanes. Build it from whatever client material the engagement
holds -- comments, notes, call summaries -- before any of them run:

```bash
python scripts/client_input_comments.py NOTES.csv \
  --records RUN/controls/consensus.json --out RUN/client/context.json
```

Omitting it does not fail loudly. The lanes run without their evidence anchor or
do not run at all, and it surfaces as a thin result rather than an error.

**Run the relationship lanes before the gate**, not after: every exception they
resolve is a question the client never has to answer, and a queue built before
them carries findings that would have been settled.

## Card review

```bash
python scripts/client_review_llm.py RUN/review/final_queue.json --enable \
  --context RUN/client/context.json --max-cards 500 \
  --out RUN/review/cards.json --exceptions RUN/review/card_exceptions.json \
  --raw-dir RUN/review/raw/cards
```

One call per **card**, not per queue item: a queue of 82,801 items grouped into
718 cards is 718 calls. `--resume-from` continues a stopped run against hash-bound
queue, context, config, card, and raw state — all of them must match, and it
re-verifies every prior card's request hash and raw response before trusting it.

`--auto-accept-llm-proposals` marks eligible proposals accepted. This is a
workload control, not a confidence claim.

**In this lane the threshold is not a term.** `--auto-accept-threshold` is
validated and recorded in the artifact, but the gate is protection taxonomy, an
explicit `propose_resolution` decision, and a proposal bound to the reviewed
field — none of which a model can self-report. Raising or lowering the number
changes nothing here. The same environment variable *does* gate
`client_review_cross_record.py`, which rejects a match `below_auto_accept_threshold`;
that is the lane the confidence floor describes.

Expect a low yield, and size the lane accordingly. On a corpus where most queue
items are a single reading — one engine read the field, so nobody disagreed —
there is nothing for a reviewer to reduce. One measured run: 82,801 items, 718
cards, `propose_resolution` on 88 items (0.1%), 29 covered items, **zero**
auto-accepted. A reviewer cannot resolve a field only one engine ever read; that
needs a second extraction or the client.

`--final-run-include-protected-arithmetic-reassembly` adds arithmetic and
reassembly **next-step proposals** to the final client-review flag. Those
findings stay protected and review-required; the option changes what the reviewer
is shown, not what is decided.

## Full-dataset agent

```bash
python scripts/review_agent.py --enable \
  --context RUN/client/context.json --iterations 3 \
  --out RUN/review/agent.json --exceptions RUN/review/agent_exceptions.json \
  --raw-dir RUN/review/raw/agent
```

Bounded reasoning over analysis slices, not the whole dataset at once.
`--max-slice-context-bytes` and `--max-evidence-per-slice` bound each slice; work
that cannot fit is an explicit exception.

## Grouping

```bash
python scripts/review_grouping.py RUN/review/final_queue.json \
  --consensus RUN/controls/consensus.json \
  --classifications RUN/controls/classification_consensus.json \
  --out RUN/review/grouping.json
```

Groups repeated root causes. It removes no source item — a group is a view over
items that all remain individually present.

**Pass `--classifications`.** The extraction consensus writes
`document_type: "unknown"` wherever its lanes could not agree, which on a real
corpus is most of it — that is consensus correctly declining to claim a type,
and it is the reason `classification_consensus.py` exists as its own lane.
Without it, grouping labels those documents `unclassified_document_family` and
falls back to guessing a family from words in the reason string. On the
commission run that meant 691 documents in one undifferentiated group and 442
`purchase_order`, `invoice_like` and `statement_like` groups invented out of
prose. Supplying it: 127 unclassified, and three times as many questions that
clear their group outright.

## Table-audit findings group by concern, not by wording

The audit lane writes one sentence per cell, and no two are the same. Grouping on
the sentence gives one group per finding: on a real corpus 11,251 audit findings
produced 4,455 groups of one, and a third of the client queue arrived as
individual prose.

`grouping.audit_concern` sorts them into nine concerns -- totals and to-date
rows, handwriting, clipped values, missing headers, conflicting readings, unclear
field association, pages populated contrary to the concern, agreement that does
not resolve meaning, and values not visible -- with a fallback that is never the
sentence itself. Adding a concern means adding its question template too, or the
pack asks about it generically; `items_behind_generic_wording` reports that.

Two of the nine are usually already answered elsewhere in the pack. Check before
asking again.

## Safe consolidation

The orchestrator for the whole reduction:

```bash
python scripts/safe_review_consolidation.py --enable --run-dir RUN \
  --queue review/final_queue.json --client-review review/card_review.json \
  --context client/context.json --output-dir review/post_review_consolidation \
  --out safe_review_consolidation.json
```

`--carry-forward` with `--carry-forward-threshold` allows eligible proposals to
carry forward; protected categories never do. `--disable-grouping` and
`--disable-client-package` turn off stages you do not want. Paths are resolved
**relative to `--run-dir`**, which is why the other options take bare filenames.

**`--enable` re-runs the proposal lanes; it does not reuse earlier ones.** This
command is a self-contained orchestration: with `--enable` it shells out to
`client_review_cross_record.py` and `review_agent.py` and writes *its own*
`client_review_cross_record.json` and `review_agent.json` inside `--output-dir`.
Artifacts you produced earlier in the run -- `cross_record_01.json`,
`review_agent_01.json` -- are ignored. Budget for a second cross-record pass:
on one corpus that was 553 batches and about $12.

Without `--enable` it runs nothing and instead *requires* those three inputs to
already exist at its own expected names -- `<output-dir>/client_review_cross_record.json`,
`<output-dir>/review_agent.json`, and the `--client-review` file -- or it refuses.
Placing artifacts there by hand is possible and free, but the consolidation then
cites inputs an operator positioned rather than ones a lane produced; prefer
`--enable` on any delivery-bound run and keep the provenance chain intact.

**Give `--run-dir` an absolute path.** The sub-lanes are launched with the
repository root as their working directory, so a relative `--run-dir` makes the
queue path resolve against the repository instead of the run, and the first lane
dies with `No such file or directory: 'review/final_queue_03.json'`.

**It refuses to re-run over its own existing outputs.** Rerunning after a failure
means a new `--output-dir`, not a cleanup of the old one -- the refusal names
every file that blocks it.

**`--disable-client-package` stops it short of issuing the workbook.** Leave it
off only when you intend this command to produce the client-facing pack, which
is step 8 of the post-gate sequence and the end of the run.

## Simulated client comments

```bash
python scripts/ai_simulated_client_review.py RUN/review/final_queue.json --enable \
  --evidence RUN/controls/consensus.json --evidence RUN/controls/attributed.json \
  --context RUN/client/context.json \
  --out RUN/review/simulated.json --exceptions-out RUN/review/simulated_exceptions.json \
  --raw-dir RUN/review/raw/simulated \
  --comments-csv RUN/review/simulated.csv --comments-xlsx RUN/review/simulated.xlsx
```

This helps an operator prepare the review discussion. It is **not** a Phase 4 or
4Q control.

- Every visible result says **"AI client reviewed (simulated; not client
  authorization)"**.
- `--evidence` is required and repeatable. A quote must occur inside one scalar
  value of a source record assigned to that item.
- Every item gets a source-cited simulated comment **or** an explicit exception.
- It never changes an issued or returned workbook, impersonates the client,
  authorizes a fact, applies an amendment, clears a protected finding, or
  advances canonical/CRM staging.
- `--resume` requires the checkpoint hashes to match the queue, evidence, and
  complete resolved configuration. `--retry-exceptions` produces a **new
  overlay**; it never overwrites or silently merges the original run.

## Approval projection

`simulate_client_approval.py` builds a non-production approval projection for
stress tests. It is never a client decision and never a gate.

## Then what

Reduction produces proposals. The queue is still the queue until an authorized
client decision is applied and the affected controls are rerun — see
[`client-review.md`](client-review.md).
