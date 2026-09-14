"""Tests for the run-level provider usage report."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

usage_report = importlib.import_module("llm_usage_report")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def google_response():
    return {
        "model_version": "gemini-test",
        "usage_metadata": {
            "prompt_token_count": 10,
            "candidates_token_count": 4,
            "thoughts_token_count": 3,
            "total_token_count": 17,
            "cached_content_token_count": 2,
            "traffic_type": "ON_DEMAND",
        },
    }


def openai_response():
    return {
        "model": "gpt-test",
        "billing": {"payer": "developer"},
        "service_tier": "default",
        "usage": {
            "input_tokens": 20,
            "output_tokens": 8,
            "total_tokens": 28,
            "input_tokens_details": {"cached_tokens": 5, "cache_write_tokens": 6},
            "output_tokens_details": {"reasoning_tokens": 2},
        },
    }


def test_response_usage_and_lane_names():
    google, provider = usage_report.response_usage(google_response())
    assert provider == "google" and google["total_tokens"] == 17
    openai, provider = usage_report.response_usage(openai_response())
    assert provider == "openai" and openai["cache_write_tokens"] == 6
    assert usage_report.response_usage({}) == (None, "unknown")
    run = Path("/run")
    # The lane is where the directory sits, not a fixed table of names: a run
    # names its own raw directories through --raw-dir, and every lane of a real
    # 716-page run reported as "unclassified:" while that table was the source.
    assert usage_report.lane_for_directory(run, run / "providers/raw/primary_02") == (
        "providers/primary_02",
        "",
    )
    assert usage_report.lane_for_directory(run, run / "controls/raw/discovery_03") == (
        "controls/discovery_03",
        "",
    )
    # Per-page lanes: every page and role of one attempt is that one attempt.
    assert usage_report.lane_for_directory(
        run, run / "tables/tables_t01/pages/000001_page/raw_profile"
    ) == ("tables/tables_t01", "profile")
    assert usage_report.lane_for_directory(
        run, run / "tables/tables_t01/pages/000002_page/raw_audit"
    ) == ("tables/tables_t01", "audit")
    # A `<lane>_raw` directory at the run root has nothing above it to name the
    # lane, so its own name is kept -- still the name the operator chose.
    assert usage_report.lane_for_directory(run, run / "client_review_llm_raw") == (
        "client_review_llm_raw",
        "client_review_llm",
    )
    assert usage_report.is_raw_directory(Path("raw_rows"))
    assert usage_report.is_raw_directory(Path("something_raw"))
    assert not usage_report.is_raw_directory(Path("providers"))


def test_every_retained_attempt_is_counted_not_only_the_fullest(tmp_path):
    """A run pays for the attempts it abandons; hiding them understates the bill.

    This once selected the fullest directory per lane, which is right for asking
    what a run stands behind and wrong for asking what it cost.
    """
    small = tmp_path / "providers" / "raw" / "primary"
    large = tmp_path / "providers" / "raw" / "primary_02"
    write_json(small / "one.json", {"response": google_response()})
    write_json(large / "one.json", {"response": google_response()})
    write_json(large / "two.json", {"response": google_response()})
    found = usage_report.raw_directories(tmp_path)
    assert set(found) == {"providers/primary", "providers/primary_02"}
    assert [path for path, _role in found["providers/primary"]] == [small]

    (tmp_path / "raw").write_text("not a directory")
    (tmp_path / "empty" / "raw").mkdir(parents=True)
    assert "empty" not in usage_report.raw_directories(tmp_path)

    direct_raw = tmp_path / "client_review_llm_raw"
    write_json(direct_raw / "response.json", {"response": openai_response()})
    assert usage_report.raw_directories(tmp_path)["client_review_llm_raw"] == [
        (direct_raw, "client_review_llm")
    ]

    assert usage_report.exception_counts(tmp_path / "missing" / "raw")["exception_count"] == 0
    write_json(
        large.parent / "exceptions.json", {"exceptions": [{"error_type": "BadRequestError"}, {}]}
    )
    counts = usage_report.exception_counts(large)
    assert counts == {
        "exception_count": 2,
        "provider_failure_count": 1,
        "review_finding_count": 1,
        "rate_limit_count": 0,
        "retry_attempts": 0,
        "retry_delay_seconds": 0.0,
    }
    write_json(large.parent / "z-exceptions.json", {"exceptions": "bad"})
    assert usage_report.exception_counts(large)["exception_count"] == 0
    (large.parent / "z-exceptions.json").write_text("{")
    assert usage_report.exception_counts(large)["exception_count"] == 0


def test_a_per_page_lane_is_summed_across_every_page_and_role(tmp_path):
    """Reading one directory reported a single page of a 716-page lane as its cost."""
    for page in ("000001_a", "000002_b", "000003_c"):
        for role in ("raw_profile", "raw_rows", "raw_audit"):
            write_json(
                tmp_path / "tables" / "tables_t01" / "pages" / page / role / "r.json",
                {"response": openai_response()},
            )
    found = usage_report.raw_directories(tmp_path)
    assert list(found) == ["tables/tables_t01"]
    assert len(found["tables/tables_t01"]) == 9
    report = usage_report.lane_report("tables/tables_t01", found["tables/tables_t01"])
    assert report["raw_response_count"] == 9
    assert report["raw_directories"] == 9
    assert report["roles"] == {"profile": 3, "rows": 3, "audit": 3}
    single = usage_report.lane_report(
        "one", [(tmp_path / "tables/tables_t01/pages/000001_a/raw_profile", "profile")]
    )
    assert report["tokens"]["total_tokens"] == 9 * single["tokens"]["total_tokens"]


def test_page_count_and_lane_report(tmp_path):
    manifest = tmp_path / "intake" / "ingestion_manifest.json"
    write_json(manifest, {"summary": {"pages": 4}})
    assert usage_report.page_count(tmp_path) == 4
    manifest.write_text(json.dumps({"pages": [{}, {}]}))
    assert usage_report.page_count(tmp_path) == 2
    manifest.write_text("{")
    assert usage_report.page_count(tmp_path) is None
    manifest.write_text(json.dumps({"summary": {"pages": "unknown"}}))
    assert usage_report.page_count(tmp_path) is None

    raw = tmp_path / "client_review_llm" / "raw"
    write_json(raw / "google.json", {"response": google_response()})
    write_json(raw / "openai.json", {"response": openai_response()})
    write_json(raw / "error.json", {"error": "timeout"})
    (raw / "bad.json").write_text("{")
    write_json(raw / "list-response.json", {"response": []})
    no_model = google_response()
    no_model.pop("model_version")
    write_json(raw / "no-model.json", {"response": no_model})
    write_json(
        raw.parent / "client_review_llm.json",
        {"summary": {"retry_telemetry": {"retry_attempts": 2, "rate_limit_attempts": 1}}},
    )
    (raw.parent / "a-bad.json").write_text("{")
    write_json(raw.parent / "b-no-telemetry.json", {"summary": {}})
    write_json(raw.parent / "c-list-summary.json", {"summary": []})
    write_json(raw.parent / "d-list-artifact.json", [])
    result = usage_report.lane_report("client_review_reasoning", [(raw, "")])
    assert result["raw_response_count"] == 6
    assert result["usage_metadata_count"] == 3
    assert result["tokens"]["total_tokens"] == 62
    assert result["provider_spend_metadata"]["billing_payer"] == "developer"
    assert result["retry_telemetry"]["rate_limit_attempts"] == 1
    isolated = tmp_path / "isolated" / "raw"
    write_json(isolated.parent / "list-artifact.json", [])
    assert usage_report.lane_retry_telemetry(isolated) == {}


def test_build_and_write_report_no_llm_and_no_clobber(tmp_path):
    report = usage_report.build_report(tmp_path)
    assert report["current_run"]["lanes"] == []
    out = tmp_path / "llm_usage_report.json"
    assert usage_report.write_report(tmp_path, out) == out
    assert json.loads(out.read_text())["current_run"]["tokens"] == {}
    with pytest.raises(FileExistsError):
        usage_report.write_report(tmp_path, out)


def test_build_report_with_lane_and_cli_success(monkeypatch, tmp_path, capsys):
    raw = tmp_path / "cross_record" / "raw"
    write_json(raw / "response.json", {"response": openai_response()})
    report = usage_report.build_report(tmp_path)
    assert report["current_run"]["tokens"]["total_tokens"] == 28
    out = tmp_path / "cli-report.json"
    monkeypatch.setattr(sys, "argv", [usage_report.__file__, str(tmp_path), "--out", str(out)])
    usage_report.main()
    assert "usage_report" in capsys.readouterr().out


def test_cli_error_for_existing_output(monkeypatch, tmp_path):
    out = tmp_path / "report.json"
    out.write_text("{}")
    monkeypatch.setattr(sys, "argv", [usage_report.__file__, str(tmp_path), "--out", str(out)])
    with pytest.raises(SystemExit, match="LLM usage report failed"):
        usage_report.main()


def test_chat_completions_usage_is_counted_not_reported_as_zero():
    """OpenRouter returns Chat Completions names for the same numbers.

    Reading only the Responses names made a whole vendor's spend report as zero
    rather than as unknown. On a real 18-page run the secondary lane was the
    larger of the two, and it was reported free.
    """
    usage, dialect = usage_report.response_usage(
        {
            "usage": {
                "prompt_tokens": 21971,
                "completion_tokens": 14174,
                "total_tokens": 36145,
                "completion_tokens_details": {"reasoning_tokens": 10281},
            }
        }
    )
    assert dialect == "openai_chat_completions"
    assert usage["prompt_tokens"] == 21971
    assert usage["output_tokens"] == 14174
    assert usage["reasoning_tokens"] == 10281

    # The Responses dialect still wins when it is the one present.
    usage, dialect = usage_report.response_usage(
        {"usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
    )
    assert dialect == "openai" and usage["prompt_tokens"] == 10

    # A usage object naming neither is unknown, not silently zero.
    assert usage_report.response_usage({"usage": {"something_else": 1}}) == (None, "unknown")


def test_a_lane_directory_beneath_raw_is_discovered(tmp_path):
    """The documented layout puts one directory per lane under a shared raw/."""
    run = tmp_path / "run"
    for lane in ("primary", "secondary"):
        directory = run / "providers" / "raw" / lane
        directory.mkdir(parents=True)
        (directory / "page.json").write_text(
            json.dumps({"response": {"usage": {"prompt_tokens": 1, "completion_tokens": 2}}})
        )
    # A directory literally named "raw" is named by what contains it.
    htr = run / "htr" / "raw"
    htr.mkdir(parents=True)
    (htr / "page.json").write_text(json.dumps({"response": {}}))

    found = usage_report.raw_directories(run)
    # Each lane keeps its own name. Folding these into one "providers" lane would
    # report a whole run's extraction as a single undifferentiated cost.
    assert "providers/primary" in found
    assert "providers/secondary" in found
    assert "htr" in found
    # An empty directory contributes no lane at all.
    (run / "empty" / "raw").mkdir(parents=True)
    assert "empty" not in usage_report.raw_directories(run)
