---
name: mcp-api-operations
description: Set up, deploy, validate, connect, and operate approved-fact CRM MCP/APIs plus the optional client-YAML analytics, visual-intake, and governed record-proposal platform. Use for stdio or OAuth MCP, reports/exports, client registration, and acceptance; never grant a model authorization or target-write authority.
---

# MCP and API Operations

Operate one immutable, approved-fact retrieval snapshot through the repository's
shared semantic service. This skill governs setup, deployment, connection,
acceptance, use, monitoring, and shutdown. It never makes an unfinished run
queryable, approves a fact, clears review, or authorizes a target-CRM write.

## Read the authority for the requested mode

Always read `../../references/mcp-production-integration.md` completely. Also
read:

- `../../references/canonical-deployment-retrieval.md` for snapshot construction,
  integrity, PostgreSQL, or staging work.
- `../../references/runtime-configuration.md` for environment settings or
  secrets and `../../references/command-line-reference.md`, its complete
  parser-derived `../../references/cli-help-catalogue.md`, plus the executable's
  `--help` before invoking a command.
- `../../references/artifact-contracts.md` before changing an input, output,
  manifest, export, workbook, or runbook contract.
- `../../references/crm-write-readiness.md` when the request crosses from
  retrieval/export into CRM import or mutation. That is a separate no-send and
  authorization boundary.
- `../../references/business-data-platform.md` for the client YAML, typed
  analytics, analytical exports, portal, governed change adapter, and deployment
  templates; use `../record-maintenance/SKILL.md` for an actual change lifecycle.
- `../business-doc-operations/SKILL.md` when Phase 6 prerequisites do not yet
  exist and the document run itself must be operated.

Use `../../docs/TECHNICAL_DOCUMENTATION.md` for end-to-end context and
`../../HANDOFF.md` only for current repository/run state.

## Select the correct surface

| Need | Surface | Entry point |
|---|---|---|
| Same-machine Claude Code/Desktop or compatible client | Local stdio MCP | `scripts/run_retrieval_mcp.sh` |
| Claude or ChatGPT workspace/mobile/web connection | Remote Streamable HTTP MCP | `scripts/retrieval_remote_mcp.py` at the exact public `/mcp` resource |
| Unified client-configured analytics/intake/proposals | YAML-driven OAuth MCP/API | `scripts/business_platform_server.py`; templates in `deploy/business-platform/` |
| Controlled internal application or BI integration | TLS/bearer read-only REST API | `scripts/retrieval_https.py` |
| Co-located application container or ECS sidecar | Internal FastAPI sidecar | `scripts/retrieval_sidecar.py` |
| Direct local inspection or snapshot construction | Retrieval CLI | `scripts/retrieval_store.py` |

Do not present the REST API as an MCP connector. Do not infer that a ChatGPT or
Claude plan, workspace, or mobile surface supports registration merely because
the protocol is compatible; verify the selected live client during acceptance.

## Require the Phase 6 activation gate

Before starting or registering any surface, prove all of the following for the
exact run and snapshot:

1. `run_workspace.py audit` passes and every engagement-selected lane appears in
   `run_lane_coverage.py --require` or has a retained blocker.
2. The exhaustive final-review queue is `clear`; an empty review directory is
   not clearance.
3. Every applicable arithmetic, attribution, completeness, and approval gate
   passes independently.
4. `canonical_export.py` produced a non-empty approved export and explicit
   exception artifact, followed by an exact `canonical_load.py` plan.
5. `retrieval_store.py build` produced a new immutable SQLite snapshot without
   `--allow-empty`; its integrity and source-export checksums are recorded.
6. The operator has separately authorized the intended deployment/network/data
   flow. Run transmission authorization does not authorize MCP activation.

If any condition is absent, stop and identify the missing artifact or approval.
Never create a passing-shaped substitute or query provider/raw-review artifacts.

## Establish code readiness

