# Production Operations Controls

These controls improve safety, reproducibility, and reviewability without making
an unmeasured accuracy claim or altering source evidence.

## Immutable run manifest

`scripts/operations.py manifest` hashes every supplied input/artifact and the
non-secret configuration. The versioned manifest records portable artifact IDs,
basenames, byte sizes, SHA-256 values, the manifest schema, and the pipeline
release; it does not retain absolute operator filesystem paths. Retain it with
the run output. Different input or configuration hashes require a new run; do
not reuse state.

## Resumable stage state

`scripts/operations.py stage` records the completed stages in order:
`profile`, `intake`, `extract`, `validate`, and `review`. It refuses skipped
stages and a manifest-hash mismatch. This prevents a resumed batch from quietly
mixing artifacts from a different input set.

## Adapter contracts and credentials

`scripts/operations.py adapter` validates the normalized OCR, HTR,
LLM-adjudication, semantic-schema-discovery, or allocation-policy handoff
before the rest of the control layer receives it. An adapter result identifies a
non-empty engine name and evidence records; it names only an uppercase credential
environment-variable reference. It rejects embedded passwords, tokens, API keys,
or secrets. Keep the raw provider response, model version, source-page/bounding
box evidence, and retry log alongside the normalized artifact.

For `google_handwriting_ocr.py`, validate the HTR output artifact (the object
containing `engine`, `independence_group`, and `annotations`) as `--type htr`.
Retain its companion adapter handoff, rendered-page directory, raw-response
directory, and exceptions beside that contract. The Google result is one
provider-group vote; do not count another Google model or role as an independent
handwriting validator.

`scripts/llm_adapter.py` is the run-level selection entry point for the
implemented OpenAI Responses, Gemini-on-Vertex-AI, and OpenRouter extraction
integrations. The optional `LLM_EXTRACT_PROVIDER=openai` setting reads its client-approved key only from an uppercase
environment variable (default `OPENAI_API_KEY`); `LLM_EXTRACT_PROVIDER=google` uses
the Google Gen AI SDK with `vertexai=True`, the selected project/location, and
Application Default Credentials; `LLM_EXTRACT_PROVIDER=openrouter` uses its separate
OpenRouter key and endpoint. All read only immutable one-page manifest
entries and write a standard OCR adapter handoff plus one raw JSON response
artifact per page. See `runtime-configuration.md`. Their `auto` input mode uses
retained native text only when intake already rule-classified the page; all other
pages use the original one-page PDF. This bounds spend while giving uncertain
pages full document understanding.

The adapter is proposal-only: it retains the intake type separately from a model
type proposal, requires independent consensus for field acceptance, and creates
client-review items for model uncertainty, type disagreements/proposals,
handwriting detection, and provider/schema failures. Retain raw responses and
page hashes; record model, explicit reasoning effort, input mode, client-approved retention settings, retry
policy, and billing limits in the run manifest. The adapter defaults to a
1,000-page run cap, 10 MB per submitted PDF, 100,000 native-text characters per
page, a 120-second request timeout, and two SDK retries. Set lower or
client-approved values explicitly with `--max-pages`, `--max-pdf-bytes`,
`--max-text-chars`, `--timeout-seconds`, `--max-retries`, and `--reasoning-effort`; the adapter
retains the selected model, reasoning effort, limits, and transport policy in
its non-secret handoff artifact.

Independent page requests may use the configured bounded worker count and
exponential backoff. A content-addressed response cache may avoid repeating an
identical provider request, but it is never a canonical-facts store and must be
kept outside retained delivery artifacts. Results are reassembled in manifest
order and raw responses remain evidence. The optional `layout_dedup.py` command
creates review-required unique and exact-duplicate CSV views from display
proposals; it does not mutate source pages or clear client review.

`scripts/llm_adjudication.py` is a separate optional adapter boundary, disabled by default. It may query only explicit candidates whose deterministic validation is clear and whose proposed nonfinancial, printed field exactly agrees with a named independent extractor. It excludes addresses, financial values, handwriting, reassembly, and disagreement. Its `LLM_ADJUDICATION_MIN_CONFIDENCE` is adjustable from 0.99 through 1.0, but every result is either an audit-marked amendment proposal or an exception; all remain final client-review work. It accepts the `amendments` envelope emitted by `adjudicate.py` so those findings are retained, but marks them as requiring explicit candidate evidence and sends none to a provider. Its non-secret handoff records the model, reasoning effort, credential reference, threshold, sampling rate, page/candidate limits, retries, timeout, and raw-response directory.

