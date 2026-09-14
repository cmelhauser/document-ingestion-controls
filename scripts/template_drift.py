#!/usr/bin/env python3
"""Fingerprint source layouts, detect drift, and append authorized registry entries."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import registry_approval

ALGORITHM = "source_layout_v1"
LAYOUT_KEYS = ("sections", "totals", "table_regions", "column_boundaries")


def normalized(value: Any) -> Any:
    """Normalize layout observations without inventing semantic values."""
    if isinstance(value, str):
        return " ".join(value.casefold().split())
    if isinstance(value, list):
        return [normalized(item) for item in value]
    if isinstance(value, dict):
        return {key: normalized(value[key]) for key in sorted(value)}
    return value


def digest(value: Any) -> str:
    """Hash canonical JSON."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def layout_signature(template: dict[str, Any]) -> dict[str, Any]:
    """Return source-layout-only evidence used by the fingerprint."""
    headers = template.get("headers")
    if not isinstance(headers, list) or not headers:
        raise ValueError("each template requires non-empty headers")
    labels = []
    for header in headers:
        label = header.get("source_label") if isinstance(header, dict) else header
        if not isinstance(label, str) or not label.strip():
            raise ValueError("template headers require non-empty source_label values")
        labels.append(normalized(label))
    family = template.get("document_family")
    if not isinstance(family, str) or not family.strip():
        raise ValueError("each template requires a non-empty document_family")
    signature = {"document_family": normalized(family), "ordered_headers": labels}
    for key in LAYOUT_KEYS:
        if key in template:
            signature[key] = normalized(template[key])
    return signature


def fingerprint(template: dict[str, Any]) -> str:
    """Calculate the exact canonical layout fingerprint."""
    return digest({"algorithm": ALGORITHM, "layout": layout_signature(template)})


def validate_layout_signature(signature: Any) -> dict[str, Any]:
    """Require the exact canonical signature shape stored in the registry."""
    allowed = {"document_family", "ordered_headers", *LAYOUT_KEYS}
    if not isinstance(signature, dict) or set(signature) - allowed:
        raise ValueError("registry layout_signature contains unsupported fields")
    family = signature.get("document_family")
    headers = signature.get("ordered_headers")
    if not isinstance(family, str) or not family or normalized(family) != family:
        raise ValueError("registry layout_signature requires a canonical document_family")
    if (
        not isinstance(headers, list)
        or not headers
        or any(
            not isinstance(value, str) or not value or normalized(value) != value
            for value in headers
        )
    ):
        raise ValueError("registry layout_signature requires canonical ordered_headers")
    if normalized(signature) != signature:
        raise ValueError("registry layout_signature must be canonical")
    return signature


def signature_fingerprint(signature: dict[str, Any]) -> str:
    """Hash an already validated registry signature."""
    return digest({"algorithm": ALGORITHM, "layout": validate_layout_signature(signature)})


def legacy_header_fingerprint(template: dict[str, Any]) -> str:
    """Retain the prior header-only fingerprint for migration diagnostics."""
    labels = layout_signature(template)["ordered_headers"]
    return hashlib.sha256("\x1f".join(labels).encode()).hexdigest()


def load_object(path: Path, label: str) -> dict[str, Any]:
    """Load a JSON object."""
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def load_templates(path: Path) -> list[dict[str, Any]]:
    """Load unique source-template observations."""
    value = load_object(path, "templates")
    templates = value.get("templates")
    if not isinstance(templates, list) or not templates:
        raise ValueError("templates must contain a non-empty templates list")
    identifiers = []
    for template in templates:
        if not isinstance(template, dict) or not isinstance(template.get("template_id"), str):
            raise ValueError("each template requires a string template_id")
        identifiers.append(template["template_id"])
        layout_signature(template)
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("template_id values must be unique")
    return templates


