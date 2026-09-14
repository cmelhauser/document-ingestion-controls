#!/usr/bin/env python3
"""Evaluate a full pipeline result against client-authorized golden truth."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TRUTH_GROUP_KEYS = ("template_fingerprint", "document_family", "field", "risk_category")
BREAKDOWN_KEYS = (*TRUTH_GROUP_KEYS, "provider")
TELEMETRY_KEYS = ("latency_seconds", "estimated_cost_usd", "review_minutes")


def load_items(path: Path, label: str, prediction: bool = False) -> dict[str, Any]:
    """Load and validate a golden truth or prediction envelope."""
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise ValueError(f"{label} must be an object containing items")
    indexed: dict[str, dict[str, Any]] = {}
    for item in value["items"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("item_id"), str)
            or not item["item_id"]
        ):
            raise ValueError(f"{label} items require a non-empty item_id")
        if item["item_id"] in indexed:
            raise ValueError(f"{label} item_id values must be unique")
        if prediction:
            if item.get("status") not in {"predicted", "abstained", "failed"}:
                raise ValueError("prediction status must be predicted, abstained, or failed")
            if not isinstance(item.get("review_required"), bool) or not isinstance(
                item.get("auto_accepted"), bool
            ):
                raise ValueError("predictions require boolean review_required and auto_accepted")
            if item["auto_accepted"] and (item["review_required"] or item["status"] != "predicted"):
                raise ValueError(
                    "auto_accepted predictions must be predicted and not review-required"
                )
            if item["status"] == "predicted" and "predicted_value" not in item:
                raise ValueError("predicted items require predicted_value")
            for key in TELEMETRY_KEYS:
                metric = item.get(key, 0)
                if (
                    isinstance(metric, bool)
                    or not isinstance(metric, (int, float))
                    or not math.isfinite(metric)
                    or metric < 0
                ):
                    raise ValueError(f"prediction {key} must be a non-negative number")
        else:
            for key in ("expected_review_required", "protected", "baseline_review_required"):
                if not isinstance(item.get(key), bool):
                    raise ValueError(f"golden truth items require boolean {key}")
            if "expected_value" not in item:
                raise ValueError("golden truth items require expected_value")
            for key in TRUTH_GROUP_KEYS:
                if not isinstance(item.get(key), str) or not item[key]:
                    raise ValueError(f"golden truth items require non-empty {key}")
        indexed[item["item_id"]] = item
    return {"metadata": value.get("metadata", {}), "items": indexed}


def ratio(numerator: int, denominator: int) -> float | None:
    """Return a safe metric ratio."""
    return numerator / denominator if denominator else None


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate exact, abstention-aware metrics for one population."""
    total = len(rows)
    predicted = sum(row["predicted"] for row in rows)
    correct = sum(row["correct"] for row in rows)
    auto = sum(row["auto_accepted"] for row in rows)
    correct_auto = sum(row["auto_accepted"] and row["correct"] for row in rows)
    expected_review = sum(row["expected_review_required"] for row in rows)
    captured_review = sum(
        row["expected_review_required"] and row["review_required"] for row in rows
    )
    baseline_review = sum(row["baseline_review_required"] for row in rows)
    actual_review = sum(row["review_required"] for row in rows)
    return {
        "items": total,
        "predictions_present": sum(row["prediction_present"] for row in rows),
        "predicted_values": predicted,
        "correct_values": correct,
        "precision": ratio(correct, predicted),
        "recall": ratio(correct, total),
        "prediction_record_rate": ratio(sum(row["prediction_present"] for row in rows), total),
        "coverage": ratio(predicted, total),
        "abstentions_or_failures": sum(not row["predicted"] for row in rows),
        "abstention_or_failure_rate": ratio(sum(not row["predicted"] for row in rows), total),
        "auto_accepted": auto,
        "auto_accept_precision": ratio(correct_auto, auto),
        "false_auto_accepts": sum(row["false_auto_accept"] for row in rows),
        "protected_category_escapes": sum(row["protected_escape"] for row in rows),
        "expected_review_items": expected_review,
        "expected_review_captured": captured_review,
        "expected_review_recall": ratio(captured_review, expected_review),
        "baseline_review_items": baseline_review,
        "predicted_review_items": actual_review,
        "review_reduction_rate": ratio(baseline_review - actual_review, baseline_review),
        "arithmetic_status_checks": sum(row["arithmetic_checked"] for row in rows),
        "arithmetic_status_errors": sum(row["arithmetic_error"] for row in rows),
        "completeness_status_checks": sum(row["completeness_checked"] for row in rows),
        "completeness_status_errors": sum(row["completeness_error"] for row in rows),
        **{key: round(sum(row[key] for row in rows), 6) for key in TELEMETRY_KEYS},
    }


