# Source-Native Table Comprehension

This optional Phase 3 proposal lane extracts source-native table evidence before
any canonical or display mapping. It is not an OCR consensus engine, an approved
canonical mapping, a client decision-maker, or an accuracy claim.

## Controls

1. `profile` inspects a single immutable intake page for document family, selects
   a source schema route (including `commission_statement` for commission
   statements/reports), and records table
   regions, exact visible headers, column boundaries, repeated sections, totals,
   handwriting regions, and quality diagnostics.
2. `rows` extracts visible rows only against that retained source profile. Every
   cell retains its source label, visible value, page ID, table/row ID, normalized
   page box, and evidence text.
3. `audit` is a separate LLM role that checks the profile and rows against the
   immutable page. When the same model serves both roles, it is explicitly not
   independent consensus.
   A page-matched Google Document AI handoff may be supplied as attributed,
   independently-produced OCR/table evidence. The initial auditor does not see
   it. Only an LLM finding or diagnostic triggers a second buddy-audit call after
   provider, page-hash, and raw-response provenance checks; it reports conflicts
   rather than treating the evidence as a decision.
4. `mappings --propose-mappings` is opt-in. It delegates header suggestions to
   the strict `schema_discovery.py` proposal boundary. Suggestions are never
   effective mappings: the client must approve them through that workflow's
   decision return and append-only registry update.
5. `assemble` applies only an exact ordered-template fingerprint plus
   client-approved source-label registry rule. It never uses a model proposal as
   a mapping. It emits source rows, an adapter handoff, aggregated decision cards,
   final-review exceptions, and a separate quality summary.
6. `table_comprehension_corpus.py` runs page work sequentially across one or
   more intake manifests. It snapshots only observed source-layout context after
   each page, then rereads uncertain pages against the completed corpus context.
   It preserves the initial and later readings, writing any changed source cell
   as a client-review-required amendment proposal.
7. `independent_table_reconcile.py` maps Document AI table headers only through
   the same exact client-approved registry and compares the independent cells to
   source-row cells deterministically. It aggregates protected-fact review work;
   even an exact agreement cannot auto-clear financial or identity facts.

Commission statements/reports are not invoice-shaped: their source-native
commissionable amount, stated rate, allocation share, and commission amount are
retained as distinct fields. Invoice arithmetic is not applied to this family;
the allocation-policy lane owns any formula or sales-credit proof.

Financial and identity cells require deterministic reconciliation and/or a
genuinely independent extractor. Handwriting, reassembly, financial/identity,
and unresolved mapping concerns become blocking aggregated cards. Image quality,
row-boundary, and provider diagnostics are retained only in the quality summary;
the final-review queue explicitly ignores that artifact type.

The corpus runner never gives later pages earlier **model values**. Its context
contains only observed document family, exact headers, source sections, totals,
template fingerprints, and source page/region IDs. This prevents a speculative
value from becoming apparent evidence elsewhere in the five-year corpus.

Google Document AI evidence is different: it is passed only to the conditional
buddy-audit role for the same retained page, never forwarded to later pages. The
audit packet records its evidence hash and both raw LLM-response paths. It cannot
alter source rows, canonical mappings, or review status.

## Corpus context and bounded refinement

Use `table_comprehension_corpus.py` for multi-file historical intake. It writes
one immutable page packet per role, a versioned layout-context snapshot after
each forward page, atomic resumable state, a final corpus index, and a combined
adapter/decision/quality package. It rejects duplicate page IDs across manifests
and refuses a changed manifest hash or model/refinement configuration on resume.

After the forward pass it selects pages with retained provider failures,
diagnostics, or decision cards. A refinement pass rereads those pages against the
completed source-layout context. Each new value remains a proposal beside the
original; a changed value creates
`llm_refinement_amendment_proposal` with both readings and the exact context
snapshot. It never adjusts a client-approved registry rule or client decision.

`--refinement-passes` is a hard cap. `--refinement-tolerance` is the permitted
symmetric difference in successive unresolved-page sets: the runner stops early
when that set is stable within the tolerance. This is a workflow-stabilization
criterion, not an accuracy claim or a confidence threshold. Material uncertainty
continues to block the final review gate regardless of the stop condition.

## Commands

Use a new output directory for each run. Each provider-role artifact and raw
directory is no-clobber.

