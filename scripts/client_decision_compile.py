#!/usr/bin/env python3
"""Compile returned client decisions into an operator-authorized change plan.

Six change types compile and authorize, but only ``template_registry_rule`` has
an implemented consumer: ``template_drift.py registry-update``. A plan built
from ``mapping_rule``, ``document_type_rule``, ``amendment``,
``allocation_policy_rule`` or ``source_quality_action`` compiles cleanly, binds
a real operator signature, and then fails at apply with "authorization contains
no template_registry_rule patches" -- after someone has signed. Choose the
change type against its consumer before compiling.

An operator correction to a client's wording goes in ``proposed_value``. This
command rejects a catalog whose ``decision_comment`` differs from the client's
note, so the returned text stays byte-exact through the chain.

``authorize`` binds ``compiled_plan_sha256``. Re-cutting the catalog produces a
different plan and needs a fresh signature; the previous one does not carry over.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

CHANGE_TYPES = {
    "mapping_rule": (
        "mapping",
        "consensus",
        "arithmetic",
        "validation",
        "completeness",
        "final_review",
    ),
    "template_registry_rule": (
        "classification",
        "mapping",
        "consensus",
        "validation",
        "completeness",
        "final_review",
    ),
    "document_type_rule": (
        "classification",
        "consensus",
        "validation",
        "completeness",
        "final_review",
    ),
    "amendment": (
        "extraction",
        "consensus",
        "arithmetic",
        "validation",
        "attribution",
        "completeness",
        "final_review",
    ),
    "allocation_policy_rule": (
        "mapping",
        "arithmetic",
        "attribution",
        "completeness",
        "final_review",
    ),
    "source_quality_action": (
        "source_quality",
        "extraction",
        "consensus",
        "arithmetic",
        "validation",
        "completeness",
        "final_review",
    ),
}
ACTIONABLE = {
    "Approve proposed routing",
    "Provide alternative in comment",
    "Confirm document family/type",
    "Confirm document type",
}
NON_ACTIONABLE = {
    "",
    "Internal only — no client choice",
    "Need source sample",
    "Defer",
    "Not applicable",
}
CHOICE_CHANGE_TYPES = {
    "Approve proposed routing": {
        "mapping_rule",
        "template_registry_rule",
        "document_type_rule",
        "allocation_policy_rule",
        "source_quality_action",
    },
    "Provide alternative in comment": set(CHANGE_TYPES),
    "Confirm document family/type": {"template_registry_rule", "document_type_rule"},
    "Confirm document type": {"document_type_rule"},
}


def load_object(path: Path, label: str) -> dict[str, Any]:
    """Load a JSON object."""
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def digest(value: Any) -> str:
    """Hash stable JSON."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def decision_groups(consolidation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index deterministic presentation groups."""
    groups = consolidation.get("decision_groups")
    if groups is None:
        groups = consolidation.get("client_presentation", {}).get("decision_groups")
    if not isinstance(groups, list):
        raise ValueError("consolidation must contain decision_groups")
    indexed = {}
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("group_id"), str):
            raise ValueError("decision_groups require string group_id values")
        if group["group_id"] in indexed:
            raise ValueError("decision group IDs must be unique")
        indexed[group["group_id"]] = group
    return indexed


