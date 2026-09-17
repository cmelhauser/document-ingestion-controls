---
name: business-doc-ingestion
description: Operate or extend this evidence-preserving business-document pipeline, including source-image proposal intake, independent extraction and review controls, canonical staging, and approved-fact MCP/API reporting. Route live runs, connector operations, and visual intake to their dedicated skills; no skill authorizes automatic approval or CRM mutation.
---

# Business Document Ingestion

Use this skill to operate or modify the repository without weakening its audit
boundary. Version 1.0.0 is the stable implemented control baseline and
vendor-neutral post-review data boundary. Current `main` may also contain the
backward-compatible capabilities listed under **Unreleased** in `CHANGELOG.md`;
those additions are not part of the immutable 1.0.0 release record. Live
provider selection, client production accuracy, target CRM delivery, and public
tenant-isolated retrieval require separate client authorization and acceptance
evidence.
The implemented proposal layer also provides a deterministic JSON evidence
graph (`build`, `query`, `path`, `candidates`, `contradictions`, `schema`) and a bounded
environment-configured primary/buddy iterative relationship lane. Both produce
immutable overlays and proposal states only; they do not create authoritative
GL/payment facts or authorization.

Read `LICENSE`, `NOTICE`, and `CONTRIBUTING.md` before distribution or accepting
contributions. Courtesy credit to `theonlymuffinbot` describes collaborative AI
tooling context; legal authorship and ownership remain human matters.

## Invariants

1. Preserve every source page and original extracted value.
2. Record corrections as append-only amendments linked to evidence.
3. Emit a record or exception for every input and failed stage; never drop,
   invent, or silently normalize a record into passing status.
4. Require genuinely independent evidence for consensus. Same-model prompt roles
   and self-reported confidence are not independent proof. Independence belongs
   to the model vendor, not the transport, so a router serving another vendor's
   model counts as that vendor.
5. Require applicable arithmetic, attribution, completeness, and final-review
   gates separately.
6. Keep grayscale masters; create binarized variants beside them. Escalate JBIG2
   numerics.
7. Treat LLM output, mapping discovery, grouping, client workbook imports, and
   review reduction as proposals until an authorized change is applied and the
   affected controls are rerun.
8. Allow only approved, provenance-linked, review-clear facts into canonical
   retrieval or target staging.
9. A control that processed nothing has not passed. Empty or unusable input
   yields an explicit exception and a refusal, never a clean result.
10. A value outside its plausible range is a misread figure, not a policy
    question. Never infer a unit, a date order, or a country from magnitude.
11. Transmission authorization covers only its named run, source set, and
    configured providers. It never bypasses a phase prerequisite, permits a
    skipped item, accepts a proposal, or authorizes production actions.

If a request conflicts with an invariant, explain the conflict and stop before
changing evidence or approval state.

## Start here

Determine the user's current phase and available evidence. If it is unclear,
establish:

1. Corpus size, date range, document families, scan quality, and source owner.
2. Whether every monetary amount must tie to an ACK, job, order, project, claim,
   or other reference, and whether a reference export exists.
3. Whether matching GL and payment/remittance exports exist.
4. The business questions and definition of done.
5. Privacy, retention, provider, region, cost-cap, reviewer, golden-set, and
   target-system decisions.

Before receiving client records, follow `docs/CLIENT_USER_GUIDE.md` and preserve
originals in a dedicated source area.

## Route to the authority

Read the relevant file completely before operating or modifying its phase:

| Work | Required reference |
|---|---|
| Any input/output, review header, workbook, or runbook contract | `references/artifact-contracts.md` |
| Environment settings | `references/runtime-configuration.md` |
| CLI selection or interface | `references/command-line-reference.md`, parser-derived `references/cli-help-catalogue.md`, and executable `--help` |
| Phase order and exit criteria | `references/workflow-gates.md` |
| Manifests, state, adapters, privacy, exports | `references/operations.md` |
| Reassembly, duplicates, arithmetic amendment | `references/adjudication.md` |
| Validation, HTR, and review automation | `references/automation-controls.md` |
| Images, handwriting detectors, classification proposals | `references/image-intelligence-controls.md` |
| Source-native tables and corpus rereads | `references/table-comprehension.md` |
| Semantic mappings and Places candidates | `references/semantic-schema-discovery.md` |
| Commission/sales-credit policies | `references/allocation-policy.md` |
| Canonical deployment, load, and retrieval | `references/canonical-deployment-retrieval.md` |
| Common CRM import/write preparation | `references/crm-write-readiness.md` |
| Typed business analytics, governed record delivery, and client YAML deployment | `references/business-data-platform.md` and `skills/record-maintenance/SKILL.md` |
| Production MCP activation and client registration | `references/mcp-production-integration.md` |
| Deciding which optional lanes a corpus needs before spending on them | `skills/pipeline-selection/SKILL.md` |
| MCP/API setup, deployment, acceptance, use, or troubleshooting | `skills/mcp-api-operations/SKILL.md` |
| Connected-LLM visual source intake and candidate records | `skills/record-intake/SKILL.md`, `references/visual-ingestion.md` |
| Sampling or accuracy language | `references/qa-sampling.md` |
| Scoring a resolution or adjudication method before adopting it | `references/qa-sampling.md` (*The held-out answer key*) |
| Superpowers/subagent cost and review routing | `references/agent-cost-controls.md` |
| Attribution | `references/attribution.md` |
| Data or field model | `references/data-model.md`, `references/extraction-schema.md`, `references/derived-field-schema.md` |

