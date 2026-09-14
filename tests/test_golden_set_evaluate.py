"""Tests for the client-authorized golden-set acceptance harness."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

golden = importlib.import_module("golden_set_evaluate")


def write(path, value):
    path.write_text(json.dumps(value))
    return path


def truth_items():
    return {
        "metadata": {"owner": "client"},
        "items": [
            {
                "item_id": "a",
                "template_fingerprint": "t1",
                "document_family": "invoice",
                "field": "total",
                "risk_category": "financial",
                "expected_value": "10.00",
                "expected_review_required": True,
                "protected": True,
                "baseline_review_required": True,
                "expected_arithmetic_status": "pass",
            },
            {
                "item_id": "b",
                "template_fingerprint": "t2",
                "document_family": "statement",
                "field": "name",
                "risk_category": "identity",
                "expected_value": "A",
                "expected_review_required": False,
                "protected": False,
                "baseline_review_required": True,
                "expected_completeness_status": "complete",
            },
        ],
    }


def predictions():
    return {
        "metadata": {"run": "1"},
        "items": [
            {
                "item_id": "a",
                "status": "predicted",
                "predicted_value": "10.00",
                "review_required": True,
                "auto_accepted": False,
                "arithmetic_status": "pass",
            },
            {
                "item_id": "b",
                "status": "predicted",
                "predicted_value": "A",
                "review_required": False,
                "auto_accepted": True,
                "completeness_status": "complete",
                "provider": "openai",
                "latency_seconds": 1.5,
                "estimated_cost_usd": 0.02,
                "review_minutes": 0.5,
            },
        ],
    }


def loaded(tmp_path):
    truth = golden.load_items(write(tmp_path / "truth.json", truth_items()), "truth")
    proposed = golden.load_items(
        write(tmp_path / "predictions.json", predictions()), "predictions", True
    )
    return truth, proposed


def test_evaluate_success_and_failure_reasons(tmp_path):
    truth, proposed = loaded(tmp_path)
    report, failures = golden.evaluate(truth, proposed, 1, 1, 1, 0, 0)
    assert report["eligible_for_client_acceptance_review"] is True
    assert report["automation_authorized"] is False
    assert report["overall"]["review_reduction_rate"] == 0.5
    assert report["overall"]["estimated_cost_usd"] == 0.02
    assert any(item["dimension"] == "provider" for item in report["breakdowns"])
    assert failures == []
    assert golden.ratio(1, 0) is None
    assert golden.summarize([])["precision"] is None

    bad = golden.load_items(
        write(
            tmp_path / "bad-predictions.json",
            {
                "items": [
                    {
                        "item_id": "a",
                        "status": "predicted",
                        "predicted_value": "wrong",
                        "review_required": False,
                        "auto_accepted": True,
                        "arithmetic_status": "wrong",
                    },
                    {
                        "item_id": "b",
                        "status": "predicted",
                        "predicted_value": "wrong",
                        "review_required": False,
                        "auto_accepted": True,
                        "completeness_status": "wrong",
                    },
                    {
                        "item_id": "extra",
                        "status": "abstained",
                        "review_required": True,
                        "auto_accepted": False,
                    },
                ]
            },
        ),
        "predictions",
        True,
    )
    failed, reviews = golden.evaluate(truth, bad, 1, 1, 1, 0, 0)
    assert failed["eligible_for_client_acceptance_review"] is False
    assert failed["unexpected_prediction_ids"] == ["extra"]
    assert {item["reason"] for item in reviews} >= {
        "golden_set_protected_category_escape",
        "golden_set_false_auto_accept",
    }
    assert reviews[0]["priority"] == "critical"


def test_missing_and_status_mismatch_reviews(tmp_path):
    truth, proposed = loaded(tmp_path)
    del proposed["items"]["a"]
    proposed["items"]["b"].update(
        {"auto_accepted": False, "review_required": True, "completeness_status": "wrong"}
    )
    report, failures = golden.evaluate(truth, proposed, 0, 0, 0, 2, 2)
    assert report["overall"]["abstentions_or_failures"] == 1
    assert [item["reason"] for item in failures if item["document_id"] != "golden-set"] == [
        "golden_set_prediction_missing",
        "golden_set_completeness_status_mismatch",
    ]
    proposed["items"]["a"] = {
        "item_id": "a",
        "status": "predicted",
        "predicted_value": "10.00",
        "review_required": True,
        "auto_accepted": False,
        "arithmetic_status": "wrong",
    }
    _, failures = golden.evaluate(truth, proposed, 0, 0, 0, 2, 2)
    assert next(item for item in failures if item["document_id"] != "golden-set")["reason"] == (
        "golden_set_arithmetic_status_mismatch"
    )
    proposed["items"]["a"]["arithmetic_status"] = "pass"
    proposed["items"]["a"]["review_required"] = False
    _, failures = golden.evaluate(truth, proposed, 0, 0, 0, 2, 2)
    assert next(item for item in failures if item["document_id"] != "golden-set")["reason"] == (
        "golden_set_expected_review_not_captured"
    )


def test_aggregate_threshold_failure_always_emits_blocking_review_items(tmp_path):
    truth, proposed = loaded(tmp_path)
    proposed["items"]["b"].update(
        {
            "predicted_value": "wrong",
            "auto_accepted": False,
            "review_required": False,
            "completeness_status": "complete",
        }
    )
    report, failures = golden.evaluate(truth, proposed, 1, 1, 1, 0, 0)
    assert report["eligible_for_client_acceptance_review"] is False
    assert {item["reason"] for item in failures} >= {
        "golden_set_acceptance_check_failed:precision",
        "golden_set_acceptance_check_failed:recall",
    }


@pytest.mark.parametrize(
    ("payload", "prediction", "message"),
    [
        ([], False, "object containing items"),
        ({"items": [None]}, False, "item_id"),
        (
            {
                "items": [
                    {
                        "item_id": "x",
                        "expected_value": 1,
                        "expected_review_required": False,
                        "protected": False,
                        "baseline_review_required": False,
                        "template_fingerprint": "t",
                        "document_family": "d",
                        "field": "f",
                        "risk_category": "r",
                    }
                ]
                * 2
            },
            False,
            "unique",
        ),
        ({"items": [{"item_id": "x"}]}, True, "status"),
        (
            {
                "items": [
                    {
                        "item_id": "x",
                        "status": "abstained",
                        "review_required": 1,
                        "auto_accepted": False,
                    }
                ]
            },
            True,
            "boolean",
        ),
        (
            {
                "items": [
                    {
                        "item_id": "x",
                        "status": "abstained",
                        "review_required": True,
                        "auto_accepted": False,
                        "latency_seconds": -1,
                    }
                ]
            },
            True,
            "non-negative number",
        ),
        (
            {
                "items": [
                    {
                        "item_id": "x",
                        "status": "abstained",
                        "review_required": False,
                        "auto_accepted": True,
                    }
                ]
            },
            True,
            "auto_accepted",
        ),
        (
            {
                "items": [
                    {
                        "item_id": "x",
                        "status": "predicted",
                        "review_required": False,
                        "auto_accepted": False,
                    }
                ]
            },
            True,
            "predicted_value",
        ),
        (
            {"items": [{"item_id": "x", "expected_value": 1}]},
            False,
            "boolean",
        ),
    ],
)
def test_load_validation(tmp_path, payload, prediction, message):
    with pytest.raises(ValueError, match=message):
        golden.load_items(write(tmp_path / "value.json", payload), "value", prediction)


def test_truth_value_and_group_validation(tmp_path):
    value = truth_items()
    del value["items"][0]["expected_value"]
    with pytest.raises(ValueError, match="expected_value"):
        golden.load_items(write(tmp_path / "missing.json", value), "truth")
    value = truth_items()
    value["items"][0]["field"] = ""
    with pytest.raises(ValueError, match="field"):
        golden.load_items(write(tmp_path / "group.json", value), "truth")


@pytest.mark.parametrize("metric", [True, "slow", float("inf")])
def test_prediction_telemetry_rejects_non_finite_or_non_numeric(tmp_path, metric):
    value = predictions()
    value["items"][0]["latency_seconds"] = metric
    with pytest.raises(ValueError, match="non-negative number"):
        golden.load_items(write(tmp_path / "telemetry.json", value), "predictions", True)


def test_threshold_and_run_main_paths(monkeypatch, tmp_path, capsys):
    truth_path = write(tmp_path / "truth.json", truth_items())
    predictions_path = write(tmp_path / "predictions.json", predictions())
    out, exceptions = tmp_path / "out.json", tmp_path / "exceptions.json"
    result = golden.run(truth_path, predictions_path, out, exceptions, 1, 1, 1, 0, 0)
    assert result["eligible_for_client_acceptance_review"]
    assert json.loads(exceptions.read_text())["summary"]["gate_status"] == "clear"
    with pytest.raises(ValueError, match="distinct and new"):
        golden.run(truth_path, predictions_path, out, exceptions, 1, 1, 1, 0, 0)
    with pytest.raises(ValueError, match="from 0"):
        golden.evaluate(*loaded(tmp_path), -1, 1, 1, 0, 0)
    with pytest.raises(ValueError, match="non-negative"):
        golden.evaluate(*loaded(tmp_path), 1, 1, 1, -1, 0)

    monkeypatch.setattr(
        golden, "run", lambda *args: {"eligible_for_client_acceptance_review": True}
    )
    assert (
        golden.main([str(truth_path), str(predictions_path), "--out", "o", "--exceptions", "e"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["eligible"] is True
    monkeypatch.setattr(golden, "run", lambda *args: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(SystemExit, match="failed"):
        golden.main([str(truth_path), str(predictions_path), "--out", "o", "--exceptions", "e"])
