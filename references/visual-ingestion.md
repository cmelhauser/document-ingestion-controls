# Schema-guided visual record intake

## What this extension does

The optional intake extension lets a connected vision-capable assistant read a
retained photograph or page image, discover the extraction schema, and submit
source-cited candidate records. The same eleven operations are available as MCP
tools and an authenticated JSON application API. This is an implemented,
bounded **proposal-intake pilot**, not a canonical record-write service.

For example, an operator can retain a photograph of an invoice, ask the
assistant to identify the invoice number and visible amounts, and retrieve the
resulting proposal and its validation findings. A contact card can preserve a
name, address, phone number, and unfamiliar labels without inventing a supported
document classification. It uses `unknown` when the extraction vocabulary does
not have that document family. Unfamiliar fields belong in `unmapped_fields`,
not arbitrary new canonical columns.

The assistant doing the reading is the client's connected LLM. The server does
not call a provider, ask the client to perform sampling, or need an additional
model API key. Receiving image content is not proof that a particular client
can interpret it: test the selected model and interface before real intake.

## Trust and workflow boundaries

The sequence is: retain original bytes, discover the schema, read every page,
submit a proposal, inspect the retained receipt. The result stops at
`pending_review` or `rejected`. Neither state enters approved retrieval, reports,
CRM import files, or target CRM delivery.

Every receipt states `proposal_only: true`, `canonical_write_permitted: false`,
`requires_independent_verification: true`, `requires_authorization: true`, and
`published: false`. Schema validation checks structure and source references;
it does **not** prove that text or geometry matches the picture, establish
independent consensus, validate arithmetic, resolve entities, or grant approval.
An assistant's model name is self-reported provenance, not an authenticated
provider identity or independent vote. A source page containing instructions
must be treated as data, never as permission to change a record or call a tool.

This journal is a pre-pipeline source boundary, separate from a contained
pipeline run and separate from the immutable retrieval snapshot. The local
`visual_ingestion_export.py` utility now provides a hash-bound **source-only**
handoff, described below. It preserves the whole session and prepares an image
PDF for the existing profiling/intake commands. It never turns a host-LLM
proposal into an extraction handoff, independent vote, approval or canonical
record. The remaining stages are in the [development roadmap](business-data-platform-roadmap.md).

The current MCP servers still require an approved retrieval snapshot positional
argument, even for an intake-only OAuth user. A standalone intake deployment is
not implemented. Do not use an unfinished client run as that snapshot or create
an empty passing substitute. Deployment authorization and the existing
[activation gate](mcp-production-integration.md) remain required. Obtain separate
authorization for the new source set and connected model/provider data flow
before transmitting client images. Testing this code did not authorize either.

## Setup and configuration

Intake is disabled unless the operator supplies `--ingestion-dir`. There is no
new environment setting. Keep the directory outside the repository, outside the
approved snapshot directory, and separate from any active run. The service
refuses CLI startup if the approved snapshot is inside the proposed intake root,
before opening or creating the journal, including a filename collision. It
creates its final directory with mode `0700` and `intake.sqlite` with `0600`;
existing paths with group/other permissions are refused, as are a symlink at
either of those paths. Parent directories must also be controlled by the trusted
operator. These checks are not a substitute for host isolation or encryption.

Local stdio uses the direct Python entry point, not the read-only shell
launcher. After the activation and intake authorization gates, with the project
environment active:

```bash
python scripts/retrieval_mcp.py /APPROVED/SNAPSHOT/retrieval.sqlite \
  --ingestion-dir /PRIVATE/INTAKE/client-a --ingestion-tenant client-a
```

Replace both absolute paths and the tenant label. The executable and repository
path in the client's stdio registration must also be absolute. Local calls use
the fixed owner `local-operator`; this is a trusted single-user process, not a
multi-user authorization boundary. `--ingestion-tenant` defaults to `local` and
binds the journal permanently. Omit `--ingestion-dir` to retain the original
twelve-tool read-only surface. Enabled local intake exposes twenty-three tools.

For remote deployment, follow the full TLS/OAuth procedure in
[MCP Production Integration](mcp-production-integration.md), adding
`--ingestion-dir /PRIVATE/INTAKE/client-a` to that complete invocation. It binds
the journal to the existing required `--tenant`; there is no separate remote
`--ingestion-tenant` flag. A mismatch refuses startup. The same deployment's
introspection issuer, exact audience/resource, tenant claim, allowed roles,
Origin allowlist, rate limit, and audit configuration protect both interfaces.
The audience remains the configured HTTPS `/mcp` resource for API calls too;
do not obtain tokens for a different audience.

