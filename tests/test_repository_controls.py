"""Tests for the repository's own control tooling and shared ingestion contract."""

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

cli_help = importlib.import_module("cli_help")
completeness = importlib.import_module("completeness")
mutation_check = importlib.import_module("mutation_check")
release_check = importlib.import_module("release_check")
scan_profile = importlib.import_module("scan_profile")
side_channel_input = importlib.import_module("side_channel_input")


# --- the shared side-channel ingestion contract ---


def test_load_rows_returns_every_row_as_accepted_or_rejected(tmp_path):
    source = tmp_path / "rows.json"
    source.write_text(json.dumps([{"Key": "a"}, {"Key": ""}, {"Key": "c"}]))

    def extractor(index, columns):
        if not columns.get("key"):
            return None, side_channel_input.rejection(index, "blank_key", raw=columns.get("key"))
        return columns["key"], None

    accepted, rejected = side_channel_input.load_rows(
        source, extractor=extractor, source_kind="rows"
    )
    assert accepted == ["a", "c"]
    assert rejected == [
        {"row_index": 1, "reason": "blank_key", "raw": "", "disposition": "client_review_required"}
    ]


def test_rows_from_accepts_csv_json_list_and_keyed_object(tmp_path):
    csv_source = tmp_path / "rows.csv"
    csv_source.write_text("key\na\n")
    assert side_channel_input.rows_from(csv_source, "rows") == [{"key": "a"}]

    listed = tmp_path / "listed.json"
    listed.write_text(json.dumps([{"key": "a"}]))
    assert side_channel_input.rows_from(listed, "rows") == [{"key": "a"}]

    keyed = tmp_path / "keyed.json"
    keyed.write_text(json.dumps({"entries": [{"key": "a"}]}))
    assert side_channel_input.rows_from(keyed, "rows") == [{"key": "a"}]

    unknown = tmp_path / "unknown.json"
    unknown.write_text(json.dumps({"unexpected": [{"key": "a"}]}))
    with pytest.raises(ValueError, match="no recognized rows list"):
        side_channel_input.rows_from(unknown, "rows")

    scalar = tmp_path / "scalar.json"
    scalar.write_text(json.dumps("not-rows"))
    with pytest.raises(ValueError, match="must be a list"):
        side_channel_input.rows_from(scalar, "rows")


def test_lowercase_columns_normalizes_names_without_touching_values():
    assert side_channel_input.lowercase_columns({" Amount ": " 5 "}) == {"amount": " 5 "}


# --- the configured date-order policy ---


@pytest.mark.parametrize(
    "order, expected",
    [("month_first", "2023-05"), ("day_first", "2023-03"), ("iso_only", None)],
)
def test_an_ambiguous_date_follows_the_configured_order(order, expected):
    assert completeness.month_of("05/03/2023", order) == expected


def test_an_iso_date_parses_under_every_order():
    for order in completeness.DATE_ORDERS:
        assert completeness.month_of("2023-03-14", order) == "2023-03"


def test_an_impossible_month_is_refused_rather_than_bucketed():
    assert completeness.month_of("31/05/2023", "month_first") is None


def test_the_configured_order_is_validated(monkeypatch):
    monkeypatch.setenv("COMPLETENESS_DATE_ORDER", "day_first")
    assert completeness.date_order() == "day_first"
    monkeypatch.setenv("COMPLETENESS_DATE_ORDER", "guess")
    with pytest.raises(ValueError, match="COMPLETENESS_DATE_ORDER"):
        completeness.date_order()
    monkeypatch.delenv("COMPLETENESS_DATE_ORDER")
    assert completeness.month_of("05/03/2023") == "2023-05"