`scripts/schema_discovery.py` is a separate, disabled-by-default semantic mapping boundary. It accepts exact observed source headers and supplied entity/relationship evidence, produces strict LLM mapping proposals, and reuses only an exact client-approved source-template registry rule. Google Places, when separately enabled, supplies only a bounded candidate set for an observed dealer or brand name. It does not validate a source address, select a city/address, create a canonical field, or alter an entity master. Every proposal and candidate remains client-review work; `registry-update` emits a new effective-dated registry snapshot only from explicit client approvals. Its handoff records model/effort, confidence floor, request cap, and raw-response directory without a key. See `semantic-schema-discovery.md`.

`scripts/table_comprehension.py` is a separate source-native proposal boundary.
It profiles one immutable page, extracts visible source rows against that profile,
and audits the evidence with a separate LLM role. Same-model roles are explicitly
not independent consensus. The assembler maps only exact client-approved registry
rules, produces one aggregated card for a material table concern, and retains
image/row-boundary/provider diagnostics in a quality summary. Its `mappings
--propose-mappings` opt-in delegates only suggestions to `schema_discovery.py`;
a proposal is never an effective mapping. Validate its proposal-only handoff with
`operations.py adapter --type table_comprehension`. See `table-comprehension.md`.

`scripts/table_comprehension_corpus.py` is the operational sequential runner for
large multi-file historical ingestion. It maintains atomically replaced state but
never overwrites page evidence, raw provider responses, source-row proposals, or
layout-context snapshots. Its capped refinement loop is driven by the retained
unresolved-page set, not a model self-confidence score. It sends later pages only
observed source-layout context; it does not send prior speculative cell values.
A changed reread becomes a linked amendment proposal and remains client review.

`scripts/allocation_policy.py` is a separate review-safe policy boundary. Its
adapter handoff records proposal-only operation, disables automatic sales
credit, and leaves client approval outside the provider. Only the generated
client decision return file can authorize a new append-only, effective-dated,
scoped registry rule; raw proposals and registry files are not editable client
decisions. See `allocation-policy.md`.

## Output retention

Use a new output directory for every run. The operations CLI refuses to replace
an existing manifest, adapter contract, privacy inventory, or CSV/HTML/XLSX
review export. Resumable stage state is the sole mutable operations artifact and
is replaced atomically after its manifest and stage-order checks pass.

Initialize and operate a run through `scripts/run_workspace.py`. It creates the
standard run layout and sets the process-local `LLM_CACHE_DIR` and
`LLM_THROTTLE_DIR` to `RUN/runtime/cache` and `RUN/runtime/throttle`; therefore
those generated runtime artifacts cannot accumulate at repository root or in a
later sibling phase directory. It accepts external source/reference inputs and
credentials as reads only, rejects declared write destinations outside `RUN`,
and retains a non-secret command ledger under `RUN/logs/`.

For a full run, retain `RUN/logs/run_authorization.md` before the first provider
call. It is a non-secret operational scope record: run identifier, source
identifiers/hashes, time, selected lanes, authorized provider families, and
whether every in-scope source item may be sent. It authorizes transmission only;
it cannot accept a proposal, clear a gate, enable auto-accept/carry-forward, or
authorize canonical, CRM, or delivery work. Treat phase order as a state machine:
read upstream exceptions before dependent work, retain explicit blockers instead
of omitting lanes/items, and close through workspace audit, required lane
coverage, and a final queue containing base and recovery artifacts.

## Image variants

For the optional pre-pipeline image journal, `visual_ingestion_export.py` is a
local source-retention utility rather than a new extraction lane. It reads an
existing journal without writes and snapshots every original attempt, proposal
version and exception into a new private run subdirectory. Follow
[Visual Intake](visual-ingestion.md) for its receipt-pinned verification and the
subsequent normal source profiling/intake sequence. Keep original bytes and the
derived PDF's transformation/page map together. Include its exception artifact
in final review; source preparation never grants approval. Use a new directory
after an interrupted or later-session export; there is no overwrite/resume.

`scripts/preprocess_pages.py` renders immutable one-page PDF masters to grayscale
PGM files and makes an enhanced grayscale and three complementary binarized siblings. It never
changes the PDF master. Use the variants as OCR voters; retain every source and
variant path in `preprocessing_manifest.json`.

