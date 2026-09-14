"""Regression contracts for repository-owned GitHub Actions workflows."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def load_workflow(name):
    """Load a workflow without YAML 1.1 coercing its ``on`` trigger key."""
    # BaseLoader constructs only strings -- it is strictly weaker than safe_load
    # and is used here so YAML 1.1 does not coerce the `on` trigger key to True.
    return yaml.load(
        (WORKFLOWS / name).read_text(),
        Loader=yaml.BaseLoader,  # noqa: S506 - BaseLoader builds only strings
    )


def action_references(workflow):
    """Yield every action reference declared by workflow steps."""
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if "uses" in step:
                yield step["uses"]


def test_quality_workflow_runs_strict_gate_before_acceptance_with_least_privilege():
    """Acceptance runs after the gate, now as a later step rather than a second runner.

    It used to be its own job that repeated the checkout, apt and pip install to
    rebuild the same environment, and `needs: quality` made it wait for the gate
    regardless. As a step it cannot start unless the gate step succeeded, so the
    ordering is stricter and costs one runner instead of two.
    """
    workflow = load_workflow("quality.yml")

    assert set(workflow["on"]) == {"push", "pull_request", "workflow_dispatch", "schedule"}
    assert workflow["on"]["push"]["branches"] == ["main"]
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] == "true"

    quality = workflow["jobs"]["quality"]
    assert quality["timeout-minutes"] == "45"
    assert "acceptance" not in workflow["jobs"]

    runs = [step.get("run", "") for step in quality["steps"]]
    gate_index = runs.index('PYTHON_BIN="$(command -v python)" bash scripts/quality_gate.sh')
    acceptance_index = next(
        index for index, run in enumerate(runs) if "run_acceptance_packet.sh" in run
    )
    assert gate_index < acceptance_index

    acceptance_commands = runs[acceptance_index]
    for command in (
        "scripts/run_acceptance_packet.sh",
        "scripts/run_operations_acceptance.sh",
        "scripts/run_mcp_production_acceptance.sh",
        "fixtures/public_business_document_stress_corpus/run_validation.sh",
    ):
        assert command in acceptance_commands


def test_all_action_references_are_full_commit_pins_and_checkouts_do_not_retain_credentials():
    for workflow_name in ("quality.yml", "release.yml", "render-docs.yml"):
        workflow = load_workflow(workflow_name)
        for action in action_references(workflow):
            _, revision = action.rsplit("@", 1)
            assert len(revision) == 40 and all(char in "0123456789abcdef" for char in revision)
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if str(step.get("uses", "")).startswith("actions/checkout@"):
                    assert step["with"]["persist-credentials"] == "false"


def test_latex_rendering_remains_manually_dispatched():
    workflow = load_workflow("render-docs.yml")

    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}


def test_release_workflow_verifies_an_exact_main_tag_before_publishing():
    workflow = load_workflow("release.yml")

    assert workflow["on"]["push"]["tags"] == ["v*"]
    assert workflow["permissions"] == {"contents": "write"}
    assert workflow["concurrency"]["cancel-in-progress"] == "false"

    release = workflow["jobs"]["verify-and-publish"]
    assert release["timeout-minutes"] == "45"
    commands = "\n".join(step.get("run", "") for step in release["steps"])
    for command in (
        "git rev-parse origin/main",
        'scripts/release_check.py --tag "$GITHUB_REF_NAME"',
        "scripts/quality_gate.sh",
        "scripts/run_acceptance_packet.sh",
        "scripts/run_operations_acceptance.sh",
        "scripts/run_mcp_production_acceptance.sh",
        "fixtures/public_business_document_stress_corpus/run_validation.sh",
        "scripts/generate_docs.sh",
        "scripts/render_docs.sh",
        "git diff --exit-code -- docs/*.tex docs/*.pdf",
        "gh release create",
    ):
        assert command in commands


def test_gating_jobs_install_from_the_hash_pinned_lock():
    """A version pin without hashes still trusts whatever the index serves."""
    workflow = load_workflow("quality.yml")
    commands = " ".join(
        step.get("run", "") for step in workflow["jobs"]["quality"].get("steps", [])
    )
    assert "--require-hashes -r requirements.lock" in commands
    assert "pip install -r requirements-dev.txt" not in commands


def test_the_lock_file_covers_every_declared_requirement():
    lock = (ROOT / "requirements.lock").read_text()
    assert "--hash=sha256:" in lock
    declared = [
        line.split("==")[0].strip().lower()
        for source in ("requirements.txt", "requirements-dev.txt")
        for line in (ROOT / source).read_text().splitlines()
        if line.strip() and not line.startswith(("-r", "#"))
    ]
    locked = {
        line.split("==")[0].strip().lower()
        for line in lock.splitlines()
        if line and not line[0].isspace() and "==" in line
    }
    assert set(declared).issubset(locked), sorted(set(declared) - locked)


def test_a_dependency_audit_job_runs_and_verifies_the_lock_is_current():
    workflow = load_workflow("quality.yml")
    commands = " ".join(
        step.get("run", "") for step in workflow["jobs"]["supply-chain"].get("steps", [])
    )
    assert "pip_audit" in commands
    # Regenerating and diffing catches a lock that drifted from its requirements.
    assert "piptools compile" in commands and "diff -u" in commands


def test_no_workflow_job_runs_an_unproven_mutation_check():
    """mutmut 3.x emits no machine-readable summary in a non-interactive runner.

    A job that cannot produce a result would either always fail or, worse, be
    made to pass by defaulting a count -- which is how the first version of this
    check reported zero surviving mutants while the tool had not run.
    """
    workflow = load_workflow("quality.yml")
    assert "mutation" not in workflow["jobs"]


def test_newer_runtimes_are_advisory_and_never_gate():
    workflow = load_workflow("quality.yml")
    matrix_job = workflow["jobs"]["runtime-matrix"]
    assert matrix_job["continue-on-error"] == "true"
    versions = matrix_job["strategy"]["matrix"]["python-version"]
    assert "3.13" in versions and "3.14" in versions
    # The supported runtime must stay the one that actually gates.
    assert workflow["jobs"]["quality"]["steps"][1]["with"]["python-version"] == "3.12"
    assert "needs" not in matrix_job
    # Advisory work must not spend two runners on every pull request.
    assert matrix_job["if"] == "github.event_name != 'pull_request'"
