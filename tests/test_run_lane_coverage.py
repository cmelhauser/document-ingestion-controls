"""Hold the lane-coverage report to what a run directory actually contains.

A full trial reached CRM staging with fourteen lane families never invoked, and
nothing said so, because each lane was individually optional and a lane that
never runs writes nothing to notice. These tests are written from that failure:
the report's only job is to make an absent lane visible.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

coverage = importlib.import_module("run_lane_coverage")

LANES = (
    ("named file", "a.py", (), ("consensus.json",)),
    ("identified artifact", "b.py", ("evidence_graph_v1",), ()),
    ("distinctive key", "c.py", ("grouping_method",), ()),
)


def run_dir(tmp_path, files):
    """Write a run directory containing the given relative files."""
    for name, payload in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return tmp_path


def test_each_retained_file_is_parsed_once_however_many_lanes_look(tmp_path, monkeypatch):
    """The commission run holds 44,963 JSON files, and parsing each once per lane
    kept this report running for most of an hour without finishing."""
    directory = run_dir(
        tmp_path,
        {
            "a/graph.json": {"artifact_type": "evidence_graph_v1"},
            "b/rows.json": {"grouping_method": "rows"},
        },
    )
    parsed = []
    original = coverage.top_level_keys

    def counted(path):
        parsed.append(path.name)
        return original(path)

    monkeypatch.setattr(coverage, "top_level_keys", counted)
    results = coverage.coverage(directory, LANES)
    assert [item["ran"] for item in results] == [False, True, True]
    assert sorted(parsed) == ["graph.json", "rows.json"]


def test_a_lane_that_left_nothing_is_named_rather_than_assumed(tmp_path):
    """The whole point: silence about a lane is what let fourteen go unrun."""
    directory = run_dir(
        tmp_path,
        {
            "controls/consensus.json": {"documents": []},
            "graph/graph.json": {"artifact_type": "evidence_graph_v1"},
        },
    )
    results = coverage.coverage(directory, LANES)
    ran = {item["lane"]: item["ran"] for item in results}
    assert ran == {"named file": True, "identified artifact": True, "distinctive key": False}
    assert results[0]["evidence"] == ["controls/consensus.json"]
    assert results[2]["evidence"] == []


def test_scan_profile_uses_the_documented_producer_filename(tmp_path):
    """A real `profile.json` is evidence that scan profiling ran."""
    directory = run_dir(tmp_path, {"profile.json": {"summary": {"total_pages_profiled": 18}}})
    scan_profile = next(
        item for item in coverage.coverage(directory) if item["lane"] == "scan profiling"
    )
    assert scan_profile == {
        "lane": "scan profiling",
        "command": "scan_profile.py",
        "ran": True,
        "evidence": ["profile.json"],
    }


def test_a_producer_is_identified_by_either_field_it_names_itself_in(tmp_path):
    """Some artifacts carry `artifact_type`, some `schema_version`; both count."""
    directory = run_dir(tmp_path, {"review/pack.json": {"schema_version": "evidence_graph_v1"}})
    assert coverage.coverage(directory, LANES)[1]["ran"] is True
    # A distinctive top-level key identifies a producer that names neither.
    directory = run_dir(tmp_path, {"review/g.json": {"grouping_method": "root_cause"}})
    assert coverage.coverage(directory, LANES)[2]["ran"] is True


def test_a_provider_request_is_not_evidence_that_a_lane_produced_something(tmp_path):
    """A raw response names the lane that asked for it, including one that failed."""
    directory = run_dir(
        tmp_path, {"providers/raw_profile/x.json": {"artifact_type": "evidence_graph_v1"}}
    )
    assert coverage.coverage(directory, LANES)[1]["ran"] is False
    assert not coverage.candidate_files(directory)


def test_unreadable_and_oversized_artifacts_are_skipped_not_guessed_at(tmp_path):
    """A file this cannot parse says nothing about a lane, in either direction."""
    directory = run_dir(
        tmp_path,
        {
            "a/not-json.json": "{not json at all",
            "a/list.json": [1, 2, 3],
            "a/plain.txt": "grouping_method",
        },
    )
    assert all(item["ran"] is False for item in coverage.coverage(directory, LANES))
    assert coverage.top_level_keys(directory / "a/not-json.json") == set()
    assert coverage.top_level_keys(directory / "a/list.json") == set()
    assert coverage.top_level_keys(directory / "missing.json") == set()

    with pytest.raises(ValueError, match="not a run directory"):
        coverage.coverage(directory / "nope", LANES)


def test_a_large_ocr_artifact_is_read_not_written_off_as_an_empty_lane(tmp_path):
    """An OCR artifact is legitimately large, and the ceiling was below realistic.

    A real 18-page corpus produced a 13 MB Document AI handoff against an 8 MB
    ceiling, so this report announced that lane had never run when it had just
    succeeded on all 18 pages. Silently under-reporting is the worst failure
    available to a tool whose whole job is saying honestly what ran: an operator
    reruns a lane that already succeeded, or calls a complete pipeline broken.
    """
    directory = run_dir(tmp_path, {"a/keep.json": {"x": 1}})
    big = directory / "a/document_ai.json"
    # Comfortably past the old ceiling and past any realistic artifact.
    big.write_text(json.dumps({"processor_configuration": "x", "pad": "y" * 9_000_000}))
    assert "processor_configuration" in coverage.top_level_keys(big)
    assert not coverage.UNSCANNED

    result = coverage.report(directory)
    assert result["unscannable_artifacts"] == []
    assert next(i for i in result["lanes"] if i["lane"] == "independent OCR")["ran"] is True


def test_an_artifact_too_large_to_parse_is_reported_not_taken_for_an_empty_one(
    tmp_path, monkeypatch, capsys
):
    """ "This lane produced nothing" and "I could not read it" are different claims."""
    directory = run_dir(tmp_path, {"a/keep.json": {"x": 1}})
    huge = directory / "a/huge.json"
    huge.write_text(json.dumps({"grouping_method": "x"}))
    # Far cheaper than writing half a gigabyte to prove the same boundary.
    monkeypatch.setattr(coverage, "UNSCANNABLE_BYTES", 4)

    assert coverage.top_level_keys(huge) == set()
    assert huge in coverage.UNSCANNED

    result = coverage.report(directory)
    assert str(huge) in result["unscannable_artifacts"]
    # The lane it would have proved is still reported as unproven -- this does
    # not credit a lane it could not read -- but the reason is now visible.
    assert next(i for i in result["lanes"] if i["lane"] == "root-cause grouping")["ran"] is False

    monkeypatch.setattr(sys, "argv", ["run_lane_coverage.py", str(directory)])
    coverage.main()
    assert "NOT SCANNED (too large to parse)" in capsys.readouterr().out

    # A later report over clean input does not inherit the earlier complaint.
    monkeypatch.setattr(coverage, "UNSCANNABLE_BYTES", 512_000_000)
    assert coverage.report(directory)["unscannable_artifacts"] == []


def test_a_required_lane_that_produced_nothing_fails_the_report(tmp_path, monkeypatch, capsys):
    """A run profile that must include a lane can say so and be held to it."""
    directory = run_dir(tmp_path, {"controls/consensus.json": {"documents": []}})
    monkeypatch.setattr(coverage, "LANES", LANES)
    monkeypatch.setattr(
        sys, "argv", ["run_lane_coverage.py", str(directory), "--require", "distinctive key"]
    )
    with pytest.raises(SystemExit, match="required lanes produced nothing: distinctive key"):
        coverage.main()
    printed = capsys.readouterr().out
    assert "Lanes with retained output: 1 of 3" in printed
    assert "no retained output: distinctive key (c.py)" in printed

    with pytest.raises(ValueError, match="--require names no such lane: typo"):
        coverage.report(directory, ("typo",), LANES)


def test_the_report_is_written_once_and_never_overwritten(tmp_path, monkeypatch, capsys):
    """A prior run's report is a record, not a scratch file."""
    directory = run_dir(tmp_path, {"controls/consensus.json": {"documents": []}})
    monkeypatch.setattr(coverage, "LANES", LANES)
    out = tmp_path / "coverage.json"
    argv = ["run_lane_coverage.py", str(directory), "--out", str(out), "--quiet"]
    monkeypatch.setattr(sys, "argv", argv)
    assert coverage.main() == 0
    assert capsys.readouterr().out == ""
    written = json.loads(out.read_text())
    assert written["summary"] == {
        "lanes": 3,
        "lanes_with_retained_output": 1,
        "lanes_with_no_retained_output": 2,
        "required_lanes_missing": 0,
    }
    assert "was not run" in written["interpretation"]

    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        coverage.main()


