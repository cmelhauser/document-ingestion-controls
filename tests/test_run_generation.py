"""Hold a run's generation declaration to what the run actually contains.

A restarted run keeps every attempt on purpose, and `run_lane_coverage.py`
credits a lane from any artifact that matches. So an attempt the operator
discarded can make a lane read as covered while the artifact the run stands
behind was never written -- a false positive in the one report whose job is to
say what did not run. These tests are written from that failure.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

generation = importlib.import_module("run_generation")
coverage = importlib.import_module("run_lane_coverage")

LANES = (
    ("named file", "a.py", (), ("consensus*.json",)),
    ("distinctive key", "b.py", ("grouping_method",), ()),
)


def run_dir(tmp_path, files):
    """Write a run directory containing the given relative files."""
    for name, payload in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return tmp_path


def manifest(tmp_path, lanes, name="generation.json"):
    """Write a generation manifest and return its path."""
    path = tmp_path / name
    path.write_text(
        json.dumps({"artifact_type": generation.ARTIFACT_TYPE, "lanes": lanes}, indent=2)
    )
    return path


def test_a_superseded_attempt_no_longer_credits_its_lane(tmp_path):
    """The whole point: a discarded attempt must not make a lane look covered."""
    root = run_dir(tmp_path, {"controls/consensus.json": {"documents": []}})
    without = coverage.coverage(root, LANES)
    assert [item for item in without if item["lane"] == "named file"][0]["ran"]

    declared = {"named file": ["controls/consensus_02.json"]}
    with_declaration = coverage.coverage(root, LANES, declared)
    assert not [item for item in with_declaration if item["lane"] == "named file"][0]["ran"]


def test_the_authoritative_attempt_still_credits_its_lane(tmp_path):
    """Restricting evidence must not stop the declared artifact counting."""
    root = run_dir(
        tmp_path,
        {
            "controls/consensus.json": {"documents": []},
            "controls/consensus_02.json": {"documents": []},
        },
    )
    declared = {"named file": ["controls/consensus_02.json"]}
    result = coverage.coverage(root, LANES, declared)
    entry = [item for item in result if item["lane"] == "named file"][0]
    assert entry["ran"]
    assert entry["evidence"] == ["controls/consensus_02.json"]


def test_an_undeclared_lane_is_unrestricted(tmp_path):
    """A declaration covering one lane must not silently blank the others."""
    root = run_dir(tmp_path, {"controls/x.json": {"grouping_method": "root_cause"}})
    result = coverage.coverage(root, LANES, {"named file": []})
    assert [item for item in result if item["lane"] == "distinctive key"][0]["ran"]


def test_verify_names_a_declared_artifact_that_does_not_exist(tmp_path):
    """A lane declaring a path that was never written has nothing behind it."""
    root = run_dir(tmp_path, {"controls/consensus.json": {"documents": []}})
    path = manifest(
        root,
        {
            "named file": {
                "authoritative": ["controls/consensus_02.json"],
                "superseded": [{"path": "controls/consensus.json", "reason": "superseded"}],
            }
        },
    )
    result = generation.verify(root, path, LANES)
    assert result["summary"]["declared_artifacts_missing"] == 1
    assert result["declared_artifacts_missing"][0]["path"] == "controls/consensus_02.json"


def test_verify_names_an_undeclared_artifact_that_would_credit_a_lane(tmp_path):
    """An artifact nobody ruled on is the false positive this exists to prevent."""
    root = run_dir(
        tmp_path,
        {
            "controls/consensus.json": {"documents": []},
            "controls/consensus_02.json": {"documents": []},
        },
    )
    path = manifest(root, {"named file": {"authoritative": ["controls/consensus_02.json"]}})
    result = generation.verify(root, path, LANES)
    undeclared = {item["path"] for item in result["undeclared_crediting_artifacts"]}
    assert "controls/consensus.json" in undeclared


def test_verify_names_a_lane_declared_both_ways(tmp_path):
    """Authoritative and superseded for one path is a contradiction, not a preference."""
    root = run_dir(tmp_path, {"controls/consensus.json": {"documents": []}})
    path = manifest(
        root,
        {
            "named file": {
                "authoritative": ["controls/consensus.json"],
                "superseded": [{"path": "controls/consensus.json", "reason": "both"}],
            }
        },
    )
    result = generation.verify(root, path, LANES)
    assert result["summary"]["contradictions"] == 1


def test_verify_names_a_lane_the_catalogue_does_not_have(tmp_path):
    """A declaration for a lane that does not exist is a stale declaration."""
    root = run_dir(tmp_path, {"controls/consensus.json": {"documents": []}})
    path = manifest(root, {"invented lane": {"authoritative": []}})
    result = generation.verify(root, path, LANES)
    assert result["unknown_lanes"] == ["invented lane"]


def test_a_clean_declaration_reports_nothing_to_fix(tmp_path):
    """Absence of findings is a result, so it must be reachable."""
    root = run_dir(
        tmp_path,
        {
            "controls/consensus.json": {"documents": []},
            "controls/consensus_02.json": {"documents": []},
        },
    )
    path = manifest(
        root,
        {
            "named file": {
                "authoritative": ["controls/consensus_02.json"],
                "superseded": [{"path": "controls/consensus.json", "reason": "restarted"}],
            }
        },
    )
    result = generation.verify(root, path, LANES)
    assert result["summary"]["declared_artifacts_missing"] == 0
    assert result["summary"]["contradictions"] == 0
    assert result["summary"]["undeclared_crediting_artifacts"] == 0


def test_a_superseded_entry_may_be_a_bare_path(tmp_path):
    """Operators write both shapes; a bare string must not be read as a reason-less object."""
    root = run_dir(tmp_path, {"controls/consensus.json": {"documents": []}})
    path = manifest(
        root,
        {
            "named file": {
                "authoritative": [],
                "superseded": ["controls/consensus.json"],
            }
        },
    )
    result = generation.verify(root, path, LANES)
    assert result["summary"]["undeclared_crediting_artifacts"] == 0


@pytest.mark.parametrize(
    "payload,message",
    [
        ({"artifact_type": "something_else", "lanes": {}}, "not a run_generation_manifest_v1"),
        ({"artifact_type": generation.ARTIFACT_TYPE}, "at least one lane"),
        ({"artifact_type": generation.ARTIFACT_TYPE, "lanes": {"a": []}}, "must map to an object"),
        (
            {"artifact_type": generation.ARTIFACT_TYPE, "lanes": {"a": {"authoritative": "x"}}},
            "must list authoritative and superseded paths",
        ),
    ],
)
def test_a_malformed_declaration_is_refused_rather_than_half_read(tmp_path, payload, message):
    """A partly-read declaration would restrict evidence on a guess."""
    path = tmp_path / "generation.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match=message):
        generation.load(path)


def test_template_lists_every_candidate_so_none_is_forgotten(tmp_path):
    """A scaffold that omitted a candidate would hide the decision it exists to force."""
    root = run_dir(
        tmp_path,
        {
            "controls/consensus.json": {"documents": []},
            "controls/consensus_02.json": {"documents": []},
        },
    )
    result = generation.template(root, LANES)
    assert result["artifact_type"] == generation.ARTIFACT_TYPE
    assert set(result["lanes"]["named file"]["candidates"]) == {
        "controls/consensus.json",
        "controls/consensus_02.json",
    }
    assert "distinctive key" not in result["lanes"]


def test_template_refuses_to_overwrite_a_retained_declaration(tmp_path, capsys):
    """A declaration is an operator statement; replacing it silently loses one."""
    root = run_dir(tmp_path, {"controls/consensus.json": {"documents": []}})
    out = root / "generation.json"
    out.write_text("{}")
    with pytest.raises(SystemExit) as excinfo:
        generation.main(["template", str(root), "--out", str(out)])
    assert "refusing to overwrite" in str(excinfo.value)


def test_template_writes_a_scaffold_and_says_what_to_do_next(tmp_path, capsys):
    root = run_dir(tmp_path, {"controls/consensus.json": {"documents": []}})
    out = root / "generation.json"
    assert generation.main(["template", str(root), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "declare each candidate" in printed
    assert json.loads(out.read_text())["artifact_type"] == generation.ARTIFACT_TYPE


def test_verify_exits_non_zero_when_the_declaration_does_not_hold(tmp_path, capsys):
    """A declaration that fails must fail the command, not merely be reported."""
    root = run_dir(tmp_path, {"controls/consensus.json": {"documents": []}})
    path = manifest(root, {"named file": {"authoritative": ["controls/gone.json"]}})
    with pytest.raises(SystemExit) as excinfo:
        generation.main(["verify", str(root), "--manifest", str(path)])
    assert "does not hold against the run" in str(excinfo.value)
    assert "MISSING" in capsys.readouterr().out


def test_verify_reports_a_holding_declaration_and_can_write_it(tmp_path, capsys):
    root = run_dir(tmp_path, {"controls/consensus_02.json": {"documents": []}})
    path = manifest(root, {"named file": {"authoritative": ["controls/consensus_02.json"]}})
    out = root / "verify.json"
    assert generation.main(["verify", str(root), "--manifest", str(path), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["summary"]["declared_artifacts_missing"] == 0
    assert "Lanes declared: 1" in capsys.readouterr().out


def test_quiet_suppresses_the_summary_only(tmp_path, capsys):
    root = run_dir(tmp_path, {"controls/consensus_02.json": {"documents": []}})
    path = manifest(root, {"named file": {"authoritative": ["controls/consensus_02.json"]}})
    assert generation.main(["verify", str(root), "--manifest", str(path), "--quiet"]) == 0
    assert capsys.readouterr().out == ""


def test_verify_prints_a_contradiction_and_an_undeclared_artifact(tmp_path, capsys):
    """Both findings must reach the operator's terminal, not only the JSON."""
    root = run_dir(
        tmp_path,
        {
            "controls/consensus.json": {"documents": []},
            "controls/consensus_02.json": {"documents": []},
        },
    )
    path = manifest(
        root,
        {
            "named file": {
                "authoritative": ["controls/consensus_02.json"],
                "superseded": [{"path": "controls/consensus_02.json", "reason": "both"}],
            }
        },
    )
    with pytest.raises(SystemExit):
        generation.main(["verify", str(root), "--manifest", str(path)])
    printed = capsys.readouterr().out
    assert "CONTRADICTION" in printed
    assert "UNDECLARED" in printed


