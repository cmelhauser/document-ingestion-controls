# Runtime Configuration

## Client-configured business platform

The optional unified MCP/API platform does not add fixed `.env` names. Its
secret-free YAML (`business_data_platform_v1`) declares the exact environment
variable names used for the OAuth introspection secret, TLS private-key **file
path**, record-authorization signing secret, and optional target bearer token.
All other dynamic platform settings—including tenant/claims/roles, URLs,
Origins, paths, limits, writable mappings, analytics datasets, and saved
reports—are explicit YAML fields documented exhaustively in
[`business-data-platform.md`](business-data-platform.md) and illustrated by
`config/client-platform.example.yaml`. A setting named in the YAML must exist in
the launch environment when its capability is used; secret values must never be
written into YAML or this repository.

This file is the canonical environment-variable reference. Every assignment in
the tracked `.env.example` appears once in the tables below with its checked-in
default and operational meaning. `scripts/release_check.py` enforces that
coverage and default alignment.

## Purpose and precedence

Every repository command that reads configuration loads the repository-root
`.env` file when its entry point starts, never when its module is imported,
unless the process has switched the file off with
`BUSINESS_DOCUMENT_IGNORE_PROJECT_ENV` (below). Create it once with `cp .env.example .env`, then
enter approved credentials and limits there. Settings take precedence in this
order: explicit command-line option, process environment or secret manager, root
`.env`, then the safe code default. The `.env` file is Git-ignored and must never
be placed in a manifest, handoff, source delivery, or output artifact.

Run-specific paths, output names, source files, and client reference exports stay
on the command line. They are evidence-scoped inputs, not shared runtime
configuration. See [`command-line-reference.md`](command-line-reference.md) for
the complete CLI index.

Every production manifest should be created with `operations.py manifest
--config-from-env`. That records the effective non-secret settings visible to
the process after `.env` loading and process-environment precedence. Secrets are
redacted; only credential variable names are retained.

## Process-only variables

The following three variables are intentionally **not** assignments in
`.env.example`: they choose a local executable, a local approved-fact snapshot,
or whether a process reads the root `.env` at all, not a reusable pipeline
policy. They are nevertheless part of the supported operator interface and are
checked by the release gate. Do not put a credential, client source path, or
mutable working database in any value.

| Setting | Default | Meaning |
|---|---:|---|
| `PYTHON_BIN` | script-specific | Optional Python executable for repository shell wrappers. `quality_gate.sh` uses `python3` when it is unset. The local MCP launcher and acceptance wrappers first use `.venv/bin/python` and then fall back to `python3` when available. Set an absolute or environment-resolved executable only when the standard virtual environment is not the intended runtime. |
| `BUSINESS_DOCUMENT_RETRIEVAL_DB` | `business_retrieval.sqlite` at the repository root | Default immutable SQLite retrieval snapshot for `scripts/run_retrieval_mcp.sh` when the caller does not supply the positional database path. The positional path wins. It must be a completed Phase 6 snapshot; the launcher is read-only and does not build, repair, or replace it. |
| `BUSINESS_DOCUMENT_IGNORE_PROJECT_ENV` | `false` | When true, no command loads the repository-root `.env`: the process environment and the code defaults are the whole configuration, as in CI. Child processes inherit it. The test suite sets it for its whole session so every script a test starts reads what CI reads. Export it from the shell or process manager; a line in `.env` cannot switch `.env` off. Strict boolean: any other value fails the command when it starts. |

## Value conventions and change discipline

- `blank` means there is no configured value. A blank secret or required
  provider identifier causes the explicitly invoked lane to fail closed.
- Booleans accept the repository's normalized true/false forms; use `true` or
  `false` in `.env` for clarity.
- Counts and byte/token limits are non-negative or positive integers as stated
  by the consuming parser. Timeouts, ratios, and backoff values may be decimal
  numbers. Invalid values or unsafe ranges fail before provider I/O.
- A credential setting ending in `_CREDENTIAL_ENV` or `_API_KEY_ENV` stores the
  *name* of a secret-bearing environment variable, never the secret itself.
- A confidence threshold filters proposal presentation only. It is not proof,
  client approval, or permission to clear a protected finding.
- Raising concurrency, packet, page, or token limits can increase cost, quota
  pressure, and failure blast radius. Change them only after provider-capability,
  privacy, and representative-corpus validation; record the effective values in
  the run manifest.
- Lowering timeouts or caps is safe when the expected failure behavior has been
  accepted. Raising them never expands the approval boundary.

## Pipeline control settings

| Setting | Default | Meaning |
|---|---:|---|
| `HANDWRITING_POLICY` | `comment_only` | Extraction-lane handwriting observations are retained as non-blocking comments; `strict` is available for an explicitly gated lane. |
| `ARITHMETIC_TOLERANCE` | `0.01` | Absolute per-check tolerance when the CLI does not provide `--tolerance`. |
| `ARITHMETIC_DEFER_UNASSIGNED_PAGES` | `false` | Defers page-level arithmetic proof until an evidence-backed document group exists. |
| `COMPLETENESS_DATE_ORDER` | `month_first` | How an ambiguous numeric date such as `05/03/2023` is read: `month_first` (US, the historical behavior), `day_first`, or `iso_only`, which refuses to guess and buckets only explicit `YYYY-MM` dates. ISO dates always parse regardless of this setting. Changing it re-buckets every ambiguous date in the corpus, so set it once from the client's export convention. |
| `COMPLETENESS_BLOCK_ON_UNANALYZED_POPULATION` | `false` | When true, a vendor series or vendor-month that could not be analyzed for gaps blocks the completeness gate instead of being reported as a stated limitation in `inference_coverage`. A corpus too small to infer from is a scoping decision, so this is opt-in. |
| `REASSEMBLY_BROAD_UNORDERED_PROPOSALS` | `false` | Enables review-only unordered grouping proposals when a typed identifier supports the link; the checked-in example and code default both remain fail-closed. |
| `PREPROCESS_DPI` | `300` | Resolution for grayscale and sibling binarized preprocessing variants. |
| `ANALYTICS_ENABLED` | `true` | Enables descriptive, gate-aware analytics output; it never clears review or approves financial facts. |
| `CLIENT_REVIEW_GROUPING_ENABLED` | `false` | Enables proposal-only root-cause grouping by document family and finding family as the safe artifact's client-presentation surface; it never deletes queue items or approves facts. |
| `CLIENT_REVIEW_GROUPING_OUTPUT_FILE` | `client_review_grouping.json` | Default output filename for the retained-item grouping and client batch-rule proposal artifact. |

## Run-level extraction-provider selection

<!-- supported-llm-providers: anthropic, google, openai, openrouter -->

The shared LLM provider identifiers are `anthropic`, `google`, `openai`, and
`openrouter`. Every provider selector in this reference accepts that complete
set unless its row explicitly states a narrower contract. The release check
compares this declaration and the matching `.env.example` declaration with the
provider set in `scripts/runtime_config.py`.

`scripts/llm_adapter.py` selects exactly one extraction provider for the whole
invocation. `openai` invokes the existing Responses adapter; `google` invokes
Gemini through the Google Gen AI SDK with `vertexai=True`. It never mixes
providers by page. Both output the same review-only, consensus-required engine
record contract. The model availability and client privacy/residency approval
remain engagement decisions.

| Setting | Default | Meaning |
|---|---:|---|
| `LLM_EXTRACT_PROVIDER` | `openai` | One of the shared LLM provider identifiers above; read only from `.env` and fixed for the full extraction adapter run. |
| `LLM_DOCUMENT_FAMILY` | `auto` | Source-family routing hint for extraction. Use `auto` for mixed/unfamiliar corpora or `commission_statement` / `commission_report` only after the retained corpus has been verified; a conflicting page remains review-required. |
| `LLM_CORPUS_CONTEXT` | blank | Path to a corpus-context file built by `scripts/extraction_context.py`, appended to the extraction prompt as reasoning-only context. It states which printed labels the engagement has **approved** as belonging to a controlled field and which document families two vendors agreed on; it never supplies a value, and the page always wins. Unset means no context; an empty or oversized file is refused rather than ignored. Set the **same** path for both extraction lanes -- `consensus.py` refuses a pair whose context hashes differ, because coaching one engine and not the other measures the coaching rather than the page. Changing it changes the response-cache key, so no reading taken under different conditioning is replayed. |
| `OPENAI_MODEL` | `gpt-5.6-sol` | OpenAI extraction-lane model. The configured request ceiling plus output reserve remains below Sol's context window; change only after schema/capability and golden-set evaluation. |
| `GOOGLE_VERTEX_AI_MODEL` | `gemini-2.5-pro` | Vertex extraction-lane model. Verify regional availability, schema behavior, and acceptance evidence before changing. |
| `OPENROUTER_MODEL` | `nvidia/nemotron-3-super-120b-a12b:free` | OpenRouter extraction-lane route. Confirm that the route still supports the required structured/native-text contract before a live run: on a scanned corpus the model must declare `file` in `architecture.input_modalities`, or the adapter refuses rather than let OpenRouter OCR the page with another vendor's engine and record the wrong vendor as having read it. Qualify any change with `engine_agreement.py` on a matched page sample before committing a corpus -- a route is a new reader, not a setting -- and rank candidates by **output** price, which dominates the bill on these pages. |
| `LLM_CONSENSUS_TIE_PROVIDER` | blank | Provider for the optional third consensus lane, `consensus_tiebreaker`. Deliberately has no default: a tiebreaker that resolves to the vendor already reading another lane is one engine compared with itself, so the lane refuses to run until an operator names a third vendor. |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Anthropic extraction-lane model. |
| `ANTHROPIC_CONSENSUS_MODEL` | `claude-haiku-4-5-20251001` | Model for an Anthropic consensus lane. |
| `ANTHROPIC_REASONING_MODEL` | `claude-haiku-4-5-20251001` | Model for an Anthropic reasoning lane. |
| `LLM_CONSENSUS_PRI_PROVIDER` | `openai` | Provider for the separately invoked primary consensus extraction lane. |
| `LLM_CONSENSUS_SEC_PROVIDER` | `google` | Provider for the separately invoked secondary consensus extraction lane. Configure a genuinely independent provider from the primary lane. |
| `LLM_REASONING_PROVIDER` | `openai` | Provider for the proposal-only card, cross-record, and full-dataset reasoning lanes. |
| `GOOGLE_VERTEX_AI_CONSENSUS_MODEL` | `gemini-2.5-flash` | Google model selected for a consensus lane when Google is the configured provider. |
| `GOOGLE_VERTEX_AI_REASONING_MODEL` | `gemini-2.5-pro` | Google model selected for the reasoning lane. |
| `OPENAI_CONSENSUS_MODEL` | `gpt-5.6-luna` | OpenAI model selected for a consensus lane when OpenAI is the configured provider. |
| `OPENAI_REASONING_MODEL` | `gpt-5.6-luna` | OpenAI model selected for proposal-only reasoning lanes. |
| `OPENROUTER_CONSENSUS_MODEL` | `nvidia/nemotron-3-super-120b-a12b:free` | OpenRouter model selected when OpenRouter is assigned to a consensus lane. |
| `OPENROUTER_REASONING_MODEL` | `nvidia/nemotron-3-ultra-550b-a55b:free` | OpenRouter model selected when OpenRouter is assigned to the reasoning lane. |

