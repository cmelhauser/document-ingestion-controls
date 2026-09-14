#!/usr/bin/env python3
"""Keep the agent-facing surfaces in step with the code they describe.

Operating instructions rot silently: a script is renamed, and the skill folder
still tells an agent to run the old name. Nothing fails, because nobody runs the
instructions as a test. This makes them one.

Every `scripts/NAME.py` named in the agent surfaces must exist and be indexed in
the command-line reference, and every documented root entry point must be
reachable. Reporting only; it changes nothing.
"""

import argparse
import contextlib
import importlib
import io
import json
import re
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path

from cli_help import apply_shared_help
from run_lane_coverage import LANES, uncatalogued_commands

ROOT = Path(__file__).resolve().parent.parent
# Root surfaces that instruct an agent. Each is checked against the live scripts.
ROOT_AGENT_SURFACES = (
    "SKILL.md",
    "AGENTS.md",
    "CLAUDE.md",
    "CHATGPT.md",
    "BRANCHING.md",
    ".cursor/rules/business-doc-ingestion.mdc",
)
# A client guide, README, release note, or reference that tells a person to run
# a command is an operating surface too.  These are discovered below rather
# than maintained by hand, because a new document is exactly where an otherwise
# valid command can be copied incorrectly.
ROOT_DOCUMENTATION_SURFACES = ("README.md", "RELEASE.md", "SECURITY.md", "CHANGELOG.md")
DOCUMENTATION_DIRECTORIES = ("docs", "examples", "references")
# Every operating skill file is a surface too, discovered rather than listed: a
# hand-maintained list is one more thing that can be forgotten, and a task guide
# nobody registered is exactly the file that goes stale unnoticed.
SKILL_SURFACE_ROOT = "skills"
# A reference may name a package module (scripts/client_review/queue.py), so
# the path can carry directories. Without them a module moved into a package
# would silently stop being checked -- exactly the staleness this file exists
# to prevent -- so the whole path is captured and resolved.
SCRIPT_REFERENCE = re.compile(r"scripts/((?:[a-z0-9_]+/)*[a-z0-9_]+)\.py")
CLI_TABLE_ENTRY = re.compile(r"^\|\s*`([a-z0-9_]+)\.py`\s*\|", re.MULTILINE)
CLI_ENTRY = re.compile(r"^\|\s*`([a-z0-9_]+\.(?:py|sh))`\s*\|", re.MULTILINE)
SUBCOMMAND_CHOICES = re.compile(r"^\s*\{([a-z0-9_,-]+)\}", re.MULTILINE)
CLI_HELP_CATALOGUE = "references/cli-help-catalogue.md"
USAGE_BLOCK = re.compile(r"^usage: [^\n]*(?:\n {2,}[^\n]*)*", re.MULTILINE)
# Every command an operator is shown in a fenced block. A documented invocation
# that the parser rejects is worse than no documentation: it is followed, and it
# fails at the point of use. Three such commands were shipped at once -- two
# naming the wrong input artifact, one missing two required positionals.
FENCED_BLOCK = re.compile(r"```(?:bash|sh|console)?\n(.*?)```", re.S)
DOCUMENTED_COMMAND = "python scripts/"
# The operations skill's lane table calls itself the complete operating surface,
# and three surfaces state how many workflow-lane commands it maps. Both were
# prose, so nothing kept them honest: the count read 69 while the catalogue grew
# to 74, and two catalogued lanes had no row at all.
OPERATIONS_SKILL = "skills/business-doc-operations/SKILL.md"
LANE_COUNT_SURFACES = ("AGENTS.md", "SKILL.md", OPERATIONS_SKILL)
LANE_COUNT = re.compile(r"(\d+)\s+workflow-lane\s+commands")
# A table cell boundary. A command cell escapes the pipes between its
# subcommands (`table_comprehension.py profile\|rows`), and those are not one.
TABLE_CELL = re.compile(r"(?<!\\)\|")


