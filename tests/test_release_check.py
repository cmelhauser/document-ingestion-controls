"""Release-package checks for versions, required files, links, and documents."""

import hashlib
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

release_check = importlib.import_module("release_check")


def write(path, text="content"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def minimal_release(root):
    for name in release_check.REQUIRED_FILES:
        write(root / name, "SKILL.md references/artifact-contracts.md\n")
    write(root / "pyproject.toml", '[project]\nversion = "1.0.0"\n')
    write(root / "scripts" / "retrieval_mcp.py", 'SERVER_VERSION = "1.0.0"\n')
    for name in release_check.AGENT_FILES:
        write(root / name, "SKILL.md references/artifact-contracts.md\n")
    for stem in release_check.TRACKED_DOCUMENTS:
        markdown = write(root / "docs" / f"{stem}.md")
        source_hash = hashlib.sha256(markdown.read_bytes()).hexdigest()
        write(root / "docs" / f"{stem}.tex", f"% source-sha256: {source_hash}\n")
        path = root / "docs" / f"{stem}.pdf"
        path.write_bytes(b"%PDF-fixture")


def test_repository_release_is_structurally_ready(monkeypatch, capsys):
    report = release_check.check_release(ROOT)
    assert report["status"] == "ready" and report["errors"] == []
    monkeypatch.setattr(sys, "argv", [release_check.__file__, "--root", str(ROOT)])
    release_check.main()
    assert '"status": "ready"' in capsys.readouterr().out


def test_tracked_document_build_lists_must_match_the_tracked_document_set(tmp_path):
    """A document that is tracked but never built, or built but not tracked."""
    stems = list(release_check.TRACKED_DOCUMENTS)
    built = stems[1:]
    write(
        tmp_path / "scripts" / "generate_docs.sh",
        "for source in \\\n"
        + "".join(f"  docs/{stem}.md \\\n" for stem in built)
        + "  docs/STRAY_DOCUMENT.md; do\n",
    )
    write(
        tmp_path / "scripts" / "render_docs.sh",
        "for source in \\\n" + "".join(f"  docs/{stem}.tex \\\n" for stem in stems) + "  ; do\n",
    )
    errors = release_check.tracked_document_build_errors(tmp_path)
    joined = "\n".join(errors)
    assert f"scripts/generate_docs.sh: tracked document is never built: {stems[0]}" in joined
    assert (
        "scripts/generate_docs.sh: builds a document that is not tracked: STRAY_DOCUMENT" in joined
    )
    # The renderer names every tracked document, so it contributes no error.
    assert "scripts/render_docs.sh: tracked document" not in joined
    # The workflow is absent from the fixture entirely.
    assert "missing required file: .github/workflows/render-docs.yml" in joined


def test_release_errors_cover_versions_files_agents_documents_and_links(tmp_path):
    minimal_release(tmp_path)
    (tmp_path / "RELEASE.md").unlink()
    write(tmp_path / "pyproject.toml", '[project]\nversion = "0.0.0"\n')
    write(tmp_path / "scripts" / "retrieval_mcp.py", 'SERVER_VERSION = "0.0.0"\n')
    write(tmp_path / "CLAUDE.md", "missing instructions\n")
    write(tmp_path / "README.md", "[missing](no-such-file.md)\n[escape](../../outside.md)\n")
    (tmp_path / "docs" / "CLIENT_OVERVIEW.tex").unlink()
    write(tmp_path / "docs" / "TECHNICAL_DOCUMENTATION.tex", "% source-sha256: " + "0" * 64)
    (tmp_path / "docs" / "CLIENT_USER_GUIDE.pdf").write_bytes(b"not-pdf")
    report = release_check.check_release(tmp_path)
    joined = "\n".join(report["errors"])
    assert report["status"] == "blocked"
    for message in (
        "missing required file",
        "pyproject.toml version",
        "retrieval MCP version",
        "CLAUDE.md: missing required",
        "missing tracked document",
        "invalid tracked PDF",
        "stale generated LaTeX",
        "missing link target",
        "link escapes repository",
    ):
        assert message in joined


def test_release_check_cli_blocks_and_document_discovery_handles_absent_files(
    monkeypatch, tmp_path, capsys
):
    assert release_check.documentation_files(tmp_path) == []
    assert release_check.local_link_errors(tmp_path, []) == []
    write(tmp_path / "README.md", "[web](https://example.com) [mail](mailto:a@example.com)\n")
    assert release_check.local_link_errors(tmp_path, [tmp_path / "README.md"]) == []
    monkeypatch.setattr(sys, "argv", [release_check.__file__, "--root", str(tmp_path)])
    with pytest.raises(SystemExit, match="Release check failed"):
        release_check.main()
    assert '"status": "blocked"' in capsys.readouterr().out


def test_release_rejects_root_data_artifacts_and_operator_paths(tmp_path):
    minimal_release(tmp_path)
    write(tmp_path / "generated.json", "{}")
    operator_path = "/Users/" + "operator/client/run"
    write(tmp_path / "fixtures" / "run.json", f'{{"path":"{operator_path}"}}')
    errors = release_check.repository_data_errors(tmp_path)
    assert "unexpected root data artifact: generated.json" in errors
    assert any("operator-specific absolute path" in error for error in errors)
    ignored_path = "/Users/" + "operator/private"
    write(tmp_path / ".venv" / "ignored.py", f'path = "{ignored_path}"')
    assert release_check.repository_data_errors(tmp_path) == errors
    binary = tmp_path / "tests" / "binary.py"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"\xff\xfe")
    assert release_check.repository_data_errors(tmp_path) == errors


