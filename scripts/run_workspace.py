#!/usr/bin/env python3
"""Create, execute, and audit one contained business-document pipeline run.

Operational artifacts are evidence.  A prior trial split its output between a
named run directory, later sibling phase directories, and repository-root LLM
cache/throttle directories.  That makes a run impossible to review as one
bounded record.  This command establishes a fresh run workspace, launches
existing pipeline CLIs with that workspace as their working directory, and
rejects declared output paths that leave it.

Immutable source inputs and credentials remain outside the workspace.  Every
generated artifact -- including caches, throttles, logs, raw responses, and
retry overlays -- belongs below it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from cli_help import apply_shared_help

STANDARD_DIRECTORIES = (
    "pages",
    "providers",
    "tables",
    "htr",
    "controls",
    "templates",
    "graph",
    "relationships",
    "review",
    "client",
    "analytics",
    "canonical",
    "delivery",
    "runtime/cache",
    "runtime/locks",
    "runtime/throttle",
    "retries",
    "logs",
)

# Every documented pipeline writer uses one of these controls. Original client
# evidence and credentials may stay outside the run root; known positional
# writers are handled separately below.
WRITE_OPTIONS = frozenset(
    {
        "--adapter-out",
        "--authorization-template",
        "--buddy-out",
        "--cache-dir",
        "--comments-csv",
        "--comments-xlsx",
        "--csv",
        "--decision-cards",
        "--downstream-gate-out",
        "--duplicates-out",
        "--exceptions",
        "--exceptions-out",
        "--handoff-out",
        "--html",
        "--images-dir",
        "--log",
        "--manifest",
        "--out",
        "--out-dir",
        "--output-dir",
        "--primary-out",
        "--quality-summary",
        "--raw-dir",
        "--register",
        "--report",
        "--secondary-out",
        "--state",
        "--unique-out",
        "--xlsx",
    }
)


def utc_now() -> str:
    """Return an offset-bearing timestamp suitable for an operational ledger."""
    return datetime.now(UTC).isoformat()


def resolve_run_root(path: str | Path) -> Path:
    """Return the canonical workspace path without silently creating it."""
    return Path(path).expanduser().resolve()


def require_within_run_root(run_root: str | Path, destination: str | Path) -> Path:
    """Resolve one generated destination and reject lexical and symlink escapes."""
    root = resolve_run_root(run_root)
    raw = Path(destination)
    resolved = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Run output escapes run root: {destination}") from exc
    return resolved


def initialize(run_root: str | Path) -> dict:
    """Create the standard fresh run layout without reusing retained evidence."""
    root = resolve_run_root(run_root)
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"Run directory must be new or empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    for relative in STANDARD_DIRECTORIES:
        require_within_run_root(root, relative).mkdir(parents=True, exist_ok=True)
    return {
        "schema_version": "run_workspace_v1",
        "run_root": str(root),
        "created_at": utc_now(),
        "directories": list(STANDARD_DIRECTORIES),
        "policy": {
            "generated_artifacts_must_stay_under_run_root": True,
            "external_inputs_are_read_only": True,
            "credentials_remain_external": True,
        },
    }


def declared_outputs(argv: Sequence[str]) -> list[str]:
    """Return destinations passed through writer options and known state positions."""
    result: list[str] = []
    index = 0
    while index < len(argv):
        argument = argv[index]
        option, separator, value = argument.partition("=")
        if option in WRITE_OPTIONS:
            if separator:
                if not value:
                    raise ValueError(f"Output option needs a destination: {option}")
                result.append(value)
            else:
                if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                    raise ValueError(f"Output option needs a destination: {option}")
                result.append(argv[index + 1])
                index += 1
        index += 1
    script_index = next(
        (index for index, argument in enumerate(argv) if Path(argument).name.endswith(".py")), None
    )
    if script_index is not None:
        script_name = Path(argv[script_index]).name
        script_arguments = argv[script_index + 1 :]
        # operations.py stage writes its first positional argument atomically.
        # It is the only documented pipeline writer without an output option.
        if (
            script_name == "operations.py"
            and len(script_arguments) >= 2
            and script_arguments[0] == "stage"
        ):
            result.append(script_arguments[1])
    return result


def normalize_run_arguments(command: Sequence[str]) -> list[str]:
    """Translate documented ``RUN/...`` examples to workspace-relative paths.

    The operating guides use ``RUN`` and ``RUN_DIR`` as legibility placeholders.  Inside this
    wrapper the working directory *is* that run, so retaining the prefix would
    accidentally create a second nested ``RUN/`` tree.  Only that exact prefix
    is rewritten; absolute external source inputs are left alone.
    """
    normalized = []
    for argument in command:
        if argument in {"RUN", "RUN_DIR"}:
            normalized.append(".")
        elif argument.startswith("RUN/"):
            normalized.append(argument[4:])
        elif argument.startswith("RUN_DIR/"):
            normalized.append(argument[8:])
        else:
            normalized.append(argument)
    return normalized


def run_environment(run_root: str | Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Bind shared transient runtime state to this one run workspace."""
    root = resolve_run_root(run_root)
    environment = dict(os.environ if environ is None else environ)
    environment["BUSINESS_DOC_RUN_ROOT"] = str(root)
    environment["LLM_CACHE_DIR"] = str(require_within_run_root(root, "runtime/cache"))
    environment["LLM_THROTTLE_DIR"] = str(require_within_run_root(root, "runtime/throttle"))
    return environment