Grant `ingestion:read` for schema, session discovery, retained pages, statuses,
review summaries, reviewer listings, and proposals and `ingestion:submit` for
session creation, page upload, proposal submission, and additive reviewer grants.
A normal intake workflow needs both. Neither grants `crm:read` or `crm:export`.
The authenticated OAuth subject owns a session; a different subject cannot
inspect or modify it unless the owner explicitly grants read-only reviewer
access. Owner transfer and administrator review tools are not implemented. The
remote server offers twenty-five tools when enabled and all scopes are granted,
versus fourteen by default. Enabled discovery filters tools to the caller's
scopes; an intake-only reader sees seven tools, while a caller with both intake
scopes sees eleven.

`--max-request-bytes` defaults to **1,000,000** for the entire JSON request,
including a base64 image and MCP envelope. A maximum-sized 10,000,000-byte image
requires at least **14,000,000** request bytes; set the flag explicitly and align
proxy/client limits when that size is needed. The default therefore supports
only smaller page uploads. The API and MCP share the per-address rate limit
(default 60 requests per rolling minute). Keep payload and connection limits at
the reverse proxy; this Python server is not a hardened public upload platform.
It does not implement browser CORS preflight: use an authorized same-origin
backend integration, not an assumed cross-origin browser uploader.

## Exact operation contracts

Remote API routes are `POST /api/ingestion/OPERATION` on the same HTTPS origin.
Send a bearer token, `Content-Type: application/json`, and a known Content-Length.
The body is the argument object, **not** JSON-RPC. MCP uses `tools/call` with
the same operation name and arguments. No extra argument keys are accepted.
All keys listed below are required; identifiers are nonblank strings of at most
200 characters. Use UUIDs as request keys generated by the client application.

| Operation | Scope | Arguments | Result |
|---|---|---|---|
| `get_ingestion_schema` | `ingestion:read` | `{}` | Schema version/hash, complete proposal JSON Schema, supported media, limits and boundaries |
| `create_ingestion_session` | `ingestion:submit` | `idempotency_key`, `expected_pages` | New or replayed `session_id`; expected page count |
| `upload_ingestion_page` | `ingestion:submit` | `session_id`, `idempotency_key`, `page_number`, `mime_type`, `data_base64` | Source ID, byte count, SHA-256, `retained`/`rejected`, findings |
| `get_ingestion_page` | `ingestion:read` | `session_id`, `page_number` | Retained original image and its receipt |
| `submit_record_proposal` | `ingestion:submit` | `session_id`, `idempotency_key`, `proposal` | Proposal ID/hash, record count, validation findings and remaining controls |
| `get_ingestion_status` | `ingestion:read` | `session_id` | Pages, all upload attempts, source-set hash and proposal receipt summaries |
| `list_ingestion_sessions` | `ingestion:read` | `{}` | Accessible owned/reviewer sessions with status, counts, and last activity |
| `grant_ingestion_session_reviewer` | `ingestion:submit` | `session_id`, `idempotency_key`, `reviewer_subject` | Additive read-only reviewer grant receipt for one session |
| `list_ingestion_session_reviewers` | `ingestion:read` | `session_id` | Owner-only active reviewer grants for one session |
| `get_ingestion_review_summary` | `ingestion:read` | `session_id` | Structured retained-page and proposal-version comparison for one accessible session |
| `get_record_proposal` | `ingestion:read` | `session_id`, `proposal_id` | Exact original proposal and full validation receipt |

MCP image retrieval returns actual ImageContent alongside metadata; it does not
place base64 into the text result. The JSON API returns the base64 in `data`.
Other MCP results use `structuredContent.result`; API results are plain objects.
Validation rejection is a successfully retained result, so inspect `status` and
`findings`, not just HTTP 200. Malformed arguments, missing ownership, conflicts,
or storage refusals return API 400 or an MCP error; unauthorized/insufficient
scope returns 401/403, limits 413/429, and disabled or unknown API routes 404.
Malformed JSON receives a parse-error envelope. Transport failures do not prove
that nothing was committed: retry the exact input with its original key.

For a fictional one-page intake, the create-session API body is:

```json
{"idempotency_key":"fictional-demo-001","expected_pages":1}
```

Keep the returned session ID. The uploading application reads the actual file,
encodes its bytes, and sends them with page number 1 and `image/png` or
`image/jpeg`. **Do not ask a language model to type or reconstruct base64.** A
chat attachment is not automatically accessible to an MCP server. If the
selected native client cannot transfer the bytes through a supported mechanism,
stop and use an operator-controlled API integration. The thin same-origin
browser review page at `/ingestion/review` can create sessions, upload actual
image bytes, grant reviewers, submit proposal JSON, preview retained pages, and
load review summaries against the authenticated API. A packaged mobile capture
app and attachment relay are not yet shipped.

