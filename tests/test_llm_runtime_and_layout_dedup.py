import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

runtime = importlib.import_module("llm_runtime")
dedup = importlib.import_module("layout_dedup")


def test_cache_retry_parallel_and_validation(tmp_path):
    cache = runtime.ResponseCache(tmp_path / "cache")
    key = runtime.cache_key({"page_sha256": "abc", "model": "m"})
    assert cache.read(key) is None
    cache.write(key, {"ok": True})
    cache.write(key, {"ok": False})
    # A cached entry is stamped so a replayed provider response is distinguishable
    # from a fresh one in the retained raw artifact.
    cached = cache.read(key)
    assert cached["ok"] is True
    assert cached["written_at"].endswith("+00:00")
    assert runtime.ResponseCache().read(key) is None
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("retry")
        return "done"

    sleeps = []
    assert runtime.retry_call(flaky, 2, 2, sleeps.append) == "done"
    assert runtime.retry_call(lambda: "ok", 2) == "ok"
    with pytest.raises(RuntimeError):
        runtime.retry_call(lambda: (_ for _ in ()).throw(RuntimeError("done")), 0)
    with pytest.raises(RuntimeError):
        runtime.retry_call(lambda: (_ for _ in ()).throw(RuntimeError("done")), 1, 0)
    assert sleeps == [2, 4]
    with pytest.raises(ValueError):
        runtime.retry_call(lambda: None, -1)
    with pytest.raises(ValueError):
        runtime.retry_call(lambda: None, 0, -1)
    with pytest.raises(ValueError):
        runtime.retry_call(lambda: None, 0, 2, max_backoff_seconds=1)

    class RateLimitError(Exception):
        status_code = 429

        def __init__(self):
            self.response = type("Response", (), {"headers": {"Retry-After": "4"}})()

    retry_attempts = []
    retry_sleeps = []
    with pytest.raises(RateLimitError):
        runtime.retry_call(
            lambda: (_ for _ in ()).throw(RateLimitError()),
            1,
            2,
            retry_sleeps.append,
            max_backoff_seconds=3,
            jitter=True,
            uniform=lambda low, high: retry_attempts.append((low, high)) or high,
            retryable=runtime.retryable_error,
            on_retry=lambda attempt, error, delay: retry_attempts.append(
                (attempt, type(error).__name__, delay)
            ),
        )
    assert retry_sleeps == [3]
    assert retry_attempts == [(1, "RateLimitError", 3)]
    assert runtime.retry_after_seconds(type("NoHeader", (), {})()) is None
    assert (
        runtime.retry_after_seconds(
            type(
                "BadHeader", (), {"response": type("R", (), {"headers": {"Retry-After": "x"}})()}
            )()
        )
        is None
    )
    assert runtime.parallel_map([1, 2, 3], lambda value: value * 2, 2) == [2, 4, 6]
    with pytest.raises(ValueError):
        runtime.parallel_map([], lambda value: value, 0)


def test_cross_process_throttle_and_retry_after_date(tmp_path):
    assert runtime.RequestThrottle(None).acquire() == 0.0
    sleeps = []
    throttle = runtime.RequestThrottle(tmp_path / "throttle", "openai", 1.0, sleeps.append)
    assert throttle.acquire() == 0.0
    assert throttle.acquire() >= 0.0
    assert sleeps
    date_error = type(
        "DateRetry",
        (),
        {
            "response": type(
                "Response", (), {"headers": {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}}
            )()
        },
    )()
    assert runtime.retry_after_seconds(date_error) == 0.0
    with pytest.raises(ValueError):
        runtime.RequestThrottle(tmp_path / "bad", min_interval=-1)
    with pytest.raises(ValueError, match="rate limits"):
        runtime.RequestThrottle(tmp_path / "bad-rate", requests_per_minute=-1)
    with pytest.raises(ValueError, match="safety ratio"):
        runtime.RequestThrottle(tmp_path / "bad-safety", safety_ratio=0)


def test_provider_quota_circuit_fails_fast_and_can_be_disabled(monkeypatch):
    class QuotaError(Exception):
        status_code = 429

    provider = "quota-test-provider"
    runtime.reset_provider_quota_circuits()
    calls = []

    def exhausted():
        calls.append("request")
        raise QuotaError("free-models-per-day-high-balance quota exhausted")

    monkeypatch.setenv("LLM_FAIL_FAST_ON_PROVIDER_QUOTA", "true")
    with pytest.raises(runtime.ProviderQuotaExhausted, match="quota exhausted"):
        runtime.retry_call(exhausted, 4, provider=provider)
    with pytest.raises(runtime.ProviderQuotaExhausted, match="circuit is open"):
        runtime.retry_call(exhausted, 4, provider=provider)
    assert calls == ["request"]

    runtime.reset_provider_quota_circuits()
    monkeypatch.setenv("LLM_FAIL_FAST_ON_PROVIDER_QUOTA", "false")
    attempts = []

    def transient_quota():
        attempts.append(1)
        raise QuotaError("free-models-per-day-high-balance quota exhausted")

    with pytest.raises(QuotaError):
        runtime.retry_call(transient_quota, 1, provider=provider)
    assert attempts == [1, 1]

    monkeypatch.setenv("LLM_FAIL_FAST_ON_PROVIDER_QUOTA", "maybe")
    with pytest.raises(ValueError, match="LLM_FAIL_FAST_ON_PROVIDER_QUOTA"):
        runtime.retry_call(lambda: "unreachable", 0, provider="invalid-flag-provider")


