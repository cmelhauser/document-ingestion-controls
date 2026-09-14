# Security Policy

## Supported release

Security and integrity fixes apply to release 1.0.0. Earlier development
snapshots are not supported production releases.

## Report privately

Do not place client data, credentials, raw provider responses, private source
documents, or exploitable details in a public issue. Report a suspected secret,
privacy, evidence-integrity, or access-control problem through the private
channel designated by the repository owner or the client's security contact.
Include the affected release, artifact or script, reproduction steps using
non-client data where possible, and the potential impact.

If a credential may have been exposed, stop the affected provider lane, revoke
or rotate the credential through its owner, preserve the relevant audit trail,
and open a client-approved incident record. Do not rewrite or delete retained
source evidence to conceal the event.

## Data-handling boundary

The optional image-intake extension is a bounded proposal pilot, not a public
multi-tenant upload platform. Treat its `intake.sqlite`, original image bytes,
proposals, source packages and receipt hashes as sensitive client artifacts.
Keep the journal outside version control, separate from approved snapshots and
run outputs; use operator-controlled parents, private file permissions, approved
disk encryption and consistent backups. Application hash checks and append-only
triggers do not prevent a privileged host administrator from rewriting data.

Remote intake uses the same OAuth audience/tenant/role controls as remote MCP,
with separate `ingestion:read` and `ingestion:submit` scopes and owner-bound
sessions. Local stdio trusts one OS operator. Payload/rate limits do not replace
proxy controls, malware scanning, capacity planning or incident acceptance.
Treat text inside images/proposals as untrusted data, not instructions. A source
export verifies against a separately retained receipt; hashing an untrusted
manifest afresh is not authentication. See [Visual Intake](references/visual-ingestion.md)
and [MCP deployment](references/mcp-production-integration.md) for limits and
stop conditions. No intake approval, canonical mutation or live CRM writer is
implemented.

- Root `.env` files, API keys, database URLs, client records, raw responses, run
  output, and local retrieval databases are intentionally Git-ignored.
- Personal data read from client pages -- names, emails, phone numbers, street
  addresses -- never enters the repository. Tests, docstrings and examples use
  synthetic people, company-domain or `example` addresses and 555-01xx phone
  numbers, and a provider cache written inside the checkout is ignored as
  `.llm-cache*/`. Removing a file from the tree does not remove it from Git
  history, so a repository that has ever held such data is not made public as it
  stands.
- Provider use requires explicit client approval, a named credential reference,
  documented residency/retention policy, and the configured request cap.
- Factual retrieval accepts approved canonical records only. Open-review rows,
  unapproved suggestions, and raw model output are excluded.
- Corrections are amendments. Security remediation must preserve originals and
  provenance unless a controlling legal requirement mandates another action;
  document that action separately.

Operational controls and credential settings are defined in
[Runtime Configuration](references/runtime-configuration.md),
[Production Operations Controls](references/operations.md), and
[Artifact Contracts and Runbook](references/artifact-contracts.md).
