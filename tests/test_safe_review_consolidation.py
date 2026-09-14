"""Tests for the repeatable safe post-review consolidation stage."""

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

safe = importlib.import_module("client_review.consolidation")


def write(path, value):
    path.write_text(json.dumps(value))
    return path


def test_protected_duplicate_and_carry_forward_helpers(tmp_path):
    with pytest.raises(ValueError):
        safe.validate_threshold(0.98)
    assert safe.protected({"reason": "financial_total"})
    assert not safe.protected({"reason": "formatting_review"})
    item = {"document_id": "d", "field": "formatting_note", "reason": "formatting_review"}
    duplicate = {**item, "source_item_ids": ["b"], "evidence_references": ["e2"]}
    merged, count = safe.merge_duplicate_items(
        [{**item, "source_item_ids": ["a"], "evidence_references": ["e1"]}, duplicate]
    )
    assert count == 1
    assert merged[0]["source_item_ids"] == ["a", "b"]
    assert merged[0]["evidence_references"] == ["e1", "e2"]
    same, same_count = safe.merge_duplicate_items(
        [
            {**item, "source_item_ids": ["a"], "evidence_references": ["e1"]},
            {**item, "source_item_ids": ["a"], "evidence_references": ["e1"]},
        ]
    )
    assert same_count == 1 and len(same) == 1
    proposal = {
        **item,
        "reviewer_decision": "propose_resolution",
        "reviewer_confidence": 0.995,
        "llm_proposed_update": {"field": "formatting_note", "proposed_value": "Acme"},
    }
    assert len(safe.carry_forward_items([proposal], 0.99)) == 1
    assert safe.carry_forward_items([{**proposal, "reason": "financial_total"}], 0.99) == []
    # Self-reported confidence is retained as provenance and is no longer a term
    # in the decision: the protection taxonomy and an evidence-bound proposal are.
    assert len(safe.carry_forward_items([{**proposal, "reviewer_confidence": 0.98}], 0.99)) == 1
    assert (
        safe.carry_forward_items([{**proposal, "reviewer_decision": "retain_review"}], 0.99) == []
    )
    assert safe.carry_forward_items([{**proposal, "llm_proposed_update": None}], 0.99) == []
    assert (
        safe.carry_forward_items([{**proposal, "reviewer_decision": "retain_review"}], 0.99) == []
    )
    assert safe.carry_forward_items([{**proposal, "llm_proposed_update": "bad"}], 0.99) == []
    clusters = safe.review_clusters([item, {**item, "reason": "financial_total"}])
    assert len(clusters) == 2
    assert any(cluster["client_batch_decision_allowed"] is False for cluster in clusters)


def test_completed_lane_validation_is_fail_closed(tmp_path):
    missing_summary = write(tmp_path / "missing.json", {})
    with pytest.raises(ValueError, match="missing summary"):
        safe.validate_completed_lane(missing_summary, "lane")
    failed = write(tmp_path / "failed.json", {"summary": {"analysis_status": "failed"}})
    with pytest.raises(ValueError, match="analysis_status"):
        safe.validate_completed_lane(failed, "lane")
    provider_failed = write(
        tmp_path / "provider.json",
        {"summary": {"analysis_status": "completed", "provider_exceptions": 1}},
    )
    with pytest.raises(ValueError, match="provider exceptions"):
        safe.validate_completed_lane(provider_failed, "lane")
    retention_failed = write(
        tmp_path / "retention.json",
        {
            "summary": {
                "analysis_status": "completed",
                "provider_exceptions": 0,
                "all_source_values_retained": False,
            }
        },
    )
    with pytest.raises(ValueError, match="source-value retention"):
        safe.validate_completed_lane(retention_failed, "lane")


