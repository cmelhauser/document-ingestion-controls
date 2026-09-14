# Precision-First Automation and Final Client Review

These controls improve first-pass accuracy without allowing automation to turn an
uncertain reading into a fact. They are the execution boundary for external OCR
and handwriting-recognition providers.

## Operating target

The project may target a high-precision operating posture, but it must not claim
99% accuracy until a client-authorized, representative golden set measures it.
Precision is preferred to coverage: abstention and final review are correct
results when source evidence is incomplete or engines disagree.

## Deterministic field validation

Run `scripts/validate_extraction.py` after arithmetic proof. It carries every
record forward unchanged and checks ISO dates, date ordering, controlled
currencies, numeric parseability, non-negative operational amounts, identifier
plausibility, and field provenance. It emits a separate exception artifact.

```bash
python scripts/validate_extraction.py proofed.json \
  --out validated.json --exceptions validation_exceptions.json
```

Validation never corrects a value. A client reviewer or an evidence-backed
amendment is required to resolve a finding.

## CRM address normalization

Run `scripts/address_normalize.py` after deterministic validation and before entity resolution. It preserves each raw address (for example, `buyer_address`) exactly as extracted and adds only populated derived fields: `buyer_address_line1`, `buyer_address_line2`, `buyer_city`, `buyer_state_or_region`, `buyer_postal_code`, and `buyer_country_code`. The same role-prefix pattern applies to seller, ship-to, remit-to, vendor, origin, and destination addresses.

```bash
python scripts/address_normalize.py validated.json \
  --out address_normalized.json --exceptions address_exceptions.json
```

The default is a local, conservative layout and postal-format check. Configure `GOOGLE_ADDRESS_VALIDATION_ENABLED`, `GOOGLE_MAPS_API_KEY`, and the request controls in the root `.env` to opt in; see [`runtime-configuration.md`](runtime-configuration.md). Explicit CLI overrides remain available, including `--google-address-validation` and `--max-google-requests N`, where `N` is from 1 through 5,000. The code sends each unique raw address only once per run, enables USPS CASS only for US/PR requests unless disabled, records the non-secret provider configuration, and reuses the returned geocode rather than calling Geocoding separately. Billing is still required and the free cap is shared by the billing account, so set Cloud quotas/budgets independently. Coverage varies by country; an unsupported region and other non-secret provider categories are review work. Existing component values are never overwritten; a partial verdict, provider failure, limit reached, or conflicting validated value is final-review work.

## OpenAI Responses extraction adapter

Use `scripts/openai_adapter.py` only after immutable intake. The adapter submits
one retained page at a time with strict structured output for document type,
header fields, lines, handwriting indication, and review flags. It preserves the
raw provider response and page SHA-256 next to normalized results. It does not
store a key, overwrite evidence, apply an amendment, or auto-accept a field.

```bash
# Configure OPENAI_* in the root .env; flags remain per-run overrides.
python scripts/openai_adapter.py intake/ingestion_manifest.json \
  --out openai_engine.json --adapter-out openai_adapter.json \
  --exceptions openai_exceptions.json --raw-dir openai_raw/
python scripts/operations.py adapter openai_adapter.json --type ocr \
  --credential-env OPENAI_API_KEY --out openai_adapter_contract.json
```

`--input-mode auto` sends retained native text only if conservative intake rules
already classified the page; it sends the original one-page PDF for uncertain or
no-text pages. `--input-mode text` and `pdf` are explicit overrides for a
measured run. The default run cap is 1,000 pages, 10 MB per PDF, and 100,000
native-text characters per page. The explicit default reasoning effort is
`medium`; `none`, `low`, `medium`, `high`, `xhigh`, and `max` are accepted values. The adapter
records model, reasoning effort, lower or client-approved limits, timeout, and
retry policy in its non-secret handoff artifact. Treat the adapter as one independent consensus
voter. Feed its exceptions into `final_review_queue.py`; do not use it as a sole
financial reader or an HTR replacement.

## Optional LLM adjudication

`scripts/llm_adjudication.py` is a separate, disabled-by-default endpoint for explicit amendment candidates, not a second extraction pass. Before a model request, it requires clear deterministic validation and exact agreement with a named independent extractor, then excludes all financial fields, address corrections, handwriting, multi-page/reassembly decisions, and disagreements. `LLM_ADJUDICATION_MIN_CONFIDENCE` defaults to `0.99` and is configurable only from `0.99` through `1.0`; it is a gate in addition to, never a replacement for, deterministic evidence. A successful model result is an audit-marked `llm_generated_amendment_proposal` with raw-response retention, model/reasoning/prompt provenance, and a deterministic continuous-QA sample marker. It is always final client-review work, never client approval or a source replacement. Include its output and exceptions in `final_review_queue.py`.

