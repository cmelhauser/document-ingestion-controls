---
title: "Inferred Control Proposal Lane — Verification Record"
date: "2026-08-21"
status: "implemented on main; proposal-only at runtime"
---

# Purpose

This record documents the repeatable fallback lane for runs where the client
does not have authoritative GL or payment exports. It is a review-prioritization
lane only. It must never be presented as an accounting-system reconciliation or
used to clear the completeness gate.

# Implementation

The lane is implemented by `scripts/inferred_controls.py` and accepts a retained
consensus or proofed JSON envelope:

```bash
python scripts/inferred_controls.py consensus.json \
  --out-dir inferred_controls_proposal_YYYYMMDDTHHMMSSZ
```

Each invocation requires a new output directory and emits:

- `attributed.json` — explicit source-key attribution proposals;
- `gl.csv` — deterministic document-total rollups by period and vendor;
- `payments.json` — only explicit payment evidence found in source records;
- `exceptions.json` — missing, malformed, or unresolved evidence; and
- `manifest.json` — source and output hashes, schema version, and gate status.

All outputs are marked `proposal_only: true` and `authoritative: false`. The
manifest gate is always `blocked_inferred_controls_not_authoritative`.
Missing amounts, dates, attribution keys, and payment evidence are retained as
exceptions. No payment is inferred from an invoice amount.

# Verification evidence

The figures below are the record of the August 21, 2026 verification and are
retained as written. They are not the current gate: see `HANDOFF.md` for the
measurements from the most recent run. Every behavioral claim in this document
was re-verified against the implementation before this record was merged.

The implementation and regression tests were merged into `main`:

- `f9f93f6` — repeatable inferred control proposal lane;
- `4d39c54` — fail-closed malformed-input and missing-amount hardening.

The verification run passed:

- full test suite;
- strict statement and branch coverage at 100% (10,376 statements and 4,036
  branches, as of that run);
- Ruff lint and format checks; and
- `scripts/release_check.py`.

Two fresh runs on the same production consensus input produced byte-identical
`attributed.json`, `gl.csv`, `payments.json`, and `exceptions.json`, with the
same source hash and exception counts. The generated manifest timestamps are
run metadata; the evidence artifacts themselves are deterministic.

# Operational boundary

Inferred artifacts may prioritize client review and identify evidence gaps.
They cannot clear attribution, GL variance, payment/aging closure,
completeness, final review, canonical loading, or CRM/API staging. Replace or
supersede them only with client-approved authoritative exports or an explicit
client-approved substitute recorded as an exception.
