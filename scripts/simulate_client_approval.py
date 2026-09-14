#!/usr/bin/env python3
"""Create a repeatable, simulation-only approval projection.

This command models a client approving every supplied review item. It changes
disposition in a separate artifact only; it never changes source evidence,
canonical review facts, or production retrieval eligibility.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help


def load_queue(path):
    """Load and validate a final-review queue without changing it."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("input must contain a final-review items list")
    return data


def require_new(path):
    """Refuse to overwrite a retained simulation artifact."""
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def simulate(queue):
    """Return an explicit all-approved simulation while retaining each item."""
    items = []
    for index, original in enumerate(queue["items"]):
        items.append(
            {
                "review_item_id": f"review-item-{index}",
                "simulated_client_decision": "approve",
                "simulated_accepted": True,
                "production_client_review_required": True,
                "original_item": original,
            }
        )
    return {
        "schema_version": "1.0-simulation",
        "artifact_type": "simulated_client_approval",
        "simulation_only": True,
        "production_approval_permitted": False,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "input_review_items": len(queue["items"]),
            "simulated_approved_items": len(items),
            "production_facts_approved": 0,
            "production_retrieval_eligible": False,
            "reason": (
                "Simulation changes disposition only; it does not create proof, "
                "independent agreement, approved values, or production approval."
            ),
        },
        "items": items,
    }


def downstream_gate(result):
    """Return the terminal stress-test gate without claiming production approval."""
    return {
        "schema_version": "1.0-simulation",
        "artifact_type": "simulated_downstream_gate",
        "simulation_only": True,
        "simulated_client_approval": "all_review_items_approved",
        "production_approved_facts_loaded": 0,
        "production_retrieval_status": "not_loaded_simulation_only",
        "documents_with_unresolved_fields": result["summary"]["input_review_items"],
        "reason": result["summary"]["reason"],
    }


def main():
    """Run the simulation CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queue", help="canonical final_client_review.json")
    parser.add_argument("--out", required=True, help="new simulation JSON output")
    parser.add_argument("--downstream-gate-out", help="optional terminal simulation gate output")
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        output = simulate(load_queue(args.queue))
        require_new(args.out).write_text(json.dumps(output, indent=2) + "\n")
        if args.downstream_gate_out:
            gate = downstream_gate(output)
            require_new(args.downstream_gate_out).write_text(json.dumps(gate, indent=2) + "\n")
        print(json.dumps(output["summary"], indent=2))
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"client approval simulation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