def test_documentation_contract_checks_environment_and_cli_inventory(tmp_path):
    write(tmp_path / ".env.example", "ALPHA=one\nBETA=\n")
    write(
        tmp_path / "references" / "runtime-configuration.md",
        "\n".join(
            (
                "| Setting | Default | Meaning |",
                "|---|---|---|",
                "| `ALPHA` | `wrong` | mismatch |",
                "| `ALPHA` | `also-wrong` | duplicate |",
                "| `GAMMA` | `three` | unknown |",
            )
        ),
    )
    write(
        tmp_path / "scripts" / "alpha.py",
        "import argparse\nparser = argparse.ArgumentParser()\n",
    )
    write(
        tmp_path / "references" / "command-line-reference.md",
        "| `ghost.py` | unknown | controls | help |\n",
    )

    errors = release_check.documentation_contract_errors(tmp_path)
    joined = "\n".join(errors)
    for message in (
        "duplicate setting row: ALPHA",
        "missing setting: BETA",
        "unknown setting row: GAMMA",
        "default mismatch for ALPHA",
        "missing argparse entry point: alpha.py",
        "unknown argparse entry point: ghost.py",
    ):
        assert message in joined


def test_documentation_contract_accepts_aligned_settings_and_commands(tmp_path):
    write(tmp_path / ".env.example", 'ALPHA="one"\nBETA=\n')
    write(
        tmp_path / "references" / "runtime-configuration.md",
        "| `ALPHA` | `one` | value |\n| `BETA` | blank | value |\n",
    )
    write(
        tmp_path / "scripts" / "alpha.py",
        "import argparse\nparser = argparse.ArgumentParser()\n",
    )
    write(
        tmp_path / "references" / "command-line-reference.md",
        "| `alpha.py` | command | controls | help |\n",
    )
    assert release_check.documentation_contract_errors(tmp_path) == []
    assert release_check.documentation_contract_errors(tmp_path / "absent") == []


