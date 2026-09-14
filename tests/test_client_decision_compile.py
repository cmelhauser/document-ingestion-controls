"""Tests for proposal compilation and explicit operator authorization."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

compiler = importlib.import_module("client_decision_compile")


def write(path, value):
    path.write_text(json.dumps(value))
    return path


def inputs(decision="Approve proposed routing", protected=False):
    imported = {
        "proposal_only": True,
        "complete": True,
        "invalid_decisions": [],
        "unresolved_decision_ids": [],
        "issued_workbook_sha256": "a" * 64,
        "returned_workbook_sha256": "b" * 64,
        "issued_group_count": 1,
        "decision_count": 1,
        "decisions": [
            {
                "decision_id": "g1",
                "client_decision": decision,
                "client_comment": "use alternate"
                if decision == "Provide alternative in comment"
                else "",
                "protected": protected,
                "proposal_only": True,
            }
        ],
    }
    consolidation = {
        "decision_groups": [
            {
                "group_id": "g1",
                "protected": protected,
                "source_item_ids": ["r1", "r2"],
                "document_ids": ["d1"],
            }
        ]
    }
    catalog = {
        "changes": [
            {
                "change_id": "c1",
                "decision_id": "g1",
                "change_type": "mapping_rule",
                "decision_choice": decision,
                "decision_comment": "use alternate"
                if decision == "Provide alternative in comment"
                else "",
                "target": {"source_label": "Amount", "canonical_field": "total_amount"},
                "proposed_value": "total_amount",
                "affected_review_item_ids": ["r1"],
                "affected_document_ids": ["d1"],
                "affected_fields": ["total_amount"],
                "affected_amount": "10.00",
                "evidence_references": ["page-1#region-2"],
            },
            {
                "change_id": "c2",
                "decision_id": "g1",
                "change_type": "mapping_rule",
                "decision_choice": decision,
                "decision_comment": "use alternate"
                if decision == "Provide alternative in comment"
                else "",
                "target": {"record_id": "r1", "field": "total_amount"},
                "proposed_value": "10.00",
                "affected_review_item_ids": ["r2"],
                "affected_document_ids": ["d1"],
                "affected_fields": ["total_amount"],
                "evidence_references": ["page-1#region-2"],
            },
        ]
    }
    imported["safe_consolidation_sha256"] = compiler.digest(consolidation)
    return imported, consolidation, catalog


def test_compile_and_authorize():
    plan, template = compiler.compile_plan(*inputs())
    assert plan["impact_summary"]["patch_count"] == 2
    assert plan["impact_summary"]["rerun_lanes"][0] == "mapping"
    assert plan["production_changes_applied"] is False
    assert compiler.plan_hash(plan) == plan["compiled_plan_sha256"]
    template.update(operator_id="operator@example.com", authorized_at="2026-08-20T12:00:00Z")
    template["patch_decisions"][0]["decision"] = "authorize"
    result = compiler.authorize(plan, template)
    assert len(result["authorized_patch_ids"]) == 1
    assert len(result["deferred_patch_ids"]) == 1
    assert result["production_changes_applied"] is False


def test_deferred_decision_and_client_presentation_groups():
    imported, consolidation, catalog = inputs("Defer")
    consolidation = {"client_presentation": consolidation}
    imported["safe_consolidation_sha256"] = compiler.digest(consolidation)
    plan, template = compiler.compile_plan(imported, consolidation, {"changes": []})
    assert plan["patches"] == []
    assert plan["deferred_decisions"] == [
        {"decision_id": "g1", "client_decision": "Defer", "catalog_change_ids": []}
    ]
    decision = {
        **template,
        "operator_id": "op",
        "authorized_at": "2026-08-20T12:00:00+00:00",
    }
    result = compiler.authorize(plan, decision)
    assert result["authorized_patch_ids"] == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update(proposal_only=False), "complete and proposal-only"),
        (lambda value: value.update(complete=False), "complete and proposal-only"),
        (lambda value: value.update(invalid_decisions=["x"]), "invalid or unresolved"),
        (lambda value: value.update(decisions=None), "contain decisions"),
    ],
)
def test_decision_validation(mutate, message):
    value = inputs()[0]
    mutate(value)
    with pytest.raises(ValueError, match=message):
        compiler.validate_decisions(value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(issued_group_count=True),
        lambda value: value.update(issued_group_count=2),
        lambda value: value.update(decision_count=True),
        lambda value: value.update(decision_count=2),
    ],
)
def test_decision_counts_must_exactly_match(mutate):
    value = inputs()[0]
    mutate(value)
    with pytest.raises(ValueError, match="counts must match"):
        compiler.validate_decisions(value)


def test_group_and_catalog_validation(tmp_path):
    with pytest.raises(ValueError, match="decision_groups"):
        compiler.decision_groups({})
    with pytest.raises(ValueError, match="string group_id"):
        compiler.decision_groups({"decision_groups": [None]})
    group = inputs()[1]["decision_groups"][0]
    with pytest.raises(ValueError, match="unique"):
        compiler.decision_groups({"decision_groups": [group, group]})
    with pytest.raises(ValueError, match="contain changes"):
        compiler.catalog_changes({})
    bad = inputs()[2]
    bad["changes"][0]["change_type"] = "unknown"
    with pytest.raises(ValueError, match="requires IDs"):
        compiler.catalog_changes(bad)
    bad = inputs()[2]
    bad["changes"][0]["affected_amount"] = "not-decimal"
    with pytest.raises(ValueError, match="exact decimal"):
        compiler.catalog_changes(bad)
    bad = inputs()[2]
    bad["changes"][0]["affected_amount"] = "NaN"
    with pytest.raises(ValueError, match="finite decimal"):
        compiler.catalog_changes(bad)
    bad = inputs()[2]
    bad["changes"][0]["affected_amount"] = 1
    with pytest.raises(ValueError, match="decimal string"):
        compiler.catalog_changes(bad)
    bad = inputs()[2]
    bad["changes"][0]["evidence_references"] = [""]
    with pytest.raises(ValueError, match="non-empty strings"):
        compiler.catalog_changes(bad)
    bad = inputs()[2]
    bad["changes"][0]["affected_fields"] = []
    with pytest.raises(ValueError, match="requires IDs"):
        compiler.catalog_changes(bad)
    bad = inputs()[2]
    bad["changes"][0]["change_id"] = ""
    with pytest.raises(ValueError, match="requires IDs"):
        compiler.catalog_changes(bad)
    duplicate = inputs()[2]
    duplicate["changes"] = duplicate["changes"] * 2
    with pytest.raises(ValueError, match="unique"):
        compiler.catalog_changes(duplicate)
    with pytest.raises(ValueError, match="JSON object"):
        compiler.load_object(write(tmp_path / "list.json", []), "value")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("protected", "protected decision"),
        ("missing_catalog", "lacks a catalog"),
        ("item_scope", "exceeds decision group items"),
        ("document_scope", "exceeds decision group documents"),
        ("unknown_group", "unknown decision group"),
        ("duplicate_decision", "non-empty and unique"),
        ("orphan_catalog", "unknown decision IDs"),
        ("unsupported_decision", "unsupported client decision"),
    ],
)
def test_compile_fail_closed(change, message):
    imported, consolidation, catalog = inputs()
    if change == "protected":
        consolidation["decision_groups"][0]["protected"] = True
        imported["decisions"][0]["protected"] = True
        imported["safe_consolidation_sha256"] = compiler.digest(consolidation)
    elif change == "missing_catalog":
        catalog["changes"] = []
    elif change == "item_scope":
        catalog["changes"][0]["affected_review_item_ids"] = ["outside"]
    elif change == "document_scope":
        catalog["changes"][0]["affected_document_ids"] = ["outside"]
    elif change == "unknown_group":
        imported["decisions"][0]["decision_id"] = "outside"
    elif change == "duplicate_decision":
        imported["decisions"] *= 2
        imported["decision_count"] = 2
        imported["issued_group_count"] = 2
    elif change == "unsupported_decision":
        imported["decisions"][0]["client_decision"] = "yes"
    else:
        catalog["changes"][0]["decision_id"] = "outside"
    with pytest.raises(ValueError, match=message):
        compiler.compile_plan(imported, consolidation, catalog)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("missing_workbook_hash", "lineage"),
        ("wrong_consolidation", "consolidation"),
        ("missing_decision", "exactly cover"),
        ("choice_mismatch", "decision choice"),
        ("alternative_comment", "decision comment"),
        ("choice_change_type", "change type"),
    ],
)
def test_compile_binds_workbook_consolidation_and_choice_semantics(change, message):
    decision = (
        "Confirm document type"
        if change == "choice_change_type"
        else "Provide alternative in comment"
        if change == "alternative_comment"
        else "Approve proposed routing"
    )
    imported, consolidation, catalog = inputs(decision)
    if change == "missing_workbook_hash":
        imported.pop("issued_workbook_sha256")
    elif change == "wrong_consolidation":
        consolidation["decision_groups"][0]["document_ids"] = ["changed"]
    elif change == "missing_decision":
        imported["decisions"] = []
        imported["decision_count"] = 0
        imported["issued_group_count"] = 0
    elif change == "choice_mismatch":
        catalog["changes"][0]["decision_choice"] = "Confirm document type"
    elif change == "alternative_comment":
        catalog["changes"][0]["decision_comment"] = "different"
    else:
        catalog["changes"][0]["change_type"] = "mapping_rule"
    with pytest.raises(ValueError, match=message):
        compiler.compile_plan(imported, consolidation, catalog)


@pytest.mark.parametrize(
    "change",
    ["not_proposal", "protection", "alternative_not_string", "alternative_blank"],
)
def test_compile_rejects_unbound_decision_fields(change):
    decision = "Provide alternative in comment" if change.startswith("alternative") else "Defer"
    imported, consolidation, catalog = inputs(decision)
    if decision == "Defer":
        catalog["changes"] = []
    if change == "not_proposal":
        imported["decisions"][0]["proposal_only"] = False
    elif change == "protection":
        imported["decisions"][0]["protected"] = True
    elif change == "alternative_not_string":
        imported["decisions"][0]["client_comment"] = None
    else:
        imported["decisions"][0]["client_comment"] = "   "
    with pytest.raises(ValueError):
        compiler.compile_plan(imported, consolidation, catalog)


def test_compile_accepts_exactly_bound_alternative_comment():
    plan, _ = compiler.compile_plan(*inputs("Provide alternative in comment"))
    assert len(plan["patches"]) == 2


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("plan_hash", "plan hash"),
        ("target_hash", "different compiled plan"),
        ("operator", "operator_id"),
        ("timestamp", "ISO-8601"),
        ("timestamp_offset", "UTC offset"),
        ("not_list", "must be a list"),
        ("bad_decision", "authorize or defer"),
        ("duplicate", "unique"),
        ("mismatch", "exactly match"),
    ],
)
def test_authorization_validation(change, message):
    plan, decision = compiler.compile_plan(*inputs())
    decision.update(operator_id="op", authorized_at="2026-08-20T12:00:00Z")
    if change == "plan_hash":
        plan["proposal_only"] = False
    elif change == "target_hash":
        decision["compiled_plan_sha256"] = "wrong"
    elif change == "operator":
        decision["operator_id"] = ""
    elif change == "timestamp":
        decision["authorized_at"] = "not-a-date"
    elif change == "timestamp_offset":
        decision["authorized_at"] = "2026-08-20T12:00:00"
    elif change == "not_list":
        decision["patch_decisions"] = None
    elif change == "bad_decision":
        decision["patch_decisions"][0]["decision"] = "yes"
    elif change == "duplicate":
        decision["patch_decisions"].append(decision["patch_decisions"][0])
    else:
        decision["patch_decisions"].pop()
    with pytest.raises(ValueError, match=message):
        compiler.authorize(plan, decision)


def test_write_and_main_paths(monkeypatch, tmp_path):
    imported, consolidation, catalog = inputs()
    paths = [
        write(tmp_path / "imported.json", imported),
        write(tmp_path / "consolidation.json", consolidation),
        write(tmp_path / "catalog.json", catalog),
    ]
    out, template_path = tmp_path / "plan.json", tmp_path / "authorization.json"
    assert (
        compiler.main(
            [
                "compile",
                *map(str, paths),
                "--out",
                str(out),
                "--authorization-template",
                str(template_path),
            ]
        )
        == 0
    )
    decision = json.loads(template_path.read_text())
    decision.update(operator_id="op", authorized_at="2026-08-20T12:00:00Z")
    write(tmp_path / "decision.json", decision)
    assert (
        compiler.main(
            [
                "authorize",
                str(out),
                str(tmp_path / "decision.json"),
                "--out",
                str(tmp_path / "authorized.json"),
            ]
        )
        == 0
    )
    same = tmp_path / "same.json"
    with pytest.raises(SystemExit, match="paths must be distinct"):
        compiler.main(
            [
                "compile",
                *map(str, paths),
                "--out",
                str(same),
                "--authorization-template",
                str(same),
            ]
        )
    with pytest.raises(ValueError, match="already exists"):
        compiler.require_new_distinct(out, tmp_path / "unused.json")
    with pytest.raises(ValueError, match="already exists"):
        compiler.write_new(out, {})
    monkeypatch.setattr(
        compiler, "compile_plan", lambda *args: (_ for _ in ()).throw(ValueError("bad"))
    )
    with pytest.raises(SystemExit, match="failed"):
        compiler.main(
            [
                "compile",
                *map(str, paths),
                "--out",
                str(tmp_path / "new.json"),
                "--authorization-template",
                str(tmp_path / "new.json"),
            ]
        )
