# Business Document Ingestion — Agent Instructions

Read `SKILL.md` before acting. It is the repository's operating router. Read the
relevant file in `references/` completely before changing or operating a phase.
Read `references/artifact-contracts.md` before changing an input, output, review
header, workbook, or runbook contract. Use
`docs/TECHNICAL_DOCUMENTATION.md` as the end-to-end system manual and
`HANDOFF.md` for current state only.

The project is dedicated to the public domain under The Unlicense. Read
`LICENSE`, `NOTICE`, and `CONTRIBUTING.md` before distribution or accepting
contributions; preserve third-party notices. Courtesy credit to
`theonlymuffinbot` describes collaborative work using Anthropic Claude Opus 5,
OpenAI GPT-5.6 Terra and Sol, and xAI Grok 4.5; legal authorship and ownership
remain human matters.

## Hard constraints

1. Preserve every source page and original extracted value. Corrections are
   append-only amendments.
2. Emit a record or explicit exception for every input and failed stage. Never
   drop, invent, or silently normalize a record into passing status.
3. Require genuinely independent evidence for consensus. Same-model roles and
   provider confidence are not independent proof, and independence is a property
   of the model vendor, not the transport: a router serving another vendor's
   model is that vendor. Self-reported confidence is retained as provenance and
   is never a term in a decision.
4. Require applicable arithmetic, attribution, completeness, and final-review
   gates separately.
5. Keep grayscale masters and create binarized variants beside them. Escalate
   JBIG2 numerics.
6. Treat LLM outputs, mappings, groupings, client workbook imports, and review
   reduction as proposals until authorized append-only changes are applied and
   the affected controls are rerun.
7. Keep protected and unknown review reasons in final review. Batch decisions
   cannot clear arithmetic, provider/schema, reassembly, handwriting, identity,
   review-flag, missing-field, or unresolved-evidence findings.
8. Admit only approved, provenance-linked, review-clear facts to canonical
   retrieval or target staging.
9. A control that processed nothing has not passed. An empty or unusable input
   produces an explicit exception and a refusal, never a clean result: this
   applies to graph builds, independent corroboration, retrieval indexes, and
   every side-channel input (GL exports, payment files, reference exports).
10. A value outside its plausible range is a misread figure, not a policy
    question. Route it to review rather than classifying it, and never infer a
    unit, a date order, or a country from magnitude alone.
11. A run authorization to transmit client source data covers only the named
    run, its named source set, and the configured external providers. Record
    that non-secret scope under `RUN/logs/` before the first provider call.
    It removes no phase prerequisite and never authorizes auto-acceptance,
    carry-forward, canonical deployment, CRM execution, delivery, or a change
    to evidence/approval status. Execute every applicable enabled lane; when a
    lane cannot run, retain its explicit blocked/exception result and include it
    in lane coverage rather than skipping, guessing, or creating a substitute.

## Documentation routing

- Every environment setting: `references/runtime-configuration.md`.
- Every CLI: `references/command-line-reference.md`, the parser-derived
  `references/cli-help-catalogue.md`, and executable `--help`. Every argument
  carries help text; recurring option wording lives once in `scripts/cli_help.py`
  and the release check fails if any argument or generated catalogue entry stops
  explaining itself.
- Phase gates: `references/workflow-gates.md`.
- Operations artifacts and state: `references/operations.md`.
- Images/detectors: `references/image-intelligence-controls.md`.
- Tables: `references/table-comprehension.md`.
- Allocation: `references/allocation-policy.md`.
- Semantic mappings: `references/semantic-schema-discovery.md`.
- Canonical load/retrieval: `references/canonical-deployment-retrieval.md`.
- Common CRM import/write preparation: `references/crm-write-readiness.md`.
- Connected-LLM image intake: `skills/record-intake/SKILL.md` and
  `references/visual-ingestion.md`. Intake is an optional separate proposal-only
  journal, not canonical mutation or pipeline clearance. Planned governed writes
  and vendor-neutral analytics: `references/business-data-platform-roadmap.md`.
- Choosing which optional lanes a corpus needs: `skills/pipeline-selection/SKILL.md`
  and `scripts/pipeline_plan.py`. A proposal measured from retained artifacts. It
  authorizes nothing, clears nothing, and reports an unmeasured lane as undecided
  rather than defaulting it either way.
- MCP/API setup, deployment, client registration, acceptance, use, and
  troubleshooting: `skills/mcp-api-operations/SKILL.md`, which routes to
  `references/mcp-production-integration.md` and the relevant contracts.
