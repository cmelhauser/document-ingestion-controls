# Lane checklist

The lanes an operator gets wrong, in dependency order, with the options that
change what the artifact means and the traps that have actually cost a run.
Numbering in the lane tables matches `run_lane_coverage.py`'s catalogue, which is
the authority and lists every lane: if this file and that catalogue disagree, the
catalogue is right and this file is stale. The post-gate sequence numbers its own
steps instead. A lane missing here has nothing to add beyond its row in the
operations SKILL's lane table.

Read [`run-pipeline.md`](run-pipeline.md) for the spine and the phase rules. This
file is the operating checklist, not a substitute for either.

## How to use it

Work top to bottom. **A lane starts only after the previous one has written its
terminal artifact and you have read it.** "It looks like it finished" is not the
same as a written handoff: a lane killed mid-flight leaves raw responses and no
handoff, and every downstream reader will treat that as a lane that produced
nothing.

Three standing rules, each learned the expensive way:

1. **Never resume across a configuration change.** Model, reasoning effort, and
   refinement settings are recorded in the retained state and compared on resume.
   A refusal there is the control working. Start a fresh pass instead of forcing
   the resume, or the artifact mixes two reading regimes and no reader can tell
   which page got which.
2. **A lane that processed nothing has not passed.** Retain the blocked or
   zero-coverage result and carry it into lane coverage. Do not skip it, and do
   not present it as clear.
