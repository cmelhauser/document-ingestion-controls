#!/usr/bin/env python3
"""Run mutation testing over the highest-stakes control modules.

The repository's 100% branch gate is a strong floor and a known-incomplete one:
`coverage.py` derives branch arcs between statements, so both arms of a
conditional expression and every operand of a boolean short-circuit are
invisible to it. `scripts/` contains hundreds of each, concentrated in exactly
the modules that make decisions. Mutation testing measures what the gate cannot.

The budget is a ratchet, not a pass/fail line: lower `--max-surviving` as
surviving mutants are killed, and never raise it to make a run go green.

**Not yet wired into CI.** mutmut 3.x is TUI-oriented: in a non-interactive
runner it stalls at "Running stats" and emits no machine-readable summary. This
runner therefore refuses rather than reporting a count it did not receive -- an
earlier version defaulted to zero surviving mutants and passed CI green while
mutmut had not run at all, which is precisely the failure mode this repository
exists to prevent. Run it manually on a terminal, or replace the tool. Do not
wire it into CI until it produces a count non-interactively.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from cli_help import apply_shared_help

ROOT = Path(__file__).resolve().parent.parent
# mutmut exits non-zero when mutants survive, which is a result, not a failure.
MUTMUT_RESULT_EXIT_CODES = frozenset({0, 1, 2})
SURVIVING_PATTERNS = (
    re.compile(r"(?:^|\s)survived[:\s]+(\d+)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"(\d+)\s+survived", re.IGNORECASE),
    re.compile(r"\U0001f640\s*(\d+)"),
)
# The modules whose decisions carry the most weight, in the order they gate.
CONTROL_MODULES = (
    "consensus.py",
    "arithmetic_check.py",
    "client_review/protection.py",
    "completeness.py",
    "sampling.py",
    "attribution.py",
    "evidence_graph.py",
    "allocation_policy.py",
    "client_review_package.py",
    "llm_runtime.py",
    "runtime_config.py",
    "address_normalize.py",
)


def run(paths, timeout_seconds):
    """Run mutmut over the configured modules and return its raw result text.

    mutmut 3.x reads ``paths_to_mutate`` from ``pyproject.toml`` and takes no
    path arguments, so the module selection is asserted against that
    configuration rather than passed on the command line.
    """
    configured = configured_paths()
    unexpected = sorted(set(paths) - configured)
    if unexpected:
        raise ValueError(
            "mutmut mutates the paths configured in pyproject.toml; "
            f"not configured: {', '.join(unexpected)}"
        )
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "mutmut", "run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    output = completed.stdout + completed.stderr
    if completed.returncode not in MUTMUT_RESULT_EXIT_CODES:
        # A tool that did not run has not proved anything. Reporting zero
        # surviving mutants here would be the exact failure this check exists to
        # catch, one level up.
        raise ValueError(
            f"mutmut exited {completed.returncode} without producing results:\n{output.strip()}"
        )
    return output


def configured_paths():
    """Return the module names mutmut is configured to mutate."""
    config = ROOT / "pyproject.toml"
    section = config.read_text(encoding="utf-8").split("[tool.mutmut]", 1)
    if len(section) != 2:
        raise ValueError("pyproject.toml has no [tool.mutmut] section")
    body = section[1].split("\n[", 1)[0]
    return {
        Path(item.strip().strip("\"'")).name
        for item in re.findall(r"paths_to_mutate\s*=\s*\[([^\]]*)\]", body)[0].split(",")
        if item.strip()
    }


def surviving(output):
    """Count mutants the suite failed to kill, refusing to infer a count.

    An unrecognized output shape means the run produced no result. Defaulting to
    zero would make this check incapable of ever failing.
    """
    for pattern in SURVIVING_PATTERNS:
        match = pattern.search(output)
        if match:
            return int(match.group(1))
    raise ValueError(f"mutmut output declared no surviving-mutant count:\n{output.strip()[:500]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-surviving",
        type=int,
        required=True,
        help="Maximum surviving mutants permitted. Required: the budget is a deliberate ratchet, never inferred.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=2100)
    parser.add_argument(
        "--modules",
        nargs="*",
        default=list(CONTROL_MODULES),
        help="Control modules to mutate. Each must be configured in [tool.mutmut].",
    )
    parser.add_argument("--report", default=None)
    apply_shared_help(parser)
    args = parser.parse_args()
    if args.max_surviving < 0:
        sys.exit("Mutation check failed: --max-surviving must be non-negative")
    try:
        output = run(args.modules, args.timeout_seconds)
    except (OSError, subprocess.SubprocessError) as exc:
        sys.exit(f"Mutation check failed: {exc}")
    try:
        count = surviving(output)
    except ValueError as exc:
        sys.exit(f"Mutation check failed: {exc}")
    result = {
        "modules": args.modules,
        "tool_output_tail": output.strip()[-2000:],
        "surviving_mutants": count,
        "budget": args.max_surviving,
        "within_budget": count <= args.max_surviving,
    }
    if args.report:
        Path(args.report).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["within_budget"]:
        sys.exit(
            f"Mutation check failed: {count} surviving mutants exceed the "
            f"budget of {args.max_surviving}"
        )


if __name__ == "__main__":
    main()
