#!/usr/bin/env python3
"""Report which pipeline lanes a run actually executed.

Rule 9 says a control that processed nothing has not passed. Nothing enforced
the same rule one level up: a lane nobody invoked leaves no artifact, no
exception, and no trace, so a run reaches delivery looking complete while whole
controls were never run at all.

That is not hypothetical. A full trial reached CRM staging with the source-table
lane, both mapping lanes, template drift, every relationship lane, sampling, and
analytics never invoked -- fourteen lane families -- and no command anywhere said
so, because each was individually optional and none of them ran to report it.

This command scans a run directory for the artifacts each lane leaves behind and
names the lanes that left none. It counts everything under that directory, so a
run directory must hold only that run's evidence: synthetic inputs, smoke tests,
and scratch outputs left inside it are counted as lanes that ran, and the report
then overstates the run. Keep them somewhere else. It reads only retained files, contacts no
provider, and changes nothing. It is a completeness report, not a gate on the
evidence: a lane may be deliberately skipped, and saying so out loud is the
point. ``--require`` turns a named lane into a failure when it produced nothing,
so a run profile that must include a lane can be enforced.

The catalogue below is checked against the live scripts by
``agent_surface_check.py``, so a lane added to the repository and forgotten here
fails the release gate rather than silently going unmeasured.
"""

import argparse
import fnmatch
import json
import sys
from pathlib import Path

from cli_help import apply_shared_help
from runtime_config import load_project_env

