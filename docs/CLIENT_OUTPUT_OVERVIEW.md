---
title: "Business Document Ingestion"
subtitle: "Client Output, Reporting, and CRM Access Overview"
author: "Christopher Melhauser and theonlymuffinbot"
license: "The Unlicense"
credit: "Christopher Melhauser; theonlymuffinbot"
date: "September 3, 2026"
version: "1.0.0"
---

# Client Output, Reporting, and CRM Access Overview

## Read this first

This guide explains what a completed document-processing engagement can give
you, what each output is for, and how approved data can later be searched,
reported on, exported, or prepared for a CRM. It is written for business users;
you do not need to know the internal processing steps to use the results.

This edition also covers the optional visual-intake pilot and source-only
handoff implemented in this checkout. It does not claim a deployed client
service or a completed merge. The stable release remains 1.0.0; current
repository and verification state is recorded separately in `HANDOFF.md`.

The important distinction is simple:

| If the final review is... | You receive... | What it means |
|---|---|---|
| **Open or blocked** | A review package and an exception register | Questions still need an answer. The data is useful for review, but it is not released as approved operational facts. |
| **Clear** | An approved data package, reporting/retrieval snapshot, and optional CRM-ready files | Every included fact is provenance-linked, review-clear, and tied to the exact source snapshot. |

The system does not hide an unresolved item to make a spreadsheet look clean.
It records the question, reason, source, and next step. A completed pipeline is
therefore more than a collection of extracted rows: it is a traceable answer to
what was found, what was proven, and what still needs a decision.

## The end product at a glance

A client delivery is organized into five practical layers. Depending on the
engagement and final gate, some layers may be delivered as blocked findings
rather than approved data. That distinction is visible in the package.

1. **Source and evidence layer.** Original documents, page references, hashes,
   and citations that let a user trace an important value back to its source.
2. **Review and control layer.** The review queue, validation, arithmetic,
   attribution, completeness, and exception results. This explains what was
   checked and what was not proven.
3. **Approved data layer.** A canonical JSON export containing only
   review-clear, provenance-linked facts, plus an exclusion artifact for every
   withheld record or field.
4. **Reporting and retrieval layer.** A read-only, immutable data snapshot that
   supports governed search, record lookup, account/contact cards, analysis,
   standard reports, and bounded exports.
5. **CRM handoff layer.** Checksummed no-send staging files and optional common
   CRM-import files. These prepare a controlled load; they do not upload,
   overwrite, or authorize a live CRM change.

The package is versioned by the source-export hash and supporting checksums.
If an authorized correction changes a fact, a new snapshot is built. The old
snapshot remains available for audit; it is never edited in place.

## What the files look like

The exact folder names may be tailored to the engagement, but the contents have
the following roles.

| Output | Typical form | What you use it for |
|---|---|---|
| Final review package | JSON, CSV, XLSX, and HTML | Review open questions, filter by priority or document, and return controlled decisions. |
| Control results | JSON and human-readable summaries | Understand arithmetic, validation, attribution, completeness, sampling, and final-gate results. |
| Exception register | JSON and reviewer views | See every withheld record, missing input, failed check, or unresolved value with its reason. |
| Canonical export | Versioned JSON plus exclusion artifact | The authoritative machine-readable approved-fact handoff. It is the source for downstream loading and retrieval. |
| Load plan | Versioned, checksummed JSON | See the dependency order, record counts, keys, and integrity checks required before loading another system. |
| Retrieval snapshot | Immutable SQLite database | The local, read-only source used by the MCP and REST API. It contains approved facts only. |
| CRM staging package | Ordered CSV files, API envelope, and manifest | Give a target-system team a full, reconciled, no-send handoff across all canonical tables. |
| Common CRM import package | Twelve ordered CSV files, mapping template, source catalog, and write plan | Start target-CRM mapping and sandbox testing using familiar business objects. |
| Optional source-intake package | Private original images, proposal history, exceptions, manifest, and a derived PDF when complete | Hand new source pages to the processing team. This is an evidence archive, not an approved CRM import or a report. |

### The Excel files

Excel is used as a convenient review or handoff view, not as a place where
facts silently change.

- The **final review workbook** is for answering open questions. Its issued
  copy and returned copy are preserved separately. Returned decisions are
  proposals until an operator reviews the exact impact and reruns affected
  controls.
