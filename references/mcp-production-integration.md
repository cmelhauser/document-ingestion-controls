# MCP Production Integration

This runbook connects a completed controlled run to the repository's
approved-fact MCP, read-only by default with optional separate proposal intake.
It does not change pipeline evidence, authorize a fact, clear
a review item, or write to a target CRM. The canonical deployment and retrieval
contract remains authoritative in
[`canonical-deployment-retrieval.md`](canonical-deployment-retrieval.md).
AI operators must use
[`../skills/mcp-api-operations/SKILL.md`](../skills/mcp-api-operations/SKILL.md)
as the governing setup, deployment, connection, acceptance, use, monitoring,
rebuild, and shutdown router for this runbook.

## Implemented boundary

The production code path is complete for local and remotely deployable
approved-fact query pilots:

- `retrieval_store.py` builds a no-replace, integrity-checked SQLite snapshot
  from an approved canonical export.
- `crm_service.py` is the single semantic layer used by MCP and HTTPS. It
  implements export summary and schema discovery, cross-table search, exact
  record lookup, bounded table query/export, account cards, sales analysis, and
  seven deterministic reports.
- `retrieval_mcp.py` exposes twelve read-only stdio tools: two document tools
  and ten CRM tools by default. Inputs are closed-world and bounded; each baseline tool is
  titled, carries input/output schemas plus structured results, and is marked
  read-only, non-destructive, and idempotent.
- `retrieval_remote_mcp.py` exposes those twelve tools over stateless MCP
  Streamable HTTP at `/mcp` and adds `create_crm_export` and
  `get_crm_export_job`. It requires TLS and RFC 7662 OAuth token introspection,
  exact audience/tenant/role/scope authorization, request/rate/origin bounds,
  and content-free audit events. It serves both the 2025 initialize-handshake
  era used by existing connectors and the 2026-07-28 stateless era. The modern
  path requires matching protocol metadata plus `Mcp-Method`/`Mcp-Name`
  routing headers and returns required completion and private-cache markers.
  `create_crm_export` is advertised as an additive, non-idempotent operation
  because it creates a server-side artifact; it remains non-destructive and
  never changes canonical or target-CRM facts. It can also produce ZIP package
  downloads for the verified no-send staging/common-import artifacts when the
  remote deployment is configured with the exact canonical export and load plan.
- `crm_export_jobs.py` creates bounded owner-bound CSV/XLSX table or standard-
  report artifacts plus optional ZIP package artifacts. Each immutable job
  records the snapshot and row/file checksums, requester hash, request
  arguments, optional review-link metadata, expiry, and random download-token
  hash. Spreadsheet-formula prefixes are neutralized and downloads are short-
  lived and checksum-verified.
- `retrieval_https.py` exposes the same business functions through an explicitly
  enabled, TLS-only, bearer-authenticated GET API. It is a REST transport, not a
  remote MCP endpoint.
- `retrieval_sidecar.py` exposes the same approved-fact CRM semantics through a
  private internal `FastAPI` listener intended for localhost or same-task
  container use. It serves `GET /health`, `GET /capabilities`, and POST JSON
  routes for account cards, record search/query, sales analysis, and standard
  reports against one immutable SQLite snapshot.
- `mcp/retrieval.mcp.json.example` and `scripts/run_retrieval_mcp.sh` provide the
  portable local launcher. The launcher reads the database and never creates or
  updates run artifacts.

Target-CRM writes and the public hosting environment remain separate deployment
work. The repository does not choose the client's DNS, reverse proxy, identity
provider, OAuth user-consent policy, monitoring stack, retention policy, or
ChatGPT/Claude workspace publication settings. Do not describe the included
REST listener as a mobile MCP connector; use the remote MCP server for that
deployment after client acceptance.

## Activation gate for a production run

Do not start the MCP merely because extraction or a provider lane completed.
All of these conditions are required for the exact snapshot being served:

1. Every selected run lane is represented by a successful artifact or an
   explicit retained blocker. `run_lane_coverage.py --require` has passed for
   the engagement's selected lanes.
2. `run_workspace.py audit` passes for the contained run.
3. The exhaustive final-review queue for the named artifact set is `clear`.
4. Applicable arithmetic, attribution, completeness, sampling, and final-review
   gates have separately passed or carry the documented client acceptance.
5. `canonical_export.py` has produced a new canonical export and companion
   exception artifact. The operator has read the exception artifact; omitted or
   unmapped records are not silently treated as loaded.
6. `canonical_load.py` validates the exact export and produces its ordered,
   checksummed plan. `csv_api_staging.py` self-verifies the exact matching
   no-send package when CRM staging is in scope.