Once every declared page is retained, get status and the schema, ask the model
to inspect each page via `get_ingestion_page`, and populate exactly the returned
schema. Submit using that status's exact `source_set_sha256`. Retrieve the
proposal by its receipt ID to check that the original observations and every
finding were retained. For a ChatGPT-connected workflow, keep the session ID
and any reviewer subject outside the prompt when possible, then use
`list_ingestion_sessions` and `get_ingestion_review_summary` to re-open the
retained work without replaying source bytes into the conversation.

## Proposal and observation schema

`visual_ingestion_v1` is the version. The discovery response's `schema_sha256`
identifies the current schema definition; clients should retain it and refresh
discovery on upgrade. Submissions pin `schema_version` and the exact source-set
hash. Changes to incompatible validation semantics require a version migration,
not silently reopening old proposals under a different contract.

The top level requires `schema_version`, `source_set_sha256`,
`client: {name, model}`, `records`, and `page_exceptions`. The schema reuses the
repository's controlled extraction header fields, line fields and document
types rather than exposing SQL or permitting arbitrary canonical writes.

Each record requires `record_id`, `document_type`, nonempty unique `page_numbers`,
`header`, `lines`, `unmapped_fields`, and `issues`. Record IDs are unique within a
proposal, not global CRM IDs. A header maps allowed field names to observations.
Each line has a unique `line_number` and nonempty `fields`. An unmapped item has
`source_label` and `observation`. Issues are explicit strings. A record must
contain an observation; an unreadable page belongs in `page_exceptions` with its
page number and reason. Every declared page must occur in a structurally valid
record or explicit exception. Zero extracted records never passes validation.

Every observation requires `value` (nonblank text or null), `raw_text` (nonblank
original reading), `source` (`printed` or `handwritten`), `page_number` and `box`.
The field's page must belong to its record. A box contains normalized finite
`[left, top, right, bottom]` coordinates with positive width/height, from the
original encoded pixel orientation, not a silently EXIF-rotated display. Verify
orientation in the client; retain an explicit issue if alignment is uncertain.
Keep ambiguous dates, currency labels, units and numbers as source strings;
do not infer locale, convert missing to zero, or mark a guess as validated.
Omit genuinely absent fields. Use null and an issue for an unresolved reading.

Example fictional observation:

```json
{"value":"INV-42","raw_text":"INV-42","source":"printed","page_number":1,"box":[0.1,0.1,0.4,0.2]}
```

Those example coordinates are illustrative, not evidence. Actual geometry must
be derived from the actual retained page. Contract validation cannot establish
its accuracy. User-supplied `approved`, control-clearance or publication fields
are forbidden; even a structurally valid proposal can contain handwriting,
unmapped labels and unresolved issues and remains review-required.

## Limits, durability and recovery

| Limit | Implemented value |
|---|---|
| Source pages per session | 1–20, numbered 1 through `expected_pages` |
| Original image | PNG/JPEG, single-frame, at most 10,000,000 decoded bytes and 25,000,000 pixels |
| Proposal | At most 1,000,000 canonical JSON bytes, finite JSON only |
| Records / lines / unmapped fields / issues | 100 per proposal / 200 per record / 100 per record / 100 per record |
| Observation value and raw text | At most 4,000 characters each |
| Issue or page-exception reason | At most 1,000 characters |
| Journal | 10,000 entries, 250,000,000 accounted payload/result/image bytes; conservative 1 MB result reservation per insert |
| Session activity | 100 page/proposal entries; rejected attempts count |

These are code bounds, not deployment tuning flags. Filesystem size also includes
SQLite overhead; provision disk capacity and monitoring beyond the accounted
quota. Fixed store bounds are shared by all users in that tenant deployment,
not fair per-user quotas. This is intentionally a bounded pilot.

PDF, HEIC, WebP, animated images and direct URLs are not supported input types.
For PDFs or other originals, retain the original container externally and use
the existing authorized page-preparation workflow; do not claim the image
journal has retained an original PDF merely because it holds a render.
Within bounds, unsupported or corrupt image bytes are retained with a rejected
attempt. A successful page slot cannot be overwritten. A rejected slot may be
retried under a new key. Replacing a retained page requires a new session with
the full corrected source set, while the original session remains intact.
Malformed/oversized envelopes and exhausted quotas are refused before acceptance;
the sending application must retain its originals and failed request receipts.