def ledger_path(run_root: str | Path) -> Path:
    """Locate the append-only command ledger for one workspace."""
    return require_within_run_root(run_root, "logs/command_ledger.jsonl")


def append_ledger(run_root: str | Path, record: dict) -> None:
    """Append non-secret command metadata without retaining external input paths."""
    path = ledger_path(run_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


def run_command(run_root: str | Path, command: Sequence[str]) -> int:
    """Execute an existing CLI in the workspace after validating its outputs."""
    root = resolve_run_root(run_root)
    if not root.is_dir():
        raise ValueError(f"Run directory does not exist: {root}")
    if not command:
        raise ValueError("A command is required after --")
    command = normalize_run_arguments(command)
    outputs = declared_outputs(command)
    for output in outputs:
        require_within_run_root(root, output)
    started_at = utc_now()
    completed = subprocess.run(  # noqa: S603 - explicit operator-supplied argv, never a shell
        command, cwd=root, env=run_environment(root), check=False
    )
    append_ledger(
        root,
        {
            "schema_version": "run_workspace_command_v1",
            "started_at": started_at,
            "finished_at": utc_now(),
            "command_name": next(
                (
                    Path(argument).name
                    for argument in command
                    if Path(argument).name.endswith(".py")
                ),
                Path(command[0]).name,
            ),
            "returncode": completed.returncode,
            "declared_outputs": [
                require_within_run_root(root, output).relative_to(root).as_posix()
                for output in outputs
            ],
            "runtime": {"cache_dir": "runtime/cache", "throttle_dir": "runtime/throttle"},
        },
    )
    return completed.returncode


def audit(run_root: str | Path) -> dict:
    """Verify layout, ledger destinations, and contained runtime directories."""
    root = resolve_run_root(run_root)
    if not root.is_dir():
        raise ValueError(f"Run directory does not exist: {root}")
    missing = [relative for relative in STANDARD_DIRECTORIES if not (root / relative).is_dir()]
    escaping_symlinks = []
    for path in root.rglob("*"):
        if path.is_symlink():
            try:
                path.resolve().relative_to(root)
            except ValueError:
                escaping_symlinks.append(path.relative_to(root).as_posix())
    records = []
    ledger = ledger_path(root)
    if ledger.is_file():
        for line_number, line in enumerate(
            ledger.read_text(encoding="utf-8").splitlines(), start=1
        ):
            try:
                record = json.loads(line)
                destinations = record["declared_outputs"]
                if not isinstance(destinations, list):
                    raise ValueError("declared_outputs must be a list")
                for destination in destinations:
                    require_within_run_root(root, destination)
                records.append(record)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid command ledger line {line_number}: {exc}") from exc
    return {
        "schema_version": "run_workspace_audit_v1",
        "generated_at": utc_now(),
        "run_root": str(root),
        "status": "ready" if not missing and not escaping_symlinks else "blocked",
        "missing_standard_directories": missing,
        "escaping_symlinks": escaping_symlinks,
        "command_count": len(records),
        "runtime_directories": {"cache_dir": "runtime/cache", "throttle_dir": "runtime/throttle"},
        "findings": (
            ["All declared generated destinations and runtime directories are contained."]
            if not missing and not escaping_symlinks
            else ["Run workspace containment failed; read the named missing paths or symlinks."]
        ),
    }


def write_new_json(path: Path, payload: dict) -> None:
    """Write one no-clobber audit or initialization artifact."""
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite retained artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    """Build the explicit run-workspace command surface."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    init = subparsers.add_parser("init", help="Create a fresh standard run workspace.")
    init.add_argument("run_root", type=Path, help="New or empty root directory for one run.")
    init.add_argument("--out", type=Path, help="New initialization record under the run root.")
    command = subparsers.add_parser(
        "run", help="Run one existing pipeline command inside a workspace."
    )
    command.add_argument("run_root", type=Path, help="Existing run workspace root.")
    command.add_argument("command", nargs=argparse.REMAINDER, help="Command after -- to execute.")
    check = subparsers.add_parser("audit", help="Audit workspace containment and command ledger.")
    check.add_argument("run_root", type=Path, help="Existing run workspace root.")
    check.add_argument(
        "--out",
        type=Path,
        help="New containment-audit JSON path under the run root; defaults to logs/run_workspace_audit.json.",
    )
    apply_shared_help(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch initialization, contained execution, or containment audit."""
    args = build_parser().parse_args(argv)
    try:
        if args.subcommand == "init":
            payload = initialize(args.run_root)
            out = require_within_run_root(args.run_root, args.out or "logs/run_workspace_init.json")
            write_new_json(out, payload)
            print(f"Initialized run workspace: {payload['run_root']}")
            return 0
        if args.subcommand == "run":
            command = list(args.command)
            if command[:1] == ["--"]:
                command = command[1:]
            return run_command(args.run_root, command)
        payload = audit(args.run_root)
        out = require_within_run_root(args.run_root, args.out or "logs/run_workspace_audit.json")
        write_new_json(out, payload)
        print(f"Run workspace audit: {payload['status']}")
        return 0 if payload["status"] == "ready" else 1
    except (OSError, ValueError, FileExistsError) as exc:
        sys.exit(f"Run workspace failed: {exc}")


if __name__ == "__main__":
    sys.exit(main())
