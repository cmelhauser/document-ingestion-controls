# Setting up a run

## Before the client sends anything

Read [`docs/CLIENT_USER_GUIDE.md`](../../../docs/CLIENT_USER_GUIDE.md) first and
send the client its file-preparation section. Documents that arrive already
named and grouped save more review time than any pipeline setting.

Establish these five answers before touching a command. They are Phase 0 exit
criteria, and a run started without them produces artifacts nobody can sign off:

1. Corpus size, date range, document families, scan quality, and source owner.
2. Whether every monetary amount must tie to an ACK, job, order, project, or
   claim — and whether a reference export exists.
3. Whether matching GL and payment/remittance exports exist. **If they do not,
   completeness cannot pass.** Say so now, not at the end.
4. The business questions and the definition of done.
5. Privacy, retention, provider, region, cost cap, reviewer, and target-system
   decisions.

Agree the thresholds now too, because later phases are built on them: materiality
(sets the MUS certainty stratum), tolerable error rate per field class (sets
sample sizes), GL variance tolerance (sets the Phase 4 gate), and the
handwritten-amendment review threshold (recommend $0 — all of them).

## Where files go

```
SOURCE_ROOT/            never written to; the immutable originals
  originals/            exactly what the client sent, unrenamed
  reference/            ACK/job export, GL export, payment export
RUN_ROOT/run_YYYYMMDDTHHMMSSZ/
  pages/                one-page masters and image variants
  providers/            extraction handoffs and raw responses
  tables/               source-native table comprehension artifacts
  htr/                  handwriting renders, readings, raw responses
  controls/             consensus, arithmetic, validation, business controls
  graph/                evidence-graph overlays
  relationships/        iterative and cross-packet proposals
  review/               exceptions, reduction proposals, and the final queue
  client/               context, the issued workbook, and the returned copy
  canonical/            approved export, load plan, staging
  delivery/             the client folder, built last
  runtime/              run-local cache and request-throttle state
  retries/              new append-only recovery overlays
  logs/                 command ledger, monitor, sentinel, and usage reports
```

Two rules about this layout carry weight. `SOURCE_ROOT` is never written to by
any command — if something wants to write there, stop and find out why. And
`RUN_ROOT/run_*` is created fresh for every run and every retry; the commands
refuse to reuse one.

Create the root before writing any artifact, then run every writing command
through the contained workspace wrapper. The guides use `RUN/...` as a path
placeholder; the wrapper translates that prefix to its workspace root:

```bash
python scripts/run_workspace.py init RUN_ROOT/run_YYYYMMDDTHHMMSSZ
python scripts/run_workspace.py run RUN_ROOT/run_YYYYMMDDTHHMMSSZ -- \
  python /ABSOLUTE/REPOSITORY/PATH/scripts/COMMAND.py ... --out RUN/controls/result.json
python scripts/run_workspace.py audit RUN_ROOT/run_YYYYMMDDTHHMMSSZ
```

It keeps generated caches, throttle timestamps, logs, raw responses, and retry
overlays below `RUN`, while original client inputs and external credentials stay
outside as read-only security boundaries.

## Optional photographed-source intake

If sources arrive through the schema-guided MCP/API journal, first follow
[`record-intake`](../../record-intake/SKILL.md) and the complete
[`visual intake contract`](../../../references/visual-ingestion.md). This
requires a separately authorized source/model flow and an already accepted
approved-fact snapshot for the current combined server; it is not a shortcut
around Phase 6 activation of that snapshot.

The private journal stays separate from `SOURCE_ROOT` and the run. A trusted
operator uses `visual_ingestion_export.py export` through a fresh run wrapper
to snapshot one exact tenant/owner/session into `RUN/source-package`, then
`verify` pins the separately retained receipt hash. Keep every original upload,
proposal version, rejection, and page mapping. Only a verified
`source_prepared_review_required` package with a non-null `source_pdf` proceeds
to `scan_profile.py`, then `ingest_pages.py`; keep its session-specific PDF name
unchanged. A blocked or partial export cannot advance even if its bytes verify.

Supply the package's `exceptions.json` alongside every later review artifact.
The assistant's readings remain review context, not independent extraction
evidence. Normal classification, extraction, business controls and authorization
still run. There is no new `.env` switch, standalone intake deployment, bundled
capture client, or automatic proposal-to-canonical promotion.

## Settings