# One entry per lane an operator can run. ``markers`` are matched against an
# artifact's **top-level keys** -- see ``top_level_keys``, which also folds in the
# `artifact_type` value -- not against a substring of its text. Writing a marker
# that reads like a substring is the way to get a false negative here: the
# applied-corroboration lane shipped with the marker `applied_corroboration`,
# ran on a real corpus, and was reported as never having run, because the key it
# writes is `applied_corroboration_v1`. Check a marker against a real artifact.
# ``filenames`` match the file name itself and may use shell-style wildcards,
# because a lane that chunks its work names each manifest for its chunk. A lane counts as run when any retained
# file in the run directory matches. Optional lanes are reported, never failed,
# unless --require names one.
LANES = (
    # The documented and actual output is `profile.json`, a filename shared by
    # table comprehension.  Its distinctive summary key avoids both the prior
    # invented `scan_profile.json` false negative and a table-profile false
    # positive.
    ("scan profiling", "scan_profile.py", ("total_pages_profiled",), ()),
    ("intake", "ingest_pages.py", (), ("ingestion_manifest.json",)),
    ("reassembly", "reassemble_pages.py", (), ("reassembly.json",)),
    # Chunked, per-page, and recovery passes each name their own manifest
    # (`variants_chunk_01_manifest.json`, `variants_recovery_01_manifest.json`),
    # so an exact match reported a lane that had run as missing. No marker is
    # available: the manifest's `dpi` key is shared with the scan profile.
    ("image variants", "preprocess_pages.py", (), ("variants*manifest.json",)),
    # Matched on the key only operations.py writes, not on the filename:
    # `manifest.json` is a name several lanes use, and the inferred-controls
    # manifest was standing in for a run manifest that may never have been built.
    ("run manifest", "operations.py", ("manifest_schema_version",), ()),
    ("template drift", "template_drift.py", ("fingerprint_algorithm",), ()),
    ("detector calibration", "detection_calibration.py", ("recommended_threshold",), ()),
    ("primary extraction", "llm_adapter.py", (), ("primary.json",)),
    ("secondary extraction", "llm_adapter.py", (), ("secondary.json",)),
    ("source-native tables", "table_comprehension.py", (), ("profile.json", "source_rows.json")),
    ("corpus tables", "table_comprehension_corpus.py", (), ("corpus_state.json",)),
    ("display layout", "layout_aware_extract.py", ("layout_columns",), ()),
    ("independent OCR", "google_document_ai_adapter.py", ("processor_configuration",), ()),
    ("table reconciliation", "independent_table_reconcile.py", ("reconciliations",), ()),
    (
        "classification consensus",
        "classification_consensus.py",
        ("classification_consensus_v1",),
        (),
    ),
    # Shares the consensus artifact type: an amendment appends to that artifact
    # rather than emitting one of its own, so it is credited by filename.
    (
        "classification amendment",
        "classification_amend.py",
        (),
        ("classification_consensus_0*.json",),
    ),
    (
        "context-aware extraction",
        "extraction_context.py",
        ("extraction_corpus_context_v1",),
        (),
    ),
    (
        "source templates",
        "template_observations.py",
        ("source_template_observations_v1",),
        (),
    ),
    ("semantic mappings", "schema_discovery.py", ("mapping_proposals",), ()),
    # A discovered mapping that is approved but never applied changes nothing:
    # attribution, completeness, and the inferred controls read document records,
    # not the registry. A run that skips this lane is named rather than assumed.
    (
        "applied mappings",
        "apply_mappings.py",
        ("applied_source_label_mappings_v1",),
        (),
    ),
    ("slot equivalence", "schema_discovery.py", ("slot_proposals",), ("slot_equivalence*.json",)),
    ("consensus", "consensus.py", (), ("consensus.json",)),
    # Optional, and only meaningful once an independent non-LLM extractor has
    # run: it weighs consensus findings against that extractor's own reading
    # of the same page. It resolves nothing, so a run may finish without it,
    # but a run that has the evidence and never consults it has left the
    # strongest deterministic reduction on the table.
    (
        "independent corroboration",
        "independent_corroboration.py",
        ("corroborated",),
        (),
    ),
    # Applying that corroboration is a separate authorized step, and it is the
    # only lane in the table that accepts a value on weaker-than-consensus
    # evidence. It runs only under a named client decision.
    (
        "applied corroboration",
        "apply_corroboration.py",
        ("applied_corroboration_v1", "acceptances_available"),
        (),
    ),
    ("arithmetic", "arithmetic_check.py", (), ("arithmetic.json",)),
    # `--out` is operator-chosen and this run wrote `adjudication.json`, so the
    # documented default name alone missed a lane that had run. The summary key
    # is written by this lane only.
    ("amendments", "adjudicate.py", ("proposed_amendments",), ("amendments.json",)),
    # Settles cells two vendors disagreed on, using the line's own arithmetic.
    # A run with no `no_consensus` cells has nothing for it to do, but a run
    # that has them and never ran it sent a client questions the document could
    # answer -- so it is catalogued rather than exempt.
    # Counts readings the run already holds. A run with several provider
    # handoffs that never ran it left evidence it had paid for unread.
    (
        "multi-engine vote",
        "multi_engine_vote.py",
        # `resolved` is a key the arithmetic reconciler writes too, and a marker
        # two lanes share credits either for the other's artifact. This one is
        # the vote's own.
        ("by_agreement",),
        ("multi_engine_vote*.json", "mev_*.json"),
    ),
    (
        "arithmetic reconciliation",
        "arithmetic_reconcile.py",
        ("resolved",),
        ("arithmetic_reconciliation*.json",),
    ),
    (
        "audit-only amendments",
        "llm_adjudication.py",
        ("llm_generated_amendment_proposals",),
        (),
    ),
    ("validation", "validate_extraction.py", (), ("validation.json",)),
    ("addresses", "address_normalize.py", (), ("addresses.json",)),
    ("entities", "entity_resolve.py", (), ("entities.json",)),
    # `--out` is operator-chosen, so each lane is recognised by what its artifact
    # names: brand recovery and the accuracy sample by the artifact type each
    # stamps in its summary, specifier recovery -- which stamps none -- by a
    # summary key only it writes. Catalogued as `brand_recovery`, a prefix of its
    # type, as `accuracy_sample`, and as the `specifier_recovery` key each
    # document carries, all three ran on the commission run and were reported as
    # never run.
    ("specifier recovery", "specifier_recover.py", ("with_a_specifier",), ()),
    ("brand recovery", "brand_recover.py", ("brand_recovery_v1",), ()),
    ("accuracy sample", "accuracy_sample.py", ("accuracy_sample_v1",), ()),
    ("page review", "page_review.py", ("pages_reviewed",), ()),
    ("export augmentation", "augment_export.py", ("cells_inferred",), ()),
    ("page review amendment", "page_review_amend.py", ("amendments_applied",), ()),
    # Its artifact type is shared with the operator's own public-source file, so
    # it is recognised by a summary key only this lane writes.
    ("party locations", "party_locations.py", ("not_asked_over_the_limit",), ()),
    # Runs after the export, against the schema the export loads into. Its
    # `--out` is operator-chosen, so it is recognised by its artifact type.
    ("CRM input validation", "crm_input_validate.py", ("crm_input_validation_v1",), ()),
    (
        "handwriting OCR",
        "google_handwriting_ocr.py",
        ("google_cloud_vision_handwriting_htr_v1",),
        (),
    ),
    ("handwriting reconciliation", "handwriting_review.py", (), ("htr_decisions.json",)),
    # As for amendments: `--out` is operator-chosen and the documented example
    # writes `attributed.json`. `attribution_by_method` is unique to this lane.
    ("attribution", "attribution.py", ("attribution_by_method",), ("attribution.json",)),
    ("allocation policy", "allocation_policy.py", (), ("allocation_discovery.json",)),
    ("inferred controls", "inferred_controls.py", ("inferred_attribution_proposal",), ()),
    ("completeness", "completeness.py", (), ("completeness*.json",)),
    ("sampling", "sampling.py", (), ("sampling*.json",)),
    ("golden set", "golden_set_evaluate.py", ("truth_metadata",), ()),
    # `--out` is operator-chosen and a rerun iterates it (`final_queue_02.json`),
    # so the documented default name alone reported the gate as never run on a run
    # that had built it twice.
    ("final review queue", "final_review_queue.py", (), ("final_queue*.json",)),
    (
        "simulated client comments",
        "ai_simulated_client_review.py",
        ("ai_simulated_client_review_v1",),
        (),
    ),
    ("evidence graph", "evidence_graph.py", ("evidence_graph_v1",), ()),
    ("client context", "client_input_comments.py", ("client_review_context_v1",), ()),
    (
        "reference discovery",
        "client_review_inference.py",
        ("client_review_reference_discovery_v1",),
        (),
    ),
    (
        "iterative relationships",
        "client_review_iterative.py",
        ("client_review_iterative_primary_v1",),
        (),
    ),
    (
        "cross-packet verification",
        "client_review_cross_packet.py",
        ("client_review_cross_packet_v1",),
        (),
    ),
    (
        "cross-record search",
        "client_review_cross_record.py",
        (),
        ("cross_record*.json",),
    ),
    (
        "exception resolution",
        "client_review_exception_resolution.py",
        ("client_review_exception_resolution_v1",),
        (),
    ),
    # `--out` is operator-chosen and this run wrote `card_review_02.json`, so
    # the documented filename alone reported a lane that had plainly run as
    # never run. `auto_accept_policy` is written by this lane and no other.
    (
        "card review",
        "client_review_llm.py",
        ("auto_accept_policy",),
        ("client_review_llm.json",),
    ),
    ("full-dataset agent", "review_agent.py", (), ("review_agent.json",)),
    ("client review pack", "client_review_lane.py", ("client_review_pack_v1",), ()),
    ("root-cause grouping", "review_grouping.py", ("grouping_method",), ()),
    ("safe consolidation", "safe_review_consolidation.py", (), ("safe_review_consolidation.json",)),
    ("client package", "client_review_package.py", (), ("client_review_package.xlsx",)),
    ("decision compile", "client_decision_compile.py", ("deferred_decisions",), ()),
    ("approval projection", "simulate_client_approval.py", (), ("approval_projection.json",)),
    ("analytics", "analytics.py", (), ("analytics.json",)),
    # Credited by its report rather than by the record artifact it writes: the
    # resolved records keep the consensus shape so canonical_export can read
    # them, and the report is the only artifact this lane alone produces.
    (
        "exception resolution",
        "exception_resolve.py",
        ("exception_resolution_report_v1",),
        (),
    ),
    ("canonical export", "canonical_export.py", ("canonical_export_v1",), ()),
    ("canonical DDL", "canonical_deploy.py", (), ("deploy_plan.json",)),
    ("canonical load plan", "canonical_load.py", (), ("load_plan.json",)),
    ("target staging", "csv_api_staging.py", (), ("staging_manifest.json",)),
    ("common CRM import package", "crm_import_package.py", ("crm_import_package_v1",), ()),
    ("retrieval store", "retrieval_store.py", (), ("facts.sqlite",)),
    ("delivery folder", "client_delivery_package.py", ("client_delivery_package_v1",), ()),
    # Same: a second usage report writes `usage_02.json`, and the marker is a
    # filename string rather than a key this artifact carries.
    ("usage report", "llm_usage_report.py", ("how_to_price",), ("usage*.json",)),
    # Declares which retained artifact each lane stands behind. Catalogued so the
    # command that guards against a discarded attempt crediting a lane is itself
    # visible to the report it guards.
    ("generation manifest", "run_generation.py", ("run_generation_manifest_v1",), ()),
    # Appended rather than placed beside the page review amendment, so no lane's
    # number in `tasks/lane-checklist.md` moves.
    ("column re-file", "column_refile.py", ("readings_refiled",), ()),
    # Appended for the same reason: no lane's checklist number moves.
    ("records in step", "records_in_step.py", ("cells_brought_into_step",), ()),
)
# Reading every retained byte of a large run to look for a marker is wasteful and
# slow; a lane's identity always appears near the top of its artifact.
MARKER_SCAN_BYTES = 200_000
# An OCR artifact is legitimately large: a real 18-page corpus produced a 13 MB
# Document AI handoff, and the previous 8 MB ceiling made this report claim that
# lane had never run when it had just succeeded on all 18 pages. Silently
# under-reporting is the worst failure available to a tool whose whole job is
# saying honestly what ran, so the ceiling is well clear of realistic artifacts
# and anything past it is reported rather than assumed empty.
UNSCANNABLE_BYTES = 512_000_000
# Files this run could not parse. Reported, never silently treated as empty.
UNSCANNED = set()


