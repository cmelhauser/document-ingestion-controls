# Vendor-neutral business data platform delivery record and roadmap

The repository now implements the generic client-YAML platform described in
`business-data-platform.md`: mobile/browser intake and review, governed
create/amend adapters, typed analytics, saved reports, analytical exports, and
container/systemd deployment templates. The sections below preserve the design
history. Remaining work is client-specific configuration, infrastructure,
identity/target integration, golden-total validation, and production acceptance;
it is not permission to bypass those gates.

This is an implementation sequence, not a list of production capabilities.
Salesforce was an analogy for flexible business analysis, not a target-system
dependency. Build around approved facts, a governed semantic model and reusable
adapters; preserve the existing pipeline and its evidence/authorization gates.

## Delivered first slice: visual proposal intake

The optional MCP/API journal retains original PNG/JPEG pages, exposes the
existing extraction vocabulary, accepts source-cited proposals from the
connected model, validates bounds/schema/page accounting, and preserves receipts
and failures. It has owner/tenant checks and transactional idempotency. See
[Visual Intake](visual-ingestion.md) for the complete implemented contract.
It stops before approval/publication; none of the following stages is implied
by a `pending_review` result.

## Delivered next slice: source-only handoff

The local `visual_ingestion_export.py` utility snapshots an existing journal
read-only, preserves each session's original bytes, all proposal versions and
rejections, and emits a hash-bound package with explicit review exceptions.
Complete usable source sets receive a lossless display-pixel PDF derivative
with stable session-specific filename, page identity and transformation map.
The existing profiling and intake producers then consume that PDF under a new
contained run. Missing pages or preparation failures retain the archive and
refuse progression. Verification pins the export receipt, every file and each
journal entry. No host-LLM proposal is converted into a consensus vote or
approved fact. See [Visual Intake](visual-ingestion.md) for operation and limits.

## Delivered: client transfer and review integration

The remote server includes a small authenticated capture/upload portal with actual byte
transfer, orientation-aware preview and receipt display, usable separately from
a chat conversation. It preserves supported original PNG/JPEG bytes and adds
owner-safe session discovery and proposal-history review. PDF/container intake
still enters through the established pipeline rather than this image-only journal.
Test the exact intended ChatGPT/Claude desktop and iOS clients;
native connector attachment support is a separate acceptance question. Do not
depend on an LLM generating base64 or on speculative protocol features.

Source-package round-trip, failure retention and producer integration have
repository tests. A first thin same-origin review page now covers session
creation, image upload, reviewer grants, retained-page preview, proposal
submission, and proposal-summary review over the authenticated intake API.
Still required: real-client upload and reading demonstrated with a fictional
golden set, source derivative visual acceptance for intended capture devices,
recoverable interrupted capture/provider failures, and deeper delegated-review
or mobile capture flows. Do not treat the archive as a producer-authenticated
extraction or consensus input.
The client-configured business platform adds a same-origin upload and review
portal, analytics and a governed change adapter; see
[Business Data Platform](business-data-platform.md).

## Delivered generic boundary: governed create/update adapter

Expose a separate versioned business-object schema for create and amendment
requests. Define account/contact/address/transaction/line/payment relationships,
stable keys, allowed fields, types, null/clear semantics and validation rules.
Keep user assertions and source-backed facts distinct. All operations use an
authenticated actor, tenant, reason, source references and request key.

The implementation provides `propose -> validate -> preview -> authorize -> apply -> reconcile` around
an append-only change set. Updates name the exact prior record version/hash;
stale writes conflict, not silently overwrite. A preview shows before/after,
affected relationships and downstream controls. Approval binds the immutable
preview hash, actor and expiry; submitters cannot manufacture approval payloads.
The remote scope governs submission. Approval, application, and reconciliation
are available only through three separately scoped **operator JSON API** routes
or the equivalent operator CLI; they are intentionally absent from MCP tool
discovery and require separate credentials. `records:authorize` signs an
explicit human decision, while `records:apply` permits application and
reconciliation of that retained authorization.
Rejected changes stay visible. Rollback is a compensating amendment, never
deletion of history.

Define a target-neutral adapter contract for schema/capabilities, mapping,
validation/dry-run, idempotent create/upsert/amend, status and reconciliation.
The file adapter emits verified no-send JSON alongside the existing common-object
CSV/XLSX import packages. The generic HTTPS JSON adapter follows only after
target sandbox acceptance, explicit authorization, and credential configuration.
It retains exact external IDs and forces reconciliation after ambiguous timeout.
Hard deletion, entity merge, and target-specific bulk APIs remain deliberately
outside the generic contract.

Repository acceptance covers idempotency, stale versions, owner/tenant boundaries,
expired/tampered approvals, lost acknowledgments, and reconciliation. A client
must separately exercise its target API's partial-batch and compensating-change
semantics. The `record-maintenance` skill governs the implemented tools.

## Delivered generic boundary: reusable governed metrics and query plans

Retain current account cards, exact lookup, search, filters, standard reports and
exports. Extend the shared MCP/API semantic service, not provider-specific logic.
Define measures/dimensions and allowed joins with explicit grain, aggregation,
currency, timezone, fiscal calendar, null and deduplication semantics. Avoid
double-counting invoices through lines, addresses or payment applications.

The typed query plan provides multi-dimension grouping, safe joins, typed date
windows, drill-down by dimensions, metric having, numeric sort/ranking order,
totals, and pagination. No model-supplied unrestricted SQL is accepted. The
engine scans only the configured, bounded immutable snapshot and refuses its
input budget; deployments exceeding that budget should implement and accept a
compatible warehouse compiler rather than silently raising the limit.

Build reusable saved reports and CSV/XLSX exports from that same query plan:
sales by company/region/product, trends and period-over-period change, customer
concentration, payment/receivables analysis, shipment performance, data coverage
and reconciliation. Margin requires actual cost facts, funnel requires actual
opportunity data, and historical aging requires historical balances/events;
missing data is not zero. Keep currencies separate unless an approved dated FX
policy and rate source exist. Forecasts/anomalies remain labeled estimates with
provenance, uncertainty and evaluation rather than silently replacing facts.

Acceptance: golden totals, join-fanout traps, mixed currencies, fiscal/timezone
boundaries, refunds/reversals, nulls, incomplete populations, historical-snapshot
semantics, access control, query limits and representative performance budgets.
Every result identifies the snapshot, metric definition and coverage limitations.

## Production scale and rollout

Before shared client use, design row/field-level access, delegated review,
retention/deletion obligations, tenant quotas, encrypted object storage,
malware scanning, durable queues, backups/restore, migrations, monitoring and
incident revocation. Move the bounded SQLite intake pilot to a transactional
service/object store when capacity and concurrency requirements justify it.
Do not equate private directories or hash checks with a multi-tenant security
certification. Run threat modeling, external identity/proxy acceptance and actual
client UAT separately from repository tests.

Deliver each stage as a backward-compatible reviewed branch: documented tools,
API contracts, skills, parser help and generated catalogue; deterministic and
failure-path tests; measured statement/branch coverage and the full lint/release
gate. Record operational acceptance separately. No promise of exhaustive
real-world cases or production correctness follows from 100% code coverage.