## OpenAI Responses adapter

| Setting | Default | Meaning |
|---|---:|---|
| `OPENAI_API_KEY` | blank | Required secret when the adapter is invoked. |
| `OPENAI_CREDENTIAL_ENV` | `OPENAI_API_KEY` | Name of the environment variable read for the key. |
| `OPENAI_REASONING_EFFORT` | `medium` | `none`, `low`, `medium`, `high`, `xhigh`, or `max`; recorded in the handoff. |
| `OPENAI_INPUT_MODE` | `auto` | `auto`, `text`, or `pdf`; `auto` conservatively uses retained native text only for rule-classified pages. |
| `OPENAI_MAX_PAGES` | `1000` | Maximum submitted pages per invocation. |
| `OPENAI_MAX_PDF_BYTES` | `10000000` | Maximum bytes for one submitted PDF. |
| `OPENAI_MAX_TEXT_CHARS` | `100000` | Maximum retained native-text characters per page. |
| `OPENAI_TIMEOUT_SECONDS` | `300` | SDK request timeout. This allows a bounded Sol reasoning response to complete; retry behavior remains separately capped. |
| `OPENAI_MAX_RETRIES` | `2` | SDK retry count; use a non-negative integer. |
| `OPENAI_MAX_WORKERS` | `8` | Bounded concurrent page workers; output order remains manifest order. |
| `OPENAI_RETRY_BACKOFF_SECONDS` | `1` | Initial page-retry delay in seconds; use a positive value. |
| `OPENAI_MAX_BACKOFF_SECONDS` | `120` | Maximum retry delay in seconds; keep it at or above the initial delay. |
| `OPENAI_RATE_LIMIT_REQUESTS_PER_MINUTE` | `500` | Local request ceiling for the selected OpenAI project/model; lower it to match the approved quota. |
| `OPENAI_RATE_LIMIT_TOKENS_PER_MINUTE` | `2000000` | Local token ceiling; lower it for shared projects or tighter approved quotas. |
| `OPENAI_MAX_REQUEST_TOKENS` | `550000` | Preflight input-plus-reserve ceiling; oversized packets fail before network I/O. |
| `OPENAI_OUTPUT_TOKEN_RESERVE` | `4096` | Tokens held back for structured output; increase only within the selected model's accepted context limit. |

## Gemini on Vertex AI adapter

The Vertex route is selected with `LLM_EXTRACT_PROVIDER=google`. The Google Gen AI
SDK authenticates using Application Default Credentials (ADC), not an API key:
use workload identity, `gcloud auth application-default login`, or an external
`GOOGLE_APPLICATION_CREDENTIALS` path outside the repository. Billing is charged
to `GOOGLE_VERTEX_AI_PROJECT_ID` because the SDK is initialized with
`vertexai=True`.

| Setting | Default | Meaning |
|---|---:|---|
| `GOOGLE_VERTEX_AI_PROJECT_ID` | blank | Required client-authorized GCP project for Vertex billing. |
| `GOOGLE_VERTEX_AI_LOCATION` | `us-central1` | Selected Vertex region. |
| `GOOGLE_APPLICATION_CREDENTIALS` | blank | Optional external ADC service-account file path; keep the file outside the repository and leave blank when gcloud/workload-identity ADC is used. |
| `GOOGLE_VERTEX_AI_CREDENTIAL_ENV` | `GOOGLE_APPLICATION_CREDENTIALS` | Non-secret name of the ADC environment variable, when a credential-file path is used. |
| `GOOGLE_VERTEX_AI_MODEL_CONTEXT_TOKENS` | `1048576` | Gemini 2.5 Pro hard input-context limit; recorded for capacity planning, not used as a routine packet target. |
| `GOOGLE_VERTEX_AI_MODEL_MAX_OUTPUT_TOKENS` | `65536` | Gemini 2.5 Pro hard maximum generated-output limit. Proposal lanes retain a smaller output reserve. |
| `GOOGLE_VERTEX_AI_MODEL_MAX_INPUT_BYTES` | `524288000` | Gemini 2.5 Pro hard 500 MB request-payload limit. `GOOGLE_VERTEX_AI_MAX_PDF_BYTES` is refused before work begins if it exceeds this. |
| `GOOGLE_VERTEX_AI_MODEL_MAX_FILES_PER_REQUEST` | `3000` | Gemini 2.5 Pro hard maximum input-file count. Each retained page is one file, so `GOOGLE_VERTEX_AI_MAX_PAGES` is refused before work begins if it exceeds this. |
| `GOOGLE_VERTEX_AI_MODEL_MAX_PAGES_PER_FILE` | `1000` | Gemini 2.5 Pro hard maximum PDF pages per input file on Vertex AI. Recorded for capacity planning only: intake bursts every source into one-page masters, so a submitted file always carries exactly one page and this ceiling is satisfied by construction rather than by comparison. |
| `GOOGLE_VERTEX_AI_MODEL_MAX_FILE_BYTES` | `50000000` | Gemini 2.5 Pro hard API/Cloud Storage file-import limit (50 MB). `GOOGLE_VERTEX_AI_MAX_PDF_BYTES` is refused before work begins if it exceeds this. |
| `GOOGLE_VERTEX_AI_INPUT_MODE` | `auto` | `auto`, `text`, or `pdf`; same retained-text routing policy as OpenAI. |
| `GOOGLE_VERTEX_AI_MAX_PAGES` | `1000` | Maximum submitted pages per invocation. |
| `GOOGLE_VERTEX_AI_MAX_PDF_BYTES` | `10000000` | Maximum bytes for one submitted PDF. |
| `GOOGLE_VERTEX_AI_MAX_TEXT_CHARS` | `100000` | Maximum retained native-text characters per page. |
| `GOOGLE_VERTEX_AI_TIMEOUT_SECONDS` | `180` | SDK request timeout. The larger bound is for complex scanned pages and does not approve a response. |
| `GOOGLE_VERTEX_AI_MAX_RETRIES` | `4` | SDK retry count; use a non-negative integer. Failed pages remain explicit exceptions. |
| `GOOGLE_VERTEX_AI_MAX_WORKERS` | `8` | Bounded concurrent Vertex page workers; output order remains manifest order. |
| `GOOGLE_VERTEX_AI_RETRY_BACKOFF_SECONDS` | `1` | Initial page-retry delay in seconds; use a positive value. |
| `GOOGLE_VERTEX_AI_MAX_BACKOFF_SECONDS` | `120` | Maximum retry delay; keep it at or above the initial delay. |
| `GOOGLE_VERTEX_AI_RATE_LIMIT_REQUESTS_PER_MINUTE` | `12` | Conservative local request ceiling for shared Vertex capacity; set it no higher than the accepted project/model quota. |
| `GOOGLE_VERTEX_AI_RATE_LIMIT_TOKENS_PER_MINUTE` | `80000` | Conservative local token ceiling for shared Vertex capacity; lower it for tighter quota. |
| `GOOGLE_VERTEX_AI_MAX_REQUEST_TOKENS` | `55000` | Operational preflight ceiling. It stays below the 1,048,576-token model limit and, unlike the previous `200000`, below the per-minute safety budget that binds first (see the effective-ceiling note after this table); oversized packets fail before provider I/O. |
| `GOOGLE_VERTEX_AI_OUTPUT_TOKEN_RESERVE` | `8192` | Operational maximum output for Gemini's structured review compatibility client and the matching preflight reserve. It remains well below Gemini 2.5 Pro's 65,536-token hard output limit; lower it only after validating that the selected JSON schema still completes without truncation. |
| `LLM_CACHE_DIR` | `.llm-cache` | Optional content-addressed cache keyed by provider, model, schema, page hash, and input mode. **A cache hit replays a retained response instead of calling the provider.** The retained raw artifact records `cache_hit`, `cache_key`, `cache_dir`, and `cache_written_at` so a replay is distinguishable from a fresh reading; a genuinely fresh provider call requires an empty or unset cache directory. |
| `LLM_TOKEN_ESTIMATE_SAFETY_FACTOR` | `1.15` | Multiplier applied to every request-size estimate, from 1.0 through 4.0. The estimator counts UTF-8 bytes with a wide-character correction and then applies this factor, so the preflight errs high rather than letting an oversized request reach the provider. |
| `LLM_THROTTLE_DIR` | `.llm-rate-limit` | Local lock/timestamp directory coordinating provider requests across parallel processes; it contains no credentials or response content. |
| `BUSINESS_DOC_RUN_ROOT` | blank | Set by `scripts/run_workspace.py run` to the active contained run root. Do not set it manually in `.env`: `llm_adapter.py` uses it to hold a per-lane single-flight lock, refusing a second active invocation of the same lane before provider-client construction. |

For a controlled document run, do not set any of these paths to a
repository-root value. `scripts/run_workspace.py run RUN -- ...` overrides the
process values to the contained `RUN/` root and `RUN/runtime/` directories,
retaining cache, throttle state, and lane locks as part of that run without
changing the checked-in defaults used by local tests.
| `LLM_MIN_REQUEST_INTERVAL_SECONDS` | `0.25` | Minimum interval between requests to the same provider across local processes. Set according to provider quota and client authorization. |
| `LLM_RATE_LIMIT_SAFETY_RATIO` | `0.8` | Fraction of each provider's configured request/token ceilings used by the shared throttle, leaving headroom for estimation error and parallel runs. |

### Effective per-request prompt ceiling

Two bounds apply to every request, and the smaller one governs:

