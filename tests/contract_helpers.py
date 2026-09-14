"""Test builders for strict, production-shaped artifact contracts."""

import hashlib
import json
from pathlib import Path


def write_consensus_handoff(path, records, provider, model, lane):
    """Write a hash-bound independent extraction handoff and raw evidence."""
    path = Path(path)
    prefix = {
        "openai": "openai",
        "google": "google_genai_vertex",
        "openrouter": "openrouter",
    }[provider]
    engine = f"{prefix}/{model}"
    normalized = []
    for index, source in enumerate(records, start=1):
        raw = path.parent / f"{path.stem}-{index}-raw.json"
        raw.write_text(json.dumps({"record": index}) + "\n")
        record = dict(source)
        record.update(
            {
                "engine": engine,
                "engine_version": model,
                "page_sha256": record.get("page_sha256", "a" * 64),
                "raw_response": str(raw),
                "raw_response_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
            }
        )
        normalized.append(record)
    records_sha256 = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    payload = {
        "schema_version": "independent_extraction_handoff_v1",
        "adapter_type": "ocr",
        "engine": engine,
        "provider": provider,
        "model": model,
        "lane": lane,
        "independence_group": provider,
        "records_sha256": records_sha256,
        "records": normalized,
        "raw_response_retention_required": True,
    }
    path.write_text(json.dumps(payload))
    return path
