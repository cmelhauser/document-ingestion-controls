# Vendor-neutral business data platform roadmap

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

## Next: client transfer and review integration

Build a small authenticated capture/upload application with actual byte
transfer, orientation-aware preview and receipt display, usable separately from
a chat conversation. Add PDF/container preservation and page preparation without
replacing originals. Add owner-safe session discovery and explicitly authorized
reviewer access. Test the exact intended ChatGPT/Claude desktop and iOS clients;
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

## Then: governed create/update adapter

Expose a separate versioned business-object schema for create and amendment
requests. Define account/contact/address/transaction/line/payment relationships,
stable keys, allowed fields, types, null/clear semantics and validation rules.
Keep user assertions and source-backed facts distinct. All operations use an
authenticated actor, tenant, reason, source references and request key.

Build `propose -> validate -> preview -> authorize -> apply -> reconcile` around
an append-only change set. Updates name the exact prior record version/hash;
stale writes conflict, not silently overwrite. A preview shows before/after,
affected relationships and downstream controls. Approval binds the immutable
preview hash, actor and expiry; submitters cannot manufacture approval payloads.
Apply only after required independent controls and authorization, then publish a
new immutable approved snapshot. Rejected changes stay visible. Rollback is a
compensating amendment, never deletion of history. Separate permission scopes
and optional separation of duties govern submission, approval and application.

Define a target-neutral adapter contract for schema/capabilities, mapping,
validation/dry-run, idempotent create/upsert/amend, status and reconciliation.
First ship verified no-send common-object CSV/JSON packages using existing CRM
import packages. External delivery follows only after target sandbox acceptance,
explicit authorization and credential configuration. Keep server-side outbox,
retry classification, exact external IDs and partial-failure receipts so a
timeout cannot create duplicate records. Defer hard deletion and entity merge
until their dependency and authorization semantics are designed and tested.

Acceptance: duplicates, repeated/concurrent requests, stale versions, cross-owner
and tenant access, replayed/expired approvals, tampering, partial batch outcomes,
lost acknowledgments, reconciliation and compensating changes tested. Add
record-maintenance and target-adapter skills only when their real tools exist.

## Analytics: reusable governed metrics and query plans

Retain current account cards, exact lookup, search, filters, standard reports and
exports. Extend the shared MCP/API semantic service, not provider-specific logic.
Define measures/dimensions and allowed joins with explicit grain, aggregation,
currency, timezone, fiscal calendar, null and deduplication semantics. Avoid
double-counting invoices through lines, addresses or payment applications.

Add a typed query plan for multi-dimension grouping, safe joins, date windows,
ranking, drill-down and comparison periods; compile through allowlisted SQL and
bound parameters. No model-supplied unrestricted SQL. Push bounded aggregation
into an indexed query store instead of scaling Python table scans. Add budgets,
cancellation, explainable errors, pagination and snapshot-bound cache keys.

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