def evaluation_rows(
    truth: dict[str, dict[str, Any]], predictions: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Join truth to predictions without dropping missing or unexpected IDs."""
    rows = []
    for item_id, expected in truth.items():
        proposed = predictions.get(item_id)
        present = proposed is not None
        predicted = bool(present and proposed["status"] == "predicted")
        correct = bool(predicted and proposed["predicted_value"] == expected["expected_value"])
        review_required = bool(present and proposed["review_required"])
        auto_accepted = bool(present and proposed["auto_accepted"])
        arithmetic_checked = "expected_arithmetic_status" in expected
        completeness_checked = "expected_completeness_status" in expected
        rows.append(
            {
                "item_id": item_id,
                **{key: expected[key] for key in TRUTH_GROUP_KEYS},
                "provider": str(proposed.get("provider") or "unreported")
                if present
                else "unreported",
                "prediction_present": present,
                "predicted": predicted,
                "correct": correct,
                "review_required": review_required,
                "auto_accepted": auto_accepted,
                "false_auto_accept": auto_accepted and not correct,
                "protected_escape": auto_accepted
                and (expected["protected"] or expected["expected_review_required"]),
                "expected_review_required": expected["expected_review_required"],
                "baseline_review_required": expected["baseline_review_required"],
                "protected": expected["protected"],
                **{key: proposed.get(key, 0) if present else 0 for key in TELEMETRY_KEYS},
                "arithmetic_checked": arithmetic_checked,
                "arithmetic_error": arithmetic_checked
                and (
                    not present
                    or proposed.get("arithmetic_status") != expected["expected_arithmetic_status"]
                ),
                "completeness_checked": completeness_checked,
                "completeness_error": completeness_checked
                and (
                    not present
                    or proposed.get("completeness_status")
                    != expected["expected_completeness_status"]
                ),
            }
        )
    return rows


def review_item(row: dict[str, Any], reason: str) -> dict[str, str]:
    """Create a standard review record for an evaluation failure."""
    return {
        "priority": "critical" if row["protected"] else "high",
        "document_id": row["item_id"],
        "page_id": row["item_id"],
        "region_id": "",
        "field": row["field"],
        "reason": reason,
        "review_source": "golden_set_evaluation",
        "disposition": "client_review_required",
    }


def evaluate(
    truth_input: dict[str, Any],
    prediction_input: dict[str, Any],
    minimum_precision: float,
    minimum_recall: float,
    minimum_coverage: float,
    max_false_auto_accepts: int,
    max_protected_escapes: int,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Build overall and per-dimension acceptance evidence."""
    if any(
        value < 0 or value > 1 for value in (minimum_precision, minimum_recall, minimum_coverage)
    ):
        raise ValueError("minimum precision, recall, and coverage must be from 0 through 1")
    if max_false_auto_accepts < 0 or max_protected_escapes < 0:
        raise ValueError("maximum error counts must be non-negative")
    truth, predictions = truth_input["items"], prediction_input["items"]
    rows = evaluation_rows(truth, predictions)
    overall = summarize(rows)
    unexpected = sorted(predictions.keys() - truth.keys())
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for key in BREAKDOWN_KEYS:
            grouped[f"{key}:{row[key]}"].append(row)
    breakdowns = [
        {"dimension": name.split(":", 1)[0], "value": name.split(":", 1)[1], **summarize(items)}
        for name, items in sorted(grouped.items())
    ]
    failures = []
    for row in rows:
        if not row["prediction_present"]:
            failures.append(review_item(row, "golden_set_prediction_missing"))
        elif row["protected_escape"]:
            failures.append(review_item(row, "golden_set_protected_category_escape"))
        elif row["false_auto_accept"]:
            failures.append(review_item(row, "golden_set_false_auto_accept"))
        elif row["expected_review_required"] and not row["review_required"]:
            failures.append(review_item(row, "golden_set_expected_review_not_captured"))
        elif row["arithmetic_error"]:
            failures.append(review_item(row, "golden_set_arithmetic_status_mismatch"))
        elif row["completeness_error"]:
            failures.append(review_item(row, "golden_set_completeness_status_mismatch"))
    precision = overall["precision"]
    recall = overall["recall"]
    coverage = overall["coverage"]
    checks = {
        "precision": precision is not None and precision >= minimum_precision,
        "recall": recall is not None and recall >= minimum_recall,
        "coverage": coverage is not None and coverage >= minimum_coverage,
        "false_auto_accepts": overall["false_auto_accepts"] <= max_false_auto_accepts,
        "protected_category_escapes": overall["protected_category_escapes"]
        <= max_protected_escapes,
        "expected_review_recall": overall["expected_review_recall"] in {None, 1.0},
        "arithmetic_status": overall["arithmetic_status_errors"] == 0,
        "completeness_status": overall["completeness_status_errors"] == 0,
        "unexpected_predictions": not unexpected,
    }
    failures.extend(
        {
            "priority": "critical",
            "document_id": "golden-set",
            "page_id": "golden-set",
            "region_id": "",
            "field": check,
            "reason": f"golden_set_acceptance_check_failed:{check}",
            "review_source": "golden_set_evaluation",
            "disposition": "client_review_required",
        }
        for check, passed in checks.items()
        if not passed
    )
    report = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "truth_metadata": truth_input["metadata"],
        "prediction_metadata": prediction_input["metadata"],
        "thresholds": {
            "minimum_precision": minimum_precision,
            "minimum_recall": minimum_recall,
            "minimum_coverage": minimum_coverage,
            "max_false_auto_accepts": max_false_auto_accepts,
            "max_protected_category_escapes": max_protected_escapes,
        },
        "overall": overall,
        "breakdowns": breakdowns,
        "unexpected_prediction_ids": unexpected,
        "acceptance_checks": checks,
        "eligible_for_client_acceptance_review": all(checks.values()),
        "automation_authorized": False,
        "client_approval_required": True,
        "findings": [
            "Exact canonical values are compared without identifier or numeric coercion.",
            "A passing report is acceptance evidence only; it does not authorize automation.",
        ],
    }
    return report, failures


