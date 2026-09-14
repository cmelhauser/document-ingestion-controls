#!/usr/bin/env python3
"""Monitor run progress and estimated spend from retained run artifacts.

The monitor is intentionally read-only with respect to provider services. It
polls a run directory, counts retained responses and service summaries, and
writes an atomic JSON snapshot. Credentials and response content are never
printed or copied into the snapshot.

Examples
--------
One snapshot::

    python scripts/run_monitor.py RUN_DIR --expected-pages 51 --once

Live terminal monitor::

    python scripts/run_monitor.py RUN_DIR --expected-pages 51 --interval 2 \
        --out RUN_DIR/progress_monitor.json --stop-when-complete

Pricing is an estimate. Override the built-in rates with ``--price-file`` when
the billing account, SKU, or provider contract differs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from cli_help import apply_shared_help

MODEL_PRICES: dict[str, dict[str, float]] = {
    "gpt-5.6-luna": {
        "input": 0.20,
        "cached": 0.02,
        "cache_write": 0.25,
        "output": 1.20,
    },
    "gpt-5.6-terra": {
        "input": 2.00,
        "cached": 0.20,
        "cache_write": 2.50,
        "output": 12.00,
    },
    "gpt-5.6-sol": {
        "input": 5.00,
        "cached": 0.50,
        "cache_write": 6.25,
        "output": 30.00,
    },
    # Anthropic list prices, USD per million tokens, from
    # platform.claude.com/docs/en/about-claude/pricing. Cache write is the
    # 5-minute rate (1.25x input); cache read is 0.1x input.
    "claude-haiku-4-5-20251001": {
        "input": 1.00,
        "cached": 0.10,
        "cache_write": 1.25,
        "output": 5.00,
    },
    "claude-sonnet-5": {
        "input": 2.00,
        "cached": 0.20,
        "cache_write": 2.50,
        "output": 10.00,
    },
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    # OpenRouter's selected :free models have no model-token charge. Keep the
    # entries explicit so the monitor reports $0 rather than unknown pricing;
    # rate limits and any account-level fees still require provider billing
    # reconciliation.
    "nvidia/nemotron-3-super-120b-a12b:free": {
        "input": 0.0,
        "cached": 0.0,
        "cache_write": 0.0,
        "output": 0.0,
    },
    "nvidia/nemotron-3-ultra-550b-a55b:free": {
        "input": 0.0,
        "cached": 0.0,
        "cache_write": 0.0,
        "output": 0.0,
    },
}

SERVICE_PRICES_USD_PER_1000: dict[str, float] = {
    "google_document_ai": 1.50,
    "google_address_validation": 17.00,
    "google_places_text_search_pro": 32.00,
}

RAW_PREFIXES: tuple[tuple[str, str], ...] = (
    ("consensus_primary", "consensus_primary"),
    ("consensus_secondary", "consensus_secondary"),
    # ``llm_adapter.py`` writes its contained raw responses below
    # ``providers/raw/primary*`` and ``providers/raw/secondary*``.  Keep those
    # names distinct from provider families: these are the two consensus roles,
    # not a claim that their transport itself supplies independence.
    ("primary", "consensus_primary"),
    ("secondary", "consensus_secondary"),
    ("client_review_llm", "client_review_reasoning"),
    ("cross_record", "cross_record_reasoning"),
    ("review_agent", "full_dataset_agent"),
    ("google_document_ai", "google_document_ai"),
    ("document_ai", "google_document_ai"),
    ("openai_raw", "llm_openai"),
    ("google_raw", "llm_google"),
    ("openrouter_raw", "llm_openrouter"),
)
PAGE_SERVICES = frozenset(
    {
        "consensus_primary",
        "consensus_secondary",
        "client_review_reasoning",
        "google_document_ai",
        "llm_openai",
        "llm_google",
        "llm_openrouter",
    }
)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _raw_service(path: Path) -> str | None:
    name = path.name.casefold()
    for prefix, service in RAW_PREFIXES:
        if name.startswith(prefix):
            return service
    return None


def _raw_directories(run_dir: Path) -> dict[str, Path]:
    candidates: dict[str, list[Path]] = {}
    for raw_dir in (path for path in run_dir.rglob("*") if path.is_dir()):
        if not raw_dir.is_dir():  # pragma: no cover - filtered by the generator above
            continue
        is_legacy_raw = raw_dir.name == "raw" or raw_dir.name.casefold().endswith("_raw")
        is_contained_adapter_raw = (
            raw_dir.parent.name == "raw" and raw_dir.parent.parent.name == "providers"
        )
        if not (is_legacy_raw or is_contained_adapter_raw):
            continue
        service = _raw_service(raw_dir if raw_dir.name != "raw" else raw_dir.parent)
        if service is not None:
            candidates.setdefault(service, []).append(raw_dir)
    return {
        service: max(
            paths, key=lambda item: (len(tuple(item.glob("*.json"))), item.stat().st_mtime)
        )
        for service, paths in candidates.items()
    }


def _usage(response: dict[str, Any]) -> tuple[dict[str, int], str]:
    google = response.get("usage_metadata")
    if isinstance(google, dict):
        return {
            "prompt_tokens": int(google.get("prompt_token_count") or 0),
            "output_tokens": int(google.get("candidates_token_count") or 0),
            "reasoning_tokens": int(google.get("thoughts_token_count") or 0),
            "total_tokens": int(google.get("total_token_count") or 0),
            "cached_tokens": int(google.get("cached_content_token_count") or 0),
            "cache_write_tokens": 0,
        }, "google"
    openai = response.get("usage")
    if isinstance(openai, dict):
        input_details = openai.get("input_tokens_details") or {}
        output_details = openai.get("output_tokens_details") or {}
        return {
            "prompt_tokens": int(openai.get("input_tokens") or 0),
            "output_tokens": int(openai.get("output_tokens") or 0),
            "reasoning_tokens": int(output_details.get("reasoning_tokens") or 0),
            "total_tokens": int(openai.get("total_tokens") or 0),
            "cached_tokens": int(input_details.get("cached_tokens") or 0),
            "cache_write_tokens": int(input_details.get("cache_write_tokens") or 0),
        }, "openai"
    return {}, "unknown"


def _price_for_model(model: str, prices: dict[str, dict[str, float]]) -> dict[str, float] | None:
    lowered = model.casefold()
    for name, price in prices.items():
        if name.casefold() in lowered:
            return price
    return None


def _llm_cost(
    usage: dict[str, int],
    provider: str,
    model: str,
    prices: dict[str, dict[str, float]],
) -> float | None:
    breakdown = _llm_cost_breakdown(usage, provider, model, prices)
    if breakdown is None:
        return None
    return sum(breakdown.values())


def _llm_cost_breakdown(
    usage: dict[str, int],
    provider: str,
    model: str,
    prices: dict[str, dict[str, float]],
) -> dict[str, float] | None:
    """Price each provider-reported token category exactly once.

    OpenAI's ``input_tokens`` is the total input-token count.  Its cached and
    cache-write fields are subcategories of that total, so they are removed
    before pricing the remaining uncached input.  This prevents cache reads or
    writes from being charged a second time at the standard input rate.
    """
    price = _price_for_model(model, prices)
    if price is None:
        return None
    prompt = max(int(usage.get("prompt_tokens", 0)), 0)
    cached = min(max(int(usage.get("cached_tokens", 0)), 0), prompt)
    cache_write = min(
        max(int(usage.get("cache_write_tokens", 0)), 0),
        max(prompt - cached, 0),
    )
    uncached = max(prompt - cached - cache_write, 0)
    output_tokens = max(int(usage.get("output_tokens", 0)), 0)
    if provider == "google":
        output_tokens += max(int(usage.get("reasoning_tokens", 0)), 0)
    return {
        "uncached_input_usd": uncached * price["input"] / 1_000_000,
        "cache_read_usd": cached * price.get("cached", price["input"]) / 1_000_000,
        "cache_write_usd": cache_write * price.get("cache_write", price["input"]) / 1_000_000,
        "output_usd": output_tokens * price["output"] / 1_000_000,
    }


def _raw_report(service: str, raw_dir: Path, prices: dict[str, dict[str, float]]) -> dict[str, Any]:
    files = sorted(raw_dir.glob("*.json"))
    tokens = Counter()
    models: Counter[str] = Counter()
    providers: Counter[str] = Counter()
    successful = failed = usage_count = 0
    estimated_cost = 0.0
    cost_breakdown = Counter()
    provider_model_rollup: dict[str, dict[str, Any]] = {}
    unknown_pricing = False
    for path in files:
        artifact = _read_json(path)
        if artifact is None:
            failed += 1
            continue
        response = artifact.get("response", artifact)
        if not isinstance(response, dict):
            failed += 1
            continue
        usage, provider = _usage(response)
        if not usage:
            if artifact.get("error_type") or artifact.get("error"):
                failed += 1
            else:
                successful += 1
            continue
        successful += 1
        usage_count += 1
        providers[provider] += 1
        model = str(response.get("model") or response.get("model_version") or "unknown")
        models[model] += 1
        for key, value in usage.items():
            tokens[key] += value
        cost = _llm_cost(usage, provider, model, prices)
        if cost is None:
            unknown_pricing = True
        else:
            estimated_cost += cost
            breakdown = _llm_cost_breakdown(usage, provider, model, prices)
            if breakdown is None:  # pragma: no cover - same priced lookup as _llm_cost
                # `assert` here vanished under `python -O`, leaving an
                # AttributeError in an optimized run instead of a guard.
                raise ValueError(f"priced model lookup disagreed for {provider}/{model}")
            for key, value in breakdown.items():
                cost_breakdown[key] += value
        provider_total = provider_model_rollup.setdefault(
            provider,
            {"responses": 0, "tokens": Counter(), "cost_breakdown_usd": Counter(), "models": {}},
        )
        provider_total["responses"] += 1
        for key, value in usage.items():
            provider_total["tokens"][key] += value
        model_total = provider_total["models"].setdefault(
            model,
            {"responses": 0, "tokens": Counter(), "cost_breakdown_usd": Counter()},
        )
        model_total["responses"] += 1
        for key, value in usage.items():
            model_total["tokens"][key] += value
        if cost is not None:
            provider_total["cost_breakdown_usd"].update(breakdown)
            model_total["cost_breakdown_usd"].update(breakdown)

    def serialize_rollup(rollup: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Render a service rollup without copying credentials or response content."""
        result: dict[str, Any] = {}
        for provider_name, provider_total in rollup.items():
            provider_breakdown = dict(provider_total["cost_breakdown_usd"])
            provider_models: dict[str, Any] = {}
            for model_name, model_total in provider_total["models"].items():
                model_breakdown = dict(model_total["cost_breakdown_usd"])
                provider_models[model_name] = {
                    "responses": model_total["responses"],
                    "tokens": dict(model_total["tokens"]),
                    "cost_breakdown_usd": {
                        key: round(value, 6) for key, value in model_breakdown.items()
                    },
                    "estimated_cost_usd": round(sum(model_breakdown.values()), 6),
                }
            result[provider_name] = {
                "responses": provider_total["responses"],
                "tokens": dict(provider_total["tokens"]),
                "cost_breakdown_usd": {
                    key: round(value, 6) for key, value in provider_breakdown.items()
                },
                "estimated_cost_usd": round(sum(provider_breakdown.values()), 6),
                "models": provider_models,
            }
        return result

    if service == "google_document_ai":
        estimated_cost = successful * SERVICE_PRICES_USD_PER_1000[service] / 1000
    return {
        "service": service,
        "source": str(raw_dir),
        "responses_seen": len(files),
        "successful": successful,
        "failed": failed,
        "usage_records": usage_count,
        "providers": dict(providers),
        "models": dict(models),
        "tokens": dict(tokens),
        "cost_breakdown_usd": {key: round(value, 6) for key, value in cost_breakdown.items()},
        "provider_model_rollup": serialize_rollup(provider_model_rollup),
        "estimated_cost_usd": round(estimated_cost, 6),
        "pricing_status": "partial" if unknown_pricing else "estimated",
    }