Before client-snapshot deployment, run the repository-controlled fictional
acceptance in a normal terminal or CI worker that permits an ephemeral loopback
socket:

```bash
PYTHON_BIN=.venv/bin/python bash scripts/run_mcp_production_acceptance.sh \
  /NEW/OUTPUT/mcp-production-acceptance
```

With intake disabled, require `status: passed`, all six gates, twelve local tools, fourteen remote
tools, seven reports, and retained hashes in
`mcp_production_acceptance_summary.json`. This proves repository integration
only. It does not prove the production proxy, identity provider, workspace, or
client snapshot.

## Set up and use local stdio MCP

1. Keep the approved SQLite snapshot immutable and accessible only to the local
   operator/client process.
2. Run `bash scripts/run_retrieval_mcp.sh --help`, then register the launcher
   with absolute repository and database paths and private/local scope.
3. Verify `initialize`, `tools/list`, `get_crm_capabilities`, and
   `get_crm_export_summary` before business queries.
4. Record the source-export checksum, registry version, batch, table counts, and
   integrity status used for the session.

Never log to stdout from the stdio server except for MCP protocol messages.

## Deploy and accept remote MCP

1. Use one isolated process, snapshot, tenant, private export directory, and
   append-only audit log per deployment boundary. Keep certificates, private
   keys, and the RFC 7662 client secret outside the repository and run root.
2. Use the exact canonical public HTTPS URL ending in `/mcp` as `--resource`,
   OAuth audience, protected-resource metadata resource, and connector URL.
3. Configure a trusted TLS chain, reverse proxy/timeouts, real authorization
   server and introspection endpoint, exact tenant claim, allowed roles,
   `crm:read`/`crm:export` scopes, payload/rate/export limits, and approved
   Origins. Start only with `--enable` after deployment authorization.
4. Test inactive, expired, wrong-audience, wrong-tenant, wrong-role, missing-scope,
   disallowed-Origin, oversized, rate-limited, cross-owner, expired-download,
   and modified-artifact refusals. Confirm audit events contain identifiers and
   outcomes but no raw subject or business content.
5. Run the official MCP Inspector against the deployed URL, then register it in
   the selected Claude/ChatGPT workspace and execute representative prompts.
6. Exercise token refresh, deprovisioning, credential revocation, process
   restart, backup/restore, export cleanup, alerts, and snapshot replacement.

Do not claim production acceptance until the exact deployed environment and
approved non-empty client snapshot pass this sequence.

## Deploy and accept the REST API

Use `retrieval_https.py` only for a client-authorized internal application or BI
pilot. Keep loopback binding unless the approved network design says otherwise.
Require TLS, a dedicated rotated bearer secret, firewall/proxy controls,
monitoring, bounded queries, representative consumer tests, and incident
revocation. It exposes read-only GET routes and never substitutes for OAuth
tenant isolation or remote MCP.

For a same-task application integration that does not need public exposure,
`retrieval_sidecar.py` is the lighter transport. It serves the shared
approved-fact CRM semantics over private HTTP on `HOST`/`PORT`, resolves the
snapshot from `RETRIEVAL_DB_URL` or `RETRIEVAL_ARTIFACT_ROOT`, and should stay
inside the host or task network boundary. Accept it through representative
consumer calls to `/health`, `/capabilities`, and the POST retrieval/report
routes before deployment.

## Query and report safely

For the unified platform, start from `config/client-platform.example.yaml`, keep
the client copy and all secrets outside the repository, and run
`business_platform_deploy.py` to fingerprint the configuration/snapshot and
write a no-secret acceptance plan. Launch `business_platform_server.py --enable`
through the supplied non-root Docker/Compose, single-writer Kubernetes, or
systemd template only after deployment authorization.

That surface adds eight local operations or nine remote operations when export
jobs are available. Typed analytics provides allowlisted many-to-one joins,
dimensions, measures, date/fiscal grains, typed filters, having, totals, numeric
sorting, and pagination. Analytical exports use the same owner-bound expiring
CSV/XLSX job controls as standard CRM exports. Record operations stop at
schema/propose/preview/status; authorization, live application, and
reconciliation are operator-only CLI actions, and a successful target
reconciliation still requires a new approved snapshot.