def run(
    truth_path: Path,
    predictions_path: Path,
    out_path: Path,
    exceptions_path: Path,
    minimum_precision: float,
    minimum_recall: float,
    minimum_coverage: float,
    max_false_auto_accepts: int,
    max_protected_escapes: int,
) -> dict[str, Any]:
    """Write no-clobber report and review artifacts."""
    if (
        out_path.resolve() == exceptions_path.resolve()
        or out_path.exists()
        or exceptions_path.exists()
    ):
        raise ValueError("output and exceptions paths must be distinct and new")
    report, failures = evaluate(
        load_items(truth_path, "truth"),
        load_items(predictions_path, "predictions", True),
        minimum_precision,
        minimum_recall,
        minimum_coverage,
        max_false_auto_accepts,
        max_protected_escapes,
    )
    for path in (out_path, exceptions_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n")
    exceptions_path.write_text(
        json.dumps(
            {
                "summary": {
                    "schema_version": "1.0",
                    "client_review_items": len(failures),
                    "gate_status": "blocked_pending_client_review" if failures else "clear",
                },
                "items": failures,
            },
            indent=2,
        )
        + "\n"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    """Run the golden-set evaluator CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("truth", type=Path, help="client-authorized golden truth JSON")
    parser.add_argument("predictions", type=Path, help="pipeline prediction JSON")
    parser.add_argument("--out", type=Path, required=True, help="new evaluation report JSON")
    parser.add_argument("--exceptions", type=Path, required=True, help="new review artifact JSON")
    parser.add_argument(
        "--minimum-precision", type=float, default=0.99, help="required exact-value precision"
    )
    parser.add_argument(
        "--minimum-recall", type=float, default=0.95, help="required exact-value recall"
    )
    parser.add_argument(
        "--minimum-coverage", type=float, default=1.0, help="required usable-prediction rate"
    )
    parser.add_argument(
        "--max-false-auto-accepts", type=int, default=0, help="maximum wrong auto-accepted values"
    )
    parser.add_argument(
        "--max-protected-escapes", type=int, default=0, help="maximum protected auto-acceptances"
    )
    args = parser.parse_args(argv)
    try:
        result = run(
            args.truth,
            args.predictions,
            args.out,
            args.exceptions,
            args.minimum_precision,
            args.minimum_recall,
            args.minimum_coverage,
            args.max_false_auto_accepts,
            args.max_protected_escapes,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Golden-set evaluation failed: {exc}")
    print(json.dumps({"eligible": result["eligible_for_client_acceptance_review"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
