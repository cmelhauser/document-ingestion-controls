#!/usr/bin/env python3
"""Phase 3H: conservatively reconcile independent handwriting-recognition runs.

This module deliberately does not call a vendor. It is the vendor-neutral safety
boundary around HTR output: preserve every reading, require independent engine
agreement, and emit proposals or review work rather than changing source values.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help
from runtime_config import model_vendor

NUMERIC_TYPES = {
    "amount",
    "date",
    "numeric",
    "price_override",
    "quantity_correction",
    "quantity",
    "total_override",
    "cheque_number",
}
FINANCIAL_TYPES = {"amount", "price_override", "quantity_correction", "quantity", "total_override"}
SIGNATURE_TYPES = {"signature", "initials"}


def normalize(value, numeric):
    """Normalize a candidate for agreement only; retain the original reading."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if numeric:
        cleaned = text.replace(",", "").replace("$", "")
        try:
            return f"{float(cleaned):.4f}"
        except ValueError:
            return None
    return " ".join(text.casefold().split())


def region_key(annotation):
    """Create a stable key; an engine must identify its source region."""
    required = ("document_id", "page_id", "region_id")
    if not all(annotation.get(name) for name in required):
        raise ValueError("Each annotation needs document_id, page_id, and region_id")
    return tuple(str(annotation[name]) for name in required)


def provider_group(engine):
    """Derive a conservative provider group for extraction-lane HTR proposals.

    A routed engine resolves to the vendor behind the router, so an OpenRouter
    lane serving an OpenAI model cannot corroborate a direct OpenAI lane.
    """
    text = str(engine)
    prefix, _, remainder = text.partition("/")
    prefix = prefix.strip().casefold()
    if prefix.startswith("google") or prefix.startswith("gemini"):
        return "google"
    if prefix.startswith("openai"):
        return "openai"
    if prefix.startswith("openrouter"):
        return model_vendor("openrouter", remainder)
    return prefix or text


def extraction_run(data, path):
    """Convert visual-extraction handwriting readings to the HTR contract."""
    if not isinstance(data, list) or not any(
        isinstance(record, dict) and "handwriting_readings" in record for record in data
    ):
        return None
    if not all(isinstance(record, dict) for record in data):
        raise ValueError(f"Extraction HTR result {path} contains a non-object record")
    engines = {record.get("engine") for record in data}
    if len(engines) != 1:
        raise ValueError(f"Extraction HTR result {path} must contain one engine")
    engine = engines.pop()
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError(f"Extraction HTR result {path} has no engine name")
    annotations = []
    for record in data:
        readings = record.get("handwriting_readings", [])
        if not isinstance(readings, list):
            raise ValueError(f"Extraction HTR result {path} readings must be a list")
        region_items = record.get("handwriting_regions", [])
        if not isinstance(region_items, list):
            raise ValueError(f"Extraction HTR result {path} regions must be a list")
        if not all(isinstance(item, dict) for item in region_items):
            raise ValueError(f"Extraction HTR result {path} region must be an object")
        region_ids = [item.get("region_id") for item in region_items]
        if any(not isinstance(region_id, str) or not region_id.strip() for region_id in region_ids):
            raise ValueError(f"Extraction HTR result {path} region requires region_id")
        if len(set(region_ids)) != len(region_ids):
            raise ValueError(f"Extraction HTR result {path} has duplicate region_id")
        regions = dict(zip(region_ids, region_items, strict=True))
        for reading in readings:
            if not isinstance(reading, dict):
                raise ValueError(f"Extraction HTR result {path} reading must be an object")
            region_id = reading.get("region_id")
            if region_id not in regions:
                raise ValueError(
                    f"Extraction HTR result {path} reading region_id has no matching region"
                )
            region = regions[region_id]
            annotations.append(
                {
                    **reading,
                    "document_id": record.get("document_id"),
                    "page_id": record.get("page_id"),
                    "box": region.get("box")
                    or {
                        key: region.get(key)
                        for key in ("left", "top", "right", "bottom")
                        if region.get(key) is not None
                    },
                    "page_sha256": record.get("page_sha256"),
                    "raw_response": record.get("raw_response"),
                    "proposal_only": True,
                }
            )
    return str(engine), provider_group(engine), annotations