def test_reduced_items_and_consolidate(tmp_path):
    base = {"document_id": "d", "field": "formatting_note", "reason": "formatting_review"}
    queue = write(tmp_path / "queue.json", {"items": [base], "review_cards": []})
    client = write(
        tmp_path / "client.json",
        {
            "cards": [
                {
                    "reduction": {
                        "visible_items": [base, base],
                        "covered_items": [{"review_item_id": "covered"}],
                        "auto_accepted_updates": [],
                    }
                }
            ]
        },
    )
    cross = write(
        tmp_path / "cross.json",
        {
            "summary": {"analysis_status": "completed", "provider_exceptions": 0},
            "matches": [{"id": 1}],
            "remaining_matches": [{"id": 1}],
        },
    )
    agent = write(
        tmp_path / "agent.json",
        {
            "summary": {
                "iterations": 1,
                "analysis_status": "completed",
                "provider_exceptions": 0,
            },
            "final_reduction": {"hypotheses": [1]},
        },
    )
    output = tmp_path / "safe.json"
    result = safe.consolidate(queue, client, cross, agent, output)
    assert result["summary"]["source_items"] == 1
    assert result["summary"]["visible_items"] == 1
    assert result["summary"]["exact_duplicate_presentations_collapsed"] == 1
    assert result["summary"]["covered_dependency_items"] == 1
    assert result["cross_record_proposals"] == [{"id": 1}]
    with pytest.raises(FileExistsError, match="overwrite"):
        safe.consolidate(queue, client, cross, agent, output)
    write(tmp_path / "cross_exceptions.json", {"summary": {"count": 1}})
    with pytest.raises(ValueError, match="exception artifact"):
        safe.consolidate(queue, client, cross, agent, tmp_path / "failed.json")
    (tmp_path / "cross_exceptions.json").unlink()
    unavailable_cross = write(
        tmp_path / "cross-failed.json",
        {"summary": {"analysis_status": "failed", "matches_available": False}, "matches": []},
    )
    with pytest.raises(ValueError, match="analysis_status='failed'"):
        safe.consolidate(queue, client, unavailable_cross, agent, tmp_path / "unavailable.json")
    grouped = safe.consolidate(
        queue,
        client,
        cross,
        agent,
        tmp_path / "grouped.json",
        grouping_enabled=True,
        grouping_consensus={"documents": []},
    )
    assert grouped["summary"]["decision_grouping_enabled"] is True
    assert grouped["summary"]["decision_groups"] == 1
    assert grouped["decision_grouping"]["requested_group_count"] is None
    assert grouped["decision_grouping"]["group_count_derived_from_data"] is True
    assert grouped["decision_groups"][0]["all_source_items_retained"] is True
    assert grouped["decision_groups"][0]["source_collection"] == "client_visible_items"
    assert grouped["client_presentation"]["mode"] == "data_derived_decision_groups"
    incomplete = safe.reduced_items(
        {"summary": {"provider_exceptions": 1}, "cards": [{"reduction": {"visible_items": []}}]},
        [base],
    )
    assert incomplete[0] == [base] and incomplete[1] == []
    partial = safe.reduced_items(
        {
            "summary": {"provider_exceptions": 1, "reviewer_cards": 2, "successful_cards": 1},
            "cards": [
                {
                    "reduction": {
                        "all_items": [{"review_item_id": "review-item-0", **base}],
                        "visible_items": [],
                        "covered_items": [base],
                        "auto_accepted_updates": [],
                    }
                }
            ],
        },
        [base, {**base, "document_id": "d2"}],
    )
    assert partial[1] == [base]
    assert partial[0] == [{**base, "document_id": "d2"}]
    fallback = safe.reduced_items({"cards": []}, [base])
    assert fallback[0] == [base]
    with pytest.raises(ValueError):
        safe.consolidate(
            write(tmp_path / "bad.json", {"items": {}}),
            client,
            cross,
            agent,
            tmp_path / "bad-out.json",
        )