```
effective prompt ceiling =
    min(<PROVIDER>_MAX_REQUEST_TOKENS,
        <PROVIDER>_RATE_LIMIT_TOKENS_PER_MINUTE x LLM_RATE_LIMIT_SAFETY_RATIO)
    - <PROVIDER>_OUTPUT_TOKEN_RESERVE
```

With the shipped Vertex settings that is
`min(55000, 80000 x 0.8) - 8192 = 46,808` prompt tokens. Raising
`GOOGLE_VERTEX_AI_MAX_REQUEST_TOKENS` alone changes nothing once the per-minute
budget is the smaller bound -- raise
`GOOGLE_VERTEX_AI_RATE_LIMIT_TOKENS_PER_MINUTE` as well, and only within the
capacity the account actually has. A rejected request names both bounds and the
resulting effective ceiling.
| `LLM_FAIL_FAST_ON_PROVIDER_QUOTA` | `true` | Global shared retry policy. A recognized account/model quota response (including quota-bearing 429s) opens a process-local provider circuit; later requests fail closed before provider I/O and must be retained as explicit provider exceptions. Set `false` only for an approved experiment that must continue after quota signals. |

## OpenRouter Chat Completions adapter

OpenRouter is a separate provider boundary and credential namespace. The
implementation uses the OpenAI SDK only as an HTTP client pointed at
`https://openrouter.ai/api/v1`; it does not use OpenAI credentials or OpenAI
Responses. Native text is the default because file/PDF support varies by
routed model; pages without usable native text fail closed into provider review.

| Setting | Default | Meaning |
|---|---:|---|
| `OPENROUTER_API_KEY` | blank | Required secret when an OpenRouter lane is invoked. |
| `OPENROUTER_CREDENTIAL_ENV` | `OPENROUTER_API_KEY` | Name of the environment variable read for the key. |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter API base URL. |
| `OPENROUTER_MAX_PDF_BYTES` | `10000000` | Retained contract cap for a page PDF; unsupported file input still fails closed. |
| `OPENROUTER_INPUT_MODE` | `text` | `auto`, `text`, `pdf`, or `image`. `pdf` submits a retained page PDF, which requires a model whose OpenRouter `input_modalities` include `file`; the adapter verifies that against the live catalogue and refuses otherwise, so OpenRouter cannot substitute another vendor's OCR. `image` renders the retained page locally and submits pixels, and is verified against `image` in the same catalogue. `auto` never selects `image`. |
| `OPENROUTER_PROVIDER_ONLY` | blank | Comma-separated OpenRouter provider slugs permitted to serve this lane, such as `coreweave,modal`. Empty routes normally. OpenRouter otherwise selects the serving host itself and the record names only the model, so a degraded host returns a truncated stream that reads downstream as a model which could not read the page. Slugs are lowercase and hyphenated; an unknown one is refused by OpenRouter, which answers with the providers actually serving the model. |
| `OPENROUTER_IMAGE_DPI` | `200` | Resolution of the local render used by `image` input mode, between 72 and 600. Retained per page with the image digest: the same page at a different resolution is a different reading, not the same one. |
| `OPENROUTER_REASONING_EFFORT` | `medium` | Recorded lane setting; model-specific support remains an OpenRouter/model capability decision. |
| `ANTHROPIC_API_KEY` | blank | Required secret when an Anthropic lane is invoked. |
| `ANTHROPIC_CREDENTIAL_ENV` | `ANTHROPIC_API_KEY` | Name of the environment variable read for the key. |
| `ANTHROPIC_BASE_URL` | blank | Optional Messages API base URL; blank uses the SDK default. |
| `ANTHROPIC_WORKSPACE_ID` | blank | Required when the key is identity-linked: the API refuses a request that does not name the workspace it acts in. An identifier, not a secret. |
| `ANTHROPIC_INPUT_MODE` | `pdf` | `auto`, `text`, or `pdf`. Anthropic reads a retained page PDF natively as a document block, so `pdf` keeps the recorded vendor the one that read the glyphs. |
| `ANTHROPIC_MAX_BACKOFF_SECONDS` | `120.0` | Backoff ceiling. |
| `ANTHROPIC_MAX_PAGES` | `1000` | Retained contract cap on pages per invocation. |
| `ANTHROPIC_MAX_PDF_BYTES` | `10000000` | Retained contract cap for a page PDF. |
| `ANTHROPIC_MAX_RETRIES` | `2` | Bounded retries per request after a transient provider failure. |
| `ANTHROPIC_MAX_TEXT_CHARS` | `100000` | Retained contract cap for native-text input. |
| `ANTHROPIC_MAX_WORKERS` | `8` | Concurrent page requests. |
| `ANTHROPIC_REASONING_EFFORT` | `medium` | Recorded lane setting; how it is sent depends on the model's thinking contract. |
| `ANTHROPIC_RETRY_BACKOFF_SECONDS` | `1.0` | Initial retry backoff. |
| `ANTHROPIC_THINKING_MODE` | blank | `adaptive`, `budget`, `none`, or blank to choose by model version. Claude 4.5 and earlier take `thinking.type: enabled` with a token budget; 4.7 and later reject that and take `thinking.type: adaptive` with `output_config.effort`. Asking the wrong way is a hard 400, not a downgrade. |
| `ANTHROPIC_TIMEOUT_SECONDS` | `120` | Per-request provider timeout. |
| `ANTHROPIC_MAX_OUTPUT_TOKENS` | `16384` | Required output ceiling for the Messages API, which has no server default. Too low truncates a dense page, which is indistinguishable from a model that read fewer rows. |
| `OPENROUTER_MAX_PAGES` | `1000` | Maximum pages submitted in one invocation. |
| `OPENROUTER_MAX_TEXT_CHARS` | `100000` | Maximum retained native-text characters per page; pages without usable text fail closed. |
| `OPENROUTER_TIMEOUT_SECONDS` | `120` | Per-request timeout in seconds. |
| `OPENROUTER_MAX_RETRIES` | `2` | Non-negative transient retry count after the initial attempt. |
| `OPENROUTER_MAX_WORKERS` | `8` | Maximum independent page workers; output remains manifest ordered. |
| `OPENROUTER_RETRY_BACKOFF_SECONDS` | `1` | Initial jittered retry delay in seconds. |
| `OPENROUTER_MAX_BACKOFF_SECONDS` | `120` | Maximum retry delay; keep it at or above the initial delay. |
| `ANTHROPIC_RATE_LIMIT_REQUESTS_PER_MINUTE` | `60` | Local request ceiling for Anthropic lanes. |
| `ANTHROPIC_RATE_LIMIT_TOKENS_PER_MINUTE` | `100000` | Local token ceiling for Anthropic lanes. |
| `ANTHROPIC_MAX_REQUEST_TOKENS` | `80000` | Preflight packet ceiling; an oversized request fails before network I/O rather than becoming a retained provider exception. |
| `ANTHROPIC_OUTPUT_TOKEN_RESERVE` | `4096` | Tokens reserved for structured output within the model's context limit. |
| `OPENROUTER_RATE_LIMIT_REQUESTS_PER_MINUTE` | `60` | Local request ceiling; use the lowest accepted limit among routed models. |
| `OPENROUTER_RATE_LIMIT_TOKENS_PER_MINUTE` | `100000` | Local token ceiling; use the lowest accepted route limit. |
| `OPENROUTER_MAX_REQUEST_TOKENS` | `75000` | Preflight packet ceiling, kept below the per-minute safety budget; oversized requests fail before network I/O. |
| `OPENROUTER_OUTPUT_TOKEN_RESERVE` | `4096` | Tokens reserved for structured output within the routed model's context limit. |

## Optional source-native table comprehension

`scripts/table_comprehension.py` is invoked explicitly and never enables an
automatic canonical mapping. Its profile, source-row, and evidence-audit roles
may use the configured model, but are not independent consensus when they share
that model. The `mappings --propose-mappings` flag creates review-required
semantic-mapping suggestions only; explicit client approval and a registry update
remain necessary before assembly can map a source label.

| Setting | Default | Meaning |
|---|---:|---|
| `TABLE_COMPREHENSION_REASONING_EFFORT` | `medium` | Supported Responses reasoning effort for every role in a run. |
| `TABLE_COMPREHENSION_CREDENTIAL_ENV` | `OPENAI_API_KEY` | Uppercase environment variable that names the secret. |
| `TABLE_COMPREHENSION_TIMEOUT_SECONDS` | `120` | Per-call timeout in seconds for profile, row, and audit prompts. |
| `TABLE_COMPREHENSION_MAX_RETRIES` | `2` | Non-negative transient retry count per provider call. |
| `TABLE_COMPREHENSION_MAX_PAGES` | `500` | Hard per-corpus submitted-page cap; split a larger corpus into separately approved runs. |
| `TABLE_COMPREHENSION_CLIENT_CONTEXT_MAX_BYTES` | `500000` | Maximum bytes for the complete hash-bound reasoning-only client-comment context. The lane fails before provider I/O rather than truncating or dropping a comment. |
| `TABLE_COMPREHENSION_REFINEMENT_PASSES` | `3` | Hard maximum count of post-forward reread passes. `0` disables refinement. |
| `TABLE_COMPREHENSION_REFINEMENT_TOLERANCE` | `0` | Maximum symmetric difference in successive unresolved-page sets before the loop stops as stable. It is not an accuracy or confidence threshold. |
| `TABLE_COMPREHENSION_REFINEMENT_REUSE_PROFILE` | `true` | Reuses the retained immutable source-profile packet in refinement, limiting each reread to two primary LLM PDF calls: rows and audit. Set `false` only for a separately approved full reprofiling experiment. |
| `TABLE_COMPREHENSION_MAPPING_MIN_CONFIDENCE` | `0.99` | Proposal floor for the delegated schema-discovery mapping lane; it never substitutes for client approval. |

## Google Document AI independent OCR/table evidence

`scripts/google_document_ai_adapter.py` is disabled unless
`GOOGLE_DOCUMENT_AI_ENABLED=true` or its explicit `--enable` flag is supplied.
It sends one immutable PDF page to the selected Document AI processor, retains
the complete per-page response, and emits visible text, token coordinates, and
source-native table cells. It produces neither a canonical mapping nor a final
field decision. When supplied to table comprehension, the initial LLM audit is
run without it; only an LLM finding or diagnostic triggers the bounded buddy
check with this independent provider evidence. Financial and identity-critical
values still require deterministic reconciliation and final client review.