def test_every_command_in_the_repository_is_covered_by_a_lane():
    """A lane nobody catalogued is exactly the lane that goes unmeasured."""
    catalogued = {command for _, command, _, _ in coverage.LANES}
    missing = sorted(coverage.uncatalogued_commands())
    assert not missing, f"commands absent from LANES: {missing}"
    assert "consensus.py" in catalogued


def test_a_lane_is_not_credited_for_another_lane_s_similarly_named_file():
    """`manifest.json` is a name several lanes use.

    Matching it by filename credited `operations.py manifest` with the
    inferred-controls manifest, so a run that never bound its artifacts to a hash
    would have reported that it had. Every lane in the catalogue is matched on a
    key or an identity its own producer writes, or on a filename no other lane
    uses.
    """
    generic = {name for _, _, _, filenames in coverage.LANES for name in filenames}
    assert "manifest.json" not in generic

    # Ambiguity is checked mechanically rather than by inspection: no two lanes
    # may claim the same filename or the same identifying key.
    seen: dict[str, str] = {}
    for lane, _, markers, filenames in coverage.LANES:
        for signature in (*markers, *filenames):
            assert signature not in seen, f"{lane} and {seen[signature]} both claim {signature}"
            seen[signature] = lane


def test_scratch_left_inside_a_run_is_counted_and_overstates_it(tmp_path):
    """The run directory is evidence, and this counts all of it.

    Smoke-test outputs written inside a real run raised its reported coverage by
    two lanes that had not run on that corpus at all. There is deliberately no
    exclusion mechanism: one would let a missing lane be hidden, which is the
    failure this report exists to expose. The rule is that scratch lives
    elsewhere, and this test states it.
    """
    directory = run_dir(tmp_path, {"controls/consensus.json": {"documents": []}})
    assert coverage.coverage(directory, LANES)[2]["ran"] is False

    (directory / "smoke_test").mkdir()
    (directory / "smoke_test" / "g.json").write_text(json.dumps({"grouping_method": "synthetic"}))
    assert coverage.coverage(directory, LANES)[2]["ran"] is True, (
        "anything under the run directory counts -- keep scratch outside it"
    )


