"""Tests for review-safe handwriting and party-role calibration."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

calibration = importlib.import_module("detection_calibration")


def write(path, value):
    path.write_text(json.dumps(value))
    return path


def input_items(task="party_role"):
    return {
        "task": task,
        "items": [{"item_id": "1", "label": "payer"}, {"item_id": "2", "label": "dealer"}],
    }


def predictions(task="party_role"):
    return {
        "task": task,
        "items": [
            {"item_id": "1", "label": "payer", "score": 0.99},
            {"item_id": "2", "label": "dealer", "score": 0.99},
        ],
    }


def test_evaluation_and_calibration(tmp_path):
    truth = calibration.index(input_items()["items"])
    proposed = calibration.index(predictions()["items"], True)
    metrics = calibration.evaluate(truth, proposed, 0.9)
    assert all(item["precision"] == item["recall"] == 1 for item in metrics)
    result = calibration.calibrate(input_items(), predictions(), [0.5, 0.9], 0.99, 0.99)
    assert result["recommended_threshold"] == 0.9
    assert result["deployment_permitted"] is False
    mismatch = predictions("handwriting_region")
    with pytest.raises(ValueError, match="same task"):
        calibration.calibrate(input_items(), mismatch, [0.5], 0.9, 0.9)
    bad = predictions()
    bad["items"][0]["score"] = 2
    with pytest.raises(ValueError, match="score"):
        calibration.index(bad["items"], True)
    with pytest.raises(ValueError, match="unique"):
        calibration.index(input_items()["items"] * 2)
    assert calibration.thresholds("0.5,0.9") == [0.5, 0.9]
    with pytest.raises(ValueError, match="thresholds"):
        calibration.thresholds("x")
    with pytest.raises(ValueError, match="from 0"):
        calibration.thresholds("1.1")
    with pytest.raises(ValueError, match="task"):
        calibration.load(write(tmp_path / "bad-task.json", {"task": "other", "items": []}), "truth")
    with pytest.raises(ValueError, match="item_id"):
        calibration.index([{"label": "payer"}])
    with pytest.raises(ValueError, match="label"):
        calibration.index([{"item_id": "x"}])
    mixed = calibration.index(
        {"items": [{"item_id": "1", "label": "payer"}, {"item_id": "2", "label": "dealer"}]}[
            "items"
        ]
    )
    wrong = calibration.index(
        [
            {"item_id": "1", "label": "dealer", "score": 1},
            {"item_id": "extra", "label": "payer", "score": 1},
        ],
        True,
    )
    values = {item["label"]: item for item in calibration.evaluate(mixed, wrong, 0.5)}
    assert values["payer"]["false_negative"] and values["dealer"]["false_positive"]
    assert (
        calibration.calibrate(input_items(), {"task": "party_role", "items": []}, [0.5], 0.9, 0.9)[
            "recommended_threshold"
        ]
        is None
    )


def test_run_and_main(monkeypatch, tmp_path, capsys):
    truth, proposed = (
        write(tmp_path / "truth.json", input_items("handwriting_region")),
        write(tmp_path / "predictions.json", predictions("handwriting_region")),
    )
    result = calibration.run(truth, proposed, tmp_path / "out.json", "0.5", 0.9, 0.9)
    assert result["task"] == "handwriting_region"
    with pytest.raises(ValueError, match="exists"):
        calibration.run(truth, proposed, tmp_path / "out.json", "0.5", 0.9, 0.9)
    with pytest.raises(ValueError, match="minimum"):
        calibration.run(truth, proposed, tmp_path / "x.json", "0.5", 2, 0.9)
    with pytest.raises(ValueError, match="items"):
        calibration.load(write(tmp_path / "bad.json", {}), "truth")
    monkeypatch.setattr(
        calibration, "run", lambda *args: {"task": "party_role", "recommended_threshold": None}
    )
    monkeypatch.setattr(
        sys, "argv", ["detection_calibration.py", "truth", "predictions", "--out", "out"]
    )
    calibration.main()
    assert json.loads(capsys.readouterr().out)["task"] == "party_role"
    monkeypatch.setattr(calibration, "run", lambda *args: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(SystemExit, match="failed"):
        calibration.main()