Use `docs/TECHNICAL_DOCUMENTATION.md` for the complete architecture and
operating runbook. Use `examples/README.md` only for clearly labelled fictional
field-shape samples.

This repository owns the verified no-send canonical/common CRM package
boundary. Target-specific receivers, importers, and live write adapters belong
on the destination system side rather than here.

## Phase map

| Phase | Work | Exit condition |
|---|---|---|
| 0 | Discovery and authorization | Scope, decisions, evidence, providers, privacy, and acceptance recorded |
| 0.5 | Scan profiling | Corpus profile and branch routing retained |
| 1 | Intake and preparation | Source retained; every page mastered; variants/reassembly reviewed |
| 2 | Classification | Document family and party role assigned or explicitly escalated |
| 3 | Extraction and reconciliation | Independent evidence reconciled or each unresolved field queued |
| 3H | Handwriting | Regions retained; independent readings reconciled or escalated |
| 3A | Attribution | Every in-scope dollar attributed or registered with reason/amount |
| 4 | Completeness | Sequence, calendar, GL, and aging/payment evidence accepted |
| 4Q | Sampling and final review | Sampling limitations stated; exhaustive final queue clear |
| 5 | Analytics | Phase 0 questions answered within gate and completeness limits |
| 6 | Canonical handoff | Approved export, load plan, staging, and retrieval controls accepted |
| 7 | Optional email corpus | Separately scoped privacy and evidence workflow accepted |

Do not advance past a failed exit condition.

## Running a phase rather than changing it

This file governs **changing the repository**. To operate a run against real
documents, use [`skills/business-doc-operations/SKILL.md`](skills/business-doc-operations/SKILL.md).
Its lane table maps every one of the 76 workflow-lane commands to the phase
above and says whether each is optional or disabled by default. The complete
CLI catalogue also indexes release, maintenance, and utility entry points that
do not belong to a run lane. The skill routes to task guides:

| Phase | Guide |
|---|---|
| 0 setup, settings, file placement | `tasks/setup.md` |
| 0.5-4Q the ordered command sequence | `tasks/run-pipeline.md` |
| 3 extraction, tables, layout | `tasks/extraction.md` |
| 3 corroboration and independent verification | `tasks/cross-checking.md` |
| 3H handwriting | `tasks/handwriting.md` |
| 3A/4/4Q attribution, allocation, completeness, sampling | `tasks/business-controls.md` |
| evidence graph and relationship lanes | `tasks/evidence-graph.md` |
| 4Q review reduction | `tasks/review-reduction.md` |
| client workbook issue and return | `tasks/client-review.md` |
| run monitoring and spend | `tasks/monitor.md` |
| 5/6 analytics, canonical, CRM, delivery | `tasks/deliver.md` |
| any refusal | `tasks/troubleshooting.md` |
| repository gates and branches | `tasks/maintenance.md` |
| delegating work | `tasks/subagents.md` |

`scripts/agent_surface_check.py` checks every command named in that folder
against the live scripts and the CLI index, so a rename cannot leave those
instructions stale. It runs in the release gate and in the repository's Claude
Code `PostToolUse` hook.

After Phase 6 facts are approved, use
[`skills/mcp-api-operations/SKILL.md`](skills/mcp-api-operations/SKILL.md) to set
up, deploy, connect, accept, query, monitor, or stop the local MCP, remote MCP,
or REST API. That skill does not authorize deployment or CRM mutation and sends
unfinished runs back to the operating skill.

For separately authorized new image intake, use
[`skills/record-intake/SKILL.md`](skills/record-intake/SKILL.md). Its optional
journal is a pre-pipeline source boundary, not an approved-fact store. It cannot
approve, apply or publish records. Its local source-only export utility retains
the full journal session and prepares sources for the existing profiling/intake
commands, never an extraction or clearance substitute. Read the [roadmap](references/business-data-platform-roadmap.md)
before extending the create/update or analytics boundary.