Idempotency is scoped by authenticated owner, session, operation kind, and key
(session creation has no existing session). An exact replay returns the original
receipt, including across API/MCP or process restart; the same key with different
input refuses. Corrected proposals use a new key and are appended, never edited.
The session status shows the latest proposal status only; retrieve earlier
proposal IDs to inspect their findings. A later valid submission does not erase
an earlier rejection, page exception or issue and never clears a control.

SQLite transactions serialize writes; original bytes and receipts commit
together. Application triggers refuse UPDATE/DELETE; entry hashes detect
accidental or uncoordinated modification when entries are read. This is not an
external signature, privileged-administrator tamper proofing, per-field
encryption, or a replicated disaster-recovery system. A trusted host operator
could modify the database and hashes. Restrict OS access, disk encryption and
backups according to the client policy. Stop before maintenance, retain a
consistent private SQLite backup, restore to an isolated directory, verify
tenant and sample image/proposal hashes, and test exact-key replay before use.
Do not delete rejected records or prune entries to bypass the quotas. A rotation,
retention or tenant migration requires a separately designed operator procedure.

Remote audit logs contain operation metadata and outcomes, not source image or
proposal content. A successful HTTP/MCP request is not semantic acceptance.
Inspect the retained receipt for validation disposition. On quota, corruption,
or storage errors, stop intake and preserve original inputs and receipts; never
silently reroute to canonical storage or a new unlinked journal.

## Source-only handoff into a controlled run

`visual_ingestion_export.py` is a trusted **local operator utility**, not an MCP
tool and not a remotely selectable filesystem write. It opens an existing
journal in SQLite read-only mode, checks the tenant and exact owner/session,
and captures all entries in one consistent read transaction. A missing journal
is refused, never created. Later uploads/proposals are not silently added to an
already issued package: use a new export directory for a later snapshot.

With the project environment active, inspect help before operation:

```bash
python scripts/visual_ingestion_export.py --help
python scripts/visual_ingestion_export.py export --help
python scripts/visual_ingestion_export.py verify --help
```

First initialize a fresh run through `run_workspace.py init`. Run export through
`run_workspace.py run`, using absolute paths for the interpreter and script
because the wrapper changes the working directory to the run. Its child command
is equivalent to this direct invocation:

```bash
python scripts/visual_ingestion_export.py export /PRIVATE/INTAKE/client-a \
  --tenant client-a --owner AUTHENTICATED_SUBJECT --session-id SESSION_UUID \
  --out-dir /NEW/RUN/source-package
```

Every dynamic input is explicit:

| Input | Meaning |
|---|---|
| `directory` after `export` | Existing private journal directory; external read-only input |
| `--tenant` | Exact tenant bound at journal creation; required, no default |
| `--owner` | Exact OAuth subject, or `local-operator` for stdio sessions; required. Local OS access grants operator access; this flag is a selector, not remote authentication. |
| `--session-id` | Exact retained session receipt ID; required |
| `--out-dir` | New package directory inside the new run; must not exist, even empty, and must not overlap the journal directory |
| `directory` after `verify` | Retained package directory; verification performs no writes |
| `--manifest-sha256` | SHA-256 from the export receipt, retained separately; required. Do not recompute it from an untrusted received manifest and call that verification. |

The existing `BUSINESS_DOC_RUN_ROOT` set by the wrapper enforces output
containment; no new environment setting or provider call is introduced. The
package root is `0700` and files `0600`. Keep its parent directories under the
same operator-controlled retention policy.

The package contains:

- `originals/*.bin`: exact original bytes of **every** retained upload attempt,
  including unsupported/corrupt images and duplicate/out-of-range pages. MIME
  types, page numbers, byte hashes and dispositions remain in the receipts.
- `journal.json`: tenant/owner, captured session status, original canonical JSON
  strings for every request payload and receipt, entry IDs, request keys/hashes,
  integrity hashes, timestamps, and relative original-byte paths. Every proposal
  version and its issues/unknown labels/page exceptions remain intact. JSON
  formatting is the journal's canonical representation, not the original HTTP
  whitespace. This file contains sensitive content and is not a public log.
- `exceptions.json`: the existing final-review `exceptions[]` contract. It
  includes every rejected upload, every proposal as unapproved review work,
  every missing page, preparation failures, and a source-verification item even
  when no proposal exists. Include this artifact in the final-review command
  alongside all later pipeline findings; retaining it without supplying it does
  not make the final queue exhaustive.
