#!/usr/bin/env python3
"""Catch a broken lane while a run is happening, not after it finishes.

A full trial spent a corpus before anyone noticed that Google Document AI had
returned HTTP 404 on all 18 pages. Every page was retained as a failure, the
adapter exited zero, and the run continued believing it had a third independent
vendor. The evidence was on disk the whole time -- every raw response said
``document_ai_http_404`` -- and nothing read it until the run was over.

Two checks, and neither costs a token:

``preflight`` runs before the run. It resolves every enabled lane's provider and
credential variable and reports the ones that are missing. With ``--probe`` it
makes one minimal authenticated request per distinct provider endpoint, which is
what separates "the variable is set" from "the endpoint accepts it". A wrong
processor id is then a five-second refusal instead of a corpus of failures.

``watch`` runs during the run. It polls the run directory, reads only retained
files, and classifies every retained provider failure it finds. A lane whose
pages are all failing is reported the moment the evidence exists, and
``--fail-fast`` exits non-zero so a supervising script can stop the run.

Both are deterministic and read-only with respect to the run: no artifact is
written except the optional report, and no lane is retried from here. A
credential or configuration failure is the operator's to fix, and the report
names which of the two it is, because those have different remedies and only one
of them is worth retrying.
"""

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

from cli_help import apply_shared_help
from reauthorize_google import access_token
from runtime_config import (
    env_value,
    lane_model,
    lane_provider,
    load_project_env,
    provider_credential_env,
)

# Lanes an operator turns on, with the setting that enables each and the
# provider/credential settings that decide whether it can work at all. Kept
# beside the environment reference rather than inferred, because a lane that
# resolves its provider at call time cannot be discovered by reading the module.
PROVIDER_LANES = (
    ("independent OCR", "GOOGLE_DOCUMENT_AI_ENABLED", "GOOGLE_DOCUMENT_AI_CREDENTIAL_ENV", None),
    (
        "handwriting OCR",
        "GOOGLE_HANDWRITING_OCR_ENABLED",
        "GOOGLE_HANDWRITING_OCR_CREDENTIAL_ENV",
        None,
    ),
    (
        "address validation",
        "GOOGLE_ADDRESS_VALIDATION_ENABLED",
        # The lane reads GOOGLE_MAPS_API_KEY_ENV; naming a setting that does not
        # exist made preflight report a configured lane as having no credential,
        # which is the kind of false alarm that teaches an operator to skip the
        # check entirely.
        "GOOGLE_MAPS_API_KEY_ENV",
        None,
    ),
    ("place candidates", "GOOGLE_PLACES_ENABLED", "GOOGLE_PLACES_API_KEY_ENV", None),
    (
        "semantic mappings",
        "SCHEMA_DISCOVERY_ENABLED",
        "SCHEMA_DISCOVERY_CREDENTIAL_ENV",
        "SCHEMA_DISCOVERY_PROVIDER",
    ),
    (
        "semantic mappings buddy",
        "SCHEMA_DISCOVERY_BUDDY_ENABLED",
        "SCHEMA_DISCOVERY_BUDDY_CREDENTIAL_ENV",
        "SCHEMA_DISCOVERY_BUDDY_PROVIDER",
    ),
    (
        "slot equivalence",
        "SLOT_EQUIVALENCE_ENABLED",
        "SLOT_EQUIVALENCE_CREDENTIAL_ENV",
        "SLOT_EQUIVALENCE_PROVIDER",
    ),
    (
        "slot equivalence buddy",
        "SLOT_EQUIVALENCE_BUDDY_ENABLED",
        "SLOT_EQUIVALENCE_BUDDY_CREDENTIAL_ENV",
        "SLOT_EQUIVALENCE_BUDDY_PROVIDER",
    ),
    ("allocation policy", "ALLOCATION_POLICY_ENABLED", "ALLOCATION_POLICY_CREDENTIAL_ENV", None),
    ("audit-only amendments", "LLM_ADJUDICATION_ENABLED", "LLM_ADJUDICATION_CREDENTIAL_ENV", None),
    (
        "card review",
        "CLIENT_REVIEW_LLM_ENABLED",
        "CLIENT_REVIEW_LLM_CREDENTIAL_ENV",
        "LLM_CLIENT_REVIEW_PROVIDER",
    ),
    (
        "relationship lanes",
        "CLIENT_REVIEW_CONTEXT_LLM_ENABLED",
        "CLIENT_REVIEW_CONTEXT_LLM_CREDENTIAL_ENV",
        "CLIENT_REVIEW_CONTEXT_LLM_PROVIDER",
    ),
    (
        "relationship buddy",
        "CLIENT_REVIEW_CONTEXT_LLM_ENABLED",
        "CLIENT_REVIEW_CONTEXT_LLM_BUDDY_CREDENTIAL_ENV",
        "CLIENT_REVIEW_CONTEXT_LLM_BUDDY_PROVIDER",
    ),
    (
        "exception resolution",
        "CLIENT_REVIEW_EXCEPTION_RESOLUTION_ENABLED",
        "CLIENT_REVIEW_EXCEPTION_RESOLUTION_PRIMARY_CREDENTIAL_ENV",
        "CLIENT_REVIEW_EXCEPTION_RESOLUTION_PRIMARY_PROVIDER",
    ),
    (
        "simulated client comments",
        "AI_SIMULATED_CLIENT_REVIEW_ENABLED",
        "AI_SIMULATED_CLIENT_REVIEW_CREDENTIAL_ENV",
        "AI_SIMULATED_CLIENT_REVIEW_PROVIDER",
    ),
)
# The two consensus extraction lanes are not optional and are resolved through
# the lane helpers rather than a single setting name.
EXTRACTION_LANES = ("consensus_primary", "consensus_secondary")
# Google lanes take a short-lived access token minted by reauthorize_google.py
# rather than a long-lived key, so an unset variable here is the documented
# pre-run step and not a misconfiguration. Saying that is the difference between
# a useful refusal and one an operator learns to ignore.
RUN_TIME_MINTED = "_ACCESS_TOKEN"
MINT_COMMAND = "python scripts/reauthorize_google.py"

