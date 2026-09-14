import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
allocation = importlib.import_module("allocation_policy")


def write(path, value):
    path.write_text(json.dumps(value))
    return path


def template():
    return {
        "template_id": "commission-template-v1",
        "source_labels": ["Product $", "Rate", "Partner", "Commission", "Ship-to credit only"],
        "evidence": [{"document_id": "SAMPLE-DOC", "page_id": "SAMPLE-PAGE"}],
    }


def leg(**changes):
    value = {
        "allocation_id": "LEG-1",
        "document_id": "SAMPLE-DOC",
        "page_id": "SAMPLE-PAGE",
        "template_fingerprint": allocation.fingerprint(template()),
        "brand_name": "Sample Brand",
        "dealer_name": "Sample Dealer",
        "commissionable_amount": "4496.00",
        "commission_amount": "89.92",
        "stated_commission_rate": "10%",
        "allocation_share": "20%",
        "allocation_descriptor": "ship-to credit only",
    }
    value.update(changes)
    return value


def rule(scope=None, **policy):
    return {
        "rule_id": "allocation-rule",
        "registry_version": 3,
        "status": "client_approved",
        "template_fingerprint": allocation.fingerprint(template()),
        "scope": scope or {},
        "policy": {
            "effective_rate_threshold": 0.025,
            "explicit_ship_to_precedence": True,
            "allow_sales_generated": True,
            **policy,
        },
    }


class Response:
    def __init__(self, value):
        self.output_text = json.dumps(value)

    def model_dump(self, mode="json"):
        return {"id": "response"}


class Client:
    def __init__(self, value):
        self.value = value
        self.responses = self

    def create(self, **kwargs):
        if isinstance(self.value, Exception):
            raise self.value
        return Response(self.value)


def model_output():
    return {
        "column_proposals": [
            {
                "source_label": "Commission",
                "allocation_role": "commission_amount",
                "confidence": 0.99,
                "rationale": "label",
            }
        ],
        "policy_proposal": {
            "effective_rate_threshold": 0.025,
            "explicit_ship_to_precedence": True,
            "allow_sales_generated": True,
            "confidence": 0.99,
            "rationale": "sample",
        },
    }


def test_value_helpers_and_loaders(tmp_path):
    value = template()
    assert allocation.scalar({"value": "x"}) == "x"
    assert allocation.number("1,200.00") == 1200
    assert allocation.number(True) is None and allocation.number("bad") is None
    # A value above 1 can only be a displayed percentage. A value at or below 1
    # is ambiguous -- "0.75" is equally 0.75% or 75% -- so the unit is registered
    # as review work rather than guessed 100x wrong.
    assert str(allocation.percent("2.5%")) == "0.025"
    assert allocation.percent("0.02") == allocation.AMBIGUOUS_RATE
    assert allocation.percent("0.75") == allocation.AMBIGUOUS_RATE
    assert allocation.percent("0") == 0
    assert allocation.key(" A   B ") == "a b"
    assert len(allocation.fingerprint(value)) == 64
    assert allocation.review(leg(), "x", "y")["document_id"] == "SAMPLE-DOC"
    assert (
        allocation.load_legs(write(tmp_path / "legs.json", {"allocation_legs": [leg()]}))[0][
            "allocation_id"
        ]
        == "LEG-1"
    )
    assert allocation.load_templates(
        write(tmp_path / "templates.json", {"templates": [value]})
    ) == [value]
    assert allocation.load_registry(None)["rules"] == []
    with pytest.raises(ValueError):
        allocation.load_legs(write(tmp_path / "bad-legs.json", {}))
    with pytest.raises(ValueError):
        allocation.load_templates(write(tmp_path / "bad-templates.json", {"templates": [{}]}))
    with pytest.raises(ValueError):
        allocation.load_registry(write(tmp_path / "bad-registry.json", {}))


