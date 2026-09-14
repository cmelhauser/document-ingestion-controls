#!/usr/bin/env bash
# Run every implemented control against a real, two-page public invoice.
#
# The input PDF is intentionally opt-in because source rights and retention must
# be reviewed by each deployment.  Acquire it first with:
#   fixtures/real_world_calibration/acquire.sh
#
# This is an acceptance test for the implemented control layer.  The two engine
# records below are independently entered, audited transcriptions of native text
# on the real source; they are not evidence that OCR, handwriting detection,
# document reassembly, or classifier integrations are implemented.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CORPUS_DIR="$ROOT_DIR/fixtures/real_world_calibration"
SOURCE_PDF="$CORPUS_DIR/originals/ti_multipage_commercial_invoice.pdf"
OUTPUT_DIR="${1:-$ROOT_DIR/output/real_document_acceptance}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python runtime not found: $PYTHON_BIN" >&2
  echo "Create .venv and install requirements-dev.txt, or set PYTHON_BIN." >&2
  exit 1
fi
if [[ ! -f "$SOURCE_PDF" ]]; then
  echo "Required real source is absent: $SOURCE_PDF" >&2
  echo "Run $CORPUS_DIR/acquire.sh first; it downloads the opt-in calibration corpus." >&2
  exit 1
fi
if [[ -e "$OUTPUT_DIR" ]]; then
  echo "Refusing to overwrite existing output: $OUTPUT_DIR" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

"$PYTHON_BIN" "$ROOT_DIR/scripts/scan_profile.py" "$SOURCE_PDF" \
  --out "$OUTPUT_DIR/source_profile.json" --quiet
"$PYTHON_BIN" "$ROOT_DIR/scripts/ingest_pages.py" "$SOURCE_PDF" \
  --out "$OUTPUT_DIR/intake" >/dev/null

OUTPUT_DIR="$OUTPUT_DIR" "$PYTHON_BIN" - <<'PY'
"""Write controlled companions without adding unobserved source-document data."""
import json
import hashlib
import os
from pathlib import Path

out = Path(os.environ["OUTPUT_DIR"])

# These values were manually audited against both pages of Texas Instruments
# invoice 5527088322.  The normalised ISO date and test job key are control
# fixtures; neither is represented as an original value on the source PDF.
header = {
    "seller_name": {"value": "Texas Instruments Asia Limited", "confidence": 0.99},
    "buyer_name": {
        "value": "BENCHMARK ELECTRONICS SINGAPORE IPO PTE LTD", "confidence": 0.99,
    },
    "invoice_number": {"value": "5527088322", "confidence": 0.99},
    "invoice_date": {"value": "2025-11-24", "confidence": 0.99},
    "purchase_order_number": {"value": "SPM574998-10", "confidence": 0.99},
    "subtotal": {"value": "270.00", "confidence": 0.99},
    "tax_amount": {"value": "0.00", "confidence": 0.99},
    "total_amount": {"value": "270.00", "confidence": 0.99},
}
line = {"line_number": 1, "quantity": "15000", "unit_price": "0.018000", "extended_amount": "270.00"}