## Full-run execution lock

For a requested full run, create a non-secret `RUN/logs/run_authorization.md`
before the first provider call. It must name the run, source-set
identifiers/hashes, authorized provider families, timestamp, and whether the
operator authorized all in-scope source items to be sent. Never place a
credential or raw client content in that note. A transmission authorization
authorizes those sends only; it does not accept a proposal, clear a control,
authorize an amendment, enable auto-accept/carry-forward, or authorize
canonical, CRM, or delivery actions.

Treat the documented phases as a strict state machine. Complete the required
upstream artifacts, read their exception results, and satisfy the phase exit
condition before starting a dependent lane. Only lanes that are genuinely
independent and in the same phase may run concurrently. Never launch a later
review, canonical, or delivery lane as an unstated preliminary pass while an
upstream dependency is still running.

Run every applicable lane selected for the engagement. A refusal, input-schema
mismatch, provider failure, missing prerequisite, or zero-coverage result is a
retained item to repair or explicitly block; it is never permission to skip the
input, synthesize a passing artifact, or silently continue. Close the run with
`run_workspace.py audit`, `run_lane_coverage.py --require` for every selected
lane, and a final-review queue built from every review-bearing base and recovery
artifact.

## Operate with repository scripts

Do not duplicate control logic. Discover commands through
`references/command-line-reference.md`; run `python scripts/COMMAND.py --help`
before execution. The normal sequence is:

For a controlled document run, first initialize a fresh root with
`scripts/run_workspace.py init` and invoke every pipeline-writing command
through `scripts/run_workspace.py run`. Generated artifacts, raw responses,
cache, throttle state, logs, and retries stay below that root; source inputs and
credentials are the intentional external read-only boundaries.

1. `scan_profile.py`, `ingest_pages.py`, `reassemble_pages.py`, and
   `preprocess_pages.py`.
2. `operations.py manifest --config-from-env` and manifest-bound stage state.
3. Separate `llm_adapter.py --lane consensus_primary` and
   `--lane consensus_secondary` invocations, or separately authorized
   independent adapters.
4. Optional table comprehension and Document AI corroboration under their
   reference boundary.
   Optional Google Cloud Vision handwriting OCR may then read separately
   detected regions on every retained page. It is one proposal-only HTR
   provider-group vote and cannot validate itself.
5. `consensus.py`, `arithmetic_check.py`, `adjudicate.py`, and
   `validate_extraction.py`.
6. Applicable address, HTR, entity, attribution, completeness, and sampling
   controls.
   If authoritative ledger or payment exports are unavailable, run
   `inferred_controls.py` as a separate proposal-only lane; its hash-bound
   outputs and exceptions never clear completeness or authorize canonical load.
   Build `client_review_context.py` from preserved responses before any
   reasoning call, then use the disabled-by-default `client_review_inference.py`
   only for bounded, evidence-cited reference discovery. Client context is
   excluded from independent consensus.
   For client comments supplied outside a returned workbook, build the same
   reasoning-only context with `client_input_comments.py` and pass it to
   `table_comprehension_corpus.py --client-context`; comments never become
   document evidence, authorization, or control clearance.
   `client_review_exception_resolution.py` may create bounded, source-cited
   primary/buddy proposals from supplied reassembly, validation, and attribution
   exceptions. Supply the preserved `client_review_context_v1` whenever client
   responses exist; it is hash-bound reasoning-only context, not source evidence
   or consensus input. The lane never clears the originating control; rerun that
   deterministic control after reviewing any append-only overlay. Confirm that
   its summary accounts for every input finding and that unmatched, rejected,
   capped, oversized, and provider-failed work remains in the companion artifacts.
   After the iterative primary/buddy artifacts and deterministic graph exist,
   `client_review_cross_packet.py` may make bounded convergence-controlled
   cross-packet proposal overlays: discover links for documents still unresolved in-packet and
   independently verify prior in-packet buddy proposals against retained
   documents sharing source-visible identifiers. Follow-up discovery is limited
   to newly exposed unresolved neighborhoods and stops when no material new,
   buddy-confirmed source-backed relationship is found. The configured primary
   and buddy providers must differ; the selected providers/models are retained.
   A verification may flag a
   conflict or unsupported proposal but never rewrites it. Both paths remain
   outside consensus, completeness, and authorization.
7. `final_review_queue.py`, reviewer exports, and optional proposal-only review
   reduction.