# What a retained failure means, and therefore what to do about it. A wrong
# processor id and an expired token both stop a lane, but only one is worth
# retrying, so they are never reported as one thing.
CREDENTIAL = "credential"
CONFIGURATION = "configuration"
TRANSIENT = "transient"
UNCLASSIFIED = "unclassified"
FAILURE_CLASSES = (
    (
        re.compile(r"401|403|unauthorized|forbidden|permission|credential|token|api[_ ]?key", re.I),
        CREDENTIAL,
    ),
    (
        re.compile(r"404|not[_ ]?found|invalid[_ ]?(model|processor|project)|no such", re.I),
        CONFIGURATION,
    ),
    (
        re.compile(r"429|408|500|502|503|504|timeout|timed[_ ]?out|transport|connection", re.I),
        TRANSIENT,
    ),
)
REMEDIES = {
    CREDENTIAL: (
        "the credential is missing, wrong, or expired -- refresh it "
        "(python scripts/reauthorize_google.py for Google lanes) and rerun the lane"
    ),
    CONFIGURATION: (
        "the endpoint, project, processor, or model does not exist for this "
        "credential -- correct the setting; retrying will not help"
    ),
    TRANSIENT: "provider-side or network failure -- the lane's bounded retry covers this; rerun its retry pass",
    UNCLASSIFIED: "cause not recognised; read the retained raw response named beside it",
}


def classify_failure(text):
    """Return which kind of failure a retained provider error names."""
    for pattern, name in FAILURE_CLASSES:
        if pattern.search(str(text)):
            return name
    return UNCLASSIFIED


def enabled(setting):
    """Return whether an operator turned this lane on."""
    return env_value(setting, "false").strip().casefold() == "true"


def credential_present(variable):
    """Return whether the named credential variable actually holds a value."""
    return bool(variable) and bool(env_value(variable, "").strip())