7. `retrieval_store.py build` succeeds without `--allow-empty`. The snapshot is
   stored under the run root, outside version control, and is not replaced in
   place by a later run.

An empty `review/` or `canonical/` directory means the MCP activation gate has
not been reached. An empty snapshot built with `--allow-empty` is a diagnostic
artifact and is not a production query service.

## Build the Phase 6 artifacts

Use the contained run wrapper for every command that writes into the run. The
paths below are examples; select the exact retained artifacts from the completed
run rather than guessing filenames.

```bash
python scripts/run_workspace.py audit RUN
python scripts/run_workspace.py run RUN -- \
  python scripts/run_lane_coverage.py RUN \
  --require SELECTED_LANE --out RUN/lane_coverage_phase6.json

python scripts/run_workspace.py run RUN -- \
  python scripts/canonical_export.py \
  --manifest RUN/pages/ingestion_manifest.json \
  --consensus RUN/controls/consensus.json \
  --arithmetic RUN/controls/arithmetic.json \
  --final-review RUN/review/final_client_review.json \
  --batch-id APPROVED-BATCH-ID \
  --out RUN/canonical/canonical_export.json \
  --exceptions RUN/canonical/canonical_export_exceptions.json

python scripts/run_workspace.py run RUN -- \
  python scripts/canonical_load.py RUN/canonical/canonical_export.json \
  --out RUN/canonical/canonical_load_plan.json

python scripts/run_workspace.py run RUN -- \
  python scripts/csv_api_staging.py \
  RUN/canonical/canonical_export.json \
  RUN/canonical/canonical_load_plan.json \
  --out RUN/canonical/crm_staging

python scripts/run_workspace.py run RUN -- \
  python scripts/retrieval_store.py build \
  RUN/canonical/canonical_export.json \
  --out RUN/canonical/business_retrieval.sqlite
```

Do not pass `--allow-empty`, do not reuse an existing output path, and do not
copy raw provider responses into the retrieval database or delivery package.
If an authorized amendment or mapping changes the accepted facts, rerun the
affected controls and build a new canonical export and SQLite snapshot. Never
mutate the issued snapshot in place.

## Run the repository production acceptance

Before deploying a client snapshot, run the deterministic fictional acceptance
from a normal terminal or CI worker that permits an ephemeral loopback socket:

```bash
bash scripts/run_mcp_production_acceptance.sh --help
PYTHON_BIN=.venv/bin/python bash scripts/run_mcp_production_acceptance.sh \
  /tmp/mcp-production-acceptance
```

It refuses to overwrite its output and exercises the strict canonical load
plan, all-table no-send staging, immutable retrieval build, the real stdio
launcher, all twelve local tools, all fourteen remote tools, all seven standard
reports, exact/search/query/account/sales paths, legacy and modern MCP routing,
OAuth scope/owner boundaries, content-free audit events, downloads, and both
CSV and XLSX checksums. A pass writes the versioned
`mcp_production_acceptance_summary.json` with the source snapshot and artifact
hashes. This test intentionally uses fictional records. It does not replace the
deployed-URL, identity-provider, workspace, load, restart, backup, monitoring,
or exact-client-snapshot acceptance below.

## Register the local MCP in Claude Code

For the Claude Code process operating the completed run, prefer a private local
registration. Substitute absolute paths:

```bash
bash scripts/run_retrieval_mcp.sh --help
claude mcp add --transport stdio --scope local business-document-retrieval -- \
  bash /ABSOLUTE/REPOSITORY/scripts/run_retrieval_mcp.sh \
  /ABSOLUTE/RUN/canonical/business_retrieval.sqlite
claude mcp get business-document-retrieval
```

Use `/mcp` inside Claude Code to inspect the connection and tool list. Local
scope keeps the machine- and run-specific database path out of the repository.
Project scope is appropriate only when the checked-in configuration contains no
client path or credential and each operator supplies an approved local path.

Claude Desktop can use the same stdio launcher through a private desktop
extension or its local developer MCP configuration. Local MCP servers are
available to Claude Desktop and Claude Code, not Claude mobile or claude.ai.
Tool results
become Claude conversation context, so the client's Anthropic privacy,
retention, residency, and user-access decisions must cover that data flow even
though the SQLite database remains local.

