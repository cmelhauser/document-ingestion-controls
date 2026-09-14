#!/usr/bin/env python3
"""Declare which retained artifact is the authoritative one for each lane.

A run that was restarted holds more than one artifact per lane. Extraction may
have been attempted five times, templates rebuilt twice, tables begun six times.
Every attempt is retained on purpose -- a stopped lane is evidence, and deleting
it would hide what happened -- but only one of them is the reading the run
stands behind.

Nothing in the run says which. `run_lane_coverage.py` scans the directory and
credits a lane from any artifact that matches, so a superseded attempt can make
a lane look covered when the artifact the operator actually relies on was never
written. That is a false positive in the one report whose job is to say what did
not run, and it is worse than the false negatives it was built to catch.

This command records the decision as an artifact. It never chooses: an operator
declares the authoritative path per lane and the reason each other attempt was
superseded, and this verifies the declaration against what the run contains.

`template` scaffolds a declaration from the run, listing every artifact that
currently credits each lane so none is silently forgotten. `verify` reports three
failures a hand-written declaration is prone to:

* a declared artifact that does not exist, so the lane it claims has nothing
  behind it;
* an undeclared artifact that would credit a lane on a directory scan, which is
  exactly the false positive this exists to prevent;
* a lane declared authoritative and superseded for the same path, which is a
  contradiction rather than a preference.

It changes nothing and clears no control. A declaration is an operator statement
about which reading the run stands behind, not an approval of that reading.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import run_lane_coverage
from cli_help import apply_shared_help
from runtime_config import load_project_env

ARTIFACT_TYPE = "run_generation_manifest_v1"


def require_new_file(path):
    """Refuse to replace a retained artifact."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load(path):
    """Read a generation manifest and return its lane declarations."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or data.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError(f"not a {ARTIFACT_TYPE} artifact: {path}")
    lanes = data.get("lanes")
    if not isinstance(lanes, dict) or not lanes:
        raise ValueError("a generation manifest must declare at least one lane")
    declared = {}
    for lane, entry in lanes.items():
        if not isinstance(entry, dict):
            raise ValueError(f"lane {lane!r} must map to an object")
        authoritative = entry.get("authoritative") or []
        superseded = entry.get("superseded") or []
        if not isinstance(authoritative, list) or not isinstance(superseded, list):
            raise ValueError(f"lane {lane!r} must list authoritative and superseded paths")
        declared[lane] = {
            "authoritative": [str(item) for item in authoritative],
            "superseded": [
                str(item.get("path")) if isinstance(item, dict) else str(item)
                for item in superseded
            ],
        }
    return declared


def crediting_artifacts(run_dir, lanes=None):
    """Return {lane: [relative paths]} for every artifact that credits a lane."""
    lanes = run_lane_coverage.LANES if lanes is None else lanes
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise ValueError(f"not a run directory: {run_dir}")
    files = run_lane_coverage.candidate_files(run_dir)
    found = {}
    for name, _script, markers, filenames in lanes:
        evidence = run_lane_coverage.lane_evidence(files, markers, filenames)
        found[name] = [path.relative_to(run_dir).as_posix() for path in evidence]
    return found


def template(run_dir, lanes=None):
    """Return a declaration scaffold listing what currently credits each lane."""
    credited = crediting_artifacts(run_dir, lanes)
    return {
        "artifact_type": ARTIFACT_TYPE,
        "generated_at": datetime.now(UTC).isoformat(),
        "run_directory": str(Path(run_dir)),
        "clears_no_control": True,
        "instructions": (
            "For each lane, move the artifact the run stands behind into "
            "`authoritative` and every other candidate into `superseded` with a "
            "reason. A candidate left in neither list is undeclared, and "
            "`verify` reports it: an artifact nobody has ruled on can still "
            "credit a lane on a directory scan."
        ),
        "lanes": {
            lane: {
                "authoritative": [],
                "superseded": [],
                "candidates": paths,
            }
            for lane, paths in credited.items()
            if paths
        },
    }


def verify(run_dir, manifest_path, lanes=None):
    """Return the declaration's failures against what the run actually contains."""
    declared = load(manifest_path)
    credited = crediting_artifacts(run_dir, lanes)
    known = set(credited)
    unknown_lanes = sorted(set(declared) - known)
    missing, undeclared, contradictions = [], [], []
    run_dir = Path(run_dir)
    for lane, entry in declared.items():
        authoritative = set(entry["authoritative"])
        superseded = set(entry["superseded"])
        for path in sorted(authoritative & superseded):
            contradictions.append({"lane": lane, "path": path})
        for path in sorted(authoritative):
            if not (run_dir / path).exists():
                missing.append({"lane": lane, "path": path})
        for path in sorted(set(credited.get(lane, ())) - authoritative - superseded):
            undeclared.append({"lane": lane, "path": path})
    for lane in sorted(known - set(declared)):
        for path in credited[lane]:
            undeclared.append({"lane": lane, "path": path})
    return {
        "schema_version": "1.0",
        "run_directory": str(run_dir),
        "manifest": str(manifest_path),
        "summary": {
            "lanes_declared": len(declared),
            "declared_artifacts_missing": len(missing),
            "undeclared_crediting_artifacts": len(undeclared),
            "contradictions": len(contradictions),
            "unknown_lanes": len(unknown_lanes),
        },
        "declared_artifacts_missing": missing,
        "undeclared_crediting_artifacts": undeclared,
        "contradictions": contradictions,
        "unknown_lanes": unknown_lanes,
        "interpretation": (
            "An undeclared artifact is not a defect in the run; it is a decision "
            "nobody has recorded. Until it is declared authoritative or "
            "superseded, a directory scan may credit its lane from it."
        ),
    }