```bash
# Profile one retained page, then extract source rows from the retained profile.
python scripts/table_comprehension.py profile intake/ingestion_manifest.json \
  --page-id PAGE_ID --out table/profile-PAGE_ID.json --raw-dir table/raw-profile-PAGE_ID
python scripts/table_comprehension.py rows intake/ingestion_manifest.json \
  --page-id PAGE_ID --context table/profile-PAGE_ID.json \
  --out table/rows-PAGE_ID.json --raw-dir table/raw-rows-PAGE_ID

# Send the profile and rows together as the audit context; retain the raw result.
python scripts/table_comprehension.py audit intake/ingestion_manifest.json \
  --page-id PAGE_ID --context table/profile-and-rows-PAGE_ID.json \
  --independent-evidence document-ai/document_ai_adapter.json \
  --out table/audit-PAGE_ID.json --raw-dir table/raw-audit-PAGE_ID

# Optional LLM mapping proposals. This is not a mapping approval or registry write.
python scripts/table_comprehension.py mappings table/profile-PAGE_ID.json \
  --registry approved_mapping_registry.json --propose-mappings \
  --out table/mapping-proposals-PAGE_ID.json \
  --exceptions table/mapping-proposal-exceptions-PAGE_ID.json \
  --handoff-out table/mapping-handoff-PAGE_ID.json --raw-dir table/raw-mappings-PAGE_ID

# Apply only the existing client-approved registry snapshot.
python scripts/table_comprehension.py assemble table/profile-PAGE_ID.json \
  table/rows-PAGE_ID.json table/audit-PAGE_ID.json \
  --registry approved_mapping_registry.json --out table/source-rows-PAGE_ID.json \
  --decision-cards table/decision-cards-PAGE_ID.json \
  --quality-summary table/quality-PAGE_ID.json \
  --exceptions table/exceptions-PAGE_ID.json --adapter-out table/adapter-PAGE_ID.json
python scripts/operations.py adapter table/adapter-PAGE_ID.json \
  --type table_comprehension --credential-env OPENAI_API_KEY \
  --out table/adapter-contract-PAGE_ID.json

# Compare independently read Document AI cells after approved mapping exists.
python scripts/independent_table_reconcile.py table/source-rows-PAGE_ID.json \
  --registry approved_mapping_registry.json \
  --document-ai document-ai/document_ai_adapter.json \
  --out table/independent-reconciliation-PAGE_ID.json \
  --exceptions table/independent-reconciliation-exceptions-PAGE_ID.json \
  --adapter-out table/independent-reconciliation-adapter-PAGE_ID.json

# Five years of source PDFs: intake each PDF first, then run sequentially across
# the resulting manifests. Use one new output directory per corpus run.
python scripts/table_comprehension_corpus.py intake-2022/ingestion_manifest.json \
  intake-2023/ingestion_manifest.json intake-2024/ingestion_manifest.json \
  intake-2025/ingestion_manifest.json intake-2026/ingestion_manifest.json \
  --registry approved_mapping_registry.json --out table-corpus-run/ \
  --independent-evidence document-ai/2022_adapter.json document-ai/2023_adapter.json \
  document-ai/2024_adapter.json document-ai/2025_adapter.json document-ai/2026_adapter.json \
  --max-pages 500 --refinement-passes 3 --refinement-tolerance 0
python scripts/operations.py adapter table-corpus-run/corpus_adapter.json \
  --type table_comprehension --credential-env OPENAI_API_KEY \
  --out table-corpus-run/corpus_adapter_contract.json

# Continue only the same retained run after interruption.
python scripts/table_comprehension_corpus.py intake-2022/ingestion_manifest.json \
  intake-2023/ingestion_manifest.json intake-2024/ingestion_manifest.json \
  intake-2025/ingestion_manifest.json intake-2026/ingestion_manifest.json \
  --registry approved_mapping_registry.json --out table-corpus-run/ --resume
```

Run `schema_discovery.py registry-update` only after receiving the generated
client decision return file. Re-run `assemble` with the next immutable registry
snapshot; do not edit proposal or source-row artifacts in place.

Run `template_drift.py analyze` before reusing an approved mapping registry on
a later corpus. A single exact `source_layout_v1` approved fingerprint permits
reuse. Drift and new or ambiguous layouts remain grouped review work even when
diagnostic header similarity is high.

## Calibration boundary

Before reducing review, enabling automated routing, or describing accuracy,
measure this flow on a representative client-authorized golden set by document
family, ordered-template fingerprint, source field, materiality, handwriting,
and review disposition. Retain the evaluator configuration and outcome; do not
extrapolate a score from a non-representative page sample.

## Operating the corpus lane

**Always pass `--independent-evidence`.** Without it the conditional buddy check
records `conditional_buddy_check.available: false` and a `reason` of
`no_llm_reported_issue`. That reason string is a default set *before* the check
runs, so a page with no corroboration is indistinguishable from a page where
corroboration found nothing wrong. Supplying the independent OCR handoff enables
the check; omitting it silently disables a control.