## Card-level client-review assistant

`scripts/client_review_llm.py` is a separate, disabled-by-default reviewer role,
not a third consensus voter. It receives the complete final-review card and
explicitly supplied extraction, schema/mapping, arithmetic, validation, entity,
and handwriting artifacts. It emits proposal-only decisions and dependency
edges. The deterministic reducer keeps every original item in `all_items`; only
the client-facing `visible_items` view can be reduced. Financial, handwriting,
arithmetic, provider, identity, and reassembly findings cannot be covered
automatically. Provider or schema errors remain client-review exceptions.

For an independent final opinion, use `--final-provider PROVIDER` and
`--final-model MODEL`. `PROVIDER` accepts Anthropic, Google, OpenAI, or
OpenRouter; executable `--help` is the exact choice contract. These are
invocation-only overrides for the final stage,
not a second settings bundle: timeout, context limits, reasoning effort,
retry/backoff policy, enablement, and auto-accept policy inherit the existing
client-review settings. The selected provider and model are recorded in the
output summary.

All provider lanes use bounded retries for transient rate-limit, timeout, and
5xx failures. Retry delays use capped exponential backoff with full jitter;
numeric `Retry-After` headers are honored, and permanent 4xx/schema failures
are not retried. The client-review lane records each retry attempt and delay in
its result/exception audit. Configure its base and maximum delay with
`CLIENT_REVIEW_LLM_RETRY_BACKOFF_SECONDS` and
`CLIENT_REVIEW_LLM_MAX_BACKOFF_SECONDS`. Parallel runs use separate output
directories and must not share mutable retained artifacts.

`--resume-from` accepts only a prior `client_review_resume_v1` result whose
queue hash, context hashes, provider/model, reasoning and reduction settings,
per-card request hashes, and retained raw-response hashes all match the current
run. Stale, foreign, duplicate, legacy, or tampered results fail before new
output paths are created.

The optional `client_review_cross_record.py` phase searches supplied admissible
evidence tuples for an evidence-linked answer that appears on a different
record in the run. It emits a separate match/update artifact and never rewrites
the canonical queue. Context uses the strict `cross_record_evidence_v1`
envelope from `artifact-contracts.md`: an exact producer reference binds every
declared entry to either a shared-validator-approved intake manifest or a
canonical export that passes the complete load planner. Source entries must
match an intake page; review-clear facts must match a canonical `document` row.
The reducer recomputes producer and retained-source hashes, verifies the claimed
page exists in the source PDF, matches field/value/provenance exactly, and
derives the only accepted evidence locator. A tuple's own labels never confer
authority. Requests are split by
`CLIENT_REVIEW_LLM_CROSS_RECORD_MAX_PACKET_BYTES` and
`CLIENT_REVIEW_LLM_CROSS_RECORD_MAX_ITEMS_PER_BATCH`; every batch keeps global
review-item IDs, and successful batch results remain available when another
batch fails. Auto-acceptance is disabled by default, requires an explicit flag
plus a threshold from `0.99` through `1.0`, and never applies to financial,
identity, handwriting, arithmetic, provider, reassembly, or missing-field
items. The reducer uses the shared fail-closed protection taxonomy, so an
unknown reason remains protected. Raw provider responses, update/proposal
fields, proposal-only or unapproved suggestions, unresolved review items, and
unclassified/non-clear facts remain retained context but are excluded from the
evidence index and reported in `evidence_context.ineligible_entries` with
machine reasons. Every item in the versioned `entries[]` collection produces an
eligible or ineligible count; an arbitrary/unversioned JSON object and every
malformed entry fail closed rather than disappearing. Outer envelopes are all
bounded, parsed, validated, and counted before producer validation begins. An
invalid producer retains one root finding and marks each otherwise valid
declared entry `producer_not_authenticated`; malformed entries retain their
specific reason. Every proposed update
must name the exact field on its referenced review item, name a non-empty
different source record with no surrounding whitespace, pass a local
finite non-Boolean `[0, 1]` confidence check, and resolve one unique admissible
tuple. A foreign/same record, missing target identity, absent/invalid field,
invented free text/value, malformed reference, invalid confidence, or ambiguous
match stays in `remaining_matches` with a machine-readable
`retained_review_reason`. Eligible opt-in entries record their structured exact
envelope/producer hashes, JSON paths, source hash, and page reference and remain
proposal-only; a cross-field proposal is rejected before it can enter the
reduced view.