def load_run(path):
    """Load one HTR engine result using the documented annotations contract."""
    data = json.loads(Path(path).read_text())
    extraction = extraction_run(data, path)
    if extraction is not None:
        return extraction
    if isinstance(data, list):
        engine, group, annotations = Path(path).stem, Path(path).stem, data
    elif isinstance(data, dict) and isinstance(data.get("annotations"), list):
        engine = data.get("engine", Path(path).stem)
        group = data.get("independence_group", provider_group(engine))
        annotations = data["annotations"]
    else:
        raise ValueError(f"HTR result {path} must be an annotation list or object")
    if not isinstance(engine, str) or not engine.strip():
        raise ValueError(f"HTR result {path} has no engine name")
    if not isinstance(group, str) or not group.strip():
        raise ValueError(f"HTR result {path} has no independence group")
    return engine, group, annotations


def annotation_kind(annotation):
    """Use supplied semantic typing and reject untyped financial implications."""
    kind = str(annotation.get("semantic_type", "")).strip().lower()
    content = str(annotation.get("content_class", "")).strip().lower()
    numeric = content == "numeric" or kind in NUMERIC_TYPES
    return kind, numeric


def group_runs(paths):
    """Group unique engine readings per region and preserve duplicate-engine faults."""
    grouped, exceptions = defaultdict(dict), []
    for path in paths:
        engine, group, annotations = load_run(path)
        for annotation in annotations:
            if not isinstance(annotation, dict):
                raise ValueError(f"HTR result {path} contains a non-object annotation")
            key = region_key(annotation)
            if group in grouped[key]:
                existing_engine = grouped[key][group].get("_engine", group)
                exceptions.append(
                    {
                        "document_id": key[0],
                        "page_id": key[1],
                        "region_id": key[2],
                        "reason": "duplicate_engine_region"
                        if existing_engine == engine
                        else "duplicate_independence_group_region",
                        "engine": engine,
                        "independence_group": group,
                        "disposition": "client_review_required",
                    }
                )
                continue
            grouped[key][group] = {
                **annotation,
                "_engine": engine,
                "_independence_group": group,
            }
    return grouped, exceptions


def decide(key, readings, max_passes):
    """Apply strict HTR thresholds without changing the printed record."""
    annotations = list(readings.values())
    kinds = {annotation_kind(item)[0] for item in annotations}
    kind = next(iter(kinds)) if len(kinds) == 1 else ""
    numeric = all(annotation_kind(item)[1] for item in annotations)
    iteration = max((int(item.get("iteration", 1)) for item in annotations), default=1)
    engine_names = {group: str(item.get("_engine") or group) for group, item in readings.items()}
    result = {
        "document_id": key[0],
        "page_id": key[1],
        "region_id": key[2],
        "semantic_type": kind or None,
        "engines": sorted(engine_names.values()),
        "independence_groups": sorted(readings),
        "readings": {engine_names[group]: item.get("value") for group, item in readings.items()},
        "iteration": iteration,
        "max_passes": max_passes,
    }
    if not kind or len(kinds) != 1:
        result.update(
            reason="missing_or_conflicting_semantic_type", disposition="client_review_required"
        )
        return None, result
    if kind in SIGNATURE_TYPES:
        result.update(
            reason="signature_never_transcribed_as_data", disposition="client_review_required"
        )
        return None, result
    if iteration > max_passes:
        result.update(reason="iteration_limit_exceeded", disposition="client_review_required")
        return None, result
    normalized_by_group = {
        group: normalize(item.get("value"), numeric) for group, item in readings.items()
    }
    normalized = {engine_names[group]: value for group, value in normalized_by_group.items()}
    if any(value is None for value in normalized_by_group.values()):
        result.update(
            reason="missing_or_invalid_reading",
            normalized_readings=normalized,
            disposition="client_review_required",
        )
        return None, result
    clusters = defaultdict(list)
    for group, value in normalized_by_group.items():
        clusters[value].append(group)
    top_value, top_groups = max(clusters.items(), key=lambda item: len(item[1]))
    required = 3 if numeric else 2
    unanimous = len(top_groups) == len(readings)
    accepted = len(readings) >= required and (unanimous if numeric else len(top_groups) >= required)
    result.update(
        normalized_readings=normalized,
        normalized_value=top_value,
        agreeing_engines=sorted(engine_names[group] for group in top_groups),
        agreeing_independence_groups=sorted(top_groups),
        rule=("handwritten_numeric_unanimous_3plus" if numeric else "handwritten_text_2of3"),
        accepted=accepted,
    )
    if not accepted:
        result.update(
            reason="insufficient_independent_agreement",
            disposition="client_review_required",
            next_iteration_allowed=iteration < max_passes,
        )
        return None, result
    value = next(item.get("value") for group, item in readings.items() if group in top_groups)
    decision = {
        **result,
        "value": value,
        "disposition": "accepted_reading",
        "queue_for_review": not unanimous,
    }
    explicit = kind in FINANCIAL_TYPES or any(
        item.get("financial_amendment") for item in annotations
    )
    # A handwritten number nobody has typed is the case this control exists for,
    # and it was the one case it let through. `financial_amendment` is copied
    # from the supplied region and no detector on the commission run set it;
    # `semantic_type` came back `handwritten_text` for all 3,319 annotations, so
    # `kind` was never a financial type either. Both signals were therefore
    # always false, and 501 numeric handwritten readings were accepted as
    # ordinary text against a recommended review threshold of $0.
    #
    # A numeric reading is not thereby an amendment -- nothing here establishes
    # what a mark modifies, which is exactly what the reconciler reports as
    # `missing_or_conflicting_semantic_type`. It is routed to review because it
    # could be one and nothing has established that it is not, which is the same
    # rule an implausible value follows.
    if explicit or numeric:
        decision.update(
            disposition="amendment_proposal_requires_client_review",
            amendment_source="handwritten",
            client_review_required=True,
            financial_basis="typed_as_financial" if explicit else "untyped_numeric_reading",
        )
    return decision, None


