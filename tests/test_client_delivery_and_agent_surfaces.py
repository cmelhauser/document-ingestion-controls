"""Tests for the client delivery folder and the agent-surface drift check."""

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

agent_surface_check = importlib.import_module("agent_surface_check")
delivery = importlib.import_module("client_delivery_package")


def run_directory(tmp_path, gate="clear"):
    """Build a run directory holding both deliverables and restricted artifacts."""
    run = tmp_path / "run"
    (run / "raw").mkdir(parents=True)
    (run / "final_review.json").write_text(json.dumps({"gate_status": gate, "items": []}))
    (run / "canonical_export.json").write_text(json.dumps({"batch_id": "b1", "tables": {}}))
    (run / "graph.json").write_text(
        json.dumps(
            {
                "graph_sha256": "abc",
                "run_id": "r1",
                "nodes": [
                    {"node_id": "document:d1", "approval_state": "observed", "protected": False},
                    {"node_id": "proposal:p1", "approval_state": "proposed", "protected": True},
                    {
                        "node_id": "proposal:p2",
                        "approval_state": "close_needs_review",
                        "protected": True,
                    },
                ],
                "edges": [
                    {
                        "edge_id": "kept",
                        "from_node_id": "document:d1",
                        "to_node_id": "document:d1",
                        "approval_state": "observed",
                        "protected": False,
                    },
                    {
                        "edge_id": "dangling",
                        "from_node_id": "document:d1",
                        "to_node_id": "proposal:p1",
                        "approval_state": "observed",
                        "protected": False,
                    },
                ],
            }
        )
    )
    (run / "raw" / "provider_output.json").write_text("{}")
    (run / "openai_raw_response.json").write_text("{}")
    (run / "crm.csv").write_text("id\n1\n")
    return run


def test_the_delivery_folder_contains_only_deliverables(tmp_path):
    run = run_directory(tmp_path)
    manifest = delivery.build(
        tmp_path / "client",
        run / "final_review.json",
        canonical_export=run / "canonical_export.json",
        evidence_graph=run / "graph.json",
        crm_files=[run / "crm.csv"],
    )
    delivered = sorted(
        str(path.relative_to(tmp_path / "client"))
        for path in (tmp_path / "client").rglob("*")
        if path.is_file()
    )
    assert delivered == [
        "README.md",
        "crm/crm.csv",
        "data/canonical_export.json",
        "evidence/evidence_graph.json",
        "manifest.json",
        "review/final_review.json",
    ]
    assert manifest["provisional"] is False
    assert manifest["file_count"] == 4
    assert all(len(item["sha256"]) == 64 for item in manifest["files"])


def test_the_delivered_graph_withholds_proposals_and_counts_them(tmp_path):
    run = run_directory(tmp_path)
    delivery.build(
        tmp_path / "client", run / "final_review.json", evidence_graph=run / "graph.json"
    )
    graph = json.loads((tmp_path / "client" / "evidence" / "evidence_graph.json").read_text())
    assert [node["node_id"] for node in graph["nodes"]] == ["document:d1"]
    # An edge whose endpoint was withheld cannot be delivered dangling.
    assert [edge["edge_id"] for edge in graph["edges"]] == ["kept"]
    assert graph["withheld_by_approval_state"] == {"close_needs_review": 1, "proposed": 1}
    assert graph["withheld_total"] == 2
    assert "withheld" in (tmp_path / "client" / "README.md").read_text()


@pytest.mark.parametrize("name", ["openai_raw_response.json", "raw/provider_output.json"])
def test_restricted_artifacts_are_refused_even_when_named(tmp_path, name):
    run = run_directory(tmp_path)
    with pytest.raises(ValueError, match="restricted artifact"):
        delivery.build(
            tmp_path / "client",
            run / "final_review.json",
            review_files=[run / name],
            source_root=run,
        )


def test_an_open_gate_refuses_unless_the_package_is_deliberately_provisional(tmp_path):
    run = run_directory(tmp_path, gate="blocked")
    with pytest.raises(ValueError, match="final review gate is 'blocked'"):
        delivery.build(tmp_path / "client", run / "final_review.json")
    manifest = delivery.build(
        tmp_path / "provisional", run / "final_review.json", allow_open_findings=True
    )
    assert manifest["provisional"] is True
    assert "provisional" in (tmp_path / "provisional" / "README.md").read_text()