def documented_invocations(root):
    """Return every ``python scripts/...`` command any surface shows an operator."""
    found = []
    for name in documentation_surfaces(root):
        path = root / name
        if not path.is_file():
            continue
        for block in FENCED_BLOCK.findall(path.read_text(encoding="utf-8")):
            for line in block.replace("\\\n", " ").splitlines():
                line = line.strip()
                if not line.startswith(DOCUMENTED_COMMAND):
                    continue
                try:
                    # A trailing shell comment explains the command to a reader;
                    # the shell strips it before argparse ever sees it.
                    parts = shlex.split(line, comments=True)
                except ValueError:
                    continue
                # An explicit ellipsis stands for arguments the prose describes
                # rather than lists, so there is nothing to check.
                if any(part == "..." for part in parts[2:]):
                    continue
                # A shell glob is expanded before argparse sees it. Skipping such
                # commands hid a missing required option, so one placeholder path
                # stands in: argparse counts arguments, it does not open them.
                arguments = [
                    "glob-expansion-placeholder.json" if "*" in part else part for part in parts[2:]
                ]
                found.append((name, line, Path(parts[1]).stem, arguments))
    return found


def parser_rejects(script_name, args):
    """Return argparse's own complaint about one documented invocation, if any.

    The command's parser is the contract, so the check is to run that parser and
    nothing else: ``parse_args`` is intercepted the moment it succeeds, so no
    provider is contacted and no artifact is written.
    """
    try:
        module = importlib.import_module(script_name)
    except Exception:  # A module that cannot import is reported by other checks.
        return None
    if not hasattr(module, "main"):
        return None

    class Parsed(Exception):
        pass

    original = argparse.ArgumentParser.parse_args

    def intercept(self, argv=None, namespace=None):
        original(self, argv, namespace)
        raise Parsed

    # Some entry points take ``argv``, others read ``sys.argv``. Passing the
    # arguments only to the first kind made this check silently skip the second,
    # which is precisely the false assurance it exists to remove.
    errors = io.StringIO()
    argparse.ArgumentParser.parse_args = intercept
    argv = sys.argv
    sys.argv = [f"{script_name}.py", *args]
    try:
        with contextlib.redirect_stderr(errors), contextlib.redirect_stdout(io.StringIO()):
            module.main()
    except Parsed:
        return None
    except SystemExit as exc:
        if exc.code == 2:
            return (errors.getvalue().strip().splitlines() or ["usage error"])[-1].strip()
    except Exception:  # Anything past parsing is not this check's business.
        return None
    finally:
        argparse.ArgumentParser.parse_args = original
        sys.argv = argv
    return None


def agent_surfaces(root):
    """Return every instruction surface: the fixed root files plus every skill file."""
    skills = sorted(
        path.relative_to(root).as_posix() for path in (root / SKILL_SURFACE_ROOT).rglob("*.md")
    )
    return (*ROOT_AGENT_SURFACES, *skills)


def documentation_surfaces(root):
    """Return every checked-in Markdown instruction or operator-facing guide."""
    root = Path(root)
    found = list(agent_surfaces(root))
    for name in ROOT_DOCUMENTATION_SURFACES:
        if name not in found and (root / name).is_file():
            found.append(name)
    for directory in DOCUMENTATION_DIRECTORIES:
        path = root / directory
        if path.is_dir():
            for document in sorted(path.rglob("*.md")):
                name = document.relative_to(root).as_posix()
                if name not in found:
                    found.append(name)
    return tuple(found)


def referenced_scripts(root):
    """Return every script each checked-in guide tells an operator to run."""
    found = {}
    for name in documentation_surfaces(root):
        path = root / name
        if not path.is_file():
            found[name] = None
            continue
        found[name] = sorted(set(SCRIPT_REFERENCE.findall(path.read_text(encoding="utf-8"))))
    return found


def indexed_clis(root):
    """Return every script indexed in the command-line reference."""
    reference = root / "references" / "command-line-reference.md"
    if not reference.is_file():
        return set()
    return set(CLI_TABLE_ENTRY.findall(reference.read_text(encoding="utf-8")))


