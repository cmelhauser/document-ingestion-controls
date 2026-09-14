# Cursor Project Instructions

Start with [`SKILL.md`](SKILL.md), then [`AGENTS.md`](AGENTS.md); use
[`HANDOFF.md`](HANDOFF.md) only for current state. Read the relevant phase
reference completely before operating it, and read
[`references/artifact-contracts.md`](references/artifact-contracts.md) before
changing an interface, output, workbook, or runbook.

Use [`skills/business-doc-operations/SKILL.md`](skills/business-doc-operations/SKILL.md)
to run client documents. It is the authoritative operating router and its
phase/state-machine order is mandatory. Initialize each fresh run with
`scripts/run_workspace.py init` and invoke every pipeline-writing command through
`scripts/run_workspace.py run`; all generated artifacts, raw responses, runtime
state, logs, and retries stay under that one run root. Sources and credentials
remain external read-only boundaries.

Before the first provider call in a full run, create the non-secret
`RUN/logs/run_authorization.md` recording the run, source identifiers/hashes,
time, authorized provider families, selected lanes, and authorization to send
every in-scope item. This authorizes transmission only: it never accepts a
proposal, clears a control, enables auto-accept/carry-forward, or authorizes
canonical, CRM, or delivery work. Do not start a dependent lane before its
upstream artifacts have completed and their exceptions have been read. Keep
every failure, blocked prerequisite, schema mismatch, and zero-coverage result
as an explicit retained record; do not skip, infer, or fabricate results.

Before completion, run the workspace audit and `run_lane_coverage.py --require`
for every selected lane, then build the final queue from all review-bearing base
and recovery artifacts. Preserve source pages and original values; corrections
are authorized append-only amendments, and proposals are never facts until the
required controls are rerun. Run the quality gate in `AGENTS.md` before delivery
and read [`BRANCHING.md`](BRANCHING.md) before Git operations.

After the Phase 6 gate clears, use
[`skills/mcp-api-operations/SKILL.md`](skills/mcp-api-operations/SKILL.md) for
MCP/API setup, deployment, acceptance, connection, CRM query/report/export use,
monitoring, rebuild, and shutdown. Its activation and deployment authorization
requirements are mandatory; it does not authorize a target-CRM mutation.

For separately authorized image intake, use
[`skills/record-intake/SKILL.md`](skills/record-intake/SKILL.md) and
[`references/visual-ingestion.md`](references/visual-ingestion.md). The optional
MCP/API journal accepts original PNG/JPEG bytes and source-cited proposals, not
approved CRM records. Require actual byte transfer and image-reading support;
a chat attachment is not proof of an upload. Retain receipt/session IDs and all
rejections. The local source-only exporter preserves the complete session,
prepares a derivative for normal profiling/intake, and requires receipt-pinned
verification. It never promotes model readings or clears review. Governed
create/update, native capture, and expanded analytics remain roadmap work.

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

The full checklist is `skills/business-doc-operations/tasks/lane-checklist.md`.