def test_the_delivery_folder_is_never_reused(tmp_path):
    run = run_directory(tmp_path)
    delivery.build(tmp_path / "client", run / "final_review.json")
    with pytest.raises(ValueError, match="already exists"):
        delivery.build(tmp_path / "client", run / "final_review.json")


def test_a_gate_status_is_read_but_never_inferred(tmp_path):
    assert delivery.gate_status({"gate_status": "clear"}) == "clear"
    assert delivery.gate_status({"summary": {"status": "blocked"}}) == "blocked"
    # A summary present but carrying no status falls through to the refusal.
    with pytest.raises(ValueError, match="does not state a gate status"):
        delivery.gate_status({"summary": {"items": 0}})
    with pytest.raises(ValueError, match="does not state a gate status"):
        delivery.gate_status({"items": []})


def test_missing_and_duplicate_deliverables_are_refused(tmp_path):
    run = run_directory(tmp_path)
    with pytest.raises(ValueError, match="does not exist"):
        delivery.build(
            tmp_path / "a", run / "final_review.json", review_files=[run / "absent.json"]
        )
    with pytest.raises(ValueError, match="duplicate deliverable name"):
        delivery.build(
            tmp_path / "b",
            run / "final_review.json",
            review_files=[run / "final_review.json"],
        )


def test_unreadable_json_is_reported_with_its_role(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    with pytest.raises(ValueError, match="final review queue is not readable JSON"):
        delivery.build(tmp_path / "client", broken)


def test_forbidden_paths_are_judged_inside_the_run_only(tmp_path):
    # An artifact must not be refused for living under an unrelated system
    # directory that happens to share a name with an excluded one.
    outside = tmp_path / "tmp" / "final_review.json"
    outside.parent.mkdir()
    outside.write_text("{}")
    assert delivery.is_forbidden(outside, root=tmp_path / "tmp") is False
    assert delivery.is_forbidden(outside) is True
    inside = tmp_path / "run" / "raw" / "value.json"
    inside.parent.mkdir(parents=True)
    inside.write_text("{}")
    assert delivery.is_forbidden(inside, root=tmp_path / "run") is True
    # A path outside the declared root falls back to its immediate parent.
    assert delivery.is_forbidden(inside, root=tmp_path / "elsewhere") is True


def test_the_delivery_cli_writes_a_manifest(tmp_path, monkeypatch, capsys):
    run = run_directory(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_delivery_package.py",
            "--out-dir",
            str(tmp_path / "client"),
            "--final-review",
            str(run / "final_review.json"),
            "--evidence-graph",
            str(run / "graph.json"),
        ],
    )
    delivery.main()
    assert "file_count" in capsys.readouterr().out
    assert (tmp_path / "client" / "manifest.json").is_file()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_delivery_package.py",
            "--out-dir",
            str(tmp_path / "client"),
            "--final-review",
            str(run / "final_review.json"),
            "--quiet",
        ],
    )
    with pytest.raises(SystemExit, match="Client delivery package failed"):
        delivery.main()


def test_the_quiet_delivery_cli_prints_nothing(tmp_path, monkeypatch, capsys):
    run = run_directory(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "client_delivery_package.py",
            "--out-dir",
            str(tmp_path / "client"),
            "--final-review",
            str(run / "final_review.json"),
            "--quiet",
        ],
    )
    delivery.main()
    assert capsys.readouterr().out == ""


# --- the agent-surface drift check ---


def test_the_repository_agent_surfaces_are_current():
    report = agent_surface_check.check(ROOT)
    assert report["errors"] == []
    assert report["status"] == "ready"
    assert report["script_references_checked"] > 0


def test_a_renamed_script_makes_its_instructions_stale(tmp_path, monkeypatch):
    surface = tmp_path / "SKILL.md"
    surface.write_text("Run `python scripts/gone.py --help` first.\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "command-line-reference.md").write_text("| `kept.py` | x |\n")
    monkeypatch.setattr(agent_surface_check, "ROOT_AGENT_SURFACES", ("SKILL.md",))
    errors = agent_surface_check.check(tmp_path)["errors"]
    assert errors == ["SKILL.md: names a script that does not exist: gone.py"]


def test_a_script_absent_from_the_cli_reference_is_reported(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "kept.py").write_text("import argparse\nargparse.ArgumentParser()\n")
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "command-line-reference.md").write_text("| `other.py` | x |\n")
    (tmp_path / "SKILL.md").write_text("Run `python scripts/kept.py`.\n")
    monkeypatch.setattr(agent_surface_check, "ROOT_AGENT_SURFACES", ("SKILL.md",))
    errors = agent_surface_check.check(tmp_path)["errors"]
    assert errors == ["SKILL.md: names kept.py, absent from the command-line reference"]