def test_scoped_policy_and_classification():
    default = rule()
    scoped = rule({"brand_name": "Sample Brand"})
    scoped["rule_id"] = "brand-rule"
    registry = {"rules": [default, scoped]}
    assert (
        allocation.scoped_policy(registry, leg()["template_fingerprint"], leg())["rule_id"]
        == "brand-rule"
    )
    assert (
        allocation.scoped_policy({"rules": [default, rule()]}, leg()["template_fingerprint"], leg())
        is None
    )
    assert allocation.scoped_policy(registry, "missing", leg()) is None
    temporal = rule(
        {
            "dimensions": {"product_sku": "SKU-1"},
            "effective_from": "2026-01-01",
            "effective_to": "2026-12-31",
        }
    )
    temporal["rule_id"] = "temporal-rule"
    assert (
        allocation.scoped_policy(
            {"rules": [temporal]},
            leg()["template_fingerprint"],
            leg(product_sku="SKU-1", effective_date="2026-06-01"),
        )["template_fingerprint"]
        == leg()["template_fingerprint"]
    )
    assert (
        allocation.scoped_policy(
            {"rules": [temporal]},
            leg()["template_fingerprint"],
            leg(product_sku="SKU-1", effective_date="2027-01-01"),
        )
        is None
    )
    assert (
        allocation.scoped_policy(
            {"rules": [temporal]}, leg()["template_fingerprint"], leg(product_sku="SKU-1")
        )
        is None
    )
    assert allocation.scope_matches({"dimensions": "bad"}, leg()) is False
    admin, finding = allocation.classify(leg(), default)
    assert admin["classification"] == "administrative_ship_to" and finding is None
    low, finding = allocation.classify(
        leg(
            allocation_descriptor="",
            commission_amount="100",
            stated_commission_rate=None,
            allocation_share=None,
        ),
        default,
    )
    assert low["classification"] == "administrative_ship_to" and finding is None
    sales, finding = allocation.classify(
        leg(
            allocation_descriptor="",
            commission_amount="450",
            stated_commission_rate=None,
            allocation_share=None,
        ),
        default,
    )
    assert sales["classification"] == "sales_generated" and finding is None
    review, finding = allocation.classify(leg(commissionable_amount="0"), default)
    assert review["classification"] == "review_required" and finding["priority"] == "critical"
    review, finding = allocation.classify(leg(), None)
    assert review["reason"].endswith("unapproved_template_policy") and finding
    limited, finding = allocation.classify(
        leg(
            allocation_descriptor="",
            commission_amount="450",
            stated_commission_rate=None,
            allocation_share=None,
        ),
        rule(allow_sales_generated=False),
    )
    assert limited["reason"].endswith("does_not_authorize_sales_credit") and finding
    mismatch, finding = allocation.classify(leg(commission_amount="90.00"), default)
    assert (
        mismatch["reason"] == "allocation_formula_not_proved" and finding["priority"] == "critical"
    )


def test_apply_and_discovery(tmp_path):
    registry = {"rules": [rule({"brand_name": "Sample Brand"})]}
    output = allocation.apply(
        [leg(), leg(allocation_id="LEG-2", brand_name="Other")],
        registry,
        tmp_path / "out.json",
        tmp_path / "exceptions.json",
    )
    assert output["summary"]["administrative_ship_to_count"] == 1
    assert output["summary"]["client_review_items"] == 1
    with pytest.raises(ValueError):
        allocation.apply([leg()], registry, tmp_path / "out.json", tmp_path / "new.json")
    disabled = allocation.discover(
        [template()],
        {"rules": []},
        tmp_path / "d.json",
        tmp_path / "e.json",
        tmp_path / "h.json",
        tmp_path / "raw",
        False,
        None,
        "m",
        "medium",
    )
    assert disabled["policy_proposals"][0]["status"] == "provider_not_enabled"
    enabled = allocation.discover(
        [template()],
        {"rules": []},
        tmp_path / "d2.json",
        tmp_path / "e2.json",
        tmp_path / "h2.json",
        tmp_path / "raw2",
        True,
        Client(model_output()),
        "m",
        "medium",
    )
    assert enabled["policy_proposals"][0]["status"] == "proposed"
    failed = allocation.discover(
        [template()],
        {"rules": []},
        tmp_path / "d3.json",
        tmp_path / "e3.json",
        tmp_path / "h3.json",
        tmp_path / "raw3",
        True,
        Client(RuntimeError("down")),
        "m",
        "medium",
    )
    assert failed["policy_proposals"][0]["status"] == "provider_failure"


