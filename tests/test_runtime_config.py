"""Tests for root-local runtime configuration without provider calls."""

import ast
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

runtime_config = importlib.import_module("runtime_config")


def test_runtime_configuration_loader_and_typed_values(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# comment\n"
        "export SAMPLE_VALUE=from-file\n"
        "SINGLE_QUOTED='two words'\n"
        'DOUBLE_QUOTED="three words"\n'
        "INLINE_COMMENT=value # ignored\n"
        "EMPTY_VALUE=\n"
    )
    environ = {"SAMPLE_VALUE": "from-shell"}
    loaded = runtime_config.load_project_env(env_path, environ)
    assert loaded == {
        "SAMPLE_VALUE": "from-file",
        "SINGLE_QUOTED": "two words",
        "DOUBLE_QUOTED": "three words",
        "INLINE_COMMENT": "value",
        "EMPTY_VALUE": "",
    }
    assert environ["SAMPLE_VALUE"] == "from-shell"
    assert environ["DOUBLE_QUOTED"] == "three words"
    assert runtime_config.parse_env_value("plain#kept", env_path, 1) == "plain#kept"
    with pytest.raises(ValueError, match="unterminated"):
        runtime_config.parse_env_value("'missing", env_path, 2)
    env_path.write_text("not valid\n")
    with pytest.raises(ValueError, match="UPPERCASE"):
        runtime_config.load_project_env(env_path, {})
    assert runtime_config.load_project_env(tmp_path / "missing", {}) == {}

    project_env = tmp_path / "project.env"
    project_env.write_text("PROJECT_VALUE=loaded\n")
    monkeypatch.setattr(runtime_config, "project_env_path", lambda: project_env)
    monkeypatch.delenv("PROJECT_VALUE", raising=False)
    assert runtime_config.load_project_env()["PROJECT_VALUE"] == "loaded"
    assert os.environ["PROJECT_VALUE"] == "loaded"
    assert runtime_config.project_env_path() == project_env

    monkeypatch.delenv("BOOL_SETTING", raising=False)
    assert runtime_config.env_bool("BOOL_SETTING", True)
    monkeypatch.setenv("BOOL_SETTING", "NO")
    assert not runtime_config.env_bool("BOOL_SETTING", True)
    monkeypatch.setenv("BOOL_SETTING", "yes")
    assert runtime_config.env_bool("BOOL_SETTING", False)
    monkeypatch.setenv("BOOL_SETTING", "maybe")
    with pytest.raises(ValueError, match="BOOL_SETTING"):
        runtime_config.env_bool("BOOL_SETTING", False)
    monkeypatch.setenv("VALUE_SETTING", " value ")
    assert runtime_config.env_value("VALUE_SETTING", "default") == "value"
    monkeypatch.setenv("VALUE_SETTING", " ")
    assert runtime_config.env_value("VALUE_SETTING", "default") == "default"
    monkeypatch.setenv("INT_SETTING", "7")
    assert runtime_config.env_int("INT_SETTING", 1) == 7
    monkeypatch.setenv("INT_SETTING", "nope")
    with pytest.raises(ValueError, match="integer"):
        runtime_config.env_int("INT_SETTING", 1)
    monkeypatch.setenv("FLOAT_SETTING", "2.5")
    assert runtime_config.env_float("FLOAT_SETTING", 1.0) == 2.5
    monkeypatch.setenv("FLOAT_SETTING", "nope")
    with pytest.raises(ValueError, match="number"):
        runtime_config.env_float("FLOAT_SETTING", 1.0)
    monkeypatch.setenv("LLM_EXTRACT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "openai-model")
    assert runtime_config.llm_provider() == "openai"
    assert runtime_config.llm_model() == "openai-model"
    monkeypatch.setenv("LLM_EXTRACT_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_VERTEX_AI_MODEL", "google-model")
    assert runtime_config.llm_model() == "google-model"
    monkeypatch.setenv("LLM_EXTRACT_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_MODEL", "router-model")
    assert runtime_config.llm_model() == "router-model"
    assert runtime_config.llm_model("openai") == "openai-model"
    with pytest.raises(ValueError, match="provider"):
        runtime_config.llm_model("other")
    monkeypatch.setenv("LLM_EXTRACT_PROVIDER", "other")
    with pytest.raises(ValueError, match="LLM_EXTRACT_PROVIDER"):
        runtime_config.llm_provider()


def test_named_lane_provider_and_model_resolution(monkeypatch):
    monkeypatch.setenv("LLM_CONSENSUS_PRI_PROVIDER", "openai")
    monkeypatch.setenv("LLM_CONSENSUS_SEC_PROVIDER", "google")
    monkeypatch.setenv("LLM_REASONING_PROVIDER", "openai")
    monkeypatch.setenv("GOOGLE_VERTEX_AI_CONSENSUS_MODEL", "gemini-consensus")
    monkeypatch.setenv("GOOGLE_VERTEX_AI_REASONING_MODEL", "gemini-reasoning")
    monkeypatch.setenv("OPENAI_CONSENSUS_MODEL", "gpt-consensus")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-reasoning")
    assert runtime_config.lane_configuration("consensus_primary") == {
        "lane": "consensus_primary",
        "provider": "openai",
        "model": "gpt-consensus",
    }
    assert runtime_config.lane_configuration("consensus_secondary")["model"] == "gemini-consensus"
    assert runtime_config.lane_configuration("reasoning")["model"] == "gpt-reasoning"
    assert runtime_config.lane_model("extraction", "openai") == runtime_config.llm_model("openai")
    assert runtime_config.provider_credential_env("openai") == "OPENAI_API_KEY"
    assert runtime_config.provider_credential_env("google") == "GOOGLE_APPLICATION_CREDENTIALS"
    assert runtime_config.provider_credential_env("openrouter") == "OPENROUTER_API_KEY"
    with pytest.raises(ValueError, match="lane must be"):
        runtime_config.lane_provider("unknown")
    monkeypatch.setenv("LLM_REASONING_PROVIDER", "other")
    with pytest.raises(ValueError, match="LLM_REASONING_PROVIDER"):
        runtime_config.lane_provider("reasoning")
    with pytest.raises(ValueError, match="provider must be"):
        runtime_config.lane_model("reasoning", "other")
    with pytest.raises(ValueError, match="provider must be"):
        runtime_config.provider_credential_env("other")


def test_every_env_example_setting_is_documented():
    """Keep the checked-in configuration template aligned with its runbook."""
    repository = Path(__file__).resolve().parents[1]
    example_keys = {
        line.split("=", 1)[0].strip()
        for line in (repository / ".env.example").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }
    runtime_configuration = (repository / "references/runtime-configuration.md").read_text()
    missing = sorted(f"`{key}`" for key in example_keys if f"`{key}`" not in runtime_configuration)
    assert missing == [], f".env.example settings missing from runtime documentation: {missing}"


def _import_time_roots(body):
    """Return the parts of a module or class body that Python runs while importing it."""
    roots = []
    for node in body:
        if isinstance(node, ast.If) and "__name__" in ast.unparse(node.test):
            continue  # a script's __main__ block does not run on import
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            roots += [*node.decorator_list, *node.args.defaults]
            roots += [value for value in node.args.kw_defaults if value is not None]
        elif isinstance(node, ast.ClassDef):
            roots += [*node.decorator_list, *node.bases, *node.keywords]
            roots += _import_time_roots(node.body)
        else:
            roots.append(node)
    return roots


def _import_time_configuration_reads(source):
    """Return the lines where a module reads configuration while it is being imported.

    A read is ``os.environ`` or ``os.getenv``, or a call into ``runtime_config``.
    Function and lambda bodies run later and are skipped; their default values
    and decorators run at import and are not.
    """
    tree = ast.parse(source)
    accessors = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "runtime_config"
        for alias in node.names
    }
    lines = set()
    pending = _import_time_roots(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
            lines.add(node.lineno)
        if isinstance(node, ast.Call):
            called = ast.unparse(node.func)
            if called in accessors or called.startswith("runtime_config."):
                lines.add(node.lineno)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Lambda):
                pending += child.args.defaults
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                pending += _import_time_roots([child])
            else:
                pending.append(child)
    return sorted(lines)