def test_a_producer_that_writes_an_array_or_hides_its_identity_in_a_summary(tmp_path):
    """Five catalogue signatures named keys their producers never write.

    They were written while those lanes had never produced output, so nothing
    contradicted them, and each would have reported "not run" forever. Two
    shapes defeated the scan: a producer that writes a JSON array rather than an
    envelope, and one whose top-level keys are all generic but which names
    itself inside its summary.
    """
    array_producer = run_dir(tmp_path / "a", {"out/rows.json": [{"grouping_method": "x"}]})
    assert coverage.coverage(array_producer, LANES)[2]["ran"] is True
    assert coverage.top_level_keys(array_producer / "out/rows.json") == {"grouping_method"}
    # An array of non-objects identifies nothing.
    empty = run_dir(tmp_path / "b", {"out/rows.json": [1, 2, 3]})
    assert coverage.top_level_keys(empty / "out/rows.json") == set()

    summary_producer = run_dir(
        tmp_path / "c", {"out/x.json": {"summary": {"grouping_method": "x"}, "items": []}}
    )
    assert coverage.coverage(summary_producer, LANES)[2]["ran"] is True
    # A summary that is not an object contributes nothing rather than failing.
    odd = run_dir(tmp_path / "d", {"out/x.json": {"summary": "none"}})
    assert coverage.top_level_keys(odd / "out/x.json") == {"summary"}


def lane(name):
    """Return the catalogued entry for one lane."""
    for entry in coverage.LANES:
        if entry[0] == name:
            return entry
    raise AssertionError(f"no catalogued lane named {name!r}")


def test_a_chunked_variant_manifest_is_evidence_the_lane_ran(tmp_path):
    """Preprocessing names each manifest for its chunk, page, or recovery pass.

    A production run wrote `variants_chunk_01_manifest.json` and no
    `variants_manifest.json`. Matching the documented name exactly reported a
    lane that had rendered every page as one that never ran.
    """
    _, _, markers, filenames = lane("image variants")
    for written in (
        "variants_manifest.json",
        "variants_chunk_01_manifest.json",
        "variants_page13_manifest.json",
        "variants_recovery_01_manifest.json",
    ):
        files = [Path(run_dir(tmp_path, {f"pages/{written}": {"dpi": 300}}) / "pages" / written)]
        assert coverage.lane_evidence(files, markers, filenames), written


