# CRM Write Readiness and Common Import Files

This is the Phase 6 boundary between the approved canonical export and a
client-selected CRM. It prepares importable files and the controls an adapter
must implement. It does not authorize or execute a CRM write.

## Implemented no-send package

Do not confuse this output with the optional
[visual-intake source package](visual-ingestion.md). Intake retains original
images and unapproved model candidates, including corrected proposal versions;
it cannot create or update an approved account, contact, address or invoice.
Its local export only prepares sources for the existing pipeline. None of those
proposals may appear in the common CRM files until the normal independent
controls, authorization, canonical export and exact load plan exist. The
[vendor-neutral roadmap](business-data-platform-roadmap.md) specifies future
governed create/update and adapter work; it is not an executable write service.

After the strict canonical load plan has been created, run:

```bash
python scripts/crm_import_package.py canonical_export.json canonical_load_plan.json \
  --out crm_import_run/
```

The output directory must be new. The command recomputes the strict load plan
for the exact export, rejects open-review or provenance-invalid rows through the
canonical validator, writes into a temporary sibling directory, reopens and
verifies every artifact, and publishes atomically only after verification.

It creates twelve UTF-8, header-bearing CSV files in dependency order:

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

These are common object shapes, not assertions that every CRM has the same
native object model. Accounts, contacts, and products commonly map to standard
objects. Locations, shipments, invoices, charges, payments, and applications
often require client-configured custom objects or an ERP/accounting destination.
Do not force financial records into a sales-pipeline object merely because a
particular CRM calls that object a deal or opportunity.

Every displayed cell is formula-safe. Every row also retains the canonical
source table, exact canonical idempotency key, authoritative compact canonical
JSON, and canonical-row SHA-256. The manifest binds the export, strict load
plan, files, mapping worksheet, write plan, official-source list, counts, and
checksums. Its canonical coverage section accounts for all 28 input tables:
each is either mapped to one common file or explicitly marked as outside the
common CRM profile. An empty common profile produces headings plus a finding;
it is never a successful load.

## Package contents

- `crm_import_manifest.json` — closed-world inventory, source/load/package
  hashes, per-file shape and hashes, complete canonical coverage, and explicit
  `live_write_permitted=false`.
- `field_mapping_template.csv` — every generic field, its canonical source,
  blank target-object/type fields for the target owner, and proposal-only
  Salesforce, HubSpot, Dynamics/Dataverse, and Zoho suggestions.
- `crm_write_plan.json` — discovery, mapping approval, sandbox, separately
  authorized upsert, reconciliation, and acceptance/rollback stages.
- `official_import_sources.json` — official vendor documentation used to bound
  the suggestions. Tenant metadata and current vendor documentation remain
  authoritative.
- Twelve numbered common-object CSV files.

## Required target decisions

Before adapter development, obtain and retain:

1. Target vendor, tenant, region, subscription/edition, and sandbox.
2. Current object/property metadata, required fields, field types, enumerations,
   validation rules, duplicate rules, alternate/external keys, and association
   labels.
3. Whether each generic object maps to a standard object, custom object, or a
   different destination.
4. Owner assignment, currency, time zone, locale, date, null/blank, and picklist
   policies.
5. API/import limits, retry semantics, automation/workflow side effects, and
   maintenance window.
6. Backup/export, reject retention, rollback, acceptance, and data-retention
   requirements.

Complete `field_mapping_template.csv` against the exported tenant metadata and
approve it as a separate controlled artifact. The included vendor suggestions
are not approved mappings and must never override tenant configuration.

## Adapter contract

An authorized adapter must:

- verify the package and source/load checksums before opening a connection;
- enforce one unique external or alternate key per target object;
- load parent objects before children and resolve relationships only through
  approved stable keys;
- distinguish create, update, unchanged, and rejected outcomes;
- halt on rejected or unaccounted rows rather than silently omitting them;
- retain the exact source row, canonical key, target job/record ID, error code,
  and error text for each rejection;
