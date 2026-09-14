#!/usr/bin/env python3
"""Build and verify the ordered, public-PDF intake stress fixture.

This utility deliberately lives with the fixture, not the production pipeline.
It never fetches from the network and never changes the downloaded originals.
"""

import argparse
import hashlib
import json
from pathlib import Path

import pikepdf

ROOT = Path(__file__).resolve().parent
ORIGINALS = ROOT / "originals"
DERIVED = ROOT / "derived"
BUNDLE_NAME = "public_business_document_stress_bundle.pdf"
MANIFEST_NAME = "public_business_document_stress_manifest.json"

SOURCES = {
    "fedex_commercial_invoice": {
        "filename": "01_fedex_commercial_invoice_template.pdf",
        "url": "https://www.fedex.com/content/dam/fedex/us-united-states/services/CommercialInvoice.pdf",
        "expected_type": "commercial_invoice",
    },
    "incodocs_commercial_invoice": {
        "filename": "02_incodocs_commercial_invoice_template.pdf",
        "url": "https://incodocs.com/templates/pdfs/Commercial%20Invoice.pdf",
        "expected_type": "commercial_invoice",
    },
    "sample_commercial_invoice_air_waybill": {
        "filename": "03_sample_commercial_invoice_air_waybill.pdf",
        "url": "https://illusionbookstore.com/wp-content/uploads/2016/12/Sample-Commercial-Invoice-1.pdf",
        "expected_type": "commercial_invoice",
    },
    "packing_list": {
        "filename": "04_packing_list_template.pdf",
        "url": "https://documentof.com/pdf-files/794/packing-list-template-international-export-shipments.pdf",
        "expected_type": "packing_list",
    },
    "credit_memo": {
        "filename": "05_credit_memo_sample.pdf",
        "url": "https://documentof.com/pdf-files/622/credit-memo-sample-overcharge-correction.pdf",
        "expected_type": "credit_memo",
    },
    "bill_of_lading": {
        "filename": "06_incodocs_bill_of_lading_template.pdf",
        "url": "https://incodocs.com/templates/pdfs/Bill%20of%20Lading.pdf",
        "expected_type": "bill_of_lading",
    },
    "neutral_native_text_control": {
        "filename": "07_w3c_dummy_native_text_control.pdf",
        "url": "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
        "expected_type": None,
    },
    "packair_packing_list": {
        "filename": "08_packair_packing_list_template.pdf",
        "url": "https://www.packair.com/wp-content/uploads/2017/04/PACKAIR-PACKING-LIST.pdf",
        "expected_type": "packing_list",
    },
}

# The order is intentionally non-grouped. The three-page source is interleaved
# with other source types to prove the intake stage preserves page order but
# does not silently force unsafe document reassembly.
ORDER = (
    ("sample_commercial_invoice_air_waybill", 2),
    ("packing_list", 1),
    ("fedex_commercial_invoice", 1),
    ("bill_of_lading", 1),
    ("sample_commercial_invoice_air_waybill", 1),
    ("credit_memo", 1),
    ("incodocs_commercial_invoice", 1),
    ("packair_packing_list", 1),
    ("neutral_native_text_control", 1),
    ("sample_commercial_invoice_air_waybill", 3),
)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_inventory():
    inventory = {}
    for source_id, spec in SOURCES.items():
        path = ORIGINALS / spec["filename"]
        if not path.is_file():
            raise FileNotFoundError(f"Missing required original: {path}")
        with pikepdf.open(path) as pdf:
            page_count = len(pdf.pages)
        inventory[source_id] = {
            **spec,
            "path": str(path.relative_to(ROOT)),
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
            "pages": page_count,
        }
    return inventory


def build():
    inventory = source_inventory()
    DERIVED.mkdir(exist_ok=True)
    bundle = DERIVED / BUNDLE_NAME
    merged = pikepdf.Pdf.new()
    pages = []
    for output_page, (source_id, source_page) in enumerate(ORDER, start=1):
        source = inventory[source_id]
        if source_page > source["pages"]:
            raise ValueError(f"{source_id} has no page {source_page}")
        with pikepdf.open(ROOT / source["path"]) as original:
            # Templates can contain AcroForm widgets. The fixture is a static
            # visual/test corpus, so strip inputs while retaining page content.
            merged.add_pages_from(original, [source_page - 1], forms="strip")
        pages.append(
            {
                "output_sort_key": f"{output_page:06d}",
                "source_id": source_id,
                "source_file": source["path"],
                "source_page_number": source_page,
                "expected_type": source["expected_type"],
                "purpose": "native_text_control"
                if source_id == "neutral_native_text_control"
                else "public_business_document_template",
            }
        )
    merged.save(bundle)
    manifest = {
        "fixture_name": "public_business_document_stress_corpus",
        "purpose": "Phase 0.5 through Phase 2 public-PDF stress fixture",
        "bundle": str(bundle.relative_to(ROOT)),
        "bundle_sha256": sha256(bundle),
        "bundle_pages": len(pages),
        "ordering": "ascending output_sort_key; deliberately non-grouped source pages",
        "source_usage": (
            "Downloaded public templates and controls for local pipeline testing only. "
            "Preserve URLs and SHA-256 values; do not redistribute or treat any "
            "source as licensed for broader reuse without an independent rights review."
        ),
        "coverage_limits": [
            "Native-text document and provenance test; not an OCR accuracy benchmark.",
            "Does not represent client data, handwriting, JBIG2, encrypted PDFs, or a real GL population.",
            "The intentionally interleaved pages must remain unassigned for reassembly unless later evidence supports a grouping.",
        ],
        "sources": inventory,
        "expected_pages": pages,
    }
    (DERIVED / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def verify():
    manifest_path = DERIVED / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError("Build the fixture before verifying it")
    manifest = json.loads(manifest_path.read_text())
    if manifest["sources"] != source_inventory():
        raise ValueError("An original PDF no longer matches the recorded provenance")
    bundle = ROOT / manifest["bundle"]
    if sha256(bundle) != manifest["bundle_sha256"]:
        raise ValueError("Bundle SHA-256 does not match its recorded provenance")
    with pikepdf.open(bundle) as pdf:
        page_count = len(pdf.pages)
    if page_count != manifest["bundle_pages"] or page_count != len(manifest["expected_pages"]):
        raise ValueError("Bundle page count does not match the expected ordered manifest")
    expected_keys = [page["output_sort_key"] for page in manifest["expected_pages"]]
    actual_keys = [f"{number:06d}" for number in range(1, page_count + 1)]
    if expected_keys != actual_keys:
        raise ValueError("Manifest output sort keys are not contiguous and ordered")
    return {"bundle_pages": page_count, "bundle_sha256": manifest["bundle_sha256"]}


def main():
    parser = argparse.ArgumentParser(description="Build the public-PDF stress corpus fixture.")
    parser.add_argument("--verify", action="store_true", help="verify an existing derived bundle")
    args = parser.parse_args()
    result = verify() if args.verify else build()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