def test_consolidate_can_emit_small_client_package(tmp_path):
    base = {
        "review_item_id": "review-item-0",
        "document_id": "d",
        "page_id": "p",
        "field": "formatting_note",
        "reason": "formatting_review",
    }
    queue = write(tmp_path / "queue.json", {"items": [base]})
    client = write(tmp_path / "client.json", {"cards": []})
    cross = write(
        tmp_path / "cross.json",
        {"summary": {"analysis_status": "completed", "provider_exceptions": 0}, "matches": []},
    )
    agent = write(
        tmp_path / "agent.json",
        {
            "summary": {"analysis_status": "completed", "provider_exceptions": 0},
            "final_reduction": {},
        },
    )
    package_dir = tmp_path / "client_package"
    safe.consolidate(
        queue,
        client,
        cross,
        agent,
        tmp_path / "safe.json",
        grouping_enabled=True,
        grouping_consensus={"documents": []},
        client_package_dir=package_dir,
    )
    assert sorted(path.name for path in package_dir.iterdir()) == [
        "client_review_guide.docx",
        "client_review_package.xlsx",
        "decision_pages.docx",
    ]
    assert (package_dir.parent / "client_package.zip").exists()


def test_load_and_run_lane_failures(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        safe.load_json(write(tmp_path / "list.json", []))
    monkeypatch.setattr(
        safe.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1)
    )
    with pytest.raises(RuntimeError, match="exit code"):
        safe.run_lane(["false"], tmp_path / "missing.json")
    monkeypatch.setattr(
        safe.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0)
    )
    with pytest.raises(RuntimeError, match="did not create"):
        safe.run_lane(["true"], tmp_path / "missing.json")
    created = tmp_path / "created.json"
    monkeypatch.setattr(
        safe.subprocess,
        "run",
        lambda *args, **kwargs: (created.write_text("{}"), subprocess.CompletedProcess(args[0], 0))[
            1
        ],
    )
    safe.run_lane(["true"], created)


def test_main_disabled_and_enabled(tmp_path, monkeypatch):
    missing_run = tmp_path / "missing-run"
    missing_run.mkdir()
    with pytest.raises(SystemExit, match="requires completed final-review queue outputs"):
        safe.main(["--run-dir", str(missing_run), "--enable"])

    run = tmp_path / "run"
    run.mkdir()
    post = run / "post_review_consolidation"
    post.mkdir()
    for name, value in (
        ("final_client_review.json", {"items": []}),
        ("client_review_llm.json", {"cards": []}),
        (
            "post_review_consolidation/client_review_cross_record.json",
            {
                "summary": {"analysis_status": "completed", "provider_exceptions": 0},
                "matches": [],
                "remaining_matches": [],
            },
        ),
        (
            "post_review_consolidation/review_agent.json",
            {
                "summary": {"analysis_status": "completed", "provider_exceptions": 0},
                "final_reduction": {},
            },
        ),
    ):
        write(run / name, value)
    monkeypatch.delenv("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_ENABLED", raising=False)
    monkeypatch.delenv("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_CARRY_FORWARD", raising=False)
    monkeypatch.delenv("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_THRESHOLD", raising=False)
    monkeypatch.delenv("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_OUTPUT_DIR", raising=False)
    monkeypatch.delenv("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_OUTPUT_FILE", raising=False)
    monkeypatch.setattr(safe, "load_project_env", lambda: None)
    assert safe.main(["--run-dir", str(run)]) == 0
    assert (run / "post_review_consolidation/safe_review_consolidation.json").is_file()
    with pytest.raises(SystemExit, match="output already exists"):
        safe.main(["--run-dir", str(run)])
    (run / "post_review_consolidation/safe_review_consolidation.json").unlink()
    calls = []

    def fake_lane(command, output):
        calls.append(command)
        if output.name.startswith("client_review_cross_record"):
            write(
                output,
                {
                    "summary": {"analysis_status": "completed", "provider_exceptions": 0},
                    "matches": [],
                    "remaining_matches": [],
                },
            )
        else:
            write(
                output,
                {
                    "summary": {"analysis_status": "completed", "provider_exceptions": 0},
                    "final_reduction": {},
                },
            )

    monkeypatch.setattr(safe, "run_lane", fake_lane)
    monkeypatch.setenv("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_ENABLED", "true")
    monkeypatch.setenv("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_CARRY_FORWARD", "true")
    monkeypatch.setenv("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_THRESHOLD", "0.995")
    monkeypatch.setenv("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_OUTPUT_DIR", "post_review_retry")
    monkeypatch.setenv("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_OUTPUT_FILE", "custom.json")
    monkeypatch.setenv("LLM_POST_REVIEW_PROVIDER", "google")
    monkeypatch.setenv("CLIENT_REVIEW_GROUPING_ENABLED", "true")
    assert (
        safe.main(
            [
                "--run-dir",
                str(run),
                "--context",
                "final_client_review.json",
                "--output-dir",
                "post_review_retry",
            ]
        )
        == 0
    )
    assert len(calls) == 2
    assert (run / "post_review_retry/custom.json").is_file()
    assert all("--final-provider" in call and "google" in call for call in calls)

    existing = run / "post_review_existing"
    existing.mkdir()
    write(existing / "client_review_cross_record.json", {})
    monkeypatch.setenv("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_OUTPUT_DIR", "post_review_existing")
    with pytest.raises(SystemExit, match="existing artifacts"):
        safe.main(["--run-dir", str(run), "--enable"])

    write(run / "consensus.json", {"documents": []})
    monkeypatch.setenv("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_OUTPUT_DIR", "post_review_explicit")
    assert (
        safe.main(
            [
                "--run-dir",
                str(run),
                "--context",
                "final_client_review.json",
                "--grouping-consensus",
                "consensus.json",
            ]
        )
        == 0
    )
    assert (run / "post_review_explicit/custom.json").is_file()


