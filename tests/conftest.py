"""Isolate every test from the operator's private ``.env``.

The gating CI job runs in a fresh checkout with no ``.env``, so a suite that
reads one answers a different question on a developer machine than it does in
the gate. That is not hypothetical: enabling a disabled-by-default lane in a
local ``.env`` turned a passing run into a failing one, because a CLI's
``--enable`` default is resolved from the environment and the test asserted the
disabled refusal.

A quality gate whose verdict depends on a gitignored file is not a control. Each
test therefore starts from the environment CI sees: no project ``.env``, and no
inherited project settings. A test that needs a setting sets it explicitly with
``monkeypatch``, which runs after this fixture and still wins.

The isolation starts with the session, not with the first test. Collection
imports every test module, and the scripts they import, before any fixture
runs. ``retrieval_sidecar.py`` once loaded ``.env`` at import, so the operator's
settings were in the process before the fixture existed, and the fixture then
cleared only the prefixes it listed -- a list without ``SLOT_EQUIVALENCE_``.
``SLOT_EQUIVALENCE_ENABLED`` reached every later test, and a slot-equivalence
test that never enabled the lane failed for want of a credential: in a checkout
holding a ``.env``, and only in the full suite. A setting now counts as the
project's when ``.env.example`` documents it, whatever its prefix.

A script a test runs as its own process resolves the root ``.env`` for itself,
where no patch in this process reaches: the control-layer workflow, the
acceptance wrappers and every ``--help`` run read the operator's file. The
session therefore also exports ``BUSINESS_DOCUMENT_IGNORE_PROJECT_ENV``, a
process-only switch that every child inherits.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import reauthorize_google  # noqa: E402 - same
import runtime_config  # noqa: E402 - the scripts directory must be importable first

# The namespaces the project and its provider SDKs read. A prefix also catches a
# name no script spells out: the credential a ``*_CREDENTIAL_ENV`` setting points
# at, or ``OPENAI_BASE_URL`` and ``ANTHROPIC_AUTH_TOKEN``, which an SDK reads on
# its own. A variable outside these and outside ``.env.example`` is the shell's
# own (PATH, HOME) and is left alone.
PROJECT_ENV_PREFIXES = (
    "AI_SIMULATED_CLIENT_REVIEW_",
    "ALLOCATION_POLICY_",
    "ANALYTICS_",
    "ANTHROPIC_",
    "ARITHMETIC_",
    "CANONICAL_",
    "CLIENT_REVIEW_",
    "COMPLETENESS_",
    "GOOGLE_",
    "HANDWRITING_",
    "LLM_",
    "OPENAI_",
    "OPENROUTER_",
    "PREPROCESS_",
    "REASSEMBLY_",
    "RETRIEVAL_",
    "SCHEMA_DISCOVERY_",
    "SLOT_EQUIVALENCE_",
    "TABLE_COMPREHENSION_",
)

# Every setting ``.env.example`` documents, read with the project's own parser
# into a throwaway mapping. The prefix list above is a hand-kept copy of that
# file and it went stale: it lacked ``SLOT_EQUIVALENCE_`` and ``ANTHROPIC_``, and
# ``BUSINESS_DOC_RUN_ROOT``, ``HOST``, ``PORT`` and ``LOG_LEVEL`` fit no prefix.
# The release check already refuses a setting a script reads that this file does
# not name, so reading the names from it covers the next lane as well.
DOCUMENTED_SETTINGS = frozenset(runtime_config.load_project_env(ROOT / ".env.example", {}))

# Captured before the session hook and the autouse fixture replace it, so a test
# can still assert what the real resolution does.
REAL_PROJECT_ENV_PATH = runtime_config.project_env_path


def is_project_setting(name):
    """Return whether a variable configures the project, so no test may inherit it."""
    return name.startswith(PROJECT_ENV_PREFIXES) or name in DOCUMENTED_SETTINGS


def _withhold_project_settings(patch):
    """Delete every inherited project setting through ``patch``, which restores it later."""
    for name in list(os.environ):
        if is_project_setting(name):
            patch.delenv(name, raising=False)


def pytest_configure(config):
    """Keep the operator's ``.env`` out of collection as well as out of each test.

    Collection imports every script a test module imports before the autouse
    fixture below exists. For the whole session the project ``.env`` resolves to
    a missing file and inherited project settings are withheld, so nothing that
    runs at import can load or capture them. A script a test starts as its own
    process cannot see that patch; it inherits the exported switch instead.
    """
    session_env = pytest.MonkeyPatch()
    config.add_cleanup(session_env.undo)
    session_dir = tempfile.TemporaryDirectory(prefix="no-project-env-")
    config.add_cleanup(session_dir.cleanup)
    absent = Path(session_dir.name) / ".env"
    session_env.setattr(runtime_config, "project_env_path", lambda: absent)
    _withhold_project_settings(session_env)
    session_env.setenv("BUSINESS_DOCUMENT_IGNORE_PROJECT_ENV", "true")


@pytest.fixture
def real_project_env_path():
    """Return the unpatched project ``.env`` resolver."""
    return REAL_PROJECT_ENV_PATH


@pytest.fixture
def withhold_project_settings():
    """Return the isolation step itself, so a test can check what it withholds."""
    return _withhold_project_settings


def _no_subprocess(command, *, capture=False):
    """Refuse a real credential subprocess from inside the test suite."""
    raise RuntimeError(f"tests must not run external commands: {list(command)}")


@pytest.fixture(autouse=True)
def isolate_project_env(monkeypatch, tmp_path_factory):
    """Give each test the environment the gating CI job runs in.

    Beyond the missing ``.env``, this blocks the gcloud subprocess that mints a
    Google access token. Without it a developer machine with working ADC quietly
    minted real tokens during the suite while CI, which has no gcloud, failed —
    the same local-versus-CI divergence this fixture exists to remove. A test
    that exercises the minting path injects its own ``command_runner``.
    """
    absent = tmp_path_factory.mktemp("no-project-env") / ".env"
    monkeypatch.setattr(runtime_config, "project_env_path", lambda: absent)
    monkeypatch.setattr(reauthorize_google, "run", _no_subprocess)
    _withhold_project_settings(monkeypatch)