def catalog_changes(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate and index explicit append-only changes."""
    changes = catalog.get("changes")
    if not isinstance(changes, list):
        raise ValueError("change catalog must contain changes")
    indexed = {}
    for change in changes:
        required_lists = (
            "affected_review_item_ids",
            "affected_document_ids",
            "affected_fields",
            "evidence_references",
        )
        if (
            not isinstance(change, dict)
            or not isinstance(change.get("change_id"), str)
            or not change["change_id"]
            or not isinstance(change.get("decision_id"), str)
            or not change["decision_id"]
            or change.get("change_type") not in CHANGE_TYPES
            or change.get("decision_choice") not in ACTIONABLE | NON_ACTIONABLE
            or not isinstance(change.get("target"), dict)
            or not change["target"]
            or "proposed_value" not in change
            or any(not isinstance(change.get(key), list) for key in required_lists)
            or any(not change[key] for key in required_lists)
        ):
            raise ValueError(
                "each catalog change requires IDs, a supported type, target, impact lists, evidence, and proposed_value"
            )
        if any(
            any(not isinstance(value, str) or not value for value in change[key])
            for key in required_lists
        ):
            raise ValueError("catalog impact and evidence lists require non-empty strings")
        if change["change_id"] in indexed:
            raise ValueError("change_id values must be unique")
        if "affected_amount" in change:
            if not isinstance(change["affected_amount"], str):
                raise ValueError("affected_amount must be an exact decimal string")
            try:
                amount = Decimal(change["affected_amount"])
            except InvalidOperation as exc:
                raise ValueError("affected_amount must be an exact decimal") from exc
            if not amount.is_finite():
                raise ValueError("affected_amount must be a finite decimal")
        indexed[change["change_id"]] = change
    return indexed


def validate_decisions(value: dict[str, Any]) -> list[dict[str, Any]]:
    """Require a complete, valid proposal-only workbook import."""
    if not value.get("proposal_only") or not value.get("complete"):
        raise ValueError("client decision import must be complete and proposal-only")
    if value.get("invalid_decisions") or value.get("unresolved_decision_ids"):
        raise ValueError("client decision import contains invalid or unresolved decisions")
    decisions = value.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("client decision import must contain decisions")
    lineage = (
        value.get("issued_workbook_sha256"),
        value.get("returned_workbook_sha256"),
        value.get("safe_consolidation_sha256"),
    )
    if any(
        not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item) for item in lineage
    ):
        raise ValueError("client decision import lineage requires exact SHA-256 values")
    if (
        isinstance(value.get("issued_group_count"), bool)
        or not isinstance(value.get("issued_group_count"), int)
        or value["issued_group_count"] != len(decisions)
        or isinstance(value.get("decision_count"), bool)
        or value.get("decision_count") != len(decisions)
    ):
        raise ValueError("client decision counts must match the complete imported decision set")
    return decisions


def plan_hash(plan: dict[str, Any]) -> str:
    """Calculate the compiler-controlled content hash."""
    return digest(
        {key: plan[key] for key in plan if key not in {"compiled_plan_sha256", "generated_at"}}
    )


def compile_plan(
    imported: dict[str, Any], consolidation: dict[str, Any], catalog: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compile decisions, exact impact, patches, and minimum rerun lanes."""
    imported_decisions = validate_decisions(imported)
    if imported["safe_consolidation_sha256"] != digest(consolidation):
        raise ValueError("client decision import targets a different safe consolidation")
    groups = decision_groups(consolidation)
    changes = catalog_changes(catalog)
    by_decision: dict[str, list[dict[str, Any]]] = {}
    for change in changes.values():
        by_decision.setdefault(change["decision_id"], []).append(change)
    patches = []
    deferred = []
    seen_decisions = set()
    for decision in imported_decisions:
        decision_id = decision.get("decision_id")
        if not isinstance(decision_id, str) or decision_id in seen_decisions:
            raise ValueError("decision_id values must be non-empty and unique")
        seen_decisions.add(decision_id)
        group = groups.get(decision_id)
        if group is None:
            raise ValueError(f"unknown decision group: {decision_id}")
        selected = decision.get("client_decision")
        if selected not in ACTIONABLE | NON_ACTIONABLE:
            raise ValueError(f"unsupported client decision: {selected}")
        if decision.get("proposal_only") is not True:
            raise ValueError(f"decision must remain proposal-only: {decision_id}")
        if decision.get("protected") is not group.get("protected"):
            raise ValueError(f"decision protection differs from consolidation: {decision_id}")
        proposed = by_decision.get(decision_id, [])
        if selected in ACTIONABLE:
            if group.get("protected"):
                raise ValueError(
                    f"protected decision cannot compile to a batch change: {decision_id}"
                )
            if not proposed:
                raise ValueError(f"actionable decision lacks a catalog change: {decision_id}")
            allowed_items = set(group.get("source_item_ids", []))
            allowed_documents = set(group.get("document_ids", []))
            for change in proposed:
                if change.get("decision_choice") != selected:
                    raise ValueError(f"catalog decision choice differs from client: {decision_id}")
                if change["change_type"] not in CHOICE_CHANGE_TYPES[selected]:
                    raise ValueError(
                        f"catalog change type is not permitted for client choice: {decision_id}"
                    )
                if selected == "Provide alternative in comment":
                    comment = decision.get("client_comment")
                    if (
                        not isinstance(comment, str)
                        or not comment.strip()
                        or change.get("decision_comment") != comment.strip()
                    ):
                        raise ValueError(
                            f"catalog decision comment differs from client alternative: {decision_id}"
                        )
                if not set(change["affected_review_item_ids"]) <= allowed_items:
                    raise ValueError(
                        f"change impact exceeds decision group items: {change['change_id']}"
                    )
                if not set(change["affected_document_ids"]) <= allowed_documents:
                    raise ValueError(
                        f"change impact exceeds decision group documents: {change['change_id']}"
                    )
                patch_id = "patch-" + digest(change)[:16]
                patches.append(
                    {
                        "patch_id": patch_id,
                        "change_id": change["change_id"],
                        "decision_id": decision_id,
                        "change_type": change["change_type"],
                        "append_only_payload": {
                            **change["target"],
                            "proposed_value": change["proposed_value"],
                            "evidence_references": change["evidence_references"],
                        },
                        "impact": {
                            "review_item_ids": sorted(set(change["affected_review_item_ids"])),
                            "document_ids": sorted(set(change["affected_document_ids"])),
                            "fields": sorted(set(change["affected_fields"])),
                            "affected_amount": str(change["affected_amount"])
                            if "affected_amount" in change
                            else None,
                        },
                        "rerun_lanes": list(CHANGE_TYPES[change["change_type"]]),
                        "operator_authorization_required": True,
                    }
                )
        else:
            deferred.append(
                {
                    "decision_id": decision_id,
                    "client_decision": selected,
                    "catalog_change_ids": sorted(item["change_id"] for item in proposed),
                }
            )
    missing = sorted(set(groups) - seen_decisions)
    if missing:
        raise ValueError(
            f"client decisions must exactly cover consolidation groups: {', '.join(missing)}"
        )
    orphaned = sorted(set(by_decision) - seen_decisions)
    if orphaned:
        raise ValueError(f"catalog contains unknown decision IDs: {', '.join(orphaned)}")
    rerun_lanes = []
    for lane in (lane for patch in patches for lane in patch["rerun_lanes"]):
        if lane not in rerun_lanes:
            rerun_lanes.append(lane)
    plan = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "proposal_only": True,
        "production_changes_applied": False,
        "source_lineage": {
            "issued_workbook_sha256": imported.get("issued_workbook_sha256"),
            "returned_workbook_sha256": imported.get("returned_workbook_sha256"),
            "decision_import_sha256": digest(imported),
            "safe_consolidation_sha256": imported["safe_consolidation_sha256"],
            "change_catalog_sha256": digest(catalog),
        },
        "patches": patches,
        "deferred_decisions": deferred,
        "impact_summary": {
            "patch_count": len(patches),
            "review_item_ids": sorted(
                {item for patch in patches for item in patch["impact"]["review_item_ids"]}
            ),
            "document_ids": sorted(
                {item for patch in patches for item in patch["impact"]["document_ids"]}
            ),
            "fields": sorted({item for patch in patches for item in patch["impact"]["fields"]}),
            "rerun_lanes": rerun_lanes,
        },
        "authorization_required": True,
    }
    plan["compiled_plan_sha256"] = plan_hash(plan)
    template = {
        "schema_version": "1.0",
        "compiled_plan_sha256": plan["compiled_plan_sha256"],
        "operator_id": "",
        "authorized_at": "",
        "patch_decisions": [
            {"patch_id": patch["patch_id"], "decision": "defer"} for patch in patches
        ],
        "instructions": "Set only reviewed patches to authorize; then run the authorize subcommand.",
    }
    return plan, template