def test_a_run_directory_that_is_not_one_is_refused(tmp_path):
    missing = tmp_path / "absent"
    with pytest.raises(ValueError, match="not a run directory"):
        generation.crediting_artifacts(missing, LANES)


def test_coverage_reports_the_declared_lanes_it_restricted(tmp_path):
    """A report that restricted evidence must say so, or a reader cannot tell."""
    root = run_dir(tmp_path, {"controls/consensus_02.json": {"documents": []}})
    result = coverage.report(root, declared={"named file": ["controls/consensus_02.json"]})
    assert result["generation_declared_lanes"] == ["named file"]


def test_coverage_without_a_declaration_lists_none(tmp_path):
    root = run_dir(tmp_path, {"controls/consensus_02.json": {"documents": []}})
    assert coverage.report(root)["generation_declared_lanes"] == []


def test_the_coverage_command_restricts_evidence_to_the_declaration(tmp_path, capsys):
    """The flag must work through the CLI, not only the function beneath it.

    A superseded attempt sits in the run directory, the declared artifact does
    not, and the lane must therefore report as having produced nothing.
    """
    root = run_dir(tmp_path, {"controls/consensus.json": {"documents": []}})
    path = manifest(root, {"consensus": {"authoritative": ["controls/consensus_02.json"]}})
    out = root / "coverage.json"
    assert coverage.main([str(root), "--generation", str(path), "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["generation_declared_lanes"] == ["consensus"]
    entry = [item for item in report["lanes"] if item["lane"] == "consensus"][0]
    assert not entry["ran"]
    assert "no retained output: consensus" in capsys.readouterr().out