# Commands that are not pipeline lanes: repository maintenance, credential
# refresh, live monitoring, and the retrieval servers. A run does not "miss" these, so
# they are named here rather than left to look like an uncatalogued lane.
NON_LANE_COMMANDS = frozenset(
    {
        "agent_surface_check.py",
        "branch_state.py",
        "mutation_check.py",
        # A planning report read from artifacts a run already produced. It runs
        # before or between lanes and produces a proposal, so a run does not
        # "miss" it the way it can miss a control.
        "pipeline_plan.py",
        "release_check.py",
        "reauthorize_google.py",
        "run_lane_coverage.py",
        "run_monitor.py",
        "run_sentinel.py",
        "run_workspace.py",
        "retrieval_https.py",
        "retrieval_remote_mcp.py",
        "retrieval_mcp.py",
        "retrieval_sidecar.py",
        "visual_ingestion_export.py",  # Optional pre-pipeline journal maintenance, not a run lane.
        "openai_adapter.py",
        "openrouter_adapter.py",
        "google_genai_adapter.py",
        "layout_dedup.py",
        "extraction_schema.py",
        # A measurement taken before a corpus is committed to a new engine, not
        # a lane a run must account for: it clears no control and its artifact
        # is evidence for a decision rather than an input to one.
        "engine_agreement.py",
        # A read of artifacts a run already produced, for the same reason: it
        # writes every extracted field with its status so an operator can see
        # the run's own evidence, clears no control, and nothing downstream
        # consumes it. A run that never calls it has not missed a control.
        "field_extract_export.py",
    }
)