| Setting | Default | Meaning |
|---|---:|---|
| `GOOGLE_DOCUMENT_AI_ENABLED` | `false` | Explicit live-call permission for this proposal-only adapter. |
| `GOOGLE_DOCUMENT_AI_CREDENTIAL_ENV` | `GOOGLE_DOCUMENT_AI_ACCESS_TOKEN` | Name of the environment variable that holds the token. |
| `GOOGLE_DOCUMENT_AI_PROJECT_ID` | blank | The client-authorized Google Cloud project. |
| `GOOGLE_DOCUMENT_AI_LOCATION` | `us` | The client-authorized processor region. |
| `GOOGLE_DOCUMENT_AI_PROCESSOR_ID` | blank | The client-authorized processor identifier. |
| `GOOGLE_DOCUMENT_AI_PROCESSOR_VERSION` | blank | Optional pinned processor version; blank follows the processor default. |
| `GOOGLE_DOCUMENT_AI_MAX_PAGES` | `1000` | Maximum submitted pages per invocation. |
| `GOOGLE_DOCUMENT_AI_MAX_PDF_BYTES` | `10000000` | Maximum bytes for one submitted PDF. |
| `GOOGLE_DOCUMENT_AI_TIMEOUT_SECONDS` | `180` | Per-page HTTPS timeout for complex scanned pages. |
| `GOOGLE_DOCUMENT_AI_NATIVE_PDF_PARSING` | `false` | Requests provider native-PDF parsing only when approved. |

Document AI uses OAuth authentication for its REST processing endpoint, so a
Google Maps-style API key is not an adequate credential. Keep a service-account
credential file outside this repository and exchange it for a short-lived access
token in the approved runtime. The handoff records only the credential variable
name, processor configuration, caps, and raw-response directory.

## Google Cloud Vision handwriting OCR

`scripts/google_handwriting_ocr.py` is disabled unless
`GOOGLE_HANDWRITING_OCR_ENABLED=true` or `--enable` is supplied. It renders
each immutable one-page PDF at the configured DPI, sends that image to Google
Cloud Vision `DOCUMENT_TEXT_DETECTION`, and retains the full provider response.
The default language hint selects handwriting-aware English OCR. Blank the hint
to use automatic language detection, or configure a client-approved supported
hint. The service does not classify every returned word as handwriting; the
lane binds words only to separately supplied normalized handwriting regions.
Its output is one proposal-only HTR vote and requires independent reconciliation.

| Setting | Default | Meaning |
|---|---:|---|
| `GOOGLE_HANDWRITING_OCR_ENABLED` | `false` | Explicit live-call permission for the proposal-only handwriting OCR lane. |
| `GOOGLE_HANDWRITING_OCR_CREDENTIAL_ENV` | `GOOGLE_HANDWRITING_OCR_ACCESS_TOKEN` | Name of the environment variable containing a short-lived OAuth access token. |
| `GOOGLE_HANDWRITING_OCR_PROJECT_ID` | blank | Client-authorized Google Cloud project used for quota and regional routing. |
| `GOOGLE_HANDWRITING_OCR_LOCATION` | `us` | `us`, `eu`, or `global`; choose the approved processing/residency endpoint. |
| `GOOGLE_HANDWRITING_OCR_LANGUAGE_HINTS` | `en-t-i0-handwrit` | Comma-separated Cloud Vision OCR language hints; blank enables provider auto-detection. |
| `GOOGLE_HANDWRITING_OCR_MAX_PAGES` | `1000` | Maximum submitted pages per invocation. |
| `GOOGLE_HANDWRITING_OCR_MAX_PDF_BYTES` | `10000000` | Maximum bytes for one immutable input page PDF. |
| `GOOGLE_HANDWRITING_OCR_MAX_IMAGE_BYTES` | `20000000` | Maximum bytes for one rendered page image before network I/O. |
| `GOOGLE_HANDWRITING_OCR_TIMEOUT_SECONDS` | `180` | Per-page HTTPS timeout, from greater than zero through 300 seconds. |
| `GOOGLE_HANDWRITING_OCR_MAX_RETRIES` | `2` | Non-negative transient retry count per page; remaining failures stay explicit. |
| `GOOGLE_HANDWRITING_OCR_RENDER_DPI` | `300` | Retained PNG render resolution, from 72 through 600 DPI. |

`scripts/reauthorize_google.py` enables `vision.googleapis.com` when approved
and writes the same short-lived ADC token to both Document AI and handwriting
OCR variable names in its external `0600` file.

There is deliberately no token setting in `.env` or `.env.example`. When the
variable named by `GOOGLE_DOCUMENT_AI_CREDENTIAL_ENV` or
`GOOGLE_HANDWRITING_OCR_CREDENTIAL_ENV` is unset or blank, the lane mints a fresh
token from Application Default Credentials through
`reauthorize_google.access_token`, which is non-interactive once ADC exists. An
exported token still takes precedence, so CI and secret managers are unaffected.
A `ya29.` token lives about an hour, so one pasted into `.env` is stale before it
is useful; the token is never printed, cached, or written to a run artifact.

## Optional authenticated HTTPS retrieval transport

The local stdio MCP server is the default read-only retrieval transport.
`scripts/retrieval_https.py` is a separately enabled TLS-only option for an
approved remote deployment. It requires an approved-facts SQLite database, a
certificate/private-key pair stored outside the repository, and a dedicated
bearer token. It exposes no write route and defaults to loopback binding.

| Setting | Default | Meaning |
|---|---:|---|
| `RETRIEVAL_HTTPS_ENABLED` | `false` | Explicit live-listener permission. |
| `RETRIEVAL_HTTPS_BEARER_TOKEN` | blank | Secret bearer token; never include it in an MCP configuration, manifest, or output. |
| `RETRIEVAL_HTTPS_CREDENTIAL_ENV` | `RETRIEVAL_HTTPS_BEARER_TOKEN` | Name of the token environment variable. |
| `RETRIEVAL_REMOTE_MCP_ENABLED` | `false` | Explicit permission to start the OAuth-protected remote Streamable HTTP MCP listener. |
| `RETRIEVAL_REMOTE_MCP_CLIENT_SECRET` | blank | RFC 7662 introspection client secret. Supply it only through the environment or an approved secret manager; never write it to a run artifact or connector configuration. |
| `HOST` | `0.0.0.0` | Internal retrieval sidecar bind address. Keep it private to the host or task network boundary. |
| `PORT` | `8080` | Internal retrieval sidecar listen port. |
| `LOG_LEVEL` | `info` | Internal retrieval sidecar uvicorn log level. |
| `RETRIEVAL_DB_URL` | blank | Optional direct path or `sqlite:///` URL for the immutable retrieval snapshot the sidecar serves. |
| `RETRIEVAL_ARTIFACT_ROOT` | blank | Optional directory containing `business_retrieval.sqlite` when the sidecar should discover the snapshot by artifact root instead of a direct path. |

Before binding beyond `127.0.0.1`, obtain explicit client approval for the host,
TLS termination, certificate rotation, token distribution/rotation, firewall,
rate limiting, audit logging, monitoring, tenant isolation, retention, and
incident response. A service startup is not authorization to make the retrieval
database internet-facing.

### Google Cloud enablement after build and processing validation

Do this only after the local build, fixture tests, privacy approval, processor
choice, and client acceptance scope are complete:

1. In the client-authorized Google Cloud project, confirm billing and choose the
   data-residency location (`us` or `eu`) before creating a processor. Enable the
   **Document AI API** (`documentai.googleapis.com`).
2. In **Document AI → Processors**, create the agreed OCR processor in that same
   location. Record its project ID, location, processor ID, and, if required by
   the accepted test, a pinned processor version. Evaluate the processor on the
   client-authorized golden set before relying on table output.
3. Create a dedicated runtime service account. Grant the narrow
   **Document AI API User** role (`roles/documentai.apiUser`) on the project or
   specific processor; do not use Owner or a personal administrator account for
   the operational run. Prefer workload identity or a secret manager. If a
   temporary service-account key is unavoidable, store it outside the repository
   and rotate it after the run.
4. Establish Application Default Credentials in the approved runtime with
   `python scripts/reauthorize_google.py`. The lane then mints its own
   short-lived token per run, so nothing needs to be copied anywhere. Supply the
   variable named by `GOOGLE_DOCUMENT_AI_CREDENTIAL_ENV` explicitly only where
   ADC is unavailable, such as a CI job using a secret manager. Never place a
   token in `.env`, commit it, paste it into a review artifact, or send it in
   chat.
5. Set the non-secret processor settings and explicit enablement flag in `.env`,
   run the adapter into a new output directory, validate its adapter contract,
   then supply its handoff(s) to the conditional table-audit buddy check. Keep
   `GOOGLE_DOCUMENT_AI_ENABLED=false` outside an approved run.

## Canonical PostgreSQL deployment

| Setting | Default | Meaning |
|---|---:|---|
| `CANONICAL_DATABASE_URL` | blank | PostgreSQL connection string read only by the explicit `canonical_deploy.py --execute` command. The generated plan records its environment-variable name, never the value. |

## Optional LLM adjudication lane

`scripts/llm_adjudication.py` is a separate, disabled-by-default, audit-only workflow. It never approves a client record or replaces evidence. It queries an LLM only for an explicit candidate that has a retained page, clear deterministic validation, and an exactly agreeing independent extractor. The initial safe allowlist is nonfinancial, printed party/descriptive fields. Any monetary or address field, handwriting, multi-page/reassembly decision, disagreement, provider failure, or low-confidence response remains client review. It also accepts `adjudicate.py`'s `amendments` envelope to preserve integration findings, but those records lack the explicit candidate contract and are retained as no-provider-call blockers. Every successful model result is an `llm_generated_amendment_proposal` with raw-response provenance and a deterministic continuous-QA sample marker.