Anthropic's current local setup guidance prefers a packaged `.mcpb` desktop
extension for repeatable Desktop distribution; the tracked launcher remains the
server command inside that package. Packaging and organization allow-listing are
deployment work, not prerequisites for the private Claude Code registration
above. See the official [Claude Code MCP setup](https://code.claude.com/docs/en/mcp)
and [Claude Desktop local MCP guidance](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop).

## Acceptance sequence

After registration, verify the snapshot through the public tools rather than by
opening SQLite directly:

1. Call `get_crm_capabilities` and confirm its approved-fact `read_only`,
   `approved_facts_only`, and the expected report catalog.
2. Call `get_crm_export_summary` and record the source-export checksum, batch,
   registry version, table counts, and integrity status used for the session.
3. Call `get_crm_schema` for `party`, `address`, `contact`, and
   `invoice_header`; confirm expected row counts and idempotency keys.
4. Search a known approved company, invoice, ACK, job, address fragment, and
   contact with `search_crm_records`, then retrieve representative exact rows
   with `get_crm_record`.
5. Pull one `get_account_card`; confirm address/contact/role history, activity,
   source documents, and currency-separated totals.
6. Run `analyze_sales` for customer, region, selling location, and period. Verify
   that currency remains a grouping dimension.
7. Run each applicable standard report. `receivables_aging` requires an explicit
   `as_of` date and describes the current approved balance snapshot, not a
   reconstructed historical ledger.
8. Exercise bounded pagination on `query_crm_records` and `export_crm_table`;
   reconcile page checksums and snapshot hashes before using a large export.
9. On a remote deployment, create one CSV and one XLSX job, verify that a second
   user cannot read its status, download it before expiry, and reconcile the
   reported file checksum. Confirm an expired or modified artifact is refused.

The standard report catalog is `account_directory`, `invoice_register`,
`receivables_aging`, `sales_by_customer`, `sales_by_selling_location`,
`attribution_coverage`, and `shipment_performance`. The fictional workbook in
`examples/business_document_schema_samples.xlsx` documents the full CRM export,
API/MCP mappings, report examples, and common query recipes.

## Desktop, mobile, and application options

| Client | Supported now | Required boundary |
|---|---|---|
| Claude Code | Local stdio MCP | Private local registration against one approved snapshot |
| Claude Desktop | Local stdio MCP | Local extension/configuration and client data-flow approval |
| Local MCP-compatible desktop client | Local stdio MCP | Use the tracked launcher/configuration example |
| Internal application or BI pilot | HTTPS REST API | Explicit enablement, TLS, bearer-token rotation, network controls, monitoring, and acceptance |
| Co-located internal application or ECS sidecar | Internal FastAPI sidecar | Keep it private to localhost or task networking, bind one immutable snapshot, and pass the exact image/env/health-check contract to the host application |
| Claude mobile / claude.ai | Remote Streamable HTTP MCP | Deploy the implemented `/mcp` server on public HTTPS, connect the custom connector, and complete OAuth/user authorization |
| ChatGPT workspace or Responses API | Remote Streamable HTTP MCP | Deploy the implemented `/mcp` server publicly or through an approved Secure MCP Tunnel, complete OAuth when required, and verify the selected plan/client surfaces during acceptance; mobile availability is not inferred from API compatibility |

The implemented remote connector is a thin transport over `crm_service.py`, not
a second reporting implementation. It is deliberately stateless and binds one
process to one immutable snapshot and exact tenant. For multiple tenants, run
separate isolated deployments/snapshots or add a separately reviewed routing
layer that resolves the authenticated tenant before any database access.
Conversational tools remain paginated; large CSV/XLSX results use an audited
short-lived download rather than model context.

## Deploy the remote MCP for mobile and web clients

The authorization server must publish OAuth metadata and support the client
registration/authorization flow required by the selected Claude or ChatGPT
workspace, while its access tokens are verifiable through the configured RFC
7662 introspection endpoint. Tokens must contain `sub`, numeric `exp`, the exact
MCP endpoint resource in `aud`, the configured tenant claim, an allowed role,
and either `crm:read` or `crm:export` as applicable. For the example below, the
resource and audience are exactly `https://crm.example.com/mcp`, not merely the
site origin. The MCP publishes protected-resource metadata at both the root
fallback and the path-specific
`/.well-known/oauth-protected-resource/mcp` location; authorization challenges
name the path-specific document.

```bash
export RETRIEVAL_REMOTE_MCP_CLIENT_SECRET='FROM-APPROVED-SECRET-MANAGER'
python scripts/retrieval_remote_mcp.py business_retrieval.sqlite --enable \
  --certificate /restricted/tls.crt \
  --private-key /restricted/tls.key \
  --bind-host 127.0.0.1 --port 9443 \
  --resource https://crm.example.com/mcp \
  --authorization-server https://identity.example.com \
  --introspection-endpoint https://identity.example.com/oauth2/introspect \
  --client-id crm-mcp-introspection \
  --tenant CLIENT-TENANT-ID --allow-role crm_reader \
  --audit-log /restricted/audit/crm-mcp.jsonl \
  --export-dir /restricted/exports \
  --download-base-url https://crm.example.com
```

Terminate public traffic only at the same canonical resource origin or an
approved reverse proxy that preserves `/mcp`, `/downloads/`, and both protected-
resource metadata paths. The listener must remain inaccessible until TLS,
identity-provider claims, user deprovisioning, log/alert retention, backups,
export cleanup, penetration testing, and incident response are accepted. The
signed URL proves possession only; distribute it through the authenticated MCP
tool and retain no raw token in logs. The export root must be a dedicated real
directory with no group/other permissions. A job fails closed if a paginated
read crosses snapshot hashes, cleans its partial directory if creation fails,
and fully validates its manifest before status or download.

For Claude, add `https://crm.example.com/mcp` as a custom web connector in the
approved Claude organization/account. Claude reaches remote connectors from
Anthropic's cloud infrastructure on every client, so the endpoint must be
publicly reachable from the current Anthropic IP ranges; a workstation-only or
VPN-only address is not sufficient. Team/Enterprise owners add the organization
connector, while individual users connect their own authorized identity. A
user can then test the already-added connector from Claude on iOS/Android.
As checked on September 3, 2026, Anthropic describes mobile installation as
beta; web/Desktop remain the primary custom-connector setup path. Do not
promise a mobile-only setup workflow or native image-byte transfer. See
[Claude connector setup](https://support.claude.com/en/articles/11176164-use-connectors-to-extend-claude-s-capabilities).
For ChatGPT,
register the same endpoint in the approved workspace or exercise it through the
Responses API. OpenAI's current
[remote MCP guidance](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)
permits a public Internet endpoint and documents Secure MCP Tunnel for a private
or on-premises server;
OAuth authorization is supplied when the server requires it. Product plan,
workspace publication, and mobile-client availability remain live acceptance
questions rather than properties this server can prove. Exact UI labels and
plan availability can change; verify the Claude side during deployment against Anthropic's current
[custom connector instructions](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
and [Claude Code MCP instructions](https://code.claude.com/docs/en/mcp).

Claude Code can also exercise the deployed endpoint before workspace rollout:

```bash
claude mcp add --transport http --scope local business-document-retrieval-remote \
  https://crm.example.com/mcp
claude mcp get business-document-retrieval-remote
```

Open `/mcp` in Claude Code to complete OAuth and inspect tool count. The
authorization server must support token refresh and either a registration
method Claude supports or a preconfigured client ID/secret. For claude.ai and
mobile, configure the connector in Claude's connector settings rather than
placing a bearer token in a checked-in MCP file.

## Production verification matrix

Measured 100% statement and branch coverage is a release gate, not a claim that
every future client, CRM, network, or adversarial condition has been imagined.
Before production acceptance, retain evidence for each applicable row:

| Layer | Required cases |
|---|---|
| Deployment | DDL transaction and object inventory; planned schema hash unchanged at execution; missing credential; `psql` refusal; no secret in plan or argv |
| Canonical/load | empty and partial exports; every review/provenance status; duplicate/composite keys; foreign-key chains; stable semantic load-plan checksum |
| No-send packages | all 28 canonical tables accounted for; twelve common objects; empty files; formula-like text; Unicode/newlines; tampered rows/files/manifests; partial-build cleanup; no-clobber publication |
| Snapshot | empty refusal; all table/row hashes; duplicate keys; FTS search; exact and composite lookup; corrupt database/JSON/index; read-only open; atomic no-replace publication |
| Semantic CRM | every filter and invalid type; projections/sorts/pages; addresses and contact cards; mixed currencies; date/value boundaries; every report; missing relationships; source/checksum retention |
| Local MCP | malformed JSON/JSON-RPC; discovery and initialize negotiation; closed-world arguments; unknown tools/methods; structured output; quiet stdout; launcher path/database failures |
| Remote MCP | 2025 and 2026 protocol eras; path-specific OAuth discovery; routing-header/body agreement; inactive/expired/wrong-audience/wrong-tenant/wrong-role tokens; read/export scope step-up; Origin, size, rate, method, and content-type refusal |
| Exports | table/report CSV and XLSX; snapshot change between pages; row/cell/column caps; formula text; owner isolation; exact-expiry refusal; bad token; changed file; malformed manifest; failed-build cleanup |
| Claude acceptance | local Claude Code registration; Desktop extension/configuration where selected; remote Claude Code OAuth; claude.ai connector; selected iOS/Android use; representative prompts and all fourteen baseline remote tools, plus nine scope-filtered intake and nine unified-platform tools when enabled |
| Operations | TLS/reverse-proxy headers; Anthropic egress allow-list; identity deprovisioning and refresh; audit/alert retention; backup; export cleanup; load/rate test; restart; incident revoke/rebuild exercise |

Run the official MCP Inspector against the deployed URL in addition to the
repository tests, then run the conversational acceptance sequence above in the
exact Claude workspace. The repository can prove its code and fixtures; only
that deployed exercise can prove the selected DNS, proxy, identity provider,
Claude account policy, and production snapshot work together.

## Stop and rebuild conditions

### Unified client-YAML deployment

For typed analytics, analytical downloads, the review portal, and governed
record proposals, use `config/client-platform.example.yaml` and the complete
[Business Data Platform](business-data-platform.md) contract. Validate the exact
client configuration and approved snapshot with `business_platform_deploy.py`,
then run `business_platform_server.py --enable` through one of the templates in
`deploy/business-platform/`. The unified remote catalogue has up to thirty-two
tools: fourteen baseline, nine intake, and nine platform/export operations,
filtered by granted scopes. Local stdio has up to twenty-nine because the
download-job operation is remote-only.

The YAML owns every non-secret deployment path, OAuth claim/role/Origin setting,
limit, semantic dataset, saved report, writable object, and target mapping.
Secrets are supplied only through the environment variable names in the YAML.
Models can submit proposals but cannot call authorize/apply/reconcile. Client
identity, infrastructure, target sandbox, golden totals, backup/recovery, and
the exact Claude/ChatGPT desktop/mobile client still require deployed acceptance.

The remote JSON API additionally exposes three operator-only record lifecycle
routes. `authorize_business_record_change` requires `records:authorize` and
signs an explicit human decision; `apply_business_record_change` and
`reconcile_business_record_change` require `records:apply`. These routes are
intentionally excluded from MCP discovery, so a connected model can never
self-authorize or execute a write. The equivalent local operator interface is
`business_record_changes.py`.

Stop serving the snapshot and build a new one when its canonical source hash no
longer matches the authorized facts, an affected control is rerun, an amendment
or mapping is authorized, integrity verification fails, the client revokes the
data-flow approval, or a user/tenant boundary cannot be enforced. MCP access is
read-only for canonical facts, but access to the wrong snapshot is still a
production defect. Intake integrity, ownership or source-flow failures also
require stopping the affected intake service while preserving its journal.

## Optional visual proposal intake

The baseline above remains the default read-only canonical service. Supplying
`--ingestion-dir` to the local or remote MCP server additionally enables a
separate source/proposal journal. The complete opt-in contract, flags, limits,
scopes, client byte-transfer requirements, eleven operations and acceptance
checklist are in [Visual Intake](visual-ingestion.md), with an operator skill at
[`skills/record-intake/SKILL.md`](../skills/record-intake/SKILL.md).

Enabled local discovery has twenty-three tools; enabled remote has up to
twenty-five filtered by scopes; the client platform adds eight local or nine
remote tools. Remote JSON API routes share the OAuth
deployment at `/api/ingestion/{operation}`; the separate TLS/bearer REST
service remains GET only. `ingestion:read`/`ingestion:submit` do not grant CRM
access. The intake extension now includes session discovery, additive reviewer
grants, owner-only reviewer listings, and structured per-session review
summaries for ChatGPT or other remote MCP clients. The same deployment also
serves a thin same-origin review page at `/ingestion/review` and JavaScript at
`/ingestion/review.js`, both backed by the authenticated intake JSON API.
`/health` and `/docs` report `read_only: false` for enabled intake while
`canonical_read_only: true` remains invariant. No new environment variable is
needed. Explicit deployment/source-flow authorization is still required.

A structurally valid submitted record is pending review, never approved or
published. The local source-only export utility preserves originals/history and
prepares an image PDF for normal profiling/intake, not an extraction or
consensus handoff. There is no automatic proposal promotion, canonical update,
owner transfer, admin moderation, or native mobile capture application in this
slice. Existing fictional acceptance tests the default disabled-intake surface;
run intake-specific tests and actual deployed-client acceptance separately. The
[roadmap](business-data-platform-roadmap.md) tracks those next stages and
governed vendor-neutral analytics.
The implemented client-YAML analytics and governed change adapter are specified
in [Business Data Platform](business-data-platform.md).