- Sampling claims: `references/qa-sampling.md`.
- Adopting a method that settles open fields: score it against a held-out
  answer key first, and report the chance baseline beside the score. A
  resolution count is not a result -- a corpus-frequency tiebreak offered
  1,880 free resolutions at 58.1% and was deleted. See
  `references/qa-sampling.md`.
- Superpowers model, batching, review, and verification budgets:
  `references/agent-cost-controls.md`.
- Operating a run rather than changing the repository:
  `skills/business-doc-operations/SKILL.md`. Its lane table maps all 76
  workflow-lane commands to their phase with optional/disabled status. The
  parser-derived CLI catalogue also includes release, maintenance, and utility
  entry points that are intentionally outside that lane count. Its task guides
  cover setup,
  the pipeline sequence, extraction, cross-checking, handwriting, the business
  controls, the evidence graph and relationship lanes, the client review lane,
  review reduction, client review, monitoring, delivery, troubleshooting,
  maintenance, and subagents.
  `scripts/agent_surface_check.py` keeps every command it names in step with the
  code; it runs in the release gate and in the Claude Code `PostToolUse` hook.

## Cost-controlled Superpowers

Use `references/agent-cost-controls.md` whenever Superpowers, implementation
plans, subagents, or code review are involved. It is the repository override
for orchestration cost: bounded and tightly coupled work stays with the main
agent; related changes are batched; subagents use isolated context and explicit
model/effort routing; and full gates run at stable batch boundaries. An
independent whole-branch audit is recommended when another operator is
available, but a single authorized operator may complete the documented
self-audit and quality gate. These savings never weaken the hard constraints or
the quality gate below.

Before accepting client records, follow `docs/CLIENT_USER_GUIDE.md` and preserve
the originals in a dedicated source area. Use `examples/README.md` only for
clearly labelled fictional samples.

## Operating boundary

The optional visual-intake extension is a separate pre-pipeline proposal store:
seven MCP/JSON API operations retain original images, schema-mapped candidates,
and receipts; the local source-only exporter preserves the entire session for
normal profiling/intake. Approved canonical data remains read-only. Do not
claim a pending proposal is published, count its self-reported model as an
independent voter, or mistake byte-intact source export for review clearance.
Use `skills/record-intake/SKILL.md` for this boundary and
`references/business-data-platform-roadmap.md` for unimplemented create/update,
client capture, review integration, and advanced analytics. Deployment still
requires an approved non-empty snapshot plus separate source-flow authority.

Version 1.0.0 is the stable implemented control baseline and vendor-neutral
post-review boundary. Current `main` may also contain backward-compatible work
listed under **Unreleased** in `CHANGELOG.md`; those additions are not part of
the immutable 1.0.0 release record. The stable baseline includes immutable
intake, scan profiling, image variants,
reassembly and duplicate proposals, OpenAI/Gemini/OpenRouter extraction lanes,
source-native table comprehension, Document AI corroboration, page-complete
Google Cloud Vision handwriting OCR proposals, consensus, arithmetic,
validation, HTR-result reconciliation, entity/attribution/
completeness/sampling controls, proposal-only review reduction, reproducible
client packages, canonical planning/staging, and approved-fact local retrieval.
It also includes reusable JSON evidence-graph commands and a bounded
configured-primary/configured-buddy iterative relationship-proposal lane. These remain
immutable, provenance-linked proposals and cannot substitute for authoritative
GL, payment-system, or client-authorization evidence.

For the iterative lane, preserve every client response as reasoning-only
context, retain primary and buddy raw responses, and use up to three bounded
passes. An exhausted provider batch must retain its exact source document IDs
as an exception for a separate no-clobber recovery run; never silently retry
forever or treat an exception as an inferred fact.

General client comments may be parsed by `client_input_comments.py` and supplied
to `table_comprehension_corpus.py --client-context`. Preserve them verbatim and
hash-bound as reasoning-only context. They may explain terminology, priorities,
or questions but are never source evidence, authorization, independent
consensus, or control clearance.

`google_handwriting_ocr.py` uses Google Cloud Vision handwriting-capable
`DOCUMENT_TEXT_DETECTION` on every retained page and binds returned words only
to separately detected normalized regions. Preserve page renders, raw responses,
region geometry, provider identities, and all failures. Treat its output as one
Google provider-group HTR vote; it cannot validate itself, and another Google
model or role is not independent.