def test_discovery_errors_registry_updates_and_cli(monkeypatch, tmp_path, capsys):
    value = template()
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "x").write_text("x")
    with pytest.raises(ValueError):
        allocation.discover(
            [value],
            {"rules": []},
            tmp_path / "a",
            tmp_path / "b",
            tmp_path / "c",
            raw,
            False,
            None,
            "m",
            "invalid",
        )
    with pytest.raises(ValueError):
        allocation.discover(
            [value],
            {"rules": []},
            tmp_path / "a1",
            tmp_path / "b1",
            tmp_path / "c1",
            tmp_path / "raw1",
            True,
            None,
            "m",
            "medium",
        )
    discovery = {
        "policy_proposals": [
            {
                "template_id": value["template_id"],
                "template_fingerprint": allocation.fingerprint(value),
                "status": "proposed",
                "policy_proposal": model_output()["policy_proposal"],
            }
        ]
    }
    updated = allocation.update_registry(
        {"registry_version": 0, "rules": []},
        discovery,
        {
            "decisions": [
                {
                    "template_fingerprint": allocation.fingerprint(value),
                    "decision": "approve",
                    "scope": {
                        "dimensions": {"brand_name": "Sample Brand"},
                        "effective_from": "2026-01-01",
                    },
                }
            ]
        },
    )
    assert len(updated["update_log"]) == 1 and updated["rules"][0]["scope"]
    assert (
        allocation.update_registry(
            updated,
            discovery,
            {
                "decisions": [
                    {
                        "template_fingerprint": allocation.fingerprint(value),
                        "decision": "approve",
                        "scope": {"dimensions": {"bad": ""}},
                    }
                ]
            },
        )["update_log"]
        == []
    )
    with pytest.raises(ValueError):
        allocation.update_registry({}, discovery, {})
    input_path = write(tmp_path / "input.json", {"allocation_legs": [leg()]})
    registry_path = write(tmp_path / "registry.json", {"rules": [rule()]})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            allocation.__file__,
            "apply",
            str(input_path),
            "--registry",
            str(registry_path),
            "--out",
            str(tmp_path / "cli-out.json"),
            "--exceptions",
            str(tmp_path / "cli-ex.json"),
        ],
    )
    allocation.main()
    assert "client_review_items" in capsys.readouterr().out
    monkeypatch.setattr(
        sys,
        "argv",
        [
            allocation.__file__,
            "decision-template",
            str(write(tmp_path / "template-discovery.json", discovery)),
            "--out",
            str(tmp_path / "decision-template.json"),
        ],
    )
    allocation.main()
    assert json.loads((tmp_path / "decision-template.json").read_text())["decisions"]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            allocation.__file__,
            "registry-update",
            str(registry_path),
            str(write(tmp_path / "disc.json", discovery)),
            str(write(tmp_path / "decisions.json", {"decisions": []})),
            "--out",
            str(tmp_path / "next.json"),
        ],
    )
    allocation.main()
    assert (tmp_path / "next.json").exists()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            allocation.__file__,
            "discover",
            str(write(tmp_path / "templates-cli.json", {"templates": [value]})),
            "--out",
            str(tmp_path / "discover-cli.json"),
            "--exceptions",
            str(tmp_path / "discover-cli-ex.json"),
            "--handoff-out",
            str(tmp_path / "discover-cli-hand.json"),
            "--raw-dir",
            str(tmp_path / "discover-cli-raw"),
        ],
    )
    allocation.main()
    with pytest.raises(SystemExit, match="Allocation policy failed"):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                allocation.__file__,
                "apply",
                str(input_path),
                "--registry",
                str(registry_path),
                "--out",
                str(tmp_path / "cli-out.json"),
                "--exceptions",
                str(tmp_path / "cli-ex-2.json"),
            ],
        )
        allocation.main()


