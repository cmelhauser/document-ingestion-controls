#!/usr/bin/env python3
"""Fail-closed repository checks for the versioned release package."""

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import agent_surface_check
import cli_help
from cli_help import apply_shared_help
from project_metadata import PROJECT_VERSION

REQUIRED_FILES = (
    "AGENTS.md",
    "BRANCHING.md",
    "CHANGELOG.md",
    "HANDOFF.md",
    "README.md",
    "RELEASE.md",
    "SECURITY.md",
    "SKILL.md",
    "docs/CLIENT_OVERVIEW.md",
    "docs/CLIENT_OUTPUT_OVERVIEW.md",
    "docs/CLIENT_PROCESS_PLAIN_LANGUAGE.md",
    "docs/CLIENT_USER_GUIDE.md",
    "docs/TECHNICAL_DOCUMENTATION.md",
    "references/artifact-contracts.md",
    "references/canonical-deployment-retrieval.md",
    "references/cli-help-catalogue.md",
    "references/command-line-reference.md",
    "references/runtime-configuration.md",
)
AGENT_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "CHATGPT.md",
    ".cursor/rules/business-doc-ingestion.mdc",
    "HANDOFF.md",
)
TRACKED_DOCUMENTS = (
    "BUSINESS_DOCUMENT_INGESTION_ANALYTICS_PLAN",
    "CLIENT_OVERVIEW",
    "CLIENT_OUTPUT_OVERVIEW",
    "CLIENT_PROCESS_PLAIN_LANGUAGE",
    "CLIENT_USER_GUIDE",
    "TECHNICAL_DOCUMENTATION",
)
LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
VERSION_PATTERN = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)
MCP_VERSION_PATTERN = re.compile(r'^SERVER_VERSION\s*=\s*"([^"]+)"', re.MULTILINE)
SEMVER_TAG_PATTERN = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SOURCE_HASH_PATTERN = re.compile(r"^% source-sha256: ([0-9a-f]{64})$", re.MULTILINE)
RULE_ITEM_PATTERN = re.compile(r"^\s*\d+\.\s", re.MULTILINE)
HANDOFF_VERIFIED_PATTERN = re.compile(r'^last_verified:\s*"?(\d{4}-\d{2}-\d{2})"?', re.MULTILINE)
# A handoff scoped to current state must not describe finished work as pending.
# "merged" is listed beside "committed" because a squash-merged branch stops
# existing while a sentence claiming it is unmerged reads as current work; the
# `because ` lookbehind keeps the standing instruction "do not overwrite a shared
# change merely because it is not yet committed" from tripping the rule.
STALE_PENDING_PATTERN = re.compile(
    r"(?<!because )(?:These|This|That|The|It)\s+(?:\w+\s+){0,3}"
    r"(?:are|is|remain|remains)\s+not yet (?:committed|merged|applied|pushed)",
    re.IGNORECASE,
)
# A loader that reads a client-supplied side channel must return its rejected rows
# rather than skipping them; see the ingestion contract in artifact-contracts.md.
SIDE_CHANNEL_LOADERS = (
    ("scripts/completeness.py", "load_gl"),
    ("scripts/completeness.py", "apply_payment_record"),
    ("scripts/attribution.py", "load_reference"),
)
ENV_ASSIGNMENT_PATTERN = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$", re.MULTILINE)
# A setting name the code assembles at runtime, recognised only where it is
# handed to the environment reader. Anchoring on the `env_*` call is what makes
# this precise: matching f-strings generally swallowed every format string in
# the repository and silently matched any name at all, so the check passed while
# reading nothing.
COMPOSED_SETTING_PATTERN = re.compile(
    r'env_(?:value|int|float|bool)\(\s*f"((?:[^"{}]*\{[^"{}]+\})+[^"{}]*)"'
)
# A setting name written out in full where the code reads it. Anchored on the
# reader for the same reason as the composed pattern above: an unanchored
# uppercase-identifier match sweeps in every constant in the repository. A
# subscript of `os.environ` reads the setting as surely as `.get` does; only a
# narrower copy of this rule in the test suite knew that, and it looked at
# `scripts/*.py` alone.
SETTING_READ_PATTERN = re.compile(
    r"(?:(?:env_(?:value|int|float|bool)|os\.environ\.get|os\.getenv|environ\.get)\("
    r"|environ\s*\[)"
    r'\s*["\']([A-Z][A-Z0-9_]{2,})["\']'
)
ENV_TABLE_ROW_PATTERN = re.compile(r"^\| `([A-Z][A-Z0-9_]*)` \| ([^|]+) \|", re.MULTILINE)
CLI_TABLE_ROW_PATTERN = re.compile(r"^\| `([a-z0-9_]+\.py)` \|", re.MULTILINE)
SUPPORTED_PROVIDER_MARKER = "supported-llm-providers:"
OPERATOR_PATH_PATTERN = re.compile(r"(?:^|[\"'\s])/(?:Users|home)/[^/\s]+/")
PROHIBITED_ROOT_SUFFIXES = {".csv", ".docx", ".json", ".sqlite", ".xlsx", ".zip"}
TEXT_SUFFIXES = {
    ".example",
    ".json",
    ".lua",
    ".md",
    ".mdc",
    ".py",
    ".sh",
    ".sql",
    ".tex",
    ".toml",
    ".yaml",
    ".yml",
}
# These are process-level controls intentionally kept out of `.env`: one
# chooses a local Python runtime, one selects the local read-only MCP snapshot,
# and one switches the root `.env` off for a process and its children, which a
# line inside that file could not do. They still change execution, so the
# runtime guide must describe them alongside the tracked `.env` settings.
PROCESS_ENVIRONMENT_SETTINGS = frozenset(
    {"BUSINESS_DOCUMENT_IGNORE_PROJECT_ENV", "BUSINESS_DOCUMENT_RETRIEVAL_DB", "PYTHON_BIN"}
)