Apply the documented convergence threshold to iterative and cross-packet
proposal runs. A material result is a new, source-backed, buddy-`confirmed`
relationship; terminate when a completed pass falls below the threshold. Later
cross-packet discovery may inspect only newly exposed unresolved neighborhoods.

After in-packet refinement, `client_review_cross_packet.py` may inspect
documents without an in-packet buddy candidate for new source-visible links and
independently verify documents with an in-packet buddy candidate against a
bounded retained cross-packet neighborhood. Bind context, records, prior
artifacts, graph, raw responses, provider statuses, and exact exception IDs;
configure primary/buddy provider, model, credential, timeout, and retry values
only through `.env`, and require distinct providers before any request. Defer
cap-limited or identifier-less coverage explicitly. A verification can
flag `close_needs_review`, `conflict`, or `unsupported`, but never rewrites the
existing proposal. Rebuild the graph as a new overlay. It is never client
evidence, authorization, or a completeness pass.

`client_review_exception_resolution.py` may create bounded, source-cited
primary/buddy proposals from supplied reassembly, validation, and attribution
exceptions. When preserved client responses exist, supply their
`client_review_context_v1` as hash-bound reasoning-only context; it is never
source evidence or independent consensus input. The lane is disabled by default
and never clears its originating control. It must count every input finding,
send both reviewers the same exception/source/context packet, and explicitly
retain missing-source, rejected, capped, oversized, and provider-failed work.
The final-run client-review flag may include arithmetic/reassembly next-step
proposals, but those findings remain protected and review-required.
Every retryable provider, oversized, or cap-deferred exception must retain the
exact original named findings, kinds, and exception IDs; a no-clobber retry may
not reconstruct control context from document IDs alone.

`ai_simulated_client_review.py` is a separate, disabled-by-default draft-comment
lane. It requires supplied source-evidence artifacts (`--evidence`) and may use
a configured provider/model over a complete final-review card plus optional
hash-bound reasoning-only context, but every visible result must
say **“AI client reviewed (simulated; not client authorization)”**. It produces
new JSON/exception/raw/CSV/XLSX artifacts only; it never changes an issued or
returned workbook, impersonates the client, authorizes a fact, applies an
append-only amendment, clears a protected finding, or advances canonical/CRM
staging. Every item must receive a source-cited simulated comment or an explicit
exception; protected items remain human-review-required regardless of output.
The lane de-duplicates packet evidence but binds every item to its own source
references; a quote must occur inside one scalar value of a source record
assigned to that item. Preserve its atomic `checkpoint.json`. Resume only when
the checkpoint hashes match the queue, evidence, and complete resolved
configuration. Retry retained provider, size, cap, missing-source, or explicit
post-run evidence-audit failure cards only
as a new `--retry-exceptions` overlay; never overwrite or silently merge the
original run.

When a bounded iterative or cross-packet pass completes, freeze its output
directory before finalization. Use the retained exception manifest to run one
separate, no-clobber retry pass for every provider-retryable exception, then
reconcile the retry overlay with the original pass. Keep data, cap, identifier,
and unsupported findings explicit; they are not silently converted into
successes. Final proposal and reconciliation manifests are generated only
after that retry reconciliation and all applicable controls pass.

Do not describe the following as implemented production outcomes without their
client-authorized integration and acceptance evidence: additional independent
OCR/HTR providers, production detector or template-comparison deployment,
target CRM delivery, public tenant-isolated retrieval, hosted vector/reranking,
or production accuracy/reduced-review performance.

This repository ends at verified no-send staging/import packages and the
approved-fact Claude/ChatGPT MCP/API surfaces that expose them. Target-specific
receivers, importers, reconciliation, rollback, and live write adapters belong
on the destination system side.

Use a new output directory for every run or retry. Initialize it with
`scripts/run_workspace.py init` and invoke every pipeline-writing command with
`scripts/run_workspace.py run`; caches, throttle state, logs, raw responses, and
retry overlays must stay beneath that same run root. Preserve raw responses,
provider/schema failures, source item indexes, evidence references, and partial
successes. Resume only through a supported verified mechanism. Use existing
scripts and shared provider/review-protection/no-clobber logic rather than
duplicating controls.

## Strict full-run lock