def test_blocking_on_an_unanalyzed_population_is_opt_in(tmp_path, monkeypatch, capsys):
    records = tmp_path / "records.json"
    records.write_text(
        json.dumps(
            [
                {
                    "document_id": "i1",
                    "document_type": "invoice",
                    "vendor_name": "Acme",
                    "invoice_number": "INV-1",
                    "invoice_date": "2026-01-05",
                    "total_amount": 10.0,
                }
            ]
        )
    )
    results = {}
    for flag in ("false", "true"):
        monkeypatch.setenv("COMPLETENESS_BLOCK_ON_UNANALYZED_POPULATION", flag)
        out = tmp_path / f"out-{flag}.json"
        monkeypatch.setattr(
            sys, "argv", ["completeness.py", str(records), "--out", str(out), "--quiet"]
        )
        completeness.main()
        capsys.readouterr()
        results[flag] = json.loads(out.read_text())["gate_reasons"]
    assert "inference_population_not_analyzed" not in results["false"]
    assert "inference_population_not_analyzed" in results["true"]


# --- scan profiling records what it could not measure ---


def test_a_document_whose_producer_cannot_be_read_says_so(tmp_path, monkeypatch):
    class Info:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("unreadable docinfo")

    class Pdf:
        docinfo = Info()
        pages = [{}]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(scan_profile.pikepdf, "open", lambda *_args, **_kwargs: Pdf())
    records = scan_profile.profile_pdf(str(tmp_path / "any.pdf"))
    assert "document_producer" in records[0]["probe_failures"]


def test_an_unreadable_page_object_is_named_rather_than_skipped():
    class Hostile:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("unreadable")

    class Page(dict):
        @property
        def Resources(self):
            raise AttributeError

    record = scan_profile.profile_page(Page(), 0)
    assert "media_box" in record["probe_failures"]


# --- the mutation-testing runner ---


def test_the_mutation_runner_reports_and_ratchets(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(mutation_check, "run", lambda paths, timeout: "survived: 3\n")
    report = tmp_path / "mutation.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mutation_check.py",
            "--max-surviving",
            "5",
            "--modules",
            "consensus.py",
            "--report",
            str(report),
        ],
    )
    mutation_check.main()
    capsys.readouterr()
    saved = json.loads(report.read_text())
    assert saved["within_budget"] is True
    # The raw output is retained so a reported count is auditable.
    assert "survived: 3" in saved["tool_output_tail"]

    monkeypatch.setattr(
        sys, "argv", ["mutation_check.py", "--max-surviving", "2", "--modules", "consensus.py"]
    )
    with pytest.raises(SystemExit, match="exceed the budget"):
        mutation_check.main()


def test_the_mutation_runner_rejects_a_negative_budget(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["mutation_check.py", "--max-surviving", "-1"])
    with pytest.raises(SystemExit, match="non-negative"):
        mutation_check.main()


def test_the_mutation_runner_surfaces_a_tool_failure(monkeypatch):
    def explode(paths, timeout):
        raise OSError("mutmut is not installed")

    monkeypatch.setattr(mutation_check, "run", explode)
    monkeypatch.setattr(sys, "argv", ["mutation_check.py", "--max-surviving", "0"])
    with pytest.raises(SystemExit, match="mutmut is not installed"):
        mutation_check.main()


@pytest.mark.parametrize(
    "output, expected",
    [("survived: 4", 4), ("7 survived", 7), ("survived: none\nsurvived: 2", 2)],
)
def test_surviving_counts_are_read_from_tool_output(output, expected):
    assert mutation_check.surviving(output) == expected


def test_an_unrecognized_output_shape_refuses_rather_than_reporting_zero():
    """Defaulting to zero made this check incapable of ever failing.

    The first version did exactly that and passed CI green while mutmut had not
    run at all.
    """
    with pytest.raises(ValueError, match="declared no surviving-mutant count"):
        mutation_check.surviving("mutmut: command not found")


def test_a_module_outside_the_configured_paths_is_refused():
    with pytest.raises(ValueError, match="not configured: consensus.py"):
        mutation_check.run(["consensus.py"], 10)


def test_the_configured_paths_come_from_pyproject():
    assert mutation_check.configured_paths() == {"review_protection.py", "runtime_config.py"}


