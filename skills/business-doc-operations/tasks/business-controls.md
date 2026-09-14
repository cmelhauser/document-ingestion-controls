# Attribution, allocation, completeness, sampling

Phases 3A, 4, and 4Q. These are the controls that decide whether the dataset can
be signed off, and they run in this order because each needs the last.

## Phase 3A — attribution

Every in-scope dollar gets an approved reference or an explicit register entry.
There is no third outcome, and no residual is ever plugged.

```bash
python scripts/attribution.py RUN/controls/arithmetic.json \
  --reference SOURCE_ROOT/reference/acks.json \
  --out RUN/controls/attributed.json \
  --register RUN/controls/unattributable.json
```

`--ack-pattern` and `--job-pattern` constrain what a valid identifier looks like,
and `--dispositions` limits the allowed outcomes. The unattributable register
carries a reason **and an amount** for each entry — the amount is what makes the
gap measurable rather than anecdotal.

If the client supplied no reference export, say so now rather than at the end.
Attribution against nothing is not attribution.

### Commission and sales credit

`discover` reads the **source-template observation artifact**, not the attributed
records. Build that first with `template_observations.py`; the attributed records
are what `apply` consumes.

```bash
python scripts/template_observations.py RUN/controls/consensus.json \
  --out RUN/templates/observed_templates.json \
  --exceptions RUN/templates/observed_template_exceptions.json
python scripts/allocation_policy.py discover RUN/templates/observed_templates.json --enable \
  --out RUN/controls/allocation_discovery.json \
  --exceptions RUN/controls/allocation_exceptions.json \
  --handoff-out RUN/controls/allocation_handoff.json \
  --raw-dir RUN/controls/raw/allocation
python scripts/allocation_policy.py decision-template RUN/controls/allocation_discovery.json \
  --out RUN/controls/allocation_template.json
python scripts/allocation_policy.py registry-update registry/allocation.json \
  RUN/controls/allocation_discovery.json RUN/controls/allocation_decisions.json \
  --out registry/allocation.v2.json
python scripts/allocation_policy.py apply RUN/controls/attributed.json \
  --registry registry/allocation.v2.json --out RUN/controls/allocated.json \
  --exceptions RUN/controls/allocation_apply_exceptions.json
```

Rules are effective-dated and client-approved. `discover` proposes; only
`registry-update` followed by `apply` changes anything.

Three refusals here are misread-figure detectors, not policy questions:

- `allocation_effective_rate_exceeds_plausibility_ceiling` — usually a displaced
  decimal point. Check the source figure; do not raise the ceiling.
- a negative computed rate — a sign or subtraction error upstream.
- `allocation_rate_unit_ambiguous` — a rate at or below 1 could be a percentage
  or a decimal, and the unit is never inferred from magnitude.

### When the client has no ledger

```bash
python scripts/inferred_controls.py RUN/controls/consensus.json --out-dir RUN/controls/inferred
```

Deterministic document rollups and explicit-source payment/attribution
candidates. Its outputs are marked non-authoritative and **cannot clear Phase 4**.
Preserve its manifest and exceptions beside the run; supersede them only with
client-approved authoritative exports.

### How a document is valued

Attribution, completeness, and sampling all value a document through one shared
basis, and every summary reports which evidence it rested on:

| Basis | Meaning |
|---|---|
| `printed_header_total` | The document states its own total. Strongest, and always preferred. |
| `summed_line_items` | The document states no total, so its own lines were netted. Weaker, and labelled everywhere it is used. |
| `unavailable` | No total and no line money. Excluded with a reason, never counted as zero. |

A derived total is signed: a statement carrying a chargeback is worth its net,
and summing absolute values would report money that was never earned. Read
`value_basis` before quoting a corpus total — a figure mostly derived is this
pipeline's arithmetic over the client's lines, not the client's own statement of
what it is owed.

## Phase 4 — completeness

```bash
python scripts/completeness.py RUN/controls/attributed.json \
  --gl SOURCE_ROOT/reference/gl.csv --payments SOURCE_ROOT/reference/payments.csv \
  --gl-tolerance-pct 0.5 --out RUN/controls/completeness.json
```