def test_no_script_reads_configuration_while_it_is_imported():
    """Configuration is read when a command runs, never when its module is imported.

    Test collection imports every script a test module imports before the
    suite's isolation begins. ``retrieval_sidecar.py`` called
    ``load_project_env()`` at import, so a checkout holding the operator's
    ``.env`` put its settings into the whole test process, and one test failed
    there and nowhere else. A default resolved at import captures the operator's
    value past every later ``monkeypatch`` in the same way. The whole tree is
    walked, packages behind thin entry points included.
    """
    control = (
        "import os\n"
        "import runtime_config\n"
        "from runtime_config import env_bool, load_project_env\n"
        "load_project_env()\n"
        "HOST = os.environ.get('HOST')\n"
        "def build(enabled=env_bool('A_ENABLED', False)):\n"
        "    return env_bool('B_ENABLED', False)\n"
        "class Settings:\n"
        "    port = os.getenv('PORT')\n"
        "    def read(self):\n"
        "        return os.environ['C']\n"
        "late = lambda: runtime_config.env_int('D', 1)\n"
        "if __name__ == '__main__':\n"
        "    load_project_env()\n"
        "ENV_FILE = runtime_config.project_env_path()\n"
    )
    # A scan that cannot see a read would pass the tree vacuously.
    assert _import_time_configuration_reads(control) == [4, 5, 6, 9, 15]
    scripts = sorted(SCRIPTS.rglob("*.py"))
    assert any(path.parent.name == "client_review" for path in scripts)
    offenders = [
        f"{path.relative_to(SCRIPTS)}:{line}"
        for path in scripts
        for line in _import_time_configuration_reads(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_the_suite_withholds_every_documented_setting(monkeypatch, withhold_project_settings):
    """A setting is the project's when ``.env.example`` documents it, whatever its prefix.

    The suite's isolation cleared a hand-kept list of prefixes. It lacked
    ``SLOT_EQUIVALENCE_`` and ``ANTHROPIC_``, and ``BUSINESS_DOC_RUN_ROOT``,
    ``HOST``, ``PORT`` and ``LOG_LEVEL`` fit no prefix, so an operator's
    ``SLOT_EQUIVALENCE_ENABLED`` reached every test and enabled a lane in one that
    never asked for it.
    """
    documented = runtime_config.load_project_env(SCRIPTS.parent / ".env.example", {})
    assert "SLOT_EQUIVALENCE_ENABLED" in documented
    for name in documented:
        monkeypatch.setenv(name, "inherited")
    withhold_project_settings(monkeypatch)
    assert sorted(documented.keys() & os.environ.keys()) == []
    assert "PATH" in os.environ


def test_provider_mains_use_env_defaults_and_reject_invalid_configuration(
    monkeypatch, tmp_path, capsys
):
    import address_normalize
    import openai_adapter

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"pages": []}))
    openai_called = {}
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("OPENAI_MODEL", "configured-model")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "high")
    monkeypatch.setenv("OPENAI_INPUT_MODE", "pdf")
    monkeypatch.setenv("OPENAI_MAX_PAGES", "7")
    monkeypatch.setenv("OPENAI_MAX_PDF_BYTES", "8")
    monkeypatch.setenv("OPENAI_MAX_TEXT_CHARS", "9")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "1")
    monkeypatch.setattr(openai_adapter, "build_client", lambda *_args: object())
    monkeypatch.setattr(
        openai_adapter,
        "run_adapter",
        lambda *args: openai_called.setdefault("args", args) and {"pages": 1},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            openai_adapter.__file__,
            str(manifest),
            "--out",
            str(tmp_path / "engine.json"),
            "--adapter-out",
            str(tmp_path / "adapter.json"),
            "--exceptions",
            str(tmp_path / "exceptions.json"),
            "--raw-dir",
            str(tmp_path / "raw"),
        ],
    )
    openai_adapter.main()
    assert openai_called["args"][5] == "configured-model"
    assert openai_called["args"][7:] == ("pdf", 7, 8, 9, 10.0, 1, "high", "OPENAI_API_KEY")
    assert json.loads(capsys.readouterr().out)["pages"] == 1

    address_input = tmp_path / "address-input.json"
    address_input.write_text(
        json.dumps({"document_id": "doc", "buyer_address": "1 Main St, Boston, MA 02110"})
    )
    google_called = {}
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "not-a-real-key")
    monkeypatch.setenv("GOOGLE_ADDRESS_VALIDATION_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_MAX_REQUESTS", "1")
    monkeypatch.setenv("GOOGLE_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("GOOGLE_USPS_CASS_ENABLED", "false")
    monkeypatch.setattr(
        address_normalize,
        "apply_google_validation",
        lambda records, *args: google_called.setdefault("args", args) and ([], 1),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            address_normalize.__file__,
            str(address_input),
            "--out",
            str(tmp_path / "address-output.json"),
            "--exceptions",
            str(tmp_path / "address-exceptions.json"),
            "--quiet",
        ],
    )
    address_normalize.main()
    assert google_called["args"] == ("not-a-real-key", 1, 2.0, False)

    monkeypatch.setenv("GOOGLE_ADDRESS_VALIDATION_ENABLED", "invalid")
    with pytest.raises(SystemExit, match="GOOGLE_ADDRESS_VALIDATION_ENABLED"):
        address_normalize.main()
    monkeypatch.setenv("OPENAI_MAX_PAGES", "invalid")
    with pytest.raises(SystemExit, match="OPENAI_MAX_PAGES"):
        openai_adapter.main()


