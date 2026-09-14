# Supported Taxonomy Coverage Matrix

This matrix defines the bounded corpus expansion: the document types in
`references/extraction-schema.md`, plus receipts, packing lists, and statements
that commonly occur in a mixed accounting packet. It is not a claim that any
finite public corpus covers every layout, language, country, vendor, or client
process. Each public source remains opt-in and local pending rights review.

## Documents: two samples per supported type

| Type | Sample A | Sample B | Evidence to retain |
|---|---|---|---|
| Commercial invoice | Existing FedEx template | Existing two-page TI invoice | invoice ID, total, lines, page markers |
| Receipt | SROIE `001` | SROIE `002` | published word boxes and header fields |
| Carrier freight bill | [CarrierInvoice sample](https://carrierinvoice.com/tools/trucking-invoice-template/) | [InvoiceGenerator freight sample](https://invoicegenerator.com/templates/freight/) | PRO/invoice ID, accessorials, total |
| Bill of lading / AWB | Existing IncoDocs BOL | Existing commercial-invoice/AWB packet | BOL/AWB number, parties, weight, pages |
| Proof of delivery | [Signed POD](https://www.docdroid.net/file/download/rLnKbV3/proofofdelivery-2343053963-pdf.pdf) | [Delivery confirmations](https://planning.lacity.gov/eir/CrossroadsHwd/Deir/Deir_Ex/Notices/Files/DEIR_Delivery_Confirmations.pdf) | delivery ID, signature region, exception note |
| Purchase order | [IncoDocs PO PDF](https://incodocs.com/templates/pdfs/Purchase%20Order.pdf) | client-authorized PO required | PO ID, lines, approval, total |
| Remittance advice | [Sample remittance advice](https://www.placer.ca.gov/DocumentCenter/View/3863/Sample-Sick-Leave-Pay-Stub-PDF) | [EDI 820 remittance sample](https://assets.ctfassets.net/416ywc1laqmd/FZrchxwSNMjJqf1g8jQ7N/86a2603ac7c4b57aabb154a3858ced74/edi-820-payment-sample.pdf) | payment ID, applied invoices, total paid |
| Credit memo | Existing credit-memo sample | client-authorized credit memo required | memo ID, original invoice, reason, amount |
| Debit memo | [3M debit memo](https://multimedia.3m.com/mws/media/1459346O/debit-memo-example-3m-business-transformation-pdf.pdf) | client-authorized debit memo required | memo ID, original invoice, increment, amount |
| Customs entry | official CBP 7501/ACE sample required | client-authorized entry summary required | entry ID, HS code, duty and tax fields |
| Packing list | Existing DocumentOf packing list | Existing Packair packing list | items, package count, weight, linked reference |
| Statement of account | [Impact Bank sample](https://www.impact-bank.com/user/file/dummy_statement.pdf) | [Knysna template](https://www.knysna.gov.za/wp-content/uploads/2020/02/Customer-statement-of-account-template.pdf) | period, transactions, opening/closing balance |

`client-authorized ... required` is intentional: a blank template can establish
layout handling but cannot establish realistic extraction accuracy, financial
semantics, or client-specific classification.

## Capability matrix

| Capability | Corpus evidence | Acceptance result when implemented |
|---|---|---|
| PDF intake and one-page output | every PDF | immutable, sorted one-page outputs and source provenance |
| Scan branch routing | NARA, Darien, TI, JBIG2 | C/G/B1/B2 route and quality findings are measured |
| Native text / image-only fallback | TI vs NARA/Darien | OCR skipped only when text is usable |
| Multi-page reassembly | TI two-page invoice, interleaved AWB packet | correct grouping or explicit unassigned exception; never force-fit |
| Handwriting and signatures | NARA invoice, POD | regions captured, typed by function, unresolved readings escalated |
| Classification and party roles | two samples per type | measured against human-reviewed labels; low confidence escalates |
| Printed OCR / headers | SROIE and selected invoices | field-level precision/recall against published or reviewed truth |
| Lines and arithmetic | invoice, PO, freight bill, credit/debit memo | independent reads plus cent-level self-proof |
| Cross-document matching | PO, invoice, POD, remittance, memo | links carry an explicit method and exceptions remain open |
| Attribution / completeness / sampling | client reference export + GL required | no monetary claim without its reference population and accepted report |
| Hazard handling | JBIG2 and malformed/encrypted source samples | numerics escalate; unreadable/encrypted files remain explicit exceptions |

## Release rule

Use public documents only to test transport, scan routing, parsers, and generic
layout behavior. Before a production claim, add a redacted, client-authorized
golden set containing at least two examples of every type actually present,
including each vendor/layout and the linked PO, receipt/POD, invoice, payment,
memo, reference, and GL records needed for reconciliation.