def test_provider_throttle_reserves_tokens_and_fails_closed(tmp_path):
    throttle = runtime.RequestThrottle(
        tmp_path / "budget",
        "openai",
        requests_per_minute=500,
        tokens_per_minute=200,
        max_request_tokens=200,
        output_reserve=10,
        safety_ratio=0.8,
        sleep=lambda _seconds: None,
    )
    assert throttle.acquire(request_tokens=20) >= 0.0
    with pytest.raises(runtime.RateLimitConfigurationError, match="max request tokens"):
        throttle.acquire(request_tokens=200)
    with pytest.raises(runtime.RateLimitConfigurationError, match="safe token budget"):
        throttle.acquire(request_tokens=151)
    with pytest.raises(ValueError, match="estimated request tokens"):
        throttle.acquire(request_tokens=-1)
    with pytest.raises(ValueError, match="model context"):
        runtime.RequestThrottle(
            tmp_path / "context", max_request_tokens=100, output_reserve=10, context_tokens=100
        )
    with pytest.raises(ValueError, match="model maximum"):
        runtime.RequestThrottle(tmp_path / "output", output_reserve=11, model_max_output_tokens=10)


def test_provider_throttle_reads_provider_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_THROTTLE_DIR", str(tmp_path / "env-budget"))
    monkeypatch.setenv("LLM_MIN_REQUEST_INTERVAL_SECONDS", "0.1")
    monkeypatch.setenv("LLM_RATE_LIMIT_SAFETY_RATIO", "0.5")
    monkeypatch.setenv("OPENAI_RATE_LIMIT_REQUESTS_PER_MINUTE", "500")
    monkeypatch.setenv("OPENAI_RATE_LIMIT_TOKENS_PER_MINUTE", "200000")
    monkeypatch.setenv("OPENAI_MAX_REQUEST_TOKENS", "100000")
    monkeypatch.setenv("OPENAI_OUTPUT_TOKEN_RESERVE", "4096")
    throttle = runtime.request_throttle("openai")
    assert throttle.requests_per_minute == 500
    assert throttle.tokens_per_minute == 200000
    assert throttle.max_request_tokens == 100000
    assert throttle.output_reserve == 4096
    assert throttle.safety_ratio == 0.5
    monkeypatch.setenv("GOOGLE_VERTEX_AI_RATE_LIMIT_REQUESTS_PER_MINUTE", "60")
    monkeypatch.setenv("GOOGLE_VERTEX_AI_RATE_LIMIT_TOKENS_PER_MINUTE", "100000")
    monkeypatch.setenv("GOOGLE_VERTEX_AI_MAX_REQUEST_TOKENS", "200000")
    monkeypatch.setenv("GOOGLE_VERTEX_AI_OUTPUT_TOKEN_RESERVE", "4096")
    monkeypatch.setenv("GOOGLE_VERTEX_AI_MODEL_CONTEXT_TOKENS", "1048576")
    monkeypatch.setenv("GOOGLE_VERTEX_AI_MODEL_MAX_OUTPUT_TOKENS", "65536")
    google = runtime.request_throttle("google")
    assert google.context_tokens == 1048576
    assert google.model_max_output_tokens == 65536


def test_estimate_tokens_is_stable_and_positive():
    assert runtime.estimate_tokens("") == 1
    assert runtime.estimate_tokens({"b": 2, "a": 1}) == runtime.estimate_tokens({"a": 1, "b": 2})


def test_adaptive_byte_batches_retains_oversized_and_capped_items():
    items = ["a", "bb", "ccc", "dddd"]
    batches, oversized, deferred = runtime.adaptive_byte_batches(
        items, lambda group: 5 - sum(len(item) for item in group), 3, 1
    )
    assert batches == [["a", "bb"]]
    assert oversized == []
    assert deferred == ["ccc", "dddd"]
    batches, oversized, deferred = runtime.adaptive_byte_batches(
        items, lambda group: 2 - sum(len(item) for item in group), 3
    )
    assert batches == [["a"], ["bb"]]
    assert oversized == ["ccc", "dddd"]
    assert deferred == []
    with pytest.raises(ValueError, match="max_items"):
        runtime.adaptive_byte_batches([], lambda _: 0, 0)
    with pytest.raises(ValueError, match="max_batches"):
        runtime.adaptive_byte_batches([], lambda _: 0, 1, -1)


