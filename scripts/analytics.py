#!/usr/bin/env python3
"""Produce a gate-aware, non-decisional analytics summary.

This stage reports corpus shape and data-quality controls. Financial, attribution,
and completeness analytics remain explicitly blocked until their required client
reference inputs and final review gate are clear.
"""

import argparse
import collections
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from runtime_config import effective_settings_snapshot, env_bool, load_project_env


def records_from(data):
    """Read retained records from an artifact or a bare list of records."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("documents", "results", "records", "attributions"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    raise ValueError("Analytics input must be a record list or an object containing records")


def load_records(path):
    """Read the record list this report will summarize."""
    return records_from(json.loads(Path(path).read_text()))


def main():
    load_project_env()
    parser = argparse.ArgumentParser(description="Build a gate-aware analytics summary.")
    parser.add_argument("input", help="validated/proofed/normalized JSON")
    parser.add_argument("--review", help="final_client_review.json")
    parser.add_argument("--parties", help="parties.json")
    parser.add_argument("--completeness", help="completeness.json")
    parser.add_argument("--out", required=True)
    apply_shared_help(parser)
    args = parser.parse_args()
    if not env_bool("ANALYTICS_ENABLED", True):
        raise SystemExit("Analytics disabled; set ANALYTICS_ENABLED=true")

    records = load_records(args.input)
    review = json.loads(Path(args.review).read_text()) if args.review else {}
    parties = json.loads(Path(args.parties).read_text()) if args.parties else {}
    completeness = json.loads(Path(args.completeness).read_text()) if args.completeness else {}

    result = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "settings": effective_settings_snapshot(),
        "source": str(Path(args.input)),
        "corpus": {
            "documents": len(records),
            "document_types": dict(
                collections.Counter(str(r.get("document_type", "unknown")) for r in records)
            ),
            "review_status": dict(
                collections.Counter(str(r.get("review_status", "unknown")) for r in records)
            ),
            "handwriting_pages": sum(bool(r.get("has_handwriting")) for r in records),
            "arithmetic_status": dict(
                collections.Counter(str(r.get("arithmetic_status", "not_run")) for r in records)
            ),
        },
        "entity_resolution": parties.get("summary", {"status": "not_run"}),
        "completeness": completeness.get("summary", {"status": "not_run"}),
        "review_gate": review.get("summary", {"gate_status": "not_run"}),
        "financial_analytics": {
            "status": "blocked",
            "reason": "Final client review, attribution, and/or completeness prerequisites are not clear.",
        },
        "findings": [
            "This artifact is descriptive quality analytics, not an approval or financial conclusion.",
            "Financial analytics require a clear final-review gate and client-authorized reference inputs.",
        ],
    }
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(f"Analytics summary written to {args.out}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Analytics failed: {exc}")