def test_a_shared_library_needs_no_cli_entry(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "helper.py").write_text("VALUE = 1\n")
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "command-line-reference.md").write_text("| `other.py` | x |\n")
    (tmp_path / "SKILL.md").write_text("See `scripts/helper.py`.\n")
    monkeypatch.setattr(agent_surface_check, "ROOT_AGENT_SURFACES", ("SKILL.md",))
    assert agent_surface_check.check(tmp_path)["errors"] == []


def test_a_missing_surface_is_reported(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    monkeypatch.setattr(agent_surface_check, "ROOT_AGENT_SURFACES", ("ABSENT.md",))
    assert agent_surface_check.check(tmp_path)["errors"] == ["missing agent surface: ABSENT.md"]


def test_a_missing_cli_reference_indexes_nothing(tmp_path):
    assert agent_surface_check.indexed_clis(tmp_path) == set()


def _lane_surfaces(root, rows, count):
    """Write the three surfaces that state a lane count, and the lane table itself."""
    skill = root / agent_surface_check.OPERATIONS_SKILL
    skill.parent.mkdir(parents=True)
    table = "\n".join(["| Phase | Lane | Command | Notes |", "|---|---|---|---|", *rows])
    skill.write_text(f"All {count} workflow-lane\ncommands remain.\n\n{table}\n")
    for surface in ("AGENTS.md", "SKILL.md"):
        (root / surface).write_text(f"Its lane table maps all {count}\n  workflow-lane commands.\n")


def test_every_catalogued_lane_needs_a_row_in_the_operations_lane_table(tmp_path):
    """A lane with no row is a lane the operating surface never tells anyone to run.

    Only the Command column counts. `accuracy_sample.py` and `run_generation.py`
    had no row while the skill still called its table the complete operating
    surface, and a name in another row's notes must not stand in for one.
    """
    commands = agent_surface_check.catalogued_lane_commands()
    missing, *kept = commands
    rows = [f"| 3 | Lane | `{command} run\\|verify` | Notes. |" for command in kept]
    rows.append(f"| 3 | Other | `helper.py` | Mentions `{missing}` in its notes only. |")
    _lane_surfaces(tmp_path, rows, len(commands))
    assert agent_surface_check.lane_table_errors(tmp_path) == [
        f"{agent_surface_check.OPERATIONS_SKILL}: "
        f"the lane table has no row for catalogued lane {missing}"
    ]


def test_a_stated_lane_count_must_be_the_catalogues(tmp_path):
    """The count read 69 on three surfaces while the catalogue grew to 74."""
    commands = agent_surface_check.catalogued_lane_commands()
    rows = [f"| 3 | Lane | `{command}` | Notes. |" for command in commands]
    _lane_surfaces(tmp_path, rows, 69)
    assert agent_surface_check.lane_table_errors(tmp_path) == [
        f"{surface}: says 69 workflow-lane commands; the catalogue has {len(commands)}"
        for surface in agent_surface_check.LANE_COUNT_SURFACES
    ]


def test_the_surface_cli_reports_and_fails_when_stale(tmp_path, monkeypatch, capsys):
    out = tmp_path / "surfaces.json"
    monkeypatch.setattr(sys, "argv", ["agent_surface_check.py", "--out", str(out), "--quiet"])
    agent_surface_check.main()
    assert capsys.readouterr().out == ""
    assert json.loads(out.read_text())["status"] == "ready"

    monkeypatch.setattr(agent_surface_check, "check", lambda root=None: {"errors": ["stale"]})
    monkeypatch.setattr(sys, "argv", ["agent_surface_check.py"])
    with pytest.raises(SystemExit, match="stale"):
        agent_surface_check.main()


def test_every_skill_file_is_checked_without_being_registered(tmp_path, monkeypatch):
    """A task guide nobody remembered to register is the one that goes stale.

    Skill files are discovered rather than listed, so adding a guide cannot
    silently opt it out of the staleness check.
    """
    (tmp_path / "scripts").mkdir()
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "command-line-reference.md").write_text("| `kept.py` | x |\n")
    guides = tmp_path / "skills" / "business-doc-operations" / "tasks"
    guides.mkdir(parents=True)
    (guides / "brand-new-guide.md").write_text("Run `python scripts/gone.py`.\n")
    monkeypatch.setattr(agent_surface_check, "ROOT_AGENT_SURFACES", ())
    errors = agent_surface_check.check(tmp_path)["errors"]
    assert errors == [
        "skills/business-doc-operations/tasks/brand-new-guide.md: "
        "names a script that does not exist: gone.py"
    ]