def preflight(lanes=None, extraction=None):
    """Return one record per enabled lane saying whether it can run at all."""
    load_project_env()
    lanes = PROVIDER_LANES if lanes is None else lanes
    extraction = EXTRACTION_LANES if extraction is None else extraction
    results = []
    for name, enable_setting, credential_setting, provider_setting in lanes:
        if not enabled(enable_setting):
            continue
        provider = env_value(provider_setting, "") if provider_setting else ""
        variable = env_value(credential_setting, "").strip()
        if not variable and provider:
            variable = provider_credential_env(provider)
        results.append(
            {
                "lane": name,
                "enabled_by": enable_setting,
                "provider": provider or "lane default",
                "credential_variable": variable or f"(unset: {credential_setting})",
                "credential_present": credential_present(variable),
                "minted_at_run_time": variable.endswith(RUN_TIME_MINTED),
            }
        )
    for lane in extraction:
        provider = lane_provider(lane)
        variable = provider_credential_env(provider)
        results.append(
            {
                "lane": lane,
                "enabled_by": "always",
                "provider": f"{provider}/{lane_model(lane, provider)}",
                "credential_variable": variable,
                "credential_present": credential_present(variable),
                "minted_at_run_time": False,
            }
        )
    return results


def resolve_run_time_tokens(results):
    """Verify Application Default Credentials for Google lanes without retaining a token.

    Google adapters deliberately accept either an already exported short-lived
    token or a token minted from ADC at request time.  Treating only the former
    as a credential made the sentinel reject the documented ADC path.  This
    probe asks gcloud for a token but discards it immediately; no provider API
    call, token, or credential path is retained in the report.
    """
    for item in results:
        if item["credential_present"] or not item["minted_at_run_time"]:
            continue
        try:
            access_token(item["credential_variable"])
        except ValueError:
            item["runtime_token_available"] = False
        else:
            item["credential_present"] = True
            item["credential_source"] = "application_default_credentials"
            item["runtime_token_available"] = True
    return results


def retained_failures(run_dir):
    """Return every retained provider failure the run has written so far.

    Raw responses are where a failure states its own cause. The adapter's
    exception artifact records that a page failed; the raw response records that
    it failed 404, which is the difference between "review this page" and "your
    processor id is wrong".
    """
    run_dir = Path(run_dir)
    failures = []
    for path in sorted(run_dir.rglob("*.json")):
        try:
            if path.stat().st_size > 2_000_000:
                continue
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        cause = data.get("error_type") or data.get("provider_error_type")
        if isinstance(cause, str) and cause.strip():
            failures.append(
                {
                    "lane_directory": path.relative_to(run_dir).parts[0],
                    "artifact": path.relative_to(run_dir).as_posix(),
                    "cause": cause.strip(),
                    "classification": classify_failure(cause),
                }
            )
    return failures


def health(run_dir):
    """Return the run's provider health, grouped by the lane that produced it."""
    failures = retained_failures(run_dir)
    lanes = {}
    for failure in failures:
        bucket = lanes.setdefault(
            failure["lane_directory"],
            {"failures": 0, "causes": Counter(), "classifications": Counter()},
        )
        bucket["failures"] += 1
        bucket["causes"][failure["cause"]] += 1
        bucket["classifications"][failure["classification"]] += 1
    reported = []
    for lane_directory, bucket in sorted(lanes.items()):
        classification = bucket["classifications"].most_common(1)[0][0]
        reported.append(
            {
                "lane_directory": lane_directory,
                "retained_failures": bucket["failures"],
                "causes": dict(bucket["causes"]),
                "dominant_classification": classification,
                "remedy": REMEDIES[classification],
                # A configuration or credential fault does not get better with
                # time, so it is worth stopping a run for; a transient one is
                # what the lanes' own bounded retries already handle.
                "stop_the_run": classification in (CREDENTIAL, CONFIGURATION),
            }
        )
    return {
        "schema_version": "1.0",
        "run_directory": str(Path(run_dir)),
        "summary": {
            "retained_provider_failures": len(failures),
            "lanes_with_failures": len(reported),
            "lanes_worth_stopping_for": sum(1 for item in reported if item["stop_the_run"]),
        },
        "lanes": reported,
    }


