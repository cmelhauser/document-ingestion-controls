"""Tests for the fail-closed, audit-only LLM adjudication lane."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

llm = importlib.import_module("llm_adjudication")
final_review_queue = importlib.import_module("client_review.queue")


def write(path, value):
    path.write_text(json.dumps(value) if not isinstance(value, str) else value)
    return path


def manifest(tmp_path):
    page = tmp_path / "page.pdf"
    page.write_text("%PDF-evidence")
    return write(
        tmp_path / "manifest.json",
        {"pages": [{"page_id": "page-1", "page_pdf": page.name}]},
    )


def candidate(**changes):
    value = {
        "candidate_id": "candidate-1",
        "document_id": "doc-1",
        "page_id": "page-1",
        "field": "seller_name",
        "original_value": "Acme, Inc.",
        "candidate_value": "Acme Incorporated",
        "original_source": "printed",
        "evidence_text": "Seller: Acme Incorporated",
        "deterministic_validation_status": "clear",
        "independent_extractor": {"engine": "ocr-b/1", "value": "Acme Incorporated"},
        "has_handwriting": False,
        "is_reassembly": False,
        "has_disagreement": False,
    }
    value.update(changes)
    return value


class Response:
    def __init__(self, decision, raw=None):
        self.output_text = json.dumps(decision)
        self.raw = raw or {"provider_id": "decision-1"}

    def model_dump(self, mode="json"):
        return self.raw


class Responses:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Client:
    def __init__(self, outcomes):
        self.responses = Responses(outcomes)


def decision(name="propose_amendment", confidence=0.99):
    return {
        "decision": name,
        "confidence": confidence,
        "rationale": "Visible seller header supports the supplied candidate.",
        "evidence_text": "Seller: Acme Incorporated",
    }


def run(tmp_path, candidates, client, **kwargs):
    candidates_path = write(tmp_path / "candidates.json", {"candidates": candidates})
    return llm.run_adjudication(
        candidates_path,
        manifest(tmp_path),
        tmp_path / "out.json",
        tmp_path / "exceptions.json",
        tmp_path / "handoff.json",
        tmp_path / "raw",
        "test-model",
        client,
        **kwargs,
    )


def test_helpers_and_eligibility_boundaries(tmp_path):
    assert llm.scalar({"value": "x"}) == "x"
    assert llm.scalar("x") == "x"
    assert llm.normalized(" A   B ") == "a b"
    assert llm.normalized(None) is None
    assert llm.field_has_token("header.total_amount", llm.FINANCIAL_TOKENS)
    assert not llm.field_has_token("seller_name", llm.ADDRESS_TOKENS)
    assert llm.candidate_id(candidate()) == "candidate-1"
    automatic = candidate(candidate_id=" ")
    assert llm.candidate_id(automatic).startswith("llm-candidate-")
    assert llm.raw_name(1, automatic).startswith("000001_llm-candidate-")
    assert llm.sampled(candidate(), 1.0)
    assert not llm.sampled(candidate(), 0.000000001)
    assert llm.exception(candidate(), "reason")["disposition"] == "client_review_required"
    assert llm.eligibility_reason(candidate()) is None
    cases = {
        "llm_adjudication_candidate_missing_required_evidence": candidate(evidence_text=None),
        "llm_adjudication_handwriting_requires_client_review": candidate(has_handwriting=True),
        "llm_adjudication_reassembly_requires_client_review": candidate(is_reassembly=True),
        "llm_adjudication_disagreement_requires_client_review": candidate(has_disagreement=True),
        "llm_adjudication_financial_field_requires_client_review": candidate(field="total_amount"),
        "llm_adjudication_address_requires_client_review": candidate(field="buyer_address"),
        "llm_adjudication_field_not_in_initial_safe_scope": candidate(field="invoice_number"),
        "llm_adjudication_deterministic_validation_not_clear": candidate(
            deterministic_validation_status="failed"
        ),
        "llm_adjudication_independent_extractor_missing": candidate(independent_extractor=[]),
        "llm_adjudication_independent_extractor_disagrees": candidate(
            independent_extractor={"engine": "b", "value": "Other"}
        ),
        "llm_adjudication_no_amendment_candidate": candidate(
            candidate_value="Acme, Inc.",
            independent_extractor={"engine": "b", "value": "Acme, Inc."},
        ),
    }
    for expected, item in cases.items():
        assert llm.eligibility_reason(item) == expected
    assert llm.eligibility_reason(candidate(original_source="handwritten")) == (
        "llm_adjudication_handwriting_requires_client_review"
    )
    assert llm.eligibility_reason(candidate(field="document_group")) == (
        "llm_adjudication_reassembly_requires_client_review"
    )
    assert llm.eligibility_reason(candidate(is_address=True)) == (
        "llm_adjudication_address_requires_client_review"
    )
    assert llm.eligibility_reason({"field": "seller_name"}) == (
        "llm_adjudication_candidate_missing_required_evidence"
    )
    assert llm.eligibility_reason(candidate(field=" ")) == (
        "llm_adjudication_candidate_missing_required_evidence"
    )
    assert llm.eligibility_reason(candidate(has_disagreement="false")) == (
        "llm_adjudication_candidate_missing_required_evidence"
    )
    with pytest.raises(ValueError, match="candidates or amendments list"):
        llm.load_candidates(write(tmp_path / "bad.json", []))
    with pytest.raises(ValueError, match="candidates or amendments list"):
        llm.load_candidates(write(tmp_path / "missing.json", {}))
    with pytest.raises(ValueError, match="candidates must be a list"):
        llm.load_candidates(write(tmp_path / "bad-candidates.json", {"candidates": ["bad"]}))
    with pytest.raises(ValueError, match="amendments must be a list"):
        llm.load_candidates(write(tmp_path / "bad-amendments.json", {"amendments": ["bad"]}))
    assert llm.load_candidates(write(tmp_path / "ok.json", {"candidates": []})) == []
    amendment = {"amendment_id": "amendment-1", "document_id": "doc-1", "proposed_value": "12"}
    loaded = llm.load_candidates(write(tmp_path / "amendments.json", {"amendments": [amendment]}))
    assert loaded[0]["_source_input_kind"] == "adjudicate_amendment"
    assert loaded[0]["_source_amendment"] == amendment
    for values in ((0, 0.99, 1), (1, 0.98, 1), (1, 1.01, 1), (1, 0.99, 0), (1, 0.99, 1.1)):
        with pytest.raises(ValueError):
            llm.validate_limits(*values)
    llm.validate_limits(1, 0.99, 1.0)


def test_adjudicate_amendments_are_retained_without_provider_calls(tmp_path):
    amendment = {
        "amendment_id": "amendment-1",
        "document_id": "doc-1",
        "target_field": "header.total_amount",
        "original_value": "10.00",
        "proposed_value": "12.00",
        "reason": "arithmetic_unique_candidate",
    }
    candidates_path = write(tmp_path / "amendments.json", {"amendments": [amendment]})
    client = Client([])
    summary = llm.run_adjudication(
        candidates_path,
        manifest(tmp_path),
        tmp_path / "out.json",
        tmp_path / "exceptions.json",
        tmp_path / "handoff.json",
        tmp_path / "raw",
        "test-model",
        client,
    )
    exceptions = json.loads((tmp_path / "exceptions.json").read_text())["exceptions"]
    assert summary["llm_queries_sent"] == 0
    assert exceptions[0]["reason"] == (
        "llm_adjudication_amendment_requires_explicit_candidate_evidence"
    )
    assert exceptions[0]["source_amendment"] == amendment
    assert client.responses.calls == []


def test_page_request_and_amendment_audit(tmp_path):
    page = tmp_path / "page.pdf"
    page.write_text("%PDF-evidence")
    client = Client([Response(decision())])
    response = llm.page_request(client, page, candidate(), "model", "high")
    assert response.output_text
    request = client.responses.calls[0]
    assert request["reasoning"] == {"effort": "high"}
    assert request["input"][0]["content"][1]["type"] == "input_file"
    raw = tmp_path / "raw.json"
    llm.write_raw(raw, {"page_id": "page-1"}, response={"id": "r"})
    proposal = llm.amendment(candidate(), decision(), raw, "model", "high", 0.99, 1.0)
    assert proposal["decision"] == "llm_generated_amendment_proposal"
    assert proposal["client_review_required"]
    assert proposal["audit"]["raw_response_sha256"] == llm.sha256(raw)
    llm.write_raw(tmp_path / "error.json", {}, error="RuntimeError")


def test_run_retains_all_outcomes_and_final_review_gate(tmp_path):
    low = candidate(candidate_id="low")
    abstain = candidate(candidate_id="abstain")
    bad_confidence = candidate(candidate_id="bad-confidence")
    provider_failure = candidate(candidate_id="provider-failure")
    missing_page = candidate(candidate_id="missing-page", page_id="missing")
    financial = candidate(candidate_id="financial", field="total_amount")
    client = Client(
        [
            Response(decision()),
            Response(decision(confidence=0.98)),
            Response(decision("needs_client_review")),
            Response({**decision(), "confidence": True}),
            RuntimeError("network"),
        ]
    )
    summary = run(
        tmp_path,
        [candidate(), low, abstain, bad_confidence, provider_failure, missing_page, financial],
        client,
        sampling_rate=1.0,
    )
    out = json.loads((tmp_path / "out.json").read_text())
    exceptions = json.loads((tmp_path / "exceptions.json").read_text())["exceptions"]
    handoff = json.loads((tmp_path / "handoff.json").read_text())
    assert summary["llm_generated_amendment_proposals"] == 1
    assert summary["client_review_items"] == 7
    assert out["summary"]["client_approval_permitted"] is False
    assert out["amendment_proposals"][0]["decision_source"] == "llm"
    assert out["amendment_proposals"][0]["sampling_required"]
    assert handoff["candidate_count"] == 7
    assert handoff["policy"]["minimum_confidence"] == 0.99
    assert handoff["policy"]["timeout_seconds"] == 120.0
    reasons = {item["reason"] for item in exceptions}
    assert {
        "llm_adjudication_model_confidence_below_threshold",
        "llm_adjudication_model_requires_client_review",
        "llm_adjudication_provider_or_schema_failure",
        "llm_adjudication_page_not_in_manifest",
        "llm_adjudication_financial_field_requires_client_review",
    } <= reasons
    final_items, _, _ = final_review_queue.consolidate([tmp_path / "out.json"])
    assert final_items[0]["disposition"] == "client_review_required"
    assert len(final_items) == 1
    assert len(client.responses.calls) == 5
    assert len(list((tmp_path / "raw").glob("*.json"))) == 5


def test_run_rejects_invalid_configurations_and_retains_schema_failure(tmp_path):
    candidates = write(tmp_path / "candidates.json", {"candidates": [candidate()]})
    manifest_path = manifest(tmp_path)
    targets = (tmp_path / "out.json", tmp_path / "exceptions.json", tmp_path / "handoff.json")
    with pytest.raises(ValueError, match="positive integer"):
        llm.run_adjudication(
            candidates,
            manifest_path,
            *targets,
            tmp_path / "raw",
            "model",
            Client([]),
            max_candidates=0,
        )
    two_candidates = write(
        tmp_path / "two-candidates.json",
        {"candidates": [candidate(), candidate(candidate_id="two")]},
    )
    with pytest.raises(ValueError, match="configured limit"):
        llm.run_adjudication(
            two_candidates,
            manifest_path,
            tmp_path / "too-many-out.json",
            tmp_path / "too-many-exc.json",
            tmp_path / "too-many-handoff.json",
            tmp_path / "too-many-raw",
            "model",
            Client([]),
            max_candidates=1,
        )
    for kwargs, message in (
        ({"max_pdf_bytes": 0}, "PDF bytes"),
        ({"timeout_seconds": 0}, "timeout"),
        ({"max_retries": -1}, "retries"),
        ({"reasoning_effort": "bad"}, "reasoning"),
    ):
        paths = tuple(tmp_path / f"{name}-{len(kwargs)}.json" for name in ("out", "exc", "handoff"))
        with pytest.raises(ValueError, match=message):
            llm.run_adjudication(
                candidates,
                manifest_path,
                *paths,
                tmp_path / f"raw-{len(kwargs)}",
                "model",
                Client([]),
                **kwargs,
            )
    with pytest.raises(ValueError, match="distinct"):
        llm.run_adjudication(
            candidates,
            manifest_path,
            tmp_path / "same.json",
            tmp_path / "same.json",
            tmp_path / "handoff-same.json",
            tmp_path / "raw-same",
            "model",
            Client([]),
        )
    schema_client = Client([Response({"decision": "propose_amendment", "confidence": "bad"})])
    summary = llm.run_adjudication(
        candidates,
        manifest_path,
        tmp_path / "schema-out.json",
        tmp_path / "schema-exc.json",
        tmp_path / "schema-handoff.json",
        tmp_path / "schema-raw",
        "model",
        schema_client,
    )
    assert summary["llm_generated_amendment_proposals"] == 0
    assert json.loads((tmp_path / "schema-exc.json").read_text())["exceptions"][0]["reason"] == (
        "llm_adjudication_provider_or_schema_failure"
    )


def test_main_uses_env_threshold_and_requires_explicit_enable(monkeypatch, tmp_path, capsys):
    candidates_path = write(tmp_path / "candidates.json", {"candidates": []})
    manifest_path = manifest(tmp_path)
    monkeypatch.setattr(llm, "load_project_env", lambda: {})
    disabled_args = [
        llm.__file__,
        str(candidates_path),
        str(manifest_path),
        "--out",
        str(tmp_path / "disabled-out.json"),
        "--exceptions",
        str(tmp_path / "disabled-exc.json"),
        "--handoff-out",
        str(tmp_path / "disabled-handoff.json"),
        "--raw-dir",
        str(tmp_path / "disabled-raw"),
    ]
    monkeypatch.setattr(sys, "argv", disabled_args)
    with pytest.raises(SystemExit, match="disabled"):
        llm.main()
    called = {}
    monkeypatch.setenv("LLM_ADJUDICATION_ENABLED", "true")
    monkeypatch.setenv("LLM_ADJUDICATION_MIN_CONFIDENCE", "0.995")
    monkeypatch.setenv("LLM_ADJUDICATION_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("LLM_ADJUDICATION_MAX_RETRIES", "1")
    monkeypatch.setattr(
        llm, "build_client", lambda *args: called.setdefault("client", args) or object()
    )
    monkeypatch.setattr(
        llm,
        "run_adjudication",
        lambda *args: (
            called.setdefault("args", args)
            or {"llm_generated_amendment_proposals": 0, "client_review_items": 0}
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            llm.__file__,
            str(candidates_path),
            str(manifest_path),
            "--out",
            str(tmp_path / "out.json"),
            "--exceptions",
            str(tmp_path / "exc.json"),
            "--handoff-out",
            str(tmp_path / "handoff.json"),
            "--raw-dir",
            str(tmp_path / "raw"),
            "--quiet",
        ],
    )
    llm.main()
    assert called["client"] == ("OPENAI_API_KEY", 7.0, 1)
    assert called["args"][8] == 0.995
    assert capsys.readouterr().out == ""
    monkeypatch.setenv("LLM_ADJUDICATION_MIN_CONFIDENCE", "0.99")
    monkeypatch.setattr(
        llm,
        "run_adjudication",
        lambda *_args: {"llm_generated_amendment_proposals": 0, "client_review_items": 0},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            llm.__file__,
            str(candidates_path),
            str(manifest_path),
            "--out",
            str(tmp_path / "printed-out.json"),
            "--exceptions",
            str(tmp_path / "printed-exc.json"),
            "--handoff-out",
            str(tmp_path / "printed-handoff.json"),
            "--raw-dir",
            str(tmp_path / "printed-raw"),
        ],
    )
    llm.main()
    assert "LLM amendment proposals: 0" in capsys.readouterr().out
    monkeypatch.setattr(
        llm, "run_adjudication", lambda *_args: (_ for _ in ()).throw(ValueError("bad run"))
    )
    with pytest.raises(SystemExit, match="bad run"):
        llm.main()
    monkeypatch.setenv("LLM_ADJUDICATION_MIN_CONFIDENCE", "bad")
    with pytest.raises(SystemExit, match="MIN_CONFIDENCE"):
        llm.main()
