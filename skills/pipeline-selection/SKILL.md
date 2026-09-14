---
name: pipeline-selection
description: Decide which pipeline this corpus needs, from the corpus itself — measure the retained artifacts, choose the optional lanes worth running, skip the ones this corpus has no use for, and name the client questions the plan cannot avoid. Use before committing a run to the fixed lane order, when a run is stalling on review volume, or when deciding what to ask the client for. Not an authorization and not a gate.
---

# Choosing the pipeline instead of running it

[`business-doc-operations`](../business-doc-operations/SKILL.md) is the **order**:
what must run before what, and what refuses when it does not. This skill is the
**selection**: which of the optional lanes this particular corpus needs at all.

The two are not in tension. Nothing here reorders the spine, weakens a control,
or makes a lane optional that is not already marked optional in the lane table.
Every invariant in the root [`SKILL.md`](../../SKILL.md) applies unchanged.

## Why this is a separate decision

The pipeline has seventy-six lanes (`run_lane_coverage.py` lists them). A large minority are marked optional
or disabled, and running all of them on every corpus is neither possible nor
sensible — several need client-authorized truth that does not exist yet, and
several cost a provider call per page for evidence this corpus does not contain.

Running none of them is the failure that actually happens. A full trial reached
CRM staging with fourteen lane families never invoked and nothing said so,
because each was individually optional and a lane that never runs writes nothing
to notice. `run_lane_coverage.py` exists to catch that after the fact. This
skill is the same question asked before the spend.

The cost of guessing lands on the client. Every lane not run because nobody
measured whether it was needed becomes review items, and review items are the
client's time. The objective is not "run fewer lanes" or "run more" — it is
**the fewest client questions per dollar resolved**.

## Start here

```bash
python scripts/pipeline_plan.py RUN --out RUN/analytics/pipeline_plan.json
```

Read-only. It contacts no provider, writes nothing into the run except the plan
you name, and takes about a minute and a half on a 52 GB run directory.

```bash
python scripts/pipeline_plan.py RUN --objective fewest-client-questions
```

The objective changes **which way a close call falls, and nothing else**. The
measurements are identical under all three; only the verdict on a marginal lane
moves. `least-spend` skips a lane whose measurement says it has little to do;
`fewest-client-questions` runs it; `balanced` leaves it undecided.

## What the three verdicts mean

| Verdict | Meaning | What you do |
|---|---|---|
| `selected` | A measurement says this lane has work to do on this corpus. | Run it, in spine order. Check `then` first — several selected lanes are disabled by default and name the setting. |
| `skipped` | A measurement says this lane has nothing to do here. | Do not run it. Record the reason; `run_lane_coverage.py` will report it as producing nothing and that is now an answer rather than a gap. |
| `undecided` | The deciding measurement is missing, or the decision is not a measurement at all. | Read `then`. It names either the artifact that would decide it or the person who must. |

**`undecided` is the load-bearing verdict.** There is deliberately no fourth
value meaning "probably skip". Defaulting an unmeasured lane to skip is how a
corpus reaches delivery with a control never run; defaulting it to run is how a
run spends a client's money on a lane its own evidence says is pointless. When
the plan cannot tell, it says so and names what would tell it.

## What it is not

* **Not an authorization.** A disabled lane stays disabled. A `selected` verdict
  on `google_handwriting_ocr.py` names `GOOGLE_HANDWRITING_OCR_ENABLED`; it does
  not set it, and it does not authorize sending anything to a provider. The run
  authorization in `RUN/logs/run_authorization.md` is still the only thing that
  does, and it must still name the provider families before the first call.
* **Not a gate.** It clears nothing. `skipped` is a claim about cost, never a
  finding about evidence, and it never substitutes for a control's own refusal.
* **Not a substitute for lane coverage.** `run_lane_coverage.py` reports what a
  run *did*. This reports what a run *should*. Run both.
* **Not a client decision.** Where the answer belongs to the client — acceptance
  on page-scoped corroboration, whether a unanimous line key attributes a
  document, typing a document consensus abstained on — the plan produces a
  question, not a verdict.

## The measurements, and where each comes from

Every one of these was read off a real production artifact. Each is either
measured or absent; nothing is defaulted to zero, because a missing measurement
and a measured zero lead to different plans.

| Measurement | Read from | Decides |
|---|---|---|
| `handwriting_share` | `has_handwriting` on the records | Both 3H lanes |
| `consensus_exception_rate` | consensus `summary.exception_rate_pct` | `extraction_context.py`, which `run-pipeline.md` makes **mandatory** above 50% |
| `single_engine_share` | the per-field `flag` breakdown in the consensus exceptions | `independent_corroboration.py`, then the acceptance question |
| `attribution_key_shape` | `documents[].fields` on the records | `allocation_policy.py`, and which attribution question to ask |
| `unconsulted_key_fields` | attribution `summary` | Whether to re-run attribution before asking the client for anything |
| `classification_unresolved` | classification consensus `summary` | `classification_amend.py` |
| `review_items_by_source` | `source_artifact` on each queue item | Where reduction effort actually pays |

