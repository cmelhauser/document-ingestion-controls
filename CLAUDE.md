# Claude Project Instructions

Start with [`SKILL.md`](SKILL.md), then [`AGENTS.md`](AGENTS.md). Use
[`HANDOFF.md`](HANDOFF.md) for current state only. Read the relevant phase file
in `references/` completely before acting and read
[`references/artifact-contracts.md`](references/artifact-contracts.md) before
changing any interface, workbook, output, or runbook.

Preserve source pages and original values; corrections are append-only
amendments. Keep every failure and uncertainty as an explicit exception, and
treat a control that processed nothing as failed rather than clear. Same-model
roles and provider confidence are not independent consensus; independence
belongs to the model vendor rather than the transport, and self-reported
confidence is provenance, never a decision term. Route an implausible value to
review instead of classifying it, and never infer a unit, date order, or country
from magnitude. Require applicable arithmetic, attribution, completeness, and
final review separately.
LLM output, mapping discovery, grouping, returned client workbooks, and review
reduction remain proposals until authorized changes are applied and downstream
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

Before re-reading any page, reconcile from what the run already retains. Every
provider response is kept, and the independent non-LLM extractor retains the
whole page rather than the fields the schema named, so a value the pipeline
dropped is usually still on disk with enough layout to place it against a
printed key the records already carry. Re-extract only when the value is absent
from every retained artifact, and then only the affected pages. A recovered
value is single-extractor evidence, never vendor agreement.

Two vendors agreeing measures whether they read alike, never whether the field
means what they assumed: a correct reading filed under the wrong field arrives
accepted. Define every field the corpus populates, and check a numeric field
against the document's own arithmetic. Carry a value that fails such a check
and label it; never repair a reading to make it agree, and never drop a row for
failing one.

Use repository scripts and fresh no-clobber paths. Initialize every run with
`scripts/run_workspace.py init` and execute every pipeline-writing command
through `scripts/run_workspace.py run`, so generated artifacts, raw responses,
cache, throttle state, logs, and retry overlays stay below the run root. Source
inputs and credentials remain external read-only boundaries. Environment controls are in
[`references/runtime-configuration.md`](references/runtime-configuration.md);
CLI controls are in
[`references/command-line-reference.md`](references/command-line-reference.md)
and the complete parser-derived
[`references/cli-help-catalogue.md`](references/cli-help-catalogue.md), with
executable `--help` as the exact contract. Follow
[`docs/CLIENT_USER_GUIDE.md`](docs/CLIENT_USER_GUIDE.md) before receiving client
records. Before Google-backed work, run `python scripts/reauthorize_google.py`
and keep credentials outside the repository.

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
retrieval or target staging. Production provider selection, golden-set accuracy,
detector deployment, target CRM delivery, and public tenant-isolated retrieval
remain client-authorized integration work.
This repository stops at verified no-send staging/import packages and the
approved-fact MCP/API surfaces; target-specific receivers and live write
adapters belong on the destination system side.

To operate a run rather than change the repository, follow
[`skills/business-doc-operations/SKILL.md`](skills/business-doc-operations/SKILL.md)
and its task guides. Its lane table lists every command with its phase and
whether it is optional or disabled by default; the guides cover setup, the
pipeline sequence, extraction, cross-checking, handwriting, the business
controls, the evidence graph and relationship lanes, the client review lane
that turns any blocked control into a client-answerable pack, review reduction,
client review, monitoring, delivery, troubleshooting, maintenance, and
subagents. To decide which of the optional lanes a corpus needs before
committing to them, follow
[`skills/pipeline-selection/SKILL.md`](skills/pipeline-selection/SKILL.md): it
measures the run's own retained artifacts and proposes a plan, and it authorizes
nothing, clears no control, and reports an unmeasured lane as undecided rather
than defaulting it either way. The repository's Claude Code hook runs
`scripts/agent_surface_check.py` after an edit so operating instructions cannot
go stale against the code.

For approved Phase 6 facts, follow
[`skills/mcp-api-operations/SKILL.md`](skills/mcp-api-operations/SKILL.md) for
local stdio setup, remote OAuth MCP or REST deployment, client registration,
production acceptance, CRM queries/reports/exports, monitoring, rebuild, and
shutdown. Do not start a listener or register a client until that skill's exact
activation and deployment-authorization gates pass.

For separately authorized new page-image intake, use
[`skills/record-intake/SKILL.md`](skills/record-intake/SKILL.md) and
[`references/visual-ingestion.md`](references/visual-ingestion.md). The optional
MCP/API journal retains sources and candidate records only. Transfer real image
bytes through the client/application; do not invent base64 or assume a chat
attachment reached the server. No intake tool approves, applies or publishes a
record. The remote platform includes a same-origin image upload/review portal,
but no native chat attachment relay; client acceptance must prove actual upload
and vision support separately from queries.

For a trusted local operator handoff, follow `visual_ingestion_export.py`
through the intake guide: export into a new contained run, retain the receipt
separately, verify its pinned manifest hash, and use only the declared complete
source PDF for normal profiling/intake. Preserve originals, all proposal
versions and rejections, the page map, and final-review exceptions. This is not
an extraction/consensus handoff. For typed analytics, saved analytical exports,
or governed create/amend delivery, read
[`references/business-data-platform.md`](references/business-data-platform.md)
and use [`skills/record-maintenance/SKILL.md`](skills/record-maintenance/SKILL.md).
Models may propose only; authorization, apply, reconciliation, and replacement
approved snapshots remain separate operator/client-controlled steps. The remote
operator API is separately scoped and is not a Claude MCP tool.

A question put to a client is only as good as the page shown with it. Give the
review lane `--scan-profile` so a near-blank page is never a worked example, show
several examples spread across a group rather than one, and name the documents
and columns each question covers. A wrong answer to a badly evidenced question is
the pack's defect, not the client's.

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
comparison. Qualify a second reader by value recall and field-exact agreement
through `engine_agreement.py`, never by a matching row count: an engine can
return the right number of rows having misread most of their content. A schema
translated for one transport must be re-verified on another, because a dropped
nullability flag turns every empty cell into the text `null` and every one of
those into a claimed reading.

Run the complete gate in `AGENTS.md`. Regenerate and visually inspect every
changed tracked PDF. Read [`BRANCHING.md`](BRANCHING.md) before branch work.