def uncatalogued_commands(root=None):
    """Return every operator command absent from the lane catalogue.

    The catalogue is hand-written, and a hand-written list is the thing that
    silently stops matching the code. This is checked in the release gate, so a
    new lane cannot be added without deciding whether a run must account for it.
    """
    scripts = Path(root) if root else Path(__file__).resolve().parent
    catalogued = {command for _, command, _, _ in LANES} | NON_LANE_COMMANDS
    return {
        path.name
        for path in scripts.glob("*.py")
        if path.name not in catalogued
        and "argparse.ArgumentParser(" in path.read_text(encoding="utf-8")
    }


def is_raw(relative):
    """Return whether a path sits under a retained raw-response directory."""
    # Directory parts only. The guard is about raw-response directories, and
    # testing the file name too would exclude a retained artifact that merely
    # happens to be called `raw_something.json`.
    return any(part.startswith("raw") or part.endswith("_raw") for part in relative.parts[:-1])


def candidate_files(run_dir):
    """Return every retained file in the run, excluding raw provider responses.

    A raw response is the provider's own words. It names the lane that asked for
    it, so scanning raw directories would report a lane as run on the strength of
    a request that failed.

    A run names those directories both ways. `providers/raw/` and `htr/raw_01/`
    start with the word; `review_agent_raw/` and
    `client_review_cross_record_raw/` end with it, and the prefix test alone let
    558 provider responses through on a real 52 GB run. Both forms are excluded.
    """
    return [
        path
        for path in sorted(Path(run_dir).rglob("*"))
        if path.is_file() and not is_raw(path.relative_to(run_dir))
    ]