### Two measurement traps this command was built around

**A rate over a different population is not a comparison.** The consensus
summary's `single_engine_documents` counts documents where only one engine
produced anything at all. It was `0` on a run with 4,271 single-engine *fields*.
Deciding the corroboration lane on the document count skips it on exactly the
corpus that needs it. The plan reports both and decides on the field breakdown.

**A lane that looks unconditioned may only be undeclared.** `consensus.py`
compares `corpus_context.sha256` across handoffs and refuses a mismatched pair.
A `None` there is ambiguous: it means either "this lane was not conditioned" or
"this producer never recorded that it was". Before reporting a lane as
unconditioned, check whether every other lane from the same adapter also says
`None` -- if they do, suspect the producer rather than the run. On this corpus
every OpenRouter lane carried the hash and both Vertex lanes said `None`, which
was a missing field, not a missing conditioning.

**A consensus artifact is where acceptance starts, not where it ends.** Every
measurement of "how much is accepted" must be read off the run's newest record
artifact, not its consensus artifact. `apply_corroboration.py` and
`apply_mappings.py` append acceptances after consensus, and on the commission
corpus the same 716 documents and the same 83,453 fields read 26,300 accepted
from `consensus_04.json` and 73,878 from `records_with_mappings_07.json`. A plan
built on the earlier artifact proposes lanes that have already run and reports a
corpus as two-thirds unresolved when it is 88.5% accepted. Walk
`summary.source_artifact` forward until nothing consumes the result.

**The unattributed count conflates opposite findings.** A statement whose lines
name twenty jobs is not missing an attribution — it has twenty, and no reference
table will resolve it. A statement whose lines unanimously name one job is a
different question with a different answer. On the commission corpus that split
was 307 multi-key, 36 unanimous, 217 with no key at all; only the last group is
what a reference table is for. Asking the client for a table to fix the first
group would have been asking for the wrong thing.

## Reading the client questions

The `client_questions` section is the output that matters most, because it is
the only part that costs the client anything. Each entry names:

* `unblocks` — what is waiting on the answer, in documents or items
* `needs` — the specific input. Never "ask the client"; always what to ask for.
* `removes_lanes` — work that disappears once it is answered

Take these to the client **together**, and take them **before** the lanes that
depend on them. A question asked after the lane has run is a re-run.

## Working the plan

1. Run the plan. Read `summary.unscanned` first — an artifact too large to read
   is a different finding from a lane that never ran, and only one of the two is
   yours to fix.
2. Act on every `selected` lane, in the spine order from
   [`tasks/run-pipeline.md`](../business-doc-operations/tasks/run-pipeline.md).
   The plan chooses lanes; it does not reorder them.
3. Resolve every `undecided` lane whose `then` names an artifact — that is
   missing measurement, and it is cheap.
4. Take the remaining `undecided` lanes and the client questions to the client
   in one pack. [`tasks/client-review-lane.md`](../business-doc-operations/tasks/client-review-lane.md)
   turns any blocked control into a client-answerable pack.
5. Re-run the plan after the answers land. It is cheap, and the measurements
   move: acceptance, mapping promotion, and a reference table each change which
   lanes have work left.
6. Before calling the run complete, run `run_lane_coverage.py` and reconcile it
   against the plan. A lane the plan skipped and coverage reports as empty is
   consistent. A lane the plan selected and coverage reports as empty is not.

## A lane this plan cannot propose

The planner reads what the run retains and names optional lanes by their
measurements. It cannot see a lane whose *input* is a column the schema never
named, because nothing measures a field that does not exist.

`specifier_recover.py` is that case: 145 pages of one corpus printed a
SPECIFIER column, the schema had no specifier field, and the value was either
written into `brand_name` or discarded. No measurement in this plan would have
surfaced it. What found it was comparing the column headings a form prints
against the fields it emits -- `pdftotext -layout` reads a heading well enough
even where the text layer is corrupt.

So before acting on a plan that reports nothing to do, spend one pass on that
comparison. A heading the schema cannot name is the next value about to be
misfiled, and the fix is usually a recovery from retained extractor layout
rather than a re-extraction.

## When the plan and the artifacts disagree, the artifacts win

This command was wrong five times before it was right, and every one of those
errors survived a green test suite: it looked for `total_pages_profiled` at the
top level when it lives in `summary`; it compared a percentage against a
fraction; it read a document count and called it a field rate; it looked for a
queue item's `source` when the item names `source_artifact`; and it counted 558
provider responses as lane artifacts because the raw-directory guard tested only
the start of a directory name. Each was found by running it against a real run
and reading the output, not by a test.

So: **read the plan against the run's own artifacts before you act on it.** If a
number in the plan does not match what the artifact says, the artifact is right
and this command has a defect. Fix the command.
