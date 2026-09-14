"""Tests for weighing a single reading against an independent extractor."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

corroboration = importlib.import_module("independent_corroboration")


def extractor(path, pages):
    """An extractor artifact whose cells merge whole rows, as Document AI's do."""
    path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "page_id": page,
                        "source_tables": [{"source_rows": [{"cells": [{"evidence_text": text}]}]}],
                    }
                    for page, text in pages.items()
                ]
            }
        )
    )
    return path


def finding(document_id="p1", field="lines[0].sales_amount", candidates=("5,193.60",)):
    return {
        "document_id": document_id,
        "field": field,
        "flag": "single_engine",
        "candidates": list(candidates),
    }


def test_a_single_reading_the_independent_extractor_also_saw_is_corroborated(tmp_path):
    evidence = corroboration.page_evidence(
        [extractor(tmp_path / "e.json", {"p1": "12497 Ponte Verra 5,193.60 Total"})]
    )
    corroborations, ties, unresolved = corroboration.corroborate([finding()], evidence)
    assert not ties and not unresolved
    entry = corroborations[0]
    assert entry["evidence"] == "corroborated_by_independent_extractor"
    assert entry["match_scope"] == "page" and entry["match_mode"] == "substring"
    assert entry["occurrences"] == 1


def test_thousands_separators_and_currency_do_not_defeat_a_match(tmp_path):
    """Two readers of the same glyphs should not differ over a comma."""
    evidence = corroboration.page_evidence([extractor(tmp_path / "e.json", {"p1": "$5193.60"})])
    corroborations, _, _ = corroboration.corroborate([finding()], evidence)
    assert corroborations[0]["value"] == "5,193.60"


def test_occurrences_says_how_located_the_match_is(tmp_path):
    """A value seen twenty times says little about which row it belongs to."""
    evidence = corroboration.page_evidence(
        [extractor(tmp_path / "e.json", {"p1": "100.00 x 100.00 y 100.00"})]
    )
    corroborations, _, _ = corroboration.corroborate([finding(candidates=("100.00",))], evidence)
    assert corroborations[0]["occurrences"] == 3


def test_a_short_value_is_matched_as_a_token_not_inside_a_longer_number(tmp_path):
    """ "2" as a substring appears inside "1,234" and would corroborate anything."""
    evidence = corroboration.page_evidence(
        [extractor(tmp_path / "e.json", {"p1": "1,234 and 5,678"})]
    )
    _, _, unresolved = corroboration.corroborate([finding(candidates=("2",))], evidence)
    assert unresolved[0]["reason"] == "value_absent_from_independent_evidence"
    present = corroboration.page_evidence([extractor(tmp_path / "e2.json", {"p1": "qty 2 each"})])
    corroborations, _, _ = corroboration.corroborate([finding(candidates=("2",))], present)
    assert corroborations[0]["match_mode"] == "token"


def test_a_disagreement_is_broken_only_when_one_candidate_is_present(tmp_path):
    evidence = corroboration.page_evidence(
        [extractor(tmp_path / "e.json", {"p1": "2,451.84 printed here"})]
    )
    decided = finding(candidates=("2,451.84", "2,451.94"))
    _, ties, unresolved = corroboration.corroborate([decided], evidence)
    assert not unresolved
    assert ties[0]["supported_value"] == "2,451.84"
    assert ties[0]["candidates"] == ["2,451.84", "2,451.94"]

    both = corroboration.page_evidence(
        [extractor(tmp_path / "e2.json", {"p1": "2,451.84 and 2,451.94"})]
    )
    _, ties, unresolved = corroboration.corroborate([decided], both)
    assert not ties and unresolved[0]["reason"] == "both_candidates_present"

    neither = corroboration.page_evidence([extractor(tmp_path / "e3.json", {"p1": "nothing"})])
    _, ties, unresolved = corroboration.corroborate([decided], neither)
    assert not ties and unresolved[0]["reason"] == "neither_candidate_present"


def test_a_page_with_no_independent_evidence_is_an_exception_not_a_pass(tmp_path):
    evidence = corroboration.page_evidence([extractor(tmp_path / "e.json", {"other": "text"})])
    _, _, unresolved = corroboration.corroborate([finding()], evidence)
    assert unresolved[0]["reason"] == "no_independent_evidence_for_page"


