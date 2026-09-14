#!/usr/bin/env python3
"""Evaluate handwriting-region or party-role proposals against a labeled golden set."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from cli_help import apply_shared_help

TASKS = {"handwriting_region", "party_role"}


def load(path, label):
    """Load an object-shaped calibration input."""
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise ValueError(f"{label} requires items list")
    if value.get("task") not in TASKS:
        raise ValueError(f"{label} task must be handwriting_region or party_role")
    return value


def thresholds(value):
    """Parse a non-empty ordered list of usable score cutoffs."""
    try:
        result = sorted({float(item) for item in value.split(",")})
    except ValueError as exc:
        raise ValueError("thresholds must be comma-separated numbers") from exc
    if not result or any(item < 0 or item > 1 for item in result):
        raise ValueError("thresholds must be from 0 through 1")
    return result


def index(items, predictions=False):
    """Validate unique labels/predictions keyed by a stable golden-set item ID."""
    result = {}
    for item in items:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("item_id"), str)
            or not item["item_id"]
        ):
            raise ValueError("Calibration items require item_id")
        if item["item_id"] in result:
            raise ValueError("Calibration item_id must be unique")
        if not isinstance(item.get("label"), str) or not item["label"]:
            raise ValueError("Calibration items require label")
        if predictions:
            score = item.get("score")
            if not isinstance(score, (int, float)) or not 0 <= score <= 1:
                raise ValueError("Prediction items require score from 0 through 1")
        result[item["item_id"]] = item
    return result


def evaluate(truth, predictions, cutoff):
    """Calculate exact-label precision/recall while treating low scores as abstentions."""
    labels = sorted(
        {item["label"] for item in truth.values()}
        | {item["label"] for item in predictions.values()}
    )
    counts = {label: Counter() for label in labels}
    for item_id in set(truth) | set(predictions):
        actual = truth.get(item_id, {}).get("label")
        predicted = predictions.get(item_id)
        proposed = predicted.get("label") if predicted and predicted["score"] >= cutoff else None
        if actual == proposed and actual is not None:
            counts[actual]["true_positive"] += 1
        else:
            if actual is not None:
                counts[actual]["false_negative"] += 1
            if proposed is not None:
                counts[proposed]["false_positive"] += 1
    metrics = []
    for label in labels:
        values = counts[label]
        precision_denominator = values["true_positive"] + values["false_positive"]
        recall_denominator = values["true_positive"] + values["false_negative"]
        metrics.append(
            {
                "label": label,
                **values,
                "precision": values["true_positive"] / precision_denominator
                if precision_denominator
                else None,
                "recall": values["true_positive"] / recall_denominator
                if recall_denominator
                else None,
            }
        )
    return metrics


def calibrate(truth_input, predictions_input, cutoffs, minimum_precision, minimum_recall):
    """Evaluate all cutoffs; return a recommendation only for later client approval."""
    if truth_input["task"] != predictions_input["task"]:
        raise ValueError("Truth and predictions must use the same task")
    truth, predictions = index(truth_input["items"]), index(predictions_input["items"], True)
    results, eligible = [], []
    for cutoff in cutoffs:
        metrics = evaluate(truth, predictions, cutoff)
        complete = all(
            item["precision"] is not None and item["recall"] is not None for item in metrics
        )
        passes = complete and all(
            item["precision"] >= minimum_precision and item["recall"] >= minimum_recall
            for item in metrics
        )
        result = {"threshold": cutoff, "metrics": metrics, "meets_minimums": passes}
        results.append(result)
        if passes:
            eligible.append(cutoff)
    return {
        "schema_version": "1.0",
        "task": truth_input["task"],
        "golden_items": len(truth),
        "prediction_items": len(predictions),
        "minimum_precision": minimum_precision,
        "minimum_recall": minimum_recall,
        "evaluations": results,
        "recommended_threshold": max(eligible) if eligible else None,
        "deployment_permitted": False,
        "client_approval_required": True,
        "note": "A measured threshold is a calibration proposal, not permission to reduce review.",
    }


def run(truth_path, predictions_path, out_path, cutoff_text, minimum_precision, minimum_recall):
    """Write one no-clobber calibration report."""
    if not 0 <= minimum_precision <= 1 or not 0 <= minimum_recall <= 1:
        raise ValueError("minimum precision and recall must be from 0 through 1")
    out_path = Path(out_path)
    if out_path.exists():
        raise ValueError(f"Output already exists: {out_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = calibrate(
        load(truth_path, "truth"),
        load(predictions_path, "predictions"),
        thresholds(cutoff_text),
        minimum_precision,
        minimum_recall,
    )
    out_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Calibrate a detection proposal against labeled golden-set items."
    )
    parser.add_argument(
        "truth", help="Labeled ground-truth artifact the proposals are scored against."
    )
    parser.add_argument(
        "predictions", help="Detector proposal artifact scored against the labeled truth."
    )
    parser.add_argument("--out", required=True)
    parser.add_argument("--thresholds", default="0.5,0.75,0.9", help="Threshold grid to evaluate.")
    parser.add_argument(
        "--minimum-precision",
        type=float,
        default=0.99,
        help="Precision a threshold must reach to be reported as acceptable.",
    )
    parser.add_argument(
        "--minimum-recall",
        type=float,
        default=0.99,
        help="Recall a threshold must reach to be reported as acceptable.",
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        result = run(
            args.truth,
            args.predictions,
            args.out,
            args.thresholds,
            args.minimum_precision,
            args.minimum_recall,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Detection calibration failed: {exc}")
    print(
        json.dumps(
            {"task": result["task"], "recommended_threshold": result["recommended_threshold"]},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