def test_an_operator_cap_above_the_model_limit_is_refused_before_work(monkeypatch):
    """A recorded model limit nothing compares against is the same as no limit.

    Raising the per-file cap past what Vertex accepts would otherwise surface as
    a provider error partway through a paid run.
    """
    monkeypatch.setenv("GOOGLE_VERTEX_AI_MAX_PDF_BYTES", "60000000")
    errors = runtime_config.google_capability_errors()
    assert any("GOOGLE_VERTEX_AI_MODEL_MAX_FILE_BYTES" in error for error in errors)
    with pytest.raises(ValueError, match="exceeds"):
        runtime_config.require_google_capability_limits()


def test_a_page_cap_above_the_files_per_request_limit_is_refused(monkeypatch):
    monkeypatch.setenv("GOOGLE_VERTEX_AI_MAX_PAGES", "4000")
    errors = runtime_config.google_capability_errors()
    assert any("GOOGLE_VERTEX_AI_MODEL_MAX_FILES_PER_REQUEST" in error for error in errors)


def test_the_shipped_google_defaults_are_within_the_model_limits(monkeypatch):
    for name in (
        "GOOGLE_VERTEX_AI_MAX_PAGES",
        "GOOGLE_VERTEX_AI_MAX_PDF_BYTES",
        "GOOGLE_VERTEX_AI_MODEL_MAX_FILE_BYTES",
        "GOOGLE_VERTEX_AI_MODEL_MAX_FILES_PER_REQUEST",
        "GOOGLE_VERTEX_AI_MODEL_MAX_INPUT_BYTES",
    ):
        monkeypatch.delenv(name, raising=False)
    assert runtime_config.google_capability_errors() == []
    assert runtime_config.require_google_capability_limits() is None