def documentation_files(root):
    """Return the release-controlled Markdown and Cursor documentation files."""
    files = []
    for name in ("README.md", "RELEASE.md", "SECURITY.md", "CHANGELOG.md", *AGENT_FILES):
        path = root / name
        if path.is_file() and path not in files:
            files.append(path)
    for directory in ("docs", "examples", "references"):
        files.extend(sorted((root / directory).glob("*.md")))
    return files


def local_link_errors(root, files):
    """Find local Markdown links whose target is absent from the release tree."""
    errors = []
    for source in files:
        for raw_target in LINK_PATTERN.findall(source.read_text()):
            target = raw_target.strip().strip("<>").split(maxsplit=1)[0]
            target = unquote(target.split("#", 1)[0])
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (source.parent / target).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                errors.append(f"{source.relative_to(root)}: link escapes repository: {raw_target}")
            else:
                if not resolved.exists():
                    errors.append(f"{source.relative_to(root)}: missing link target: {raw_target}")
    return errors


def repository_data_errors(root):
    """Reject common generated artifacts and operator-specific paths."""
    errors = []
    for path in root.iterdir() if root.is_dir() else ():
        if path.is_file() and path.suffix.casefold() in PROHIBITED_ROOT_SUFFIXES:
            errors.append(f"unexpected root data artifact: {path.name}")
    scan_roots = [
        root / name
        for name in (
            ".cursor",
            ".github",
            "assets",
            "docs",
            "examples",
            "fixtures",
            "mcp",
            "references",
            "scripts",
            "tests",
        )
    ]
    seen = set()
    root_files = [path for path in root.iterdir() if path.is_file()] if root.is_dir() else []
    candidates = list(root_files)
    for scan_root in scan_roots:
        if scan_root.exists():
            candidates.extend(scan_root.rglob("*"))
    for path in candidates:
        if not path.is_file() or path in seen or path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        seen.add(path)
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if OPERATOR_PATH_PATTERN.search(content):
            errors.append(f"operator-specific absolute path: {path.relative_to(root)}")
    return errors


