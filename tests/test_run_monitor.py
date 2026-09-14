"""Tests for the read-only live run monitor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import run_monitor as monitor


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def google_response(model: str = "gemini-2.5-pro") -> dict:
    return {
        "model_version": model,
        "usage_metadata": {
            "prompt_token_count": 100,
            "candidates_token_count": 20,
            "thoughts_token_count": 10,
            "total_token_count": 130,
            "cached_content_token_count": 5,
        },
    }


def openai_response(model: str = "gpt-5.6-terra") -> dict:
    return {
        "model": model,
        "usage": {
            "input_tokens": 200,
            "output_tokens": 40,
            "total_tokens": 240,
            "input_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 30},
            "output_tokens_details": {"reasoning_tokens": 10},
        },
    }


def test_read_and_classify_helpers(tmp_path):
    valid = tmp_path / "valid.json"
    write_json(valid, {"value": 1})
    assert monitor._read_json(valid) == {"value": 1}
    assert monitor._read_json(tmp_path / "missing.json") is None
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    assert monitor._read_json(invalid) is None
    non_object = tmp_path / "list.json"
    write_json(non_object, [])
    assert monitor._read_json(non_object) is None
    assert monitor._raw_service(Path("consensus_primary_retry")) == "consensus_primary"
    assert monitor._raw_service(Path("unknown")) is None


def test_raw_directories_choose_fullest_pass(tmp_path):
    first = tmp_path / "consensus_primary" / "raw"
    retry = tmp_path / "consensus_primary_retry" / "raw"
    document_ai = tmp_path / "google_document_ai_raw"
    openrouter = tmp_path / "openrouter_raw"
    (tmp_path / "unknown_raw").mkdir()
    write_json(first / "one.json", {"response": google_response()})
    write_json(retry / "one.json", {"response": google_response()})
    write_json(retry / "two.json", {"response": google_response()})
    write_json(document_ai / "one.json", {"response": {}})
    write_json(openrouter / "one.json", {"response": openai_response()})
    selected = monitor._raw_directories(tmp_path)
    assert selected["consensus_primary"] == retry
    assert selected["google_document_ai"] == document_ai
    assert selected["llm_openrouter"] == openrouter
    (tmp_path / "not-a-raw-directory").write_text("x", encoding="utf-8")


def test_raw_directories_recognize_contained_adapter_lanes(tmp_path):
    """Live adapters retain role-named overlays under one contained raw root."""
    primary = tmp_path / "providers" / "raw" / "primary"
    primary_recovery = tmp_path / "providers" / "raw" / "primary_recovery_01"
    secondary = tmp_path / "providers" / "raw" / "secondary"
    write_json(primary / "one.json", {"response": openai_response()})
    write_json(primary_recovery / "one.json", {"response": openai_response()})
    write_json(primary_recovery / "two.json", {"response": openai_response()})
    write_json(secondary / "one.json", {"response": openai_response()})

    selected = monitor._raw_directories(tmp_path)

    assert selected["consensus_primary"] == primary_recovery
    assert selected["consensus_secondary"] == secondary


def test_usage_and_model_pricing():
    google, google_provider = monitor._usage(google_response())
    assert google_provider == "google"
    assert google["reasoning_tokens"] == 10
    openai, openai_provider = monitor._usage(openai_response())
    assert openai_provider == "openai"
    assert openai["cache_write_tokens"] == 30
    assert monitor._usage({}) == ({}, "unknown")
    assert monitor._price_for_model("gpt-5.6-terra-preview", monitor.MODEL_PRICES)
    assert monitor._price_for_model("unknown", monitor.MODEL_PRICES) is None
    # Anthropic list prices, so an Anthropic run is priced rather than reported
    # at zero. Sonnet 5 is exactly twice Haiku 4.5 on both input and output.
    haiku = monitor.MODEL_PRICES["claude-haiku-4-5-20251001"]
    sonnet = monitor.MODEL_PRICES["claude-sonnet-5"]
    assert (haiku["input"], haiku["output"]) == (1.00, 5.00)
    assert (sonnet["input"], sonnet["output"]) == (2.00, 10.00)
    for rates in (haiku, sonnet):
        assert rates["cached"] == rates["input"] * 0.1, "cache read is 0.1x input"
        assert rates["cache_write"] == rates["input"] * 1.25, "5-minute write is 1.25x input"
    assert monitor._price_for_model("nvidia/nemotron-3-super-120b-a12b:free", monitor.MODEL_PRICES)


def test_cost_rules_for_google_and_openai():
    google, provider = monitor._usage(google_response())
    google_cost = monitor._llm_cost(google, provider, "gemini-2.5-pro", monitor.MODEL_PRICES)
    assert google_cost == pytest.approx((100 * 1.25 + 30 * 10) / 1_000_000)
    openai, provider = monitor._usage(openai_response())
    openai_cost = monitor._llm_cost(openai, provider, "gpt-5.6-terra", monitor.MODEL_PRICES)
    assert openai_cost == pytest.approx((150 * 2 + 20 * 0.2 + 30 * 2.5 + 40 * 12) / 1_000_000)
    assert monitor._llm_cost_breakdown(
        openai, provider, "gpt-5.6-terra", monitor.MODEL_PRICES
    ) == pytest.approx(
        {
            "uncached_input_usd": 150 * 2 / 1_000_000,
            "cache_read_usd": 20 * 0.2 / 1_000_000,
            "cache_write_usd": 30 * 2.5 / 1_000_000,
            "output_usd": 40 * 12 / 1_000_000,
        }
    )
    assert monitor._llm_cost(openai, provider, "unknown", monitor.MODEL_PRICES) is None


def test_raw_report_counts_success_failure_and_document_ai_cost(tmp_path):
    raw = tmp_path / "google_document_ai_raw"
    write_json(raw / "ok.json", {"response": {"document": {}}})
    write_json(raw / "bad.json", {"error_type": "HTTP 500"})
    (raw / "invalid.json").write_text("{", encoding="utf-8")
    report = monitor._raw_report("google_document_ai", raw, monitor.MODEL_PRICES)
    assert report["successful"] == 1
    assert report["failed"] == 2
    assert report["estimated_cost_usd"] == pytest.approx(0.0015)


def test_raw_report_token_pricing_and_partial_status(tmp_path):
    raw = tmp_path / "consensus_primary" / "raw"
    write_json(raw / "google.json", {"response": google_response()})
    write_json(raw / "openai.json", {"response": openai_response("unpriced-model")})
    write_json(raw / "failure.json", {"error": "timeout"})
    report = monitor._raw_report("consensus_primary", raw, monitor.MODEL_PRICES)
    assert report["responses_seen"] == 3
    assert report["usage_records"] == 2
    assert report["failed"] == 1
    assert report["pricing_status"] == "partial"


def test_raw_report_exposes_all_four_cost_components(tmp_path):
    raw = tmp_path / "review_agent_raw"
    write_json(raw / "one.json", {"response": openai_response("gpt-5.6-terra")})
    report = monitor._raw_report("full_dataset_agent", raw, monitor.MODEL_PRICES)
    assert report["cost_breakdown_usd"] == pytest.approx(
        {
            "uncached_input_usd": 150 * 2 / 1_000_000,
            "cache_read_usd": 20 * 0.2 / 1_000_000,
            "cache_write_usd": 30 * 2.5 / 1_000_000,
            "output_usd": 40 * 12 / 1_000_000,
        }
    )
    assert report["provider_model_rollup"]["openai"]["models"]["gpt-5.6-terra"][
        "estimated_cost_usd"
    ] == pytest.approx(report["estimated_cost_usd"])


def test_snapshot_rolls_up_provider_and_actual_model_names(tmp_path):
    raw = tmp_path / "review_agent_raw"
    write_json(raw / "one.json", {"response": openai_response("gpt-5.6-terra")})
    write_json(raw / "two.json", {"response": openai_response("gpt-5.6-luna")})
    current = monitor.snapshot(tmp_path)
    openai = current["provider_model_rollup"]["openai"]
    assert set(openai["models"]) == {"gpt-5.6-terra", "gpt-5.6-luna"}
    assert openai["responses"] == 2
    assert openai["estimated_cost_usd"] == pytest.approx(
        sum(model["estimated_cost_usd"] for model in openai["models"].values()),
        abs=0.000002,
    )


def test_raw_report_rejects_non_object_response_and_summary_skips_bad_json(tmp_path):
    raw = tmp_path / "consensus_primary" / "raw"
    write_json(raw / "not-an-object.json", {"response": []})
    report = monitor._raw_report("consensus_primary", raw, monitor.MODEL_PRICES)
    assert report["failed"] == 1
    invalid = tmp_path / "bad-summary.json"
    invalid.write_text("{", encoding="utf-8")
    assert monitor._summary_services(tmp_path) == {}


def test_summary_services_and_page_count(tmp_path):
    write_json(
        tmp_path / "address.json",
        {"summary": {"external_validation": {"requests_sent": 2}}},
    )
    write_json(
        tmp_path / "schema.json",
        {"google_places": {"requests_sent": 3}},
    )
    assert monitor._summary_services(tmp_path)["google_address_validation"]["requests"] == 2
    assert monitor._summary_services(tmp_path)["google_places"]["requests"] == 3
    manifest = tmp_path / "ingestion_manifest.json"
    write_json(manifest, {"summary": {"pages": 4}})
    assert monitor._page_count(tmp_path) == 4
    write_json(manifest, {"pages": [{}, {}]})
    assert monitor._page_count(tmp_path) == 2
    write_json(manifest, {"summary": {"pages": "unknown"}})
    assert monitor._page_count(tmp_path) is None


def test_snapshot_progress_and_atomic_write(tmp_path):
    manifest = tmp_path / "ingestion_manifest.json"
    write_json(manifest, {"pages": [{}, {}]})
    raw = tmp_path / "consensus_primary" / "raw"
    write_json(raw / "one.json", {"response": google_response("gemini-2.5-flash")})
    write_json(raw / "two.json", {"response": google_response("gemini-2.5-flash")})
    write_json(tmp_path / "address.json", {"external_validation": {"requests_sent": 1}})
    current = monitor.snapshot(tmp_path)
    assert current["expected_pages"] == 2
    assert current["progress"]["complete"] is True
    assert current["spend"]["estimated_cost_usd"] > 0
    output = monitor.write_snapshot(current, tmp_path / "monitor.json")
    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == "1.0"


def test_snapshot_without_services_and_cli_once(tmp_path, capsys):
    current = monitor.snapshot(tmp_path, expected_pages=2)
    assert current["progress"]["percent"] == 0.0
    assert current["progress"]["complete"] is False
    assert monitor.main([str(tmp_path), "--expected-pages", "2", "--once"]) == 0
    assert "0.0% complete" in capsys.readouterr().out
    price_file = tmp_path / "prices.json"
    write_json(price_file, {})
    output = tmp_path / "snapshot.json"
    assert (
        monitor.main(
            [str(tmp_path), "--price-file", str(price_file), "--out", str(output), "--once"]
        )
        == 0
    )
    assert output.exists()


def test_cli_rejects_non_object_price_file(tmp_path):
    price_file = tmp_path / "prices.json"
    write_json(price_file, [])
    with pytest.raises(SystemExit, match="price-file"):
        monitor.main([str(tmp_path), "--price-file", str(price_file), "--once"])


def test_line_and_monitor_loop_cover_service_display_and_sleep(tmp_path, monkeypatch):
    line = monitor._line(
        {
            "progress": {"percent": 50.0},
            "spend": {"estimated_cost_usd": 1.25},
            "expected_pages": 2,
            "services": {
                "openai": {"successful": 1},
                "google_places": {"requests": 0},
            },
        }
    )
    assert "openai 1/2" in line and "google_places 0" in line

    monkeypatch.setattr(monitor, "_raw_directories", lambda _root: {"custom": tmp_path})
    monkeypatch.setattr(
        monitor.time, "sleep", lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt)
    )
    with pytest.raises(KeyboardInterrupt):
        monitor.main([str(tmp_path), "--interval", "0.1"])