def test_a_project_without_a_mutmut_section_is_reported(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    monkeypatch.setattr(mutation_check, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="no \\[tool.mutmut\\] section"):
        mutation_check.configured_paths()


def test_the_runner_surfaces_an_unusable_tool_result(monkeypatch, capsys):
    monkeypatch.setattr(mutation_check, "run", lambda paths, timeout: "unrecognized output")
    monkeypatch.setattr(
        sys,
        "argv",
        ["mutation_check.py", "--max-surviving", "5", "--modules", "review_protection.py"],
    )
    with pytest.raises(SystemExit, match="declared no surviving-mutant count"):
        mutation_check.main()


def test_the_runner_invokes_the_configured_tool(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 1, "survived: 0", "")

    monkeypatch.setattr(mutation_check.subprocess, "run", fake_run)
    assert "survived: 0" in mutation_check.run(["review_protection.py"], 10)
    assert captured["command"][-2:] == ["mutmut", "run"]


def test_a_tool_that_did_not_run_is_never_reported_as_a_result(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 127, "", "mutmut: not found")

    monkeypatch.setattr(mutation_check.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="exited 127 without producing results"):
        mutation_check.run(["review_protection.py"], 10)


# --- the repository's own release checks ---


def sandbox(tmp_path):
    """Copy just enough of the repository for the structural checks to run."""
    for name in ("AGENTS.md", "SKILL.md", "README.md", "HANDOFF.md"):
        shutil.copy(ROOT / name, tmp_path / name)
    (tmp_path / "docs").mkdir(exist_ok=True)
    shutil.copy(
        ROOT / "docs/TECHNICAL_DOCUMENTATION.md", tmp_path / "docs/TECHNICAL_DOCUMENTATION.md"
    )
    (tmp_path / "scripts").mkdir(exist_ok=True)
    for name in ("completeness.py", "attribution.py"):
        shutil.copy(ROOT / "scripts" / name, tmp_path / "scripts" / name)
    return tmp_path


def test_the_rule_lists_must_stay_in_step(tmp_path):
    root = sandbox(tmp_path)
    assert release_check.normative_rule_errors(root) == []
    skill = root / "SKILL.md"
    skill.write_text(skill.read_text().replace("10. A value outside", "XX. A value outside"))
    assert "SKILL.md" in release_check.normative_rule_errors(root)[0]


def test_a_missing_normative_list_is_reported(tmp_path):
    (tmp_path / "AGENTS.md").unlink(missing_ok=True)
    assert release_check.normative_rule_errors(tmp_path) == ["missing required file: AGENTS.md"]


def test_a_readme_that_drops_the_normative_pointer_is_reported(tmp_path):
    root = sandbox(tmp_path)
    readme = root / "README.md"
    readme.write_text(readme.read_text().replace("](AGENTS.md) holds the normative", "] holds"))
    assert any(
        "normative rule list" in error for error in release_check.normative_rule_errors(root)
    )


def test_a_missing_restatement_file_is_reported(tmp_path):
    root = sandbox(tmp_path)
    (root / "SKILL.md").unlink()
    assert "missing required file: SKILL.md" in release_check.normative_rule_errors(root)


def test_a_stale_pending_work_claim_is_rejected(tmp_path):
    root = sandbox(tmp_path)
    assert release_check.handoff_freshness_errors(root) == []
    handoff = root / "HANDOFF.md"
    handoff.write_text(handoff.read_text() + "\nThese changes are not yet committed.\n")
    assert "stale pending-work claim" in release_check.handoff_freshness_errors(root)[0]


def test_a_standing_instruction_is_not_a_stale_claim(tmp_path):
    root = sandbox(tmp_path)
    handoff = root / "HANDOFF.md"
    handoff.write_text(
        handoff.read_text() + "\ndo not overwrite a shared change merely because it is not yet "
        "committed.\n"
    )
    assert release_check.handoff_freshness_errors(root) == []


def test_mcp_api_skill_is_discoverable_from_human_and_agent_entry_points():
    skill_path = "skills/mcp-api-operations/SKILL.md"
    for name in (
        "README.md",
        "SKILL.md",
        "AGENTS.md",
        "CLAUDE.md",
        "CHATGPT.md",
        "CURSOR.md",
        ".cursor/rules/business-doc-ingestion.mdc",
    ):
        assert skill_path in (ROOT / name).read_text(), name

    skill = (ROOT / skill_path).read_text()
    for authority in (
        "references/mcp-production-integration.md",
        "references/canonical-deployment-retrieval.md",
        "references/runtime-configuration.md",
        "references/command-line-reference.md",
        "references/artifact-contracts.md",
        "references/crm-write-readiness.md",
    ):
        assert authority in skill


def test_a_handoff_without_a_verified_date_is_reported(tmp_path):
    root = sandbox(tmp_path)
    handoff = root / "HANDOFF.md"
    handoff.write_text(handoff.read_text().replace("last_verified:", "verified_on:"))
    assert any("last_verified" in error for error in release_check.handoff_freshness_errors(root))


def test_a_missing_handoff_is_reported(tmp_path):
    assert release_check.handoff_freshness_errors(tmp_path) == ["missing required file: HANDOFF.md"]


def test_a_side_channel_loader_must_keep_its_rejection_register(tmp_path):
    root = sandbox(tmp_path)
    assert release_check.side_channel_loader_errors(root) == []
    source = (root / "scripts/attribution.py").read_text()
    start = source.index("        if not key:")
    end = source.index("        key = str(key).strip()", start)
    (root / "scripts/attribution.py").write_text(
        source[:start] + "        if not key:\n            continue\n" + source[end:]
    )
    errors = release_check.side_channel_loader_errors(root)
    assert errors and "skips a row without recording it" in errors[0]


def test_a_missing_or_renamed_loader_is_reported(tmp_path):
    root = sandbox(tmp_path)
    source = (root / "scripts/completeness.py").read_text()
    (root / "scripts/completeness.py").write_text(
        source.replace("def load_gl(path):", "def load_general_ledger(path):")
    )
    assert any(
        "missing side-channel loader" in e for e in release_check.side_channel_loader_errors(root)
    )
    (root / "scripts/attribution.py").unlink()
    assert any("missing required file" in e for e in release_check.side_channel_loader_errors(root))


# --- resolving branch state without `git branch --merged` ---

branch_state = importlib.import_module("branch_state")


def repository(tmp_path):
    """Build a small repository whose history mirrors this project's merge policy."""
    root = tmp_path / "repo"
    root.mkdir()

    def run(*args):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    run("init", "-q", "-b", "main")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    (root / "a.txt").write_text("one\n")
    run("add", "-A")
    run("commit", "-qm", "base")
    return root, run


def test_branch_state_classifies_each_branch_by_evidence(tmp_path):
    root, run = repository(tmp_path)

    # An ancestor of main: an ordinary fast-forwarded branch.
    run("branch", "ancestor")

    # Squash-merged: the same change reaches main under a PR-referencing subject.
    run("checkout", "-qb", "squashed")
    (root / "b.txt").write_text("two\n")
    run("add", "-A")
    run("commit", "-qm", "feat: add b")
    run("checkout", "-q", "main")
    (root / "b.txt").write_text("two\n")
    run("add", "-A")
    run("commit", "-qm", "feat: add b (#42)")

    # Squash-merged with a later touch-up on main, so the patch no longer matches
    # but the subject does. This is the shape a real squash merge leaves behind.
    run("checkout", "-qb", "amended")
    (root / "d.txt").write_text("four\n")
    run("add", "-A")
    run("commit", "-qm", "feat: add d")
    run("checkout", "-q", "main")
    (root / "d.txt").write_text("four, revised\n")
    run("add", "-A")
    run("commit", "-qm", "feat: add d (#43)")

    # Genuinely unmerged work.
    run("checkout", "-qb", "outstanding")
    (root / "c.txt").write_text("three\n")
    run("add", "-A")
    run("commit", "-qm", "feat: add c")
    run("checkout", "-q", "main")

    report = branch_state.resolve("main", cwd=root)
    states = {item["branch"]: item["state"] for item in report["branches"]}
    assert states["ancestor"] == "merged_by_ancestry"
    # Either patch identity or the subject match proves the squash; both are safe.
    assert states["squashed"] in {"merged_by_patch", "merged_by_subject"}
    assert states["amended"] == "merged_by_subject"
    assert states["outstanding"] == "unresolved"
    assert report["unresolved"] == ["outstanding"]
    assert set(report["safe_to_delete"]) == {"ancestor", "squashed", "amended"}
    assert report["branches_examined"] == 4
    assert report["state_counts"][states["outstanding"]] >= 1


def test_a_squash_merge_pr_suffix_does_not_defeat_the_subject_match():
    assert branch_state.normalize_subject("feat: add b (#42)") == "feat: add b"
    assert branch_state.normalize_subject("feat: add b") == "feat: add b"
    assert branch_state.normalize_subject("  spaced (#7)  ") == "spaced"
    # A trailing parenthesis that is not a PR reference is left alone.
    assert branch_state.normalize_subject("fix: handle (edge)") == "fix: handle (edge)"


def test_a_failing_git_command_is_reported_rather_than_swallowed(tmp_path):
    with pytest.raises(ValueError, match="git .* failed"):
        branch_state.git("rev-parse", "definitely-not-a-ref", cwd=tmp_path)


def test_the_branch_state_cli_writes_a_report(tmp_path, monkeypatch, capsys):
    root, _ = repository(tmp_path)
    monkeypatch.chdir(root)
    out = tmp_path / "state.json"
    monkeypatch.setattr(sys, "argv", ["branch_state.py", "--out", str(out), "--quiet"])
    branch_state.main()
    assert capsys.readouterr().out == ""
    assert json.loads(out.read_text())["branches_examined"] == 0

    monkeypatch.setattr(sys, "argv", ["branch_state.py"])
    branch_state.main()
    assert "branches_examined" in capsys.readouterr().out


def test_the_branch_state_cli_surfaces_a_repository_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["branch_state.py", "--main-branch", "nope"])
    with pytest.raises(SystemExit, match="Branch state failed"):
        branch_state.main()