def test_every_user_facing_document_is_checked_without_manual_registration(tmp_path, monkeypatch):
    """A README or client guide is still an instruction surface when it names a CLI."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "command-line-reference.md").write_text("| `kept.py` | indexed |\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "client-guide.md").write_text("Run `python scripts/gone.py`.\n")
    monkeypatch.setattr(agent_surface_check, "ROOT_AGENT_SURFACES", ())
    errors = agent_surface_check.check(tmp_path)["errors"]
    assert errors == ["docs/client-guide.md: names a script that does not exist: gone.py"]


def test_documentation_surface_discovery_deduplicates_an_already_routed_guide(
    tmp_path, monkeypatch
):
    """A guide reached through two routes is validated once, not reported twice."""
    guide = tmp_path / "docs" / "guide.md"
    guide.parent.mkdir()
    guide.write_text("guide\n")
    monkeypatch.setattr(agent_surface_check, "agent_surfaces", lambda root: ("docs/guide.md",))
    assert agent_surface_check.documentation_surfaces(tmp_path) == ("docs/guide.md",)


def test_checked_in_cli_help_catalogue_must_match_the_live_help(tmp_path):
    """The detailed flag appendix is useful only when it is the parser's output."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "alpha.py").write_text(
        "import argparse\n"
        "def main():\n"
        "    parser = argparse.ArgumentParser(description='alpha command')\n"
        "    parser.add_argument('--limit', help='maximum rows')\n"
        "    parser.parse_args()\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "command-line-reference.md").write_text("| `alpha.py` | indexed |\n")
    catalogue = tmp_path / agent_surface_check.CLI_HELP_CATALOGUE
    catalogue.write_text(agent_surface_check.render_cli_help_catalogue(tmp_path))
    assert agent_surface_check.cli_help_catalogue_errors(tmp_path) == []

    catalogue.write_text("outdated\n")
    assert agent_surface_check.cli_help_catalogue_errors(tmp_path) == [
        "references/cli-help-catalogue.md: generated CLI help is stale; "
        "run python scripts/agent_surface_check.py --write-cli-help-catalogue"
    ]