| Setting | Default | Meaning |
|---|---:|---|
| `LLM_ADJUDICATION_ENABLED` | `false` | Set `true` only for an approved run; `--enable` is a per-run override. |
| `LLM_ADJUDICATION_REASONING_EFFORT` | `medium` | Recorded reasoning effort; same supported values as the extraction adapter. |
| `LLM_ADJUDICATION_CREDENTIAL_ENV` | `OPENAI_API_KEY` | Uppercase environment variable that names the secret. |
| `LLM_ADJUDICATION_MIN_CONFIDENCE` | `0.99` | Adjustable floor from `0.99` through `1.0`; confidence never overrides a protected category. |
| `LLM_ADJUDICATION_SAMPLING_RATE` | `1.0` | Deterministic continuous-QA rate, greater than zero and at most one. Keep `1.0` until client-authorized calibration supports a lower rate. |
| `LLM_ADJUDICATION_MAX_CANDIDATES` | `500` | Maximum explicit candidates queried in one invocation. |
| `LLM_ADJUDICATION_MAX_PDF_BYTES` | `10000000` | Maximum immutable page-PDF size sent for one candidate. |
| `LLM_ADJUDICATION_TIMEOUT_SECONDS` | `120` | SDK timeout recorded in the handoff. |
| `LLM_ADJUDICATION_MAX_RETRIES` | `2` | SDK retry count recorded in the handoff. |

### Card-level client-review assistant

