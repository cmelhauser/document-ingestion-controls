"""Tests for the full-dataset proposal-only review agent."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

agent = importlib.import_module("client_review.agent")


def write(path, value):
    path.write_text(json.dumps(value))
    return path


def context():
    return {
        "records": [
            {
                "record_id": "r1",
                "document_id": "d1",
                "field": "seller_name",
                "value": "Acme",
                "review_status": "auto_accepted",
            },
            {
                "record_id": "r2",
                "document_id": "d1",
                "field": "seller_name",
                "value": "Acme",
                "reason": "formatting_review",
            },
            {
                "record_id": "r2",
                "document_id": "d1",
                "field": "seller_name",
                "value": "Acne",
                "reason": "consensus_disagreement",
            },
            {
                "record_id": "r2",
                "field": "note",
                "value": "handwritten note says check address",
            },
        ],
        "items": [
            {
                "review_item_id": "review-1",
                "field": "seller_name",
                "reason": "formatting_review",
            }
        ],
    }


def proposal(ref):
    return {
        "field": "seller_name",
        "original_value": "Acne",
        "proposed_value": "Acme",
        "update_type": "correction",
        "evidence": ref,
        "rationale": "The retained record supports the spelling.",
    }


def decision(next_action="stabilized"):
    return {
        "hypotheses": [
            {
                "hypothesis_id": "h1",
                "statement": "A source label is inconsistently extracted.",
                "status": "supported",
                "evidence_refs": ["evidence-ref"],
                "affected_item_ids": ["review-1"],
                "proposed_process_change": "Add a template-specific check.",
            }
        ],
        "item_updates": [
            {
                "review_item_id": "review-1",
                "decision": "propose_resolution",
                "confidence": 0.995,
                "rationale": "Cross-record evidence supports the correction.",
                "evidence_refs": ["evidence-ref"],
                "proposed_update": proposal("evidence-ref"),
                "dependency_item_ids": [],
            }
        ],
        "process_findings": [
            {
                "finding_id": "p1",
                "pattern": "Repeated source-label mismatch.",
                "evidence_refs": ["evidence-ref"],
                "proposed_change": "Add a regression fixture.",
            }
        ],
        "handwriting_comments": [
            {
                "review_item_id": "review-1",
                "comment": "Handwriting is visible near the address, but it is not used.",
                "evidence_refs": ["evidence-ref"],
                "relevance": "secondary_context_only",
            }
        ],
        "next_action": next_action,
        "confidence": 0.995,
    }


class Response:
    def __init__(self, value):
        self.output_text = json.dumps(value)

    def model_dump(self, mode="json"):
        return {"mode": mode, "output": self.output_text}


class Responses:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        value = self.outcomes.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class Client:
    def __init__(self, outcomes):
        self.responses = Responses(outcomes)


def test_index_inventory_signals_and_boundaries(tmp_path):
    path = write(tmp_path / "context.json", context())
    index = agent.build_evidence_index([path])
    assert index["all_artifacts_indexed"]
    assert index["all_source_values_retained"]
    assert any(entry["object"].get("record_id") == "r1" for entry in index["evidence"])
    assert index["cross_record_signals"]
    assert index["contradictions"]
    assert index["handwriting_context_refs"]
    assert agent.review_item_ids(index)
    assert agent.is_handwriting({"note": "HTR"})
    assert not agent.is_handwriting({"note": "printed"})
    assert agent.identity({"record_id": "r"}) == "r"
    assert agent.identity({"id": 2}) == "2"
    assert agent.identity({}) is None
    assert agent.field_name({"source_field": "x"}) == "x"
    assert agent.field_name({}) is None
    assert agent.field_value({"value": {"value": "x"}}) == "x"
    assert agent.field_value({}) is None
    assert agent.evidence_kind({"reason": "financial_total"}) == "protected_outstanding"
    assert agent.evidence_kind({"reason": "formatting_review"}) == "outstanding_or_decision"
    assert agent.evidence_kind({"decision": "retain_review"}) == "outstanding_or_decision"
    assert agent.evidence_kind({"review_status": "sampled_verified"}) == "validated"
    assert agent.evidence_kind({}) == "source_or_derived"
    with pytest.raises(ValueError, match="object or array"):
        bad = write(tmp_path / "bad.json", "bad")
        agent.load_json(bad)


def test_reduction_enforces_secondary_handwriting_and_unknown_items(tmp_path):
    path = write(tmp_path / "context.json", context())
    index = agent.build_evidence_index([path])
    result = agent.reduce_decision(decision(), index)
    assert result["all_review_items_retained"]
    assert result["production_approval_permitted"] is False
    assert result["handwriting_comments"][0]["blocking"] is False
    assert result["handwriting_comments"][0]["primary_decision_permitted"] is False
    assert result["item_updates"][0]["reducer_status"] == "retained_review"
    linked = decision()
    valid_ref = index["evidence"][0]["ref"]
    linked["item_updates"][0]["evidence_refs"] = [valid_ref]
    linked["item_updates"][0]["proposed_update"]["evidence"] = valid_ref
    assert (
        agent.reduce_decision(linked, index)["item_updates"][0]["reducer_status"]
        == "proposal_requires_existing_review_policy"
    )
    linked["item_updates"][0]["proposed_update"]["field"] = "other"
    assert (
        agent.reduce_decision(linked, index)["item_updates"][0]["reducer_status"]
        == "retained_review"
    )
    unknown = decision()
    unknown["item_updates"][0]["review_item_id"] = "missing"
    reduced = agent.reduce_decision(unknown, index)
    assert reduced["item_updates"][0]["reducer_status"] == "unknown_review_item"
    handwriting = decision()
    handwriting["item_updates"][0]["rationale"] = "handwriting confirms this"
    assert (
        agent.reduce_decision(handwriting, index)["item_updates"][0]["reducer_status"]
        == "handwriting_secondary_only"
    )
    assert agent.valid_update(None, set()) is False
    assert agent.valid_update({"field": "x"}, set()) is False
    invalid_type = proposal("evidence-ref")
    invalid_type["update_type"] = "delete"
    assert agent.valid_update(invalid_type, {"evidence-ref"}, ["evidence-ref"]) is False
    retained = decision()
    retained["item_updates"][0]["decision"] = "retain_review"
    assert (
        agent.reduce_decision(retained, index)["item_updates"][0]["reducer_status"]
        == "retained_review"
    )
    assert agent.stable_signature(result)


def test_run_agent_success_convergence_and_failure(tmp_path):
    path = write(tmp_path / "context.json", context())
    out = tmp_path / "agent.json"
    exc = tmp_path / "agent-exc.json"
    summary = agent.run_agent(
        [path],
        out,
        exc,
        tmp_path / "raw",
        "model",
        Client([Response(decision("iterate")), Response(decision("stabilized"))]),
        max_iterations=3,
        max_context_bytes=100000,
        provider="openai",
    )
    assert summary["iterations"] == 2
    assert summary["analysis_status"] == "completed"
    payload = json.loads(out.read_text())
    assert payload["summary"]["handwriting_policy"].startswith("secondary_context_only")
    assert payload["final_reduction"]["handwriting_comments"]
    assert json.loads(exc.read_text())["summary"]["count"] == 0
    exhausted = agent.run_agent(
        [path],
        tmp_path / "exhausted.json",
        tmp_path / "exhausted-exc.json",
        tmp_path / "exhausted-raw",
        "model",
        Client([Response(decision("iterate")), Response(decision("iterate"))]),
        max_iterations=2,
        max_context_bytes=100000,
    )
    assert exhausted["iterations"] == 2
    failed = agent.run_agent(
        [path],
        tmp_path / "failed.json",
        tmp_path / "failed-exc.json",
        tmp_path / "failed-raw",
        "model",
        Client([RuntimeError("provider")]),
    )
    assert failed["provider_exceptions"] == 1
    assert failed["analysis_status"] == "failed"
    with pytest.raises(ValueError, match="at least one"):
        agent.run_agent([], tmp_path / "x", tmp_path / "y", tmp_path / "z", "m", Client([]))
    with pytest.raises(ValueError, match="iterations"):
        agent.run_agent(
            [path],
            tmp_path / "x2",
            tmp_path / "y2",
            tmp_path / "z2",
            "m",
            Client([]),
            max_iterations=0,
        )
    with pytest.raises(ValueError, match="context bytes"):
        agent.run_agent(
            [path],
            tmp_path / "x3",
            tmp_path / "y3",
            tmp_path / "z3",
            "m",
            Client([]),
            max_context_bytes=0,
        )


def test_run_agent_keeps_successful_slices_when_a_later_slice_fails(tmp_path):
    path = write(tmp_path / "context-sliced.json", context())
    out = tmp_path / "sliced-agent.json"
    exc = tmp_path / "sliced-agent-exc.json"
    summary = agent.run_agent(
        [path],
        out,
        exc,
        tmp_path / "sliced-raw",
        "model",
        Client([Response(decision("stabilized")), RuntimeError("provider")]),
        max_iterations=1,
        max_context_bytes=100000,
        max_slice_context_bytes=100000,
        max_evidence_per_slice=1,
    )
    assert summary["slices"] > 1
    assert summary["successful_slices"] == 1
    assert summary["provider_exceptions"] > 0
    assert summary["partial_results_retained"] is True
    payload = json.loads(out.read_text())
    assert payload["final_reduction"]["item_updates"]
    assert json.loads(exc.read_text())["summary"]["count"] == summary["provider_exceptions"]


def test_agent_helpers_and_cli(monkeypatch, tmp_path):
    path = write(tmp_path / "context.json", context())
    index = agent.build_evidence_index([path])
    with pytest.raises(ValueError, match="slice context"):
        agent.analysis_slices(index, 0)
    with pytest.raises(ValueError, match="evidence per slice"):
        agent.analysis_slices(index, 100000, 0)
    empty_index = {**index, "evidence": []}
    assert agent.analysis_slices(empty_index, 100000)[0]["evidence"] == []
    with pytest.raises(ValueError, match="exceeds"):
        agent.analysis_slices(index, 1)
    large_index = {
        **index,
        "evidence": [
            {
                "ref": f"large-{item}",
                "kind": "source_or_derived",
                "object": {"value": "x" * 1200},
            }
            for item in range(2)
        ],
    }
    assert len(agent.analysis_slices(large_index, 3000, 2)) == 2
    with pytest.raises(ValueError, match="slice packet"):
        agent.slice_packet(agent.analysis_slices(index, 100000)[0], {}, 1, 1)
    assert (
        agent.merge_reductions(agent.empty_reduction(), agent.empty_reduction())["confidence"]
        is None
    )
    with pytest.raises(ValueError, match="unsupported"):
        agent.run_agent(
            [path],
            tmp_path / "a",
            tmp_path / "b",
            tmp_path / "c",
            "m",
            Client([]),
            reasoning_effort="bad",
        )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            agent.__file__,
            "--out",
            str(tmp_path / "disabled.json"),
            "--exceptions",
            str(tmp_path / "disabled-exc.json"),
            "--raw-dir",
            str(tmp_path / "disabled-raw"),
        ],
    )
    monkeypatch.setenv("CLIENT_REVIEW_LLM_ENABLED", "false")
    agent.main()
    assert json.loads((tmp_path / "disabled.json").read_text())["summary"]["enabled"] is False
    monkeypatch.setenv("CLIENT_REVIEW_LLM_ENABLED", "true")
    monkeypatch.setattr(agent, "load_project_env", lambda: None)
    monkeypatch.setattr(
        agent, "build_reviewer_client", lambda *args: Client([Response(decision())])
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            agent.__file__,
            "--context",
            str(path),
            "--out",
            str(tmp_path / "enabled.json"),
            "--exceptions",
            str(tmp_path / "enabled-exc.json"),
            "--raw-dir",
            str(tmp_path / "enabled-raw"),
            "--enable",
            "--final-provider",
            "google",
            "--final-model",
            "advanced",
            "--iterations",
            "1",
        ],
    )
    agent.main()
    assert (
        json.loads((tmp_path / "enabled.json").read_text())["summary"]["agent_model"] == "advanced"
    )
    with pytest.raises(ValueError, match="slice context bytes"):
        agent.run_agent(
            [path],
            tmp_path / "x4",
            tmp_path / "y4",
            tmp_path / "z4",
            "m",
            Client([]),
            max_slice_context_bytes=0,
        )


def test_stress_heterogeneous_records_and_review_items(tmp_path):
    records = []
    for index in range(50):
        records.append(
            {
                "record_id": f"record-{index % 7}",
                "document_id": f"document-{index % 5}",
                "field": "amount" if index % 2 == 0 else "seller_name",
                "value": index if index % 2 == 0 else f"Seller {index % 3}",
                "reason": "financial_total" if index % 11 == 0 else "formatting_review",
                "nested": {
                    "lines": [index, None, {"note": "handwritten" if index % 9 == 0 else "printed"}]
                },
            }
        )
    records.extend(
        [
            {"review_item_id": "bare-review-item", "field": "seller_name", "value": "Acme"},
            {"client_review_required": True, "field": "unknown", "value": None},
            {"provider_error": {"status": 503}, "exception": True},
        ]
    )
    path = write(tmp_path / "stress.json", {"records": records, "metadata": [None, True, "x"]})
    index = agent.build_evidence_index([path])
    assert "bare-review-item" in agent.review_item_ids(index)
    assert index["all_artifacts_indexed"]
    assert len(index["evidence"]) >= 50
    result = agent.reduce_decision(
        {"item_updates": [], "handwriting_comments": [], "hypotheses": [], "process_findings": []},
        index,
    )
    assert result["all_review_items_retained"]


class UniformClient:
    """Answer every request identically, so replies cannot depend on call order.

    A stub that pops replies off a list answers by call order, which is exactly
    what concurrency is free to change; it would pass while proving nothing.
    """

    def __init__(self, next_action="stabilized"):
        self.responses = self
        self.next_action = next_action
        self.calls = 0

    def create(self, **kwargs):
        del kwargs
        self.calls += 1
        return Response(decision(self.next_action))


def test_worker_count_changes_speed_but_never_the_retained_artifact(tmp_path):
    """Slices may finish in any order, but `merge_reductions` is not commutative.

    It extends lists and overwrites next_action/confidence with whatever merged
    last, so a concurrent run that merged as it went would be non-deterministic.
    Slice reductions must be merged in slice order instead.
    """
    path = write(tmp_path / "context.json", context())

    def analyse(tag, workers):
        out = tmp_path / f"{tag}.json"
        summary = agent.run_agent(
            [path],
            out,
            tmp_path / f"{tag}-exc.json",
            tmp_path / f"{tag}-raw",
            "model",
            UniformClient(),
            max_iterations=2,
            max_context_bytes=100000,
            max_evidence_per_slice=1,
            provider="openai",
            workers=workers,
        )
        return summary, json.loads(out.read_text())

    serial_summary, serial = analyse("serial", 1)
    concurrent_summary, concurrent = analyse("concurrent", 4)

    assert serial_summary["slices"] > 1, "the fixture must produce more than one slice"
    assert concurrent_summary["slices"] == serial_summary["slices"]
    assert concurrent_summary["iterations"] == serial_summary["iterations"]
    assert concurrent_summary["successful_slices"] == serial_summary["successful_slices"]
    assert concurrent["final_reduction"] == serial["final_reduction"]
    assert [item["slice"] for item in concurrent["iterations"]] == [
        item["slice"] for item in serial["iterations"]
    ]
