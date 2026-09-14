# Running the pipeline

The exact options for any command are in its `--help`; every argument carries
help text. The complete runbook is
[`docs/TECHNICAL_DOCUMENTATION.md`](../../../docs/TECHNICAL_DOCUMENTATION.md).
This file is the **order**, and the decisions between the steps.

Phase entry and exit criteria are in
[`references/workflow-gates.md`](../../../references/workflow-gates.md). Do not
advance past a failed exit condition.

Initialize a fresh run with `run_workspace.py init RUN`, and invoke every
writing command below through `run_workspace.py run RUN --`. The examples retain
the `RUN/...` notation for readability; the wrapper makes it workspace-relative,
records each declared output, and routes cache/throttle state under `RUN/runtime`.
Source inputs and credentials are the only intentional external paths.

## Execution discipline

This is a strict state machine, not a menu of commands. Before a dependent lane
starts, its upstream artifacts must be complete, their exception artifacts must
be read, and the applicable phase exit condition must be met. Only lanes that
are genuinely independent and within the same phase may run in parallel. Do not
start a later review, canonical, or delivery lane as an implicit preliminary
pass while an upstream lane is active; after upstream completion, create a new
no-clobber downstream artifact that includes its output.

A command that refuses, processes zero items, receives the wrong schema, or
retains provider failures has produced an integration finding. Keep it in the
run, repair it or record the exact blocker, and use the documented retry/recovery
overlay when applicable. Never skip affected input, manually manufacture a
passing-shaped JSON file, or silently treat a prerequisite as satisfied.

At the beginning, `RUN/logs/run_authorization.md` records whether every
in-scope source item may be sent to each selected configured provider. At the
end, `run_workspace.py audit` and `run_lane_coverage.py --require` must account
for every selected lane before final review is built.

## Before you spend: choose the lanes

The spine below is the **order**. It does not say which of the optional lanes
this corpus needs, and that question has a measurable answer.

```bash
python scripts/pipeline_plan.py RUN --out RUN/analytics/pipeline_plan.json
```

Read-only, contacts no provider, authorizes nothing. It measures the run's own
retained artifacts and proposes which optional lanes have work to do here, which
do not, and which client questions the plan cannot avoid. Run it once the first
artifacts exist, and again after each set of client answers lands — acceptance,
mapping promotion, and a reference table each change which lanes have work left.
Full guidance is in [`../../pipeline-selection/SKILL.md`](../../pipeline-selection/SKILL.md).

It never reorders the spine and never makes a lane optional that is not already
marked optional below.

## The spine

Everything below hangs off this. Optional and disabled lanes attach at marked
points; none of them can replace a step on this line.

```
scan_profile ─→ ingest_pages ─→ reassemble_pages ─→ preprocess_pages
                                      │
                          operations.py manifest / stage
                                      │
              ┌───────────────────────┴───────────────────────┐
    llm_adapter --lane consensus_primary      llm_adapter --lane consensus_secondary
              └───────────────────────┬───────────────────────┘
                                 consensus
                                      │
                              arithmetic_check
                                      │
                       adjudicate ─→ validate_extraction
                                      │
                  address_normalize ─→ entity_resolve
                                      │
                                 attribution
                                      │
                                completeness
                                      │
                                  sampling
                                      │
                             final_review_queue
```

**Why this order.** Extraction precedes consensus because consensus compares two
independent readings and cannot manufacture the second one. Arithmetic precedes
the business controls because a document that does not add up should not be
attributed or counted toward completeness. Completeness is last among the
controls because it needs everything else resolved. `final_review_queue` is last
because it is the gate, and a finding that is not handed to it is absent from it.

## Phase 0.5 — profile the corpus

```bash
python scripts/scan_profile.py SOURCE_ROOT/originals --out RUN/profile.json
```

Runs against the **full corpus**, not a sample. It resolves the colour-depth
question empirically, which sets the handwriting branch, the QA sample sizing,
and the rescan decision. Two traps it exists to catch: 24-bit files containing
only grey pixels, and lossy JBIG2 that can substitute digits with no visible
artefact. Numerics from JBIG2 pages stay suspect regardless of OCR confidence.

## Phase 1 — intake

