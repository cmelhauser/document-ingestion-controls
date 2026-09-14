# Extraction Schemas

Supported source-extraction field definitions by document type, plus validation rules that run at extraction time. This is the target data dictionary, not a claim that every provider or page supplies every field.

Header fields are comparatively cheap. **Line items are 3–5× the effort** and are
the primary cost driver in Phase 3 — scope them explicitly rather than assuming
they come free.

## Contents

- [Universal fields](#universal-fields-every-document)
- [Commercial invoice](#commercial-invoice)
- [Commission statement / report](#commission-statement--report)
- [Carrier freight bill](#carrier-freight-bill)
- [Bill of lading / air waybill](#bill-of-lading--air-waybill)
- [Proof of delivery](#proof-of-delivery)
- [Purchase order](#purchase-order)
- [Remittance advice](#remittance-advice)
- [Credit / debit memo](#credit--debit-memo)
- [Customs entry](#customs-entry)
- [Derived and control fields](#derived-and-control-fields)
- [Validation rules](#validation-rules)
- [Record shape](#record-shape)

---

## Universal fields (every document)

Carried by every extracted record without exception. Provenance that is optional
is provenance that goes missing.

| Field | Type | Notes |
|---|---|---|
| `document_id` | uuid | Surrogate |
| `source_file` | string | Manifest-relative retained original PDF path |
| `source_page_range` | string | e.g. `"12-14"` |
| `document_type` | enum | Phase 2 output |
| `engine` / `engine_version` | string | Per extracting engine |
| `run_timestamp` | datetime | |
| `branch` | enum | `A` \| `B` \| `B-rescan` |
| `has_handwriting` | bool | Phase 3H |
| `consensus_flag` | enum | `consensus_2of2` \| `consensus_2of3` \| `no_consensus` |
| `arithmetic_status` | enum | `proved` \| `proved_with_unproved_lines` \| `failed` \| `not_provable` \| `not_applicable` \| `deferred_reassembly` |
| `review_status` | enum | `auto_accepted` \| `sampled_verified` \| `exception_resolved` \| `open_exception` |
| `jbig2_suspect` | bool | From Phase 0.5 |

### Naming boundaries

### Raw and derived addresses

Address fields such as `buyer_address`, `seller_address`, `ship_to_address`, and `remit_to_address` are source-extraction targets. They retain the exact value read from the document. The CRM-ready components (`*_address_line1`, `*_address_line2`, `*_city`, `*_state_or_region`, `*_postal_code`, and `*_country_code`) are later derived controls defined in [`derived-field-schema.md`](derived-field-schema.md); they never replace the raw address.

`purchase_order_number` is the invoice-side reference to a PO; `po_number` is the identifier extracted from a purchase-order document. `uom` is the implemented record field name for unit of measure. An external provider using a different name must map it explicitly before consensus; the control layer does not silently rename evidence.

### Contacts and sales representatives

`contact_name`, `contact_email`, and `contact_phone` preserve a contact only
when a printed label or unambiguous labeled block identifies it. The
`sales_representative_name`, `sales_representative_email`,
`sales_representative_phone`, and `sales_representative_address` fields preserve
the equivalent source-visible sales-representative evidence. They are optional
header fields for every document family because layouts vary. Do not infer a
role from an unlabeled name, a signature, letterhead, or an email domain. These
raw fields remain proposals until independent consensus, entity/contact review,
and the applicable downstream controls complete.

### Broad core and source-labelled extension

The shared header contract also covers the common business-document space:

- commercial references: quote, estimate, order, sales-order, ACK, job,
  project, contract, revision, account, and free reference numbers;
- party, tax, and destination roles: bill-to, sold-to, buyer, seller, vendor,
  payer, payee, shipper, consignee, carrier, tax identifiers, and the matching
  addresses where printed;
- financial and payment facts: terms, method, reference, cheque, remittance,
  payment date, exchange rate, tax rate, credits, debits, balance, and totals;
- logistics facts: shipment, tracking, booking, container, seal, carrier,
  service, Incoterms, origin/destination, weight, and piece count.

No finite controlled list can cover every industry-specific document. For any
other visible labeled value, the extraction adapter requires a
`source_labelled_fields[]` entry with the exact printed label, value, source,
evidence text, and provider provenance. Normalized records retain each entry
under a stable opaque key with separate `source_label` and `observed_value`
claims. This is an append-only source extension, not a canonical mapping.

The opaque key is derived from the printed label plus an occurrence index, so it
identifies an entry within one engine's reading and **not** across engines: two
engines share a key only when they spell a label identically and enumerate
repeated labels in the same order. Consensus therefore retains these entries per
engine under `source_labelled_field_proposals` instead of reconciling them, and
they reach the schema surface through `schema_discovery.py`. Unfamiliar fields
become explicit schema-review work rather than disappearing, which is what this
channel is for; what they never become is a claim that two engines agreed.

---

## Commercial invoice

### Header

`invoice_number` · `invoice_date` · `due_date` · `payment_terms` ·
`purchase_order_number` · `seller_name` · `seller_address` · `seller_tax_id` ·
`buyer_name` · `buyer_address` · `buyer_tax_id` · `ship_to_name` ·
`ship_to_address` · `currency` · `subtotal` · `tax_amount` · `tax_rate` ·
`freight_amount` · `accessorial_total` · `discount_amount` · `total_amount` ·
`amount_due` · `remit_to_address`

### Lines

`line_number` · `item_code` · `description` · `quantity` · `uom` ·
`unit_price` · `extended_amount` · `discount_amount` · `tax_amount` ·
`tax_rate` · `tax_code` · `gl_account_code` · `cost_center` · `department` ·
`project_number` · `job_number` · `country_of_origin` · `hs_code` ·
`lot_number` · `serial_number` · `warehouse_location`

### Accessorials (separate rows, not folded into freight)

`charge_code` · `charge_description` · `charge_amount` (shared line fields)

Keep accessorials itemized. Collapsing them into a single freight figure destroys
the leakage analysis in Phase 5, which is usually where the recoverable money is.

## Commission statement / report

Commission statements and reports are source-native financial reports, not
invoices. Route them to this family when the page presents commission,
commissionable, dealer, brand, sales-credit, allocation, or reporting-period
labels. Do not force these fields into buyer/seller, invoice subtotal, or invoice
line-item semantics.

### Header

`statement_number` · `statement_date` · `period_start` · `period_end` ·
`dealer_name` · `brand_name` · `customer_name` · `sales_amount` ·
`commissionable_amount` · `stated_commission_rate` · `allocation_share` ·
`commission_amount` · `statement_total`

### Transaction or allocation rows

`transaction_id` · `transaction_date` · `dealer_name` · `brand_name` ·
`customer_name` · `description` · `sales_amount` · `commissionable_amount` ·
`stated_commission_rate` · `allocation_share` · `commission_amount`

The invoice arithmetic lane is `not_applicable` for this family. Preserve every
reported amount and formula input, then use the separate allocation-policy lane
for effective-rate or formula checks. A missing or conflicting commission
relationship remains review work; it is not converted into an invoice arithmetic
failure.

---

## Carrier freight bill

### Header

`pro_number` · `bill_date` · `carrier_name` · `carrier_scac` · `shipper_name` ·
`consignee_name` · `origin_city` · `origin_state` · `origin_postal` ·
`destination_city` · `destination_state` · `destination_postal` · `ship_date` ·
`delivery_date` · `service_level` · `equipment_type` · `weight` · `weight_uom` ·
`piece_count` · `freight_class` · `linehaul_amount` · `fuel_surcharge` ·
`accessorial_total` · `total_amount` · `currency`

### Accessorial lines

`charge_code` · `description` · `amount`

Common codes worth normalizing early: detention, demurrage, layover, redelivery,
reconsignment, storage, liftgate, inside delivery, residential, limited access.

---

## Bill of lading / air waybill

`bol_number` · `awb_number` · `booking_number` · `container_number` ·
`seal_number` · `shipper` · `consignee` · `notify_party` · `carrier` ·
`vessel_or_flight` · `voyage_number` · `port_of_loading` ·
`port_of_discharge` · `place_of_delivery` · `ship_date` · `piece_count` ·
`gross_weight` · `volume` · `freight_terms` (prepaid/collect) ·
`description_of_goods` · `marks_and_numbers`

No financial totals in most cases. Registers as an event with a null amount so
the shipment lifecycle stays complete.

---

## Proof of delivery

`pro_number` · `bol_reference` · `delivery_date` · `delivery_time` ·
`received_by_name` · `signed` · `signature_region` · `pieces_received` ·
`condition_notes` · `exception_flag` · `damage_description`

**This document type is handwriting-dominant.** Delivery date, receiver name,
piece count, and damage notes are usually all handwritten. Every POD routes
through the Phase 3H path irrespective of branch, and short-receipt quantities
here are the source of the most consequential amendments in the corpus.

---

## Purchase order

`po_number` · `po_date` · `buyer_name` · `vendor_name` · `ship_to_address` ·
`requested_delivery_date` · `payment_terms` · `currency` · `subtotal` ·
`total_amount` · `approver` · `cost_centre`

Lines: `line_number` · `item_code` · `description` · `quantity_ordered` ·
`uom` · `unit_price` · `extended_amount` · `gl_account_code` ·
`project_number` · `job_number`

Feeds the three-way match. `quantity_ordered` vs `quantity_received` (from POD,
often handwritten) vs `quantity_invoiced` is the comparison that surfaces
overbilling.

---

## Remittance advice

`remittance_number` · `payment_date` · `payer_name` · `payee_name` ·
`payment_method` · `cheque_number` · `payment_reference` · `total_paid` ·
`currency`

Applied lines: `invoice_number` · `invoice_amount` · `discount_taken` ·
`amount_applied` · `adjustment_reason`

Critical for open-item matching. Where no remittance advice exists, a
**handwritten cheque number on the invoice itself is frequently the only link**
between an invoice and its payment — treat it as a first-class extraction target,
not an incidental annotation.

---

## Credit / debit memo

`memo_number` · `memo_date` · `memo_type` · `original_invoice_number` ·
`reason_code` · `reason_description` · `amount` · `currency` · `approver`

Must link to the original invoice or the aging closure check in Phase 4 will
report a false open item.

---

## Customs entry

`entry_number` · `entry_date` · `importer_of_record` · `importer_number` ·
`port_of_entry` · `country_of_origin` · `hs_code` · `entered_value` · `duty` ·
`merchandise_processing_fee` · `harbor_maintenance_fee` · `total_duties_taxes`

Required for landed-cost analysis. Skip only if the client has no import
activity.

---

## Derived and control fields

Values listed above are source-extraction targets. Consensus, arithmetic proof, entity resolution, attribution, sampling, and final-review fields are derived controls and are defined separately in [`derived-field-schema.md`](derived-field-schema.md). They never replace an original extracted value.

The run-level OpenAI, Gemini-on-Vertex, and isolated OpenRouter adapters propose
the deliberately narrow provider schema implemented by their adapter modules;
OpenRouter reuses the normalized OpenAI-shaped contract while keeping separate
credentials and transport. Each is a proposal voter, not the canonical source
of truth. A client-approved provider mapping is required before a run claims
coverage for any additional target field.

## Validation rules

Run at extraction time, before consensus is finalized.

### Arithmetic (see `scripts/arithmetic_check.py`)

- `quantity × unit_price = extended_amount` per line
- `Σ extended_amount = subtotal`
- `subtotal + tax + freight + accessorials − discount = total_amount`
- `total_amount − credits_applied = amount_due`

All to the cent. Tolerance for accumulated rounding is at most `0.01 × line_count`
and should be stated, not assumed.

### Range and format

- Dates fall within the operating window agreed in Phase 0
- Currency resolves against ISO 4217
- UOM resolves against a controlled vocabulary
- Invoice numbers conform to the per-vendor format pattern detected across the
  corpus — a vendor whose numbers are always `INV-####` producing `1NV-2231` has
  an OCR error, not a new format
- Tax rate within plausible jurisdictional range
- `due_date >= invoice_date`; `delivery_date >= ship_date`
- Weight, piece count, and volume non-negative and within physical plausibility

### Cross-document

- Invoice `purchase_order_number` resolves to a known PO
- Credit memo `original_invoice_number` resolves to a known invoice
- Remittance applied lines sum to `total_paid`

---

## Record shape

See `assets/record_template.json` for the full JSON structure consumed by
`scripts/consensus.py` and `scripts/arithmetic_check.py`.

Minimum shape for the scripts to work:

```json
{
  "document_id": "uuid",
  "document_type": "commercial_invoice",
  "header": {
    "invoice_number": {"value": "INV-2231", "confidence": 0.98, "source": "printed"},
    "subtotal":       {"value": 1250.00,    "confidence": 0.99, "source": "printed"},
    "tax_amount":     {"value": 100.00,     "confidence": 0.99, "source": "printed"},
    "total_amount":   {"value": 1350.00,    "confidence": 0.99, "source": "printed"}
  },
  "lines": [
    {
      "line_number": 1,
      "quantity":         {"value": 10,     "confidence": 0.97, "source": "printed"},
      "unit_price":       {"value": 125.00, "confidence": 0.99, "source": "printed"},
      "extended_amount":  {"value": 1250.00,"confidence": 0.99, "source": "printed"}
    }
  ],
  "amendments": []
}
```

Every field is a `{value, confidence, source}` object rather than a bare scalar.
Provider adapters may additionally retain `model_confidence` (a numeric score
or `null`) on the field and a top-level `model_confidences` audit list. This is
provider self-reporting for later calibration only; consensus and approval
logic ignore it, and it is never evidence of arithmetic correctness or engine
independence.
`source` ∈ `printed` | `handwritten` | `manual` | `system`. This is what makes the
precedence rule enforceable downstream — a bare scalar loses the distinction that
determines which value wins.

Visual-provider records may also include `handwriting_regions[]` and
`handwriting_readings[]`. Every region requires a non-empty, page-stable
`region_id` plus normalized `left`, `top`, `right`, and `bottom` coordinates.
Each reading repeats that exact `region_id` and carries `semantic_type`,
`content_class`, proposed `value`, `evidence_text`, `financial_amendment`, and
optional `model_confidence`. The stable ID is the join key used by Google Cloud
Vision and the HTR reconciler; line numbers are source locations, not schema
field names. These are LLM transcription proposals, not replacements for the
printed field or client approval. `handwriting_review.py` accepts the retained
visual-extraction record list directly and counts all models from one provider
family as a single independence-group vote.
