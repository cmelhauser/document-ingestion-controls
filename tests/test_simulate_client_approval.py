"""Tests for the explicit client-approval simulation boundary."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

simulation = importlib.import_module("simulate_client_approval")


def test_simulation_preserves_items_and_blocks_production():
    result = simulation.simulate({"items": [{"reason": "financial"}, {"reason": "missing_field"}]})
    assert result["simulation_only"] is True
    assert result["production_approval_permitted"] is False
    assert result["summary"]["simulated_approved_items"] == 2
    assert result["summary"]["production_facts_approved"] == 0
    assert all(item["simulated_accepted"] for item in result["items"])
    assert all(item["production_client_review_required"] for item in result["items"])
    assert simulation.downstream_gate(result)["production_approved_facts_loaded"] == 0


def test_load_queue_and_no_clobber(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps({"items": []}))
    assert simulation.load_queue(queue)["items"] == []
    output = tmp_path / "nested" / "out.json"
    simulation.require_new(output)
    output.write_text("{}")
    with pytest.raises(FileExistsError):
        simulation.require_new(output)


def test_invalid_queue_and_cli_failure(tmp_path):
    with pytest.raises(OSError):
        simulation.load_queue(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("[]")
    sys.argv = ["simulate_client_approval.py", str(bad), "--out", str(tmp_path / "out.json")]
    assert simulation.main() == 1


def test_cli_success(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps({"items": [{"reason": "provider"}]}))
    output = tmp_path / "out.json"
    gate = tmp_path / "gate.json"
    sys.argv = [
        "simulate_client_approval.py",
        str(queue),
        "--out",
        str(output),
        "--downstream-gate-out",
        str(gate),
    ]
    assert simulation.main() == 0
    assert json.loads(output.read_text())["summary"]["input_review_items"] == 1
    assert json.loads(gate.read_text())["simulation_only"] is True


def test_cli_success_without_terminal_gate(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps({"items": []}))
    output = tmp_path / "out.json"
    sys.argv = ["simulate_client_approval.py", str(queue), "--out", str(output)]
    assert simulation.main() == 0