For a full run, record the operator's non-secret transmission authorization in
`RUN/logs/run_authorization.md` before the first external provider invocation:
the run identifier, source-set identifiers/hashes, authorized provider families,
date/time, and the statement that all in-scope items may be sent. Do not record
credentials or raw client content in this note. An authorization means the
operator has approved sending every in-scope item to those configured providers;
it is never an instruction to omit a page, ignore an exception, manufacture an
output, or treat a proposal as accepted.

Execute the documented phase order. A later-phase, review, canonical, or
delivery command may start only after its required upstream artifacts are
complete and their exceptions have been read. Independent lanes within the same
phase may run together only when neither consumes the other's output. Do not
create an implicit "preliminary" substitute to overlap later work with an
unfinished upstream lane. A command refusal, schema mismatch, or zero-coverage
result is a retained integration finding: fix or explicitly block it, run the
documented no-clobber recovery where applicable, and never hand-create a
passing-shaped artifact.

A setting that is documented, defaulted, and exported is not thereby read.
`release_check.py` now refuses a release where `.env.example` names a setting no
script reads, whether written out or assembled at runtime -- the check that did
not exist while a 716-page lane ran single-threaded because its CLI passed no
execution arguments at all.

That check ran in one direction only. It now also refuses a release where a
script reads a setting `.env.example` names nowhere -- an operator configures a
run from that file, so an undocumented setting's default is the only value it
will ever have and nobody knows the knob exists.

Two more rules that were written against `scripts/*.py` now walk the whole tree.
Fourteen parsers live in `scripts/client_review/` behind thin entry points, so
the rule that every command-line argument explains itself did not apply to a
fifth of the repository's parsers. Alongside it, every module and every
module-level public definition must carry a docstring: the contract here lives
in prose next to the code, and a reader building a pipeline learns what a lane
refuses by reading it. `main`, `__init__`, and helpers defined inside another
function are exempt, because requiring a docstring there produces filler.

The same blind spot has now appeared three times -- the lane catalogue, the CLI
help rule, and the docstring rule each missed code behind a thin entry point
that delegates to a module. When a check globs, check what it does not reach.

A control that reads a field name the extraction schema does not emit reports
the same thing as a control whose corpus had nothing to find. Three did:
attribution rank 1 asked for `ack_number` and reported 26 attributed documents
where the true figure was 137; the selling-location hierarchy asked for `branch`,
`office`, `letterhead_city` and `salesperson` and resolved 0 of 716 while the
corpus carried `sales_representative_name` on 112; completeness read
`credits_applied` and `payment_status` and ran aging closure on permanently empty
inputs. Every literal field name a control looks up must now be either a field in
`extraction_schema` or an entry in its `NON_SCHEMA_RECORD_ALIASES` registry with
the reason it cannot come from extraction. The test suite enforces it.

The same shape appears wherever a decision is made by matching a substring of
prose rather than a structure. The review gate suppressed 2,590 findings because
their explanation contained the word "handwriting" — including 838 lane failures
and 1,068 findings a control had marked blocking. Grouping labelled 691
documents `unclassified_document_family` because it read `document_type` from the
extraction consensus, which correctly says `unknown` there, instead of from the
classification consensus that had accepted 686 of them; its text fallback then
invented 442 `purchase_order`, `invoice_like` and `statement_like` groups out of
words in reason strings. Match on the field, the flag, or the artifact that owns
the answer — never on the sentence describing it.

A question put to a client is only as good as the page shown with it. A pack
asked about the layout of 285 documents and led with the group's first page,
which was blank; the client answered "Blank Page. Can be ignored." for all of
them, twice, and was right about the only page they were shown. Four of the 285
were blank and 194 carried more than fifty populated fields. `scan_profile.py`
had flagged that page `near_blank` from the first hour of the run and nothing
read it. Pass `--scan-profile` to the review lane, show several examples spread
across a group rather than one, and treat a wrong answer to a badly evidenced
question as the pack's defect rather than the client's.

`near_blank` is a quality warning, not a document disposition. Its floor of
0.002 ink coverage also flags a rotated commission report carrying a full table,
so it is safe for declining to *show* a page and unsafe for excluding one from
processing. Only zero ink coverage was blank on the corpus that produced it.

Qualify a second reader by the values it returns, never by how many rows it
returned. A candidate that matches the reference engine's row count can still
have misread most of the content, including digits inside financial amounts,
and a wrong amount does not abstain -- it disagrees, and every disagreement it
manufactures costs a person an adjudication. Run `engine_agreement.py` over a
handful of matched pages before committing a corpus to a new engine, compare
only pages both engines read and read the same way, and read value recall and
field-exact agreement rather than the row-count ratio.