def top_level_keys(path):
    """Return an artifact's own top-level keys, or nothing when it has none.

    Matching a substring of the file text reported a lane as run because an
    unrelated artifact happened to contain the word. A producer is identified by
    the keys it writes at the root of its own artifact, which no other lane
    emits.
    """
    try:
        if path.stat().st_size > UNSCANNABLE_BYTES:
            # Not the same answer as "this artifact has no keys". A file too
            # large to parse is recorded so the report can say so, because the
            # alternative is claiming a lane never ran when it may well have.
            UNSCANNED.add(path)
            return set()
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, ValueError):
        return set()
    # Some producers write a JSON array of records rather than an envelope, and
    # a key scan that only understood objects reported those lanes as never run.
    if isinstance(data, list):
        data = next((item for item in data if isinstance(item, dict)), None)
    if not isinstance(data, dict):
        return set()
    keys = set(data)
    # Producers in this repository name themselves in one of two fields, and
    # which one is historical rather than meaningful.
    for field in ("artifact_type", "schema_version"):
        identity = data.get(field)
        if isinstance(identity, str):
            keys.add(identity)
    # A producer whose top-level keys are all generic often names itself inside
    # its summary. `amendment_proposals` alone is written by three lanes;
    # `llm_generated_amendment_proposals` by exactly one.
    summary = data.get("summary")
    if isinstance(summary, dict):
        keys.update(summary)
        # And some stamp their identity there rather than at the root: brand
        # recovery and the accuracy sample did, and both were reported as never
        # having run.
        for field in ("artifact_type", "schema_version"):
            identity = summary.get(field)
            if isinstance(identity, str):
                keys.add(identity)
    return keys


def lane_evidence(files, markers, filenames, keys_of=None):
    """Return the retained files that show this lane ran.

    ``keys_of`` reads a file's top-level keys; the report passes one that
    remembers what it has read, so no file is parsed twice.
    """
    keys_of = keys_of or top_level_keys
    found = {
        path for path in files if any(fnmatch.fnmatch(path.name, pattern) for pattern in filenames)
    }
    if markers:
        wanted = set(markers)
        found.update(path for path in files if path.suffix == ".json" and wanted & keys_of(path))
    return sorted(found)


def context_update_demanded(run_dir):
    """Return the consensus artifacts that demanded a context-aware update.

    ``consensus`` records this itself when the corpus came back more than half in
    exception with neither lane conditioned by a corpus context. Reading the
    demand from the artifact -- rather than leaving it to ``--require`` -- is the
    point: an operator who forgets the flag still cannot close the run, and a
    future engagement inherits the check without anyone remembering to ask for it.
    """
    demanded = []
    for path in Path(run_dir).rglob("*.json"):
        if path.name.startswith("consensus") and "raw" not in path.parts:
            try:
                summary = json.loads(path.read_text()).get("summary") or {}
            except (OSError, ValueError):
                continue
            if summary.get("context_aware_update_required"):
                demanded.append(path)
    return sorted(demanded)


def coverage(run_dir, lanes=None, declared=None):
    """Return one record per lane saying whether the run shows it ran.

    ``declared`` maps a lane to the artifacts a generation manifest names as
    authoritative. A restarted run holds several attempts per lane, and without a
    declaration any of them credits the lane -- so a lane can read as covered on
    the strength of an attempt the operator discarded. Where a lane is declared,
    only its authoritative artifacts are evidence.
    """
    lanes = LANES if lanes is None else lanes
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise ValueError(f"not a run directory: {run_dir}")
    files = candidate_files(run_dir)
    # Each retained file is parsed once per report, however many lanes look for
    # a marker in it. The commission run holds 44,963 JSON files, and parsing
    # every one again for each lane kept this report running for most of an
    # hour without finishing.
    parsed = {}

    def keys_of(path):
        if path not in parsed:
            parsed[path] = top_level_keys(path)
        return parsed[path]

    results = []
    for name, script, markers, filenames in lanes:
        evidence = lane_evidence(files, markers, filenames, keys_of)
        if declared is not None and name in declared:
            authoritative = {(run_dir / item).resolve() for item in declared[name]}
            evidence = [path for path in evidence if path.resolve() in authoritative]
        results.append(
            {
                "lane": name,
                "command": script,
                "ran": bool(evidence),
                "evidence": [path.relative_to(run_dir).as_posix() for path in evidence[:3]],
            }
        )
    return results