def load_registry(path: Path | None) -> dict[str, Any]:
    """Load a registry or a safe empty baseline."""
    if path is None:
        return {
            "schema_version": "1.0",
            "registry_version": 0,
            "fingerprint_algorithm": ALGORITHM,
            "templates": [],
        }
    value = load_object(path, "registry")
    if (
        not isinstance(value.get("registry_version"), int)
        or isinstance(value.get("registry_version"), bool)
        or value["registry_version"] < 0
        or value.get("fingerprint_algorithm") != ALGORITHM
        or not isinstance(value.get("templates"), list)
    ):
        raise ValueError(f"registry must use {ALGORITHM} and contain templates")
    registry_ids = []
    fingerprints = []
    for item in value["templates"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("template_registry_id"), str)
            or not item["template_registry_id"]
            or not isinstance(item.get("template_id"), str)
            or not item["template_id"]
            or not registry_approval.is_approved(item)
        ):
            raise ValueError("registry templates require approved non-empty template identities")
        expected = signature_fingerprint(item.get("layout_signature"))
        if item.get("template_fingerprint") != expected:
            raise ValueError("registry template fingerprint does not match layout_signature")
        registry_ids.append(item["template_registry_id"])
        fingerprints.append(item["template_fingerprint"])
    if len(registry_ids) != len(set(registry_ids)) or len(fingerprints) != len(set(fingerprints)):
        raise ValueError("registry template identities and fingerprints must be unique")
    return value


def similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    """Score deterministic header overlap and order, never semantic equivalence."""
    first = left["ordered_headers"]
    second = right["ordered_headers"]
    union = set(first) | set(second)
    overlap = len(set(first) & set(second)) / len(union) if union else 1.0
    positions = sum(a == b for a, b in zip(first, second, strict=False)) / max(
        len(first), len(second), 1
    )
    family = float(left["document_family"] == right["document_family"])
    return round((0.5 * overlap) + (0.3 * positions) + (0.2 * family), 6)


def review_item(template_id: str, reason: str) -> dict[str, str]:
    """Create a standard fail-closed review item."""
    return {
        "priority": "high",
        "document_id": template_id,
        "page_id": template_id,
        "region_id": "",
        "field": "template_layout",
        "reason": reason,
        "review_source": "template_drift",
        "disposition": "client_review_required",
    }


def analyze(templates: list[dict[str, Any]], registry: dict[str, Any]) -> dict[str, Any]:
    """Classify exact matches, changed templates, and new templates."""
    approved = [item for item in registry["templates"] if registry_approval.is_approved(item)]
    results = []
    reviews = []
    for template in templates:
        signature = layout_signature(template)
        exact_hash = fingerprint(template)
        exact = [item for item in approved if item.get("template_fingerprint") == exact_hash]
        candidates = []
        for item in approved:
            prior = item.get("layout_signature")
            if not isinstance(prior, dict):
                continue
            score = similarity(signature, prior)
            if item.get("template_id") == template["template_id"] or score >= 0.5:
                candidates.append(
                    {"template_registry_id": item.get("template_registry_id"), "similarity": score}
                )
        candidates.sort(key=lambda item: (-item["similarity"], str(item["template_registry_id"])))
        if len(exact) == 1:
            status, reusable = "exact_approved_match", True
        elif len(exact) > 1:
            status, reusable = "ambiguous_exact_match", False
        elif candidates:
            status, reusable = "layout_drift", False
        else:
            status, reusable = "new_template", False
        if not reusable:
            reviews.append(review_item(template["template_id"], f"template_{status}"))
        results.append(
            {
                "template_id": template["template_id"],
                "document_family": signature["document_family"],
                "template_fingerprint": exact_hash,
                "legacy_header_fingerprint": legacy_header_fingerprint(template),
                "layout_signature": signature,
                "status": status,
                "mapping_reuse_permitted": reusable,
                "candidate_approved_templates": candidates,
            }
        )
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "fingerprint_algorithm": ALGORITHM,
        "registry_version": registry["registry_version"],
        "templates": results,
        "review": {
            "summary": {
                "client_review_items": len(reviews),
                "gate_status": "blocked_pending_client_review" if reviews else "clear",
            },
            "items": reviews,
        },
        "production_changes_applied": False,
    }


def canonical_plan_hash(plan: dict[str, Any]) -> str:
    """Recalculate the compiler-controlled plan hash."""
    value = {key: plan[key] for key in plan if key not in {"compiled_plan_sha256", "generated_at"}}
    return digest(value)