```bash
python scripts/ingest_pages.py SOURCE.pdf --out RUN/pages
python scripts/reassemble_pages.py RUN/pages/ingestion_manifest.json \
  --out RUN/pages/reassembly.json --exceptions RUN/pages/reassembly_exceptions.json
python scripts/preprocess_pages.py RUN/pages/pages --out RUN/pages/variants \
  --manifest RUN/pages/variants_manifest.json
```

Intake writes its one-page masters to a `pages/` subdirectory, so
`preprocess_pages.py` takes `RUN/pages/pages` rather than the run's page
root. Pointing it at the root refuses with that instruction rather than
reporting success over zero input.

`reassemble_pages.py` produces **proposals**. Pages it cannot confidently assign
go to an unassigned pool and are worked by hand. `--broad-unordered-proposals`
widens it to same-type unique-identifier candidates without claiming order — use
it deliberately, because it produces more review, not less.

Bind the run before spending anything:

```bash
python scripts/operations.py manifest RUN/pages/*.json --config-from-env \
  --out RUN/manifest.json
```

## Phase 2 — classification and layout

Template drift compares **observed source layouts** against an approved
registry, and the observation artifact is built from a completed consensus run
by `template_observations.py`. So although drift belongs to Phase 2 by meaning,
it can only be computed after Phase 3 — see [after consensus](#after-consensus-source-templates-and-drift).
Know this limitation rather than working around it: a changed vendor layout is
detected after that layout has already been extracted, not before.

`detection_calibration.py` is optional and needs labeled truth; without truth
there is nothing to calibrate against.

## Phase 3 — extraction

Read [`tasks/extraction.md`](extraction.md) before running this phase. In short:

```bash
python scripts/llm_adapter.py --lane consensus_primary RUN/pages/ingestion_manifest.json \
  --out RUN/providers/primary.json --adapter-out RUN/providers/primary_handoff.json \
  --exceptions RUN/providers/primary_exceptions.json --raw-dir RUN/providers/raw/primary
python scripts/llm_adapter.py --lane consensus_secondary RUN/pages/ingestion_manifest.json \
  --out RUN/providers/secondary.json --adapter-out RUN/providers/secondary_handoff.json \
  --exceptions RUN/providers/secondary_exceptions.json --raw-dir RUN/providers/raw/secondary
```

Separate invocations, separate output paths, genuinely independent providers.

Then reconcile:

```bash
python scripts/consensus.py RUN/providers/primary_handoff.json RUN/providers/secondary_handoff.json \
  --out RUN/controls/consensus.json --exceptions RUN/controls/consensus_exceptions.json
python scripts/arithmetic_check.py RUN/controls/consensus.json --out RUN/controls/arithmetic.json
python scripts/adjudicate.py RUN/controls/arithmetic.json \
  RUN/providers/primary.json RUN/providers/secondary.json \
  --out RUN/controls/amendments.json --exceptions RUN/controls/adjudication_exceptions.json
python scripts/validate_extraction.py RUN/controls/consensus.json \
  --out RUN/controls/validation.json --exceptions RUN/controls/validation_exceptions.json
```

A row's `line_number` does not go through this vote. It is the row's position
in the table, the run derives it, and no vendor reads it off the page — so it
comes back flagged `derived_ordinal` with both engines' readings kept as
provenance, and it never reaches the gate. Before that, it was 4,086 blocking
findings on the production run, 5% of the whole client gate, most of them an
engine that had filled the field with the row's invoice number.

Optional cross-checks attach here — see [`tasks/cross-checking.md`](cross-checking.md).
Handwriting attaches here — see [`tasks/handwriting.md`](handwriting.md).

Slot equivalence attaches here too, and only here: it reads the completed
consensus run and proposes which controlled fields the client's documents treat
as one business fact. It resolves nothing on its own — see
[`tasks/extraction.md`](extraction.md).

```bash
python scripts/schema_discovery.py slot-equivalence \
  RUN/controls/consensus.json RUN/controls/consensus_exceptions.json --enable \
  --registry registry/mappings.json --out RUN/controls/slot_equivalence.json \
  --exceptions RUN/controls/slot_exceptions.json \
  --handoff-out RUN/controls/slot_handoff.json --raw-dir RUN/controls/raw/slots
```

### After consensus: source templates and drift

The mapping lanes and drift analysis all read one artifact, and nothing else
produces it. Build it here, from the consensus run:

```bash
python scripts/classification_consensus.py \
  RUN/providers/primary_handoff.json RUN/providers/secondary_handoff.json \
  --out RUN/controls/classification_consensus.json \
  --exceptions RUN/controls/classification_exceptions.json
python scripts/template_observations.py RUN/controls/consensus.json \
  --classifications RUN/controls/classification_consensus.json \
  --out RUN/templates/observed_templates.json \
  --exceptions RUN/templates/observed_template_exceptions.json
python scripts/template_drift.py analyze RUN/templates/observed_templates.json \
  --registry registry/templates.json --out RUN/controls/template_drift.json
```

**Check the intake classification before you build templates.** Phase 2 rules are
high-signal phrase matches; a client whose documents carry no such phrase leaves
`classification_status: no_high_signal_document_type` on most of the corpus, and
every one of those documents groups under the family `unknown`. One corpus put
645 documents into a single "template" of 2,430 labels that way, and mapping
discovery then proposed rules against a fingerprint that stood for nothing.

`classification_consensus.py` recovers it without re-reading a page: both engines
already proposed a type, and it accepts one only where two independent model
vendors agreed. Read `label_cohesion` on every template afterwards — a template
whose commonest label appears in a small share of its documents is a union of
unlike layouts, and `template_observations.py` now raises that as a high-priority
exception rather than letting it pass as a layout.

### When consensus comes back mostly in exception: the context-aware update

Read `context_aware_update_required` in `consensus.json` before you work the
queue. When more than half the fields are in exception and neither lane was
conditioned by a corpus context, `consensus.py` sets it and
`run_lane_coverage.py` fails until the update exists. That is not a formality:
at that rate most exceptions are fields only one engine saw at all, and every
downstream control inherits them.

Working such a queue by hand is the expensive answer. The cheaper one is that
both engines were reading without the vocabulary the corpus uses. Extraction
sees one page at a time and remembers nothing, so a label printed on hundreds of
pages is rediscovered or missed page by page. One 716-page commission corpus
printed its acknowledgement numbers under `ACK#`, `ACK NO` and dealer-qualified
variants throughout; the header carried one on four documents, and attribution
then had no key on 715 of 716.

Build the context from what the run has already established, then re-extract:

```bash
python scripts/extraction_context.py \
  --registry RUN/controls/mappings_v2.json \
  --classifications RUN/controls/classification_consensus.json \
  --notes /path/outside/repo/operator_engagement_notes.md \
  --out RUN/controls/corpus_context.txt \
  --provenance RUN/controls/corpus_context_provenance.json
```

Only **approved** rules become glossary lines — an unapproved proposal put in
front of the model would be applied to every page without anyone accepting it.
Nothing in the file is a value: it says where a printed label belongs, and the
prompt states that the page wins and an absent label stays null.

Then set `LLM_CORPUS_CONTEXT` to that file and re-run **both** extraction lanes
into fresh paths. Both, not one: `consensus.py` refuses a pair whose context
hashes differ, because coaching one engine and not the other measures the
coaching rather than the page. Changing the context changes the prompt, so the
response cache cannot replay a reading taken under different conditioning.

Re-run every control downstream of consensus against the new artifacts. This is
an update to the run, not a repair of the old one: the previous artifacts stay
retained.

Drift fails closed on a changed or unknown layout. That is the point: a silently
changed vendor layout produces structurally wrong extraction, not slightly
inaccurate extraction. With no registry every layout is new, which is a correct
first-run result and not a clean one.

The same artifact is what `schema_discovery.py discover` and
`allocation_policy.py discover` read. Passing them a consensus or attributed
artifact instead is refused.

### Approving a mapping is not applying it

`discover` proposes, `registry-update` approves, and **`apply_mappings.py` is what
carries the approved rule into the controlled field on the document record**:

```bash
python scripts/apply_mappings.py RUN/controls/consensus.json \
  RUN/templates/observed_templates.json --registry registry/mappings.v2.json \
  --out RUN/controls/applied_mappings.json \
  --records-out RUN/controls/records_with_mappings.json \
  --exceptions RUN/controls/applied_mapping_exceptions.json
```

Attribution, completeness, and `inferred_controls.py` read document records, not
the registry. A run that stops after approval leaves every mapped value invisible
to them — one corpus printed its acknowledgement numbers on almost every page,
retained 21,374 source-labelled entries, and still reported
`no_explicit_attribution_key` for 715 of 716 documents, because nothing carried
the label into `header.acknowledgement_number`.

Two engines must agree on the value before it is promoted, and a label only one
engine read is retained as an explicit exception rather than promoted. Feed
`--records-out` to the controls that consume document records.

Then the identity controls:

```bash
python scripts/address_normalize.py RUN/controls/consensus.json \
  --out RUN/controls/addresses.json --exceptions RUN/controls/address_exceptions.json
python scripts/entity_resolve.py RUN/controls/addresses.json \
  --out RUN/controls/entities.json --log RUN/controls/entity_merges.json \
  --client-name "THE CLIENT"
```

**Always name the client.** It is printed on nearly every page -- as a
statement's addressee, as the agent of record, in a letterhead -- and none of
that makes it a counterparty. Left undeclared on the commission run it took
1,395 mentions across nine role fields and became the largest account in the
master, its own customer, its own dealer and its own brand. One spelling is
enough: the match covers legal-suffix variants, the mid-word truncation a narrow
column prints (`NORTHGATE C`), and the name with a totals label or an annotator's
initials appended. Every refusal is retained under `refused_as_the_client` with
the field and document it came from, and the summary reports the count -- a zero
with no `--client-name` set means the check never ran, not that the client is
absent.

A person's decisions about named pairs go in with `--party-decisions`:
`same_party`, `branch_of` (both accounts kept, the branch linked to its parent),
`different_parties` and `not_a_party`, under the authorization the file names. A
decided pair leaves pending adjudication with its score kept, and a decision the
resolver cannot apply is reported with its reason rather than guessed at.

The canonical name is the party's most complete reading, not its most frequent
one. The corpus cuts names with no marker, so the stump is often the commonest
spelling; a reading another variant finishes is set aside before frequency
decides. A completion that appends a code rather than restoring words is
refused, so an account is never named `KOK Interic PO4705`.

The lane also lifts a location key off an account name before grouping:
`7144 KD Frost` and `KD Frost` are one account, and the keys it was printed with
are carried on the party as `location_identifiers`. That is what picks the
address for a row, so it belongs beside the account rather than inside its name.
A street number is not a location key -- `801 Bayshore Avenue` keeps its digits.

## Phase 3A, 4, 4Q — the business controls

Read [`tasks/business-controls.md`](business-controls.md). The order is
attribution → allocation (if commission applies) → completeness → sampling.

## The gate

```bash
python scripts/final_review_queue.py RUN/controls/*.json RUN/providers/*_exceptions.json \
  --resolved-by RUN/controls/classification_consensus_04.json \
  --out RUN/review/final_queue.json
```

Hand it **every** review-bearing artifact from the run. A finding you do not pass
in is not in the gate, and the gate is what a clear result is claimed from.

That includes the adapters' raw `document_type` proposals — and it is why
`--resolved-by` exists. Pass the accepted-classification artifact and a raw
proposal for a document that control already accepted, naming the same type, is
retained under `reconciled` instead of being asked again; on the production run
that was 1,364 questions the run had already answered itself. A proposal naming
a **different** type than the accepted one is a real disagreement with a
resolved control and stays blocking. Reconciled items are retained in the queue
with the control that answered them, never dropped, and `--resolved-by` refuses
an artifact that accepted nothing.

Read `gate_status`. If it is not `clear`, the remaining findings are listed with
their reasons. Protected findings — arithmetic, handwriting, identity,
provider/schema, reassembly, missing fields, unresolved evidence — cannot be
cleared by any batch decision, threshold, or grouping. They need a human.

## Stop at the first refusal

Every stage writes an exception artifact beside its output. Read it before
running the next stage. A stage that produced exceptions has not failed — it has
told you what it could not resolve, which is its job. What matters is that you
know the count before you build on it.

Two refusals mean something specific:

- **`consensus` refuses the handoffs.** The two lanes resolved to the same model
  vendor. That is independence enforcement, not a bug. Reconfigure and rerun the
  second lane.
- **A control reports zero work done.** Check `coverage`/`corroborated` in the
  artifact. A clean-looking result covering nothing is a failure; find the
  upstream artifact that produced nothing.

## Before you call the run complete

```bash
python scripts/run_lane_coverage.py RUN --out RUN/lane_coverage.json
```

It names every lane that produced no output. A lane may be deliberately skipped;
what this prevents is a run reaching delivery while a lane you meant to run
never was. Add `--require LANE` for each lane this engagement must include, and
the command fails rather than reports.

It counts **everything** under the run directory. Keep synthetic inputs, smoke
tests, and scratch outputs outside it — anything left inside is counted as a
lane that ran, and the report then overstates the run.

## Retries

Use a new output directory. Retry only through a command's documented resume or
`--retry-exceptions` mechanism — never by rerunning over an existing directory.
The retained exception artifact holds the exact IDs a retry needs.

After a bounded lane completes, freeze its output directory, run one separate
no-clobber retry pass for the provider-retryable exceptions, then reconcile the
overlay with the original. Data, cap, identifier, and unsupported findings stay
explicit; they are never converted into successes by a retry.

## When canonical admits nothing: check, fill and validate the export

`canonical_export.py` admits only review-clear documents. Where that is none,
the readings leave through `field_extract_export.py` (see
[`tasks/deliver.md`](deliver.md)), and these lanes check and complete that
export, in this order. None of them is canonical or clears a control.

1. `page_review.py` verifies the export against the source page rather than
   against another model. `--packet` emits one page's image with the rows the
   export claims for it; the lane reads the reviewers' returns from `--reviews`,
   names lost money (printed, held nowhere) and invented money (counted, never
   printed) mechanically, and `--proposals` writes what the reviews propose,
   graded by the independent evidence beside each correction.
2. `page_review_amend.py` applies those proposals as append-only amendments
   under the operator authorization `--authorization` names, at the `--evidence`
   grades chosen -- by default every grade independent of the reviewer. It
   refuses a correction that would break a line's own base x rate = commission,
   and holds a figure moved between lines until both halves are proved. Rebuild
   the export from the amended records. `column_refile.py` is the same kind of
   step for a reading filed under the wrong field -- a Specifier column read as
   the dealer: under a named authorization it moves each reading whole to the
   field the page's heading names, and refuses to move one onto a reading.
   `records_in_step.py` brings a record artifact written before every lane kept
   its fields and views in step into step, and `--check` gates on it; rebuild
   the controls and the export from its output.
3. `party_locations.py` is optional and off unless enabled, with `--enabled` or
   in the environment. It asks Google Places where each dealer and brand
   trades, one request per party within `--max-requests`, and accepts a
   candidate only when it accounts for every word of the party's name.
   `--countries` limits where an accepted business may trade, `--skip-known`
   skips parties a public-source file already covers, and `--from-lookups`
   decides again from a previous run's retained candidates, sending nothing.
4. `augment_export.py` fills empty cells beside the reading from the run's own
   evidence, and `--external` adds operator-compiled public-source facts,
   accepted Places locations among them. Each inference sits in
   `<column>__inferred` with its method and evidence. With `--parties` it also
   writes the accounts (`--out-accounts`, joined on `<field>__party_key`, never
   from the client's fields, a job or a refused reading) and the contacts
   (`--out-contacts`, one person's spellings joined on evidence, a company only
   where an email naming the person proves it) a CRM loads beside the grains.
   With `--product-rules` it reads each line's product by its manufacturer's
   own rule and `--out-products` writes one product per manufacturer and code;
   `--extractor-raw` checks each code against the row the line's own amounts
   are printed on. An inferred party carries the master's key in
   `<column>__inferred_party_key`, and `same_key_on_other_lines` fills a line's
   customer or dealer from the same maker's lines printing its order, job,
   project or transaction number, where every such line names one party. It is
   scored first on held-out lines, and a field under 95% fills nothing.
5. `crm_input_validate.py` checks each `--grain` against the canonical
   `--schema`. Nothing is repaired: every finding is a mapping decision.

A page score compares a page's amounts as a set, so it cannot see a figure
moved to the wrong line. Check each line's own arithmetic before and after any
change to line-level money.

## What comes after the gate

| Gate state | Next |
|---|---|
| Not clear | [`tasks/review-reduction.md`](review-reduction.md), then the client workbook |
| Clear, client review needed | [`tasks/client-review.md`](client-review.md) |
| Clear, decisions applied and rerun | [`tasks/deliver.md`](deliver.md) |
