# Real-World Calibration Corpus

This opt-in corpus exercises the integration work that the built-in fixture
cannot: image-only PDFs, mixed scan quality, real receipt OCR ground truth,
handwritten annotations, a long multi-document packet, and JBIG2 hazard
detection.

It is intentionally **not committed to git**. The input records are public
sources, not client evidence, and public availability does not establish that
they can be redistributed in this repository. Run `acquire.sh` to download a
small local copy, then run `run_validation.sh` with a new output directory.

## What it covers

| Source | Real-world property | Expected use |
|---|---|---|
| NARA RFK release packet | 50 scanned bilevel pages; typed invoice with handwritten total/payment evidence | Branch B OCR and handwriting capture/review |
| Village of Darien finance packet | 41-page municipal packet with colour, grayscale, bilevel, blank/separator, low-DPI and poor-quality pages | scan profiling, one-page intake, conservative reassembly and classification |
| Texas Instruments commercial invoice | Contiguous two-page commercial invoice with the same invoice number and explicit `PAGE 1 OF 2` / `PAGE 2 OF 2` markers | reassembly-evidence and conservative-unassigned behavior |
| ICDAR 2019 SROIE subset | Six real scanned receipts with word boxes and company/date/address/total ground truth | OCR and receipt-header extraction scorecard |
| PDFium JBIG2 fixture | One PDF page containing `/JBIG2Decode` | parser/hazard routing; never trust its numerics |

The source URLs, selection, expected page counts, and acquired-file SHA-256
values are stored in `manifest.json` after acquisition.

## Acquire and validate

```bash
fixtures/real_world_calibration/acquire.sh
fixtures/real_world_calibration/run_validation.sh \
  /private/tmp/business-doc-real-calibration-validation
```

`acquire.sh` requires `curl`, the configured project Python runtime, and
network access. It downloads only six selected SROIE receipt images and their
published annotations. It never fetches client data and never writes into the public
template fixture.

The validation runner checks source provenance, profiles each PDF, verifies
one-page intake output and source-page order for the public packets, checks the
contiguous two-page invoice retains its number and PAGE X OF Y evidence while
safely remaining unassigned, checks all receipt ground-truth assets exist, and
requires the JBIG2 page to be marked suspect. It also verifies that the immutable two-page invoice intake remains unassigned while the later reassembly control emits one review-required grouping proposal and no exact-duplicate candidate. It does not claim an OCR, handwriting, broad automatic reassembly, classification, or arithmetic accuracy result until those integrations are implemented and scored.

For a contained run of every implemented control, execute after acquisition:

```bash
bash scripts/run_real_document_acceptance.sh /private/tmp/business-doc-real-acceptance
```

It begins with the TI two-page invoice, then verifies consensus, arithmetic
proof, entity resolution, rank-3 PO attribution, completeness reconciliation,
and sampling using clearly disclosed control companions. It is not an OCR or
financial-accuracy benchmark.

## Rights, privacy, and release rule

These records can contain historical business names, addresses, signatures, or
other information. Keep the downloaded `originals/` directory local. Do not
commit, publish, train on, or use it outside test/calibration without an
independent review of the underlying source's licence, terms, privacy status,
and intended use. The SROIE source repository is MIT-licensed, but that alone
does not resolve rights in its underlying receipt dataset; treat it as an
external evaluation source.

For a production acceptance test, use a client-authorized, redacted calibration
sample with human-reviewed expected results. That is the only appropriate
benchmark for actual extraction, handwriting, document reassembly, and
financial accuracy.