def reconcile(paths, max_passes):
    """Return accepted readings, review-required amendments, and final exceptions."""
    grouped, exceptions = group_runs(paths)
    accepted, amendments = [], []
    for key, readings in sorted(grouped.items()):
        decision, exception = decide(key, readings, max_passes)
        if exception:
            exceptions.append(exception)
        elif decision.get("client_review_required"):
            amendments.append(decision)
        else:
            accepted.append(decision)
            if decision["queue_for_review"]:
                exceptions.append(
                    {
                        **decision,
                        "reason": "partial_text_disagreement",
                        "disposition": "client_review_recommended",
                    }
                )
    return accepted, amendments, exceptions


def main():
    parser = argparse.ArgumentParser(
        description="Reconcile independent HTR runs without mutating source values."
    )
    parser.add_argument("runs", nargs="+", help="independent HTR JSON result files")
    parser.add_argument("--out", required=True, help="accepted-reading and amendment-proposal JSON")
    parser.add_argument(
        "--exceptions", required=True, help="final client-review handwriting queue JSON"
    )
    parser.add_argument(
        "--max-passes",
        type=int,
        choices=(1, 2),
        default=2,
        help="Maximum reconciliation passes. Two passes may propose an amendment; one records readings only.",
    )
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        accepted, amendments, exceptions = reconcile(args.runs, args.max_passes)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Handwriting reconciliation failed: {exc}")
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "accepted_readings": len(accepted),
        "amendment_proposals": len(amendments),
        "client_review_items": len(exceptions),
        "max_passes": args.max_passes,
        "findings": [
            "Printed values were not overwritten; financial handwriting remains a client-review amendment proposal."
        ],
    }
    Path(args.out).write_text(
        json.dumps(
            {"summary": summary, "accepted_readings": accepted, "amendment_proposals": amendments},
            indent=2,
        )
        + "\n"
    )
    Path(args.exceptions).write_text(
        json.dumps({"summary": {"count": len(exceptions)}, "exceptions": exceptions}, indent=2)
        + "\n"
    )
    if not args.quiet:
        print(f"Accepted handwriting readings: {len(accepted)}")
        print(f"Client review items: {len(exceptions) + len(amendments)}")


if __name__ == "__main__":
    main()
