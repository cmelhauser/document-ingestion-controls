#!/usr/bin/env python3
"""Collect provider usage and billing metadata from one retained run.

The report is intentionally token-only: provider prices vary by account and
are not invented here.  Raw responses remain the evidence source; this module
never sends a request, changes a source artifact, or treats a review finding as
an API failure.
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help

# A raw directory is named by the operator through --raw-dir, so a fixed table of
# lane names cannot cover a real run: `primary_02`, `secondary_06`, and
# `discovery_03` match none of the prefixes this once carried, and every lane in a
# 716-page run reported as `unclassified:`. The lane is derived from where the
# directory sits instead, which is both stable and traceable back to the command.
RAW_DIRECTORY_NAMES = ("raw",)
RAW_DIRECTORY_PREFIX = "raw_"
RAW_DIRECTORY_SUFFIX = "_raw"
# Segments that identify one page inside a per-page lane rather than the lane
# itself. Table comprehension writes raw responses three directories deep, one
# set per page, so without this every page would read as its own lane.
PAGE_SEGMENT = re.compile(r"^\d{4,}[_-]|^pages$", re.IGNORECASE)


def response_usage(response):
    """Normalize Google or OpenAI usage fields without reading content fields."""
    google = response.get("usage_metadata")
    if isinstance(google, dict):
        return {
            "prompt_tokens": google.get("prompt_token_count") or 0,
            "output_tokens": google.get("candidates_token_count") or 0,
            "reasoning_tokens": google.get("thoughts_token_count") or 0,
            "total_tokens": google.get("total_token_count") or 0,
            "cached_tokens": google.get("cached_content_token_count") or 0,
            "cache_write_tokens": 0,
        }, "google"
    openai = response.get("usage")
    if isinstance(openai, dict):
        input_details = openai.get("input_tokens_details") or {}
        # A Chat Completions response -- which is what the OpenRouter lane
        # returns -- names the same numbers prompt_tokens and completion_tokens.
        # Reading only the Responses names left a whole vendor's spend reported
        # as zero rather than reported as unknown, and the secondary lane was the
        # larger of the two.
        output_details = (
            openai.get("output_tokens_details") or openai.get("completion_tokens_details") or {}
        )
        prompt = openai.get("input_tokens")
        completion = openai.get("output_tokens")
        dialect = "openai"
        if prompt is None and completion is None:
            prompt = openai.get("prompt_tokens")
            completion = openai.get("completion_tokens")
            dialect = "openai_chat_completions"
        if prompt is None and completion is None:
            return None, "unknown"
        return {
            "prompt_tokens": prompt or 0,
            "output_tokens": completion or 0,
            "reasoning_tokens": output_details.get("reasoning_tokens") or 0,
            "total_tokens": openai.get("total_tokens") or 0,
            "cached_tokens": input_details.get("cached_tokens") or 0,
            "cache_write_tokens": input_details.get("cache_write_tokens") or 0,
        }, dialect
    return None, "unknown"


def is_raw_directory(path):
    """Return whether this directory holds a lane's retained raw responses."""
    name = path.name.casefold()
    return (
        name in RAW_DIRECTORY_NAMES
        or name.startswith(RAW_DIRECTORY_PREFIX)
        or name.endswith(RAW_DIRECTORY_SUFFIX)
    )


def lane_for_directory(run_dir, raw_dir):
    """Return the (lane, role) a raw directory belongs to.

    The lane is the attempt that produced the responses, not the directory they
    landed in. `providers/raw/primary_02` is the primary_02 attempt; the hundreds
    of `tables/<attempt>/pages/<page>/raw_profile` directories are all one
    attempt, split per page and per role. Grouping by attempt is what lets a
    per-page lane be counted at all: selecting a single directory per lane, as
    this once did, reported one page of a 716-page lane.
    """
    parts = list(raw_dir.relative_to(run_dir).parts)
    role = ""
    name = parts[-1].casefold()
    if name.startswith(RAW_DIRECTORY_PREFIX):
        role = parts[-1][len(RAW_DIRECTORY_PREFIX) :]
        parts.pop()
    elif name.endswith(RAW_DIRECTORY_SUFFIX):
        role = parts[-1][: -len(RAW_DIRECTORY_SUFFIX)]
        parts.pop()
    elif name in RAW_DIRECTORY_NAMES:
        parts.pop()
    # A lane directory beneath a shared raw/ folder keeps its own name: dropping
    # it would fold `providers/raw/primary_02` and `providers/raw/secondary_06`
    # into one `providers` lane and report the whole run's extraction as a single
    # undifferentiated cost.
    parts = [part for part in parts if part.casefold() not in RAW_DIRECTORY_NAMES]
    while parts and PAGE_SEGMENT.match(parts[-1]):
        parts.pop()
    return ("/".join(parts) if parts else raw_dir.name), role