configs = (
    ("audited_native_text_a", "openai", "audited-native-text-a", "consensus_primary"),
    ("audited_native_text_b", "google", "audited-native-text-b", "consensus_secondary"),
)
prefixes = {"openai": "openai", "google": "google_genai_vertex"}
for engine_name, provider, model, lane in configs:
    engine = f"{prefixes[provider]}/{model}"
    raw_path = out / f"{engine_name}_raw.json"
    raw_path.write_text(
        json.dumps(
            {
                "document_id": "TI-5527088322",
                "fixture_provider_identity": provider,
                "evidence_role": "manual_audit_control_fixture",
            }
        )
        + "\n"
    )
    record = {
        "engine": engine,
        "engine_version": model,
        "document_id": "TI-5527088322",
        "document_type": "commercial_invoice",
        "branch": "B",
        "source_pages": [1, 2],
        "source_evidence": {
            "kind": "manual_audit_of_native_text",
            "source_invoice_number": "5527088322",
            "source_page_markers": ["PAGE 1 OF 2", "PAGE 2 OF 2"],
            "notice": "Control-test fixture only; not OCR or reassembly output.",
        },
        "header": header,
        "lines": [line],
        "page_sha256": hashlib.sha256(b"TI-5527088322-pages-1-2").hexdigest(),
        "raw_response": str(raw_path),
        "raw_response_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }
    records = [record]
    handoff = {
        "schema_version": "independent_extraction_handoff_v1",
        "adapter_type": "ocr",
        "engine": engine,
        "provider": provider,
        "model": model,
        "lane": lane,
        "independence_group": provider,
        "records_sha256": hashlib.sha256(
            json.dumps(
                records,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest(),
        "records": records,
        "raw_response_retention_required": True,
    }
    (out / f"{engine_name}_handoff.json").write_text(
        json.dumps(handoff, indent=2) + "\n"
    )

# Controlled companions deliberately distinguish an expected control result from
# an independent customer accounting system.  The reference tests rank-3 PO
# attribution; GL/payment entries test reconciliation and aging closure.
(out / "reference.json").write_text(json.dumps({"acks": [{
    "ack_number": "CONTROL-5527088322", "date": "2025-11-24",
    "customer": "BENCHMARK ELECTRONICS SINGAPORE IPO PTE LTD", "amount": 270,
    "po_number": "SPM574998-10", "selling_location": "Shanghai",
}]}, indent=2) + "\n")
(out / "gl.json").write_text(json.dumps({"entries": [{
    "period": "2025-11", "vendor": "Texas Instruments Asia Limited", "amount": 270,
}]}, indent=2) + "\n")
(out / "payments.json").write_text(json.dumps({"payments": [{
    "invoice_number": "5527088322", "amount": 270,
}]}, indent=2) + "\n")
PY

"$PYTHON_BIN" "$ROOT_DIR/scripts/consensus.py" \
  "$OUTPUT_DIR/audited_native_text_a_handoff.json" \
  "$OUTPUT_DIR/audited_native_text_b_handoff.json" \
  --out "$OUTPUT_DIR/consensus.json" --exceptions "$OUTPUT_DIR/exceptions.json" --quiet
"$PYTHON_BIN" "$ROOT_DIR/scripts/arithmetic_check.py" \
  "$OUTPUT_DIR/consensus.json" --out "$OUTPUT_DIR/proofed.json" --quiet
"$PYTHON_BIN" "$ROOT_DIR/scripts/entity_resolve.py" \
  "$OUTPUT_DIR/proofed.json" --out "$OUTPUT_DIR/parties.json" --log "$OUTPUT_DIR/merges.json" --quiet
"$PYTHON_BIN" "$ROOT_DIR/scripts/attribution.py" \
  "$OUTPUT_DIR/proofed.json" --reference "$OUTPUT_DIR/reference.json" \
  --out "$OUTPUT_DIR/attributed.json" --register "$OUTPUT_DIR/unattributable.json" --quiet
"$PYTHON_BIN" "$ROOT_DIR/scripts/completeness.py" \
  "$OUTPUT_DIR/attributed.json" --gl "$OUTPUT_DIR/gl.json" --payments "$OUTPUT_DIR/payments.json" \
  --out "$OUTPUT_DIR/completeness.json" --quiet
"$PYTHON_BIN" "$ROOT_DIR/scripts/sampling.py" \
  "$OUTPUT_DIR/attributed.json" --materiality 100 --mus-n 1 --attribute-n 1 \
  --out "$OUTPUT_DIR/sample_plan.json" --quiet

OUTPUT_DIR="$OUTPUT_DIR" "$PYTHON_BIN" - <<'PY'
import json
import os
from pathlib import Path

out = Path(os.environ["OUTPUT_DIR"])

def load(name):
    return json.loads((out / name).read_text())

profile = load("source_profile.json")
intake = load("intake/ingestion_manifest.json")
consensus = load("consensus.json")
proofed = load("proofed.json")
parties = load("parties.json")
attributed = load("attributed.json")
completeness = load("completeness.json")
sample = load("sample_plan.json")

pages = intake["pages"]
assert profile["summary"]["total_pages_profiled"] == 2
assert intake["summary"]["pages"] == 2
assert [p["source_page_number"] for p in pages] == [1, 2]
assert [p["output_sort_key"] for p in pages] == ["000001", "000002"]
assert all(p["page_pdf"].endswith(".pdf") for p in pages)
assert all(p["document_type"] == "commercial_invoice" for p in pages)
assert all(p["reassembly_status"] == "unassigned" for p in pages)
assert consensus["summary"]["documents"] == 1
assert consensus["summary"]["exception_fields"] == 0
assert proofed["summary"]["proved"] == 1
assert parties["summary"]["party_mentions"] == 2
assert attributed["summary"]["gate_status"] == "clear"
assert attributed["documents"][0]["rank"] == 3
assert completeness["gate_status"] == "clear"
assert sample["population"]["eligible_documents"] == 1

summary = {
    "source": "public TI commercial invoice 5527088322",
    "source_pages": 2,
    "algorithms": {
        "scan_profile": "passed",
        "immutable_page_ingestion": "passed",
        "consensus": "passed",
        "arithmetic_proof": "passed",
        "entity_resolution": "passed",
        "attribution_rank_3_po_cross_reference": "passed",
        "completeness_reconciliation": "passed",
        "sampling_plan": "passed",
    },
    "single_page_outputs": [p["page_pdf"] for p in pages],
    "scope_note": (
        "The source PDF and audited source values are real. Downstream reference, GL, and payment "
        "companions are controlled fixtures. This test does not implement or validate OCR, handwriting "
        "detection, document reassembly, classifier, or CRM-load integrations."
    ),
    "status": "passed",
}
(out / "real_document_acceptance_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary, indent=2))
PY
