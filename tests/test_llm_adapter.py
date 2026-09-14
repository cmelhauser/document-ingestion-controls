"""Tests for run-level OpenAI versus Google provider selection."""

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

adapter = importlib.import_module("llm_adapter")


def arguments():
    return type(
        "Arguments",
        (),
        {
            "manifest": "manifest.json",
            "out": "out.json",
            "adapter_out": "adapter.json",
            "exceptions": "exceptions.json",
            "raw_dir": "raw",
            "model": None,
            "credential_env": None,
            "input_mode": None,
            "max_pages": None,
            "max_pdf_bytes": None,
            "max_text_chars": None,
            "timeout_seconds": None,
            "max_retries": None,
            "reasoning_effort": None,
            "project_id": None,
            "location": None,
            "max_workers": None,
            "cache_dir": None,
            "retry_backoff_seconds": None,
        },
    )()


def test_provider_defaults_and_validation(monkeypatch):
    monkeypatch.setattr(adapter, "env_value", lambda name, default: f"value-{name}")
    monkeypatch.setattr(adapter, "env_int", lambda name, default: default + 1)
    monkeypatch.setattr(adapter, "env_float", lambda name, default: default + 1)
    monkeypatch.setattr(adapter, "llm_model", lambda provider: f"value-{provider}")
    openai = adapter.provider_defaults("openai")
    google = adapter.provider_defaults("google")
    openrouter = adapter.provider_defaults("openrouter")
    anthropic = adapter.provider_defaults("anthropic")
    assert anthropic["model"] == "value-anthropic"
    assert anthropic["input_mode"] == "value-ANTHROPIC_INPUT_MODE"
    assert anthropic["credential_env"] == "value-ANTHROPIC_CREDENTIAL_ENV"
    assert openai["model"] == "value-openai" and openai["max_pages"] == 1001
    assert google["model"] == "value-google" and google["location"]
    assert openrouter["model"] == "value-openrouter"
    with pytest.raises(ValueError, match="openai.*google"):
        adapter.provider_defaults("other")


def test_anthropic_defaults_to_reading_the_page_itself():
    """Its unmonkeypatched default input mode is the retained PDF.

    Anthropic reads a page PDF natively as a document block, so the recorded
    vendor is the one that read the glyphs. Defaulting to extracted text would
    quietly make it a reader of someone else's OCR.
    """
    assert adapter.provider_defaults("anthropic")["input_mode"] == "pdf"


def test_contained_lane_lock_refuses_a_second_live_invocation(tmp_path):
    """Recovery paths cannot bypass the one-active-invocation-per-lane rule."""
    with adapter.lane_execution_lock("consensus_secondary", tmp_path):
        with pytest.raises(ValueError, match="already active"):
            with adapter.lane_execution_lock("consensus_secondary", tmp_path):
                pass

    # Releasing the first process lock makes a later, separate recovery legal.
    with adapter.lane_execution_lock("consensus_secondary", tmp_path):
        pass


def test_contained_lane_lock_fails_closed_without_posix_locking(tmp_path, monkeypatch):
    """A controlled run must not silently permit concurrent provider spend."""
    monkeypatch.setattr(adapter, "fcntl", None)
    with pytest.raises(ValueError, match="requires a POSIX host"):
        with adapter.lane_execution_lock("consensus_secondary", tmp_path):
            pass