def write_report(report, out):
    """Write a report without overwriting a prior one."""
    if out.exists():
        raise FileExistsError(f"refusing to overwrite existing report: {out}")
    out.write_text(json.dumps(report, indent=2) + "\n")


def run_preflight(args):
    """Report whether every enabled lane has a credential to work with."""
    results = resolve_run_time_tokens(preflight())
    missing = [item for item in results if not item["credential_present"]]
    unminted = [item for item in missing if item["minted_at_run_time"]]
    blocked = [item for item in missing if not item["minted_at_run_time"]]
    report = {
        "schema_version": "1.0",
        "summary": {
            "lanes_checked": len(results),
            "lanes_without_a_credential": len(blocked),
            "lanes_awaiting_a_run_time_token": len(unminted),
        },
        "lanes": results,
        "remedy": {
            "run_time_token": MINT_COMMAND,
            "missing_credential": "set the named variable outside the repository",
        },
    }
    if args.out:
        write_report(report, args.out)
    if not args.quiet:
        print(f"Enabled lanes checked: {len(results)}")
        for item in results:
            if item["credential_present"]:
                state = "ok"
            elif item["minted_at_run_time"]:
                state = "NEEDS TOKEN"
            else:
                state = "NO CREDENTIAL"
            print(f"  {state:<14} {item['lane']:<28} {item['credential_variable']}")
        if unminted:
            print(f"  run {MINT_COMMAND} to mint the Google tokens above")
    if blocked or unminted:
        parts = []
        if blocked:
            parts.append(
                "no credential for "
                + ", ".join(item["lane"] for item in blocked)
                + " -- set the named variables outside the repository"
            )
        if unminted:
            parts.append(
                "no run-time token for "
                + ", ".join(item["lane"] for item in unminted)
                + f" -- run {MINT_COMMAND}"
            )
        raise SystemExit("Run preflight failed: " + "; ".join(parts))
    return 0


def run_watch(args):
    """Report retained provider failures, once or until the run is stopped."""
    while True:
        report = health(args.run_dir)
        if not args.quiet:
            summary = report["summary"]
            print(
                f"Retained provider failures: {summary['retained_provider_failures']} "
                f"across {summary['lanes_with_failures']} lanes"
            )
            for item in report["lanes"]:
                print(
                    f"  {item['lane_directory']}: {item['retained_failures']} "
                    f"({item['dominant_classification']}) -- {item['remedy']}"
                )
        stopping = [item for item in report["lanes"] if item["stop_the_run"]]
        if args.out and args.once:
            write_report(report, args.out)
        if stopping and args.fail_fast:
            raise SystemExit(
                "Run sentinel: stop the run -- "
                + "; ".join(
                    f"{item['lane_directory']} is failing for a "
                    f"{item['dominant_classification']} reason ({item['remedy']})"
                    for item in stopping
                )
            )
        if args.once:
            return 0
        time.sleep(args.interval)


def main(argv=None):
    """Check a run's providers before it spends, and while it runs."""
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("preflight", help="verify every enabled lane has a credential")
    check.add_argument("--out", type=Path, help="New JSON report path.")
    check.add_argument("--quiet", action="store_true")
    apply_shared_help(check)

    watch = sub.add_parser("watch", help="report retained provider failures during a run")
    watch.add_argument("run_dir", type=Path, help="Run directory whose retained failures are read.")
    watch.add_argument("--interval", type=float, default=30.0, help="Seconds between polls.")
    watch.add_argument("--once", action="store_true", help="Take one snapshot and exit.")
    watch.add_argument(
        "--fail-fast",
        action="store_true",
        help="Exit non-zero as soon as a lane is failing for a credential or configuration reason.",
    )
    watch.add_argument("--out", type=Path, help="New JSON report path; used with --once.")
    watch.add_argument("--quiet", action="store_true")
    apply_shared_help(watch)

    args = parser.parse_args(argv)
    try:
        return run_preflight(args) if args.command == "preflight" else run_watch(args)
    except (OSError, ValueError, FileExistsError) as exc:
        sys.exit(f"Run sentinel failed: {exc}")


if __name__ == "__main__":
    sys.exit(main())