**Keep `--reasoning-effort` consistent with the extraction and discovery lanes.**
The effort is recorded in the retained corpus state and compared on resume. A
resume across a changed effort is refused, and that refusal is correct: mixing
effort levels in one artifact makes a page's reading regime unrecoverable. Start a
fresh pass rather than forcing the resume.

**`--refinement-passes` is a cap, not a quota.** Refinement runs only over pages
that remain unresolved candidates, and exits with `no_unresolved_candidates`
without running a round when none do. `--refinement-passes 1` means "one pass if
needed". `--refinement-tolerance 0` is strict: a round stops early only when the
candidate set is unchanged from the previous round.

### The lane is sequential by design

Each page reads a snapshot of the *layout* observations accumulated from the pages
before it. That continuity is why this lane exists rather than the per-document
command, so there is no worker pool and no `--workers` flag, and adding one would
defeat the lane.

To use more than one provider connection, **shard**: split the intake manifest
into disjoint contiguous page sets and run one pass per shard concurrently.
Contiguous rather than interleaved keeps a multi-page document inside one shard.

The trade is bounded. A shard learns only from its own pages, so a layout first
seen in one shard does not inform another. Because the snapshot carries layout
only -- never cell values, unapproved mappings, or prior model decisions -- the
failure mode is a shard treating a known layout as novel. Reduced continuity,
never a wrong value. Record the shard boundaries in the run authorization.

### Table mapping requires an approved rule that still matches

`independent_table_reconcile.py` compares mapped rows against independent cells,
and a row is only mapped where an **approved** registry rule matches the table's
header fingerprint. A registry whose rules were approved against a superseded
extraction will match nothing, and the lane will reconcile nothing -- a
zero-coverage failure, not a pass. Check fingerprint overlap before running it.

## Budget two passes, not one

`--refinement-passes 1` is a cap, and on a corpus with pervasive uncertainty it
binds every page. `needs_refinement` returns true when a page produced **any**
decision card or diagnostic, so a corpus reading at a high exception rate makes
every page a candidate and the refinement round becomes a **second full pass**.

Measured on a 716-page commission corpus: the forward pass took ~10.5 hours and
the refinement round took ~9 more, for ~19.5 hours total against a ~10-hour
estimate drawn from the page count alone.

Size the lane at **2x the forward pass** unless you have checked the candidate
count, which the retained state reports:

```bash
python -c "import json;s=json.load(open('RUN/tables/<attempt>/corpus_state.json'));\
print(len((s.get('active_refinement') or {}).get('candidate_page_ids') or []),'of',\
len(s.get('completed_page_ids') or []))"
```

The refinement pass is not overhead. On that corpus it produced 2,494 amendment
proposals the forward pass had missed, concentrated in the shards whose pages were
densest -- the same shards that ran slowest. Slow shards are where rereading pays.

**A shard's own slice governs completion, not the aggregate rate.** Contiguous
shards get uneven page difficulty, so a combined throughput figure understates the
tail: five shards finishing early do not speed up the sixth. Read per-shard
counts, and expect the last shard to run well past the aggregate estimate.

## Sizing timeouts before sharding

The lane's timeout and retry defaults assume one sequential pass. Concurrency
raises per-call latency, so calls that finished inside the timeout begin to be
abandoned -- and an abandoned call is usually still completed and billed by the
provider before the client gives up on it. Each retry pays again for the same
work.

On a real run this made sharding *slower than sequential*. Six shards at a 120s
timeout with 10 retries produced 1.07 pages/min against 0.30 for a single shard,
where six should have approached 1.8; three of the six sat idle for sixteen
minutes apiece, each burning retries on one page. The same six shards at a 600s
timeout with 3 retries produced an even 1.32 pages/min with no retries recorded.

Before sharding: raise `--timeout-seconds` well past the observed per-call
latency at your reasoning effort, and lower `--max-retries`. Ten retries of a
call failing for a structural reason is ten times the bill for one failure.

**The diagnostic is the spread across shards.** Even page counts mean the timeout
fits. A spread like 1/7/1/1/3/5 across identically configured shards is not page
difficulty -- it is some shards caught in the retry loop and others not.

## Supply the recovery handoff, not the base one

`--independent-evidence` must name an independent-reading artifact whose records
are readings. A base adapter handoff retains the pages whose independent reading
*failed*, carrying `review_status: open_exception`, and the lane refuses them:
a retained failure is not evidence, and corroborating against one would be
worse than having no corroboration at all.

The refusal names the page and stops the shard, which is the correct behaviour
and not a defect to work around. Supply the recovery handoff -- the artifact
whose exceptions were resolved -- and confirm before the run that it carries no
`open_exception` records.
