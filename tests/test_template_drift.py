"""Tests for exact template fingerprinting and drift controls."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

drift = importlib.import_module("template_drift")


def write(path, value):
    path.write_text(json.dumps(value))
    return path


def template(template_id="invoice-a", headers=None):
    return {
        "template_id": template_id,
        "document_family": "Invoice",
        "headers": [{"source_label": item} for item in (headers or ["Amount", "Tax"])],
        "sections": ["Summary"],
        "column_boundaries": [0, 100, 200],
    }


def registry_for(item):
    return {
        "schema_version": "1.0",
        "registry_version": 1,
        "fingerprint_algorithm": drift.ALGORITHM,
        "templates": [
            {
                "template_registry_id": "registry-1",
                "template_id": item["template_id"],
                "template_fingerprint": drift.fingerprint(item),
                "layout_signature": drift.layout_signature(item),
                "status": "client_approved",
            }
        ],
    }


def test_normalization_fingerprints_similarity_and_analysis():
    item = template()
    assert drift.normalized({"B": [" X  Y "], "a": 2}) == {"B": ["x y"], "a": 2}
    assert drift.fingerprint(item) == drift.fingerprint({**item, "template_id": "ignored"})
    with pytest.raises(ValueError):
        drift.digest(float("nan"))
    assert len(drift.legacy_header_fingerprint(item)) == 64
    registry = registry_for(item)
    result = drift.analyze([item], registry)
    assert result["templates"][0]["mapping_reuse_permitted"] is True
    assert result["review"]["summary"]["gate_status"] == "clear"

    changed = template(headers=["Amount", "Fee"])
    new = template("new", ["Unrelated"])
    result = drift.analyze([changed, new], registry)
    assert [item["status"] for item in result["templates"]] == ["layout_drift", "new_template"]
    assert result["review"]["summary"]["client_review_items"] == 2
    assert (
        drift.similarity(
            {"ordered_headers": [], "document_family": "a"},
            {"ordered_headers": [], "document_family": "a"},
        )
        == 0.7
    )

    registry["templates"].append(dict(registry["templates"][0], template_registry_id="duplicate"))
    ambiguous = drift.analyze([item], registry)
    assert ambiguous["templates"][0]["status"] == "ambiguous_exact_match"
    registry["templates"].append(
        {
            "template_registry_id": "legacy-incomplete",
            "template_id": "invoice-a",
            "status": "client_approved",
        }
    )
    assert drift.analyze([changed], registry)["templates"][0]["status"] == "layout_drift"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"document_family": "invoice", "headers": []}, "headers"),
        ({"document_family": "invoice", "headers": [None]}, "source_label"),
        ({"document_family": "", "headers": ["A"]}, "document_family"),
    ],
)
def test_layout_validation(value, message):
    with pytest.raises(ValueError, match=message):
        drift.layout_signature(value)


def test_load_validation(tmp_path):
    with pytest.raises(ValueError, match="JSON object"):
        drift.load_object(write(tmp_path / "list.json", []), "value")
    with pytest.raises(ValueError, match="non-empty"):
        drift.load_templates(write(tmp_path / "empty.json", {}))
    with pytest.raises(ValueError, match="template_id"):
        drift.load_templates(write(tmp_path / "bad.json", {"templates": [None]}))
    with pytest.raises(ValueError, match="unique"):
        drift.load_templates(write(tmp_path / "dupe.json", {"templates": [template(), template()]}))
    assert drift.load_registry(None)["registry_version"] == 0
    with pytest.raises(ValueError, match="source_layout_v1"):
        drift.load_registry(write(tmp_path / "registry.json", {}))
    item = template()
    inconsistent = registry_for(item)
    inconsistent["templates"][0]["template_fingerprint"] = "f" * 64
    with pytest.raises(ValueError, match="fingerprint"):
        drift.load_registry(write(tmp_path / "inconsistent.json", inconsistent))
    duplicate = registry_for(item)
    duplicate["templates"] *= 2
    with pytest.raises(ValueError, match="unique"):
        drift.load_registry(write(tmp_path / "duplicate-registry.json", duplicate))


@pytest.mark.parametrize(
    "signature",
    [
        {"document_family": "invoice", "ordered_headers": ["amount"], "extra": "x"},
        {"document_family": 1, "ordered_headers": ["amount"]},
        {"document_family": "", "ordered_headers": ["amount"]},
        {"document_family": "Invoice", "ordered_headers": ["amount"]},
        {"document_family": "invoice", "ordered_headers": None},
        {"document_family": "invoice", "ordered_headers": []},
        {"document_family": "invoice", "ordered_headers": [1]},
        {"document_family": "invoice", "ordered_headers": [""]},
        {"document_family": "invoice", "ordered_headers": ["Amount"]},
        {"document_family": "invoice", "ordered_headers": ["amount"], "sections": ["Summary"]},
    ],
)
def test_registry_layout_signature_is_exactly_canonical(signature):
    with pytest.raises(ValueError):
        drift.validate_layout_signature(signature)


@pytest.mark.parametrize(
    "item",
    [
        None,
        {"template_registry_id": 1, "template_id": "a", "status": "client_approved"},
        {"template_registry_id": "", "template_id": "a", "status": "client_approved"},
        {"template_registry_id": "r", "template_id": 1, "status": "client_approved"},
        {"template_registry_id": "r", "template_id": "", "status": "client_approved"},
        {"template_registry_id": "r", "template_id": "a", "status": "draft"},
    ],
)
def test_registry_rejects_invalid_template_identities(tmp_path, item):
    value = {
        "schema_version": "1.0",
        "registry_version": 1,
        "fingerprint_algorithm": drift.ALGORITHM,
        "templates": [item],
    }
    with pytest.raises(ValueError, match="approved non-empty template identities"):
        drift.load_registry(write(tmp_path / "invalid-registry.json", value))


def test_registry_update_and_validation():
    registry = drift.load_registry(None)
    payload = {
        "template_registry_id": "new-rule",
        "template_id": "invoice-a",
        "layout_signature": {"document_family": "invoice", "ordered_headers": ["amount"]},
    }
    payload["template_fingerprint"] = drift.signature_fingerprint(payload["layout_signature"])
    plan = {
        "schema_version": "1.0",
        "proposal_only": True,
        "production_changes_applied": False,
        "patches": [
            {
                "patch_id": "p1",
                "change_type": "template_registry_rule",
                "append_only_payload": payload,
            },
            {"patch_id": "p2", "change_type": "mapping_rule", "append_only_payload": {}},
        ],
    }
    plan["compiled_plan_sha256"] = drift.canonical_plan_hash(plan)
    authorization = {
        "authorization_status": "operator_authorized",
        "authorization_id": "auth-1",
        "authorized_patch_ids": ["p1", "p2"],
        "deferred_patch_ids": [],
        "compiled_plan": plan,
    }
    updated = drift.update_registry(registry, authorization)
    assert updated["registry_version"] == 1
    assert updated["templates"][0]["status"] == "client_approved"
    with pytest.raises(ValueError, match="operator-authorized"):
        drift.update_registry(registry, {})
    changed = json.loads(json.dumps(authorization))
    changed["compiled_plan"]["proposal_only"] = False
    with pytest.raises(ValueError, match="hash"):
        drift.update_registry(registry, changed)
    none = json.loads(json.dumps(authorization))
    none["authorized_patch_ids"] = ["p2"]
    none["deferred_patch_ids"] = ["p1"]
    with pytest.raises(ValueError, match="no template"):
        drift.update_registry(registry, none)
    incomplete = json.loads(json.dumps(authorization))
    incomplete["compiled_plan"]["patches"][0]["append_only_payload"] = {}
    incomplete["compiled_plan"]["compiled_plan_sha256"] = drift.canonical_plan_hash(
        incomplete["compiled_plan"]
    )
    with pytest.raises(ValueError, match="complete"):
        drift.update_registry(registry, incomplete)
    duplicate = drift.load_registry(None)
    duplicate["templates"] = [{"template_registry_id": "new-rule"}]
    with pytest.raises(ValueError, match="already exists"):
        drift.update_registry(duplicate, authorization)
    for key, value, message in (
        ("authorization_id", "", "authorization_id"),
        ("authorized_patch_ids", None, "non-empty strings"),
        ("authorized_patch_ids", ["p1", "p1"], "unique"),
        ("authorized_patch_ids", ["outside"], "unknown patch"),
        ("deferred_patch_ids", None, "non-empty strings"),
        ("deferred_patch_ids", [1], "non-empty strings"),
    ):
        invalid = json.loads(json.dumps(authorization))
        invalid[key] = value
        with pytest.raises(ValueError, match=message):
            drift.update_registry(registry, invalid)
    inconsistent = json.loads(json.dumps(authorization))
    inconsistent["compiled_plan"]["patches"][0]["append_only_payload"]["template_fingerprint"] = (
        "0" * 64
    )
    inconsistent["compiled_plan"]["compiled_plan_sha256"] = drift.canonical_plan_hash(
        inconsistent["compiled_plan"]
    )
    with pytest.raises(ValueError, match="fingerprint"):
        drift.update_registry(registry, inconsistent)
    incomplete_partition = json.loads(json.dumps(authorization))
    incomplete_partition["authorized_patch_ids"] = ["p1"]
    incomplete_partition["deferred_patch_ids"] = []
    with pytest.raises(ValueError, match="partition"):
        drift.update_registry(registry, incomplete_partition)
    for key, value in (
        ("template_registry_id", 1),
        ("template_registry_id", ""),
        ("template_id", 1),
        ("template_id", ""),
    ):
        invalid_identity = json.loads(json.dumps(authorization))
        invalid_identity["compiled_plan"]["patches"][0]["append_only_payload"][key] = value
        invalid_identity["compiled_plan"]["compiled_plan_sha256"] = drift.canonical_plan_hash(
            invalid_identity["compiled_plan"]
        )
        with pytest.raises(ValueError, match="non-empty template identities"):
            drift.update_registry(registry, invalid_identity)


def test_write_and_main_paths(monkeypatch, tmp_path):
    output = tmp_path / "out.json"
    drift.write_new(output, {"ok": True})
    with pytest.raises(ValueError, match="already exists"):
        drift.write_new(output, {})
    source = write(tmp_path / "templates.json", {"templates": [template()]})
    assert drift.main(["analyze", str(source), "--out", str(tmp_path / "analysis.json")]) == 0
    registry = write(tmp_path / "registry.json", registry_for(template()))
    monkeypatch.setattr(drift, "update_registry", lambda *args: {"updated": True})
    assert (
        drift.main(
            ["registry-update", str(registry), str(source), "--out", str(tmp_path / "updated.json")]
        )
        == 0
    )
    monkeypatch.setattr(drift, "analyze", lambda *args: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(SystemExit, match="failed"):
        drift.main(["analyze", str(source), "--out", str(tmp_path / "failed.json")])
