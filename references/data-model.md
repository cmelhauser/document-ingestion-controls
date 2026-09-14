# Data Model and CRM Handoff

Full DDL in `assets/canonical_schema.sql`. This document explains the shape and
the rules the CRM application has to honour.

## Contents

- [Layer architecture](#layer-architecture)
- [Common control columns](#common-control-columns)
- [Core entities](#core-entities)
- [Amendments](#amendments)
- [Load order](#load-order)
- [Load mechanics](#load-mechanics)
- [Handoff deliverables](#handoff-deliverables)

---

## Layer architecture

Four layers, each materialized and independently queryable.

| Layer | Contents | Mutability |
|---|---|---|
| **Raw** | Immutable engine output as returned, plus page images | Append-only, never modified |
| **Staged** | Typed, parsed, validated; one row per source document | Rebuilt on reprocessing |
| **Canonical** | Deduplicated, entity-resolved, amendments applied | Rebuilt on reprocessing |
| **CRM load** | Target-shaped, keyed to the CRM schema | Regenerated per load |

**Raw is retained permanently.** Any downstream layer rebuilds from raw without
re-OCR. This is what makes rule corrections cheap after delivery — and rule
corrections *will* be needed, because vendor formats that only appear twice in
five years are always discovered late.

---

## Common control columns

Every batch-managed business row carries its idempotency key, `batch_id`, and a
supported `review_status`. Evidence-bearing rows also carry a direct source
document key or another explicit evidence link. Static vocabulary rows and pure
association/history rows use their own natural keys and do not pretend to have a
source page when none exists.

| Column | Purpose |
|---|---|
| Primary/idempotency key | Stable internal or composite key used for repeatable upsert |
| `natural_key` | Business key where the entity has one |
| `source_document_id` or evidence link | Traceability to the originating document or retained decision evidence |
| `source_page_range` / `source_sha256` on `document` | Page citation and immutable source fingerprint |
| `source_confidence` | Aggregated extraction confidence where applicable |
| `review_status` | Canonical storage enum: `auto_accepted` \| `sampled_verified` \| `exception_resolved` \| `open_exception`; load planning/no-send staging admit only the first three. |
| `has_handwriting` | Annotation presence flag on document/transaction rows where applicable |
| `amendment_source` | `printed` \| `handwritten` \| `manual` \| `system` on amendable values |
| `created_at` / `updated_at` | Processing audit trail |
| `batch_id` | Load batch, for rollback |

Batch-managed rows must supply `review_status`. Source-independent
vocabulary/master and pure association/history rows may omit it, but if any
canonical row supplies the field, load planning/no-send staging rejects every
value outside the three review-clear statuses. This makes the accuracy
statement checkable rather than asserted.

---

## Core entities

```
party ─┬─ address
       ├─ contact
       └─ party_role ── (shipper | consignee | biller | payer)

carrier    item    lane
    │        │       │
    └────────┴───┬───┘
                 │
selling_location ── acknowledgement ── job
          │                 │             │
          └─────────────────┴──────┬──────┘
                                   │
             shipment ── document │
                 │
          invoice_header ── invoice_line
                 │
          payment_application ── payment

attribution        (links every monetary target to ACK/job or reason)
handwriting_region (links retained annotation evidence to document/page)

amendment          (links to any table + column)
exception_event    (links to shipment or document)
```

### Notes on specific entities

**`party`** is the single account master. Customers, vendors, and carriers are
all parties; role is expressed through `party_role`, not through separate tables.
A company that is both a customer and a supplier — common — is one party with two
roles, and modelling it as two records breaks the concentration analysis.

### Address component contract

`address.raw_address` is immutable source evidence. `line1`, `line2`, `city`, `state_province`, `postal_code`, and ISO `country_code` are derived values created by `scripts/address_normalize.py`. A format-valid local result is not proof of deliverability. When explicitly enabled with a client-approved key and cap, Google Address Validation evidence, including separately named validated components and geocode, remains a separate derived proposal. Preserve `address_normalizations` and any exception with the load batch; do not overwrite a component without a separately approved, evidence-bearing amendment.

**`lane`** is origin/destination pair, normalized to a canonical direction. Store
both the raw origin/destination strings and the resolved lane id; raw strings are
needed when the resolution turns out wrong.

**`document`** holds the link back to page images. Every financial row traces to
a document row and thence to a specific page range in a specific source PDF.

**`acknowledgement`**, **`job`**, and **`attribution`** implement the configured
business-reference gate. An attribution row records the target, amount, rank,
method, evidence chain, and selling location; an unattributed row requires an
explicit reason code rather than disappearing from job analytics.

**`handwriting_region`** retains page/region/crop and recognition status even
when transcription is descoped. A region never becomes an amendment merely by
being detected.

**`payment_application`** is the many-to-many between payments and invoices. Do
not put `invoice_id` on `payment` — partial payments and one-cheque-many-invoices
are the normal case in this data, not an edge case.

---

## Amendments

The table that encodes the precedence rule from `references/handwriting.md`.

```sql
amendment (
  amendment_id,
  target_table,      -- e.g. 'invoice_line'
  target_key,        -- surrogate key of the amended row
  target_column,     -- e.g. 'quantity'
  original_value,
  amended_value,
  amendment_source,  -- 'handwritten' | 'manual' | 'system'
  effective_time,
  evidence_document_id,
  evidence_region,   -- bounding box on the page
  reviewed_by,
  reviewed_at
)
```

The canonical layer exposes the **amended** value as current and retains the
**original**. Both are queryable. Nothing is overwritten. PostgreSQL rejects
every attempted amendment update or delete; a later correction is a new row,
not a mutation of history.

Two things this buys:

- If a handwritten reading turns out wrong, it is a correction rather than an
  unrecoverable loss.
- An auditor can see exactly which figures were altered from the printed
  document, by what evidence, and who reviewed it.

---

## Load order

Enforced by foreign-key dependency. Loading out of sequence produces orphan rows
and referential failures that are tedious to unwind.

1. **Reference data** — currency, unit of measure, charge codes, country, document type, selling location
2. **`document`** registry — before dependent source-provenance rows
3. **`party`** → **`party_name_variant`** → **`address`** → **`contact`** → **`party_role`**
4. **`carrier`**, **`item`**, **`lane`**
5. **`acknowledgement`**, then **`job`**
6. **`shipment`**
7. **`invoice_header`** → **`invoice_line`** → **`invoice_accessorial`**
8. **`payment`**, then **`payment_application`**
9. **`attribution`**
10. **`handwriting_region`**, **`amendment`**, **`exception_event`**, **`financial_event`**
11. **`load_manifest`** entries generated after a reconciled target load

The document registry must precede every table that carries `source_document_id`. Independent reference-data rows may be prepared in parallel; dependency order remains strict at load time.

---

## Load mechanics

**Idempotent upsert by natural key.** Non-negotiable. Corrected batches will be
re-run — that is the normal operating mode, not an exception — and re-running
must not duplicate.

**Referential integrity enforced at load, not assumed.** A failed foreign key
halts the load. It does not silently drop the row, because a silently dropped row
becomes a missing invoice that nobody notices until a reconciliation fails months
later.

**Load manifest per run:** record counts by entity, rejects by cause, checksum
per entity. Its idempotency grain is `(batch_id, load_step, entity)`, so one
batch can reconcile every ordered entity without overwriting another step.
Compare against the previous manifest on every re-run.

**Rollback capability:** every load tagged with `batch_id` permitting complete
reversal.

## Vendor-neutral deployment, load, and retrieval

The post-review layer is implemented without choosing a CRM vendor. Use
`scripts/canonical_deploy.py` to fingerprint and, only with explicit
`--execute`, apply the PostgreSQL DDL; use `scripts/canonical_load.py` to
validate an ordered, checksummed idempotent load plan; and use
`scripts/retrieval_store.py` to create a local approved-facts retrieval store.
`scripts/retrieval_mcp.py` provides twelve default local tools over that store:
document search/lookup, CRM discovery/schema, exact records, bounded one-table
queries and exports, account cards, multidimensional sales analysis, and seven
standard reports. It excludes open-review records, raw model responses, and unapproved
suggestions. Load planning, no-send staging, and the factual retrieval builder
have no open-review override. Load planning also validates the artifact
contract's table-driven source-document/parent provenance chain before emitting
checksums. See
`canonical-deployment-retrieval.md` for the exact
envelope, commands, and constraints. A target-specific CRM adapter, public or
tenant-isolated retrieval deployment, and hosted vector search remain separately
selected integration work. The included TLS-only bearer-authenticated reader
and OAuth-protected remote MCP server are deployment building blocks, not
evidence of an accepted client deployment. Remote MCP adds two report-job tools
to the twelve-tool baseline; see `mcp-production-integration.md` for scopes,
download authorization, client registration, and exact-snapshot acceptance.

Optional visual intake adds seven MCP/JSON API operations in a private,
owner-bound proposal journal, not a fifth approved data layer. Original images,
candidate versions, and rejection history stay separate from canonical facts.
The local export/verify utility can prepare a receipt-bound source-only package
and usable PDF for normal profiling/intake; it does not load candidate values
into these tables. See `visual-ingestion.md` and
`../skills/record-intake/SKILL.md`. Governed create/update application, a capture
or reviewer UI, arbitrary analytical joins, saved dashboards and forecasting
remain roadmap work, not current load mechanics; see
`business-data-platform-roadmap.md`.

---

## Handoff deliverables

These are engagement-dependent deliverables, not automatic proof of approval.
A blocked completeness result or unavailable sampling basis must remain an
explicit limitation; never invent an accuracy interval or promote a findings
package into an approved-facts package.

1. Canonical dataset, materialized
2. ERD and full data dictionary
3. Load order specification with dependency graph
4. Integrity rules the CRM application must enforce
5. Completeness Report as at handoff
6. Accuracy statement with confidence bounds, by field and stratum
7. Open exception register — unresolved records, quantified
8. Attribution coverage report and unattributable register
9. When a CRM import is in scope, the verified common-object import package,
   completed target mapping, sandbox evidence, reconciliation template, and
   tested rollback plan described in `crm-write-readiness.md`

### Integrity rules to hand the CRM team

State these explicitly; they are easy to lose in translation and expensive to
retrofit.

- `party` merges must remain reversible — keep the merge log, never hard-delete a
  merged party record
- `amendment` rows are append-only
- `raw` layer is never written to by the application
- Any new document ingested through the CRM follows the same consensus and
  arithmetic-proof rules, or the historical dataset's accuracy statement stops
  applying to the combined data
- `review_status` must be preserved on load and surfaced in the UI wherever a
  figure is displayed, so users can see which numbers are verified
