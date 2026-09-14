# The client review lane

One command turns whatever a blocked control produced into a pack a client can
actually answer. Run it whenever a control blocks and you need ambiguity removed
before the run can move on.

**This is the mid-run tool, not the end-of-run one.** To issue the workbook a
client answers at the end of a run, use `client_review_package.py create` against
`safe_review_consolidation.json` -- see [`client-review.md`](client-review.md).
Reaching for this command instead produces a pack that looks finished while
skipping card review, grouping, and consolidation, so the client is asked
questions the reduction would have collapsed or already answered.

```bash
python scripts/client_review_lane.py build RUN/controls/consensus_exceptions.json \
  --out-dir RUN/review/pack_01 \
  --consensus RUN/controls/consensus.json \
  --classifications RUN/controls/classification_consensus.json \
  --resolved-by RUN/controls/classification_consensus.json \
  --manifest RUN/pages/ingestion_manifest.json \
  --scan-profile RUN/profile.json
```

**Pass `--scan-profile`.** Without it a page flagged `near_blank` can be chosen
as a question's worked example, and a client shown a blank page and asked to
confirm a layout will answer about the blank page. That is not hypothetical: one
question covered 285 documents, led with a blank page, and was answered "Blank
Page. Can be ignored." for the whole group and reaffirmed twice. Four of the 285
were blank.

`--resolved-by` is the same option `final_review_queue.py` takes. This lane
builds the queue by its own path, so without it the pack re-asks every
document-type question the gate already reconciled -- 1,121 of them on the
commission run.

Pass `--classifications` for the reason in
[`review-reduction.md`](review-reduction.md): the extraction consensus says
`unknown` wherever its lanes could not agree a document type, and without the
classification artifact the pack asks one undifferentiated question about a
corpus whose type is already settled.

**Re-issuing after a correction?** Pass `--prefill-from` the previous round's
answer artifact. Matching questions come back filled with what the client
already said, matched by group rather than question number -- correcting how
findings are grouped renumbers every question after it, so the number is not a
stable identity and the template/finding pair is. An answer whose question no
longer exists is reported in `carried_answers`, not dropped: on the commission
run those were the two answers to a question the pack should never have asked.

It always writes the same four things:

| File | What it is |
|---|---|
| `client_review.xlsx` | Where the client answers. Start-here tab, one row per question with dropdowns, the exhaustive queue, and a column glossary. **What we read** sits immediately left of **Your answer** and carries the values on record for that group, separated by `|` -- two or more means the engines disagreed and the answer decides between them, one means a single engine read it and the answer confirms it, and blank means the question is about something other than a reading. |
| `client_review_instructions.pdf` | One page per question in plain language, each followed by a full sheet showing the source page it came from. |
| `review_pack.json` | The pack itself: questions, groups, and every queue item behind them. |
| `review_pack_exceptions.json` | Every example page it could not produce. |

## Getting the answers to an authorized change

Answers from this lane are proposals. To reach a change an operator can
authorize they have to enter the compile chain, and that chain reads two
artifacts this lane does not otherwise produce:

```bash
python scripts/client_review_lane.py compile-inputs \
  --pack RUN/review/pack_01/review_pack.json \
  --answers RUN/client/answers_01.json \
  --issued-workbook RUN/review/pack_01/client_review.xlsx \
  --returned-workbook RUN/client/returned_01.xlsx \
  --consolidation-out RUN/review/lane_consolidation_01.json \
  --decisions-out RUN/review/lane_decisions_01.json
```

Then `client_decision_compile.py compile`, `authorize`, and the consumer for the
change type. Without this step a client can answer every question correctly and
nothing can be applied.

Expect most answers not to compile to a batch change, and that is rule 7 working
rather than a fault: an answer to a protected finding -- arithmetic, provider,
handwriting, identity -- informs an item-by-item recheck and never clears the
group. On the commission run 3 of 23 answers were on unprotected groups.

A protected group's answer is recorded as `Defer`, with the choice the client
actually made kept beside it as `client_decision_as_answered`. That is not the
lane softening the answer -- it is the only way the other answers compile at
all. `client_decision_compile.py` refuses an actionable choice on a protected
group by raising, which ends the **whole** compilation, so passing 20 protected
answers through verbatim blocked the 3 unprotected ones that were ready to
apply. Nothing is discarded: the comment is untouched and the original choice is
retained, because what a protected answer authorizes is a recheck rather than a
change.