- reconcile `attempted = created + updated + unchanged + rejected` per object;
- verify relationship counts and applicable currency-partitioned financial
  totals after load;
- record before-images for updates and created IDs for rollback;
- perform no hard delete unless a later, separately scoped authorization names
  the exact records and recovery method; and
- write a final immutable reconciliation manifest linked to the package hash.

## Recommended execution sequence

1. Export tenant metadata and a pre-load target backup.
2. Complete and review the target mapping.
3. Create/enforce external or alternate keys and required custom objects/fields.
4. Run a small representative sandbox import including updates, duplicates,
   missing optional values, associations, and intentional rejects.
5. Prove idempotency by rerunning the same sandbox batch without duplicates.
6. Test relationship integrity, automations, reject capture, and rollback.
7. Freeze the approved package hashes and obtain separate production-write
   authorization.
8. Execute bounded production batches with per-object reconciliation.
9. Run post-load counts, relationship checks, financial/control totals, and
   sample record comparisons back to source citations.
10. Obtain client acceptance or execute the tested rollback.

## Current vendor import observations

These observations explain the conservative generic design; they are not a
substitute for checking the current tenant.

- Salesforce file imports require the target's required fields, use IDs or
  external IDs for reliable updates and relationships, and expect split address
  components. See the official
  [Salesforce import checklist](https://help.salesforce.com/s/articleView?id=sf.essentials_import_checklist.htm&language=en_US&type=5)
  and [Data Loader CSV guidance](https://help.salesforce.com/s/articleView?id=000313396&language=en_US&type=1).
- HubSpot accepts spreadsheet imports with property headers and uses Record ID,
  email, company domain, or a configured unique property to identify records;
  associations require unique identifiers or a common key. See the official
  [file-format and association requirements](https://knowledge.hubspot.com/import-and-export/set-up-your-import-file).
- Dynamics 365/Dataverse supports CSV/Excel-oriented imports, but lookup fields
  must resolve through primary or alternate keys and a tenant-exported template
  is the safest field baseline. See Microsoft's
  [Customer Engagement data-management guidance](https://learn.microsoft.com/en-us/dynamics365/guidance/implementation-guide/data-management-product-specific-ce).
- Zoho CRM requires module-mandatory fields and uses module-specific duplicate
  identifiers or Record ID for updates; related accounts and contacts need
  explicit relationship mapping. See the official
  [Zoho CRM import FAQ](https://help.zoho.com/portal/en/kb/crm/faqs/data-administration/import/articles/faqs-import).

## Governed generic adapter now implemented

No live Salesforce, HubSpot, Dynamics/Dataverse, Zoho, or other CRM adapter is
included. A generic live writer would be unsafe because tenant schemas,
required fields, duplicate behavior, automations, permissions, and rollback
capabilities differ. Once the client selects a target and approves the mapping,
implement one adapter against this package and contract in the destination
system/repository, with target-specific acceptance tests and separate
credentials. Read-only API/MCP access remains independent and does not grant
`crm:write` authority.

The optional client-YAML platform implements a target-neutral no-send file
adapter and HTTPS JSON adapter. Read
[Business Data Platform](business-data-platform.md) and use the
[`record-maintenance` skill](../skills/record-maintenance/SKILL.md). MCP
exposes only schema, proposal, preview, and owner-scoped lifecycle status. The
remote API separately exposes operator-only authorization, application, and
reconciliation routes under `records:authorize` and `records:apply`; equivalent
CLI commands remain available. They create a signed expiring authorization,
apply POST/PATCH through the configured adapter, and independently reconcile
every expected field. No path hard-deletes or edits the immutable canonical
snapshot.

Vendor-specific bulk APIs, required-field metadata, duplicate rules,
automations, entity merge, and rollback still require the selected client's
sandbox acceptance and configuration. A successful target write becomes
queryable only after affected controls produce a new approved canonical export
and replacement snapshot. Read-only access and `records:propose` never grant
authorization or live target credentials.