def unread_setting_errors(root):
    """Require every documented setting to be reachable from the code.

    `.env.example` and the runtime reference are checked against each other, and
    both can agree perfectly about a setting nothing reads. That is not
    hypothetical: `GOOGLE_VERTEX_AI_MAX_WORKERS` was documented, defaulted, and
    exported while the adapter's CLI passed no execution arguments at all, so a
    716-page lane ran single-threaded at roughly fifty hours instead of three.
    The same shape hid a transport timeout and an output ceiling.

    A name may be built rather than written -- `lane_model` composes
    `f"{prefix}_{suffix}_MODEL"` and the client-review lane composes
    `f"CLIENT_REVIEW_LANE_{suffix}_THRESHOLD"` -- so a documented name is also
    satisfied when a composed pattern in the code can produce it.
    """
    env_example = root / ".env.example"
    scripts = root / "scripts"
    if not env_example.is_file() or not scripts.is_dir():
        return []
    documented = {name for name, _ in ENV_ASSIGNMENT_PATTERN.findall(env_example.read_text())}
    source = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore") for path in sorted(scripts.rglob("*.py"))
    )
    composed = [
        re.compile(
            "^"
            + "[A-Z0-9_]+".join(re.escape(part) for part in re.split(r"\{[^{}]+\}", template))
            + "$"
        )
        for template in COMPOSED_SETTING_PATTERN.findall(source)
    ]
    errors = []
    for name in sorted(documented):
        if name in source:
            continue
        if any(pattern.match(name) for pattern in composed):
            continue
        errors.append(
            f"documented setting is never read by any script: {name}; "
            "either read it where it is documented to apply, or remove it"
        )
    return errors


