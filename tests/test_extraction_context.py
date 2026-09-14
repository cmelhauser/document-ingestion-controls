"""A corpus context conditions a second reading without becoming evidence.

Extraction reads one page at a time and remembers nothing between pages, so a
label printed across a whole corpus is rediscovered or missed page by page. This
lane states, once, what the run has already learned about its own documents. The
tests below hold the three properties that make that safe: only approved rules
reach the prompt, both lanes must be conditioned identically, and a corpus that
came back mostly in exception cannot quietly skip the update.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import consensus  # noqa: E402
import extraction_context as lane  # noqa: E402
import extraction_schema  # noqa: E402
import run_lane_coverage  # noqa: E402


def approved(label, field, status="client_approved"):
    """Build one registry rule."""
    return {
        "rule_type": "source_label_mapping",
        "status": status,
        "approval_authority": "client",
        "source_label": label,
        "canonical_field": field,
        "template_fingerprint": "fp-1",
    }


# --- the glossary carries only what someone approved -------------------------


def test_only_approved_rules_become_glossary_lines():
    registry = {
        "rules": [
            approved("ACK#", "acknowledgement_number"),
            approved("ACK NO", "acknowledgement_number"),
            {**approved("Guess", "total_amount"), "status": "proposed"},
        ]
    }
    assert lane.approved_label_glossary(registry) == {"acknowledgement_number": ["ACK NO", "ACK#"]}


def test_the_same_label_approved_per_template_is_collapsed_once():
    # Rules are keyed by template fingerprint, but the prompt carries no
    # fingerprint: the model cannot see which template it is reading.
    registry = {
        "rules": [
            approved("ACK#", "acknowledgement_number"),
            {**approved("ACK#", "acknowledgement_number"), "template_fingerprint": "fp-2"},
            "not-a-rule",
        ]
    }
    assert lane.approved_label_glossary(registry) == {"acknowledgement_number": ["ACK#"]}


def test_a_rule_that_is_not_a_source_label_mapping_is_ignored():
    registry = {"rules": [{**approved("x", "y"), "rule_type": "slot_equivalence"}]}
    assert lane.approved_label_glossary(registry) == {}


def test_families_are_counted_from_independently_agreed_classifications():
    accepted = {
        "accepted": [
            {"document_id": "a", "document_type": "commission_statement"},
            {"document_id": "b", "document_type": "commission_statement"},
            {"document_id": "c", "document_type": "receipt"},
            {"document_id": "d"},
            "not-a-record",
        ]
    }
    assert lane.accepted_families(accepted) == {"commission_statement": 2, "receipt": 1}
    assert lane.accepted_families(None) == {}


def test_a_corrected_classification_is_one_document_not_two():
    """Acceptances are appended, so a correction leaves the earlier one in the list.

    Counting entries reported four documents where there are three, and kept
    naming `commission_statement` as a family after the only document carrying
    it had been retyped.
    """
    accepted = {
        "accepted": [
            {"document_id": "a", "document_type": "commission_statement"},
            {"document_id": "b", "document_type": "receipt"},
            {"document_id": "c", "document_type": "receipt"},
            {
                "document_id": "a",
                "document_type": "payment_record",
                "supersedes_authorization_id": "authorization-round-1",
            },
        ]
    }
    assert lane.accepted_families(accepted) == {"receipt": 2, "payment_record": 1}


def test_rendered_context_states_that_the_page_wins_and_never_supplies_a_value():
    text = lane.render(
        {"acknowledgement_number": ["ACK#"]},
        {"commission_statement": 3},
        "Northgate & Co is a commission sales agency.",
    )
    assert "never to assign one" in text
    assert "Approval covers where a label belongs, never what it says." in text
    assert "'ACK#'" in text and "commission_statement: 3 pages" in text
    assert "Northgate & Co is a commission sales agency." in text


# --- the command ------------------------------------------------------------


def build(tmp_path, **kwargs):
    """Run the builder over a registry carrying one approved rule."""
    registry = kwargs.get("registry")
    if registry is None:
        registry = tmp_path / "registry.json"
        registry.write_text(json.dumps({"rules": [approved("ACK#", "acknowledgement_number")]}))
    argv = [
        "--registry",
        str(registry),
        "--out",
        str(tmp_path / kwargs.get("out", "context.txt")),
        "--provenance",
        str(tmp_path / kwargs.get("provenance", "provenance.json")),
        "--quiet",
    ]
    return lane.main(argv)


def test_build_writes_the_context_and_its_provenance(tmp_path, capsys):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"rules": [approved("ACK#", "acknowledgement_number")]}))
    classifications = tmp_path / "classes.json"
    classifications.write_text(
        json.dumps({"accepted": [{"document_id": "a", "document_type": "receipt"}]})
    )
    notes = tmp_path / "notes.md"
    notes.write_text("The identifier of record is the acknowledgement number.\n")

    assert (
        lane.main(
            [
                "--registry",
                str(registry),
                "--classifications",
                str(classifications),
                "--notes",
                str(notes),
                "--out",
                str(tmp_path / "context.txt"),
                "--provenance",
                str(tmp_path / "provenance.json"),
            ]
        )
        == 0
    )
    text = (tmp_path / "context.txt").read_text()
    assert "'ACK#'" in text and "receipt: 1 pages" in text
    record = json.loads((tmp_path / "provenance.json").read_text())
    assert record["artifact_type"] == lane.ARTIFACT_TYPE
    assert record["reasoning_only"] is True and record["clears_no_control"] is True
    assert record["summary"] == {
        "canonical_fields": 1,
        "approved_labels": 1,
        "document_families": 1,
        "operator_notes_bytes": len(notes.read_text().encode()),
    }
    assert "point LLM_CORPUS_CONTEXT at this file for BOTH" in capsys.readouterr().out


def test_a_context_with_no_source_is_refused(tmp_path):
    with pytest.raises(SystemExit) as exc:
        lane.main(["--out", str(tmp_path / "c.txt"), "--provenance", str(tmp_path / "p.json")])
    assert "at least one source" in str(exc.value)


def test_a_context_whose_sources_are_all_empty_is_refused(tmp_path):
    # Rule 9: writing it would let a run report a context-aware update it never took.
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"rules": []}))
    with pytest.raises(SystemExit) as exc:
        build(tmp_path, registry=registry)
    assert "nothing to condition a reading with" in str(exc.value)


def test_an_oversized_context_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "MAX_CORPUS_CONTEXT_BYTES", 10)
    with pytest.raises(SystemExit) as exc:
        build(tmp_path)
    assert "above the 10-byte prompt ceiling" in str(exc.value)


def test_an_existing_artifact_is_never_replaced(tmp_path):
    build(tmp_path)
    with pytest.raises(SystemExit) as exc:
        build(tmp_path)
    assert "refusing to overwrite" in str(exc.value)


def test_unreadable_input_fails_with_its_reason(tmp_path):
    with pytest.raises(SystemExit) as exc:
        build(tmp_path, registry=tmp_path / "absent.json")
    assert "Extraction context build failed" in str(exc.value)


# --- the prompt channel -----------------------------------------------------


def test_no_configured_context_leaves_the_prompt_unchanged(monkeypatch):
    monkeypatch.delenv("LLM_CORPUS_CONTEXT", raising=False)
    assert extraction_schema.corpus_context() is None
    assert "Operator corpus notes follow." not in extraction_schema.extraction_instructions()


def test_a_configured_context_is_appended_under_its_constraining_preamble(tmp_path, monkeypatch):
    path = tmp_path / "context.txt"
    path.write_text("- acknowledgement_number: 'ACK#'\n")
    monkeypatch.setenv("LLM_CORPUS_CONTEXT", str(path))
    context = extraction_schema.corpus_context()
    assert context["file"] == "context.txt" and context["bytes"] == len(path.read_bytes())

    prompt = extraction_schema.extraction_instructions()
    assert "- acknowledgement_number: 'ACK#'" in prompt
    assert "Where a note and the page disagree, the page wins." in prompt
    assert "Never emit a value these notes supply." in prompt


def test_the_family_hint_and_the_context_both_reach_the_prompt(tmp_path, monkeypatch):
    path = tmp_path / "context.txt"
    path.write_text("corpus notes\n")
    monkeypatch.setenv("LLM_CORPUS_CONTEXT", str(path))
    monkeypatch.setenv("LLM_DOCUMENT_FAMILY", "commission_statement")
    prompt = extraction_schema.extraction_instructions()
    assert "The operator selected document family 'commission_statement'" in prompt
    assert "corpus notes" in prompt


def test_an_empty_context_file_is_a_misconfiguration_not_a_decision(tmp_path, monkeypatch):
    path = tmp_path / "context.txt"
    path.write_text("   \n")
    monkeypatch.setenv("LLM_CORPUS_CONTEXT", str(path))
    with pytest.raises(ValueError, match="names an empty file"):
        extraction_schema.corpus_context()


def test_an_unreadable_context_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_CORPUS_CONTEXT", str(tmp_path / "absent.txt"))
    with pytest.raises(ValueError, match="unreadable"):
        extraction_schema.corpus_context()


def test_a_context_above_the_prompt_ceiling_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "context.txt"
    path.write_text("x" * (extraction_schema.MAX_CORPUS_CONTEXT_BYTES + 1))
    monkeypatch.setenv("LLM_CORPUS_CONTEXT", str(path))
    with pytest.raises(ValueError, match="above the"):
        extraction_schema.corpus_context()


def test_changing_the_context_changes_the_prompt_so_the_cache_cannot_replay(tmp_path, monkeypatch):
    path = tmp_path / "context.txt"
    path.write_text("first\n")
    monkeypatch.setenv("LLM_CORPUS_CONTEXT", str(path))
    before = extraction_schema.extraction_instructions()
    path.write_text("second\n")
    assert extraction_schema.extraction_instructions() != before


# --- both lanes must be conditioned identically -----------------------------


def handoff(tmp_path, name, context_sha):
    """Write a minimal handoff carrying only what the context guard reads."""
    path = tmp_path / name
    path.write_text(
        json.dumps({"corpus_context": None if context_sha is None else {"sha256": context_sha}})
    )
    return path


def test_the_shared_context_is_reported_from_whichever_lane_carries_it(tmp_path):
    a = handoff(tmp_path, "a.json", None)
    b = handoff(tmp_path, "b.json", "abc")
    assert consensus.handoff_corpus_context([a, b]) == {"sha256": "abc"}
    assert consensus.handoff_corpus_context([a]) is None


def test_a_corpus_over_the_threshold_with_no_context_demands_an_update():
    assert consensus.CONTEXT_UPDATE_THRESHOLD_PCT == 50.0


def test_run_lane_coverage_reads_the_demand_from_the_consensus_artifact(tmp_path):
    (tmp_path / "controls").mkdir()
    (tmp_path / "controls" / "consensus.json").write_text(
        json.dumps({"summary": {"context_aware_update_required": True}})
    )
    assert [p.name for p in run_lane_coverage.context_update_demanded(tmp_path)] == [
        "consensus.json"
    ]


def test_an_unreadable_or_satisfied_consensus_demands_nothing(tmp_path):
    (tmp_path / "controls").mkdir()
    (tmp_path / "controls" / "consensus.json").write_text(
        json.dumps({"summary": {"context_aware_update_required": False}})
    )
    (tmp_path / "controls" / "consensus_broken.json").write_text("{not json")
    assert run_lane_coverage.context_update_demanded(tmp_path) == []


def test_the_demand_fails_the_closing_gate_even_without_require(tmp_path):
    (tmp_path / "controls").mkdir()
    (tmp_path / "controls" / "consensus.json").write_text(
        json.dumps({"summary": {"context_aware_update_required": True}})
    )
    result = run_lane_coverage.report(tmp_path)
    assert "context-aware extraction" in result["required_lanes_missing"]
    assert result["context_aware_update_demanded_by"] == ["controls/consensus.json"]


def test_a_run_that_took_the_update_satisfies_the_demand(tmp_path):
    (tmp_path / "controls").mkdir()
    (tmp_path / "controls" / "consensus.json").write_text(
        json.dumps({"summary": {"context_aware_update_required": True}})
    )
    (tmp_path / "controls" / "context.json").write_text(
        json.dumps({"artifact_type": "extraction_corpus_context_v1"})
    )
    result = run_lane_coverage.report(tmp_path)
    assert result["required_lanes_missing"] == []


def test_the_context_aware_lane_is_catalogued():
    assert ("context-aware extraction", "extraction_context.py") in [
        (name, command) for name, command, _, _ in run_lane_coverage.LANES
    ]


def test_an_approved_rule_missing_its_field_or_label_contributes_nothing():
    # An approved rule that names no canonical field, or no printed label, cannot
    # tell the model where anything belongs.
    registry = {
        "rules": [
            approved("ACK#", None),
            approved("   ", "acknowledgement_number"),
            approved("ACK NO", "acknowledgement_number"),
        ]
    }
    assert lane.approved_label_glossary(registry) == {"acknowledgement_number": ["ACK NO"]}


def test_a_context_of_notes_alone_renders_without_a_glossary_or_families():
    text = lane.render({}, {}, "The client keeps no general ledger.")
    assert text == "Operator notes on this corpus:\nThe client keeps no general ledger.\n"


def test_a_context_of_families_alone_renders_without_notes():
    text = lane.render({}, {"receipt": 2}, "")
    assert "receipt: 2 pages" in text
    assert "Operator notes" not in text and "Printed labels" not in text
