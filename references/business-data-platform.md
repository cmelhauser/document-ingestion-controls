# Client-configured business data platform

This is the operating contract for the optional business-data layer above one
immutable approved-fact retrieval snapshot. It is vendor-neutral and is
configured by one secret-free YAML file. It provides typed multidimensional
analytics, checksummed exports, source-image proposal intake, and governed
create/amend delivery to a client-selected target. It does not weaken any
upstream approval or evidence gate.

## Trust boundaries

The served SQLite snapshot remains read-only. Retrieval and analytics contain
only facts already admitted through the canonical gate. Image intake and record
changes live in separate tenant/config-bound append-only journals. An assistant
may read schemas and approved facts and may create proposals. It cannot
authorize, apply, delete, or publish a record. Authorization and target
application are operator-only, separately scoped API/CLI steps using secrets
that are never made MCP tools.

A successful target reconciliation still does not mutate the served snapshot.
The change becomes queryable only after the normal controls produce a new
approved canonical export and a replacement retrieval snapshot.

## Configuration

Copy `config/client-platform.example.yaml` outside the repository, replace every
fictional value, and validate it with `business_platform_deploy.py`. Commit a
sanitized template only; do not commit a client's paths, identifiers, endpoints,
or secrets. The whole YAML is fingerprinted and every journal is bound to that
fingerprint and tenant.

| Section / field | Meaning |
|---|---|
| `schema_version` | Must be `business_data_platform_v1`. |
| `client.tenant_id` | Exact OAuth tenant and journal partition. |
| `client.display_name` | Human-readable deployment name. |
| `client.timezone` | Reporting timezone label. Source timestamps are not silently converted. |
| `client.fiscal_year_start_month` | Integer 1–12 used for fiscal quarters/years. |
| `deployment.snapshot` | Exact approved, immutable retrieval SQLite file. |
| `bind_host`, `port`, `public_base_url` | Listener and canonical public HTTPS origin. MCP is exactly `public_base_url + /mcp`. |
| `tls_certificate`, `tls_private_key_env` | Public chain path and environment variable containing the private-key file path. |
| `authorization_server`, `introspection_endpoint`, `client_id`, `client_secret_env` | OAuth protected-resource and RFC 7662 introspection settings. |
| `tenant_claim`, `role_claim`, `allowed_roles` | Required tenant and role authorization contract. |
| `allowed_origins` | Exact browser Origins allowed to call MCP/API; absence of Origin remains valid for native clients. |
| `audit_log`, `export_dir`, `intake_dir`, `change_dir` | Private state paths. Omit `intake_dir` to disable visual intake. |
| `max_request_bytes`, `rate_limit_per_minute` | Network request and per-address budgets. Base64 images must fit the request budget. |
| `export_ttl_seconds`, `max_export_rows` | Expiring download lifetime (60–3600 seconds) and row ceiling (1–100,000). |
| `record_maintenance.enabled` | Enables schema, propose, preview, and status MCP operations plus separately scoped operator API lifecycle routes. |
| `approval_ttl_seconds`, `separation_of_duties` | Authorization lifetime and submitter/approver policy. |
| `authorization_secret_env` | Environment variable holding at least 32 signing characters. |
| `objects` | Allowlisted canonical table/key, writable fields, target endpoint/object, and field mapping per business object. |
| `adapter.kind` | `file` for no-send staging or `http_json` for HTTPS POST/PATCH/GET. |
| `adapter.output_directory` | Private no-clobber target staging directory for `file`. |
| `adapter.base_url`, `credential_env` | HTTPS origin and bearer-token environment variable for `http_json`. |
| `adapter.timeout_seconds`, `headers`, `response_id_field` | Bounded target transport controls. Authorization headers are forbidden in YAML. |
| `analytics.enabled` | Enables typed semantic-model/report operations. |
| `max_input_rows`, `max_result_rows` | Fail-closed source scan and response group limits. |
| `datasets` | Base canonical table, bounded many-to-one joins, typed dimensions, and governed metrics. |
| `saved_reports` | Named immutable query plans exposed to clients. |

The loader rejects unknown outer fields, unsafe URLs, embedded secret-like
values, Authorization headers, invalid bounds, symlinks, and oversized YAML.
The analytics and write engines then validate every table and field against the
canonical catalog.

## Analytics query language

Clients first call `get_analytics_semantic_model`. A query plan names one
configured dataset, zero to eight dimensions, one to twelve metrics, and
optional filters, date grains, having filters, sort, totals, limit, and offset.
No SQL or arbitrary expression is accepted.

Dimensions are `string`, ISO `date`, or decimal `number`. Filters support exact
equality/inequality, bounded membership, null tests, typed ranges and between;
string dimensions also support contains/prefix/suffix. Date grains are day,
month, calendar quarter/year, and configured fiscal quarter/year. Metrics are
sum, count, distinct count, average, minimum, or maximum. A currency-valued
metric automatically includes its declared currency dimension so currencies
are never combined silently.