def indexed_cli_entries(root):
    """Return indexed Python and shell entry points in documented order."""
    reference = Path(root) / "references" / "command-line-reference.md"
    if not reference.is_file():
        return ()
    # ``dict.fromkeys`` preserves table order while protecting the generated
    # catalogue from an accidental duplicate row.
    return tuple(dict.fromkeys(CLI_ENTRY.findall(reference.read_text(encoding="utf-8"))))


def normalize_help_usage(help_text):
    """Make parser usage wrapping stable without changing documented content."""
    return USAGE_BLOCK.sub(
        lambda match: textwrap.fill(
            " ".join(match.group().split()), width=100, subsequent_indent="       "
        ),
        help_text,
    ).rstrip()


def help_output(root, entry, arguments=()):
    """Return one entry point's side-effect-free help text or raise clearly."""
    script = Path(root) / "scripts" / entry
    command = [sys.executable, str(script)] if entry.endswith(".py") else ["bash", str(script)]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed, checked-in CLI plus --help only
            [*command, *arguments, "--help"],
            cwd=root,
            check=False,
            capture_output=True,
            encoding="utf-8",
            timeout=5,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"{entry} help timed out") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        message = detail[-1] if detail else f"exit status {completed.returncode}"
        raise ValueError(f"{entry} help failed: {message}")
    return normalize_help_usage(completed.stdout.rstrip())


def help_subcommands(help_text):
    """Return argparse subcommand choices shown in a root or nested help page."""
    before_options = help_text.split("\noptions:\n", 1)[0]
    return tuple(
        token
        for choice in SUBCOMMAND_CHOICES.findall(before_options)
        for token in choice.split(",")
    )


def cli_help_pages(root):
    """Return every indexed command and documented subcommand's live help."""
    pages = []
    for entry in indexed_cli_entries(root):
        queue = [()]
        visited = set()
        while queue:
            arguments = queue.pop(0)
            if arguments in visited:
                continue
            visited.add(arguments)
            output = help_output(root, entry, arguments)
            pages.append((entry, arguments, output))
            # A first-level parser choice is a real subcommand. A choice shown
            # inside a subcommand's positional-argument help is often a value
            # domain (for example, a lifecycle stage), not another parser. The
            # latter used to expand one five-value stage argument into an
            # exponential fake command tree and never finished its catalogue.
            if not arguments:
                for subcommand in help_subcommands(output):
                    queue.append((subcommand,))
    return pages


def render_cli_help_catalogue(root):
    """Render the checked-in, parser-derived complete option catalogue."""
    pages = cli_help_pages(root)
    rendered = [
        "# Complete CLI Help Catalogue",
        "",
        "This is the complete, released option catalogue for every supported command",
        "and subcommand. It is generated from each executable's `--help`, so it",
        "lists positional inputs, flags, defaults, allowed values, and the exact",
        "wording an operator sees. The parser remains the executable contract.",
        "",
        "Do not edit this file by hand. After changing a parser or shared option",
        "help, run:",
        "",
        "```bash",
        "python scripts/agent_surface_check.py --write-cli-help-catalogue",
        "```",
        "",
        "`scripts/release_check.py` compares this file with live help and fails on",
        "any drift. Command purpose, phase placement, and safety context remain in",
        "[Command-Line Reference](command-line-reference.md).",
    ]
    for entry, arguments, output in pages:
        suffix = " ".join(arguments)
        label = f"{entry} {suffix}".strip()
        rendered.extend(("", f"## `{label}`", "", "```text", output, "```"))
    return "\n".join(rendered) + "\n"


def cli_help_catalogue_errors(root):
    """Reject a checked-in option catalogue that differs from live parser help."""
    root = Path(root)
    catalogue = root / CLI_HELP_CATALOGUE
    if not catalogue.is_file() or not indexed_cli_entries(root):
        return []
    try:
        expected = render_cli_help_catalogue(root)
    except ValueError as exc:
        return [f"{CLI_HELP_CATALOGUE}: {exc}"]
    if catalogue.read_text(encoding="utf-8") != expected:
        return [
            f"{CLI_HELP_CATALOGUE}: generated CLI help is stale; "
            "run python scripts/agent_surface_check.py --write-cli-help-catalogue"
        ]
    return []


