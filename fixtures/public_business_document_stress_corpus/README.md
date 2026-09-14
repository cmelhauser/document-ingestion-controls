# Public PDF Business-Document Stress Corpus

This is a small, intentionally varied public fixture for Phase 0.5 through
Phase 2 validation. It is not client evidence, an OCR accuracy benchmark, or a
substitute for a redacted client calibration corpus.

## Contents

- `originals/` holds the downloaded source PDFs unchanged.
- `derived/public_business_document_stress_bundle.pdf` is a 10-page, deliberately
  non-grouped bundle. It interleaves pages from a three-page commercial
  invoice/air-waybill with commercial-invoice, packing-list, credit-memo,
  bill-of-lading, and neutral native-text control documents.
- `derived/public_business_document_stress_manifest.json` records every source URL,
  SHA-256 digest, source page, expected document type, and the required output
  sort key.

The neutral control must go to the exception queue. Pages from the three-page
source are deliberately separated, so an intake run must preserve them as
unassigned pages rather than force a document grouping.

## Rebuild and run

```bash
.venv/bin/python fixtures/public_business_document_stress_corpus/build_corpus.py
.venv/bin/python fixtures/public_business_document_stress_corpus/build_corpus.py --verify
.venv/bin/python scripts/scan_profile.py \
  fixtures/public_business_document_stress_corpus/derived/public_business_document_stress_bundle.pdf \
  --out /tmp/public-stress-profile.json
.venv/bin/python scripts/ingest_pages.py \
  fixtures/public_business_document_stress_corpus/derived/public_business_document_stress_bundle.pdf \
  --out /tmp/public-stress-intake

# Or run both controls plus integrity assertions into a new output folder.
fixtures/public_business_document_stress_corpus/run_validation.sh /tmp/public-stress-validation
```

The intake output must contain exactly 10 one-page PDFs in ascending,
zero-padded order. Compare its `ingestion_manifest.json` with the derived
manifest; do not treat the source-template category as a production
classification ground truth.

## Rights and safety

The originals are public PDFs fetched from their stated URLs on 2026-08-12.
They are retained only for local testing at the user's request. Public access
does not establish redistribution rights, so do not publish this corpus or
reuse the documents outside this workspace without separately reviewing each
source's license and terms. Never mix client records into this directory.

## Deliberate limits

All source files are native-text PDFs. The fixture exercises source diversity,
multi-page splitting, ordering, provenance, conservative classification, and
exception routing. It does not exercise actual scanned-page OCR, handwriting,
JBIG2, image cleanup, secure/encrypted PDF handling, factual extraction, or
financial reconciliation. Those require a separately authorized corpus and
human-reviewed expected values.