def test_allocation_policy_remaining_branches(monkeypatch, tmp_path):
    value = template()
    with pytest.raises(ValueError):
        allocation.load_templates(write(tmp_path / "empty-templates.json", {"templates": []}))
    assert allocation.scope_matches("bad", leg()) is False
    assert (
        allocation.scope_matches(
            {"dimensions": {"brand_name": "Sample Brand"}, "effective_from": "bad"},
            leg(effective_date="2026-01-01"),
        )
        is False
    )
    raw = tmp_path / "occupied-raw"
    raw.mkdir()
    (raw / "x").write_text("x")
    with pytest.raises(ValueError):
        allocation.discover(
            [value],
            {"rules": []},
            tmp_path / "ra",
            tmp_path / "rb",
            tmp_path / "rc",
            raw,
            False,
            None,
            "m",
            "medium",
        )
    skipped = allocation.discover(
        [value],
        {"rules": [rule()]},
        tmp_path / "sa",
        tmp_path / "sb",
        tmp_path / "sc",
        tmp_path / "skip-raw",
        False,
        None,
        "m",
        "medium",
    )
    assert skipped["policy_proposals"] == []
    proposal = {
        "template_id": value["template_id"],
        "template_fingerprint": allocation.fingerprint(value),
        "status": "proposed",
        "policy_proposal": model_output()["policy_proposal"],
    }
    discovery = {"policy_proposals": [proposal]}
    assert allocation.policy_with_override(model_output()["policy_proposal"], "bad") is None
    assert (
        allocation.policy_with_override(
            model_output()["policy_proposal"], {"effective_rate_threshold": 1.0}
        )
        is None
    )
    invalid_policy = allocation.update_registry(
        {"rules": []},
        {"policy_proposals": [{**proposal, "policy_proposal": {}}]},
        {
            "decisions": [
                {
                    "decision": "approve",
                    "template_fingerprint": allocation.fingerprint(value),
                    "scope": {"dimensions": {}},
                }
            ]
        },
    )
    assert invalid_policy["update_log"] == []
    invalid = allocation.update_registry(
        {"rules": []},
        discovery,
        {
            "decisions": [
                "skip",
                {
                    "decision": "approve",
                    "template_fingerprint": allocation.fingerprint(value),
                    "scope": {
                        "dimensions": {"brand_name": "Sample Brand"},
                        "effective_from": "bad",
                    },
                },
            ]
        },
    )
    assert invalid["update_log"] == []
    base = allocation.update_registry(
        {"rules": []},
        discovery,
        {
            "decisions": [
                {
                    "decision": "approve",
                    "template_fingerprint": allocation.fingerprint(value),
                    "scope": {"dimensions": {"brand_name": "Sample Brand"}},
                }
            ]
        },
    )
    duplicate = allocation.update_registry(
        base,
        discovery,
        {
            "decisions": [
                {
                    "decision": "approve",
                    "template_fingerprint": allocation.fingerprint(value),
                    "scope": {"dimensions": {"brand_name": "Sample Brand"}},
                }
            ]
        },
    )
    assert duplicate["update_log"] == []
    decision_out = tmp_path / "existing-decision.json"
    decision_out.write_text("x")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            allocation.__file__,
            "decision-template",
            str(write(tmp_path / "discovery-existing.json", discovery)),
            "--out",
            str(decision_out),
        ],
    )
    with pytest.raises(SystemExit, match="Allocation policy failed"):
        allocation.main()
    registry_out = tmp_path / "existing-registry.json"
    registry_out.write_text("x")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            allocation.__file__,
            "registry-update",
            str(write(tmp_path / "registry-existing.json", {"rules": []})),
            str(write(tmp_path / "discovery-existing-2.json", discovery)),
            str(write(tmp_path / "decisions-existing.json", {"decisions": []})),
            "--out",
            str(registry_out),
        ],
    )
    with pytest.raises(SystemExit, match="Allocation policy failed"):
        allocation.main()
