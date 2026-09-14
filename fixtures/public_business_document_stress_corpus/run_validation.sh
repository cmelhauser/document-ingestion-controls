#!/usr/bin/env bash
# Run the public-PDF stress fixture through the implemented Phase 0.5–2 controls.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CORPUS_DIR="$ROOT_DIR/fixtures/public_business_document_stress_corpus"
OUTPUT_DIR="${1:-$ROOT_DIR/output/public_stress_validation}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python runtime not found: $PYTHON_BIN" >&2
  echo "Create .venv and install requirements-dev.txt, or set PYTHON_BIN." >&2
  exit 1
fi
if [[ -e "$OUTPUT_DIR" ]]; then
  echo "Output path already exists; choose a new empty path: $OUTPUT_DIR" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
"$PYTHON_BIN" "$CORPUS_DIR/build_corpus.py" --verify >"$OUTPUT_DIR/fixture_verification.json"
"$PYTHON_BIN" "$ROOT_DIR/scripts/scan_profile.py" \
  "$CORPUS_DIR/derived/public_business_document_stress_bundle.pdf" \
  --out "$OUTPUT_DIR/scan_profile.json" --quiet
"$PYTHON_BIN" "$ROOT_DIR/scripts/ingest_pages.py" \
  "$CORPUS_DIR/derived/public_business_document_stress_bundle.pdf" \
  --out "$OUTPUT_DIR/intake" >/dev/null

"$PYTHON_BIN" - "$CORPUS_DIR" "$OUTPUT_DIR" <<'PY'
import json
import sys
from pathlib import Path

import pikepdf

corpus, output = map(Path, sys.argv[1:])
expected = json.loads((corpus / "derived" / "public_business_document_stress_manifest.json").read_text())
profile = json.loads((output / "scan_profile.json").read_text())
intake = json.loads((output / "intake" / "ingestion_manifest.json").read_text())
exceptions = json.loads((output / "intake" / "classification_exceptions.json").read_text())
pages = sorted((output / "intake" / "pages").glob("*.pdf"))

count = expected["bundle_pages"]
assert intake["summary"]["pages"] == count == len(pages)
assert [page.name[:6] for page in pages] == [f"{number:06d}" for number in range(1, count + 1)]
assert all(len(pikepdf.Pdf.open(page).pages) == 1 for page in pages)

result = {
    "fixture_pages": count,
    "native_text_only_pages": profile["summary"]["native_text_only_pages"],
    "scan_branches": profile["summary"]["branch_routing"],
    "intake_one_page_outputs": len(pages),
    "rule_classified_pages": intake["summary"]["rule_classified_pages"],
    "exception_pages": exceptions["summary"]["count"],
    "status": "passed",
}
(output / "validation_summary.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
PY