def _summary_services(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Extract non-token service counters from output summaries."""
    found: dict[str, dict[str, Any]] = {}
    for path in run_dir.rglob("*.json"):
        if "raw" in path.parts:
            continue
        data = _read_json(path)
        if data is None:
            continue
        summary = data.get("summary")
        external = data.get("external_validation")
        if not isinstance(external, dict) and isinstance(summary, dict):
            external = summary.get("external_validation")
        if isinstance(external, dict) and "requests_sent" in external:
            requests = int(external.get("requests_sent") or 0)
            found["google_address_validation"] = {
                "service": "google_address_validation",
                "artifact": str(path),
                "requests": requests,
                "estimated_cost_usd": round(
                    requests * SERVICE_PRICES_USD_PER_1000["google_address_validation"] / 1000,
                    6,
                ),
                "pricing_status": "estimated",
            }
        places = data.get("google_places")
        if isinstance(places, dict) and "requests_sent" in places:
            requests = int(places.get("requests_sent") or 0)
            found["google_places"] = {
                "service": "google_places",
                "artifact": str(path),
                "requests": requests,
                "estimated_cost_usd": round(
                    requests * SERVICE_PRICES_USD_PER_1000["google_places_text_search_pro"] / 1000,
                    6,
                ),
                "pricing_status": "estimated",
            }
    return found


def _page_count(run_dir: Path) -> int | None:
    for path in sorted(run_dir.rglob("ingestion_manifest.json")):
        data = _read_json(path)
        if isinstance(data, dict) and isinstance(data.get("pages"), list):
            return len(data["pages"])
        summary = data.get("summary") if isinstance(data, dict) else None
        if isinstance(summary, dict) and isinstance(summary.get("pages"), int):
            return int(summary["pages"])
    return None


def snapshot(
    run_dir: str | Path,
    *,
    expected_pages: int | None = None,
    prices: dict[str, dict[str, float]] | None = None,
    required_services: set[str] | None = None,
) -> dict[str, Any]:
    """Return a non-secret point-in-time progress and spend snapshot."""
    root = Path(run_dir).resolve()
    rates = prices or MODEL_PRICES
    expected = expected_pages or _page_count(root)
    services = {
        service: _raw_report(service, raw_dir, rates)
        for service, raw_dir in _raw_directories(root).items()
    }
    services.update(_summary_services(root))
    total_cost = sum(float(item.get("estimated_cost_usd") or 0.0) for item in services.values())
    provider_model_rollup: dict[str, dict[str, Any]] = {}
    for service in services.values():
        for provider, provider_data in service.get("provider_model_rollup", {}).items():
            destination = provider_model_rollup.setdefault(
                provider,
                {
                    "responses": 0,
                    "tokens": Counter(),
                    "cost_breakdown_usd": Counter(),
                    "models": {},
                },
            )
            destination["responses"] += int(provider_data.get("responses") or 0)
            destination["tokens"].update(provider_data.get("tokens") or {})
            destination["cost_breakdown_usd"].update(provider_data.get("cost_breakdown_usd") or {})
            for model, model_data in provider_data.get("models", {}).items():
                model_destination = destination["models"].setdefault(
                    model,
                    {
                        "responses": 0,
                        "tokens": Counter(),
                        "cost_breakdown_usd": Counter(),
                    },
                )
                model_destination["responses"] += int(model_data.get("responses") or 0)
                model_destination["tokens"].update(model_data.get("tokens") or {})
                model_destination["cost_breakdown_usd"].update(
                    model_data.get("cost_breakdown_usd") or {}
                )

    def serialize_snapshot_rollup() -> dict[str, Any]:
        """Render a snapshot rollup without copying credentials or response content."""
        result: dict[str, Any] = {}
        for provider, provider_data in provider_model_rollup.items():
            provider_breakdown = dict(provider_data["cost_breakdown_usd"])
            models: dict[str, Any] = {}
            for model, model_data in provider_data["models"].items():
                model_breakdown = dict(model_data["cost_breakdown_usd"])
                models[model] = {
                    "responses": model_data["responses"],
                    "tokens": dict(model_data["tokens"]),
                    "cost_breakdown_usd": {
                        key: round(value, 6) for key, value in model_breakdown.items()
                    },
                    "estimated_cost_usd": round(sum(model_breakdown.values()), 6),
                }
            result[provider] = {
                "responses": provider_data["responses"],
                "tokens": dict(provider_data["tokens"]),
                "cost_breakdown_usd": {
                    key: round(value, 6) for key, value in provider_breakdown.items()
                },
                "estimated_cost_usd": round(sum(provider_breakdown.values()), 6),
                "models": models,
            }
        return result

    progress_values: list[float] = []
    for item in services.values():
        seen = item.get("responses_seen", item.get("requests", 0))
        if expected and item["service"] in PAGE_SERVICES:
            progress_values.append(min(float(seen) / expected, 1.0))
        elif item["service"] in {"google_address_validation", "google_places"}:
            progress_values.append(1.0)
        else:
            progress_values.append(1.0 if seen else 0.0)
    progress = sum(progress_values) / len(progress_values) if progress_values else 0.0
    required = required_services or set(services)
    required_ready = all(
        service in services
        and (
            service not in PAGE_SERVICES
            or not expected
            or services[service].get("responses_seen", 0) >= expected
        )
        for service in required
    )
    complete = bool(required) and required_ready
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "run_directory": str(root),
        "expected_pages": expected,
        "required_services": sorted(required),
        "progress": {
            "percent": round(progress * 100, 2),
            "complete": complete,
            "services_seen": len(services),
            "services_completed": sum(value >= 1.0 for value in progress_values),
        },
        "spend": {
            "estimated_cost_usd": round(total_cost, 6),
            "pricing_status": (
                "estimated"
                if all(item.get("pricing_status") == "estimated" for item in services.values())
                else "partial"
            ),
        },
        "services": services,
        "provider_model_rollup": serialize_snapshot_rollup(),
    }


def write_snapshot(snapshot_data: dict[str, Any], out_path: str | Path) -> Path:
    """Atomically write a monitor snapshot without overwriting secrets."""
    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", encoding="utf-8", dir=destination.parent, prefix=f".{destination.name}.", delete=False
    ) as stream:
        json.dump(snapshot_data, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, destination)
    return destination


def _line(snapshot_data: dict[str, Any]) -> str:
    progress = snapshot_data["progress"]
    spend = snapshot_data["spend"]
    services = snapshot_data["services"]
    parts = [
        f"{progress['percent']:5.1f}% complete",
        f"${spend['estimated_cost_usd']:.4f} estimated",
    ]
    for name, item in sorted(services.items()):
        count = item.get("responses_seen", item.get("successful", item.get("requests", 0)))
        expected = snapshot_data["expected_pages"]
        suffix = f"/{expected}" if expected and "successful" in item else ""
        parts.append(f"{name} {count}{suffix}")
    return " | ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run_dir", type=Path, help="Run directory whose retained progress is monitored."
    )
    parser.add_argument(
        "--expected-pages",
        type=int,
        help="Expected page count, used to report completion percentage.",
    )
    parser.add_argument("--interval", type=float, default=5.0, help="Seconds between snapshots.")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--price-file",
        type=Path,
        help="JSON price table used to estimate spend. Estimates are indicative, not billing.",
    )
    parser.add_argument(
        "--required-service",
        action="append",
        default=[],
        help="Service that must appear before the run counts as complete. Repeatable.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Take a single snapshot and exit instead of monitoring continuously.",
    )
    parser.add_argument(
        "--stop-when-complete",
        action="store_true",
        help="Exit once every required service has reported and the expected pages are accounted for.",
    )
    apply_shared_help(parser)
    args = parser.parse_args(argv)
    prices = MODEL_PRICES
    if args.price_file:
        override = json.loads(args.price_file.read_text(encoding="utf-8"))
        if not isinstance(override, dict):
            raise SystemExit("--price-file must contain a JSON object")
        prices = {**MODEL_PRICES, **override}
    while True:
        current = snapshot(
            args.run_dir,
            expected_pages=args.expected_pages,
            prices=prices,
            required_services=set(args.required_service) or None,
        )
        if args.out:
            write_snapshot(current, args.out)
        print(_line(current), flush=True)
        if args.once or (args.stop_when_complete and current["progress"]["complete"]):
            return 0
        time.sleep(max(args.interval, 0.1))


if __name__ == "__main__":
    sys.exit(main())
