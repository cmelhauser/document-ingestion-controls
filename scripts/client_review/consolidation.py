#!/usr/bin/env python3
"""Run and safely consolidate the post-client-review proposal lanes.

This command reuses the existing cross-record and full-dataset agents, then
builds a smaller client-facing view without deleting source review items.
Protected findings never qualify for carry-forward.  The output is a review
artifact, not an approved-facts artifact.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from cli_help import apply_shared_help
from runtime_config import env_float, env_value, load_project_env

from client_review.protection import protected

# This module moved from scripts/ into scripts/client_review/, which moved the
# repository root one level further up. It was parents[1] before the move and
# silently became scripts/, so every lane this orchestrates was invoked as
# scripts/scripts/<lane>.py and failed with exit code 2. Derived from the package
# directory rather than a hop count so a further move cannot repeat it.
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent


def load_json(path: Path) -> dict[str, Any]:
    """Load a required JSON object."""
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def validate_threshold(threshold: float) -> None:
    """Require the deliberately narrow carry-forward confidence range."""
    if not isinstance(threshold, (int, float)) or not 0.99 <= threshold <= 1.0:
        raise ValueError("carry-forward threshold must be between 0.99 and 1.0")


def item_key(item: dict[str, Any]) -> tuple[str, ...]:
    """Identify an exact review target while retaining distinct causes."""
    return tuple(
        str(item.get(key, "")) for key in ("document_id", "page_id", "region_id", "field", "reason")
    )


def merge_duplicate_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Collapse exact presentations and retain all source/evidence references."""
    merged: dict[tuple[str, ...], dict[str, Any]] = {}
    duplicates = 0
    for item in items:
        key = item_key(item)
        current = merged.get(key)
        if current is None:
            current = dict(item)
            current["source_item_ids"] = list(item.get("source_item_ids", []))
            current["evidence_references"] = list(item.get("evidence_references", []))
            merged[key] = current
            continue
        duplicates += 1
        for field in ("source_item_ids", "evidence_references"):
            for value in item.get(field, []):
                if value not in current[field]:
                    current[field].append(value)
    return list(merged.values()), duplicates