def test_a_wildcard_lane_is_not_credited_for_an_unrelated_manifest(tmp_path):
    """The wildcard must stay anchored to the lane that owns the name."""
    _, _, markers, filenames = lane("image variants")
    for unrelated in ("ingestion_manifest.json", "reassembly_manifest.json"):
        files = [Path(run_dir(tmp_path, {unrelated: {"dpi": 300}}) / unrelated)]
        assert not coverage.lane_evidence(files, markers, filenames), unrelated


def test_attribution_is_recognised_under_an_operator_chosen_output_name(tmp_path):
    """`--out` is the operator's to name, so the filename cannot be the only proof.

    The command's own documented example writes `attributed.json`, and the
    production run did too, while the catalogue looked only for
    `attribution.json` -- so the one lane that reported on 716 documents read as
    never having run.
    """
    _, _, markers, filenames = lane("attribution")
    written = run_dir(tmp_path, {"attributed.json": {"summary": {"attribution_by_method": {}}}})
    assert coverage.lane_evidence([written / "attributed.json"], markers, filenames)


def test_amendments_are_recognised_under_an_operator_chosen_output_name(tmp_path):
    """Same defect, same shape: the run wrote `adjudication.json`."""
    _, _, markers, filenames = lane("amendments")
    written = run_dir(tmp_path, {"adjudication.json": {"summary": {"proposed_amendments": 0}}})
    assert coverage.lane_evidence([written / "adjudication.json"], markers, filenames)


def test_the_documented_default_names_still_count_as_evidence(tmp_path):
    """Widening the match must not drop the names the runbooks tell operators to use."""
    for name, written in (("attribution", "attribution.json"), ("amendments", "amendments.json")):
        _, _, markers, filenames = lane(name)
        path = run_dir(tmp_path, {written: {"summary": {}}}) / written
        assert coverage.lane_evidence([path], markers, filenames), written


def test_an_iterated_output_name_still_credits_its_lane(tmp_path):
    """A rerun iterates `--out`, and the documented default is only the first name.

    A run that rebuilt its gate wrote `final_queue_02.json` and reported the gate
    as never run; a second usage report wrote `usage_02.json` and reported the
    same. Both lanes had run, twice.
    """
    for lane_name, written in (
        ("final review queue", "final_queue_02.json"),
        ("usage report", "usage_02.json"),
    ):
        _, _, markers, filenames = lane("usage report" if "usage" in written else lane_name)
        path = run_dir(tmp_path / written, {written: {"summary": {}}}) / written
        assert coverage.lane_evidence([path], markers, filenames), written


def test_the_usage_report_is_identified_by_a_key_it_actually_carries(tmp_path):
    """Its marker was a filename string, which no artifact carries as a key."""
    _, _, markers, _ = lane("usage report")
    assert "how_to_price" in markers
    path = run_dir(tmp_path, {"logs/usage_09.json": {"how_to_price": {"formula": "x"}}})
    files = [path / "logs" / "usage_09.json"]
    assert coverage.lane_evidence(files, markers, ())


def test_every_catalogued_marker_is_a_key_a_lane_can_actually_write():
    """A marker that reads like a substring is a silent false negative.

    `apply_corroboration.py` shipped catalogued as `applied_corroboration`, ran
    on a real corpus, and was reported as never having run -- because markers
    match an artifact's top-level keys and the key it writes is
    `applied_corroboration_v1`. A lane wrongly reported as unrun invites an
    operator to run an authorized acceptance a second time.
    """
    import re

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    # A thin entry point delegates to a module, so the whole tree is searched:
    # this catches a marker that exists nowhere at all, which is the bug class.
    # The catalogue itself is left out: it quotes every marker, so searching it
    # found each one and this test could not fail.
    source = "\n".join(
        path.read_text()
        for path in sorted(scripts.rglob("*.py"))
        if path.name != "run_lane_coverage.py"
    )
    for name, script, markers, filenames in coverage.LANES:
        assert markers or filenames, f"{name}: catalogued with nothing to match on"
        for marker in markers:
            # The marker has to appear as a literal -- a key the lane writes, or
            # the artifact_type it stamps -- not as a plausible-looking prefix.
            assert re.search(rf"[\"']{re.escape(marker)}[\"']", source), (
                f"{name} ({script}): marker {marker!r} is a key no lane writes"
            )