def has_cli(script):
    """Return whether a script declares its own argparse entry point."""
    return "argparse.ArgumentParser(" in script.read_text(encoding="utf-8")


def catalogued_lane_commands():
    """Return each command `run_lane_coverage.py` catalogues as a lane, once, in catalogue order."""
    return list(dict.fromkeys(command for _lane, command, _types, _keys in LANES))


def lane_table_errors(root):
    """Require a lane-table row for every catalogued lane, and every stated lane count to hold.

    Only the Command column counts. The notes of one row name other commands --
    the accuracy sample's row mentions `sampling.py` -- and matching anywhere in
    the line would let a deleted row pass on the strength of another row's prose.
    """
    root = Path(root)
    commands = catalogued_lane_commands()
    command_cells = [
        cells[3]
        for cells in (
            TABLE_CELL.split(line)
            for line in (root / OPERATIONS_SKILL).read_text(encoding="utf-8").splitlines()
            if line.startswith("|")
        )
        if len(cells) > 4
    ]
    errors = [
        f"{OPERATIONS_SKILL}: the lane table has no row for catalogued lane {command}"
        for command in commands
        if not any(f"`{command}" in cell for cell in command_cells)
    ]
    for surface in LANE_COUNT_SURFACES:
        text = (root / surface).read_text(encoding="utf-8")
        errors.extend(
            f"{surface}: says {stated} workflow-lane commands; the catalogue has {len(commands)}"
            for stated in LANE_COUNT.findall(text)
            if int(stated) != len(commands)
        )
    return errors


def check(root=ROOT):
    """Return a report naming every stale or unindexed instruction."""
    root = Path(root)
    errors = []
    indexed = indexed_clis(root)
    surfaces = referenced_scripts(root)
    checked = 0
    for surface, scripts in sorted(surfaces.items()):
        if scripts is None:
            errors.append(f"missing agent surface: {surface}")
            continue
        for name in scripts:
            checked += 1
            script = root / "scripts" / f"{name}.py"
            if not script.is_file():
                errors.append(f"{surface}: names a script that does not exist: {name}.py")
            elif has_cli(script) and name not in indexed:
                # A shared library such as client_review/protection.py has no CLI to index.
                errors.append(f"{surface}: names {name}.py, absent from the command-line reference")
    # A lane absent from the coverage catalogue is one a run can skip without
    # anything saying so, which is how fourteen lane families went unrun. The
    # catalogue describes this repository's lanes, so it is only meaningful
    # against this repository -- a synthetic fixture root has no lanes.
    catalogue = sorted(uncatalogued_commands(root / "scripts")) if root == ROOT else []
    for command in catalogue:
        errors.append(f"run_lane_coverage.py: {command} is neither a catalogued lane nor exempt")
    if root == ROOT:
        errors.extend(lane_table_errors(root))
    invocations = documented_invocations(root)
    for surface, line, script_name, args in invocations:
        rejection = parser_rejects(script_name, args)
        if rejection:
            errors.append(
                f"{surface}: documented command is rejected by its own parser: "
                f"{rejection} -- in `{line}`"
            )
    errors.extend(cli_help_catalogue_errors(root))
    return {
        "surfaces_checked": len([value for value in surfaces.values() if value is not None]),
        "documentation_surfaces_checked": len(documentation_surfaces(root)),
        "script_references_checked": checked,
        "indexed_clis": len(indexed),
        "documented_invocations_checked": len(invocations),
        "lane_catalogue": "stale" if catalogue else "complete",
        "status": "ready" if not errors else "stale",
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--write-cli-help-catalogue",
        action="store_true",
        help="regenerate the checked-in complete CLI help catalogue from live parser help",
    )
    parser.add_argument("--quiet", action="store_true")
    apply_shared_help(parser)
    args = parser.parse_args()
    if args.write_cli_help_catalogue:
        path = ROOT / CLI_HELP_CATALOGUE
        path.write_text(render_cli_help_catalogue(ROOT), encoding="utf-8")
    report = check()
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(rendered)
    if not args.quiet:
        print(rendered, end="")
    if report["errors"]:
        sys.exit("Agent surfaces are stale; see the errors above.")


if __name__ == "__main__":
    main()