- The **schema and CRM capability workbook** is an orientation aid. It explains
  tables, fields, API/MCP capabilities, sample report shapes, and import-file
  structure. It does not contain hidden authority to load a CRM or change an
  approved fact.
- The **CRM import mapping template** is where a target-system owner completes
  vendor- and tenant-specific object and field mapping. Its included suggestions
  are starting points, not approved target mappings.

## What "approved data" means

An approved row is not simply a row that an AI model read. Before it reaches the
canonical export or the retrieval snapshot, the system requires applicable
controls to be satisfied separately:

- source and page provenance are retained;
- the record has a review-clear status;
- applicable arithmetic is proved or explicitly not applicable;
- the exhaustive final review queue has no open item for that record; and
- every relevant attribution, completeness, and approval condition is recorded.

If a source, ledger export, payment export, mapping decision, or review answer
is unavailable, the package says so. It does not manufacture a substitute
fact. This is why a delivery can be valuable even when it is not ready for a
CRM load: it makes the outstanding business decisions visible and bounded.

## What an MCP is

**MCP** stands for **Model Context Protocol**. In plain language, it is a
controlled connection that lets an AI assistant use approved business tools.
Instead of pasting spreadsheets into a chat, the assistant can ask the
approved retrieval snapshot specific, bounded questions and show you the
result.

Think of it as a librarian with a fixed catalog:

1. You ask your assistant a business question in normal language.
2. The assistant selects an approved tool, such as search, exact lookup, sales
   analysis, or a standard report.
3. The tool reads one immutable, approved-fact snapshot.
4. The result includes source-export identity, checksums, and relevant source
   document IDs so it can be checked.

The approved-data MCP does **not** let the assistant run arbitrary SQL, browse raw AI
responses, invent records, bypass the review gate, or write to a CRM. Tool
results do become part of the conversation context, so the client must approve
the selected AI provider's privacy, retention, residency, and user-access
settings before connecting real data.

## How you can connect

Choose the connection that fits how your team will work. The same approved
snapshot and semantic rules are used by every option; the transport changes,
not the facts or calculations.

| Your need | Connection | Suitable clients | What is required |
|---|---|---|---|
| A quick, private desktop pilot | **Local stdio MCP** | Claude Code, Claude Desktop, and compatible local desktop clients | The completed run's local immutable snapshot and a private local registration. This is the recommended first connection. |
| A controlled internal application or BI tool | **TLS/bearer REST API** | An approved internal application or BI consumer | Explicit enablement, TLS, dedicated rotated bearer secret, network controls, monitoring, and client acceptance. This is an API, not an MCP connector. |
| Web, iOS, or organization workspace use | **Remote Streamable HTTP MCP** at a public `/mcp` URL | Claude web/desktop/mobile after connector setup; selected ChatGPT workspace/API surfaces | Public HTTPS, OAuth/OIDC authorization, exact tenant/role/scope rules, approved workspace registration, monitoring, and live acceptance. |

### Local desktop connection: the simplest first step

After the engagement reaches the approved-data stage, the operator registers a
private local MCP configuration that points to the exact local snapshot. In
Claude Code, this is normally a local stdio registration; Claude Desktop and
other compatible desktop clients use the same launcher through their local MCP
configuration. The database stays on the workstation, and the server is
read-only.

This is the right starting point when one approved team is working from the
machine holding the completed snapshot. It does not make the service public or
available on a phone.

### Remote connection: needed for mobile and web

Claude mobile, claude.ai, and selected ChatGPT web, desktop, or mobile
experiences require a separately deployed remote MCP service. The repository
already supplies the remote approved-data server, but a real client deployment still
needs:

- one public HTTPS URL ending exactly in `/mcp`;
- a trusted TLS certificate and reverse proxy;
- a real OAuth/OIDC authorization service with token introspection;
- a specific tenant claim, approved roles, and `crm:read`/`crm:export` scopes;
- approved logging, alerting, retention, backup, export cleanup, and incident
  response procedures; and
- connector registration and representative acceptance tests in the selected
  Claude and/or ChatGPT workspace.

