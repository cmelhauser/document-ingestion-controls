---
project: business-doc-ingestion
release_baseline: "1.0.0"
repository_channel: unreleased-main
state: public-snapshot
last_verified: "2026-09-14"
documentation_verified: "2026-09-14"
next_external_event: none
---

# Cross-Agent Handoff

Read `SKILL.md`, `AGENTS.md`, and the phase-specific reference before acting.
Use `references/artifact-contracts.md` for interfaces and
`docs/TECHNICAL_DOCUMENTATION.md` for the end-to-end manual. This file contains
current continuation state only.

## Repository state

- This is a public snapshot of the repository. It carries no production run,
  client record, provider response or run state. Every company, person and
  place named in its tests, examples and documentation is fictional; figures
  quoted from a production run are kept to show what a control measured.
- Version 1.0.0 is the stable release baseline. `main` also contains the
  backward-compatible capabilities listed under `CHANGELOG.md` **Unreleased**.

## Required continuation

1. Start a run with `scripts/run_workspace.py init` and follow
   `skills/business-doc-operations/SKILL.md`.
2. Run the complete gate in `AGENTS.md` before proposing a change.

## Operating boundaries

- Keep run inputs, outputs and credentials outside the repository.
- Treat every model output as a proposal until an authorized, append-only change
  is applied and the affected controls are rerun.