| Setting | Default | Purpose |
|---|---:|---|
| `CLIENT_REVIEW_LLM_ENABLED` | `false` | Enables the proposal-only card reviewer for an explicit run. |
| `LLM_CLIENT_REVIEW_PROVIDER` | blank | Optional reviewer-specific provider override; blank inherits `LLM_REASONING_PROVIDER`. |
| `LLM_POST_REVIEW_PROVIDER` | blank | Provider for cross-record and full-dataset post-review lanes; blank inherits the client-review/reasoning provider and always selects that provider's `*_REASONING_MODEL`. |
| `CLIENT_REVIEW_LLM_MODEL` | blank | Optional reviewer-specific model override; blank inherits the selected reasoning-lane model. |
| `CLIENT_REVIEW_LLM_REASONING_EFFORT` | `medium` | Bounded reasoning setting recorded in the reviewer output. |
| `CLIENT_REVIEW_LLM_CREDENTIAL_ENV` | blank | Optional credential-variable override; blank resolves to the selected provider's credential setting. |
| `CLIENT_REVIEW_LLM_RETRY_BACKOFF_SECONDS` | `1.0` | Initial retry delay for transient reviewer failures; positive decimal seconds. Jitter applies when no server delay is supplied. |
| `CLIENT_REVIEW_LLM_MAX_BACKOFF_SECONDS` | `120.0` | Maximum reviewer retry delay; keep it at or above the initial delay. `Retry-After` remains a server-directed minimum. |
| `CLIENT_REVIEW_CROSS_RECORD_MAX_WORKERS` | `8` | Batches searched concurrently by the cross-record lane. Output order is independent of this setting, so the retained artifact is identical at any worker count. |
| `CLIENT_REVIEW_EXCEPTION_RESOLUTION_MAX_WORKERS` | `8` | Batches resolved concurrently. Each batch still runs its primary call before its buddy call; only separate batches overlap. Output order is independent of this setting, so the retained artifact is identical at any worker count. |
| `AI_SIMULATED_CLIENT_REVIEW_MAX_WORKERS` | `8` | Cards commented on concurrently. The resume checkpoint is taken under a lock, so a card is recorded complete only together with the comments it produced. Output order is independent of this setting, so the retained artifact is identical at any worker count. |
| `CLIENT_REVIEW_LLM_AGENT_MAX_WORKERS` | `8` | Analysis slices run concurrently. Iterations within a slice stay sequential because each reads the previous one, and slice reductions are merged in slice order. Output order is independent of this setting, so the retained artifact is identical at any worker count. |
| `CLIENT_REVIEW_CONTEXT_LLM_MAX_WORKERS` | `8` | Batches run concurrently in the reference-discovery and iterative relationship lanes. Iterative passes stay sequential because each reads the prior pass. Output order is independent of this setting, so the retained artifact is identical at any worker count. |
| `CLIENT_REVIEW_LLM_MAX_WORKERS` | `8` | Cards reviewed concurrently. Output order is independent of this setting, so the retained artifact is identical at any worker count; only elapsed time changes. Raise it when a queue is large and the provider budget has headroom, and note that one slow card no longer blocks the rest. |
| `CLIENT_REVIEW_LLM_MAX_RETRIES` | `4` | Maximum reviewer request attempts after the initial request; a terminal 429 opens a lane circuit breaker so remaining cards are retained without more API calls. |
| `CLIENT_REVIEW_LLM_MAX_CARDS` | `1000` | Maximum client-review cards per invocation. |
| `CLIENT_REVIEW_LLM_MAX_CONTEXT_BYTES` | `20000` | Maximum serialized size of each supporting artifact included in a card packet; the reviewer fails closed rather than exceeding provider request limits. |
| `CLIENT_REVIEW_LLM_TIMEOUT_SECONDS` | `120.0` | Per-request client-review timeout. |
| `--resume-from REVIEW_OUTPUT.json` | invocation-only | Reuses successful card decisions from an earlier reviewer output and retries only unresolved cards. The hash-bound resume contract includes the queue, context artifacts, provider/model, reasoning and reduction settings, and exact reviewer instructions; changing the protected-final-run flag therefore requires fresh decisions. The new output remains separate and proposal-only. |
| `CLIENT_REVIEW_LLM_AUTO_ACCEPT_PROPOSALS` | `false` | Opt-in thresholded carry-forward for explicitly eligible low-risk proposals; it never clears protected categories or approves production facts. |
| `CLIENT_REVIEW_LLM_AUTO_ACCEPT_THRESHOLD` | `0.99` | Confidence floor, from `0.99` through `1.0`. It gates `client_review_cross_record.py`, which rejects a match as `below_auto_accept_threshold`. The card-review lane validates and records it but does **not** use it: there the gate is the protection taxonomy, an explicit `propose_resolution` decision, and a field-bound proposal, because self-reported confidence is provenance rather than a decision term. |
| `CLIENT_REVIEW_LLM_FINAL_RUN_INCLUDE_PROTECTED_ARITHMETIC_REASSEMBLY` | `false` | Lets final-review cards request source-backed next-step proposals for protected arithmetic/reassembly work. It never auto-accepts, closes, or removes those findings. |
| `CLIENT_REVIEW_LLM_CROSS_RECORD_ENABLED` | `false` | Enables the separate evidence-search phase over supplied normalized records; matches remain proposals and protected categories remain review-only. |
| `CLIENT_REVIEW_LLM_CROSS_RECORD_MAX_CONTEXT_BYTES` | `12000` | Maximum serialized bytes retained from each supporting artifact in a cross-record request. |
| `CLIENT_REVIEW_LLM_CROSS_RECORD_MAX_PACKET_BYTES` | `600000` | Maximum serialized bytes for one cross-record provider request; kept below the configured OpenAI request-token ceiling while using the larger GPT-5.6 Luna context window. |
| `CLIENT_REVIEW_LLM_CROSS_RECORD_MAX_ITEMS_PER_BATCH` | `150` | Maximum unresolved items in one cross-record batch; every global item ID is retained across batches. |
| `CLIENT_REVIEW_LLM_CROSS_RECORD_MAX_EVIDENCE_ARTIFACT_BYTES` | `10000000` | Maximum bytes for each evidence envelope and each required producer, handoff, source PDF, page PDF, or retained text artifact. Every artifact is checked before its first hash, open, or read; preflight fails before provider I/O when any artifact exceeds it. |
| `CLIENT_REVIEW_LLM_CROSS_RECORD_MAX_EVIDENCE_ENTRIES` | `10000` | Maximum total declared `entries[]` across all supplied evidence envelopes. The reducer size-bounds, parses, and validates every outer envelope and totals all declared items before loading a producer; overflow therefore fails before producer indexing or provider I/O. |
| `CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_ENABLED` | `false` | Enables the repeatable post-review sequence: cross-record pass, full-dataset pass, homogeneous review clustering, deterministic grouping/deduplication, and safe presentation output. It never approves facts. |
| `CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_CARRY_FORWARD` | `false` | Opt-in presentation-only carry-forward for explicitly eligible low-risk proposals; protected categories and provider/schema failures remain review-required. |
| `CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_THRESHOLD` | `0.99` | Confidence floor for the narrow carry-forward stage; must be between `0.99` and `1.0`. |
| `CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_OUTPUT_DIR` | `post_review_consolidation` | Default new output directory for post-review lanes; choose a fresh run-scoped path. |
| `CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_OUTPUT_FILE` | `safe_review_consolidation.json` | Final consolidation filename inside the output directory; the command refuses to overwrite it. |
| `CLIENT_REVIEW_LLM_AGENT_ITERATIONS` | `3` | Maximum bounded full-dataset reasoning passes; later passes may test prior hypotheses, but no pass approves production facts. |
| `CLIENT_REVIEW_LLM_AGENT_MAX_CONTEXT_BYTES` | `3500000` | Retained-inventory size guard and upper bound for a provider slice; conservatively below the GPT-5.6 Luna context window after tokenization and request overhead. The complete inventory remains in output while calls use the smaller slice setting. |
| `CLIENT_REVIEW_LLM_AGENT_MAX_SLICE_CONTEXT_BYTES` | `2000000` | Maximum serialized bytes for one full-dataset provider slice, approximately 500k estimated tokens; the complete inventory remains retained in the output. |
| `CLIENT_REVIEW_LLM_AGENT_MAX_EVIDENCE_PER_SLICE` | `5000` | Maximum compact evidence objects in one full-dataset slice; the byte cap remains authoritative. |
| `AI_SIMULATED_CLIENT_REVIEW_ENABLED` | `false` | Enables the separate AI simulated-client comment lane. It remains a proposal-only reviewer aid and cannot be used as client authorization, a control clearance, an applied change, or a canonical/CRM gate. |
| `AI_SIMULATED_CLIENT_REVIEW_PROVIDER` | `openai` | Provider for the simulated-client reviewer. It accepts the shared LLM provider set above; no provider is hard-coded into the runtime. |
| `AI_SIMULATED_CLIENT_REVIEW_MODEL` | `gpt-5.6-luna` | Model recorded in every simulated-comment artifact and raw response. Choose the model through this setting, not a source edit. |
| `AI_SIMULATED_CLIENT_REVIEW_CREDENTIAL_ENV` | blank | Optional credential-variable override; blank resolves the selected provider's normal credential variable. |
| `AI_SIMULATED_CLIENT_REVIEW_REASONING_EFFORT` | `high` | Requested bounded reasoning effort recorded with the run. Provider adapters retain the request even when they expose no equivalent setting. |
| `AI_SIMULATED_CLIENT_REVIEW_TIMEOUT_SECONDS` | `300` | Per-card provider timeout. |
| `AI_SIMULATED_CLIENT_REVIEW_MAX_RETRIES` | `2` | Maximum transient retries per card. Shared retry, throttle, and quota-circuit controls apply. |
| `AI_SIMULATED_CLIENT_REVIEW_RETRY_BACKOFF_SECONDS` | `5.0` | Initial bounded retry delay. |
| `AI_SIMULATED_CLIENT_REVIEW_MAX_BACKOFF_SECONDS` | `180.0` | Maximum retry delay; server retry instructions remain bounded. |
| `AI_SIMULATED_CLIENT_REVIEW_MAX_CARDS` | `0` | Maximum cards per invocation. `0` processes all retained cards; a positive cap retains every deferred card as an explicit exception. |
| `AI_SIMULATED_CLIENT_REVIEW_MAX_CONTEXT_BYTES` | `2000000` | Serialized per-packet ceiling. A card larger than this is **split into as many packets as the budget needs** by the shared byte-adaptive packet utility, each carrying the same card and context; only a single review item too large to fit alone is retained as a `simulated_review_card_too_large` exception. Each packet retains its own `.partNN.json` raw response and is checkpointed, so a resumed run never re-sends one that already answered. On a real 18-page corpus a card's packet runs 47KB–155KB and a single review item's up to 81KB. |
| `AI_SIMULATED_CLIENT_REVIEW_CONTEXT_RESERVE_BYTES` | `16000` | **Floor** for the bytes kept inside the ceiling for the card and its source evidence. The lane measures what the queue's largest single review item actually costs and reserves the greater of the two, so context can never crowd out the payload; context is then clipped to whatever remains. This value alone used to decide it, and a fixed 16000 bytes was less than any real card needed. |
| `AI_SIMULATED_CLIENT_REVIEW_MAX_CONTEXT_ARTIFACT_BYTES` | `10000000` | Maximum bytes for one optional context artifact before hashing/parsing. Oversized context fails before provider I/O. |
| `AI_SIMULATED_CLIENT_REVIEW_COMMENT_LABEL` | `AI client reviewed (simulated; not client authorization)` | Required visible label on every generated comment. The implementation rejects a label that does not explicitly state that it is not client authorization. |
| `CLIENT_REVIEW_EXCEPTION_RESOLUTION_ENABLED` | `false` | Enables the generic proposal-only exception-resolution lane. |
| `CLIENT_REVIEW_EXCEPTION_RESOLUTION_PRIMARY_PROVIDER` | `openai` | Primary provider; it must differ from the buddy provider. |
| `CLIENT_REVIEW_EXCEPTION_RESOLUTION_PRIMARY_MODEL` | `gpt-5.6-luna` | Recorded primary model. |
| `CLIENT_REVIEW_EXCEPTION_RESOLUTION_PRIMARY_CREDENTIAL_ENV` | blank | Optional primary credential-variable override. |
| `CLIENT_REVIEW_EXCEPTION_RESOLUTION_BUDDY_PROVIDER` | `openrouter` | Independent buddy provider. |
| `CLIENT_REVIEW_EXCEPTION_RESOLUTION_BUDDY_MODEL` | `openai/gpt-oss-120b` | Recorded buddy model. |
| `CLIENT_REVIEW_EXCEPTION_RESOLUTION_BUDDY_CREDENTIAL_ENV` | blank | Optional buddy credential-variable override. |
| `CLIENT_REVIEW_EXCEPTION_RESOLUTION_REASONING_EFFORT` | `medium` | Bounded request reasoning effort. |
| `CLIENT_REVIEW_EXCEPTION_RESOLUTION_TIMEOUT_SECONDS` | `180` | Per-request timeout. |
| `CLIENT_REVIEW_EXCEPTION_RESOLUTION_MAX_RETRIES` | `2` | Maximum transient retries. |
| `CLIENT_REVIEW_EXCEPTION_RESOLUTION_RETRY_BACKOFF_SECONDS` | `5.0` | Initial bounded retry delay. |
| `CLIENT_REVIEW_EXCEPTION_RESOLUTION_MAX_BACKOFF_SECONDS` | `180` | Maximum retry delay. |
| `CLIENT_REVIEW_EXCEPTION_RESOLUTION_BATCH_SIZE` | `6` | Maximum exception records per request. |
| `CLIENT_REVIEW_EXCEPTION_RESOLUTION_MAX_BATCHES` | `0` | Maximum adaptive packets per invocation; `0` derives every byte-bounded packet and records any individually oversized record explicitly. A positive value is an explicit cost cap and retains each deferred work item, with its exact source IDs, in the companion exception artifact. |
| `CLIENT_REVIEW_EXCEPTION_RESOLUTION_MAX_CONTEXT_BYTES` | `120000` | Maximum serialized packet bytes; overflow remains explicit. |
| `CLIENT_REVIEW_CONTEXT_LLM_ENABLED` | `false` | Enables the bounded proposal-only reference-discovery lane using preserved client context. |
| `CLIENT_REVIEW_CONTEXT_LLM_PROVIDER` | `google` | Provider for the context reference-discovery lane; Google is the default and must remain independently configured from consensus when used for reasoning. |
| `CLIENT_REVIEW_CONTEXT_LLM_MODEL` | `gemini-2.5-pro` | Model for context reference discovery; recorded in every output. |
| `CLIENT_REVIEW_CONTEXT_LLM_REASONING_EFFORT` | `medium` | Requested bounded reasoning effort for the iterative lane. It is effective for the OpenAI buddy; Vertex Gemini does not expose this OpenAI-compatible control, but both outputs retain the selected metadata. |
| `CLIENT_REVIEW_CONTEXT_LLM_CREDENTIAL_ENV` | blank | Optional credential-variable override for the selected provider. |
| `CLIENT_REVIEW_CONTEXT_LLM_TIMEOUT_SECONDS` | `180` | Per-request timeout for a bounded Gemini review batch. A timeout remains a retained exception after the configured retries. |
| `CLIENT_REVIEW_CONTEXT_LLM_MAX_RETRIES` | `4` | Maximum retries per bounded batch for transient capacity/deadline failures. |
| `CLIENT_REVIEW_CONTEXT_LLM_RETRY_BACKOFF_SECONDS` | `5.0` | Initial retry delay for transient provider failures. |
| `CLIENT_REVIEW_CONTEXT_LLM_MAX_BACKOFF_SECONDS` | `180` | Maximum retry delay; `Retry-After` is honored within this bound. |
| `CLIENT_REVIEW_CONTEXT_LLM_MAX_DOCUMENTS` | `75` | Deterministic stratified pilot-document cap. |
| `CLIENT_REVIEW_CONTEXT_LLM_BATCH_SIZE` | `6` | Initial maximum source records per provider request; adaptive byte sizing may make batches smaller. |
| `CLIENT_REVIEW_CONTEXT_LLM_MAX_BATCHES` | `12` | Positive batch cap used by legacy client-review lanes. It does not control the iterative primary/buddy lane. |
| `CLIENT_REVIEW_CONTEXT_LLM_ITERATIVE_MAX_BATCHES` | `0` | Iterative primary/buddy provider-batch cap; `0` derives every adaptive byte-bounded batch needed to cover the supplied corpus. A positive value is an explicit cost cap and must be paired with retained deferred records. |
| `CLIENT_REVIEW_CONTEXT_LLM_MAX_CONTEXT_BYTES` | `120000` | Maximum serialized request size per batch. Adaptive source batching reserves 40,000 bytes for instructions, carried proposals, and buddy decisions; an oversized source record remains an explicit exception. |
| `CLIENT_REVIEW_CONTEXT_LLM_BUDDY_PROVIDER` | `openai` | Independent buddy provider for the iterative client-context lane. It must differ from `CLIENT_REVIEW_CONTEXT_LLM_PROVIDER`; matching providers fail before any call. |
| `CLIENT_REVIEW_CONTEXT_LLM_BUDDY_MODEL` | `gpt-5.6-sol` | OpenAI buddy model; recorded in every output. It supports the configured `medium` reasoning effort and structured Responses output. |
| `CLIENT_REVIEW_CONTEXT_LLM_BUDDY_CREDENTIAL_ENV` | blank | Optional buddy credential-variable override. Blank resolves the configured buddy provider's standard credential setting. |
| `CLIENT_REVIEW_CONTEXT_LLM_BUDDY_TIMEOUT_SECONDS` | `300` | Timeout for each bounded Sol buddy request. |
| `CLIENT_REVIEW_CONTEXT_LLM_MAX_ITERATIONS` | `3` | Maximum iterative primary/buddy passes (hard capped at 5); all passes remain proposals. |
| `CLIENT_REVIEW_CONTEXT_LLM_CONVERGENCE_MIN_NEW_CANDIDATES` | `1` | Stop iterative refinement when a completed pass adds fewer than this many new, buddy-confirmed, source-backed relationships; must be at least 1. |
| `CLIENT_REVIEW_CONTEXT_LLM_SELF_CHECK_ROUNDS` | `0` | Deprecated compatibility setting for the older discovery CLI; same-provider checks are not independent consensus. |
| `CLIENT_REVIEW_CONTEXT_LLM_OPENAI_MODEL` | `gpt-5.6-sol` | Deprecated compatibility setting for the older adjudication CLI. Use `CLIENT_REVIEW_CONTEXT_LLM_BUDDY_MODEL` for the iterative lane. |
| `CLIENT_REVIEW_CONTEXT_LLM_OPENAI_TIMEOUT_SECONDS` | `300` | Deprecated compatibility timeout for the older adjudication CLI. |
| `CLIENT_REVIEW_CROSS_PACKET_PRIMARY_PROVIDER` | `openai` | Provider for cross-packet proposals and independent verification. It must differ from the buddy provider. |
| `CLIENT_REVIEW_CROSS_PACKET_PRIMARY_MODEL` | `gpt-5.6-luna` | Model for the configured cross-packet primary provider; retained in every output. |
| `CLIENT_REVIEW_CROSS_PACKET_PRIMARY_CREDENTIAL_ENV` | blank | Optional primary credential-variable override. Blank resolves the configured primary provider's standard credential setting. |
| `CLIENT_REVIEW_CROSS_PACKET_PRIMARY_TIMEOUT_SECONDS` | `300` | Bounded timeout for the configured primary provider. |
| `CLIENT_REVIEW_CROSS_PACKET_BUDDY_PROVIDER` | `google` | Independent buddy selected from the shared LLM provider set above. It classifies every cross-packet primary proposal and verification and must differ from the primary provider. |
| `CLIENT_REVIEW_CROSS_PACKET_BUDDY_MODEL` | `gemini-2.5-pro` | Model for the configured cross-packet buddy provider; set it explicitly when selecting a non-default provider and retain it in every output. |
| `CLIENT_REVIEW_CROSS_PACKET_BUDDY_CREDENTIAL_ENV` | blank | Optional buddy credential-variable override. Blank resolves the configured buddy provider's standard credential setting. |
| `CLIENT_REVIEW_CROSS_PACKET_BUDDY_TIMEOUT_SECONDS` | `180` | Bounded timeout for the configured buddy provider. |
| `CLIENT_REVIEW_CROSS_PACKET_REASONING_EFFORT` | `medium` | Requested reasoning effort for both configured roles; provider adapters record it even when a provider exposes no equivalent control. |
| `CLIENT_REVIEW_CROSS_PACKET_MAX_RETRIES` | `4` | Maximum retries after the initial cross-packet provider attempt. |
| `CLIENT_REVIEW_CROSS_PACKET_RETRY_BACKOFF_SECONDS` | `5.0` | Initial cross-packet retry delay in seconds. |
| `CLIENT_REVIEW_CROSS_PACKET_MAX_BACKOFF_SECONDS` | `180` | Maximum cross-packet retry delay in seconds; `Retry-After` remains a server-directed minimum. |
| `CLIENT_REVIEW_CROSS_PACKET_MAX_DOCUMENTS` | `6` | Maximum documents in one graph-guided cross-packet neighborhood, including its discovery or verification target. The smaller default reduces provider pressure and limits malformed structured responses. |
| `CLIENT_REVIEW_CROSS_PACKET_MAX_NEIGHBORHOODS` | `500` | Maximum unresolved-document discovery neighborhoods examined in one no-clobber cross-packet refinement run. Deferred items are explicit exceptions. |
| `CLIENT_REVIEW_CROSS_PACKET_MAX_VERIFICATION_NEIGHBORHOODS` | `1000` | Maximum already-resolved-document verification neighborhoods examined in one no-clobber run. This covers the current full retry set; deferred or identifier-less items remain explicit exceptions. |
| `CLIENT_REVIEW_CROSS_PACKET_MAX_CONTEXT_BYTES` | `80000` | Serialized request-size ceiling for one cross-packet neighborhood. An oversized neighborhood is retained as an explicit exception. |
| `CLIENT_REVIEW_CROSS_PACKET_MAX_ITERATIONS` | `2` | Maximum convergence-controlled cross-packet passes (hard capped at 5). The second and later passes consider only newly exposed source-visible unresolved neighborhoods. |
| `CLIENT_REVIEW_CROSS_PACKET_CONVERGENCE_MIN_NEW_CANDIDATES` | `1` | Stop cross-packet refinement once a completed pass adds fewer than this many new, buddy-confirmed, source-backed relationships; must be at least 1. |