Mobile users normally use an already configured connector; they do not create
the deployment from the mobile app. Product-plan and workspace availability can
change, so the chosen provider surface is tested during the deployment
acceptance rather than assumed from protocol compatibility.

## What you can ask for

The system is designed for normal business questions. The assistant chooses a
bounded tool and returns a small, checkable result; it does not send an entire
large report into a chat conversation.

### Find a record when you do not know its key

Use search for a company name, invoice number, address fragment, acknowledgement
number, job, project, or other source-visible phrase.

Examples:

- "Find documents and records related to Acme Industrial."
- "Search for invoice INV-1042 and show the approved source citation."
- "Find the address containing 125 Harbor Drive."
- "Which records mention ACK-7781 or job 44-19?"

The MCP uses `search_crm_records` for this cross-table discovery. Once the
right row is known, `get_crm_record` retrieves the exact canonical record by
table and key.

### Pull an account, address, or contact card

`get_account_card` is the preferred way to ask for a business-ready view of a
company or party. It joins approved party names and roles with addresses,
contacts, invoice/payment activity, related source documents, and summary
figures.

Examples:

- "Show the contact card for Acme Industrial, including addresses and contacts."
- "Give me the approved address and invoice history for customer CUST-104."
- "Which source documents support this vendor's contact information?"

Money is separated by currency in account cards. The service will not add US
dollars, Canadian dollars, euros, or other currencies into one misleading total.

### Slice and aggregate sales safely

`analyze_sales` is the governed analysis tool. It can group up to three business
dimensions, including company/customer, vendor, region, selling location,
month, quarter, year, currency, acknowledgement number, and job number. It can
also filter by date, those same dimensions, and invoice-value bounds.

Examples:

- "Show sales by company and region for Q2, separated by currency."
- "Compare sales by selling location for this year."
- "Break down customer sales for job J-204 by month."
- "Which acknowledgement numbers contributed to Midwest region sales?"

Currency is always treated as a grouping dimension, even if it is not mentioned
in the question. That prevents unlike currencies from being added together.

### Query one approved table

`query_crm_records` is for a focused, bounded slice of one table. It supports
approved filters, selected fields, stable sorting, and pagination. It does not
execute formulas, code, or arbitrary SQL.

Examples:

- "List approved invoices for Acme Industrial with invoice number, date, amount
  due, and source document ID."
- "Show the next 50 approved shipment records, using the available schema fields."
- "Find addresses whose city starts with 'New', then show the account cards for
  those parties."

The last example needs more than one tool call: the one-table query does not
perform arbitrary joins from contacts to addresses. The assistant must discover
available fields and keys before choosing filters, not invent columns.

For a larger reconciliation, `export_crm_table` returns checksummed pages rather
than an unbounded conversation response.

## Standard reports available out of the box

The retrieval layer supplies seven deterministic reports over approved facts.
Each report is bound to the snapshot hash and includes source document IDs for
its supporting data.

| Report | Typical use |
|---|---|
| `account_directory` | List approved accounts/parties with key contact and address information. |
| `invoice_register` | Review invoice-level amounts, dates, parties, and source citations. |
| `receivables_aging` | Review the current approved balance snapshot by aging bucket as of a required date. It is not a reconstructed historical ledger. |
| `sales_by_customer` | Compare approved sales by customer, with currencies kept separate. |
| `sales_by_selling_location` | Compare approved sales by sales office, branch, or selling location. |
| `attribution_coverage` | See which approved invoice values have a supported attribution and which do not. |
| `shipment_performance` | Review approved shipment activity and related operational measures available in the snapshot. |

You can ask an assistant for these in ordinary language: "Run the invoice
register for April," "show sales by customer," or "run receivables aging as of
June 30." The assistant maps the request to the report and must preserve its
parameters and snapshot identity in the result.

## Downloads and large exports

Chat is useful for concise answers, not for sending thousands of rows into a
model conversation. The remote MCP therefore supports controlled CSV and XLSX
export jobs.

An authorized user can request one approved table or one standard report in CSV
or XLSX form. The server creates a bounded, short-lived download with:

- the snapshot and source-export hash used at the start;
- the requester identity in hashed form;
- the exact table/report and filters requested;
- row, column, and file checksums; and
- an expiry time and owner-bound status lookup.