Copy `.env.example` to `.env` and set only what the engagement needs. Every
setting is documented in
[`references/runtime-configuration.md`](../../../references/runtime-configuration.md);
that file is authoritative and this list is only the short path.

The ones that most often need a deliberate decision:

| Setting | Decide because |
|---|---|
| `LLM_CONSENSUS_PRI_PROVIDER` / `LLM_CONSENSUS_SEC_PROVIDER` | They must be genuinely independent. A router serving another vendor's model counts as that vendor and will be refused at consensus time. |
| `COMPLETENESS_DATE_ORDER` | `05/03/2023` is May in the US and March elsewhere. Set it from the client's export convention once, at the start. |
| `ARITHMETIC_TOLERANCE` | The per-check currency tolerance. Raising it to make documents pass is the failure this pipeline exists to prevent. |
| `*_RATE_LIMIT_TOKENS_PER_MINUTE` | With `LLM_RATE_LIMIT_SAFETY_RATIO`, this sets the real per-request ceiling. Raising `*_MAX_REQUEST_TOKENS` alone does nothing. |
| `LLM_CACHE_DIR` | A cache hit replays a retained response instead of calling the provider. Use a new run-scoped cache for a genuinely fresh reading; preserve prior cached/raw evidence. |
| `LLM_FAIL_FAST_ON_PROVIDER_QUOTA` | When enabled, a recognized quota response opens a process-local circuit and the remaining work is retained as provider exceptions for a separate retry, instead of being called repeatedly. |
| `*_ENABLED` for each optional lane | Every provider-calling lane is disabled by default. Enabling one is a spending decision. |

The `GOOGLE_VERTEX_AI_MODEL_MAX_*` settings record what the served model
actually accepts. The operator caps (`GOOGLE_VERTEX_AI_MAX_PAGES`,
`GOOGLE_VERTEX_AI_MAX_PDF_BYTES`) are checked against them before work begins, so
a cap set above the model's own limit is refused up front rather than surfacing
as a provider error partway through a paid run.

Keep credentials out of the repository. Before any Google-backed work:

```bash
python scripts/reauthorize_google.py --project PROJECT
```

That establishes Application Default Credentials and enables the APIs the run
needs. **There is no token setting in `.env`** — Document AI and handwriting OCR
mint a fresh short-lived token from ADC at run time, so nothing is copied or
pasted anywhere. A `ya29.` token lives about an hour, which is why a pasted one
is stale before it is useful.

Supply the variable named by `GOOGLE_DOCUMENT_AI_CREDENTIAL_ENV` or
`GOOGLE_HANDWRITING_OCR_CREDENTIAL_ENV` explicitly only where ADC is not
available, such as a CI job backed by a secret manager; an exported value still
takes precedence. `--token-env-out` still writes a `0600` file for that case.
Keep any service-account credential **outside** the repository, and use separate
restricted credentials for Vertex ADC, Document AI, Address Validation, and
Places.

## Turning the optional lanes on

Every provider-calling lane is disabled by default because enabling one spends
money and crosses an external boundary. For each applicable lane selected for a
full run, enable its settings below only after source/provider authorization and
its prerequisites are recorded. This table is not blanket transmission
authority; retain an explicit blocker if a selected lane cannot run.

| Setting | Lane |
|---|---|
| `GOOGLE_DOCUMENT_AI_ENABLED` | Independent OCR/table corroboration |
| `GOOGLE_HANDWRITING_OCR_ENABLED` | Cloud Vision handwriting OCR |
| `GOOGLE_ADDRESS_VALIDATION_ENABLED` / `GOOGLE_PLACES_ENABLED` | Address evidence and place candidates |
| `SCHEMA_DISCOVERY_ENABLED` / `SCHEMA_DISCOVERY_BUDDY_ENABLED` | Semantic mapping proposals |
| `ALLOCATION_POLICY_ENABLED` | Commission and sales-credit rule discovery |
| `LLM_ADJUDICATION_ENABLED` | Audit-only amendment proposals |
| `SLOT_EQUIVALENCE_ENABLED` / `SLOT_EQUIVALENCE_BUDDY_ENABLED` | Whether two schema slots hold one fact |
| `CLIENT_REVIEW_LLM_ENABLED` / `..._CROSS_RECORD_ENABLED` | Card review and cross-record search |
| `CLIENT_REVIEW_GROUPING_ENABLED` / `CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_ENABLED` | Root-cause grouping and the consolidation orchestrator |
| `CLIENT_REVIEW_CONTEXT_LLM_ENABLED` | Reference discovery and iterative relationship proposals |
| `CLIENT_REVIEW_EXCEPTION_RESOLUTION_ENABLED` | Exception-resolution proposals |
| `AI_SIMULATED_CLIENT_REVIEW_ENABLED` | Simulated client comments (never authorization) |

