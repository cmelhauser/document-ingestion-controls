#!/usr/bin/env bash
# Validate the opt-in real-world corpus through the implemented control layer.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CORPUS_DIR="$ROOT_DIR/fixtures/real_world_calibration"
OUTPUT_DIR="${1:-$ROOT_DIR/output/real_world_calibration_validation}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python runtime not found: $PYTHON_BIN" >&2
  echo "Create .venv and install requirements-dev.txt, or set PYTHON_BIN." >&2
  exit 1
fi
if [[ ! -f "$CORPUS_DIR/manifest.json" ]]; then
  echo "Calibration corpus is absent. Run fixtures/real_world_calibration/acquire.sh first." >&2
  exit 1
fi
if [[ -e "$OUTPUT_DIR" ]]; then
  echo "Output path already exists; choose a new empty path: $OUTPUT_DIR" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
"$PYTHON_BIN" "$ROOT_DIR/scripts/scan_profile.py" "$CORPUS_DIR" \
  --out "$OUTPUT_DIR/scan_profile.json" --quiet
"$PYTHON_BIN" "$ROOT_DIR/scripts/ingest_pages.py" \
  "$CORPUS_DIR/originals/nara_rfk_release_packet.pdf" --out "$OUTPUT_DIR/nara_intake" >/dev/null
"$PYTHON_BIN" "$ROOT_DIR/scripts/ingest_pages.py" \
  "$CORPUS_DIR/originals/darien_finance_packet.pdf" --out "$OUTPUT_DIR/darien_intake" >/dev/null
"$PYTHON_BIN" "$ROOT_DIR/scripts/ingest_pages.py" \
  "$CORPUS_DIR/originals/ti_multipage_commercial_invoice.pdf" --out "$OUTPUT_DIR/ti_invoice_intake" >/dev/null
"$PYTHON_BIN" "$ROOT_DIR/scripts/reassemble_pages.py" \
  "$OUTPUT_DIR/ti_invoice_intake/ingestion_manifest.json" --out "$OUTPUT_DIR/ti_invoice_reassembly.json" \
  --exceptions "$OUTPUT_DIR/ti_invoice_reassembly_exceptions.json" --quiet

"$PYTHON_BIN" - "$CORPUS_DIR" "$OUTPUT_DIR" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

import pikepdf

corpus, output = map(Path, sys.argv[1:])
manifest = json.loads((corpus / "manifest.json").read_text())
profile = json.loads((output / "scan_profile.json").read_text())

def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def intake_result(source_id, output_name):
    spec = manifest["sources"][source_id]
    source_path = corpus / spec["local_file"]
    assert sha256(source_path) == spec["sha256"], f"Provenance mismatch: {source_id}"
    intake_dir = output / output_name
    intake = json.loads((intake_dir / "ingestion_manifest.json").read_text())
    pages = sorted((intake_dir / "pages").glob("*.pdf"))
    expected = spec["expected_pages"]
    assert intake["summary"]["pages"] == expected == len(pages)
    assert [page.name[:6] for page in pages] == [f"{page_no:06d}" for page_no in range(1, expected + 1)]
    assert all(len(pikepdf.Pdf.open(page).pages) == 1 for page in pages)
    return len(pages)

nara_pages = intake_result("nara_rfk_release_packet", "nara_intake")
darien_pages = intake_result("darien_finance_packet", "darien_intake")
ti_pages = intake_result("ti_multipage_commercial_invoice", "ti_invoice_intake")
ti_spec = manifest["sources"]["ti_multipage_commercial_invoice"]
ti_intake_dir = output / "ti_invoice_intake"
ti_intake = json.loads((ti_intake_dir / "ingestion_manifest.json").read_text())
ti_reassembly = json.loads((output / "ti_invoice_reassembly.json").read_text())
ti_text = "\n".join((ti_intake_dir / page["text_file"]).read_text() for page in ti_intake["pages"])
assert [page["document_type"] for page in ti_intake["pages"]] == ["commercial_invoice", "commercial_invoice"]
assert all(page["reassembly_status"] == "unassigned" for page in ti_intake["pages"])
assert ti_spec["expected_reassembly"]["invoice_number"] in ti_text
assert all(marker in ti_text for marker in ti_spec["expected_reassembly"]["page_markers"])
assert ti_reassembly["summary"]["proposed_groups"] == 1
assert ti_reassembly["summary"]["exact_duplicate_candidates"] == 0
assert ti_reassembly["groups"][0]["source_page_range"] == ti_spec["expected_reassembly"]["source_page_range"]
assert ti_reassembly["groups"][0]["evidence"]["shared_identifiers"] == [ti_spec["expected_reassembly"]["invoice_number"]]

sroie = manifest["sources"]["sroie_receipt_subset"]
for receipt_id in sroie["selected_ids"]:
    expected = sroie["sha256"][receipt_id]
    assets = {
        "image": corpus / "originals" / f"sroie_{receipt_id}.jpg",
        "word_boxes": corpus / "ground_truth" / "sroie" / f"{receipt_id}.csv",
        "key_fields": corpus / "ground_truth" / "sroie" / f"{receipt_id}.json",
        "derived_pdf": corpus / "derived" / "sroie" / f"{receipt_id}.pdf",
    }
    assert {name: sha256(path) for name, path in assets.items()} == expected
    assert len(pikepdf.Pdf.open(assets["derived_pdf"]).pages) == 1

jbig2_path = corpus / manifest["sources"]["pdfium_jbig2_fixture"]["local_file"]
assert sha256(jbig2_path) == manifest["sources"]["pdfium_jbig2_fixture"]["sha256"]
jbig2_pages = [page for page in profile["pages"] if page["source_file"].endswith(jbig2_path.name)]
assert len(jbig2_pages) == 1 and jbig2_pages[0]["jbig2_suspect"] is True

result = {
    "profiled_pages": profile["summary"]["total_pages_profiled"],
    "nara_one_page_outputs": nara_pages,
    "darien_one_page_outputs": darien_pages,
    "multipage_invoice_one_page_outputs": ti_pages,
    "multipage_invoice_reassembly_evidence": "commercial_invoice on both pages; invoice 5527088322; PAGE 1 OF 2 and PAGE 2 OF 2; immutable intake remains unassigned and the later control emitted one review-required group proposal",
    "sroie_receipts_with_ground_truth": len(sroie["selected_ids"]),
    "jbig2_hazard_pages": len(jbig2_pages),
    "status": "passed",
    "scope_note": "Control-layer and corpus-integrity result only; OCR, handwriting, broad automatic reassembly, classification, and arithmetic accuracy remain unscored integrations.",
}
(output / "validation_summary.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
PY