def undocumented_setting_errors(root):
    """Require every setting the code reads to be documented in `.env.example`.

    `unread_setting_errors` checks one direction: a documented setting nothing
    reads. The other direction is worse and was unchecked. An operator
    configures a run from `.env.example` and the runtime reference; a setting
    read by the code and named in neither is invisible, so its default is the
    only value it will ever have and nobody knows the knob exists. That is how
    `GOOGLE_VERTEX_AI_MAX_WORKERS` cost fifty hours -- documented, in that case,
    but unread. A read, undocumented setting fails the same way from the other
    side.

    Only literal names passed to a configuration accessor count. A name the code
    composes at runtime is matched by `unread_setting_errors` from the
    documented side instead. A process-level setting is by design not a `.env`
    assignment; `documentation_contract_errors` requires its row in the runtime
    reference instead.
    """
    env_example = root / ".env.example"
    scripts = root / "scripts"
    if not env_example.is_file() or not scripts.is_dir():
        return []
    documented = {name for name, _ in ENV_ASSIGNMENT_PATTERN.findall(env_example.read_text())}
    read = set()
    for path in sorted(scripts.rglob("*.py")):
        read.update(SETTING_READ_PATTERN.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return [
        f"setting is read by a script but documented nowhere: {name}; "
        "add it to .env.example and references/runtime-configuration.md"
        for name in sorted(read - documented - PROCESS_ENVIRONMENT_SETTINGS)
    ]


def undocumented_definition_errors(root):
    """Require every module, and every module-level public definition, to explain itself.

    The contract in this repository lives in prose next to the code, not in a
    separate manual: a reader building a pipeline reads the module docstring to
    learn what a lane refuses and why. A module or a public helper with no
    docstring is a hole in that contract, and holes appear one commit at a time.

    Three deliberate exemptions. `main` is covered by its module's own
    docstring, which carries the usage. `__init__` describes a constructor whose
    class docstring already says what the object is. A function defined inside
    another function is a local helper whose meaning comes from its enclosing
    scope, and requiring a docstring there produces filler, which is worse than
    nothing.
    """
    scripts = root / "scripts"
    if not scripts.is_dir():
        return []
    errors = []
    for path in sorted(scripts.rglob("*.py")):
        relative = path.relative_to(root)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not ast.get_docstring(tree):
            errors.append(f"{relative}: module has no docstring")
        nested = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nested.update(
                    id(child)
                    for child in ast.walk(node)
                    if child is not node
                    and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                )
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if id(node) in nested or node.name in {"main", "__init__"}:
                continue
            if node.name.startswith("_") and not node.name.startswith("__"):
                continue
            if not ast.get_docstring(node):
                errors.append(f"{relative}:{node.lineno}: {node.name} has no docstring")
    return errors


def documentation_contract_errors(root):
    """Require complete, default-aligned environment and CLI documentation."""
    errors = []
    env_example = root / ".env.example"
    runtime_reference = root / "references" / "runtime-configuration.md"
    if env_example.is_file() and runtime_reference.is_file():
        settings = dict(ENV_ASSIGNMENT_PATTERN.findall(env_example.read_text()))
        settings = {
            name: (
                value[1:-1]
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}
                else value
            )
            for name, value in settings.items()
        }
        rows = ENV_TABLE_ROW_PATTERN.findall(runtime_reference.read_text())
        documented = {}
        duplicates = set()
        for name, default in rows:
            if name in documented:
                duplicates.add(name)
            documented[name] = default.strip().strip("`")
        for name in sorted(duplicates):
            errors.append(f"runtime configuration has duplicate setting row: {name}")
        for name in sorted(settings.keys() - documented.keys()):
            errors.append(f"runtime configuration missing setting: {name}")
        required_process_settings = (
            PROCESS_ENVIRONMENT_SETTINGS
            if (root / "scripts" / "run_retrieval_mcp.sh").is_file()
            or (root / "scripts" / "quality_gate.sh").is_file()
            else frozenset()
        )
        for name in sorted(required_process_settings - documented.keys()):
            errors.append(f"runtime configuration missing process setting: {name}")
        for name in sorted(documented.keys() - settings.keys() - required_process_settings):
            errors.append(f"runtime configuration has unknown setting row: {name}")
        for name in sorted(settings.keys() & documented.keys()):
            expected = settings[name] or "blank"
            if documented[name] != expected:
                errors.append(
                    f"runtime configuration default mismatch for {name}: "
                    f"expected {expected}, found {documented[name]}"
                )

    provider_source = root / "scripts" / "runtime_config.py"
    if provider_source.is_file():
        expected_providers = _llm_provider_set(provider_source)
        if not expected_providers:
            errors.append("scripts/runtime_config.py: LLM_PROVIDERS must be a literal collection")
        for name in (".env.example", "references/runtime-configuration.md"):
            path = root / name
            if not path.is_file():
                continue
            declarations = _documented_provider_sets(path.read_text())
            if len(declarations) != 1:
                errors.append(
                    f"{name}: expected exactly one {SUPPORTED_PROVIDER_MARKER} declaration"
                )
                continue
            found = declarations[0]
            if found != expected_providers:
                errors.append(
                    f"{name}: provider declaration mismatch: expected "
                    f"{', '.join(sorted(expected_providers))}; found "
                    f"{', '.join(sorted(found))}"
                )

    command_reference = root / "references" / "command-line-reference.md"
    scripts_dir = root / "scripts"
    if command_reference.is_file() and scripts_dir.is_dir():
        cli_scripts = {
            path.name
            for path in scripts_dir.glob("*.py")
            if "ArgumentParser(" in path.read_text()
            # A thin entry point whose parser lives in a package module is still
            # a command an operator runs, and the reference must still describe
            # it. Matching only on ArgumentParser would let a moved parser take
            # its command out of the documented surface silently.
            or "from client_review." in path.read_text()
        }
        documented_clis = set(CLI_TABLE_ROW_PATTERN.findall(command_reference.read_text()))
        for name in sorted(cli_scripts - documented_clis):
            errors.append(f"command-line reference missing argparse entry point: {name}")
        for name in sorted(documented_clis - cli_scripts):
            errors.append(f"command-line reference has unknown argparse entry point: {name}")
    return errors