def test_run_openai_and_google_route_arguments(monkeypatch):
    openai_calls, google_calls = [], []
    monkeypatch.setattr(
        adapter.openai_adapter, "build_client", lambda *args: openai_calls.append(args) or "o"
    )
    monkeypatch.setattr(
        adapter.openai_adapter,
        "run_adapter",
        lambda *args, **kwargs: openai_calls.append((args, kwargs)) or {"ok": True},
    )
    defaults = {
        "model": "gpt",
        "credential_env": "OPENAI_API_KEY",
        "input_mode": "auto",
        "max_pages": 5,
        "max_pdf_bytes": 6,
        "max_text_chars": 7,
        "timeout_seconds": 8.0,
        "max_retries": 2,
        "reasoning_effort": "medium",
        "max_workers": 8,
        "cache_dir": "cache",
        "retry_backoff_seconds": 1.0,
    }
    assert adapter.run_openai(arguments(), defaults) == {"ok": True}
    assert openai_calls[0] == ("OPENAI_API_KEY", 8.0, 2)
    assert openai_calls[1][0][5:8] == ("gpt", "o", "auto")
    assert openai_calls[1][1] == {
        "max_backoff_seconds": 120.0,
        "lane": "extraction",
    }

    sdk = ("genai", "types")
    monkeypatch.setattr(adapter.google_genai_adapter, "load_sdk", lambda: sdk)
    monkeypatch.setattr(
        adapter.google_genai_adapter,
        "build_client",
        lambda *args: google_calls.append(("client", args)) or "g",
    )
    monkeypatch.setattr(
        adapter.google_genai_adapter,
        "run_adapter",
        lambda *args: google_calls.append(("run", args)) or {"google": True},
    )
    defaults = {
        "model": "gemini",
        "credential_env": "GOOGLE_APPLICATION_CREDENTIALS",
        "input_mode": "pdf",
        "max_pages": 5,
        "max_pdf_bytes": 6,
        "max_text_chars": 7,
        "timeout_seconds": 8.0,
        "max_retries": 2,
        "project_id": "project-12345",
        "location": "us-central1",
        "max_workers": 8,
        "cache_dir": "cache",
        "retry_backoff_seconds": 1.0,
        "max_output_tokens": 65536,
    }
    assert adapter.run_vertex(arguments(), defaults) == {"google": True}
    assert google_calls[0] == ("client", ("project-12345", "us-central1", 8.0, 2, sdk))
    assert google_calls[1][1][5:8] == ("gemini", "g", "pdf")
    assert google_calls[1][1][-2] == "extraction"
    # Reaches the adapter, or a wide page truncates mid-object and is lost.
    assert google_calls[1][1][-1] == 65536


def test_run_openrouter_routes_arguments(monkeypatch):
    calls = []
    monkeypatch.setattr(
        adapter.openrouter_adapter,
        "build_client",
        lambda *args, **kwargs: calls.append(("client", args, kwargs)) or "r",
    )
    monkeypatch.setattr(
        adapter.openrouter_adapter,
        "run_adapter",
        lambda *args, **kwargs: calls.append(("run", args, kwargs)) or {"openrouter": True},
    )
    defaults = {
        "model": "nemotron",
        "credential_env": "OPENROUTER_API_KEY",
        "input_mode": "text",
        "max_pages": 5,
        "max_pdf_bytes": 6,
        "max_text_chars": 7,
        "timeout_seconds": 8.0,
        "max_retries": 2,
        "reasoning_effort": "medium",
        "max_workers": 8,
        "cache_dir": "cache",
        "retry_backoff_seconds": 1.0,
    }
    assert adapter.run_openrouter(arguments(), defaults) == {"openrouter": True}
    # An unrouted lane must reach OpenRouter with no provider preference at all,
    # so the documented default routing is what actually runs.
    assert calls[0] == ("client", ("OPENROUTER_API_KEY", 8.0, 2), {"provider_only": ""})
    assert calls[1][1][5:8] == ("nemotron", "r", "text")
    assert calls[1][2] == {
        "max_backoff_seconds": 120.0,
        "lane": "extraction",
        "image_dpi": adapter.openai_adapter.DEFAULT_IMAGE_DPI,
    }