def test_a_finding_with_no_candidate_value_cannot_be_weighed(tmp_path):
    evidence = corroboration.page_evidence([extractor(tmp_path / "e.json", {"p1": "text"})])
    _, _, unresolved = corroboration.corroborate([finding(candidates=())], evidence)
    assert unresolved[0]["reason"] == "no_candidate_value_to_weigh_against_evidence"
    _, _, three = corroboration.corroborate([finding(candidates=("a", "b", "c"))], evidence)
    assert three[0]["reason"] == "no_candidate_value_to_weigh_against_evidence"


def test_every_retained_extractor_artifact_contributes(tmp_path):
    """A recovery pass commonly covers pages the first pass did not."""
    first = extractor(tmp_path / "main.json", {"p1": "5,193.60"})
    second = extractor(tmp_path / "recovery.json", {"p2": "471.24"})
    evidence = corroboration.page_evidence([first, second])
    assert set(evidence) == {"p1", "p2"}
    corroborations, _, _ = corroboration.corroborate(
        [finding(), finding(document_id="p2", candidates=("471.24",))], evidence
    )
    assert len(corroborations) == 2


def test_the_artifact_states_what_it_is_not(tmp_path):
    evidence = corroboration.page_evidence([extractor(tmp_path / "e.json", {"p1": "5,193.60"})])
    result, unresolved = corroboration.build([finding()], evidence, ["e.json"], "openai/model-a")
    assert result["artifact_type"] == "independent_corroboration_v1"
    assert result["reference_engine"] == "openai/model-a"
    assert "not independent model vendor agreement" in result["limits"]
    assert "resolves nothing" in result["limits"]
    assert result["summary"]["corroborated"] == 1
    assert unresolved == []


def test_malformed_inputs_are_refused(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"records": {}}))
    with pytest.raises(ValueError, match="records"):
        corroboration.page_evidence([bad])
    findings = tmp_path / "f.json"
    findings.write_text(json.dumps({"summary": {}}))
    with pytest.raises(ValueError, match="exceptions list"):
        corroboration.load_findings(findings)
    # A record with no page identity cannot be matched to a finding.
    anon = tmp_path / "anon.json"
    anon.write_text(json.dumps([{"document_text": "text"}]))
    assert corroboration.page_evidence([anon]) == {}
    # A record whose evidence normalises away carries no page.
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps([{"page_id": "p1", "document_text": "   "}]))
    assert corroboration.page_evidence([empty]) == {}


def test_cli_writes_both_artifacts_and_refuses_empty_evidence(tmp_path, capsys):
    findings = tmp_path / "findings.json"
    findings.write_text(json.dumps({"exceptions": [finding()]}))
    evidence = extractor(tmp_path / "e.json", {"p1": "5,193.60"})
    out, exc = tmp_path / "corroboration.json", tmp_path / "exceptions.json"
    assert (
        corroboration.main(
            [str(findings), str(evidence), "--out", str(out), "--exceptions", str(exc)]
        )
        == 0
    )
    printed = capsys.readouterr().out
    assert "not vendor agreement and resolves nothing" in printed
    assert json.loads(out.read_text())["summary"]["corroborated"] == 1
    assert json.loads(exc.read_text())["summary"]["count"] == 0

    # A run with unresolved findings prints why, reason by reason.
    unmatched = tmp_path / "unmatched.json"
    unmatched.write_text(json.dumps({"exceptions": [finding(candidates=("absent-value",))]}))
    u_out, u_exc = tmp_path / "u.json", tmp_path / "ue.json"
    assert (
        corroboration.main(
            [str(unmatched), str(evidence), "--out", str(u_out), "--exceptions", str(u_exc)]
        )
        == 0
    )
    assert "value_absent_from_independent_evidence: 1" in capsys.readouterr().out

    quiet_out, quiet_exc = tmp_path / "q.json", tmp_path / "qe.json"
    assert (
        corroboration.main(
            [
                str(findings),
                str(evidence),
                "--out",
                str(quiet_out),
                "--exceptions",
                str(quiet_exc),
                "--quiet",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == ""

    blank = extractor(tmp_path / "blank.json", {"p1": "  "})
    with pytest.raises(SystemExit, match="processed nothing"):
        corroboration.main(
            [
                str(findings),
                str(blank),
                "--out",
                str(tmp_path / "x.json"),
                "--exceptions",
                str(tmp_path / "y.json"),
            ]
        )


def test_normalize_and_presence_edges(tmp_path):
    assert corroboration.normalize(None) == ""
    assert corroboration.normalize("  $1,750.97 ") == "1750.97"
    evidence = corroboration.page_evidence([extractor(tmp_path / "e.json", {"p1": "abc"})])
    assert corroboration.presence(None, evidence["p1"]) == (None, 0)
    assert corroboration.presence("   ", evidence["p1"]) == (None, 0)