The reviewer never approves production facts. It may only expose explicitly
covered low-risk dependencies in a client-facing view; all source review items,
protected findings, provider failures, and raw responses remain retained.

Safe consolidation is fail-closed: it refuses to consume a missing, partial,
failed, provider-exception-bearing, or non-retention-certified cross-record or
full-dataset artifact. It also checks the final output path before starting any
provider lane and refuses to overwrite an existing lane or final artifact; use a
new run/output directory for every retry.

For an advanced independent final opinion, pass `--final-provider PROVIDER`
and `--final-model MODEL` to `scripts/client_review_llm.py`. `PROVIDER` accepts
the shared LLM provider set above; executable `--help` and the parser-derived
CLI catalogue are the exact choice contract. These are
invocation-only overrides, not a second settings bundle: enablement, context
limits, reasoning effort, retry policy, and auto-accept policy inherit the
current client-review configuration. The selected provider and model are
recorded in the output summary.

## Google Maps Address Validation

| Setting | Default | Meaning |
|---|---:|---|
| `GOOGLE_MAPS_API_KEY` | blank | Required secret only when validation is enabled. |
| `GOOGLE_MAPS_API_KEY_ENV` | `GOOGLE_MAPS_API_KEY` | Name of the environment variable read for the key. |
| `GOOGLE_ADDRESS_VALIDATION_ENABLED` | `false` | Set `true` only after client privacy approval; `false` keeps local-only parsing. |
| `GOOGLE_MAX_REQUESTS` | `5000` | Per-run ceiling when enabled; must be from 1 through 5,000, and every provider attempt counts. |
| `GOOGLE_TIMEOUT_SECONDS` | `15` | HTTP request timeout; must be from 1 through 120. |
| `GOOGLE_USPS_CASS_ENABLED` | `true` | Requests USPS CASS data for US/PR addresses only. |

The provider response is derived evidence. The pipeline never overwrites a
retained source address; uncertain, incomplete, failed, capped, or conflicting
results become client-review work.

Google Address Validation already supplies geocode evidence, so this pipeline does
not call the separate Geocoding API. Coverage varies by country. A provider
response categorized as `unsupported_region`, `permission_denied`,
`request_rejected`, or `failed` is explicit client-review work, never a silent
fallback. The 5,000 setting is a fail-closed run ceiling, not a billing-account
usage monitor: Google applies its free-usage threshold and billing across the
linked billing account. Set Cloud quotas and budgets separately.

## Operator use

```bash
cp .env.example .env
# Edit .env locally. Do not commit it.
python scripts/openai_adapter.py intake/ingestion_manifest.json ...
python scripts/google_document_ai_adapter.py intake/ingestion_manifest.json --enable ...
python scripts/address_normalize.py validated.json ...
```

Use `--help` for explicit per-run overrides. For booleans, the scripts accept
paired overrides such as `--google-address-validation` and
`--no-google-address-validation`.


## Semantic schema discovery and Google Places candidates

`scripts/schema_discovery.py` is a disabled-by-default proposal lane for new
source templates. It preserves source labels and sample evidence, proposes a
controlled canonical field and semantic type, and emits separate dealer, brand,
relationship, location-candidate, schema-change, and standard client-review
artifacts. Its `registry-update` command creates a new versioned mapping-registry
snapshot only from explicit client `approve` decisions; it never changes source
records, the canonical model, or an existing rule in place.

| Setting | Default | Meaning |
|---|---:|---|
| `SCHEMA_DISCOVERY_ENABLED` | `false` | Enables strict-JSON LLM proposals only for the explicit run. |
| `SCHEMA_DISCOVERY_PROVIDER` | `google` | Configurable primary selected from the shared LLM provider set above for source-template schema proposals; it must remain independent from the buddy when enabled. |
| `SCHEMA_DISCOVERY_MODEL` | `gemini-2.5-pro` | Model for the configured schema-discovery primary provider; set explicitly when selecting a non-default route. |
| `SCHEMA_DISCOVERY_REASONING_EFFORT` | `medium` | Provider reasoning control recorded in the non-secret handoff; accepted values follow the selected reasoning provider. |
| `SCHEMA_DISCOVERY_CREDENTIAL_ENV` | blank | Optional primary credential-variable override; blank resolves the selected provider's credential setting and Google uses ADC. |
| `SCHEMA_DISCOVERY_MIN_CONFIDENCE` | `0.99` | Flags lower-confidence suggestions; a high score still requires initial approval. |
| `SCHEMA_DISCOVERY_TIMEOUT_SECONDS` | `120` | Per-request schema-discovery timeout in seconds. |
| `SCHEMA_DISCOVERY_MAX_RETRIES` | `2` | Non-negative transient retry count. |
| `SCHEMA_DISCOVERY_BUDDY_ENABLED` | `true` | Requires an independent configured buddy classification for every primary mapping proposal. Disable only for an explicitly documented compatibility replay. |
| `SCHEMA_DISCOVERY_BUDDY_PROVIDER` | `openai` | Configurable independent buddy selected from the shared LLM provider set above; it must differ from `SCHEMA_DISCOVERY_PROVIDER`. |
| `SCHEMA_DISCOVERY_BUDDY_MODEL` | `gpt-5.6-luna` | Model for the configured schema-discovery buddy provider. |
| `SCHEMA_DISCOVERY_BUDDY_CREDENTIAL_ENV` | blank | Name of the configured buddy provider's credential variable; blank/provider default resolution is supported. |
| `SCHEMA_DISCOVERY_BUDDY_TIMEOUT_SECONDS` | `120` | Per-request buddy timeout. |
| `SCHEMA_DISCOVERY_BUDDY_MAX_RETRIES` | `2` | Non-negative transient buddy retry count. |
| `GOOGLE_PLACES_API_KEY` | blank | Separate secret restricted to Places API (New); required only when Places is enabled. |
| `GOOGLE_PLACES_ENABLED` | `false` | Allows candidate location lookup for supplied dealer/brand names only. |
| `GOOGLE_PLACES_API_KEY_ENV` | `GOOGLE_PLACES_API_KEY` | Name of the separate Places key variable. |
| `GOOGLE_PLACES_MAX_REQUESTS` | `100` | Positive per-run candidate lookup cap; every attempted provider request counts. |
| `GOOGLE_PLACES_TIMEOUT_SECONDS` | `15` | Per-request Places timeout in seconds. |
| `GOOGLE_PLACES_AUTH` | `key` | Credential `party_locations.py` sends: `key` uses the restricted key named by `GOOGLE_PLACES_API_KEY_ENV`; `adc` uses Application Default Credentials, billed to their quota project. Neither is written to an artifact. |

Use Google Address Validation for a source address already present on a page, using `GOOGLE_MAPS_API_KEY`. Use Google Places only to find possible dealer/brand locations, using the separately restricted `GOOGLE_PLACES_API_KEY` and `GOOGLE_PLACES_API_KEY_ENV=GOOGLE_PLACES_API_KEY`, or Application Default Credentials with `GOOGLE_PLACES_AUTH=adc`; `party_locations.py` applies the same settings to the resolved party master. A Places result
is not proof of a legal entity, current trading name, relationship, or preferred
site; retain every candidate and route it to client review.


## Client review lane

