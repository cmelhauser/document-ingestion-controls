#!/usr/bin/env bash
# Exercise operational safeguards against contained, disposable data.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${1:-$ROOT_DIR/output/operations_acceptance}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python runtime not found: $PYTHON_BIN" >&2
  exit 1
fi
if [[ -e "$OUTPUT_DIR" ]]; then
  echo "Refusing to overwrite existing output: $OUTPUT_DIR" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
"$PYTHON_BIN" "$ROOT_DIR/scripts/operations.py" manifest \
  "$ROOT_DIR/fixtures/acceptance_packet/engine_a.json" \
  "$ROOT_DIR/fixtures/acceptance_packet/engine_b.json" \
  --config '{"acceptance":"operations"}' --out "$OUTPUT_DIR/run_manifest.json" >/dev/null
manifest_hash="$(shasum -a 256 "$OUTPUT_DIR/run_manifest.json" | awk '{print $1}')"
for stage in profile intake extract validate review; do
  "$PYTHON_BIN" "$ROOT_DIR/scripts/operations.py" stage "$OUTPUT_DIR/run_state.json" "$stage" \
    --manifest-hash "$manifest_hash" >/dev/null
done

"$PYTHON_BIN" "$ROOT_DIR/scripts/ingest_pages.py" "$ROOT_DIR/docs/TECHNICAL_DOCUMENTATION.pdf" \
  --out "$OUTPUT_DIR/intake" >/dev/null
"$PYTHON_BIN" "$ROOT_DIR/scripts/preprocess_pages.py" "$OUTPUT_DIR/intake/pages" \
  --out "$OUTPUT_DIR/preprocessing" --manifest "$OUTPUT_DIR/preprocessing_manifest.json" --dpi 150 >/dev/null
"$PYTHON_BIN" "$ROOT_DIR/scripts/operations.py" privacy "$OUTPUT_DIR/intake/text/"*.txt \
  --out "$OUTPUT_DIR/privacy_inventory.json" >/dev/null

"$PYTHON_BIN" "$ROOT_DIR/scripts/operations.py" adapter "$ROOT_DIR/fixtures/acceptance_packet/operations_adapter.json" \
  --type ocr --credential-env OCR_PROVIDER_TOKEN --out "$OUTPUT_DIR/adapter_contract.json" >/dev/null
"$PYTHON_BIN" "$ROOT_DIR/scripts/operations.py" adapter "$ROOT_DIR/fixtures/acceptance_packet/operations_llm_handoff.json" \
  --type llm_adjudication --credential-env OPENAI_API_KEY --out "$OUTPUT_DIR/llm_adjudication_contract.json" >/dev/null
cp "$ROOT_DIR/fixtures/acceptance_packet/final_client_review.json" "$OUTPUT_DIR/final_client_review_input.json"
"$PYTHON_BIN" "$ROOT_DIR/scripts/operations.py" review-export "$OUTPUT_DIR/final_client_review_input.json" \
  --csv "$OUTPUT_DIR/final_client_review.csv" --html "$OUTPUT_DIR/final_client_review.html" \
  --xlsx "$OUTPUT_DIR/final_client_review.xlsx" >/dev/null

OUTPUT_DIR="$OUTPUT_DIR" "$PYTHON_BIN" - <<'PY'
import json
import os
from pathlib import Path

out = Path(os.environ["OUTPUT_DIR"])
assert len(json.loads((out / "run_manifest.json").read_text())["artifacts"]) == 2
assert json.loads((out / "run_state.json").read_text())["completed"][-1] == "review"
assert json.loads((out / "preprocessing_manifest.json").read_text())["pages"]
assert json.loads((out / "adapter_contract.json").read_text())["record_count"] == 1
assert json.loads((out / "llm_adjudication_contract.json").read_text())["record_count"] == 1
assert json.loads((out / "privacy_inventory.json").read_text()) == {"findings": []}
assert "acceptance-1" in (out / "final_client_review.csv").read_text()
assert "acceptance-1" in (out / "final_client_review.html").read_text()
assert (out / "final_client_review.xlsx").is_file()
print(json.dumps({"operations_controls": "passed", "status": "passed"}))
PY