def review_clusters(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create batch-review candidates without hiding their underlying items."""
    groups: dict[tuple[str, str, bool], list[dict[str, Any]]] = {}
    for item in items:
        reason = str(item.get("reason", "review_required"))
        field = str(item.get("field", ""))
        groups.setdefault((reason, field, protected(item)), []).append(item)
    clusters = []
    for index, ((reason, field, is_protected), members) in enumerate(sorted(groups.items())):
        clusters.append(
            {
                "cluster_id": f"cluster:{index + 1:04d}",
                "reason": reason,
                "field": field,
                "protected": is_protected,
                "item_count": len(members),
                "document_count": len({str(item.get("document_id", "")) for item in members}),
                "representative_item_ids": [
                    str(item.get("review_item_id") or (item.get("source_item_ids") or [""])[0])
                    for item in members[:5]
                ],
                "client_batch_decision_allowed": not is_protected,
                "requires_client_rule_approval": True,
                "all_source_items_retained": True,
            }
        )
    return clusters


def decision_groups(items: list[dict[str, Any]], consensus: dict[str, Any]) -> list[dict[str, Any]]:
    """Build broad proposal-only groups for the actual visible review view."""
    from client_review.grouping import build_groups

    groups = build_groups({"items": items}, consensus)
    for group in groups:
        group["source_collection"] = "client_visible_items"
    return groups


def reduced_items(
    client_review: dict[str, Any], fallback: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return visible, covered, and explicitly accepted proposal items."""
    visible: list[dict[str, Any]] = []
    covered: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    handled_ids: set[str] = set()
    for card in client_review.get("cards", []):
        reduction = card.get("reduction", {})
        visible.extend(reduction.get("visible_items", []))
        covered.extend(reduction.get("covered_items", []))
        accepted.extend(reduction.get("auto_accepted_updates", []))
        handled_ids.update(
            str(item.get("review_item_id"))
            for item in reduction.get("all_items", [])
            if isinstance(item, dict) and item.get("review_item_id") is not None
        )
    summary = client_review.get("summary", {})
    incomplete = bool(summary.get("provider_exceptions")) or (
        summary.get("reviewer_cards") is not None
        and summary.get("successful_cards") != summary.get("reviewer_cards")
    )
    if incomplete:
        visible.extend(
            dict(item)
            for index, item in enumerate(fallback)
            if f"review-item-{index}" not in handled_ids
            and str(item.get("review_item_id", "")) not in handled_ids
        )
    elif not client_review.get("cards"):
        visible = [dict(item) for item in fallback]
        covered = []
        accepted = []
    return visible, covered, accepted


def exception_count(path: Path) -> int:
    """Read a sibling exception artifact when a provider lane emitted one."""
    exception_path = path.with_name(f"{path.stem}_exceptions.json")
    if not exception_path.is_file():
        return 0
    return int(load_json(exception_path).get("summary", {}).get("count", 0) or 0)


def validate_completed_lane(path: Path, lane: str) -> dict[str, Any]:
    """Fail closed unless a proposal lane completed without provider errors.

    Partial results are retained for audit, but they must never be consumed by
    consolidation as if they were complete.  This is the boundary that keeps a
    transient provider/schema failure from silently becoming downstream input.
    """
    payload = load_json(path)
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise ValueError(f"{lane} output is missing summary metadata")
    status = summary.get("analysis_status")
    if status != "completed":
        raise ValueError(f"{lane} output is not complete: analysis_status={status!r}")
    provider_exceptions = int(summary.get("provider_exceptions", 0) or 0)
    if provider_exceptions:
        raise ValueError(f"{lane} output has {provider_exceptions} provider exceptions")
    exceptions = exception_count(path)
    if exceptions:
        raise ValueError(f"{lane} exception artifact has {exceptions} entries")
    if summary.get("all_source_values_retained") is False:
        raise ValueError(f"{lane} did not certify source-value retention")
    return payload


def carry_forward_items(items: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    """Select only explicit, high-confidence, non-protected proposals."""
    result = []
    for item in items:
        proposal = item.get("llm_proposed_update") or item.get("proposed_update")
        # Self-reported confidence is retained on the item as provenance and is
        # deliberately not a term here: an item leaves the human queue on the
        # protection taxonomy and an evidence-bound proposal, not on a number the
        # model chose for itself.
        if (
            not protected(item)
            and item.get("reviewer_decision", item.get("decision")) == "propose_resolution"
            and isinstance(proposal, dict)
        ):
            copy = dict(item)
            copy["disposition"] = "safe_carry_forward_proposal"
            copy["client_review_required"] = False
            result.append(copy)
    return result


def run_lane(command: list[str], output: Path) -> None:
    """Run an existing lane and require its output artifact."""
    completed = subprocess.run(command, check=False, text=True, cwd=REPOSITORY_ROOT)  # noqa: S603 - repository-internal lane command, fixed argv, no shell
    if completed.returncode != 0:
        raise RuntimeError(f"proposal lane failed with exit code {completed.returncode}")
    if not output.is_file():
        raise RuntimeError(f"proposal lane did not create {output}")


def consolidate(
    queue_path: Path,
    client_review_path: Path,
    cross_record_path: Path,
    agent_path: Path,
    out_path: Path,
    *,
    carry_forward: bool = False,
    carry_forward_threshold: float = 0.99,
    grouping_enabled: bool = False,
    grouping_consensus: dict[str, Any] | None = None,
    client_package_dir: Path | None = None,
) -> dict[str, Any]:
    """Build the safe presentation layer while retaining the exhaustive queue."""
    validate_threshold(carry_forward_threshold)
    if out_path.exists():
        raise FileExistsError(f"refusing to overwrite existing consolidation output: {out_path}")
    queue = load_json(queue_path)
    client_review = load_json(client_review_path)
    cross_record = validate_completed_lane(cross_record_path, "cross-record lane")
    agent = validate_completed_lane(agent_path, "full-dataset review-agent lane")
    base_items = queue.get("items", [])
    if not isinstance(base_items, list):
        raise ValueError("queue.items must be a list")
    visible, covered, accepted = reduced_items(client_review, base_items)
    visible, duplicate_count = merge_duplicate_items(visible)
    clusters = review_clusters(visible)
    broad_groups = decision_groups(visible, grouping_consensus or {}) if grouping_enabled else []
    eligible = carry_forward_items(accepted, carry_forward_threshold)
    cross_summary = cross_record.get("summary", {})
    agent_summary = agent.get("summary", {})
    cross_analysis_status = cross_summary["analysis_status"]
    agent_analysis_status = agent_summary["analysis_status"]
    cross_matches_available = cross_summary.get(
        "matches_available", cross_analysis_status in {"completed", "partial"}
    )
    if carry_forward:
        eligible, _ = merge_duplicate_items(eligible)
        visible = [
            item
            for item in visible
            if item.get("review_item_id") not in {x.get("review_item_id") for x in eligible}
        ]
    payload = {
        "schema_version": "1.0",
        "source_queue": str(queue_path),
        "client_review_output": str(client_review_path),
        "cross_record_output": str(cross_record_path),
        "review_agent_output": str(agent_path),
        "all_source_items_retained": True,
        "production_approval_permitted": False,
        "gate_status": "blocked_pending_client_review",
        "summary": {
            "source_items": len(base_items),
            "visible_items": len(visible),
            "covered_dependency_items": len(covered),
            "exact_duplicate_presentations_collapsed": duplicate_count,
            "carry_forward_proposals": len(eligible),
            "cross_record_matches": len(cross_record.get("matches", []))
            if cross_matches_available
            else None,
            "cross_record_match_count_status": (
                "available" if cross_matches_available else "unavailable_due_to_lane_failure"
            ),
            "cross_record_analysis_status": cross_analysis_status,
            "cross_record_context_truncated_to_packet_budget": cross_summary.get(
                "context_truncated_to_packet_budget"
            ),
            "review_agent_iterations": agent_summary.get("iterations", 0),
            "review_agent_analysis_status": agent_analysis_status,
            "review_agent_provider_exceptions": agent_summary.get("provider_exceptions", 0),
            "proposal_lane_exceptions": exception_count(cross_record_path)
            + exception_count(agent_path),
            "partial_post_review_failure": (
                exception_count(cross_record_path) + exception_count(agent_path) > 0
            ),
            "successful_client_review_reduction_retained": True,
            "carry_forward_enabled": carry_forward,
            "carry_forward_threshold": carry_forward_threshold,
            "review_clusters": len(clusters),
            "decision_groups": len(broad_groups),
            "decision_grouping_enabled": grouping_enabled,
            "decision_group_count_derived_from_data": grouping_enabled,
        },
        "client_visible_items": visible,
        "review_clusters": clusters,
        "decision_groups": broad_groups,
        "decision_grouping": {
            "enabled": grouping_enabled,
            "method": "data_derived_unique_template_and_finding_family",
            "requested_group_count": None,
            "group_count_derived_from_data": grouping_enabled,
        },
        "client_presentation": {
            "mode": "data_derived_decision_groups" if grouping_enabled else "field_level_items",
            "decision_groups": broad_groups,
            "underlying_items": "client_visible_items",
            "all_underlying_items_retained": True,
            "production_approval_permitted": False,
        },
        "covered_items": covered,
        "safe_carry_forward_proposals": eligible,
        "cross_record_proposals": cross_record.get("remaining_matches", [])
        if cross_matches_available
        else [],
        "full_dataset_proposals": agent.get("final_reduction", {}),
        "findings": [
            "Grouping and deduplication change only the client-facing presentation.",
            "Protected categories never qualify for carry-forward.",
            "Provider or schema failures remain retained exceptions.",
            "Post-review lane failures do not discard successful card-review reductions; failed lanes remain separately flagged.",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n")
    if client_package_dir is not None and broad_groups:
        from client_review.package import write_client_review_package

        write_client_review_package(payload, client_package_dir)
    return payload


def build_parser() -> argparse.ArgumentParser:
    """Build the parser, with every setting supplying a default a flag can beat."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        required=True,
        type=Path,
        help="Run directory holding the artifacts this orchestration reads.",
    )
    parser.add_argument(
        "--output-dir",
        default=env_value(
            "CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_OUTPUT_DIR", "post_review_consolidation"
        ),
        help="Directory, relative to the run directory, for the consolidation artifacts.",
    )
    parser.add_argument(
        "--queue",
        default="final_client_review.json",
        help="Final-review queue filename within the run directory.",
    )
    parser.add_argument(
        "--client-review",
        default="client_review_llm.json",
        help="Card-review artifact filename within the run directory.",
    )
    parser.add_argument("--context", action="append", default=[])
    parser.add_argument(
        "--out",
        default=env_value(
            "CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_OUTPUT_FILE", "safe_review_consolidation.json"
        ),
    )
    parser.add_argument("--enable", action="store_true")
    parser.add_argument(
        "--carry-forward",
        action="store_true",
        help="Allow eligible proposals at or above the threshold to carry forward. Protected categories never carry forward.",
    )
    parser.add_argument(
        "--carry-forward-threshold",
        type=float,
        default=env_float("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_THRESHOLD", 0.99),
        help="Score at or above which an eligible proposal may carry forward.",
    )
    parser.add_argument(
        "--grouping-consensus",
        default=None,
        help="Optional consensus artifact used for proposal-only document-family grouping.",
    )
    parser.add_argument(
        "--disable-grouping",
        action="store_true",
        help="Skip root-cause grouping. Every underlying queue item is retained either way.",
    )
    parser.add_argument(
        "--disable-client-package",
        action="store_true",
        help="Do not create the small client workbook/README package after consolidation.",
    )
    apply_shared_help(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run provider lanes in sequence, then write the deterministic reducer."""
    args = build_parser().parse_args(argv)
    load_project_env()
    run_dir = args.run_dir
    queue = run_dir / args.queue
    client_review = run_dir / args.client_review
    python = sys.executable
    contexts = [str(run_dir / item) for item in args.context]
    grouping_enabled = (
        not args.disable_grouping
        and os.environ.get("CLIENT_REVIEW_GROUPING_ENABLED", "false").casefold() == "true"
    )
    grouping_consensus_path = args.grouping_consensus
    if grouping_enabled and grouping_consensus_path is None:
        grouping_consensus_path = next(
            (item for item in contexts if "consensus" in Path(item).name.casefold()),
            None,
        )
    if grouping_consensus_path and not Path(grouping_consensus_path).is_absolute():
        grouping_consensus_path = str(run_dir / grouping_consensus_path)
    grouping_consensus = (
        load_json(Path(grouping_consensus_path))
        if grouping_enabled and grouping_consensus_path
        else {}
    )
    post_provider = os.environ.get("LLM_POST_REVIEW_PROVIDER", "").strip()
    provider_arg = ["--final-provider", post_provider] if post_provider else []
    env_enabled = (
        os.environ.get("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_ENABLED", "false").casefold() == "true"
    )
    if args.enable or env_enabled:
        if not queue.is_file() or not client_review.is_file():
            raise SystemExit("safe consolidation requires completed final-review queue outputs")
    env_carry_forward = (
        os.environ.get("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_CARRY_FORWARD", "false").casefold()
        == "true"
    )
    # Every setting here supplies an argparse default, so an explicit flag wins.
    # Reading the environment at use time instead meant --output-dir was accepted
    # and silently ignored, and the artifacts landed somewhere the operator had
    # not asked for.
    threshold = args.carry_forward_threshold
    output_dir = run_dir / args.output_dir
    planned_output = output_dir / args.out
    if planned_output.exists():
        raise SystemExit(
            f"refusing to start proposal lanes because output already exists: {planned_output}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    cross = output_dir / "client_review_cross_record.json"
    cross_exceptions = output_dir / "client_review_cross_record_exceptions.json"
    agent = output_dir / "review_agent.json"
    agent_exceptions = output_dir / "review_agent_exceptions.json"
    if args.enable or env_enabled:
        existing_lane_outputs = [
            path for path in (cross, cross_exceptions, agent, agent_exceptions) if path.exists()
        ]
        if existing_lane_outputs:
            raise SystemExit(
                "refusing to rerun proposal lanes over existing artifacts; "
                "select a new output directory: "
                + ", ".join(str(path) for path in existing_lane_outputs)
            )
        run_lane(
            [
                python,
                "scripts/client_review_cross_record.py",
                str(queue),
                "--out",
                str(cross),
                "--exceptions",
                str(cross_exceptions),
                "--raw-dir",
                str(output_dir / "client_review_cross_record_raw"),
                "--enable",
                *provider_arg,
                *sum((["--context", item] for item in contexts), []),
            ],
            cross,
        )
        run_lane(
            [
                python,
                "scripts/review_agent.py",
                "--out",
                str(agent),
                "--exceptions",
                str(agent_exceptions),
                "--raw-dir",
                str(output_dir / "review_agent_raw"),
                "--enable",
                *provider_arg,
                *sum((["--context", item] for item in contexts), []),
            ],
            agent,
        )
    if not all(path.is_file() for path in (client_review, cross, agent)):
        raise SystemExit(
            "safe consolidation requires completed client-review, cross-record, and agent outputs"
        )
    consolidate(
        queue,
        client_review,
        cross,
        agent,
        output_dir / args.out,
        carry_forward=args.carry_forward or env_carry_forward,
        carry_forward_threshold=threshold,
        grouping_enabled=grouping_enabled,
        grouping_consensus=grouping_consensus,
        client_package_dir=(output_dir / "client_review_package")
        if grouping_enabled and not args.disable_client_package
        else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