### Full-dataset review agent

`scripts/review_agent.py` is an optional, proposal-only dataset pass. It indexes
every supplied JSON artifact, retains hashes and JSON-path evidence references,
groups repeated values across records, detects contradictions, and reasons over
validated facts and outstanding findings together. It runs bounded iterations so
later passes can test earlier hypotheses and identify process-level issues such
as recurring field-mapping, arithmetic, or extraction mistakes. The complete
inventory remains in the output. Provider calls use bounded compact slices
controlled by `CLIENT_REVIEW_LLM_AGENT_MAX_SLICE_CONTEXT_BYTES` and
`CLIENT_REVIEW_LLM_AGENT_MAX_EVIDENCE_PER_SLICE`; a failed slice is recorded as
an exception while successful slices remain retained. No evidence is silently
omitted from the retained inventory.

The agent emits hypotheses, item proposals, process-improvement findings, and
an iteration log. None is a canonical fact, client approval, effective mapping,
or production rule. Every proposal must retain evidence references and the
original value. The reducer verifies that a resolution proposal cites a
retained evidence reference and names the reviewed field before marking it
eligible for any later policy. Cross-record repetition is corroboration, never
proof.

Handwriting is explicitly a secondary contextual signal in this lane. The agent
may extract a handwriting observation or comment when it helps explain a record,
but handwriting is never a primary fact, decision input, blocker, or source of
auto-acceptance. Financial, identity, arithmetic, provider, reassembly, and
missing-field findings remain client review.

```bash
python scripts/review_agent.py \
  --context final_client_review.json --context validated.json \
  --context consensus.json --context arithmetic_proof.json \
  --context entity_resolved.json --context handwriting_decisions.json \
  --enable --final-provider openai --final-model gpt-5.6-sol \
  --out review_agent.json --exceptions review_agent_exceptions.json \
  --raw-dir review_agent_raw/
```

### Golden dataset for calibration

Before a client authorizes auto-acceptance, the golden dataset must contain a
representative, client-approved sample of every important template/layout and
scan-quality band. Each source page keeps immutable truth labels for document
boundaries, printed fields, exact financial values, arithmetic relationships,
handwriting regions and trusted readings, entity/identity mappings, cross-record
matches and deliberate non-matches, missing/ambiguous fields, provider failures,
and the expected final-review queue. The evaluation reports field/template
precision and recall, false auto-accept rate, protected-category escape rate,
arithmetic-proof accuracy, completeness, and review-volume reduction. Aggregate
model confidence alone is not acceptance evidence.

`scripts/golden_set_evaluate.py` implements this acceptance harness. Every
failed aggregate acceptance check also creates a blocking standard review item,
so a failed report cannot be paired with a clear exception artifact. Its truth
envelope contains unique `items[]` with exact expected values, template
fingerprint, document family, field, risk category, protected status, expected
review disposition, and baseline review disposition. Optional expected
arithmetic and completeness statuses test those gates directly. The prediction
envelope records `predicted`, `abstained`, or `failed`, the proposed value when
present, review routing, auto-acceptance, and applicable gate statuses.

```bash
python scripts/golden_set_evaluate.py golden_truth.json pipeline_predictions.json \
  --out golden_set_evaluation.json \
  --exceptions golden_set_exceptions.json \
  --minimum-precision 0.99 --minimum-recall 0.95 \
  --minimum-coverage 1.0 \
  --max-false-auto-accepts 0 --max-protected-escapes 0
```

Coverage counts only usable predicted values; retained abstentions and failures
lower it. Missing expected review, unexpected prediction IDs, arithmetic or
completeness mismatches, false auto-accepts, and protected-category escapes fail
the acceptance checks. Results are reported overall and by template, document
family, field, and risk category. Even a passing report sets
`automation_authorized=false`: it is evidence for a separate client acceptance
decision, never the decision itself.

### Full-approval stress test