def _llm_provider_set(path):
    """Return the literal provider identifiers declared by runtime_config.py."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not any(
            isinstance(target, ast.Name) and target.id == "LLM_PROVIDERS" for target in node.targets
        ):
            continue
        if not isinstance(node.value, (ast.Set, ast.List, ast.Tuple)):
            return set()
        values = {
            item.value
            for item in node.value.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }
        return values if len(values) == len(node.value.elts) else set()
    return set()


def _documented_provider_sets(text):
    """Parse provider markers from comments or Markdown source comments."""
    declarations = []
    for line in text.splitlines():
        if SUPPORTED_PROVIDER_MARKER not in line:
            continue
        payload = line.split(SUPPORTED_PROVIDER_MARKER, 1)[1]
        declarations.append(set(re.findall(r"[a-z][a-z0-9-]*", payload)))
    return declarations


def side_channel_loader_errors(root):
    """Require every client side-channel loader to keep a rejection register.

    Document-side controls enumerate everything they could not process. The
    side-channel inputs -- GL exports, payment files, reference exports -- reached
    the same standard by hand, and a bare ``continue`` in one of these loaders is
    how that regresses silently.
    """
    errors = []
    for name, function in SIDE_CHANNEL_LOADERS:
        path = root / name
        if not path.is_file():
            errors.append(f"missing required file: {name}")
            continue
        tree = ast.parse(path.read_text())
        target = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == function
            ),
            None,
        )
        if target is None:
            errors.append(f"{name}: missing side-channel loader {function}")
            continue
        for node in ast.walk(target):
            # A bare ``return`` skips an input exactly as ``continue`` does. Only
            # ``continue`` was checked, and a payment row that could not be keyed
            # left through a bare return with no register -- reporting an invoice
            # as open while the client's export said it was paid.
            if isinstance(node, ast.If) and any(
                isinstance(item, ast.Continue)
                or (isinstance(item, ast.Return) and item.value is None)
                for item in node.body
            ):
                appends = [
                    call
                    for call in ast.walk(node)
                    if isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "append"
                ]
                if not appends:
                    errors.append(
                        f"{name}:{node.lineno}: {function} skips a row without recording it"
                    )
    return errors


def tracked_document_build_errors(root):
    """Require every build stage to name the same tracked documents.

    A tracked document only reaches a client if four separate lists agree: the
    Markdown-to-LaTeX generator, the LaTeX-to-PDF renderer, this module's
    ``TRACKED_DOCUMENTS``, and the render workflow's page-count probe. Nothing
    connected them, and the plain-language client guide sat in the repository
    for a full release cycle with no ``.tex`` or ``.pdf`` because it had been
    added to none of them. Omission is the failure mode here, so the lists are
    compared against each other rather than each being read on its own.
    """
    expected = set(TRACKED_DOCUMENTS)
    sources = (
        # The final entry of each shell list ends with "; do" rather than a
        # line continuation, so match the path itself and not the line end.
        ("scripts/generate_docs.sh", re.compile(r"^\s*docs/([A-Z0-9_]+)\.md\b", re.M)),
        ("scripts/render_docs.sh", re.compile(r"^\s*docs/([A-Z0-9_]+)\.tex\b", re.M)),
        (
            ".github/workflows/render-docs.yml",
            re.compile(r"^\s*pdfinfo docs/([A-Z0-9_]+)\.pdf\s*$", re.M),
        ),
    )
    errors = []
    for name, pattern in sources:
        path = root / name
        if not path.is_file():
            errors.append(f"missing required file: {name}")
            continue
        found = set(pattern.findall(path.read_text(encoding="utf-8")))
        for stem in sorted(expected - found):
            errors.append(f"{name}: tracked document is never built: {stem}")
        for stem in sorted(found - expected):
            errors.append(f"{name}: builds a document that is not tracked: {stem}")
    return errors


def normative_rule_errors(root):
    """Require the duplicated rule lists to stay in step with the normative one.

    `AGENTS.md` owns the rule count; `SKILL.md`, `README.md`, and the technical
    guide restate it for different audiences. Keeping them in step by hand is how
    a rule drifts out of one copy, so the count is compared mechanically.
    """
    normative = root / "AGENTS.md"
    if not normative.is_file():
        return ["missing required file: AGENTS.md"]
    expected = len(
        RULE_ITEM_PATTERN.findall(_rule_block(normative.read_text(), "## Hard constraints"))
    )
    errors = []
    for name, heading in (
        ("SKILL.md", "## Invariants"),
        ("docs/TECHNICAL_DOCUMENTATION.md", "## Non-negotiable controls"),
    ):
        path = root / name
        if not path.is_file():
            errors.append(f"missing required file: {name}")
            continue
        found = len(RULE_ITEM_PATTERN.findall(_rule_block(path.read_text(), heading)))
        if found != expected:
            errors.append(
                f"{name}: {found} rules under {heading!r} but AGENTS.md declares {expected}"
            )
    readme = root / "README.md"
    if readme.is_file() and "](AGENTS.md) holds the normative" not in readme.read_text():
        errors.append("README.md: trust model must name AGENTS.md as the normative rule list")
    return errors


def _rule_block(text, heading):
    """Return the numbered-list section that follows a heading."""
    if heading not in text:
        return ""
    body = text.split(heading, 1)[1]
    return body.split("\n## ", 1)[0].split("\n### ", 1)[0]


def cli_help_errors(root):
    """Require every command-line argument to explain itself.

    `references/command-line-reference.md` tells an operator that the executable
    parser is the exact contract and routes every command through `--help`. An
    argument with no help text silently breaks that contract: the reference sends
    the reader to a parser that does not answer the question. An argument
    satisfies this rule by carrying its own `help=`, or by using one of the
    recurring names whose canonical wording lives in `scripts/cli_help.py`.

    A parser that relies on the shared vocabulary must also call
    `apply_shared_help`, or the canonical text is never attached at runtime.

    The whole tree is walked, not just its top level. Fourteen parsers live in
    `scripts/client_review/` behind thin entry points, and a top-level glob
    inspected none of them -- the same blind spot that let the lane catalogue
    miss a lane whose entry point delegates to a module.
    """
    errors = []
    for path in sorted((root / "scripts").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "add_argument" not in source:
            continue
        relative = path.relative_to(root)
        uses_shared = False
        for node in ast.walk(ast.parse(source)):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
            ):
                continue
            if any(keyword.arg == "help" for keyword in node.keywords):
                continue
            name = (
                node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
            )
            if name in cli_help.SHARED_OPTION_HELP:
                uses_shared = True
                continue
            errors.append(f"{relative}:{node.lineno}: argument {name!r} has no help text")
        if uses_shared and "apply_shared_help(" not in source:
            errors.append(f"{relative}: relies on shared help but never calls apply_shared_help")
    return errors


def handoff_freshness_errors(root):
    """Reject a handoff that describes committed work as pending.

    `HANDOFF.md` is scoped to current state and is the first file a new operator
    or agent reads, so a stale claim there is more costly than elsewhere.
    """
    path = root / "HANDOFF.md"
    if not path.is_file():
        return ["missing required file: HANDOFF.md"]
    text = path.read_text()
    errors = []
    # Only an assertion about *this* tree is stale-able. "do not overwrite a
    # shared change merely because it is not yet committed" is a standing
    # instruction and must not trip the check.
    for match in STALE_PENDING_PATTERN.finditer(text):
        errors.append(f"HANDOFF.md: stale pending-work claim: {match.group(0)[:70]!r}")
    verified = HANDOFF_VERIFIED_PATTERN.search(text)
    if not verified:
        errors.append("HANDOFF.md: frontmatter must declare last_verified")
    return errors


def release_tag_errors(root, tag):
    """Require an immutable stable tag to name the checked-in release package."""
    root = Path(root)
    errors = []
    expected_tag = f"v{PROJECT_VERSION}"
    if not isinstance(tag, str) or not SEMVER_TAG_PATTERN.fullmatch(tag):
        errors.append(
            "release tag must use the stable semantic-version tag form vMAJOR.MINOR.PATCH"
        )
    if tag != expected_tag:
        errors.append(
            f"release tag must match project version: expected {expected_tag}, found {tag}"
        )

    release = root / "RELEASE.md"
    expected_release_heading = f"# Release {PROJECT_VERSION}"
    if not release.is_file() or expected_release_heading not in release.read_text(encoding="utf-8"):
        errors.append(f"RELEASE.md must contain {expected_release_heading!r}")

    changelog = root / "CHANGELOG.md"
    changelog_pattern = re.compile(
        rf"^## {re.escape(PROJECT_VERSION)}\s+—\s+\d{{4}}-\d{{2}}-\d{{2}}$", re.MULTILINE
    )
    if not changelog.is_file() or not changelog_pattern.search(
        changelog.read_text(encoding="utf-8")
    ):
        errors.append(
            f"CHANGELOG.md must contain a dated stable-release heading for {PROJECT_VERSION}"
        )
    return errors


def check_release(root, tag=None):
    """Return a machine-readable release report without changing the repository."""
    root = Path(root)
    errors = [
        f"missing required file: {name}" for name in REQUIRED_FILES if not (root / name).is_file()
    ]

    pyproject = root / "pyproject.toml"
    version_match = VERSION_PATTERN.search(pyproject.read_text()) if pyproject.is_file() else None
    if not version_match or version_match.group(1) != PROJECT_VERSION:
        errors.append("pyproject.toml version does not match project release metadata")

    mcp_server = root / "scripts" / "retrieval_mcp.py"
    mcp_text = mcp_server.read_text() if mcp_server.is_file() else ""
    mcp_match = MCP_VERSION_PATTERN.search(mcp_text)
    mcp_version_current = (mcp_match and mcp_match.group(1) == PROJECT_VERSION) or (
        "SERVER_VERSION = PROJECT_VERSION" in mcp_text
    )
    if not mcp_version_current:
        errors.append("retrieval MCP version does not match project release metadata")

    for name in AGENT_FILES:
        path = root / name
        if path.is_file():
            content = path.read_text()
            if "SKILL.md" not in content or "artifact-contracts.md" not in content:
                errors.append(f"{name}: missing required operating-instruction link")

    for stem in TRACKED_DOCUMENTS:
        for suffix in (".md", ".tex", ".pdf"):
            path = root / "docs" / f"{stem}{suffix}"
            if not path.is_file():
                errors.append(f"missing tracked document: {path.relative_to(root)}")
            elif suffix == ".pdf" and not path.read_bytes().startswith(b"%PDF-"):
                errors.append(f"invalid tracked PDF header: {path.relative_to(root)}")
        markdown = root / "docs" / f"{stem}.md"
        latex = root / "docs" / f"{stem}.tex"
        if markdown.is_file() and latex.is_file():
            source_hash = hashlib.sha256(markdown.read_bytes()).hexdigest()
            declared = SOURCE_HASH_PATTERN.search(latex.read_text())
            if not declared or declared.group(1) != source_hash:
                errors.append(f"stale generated LaTeX source: {latex.relative_to(root)}")

    docs = documentation_files(root)
    errors.extend(local_link_errors(root, docs))
    errors.extend(repository_data_errors(root))
    errors.extend(documentation_contract_errors(root))
    errors.extend(unread_setting_errors(root))
    errors.extend(undocumented_setting_errors(root))
    errors.extend(undocumented_definition_errors(root))
    errors.extend(tracked_document_build_errors(root))
    errors.extend(normative_rule_errors(root))
    errors.extend(cli_help_errors(root))
    errors.extend(handoff_freshness_errors(root))
    errors.extend(side_channel_loader_errors(root))
    errors.extend(agent_surface_check.check(root)["errors"])
    if tag is not None:
        errors.extend(release_tag_errors(root, tag))
    return {
        "project_version": PROJECT_VERSION,
        "release_tag": tag,
        "status": "ready" if not errors else "blocked",
        "markdown_files_checked": len(docs),
        "tracked_pdfs_checked": len(TRACKED_DOCUMENTS),
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Validate the versioned repository release package."
    )
    parser.add_argument(
        "--root", default=Path(__file__).resolve().parents[1], help="Repository root to validate."
    )
    parser.add_argument(
        "--tag",
        help="verify that a stable vMAJOR.MINOR.PATCH tag matches the checked-in release package",
    )
    apply_shared_help(parser)
    args = parser.parse_args()
    report = check_release(args.root, args.tag)
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["errors"]:
        sys.exit("Release check failed")


if __name__ == "__main__":
    main()