def test_the_recovery_lanes_are_credited_for_what_they_actually_write(tmp_path, monkeypatch):
    """Both recovery lanes ran on the commission run and were reported as never run.

    Brand recovery was catalogued as `brand_recovery`, a prefix of the artifact
    type it stamps (`brand_recovery_v1`); specifier recovery as the key it writes
    on each document, where the report does not look. A literal found somewhere
    in the source proves neither, so each lane's own command writes its artifact
    here and the report has to credit it.
    """
    brand_lane = importlib.import_module("brand_recover")
    specifier_lane = importlib.import_module("specifier_recover")
    raw = tmp_path / "raw"
    raw.mkdir()
    pages = {"a": "HALVOR furniture", "b": "HALVOR furniture", "e": "HALVOR furniture"}
    pages["s"] = "2308.00125\n09/22/23\nNORCROSS TAMPA"
    for index, (doc_id, text) in enumerate(pages.items()):
        body = {"response": {"document": {"text": text}}}
        (raw / f"{index:06d}_{doc_id}.json").write_text(json.dumps(body))
    run = tmp_path / "run"
    source = (
        run_dir(
            run,
            {
                "inputs/records.json": [
                    {"document_id": "a", "header": {"brand_name": "HALVOR"}},
                    {"document_id": "b", "header": {"brand_name": "HALVOR"}},
                    {"document_id": "e", "header": {}},
                    {"document_id": "s", "lines": [{"purchase_order_number": "2308.00125"}]},
                ]
            },
        )
        / "inputs/records.json"
    )
    for lane_module, out in (
        (brand_lane, "controls/records_with_brands_01.json"),
        (specifier_lane, "controls/records_with_specifiers_01.json"),
    ):
        (run / "controls").mkdir(exist_ok=True)
        argv = [lane_module.__name__, str(source), "--extractor-raw", str(raw)]
        argv += ["--out", str(run / out), "--exceptions", str(run / f"{out}.exceptions.json")]
        monkeypatch.setattr(sys, "argv", [*argv, "--quiet"])
        lane_module.main()
    ran = {item["lane"]: item for item in coverage.coverage(run, coverage.LANES)}
    # Brand recovery writes its summary into its exceptions file too, which is
    # evidence of the same run.
    assert "controls/records_with_brands_01.json" in ran["brand recovery"]["evidence"]
    assert ran["specifier recovery"]["evidence"] == ["controls/records_with_specifiers_01.json"]
    # The accuracy sample stamps its type in its summary the same way.
    sampled = run_dir(
        tmp_path / "sampled",
        {"review/sample.json": {"summary": {"artifact_type": "accuracy_sample_v1"}}},
    )
    accuracy = next(
        item for item in coverage.coverage(sampled) if item["lane"] == "accuracy sample"
    )
    assert accuracy["evidence"] == ["review/sample.json"]


def test_a_raw_directory_is_excluded_whichever_way_the_run_named_it(tmp_path):
    """A raw response names the lane that asked for it, even when the ask failed.

    The exclusion tested only the start of a directory name. A run names these
    both ways -- `providers/raw/` and
    `post_review_consolidation_02/client_review_cross_record_raw/` -- and on a
    real 52 GB run the trailing form let 558 provider responses through to be
    counted as lane evidence.
    """
    run = tmp_path / "run"
    for relative in (
        "providers/raw/response.json",
        "htr/raw_01/response.json",
        "post_review_consolidation_02/client_review_cross_record_raw/cross_record-0047.json",
        "post_review_consolidation_02/review_agent_raw/slice-01.json",
    ):
        path = run / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    kept = run / "controls" / "consensus.json"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("{}")

    assert coverage.candidate_files(run) == [kept]
    assert coverage.is_raw(Path("providers/raw/response.json"))
    assert coverage.is_raw(Path("a/client_review_cross_record_raw/b.json"))
    assert not coverage.is_raw(Path("controls/raw_material_report.json"))


def test_the_planning_report_is_catalogued_as_a_non_lane_command():
    """A run does not miss a report it reads out of its own artifacts."""
    assert "pipeline_plan.py" in coverage.NON_LANE_COMMANDS
    assert coverage.uncatalogued_commands() == set()