3. **Measure with the code's own functions.** Reimplementing a fingerprint or a
   field scan by hand produces confident wrong numbers. See
   [Measurement traps](#measurement-traps).

## Phase 0.5 – 2: intake and layout

| # | Lane | Command | Must know |
|---|---|---|---|
| 1 | Scan profiling | `scan_profile.py` | Writes `profile.json` at the run root. **This is not a table profile**, despite sharing the name with `table_comprehension.py profile`. |
| 2 | Intake | `ingest_pages.py` | One immutable master per page. Everything downstream binds to these hashes. |
| 3 | Reassembly | `reassemble_pages.py` | Writes `pages/reassembly.json`, not `controls/`. Grouping proposals only; never force-fit. |
| 4 | Image variants | `preprocess_pages.py` | Chunked, per-page, and recovery passes each name their own manifest (`variants_chunk_01_manifest.json`). Do not expect a single `variants_manifest.json`. |
| 5 | Manifest and state | `operations.py manifest\|stage` | Binds every later artifact to a hash. |
| 6 | Template drift | `template_drift.py analyze` | Reads a source-template artifact. Terminal: only `run_lane_coverage` and `template_observations` read its output, so a rerun propagates nowhere. |
| 7 | Detector thresholds | `detection_calibration.py` | *Optional.* Needs labeled truth. Without it, blocked — not skipped. |

## Phase 3: extraction and consensus

| # | Lane | Command | Must know |
|---|---|---|---|
| 8 / 9 | Extraction lanes | `llm_adapter.py --lane consensus_primary` / `--lane consensus_secondary` | Two distinct **model vendors**. A router is transport, not a second vendor. |
| 17 | Context-aware update | `extraction_context.py` | **Required when consensus reports >50% exceptions with no context.** Build it, then re-extract **both** lanes with `LLM_CORPUS_CONTEXT` set. Conditioning must be symmetric: `consensus.py` refuses a pair whose context hashes differ. |
| 22 | Consensus | `consensus.py` | Claims nothing over `source_labelled_field_proposals`. A corpus can read far better and leave this number unmoved — see [Reading the exception rate](#reading-the-exception-rate). |
| 15 | Classification consensus | `classification_consensus.py` | Run **after** extraction when intake rules classified little. Accepts a family only on two-vendor agreement. Shared `unknown` is never agreement. |
| 18 | Source templates | `template_observations.py` | **Always pass `--classifications`.** Without it every unclassified document falls into one family and the corpus collapses into a single "template" that is not a layout. Check `cohesive_layout` on every template before using the artifact. |
| 28 | Arithmetic reconciliation | `arithmetic_reconcile.py` | Settles `no_consensus` cells the line's own arithmetic decides. Run before building the review queue, or those cells reach a client who cannot answer them better than the document can. |
| 25 / 26 / 30 | Arithmetic, amendments, validation | `arithmetic_check.py`, `adjudicate.py`, `validate_extraction.py` | Commission documents are excluded from invoice arithmetic by design; use the allocation-policy control instead. |
| 31 / 32 | Addresses, entities | `address_normalize.py`, `entity_resolve.py` | Zero normalized addresses across a corpus is a finding, not a pass. Check `party_mentions` against the corpus before reading the party count: roughly one mention per document means the lane saw headers only, and a corpus that names its counterparty per line will have thousands. `--party-decisions` carries a person's authorized decisions about named pairs -- same party, branch of a parent, kept apart, not a party -- and reports any it cannot apply. |
| 33 | Specifier recovery | `specifier_recover.py` | *Optional.* Reads a printed column the schema once had no field for, out of retained extractor layout. Independent-extractor evidence, never vendor agreement. |
| 34 | Brand recovery | `brand_recover.py` | *Optional.* Recovers the manufacturer from the page letterhead when `brand_name` held the client, a dealer or nothing, then from a continuation page's layout. **Read the `letterhead_names_a_company_never_called_a_brand` exceptions**: each names a manufacturer the vocabulary lacks. Independent-extractor evidence, never vendor agreement. |
| 35 / 36 | Accuracy sample, page review | `accuracy_sample.py`, `page_review.py` | Verification against the page, not another model; the sample is stratified by layout and drawn from every document. **A page score compares a page's amounts as a set, so it cannot see a figure moved to the wrong line** -- check each line's own arithmetic before and after any change. |
| 37 | Export augmentation | `augment_export.py` | Fills empty cells beside the reading, never over it (`<column>__inferred`, `__inferred_by`, `__inferred_evidence`). Measure each method against the page text, per column, before trusting it: a method that looks sound can still carry a value the page never meant. With `--parties` it writes accounts and contacts: **check `party_keys_not_in_the_master` reads zero** -- a master other than the one the export resolved against joins nothing, and the first build of this wrote no accounts at all. With `--product-rules`, **read `products.lines` in the summary**: every line is counted by why it has, or lacks, a product, and `codes_taken_from_the_printed_row_over_the_engines` counts lines whose code column slipped a row. **Read `parties_from_shared_keys`**: each field's held-out accuracy beside its chance baseline; a field under 95% fills nothing, and a low score means the corpus's printed keys do not name one party. |
| 38 | Page review amendment | `page_review_amend.py` | Applies only corrections an independent source backs, under a named `--authorization`. **Refuses a correction that would break a line's own base x rate = commission**, and holds a figure moved between lines until both halves are proved. Rebuild the export from the amended records. |
| 39 | Party locations | `party_locations.py` | *Optional, disabled.* Google Places, one request per party within `--max-requests`, business names only. A candidate is accepted only when it accounts for every word of the party's name; every candidate is kept, and `--from-lookups` decides again without a request. Reaches the export only through `augment_export.py --external`. |
| 40 | CRM input validation | `crm_input_validate.py` | Runs **after** the export, against the canonical schema. Nothing is repaired: every finding is a mapping decision. |
| 41 / 42 | Handwriting | `google_handwriting_ocr.py`, `handwriting_review.py` | Reconciliation writes `controls/htr_decisions.json`. |
| 75 | Column re-file | `column_refile.py` | *Optional.* A reading filed under the nearest field -- a Specifier column read as the dealer -- moves whole to the field the page's heading names, under a named authorization. **Name the heading from the page image, on two pages, never from the values**: the column names dealers as well as architects. Read `refused` and `pages_not_in_records` in the report, then rebuild the export from the re-filed records. |
| 76 | Records in step | `records_in_step.py` | *Optional.* Run on any record artifact written before every writer kept `fields` and the `header`/`lines` views in step, then rebuild validation, arithmetic, attribution, the queue and the export from its output. `--check` exits 1 while any document is out of step; read `behind_by_acceptance` and `mappings_by_column` in the report, and `view_entries_kept` for the view-only flags it leaves alone. |

### Choosing a second extraction vendor

A secondary extraction model must clear four bars at once, and a price-sorted
model list will happily hand you one that fails any of them:

1. **Native file input.** The lane sends a page PDF. A model advertising only
   `image` input causes the router to parse the PDF with *another* vendor's OCR
   and record the wrong vendor as having read the page -- which quietly destroys
   the two-vendor independence consensus rests on. The lane refuses this rather
   than proceed; the refusal is correct.
2. **The extraction schema.** It is large and mostly optional properties. Some
   providers reject it outright (`the specified schema produces a constraint that
   has too much branching for serving`). **No metadata field predicts this** --
   `structured_outputs: true` does not mean this schema will serve. Only a probe
   tells you.
3. **A vendor distinct from the primary.** The cheapest candidates are usually
   from the same vendor as the primary, which collapses consensus to one reading.
4. **The account's data policy.** A model whose only endpoints fail your
   guardrails will 404. Relaxing the policy applies to *every* lane, including
   the ones sending client documents -- so a saving of a few dollars can quietly
   widen what a provider may retain or train on. Weigh that as a data decision,
   not a cost one.

**Probe before switching, on matched pages.** A cheaper engine that reads less is
not cheaper: every field it misses becomes a single-reading exception, which is
usually the largest category in the queue already. Compare emitted output only,
on the same page set, counting header fields, source-labelled proposals, and line
items separately -- a model can be much better at one and much worse at another.

Probing is nearly free: a model that fails bars 1, 2, or 4 fails before inference
and costs nothing.

## Phase 3: tables

Tables are the largest single spend in a run. Read
[`references/table-comprehension.md`](../../../references/table-comprehension.md)
before starting one.

| # | Lane | Command | Must know |
|---|---|---|---|
| 10 | Source-native tables | `table_comprehension.py profile\|rows\|audit\|mappings\|assemble` | `mappings --propose-mappings` **is not approval-blocked** — it generates the proposals a client approves. It takes one profile at a time. |
| 11 | Corpus tables | `table_comprehension_corpus.py` | Sequential **by design**: it carries a snapshot of prior *layout* observations forward. See [Parallelising tables](#parallelising-tables). |
| 14 | Table reconciliation | `independent_table_reconcile.py` | Needs mapped source rows **and** independent evidence **and** an approved registry. With no approved rule matching, it reconciles nothing — a zero-coverage failure, not a pass. |

Options that change what the artifact means:

- **`--independent-evidence`** — supply the Document AI handoff. Without it the
  conditional buddy check records `available: false`, and the retained `reason`
  reads `no_llm_reported_issue`, which is a default set *before* the check. It
  looks like a considered skip; it is an unavailable check. **Always pass it.**
  **Pass the recovery handoff, not the base one.** A base handoff retains the
  pages whose independent reading failed (`review_status: open_exception`), and
  the lane refuses those: *a retained failure is not evidence*. The refusal is
  correct and it stops the shard. Check the artifact you are about to supply:

  ```bash
  python - <<'EOF'
  import json; h=json.load(open("RUN/providers/docai_recovery_01_handoff.json"))
  bad=[r for r in h["records"] if r.get("review_status")=="open_exception"]
  print(len(h["records"]), "records,", len(bad), "open_exception")
  EOF
  ```
- **`--reasoning-effort`** — recorded in the corpus state and compared on resume.
  Keep it consistent with the extraction and discovery lanes, or the run carries
  two reading regimes.
- **`--refinement-passes`** — a **cap, not a quota**. Refinement runs only over
  pages that remain unresolved candidates and exits at `no_unresolved_candidates`
  without a round when none do. `1` means "one pass if needed".
- **`--refinement-tolerance`** — `0` is strict: a round stops early only when the
  candidate set is unchanged from the previous round.
- **`--resume`** — only valid when manifest hashes *and* model configuration
  match the retained state.

### Parallelising tables

The corpus lane cannot be parallelised internally: each page reads the layout
context accumulated from the pages before it, and that continuity is the reason
this lane exists rather than the per-document command. There are no worker
threads and no `--workers` flag; this is deliberate.

**Shard instead.** Split the intake manifest into disjoint contiguous page sets
and run one pass per shard concurrently. Contiguous rather than interleaved keeps
multi-page documents inside one shard.

**Size the timeout for concurrency before sharding.** The table lane's timeout
and retry defaults are calibrated for one sequential pass. Under concurrency,
per-call latency rises, calls that used to finish inside the timeout start being
abandoned, and each abandoned call is retried -- while the provider most likely
completed and billed the original. With a 120s timeout and 10 retries, one page
consumed twenty minutes and produced nothing.

Sharding therefore made a real run **slower than sequential**: 1.07 pages/min
across six shards against 0.30 for one, where six should have approached 1.8.
Three of six shards sat idle for sixteen minutes each. Raising the timeout to
600s and capping retries at 3 produced an even 1.32 pages/min with no retries at
all.

Before sharding, raise `--timeout-seconds` to comfortably exceed the *observed*
per-call latency at your effort level, and lower `--max-retries`: ten retries of
a call failing for a structural reason is ten times the bill for one failure.
Watch for uneven page counts across shards -- an even spread means the timeout
fits, and a spread like 1/7/1/1/3/5 means it does not.

The trade is honest and bounded: a shard learns only from its own pages, so a
layout first seen in shard 5 does not inform shard 1. Because the snapshot carries
**layout only** — never cell values, unapproved mappings, or prior model
decisions — the failure mode is a shard treating a known layout as novel. Reduced
continuity, never a wrong value.

## Phase 3: mappings

| # | Lane | Command | Must know |
|---|---|---|---|
| 19 | Semantic mappings | `schema_discovery.py discover` | Proposals only. Primary and buddy must be distinct vendors. |
| 21 | Slot equivalence | `schema_discovery.py slot-equivalence` | Proposals only. |
| 20 | Applied mappings | `apply_mappings.py` | The only command that moves an **approved** rule into a controlled field. Discovery and approval alone change nothing the document controls read. |

### Approvals do not survive re-extraction

A registry rule is keyed to a template fingerprint. A fingerprint derives from the
**emitted label set**. Re-extraction changes that set, so every approval keyed to
the old fingerprints is orphaned.

This is not hypothetical: a context-aware update on a 716-page corpus moved every
fingerprint, and overlap between 292 previously approved rules and the rebuilt
templates was **exactly zero**. Improving extraction invalidated every approval.

**Before sending proposals for approval, and before running any
mapping-dependent control, check the overlap.** Compute the fingerprints of the
current template artifact, intersect with the fingerprints in the registry, and
report the count. Zero overlap means the approval cycle must be rerun and the
prior artifacts must not be sent to the client.

## Phase 3A – 4Q: controls and the gate

| # | Lane | Command | Must know |
|---|---|---|---|
| 43 | Attribution | `attribution.py` | Mapping-dependent. Running it before `apply_mappings` yields a BLOCKED result that must be rerun. `--out`/`--register` names are operator-chosen. |
| 44 | Allocation policy | `allocation_policy.py discover\|decision-template\|registry-update\|apply` | The correct control for commission documents. |
| 45 | Inferred controls | `inferred_controls.py` | Proposal-only. **Never clears Phase 4**, even where no ledger will ever exist. |
| 46 | Completeness | `completeness.py` | Needs GL and payment evidence. Where the client has no ledger, this is permanently blocked — run it and retain the blocked result. |
| 47 | Sampling | `sampling.py` | Draws only from the **auto-accepted** population, so a corpus entirely in exception yields an empty frame -- a result, not a failure to run. It blocks an accuracy statement while any selection is unreviewed (`blocked_incomplete_sample_review`), and it does not prove corpus completeness. |
| 48 | Golden set | `golden_set_evaluate.py` | Needs client-authorized truth. |
| 49 | **Final review queue** | `final_review_queue.py` | **The gate. Hand it every review-bearing artifact.** A finding you do not pass in is absent from it — including table findings. Running the gate on a knowingly incomplete set produces a queue and a pack that are provisional, and they must be rebuilt. Pass `--resolved-by` the accepted-classification artifact: without it the gate asks the client every raw adapter `document_type` proposal, including the ones a downstream control already answered. |
| 61 | Root-cause grouping | `review_grouping.py` | **Consumes the final queue.** It runs *after* the gate, not before. |
| 62 | Safe consolidation | `safe_review_consolidation.py` | Drives the cross-record and full-dataset agents — an LLM lane despite the name, and documented as post-client-review. Needs `--enable`. |

## Client review boundary

| # | Lane | Command | Must know |
|---|---|---|---|
| 60 | Client review pack | `client_review_lane.py build\|read-answers` | Deterministic and free. This is the deliverable that turns a blocked run into something a client can act on. |
| 50 | Simulated client comments | `ai_simulated_client_review.py` | Reads the final queue. Structurally non-authoritative: every output is labelled *"simulated; not client authorization"* and its verdicts are `propose_*_as_simulated_client`. It cannot impersonate a client or clear a finding. Useful for stress-testing the questions. |
| 56 / 57 | Cross-record, exception resolution | `client_review_cross_record.py`, `client_review_exception_resolution.py` | Run **before** the client sees the pack — every exception they resolve is a question you do not have to ask. |

**Three relationship lanes need a client-context artifact**, and there are two
different ways to build one. Which you have decides whether they run before or
after the client answers.

| Builder | Input | Available |
|---|---|---|
| `client_input_comments.py` | client **comment files** (JSON/CSV/TSV/TXT/Markdown) | **before** review, whenever the client has supplied comments |
| `client_review_context.py` | `client_responses_preserved.json` -- the **returned workbook** | after review |

Both write `client_review_context_v1`, which `client_review_inference.py`,
`client_review_iterative.py`, and `client_review_cross_packet.py` all consume. So
these lanes are **not** post-review by construction: if the engagement holds
client comments, build the context from those and run them before the pack goes
out, where every relationship they resolve is a question you do not have to ask.

**Pass `--records`** (consensus or proofed JSON) or the lanes get no deterministic
evidence to anchor against. Do not fabricate a context artifact to force a lane:
the context is hash-bound to real client input, and inventing one puts
manufactured client material in the evidence trail.

## The post-gate sequence

Where the gate is **not clear** -- the normal outcome on a corpus that needs
client input -- the route is reduction, then the workbook, then delivery. Delivery
is last, and `client_delivery_package.py` enforces it: with an open queue it
refuses unless `--allow-open-findings` is passed, which marks the package
explicitly incomplete.

**Build the client context first.** Card review, the full-dataset agent, and three
of the relationship lanes all take `--context`. Build it from whatever client
material the engagement holds:

```bash
python scripts/client_input_comments.py NOTES.csv --records RUN/controls/consensus.json \
  --out RUN/client/context.json
```

Skipping it does not fail loudly: the lanes run without their evidence anchor, or
do not run at all, and the omission surfaces as a thin result rather than an error.

**Run the relationship lanes before the gate, not after.** Every exception they
resolve is a question the client never has to answer, and a queue built before
them carries findings that would have been settled. They are
`client_review_inference`, `client_review_iterative`, `client_review_cross_packet`,
`client_review_cross_record`, and `client_review_exception_resolution`.

Then, in this order:

| # | Step | Command | Cost |
|---|---|---|---|
| 1 | Gate | `final_review_queue.py` -- **every** review-bearing artifact | free |
| 2 | Card review | `client_review_llm.py --enable --workers N` | **LLM, one call per card** |
| 3 | Full-dataset agent | `review_agent.py --enable` | LLM, bounded slices |
| 4 | Grouping | `review_grouping.py` | free |
| 5 | Safe consolidation | `safe_review_consolidation.py --enable` | LLM |
| 6 | Simulated client comments | `ai_simulated_client_review.py --enable` | LLM, one call per card |
| 7 | Approval projection | `simulate_client_approval.py` | *optional stress test only* |
| 8 | **Issue the workbook** | `client_review_package.py create RUN/review/safe_review_consolidation.json` | free |

Step 2 is the one to size before running, and it is **one call per card, not per
queue item**. A queue of 82,801 items grouped into 718 cards is 718 calls. Read
the card count off the queue rather than the item count, or a lane that takes an
afternoon will look like one that takes a month.

`--max-cards` does **not** sample. It is a refusal threshold: exceed it and the
lane refuses the whole run, naming the true count in the message
(`card count exceeds configured limit: 718 > 20`), which is a cheap way to learn
that number before committing to it.

**Run it with workers.** `--workers` (`CLIENT_REVIEW_LLM_MAX_WORKERS`, default 8)
reviews cards concurrently. Output order does not depend on the worker count, so
the retained artifact is identical whatever you choose -- only elapsed time
changes. On a 718-card queue this is the difference between roughly nineteen
hours and under three. The throttle governs the actual request rate, so the
useful ceiling is the provider budget, not the worker count: eight workers on
~22K-token cards is ~176K tokens/min against a 1.6M/min budget.

Concurrency also removes the failure mode where one pathological card stalls
everything behind it. Sequentially, a single card that exhausts a 900-second
timeout three times costs an hour of wall-clock during which nothing else
happens; with workers, the other seven lanes keep moving.

### Which client command issues the workbook

Two commands build a client-facing pack and they are not interchangeable.

| Command | Use it |
|---|---|
| `client_review_lane.py build` | **Mid-run**, when one control blocks and you need that ambiguity removed to continue. Self-contained: it queues, groups, questions, and renders in one step from the artifacts you name. |
| `client_review_package.py create` | **End of run**, to issue the workbook the client actually answers. It consumes `safe_review_consolidation.json`, so it carries the whole reduction rather than one control's findings. |

Reaching for the lane command at the end of a run produces a pack that looks
finished and skips the reduction, so the client is asked questions that grouping
and consolidation would have collapsed or answered.

### After the workbook comes back

```
preserve-responses -> import-decisions -> client_decision_compile compile
  -> authorize -> schema_discovery registry-update -> apply_mappings
  -> rerun every affected control -> gate again -> client_delivery_package.py
```

Applying a decision is not the end of it: every control the decision touches is
rerun, and the gate is rebuilt from the results. A decision that is applied but
not rerun leaves the queue describing a state that no longer exists.

## Phase 5 – 6

Analytics, canonical export/DDL/load, target staging, retrieval, and the delivery
folder all require approved, provenance-linked, review-clear facts. With Phase 4
blocked, delivery is a **findings** package, not a facts package.

## Measurement traps

Every one of these produced a confident wrong number in a real run.

- **Raw responses contain the echoed prompt.** Some providers return the rendered
  instructions in the response body. A naive scan of the whole retained record
  will match vocabulary from the *prompt* and score every page a hit, including
  pages that carry nothing. **Extract only the model's emitted output**, per
  provider shape, before counting anything.
- **Compare matched populations.** Lanes at different progress have processed
  different pages. Rates computed over disjoint page sets are not comparable, and
  the difference will look like a quality gap between engines.
- **Use the code's own helpers.** `profile_headers()` yields header *strings*;
  passing the raw header dicts to `template_fingerprint()` includes column
  coordinates and fragments one schema into dozens. Import the function the lane
  imports.
- **Check the key you think you are reading.** A retained state's completed work
  may be under `completed_page_ids`, not `pages`.
- **A filename is not a lane.** `--out` is operator-chosen, and two lanes may
  share a default name. Match on a distinctive artifact key.

## Reading the exception rate

A high consensus exception rate is not automatically an extraction problem.
Improving extraction can raise it: better reading produces more
`source_labelled_field_proposals`, consensus claims nothing over those, and the
denominator grows.

Check the composition before choosing a remedy. Where most exceptions are
**single-engine**, coverage depth is the issue. Where most are **provider
disagreement**, both engines read the field and differed — that is answerable by
the client with a rule per document family, and no amount of re-extraction fixes
it.

## What parallelises, and what deliberately does not

Every lane that makes one provider call per independent unit takes `--workers`
(`*_MAX_WORKERS`, default 8) over the shared `parallel_map` helper. That helper
preserves input order, so **the retained artifact is identical at any worker
count** -- only elapsed time changes. Measured on a 718-card review queue,
sequential ran 0.65 cards/min and eight workers ran ~5.4: nineteen hours became
under two.

| Lane | Parallel unit |
|---|---|
| Card review | cards |
| Cross-record search | batches |
| Exception resolution | batches (primary then buddy stays ordered *within* a batch) |
| Simulated client comments | cards (checkpoint taken under a lock) |
| Full-dataset agent | slices (iterations within a slice stay sequential) |
| Reference discovery | batches |
| Iterative relationships | batches within a pass (passes stay sequential) |

Four lanes are sequential on purpose, and changing that would be a defect:

- **Tables** take one `page_id` per invocation, so there is no internal loop to
  widen; parallelism is external, by sharding pages across processes. The three
  roles are ordered because the audit reads the profile and rows.
- **Address validation** and **Google Places entity lookup** carry a shared
  request-budget counter and a dedupe cache. Concurrency would make an explicit
  cost cap racy and would pay for lookups the cache should have answered.
- **Allocation policy** and **LLM adjudication** make a single provider call
  each. There is nothing to overlap.

Two ordering rules worth keeping in mind when adding a lane:

- **Check whether the merge is commutative.** The agent's `merge_reductions`
  extends lists and overwrites `next_action`/`confidence` with whatever merged
  last, so merging concurrently would be non-deterministic. Each slice collects
  its own reductions and they are merged afterwards in slice order.
- **A stub that pops replies off a list tests nothing under concurrency.** It
  answers by call order, which is exactly what concurrency is allowed to change.
  Key the stub's reply to the request, so a crossed response fails a check
  instead of passing quietly.

## Why card review reduces so little, and what auto-accept can do about it

`CLIENT_REVIEW_LLM_AUTO_ACCEPT_PROPOSALS` exists and is worth enabling, but it
will not turn a large queue into a small one, and the threshold is not the lever
it looks like.

The card-review gate is:

    not protected(item)
      and reviewer decision == "propose_resolution"
      and a proposal bound to the reviewed field with retained evidence

**Confidence is not in it.** `--auto-accept-threshold` is validated and recorded
in the artifact, but the lane never compares against it, because self-reported
confidence is provenance rather than a decision term. The same variable *does*
gate `client_review_cross_record.py`, which rejects a match
`below_auto_accept_threshold` -- if a run shows carry-forward responding to that
number, it is that lane, not this one.

So when the yield is low, do not reach for the threshold. Measure where the gate
is actually failing:

    grep -o '"reviewer_decision": "[a-z_]*"' CARD_REVIEW.json | sort | uniq -c

One measured run: 82,801 items in 718 cards returned `retain_review` on 81,243
items, `covered_by_other_item` on 1,470, and `propose_resolution` on 88 (0.1%) --
of which none cleared protection and field-binding, so zero were auto-accepted.

The cause is upstream of review. Where most queue items are a *single reading* --
one engine read the field, so no second reading disagreed -- there is nothing for
a reviewer to reduce. It can only retain them. Converting that population into
something reducible means a second extraction over those pages, not a different
review setting; otherwise those findings are the client's to answer, and the
review lanes are a cost with a small return. Decide that before paying for the
remaining LLM steps.

## Process traps

- **Killing the workspace wrapper orphans the adapter.** `run_workspace.py run`
  does not propagate signals to its child. Killing the wrapper leaves the adapter
  running, still calling the provider, reparented to init. Kill by pattern
  (`pkill -f "llm_adapter.py.*<raw-dir>"`), then confirm no process remains and
  the lane lock has released.
- **The lane lock is a kernel `flock`.** It releases when the holder dies. A
  "lane already active" refusal means a process really is alive — look for an
  orphan before assuming a stale lock file.
- **Model resolution is per lane.** A consensus lane reads its lane-specific
  model setting, not the provider default. Changing the provider-level model may
  do nothing. Verify against the model recorded in a retained raw response, which
  is the authority over any config file.
- **A batch cap of `0` means unlimited**, deriving every packet needed to cover
  the corpus. A positive value is an explicit cost cap that defers work. Do not
  "fix" a zero.
- **`pgrep -f` matches any command line containing the pattern, including a
  watcher that mentions it.** A `while pgrep -f other_job.sh; do sleep; done`
  gate will wait forever if a monitor's own command line contains
  `other_job.sh`. It deadlocked a queued job against a process that had already
  finished. Match on something the watcher cannot contain, or check for the
  artifact the job writes rather than for the job.
- **A manifest's page paths resolve against the manifest's own directory.** The
  intake manifest sits in `pages/` and names `pages/000001__....pdf`, which
  resolves to `pages/pages/000001__....pdf`. A derived manifest written to a
  subdirectory resolves every page one level too deep and every page becomes
  unreadable. Write derived manifests **beside** the original, not beneath it.
- **`find -newermt` is not portable.** Some systems reject the relative form
  outright, and a staleness check built on it silently finds nothing — which
  reads as "no recent activity" and fires a false stall alarm. Compare
  `os.path.getmtime` values instead.
- **A short timeout multiplied by a high retry count is a silent stall, not
  resilience.** The retry loop is `range(max_retries + 1)`, and **nothing is
  retained until the loop returns or raises** — so a lane that has been working
  for twenty minutes can hold zero raw responses and zero exceptions. Flat CPU
  with an open socket and no output is this, not a hang. It has now bitten twice:
  the table lane (120s x 10) and card review (120s x 10, where the largest packet
  is 139K tokens and card 1 was one of fourteen over 100K). Before running a lane,
  measure its largest packet rather than its median — `estimate_tokens(packet(...))`
  over the real queue takes seconds — and set the timeout for the outlier. Lower
  the retry count while raising the timeout: a high retry count on a request that
  *cannot* succeed is the mechanism that hides the failure. Card duration tracks
  reasoning depth rather than packet size alone, so the slowest card is not
  reliably the biggest one; run the lane with workers so a single pathological
  card cannot stall the whole queue behind it.
- **A setting that is validated is not necessarily a setting that is applied.**
  `CLIENT_REVIEW_LLM_TIMEOUT_SECONDS` and `CLIENT_REVIEW_LLM_MAX_RETRIES` were
  read into `run_review`, validated, echoed into the artifact -- and never used
  for a request. The timeout that governs a call belongs to the *SDK client*,
  which was built from hardcoded defaults. Raising either value changed nothing.
  Confirm a limit against the completed artifact's own summary, not against
  `.env`: a run that records `max_retries: 4` while `.env` says `3` is telling
  you the variable is not wired.
- **Retries nest.** The provider SDK is constructed with `max_retries` and the
  lane retries around it, so four each is up to twenty-five requests for one
  unit of work, not five. Budget a timeout against the product, not the lane's
  own count.
- **A failed unit's retry log is the one that matters.** When the log is built
  inside the retry helper and returned only on success, every failure reports
  zero attempts -- which reads as "never retried" when the truth may be the
  opposite. Have the caller own the list so a raise cannot discard it.
- **An orchestration lane may re-run what you already ran.**
  `safe_review_consolidation.py --enable` shells out to cross-record and the
  full-dataset agent and writes its own copies inside `--output-dir`; the
  `*_01.json` artifacts from earlier in the run are ignored. Check what a lane
  *invokes* before assuming it consumes. Without `--enable` it runs nothing and
  requires those inputs at its own filenames instead -- free, but the artifact
  then cites inputs an operator placed rather than ones a lane produced.
- **A sub-lane's working directory is the repository, not the run.**
  `run_lane` launches with `cwd=REPOSITORY_ROOT`, so a relative `--run-dir`
  resolves the queue against the repository and the first lane dies with
  `No such file or directory`. Pass an absolute run path to any command that
  shells out to another.
- **A change type that compiles is not a change type that applies.**
  `client_decision_compile.py` accepts six change types and `authorize` signs any
  of them, but only `template_registry_rule` has a consumer
  (`template_drift.py registry-update`). `document_type_rule`, `mapping_rule`,
  `amendment`, `allocation_policy_rule` and `source_quality_action` compile,
  authorize, and then fail at apply with `authorization contains no
  template_registry_rule patches` -- *after* an operator has signed. Check the
  consumer before building the catalog: `grep -rl '"<change_type>"' scripts/`.
- **An authorization binds one plan hash.** `authorize` records
  `compiled_plan_sha256`; re-cutting the catalog invalidates the signature and
  needs a fresh one. Getting the change type wrong therefore costs an operator's
  signature, not just a rerun.
- **Do not hand-verify a returned workbook's binding.** Its embedded
  `safe_consolidation_sha256` is over canonical content, not file bytes, so a
  `shasum` of the consolidation will never match and looks like tampering. Pass
  `--issued-workbook` and read `lineage_status` and `fixed_cell_differences`.
- **Consolidation regroups.** `safe_review_consolidation` runs its own grouping
  over the *visible* items, so its decision groups differ from a standalone
  `review_grouping` run (1,353 items where the standalone said 1,382). Build a
  change catalog from the consolidation's groups, which is what `compile`
  validates against.
- **Check that a launch-time flag was persisted.** The table timeout was
  corrected on the command line mid-run and never written back, so `.env` still
  carried the known-bad value and a rerun would have hit it again. A fix that
  lives only in a shell command is not a fix.
- **Timeout tuning belongs in `.env`, never in `.env.example`.** `release_check`
  asserts that every value in `.env.example` equals the *code* default, so
  raising a timeout there blocks the release gate with a "runtime configuration
  default mismatch". `.env.example` documents what the code does out of the box;
  `.env` is where a corpus's own measurements go.

- **A rate is only as good as the clock it divides by.** Throughput computed
  against a watcher's own uptime counts work done before the watcher started and
  overstates the rate — one such reading claimed 4.88 pages/min and a 2.1-hour
  ETA where the true figures were 1.32 and 7.8 hours. Divide by the elapsed time
  of the process doing the work.

## Reading the usage report

`llm_usage_report.py` answers **what a run cost**, not what it stands behind. It
counts every retained attempt, superseded ones included, because an abandoned
attempt was paid for and is usually the number worth reviewing. To ask which
attempt the run relies on, declare a generation and read
`run_lane_coverage.py --generation`; the two questions are deliberately separate.

Lanes are named from where their raw responses sit -- `providers/primary_02`,
`tables/tables_t01` -- so the name traces back to the `--raw-dir` the operator
chose. A per-page lane is summed across every page and role rather than sampled
from one directory.

The report is **token-only**. Provider prices vary by account and are not
invented; it ships a `how_to_price` formula instead. Take its totals to your own
billing console rather than multiplying them here.
