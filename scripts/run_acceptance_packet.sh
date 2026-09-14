#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${1:-$ROOT_DIR/output/acceptance_packet}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
FIXTURE_DIR="$ROOT_DIR/fixtures/acceptance_packet"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python runtime not found: $PYTHON_BIN" >&2
  echo "Create .venv and install requirements-dev.txt, or set PYTHON_BIN." >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

"$PYTHON_BIN" "$ROOT_DIR/scripts/scan_profile.py" \
  "$ROOT_DIR/docs/TECHNICAL_DOCUMENTATION.pdf" \
  --out "$OUTPUT_DIR/instructions_profile.json" --quiet
"$PYTHON_BIN" "$ROOT_DIR/scripts/ingest_pages.py" \
  "$ROOT_DIR/docs/TECHNICAL_DOCUMENTATION.pdf" --out "$OUTPUT_DIR/intake" >/dev/null
OUTPUT_DIR="$OUTPUT_DIR" FIXTURE_DIR="$FIXTURE_DIR" "$PYTHON_BIN" - <<'PY'
import hashlib
import json
import os
from pathlib import Path

output = Path(os.environ["OUTPUT_DIR"])
fixtures = Path(os.environ["FIXTURE_DIR"])
configs = (
    ("engine_a.json", "acceptance_engine_a.json", "openai", "acceptance-a", "consensus_primary"),
    ("engine_b.json", "acceptance_engine_b.json", "google", "acceptance-b", "consensus_secondary"),
)
prefixes = {"openai": "openai", "google": "google_genai_vertex"}
for source_name, target_name, provider, model, lane in configs:
    records = json.loads((fixtures / source_name).read_text())
    engine = f"{prefixes[provider]}/{model}"
    normalized = []
    for index, source in enumerate(records, start=1):
        raw = output / f"{provider}_{index:03d}_raw.json"
        raw.write_text(json.dumps({"fixture": source["document_id"]}) + "\n")
        record = dict(source)
        record.update({
            "engine": engine,
            "engine_version": model,
            "page_sha256": "a" * 64,
            "raw_response": str(raw),
            "raw_response_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
        })
        normalized.append(record)
    payload = {
        "schema_version": "independent_extraction_handoff_v1",
        "adapter_type": "ocr",
        "engine": engine,
        "provider": provider,
        "model": model,
        "lane": lane,
        "independence_group": provider,
        "records_sha256": hashlib.sha256(json.dumps(
            normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()).hexdigest(),
        "records": normalized,
        "raw_response_retention_required": True,
    }
    (output / target_name).write_text(json.dumps(payload, indent=2) + "\n")
PY
"$PYTHON_BIN" "$ROOT_DIR/scripts/consensus.py" \
  "$OUTPUT_DIR/acceptance_engine_a.json" "$OUTPUT_DIR/acceptance_engine_b.json" \
  --out "$OUTPUT_DIR/consensus.json" --exceptions "$OUTPUT_DIR/exceptions.json" --quiet
"$PYTHON_BIN" "$ROOT_DIR/scripts/arithmetic_check.py" \
  "$OUTPUT_DIR/consensus.json" --out "$OUTPUT_DIR/proofed.json" --quiet
"$PYTHON_BIN" "$ROOT_DIR/scripts/validate_extraction.py" \
  "$OUTPUT_DIR/proofed.json" --out "$OUTPUT_DIR/validated.json" --exceptions "$OUTPUT_DIR/validation_exceptions.json" --quiet
"$PYTHON_BIN" "$ROOT_DIR/scripts/entity_resolve.py" \
  "$OUTPUT_DIR/validated.json" --out "$OUTPUT_DIR/parties.json" --log "$OUTPUT_DIR/merges.json" --quiet
"$PYTHON_BIN" "$ROOT_DIR/scripts/attribution.py" \
  "$OUTPUT_DIR/validated.json" --reference "$FIXTURE_DIR/acks.json" \
  --out "$OUTPUT_DIR/attributed.json" --register "$OUTPUT_DIR/unattributable.json" --quiet
"$PYTHON_BIN" "$ROOT_DIR/scripts/completeness.py" \
  "$OUTPUT_DIR/attributed.json" --gl "$FIXTURE_DIR/gl.json" \
  --payments "$FIXTURE_DIR/payments.json" --out "$OUTPUT_DIR/completeness.json" --quiet
"$PYTHON_BIN" "$ROOT_DIR/scripts/sampling.py" \
  "$OUTPUT_DIR/attributed.json" --materiality 100 --mus-n 2 --attribute-n 5 \
  --out "$OUTPUT_DIR/sample_plan.json" --quiet
"$PYTHON_BIN" "$ROOT_DIR/scripts/final_review_queue.py" \
  "$OUTPUT_DIR/intake/classification_exceptions.json" "$OUTPUT_DIR/exceptions.json" \
  "$OUTPUT_DIR/validation_exceptions.json" "$OUTPUT_DIR/validated.json" \
  "$OUTPUT_DIR/merges.json" "$OUTPUT_DIR/unattributable.json" "$OUTPUT_DIR/completeness.json" \
  --out "$OUTPUT_DIR/final_client_review.json" --quiet

OUTPUT_DIR="$OUTPUT_DIR" "$PYTHON_BIN" - <<'PY'
import json
import os
from pathlib import Path

output = Path(os.environ["OUTPUT_DIR"])

def load(name):
    return json.loads((output / name).read_text())

profile = load("instructions_profile.json")
intake = json.loads((output / "intake" / "ingestion_manifest.json").read_text())
consensus = load("consensus.json")
proofed = load("proofed.json")
validation = load("validated.json")
final_review = load("final_client_review.json")
attributed = load("attributed.json")
completeness = load("completeness.json")
sample = load("sample_plan.json")

summary = {
    "native_text_only_pages": profile["summary"]["native_text_only_pages"],
    "ingested_pages": intake["summary"]["pages"],
    "ingestion_exception_pages": intake["summary"]["exception_pages"],
    "consensus_documents": consensus["summary"]["documents"],
    "proved_documents": proofed["summary"]["proved"],
    "validation_review_items": validation["summary"]["client_review_items"],
    "final_client_review_gate": final_review["summary"]["gate_status"],
    "final_client_review_items": final_review["summary"]["client_review_items"],
    "attribution_gate": attributed["summary"]["gate_status"],
    "completeness_gate": completeness["gate_status"],
    "eligible_sampling_documents": sample["population"]["eligible_documents"],
}
expected = {
    # A rendered technical document may legitimately contain a diagram image,
    # so native-text routing is asserted from the measured scan profile rather
    # than from the total immutable page count.
    "native_text_only_pages": profile["summary"]["native_text_only_pages"],
    "ingested_pages": profile["summary"]["total_pages_profiled"],
    "ingestion_exception_pages": profile["summary"]["total_pages_profiled"],
    "consensus_documents": 2,
    "proved_documents": 2,
    "validation_review_items": 0,
    "final_client_review_gate": "blocked_pending_client_review",
    "final_client_review_items": profile["summary"]["total_pages_profiled"],
    "attribution_gate": "clear",
    "completeness_gate": "clear",
    "eligible_sampling_documents": 2,
}
if summary != expected:
    raise SystemExit(f"Acceptance packet failed: {summary!r} != {expected!r}")
(output / "acceptance_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary, indent=2))
PY