def test_documentation_contract_requires_provider_declarations_to_match_code(tmp_path):
    write(
        tmp_path / "scripts" / "runtime_config.py",
        'LLM_PROVIDERS = {"anthropic", "google", "openai", "openrouter"}\n',
    )
    write(
        tmp_path / ".env.example",
        "# supported-llm-providers: google, openai, openrouter\nALPHA=one\n",
    )
    write(
        tmp_path / "references" / "runtime-configuration.md",
        "<!-- supported-llm-providers: google, openai, openrouter -->\n"
        "| `ALPHA` | `one` | value |\n",
    )

    errors = release_check.documentation_contract_errors(tmp_path)
    assert len([error for error in errors if "provider declaration mismatch" in error]) == 2

    providers = "anthropic, google, openai, openrouter"
    write(
        tmp_path / ".env.example",
        f"# supported-llm-providers: {providers}\nALPHA=one\n",
    )
    write(
        tmp_path / "references" / "runtime-configuration.md",
        f"<!-- supported-llm-providers: {providers} -->\n| `ALPHA` | `one` | value |\n",
    )
    assert release_check.documentation_contract_errors(tmp_path) == []


def test_documentation_contract_rejects_ambiguous_provider_sources_and_markers(tmp_path):
    write(
        tmp_path / "scripts" / "runtime_config.py",
        "import os\nLLM_PROVIDERS = providers_from_environment()\n",
    )
    write(
        tmp_path / ".env.example",
        "# supported-llm-providers: openai\n# supported-llm-providers: anthropic, openai\n",
    )

    errors = release_check.documentation_contract_errors(tmp_path)
    assert errors == [
        "scripts/runtime_config.py: LLM_PROVIDERS must be a literal collection",
        ".env.example: expected exactly one supported-llm-providers: declaration",
    ]


def test_provider_source_parser_accepts_literal_sequences_and_rejects_other_shapes(tmp_path):
    source = tmp_path / "runtime_config.py"
    write(source, 'LLM_PROVIDERS = ["anthropic", "openai"]\n')
    assert release_check._llm_provider_set(source) == {"anthropic", "openai"}

    write(source, 'LLM_PROVIDERS = ("google", "openrouter")\n')
    assert release_check._llm_provider_set(source) == {"google", "openrouter"}

    write(source, 'LLM_PROVIDERS = {"openai", 1}\n')
    assert release_check._llm_provider_set(source) == set()

    write(source, 'import os\nOTHER = {"openai"}\nregistry["providers"] = {"google"}\n')
    assert release_check._llm_provider_set(source) == set()


def test_documentation_contract_requires_operator_supplied_process_variables(tmp_path):
    write(tmp_path / ".env.example", "ALPHA=one\n")
    write(
        tmp_path / "references" / "runtime-configuration.md",
        "| `ALPHA` | `one` | value |\n",
    )
    write(tmp_path / "scripts" / "run_retrieval_mcp.sh", "#!/usr/bin/env bash\n")
    errors = release_check.documentation_contract_errors(tmp_path)
    assert errors == [
        "runtime configuration missing process setting: BUSINESS_DOCUMENT_IGNORE_PROJECT_ENV",
        "runtime configuration missing process setting: BUSINESS_DOCUMENT_RETRIEVAL_DB",
        "runtime configuration missing process setting: PYTHON_BIN",
    ]

    write(
        tmp_path / "references" / "runtime-configuration.md",
        "\n".join(
            (
                "| `ALPHA` | `one` | value |",
                "| `BUSINESS_DOCUMENT_IGNORE_PROJECT_ENV` | `false` | value |",
                "| `BUSINESS_DOCUMENT_RETRIEVAL_DB` | `business_retrieval.sqlite` | value |",
                "| `PYTHON_BIN` | `.venv/bin/python` | value |",
            )
        ),
    )
    assert release_check.documentation_contract_errors(tmp_path) == []


def test_release_tag_contract_requires_semver_tag_and_matching_release_documents(tmp_path):
    minimal_release(tmp_path)
    write(tmp_path / "RELEASE.md", "# Release 1.0.0\n")
    write(tmp_path / "CHANGELOG.md", "## 1.0.0 — 2026-08-13\n")

    assert release_check.release_tag_errors(tmp_path, "v1.0.0") == []

    write(tmp_path / "RELEASE.md", "# Release 0.9.0\n")
    write(tmp_path / "CHANGELOG.md", "## 0.9.0 — 2026-08-13\n")
    errors = "\n".join(release_check.release_tag_errors(tmp_path, "release-1.0.0"))
    assert "semantic-version tag" in errors
    assert "RELEASE.md" in errors
    assert "CHANGELOG.md" in errors