Spreadsheet-formula-like text is neutralized in exports. A different user
cannot inspect another user's job, a changed file fails verification, and an
expired link is refused. These exports create a read-only report artifact; they
never update canonical data or write to a CRM.

## CRM inputs: what is ready and what still needs a decision

### Adding photographs and proposed records first

The optional intake pilot can retain original PNG/JPEG page images and let the
connected, vision-capable assistant read them using the pipeline's published
field dictionary. It can preserve an invoice reading or contact-card observation
without changing the approved business database. Unknown document families and
unfamiliar labels stay explicit, not forced into made-up fields.

1. Agree which images and AI provider may receive the data; the operator enables
   intake separately from approved reporting.
2. A compatible application uploads the actual original bytes to a named
   session. Keep its receipt outside the chat. A chat attachment alone does not
   prove the service received it.
3. The assistant discovers the schema, reads every retained image, and proposes
   source-visible fields with original readings, page references and uncertainty.
4. The service checks the structure and returns **pending review** or
   **rejected**, retaining the original and every finding. Neither status is
   verification of the reading or permission to publish it.
5. Corrections append another proposal. An uncertain network retry uses the
   same request key; changed content uses a new one. A later proposal never
   erases an earlier failure or changes an approved record.

For example: "Read this session's retained invoice images. Propose the invoice
number, visible line items and amounts. Keep unreadable fields and unfamiliar
labels explicit, and show me the review receipt." For a contact card, request
source-visible observations, not an immediate customer update.

The pilot accepts 1 to 20 pages per session: PNG/JPEG only, at most 10,000,000
original image bytes and 25,000,000 pixels per page. PDF, HEIC, WebP, animations
and image URLs are not direct upload types. Preserve unsupported originals
separately. Deployment request limits may be smaller than the image limit.
No mobile capture app, attachment relay, session search, shared reviewer screen,
or automatic record publication is bundled. Actual upload and image reading
must be tested in the selected client; a working report connector proves neither.

### The source-only handoff

A local operator can export the session into a new private source package. It
retains every upload attempt, proposal version and rejection with checksums.
A complete usable image set also gets a session-named PDF and an original-to-page
map. The operator verifies the package against a separately retained receipt,
visually checks the PDF, and starts the normal profiling/intake commands.

The PDF never replaces the originals. Display conversion keeps page order and
encoded orientation and shows transparency on white. It is not a rescan, OCR
result, or proof of original physical resolution. Missing pages or conversion
failures keep the archive blocked; a failed partial PDF is never used. This is
a controlled source archive, not automatically a client-approved delivery.

Independent extraction, business controls, authorization and final review still
stand between these sources and a new approved snapshot. The assistant's
proposal is not automatically converted into a pipeline vote and cannot change
account cards, sales totals, reports or CRM-import files. Full setup, fields,
limits and recovery are in [Visual Intake](../references/visual-ingestion.md).

### Approved-data CRM preparation

The pipeline can prepare CRM-ready inputs without sending anything to a CRM.
This separation gives the target-system owner a chance to validate mappings,
permissions, duplicate behavior, automations, and rollback before production.

### Full canonical staging package

The no-send staging package preserves all **28 canonical tables** in dependency
order. It includes ordered CSVs, a versioned API-operation envelope, source and
load-plan checksums, row counts, idempotency keys, formula-safe display cells,
and authoritative compact canonical JSON for every row. It is designed for a
receiver or integration team that needs complete reconciliation across the full
data model.

### Common CRM import package

For teams that work with familiar CRM objects, the optional common package
creates twelve ordered CSV files:

1. accounts
2. account roles
3. addresses
4. contacts
5. products
6. sales locations
7. shipments
8. sales transactions
9. sales transaction lines
10. transaction charges
11. payments
12. payment applications

It also includes a field-mapping template, a vendor-source catalog, a
write-readiness plan, exact row and package checksums, and complete coverage
accounting for all 28 canonical tables. A field not represented by the common
profile is explicitly marked as outside the profile rather than silently
dropped.

### What must happen before a live CRM write

No live Salesforce, HubSpot, Dynamics/Dataverse, Zoho, or other CRM writer is
enabled by this package. A target-specific implementation starts only after the
client selects the vendor and tenant, supplies current object/property metadata,
approves mapping and credentials, and proves the adapter in a sandbox.