def test_cli_catalogue_does_not_treat_a_value_domain_as_nested_subcommands(tmp_path):
    """Only the root parser's choices identify separately callable commands."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "staged.py").write_text(
        "import argparse\n"
        "def main():\n"
        "    parser = argparse.ArgumentParser()\n"
        "    commands = parser.add_subparsers(dest='command', required=True)\n"
        "    stage = commands.add_parser('stage')\n"
        "    stage.add_argument('phase', choices=('profile', 'review'))\n"
        "    parser.parse_args()\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "command-line-reference.md").write_text(
        "| `staged.py` | indexed |\n"
    )
    pages = agent_surface_check.cli_help_pages(tmp_path)
    assert [(entry, arguments) for entry, arguments, _ in pages] == [
        ("staged.py", ()),
        ("staged.py", ("stage",)),
    ]


def test_cli_catalogue_reports_missing_indexes_and_unusable_help(tmp_path, monkeypatch):
    assert agent_surface_check.indexed_cli_entries(tmp_path) == ()

    timeout = agent_surface_check.subprocess.TimeoutExpired(["python"], 5)
    monkeypatch.setattr(
        agent_surface_check.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(timeout),
    )
    with pytest.raises(ValueError, match="alpha.py help timed out"):
        agent_surface_check.help_output(tmp_path, "alpha.py")

    monkeypatch.setattr(
        agent_surface_check.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(
            returncode=2, stderr="broken help\n", stdout=""
        ),
    )
    with pytest.raises(ValueError, match="alpha.py help failed: broken help"):
        agent_surface_check.help_output(tmp_path, "alpha.py")


def test_cli_catalogue_normalizes_parser_specific_usage_wrapping(tmp_path, monkeypatch):
    python_312 = (
        "usage: alpha.py --registry REGISTRY --out OUT --records-out\n"
        "                RECORDS_OUT\n\n"
        "options:\n  --quiet\n"
    )
    python_314 = (
        "usage: alpha.py --registry REGISTRY --out OUT\n"
        "                --records-out RECORDS_OUT\n\n"
        "options:\n  --quiet\n"
    )
    expected = (
        "usage: alpha.py --registry REGISTRY --out OUT --records-out RECORDS_OUT\n\n"
        "options:\n  --quiet"
    )
    assert agent_surface_check.normalize_help_usage(python_312) == expected
    assert agent_surface_check.normalize_help_usage(python_314) == expected

    monkeypatch.setattr(
        agent_surface_check.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(returncode=0, stderr="", stdout=python_312),
    )
    assert agent_surface_check.help_output(tmp_path, "alpha.py") == expected


def test_cli_catalogue_deduplicates_repeated_subcommand_choices(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_surface_check, "indexed_cli_entries", lambda root: ("alpha.py",))
    monkeypatch.setattr(
        agent_surface_check,
        "help_output",
        lambda root, entry, arguments: (
            "usage: alpha.py {run,run}\n\npositional arguments:\n  {run,run}\n"
        ),
    )

    pages = agent_surface_check.cli_help_pages(tmp_path)

    assert [(entry, arguments) for entry, arguments, _ in pages] == [
        ("alpha.py", ()),
        ("alpha.py", ("run",)),
    ]


def test_cli_catalogue_reports_a_live_help_render_failure_and_can_be_written(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / "references").mkdir()
    catalogue = tmp_path / agent_surface_check.CLI_HELP_CATALOGUE
    catalogue.write_text("current\n")
    monkeypatch.setattr(agent_surface_check, "indexed_cli_entries", lambda root: ("alpha.py",))
    monkeypatch.setattr(
        agent_surface_check,
        "render_cli_help_catalogue",
        lambda root: (_ for _ in ()).throw(ValueError("parser unavailable")),
    )
    assert agent_surface_check.cli_help_catalogue_errors(tmp_path) == [
        "references/cli-help-catalogue.md: parser unavailable"
    ]

    monkeypatch.setattr(agent_surface_check, "ROOT", tmp_path)
    monkeypatch.setattr(agent_surface_check, "render_cli_help_catalogue", lambda root: "fresh\n")
    monkeypatch.setattr(agent_surface_check, "check", lambda root=None: {"errors": []})
    monkeypatch.setattr(
        sys, "argv", ["agent_surface_check.py", "--write-cli-help-catalogue", "--quiet"]
    )
    agent_surface_check.main()
    assert catalogue.read_text() == "fresh\n"
    assert capsys.readouterr().out == ""


def test_surface_discovery_tolerates_a_repository_with_no_skill_folder(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    monkeypatch.setattr(agent_surface_check, "ROOT_AGENT_SURFACES", ())
    assert agent_surface_check.agent_surfaces(tmp_path) == ()


def test_a_missing_deliverable_leaves_no_directory_behind(tmp_path):
    """Validate everything before creating anything.

    The directory was created first, so a missing file left a folder behind that
    then blocked the retry with "already exists" -- the no-clobber rule working
    against the operator over a mistake they had already been told about.
    """
    run = run_directory(tmp_path)
    out_dir = tmp_path / "client"

    with pytest.raises(ValueError, match="deliverable does not exist"):
        delivery.build(out_dir, run / "final_review.json", crm_files=[run / "absent.csv"])
    assert not out_dir.exists(), "a failed build must not block its own retry"

    # The same destination now succeeds, which is the point of not creating it.
    delivery.build(out_dir, run / "final_review.json", crm_files=[run / "crm.csv"])
    assert (out_dir / "crm" / "crm.csv").is_file()


def surface_repo(tmp_path, guide):
    """Build a minimal repository whose one skill guide carries the given block."""
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "run_lane_coverage.py").write_text("argparse.ArgumentParser(\n")
    (tmp_path / "references").mkdir(exist_ok=True)
    reference = tmp_path / "references" / "command-line-reference.md"
    reference.write_text("| `run_lane_coverage.py` | indexed |\n")
    guides = tmp_path / "skills" / "business-doc-operations" / "tasks"
    guides.mkdir(parents=True, exist_ok=True)
    (guides / "guide.md").write_text(guide)
    return tmp_path


def test_a_documented_command_its_own_parser_rejects_is_reported(tmp_path, monkeypatch):
    """The check that matters most here: instructions are followed, then fail.

    Eleven commands in the operating skill were rejected by their own parsers --
    wrong input artifact, missing required positionals, an option that does not
    exist. Each was correct-looking prose that failed at the point of use.
    """
    monkeypatch.setattr(agent_surface_check, "ROOT_AGENT_SURFACES", ())
    root = surface_repo(
        tmp_path,
        "```bash\npython scripts/run_lane_coverage.py --require lane\n```\n",
    )
    errors = agent_surface_check.check(root)["errors"]
    assert len(errors) == 1
    assert "rejected by its own parser" in errors[0]
    assert "run_dir" in errors[0]

    # The same command with its required argument is not reported.
    (root / "skills/business-doc-operations/tasks/guide.md").write_text(
        "```bash\npython scripts/run_lane_coverage.py RUN --quiet\n```\n"
    )
    assert agent_surface_check.check(root)["errors"] == []


def test_what_the_shell_handles_before_argparse_is_not_a_parser_error(tmp_path, monkeypatch):
    """A comment, a glob, and an ellipsis are prose, not arguments.

    Skipping glob commands outright hid a missing required option, so a glob is
    replaced by one placeholder path rather than dropped.
    """
    monkeypatch.setattr(agent_surface_check, "ROOT_AGENT_SURFACES", ())
    root = surface_repo(
        tmp_path,
        "```bash\n"
        "python scripts/run_lane_coverage.py RUN --quiet   # explains itself\n"
        "python scripts/run_lane_coverage.py ... --quiet\n"
        "python scripts/run_lane_coverage.py\n"
        "cd somewhere-else\n"
        "```\n"
        "```python\nnot a shell block\n```\n",
    )
    errors = agent_surface_check.check(root)["errors"]
    assert len(errors) == 1, errors
    assert "the following arguments are required: run_dir" in errors[0]

    # A glob is checked, with a placeholder standing in for what the shell expands.
    invocations = agent_surface_check.documented_invocations(
        surface_repo(tmp_path, "```bash\npython scripts/run_lane_coverage.py RUN/*.json\n```\n")
    )
    assert invocations[0][3] == ["glob-expansion-placeholder.json"]
    # An unterminated quote is not a command anyone can run.
    assert not agent_surface_check.documented_invocations(
        surface_repo(tmp_path, '```bash\npython scripts/run_lane_coverage.py "unclosed\n```\n')
    )
    # A surface named but absent contributes no invocations rather than failing.
    monkeypatch.setattr(agent_surface_check, "ROOT_AGENT_SURFACES", ("absent.md",))
    assert agent_surface_check.documented_invocations(tmp_path) == []


def test_only_an_argparse_refusal_counts_as_a_rejection(monkeypatch):
    """Anything past parsing belongs to the command, not to this check."""
    assert agent_surface_check.parser_rejects("no_such_module_at_all", []) is None
    assert agent_surface_check.parser_rejects("cli_help", []) is None

    module = types.SimpleNamespace(main=lambda: (_ for _ in ()).throw(SystemExit(1)))
    monkeypatch.setattr(agent_surface_check.importlib, "import_module", lambda name: module)
    assert agent_surface_check.parser_rejects("anything", []) is None

    module.main = lambda: (_ for _ in ()).throw(RuntimeError("ran real work"))
    assert agent_surface_check.parser_rejects("anything", []) is None

    # An argparse refusal with nothing written to stderr still names itself.
    module.main = lambda: (_ for _ in ()).throw(SystemExit(2))
    assert agent_surface_check.parser_rejects("anything", []) == "usage error"


def test_an_uncatalogued_command_fails_the_surface_check(monkeypatch):
    """A lane absent from the coverage catalogue is one a run can skip unnoticed.

    The catalogue describes this repository's lanes, so it is only checked
    against this repository; a fixture root has no lanes to catalogue.
    """
    assert agent_surface_check.check()["lane_catalogue"] == "complete"
    monkeypatch.setattr(
        agent_surface_check, "uncatalogued_commands", lambda scripts: {"brand_new_lane.py"}
    )
    report = agent_surface_check.check()
    assert report["lane_catalogue"] == "stale"
    assert (
        "run_lane_coverage.py: brand_new_lane.py is neither a catalogued lane nor exempt"
        in report["errors"]
    )