def test_check_release_applies_requested_tag_contract(monkeypatch, tmp_path):
    minimal_release(tmp_path)
    monkeypatch.setattr(release_check, "documentation_files", lambda root: [])
    monkeypatch.setattr(release_check, "repository_data_errors", lambda root: [])
    monkeypatch.setattr(release_check, "documentation_contract_errors", lambda root: [])
    monkeypatch.setattr(release_check, "release_tag_errors", lambda root, tag: [f"tag {tag}"])
    assert release_check.check_release(tmp_path, tag="v1.0.0")["status"] == "blocked"


def test_a_documented_setting_no_code_reads_blocks_the_release(tmp_path):
    """Documentation can agree with itself perfectly about a setting nothing reads.

    That is how a lane ran single-threaded for a corpus while
    `GOOGLE_VERTEX_AI_MAX_WORKERS` sat documented, defaulted, and exported.
    """
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (tmp_path / ".env.example").write_text("READ_ME=1\nNOBODY_READS_ME=2\n")
    (scripts / "thing.py").write_text('env_int("READ_ME", 1)\n')
    errors = release_check.unread_setting_errors(tmp_path)
    assert len(errors) == 1
    assert "NOBODY_READS_ME" in errors[0]


def test_a_setting_name_the_code_assembles_counts_as_read(tmp_path):
    """`lane_model` composes f"{prefix}_{suffix}_MODEL"; that is still reading it."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (tmp_path / ".env.example").write_text(
        "OPENAI_CONSENSUS_MODEL=a\nCLIENT_REVIEW_LANE_TABLES_THRESHOLD=1\nUNREAD_ONE=2\n"
    )
    (scripts / "thing.py").write_text(
        'env_value(f"{prefix}_{suffix}_MODEL", d)\n'
        'env_int(f"CLIENT_REVIEW_LANE_{suffix}_THRESHOLD", 1)\n'
    )
    errors = release_check.unread_setting_errors(tmp_path)
    assert [e for e in errors if "UNREAD_ONE" in e]
    assert not [e for e in errors if "CONSENSUS_MODEL" in e or "THRESHOLD" in e]


def test_a_format_string_that_is_not_a_setting_name_matches_nothing(tmp_path):
    """Matching f-strings generally made the check pass while reading nothing."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (tmp_path / ".env.example").write_text("ANYTHING_AT_ALL=1\n")
    (scripts / "thing.py").write_text('name = f"{index:06d}_{label}"\nother = f"{a}{b}"\n')
    errors = release_check.unread_setting_errors(tmp_path)
    assert len(errors) == 1 and "ANYTHING_AT_ALL" in errors[0]


def test_a_repository_without_the_files_reports_nothing(tmp_path):
    assert release_check.unread_setting_errors(tmp_path) == []


def test_a_setting_the_code_reads_and_nobody_documented_blocks_the_release(tmp_path):
    """The direction `unread_setting_errors` never checked.

    An operator configures a run from `.env.example` and the runtime reference.
    A setting read by the code and named in neither is invisible: its default is
    the only value it will ever have, and nobody knows the knob is there.
    """
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (tmp_path / ".env.example").write_text("DOCUMENTED_ONE=1\n")
    (scripts / "thing.py").write_text(
        'env_int("DOCUMENTED_ONE", 1)\nenv_value("SECRET_KNOB", "")\n'
    )
    errors = release_check.undocumented_setting_errors(tmp_path)
    assert len(errors) == 1
    assert "SECRET_KNOB" in errors[0]
    assert ".env.example" in errors[0]


def test_the_undocumented_setting_rule_reaches_parsers_below_the_top_level(tmp_path):
    scripts = tmp_path / "scripts" / "client_review"
    scripts.mkdir(parents=True)
    (tmp_path / ".env.example").write_text("")
    (scripts / "llm.py").write_text('os.environ.get("BURIED_KNOB")\n')
    errors = release_check.undocumented_setting_errors(tmp_path)
    assert len(errors) == 1 and "BURIED_KNOB" in errors[0]


