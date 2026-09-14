# Analytics Definitions

Scope strictly against the Phase 0 decision list. This is the standard set for a
business-document and procurement corpus — a menu, not a mandate. Building metrics nobody
asked for is how these projects lose their completion criterion.

**Every output carries its completeness figures.** A revenue chart built on 94% of
documents says so on the chart. See `references/qa-sampling.md` for why this is
not optional.

## Implemented reports versus the analytics menu

The metrics below are engagement design definitions, not a list of shipped
MCP tools or dashboards. The current approved-fact service implements account
cards, exact/search/table queries, sales grouped by up to three supported
dimensions with currency partitioning, and seven reports: `account_directory`,
`invoice_register`, `receivables_aging`, `sales_by_customer`,
`sales_by_selling_location`, `attribution_coverage`, and `shipment_performance`.
The [retrieval contract](canonical-deployment-retrieval.md) defines their exact
scope. Remote report/table downloads are bounded CSV/XLSX artifacts, not saved
custom reports or an unrestricted query engine.

Advanced joins, governed custom metrics, saved reports, forecasts and broader
business analytics are [roadmap work](business-data-platform-roadmap.md).
Missing cost, opportunity, historical balance or authoritative payment data is
not zero and cannot support margin, funnel or historical-aging claims. New
visual-intake proposals never contribute to approved totals or account cards.
For current aging, require `as_of` and state that it buckets the current approved
balance snapshot rather than reconstructing past balances.

## Contents

- [Customer](#customer-analytics)
- [Vendor and carrier](#vendor-and-carrier-analytics)
- [Cost](#cost-analytics)
- [Operational](#operational-analytics)
- [Financial control](#financial-control-analytics)
- [Data quality](#data-quality-analytics)
- [Definitional traps](#definitional-traps)

---

## Customer analytics

| Metric | Definition | Depends on |
|---|---|---|
| Revenue by account/period | Σ `invoice_header.total_amount` by resolved `party_id` | Entity resolution |
| Revenue concentration | Top-N share; Pareto curve; HHI | Entity resolution |
| First-order date | Min `invoice_date` per party | Completeness at period start |
| Customer tenure | Today − first order | |
| RFM | Recency, frequency, monetary tercile/quintile scoring | |
| Churn signal | No order in > *k* × individual median inter-order interval | Calendar completeness |
| Cohort retention | Revenue retention by acquisition period | |
| Product/service mix | Line-level revenue by `item_code` | Line extraction |

**Churn signal caveat.** A customer who appears to have stopped ordering may
simply have documents in an unscanned batch. Cross-check every churn finding
against the calendar-gap report before presenting it. This is the single most
common false finding in backfile analytics.

---

## Vendor and carrier analytics

| Metric | Definition |
|---|---|
| Spend by carrier/period | Σ freight bill `total_amount` by carrier |
| Rate variance by lane | Stddev and range of `linehaul_amount / weight` per lane per period |
| On-time delivery | POD `delivery_date` ≤ committed date, by carrier |
| Claim/damage rate | Count of `exception_event` per 1,000 shipments |
| Vendor concentration | Top-N spend share; single-source exposure by category |
| Fuel surcharge ratio | `fuel_surcharge / linehaul_amount`, tracked over time |

On-time delivery and damage rate both depend heavily on **handwritten** POD
content — delivery dates and damage notes are rarely printed. Report the
handwriting-derived proportion alongside these metrics so the reader knows how
much of the result rests on the least reliable extraction path.

---

## Cost analytics

| Metric | Definition |
|---|---|
| Cost per shipment | `total_amount` per shipment |
| Cost per unit weight | `total_amount / weight`, by lane |
| Cost per lane | Mean and distribution |
| Accessorial leakage | Σ accessorials by charge code, as % of linehaul |
| Landed cost | Goods + freight + duty + fees, per item |
| Cost trend | Cost per unit weight over time, volume-normalized |

**Accessorial leakage is usually where the recoverable money is.** Detention,
demurrage, and redelivery charges accumulate quietly and are rarely reviewed in
aggregate. This requires accessorials itemized by code — see
`references/extraction-schema.md`. If they were collapsed into a single freight
figure at extraction, this analysis is impossible without re-extraction.

---

## Operational analytics

Lane volume and directional balance · seasonality by month and customer · weight
and volume distribution · transit time distribution (mean **and tail** — the tail
is what causes customer complaints) · shipment exception rate · equipment and
service-level mix.

---

## Financial control analytics

| Check | Method |
|---|---|
| Duplicate invoices | Same vendor + amount + date ± window; also same invoice number across the corpus |
| Price vs contract variance | `unit_price` against contract rate table where available |
| Payment terms adherence | `payment_date − invoice_date` vs stated terms |
| DSO / DPO | Standard formulas over the reconciled event stream |
| Early-payment discount capture | Discounts available vs discounts taken |
| Unapplied cash | Payments with no `payment_application` rows |
| Overbilling | Three-way match variance: invoiced qty > received qty |

Duplicate detection runs across the **full corpus**, not within a period.
Duplicates separated by months are the ones that got paid twice — same-week
duplicates usually get caught by AP at the time.

---

## Data quality analytics

Deliver as a permanent operational dashboard, not a one-time report.

- Extraction accuracy by field, document type, and branch
- Handwriting incidence and recognition accuracy, by branch
- Exception volume and clearance rate over time
- Completeness metrics: sequence gaps, calendar gaps, GL variance, aging closure
- Entity resolution: cluster count, merge count, pending adjudications

This dashboard is what lets the client answer "how much do we trust this?" at any
time without re-running the QA. It is also what makes it obvious when a
re-ingestion has degraded something.

---

## Definitional traps

Settle these with the client in Phase 0. Each one silently changes headline
numbers, and discovering the disagreement after the analysis is presented is
expensive.

**Revenue on invoice date or delivery date?** Changes period totals and
seasonality shape.

**Gross or net of credits?** Changes customer ranking, sometimes materially.

**Are freight charges revenue, cost recovery, or contra-cost?** Changes margin
analysis entirely.

**Is a lane directional?** ORD→ATL and ATL→ORD as one lane or two changes volume
balance and rate variance.

**Which party is "the customer"** on a third-party-billed shipment — the shipper,
the consignee, or the bill-to? Changes account revenue attribution.

**Do intercompany transfers count?** Usually not, but they are in the document
pile and will inflate everything if unflagged.