Two things the queue and pack must carry before any of this works: every queue
item needs a `review_item_id` (`final_review_queue.py` digests one from each
item's own key), and each group's `source_item_ids` must be non-empty, because
`client_decision_compile.py` requires a change's `affected_review_item_ids` to
be non-empty *and* a subset of its group's. A pack built from a queue without
item identity produces groups no change can ever cite.

## What it actually does

Findings become questions in four steps, and each one is a different thing:

1. **Queue** — consolidates every supplied artifact into the exhaustive,
   de-duplicated list. Nothing is dropped here or anywhere later.
2. **Group** — collapses repeated root causes into `(template family, finding
   family)` groups. On a real corpus 881 findings became 4 groups.
3. **Question** — gives each group one plain-language decision, ordered so the
   client answering only the first few still clears the most.
4. **Evidence** — renders the source page behind each question and embeds it.

## The two things to read before you send a pack

**Answering is not clearing.** Rule 7 forbids a batch decision clearing
arithmetic, provider, reassembly, handwriting, identity, review-flag,
missing-field, or unresolved-evidence findings. The lane still asks about them —
the client's answer is exactly what resolves them — but the pack says plainly
that the answer is applied and each affected item then re-checked individually.
Check `batch_clear_permitted` on a question before assuming an answer closes
anything.

**A threshold decides whether to ask, never what to keep.** Every control has its
own floor in `.env` (`CLIENT_REVIEW_LANE_<CONTROL>_THRESHOLD`, default 1). Below
it the lane writes only `review_pack.json` recording that nothing met a
threshold; the items stay in the queue and in the run's exceptions exactly as
before. Raise a floor to reduce noise, never to hide work.

## Options worth a deliberate decision

| Option | Decide because |
|---|---|
| `--manifest` | Without it, questions carry no source page. A question a reviewer cannot check against the document is an assertion. |
| `--consensus` | Sharpens document-family labelling. Omitting it gives broader groups, not wrong ones. |
| `--max-questions` | Caps what you put to a client at once. The rest are retained as `deferred_questions` — a shorter ask, never a shorter queue. |
| `--force` | Builds a pack even when nothing met a threshold, for a deliberate mid-run conversation. It is recorded in the pack; it never lowers a floor. |
| `--render-dpi`, `--max-image-bytes` | An oversized render becomes an explicit exception, not a downscaled guess. |

## When to run it

After any control that blocks, which in practice means after consensus,
arithmetic, and validation; after the business controls; after the handwriting
lane; and before delivery. It is safe to run after every stage — the thresholds
are what stop it producing a pack nobody needed.

Use a fresh `--out-dir` each time. An existing directory is refused, so packs
accumulate as a record of what was asked and when.

## What comes back

```bash
python scripts/client_review_lane.py read-answers RUN/client/returned.xlsx \
  --issued-workbook RUN/review/pack_01/client_review.xlsx \
  --pack RUN/review/pack_01/review_pack.json \
  --out RUN/client/answers.json
```

The returned file is untrusted: it is validated against the immutable issued
copy before an answer is read. The question rows must match in the same order and
every cell outside **Your answer** and **Your notes** must be unchanged, so a
workbook edited anywhere else is refused whole rather than partially trusted.

Read three things in the result. `application` says whether an answer closes its
group or informs an item-by-item recheck — a rule 7 finding is always the latter.
`matched_offered_option` is false when the client wrote something the options did
not anticipate, and that answer is retained exactly as written rather than
discarded or mapped to the nearest option. `unanswered_questions` names what is
still outstanding.

Nothing is applied by either verb. Answers are client decision proposals: the
authorized changes are applied as append-only amendments through the documented
path, and every affected control is then rerun.

Full settings reference:
[`references/runtime-configuration.md`](../../../references/runtime-configuration.md).
The reduction and packaging internals it builds on are in
[`review-reduction.md`](review-reduction.md) and
[`client-review.md`](client-review.md).