The production-write acceptance must show stable external keys, idempotent
reruns, parent-before-child loading, explicit rejected-row handling, count and
financial reconciliation, relationship checks, before-images/created IDs for
rollback, and separate authorization for the exact package hashes. Read-only
MCP/API access does not grant `crm:write` authority.

## A practical first-use path

For most teams, the safest and quickest sequence is:

1. Review the final package and resolve or explicitly retain any open items.
2. Confirm the exact approved snapshot and its source-export checksum.
3. Start with a private local desktop MCP connection for representative
   questions and reports.
4. Validate account cards, invoice search, a sales slice, one standard report,
   and an exported CSV/XLSX against the delivered package.
5. If mobile, web, or multi-user access is needed, deploy the remote `/mcp`
   service with OAuth and complete workspace-specific acceptance.
6. If CRM loading is needed, complete target mapping and sandbox testing from
   the no-send package before authorizing a target-specific adapter.

This order keeps a simple reporting pilot separate from a public deployment or
a live CRM change. It also gives business users an early way to test whether the
approved data answers their questions before the organization commits to a
larger integration.

## Questions this guide helps answer

- **Can I find an address or contact quickly?** Yes, use search and the
  account card over the approved snapshot.
- **Can I see sales by company, region, office, customer, job, or time period?**
  Yes, use governed sales analysis or the relevant standard report; currency is
  kept separate.
- **Can I pull any approved record?** Yes, search when the key is unknown, then
  retrieve the exact canonical record by table and key.
- **Can I download a report?** Yes, a remote deployment can create bounded,
  owner-bound CSV/XLSX jobs with checksums and expiry.
- **Can an assistant change my CRM?** No. Approved records remain read-only.
  Optional intake writes proposals to a separate journal, not canonical or
  target-CRM records. CRM inputs remain no-send until separate adapter
  implementation, authorization and acceptance.
- **Can I submit a photo for a new record?** The optional pilot supports original
  PNG/JPEG upload and source-cited proposals through a compatible application.
  It stops at review, not publication.
- **Can I ask for any possible analysis?** Shipped filters, account cards, sales
  dimensions and seven reports work within their limits. Custom joins, saved
  reports, forecasting and broader metrics need additional implementation and
  appropriate approved source data.
- **Can the system tell me what it cannot prove?** Yes. Unresolved source,
  arithmetic, attribution, completeness, mapping, and review findings remain
  explicit rather than being hidden.

## Before connecting real data

Use the following checklist with the engagement owner:

- [ ] The final review and applicable business gates are complete for the exact
  snapshot being served.
- [ ] The team has chosen local desktop, internal REST, or remote MCP access.
- [ ] The selected AI provider's privacy, retention, residency, and user-access
  terms are approved for the data returned in conversations.
- [ ] Remote use has an approved domain, TLS, OAuth/OIDC, tenant/role/scope
  policy, monitoring, retention, and incident process.
- [ ] The exact Claude and/or ChatGPT workspace connection has been tested with
  representative questions.
- [ ] Any CRM load has a completed tenant mapping, sandbox evidence,
  reconciliation plan, rollback plan, and separate production authorization.
- [ ] Optional intake has separately demonstrated byte upload, image reading,
  ownership checks, rejection/retry receipts, source export and pending-review
  wording; none is inferred from successful approved-data queries.

## Where to go next

- For the detailed output contract and approved retrieval rules, see
  [Canonical Deployment, CRM Load, and Retrieval](../references/canonical-deployment-retrieval.md).
- For activation, connection, deployment, and acceptance steps, see
  [MCP Production Integration](../references/mcp-production-integration.md).
- For the target-system import and write-readiness boundary, see
  [CRM Write Readiness](../references/crm-write-readiness.md).
- For unimplemented capabilities, see the
  [vendor-neutral roadmap](../references/business-data-platform-roadmap.md).
- For the broader evidence and review workflow, see the
  [Client Overview](CLIENT_OVERVIEW.md) and [Client User Guide](CLIENT_USER_GUIDE.md).

The guiding rule is unchanged across every output: use the approved snapshot
for business answers, keep its citations and checksums with important results,
and treat a later correction as a new, controlled version rather than an edit
to history.