Every control in this pipeline can block, and each block used to reach the client
differently -- raw exception rows from one, a packaged workbook from another,
nothing at all until delivery from most. `scripts/client_review_lane.py` is the
single path: it consolidates whatever a blocked control produced into the
exhaustive queue, groups repeated causes, reduces those to the smallest set of
questions that settles them, attaches the source page behind each question, and
writes a workbook to answer in and an instruction PDF to answer from.

It calls no provider and spends nothing, so unlike the provider-calling lanes it
is enabled by default. What bounds it is the per-control threshold below: the
lane builds a pack for a control only when that control reaches its floor, which
is what lets it be run after every stage without producing a pack nobody needed.

A threshold is never a filter. Items below a floor stay in the exhaustive queue
and in the run's exceptions exactly as before; the floor decides whether to ask
the client *now*, not whether the work exists. Setting one to `0` means that
control never triggers a pack on its own.

Two limits worth understanding before changing them. `MAX_QUESTIONS` caps how
much is put to a client at once; questions beyond it are retained as deferred
rather than dropped, so a shorter pack is a smaller ask and never a smaller
queue. `MAX_IMAGE_BYTES` bounds each rendered example page, and an oversized
render becomes an explicit exception rather than a downscaled guess.

Answering a question is kept separate from clearing what it covers. Findings
protected under rule 7 -- arithmetic, provider, handwriting, identity and the
rest -- are still asked about, because the client's answer is exactly what
resolves them, but the pack states plainly that the answer is applied and each
affected item then re-checked individually rather than closed in bulk.

| Setting | Default | Meaning |
|---|---:|---|
| `CLIENT_REVIEW_LANE_ENABLED` | `true` | Whether the lane may build packs at all. |
| `CLIENT_REVIEW_LANE_MAX_QUESTIONS` | `25` | Largest number of questions put to a client at once; the rest are retained as deferred. |
| `CLIENT_REVIEW_LANE_RENDER_DPI` | `150` | Resolution each example page is rendered at before it is embedded in the PDF. |
| `CLIENT_REVIEW_LANE_MAX_IMAGE_BYTES` | `4000000` | Per-page render ceiling; an oversized render is an exception, never a downscaled guess. |
| `CLIENT_REVIEW_LANE_EXTRACTION_THRESHOLD` | `1` | Blocking extraction findings required before the lane builds a pack for that control. |
| `CLIENT_REVIEW_LANE_CONSENSUS_THRESHOLD` | `1` | Blocking consensus findings required before the lane builds a pack for that control. |
| `CLIENT_REVIEW_LANE_ARITHMETIC_THRESHOLD` | `1` | Blocking arithmetic findings required before the lane builds a pack for that control. |
| `CLIENT_REVIEW_LANE_VALIDATION_THRESHOLD` | `1` | Blocking validation findings required before the lane builds a pack for that control. |
| `CLIENT_REVIEW_LANE_ADJUDICATION_THRESHOLD` | `1` | Blocking adjudication findings required before the lane builds a pack for that control. |
| `CLIENT_REVIEW_LANE_REASSEMBLY_THRESHOLD` | `1` | Blocking reassembly findings required before the lane builds a pack for that control. |
| `CLIENT_REVIEW_LANE_TABLES_THRESHOLD` | `1` | Blocking tables findings required before the lane builds a pack for that control. |
| `CLIENT_REVIEW_LANE_HANDWRITING_THRESHOLD` | `1` | Blocking handwriting findings required before the lane builds a pack for that control. |
| `CLIENT_REVIEW_LANE_IDENTITY_THRESHOLD` | `1` | Blocking identity findings required before the lane builds a pack for that control. |
| `CLIENT_REVIEW_LANE_ATTRIBUTION_THRESHOLD` | `1` | Blocking attribution findings required before the lane builds a pack for that control. |
| `CLIENT_REVIEW_LANE_COMPLETENESS_THRESHOLD` | `1` | Blocking completeness findings required before the lane builds a pack for that control. |
| `CLIENT_REVIEW_LANE_SAMPLING_THRESHOLD` | `1` | Blocking sampling findings required before the lane builds a pack for that control. |
| `CLIENT_REVIEW_LANE_SCHEMA_THRESHOLD` | `1` | Blocking schema findings required before the lane builds a pack for that control. |
| `CLIENT_REVIEW_LANE_SLOT_EQUIVALENCE_THRESHOLD` | `1` | Blocking slot equivalence findings required before the lane builds a pack for that control. |
| `CLIENT_REVIEW_LANE_ALLOCATION_THRESHOLD` | `1` | Blocking allocation findings required before the lane builds a pack for that control. |
| `CLIENT_REVIEW_LANE_RELATIONSHIPS_THRESHOLD` | `1` | Blocking relationships findings required before the lane builds a pack for that control. |
| `CLIENT_REVIEW_LANE_DELIVERY_THRESHOLD` | `1` | Blocking delivery findings required before the lane builds a pack for that control. |

## Schema-slot equivalence

A controlled vocabulary broad enough for unfamiliar client schemas offers more
than one plausible home for the same fact, so two independent engines can read
one printed column and file it under two different fields. Consensus is right to
refuse that -- it compares one slot at a time and cannot know the slots mean the
same thing -- but the result is a disagreement about vocabulary reported as a
disagreement about a fact.

`scripts/schema_discovery.py slot-equivalence` reads a completed consensus run,
finds values that reached two slots of the same container, and proposes which
pairs denote one business fact. Every output is a proposal: only a client-approved
registry rule may collapse two slots, and `registry-update` refuses to write a
rule from a proposal no independent verifier confirmed.

The evidence comes in two strengths and the artifact always names which applies.
`cross_engine_placement` is two engines filing one value under two names.
`intra_engine_duplicate` is one engine writing the value into both slots, which
is an engine declining to choose rather than two engines agreeing; a pair
supported only by that class is retained but is never approval-eligible.

| Setting | Default | Meaning |
|---|---:|---|
| `SLOT_EQUIVALENCE_ENABLED` | `false` | Enables strict-JSON LLM slot-equivalence proposals only for the explicit run. |
| `SLOT_EQUIVALENCE_PROVIDER` | `google` | Configurable primary selected from the shared LLM provider set above; it must remain independent from the buddy when enabled. |
| `SLOT_EQUIVALENCE_MODEL` | `gemini-2.5-pro` | Model for the configured slot-equivalence primary provider. |
| `SLOT_EQUIVALENCE_REASONING_EFFORT` | `medium` | Provider reasoning control recorded in the non-secret handoff. |
| `SLOT_EQUIVALENCE_CREDENTIAL_ENV` | blank | Name of the primary provider's credential variable; blank uses provider default resolution. |
| `SLOT_EQUIVALENCE_MIN_CONFIDENCE` | `0.99` | Minimum proposal confidence retained as approval-eligible; client approval is still required. |
| `SLOT_EQUIVALENCE_MIN_OBSERVATIONS` | `2` | Shared-value observations a slot pair needs before it is proposed at all. |
| `SLOT_EQUIVALENCE_MIN_DISTINCT_VALUES` | `2` | Distinct shared values a pair needs, which separates a real equivalence from one repeated coincidence. |
| `SLOT_EQUIVALENCE_TIMEOUT_SECONDS` | `120` | Per-request primary timeout. |
| `SLOT_EQUIVALENCE_MAX_RETRIES` | `2` | Non-negative transient primary retry count. |
| `SLOT_EQUIVALENCE_BUDDY_ENABLED` | `true` | Requires an independent verifier before any proposal can become approval-eligible. |
| `SLOT_EQUIVALENCE_BUDDY_PROVIDER` | `openai` | Configurable buddy provider; it must resolve to a different model vendor than the primary. |
| `SLOT_EQUIVALENCE_BUDDY_MODEL` | `gpt-5.6-luna` | Model for the configured buddy provider. |
| `SLOT_EQUIVALENCE_BUDDY_CREDENTIAL_ENV` | blank | Name of the buddy provider's credential variable. |
| `SLOT_EQUIVALENCE_BUDDY_TIMEOUT_SECONDS` | `120` | Per-request buddy timeout. |
| `SLOT_EQUIVALENCE_BUDDY_MAX_RETRIES` | `2` | Non-negative transient buddy retry count. |


## Allocation policy discovery

`scripts/allocation_policy.py` is a disabled-by-default, proposal-only LLM lane
for unfamiliar commission, compensation, or sales-credit report layouts. Its
policy values are deliberately **not** global environment variables: thresholds,
sales-credit permission, dimensions, and effective dates belong to the
client-approved, versioned allocation-policy registry. The root `.env` contains
only transport/model controls.

| Setting | Default | Meaning |
|---|---:|---|
| `ALLOCATION_POLICY_ENABLED` | `false` | Enables strict-JSON policy proposals only for an explicit discovery run. |
| `ALLOCATION_POLICY_REASONING_EFFORT` | `medium` | Provider reasoning control recorded in the non-secret handoff; accepted values follow the selected reasoning provider. |
| `ALLOCATION_POLICY_CREDENTIAL_ENV` | `OPENAI_API_KEY` | Name of the secret environment variable. |
| `ALLOCATION_POLICY_TIMEOUT_SECONDS` | `120` | Per-request allocation-discovery timeout in seconds. |
| `ALLOCATION_POLICY_MAX_RETRIES` | `2` | Non-negative transient retry count. |

Use the client decision return file and `registry-update` command described in
[`allocation-policy.md`](allocation-policy.md) to change policy variables. This
preserves historical rates and scope rather than replacing them in `.env`.

## Model resolution is per lane

A lane resolves its model from the **lane-specific** setting, not the provider
default. `ANTHROPIC_MODEL` does not govern a consensus lane; `ANTHROPIC_CONSENSUS_MODEL`
does, and the reasoning lanes read `ANTHROPIC_REASONING_MODEL`. The same split
applies to every provider.

Changing the provider-level setting and expecting a lane to follow is a silent
no-op: the lane keeps running the model its own setting names, and the run records
that model faithfully while the operator believes something else ran.

**The authority is the retained raw response**, which records the model that
actually served the request. Verify there rather than in the configuration file
before drawing any conclusion about a lane's behaviour or its cost.

## A batch cap of `0` means unlimited

For the adaptive-packet lanes, `0` derives every byte-bounded packet needed to
cover the supplied corpus. A **positive** value is an explicit cost cap and
retains each deferred work item, with its source IDs, in the companion exception
artifact.

A zero is therefore full coverage, not a disabled lane. Setting it to a positive
number to "enable" the lane silently reduces what it covers.
