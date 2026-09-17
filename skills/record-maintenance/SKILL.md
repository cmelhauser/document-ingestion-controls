---
name: record-maintenance
description: Propose, authorize, apply, and reconcile client-configured business-record creates or amendments through the governed MCP/API adapter. Use for target-system maintenance after approved-fact retrieval or visual intake; models may propose but cannot authorize, apply, delete, or publish canonical facts.
---

# Record Maintenance

Read `../../references/crm-write-readiness.md` and
`../../references/business-data-platform.md` completely. For deployment or MCP
registration also read `../mcp-api-operations/SKILL.md`; read
`../../references/artifact-contracts.md` before changing an interface contract.

## Choose the boundary

- An assistant may discover `get_business_object_schema`, retain a
  `propose_business_record_change`, preview it, and read its owner-scoped event
  history. This is proposal-only.
- Only an authorized operator may run `business_record_changes.py authorize`,
  `apply`, or `reconcile`, or call the equivalent remote API routes under
  `records:authorize` / `records:apply`. Those routes are not MCP tools. Never
  ask a model to supply the signing secret or target credential, and never
  expose those values in YAML, prompts, or logs.
- There is no hard-delete path. Corrections are append-only events. A target
  application never edits the immutable served snapshot.

## Propose

Discover the exact writable object schema and use only configured fields. For
an amendment, retrieve the current record and bind `expected_record_sha256` so
a changed snapshot or concurrent edit fails closed. Distinguish a source-backed
assertion from a user's assertion; source-backed requests require exact document,
page, field, and visible-evidence references. Use a fresh idempotency key for
changed content and preserve all rejected attempts.

Preview before authorization. Confirm the tenant/configuration hash, snapshot
record hash, before/after values, target mapping, clearing semantics, and all
downstream controls. A proposal or preview is never an approval.

## Authorize, apply, and reconcile

Use executable `--help` and the client YAML. The approver must be a real
authorized identity and, when configured, different from the submitter.
Authorization is signed, preview-bound, time-limited, and retained in the
private append-only journal. Apply only through the configured file or HTTPS
JSON adapter; live HTTP requires explicit `--execute`.

After any ambiguous target response, reconcile before retrying. Compare every
expected target field and retain mismatches as blocked. After successful target
reconciliation, construct and approve a new canonical export and retrieval
snapshot before claiming that MCP/API reads include the change. Stop on a stale
preview, expired/invalid authorization, ownership/config mismatch, unknown
field, partial target result, reconciliation mismatch, or missing approval.
