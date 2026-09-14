# Release 1.0.0

Release date: 2026-08-13

This file is the immutable scope record for version 1.0.0. The current `main`
branch may contain later work under **Unreleased** in [CHANGELOG.md](CHANGELOG.md);
that work is not part of release 1.0.0 unless a new stable version completes the
procedure below and receives its own immutable tag.

This repository is dedicated to the public domain under The Unlicense. Courtesy
credit is recorded in [NOTICE](NOTICE).

For clarity, `theonlymuffinbot` is the collaborative AI identity used for work
produced through a mix of Anthropic Claude Opus 5, OpenAI GPT-5.6 Terra and
Sol, and xAI Grok 4.5 models. This is courtesy context, not AI legal ownership.

## Release scope

Version 1.0.0 is the production release of the repository's auditable control
layer and vendor-neutral post-review data boundary. It is ready to receive a
client-authorized run only after the intake, privacy, retention, provider, and
acceptance decisions in [Client User Guide](docs/CLIENT_USER_GUIDE.md) are
complete.

The release preserves source evidence, records every correction as an amendment,
routes unresolved records to review, proves applicable arithmetic, reports
completeness and unattributed dollars explicitly, and blocks handoff while the
named final-review package is open.

## Included

- Immutable intake, source hashing, one-page masters, retained image variants,
  proposal-only reassembly, and exact duplicate candidates.
- Vendor-neutral extraction/HTR contracts, optional capped provider lanes,
  consensus, validation, attribution, completeness, QA sampling, and reviewer
  JSON/CSV/XLSX/HTML output.
- Source-native table profiles/rows, source-layout-only corpus refinement,
  Google Document AI evidence, deterministic exact-registry reconciliation, and
  client-review-required amendments for changed readings.
- Versioned PostgreSQL canonical DDL and deployment plan, ordered/checksummed
  load plan, no-send CSV/API staging, approved-fact SQLite retrieval, read-only
  local MCP search, and an explicitly enabled TLS-only HTTPS reader.
- Client overview, client user guide, technical runbook, technical plan,
  field/contract references, exhaustive runtime and command-line references,
  fictional schema examples, acceptance fixtures, CI, and agent-specific
  instructions.

## Client-specific work that remains outside this release

- Additional live OCR/HTR vendor selection and representative golden-set
  calibration.
- Production deployment of calibrated handwriting and party-role detection.
- A target CRM receiver, credentials, tenancy, load reconciliation, and any
  public/tenant-isolated transport deployment.
- Client production data, client policy approvals, and any accuracy percentage.

These are integration decisions, not silent release defects. Do not describe the
repository as a fully configured client production pipeline until they are
selected, implemented, and accepted.

## Verify the release

Run the complete gate in [AGENTS.md](AGENTS.md), then run:

```bash
.venv/bin/python scripts/release_check.py
bash scripts/generate_docs.sh
bash scripts/render_docs.sh
git diff --check
git diff --exit-code -- docs/*.tex docs/*.pdf
```

The release check verifies the version boundary, required operating documents,
agent instruction links, local Markdown targets, environment defaults, CLI
inventory, and all tracked Markdown/LaTeX/PDF delivery triples. The generate
command refreshes LaTeX from Markdown; the
embedded source hash and deterministic render prove the committed LaTeX/PDF
files match their canonical Markdown sources;
every changed tracked PDF must also be rendered to page images and visually
inspected before delivery.

## Create a stable release

The repository's durable branch, tag, release, and recovery procedure is in
[BRANCHING.md](BRANCHING.md). In short, prepare a version on a reviewed
`release/vMAJOR.MINOR.PATCH` branch, merge it to `main`, and tag only the exact
current `main` tip as `vMAJOR.MINOR.PATCH`. The tag must match the version in
the project metadata and the headings in this file and `CHANGELOG.md`.

Pushing that tag triggers the `release` GitHub Actions workflow. It refuses a
tag that is not the current `origin/main` tip, reruns release, quality,
acceptance, and documentation checks, then creates the matching GitHub Release.
Never move, force-update, or reuse a stable tag. If a release check fails,
correct the issue through a new reviewed commit and publish the next PATCH
version instead.

GitHub branch protection is an external repository setting: `main` requires a
pull request, the strict-quality and acceptance checks, resolved conversations,
and linear history, with direct/force pushes and deletion disabled. This makes
the checked-in workflow enforceable in the hosted repository rather than merely
advisory.

## Production operating rules

- Use a new output directory for each run. Operational manifests, adapter
  contracts, privacy inventories, and reviewer exports refuse to replace an
  existing artifact; only resumable stage state is atomically updated.
- Keep `.env`, provider keys, client records, raw responses, generated run
  output, retrieval databases, and target credentials outside version control.
- Deliver the canonical JSON review package with its reviewer views and the
  applicable completeness, attribution, sampling, manifest, and decision
  artifacts. A clear queue proves only the artifacts named in that queue.
- Follow [Security Policy](SECURITY.md) for suspected secret, privacy, or
  integrity issues.

## Distribution

This repository is public-domain software under The Unlicense. Client data,
credentials, retention policies, third-party dependencies, and any
client-specific agreement remain separate from the software dedication.