def test_a_subscript_read_below_the_top_level_is_a_read(tmp_path):
    """`os.environ["NAME"]` reads a setting as surely as `os.environ.get("NAME")`.

    Only a narrower copy of this rule in the test suite matched the subscript,
    and it looked at `scripts/*.py` alone, so a subscript read in a package
    below the top level was checked by nothing.
    """
    scripts = tmp_path / "scripts" / "client_review"
    scripts.mkdir(parents=True)
    (tmp_path / ".env.example").write_text("")
    (scripts / "llm.py").write_text('root = os.environ["BURIED_KNOB"]\n')
    errors = release_check.undocumented_setting_errors(tmp_path)
    assert len(errors) == 1 and "BURIED_KNOB" in errors[0]


def test_a_process_level_setting_is_not_asked_for_in_the_env_example(tmp_path):
    """A switch that turns `.env` off cannot live in `.env`; its row is in the runtime reference."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (tmp_path / ".env.example").write_text("")
    (scripts / "runtime_config.py").write_text(
        'env_bool("BUSINESS_DOCUMENT_IGNORE_PROJECT_ENV", False)\n'
    )
    assert release_check.undocumented_setting_errors(tmp_path) == []


def test_an_uppercase_constant_that_is_not_a_setting_is_not_reported(tmp_path):
    """Anchoring on the reader is what keeps this from sweeping in every constant."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (tmp_path / ".env.example").write_text("")
    (scripts / "thing.py").write_text(
        'ARTIFACT_TYPE = "applied_corroboration_v1"\nlookup = {"SOME_KEY": 1}\n'
    )
    assert release_check.undocumented_setting_errors(tmp_path) == []


def test_the_undocumented_setting_rule_needs_both_files(tmp_path):
    assert release_check.undocumented_setting_errors(tmp_path) == []


def test_every_setting_the_repository_reads_is_documented():
    assert release_check.undocumented_setting_errors(ROOT) == []


def test_a_module_without_a_docstring_blocks_the_release(tmp_path):
    """The contract in this repository lives in prose next to the code."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "silent.py").write_text("VALUE = 1\n")
    errors = release_check.undocumented_definition_errors(tmp_path)
    assert errors == ["scripts/silent.py: module has no docstring"]


def test_a_public_helper_without_a_docstring_blocks_the_release(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "thing.py").write_text('"""Module."""\n\n\ndef helper():\n    return 1\n')
    errors = release_check.undocumented_definition_errors(tmp_path)
    assert errors == ["scripts/thing.py:4: helper has no docstring"]


def test_the_docstring_rule_exempts_main_dunders_privates_and_local_helpers(tmp_path):
    """Requiring a docstring on a local helper produces filler, which is worse."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "thing.py").write_text(
        '"""Module."""\n'
        "\n\n"
        "def main():\n"
        "    def local():\n"
        "        return 1\n"
        "    return local()\n"
        "\n\n"
        "def _private():\n"
        "    return 1\n"
        "\n\n"
        "class Thing:\n"
        '    """A thing."""\n'
        "\n"
        "    def __init__(self):\n"
        "        self.value = 1\n"
    )
    assert release_check.undocumented_definition_errors(tmp_path) == []


def test_the_docstring_rule_reaches_modules_below_the_top_level(tmp_path):
    scripts = tmp_path / "scripts" / "client_review"
    scripts.mkdir(parents=True)
    (scripts / "buried.py").write_text('"""Module."""\n\n\ndef helper():\n    return 1\n')
    errors = release_check.undocumented_definition_errors(tmp_path)
    assert errors == ["scripts/client_review/buried.py:4: helper has no docstring"]


def test_the_docstring_rule_needs_a_scripts_directory(tmp_path):
    assert release_check.undocumented_definition_errors(tmp_path) == []


def test_every_shipped_module_and_public_definition_explains_itself():
    assert release_check.undocumented_definition_errors(ROOT) == []