## Privacy preflight

`scripts/operations.py privacy` inventories obvious email, SSN-pattern, and
card-like strings in supplied text artifacts. A match is a review signal, not a
claim that the value is valid PII. The script never redacts or changes originals.
Findings use run-local artifact IDs and basenames rather than absolute operator
paths.
Use its findings to apply client-approved residency, access, and retention rules.

## Reviewer exports

`scripts/operations.py review-export` turns `final_client_review.json` into
CSV, Excel workbook, and escaped static HTML. These are reviewer aids, not authoritative evidence.
The JSON package remains the canonical review artifact. See `references/artifact-contracts.md` for its complete eight-field review contract, and see `references/extraction-schema.md` plus `references/derived-field-schema.md` for the separate record schemas.

`scripts/ai_simulated_client_review.py` produces a different, no-clobber
companion package for AI draft comments. It does not update the review export,
issued workbook, or returned workbook. Retain its comment JSON and exception
JSON as the machine audit, raw per-card provider responses as sensitive
operational artifacts, and its CSV/XLSX only as reviewer views. Every comment
is visibly labelled as simulated and not client authorization; any use of a
draft recommendation still requires the ordinary client-decision import,
operator authorization, and control rerun sequence.
The raw directory also retains an atomic hash-bound checkpoint. `--resume`
continues only unfinished cards under identical inputs and configuration;
`--retry-exceptions` starts a separate recovery overlay for recoverable cards
after a completed run. Freeze the original and recovery directories
independently before append-only reconciliation.

## Live progress and spend monitoring

All bounded LLM lanes should use the shared byte-adaptive packet utility in
`llm_runtime.py`. A configured record-count is only an upper bound: it reduces
packets to the exact serialized-byte budget, retains an individually oversized
source item as an exception, and retains any positive batch-cap remainder rather
than silently truncating it. The utility is client-agnostic and reusable by every
provider/review lane.

The exception-resolution lane additionally fails closed on duplicate or missing
record identifiers. It counts every supplied finding, sends the same named
exceptions, compact source records, and reasoning-only client context to both
reviewers, verifies that evidence quotes and related IDs occur within that
packet, and records rejected primary outputs instead of silently filtering them.

General client comments are normalized by `client_input_comments.py` into the
same `client_review_context_v1` boundary as returned-workbook comments.
`table_comprehension_corpus.py --client-context` includes them in forward and
refinement packets and binds their SHA-256 into resumable state. Comments are
reasoning-only and cannot act as document evidence, consensus, authorization,
or control clearance.

`scripts/run_monitor.py` is a read-only observer for an active run directory.
Start it after intake has created the manifest and before the first provider
invocation:

```bash
python scripts/run_monitor.py RUN_DIRECTORY --expected-pages 51 --interval 2 \
  --out RUN_DIRECTORY/progress_monitor.json --stop-when-complete
```

The monitor selects the fullest retained pass for each raw-response lane,
counts successful and failed responses, and prices normalized OpenAI/Gemini
usage. It also counts Google Document AI pages, Address Validation requests,
and Google Places Text Search requests from their non-secret output summaries.
The Google handwriting adapter currently reports through its retained adapter
and exception artifacts; add pricing only from an approved account-specific
Cloud Vision SKU schedule rather than treating OCR pages as LLM tokens.
It writes snapshots atomically and never reads, emits, or stores credentials or
provider response content. Built-in prices are estimates; use `--price-file`
for an approved account-specific rate/SKU schedule and reconcile final spend
against provider billing exports.

## Automated release checks

`.github/workflows/quality.yml` runs `scripts/quality_gate.sh` under a
45-minute CI job limit; the wrapper runs tests, strict coverage, Ruff, the versioned
repository release check, acceptance controls, public corpus validation, LaTeX
PDF rendering, and a tracked-PDF diff check on pushes and pull requests. It
cannot replace client calibration, all-page visual PDF inspection, or the final
client-review gate.

## Layout-aware client-output proposals

`scripts/layout_aware_extract.py` is an optional, review-only, visual-layout pass. A non-secret layout contract names client-facing display columns; it is never a canonical mapping. Run `page` separately for each immutable intake page and retain its raw response; then use `combine` to make a proposal-only adapter record. Checkpointed page outputs are no-clobber and resume safely after a worker timeout. The output requires independent consensus and final client review.