def test_layout_dedup_preserves_first_row_and_flags_duplicates():
    columns = [{"key": "name", "display_label": "Name"}, {"key": "ref", "display_label": "Ref"}]
    proposals = [
        {
            "page_id": "p1",
            "row_proposals": [{"name": " Acme ", "ref": "A-1"}, {"name": "Beta", "ref": "B-2"}],
        },
        {"page_id": "p2", "row_proposals": [{"name": "acme", "ref": "A-1"}]},
    ]
    labels, unique, duplicates = dedup.deduplicate(proposals, columns)
    assert labels == ["Name", "Ref"]
    assert unique == [{"Name": " Acme ", "Ref": "A-1"}, {"Name": "Beta", "Ref": "B-2"}]
    assert duplicates[0]["review_disposition"] == "client_review_required"
    assert duplicates[0]["representative_page_id"] == "p1"
    assert dedup.normalize(None) == ""


def test_layout_dedup_cli_outputs_review_csv(tmp_path):
    layout = tmp_path / "layout.json"
    proposals = tmp_path / "proposals.json"
    layout.write_text(
        json.dumps({"layout_id": "x", "columns": [{"key": "name", "display_label": "Name"}]})
    )
    proposals.write_text(
        json.dumps([{"page_id": "p1", "row_proposals": [{"name": "A"}, {"name": "a"}]}])
    )
    unique = tmp_path / "unique.csv"
    duplicates = tmp_path / "duplicates.csv"
    assert dedup.main is not None
    labels, rows, candidates = dedup.deduplicate(
        json.loads(proposals.read_text()), dedup.load_layout(layout)
    )
    dedup.write_csv(unique, labels, rows)
    dedup.write_csv(
        duplicates,
        [
            "representative_page_id",
            "representative_row_index",
            "representative_row_number",
            "duplicate_page_id",
            "duplicate_row_index",
            "duplicate_row_number",
            "match_basis",
            "review_disposition",
        ],
        candidates,
    )
    assert unique.read_text().splitlines() == ['"Name"', '"A"']
    assert "client_review_required" in duplicates.read_text()
    with pytest.raises(ValueError):
        dedup.write_csv(unique, labels, rows)
    bad_layout = tmp_path / "bad-layout.json"
    bad_layout.write_text(json.dumps({"layout_id": "x", "columns": []}))
    with pytest.raises(ValueError):
        dedup.load_layout(bad_layout)


def test_layout_dedup_main(tmp_path, monkeypatch, capsys):
    layout = tmp_path / "layout.json"
    proposals = tmp_path / "proposals.json"
    layout.write_text(
        json.dumps({"layout_id": "x", "columns": [{"key": "name", "display_label": "Name"}]})
    )
    proposals.write_text(json.dumps([{"page_id": "p1", "row_proposals": [{"name": "A"}]}]))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "layout_dedup.py",
            str(proposals),
            str(layout),
            "--unique-out",
            str(tmp_path / "u.csv"),
            "--duplicates-out",
            str(tmp_path / "d.csv"),
        ],
    )
    dedup.main()
    assert json.loads(capsys.readouterr().out)["unique_rows"] == 1


def test_every_llm_provider_carries_a_request_budget():
    """A lane must behave the same whichever configured provider serves it.

    Anthropic was the one provider with no `PROVIDER_LIMITS` entry, so
    `request_throttle("anthropic")` returned a throttle with no requests-per-
    minute, no tokens-per-minute, and -- the costly part -- no request-size
    preflight. Moving a consensus lane and five buddy lanes onto that provider
    silently removed the guard that turns an oversized packet into a refusal
    before the call instead of a retained provider exception.
    """
    import runtime_config

    missing = sorted(runtime_config.LLM_PROVIDERS - set(runtime.PROVIDER_LIMITS))
    assert missing == [], missing
    for provider, settings in runtime.PROVIDER_LIMITS.items():
        for key in ("requests", "tokens", "max_request_tokens", "output_reserve"):
            assert settings.get(key), (provider, key)


def test_an_anthropic_lane_is_paced_and_size_checked(monkeypatch):
    """The configured Anthropic budget must reach the throttle, not a default."""
    monkeypatch.setenv("ANTHROPIC_RATE_LIMIT_REQUESTS_PER_MINUTE", "500")
    monkeypatch.setenv("ANTHROPIC_RATE_LIMIT_TOKENS_PER_MINUTE", "1000000")
    monkeypatch.setenv("ANTHROPIC_MAX_REQUEST_TOKENS", "160000")
    monkeypatch.setenv("ANTHROPIC_OUTPUT_TOKEN_RESERVE", "32768")

    throttle = runtime.request_throttle("anthropic")

    assert throttle.requests_per_minute == 500
    assert throttle.tokens_per_minute == 1000000
    assert throttle.max_request_tokens == 160000
    assert throttle.output_reserve == 32768