def test_run_anthropic_routes_arguments(monkeypatch):
    """The Anthropic lane reaches the shared evidence loop with its own client."""
    calls = []
    monkeypatch.setattr(
        adapter.anthropic_adapter,
        "build_client",
        lambda *args: calls.append(("client", args)) or "a",
    )
    monkeypatch.setattr(
        adapter.anthropic_adapter,
        "run_adapter",
        lambda *args, **kwargs: calls.append(("run", args, kwargs)) or {"anthropic": True},
    )
    defaults = {
        "model": "claude-haiku-4-5-20251001",
        "credential_env": "ANTHROPIC_API_KEY",
        "input_mode": "pdf",
        "max_pages": 5,
        "max_pdf_bytes": 6,
        "max_text_chars": 7,
        "timeout_seconds": 8.0,
        "max_retries": 2,
        "reasoning_effort": "medium",
        "max_workers": 8,
        "cache_dir": "cache",
        "retry_backoff_seconds": 1.0,
    }
    assert adapter.run_anthropic(arguments(), defaults) == {"anthropic": True}
    assert calls[0] == ("client", ("ANTHROPIC_API_KEY", 8.0, 2))
    assert calls[1][1][5:8] == ("claude-haiku-4-5-20251001", "a", "pdf")
    assert calls[1][2] == {"max_backoff_seconds": 120.0, "lane": "extraction"}


def test_main_uses_selected_provider_and_reports_errors(monkeypatch, capsys):
    monkeypatch.setattr(adapter, "load_project_env", lambda: None)
    monkeypatch.setattr(adapter, "llm_provider", lambda: "openai")
    monkeypatch.setattr(adapter, "run_openai", lambda args, defaults: {"provider": "openai"})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            adapter.__file__,
            "m",
            "--out",
            "o",
            "--adapter-out",
            "a",
            "--exceptions",
            "e",
            "--raw-dir",
            "r",
        ],
    )
    adapter.main()
    assert json.loads(capsys.readouterr().out) == {"provider": "openai"}
    monkeypatch.setattr(
        adapter, "run_openai", lambda *args: (_ for _ in ()).throw(ValueError("bad"))
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            adapter.__file__,
            "m",
            "--out",
            "o",
            "--adapter-out",
            "a",
            "--exceptions",
            "e",
            "--raw-dir",
            "r",
        ],
    )
    with pytest.raises(SystemExit, match="LLM adapter failed"):
        adapter.main()

    monkeypatch.setattr(adapter, "llm_provider", lambda: "openrouter")
    monkeypatch.setattr(
        adapter, "run_openrouter", lambda args, defaults: {"provider": "openrouter"}
    )
    adapter.main()
    assert json.loads(capsys.readouterr().out) == {"provider": "openrouter"}

    # Anthropic is dispatched from here too; it failed a whole corpus by falling
    # through to the OpenRouter branch before this existed.
    monkeypatch.setattr(adapter, "llm_provider", lambda: "anthropic")
    monkeypatch.setattr(adapter, "run_anthropic", lambda args, defaults: {"provider": "anthropic"})
    adapter.main()
    assert json.loads(capsys.readouterr().out) == {"provider": "anthropic"}
    monkeypatch.setattr(
        adapter, "llm_provider", lambda: (_ for _ in ()).throw(ValueError("bad provider"))
    )
    with pytest.raises(SystemExit, match="LLM adapter failed"):
        adapter.main()
    monkeypatch.setattr(
        adapter, "load_project_env", lambda: (_ for _ in ()).throw(ValueError("bad env"))
    )
    with pytest.raises(SystemExit, match="LLM adapter failed"):
        adapter.main()


def test_main_resolves_named_consensus_lane(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(adapter, "load_project_env", lambda: None)
    monkeypatch.setattr(
        adapter,
        "lane_configuration",
        lambda lane: {"lane": lane, "provider": "google", "model": "lane-model"},
    )
    monkeypatch.setattr(adapter, "provider_defaults", lambda provider, model: {"model": model})
    monkeypatch.setattr(
        adapter,
        "run_vertex",
        lambda args, defaults: {"provider": "google", "model": defaults["model"]},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            adapter.__file__,
            "manifest.json",
            "--out",
            str(tmp_path / "out.json"),
            "--adapter-out",
            str(tmp_path / "adapter.json"),
            "--exceptions",
            str(tmp_path / "exceptions.json"),
            "--raw-dir",
            str(tmp_path / "raw"),
            "--lane",
            "consensus_primary",
        ],
    )
    adapter.main()
    assert json.loads(capsys.readouterr().out) == {"provider": "google", "model": "lane-model"}