Example sales request:

```json
{
  "dataset": "invoice_sales",
  "dimensions": ["invoice_date", "region", "customer"],
  "date_grains": {"invoice_date": "quarter"},
  "metrics": ["sales", "invoice_count"],
  "filters": {"region": {"operator": "in", "value": ["MA", "NY"]}},
  "having": {"sales": {"operator": "gte", "value": "10000"}},
  "include_totals": true,
  "sort": ["invoice_date", "-sales"],
  "limit": 100,
  "offset": 0
}
```

Every response binds the configuration and source snapshot, accounts for input,
selected, and grouped rows, retains invalid/missing metric groups as exceptions,
and carries a result checksum. Join fan-out is refused. Pagination is explicit.
Use `create_business_analytics_export` for owner-bound expiring CSV/XLSX output
from a plan or saved report; it uses the same immutable export-job checksums and
formula-injection defenses as standard CRM exports.

## Record-change lifecycle

1. Discover `get_business_object_schema` and retrieve the current record.
2. Submit `propose_business_record_change` with a unique idempotency key. An
   amendment must bind the exact prior record SHA-256; a create must not collide.
3. Call `preview_business_record_change` and inspect the before/after state and
   mapped target payload. A changed source snapshot makes the preview stale.
4. An authorized human/operator uses the non-MCP, separately scoped JSON API
   (`authorize_business_record_change`) or the equivalent
   `business_record_changes.py authorize` command. The HMAC receipt binds one
   preview, submitter, approver, configuration, and expiry. The connector model
   is never allowed to call this operation.
5. The operator uses the separately scoped API or CLI `apply`; the `file`
   adapter creates an immutable no-send payload, while `http_json` requires an
   explicit execute confirmation, sends POST for creates or PATCH for
   amendments, and preserves an application receipt. The signed authorization
   digest is the stable outbound target idempotency key, so concurrent operator
   requests for that same approval cannot ask an idempotency-aware target to
   perform the mutation twice.
6. Use the separately scoped API or CLI `reconcile`, which independently reads
   the exact staged/remote target and compares every expected field. Missing or
   different values remain blocked.
7. Run the affected approval/control workflow and build a new immutable approved
   retrieval snapshot before clients read the change.

`get_business_record_change` returns owner-scoped integrity-verified lifecycle
receipts but never returns signing or target credentials. The remote API has
three operator-only routes—`authorize_business_record_change` requires
`records:authorize`; `apply_business_record_change` and
`reconcile_business_record_change` require `records:apply`. They are excluded
from MCP discovery and never callable by the connected model. Hard delete is
not implemented. Target timeouts are ambiguous outcomes: reconcile before
retrying.

## Image intake and review portal

When `deployment.intake_dir` is present, the nine owner-scoped intake operations
are exposed through MCP and `/api/ingestion/{operation}`. `/portal` provides a
responsive same-origin PNG/JPEG upload and review UI; its bearer value stays in
page memory, and executable/style assets are served separately under a strict
Content Security Policy. Production installations should obtain tokens through
the client's identity/session gateway; the generic page intentionally does not
store, mint, refresh, or persist credentials.

The connected vision-capable LLM discovers the proposal schema, reads every
retained source image, and submits source-cited candidates plus explicit page
exceptions. The portal lists sessions, displays exact retained images and
proposal history, and visibly states that results are pending review. This store
is still a pre-pipeline proposal journal; use the source-only exporter for the
normal controlled run.

## Deployment choices

Run:

```bash
python scripts/business_platform_deploy.py /secure/client-platform.yaml \
  --out /new/deployment-plan.json
python scripts/business_platform_server.py /secure/client-platform.yaml --enable
```

`deploy/business-platform/` includes a non-root read-only-container Dockerfile,
Compose template, single-writer Kubernetes template, and hardened systemd unit.
Replace all `CLIENT_*` placeholders, mount the approved snapshot and certificate
read-only, mount journals/exports privately writable, and inject only the
environment variable names declared by the YAML. The bundled journals are
SQLite, so the Kubernetes example deliberately uses one writer replica; scaling
requires a separately accepted transactional journal adapter.

Before production acceptance, validate OAuth audience/tenant/roles/scopes,
Origin policy, TLS/proxy behavior, token expiry/revocation, cross-owner refusal,
request/rate/export limits, checksums, backup/restore, restart, incident
shutdown, every saved report against client-approved golden totals, the complete
write lifecycle with fictional records, and the exact intended ChatGPT/Claude
desktop/mobile workspace. Repository tests do not prove the client's identity,
network, target API, or product-plan connector availability.