Before completion, run `run_workspace.py audit` and `run_lane_coverage.py` with
`--require` for every lane selected for the engagement. Reconcile each absent or
blocked lane to a retained exception or a documented missing-prerequisite
limitation. The final review queue must include all review-bearing successful,
failed, and recovery artifacts before any claim about the run.

The shared retry runtime honors the global `LLM_FAIL_FAST_ON_PROVIDER_QUOTA`
setting. When enabled, a recognized account/model quota response opens a
process-local provider circuit; later work is retained as explicit provider
exceptions for a separate no-clobber retry rather than repeatedly called.

Before a Google-backed run, use `python scripts/reauthorize_google.py` to
establish Application Default Credentials. Document AI and handwriting OCR then
mint their own short-lived token per run through
`reauthorize_google.access_token`; there is no token setting in `.env`, and an
explicitly exported token still takes precedence. Keep the
optional short-lived Document AI token file and any service-account credentials
outside the repository. If `GOOGLE_APPLICATION_CREDENTIALS` is set, verify the
external path; otherwise Vertex uses refreshed ADC.

When a client workbook returns, preserve issued and returned copies separately,
validate them together with `client_review_package.py import-decisions
--issued-workbook`, and treat the result as proposal-only. An authorized
operator must compile and review the exact impact with
`client_decision_compile.py`, authorize patches separately, create only those
append-only mappings/amendments/policies, and rerun the
affected lane plus downstream consensus, arithmetic, validation, applicable
business controls, completeness, and final review before claiming application.

## Quality gate

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHON_BIN=.venv/bin/python bash scripts/quality_gate.sh
```

Run the gate in a normal terminal, CI worker, or other process manager without
an interactive-output capture deadline. The script has no repository-imposed
wall-clock cutoff; CI has its separate 45-minute job safety limit.

Run applicable acceptance scripts after the core gate. Before delivery,
regenerate every changed tracked document, render every changed PDF to page
images, and visually verify margins, boundaries, tables, headers, wrapping, and
legibility.

Read `BRANCHING.md` before creating, committing, merging, pushing, or deleting a
branch. Keep `main` releasable and preserve unrelated user changes. For stable
release work, read `RELEASE.md` too: use the `release/vMAJOR.MINOR.PATCH`
branch type, merge it through reviewed CI, and create an immutable matching
`vMAJOR.MINOR.PATCH` annotated tag only from the resulting `main` tip. Never
retarget or reuse a stable tag.

## Operating facts that have cost a run

Approvals are keyed to template fingerprints derived from the emitted label set,
so re-extraction orphans every prior approval: check fingerprint overlap before
sending proposals for approval and before running any mapping-dependent control.
Resolve a lane's model from its own lane setting, and treat the retained raw
response as the authority over any configuration file. Supply table lanes with
independent evidence or their conditional buddy check is unavailable rather than
unnecessary. Never resume across a changed model or reasoning effort. Hand the
gate every review-bearing artifact; a finding you do not pass in is absent from
the queue, the grouping, and the client pack. Measure with the code's own
helpers over the model's emitted output only, on matched page sets -- a retained
record may echo the prompt, and a rate over a different population is not a
comparison.

**Reconcile from retained artifacts before re-reading anything.** Every provider
response is kept under `RUN/providers/raw/`, and the independent non-LLM
extractor keeps the *whole page* rather than the fields the schema named, so a
value the pipeline dropped is usually still on disk with its layout. Grep the
retained responses for the column heading, look for a printed join key the
records already carry, and only re-extract when all of that fails -- affected
pages only, amendment recorded first. On one corpus that distinction was 145
pages of provider spend against zero. A recovered value is single-extractor
evidence, never vendor agreement.

**Agreement is not accuracy.** Every defect found by reading source pages on
that corpus was a correct transcription placed in the wrong field: a
"Commission Split" column read as a rate, a SPECIFIER read as a brand. Both
vendors agree on those, because both read the page correctly and both assumed
the same thing about the column, so the value arrives accepted. A field the
schema never defines is a field engines guess at -- 35 of 48 line fields were
undefined when that was first checked -- and a printed column with no field at
all goes into whichever field looks closest. Check `FIELD_DEFINITIONS` coverage
and the document's own arithmetic; neither a better model nor another vendor
finds this class.

The full checklist is `skills/business-doc-operations/tasks/lane-checklist.md`.