To exercise downstream formatting and handoff code as if a client approved every
review item, run `scripts/simulate_client_approval.py`. This is deliberately
separate from LLM thresholded carry-forward. It preserves each original review
item, marks the decision as simulated, reports zero production-approved facts,
and must never be used as a canonical queue or retrieval input.

```bash
python scripts/client_review_llm.py final_client_review.json \
  --context openai_engine.json --context google_engine.json \
  --context proofed.json --context validated.json --enable \
  --out client_review_llm.json --exceptions client_review_llm_exceptions.json \
  --raw-dir client_review_llm_raw/
```

### AI simulated-client comment package

`scripts/ai_simulated_client_review.py` is a distinct, disabled-by-default
decision-support lane. It creates a separate JSON/CSV/XLSX companion package;
it never edits the issued or returned client workbook. Every visible comment is
labelled **“AI client reviewed (simulated; not client authorization)”** (or a
configuration value that retains the same explicit warning). The model may
recommend a simulated accept, simulated reject, human review, or abstention,
but no output is a client decision or a production authorization.

Each provider packet includes the complete final-review card, all original
member items, de-duplicated source records supplied with repeatable `--evidence`
arguments, and optional hash-bound reasoning-only context. Each item lists only
its assigned artifact-scoped source references. Source evidence is required for
provider-backed review; a missing document/page match is retained as an
explicit exception before provider I/O without suppressing sourced members of
the same card. The reducer requires a quote inside a scalar value of a cited
item source and records its exact JSON pointer. It emits
a comment or explicit exception for every review item; invalid model output,
oversized cards, cost-cap deferrals, and provider/schema failures remain
explicit. Protected categories are deterministically reset to
`needs_human_review`, even if the simulated reviewer recommends acceptance.

```bash
python scripts/ai_simulated_client_review.py final_client_review.json \
  --evidence source_evidence.json \
  --context client_responses_preserved.json --enable \
  --out ai_simulated_client_review.json \
  --exceptions-out ai_simulated_client_review_exceptions.json \
  --raw-dir ai_simulated_client_review_raw \
  --comments-csv ai_simulated_client_review_comments.csv \
  --comments-xlsx ai_simulated_client_review_comments.xlsx
```

The provider/model, retry, timeout, packet, and label settings live under
`AI_SIMULATED_CLIENT_REVIEW_*` in `.env`; the configured default is OpenAI
`gpt-5.6-luna`. A human client or authorized operator must separately decide
whether to authorize a proposed append-only change and then rerun all affected
controls. This lane never clears completeness, GL, or payment/aging gates.

The raw directory contains an atomically replaced `checkpoint.json` bound to
the final-review hash, source-evidence hashes, and complete resolved non-secret
configuration. If the process is interrupted before final outputs exist, repeat
the exact command with `--resume`; mismatched inputs or settings fail closed.
Provider/schema failures remain explicit and are not retried by resume.

After a completed run, retry only recoverable retained cards into entirely new
output paths and a new raw directory:

```bash
python scripts/ai_simulated_client_review.py final_client_review.json \
  --evidence source_evidence.json --enable \
  --retry-exceptions prior/ai_simulated_client_review_exceptions.json \
  --out retry/ai_simulated_client_review.json \
  --exceptions-out retry/ai_simulated_client_review_exceptions.json \
  --raw-dir retry/raw \
  --comments-csv retry/comments.csv \
  --comments-xlsx retry/comments.xlsx
```

This produces `run_scope=retry_overlay`; it does not overwrite or silently
merge the frozen original. Validate and reconcile the overlay append-only while
retaining residual exceptions.

## Semantic schema discovery and candidate locations

Use `scripts/schema_discovery.py` after immutable intake to propose controlled meanings for observed source labels and relationships. It is not an extraction override or an entity-master writer. Its LLM output is strict JSON containing a controlled canonical field, semantic type, alternatives, confidence, rationale, and supplied evidence only; unknown concepts enter a schema-change queue. An exact source-template fingerprint and source label must have an explicit client-approved registry rule before reuse.

Use Google Address Validation for an address actually extracted from the source. If only an observed dealer/brand name and city hint are available, the separate optional Google Places (New) call returns review-required candidate locations. Do not select a candidate, infer a city/address, or merge entities automatically. Preserve aliases and relationships as effective-dated history. Include schema-discovery exceptions in the final-review command and follow `semantic-schema-discovery.md`.

## Allocation policy and dynamic business rules

