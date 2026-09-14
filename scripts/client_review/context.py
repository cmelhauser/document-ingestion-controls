#!/usr/bin/env python3
"""Build a hash-bound, reasoning-only bundle for returned client comments.

The bundle preserves every client response and may include a deterministic,
stratified document pilot.  It is admissible context for proposal-only
reasoning, never independent consensus or canonical evidence.
"""

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from runtime_config import env_int, load_project_env

SCHEMA_VERSION = "1.0"
ARTIFACT_TYPE = "client_review_context_v1"


def load_object(path, label):
    """Read a JSON object, naming the artifact when it turns out not to be one."""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def source_records(path):
    """Load the retained records the preserved responses refer to."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = next(
            (
                data[key]
                for key in ("documents", "results", "records")
                if isinstance(data.get(key), list)
            ),
            None,
        )
    else:
        records = None
    if records is None or any(not isinstance(item, dict) for item in records):
        raise ValueError("records must contain a JSON array of objects")
    return records


def digest(path):
    """Return the content hash that binds this context to the artifact it came from."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_responses(value):
    """Reject a response artifact that does not match the preserved contract."""
    if not value.get("proposal_only"):
        raise ValueError("preserved responses must be proposal-only")
    if value.get("response_mismatches"):
        raise ValueError("preserved responses contain mismatches")
    responses = value.get("responses")
    if not isinstance(responses, list):
        raise ValueError("preserved responses must contain responses")
    if not responses:
        raise ValueError("preserved responses must contain at least one response")
    if value.get("all_decision_rows") is not None and not isinstance(
        value["all_decision_rows"], list
    ):
        raise ValueError("all_decision_rows must be a list when present")
    for response in responses:
        if not isinstance(response, dict) or not response.get("decision_id"):
            raise ValueError("each preserved response requires a decision_id")
    return responses


def stratified_records(records, maximum):
    """Select a deterministic pilot sample across the retained record strata."""
    if not isinstance(maximum, int) or maximum < 1:
        raise ValueError("max-documents must be positive")
    if len(records) <= maximum:
        return list(records)
    groups = defaultdict(list)
    for record in records:
        group = str(record.get("document_type") or record.get("model_document_type") or "unknown")
        groups[group].append(record)
    for values in groups.values():
        values.sort(
            key=lambda item: hashlib.sha256(str(item.get("document_id", "")).encode()).hexdigest()
        )
    selected = []
    target = min(maximum, len(records))
    while len(selected) < target:
        for group in sorted(groups):
            if groups[group]:
                selected.append(groups[group].pop(0))
                if len(selected) == maximum:
                    break
    return selected


def build_context(preserved, response_path, records=None, records_path=None, maximum=75):
    """Preserve every client response verbatim as hash-bound reasoning-only context."""
    responses = validate_responses(preserved)
    comments = [
        {
            "review_item_id": str(item["decision_id"]),
            "client_choice": item.get("your_choice", ""),
            "client_comment": item.get("your_note", ""),
            "field": "client_comment",
            "client_review_required": True,
            "evidence_role": "untrusted_client_context_not_source_evidence",
        }
        for item in responses
    ]
    selected = stratified_records(records or [], maximum) if records is not None else []
    return {
        "artifact_type": ARTIFACT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "proposal_only": True,
        "reasoning_only": True,
        "independent_consensus_input": False,
        "production_approval_permitted": False,
        "gate_status": "blocked_pending_client_review",
        "policy": {
            "client_comments_are_untrusted_context": True,
            "source_evidence_must_support_every_proposal": True,
            "comments_cannot_create_gl_or_payment_facts": True,
        },
        "source": {
            "responses_path": Path(response_path).name,
            "responses_sha256": digest(response_path),
            "records_path": Path(records_path).name if records_path else None,
            "records_sha256": digest(records_path) if records_path else None,
        },
        "summary": {
            "responses": len(comments),
            "pilot_records": len(selected),
            "pilot_max_documents": maximum if records is not None else 0,
        },
        "client_comments": comments,
        "pilot_records": selected,
    }


def run(responses_path, out_path, records_path=None, maximum=75):
    """Preserve client responses and select the deterministic pilot sample."""
    out_path = Path(out_path)
    if out_path.exists():
        raise FileExistsError(f"refusing to overwrite client review context: {out_path}")
    preserved = load_object(responses_path, "preserved responses")
    records = source_records(records_path) if records_path else None
    context = build_context(preserved, responses_path, records, records_path, maximum)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(context, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return context


def main():
    parser = argparse.ArgumentParser(
        description="Build reasoning-only context from client responses."
    )
    parser.add_argument("responses", help="client_responses_preserved.json")
    parser.add_argument("--records", help="consensus/proofed JSON for a deterministic pilot sample")
    load_project_env()
    parser.add_argument("--max-documents", type=int, default=None)
    parser.add_argument("--out", required=True, help="new client_review_context_v1 JSON")
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        run(
            args.responses,
            args.out,
            args.records,
            args.max_documents or env_int("CLIENT_REVIEW_CONTEXT_LLM_MAX_DOCUMENTS", 75),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Client review context written to {args.out}; consensus remains independent.")


if __name__ == "__main__":
    main()