def raw_directories(run_dir):
    """Return {lane: [(raw directory, role)]} for every retained lane.

    Every attempt is reported, including superseded ones. This report answers
    what a run cost, and an abandoned attempt was paid for: selecting only the
    fullest pass per lane, as this once did, understated spend by exactly the
    work an operator most wants to see. Which attempt a run *stands behind* is a
    separate question, answered by `run_generation.py`, not by hiding the others
    here.
    """
    run_dir = Path(run_dir)
    candidates = defaultdict(list)
    for path in run_dir.rglob("*"):
        if not path.is_dir():
            continue
        # A lane's own directory beneath a shared raw/ folder is the documented
        # layout (`--raw-dir RUN/providers/raw/primary`). It is not named `raw*`
        # itself, and the shared parent holds no responses of its own, so
        # matching only on the directory's own name finds neither and the lane
        # disappears from the report entirely.
        if not (is_raw_directory(path) or is_raw_directory(path.parent)):
            continue
        if not any(path.glob("*.json")):
            continue
        lane, role = lane_for_directory(run_dir, path)
        candidates[lane].append((path, role))
    return {lane: sorted(entries) for lane, entries in candidates.items()}


def exception_counts(raw_dir):
    """Count retained exceptions adjacent to one selected raw directory."""
    parent = raw_dir.parent
    candidates = sorted(parent.glob("*exceptions.json"))
    if not candidates:
        return {
            "exception_count": 0,
            "provider_failure_count": 0,
            "review_finding_count": 0,
            "rate_limit_count": 0,
            "retry_attempts": 0,
            "retry_delay_seconds": 0.0,
        }
    try:
        data = json.loads(candidates[-1].read_text())
    except (OSError, json.JSONDecodeError):
        return {
            "exception_count": 0,
            "provider_failure_count": 0,
            "review_finding_count": 0,
            "rate_limit_count": 0,
            "retry_attempts": 0,
            "retry_delay_seconds": 0.0,
        }
    exceptions = data.get("exceptions", [])
    if not isinstance(exceptions, list):
        exceptions = []
    provider_failures = sum(1 for item in exceptions if item.get("error_type"))
    retry_events = [
        event
        for item in exceptions
        for event in item.get("retry_log", [])
        if isinstance(event, dict)
    ]
    return {
        "exception_count": len(exceptions),
        "provider_failure_count": provider_failures,
        "review_finding_count": len(exceptions) - provider_failures,
        "rate_limit_count": sum(
            1
            for item in exceptions
            if item.get("error_type") == "RateLimitError"
            or any(
                event.get("status_code") == 429 or event.get("error_type") == "RateLimitError"
                for event in item.get("retry_log", [])
                if isinstance(event, dict)
            )
        ),
        "retry_attempts": len(retry_events),
        "retry_delay_seconds": sum(
            float(event.get("delay_seconds") or 0) for event in retry_events
        ),
    }


def lane_retry_telemetry(raw_dir):
    """Read non-secret retry telemetry retained beside a lane's raw responses."""
    for path in sorted(raw_dir.parent.glob("*.json")):
        try:
            artifact = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(artifact, dict):
            continue
        summary = artifact.get("summary", {})
        if not isinstance(summary, dict):
            continue
        telemetry = summary.get("retry_telemetry")
        if isinstance(telemetry, dict):
            return telemetry
    return {}


