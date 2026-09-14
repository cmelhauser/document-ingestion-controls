# Cost-controlled Superpowers profile

This repository uses a cost-controlled Superpowers profile by default. It
changes orchestration only; it does not weaken evidence preservation, TDD,
coverage, release, acceptance, or client-review gates.

## Routing

| Work | Default execution | Model and effort |
|---|---|---|
| Bounded or tightly coupled change | Main agent | Current session model |
| Any delegated Superpowers task | One isolated subagent | GPT-5.6 Luna, medium |

Every spawn must set the model and reasoning effort explicitly and use
`fork_turns="none"`. Give the agent a file-backed brief and report path; do not
copy the conversation or paste accumulated reports into its prompt.

Work is genuinely independent only when it has no shared writable files or
state, does not consume another task's unfinished output, and can be integrated
without resolving overlapping edits.

## Batch and review budget

- A coherent batch is the smallest group sharing files, an interface, or one
  root cause that can be proved by one focused verification surface.
- Use no more than one implementer and one independent reviewer per coherent
  batch. The main agent counts as the implementer; do not also dispatch an
  implementation agent when the main agent owns the batch.
- Resume the same implementer for scoped findings. Review the combined
  correction once rather than creating a reviewer for every micro-fix.
- A remaining critical security, destructive-action, or data-integrity finding
  may justify an additional scoped review. Record why in the execution ledger.
- When another operator is available, use one independent whole-branch review
  with the configured reviewer. For this single-operator repository, record a
  complete self-audit and quality-gate result in the handoff instead; protected
  pull-request and CI checks remain mandatory.

## Verification budget

Use focused red/green tests while editing. Run the complete test, branch
coverage, Ruff, release, applicable acceptance, and document-rendering gates
once after a coherent batch is stable. If the full gate exposes a defect, return
to focused tests and repeat the full gate only after the correction stabilizes.
Regenerate and visually inspect tracked PDFs after their source documentation is
stable, not after each intermediate wording edit.

## Non-negotiable controls

Cost limits never authorize skipped failing tests, reduced coverage, hidden
exceptions, weakened provenance, unreviewed client decisions, or bypassed final
gates. Do not escalate a Superpowers subagent above GPT-5.6 Luna/medium without
new, explicit user authorization.