def update_registry(registry: dict[str, Any], authorization: dict[str, Any]) -> dict[str, Any]:
    """Append authorized template rules to a new registry snapshot."""
    compiled = authorization.get("compiled_plan")
    if (
        not isinstance(compiled, dict)
        or authorization.get("authorization_status") != "operator_authorized"
    ):
        raise ValueError("an operator-authorized compiled plan is required")
    if compiled.get("compiled_plan_sha256") != canonical_plan_hash(compiled):
        raise ValueError("compiled plan hash does not match its content")
    if (
        not isinstance(authorization.get("authorization_id"), str)
        or not authorization["authorization_id"]
    ):
        raise ValueError("authorization_id is required")
    authorized_values = authorization.get("authorized_patch_ids")
    if not isinstance(authorized_values, list) or any(
        not isinstance(value, str) or not value for value in authorized_values
    ):
        raise ValueError("authorized_patch_ids must contain non-empty strings")
    authorized = set(authorized_values)
    if len(authorized) != len(authorized_values):
        raise ValueError("authorized_patch_ids must be unique")
    known = {patch.get("patch_id") for patch in compiled.get("patches", [])}
    if not authorized <= known:
        raise ValueError("authorization contains unknown patch IDs")
    deferred_values = authorization.get("deferred_patch_ids")
    if not isinstance(deferred_values, list) or any(
        not isinstance(value, str) or not value for value in deferred_values
    ):
        raise ValueError("deferred_patch_ids must contain non-empty strings")
    deferred = set(deferred_values)
    if (
        len(deferred) != len(deferred_values)
        or authorized & deferred
        or authorized | deferred != known
    ):
        raise ValueError("authorized and deferred patch IDs must exactly partition the plan")
    patches = [
        patch
        for patch in compiled.get("patches", [])
        if patch.get("patch_id") in authorized
        and patch.get("change_type") == "template_registry_rule"
    ]
    if not patches:
        raise ValueError("authorization contains no template_registry_rule patches")
    existing_ids = {item.get("template_registry_id") for item in registry["templates"]}
    additions = []
    for patch in patches:
        payload = patch.get("append_only_payload")
        required = {
            "template_registry_id",
            "template_id",
            "template_fingerprint",
            "layout_signature",
        }
        if not isinstance(payload, dict) or not required <= payload.keys():
            raise ValueError("template registry patches require a complete append-only payload")
        if (
            not isinstance(payload["template_registry_id"], str)
            or not payload["template_registry_id"]
            or not isinstance(payload["template_id"], str)
            or not payload["template_id"]
        ):
            raise ValueError("template registry patches require non-empty template identities")
        if payload["template_fingerprint"] != signature_fingerprint(payload["layout_signature"]):
            raise ValueError("template registry patch fingerprint does not match layout_signature")
        if payload["template_registry_id"] in existing_ids:
            raise ValueError("template_registry_id already exists")
        additions.append(
            {
                **payload,
                **registry_approval.approval_provenance(authorization),
                "authorization_id": authorization["authorization_id"],
            }
        )
        existing_ids.add(payload["template_registry_id"])
    return {
        **registry,
        "registry_version": registry["registry_version"] + 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "parent_registry_version": registry["registry_version"],
        "templates": [*registry["templates"], *additions],
        "append_count": len(additions),
        "production_facts_changed": False,
    }


def write_new(path: Path, value: dict[str, Any]) -> None:
    """Write a JSON object without replacing retained output."""
    if path.exists():
        raise ValueError(f"output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    """Run template drift analysis or an authorized registry update."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("analyze", help="compare source layouts with an approved registry")
    check.add_argument("templates", type=Path, help="source-layout observation JSON")
    check.add_argument("--registry", type=Path, help="approved template registry JSON")
    check.add_argument("--out", type=Path, required=True, help="new drift analysis JSON")
    update = commands.add_parser(
        "registry-update", help="append operator-authorized template rules"
    )
    update.add_argument("registry", type=Path, help="current approved template registry JSON")
    update.add_argument("authorization", type=Path, help="operator-authorized compiled plan JSON")
    update.add_argument("--out", type=Path, required=True, help="new registry snapshot JSON")
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            result = analyze(load_templates(args.templates), load_registry(args.registry))
        else:
            result = update_registry(
                load_registry(args.registry), load_object(args.authorization, "authorization")
            )
        write_new(args.out, result)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Template drift control failed: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