**Report variance. Never plug it.** If the books say $2.4M and the documents
account for $2.1M, there is $300K of paperwork missing and that is the finding.

This applies to the GL input itself. A row that cannot be bucketed to an ISO
year-month, or whose amount cannot be parsed, is retained in `rejected_gl_rows` —
never dropped. Dropping it would shrink the authoritative total and therefore the
reported variance, which is the one direction this control must never move. While
any row is rejected, `gl_rows_rejected` blocks the gate.

Set `COMPLETENESS_DATE_ORDER` from the client's export convention **before the
first run**. `05/03/2023` is May in the US and March elsewhere, and the order is
never inferred from the values.

Read `inference_coverage`. It enumerates every vendor series and vendor-month
that was **not** analyzed for gaps, with its reason. "No gaps found" does not
cover that population — it is reported as a stated limitation rather than a gate
block, because a corpus too small to infer from is a client scoping decision.

Exit condition: `gate_status: clear` requires that GL and payment inputs were
supplied, every GL row was ingested, the comparison is measurable and within
tolerance, aging closure has no unresolved or ambiguous application, and no
unexplained sequence or calendar gaps remain. A client explanation is an input to
a rerun; it does not rewrite a blocked report to clear.

## Phase 4Q — sampling

Entry requires the Phase 4 gate to have passed.

```bash
python scripts/sampling.py RUN/controls/attributed.json \
  --materiality 25000 --mus-n 60 --attribute-n 45 --tolerance 0.05 --seed 20260826 \
  --out RUN/controls/sampling.json
```

`completeness.json` is a rollup report (`corpus`, `gate_reasons`, `gl_variance`,
...) with no per-document list, not a per-document artifact — passing it here
used to silently sample a single fabricated `document_id: "unknown"` record
instead of the real per-document population. `sampling.py` now refuses instead
of guessing when its input has none of `documents`/`results`/`attributions`/
`records` and is not itself one record. Feed it the attribution artifact (or
the allocated artifact, once allocation applies), which carries `document_id`
and `review_status` per document.

`sampling.py`'s frame check reads `arithmetic_status`. The attribution input is
therefore `arithmetic.json` (or a later record-preserving artifact that retains
the field), never bare `consensus.json`. This keeps the arithmetic decision
attached to the same document record as attribution and the sampling frame.

Sampling governs the **clean population only**. Every record failing consensus,
arithmetic, validation, or reconciliation is worked by a human, 100% of the time
— typically 5–15% of documents, and where the error mass sits. A document
reporting `proved_with_unproved_lines` has left the sampling frame.

An empty frame is not a fault to debug in extraction. The refusal names what
emptied it, and "not auto-accepted" means the queue is the work, not the
sampler.

Feed completed outcomes back with `--findings` as a hash-bound
`sample_review_results_v1`. Invalid, duplicate, off-sample, or
confidence-factor-table-exhausting findings stay blocked.

Exit condition: a completed one-sided dollar-denominated upper overstatement
bound within tolerance, or the affected population escalated to 100% review and
reworked. Understatement and completeness are **not** inferred from the MUS
bound, and attribute-sample results are reported separately.

### Golden set

```bash
python scripts/golden_set_evaluate.py TRUTH.json RUN/controls/predictions.json \
  --minimum-precision 0.99 --minimum-recall 0.95 --minimum-coverage 0.9 \
  --max-false-auto-accepts 0 --max-protected-escapes 0 \
  --out RUN/controls/golden_set.json --exceptions RUN/controls/golden_set_exceptions.json
```

Needs client-authorized truth. `--max-protected-escapes 0` is the setting that
matters: a protected finding reaching auto-acceptance is a control failure, not a
score.

## Phase 5 — analytics

```bash
python scripts/analytics.py RUN/controls/validation.json \
  --review RUN/review/final_queue.json \
  --parties RUN/controls/entities.json --completeness RUN/controls/completeness.json \
  --out RUN/analytics/answers.json
```

Scope strictly against the Phase 0 decision list. Every output carries its
completeness figures. Do not build analytics on unreconciled data — it produces
confident wrong answers that are expensive to retract.