8. Authorized append-only changes followed by affected-lane and downstream
   reruns.
9. Only after a clear final gate: canonical export, deployment/load planning,
   no-send staging, and approved-fact retrieval.

Use a new output directory for every run or retry. Preserve raw responses,
handoffs, exceptions, source item indexes, evidence references, and partial
successes. Resume only through a command's supported verified mechanism.

## Provider and reasoning boundary

The run-level LLM registry supports OpenAI, Gemini on Vertex AI, and isolated
OpenRouter lanes. Configure them in the private root `.env`; every tracked
setting is explained in `references/runtime-configuration.md`.

- One provider is fixed for each adapter invocation.
- Primary and secondary consensus lanes must use genuinely independent
  providers.
- Same-model profile/extract/audit roles may improve a proposal but are not
  consensus.
- Raw responses, selected model/settings, usage, retries, and provider/schema
  failures remain retained.
- Provider confidence is non-decisional provenance.
- Shared throttling, request-size preflight, bounded retries, caching, and
  circuit breakers change resource behavior only; they never approve facts.

Before Google-backed work, run `python scripts/reauthorize_google.py`. Keep its
short-lived token file and any service-account credential outside the
repository. Use separate restricted credentials for Vertex ADC, Document AI,
Address Validation, and Places.

## Source-native table boundary

Prefer `table_comprehension.py` and `table_comprehension_corpus.py` for source
tables. Carry only observed source-layout context across the corpus. Preserve
the initial profile and readings. A changed reread is an amendment proposal.
Only an exact client-approved registry rule can map a source label. Document AI
may corroborate a conditional page-local audit and deterministic reconciliation;
it cannot map or clear financial or identity facts.

The layout-aware adapter remains a review-only display workflow. Do not promote
its client layout as a canonical mapping.

## Review reduction and client return

Card review, cross-record search, the full-dataset agent, grouping, safe
consolidation, and the small client package are proposal-only. They retain every
underlying queue item. All paths use `scripts/client_review/protection.py`: unknown
reasons fail closed, protected categories cannot carry forward, and a proposed
update must target the reviewed field and cite retained evidence.

When the client returns a workbook:

1. Preserve the issued workbook and returned copy separately and unchanged.
2. Import with `client_review_package.py import-decisions RETURNED.xlsx
   --issued-workbook ISSUED.xlsx --out client_decisions.json`.
3. Confirm completeness and no invalid decisions.
4. Treat the result as proposals only.
5. Build an explicit change catalog and run `client_decision_compile.py
   compile`; review its exact impact and rerun preview.
6. Complete the default-defer template and run `client_decision_compile.py
   authorize`. Apply only authorized append-only mappings, amendments, template
   rules, document-type rules, policies, or source-quality actions.
7. Rerun the affected lane, then consensus, arithmetic, validation, applicable
   business controls, completeness, final review, and package generation.

Never claim the decisions are applied until the final gate passes. Batch
decisions cannot clear arithmetic, provider/schema, reassembly, handwriting,
identity, review-flag, missing-field, or unresolved-evidence items.

## Quality and delivery

Run the complete gate from `AGENTS.md`. It requires dependency integrity, all
tests, 100% statement and branch coverage, Ruff checks, release validation, and
applicable acceptance runners. Do not report 100% unless the strict report
passes.

When a tracked PDF source changes:

1. Regenerate the Markdown/LaTeX/PDF triple with repository scripts.
2. Render every changed PDF to page images.
3. Inspect all pages for margins, boundaries, tables, headers, wrapping, and
   legibility.
4. Do not hand off a PDF that has not passed visual verification.

## Change discipline

Read `BRANCHING.md` before branch operations. Preserve unrelated user changes.
For stable release work, also read `RELEASE.md`: prepare only on
`release/vMAJOR.MINOR.PATCH`, merge the reviewed branch to `main`, and create
the immutable matching annotated tag only from that exact `main` tip. Do not
move a stable tag; correct a failed release with a new PATCH version.
When changing an interface, update code, tests, artifact contracts, runtime or
command documentation, relevant phase references, agent routing, changelog,
and generated documents together. Extend shared provider, review-protection,
no-clobber, and validation logic rather than forking it.

The durable release boundary is in `RELEASE.md` and `BRANCHING.md`; current
verification evidence is in `docs/REPOSITORY_AUDIT.md`; current continuation
state is in `HANDOFF.md`.

When Superpowers is used, apply the repository's cost-controlled profile:
prefer main-agent execution for bounded or tightly coupled changes, batch
related work, isolate subagent context, choose the least costly adequate model,
and use the model/effort and self-audit rules in that profile for branch review.