def main(argv=None):
    """Scaffold or verify a run's generation declaration."""
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    scaffold = sub.add_parser("template", help="scaffold a declaration from the run")
    scaffold.add_argument("run_dir", type=Path, help="Run directory to scaffold from.")
    scaffold.add_argument("--out", required=True, type=Path, help="New declaration path.")
    scaffold.add_argument("--quiet", action="store_true")
    apply_shared_help(scaffold)

    check = sub.add_parser("verify", help="verify a declaration against the run")
    check.add_argument("run_dir", type=Path, help="Run directory the declaration describes.")
    check.add_argument("--manifest", required=True, type=Path, help="Declaration to verify.")
    check.add_argument("--out", type=Path, help="New JSON report path.")
    check.add_argument("--quiet", action="store_true")
    apply_shared_help(check)

    args = parser.parse_args(argv)
    try:
        if args.command == "template":
            result = template(args.run_dir)
            require_new_file(args.out).write_text(json.dumps(result, indent=2) + "\n")
        else:
            result = verify(args.run_dir, args.manifest)
            if args.out:
                require_new_file(args.out).write_text(json.dumps(result, indent=2) + "\n")
    except (OSError, ValueError, FileExistsError, json.JSONDecodeError) as exc:
        sys.exit(f"Run generation failed: {exc}")

    if not args.quiet:
        if args.command == "template":
            print(f"Lanes with retained artifacts: {len(result['lanes'])}")
            print(f"  scaffold written to {args.out}")
            print("  declare each candidate authoritative or superseded, then verify")
        else:
            summary = result["summary"]
            print(f"Lanes declared: {summary['lanes_declared']}")
            print(f"  declared artifacts missing   : {summary['declared_artifacts_missing']}")
            print(f"  undeclared crediting artifacts: {summary['undeclared_crediting_artifacts']}")
            print(f"  contradictions               : {summary['contradictions']}")
            for item in result["declared_artifacts_missing"]:
                print(f"  MISSING: {item['lane']} declares {item['path']}, which does not exist")
            for item in result["undeclared_crediting_artifacts"]:
                print(f"  UNDECLARED: {item['path']} would credit {item['lane']}")
            for item in result["contradictions"]:
                print(f"  CONTRADICTION: {item['path']} is both in {item['lane']}")
    if args.command == "verify" and (
        result["declared_artifacts_missing"] or result["contradictions"]
    ):
        sys.exit(
            "Run generation failed: the declaration does not hold against the run "
            "(missing declared artifacts or contradictions)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