def report(run_dir, required=(), lanes=None, declared=None):
    """Return the coverage report and the required lanes that produced nothing."""
    UNSCANNED.clear()
    results = coverage(run_dir, lanes, declared)
    known = {item["lane"] for item in results}
    unknown = sorted(set(required) - known)
    if unknown:
        raise ValueError(f"--require names no such lane: {', '.join(unknown)}")
    missing_required = sorted(
        item["lane"] for item in results if item["lane"] in set(required) and not item["ran"]
    )
    demanded = context_update_demanded(run_dir)
    ran = {item["lane"] for item in results if item["ran"]}
    if demanded and "context-aware extraction" not in ran:
        # Not merely reported: this joins the missing-required list, which is what
        # makes the command exit non-zero.
        missing_required = sorted({*missing_required, "context-aware extraction"})
    return {
        "schema_version": "1.0",
        "run_directory": str(Path(run_dir)),
        "generation_declared_lanes": sorted(declared) if declared else [],
        "summary": {
            "lanes": len(results),
            "lanes_with_retained_output": sum(1 for item in results if item["ran"]),
            "lanes_with_no_retained_output": sum(1 for item in results if not item["ran"]),
            "required_lanes_missing": len(missing_required),
        },
        "context_aware_update_demanded_by": [
            path.relative_to(Path(run_dir)).as_posix() for path in demanded
        ],
        "lanes": results,
        "required_lanes_missing": missing_required,
        # Named so a reader can tell "this lane produced nothing" apart from
        # "this report could not read the artifact that would have proved it".
        "unscannable_artifacts": sorted(str(path) for path in UNSCANNED),
        "interpretation": (
            "A lane with no retained output was not run. That may be deliberate; "
            "this report states it rather than letting a run look complete. A lane "
            "that did produce output may still have read nothing -- every page can "
            "fail and the lane still writes its handoff -- so read run_sentinel.py "
            "for provider health beside this."
        ),
    }


def main(argv=None):
    """Report lane coverage for one run directory."""
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run_dir", type=Path, help="Run directory whose retained lanes are counted."
    )
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="LANE",
        help=(
            "Fail when this lane produced nothing. Repeatable. Lane names are the "
            "ones this command prints."
        ),
    )
    parser.add_argument(
        "--generation",
        type=Path,
        help=(
            "Generation manifest declaring the authoritative artifact per lane. "
            "A restarted run holds several attempts, and without this any of them "
            "credits its lane -- so a discarded attempt can make a lane read as "
            "covered. Where a lane is declared, only its authoritative artifacts "
            "count as evidence."
        ),
    )
    parser.add_argument("--out", type=Path, help="New JSON report path.")
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args(argv)

    try:
        declared = None
        if args.generation:
            # Imported here rather than at module scope: run_generation reads this
            # module's catalogue, and a module-level pair would be an import cycle.
            import run_generation

            declared = {
                lane: entry["authoritative"]
                for lane, entry in run_generation.load(args.generation).items()
            }
        result = report(args.run_dir, tuple(args.require), declared=declared)
        if args.out:
            if args.out.exists():
                raise FileExistsError(f"refusing to overwrite existing report: {args.out}")
            args.out.write_text(json.dumps(result, indent=2) + "\n")
    except (OSError, ValueError, FileExistsError) as exc:
        sys.exit(f"Run lane coverage failed: {exc}")

    if not args.quiet:
        summary = result["summary"]
        print(
            f"Lanes with retained output: {summary['lanes_with_retained_output']} "
            f"of {summary['lanes']}"
        )
        for item in result["lanes"]:
            if not item["ran"]:
                print(f"  no retained output: {item['lane']} ({item['command']})")
        # A file too large to parse is not evidence that a lane did not run, and
        # this report must never let the two look the same.
        for unscanned in sorted(result["unscannable_artifacts"]):
            print(f"  NOT SCANNED (too large to parse): {unscanned}")
    if result["required_lanes_missing"]:
        sys.exit(
            "Run lane coverage failed: required lanes produced nothing: "
            + ", ".join(result["required_lanes_missing"])
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