def page_count(run_dir):
    """Infer page count from an ingestion manifest when one is retained."""
    for path in sorted(Path(run_dir).rglob("ingestion_manifest.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data.get("pages"), list):
            return len(data["pages"])
        summary = data.get("summary", {})
        if isinstance(summary, dict) and isinstance(summary.get("pages"), int):
            return summary["pages"]
    return None


def lane_report(lane, entries):
    """Collect normalized usage and non-secret provider metadata for one lane.

    ``entries`` is every (raw directory, role) pair the lane wrote. A lane that
    writes one directory has one; a per-page lane has one per page and role, and
    all of them are summed. Reading only the first would report a single page of
    a 716-page lane as the lane's whole cost.
    """
    totals = defaultdict(int)
    providers = Counter()
    models = Counter()
    roles = Counter()
    provider_metadata = {}
    metadata_count = 0
    files = []
    for raw_dir, role in entries:
        found = sorted(raw_dir.glob("*.json"))
        files.extend(found)
        if role:
            roles[role] += len(found)
    raw_dir = entries[0][0]
    for path in files:
        try:
            artifact = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        response = artifact.get("response", artifact)
        if not isinstance(response, dict):
            continue
        usage, provider = response_usage(response)
        if usage is None:
            continue
        metadata_count += 1
        providers[provider] += 1
        model = response.get("model") or response.get("model_version")
        if model:
            models[str(model)] += 1
        if isinstance(response.get("billing"), dict):
            provider_metadata.setdefault("billing_payer", response["billing"].get("payer"))
        if response.get("service_tier") is not None:
            provider_metadata.setdefault("service_tier", response["service_tier"])
        usage_metadata = response.get("usage_metadata")
        if isinstance(usage_metadata, dict) and usage_metadata.get("traffic_type"):
            provider_metadata.setdefault("traffic_type", usage_metadata["traffic_type"])
        for key, value in usage.items():
            totals[key] += int(value)
    metadata = exception_counts(raw_dir)
    retry_telemetry = lane_retry_telemetry(raw_dir)
    return {
        "lane": lane,
        "roles": dict(roles),
        "raw_directories": len(entries),
        "provider": providers.most_common(1)[0][0] if providers else "unknown",
        "models": dict(models),
        "raw_response_count": len(files),
        "usage_metadata_count": metadata_count,
        **metadata,
        "tokens": dict(totals),
        "provider_spend_metadata": provider_metadata,
        "pricing": {
            "status": "not_configured",
            "estimated_cost_usd": None,
        },
        "retry_telemetry": retry_telemetry
        or {
            "retry_attempts": metadata["retry_attempts"],
            "rate_limit_attempts": metadata["rate_limit_count"],
            "total_retry_delay_seconds": metadata["retry_delay_seconds"],
        },
    }


def build_report(run_dir):
    """Build a token/billing report from retained raw responses in ``run_dir``."""
    lanes = [
        lane_report(lane, entries) for lane, entries in sorted(raw_directories(run_dir).items())
    ]
    totals = defaultdict(int)
    retry_totals = defaultdict(float)
    for lane in lanes:
        for key, value in lane["tokens"].items():
            totals[key] += value
        for key, value in lane.get("retry_telemetry", {}).items():
            retry_totals[key] += float(value or 0)
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "run_directory": str(Path(run_dir)),
        "source_policy": {
            "raw_provider_responses_are_retained": True,
            "only_successful_responses_with_provider_usage_are_counted": True,
            "failed_attempts_without_usage_are_not_invented": True,
            # Every retained attempt is counted. A run pays for the attempts it
            # abandons, and a spend report that hides them understates the bill
            # by exactly the work worth reviewing. Which attempt the run stands
            # behind is declared with run_generation.py, not inferred here.
            "every_retained_attempt_including_superseded_is_counted": True,
        },
        "current_run": {
            "pages": page_count(run_dir),
            "lanes": lanes,
            "tokens": dict(totals),
            "provider_failure_count": sum(lane["provider_failure_count"] for lane in lanes),
            "review_finding_count": sum(lane["review_finding_count"] for lane in lanes),
            "retry_telemetry": {
                "retry_attempts": int(retry_totals["retry_attempts"]),
                "rate_limit_attempts": int(retry_totals["rate_limit_attempts"]),
                "total_retry_delay_seconds": retry_totals["total_retry_delay_seconds"],
            },
            "pricing_status": "rates_not_configured",
            "estimated_cost_usd": None,
            "spend_metadata_fields_captured": [
                "model",
                "billing.payer",
                "service_tier",
                "traffic_type",
                "prompt/input tokens",
                "output/candidates tokens",
                "reasoning/thoughts tokens",
                "total tokens",
                "cached tokens",
                "cache-write tokens",
            ],
        },
        "how_to_price": {
            "formula": "estimated_cost_usd = (input_tokens * input_usd_per_million + output_tokens * output_usd_per_million + reasoning_tokens * reasoning_usd_per_million) / 1_000_000",
            "required_inputs": [
                "provider",
                "model",
                "input_usd_per_million",
                "output_usd_per_million",
                "reasoning_usd_per_million",
            ],
            "cache_note": "Cached and cache-write token fields are retained separately; apply provider billing rules before pricing them.",
        },
    }


def write_report(run_dir, out_path):
    """Write a new report and refuse to overwrite a retained run artifact."""
    path = Path(out_path)
    with path.open("x") as stream:
        json.dump(build_report(run_dir), stream, indent=2)
        stream.write("\n")
    return path


def main():
    parser = argparse.ArgumentParser(description="Capture retained LLM usage metadata for a run.")
    parser.add_argument(
        "run_dir",
        help="Run directory whose retained artifacts are summarized. No provider is contacted.",
    )
    parser.add_argument("--out", required=True)
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        path = write_report(args.run_dir, args.out)
        print(json.dumps({"usage_report": str(path)}, sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"LLM usage report failed: {exc}")


if __name__ == "__main__":
    main()