Start with capabilities, export summary, and schema discovery. Then choose:

- `search_crm_records` when the canonical key is unknown.
- `get_crm_record` for an exact table/key lookup.
- `get_account_card` for party, roles, names, addresses, contacts, transactions,
  payments, and source documents.
- `query_crm_records` for bounded one-table filtering, projection, sorting, and
  pagination.
- `analyze_sales` for company/customer, vendor, region, selling-location, time,
  ACK, or job slicing. Keep currencies partitioned.
- `run_crm_report` for account directory, invoice register, receivables aging,
  sales by customer/location, attribution coverage, or shipment performance.
- `create_crm_export` plus `get_crm_export_job` for owner-bound, expiring CSV/XLSX
  artifacts and optional ZIP staging/common-import packages on the remote MCP.
  Creation is additive and non-idempotent, but it does not change canonical or
  target-CRM facts. Optional `review_context` metadata may link a package back
  to a reviewed visual-intake session without claiming proposal publication.

Keep pagination and server limits intact. Preserve returned source IDs,
checksums, and citations. Treat receivables aging as an approved current-balance
snapshot at the requested `as_of`, not historical ledger reconstruction.

## Optional visual intake extension

For separately authorized new source intake read `../record-intake/SKILL.md`
and `../../references/visual-ingestion.md`. The servers can opt in through
`--ingestion-dir`; local stdio uses the direct Python entry point and optional
`--ingestion-tenant`, while remote uses its existing `--tenant`. The journal
retains unapproved images/proposals separately from the snapshot. No intake
operation approves, applies or publishes a fact. All deployment/snapshot gates
above still apply, and the new source/model data flow requires authorization.

Enabled local intake exposes twenty-three tools; with the client platform it can
expose thirty-one. The unified remote service exposes up to thirty-four tools,
filtered by OAuth scopes. Add `ingestion:read` and
`ingestion:submit` only for authorized users. The JSON application API is on the
remote OAuth server at `/api/ingestion/{operation}`, not the GET-only
`retrieval_https.py` surface. Check full-request base64 size limits, real image
transfer and vision support in the exact client. The fictional baseline
acceptance still expects twelve/fourteen tools with intake disabled; it does not
prove native mobile capture. The separate local source-only export utility is
covered by the intake handoff tests; it is not an MCP filesystem-write tool and
does not approve proposals. Require the intake test suite and the guide's deployed-client
acceptance checklist as well.

For a governed record change, MCP remains proposal-only. The remote JSON API
has operator-only `authorize_business_record_change` (`records:authorize`) and
`apply_business_record_change` / `reconcile_business_record_change`
(`records:apply`) routes, plus the equivalent local CLI. They are deliberately
not MCP tools and must never be called by the connected model.

Do not interpret `get_crm_capabilities().read_only` as a statement about the
whole enabled service: it describes the approved-fact semantic layer. Remote
`/health` and `/docs` report overall intake enablement separately. Preserve the
nine-operation scope-filtered catalogue and do not register the GET-only REST
URL as either MCP or the intake API. A chat tool approval permits that request,
not business-record approval or downstream publication.
Target-specific receivers remain outside this skill and belong on the
destination system side; this repository ends at the verified no-send package
boundary.

## Rebuild, revoke, and stop

Never mutate or replace a served SQLite file in place. An authorized amendment,
mapping change, affected-control rerun, or integrity mismatch requires a new
canonical export, load plan, snapshot, deployment identifier, and acceptance
record. Stop serving and revoke access on checksum failure, unexpected tenant or
scope behavior, content-bearing logs, unexplained export ownership, credential
exposure, or an open upstream gate. Preserve audit and exception evidence under
the approved retention policy; remove expired exports through the documented
cleanup process.