`CLIENT_REVIEW_LANE_ENABLED` and `ANALYTICS_ENABLED` are already `true`: neither
calls a provider.

Enabling a lane is not the same as feeding it. Two lanes have an input nothing
else supplies, and both reported success on nothing when it was missing:

- The **mapping lanes** (`schema_discovery.py discover`,
  `allocation_policy.py discover`) and **template drift** all read the
  source-template observation artifact. Build it with
  `template_observations.py` from the completed consensus run.
- The **relationship lanes** read `pilot_records` out of the client context.
  Pass `--records` to `client_input_comments.py` (or
  `client_review_context.py`) or they have nothing to reason over. The table
  lane takes the same artifact but uses only its comments, so one
  records-bearing context serves both.

Four stay **off** unless you decide otherwise, and each is a judgement rather
than a capability:

- `CLIENT_REVIEW_LLM_AUTO_ACCEPT_PROPOSALS` — lets a model proposal be marked
  accepted. Protected findings are never eligible, but this is the setting that
  decides how conservative the review output is.
- `CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_CARRY_FORWARD` — carries eligible
  proposals forward instead of leaving them for a reviewer.
- `COMPLETENESS_BLOCK_ON_UNANALYZED_POPULATION` — gates on a population too
  small to infer from, which can block a small corpus permanently.
- `RETRIEVAL_HTTPS_ENABLED` — opens a network listener; not part of a document
  run.

Every primary/buddy pair must resolve to **two different model vendors**. A
router serving `openai/...` is OpenAI, and the lane refuses before any call.

## Confirm the setup before spending money

### Lock the authorization and lane scope

Before the first external provider call, create the non-secret operational note
`RUN/logs/run_authorization.md`. Record the run identifier, source-set
identifiers/hashes, time, authorized provider families, and whether the operator
has authorized every in-scope source item to be transmitted. Do not record a
credential or raw client content.

A blanket full-run authorization means every in-scope item is sent to each
selected provider lane; do not silently reduce page/document coverage. It is
still bounded by the named run and configured provider families, and it does
not authorize auto-acceptance, carry-forward, amendments, gate clearance,
canonical deployment, CRM execution, or delivery. Record the selected lane list
with the note so `run_lane_coverage.py --require` can verify it at closure.

```bash
python scripts/run_sentinel.py preflight
python scripts/operations.py manifest RUN/pages/*.json --config-from-env --out RUN/manifest.json
```

`preflight` resolves every lane you enabled to its provider and credential
variable and refuses if one has nothing behind it. An unset Document AI or
handwriting token is valid when Application Default Credentials can mint it;
the sentinel verifies that local ADC path and immediately discards the token.
It still distinguishes unavailable ADC from a missing long-lived key. Run it
before every run: a lane whose credential is absent fails on its first page, and
by then you have paid for the lanes that ran before it.

Read the manifest. It records the resolved provider, model, and settings for the
run — with credential *variable names* rather than values. If the consensus lanes
resolve to the same model vendor, fix that now: consensus will refuse the
handoffs later, after both lanes have been paid for.

The other operations subcommands you will want:

```bash
python scripts/operations.py stage RUN/state.json extract --manifest-hash HASH
python scripts/operations.py adapter RUN/providers/primary_handoff.json --type ocr \
  --credential-env OPENAI_API_KEY --out RUN/contracts/extraction.json
python scripts/operations.py privacy RUN/controls/*.json --out RUN/privacy_inventory.json
python scripts/operations.py review-export RUN/review/final_queue.json \
  --csv RUN/review/queue.csv --html RUN/review/queue.html --xlsx RUN/review/queue.xlsx
```

After the run, confirm the lanes you enabled actually produced something:

```bash
python scripts/run_lane_coverage.py RUN --out RUN/lane_coverage.json
```

Enabling a lane and running it are different acts. A full trial reached CRM
staging with fourteen lane families never invoked, because nothing measured it.

`stage` binds a stage transition to the manifest hash; a mismatch is refused,
which is what stops a later stage from being recorded against a different run.