Use `scripts/allocation_policy.py` after normalized allocation legs are available. It computes effective commission rate from retained source amounts, keeps stated rate/share and formula proof separate, and applies only an exact client-approved policy. A policy can be scoped by any approved source dimension (brand, dealer, product, client, location, or a later approved concept) and an effective-date window. The most-specific rule wins; a missing/ambiguous rule, unknown layer, invalid date, or formula mismatch is final-review work.

For an unfamiliar report layout, run its proposal-only discovery command. The LLM can suggest allocation roles and a policy but cannot create sales credit. Generate `allocation_policy_client_decisions.json`; the client edits only `decisions[]` to approve/defer, choose dimensions/dates, and optionally override the proposed threshold or booleans. `registry-update` creates the next immutable snapshot. See [`allocation-policy.md`](allocation-policy.md).

## Handwriting-recognition adapter contract

Use dedicated HTR providers independently. Each provider writes one JSON file:

```json
{
  "engine": "provider-name-and-version",
  "independence_group": "provider-family",
  "annotations": [
    {
      "document_id": "document-identifier",
      "page_id": "immutable-page-identifier",
      "region_id": "stable-region-identifier",
      "semantic_type": "quantity_correction",
      "content_class": "numeric",
      "value": "38",
      "confidence": 0.99,
      "iteration": 1,
      "financial_amendment": true
    }
  ]
}
```

Store the provider's original bounding box, full-page reference, raw response,
and model version alongside this normalized output. The control script does not
invent those details when an adapter has not supplied them.

The implemented Google adapter uses Cloud Vision's handwriting-capable
`DOCUMENT_TEXT_DETECTION` feature and requires separately detected normalized
regions. It OCRs every page, but only region-bound readings enter its HTR
annotation list:

```bash
python scripts/google_handwriting_ocr.py ingestion_manifest.json \
  --regions extraction_records.json --out google_htr.json \
  --adapter-out google_htr_adapter.json \
  --exceptions google_htr_exceptions.json \
  --raw-dir google_htr_raw --images-dir google_htr_pages --enable
```

This is one `google` provider-group vote. A Google confidence score,
second Google model, or second prompt role cannot validate it independently.

```bash
python scripts/handwriting_review.py htr_a.json htr_b.json htr_c.json \
  --out handwriting_decisions.json \
  --exceptions handwriting_client_review.json --max-passes 2
```

- Numeric handwriting requires unanimous agreement from at least three
  independent provider groups.
- Free text may be accepted at two agreeing engines, but partial disagreement is
  retained for review.
- Signatures and initials are never transcribed as business data.
- Financial handwriting becomes an amendment proposal requiring client review,
  even when the reading agrees; it never overwrites a printed value.
- At most two provider iterations are allowed. Any unresolved result after that
  is final client-review work, not another automatic retry.

This repository provides one disabled-by-default Google recognizer adapter plus
the vendor-neutral reconciliation boundary and test contract. It does not claim
that a production detector, two additional independent HTR providers, or a
client-calibrated HTR deployment already exists.

## Handwriting-region and party-role calibration

`scripts/detection_calibration.py` evaluates either `handwriting_region` or
`party_role` predictions against a client-labeled golden set. Inputs use stable
`item_id`, expected `label`, proposed `label`, and a score from zero through one.
It reports per-label precision/recall at each requested threshold and may suggest
a threshold only as a client-review proposal. `deployment_permitted` is always
false: a measured result never reduces review without explicit client acceptance.

```bash
python scripts/detection_calibration.py handwriting_truth.json handwriting_predictions.json \
  --out handwriting_calibration.json --thresholds 0.5,0.75,0.9
python scripts/detection_calibration.py party_role_truth.json party_role_predictions.json \
  --out party_role_calibration.json --thresholds 0.5,0.75,0.9
```

## Final client-review gate

Pass every exception and review-required proposal into one package before an
analytics or load handoff:

```bash
python scripts/final_review_queue.py \
  classification_exceptions.json reassembly_exceptions.json openai_exceptions.json \
  exceptions.json \
  adjudication_exceptions.json validation_exceptions.json address_exceptions.json \
  schema_discovery_exceptions.json handwriting_client_review.json handwriting_decisions.json validated.json \
  merges.json unattributable.json completeness.json --out final_client_review.json
```

The package is de-duplicated by document/page/region/field/reason, prioritized,
and has a `gate_status` of `blocked_pending_client_review` whenever any item
remains. It never closes an exception, deletes duplicate evidence, or applies an
amendment.
