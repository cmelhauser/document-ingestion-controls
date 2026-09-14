import importlib
import json
import sys

import pytest

context = importlib.import_module("client_review.context")


def write(path, value):
    path.write_text(json.dumps(value))
    return path


def preserved():
    return {
        "proposal_only": True,
        "response_mismatches": [],
        "responses": [
            {
                "decision_id": "g2",
                "your_choice": "Provide alternative in comment",
                "your_note": "Use date two.",
            },
            {"decision_id": "g1", "your_choice": "Defer", "your_note": ""},
        ],
    }


def records():
    return [
        {"document_id": "a", "document_type": "invoice", "total_amount": 1},
        {"document_id": "b", "document_type": "statement", "total_amount": 2},
        {"document_id": "c", "document_type": "invoice", "total_amount": 3},
    ]


def test_validation_and_stratified_selection(tmp_path):
    write(tmp_path / "responses.json", preserved())
    assert len(context.source_records(write(tmp_path / "list.json", records()))) == 3
    selected = context.stratified_records(records(), 2)
    assert len(selected) == 2
    assert {item["document_type"] for item in selected} == {"invoice", "statement"}
    assert context.stratified_records(records(), 5) == records()
    with pytest.raises(ValueError, match="positive"):
        context.stratified_records(records(), 0)
    bad = {"proposal_only": False, "responses": []}
    with pytest.raises(ValueError, match="proposal-only"):
        context.validate_responses(bad)
    bad = {"proposal_only": True, "response_mismatches": ["x"], "responses": []}
    with pytest.raises(ValueError, match="mismatches"):
        context.validate_responses(bad)
    bad = {"proposal_only": True, "response_mismatches": [], "responses": "bad"}
    with pytest.raises(ValueError, match="responses"):
        context.validate_responses(bad)
    bad = {"proposal_only": True, "response_mismatches": [], "responses": [{}]}
    with pytest.raises(ValueError, match="decision_id"):
        context.validate_responses(bad)
    with pytest.raises(ValueError, match="JSON array"):
        context.source_records(write(tmp_path / "bad.json", {"x": 1}))
    with pytest.raises(ValueError, match="JSON array"):
        context.source_records(write(tmp_path / "scalar.json", 4))
    with pytest.raises(ValueError, match="JSON object"):
        context.load_object(write(tmp_path / "list-object.json", []), "value")
    with pytest.raises(ValueError, match="all_decision_rows"):
        context.validate_responses({**preserved(), "all_decision_rows": {}})
    uneven = [
        {"document_id": "a1", "document_type": "a"},
        {"document_id": "b1", "document_type": "b"},
        {"document_id": "b2", "document_type": "b"},
        {"document_id": "b3", "document_type": "b"},
    ]
    assert len(context.stratified_records(uneven, 3)) == 3


def test_build_and_run_preserve_comments_and_hashes(tmp_path, capsys):
    responses = write(tmp_path / "responses.json", preserved())
    source = write(tmp_path / "records.json", {"documents": records()})
    output = tmp_path / "context.json"
    result = context.run(responses, output, source, 2)
    assert result["independent_consensus_input"] is False
    assert result["summary"]["responses"] == 2
    assert result["summary"]["pilot_records"] == 2
    assert result["client_comments"][0]["evidence_role"].startswith("untrusted")
    assert result["source"]["responses_sha256"] == context.digest(responses)
    with pytest.raises(FileExistsError, match="refusing"):
        context.run(responses, output)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        sys,
        "argv",
        ["client_review_context.py", str(responses), "--out", str(tmp_path / "cli.json")],
    )
    context.main()
    assert "consensus remains independent" in capsys.readouterr().out
    monkeypatch.undo()


def test_main_and_input_errors(monkeypatch, tmp_path):
    bad = write(
        tmp_path / "bad.json", {"proposal_only": True, "response_mismatches": [], "responses": []}
    )
    monkeypatch.setattr(
        sys, "argv", ["client_review_context.py", str(bad), "--out", str(tmp_path / "bad-out.json")]
    )
    with pytest.raises(SystemExit, match="responses"):
        context.main()
    malformed = write(tmp_path / "malformed.json", {"documents": ["bad"]})
    with pytest.raises(ValueError, match="JSON array"):
        context.source_records(malformed)