def test_main_requires_outputs(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    write(run / "final_client_review.json", {"items": []})
    with pytest.raises(SystemExit, match="requires completed"):
        safe.main(["--run-dir", str(run)])


def test_main_requires_post_review_outputs_when_disabled(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    write(run / "final_client_review.json", {"items": [], "review_cards": []})
    write(run / "client_review_llm.json", {"cards": []})
    monkeypatch.delenv("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_ENABLED", raising=False)
    monkeypatch.setattr(safe, "load_project_env", lambda: None)
    with pytest.raises(SystemExit, match="requires completed client-review"):
        safe.main(["--run-dir", str(run)])


def test_the_lanes_this_orchestrates_actually_resolve():
    """REPOSITORY_ROOT must reach the scripts it invokes.

    Moving this module into scripts/client_review/ moved the repository root one
    level further up, and parents[1] silently became scripts/. Every lane was
    then invoked as scripts/scripts/<lane>.py and failed with exit code 2. The
    suite never noticed because it does not run the subprocess, so the paths are
    asserted directly.
    """
    root = safe.REPOSITORY_ROOT
    assert (root / "scripts").is_dir()
    assert (root / "AGENTS.md").is_file(), "REPOSITORY_ROOT is not the repository root"
    for lane in (
        "client_review_cross_record.py",
        "review_agent.py",
        "review_grouping.py",
        "client_review_package.py",
    ):
        assert (root / "scripts" / lane).is_file(), f"{lane} does not resolve from REPOSITORY_ROOT"


def test_an_explicit_output_flag_beats_the_environment(monkeypatch):
    """Settings supply defaults; a flag the operator typed wins.

    The environment was read at use time instead, so --output-dir and --out were
    accepted and silently ignored, and the artifacts landed where the operator
    had not asked for them.
    """
    monkeypatch.setenv("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_OUTPUT_DIR", "from_env")
    monkeypatch.setenv("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_OUTPUT_FILE", "from_env.json")
    monkeypatch.setenv("CLIENT_REVIEW_LLM_SAFE_CONSOLIDATION_THRESHOLD", "0.5")

    # With no flags, the environment supplies the defaults.
    defaults = safe.build_parser().parse_args(["--run-dir", "."])
    assert defaults.output_dir == "from_env"
    assert defaults.out == "from_env.json"
    assert defaults.carry_forward_threshold == 0.5

    # With flags, the operator wins.
    chosen = safe.build_parser().parse_args(
        [
            "--run-dir",
            ".",
            "--output-dir",
            "from_flag",
            "--out",
            "from_flag.json",
            "--carry-forward-threshold",
            "0.99",
        ]
    )
    assert chosen.output_dir == "from_flag"
    assert chosen.out == "from_flag.json"
    assert chosen.carry_forward_threshold == 0.99