def authorize(plan: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """Bind an explicit operator authorization to an unchanged compiled plan."""
    if plan.get("compiled_plan_sha256") != plan_hash(plan):
        raise ValueError("compiled plan hash does not match its content")
    if decision.get("compiled_plan_sha256") != plan["compiled_plan_sha256"]:
        raise ValueError("authorization template targets a different compiled plan")
    if not isinstance(decision.get("operator_id"), str) or not decision["operator_id"].strip():
        raise ValueError("operator_id is required")
    try:
        authorized_at = datetime.fromisoformat(
            str(decision["authorized_at"]).replace("Z", "+00:00")
        )
    except (KeyError, ValueError) as exc:
        raise ValueError("authorized_at must be an ISO-8601 timestamp") from exc
    if authorized_at.tzinfo is None:
        raise ValueError("authorized_at must include a UTC offset")
    values = decision.get("patch_decisions")
    if not isinstance(values, list):
        raise ValueError("patch_decisions must be a list")
    indexed = {}
    for item in values:
        if not isinstance(item, dict) or item.get("decision") not in {"authorize", "defer"}:
            raise ValueError("each patch decision must be authorize or defer")
        if item.get("patch_id") in indexed:
            raise ValueError("patch decisions must be unique")
        indexed[item.get("patch_id")] = item["decision"]
    expected = {patch["patch_id"] for patch in plan.get("patches", [])}
    if set(indexed) != expected:
        raise ValueError("patch decisions must exactly match the compiled plan")
    authorized = sorted(key for key, value in indexed.items() if value == "authorize")
    return {
        "schema_version": "1.0",
        "authorization_id": "authorization-" + digest(decision)[:16],
        "authorization_status": "operator_authorized",
        "operator_id": decision["operator_id"].strip(),
        "authorized_at": decision["authorized_at"],
        "authorized_patch_ids": authorized,
        "deferred_patch_ids": sorted(expected - set(authorized)),
        "compiled_plan": plan,
        "production_changes_applied": False,
        "next_step": "apply authorized append-only configuration changes, then execute the listed rerun lanes",
    }


def write_new(path: Path, value: dict[str, Any]) -> None:
    """Write a retained JSON output without clobbering."""
    if path.exists():
        raise ValueError(f"output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def require_new_distinct(*paths: Path) -> None:
    """Preflight a multi-artifact write before creating any output."""
    resolved = [path.resolve() for path in paths]
    if len(resolved) != len(set(resolved)):
        raise ValueError("output paths must be distinct")
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise ValueError(f"output already exists: {existing[0]}")


def main(argv: list[str] | None = None) -> int:
    """Compile or authorize an operator change plan."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    compile_command = commands.add_parser(
        "compile", help="build an impact preview and default-defer template"
    )
    compile_command.add_argument(
        "decisions", type=Path, help="complete workbook-import proposal JSON"
    )
    compile_command.add_argument("consolidation", type=Path, help="safe consolidation JSON")
    compile_command.add_argument(
        "catalog", type=Path, help="explicit append-only change catalog JSON"
    )
    compile_command.add_argument("--out", type=Path, required=True, help="new compiled plan JSON")
    compile_command.add_argument(
        "--authorization-template",
        type=Path,
        required=True,
        help="new default-defer authorization JSON",
    )
    authorize_command = commands.add_parser(
        "authorize", help="bind operator choices to an unchanged plan"
    )
    authorize_command.add_argument("plan", type=Path, help="compiled plan JSON")
    authorize_command.add_argument(
        "decisions", type=Path, help="completed operator authorization JSON"
    )
    authorize_command.add_argument(
        "--out", type=Path, required=True, help="new authorized plan JSON"
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "compile":
            require_new_distinct(args.out, args.authorization_template)
            plan, template = compile_plan(
                load_object(args.decisions, "decisions"),
                load_object(args.consolidation, "consolidation"),
                load_object(args.catalog, "catalog"),
            )
            write_new(args.out, plan)
            write_new(args.authorization_template, template)
        else:
            write_new(
                args.out,
                authorize(
                    load_object(args.plan, "compiled plan"),
                    load_object(args.decisions, "authorization decisions"),
                ),
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"Client decision compilation failed: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
