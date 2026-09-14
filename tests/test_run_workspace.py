"""Hold the run-workspace containment boundary to its evidence-preserving contract."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

workspace = importlib.import_module("run_workspace")


def test_initialize_creates_the_full_fresh_layout_and_refuses_reuse(tmp_path):
    root = tmp_path / "run"
    payload = workspace.initialize(root)
    assert payload["policy"]["generated_artifacts_must_stay_under_run_root"] is True
    assert all((root / name).is_dir() for name in workspace.STANDARD_DIRECTORIES)
    with pytest.raises(FileExistsError, match="new or empty"):
        workspace.initialize(root)


def test_cli_relative_init_and_audit_reports_stay_inside_the_run_root(tmp_path):
    root = tmp_path / "run"
    assert workspace.main(["init", str(root), "--out", "logs/init.json"]) == 0
    assert (root / "logs/init.json").is_file()
    assert not (tmp_path / "logs/init.json").exists()
    assert workspace.main(["audit", str(root), "--out", "logs/audit.json"]) == 0
    assert (root / "logs/audit.json").is_file()
    assert not (tmp_path / "logs/audit.json").exists()


def test_output_resolution_rejects_parent_absolute_and_symlink_escapes(tmp_path):
    root = tmp_path / "run"
    workspace.initialize(root)
    assert (
        workspace.require_within_run_root(root, "controls/result.json")
        == root / "controls/result.json"
    )
    with pytest.raises(ValueError, match="escapes run root"):
        workspace.require_within_run_root(root, "../outside.json")
    with pytest.raises(ValueError, match="escapes run root"):
        workspace.require_within_run_root(root, tmp_path / "outside.json")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "controls" / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes run root"):
        workspace.require_within_run_root(root, "controls/escape/result.json")


def test_command_destination_scanner_accepts_equals_and_rejects_missing_values():
    assert workspace.declared_outputs(
        ["python", "lane.py", "--out=controls/a.json", "--raw-dir", "providers/raw"]
    ) == ["controls/a.json", "providers/raw"]
    with pytest.raises(ValueError, match="needs a destination"):
        workspace.declared_outputs(["python", "lane.py", "--out"])
    with pytest.raises(ValueError, match="needs a destination"):
        workspace.declared_outputs(["python", "lane.py", "--out="])
    assert workspace.declared_outputs(["python", "not-a-script", "--out", "controls/a.json"]) == [
        "controls/a.json"
    ]
    assert workspace.normalize_run_arguments(
        ["RUN", "RUN/controls/a.json", "RUN_DIR/review/q.json", "/source/input.pdf"]
    ) == [
        ".",
        "controls/a.json",
        "review/q.json",
        "/source/input.pdf",
    ]


def test_command_destination_scanner_includes_operations_stage_state():
    assert workspace.declared_outputs(
        [
            sys.executable,
            "/repo/scripts/operations.py",
            "stage",
            "RUN/operations/run_state.json",
            "profile",
        ]
    ) == ["RUN/operations/run_state.json"]


def test_run_command_sets_local_runtime_state_and_records_only_safe_metadata(tmp_path):
    root = tmp_path / "run"
    workspace.initialize(root)
    script = tmp_path / "writer.py"
    script.write_text(
        "import os\nfrom pathlib import Path\n"
        "Path(os.environ['LLM_CACHE_DIR']).joinpath('cached.json').write_text('{}')\n"
        "Path(os.environ['LLM_THROTTLE_DIR']).joinpath('provider.timestamp').write_text('x')\n"
        "Path('controls/result.json').write_text('{}')\n"
    )
    assert (
        workspace.run_command(root, [sys.executable, str(script), "--out", "controls/result.json"])
        == 0
    )
    assert (root / "runtime/cache/cached.json").is_file()
    assert (root / "runtime/throttle/provider.timestamp").is_file()
    assert (root / "controls/result.json").is_file()
    ledger = [
        json.loads(line) for line in (root / "logs/command_ledger.jsonl").read_text().splitlines()
    ]
    assert ledger[0]["declared_outputs"] == ["controls/result.json"]
    assert str(script) not in json.dumps(ledger[0])
    assert workspace.audit(root)["status"] == "ready"


def test_run_command_refuses_an_output_outside_the_workspace(tmp_path):
    root = tmp_path / "run"
    workspace.initialize(root)
    with pytest.raises(ValueError, match="escapes run root"):
        workspace.run_command(
            root, [sys.executable, "lane.py", "--out", str(tmp_path / "outside.json")]
        )


def test_run_command_refuses_a_missing_workspace_or_empty_command(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        workspace.run_command(tmp_path / "missing", [sys.executable, "lane.py"])
    root = tmp_path / "run"
    workspace.initialize(root)
    with pytest.raises(ValueError, match="command is required"):
        workspace.run_command(root, [])
    with pytest.raises(ValueError, match="escapes run root"):
        workspace.run_command(
            root,
            [
                sys.executable,
                "/repo/scripts/operations.py",
                "stage",
                str(tmp_path / "outside-state.json"),
                "profile",
            ],
        )


def test_audit_names_an_escaping_symlink(tmp_path):
    root = tmp_path / "run"
    workspace.initialize(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "review" / "escape").symlink_to(outside, target_is_directory=True)
    result = workspace.audit(root)
    assert result["status"] == "blocked"
    assert result["escaping_symlinks"] == ["review/escape"]


def test_audit_refuses_missing_or_invalid_ledger_records(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        workspace.audit(tmp_path / "missing")
    root = tmp_path / "run"
    workspace.initialize(root)
    ledger = root / "logs/command_ledger.jsonl"
    ledger.write_text('{"declared_outputs": "not-a-list"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid command ledger line 1"):
        workspace.audit(root)
    ledger.write_text('{"declared_outputs": ["../outside.json"]}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid command ledger line 1"):
        workspace.audit(root)


def test_run_environment_always_overrides_repository_global_runtime_paths(tmp_path, monkeypatch):
    root = tmp_path / "run"
    workspace.initialize(root)
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path / "external-cache"))
    monkeypatch.setenv("LLM_THROTTLE_DIR", str(tmp_path / "external-throttle"))
    environment = workspace.run_environment(root)
    assert Path(environment["LLM_CACHE_DIR"]).is_relative_to(root)
    assert Path(environment["LLM_THROTTLE_DIR"]).is_relative_to(root)
    assert environment["BUSINESS_DOC_RUN_ROOT"] == str(root)


def test_write_new_json_refuses_overwrite_and_cli_run_handles_separator(tmp_path, capsys):
    path = tmp_path / "artifact.json"
    workspace.write_new_json(path, {"ok": True})
    with pytest.raises(FileExistsError, match="overwrite"):
        workspace.write_new_json(path, {"ok": False})

    root = tmp_path / "run"
    script = tmp_path / "success.py"
    script.write_text("raise SystemExit(0)\n", encoding="utf-8")
    assert workspace.main(["init", str(root)]) == 0
    assert workspace.main(["run", str(root), "--", sys.executable, str(script)]) == 0
    assert workspace.main(["run", str(root), "--", "--", sys.executable, str(script)]) == 0
    assert "Initialized run workspace" in capsys.readouterr().out


def test_cli_refusals_are_clear_and_never_write_outside_the_workspace(tmp_path):
    root = tmp_path / "run"
    with pytest.raises(SystemExit, match="escapes run root"):
        workspace.main(["init", str(root), "--out", "../outside.json"])
    assert not (tmp_path / "outside.json").exists()