def test_cli_help_rule_names_an_argument_that_does_not_explain_itself(tmp_path):
    """An option with neither inline help nor a shared entry must be reported.

    The command-line reference tells operators the parser is the exact contract,
    so an argument that answers nothing quietly breaks that promise.
    """
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "example.py").write_text(
        "import argparse\n"
        "parser = argparse.ArgumentParser()\n"
        'parser.add_argument("--undocumented-thing")\n'
    )
    errors = release_check.cli_help_errors(tmp_path)
    assert any("'--undocumented-thing' has no help text" in error for error in errors)


def test_cli_help_rule_accepts_inline_and_shared_help(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "example.py").write_text(
        "import argparse\n"
        "from cli_help import apply_shared_help\n"
        "parser = argparse.ArgumentParser()\n"
        'parser.add_argument("--out")\n'
        'parser.add_argument("--bespoke", help="Explains itself.")\n'
        "apply_shared_help(parser)\n"
    )
    assert release_check.cli_help_errors(tmp_path) == []


def test_cli_help_rule_requires_the_shared_vocabulary_to_be_applied(tmp_path):
    """Relying on the shared wording without applying it ships empty help."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "example.py").write_text(
        'import argparse\nparser = argparse.ArgumentParser()\nparser.add_argument("--out")\n'
    )
    errors = release_check.cli_help_errors(tmp_path)
    assert any("never calls apply_shared_help" in error for error in errors)


def test_cli_help_rule_reaches_parsers_below_the_top_level(tmp_path):
    """Fourteen parsers live in `scripts/client_review/` behind thin entry points.

    A top-level glob inspected none of them, so the rule that every argument
    must explain itself simply did not apply to a fifth of the repository's
    parsers -- the same blind spot that let the lane catalogue miss a lane whose
    entry point delegates to a module.
    """
    package = tmp_path / "scripts" / "client_review"
    package.mkdir(parents=True)
    (package / "lane.py").write_text(
        "import argparse\n"
        "parser = argparse.ArgumentParser()\n"
        'parser.add_argument("--buried-thing")\n'
    )
    errors = release_check.cli_help_errors(tmp_path)
    assert any("'--buried-thing' has no help text" in error for error in errors)
    assert any("client_review/lane.py" in error for error in errors)


def test_every_shipped_argument_explains_itself():
    assert release_check.cli_help_errors(ROOT) == []


def test_a_handoff_claiming_merged_work_is_unmerged_is_stale(tmp_path):
    """A squash-merged branch stops existing; a claim that it is pending is stale."""
    (tmp_path / "HANDOFF.md").write_text(
        '---\nlast_verified: "2026-08-26"\n---\n\nIt is not yet merged.\n'
    )
    errors = release_check.handoff_freshness_errors(tmp_path)
    assert any("stale pending-work claim" in error for error in errors)


def test_the_standing_do_not_overwrite_instruction_is_not_stale(tmp_path):
    (tmp_path / "HANDOFF.md").write_text(
        '---\nlast_verified: "2026-08-26"\n---\n\n'
        "Do not overwrite a shared change merely because it is not yet committed.\n"
    )
    assert release_check.handoff_freshness_errors(tmp_path) == []


def _cli_scripts():
    return sorted(
        path
        for path in SCRIPTS.glob("*.py")
        if "argparse.ArgumentParser(" in path.read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("script", _cli_scripts(), ids=lambda path: path.stem)
def test_every_cli_builds_its_parser(script):
    """Every command must construct its parser and answer `--help`.

    The shared help vocabulary is attached at runtime rather than in the source,
    so only actually running each parser proves the wiring holds for all of them.
    """
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=ROOT,
    )
    assert result.returncode == 0, f"{script.name} --help failed: {result.stderr}"
    assert "usage:" in result.stdout


def test_shared_help_fills_gaps_without_overwriting_inline_wording():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    parser.add_argument("--quiet", action="store_true", help="Command-specific wording.")
    parser.add_argument("--unknown-option")
    cli_help.apply_shared_help(parser)
    rendered = {
        action.option_strings[0]: action.help
        for action in parser._actions
        if action.option_strings and action.option_strings[0] != "-h"
    }
    assert rendered["--out"] == cli_help.SHARED_OPTION_HELP["--out"]
    assert rendered["--quiet"] == "Command-specific wording."
    assert rendered["--unknown-option"] is None


def test_shared_help_reaches_subcommand_parsers():
    """Subcommand parsers are where most of the repetition lives."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    child = sub.add_parser("build")
    child.add_argument("--exceptions")
    cli_help.apply_shared_help(parser)
    assert child._actions[-1].help == cli_help.SHARED_OPTION_HELP["--exceptions"]