def test_an_alias_slug_resolves_to_the_real_vendor():
    """`~openai/...` would otherwise compare unequal to `openai` and pass as independent."""
    assert runtime_config.model_vendor("openrouter", "~openai/gpt-mini-latest") == "openai"
    assert (
        runtime_config.model_vendor("openrouter", "~anthropic/claude-haiku-latest") == "anthropic"
    )


def test_an_auto_routing_slug_cannot_promise_a_vendor():
    """`openrouter/auto` picks a model at request time; it may be the primary's own."""
    for slug in ("openrouter/auto", "openrouter/auto-beta"):
        assert (
            runtime_config.model_vendor("openrouter", slug)
            == runtime_config.UNRESOLVED_MODEL_VENDOR
        )


def test_an_alias_of_the_primary_vendor_is_refused_as_a_buddy():
    lane = importlib.import_module("client_review.iterative")
    with pytest.raises(ValueError, match="a router does not create independence"):
        lane.validate_reviewer_roles(
            "openai", "openrouter", "gpt-5.6-luna", "~openai/gpt-mini-latest"
        )


def test_an_auto_routed_buddy_is_refused():
    lane = importlib.import_module("client_review.iterative")
    with pytest.raises(ValueError, match="vendor-prefixed model slug is required"):
        lane.validate_reviewer_roles("openai", "openrouter", "gpt-5.6-luna", "openrouter/auto")


def test_a_third_consensus_lane_can_be_produced_not_only_accepted(monkeypatch):
    """`consensus.py` admitted `consensus_tiebreaker`; no adapter could emit one.

    A lane a control accepts and no command can produce is a lane that does not
    exist in practice, and the only way to read a corpus with a third vendor was
    to label it the secondary -- which is the lane a later reader trusts to be
    the second of a pair.
    """
    assert "consensus_tiebreaker" in runtime_config.LLM_LANES
    monkeypatch.setenv("LLM_CONSENSUS_TIE_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_CONSENSUS_MODEL", "claude-consensus")
    assert runtime_config.lane_configuration("consensus_tiebreaker") == {
        "lane": "consensus_tiebreaker",
        "provider": "anthropic",
        "model": "claude-consensus",
    }


def test_the_tiebreaker_lane_has_no_default_provider(monkeypatch):
    """A third reading nobody chose is not a third reading.

    Every other lane defaults to a provider. This one must not: a tiebreaker
    that quietly resolves to the vendor already reading another lane produces
    one reading wearing two names, and consensus would be comparing an engine
    with itself.
    """
    monkeypatch.delenv("LLM_CONSENSUS_TIE_PROVIDER", raising=False)
    with pytest.raises(ValueError, match="LLM_CONSENSUS_TIE_PROVIDER must be set"):
        runtime_config.lane_provider("consensus_tiebreaker")
