#!/usr/bin/env bash
# Acquire a small, opt-in external calibration corpus. Never commit its outputs.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CORPUS_DIR="$ROOT_DIR/fixtures/real_world_calibration"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/business-doc-real-calibration.XXXXXX")"

cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python runtime not found: $PYTHON_BIN" >&2
  echo "Create .venv and install requirements-dev.txt, or set PYTHON_BIN." >&2
  exit 1
fi
if [[ -e "$CORPUS_DIR/manifest.json" || -d "$CORPUS_DIR/originals" ]]; then
  echo "Calibration files already exist. Preserve them and validate instead of replacing them." >&2
  exit 1
fi

mkdir -p "$CORPUS_DIR/originals" "$CORPUS_DIR/ground_truth/sroie" "$CORPUS_DIR/derived/sroie"

curl --fail --location --retry 3 --output "$CORPUS_DIR/originals/nara_rfk_release_packet.pdf" \
  "https://www.archives.gov/files/research/rfk/releases/2025/0418/166-12c-1_serial_1_section_3_56-la-156_la_report_6.9.68-part_2_of_6.pdf"
curl --fail --location --retry 3 --output "$CORPUS_DIR/originals/darien_finance_packet.pdf" \
  "https://darienvillagewi.gov/vertical/sites/%7B555B48AD-D1F6-4E05-9690-9270063B57FB%7D/uploads/08.09.23_Finance_Packet.pdf"
curl --fail --location --retry 3 --output "$CORPUS_DIR/originals/ti_multipage_commercial_invoice.pdf" \
  "https://e2e.ti.com/cfs-file/__key/communityserver-discussions-components-files/151/Proforma_5F00_5527088322.pdf"
curl --fail --location --retry 3 --output "$WORK_DIR/pdfium_jbig2_textcomposite.b64" \
  "https://pdfium.googlesource.com/pdfium_tests/+/230bf55fb5c96c3946500a437b54e4400411262e/third_party/jbig2/bitmap-symbol-textcomposite.pdf?format=TEXT"
base64 -D -i "$WORK_DIR/pdfium_jbig2_textcomposite.b64" \
  -o "$CORPUS_DIR/originals/pdfium_jbig2_textcomposite.pdf"
for receipt_id in 001 002 003 016 017 189; do
  curl --fail --location --retry 3 --output "$CORPUS_DIR/originals/sroie_$receipt_id.jpg" \
    "https://raw.githubusercontent.com/zzzDavid/ICDAR-2019-SROIE/master/data/img/$receipt_id.jpg"
  curl --fail --location --retry 3 --output "$CORPUS_DIR/ground_truth/sroie/$receipt_id.csv" \
    "https://raw.githubusercontent.com/zzzDavid/ICDAR-2019-SROIE/master/data/box/$receipt_id.csv"
  curl --fail --location --retry 3 --output "$CORPUS_DIR/ground_truth/sroie/$receipt_id.json" \
    "https://raw.githubusercontent.com/zzzDavid/ICDAR-2019-SROIE/master/data/key/$receipt_id.json"
done

"$PYTHON_BIN" - "$CORPUS_DIR" <<'PY'
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image
import pikepdf

corpus = Path(sys.argv[1])
template = json.loads((corpus / "manifest.template.json").read_text())
for receipt_id in template["sources"]["sroie_receipt_subset"]["selected_ids"]:
    image_path = corpus / "originals" / f"sroie_{receipt_id}.jpg"
    pdf_path = corpus / "derived" / "sroie" / f"{receipt_id}.pdf"
    with Image.open(image_path) as image:
        image.convert("RGB").save(pdf_path, "PDF", resolution=150.0)

def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def page_count(path):
    with pikepdf.open(path) as pdf:
        return len(pdf.pages)

sources = template["sources"]
for source_id in ("nara_rfk_release_packet", "darien_finance_packet", "ti_multipage_commercial_invoice", "pdfium_jbig2_fixture"):
    path = corpus / sources[source_id]["local_file"]
    if page_count(path) != sources[source_id]["expected_pages"]:
        raise ValueError(f"Unexpected page count for {path}")
    sources[source_id]["sha256"] = sha256(path)

receipt_hashes = {}
for receipt_id in sources["sroie_receipt_subset"]["selected_ids"]:
    paths = {
        "image": corpus / "originals" / f"sroie_{receipt_id}.jpg",
        "word_boxes": corpus / "ground_truth" / "sroie" / f"{receipt_id}.csv",
        "key_fields": corpus / "ground_truth" / "sroie" / f"{receipt_id}.json",
        "derived_pdf": corpus / "derived" / "sroie" / f"{receipt_id}.pdf",
    }
    if page_count(paths["derived_pdf"]) != 1:
        raise ValueError(f"Expected a one-page receipt PDF for {receipt_id}")
    receipt_hashes[receipt_id] = {name: sha256(path) for name, path in paths.items()}
sources["sroie_receipt_subset"]["sha256"] = receipt_hashes
template["acquisition_date"] = datetime.now(UTC).isoformat()
(corpus / "manifest.json").write_text(json.dumps(template, indent=2) + "\n")
PY

echo "Acquired local calibration corpus at $CORPUS_DIR"