def test_tests_never_read_the_operator_private_env(monkeypatch):
    """The gate must answer the same question locally and in CI.

    CI runs in a fresh checkout with no `.env`. Without isolation a local `.env`
    silently changes a CLI's `--enable` default, so enabling a lane on a
    developer machine turned a passing suite into a failing one.
    """
    runtime_config = importlib.import_module("runtime_config")
    resolved = runtime_config.project_env_path()
    assert not resolved.exists(), f"tests resolved a real project .env at {resolved}"
    assert resolved.name == ".env"


def test_the_real_env_path_is_repository_relative_not_cwd_relative(
    real_project_env_path, tmp_path, monkeypatch
):
    """The loader must find the same file whatever directory a command runs from."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BUSINESS_DOCUMENT_IGNORE_PROJECT_ENV", raising=False)
    resolved = real_project_env_path()
    assert resolved == ROOT / ".env"
    assert resolved.is_absolute()


def test_a_process_can_switch_the_project_env_off(real_project_env_path, monkeypatch):
    """The switch lives in the process environment; a line in `.env` could not turn `.env` off."""
    runtime_config = importlib.import_module("runtime_config")
    monkeypatch.setattr(runtime_config, "project_env_path", real_project_env_path)
    monkeypatch.setenv("BUSINESS_DOCUMENT_IGNORE_PROJECT_ENV", "true")
    assert real_project_env_path() is None
    assert runtime_config.load_project_env() == {}
    monkeypatch.setenv("BUSINESS_DOCUMENT_IGNORE_PROJECT_ENV", "false")
    assert real_project_env_path() == ROOT / ".env"


def test_a_script_the_suite_starts_reads_no_project_env(tmp_path):
    """Isolation must reach the scripts a test runs as their own process.

    `conftest.py` redirects `project_env_path` inside the pytest process, but a
    child resolves the root `.env` in its own process, where no patch reaches:
    the control-layer workflow, the acceptance wrappers and every `--help` run
    read the operator's file. The suite exports the process-only switch for its
    session, and every child inherits it.
    """
    copy = tmp_path / "scripts"
    copy.mkdir()
    shutil.copy2(SCRIPTS / "runtime_config.py", copy / "runtime_config.py")
    (tmp_path / ".env").write_text("CHILD_ENV_PROBE=loaded\n")
    probe = (
        f"import sys; sys.path.insert(0, {str(copy)!r}); import os, runtime_config; "
        "runtime_config.load_project_env(); print(os.environ.get('CHILD_ENV_PROBE', 'absent'))"
    )

    def child(environ):
        completed = subprocess.run(
            [sys.executable, "-c", probe], env=environ, capture_output=True, text=True, check=True
        )
        return completed.stdout.strip()

    assert child(dict(os.environ)) == "absent"
    # Without the switch the same child reads the file, so this check can fail.
    switched_on = {
        name: value
        for name, value in os.environ.items()
        if name != "BUSINESS_DOCUMENT_IGNORE_PROJECT_ENV"
    }
    assert child(switched_on) == "loaded"


def test_project_env_isolation_clears_inherited_settings():
    for name in ("GOOGLE_HANDWRITING_OCR_ENABLED", "OPENAI_API_KEY", "LLM_EXTRACT_PROVIDER"):
        assert name not in os.environ, f"{name} leaked into the test environment"


# Every field `consensus.py` compares two lanes on. A producer that omits one
# does not produce a wrong value -- it produces an absent one, and the consumer
# then refuses a pair that was actually correct.
CONSENSUS_COMPARED_FIELDS = ("corpus_context", "source_read_by", "independence_group", "lane")

# Adapters that write an `independent_extraction_handoff_v1` a consensus lane reads.
EXTRACTION_ADAPTERS = (
    "openai_adapter.py",
    "openrouter_adapter.py",
    "google_genai_adapter.py",
    "anthropic_adapter.py",
)


@pytest.mark.parametrize("adapter", EXTRACTION_ADAPTERS)
def test_every_extraction_adapter_writes_what_consensus_compares_on(adapter):
    """A field a control compares on is a field every producer must write.

    `consensus.py` refuses a pair whose `corpus_context` hashes differ, reading
    that field off the handoff. The Vertex adapter never wrote it, so a lane
    conditioned exactly like its pair -- the context is in the prompt and in the
    lane's own cache key -- recorded `None` and was refused as conditioned
    differently. The reading was right and could not prove it, and the cost of
    finding out was a full corpus read.

    `source_read_by` had the same shape before it: added to the consumer, absent
    from every handoff written earlier. This test is the cheap version of that
    discovery -- it fails when a consumer gains a check that a producer does not
    fill, instead of a run failing after the money is spent.
    """
    path = SCRIPTS / adapter
    if not path.exists():  # pragma: no cover - adapters are all present
        pytest.skip(f"{adapter} is not in this tree")
    source = path.read_text(encoding="utf-8")
    if "independent_extraction_handoff_v1" not in source:
        # Not a pass by default. An adapter that writes no handoff of its own
        # must be delegating to one that does; anything else is an adapter
        # writing a handoff this test cannot see.
        assert "openai_adapter.run_adapter" in source, (
            f"{adapter} neither writes an extraction handoff nor delegates to one"
        )
        return
    missing = [field for field in CONSENSUS_COMPARED_FIELDS if f'"{field}"' not in source]
    assert not missing, (
        f"{adapter} writes an extraction handoff without {', '.join(missing)}; "
        "consensus compares lanes on these and reads an absent one as a difference"
    )
