"""Hold the pipeline planner to what a run directory actually contains.

Every one of these tests was written after running the command against a real
52 GB run and finding the answer wrong. That is deliberate: a planner validated
only against fixtures it also authored will agree with itself about artifact
shapes that do not exist. The fixtures below carry the shapes the production
artifacts actually have -- `total_pages_profiled` inside `summary`,
`exception_rate_pct` as a percentage, `source_artifact` on a queue item -- and
the tests fail if the code goes back to the shapes it was first written for.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pipeline_plan  # noqa: E402


def write(run, relative, payload):
    path = run / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def consensus_summary(**overrides):
    """The consensus summary shape, as the lane writes it."""
    summary = {
        "documents": 716,
        "fields": 83453,
        "exception_fields": 58463,
        "exception_rate_pct": 70.06,
        "single_engine_documents": 0,
        "context_aware_update_required": True,
        "corpus_context": None,
    }
    summary.update(overrides)
    return {"summary": summary, "documents": []}


def records(documents):
    return {"artifact_type": "records_with_applied_mappings_v1", "documents": documents}


def document(document_id, header=None, fields=None, handwriting=None):
    cells = dict(fields or {})
    if handwriting is not None:
        cells["has_handwriting"] = {"value": handwriting}
    return {"document_id": document_id, "header": header or {}, "fields": cells}


def cell(value):
    return {"value": value, "accepted": True}


def test_a_run_with_no_measurable_artifact_is_refused(tmp_path):
    """Rule 9 applied to planning: an empty plan reads like a decision."""
    run = tmp_path / "run"
    run.mkdir()
    write(run, "notes.json", {"unrelated": True})
    with pytest.raises(SystemExit) as raised:
        pipeline_plan.build(run, "balanced", ("job_number",))
    assert "will not emit a plan built from nothing" in str(raised.value)


def test_a_path_that_is_not_a_run_directory_is_refused(tmp_path):
    with pytest.raises(SystemExit) as raised:
        pipeline_plan.build(tmp_path / "absent", "balanced", ("job_number",))
    assert "Not a run directory" in str(raised.value)


def test_an_unknown_objective_is_refused(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    with pytest.raises(SystemExit) as raised:
        pipeline_plan.build(run, "cheapest", ("job_number",))
    assert "Unknown objective" in str(raised.value)


def test_the_scan_profile_page_count_is_read_from_the_summary(tmp_path):
    """`total_pages_profiled` lives inside `summary`, not at the top level.

    Reading it from the top level found nothing and the plan reported that the
    run had no scan profile, on a run whose scan profile was the first artifact
    it wrote.
    """
    run = tmp_path / "run"
    write(run, "profile.json", {"summary": {"total_pages_profiled": 716}, "pages": []})
    write(run, "controls/consensus.json", consensus_summary())
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    assert report["measured"]["pages"] == 716


def test_the_exception_rate_is_compared_as_a_fraction_not_a_percentage(tmp_path):
    """The lane writes `exception_rate_pct`; the threshold is a fraction."""
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary(exception_rate_pct=70.06))
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    assert report["measured"]["consensus_exception_rate"] == pytest.approx(0.7006)
    context = next(entry for entry in report["plan"] if entry["command"] == "extraction_context.py")
    assert context["decision"] == pipeline_plan.SELECTED

    quiet = tmp_path / "quiet"
    write(quiet, "controls/consensus.json", consensus_summary(exception_rate_pct=12.5))
    calm = pipeline_plan.build(quiet, "balanced", ("job_number",))
    context = next(entry for entry in calm["plan"] if entry["command"] == "extraction_context.py")
    assert context["decision"] == pipeline_plan.SKIPPED


def test_starvation_is_measured_over_fields_not_documents(tmp_path):
    """`single_engine_documents` was 0 on a run with 4,271 single-engine fields.

    They are different populations. Deciding the corroboration lane on the
    document count skipped it on exactly the corpus that needed it.
    """
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary(single_engine_documents=0))
    write(
        run,
        "controls/consensus_exceptions.json",
        {
            "summary": {"count": 3},
            "exceptions": [
                {"flag": "single_engine", "is_handwritten": False},
                {"flag": "single_engine", "is_handwritten": False},
                {"flag": "no_consensus", "is_handwritten": False},
            ],
        },
    )
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    assert report["measured"]["single_engine_documents"] == 0
    assert report["measured"]["single_engine_fields"] == 2
    assert report["measured"]["single_engine_share"] == pytest.approx(2 / 3)
    lane = next(
        entry for entry in report["plan"] if entry["command"] == "independent_corroboration.py"
    )
    assert lane["decision"] == pipeline_plan.SELECTED


def test_acceptance_on_corroboration_stays_a_client_decision(tmp_path):
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    write(
        run,
        "controls/consensus_exceptions.json",
        {"exceptions": [{"flag": "single_engine", "is_handwritten": False}]},
    )
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    applied = next(
        entry for entry in report["plan"] if entry["command"] == "apply_corroboration.py"
    )
    assert applied["decision"] == pipeline_plan.UNDECIDED
    assert "client policy decision" in applied["because"]
    assert "--authorization" in applied["then"]


def test_key_cardinality_separates_a_multi_job_document_from_an_unattributed_one(tmp_path):
    """The raw unattributed count conflates opposite findings.

    A statement naming twenty jobs on its lines is not missing an attribution --
    it has twenty, and no reference table will resolve it. A statement naming one
    job, unanimously, is a different question with a different answer.
    """
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    write(
        run,
        "controls/records.json",
        records(
            [
                document("with-header", header={"job_number": {"value": "12497"}}),
                document(
                    "unanimous",
                    fields={
                        "lines[0].job_number": cell("12497"),
                        "lines[1].job_number": cell("12497"),
                    },
                ),
                document(
                    "multi",
                    fields={
                        "lines[0].job_number": cell("12497"),
                        "lines[1].job_number": cell("12643"),
                    },
                ),
                document("bare", fields={"lines[0].description": cell("freight")}),
            ]
        ),
    )
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    assert report["measured"]["attribution_key_shape"] == {
        "header_key": 1,
        "unanimous_line_key": 1,
        "multi_key": 1,
        "no_key": 1,
    }
    allocation = next(
        entry for entry in report["plan"] if entry["command"] == "allocation_policy.py"
    )
    assert allocation["decision"] == pipeline_plan.SELECTED
    questions = {question["question"] for question in report["client_questions"]}
    assert any("order-entry" in question for question in questions)
    assert any("same job" in question for question in questions)


def test_an_unaccepted_line_value_is_not_a_key(tmp_path):
    """A proposal is not a fact, here as everywhere else."""
    shape = pipeline_plan.key_cardinality(
        [document("d", fields={"lines[0].job_number": {"value": "12497", "accepted": False}})],
        ("job_number",),
    )
    assert shape == {"no_key": 1}


def test_handwriting_is_measured_from_the_records_not_the_scan_profile(tmp_path):
    """The scan profile carries no handwriting count; it routes on ink and morphology."""
    run = tmp_path / "run"
    write(run, "profile.json", {"summary": {"total_pages_profiled": 4}, "pages": []})
    write(run, "controls/consensus.json", consensus_summary())
    write(
        run,
        "controls/records.json",
        records(
            [
                document("a", handwriting=True),
                document("b", handwriting=True),
                document("c", handwriting=False),
                document("d", handwriting=None),
            ]
        ),
    )
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    assert report["measured"]["handwriting_documents"] == 2
    assert report["measured"]["handwriting_unknown_documents"] == 1
    assert report["measured"]["handwriting_share"] == pytest.approx(0.5)
    ocr = next(entry for entry in report["plan"] if entry["command"] == "google_handwriting_ocr.py")
    assert ocr["decision"] == pipeline_plan.SELECTED
    assert "GOOGLE_HANDWRITING_OCR_ENABLED" in ocr["then"]


def test_a_corpus_with_no_handwriting_skips_both_handwriting_lanes(tmp_path):
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    write(
        run,
        "controls/records.json",
        records([document("a", handwriting=False), document("b", handwriting=False)]),
    )
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    handwriting = [entry for entry in report["plan"] if entry["phase"] == "3H"]
    assert [entry["decision"] for entry in handwriting] == [
        pipeline_plan.SKIPPED,
        pipeline_plan.SKIPPED,
    ]


def test_an_unmeasured_lane_is_undecided_rather_than_skipped(tmp_path):
    """Defaulting an unmeasured lane either way is how a control goes unrun."""
    run = tmp_path / "run"
    write(run, "profile.json", {"summary": {"total_pages_profiled": 4}, "pages": []})
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    decisions = {entry["lane"]: entry["decision"] for entry in report["plan"]}
    assert decisions["handwriting OCR"] == pipeline_plan.UNDECIDED
    assert decisions["context-aware update"] == pipeline_plan.UNDECIDED
    assert decisions["allocation policy"] == pipeline_plan.UNDECIDED
    assert decisions["root-cause grouping"] == pipeline_plan.UNDECIDED
    assert pipeline_plan.SKIPPED not in decisions.values()


def test_the_queue_is_broken_down_by_the_key_its_items_actually_carry(tmp_path):
    """Queue items name `source_artifact`; there is no `source` key to fall back on."""
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    write(
        run,
        "review/final_queue.json",
        {
            "summary": {"client_review_cards": 2},
            "items": [
                {"source_artifact": "corpus_exceptions.json", "review_source": "tables"},
                {"source_artifact": "corpus_exceptions.json", "review_source": "tables"},
                {"source_artifact": "arithmetic.json", "review_source": "arithmetic"},
            ],
        },
    )
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    assert report["measured"]["review_items"] == 3
    assert report["measured"]["review_items_by_source"] == {
        "corpus_exceptions.json": 2,
        "arithmetic.json": 1,
    }
    target = next(
        entry for entry in report["plan"] if entry["command"] == "safe_review_consolidation.py"
    )
    assert "corpus_exceptions.json" in target["because"]


def test_an_empty_queue_is_nothing_to_group(tmp_path):
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    write(run, "review/final_queue.json", {"summary": {"client_review_cards": 0}, "items": []})
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    grouping = next(entry for entry in report["plan"] if entry["command"] == "review_grouping.py")
    assert grouping["decision"] == pipeline_plan.SKIPPED


def test_a_key_field_no_rank_reads_is_reported_as_a_planning_finding(tmp_path):
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    write(
        run,
        "controls/attributed.json",
        {
            "summary": {
                "attribution_by_method": {"unattributable": 690, "explicit_printed": 26},
                "unconsulted_key_fields": {"order_number": 12},
                "reference_rows_ingested": 0,
                "unresolved_documents": 690,
            }
        },
    )
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    entry = next(item for item in report["plan"] if item["command"] == "attribution.py")
    assert entry["decision"] == pipeline_plan.SELECTED
    assert "order_number (12)" in entry["because"]
    # The fixture is itself retained attribution output, so the entry also
    # carries the already-run warning; the finding is still the point.
    assert "already run" in entry["because"]


def test_a_reference_table_already_supplied_is_not_asked_for_again(tmp_path):
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    write(run, "controls/records.json", records([document("bare")]))
    write(
        run,
        "controls/attributed.json",
        {"summary": {"attribution_by_method": {}, "reference_rows_ingested": 4000}},
    )
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    entry = next(
        item
        for item in report["plan"]
        if item["command"] == "attribution.py" and "no attribution key" in item["because"]
    )
    assert entry["decision"] == pipeline_plan.SELECTED
    assert "order-entry export" not in (entry["then"] or "")


def test_the_objective_moves_a_close_call_and_nothing_else(tmp_path):
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    write(
        run,
        "controls/consensus_exceptions.json",
        {
            "exceptions": [
                {"flag": "single_engine", "is_handwritten": False},
                {"flag": "no_consensus", "is_handwritten": False},
                {"flag": "no_consensus", "is_handwritten": False},
            ]
        },
    )
    thrifty = pipeline_plan.build(run, "least-spend", ("job_number",))
    balanced = pipeline_plan.build(run, "balanced", ("job_number",))
    lane_of = lambda report: next(  # noqa: E731
        entry for entry in report["plan"] if entry["command"] == "independent_corroboration.py"
    )
    assert lane_of(thrifty)["decision"] == pipeline_plan.SKIPPED
    assert lane_of(balanced)["decision"] == pipeline_plan.UNDECIDED
    # The measurement is identical either way; only the verdict moved.
    assert thrifty["measured"] == balanced["measured"]


def test_a_thin_handwriting_share_still_runs_when_questions_cost_more_than_calls(tmp_path):
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    write(
        run,
        "controls/records.json",
        records([document(str(index), handwriting=index == 0) for index in range(200)]),
    )
    thrifty = pipeline_plan.build(run, "least-spend", ("job_number",))
    fewest = pipeline_plan.build(run, "fewest-client-questions", ("job_number",))
    ocr = lambda report: next(  # noqa: E731
        entry for entry in report["plan"] if entry["command"] == "google_handwriting_ocr.py"
    )
    assert ocr(thrifty)["decision"] == pipeline_plan.UNDECIDED
    assert ocr(fewest)["decision"] == pipeline_plan.SELECTED


def test_a_raw_response_directory_is_never_a_measurement_source(tmp_path):
    """Both spellings. A raw response names the lane that asked for it."""
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    write(run, "providers/raw/response.json", consensus_summary(documents=1))
    write(
        run,
        "post_review_consolidation_02/client_review_cross_record_raw/slice.json",
        consensus_summary(documents=2),
    )
    sources, _ = pipeline_plan.candidates(run)
    assert sources["consensus"] == [run / "controls" / "consensus.json"]
    assert not pipeline_plan.is_raw(Path("controls/raw_material.json"))


def test_the_newest_artifact_wins_because_a_lane_reruns_into_a_fresh_path(tmp_path):
    run = tmp_path / "run"
    old = write(run, "controls/consensus.json", consensus_summary(exception_rate_pct=90.0))
    new = write(run, "controls/consensus_04.json", consensus_summary(exception_rate_pct=12.0))
    import os

    os.utime(old, (1, 1))
    os.utime(new, (100, 100))
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    assert report["measured"]["consensus_exception_rate"] == pytest.approx(0.12)
    assert report["summary"]["measurement_sources"]["consensus"] == str(new)


def test_an_artifact_too_large_to_read_is_named_rather_than_treated_as_absent(
    tmp_path, monkeypatch
):
    """ "Too large to read" and "this lane never ran" are different findings."""
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    monkeypatch.setattr(pipeline_plan, "UNSCANNABLE_BYTES", 4)
    found, unscanned, _ = pipeline_plan.artifacts(run)
    assert "consensus" not in found
    # The one file answers two measurements, so it is named once for each.
    assert {entry["measurement"] for entry in unscanned} == {"consensus", "records"}
    assert {entry["reason"] for entry in unscanned} == {"too_large"}
    assert {entry["path"] for entry in unscanned} == {str(run / "controls" / "consensus.json")}


def test_an_unparseable_candidate_falls_through_to_the_next_one(tmp_path):
    run = tmp_path / "run"
    truncated = run / "controls" / "consensus_bad.json"
    truncated.parent.mkdir(parents=True, exist_ok=True)
    truncated.write_text('{"summary": {"single_engine_documents": 0')
    good = write(run, "controls/consensus.json", consensus_summary())
    import os

    os.utime(truncated, (100, 100))
    os.utime(good, (1, 1))
    found, unscanned, _ = pipeline_plan.artifacts(run)
    assert found["consensus"][0] == good
    consensus = [entry for entry in unscanned if entry["measurement"] == "consensus"]
    assert consensus == [
        {"measurement": "consensus", "path": str(truncated), "reason": "unparseable"}
    ]


def test_a_non_object_artifact_is_not_a_measurement(tmp_path):
    run = tmp_path / "run"
    write(run, "controls/list.json", consensus_summary())
    (run / "controls" / "array.json").write_text('["single_engine_documents"]')
    found, _, _ = pipeline_plan.artifacts(run)
    assert found["consensus"][0].name == "list.json"
    assert pipeline_plan.load(run / "absent.json") is None


def test_the_report_declares_itself_a_proposal(tmp_path):
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    assert report["artifact_type"] == pipeline_plan.ARTIFACT_TYPE
    assert report["proposal_only"] is True
    assert report["clears_no_control"] is True


def test_the_rendered_plan_names_every_lane_and_question(tmp_path):
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    write(run, "controls/records.json", records([document("bare")]))
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    text = pipeline_plan.render(report)
    for entry in report["plan"]:
        assert entry["lane"] in text
        assert entry["because"] in text
    for question in report["client_questions"]:
        assert question["question"] in text


def test_the_cli_writes_the_plan_and_refuses_to_clobber(tmp_path, monkeypatch, capsys):
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    out = tmp_path / "plans" / "pipeline_plan.json"
    monkeypatch.setattr(sys, "argv", ["pipeline_plan.py", str(run), "--out", str(out)])
    assert pipeline_plan.main() == 0
    assert "Pipeline plan for" in capsys.readouterr().out
    assert json.loads(out.read_text())["artifact_type"] == pipeline_plan.ARTIFACT_TYPE

    monkeypatch.setattr(sys, "argv", ["pipeline_plan.py", str(run), "--out", str(out), "--quiet"])
    with pytest.raises(SystemExit) as raised:
        pipeline_plan.main()
    assert "Refusing to overwrite" in str(raised.value)


def test_the_cli_key_field_override_reaches_the_measurement(tmp_path, monkeypatch, capsys):
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    write(
        run,
        "controls/records.json",
        records([document("d", header={"contract_number": {"value": "C-1"}})]),
    )
    out = tmp_path / "plan.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pipeline_plan.py",
            str(run),
            "--key-field",
            "contract_number",
            "--out",
            str(out),
            "--quiet",
        ],
    )
    assert pipeline_plan.main() == 0
    assert capsys.readouterr().out == ""
    report = json.loads(out.read_text())
    assert report["explicit_key_fields"] == ["contract_number"]
    assert report["measured"]["attribution_key_shape"] == {"header_key": 1}


def test_the_default_key_fields_are_the_ones_attribution_actually_reads(tmp_path, monkeypatch):
    """Two lists that drift apart plan for keys the lane never consults."""
    import attribution

    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    out = tmp_path / "plan.json"
    monkeypatch.setattr(sys, "argv", ["pipeline_plan.py", str(run), "--out", str(out), "--quiet"])
    assert pipeline_plan.main() == 0
    assert json.loads(out.read_text())["explicit_key_fields"] == list(
        attribution.EXPLICIT_KEY_FIELDS
    )


def test_a_corpus_with_documents_nobody_typed_needs_an_operator_decision(tmp_path):
    """An unclassified document reaches template and mapping work as noise."""
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    write(
        run,
        "controls/classification.json",
        {
            "artifact_type": "classification_consensus_v1",
            "summary": {"documents": 716, "accepted": 686, "unresolved": 30},
        },
    )
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    entry = next(item for item in report["plan"] if item["command"] == "classification_amend.py")
    assert entry["decision"] == pipeline_plan.UNDECIDED
    assert "abstained on 30 of 716" in entry["because"]
    assert "operator_authorized" in entry["then"]


def test_a_fully_typed_corpus_skips_the_amendment_lane(tmp_path):
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    write(
        run,
        "controls/classification.json",
        {
            "artifact_type": "classification_consensus_v1",
            "summary": {"documents": 716, "accepted": 716, "unresolved": 0},
        },
    )
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    entry = next(item for item in report["plan"] if item["command"] == "classification_amend.py")
    assert entry["decision"] == pipeline_plan.SKIPPED


def test_a_directory_named_like_an_artifact_is_not_read_as_one(tmp_path):
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    (run / "controls" / "decoy.json").mkdir()
    assert pipeline_plan.candidates(run)[0]["consensus"] == [run / "controls" / "consensus.json"]


def test_a_file_that_cannot_be_opened_is_passed_over(tmp_path, monkeypatch):
    run = tmp_path / "run"
    good = write(run, "controls/consensus.json", consensus_summary())
    blocked = write(run, "controls/blocked.json", consensus_summary())
    real_open = Path.open

    def refuse(self, *args, **kwargs):
        if self == blocked:
            raise OSError("permission denied")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse)
    assert pipeline_plan.candidates(run)[0]["consensus"] == [good]


def test_a_blank_line_key_is_not_a_key(tmp_path):
    """An accepted cell whose value is empty states nothing."""
    shape = pipeline_plan.key_cardinality(
        [document("d", fields={"lines[0].job_number": {"value": "   ", "accepted": True}})],
        ("job_number",),
    )
    assert shape == {"no_key": 1}


def test_a_plan_with_nothing_to_ask_the_client_renders_no_question_section(tmp_path):
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary(exception_rate_pct=5.0))
    write(
        run,
        "controls/classification.json",
        {
            "artifact_type": "classification_consensus_v1",
            "summary": {"accepted": 5, "unresolved": 0},
        },
    )
    write(
        run,
        "controls/consensus_exceptions.json",
        {"exceptions": [{"flag": "no_consensus", "is_handwritten": False}]},
    )
    write(
        run,
        "controls/records.json",
        records([document("a", header={"job_number": {"value": "12497"}}, handwriting=False)]),
    )
    write(run, "review/final_queue.json", {"summary": {"client_review_cards": 0}, "items": []})
    report = pipeline_plan.build(run, "least-spend", ("job_number",))
    assert report["client_questions"] == []
    assert "Client questions" not in pipeline_plan.render(report)


def test_the_cli_prints_without_writing_when_no_output_is_named(tmp_path, monkeypatch, capsys):
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    monkeypatch.setattr(sys, "argv", ["pipeline_plan.py", str(run)])
    assert pipeline_plan.main() == 0
    assert "Pipeline plan for" in capsys.readouterr().out
    assert sorted(path.name for path in run.rglob("*.json")) == ["consensus.json"]


def test_a_run_that_already_measured_corroboration_asks_the_sharper_question(tmp_path):
    """Once the evidence exists, the question is not whether to go and measure it.

    "Run the corroboration lane" and "decide whether corroboration may stand in
    for a second vendor" are different asks, and only the second is the client's.
    """
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    write(
        run,
        "controls/consensus_exceptions.json",
        {"exceptions": [{"flag": "single_engine", "is_handwritten": False}]},
    )
    write(
        run,
        "controls/corroboration.json",
        {
            "artifact_type": "independent_corroboration_v1",
            "summary": {"findings_examined": 58463, "corroborated": 44263, "tie_broken": 3315},
        },
    )
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    assert report["measured"]["corroborated_findings"] == 44263
    applied = next(
        entry for entry in report["plan"] if entry["command"] == "apply_corroboration.py"
    )
    assert "44263 findings already carry independent corroboration" in applied["because"]
    assert applied["decision"] == pipeline_plan.UNDECIDED
    question = next(
        item for item in report["client_questions"] if "second model vendor" in item["question"]
    )
    assert question["unblocks"] == "44263 corroborated findings"


def test_a_lane_that_already_ran_is_never_proposed_as_untouched_work(tmp_path):
    """This command shipped recommending both handwriting lanes on a run that had
    executed them twice, produced 3,319 annotations, and covered every one of the
    418 documents it cited as the reason to run them. Nothing in the plan said so.

    The verdict still follows the measurement -- a lane can genuinely need
    rerunning -- but retained output is always named, and the follow-up becomes
    the question that actually mattered on that run: a lane that ran and whose
    exceptions were never handed to the gate looks identical to one that never
    ran.
    """
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    write(
        run,
        "controls/records.json",
        records([document("a", handwriting=True), document("b", handwriting=True)]),
    )
    write(run, "controls/htr_decisions.json", {"summary": {"client_review_items": 3498}})

    report = pipeline_plan.build(run, "balanced", ("job_number",))
    assert "handwriting_review.py" in report["summary"]["lanes_with_retained_output"]
    entry = next(item for item in report["plan"] if item["command"] == "handwriting_review.py")
    assert "already run" in entry["because"]
    assert entry["already_run"] == [str(run / "controls" / "htr_decisions.json")]
    assert "reached the gate before rerunning" in entry["then"]

    # A lane with no retained output carries neither.
    ocr = next(item for item in report["plan"] if item["command"] == "google_handwriting_ocr.py")
    assert "already run" not in ocr["because"]
    assert "already_run" not in ocr


def test_every_command_the_plan_can_name_is_in_the_lane_catalogue(tmp_path):
    """A command absent from the catalogue can never be reported as already run."""
    run = tmp_path / "run"
    write(run, "controls/consensus.json", consensus_summary())
    write(run, "controls/records.json", records([document("a")]))
    write(run, "review/final_queue.json", {"summary": {"client_review_cards": 0}, "items": []})
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    named = {entry["command"] for entry in report["plan"]}
    assert named <= set(pipeline_plan.PLANNABLE_COMMANDS), sorted(
        named - set(pipeline_plan.PLANNABLE_COMMANDS)
    )


def test_the_plan_does_not_measure_its_own_earlier_output(tmp_path):
    """A plan is not evidence.

    Its `measured` block quotes the very keys discovery matches on, and being
    the newest file in the run it wins every slot it touches. A second run on
    the production corpus measured its own first run and reported 716 documents
    of records as absent.
    """
    run = tmp_path / "run"
    real = write(run, "controls/consensus.json", consensus_summary())
    report = pipeline_plan.build(run, "balanced", ("job_number",))
    write(run, "analytics/pipeline_plan_01.json", report)

    again = pipeline_plan.build(run, "balanced", ("job_number",))
    assert again["summary"]["measurement_sources"]["consensus"] == str(real)
    assert (
        again["measured"]["consensus_exception_rate"]
        == report["measured"]["consensus_exception_rate"]
    )
