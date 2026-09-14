"""Tests for sequential table-comprehension corpus orchestration without provider calls."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

corpus = importlib.import_module("table_comprehension_corpus")


def write(path, value):
    path.write_text(json.dumps(value) if not isinstance(value, str) else value)
    return path


def manifest(tmp_path, name, page_id):
    write(tmp_path / f"{name}.pdf", "%PDF-test")
    return write(
        tmp_path / f"{name}.json", {"pages": [{"page_id": page_id, "page_pdf": f"{name}.pdf"}]}
    )


def box():
    return {"left": 0.1, "top": 0.1, "right": 0.2, "bottom": 0.2}


def profile(page_id):
    return {
        "role": "profile",
        "status": "proposal",
        "page_id": page_id,
        "payload": {
            "document_family": "statement",
            "table_regions": [
                {
                    "region_id": "table",
                    "headers": [{"source_label": "Amount"}],
                    "sections": ["North"],
                    "totals": ["Total"],
                    "review_flags": [],
                }
            ],
            "handwriting_regions": [],
            "quality_diagnostics": [],
        },
    }


def fake_role(contexts):
    def run(
        manifest_path,
        page_id,
        out_path,
        raw_dir,
        model,
        client,
        effort,
        role,
        context=None,
        independent_evidence=None,
    ):
        contexts.append((role, page_id, context, independent_evidence))
        Path(raw_dir).mkdir(parents=True)
        if role == "profile":
            packet = profile(page_id)
        elif role == "rows":
            packet = {
                "role": role,
                "status": "proposal",
                "page_id": page_id,
                "payload": {
                    "rows": [
                        {
                            "source_row_id": "r",
                            "region_id": "table",
                            "row_number": 1,
                            "box": box(),
                            "cells": [
                                {
                                    "source_label": "Amount",
                                    "visible_value": "1",
                                    "box": box(),
                                    "evidence_text": "1",
                                }
                            ],
                            "review_flags": [],
                        }
                    ],
                    "quality_diagnostics": [],
                },
            }
        else:
            packet = {
                "role": role,
                "status": "proposal",
                "page_id": page_id,
                "payload": {"findings": [], "quality_diagnostics": []},
            }
        write(Path(out_path), packet)
        return packet

    return run


def test_helpers_and_layout_context(tmp_path):
    first = manifest(tmp_path, "first", "p1")
    assert len(corpus.digest(first)) == 64
    assert corpus.empty_context()["observed_templates"] == []
    assert corpus.corpus_pages([first])[0][1] == "p1"
    duplicate = manifest(tmp_path, "duplicate", "p1")
    with pytest.raises(ValueError, match="Duplicate"):
        corpus.corpus_pages([first, duplicate])
    with pytest.raises(ValueError, match="At least"):
        corpus.corpus_pages([])
    context = corpus.advance_context(corpus.empty_context(), profile("p1"))
    assert context["observed_templates"][0]["source_page_id"] == "p1"
    assert (
        corpus.advance_context(context, profile("p2"))["observed_templates"]
        == context["observed_templates"]
    )
    assert corpus.advance_context(context, {"status": "failed"}) == context
    assert (
        corpus.advance_context(
            context, {"status": "proposal", "payload": {"table_regions": ["skip", {"headers": []}]}}
        )
        == context
    )
    assert corpus.provider_context(
        profile("p1"), {"payload": {"rows": []}, "raw_response": "/secret"}, context
    )["source_rows"] == {"rows": []}
    state = corpus.initial_state([first], "model", "medium", 1, 0)
    assert state["context_snapshot"] == "context/000000.json"
    corpus.valid_resume(state, [first], "model", "medium", 1, 0)
    for changed, message in [
        ({**state, "manifest_hashes": []}, "different intake"),
        ({**state, "model_configuration": {}}, "model configuration"),
        ({**state, "completed_page_ids": "bad"}, "must be a list"),
        ({**state, "refinement_rounds": "bad"}, "must be a list"),
        ({**state, "refinement_tolerance": 1}, "refinement controls"),
    ]:
        with pytest.raises(ValueError, match=message):
            corpus.valid_resume(changed, [first], "model", "medium", 1, 0)
    evidence = write(tmp_path / "evidence.json", {"records": []})
    with pytest.raises(ValueError, match="independent evidence"):
        corpus.valid_resume(state, [first], "model", "medium", 1, 0, evidence)
    client_context_path = write(
        tmp_path / "client-context.json",
        {
            "artifact_type": "client_review_context_v1",
            "reasoning_only": True,
            "independent_consensus_input": False,
            "policy": {"client_comments_are_untrusted_context": True},
            "client_comments": [{"client_comment": "check address"}],
        },
    )
    client_context = corpus.load_client_context(client_context_path)
    assert client_context["comments"][0]["client_comment"] == "check address"
    assert client_context["sha256"] == corpus.digest(client_context_path)
    with pytest.raises(ValueError, match="reasoning-only"):
        corpus.load_client_context(write(tmp_path / "bad-context.json", {}))
    with pytest.raises(ValueError, match="positive"):
        corpus.load_client_context(client_context_path, 0)
    with pytest.raises(ValueError, match="comments exceed the configured byte limit"):
        corpus.load_client_context(client_context_path, 1)

    # This lane discards `pilot_records` and sends only the comments, so bounding
    # the whole file refused a usable context because of records that never reach
    # a provider: a records-bearing context is 1.5 MB where its comments are
    # under 2 KB. The budget measures what is actually sent.
    with_records = write(
        tmp_path / "records-context.json",
        {
            "artifact_type": "client_review_context_v1",
            "reasoning_only": True,
            "independent_consensus_input": False,
            "policy": {"client_comments_are_untrusted_context": True},
            "client_comments": [{"client_comment": "check address"}],
            "pilot_records": [
                {"document_id": f"d{index}", "pad": "x" * 4000} for index in range(200)
            ],
        },
    )
    assert with_records.stat().st_size > 500_000
    loaded = corpus.load_client_context(with_records)
    assert loaded["comments"][0]["client_comment"] == "check address"
    assert "pilot_records" not in loaded, "records must never reach a provider packet"

    # The file still has a ceiling, so nothing unbounded is read into memory.
    monkeypatch_ceiling = corpus.CLIENT_CONTEXT_FILE_CEILING
    corpus.CLIENT_CONTEXT_FILE_CEILING = 10
    try:
        with pytest.raises(ValueError, match="file exceeds the readable ceiling"):
            corpus.load_client_context(with_records)
    finally:
        corpus.CLIENT_CONTEXT_FILE_CEILING = monkeypatch_ceiling
    context_state = corpus.initial_state(
        [first], "model", "medium", 1, 0, client_context=client_context
    )
    corpus.valid_resume(
        context_state, [first], "model", "medium", 1, 0, client_context=client_context
    )
    with pytest.raises(ValueError, match="client-input context"):
        corpus.valid_resume(context_state, [first], "model", "medium", 1, 0)
    target = tmp_path / "new.json"
    corpus.write_new(target, {"x": 1})
    with pytest.raises(FileExistsError):
        corpus.write_new(target, {})
    corpus.replace_state(tmp_path / "state.json", {"x": 1})
    assert json.loads((tmp_path / "state.json").read_text()) == {"x": 1}


def test_refinement_profile_reuse_configuration_rejects_invalid_values(monkeypatch):
    monkeypatch.setattr(corpus, "env_value", lambda *_: "false")
    assert corpus.refinement_reuses_retained_profile() is False
    monkeypatch.setattr(corpus, "env_value", lambda *_: "invalid")
    with pytest.raises(ValueError, match="must be true or false"):
        corpus.refinement_reuses_retained_profile()


def test_sequential_corpus_and_one_backfill_round(monkeypatch, tmp_path):
    manifests = [manifest(tmp_path, "first", "p1"), manifest(tmp_path, "second", "p2")]
    contexts = []
    monkeypatch.setattr(corpus, "run_role", fake_role(contexts))
    client_context = {
        "artifact_type": "client_review_context_v1",
        "reasoning_only": True,
        "independent_consensus_input": False,
        "sha256": "context-sha",
        "source_name": "comments.json",
        "comments": [{"client_comment": "check delivery address"}],
    }
    result = corpus.run_corpus(
        manifests,
        tmp_path / "run",
        None,
        "model",
        "client",
        max_pages=2,
        client_context=client_context,
    )
    assert result == {
        "pages": 2,
        "proposal_records": 4,
        "source_rows": 4,
        "decision_cards": 4,
        "refinement_amendments": 0,
    }
    profile_contexts = [
        item[2]["corpus_layout_context"] for item in contexts if item[0] == "profile"
    ]
    assert (
        profile_contexts[1]["observed_templates"]
        and len(profile_contexts[-1]["observed_templates"]) == 1
    )
    root = tmp_path / "run"
    register = json.loads(
        (root / "refinements" / "round_01" / "refinement_register.json").read_text()
    )
    assert register["summary"]["count"] == 2
    source_rows = json.loads((root / "corpus_source_rows.json").read_text())["source_rows"]
    assert {item["proposal_revision"] for item in source_rows} == {"initial", "round_01"}
    assert [role for role, *_ in contexts].count("profile") == 2
    assert all(item[2]["client_input_comments"]["reasoning_only"] is True for item in contexts)
    assert (
        json.loads((root / "corpus_index.json").read_text())["client_context_sha256"]
        == "context-sha"
    )

    reprofile_contexts = []
    monkeypatch.setattr(corpus, "run_role", fake_role(reprofile_contexts))
    monkeypatch.setattr(corpus, "refinement_reuses_retained_profile", lambda: False)
    corpus.run_corpus(manifests, tmp_path / "run-reprofile", None, "model", "client", max_pages=2)
    assert [role for role, *_ in reprofile_contexts].count("profile") == 4

    state = json.loads((root / "corpus_state.json").read_text())
    assert state["status"] == "complete"
    assert (
        corpus.run_corpus(
            manifests,
            root,
            None,
            "model",
            "client",
            max_pages=2,
            resume=True,
            client_context=client_context,
        )["page_count"]
        == 2
    )
    with pytest.raises(ValueError, match="new"):
        corpus.run_corpus(
            manifests, root, None, "model", "client", max_pages=2, client_context=client_context
        )


def test_limits_resume_and_refinement_helpers(monkeypatch, tmp_path):
    value = manifest(tmp_path, "first", "p1")
    with pytest.raises(ValueError, match="max pages"):
        corpus.run_corpus([value], tmp_path / "x", None, "m", "c", max_pages=0)
    extra = manifest(tmp_path, "second", "p2")
    with pytest.raises(ValueError, match="exceed"):
        corpus.run_corpus([value, extra], tmp_path / "too-small", None, "m", "c", max_pages=1)
    with pytest.raises(ValueError, match="Resume"):
        corpus.run_corpus([value], tmp_path / "missing", None, "m", "c", resume=True)
    root = tmp_path / "needs"
    root.mkdir()
    paths = corpus.paths_for(root, 1, "p1")
    paths["profile"].parent.mkdir(parents=True)
    for name in ("profile", "rows", "audit"):
        write(paths[name], {"status": "proposal"})
    write(paths["cards"], {"decision_cards": []})
    write(paths["quality"], {"diagnostics": []})
    assert corpus.needs_refinement(root, 1, "p1") is False
    write(paths["profile"], {"status": "failed"})
    assert corpus.needs_refinement(root, 1, "p1") is True
    write(paths["profile"], {"status": "proposal"})
    write(paths["cards"], {"decision_cards": [{"x": 1}]})
    assert corpus.needs_refinement(root, 1, "p1") is True
    snapshot = root / "context.json"
    write(snapshot, {})
    round_root = root / "refinements" / "round_01"
    corpus.paths_for(round_root, 1, "p1")["source_rows"].parent.mkdir(parents=True)
    for name in ("source_rows", "cards", "quality", "exceptions"):
        write(
            corpus.paths_for(round_root, 1, "p1")[name],
            {"source_rows": [], "decision_cards": [], "diagnostics": []},
        )
    item = corpus.refinement_register_item(root, round_root, 1, "p1", snapshot)
    assert item["disposition"].startswith("refinement")
    initial_rows = {
        "source_rows": [
            {
                "source_row_id": "r",
                "cells": [
                    {
                        "source_label": "Amount",
                        "visible_value": "1",
                        "evidence_text": "1",
                        "canonical_mapping": {"canonical_field": "total_amount"},
                    }
                ],
            }
        ]
    }
    refined_rows = {
        "source_rows": [
            {
                "source_row_id": "r",
                "region_id": "table",
                "cells": [
                    {
                        "source_label": "Amount",
                        "visible_value": "2",
                        "evidence_text": "2",
                        "canonical_mapping": {"canonical_field": "total_amount"},
                    }
                ],
            }
        ]
    }
    initial_path = corpus.paths_for(root, 1, "p1")["source_rows"]
    refined_path = corpus.paths_for(round_root, 1, "p1")["source_rows"]
    initial_path.parent.mkdir(parents=True, exist_ok=True)
    refined_path.parent.mkdir(parents=True, exist_ok=True)
    write(initial_path, initial_rows)
    write(refined_path, refined_rows)
    assert corpus.refinement_amendments(root, round_root, 1, "p1", snapshot)[0][
        "reason"
    ].startswith("financial")
    same_rows = refined_rows.copy()
    same_rows["source_rows"][0]["cells"][0]["visible_value"] = "1"
    write(refined_path, same_rows)
    assert corpus.refinement_amendments(root, round_root, 1, "p1", snapshot) == []
    write(refined_path, {"source_rows": ["skip", {"source_row_id": "r", "cells": ["skip"]}]})
    assert corpus.refinement_amendments(root, round_root, 1, "p1", snapshot) == []
    monkeypatch.setattr(corpus, "run_corpus", lambda *args: {"pages": 1})
    monkeypatch.setattr(corpus, "load_project_env", lambda: None)
    monkeypatch.setattr(corpus, "build_client", lambda *args: "client")
    monkeypatch.setattr(
        sys,
        "argv",
        ["table_comprehension_corpus.py", "m", "--out", "o", "--refinement-passes", "0"],
    )
    corpus.main()


def test_resume_reuses_retained_profile_before_page_completion(monkeypatch, tmp_path):
    value = manifest(tmp_path, "first", "p1")
    root = tmp_path / "resume-profile"
    root.mkdir()
    (root / "context").mkdir()
    write(root / "context" / "000000.json", corpus.empty_context())
    state = corpus.initial_state([value], "model", "medium", 0, 0)
    corpus.replace_state(root / "corpus_state.json", state)
    targets = corpus.paths_for(root, 1, "p1")
    targets["profile"].parent.mkdir(parents=True)
    write(targets["profile"], profile("p1"))
    contexts = []
    monkeypatch.setattr(corpus, "run_role", fake_role(contexts))

    result = corpus.run_corpus(
        [value], root, None, "model", "client", resume=True, refinement_passes=0
    )

    assert result["pages"] == 1
    assert [role for role, *_ in contexts] == ["rows", "audit"]
    assert json.loads((root / "corpus_state.json").read_text())["status"] == "complete"


def test_refinement_profile_reuse_requires_the_matching_initial_profile(monkeypatch, tmp_path):
    value = manifest(tmp_path, "first", "p1")
    root = tmp_path / "refinement-profile"
    (root / "context").mkdir(parents=True)
    write(root / "context" / "000000.json", corpus.empty_context())
    state = corpus.initial_state([value], "model", "medium", 1, 0)
    state["active_refinement"] = {
        "round": 1,
        "candidate_page_ids": ["p1"],
        "completed_page_ids": [],
    }
    monkeypatch.setattr(corpus, "refinement_reuses_retained_profile", lambda: True)
    with pytest.raises(ValueError, match="requires the retained initial profile"):
        corpus.run_refinement_round(
            root, corpus.corpus_pages([value]), None, "model", "client", "medium", state, 1, ["p1"]
        )
    initial_profile = corpus.paths_for(root, 1, "p1")["profile"]
    initial_profile.parent.mkdir(parents=True)
    write(initial_profile, {"page_id": "other", "role": "profile"})
    with pytest.raises(ValueError, match="does not match the refinement page"):
        corpus.run_refinement_round(
            root, corpus.corpus_pages([value]), None, "model", "client", "medium", state, 1, ["p1"]
        )


def test_retained_packet_and_assembly_resume_controls(tmp_path):
    packet_path = tmp_path / "packet.json"
    write(packet_path, {"page_id": "other"})
    with pytest.raises(ValueError, match="does not match"):
        corpus.retained_packet_or_run("p1", packet_path, lambda: {})
    targets = corpus.paths_for(tmp_path / "assembly", 1, "p1")
    targets["source_rows"].parent.mkdir(parents=True)
    for name in ("source_rows", "cards", "quality", "exceptions", "adapter"):
        write(targets[name], {})
    assert corpus.assemble_once(targets, None) is None
    targets["adapter"].unlink()
    with pytest.raises(ValueError, match="incomplete page assembly"):
        corpus.assemble_once(targets, None)


def test_refinement_resume_stability_and_cli_failure(monkeypatch, tmp_path):
    value = manifest(tmp_path, "first", "p1")
    root = tmp_path / "rounds"
    root.mkdir()
    write(root / "context.json", corpus.empty_context())
    state = corpus.initial_state([value], "model", "medium", 2, 0)
    state["context_snapshot"] = "context.json"
    state["active_refinement"] = {
        "round": 1,
        "candidate_page_ids": ["p1"],
        "completed_page_ids": ["p1"],
    }
    first_round, selected = corpus.run_refinement_round(
        root, corpus.corpus_pages([value]), None, "model", "client", "medium", state, 1, ["p1"]
    )
    assert selected[0][2] == "p1" and (first_round / "refinement_register.json").is_file()
    corpus.run_refinement_round(
        root, corpus.corpus_pages([value]), None, "model", "client", "medium", state, 1, ["p1"]
    )
    state["active_refinement"] = None
    state["refinement_rounds"] = [
        {"round": 1, "root": "refinements/round_01", "candidate_page_ids": ["p1"]}
    ]
    monkeypatch.setattr(corpus, "candidate_page_ids", lambda *args: ["p1"])
    assert (
        corpus.run_refinements(
            root, corpus.corpus_pages([value]), None, "model", "client", "medium", state
        )[0][0]
        == first_round
    )
    assert state["refinement_stop_reason"] == "candidate_set_within_tolerance"
    state["refinement_rounds"] = []
    monkeypatch.setattr(corpus, "candidate_page_ids", lambda *args: [])
    assert (
        corpus.run_refinements(
            root, corpus.corpus_pages([value]), None, "model", "client", "medium", state
        )
        == []
    )
    assert state["refinement_stop_reason"] == "no_unresolved_candidates"
    state["refinement_max_passes"] = 0
    assert (
        corpus.run_refinements(
            root, corpus.corpus_pages([value]), None, "model", "client", "medium", state
        )
        == []
    )
    state["refinement_max_passes"] = 1
    state["active_refinement"] = {"round": 1, "candidate_page_ids": [], "completed_page_ids": []}
    monkeypatch.setattr(corpus, "run_refinement_round", lambda *args: (root / "active", []))
    assert (
        corpus.run_refinements(
            root, corpus.corpus_pages([value]), None, "model", "client", "medium", state
        )[0][0]
        == root / "active"
    )
    resume_root = tmp_path / "resume"
    resume_root.mkdir()
    resume_state = corpus.initial_state([value], "model", "medium", 0, 0)
    resume_state["completed_page_ids"] = ["p1"]
    (resume_root / "context").mkdir()
    write(resume_root / "context" / "000000.json", corpus.empty_context())
    corpus.replace_state(resume_root / "corpus_state.json", resume_state)
    monkeypatch.setattr(corpus, "finalize", lambda *args: {"pages": 0})
    assert corpus.run_corpus(
        [value], resume_root, None, "model", "client", resume=True, refinement_passes=0
    ) == {"pages": 0}
    monkeypatch.setattr(corpus, "load_project_env", lambda: None)
    monkeypatch.setattr(
        corpus, "build_client", lambda *args: (_ for _ in ()).throw(ValueError("no key"))
    )
    monkeypatch.setattr(sys, "argv", ["table_comprehension_corpus.py", "m", "--out", "o"])
    with pytest.raises(SystemExit, match="Table-comprehension corpus failed"):
        corpus.main()