- `visual_<session-identity-hash>.pdf`: only when every declared page is usable
  and derivative preparation succeeds. Its stable tenant/owner/session-derived
  filename prevents page-ID collisions between sessions. Do not rename it before
  intake. `manifest.json` names the exact file in `source_pdf` and binds each
  original source ID/hash/page number to its expected `ingest_pages.py` page ID.
- `manifest.json`: written last as the completion marker, binding all file paths,
  sizes and SHA-256 hashes, the source-set checksum, all entry/page/proposal
  counts, transformation details, page mapping and proposal-only boundary. Its
  content checksum and the separate receipt's manifest checksum are different,
  explicitly named values.

The derivative preserves encoded pixel orientation and page order. It performs
no EXIF rotation, crop, resize, OCR, inference or grouping. Pillow converts
display pixels to RGB, composites transparency over white, and does not apply
ICC color correction. PDF image streams are losslessly compressed; original
JPEG/PNG bytes remain separately authoritative. The 300-DPI PDF layout is a
display convention, **not evidence of physical dimensions or original scan
resolution**. The page map records width, height and decoded RGB pixel hashes.
Very unusual dimensions may be refused by the PDF library and retained as a
preparation failure. Inspect orientation, colors, transparency and small print
visually before using the derivative; retain any uncertainty for review.

Preparation is bounded to 50,000,000 total decoded pixels per session to limit
working memory, in addition to all existing intake bounds. Verification limits
the manifest to 1,000,000 bytes and each file to 250,000,000 bytes, checks all
listed files, rejects path traversal, symlinks, missing/unlisted files and
checksum changes, and rechecks every journal entry's integrity. No hash is an
external signature or protection against a privileged actor rewriting both a
package and the independently retained receipt.

An export exits **0** only for `source_prepared_review_required`; this means a
source derivative is available, not that a business gate passed. Missing pages
or derivative failures produce `blocked_source_preparation`, retain the archive
and exception artifact, set `source_pdf: null`, and exit **2**. A partial PDF may
be retained as a hashed failed artifact but must never be used. Invalid scope,
corruption or filesystem errors exit **1**. An interruption before the final
manifest leaves an incomplete private directory; preserve it and retry into a
new directory. There is no overwrite, resume or force flag.

After preserving the export receipt separately, verify:

```bash
python scripts/visual_ingestion_export.py verify /NEW/RUN/source-package \
  --manifest-sha256 RETAINED_RECEIPT_SHA256
```

A blocked archive can verify as byte-intact while still exiting 2. Only a
verified package whose `source_pdf` is non-null may proceed to source profiling.
Use the actual named derivative with `scan_profile.py`, read its findings, then
use the unchanged `ingest_pages.py` to produce the real intake manifest in a
**new** run subdirectory. All writing commands still go through the workspace
wrapper. Keep the package beside those artifacts; the generated intake manifest
hashes the derivative PDF, while the source package binds that derivative back
to the exact original image bytes. Include both in the operations manifest and
preserve this chain in any authorized archival/delivery selection.

Continue the existing phase order: preprocessing, classification/reassembly,
independent extraction, applicable mapping and business controls, and exhaustive
final review. The source-only handoff does not load host-LLM readings into
consensus, auto-approve mappings, apply amendments, or refresh retrieval. Those
readings stay review context requiring independent evidence and explicit
authorization. No production data transmission is authorized by exporting.

## Acceptance before client use

Run the repository quality gate and the existing fictional MCP production
acceptance. The intake unit/integration tests additionally cover schema mapping,
invalid proposals, source completeness, image limits, exact retries, concurrent
replay, journal restart and tampering, ownership, scopes, transport parity and
MCP image content. The source-handoff tests additionally verify exact original
and rejected bytes, read-only snapshots, ordered PDF pixel content, real intake
manifest production, explicit final-review items, concurrent/no-clobber exports,
incomplete sources, preparation failures and tampered-package refusals.
Measured coverage is code coverage, not evidence of complete
real-world extraction accuracy or mobile compatibility.

Then, under separate deployment authorization, test the exact TLS/proxy/OAuth
environment with fictional source pages. Exercise missing/wrong scope, tenant,
role and owner; actual maximum payload handling; rejected pages/proposals;
interrupted uploads; replay after restart; backup/restore; token revocation; and
no source-bearing logs. In each intended desktop/mobile client verify schema
visibility, real-byte transfer, image rendering, the selected model's vision,
all eleven operations, and visible pending-review boundaries. Record versions,
source hashes, expected readings, errors and results. No automated test in this
repository currently proves native iOS capture, a client-provider connection,
approved publication, or target-CRM mutation.
