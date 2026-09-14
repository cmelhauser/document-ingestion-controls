# ChatGPT Project Instructions

Start with [`SKILL.md`](SKILL.md), then [`AGENTS.md`](AGENTS.md). Use
[`HANDOFF.md`](HANDOFF.md) only for current state. Read the relevant phase file
in `references/` completely before acting; read
[`references/artifact-contracts.md`](references/artifact-contracts.md) before
changing any interface, workbook, output, or runbook.

Preserve every source page and original value. Corrections are append-only
amendments. Every failure or unresolved item remains an explicit exception, and
a control that processed nothing has not passed: empty or unusable input yields
a refusal, not a clean result. Same-model roles and provider confidence are not
independent consensus; independence belongs to the model vendor, not the
transport, and self-reported confidence is provenance rather than a decision
term. A value outside its plausible range is a misread figure, not a policy
question -- never infer a unit, a date order, or a country from magnitude.
Require applicable arithmetic, attribution, completeness, and final-review gates
separately. LLM outputs, mappings, grouping, client workbook imports, and review
reduction are proposals until an authorized change is applied and the affected
controls are rerun.

The optional iterative relationship lane uses environment-configured independent
primary and buddy providers over the same source-bound packet. Preserve every
client response as reasoning-only context, retain raw responses, cap iterations
at three, and retain exact source-document IDs for exhausted batches. It cannot
create GL/payment evidence, authorization, or independent extraction consensus.
Use the documented material-yield convergence threshold: a later pass stops when
it adds no new source-backed, buddy-confirmed relationship. Cross-packet follow-up
may inspect only newly exposed unresolved neighborhoods. Use the shared
byte-adaptive packet utility and retain individually oversized or batch-capped
records as explicit exceptions.
For cross-packet follow-up, use the dedicated graph-guided lane only after the
in-packet artifacts and graph exist. It discovers links for unmatched documents
and independently verifies already-resolved proposals against related packets;
it retains every source neighborhood, raw response, provider status, and
exception as proposals without rewriting prior results.

Use existing scripts and a new no-clobber run directory. Initialize it with
`scripts/run_workspace.py init` and execute every pipeline-writing command
through `scripts/run_workspace.py run`, so generated artifacts, raw responses,
cache, throttle state, logs, and retries stay below that one root. Source inputs
and credentials remain external read-only boundaries. Every environment
setting is in
[`references/runtime-configuration.md`](references/runtime-configuration.md);
every CLI is indexed in
[`references/command-line-reference.md`](references/command-line-reference.md)
with every option in
[`references/cli-help-catalogue.md`](references/cli-help-catalogue.md), and is
defined exactly by its `--help`. Before accepting client records, follow
[`docs/CLIENT_USER_GUIDE.md`](docs/CLIENT_USER_GUIDE.md). Before Google-backed
work, run `python scripts/reauthorize_google.py` and keep credentials outside the
repository.

For a full run, create the non-secret `RUN/logs/run_authorization.md` before
the first provider call. It names the run, source hashes/identifiers, timestamp,
provider families, selected lanes, and whether every in-scope item may be sent.
That authorization never clears a gate or enables acceptance, carry-forward,
canonical, CRM, or delivery work. Follow the operating skill's strict phase
state machine: do not launch dependent downstream work while upstream artifacts
are active or unread; retain and repair/block every refusal, schema mismatch,
provider failure, and zero-coverage result. End with workspace audit, required
lane coverage, and a final queue containing base and recovery artifacts.

Only approved, provenance-linked, review-clear facts may enter canonical
retrieval or target staging. Additional production OCR/HTR selection,
golden-set accuracy, detector deployment, target CRM delivery, and public
tenant-isolated retrieval remain client-authorized integration work.
This repository stops at verified no-send staging/import packages and the
approved-fact MCP/API surfaces; target-specific receivers and live write
adapters belong on the destination system side.

To operate a run rather than change the repository, follow
[`skills/business-doc-operations/SKILL.md`](skills/business-doc-operations/SKILL.md)
and its task guides. Its SKILL.md carries a lane-by-lane table of every command
with its phase and whether it is optional or disabled by default; the guides
cover setup, the pipeline sequence, extraction, the independent cross-checking
lanes, handwriting, the business controls, the evidence graph and relationship
lanes, the client review lane that turns any blocked control into a
client-answerable pack, review reduction, client review and return ingestion,
monitoring, delivery, troubleshooting, repository maintenance, and subagent
delegation.
After any change under `scripts/`, run
`python scripts/agent_surface_check.py` so operating instructions cannot go
stale against the code.

For approved Phase 6 facts, use
[`skills/mcp-api-operations/SKILL.md`](skills/mcp-api-operations/SKILL.md) to
select local stdio MCP, remote OAuth Streamable HTTP MCP, or TLS/bearer REST;
run the code and deployed-environment acceptance; register the selected client;
and operate CRM queries, reports, and exports. Protocol compatibility never
proves a particular ChatGPT workspace or mobile surface is available, and the
skill does not authorize deployment or target-CRM writes.

For separately authorized new page-image intake, use
[`skills/record-intake/SKILL.md`](skills/record-intake/SKILL.md) and
[`references/visual-ingestion.md`](references/visual-ingestion.md). The optional
MCP/API journal retains sources and candidate records only. Transfer real image
bytes through the client/application; do not invent base64 or assume a chat
attachment reached the server. No intake tool approves, applies or publishes a
record. No native capture app or attachment relay is bundled. Client-specific
acceptance must prove actual upload and vision support separately from queries.

For a trusted local operator handoff, follow `visual_ingestion_export.py`
through the intake guide: export into a new contained run, retain the receipt
separately, verify its pinned manifest hash, and use only the declared complete
source PDF for normal profiling/intake. Preserve originals, all proposal
versions and rejections, the page map, and final-review exceptions. This is not
an extraction/consensus handoff. See the
[`roadmap`](references/business-data-platform-roadmap.md) before discussing
record updates, publication, saved reports, or other unimplemented extensions.

Every command-line argument carries help text, so `python scripts/COMMAND.py
--help` is a complete answer to what an option does. Recurring option wording
lives once in `scripts/cli_help.py`; `scripts/release_check.py` fails if any
argument stops explaining itself.

Run the full quality gate in `AGENTS.md`. If a tracked document changes,
regenerate its LaTeX/PDF, render every changed PDF, and visually inspect every
page before handoff. Read [`BRANCHING.md`](BRANCHING.md) before branch work.

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
retained responses for the column heading, find a printed join key the records
already carry, and re-extract only when that fails -- affected pages only,
amendment recorded first. A recovered value is single-extractor evidence, never
vendor agreement.

**Agreement is not accuracy.** A correct transcription filed under the wrong
field is what two vendors agree on, because both read the page correctly and
both assumed the same thing about the column. A field the schema never defines
is a field engines guess at, and a printed column with no field at all goes
into whichever field looks closest. Check `FIELD_DEFINITIONS` coverage and the
document's own arithmetic. Carry a value that fails such a check and label it;
never repair a reading to make it agree, and never drop a row for failing one.

The full checklist is `skills/business-doc-operations/tasks/lane-checklist.md`.
