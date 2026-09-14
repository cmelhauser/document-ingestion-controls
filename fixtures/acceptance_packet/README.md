# Acceptance Packet

This is a small, fictional control-layer acceptance packet. It is not client
evidence, an OCR benchmark, or a substitute for a redacted client calibration
set. Its purpose is to prove that the implemented JSON control stages hand off
correctly: consensus, arithmetic proof, party resolution, attribution,
completeness, and sampling.

`docs/TECHNICAL_DOCUMENTATION.pdf` is profiled as a real native-text PDF. The two
engine files are disclosed fixture extractions so this deterministic, offline
acceptance packet does not call optional live OpenAI, Gemini-on-Vertex,
OpenRouter, or Document AI adapters. It is a handoff/control test, not a provider
accuracy benchmark.

The operations acceptance companion verifies manifests, resumable stages, non-secret adapter contracts, preserved grayscale and threshold variants, privacy inventory, and reviewer CSV/HTML/XLSX exports. Run both from the repository root:

```bash
bash scripts/run_acceptance_packet.sh /private/tmp/business-doc-ingestion-acceptance
bash scripts/run_operations_acceptance.sh /private/tmp/business-doc-ingestion-operations
```

Expected result: `acceptance_summary.json` reports the measured native-text page
count, one immutable intake page and one disclosed classification exception for
every page currently rendered in the technical document, two consensus
documents, two proved documents, clear attribution and completeness gates, and
a reproducible sample plan. The final client-review gate is intentionally
blocked pending the disclosed per-page intake review items. The runner derives
all document-page expectations from the current PDF so documentation growth
does not make this acceptance contract stale.
