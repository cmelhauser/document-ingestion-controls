"""Tests for fixed run-level provider client selection."""

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

provider = importlib.import_module("llm_provider")


def test_build_client_uses_openai_for_openai_run(monkeypatch):
    monkeypatch.setattr(provider, "llm_provider", lambda: "openai")
    monkeypatch.setattr(provider, "build_openai_client", lambda *args: args)
    assert provider.build_client("OPENAI_API_KEY", 3, 2) == ("OPENAI_API_KEY", 3, 2)


def test_build_client_uses_vertex_for_google_run(monkeypatch):
    monkeypatch.setattr(provider, "llm_provider", lambda: "google")
    monkeypatch.setattr(
        provider,
        "env_value",
        lambda name, default: {"GOOGLE_VERTEX_AI_PROJECT_ID": "project-12345"}.get(name, default),
    )
    monkeypatch.setattr(provider, "env_int", lambda name, default: default)
    google = importlib.import_module("google_genai_adapter")
    monkeypatch.setattr(google, "build_responses_client", lambda *args: args)
    assert provider.build_client("IGNORED", 3, 2) == (
        "project-12345",
        "us-central1",
        3,
        2,
        8192,
    )


def test_build_client_uses_isolated_openrouter_provider(monkeypatch):
    router = importlib.import_module("openrouter_adapter")
    monkeypatch.setattr(router, "build_client", lambda *args: args)
    assert provider.build_client("OPENROUTER_API_KEY", 8.0, 2, "openrouter") == (
        "OPENROUTER_API_KEY",
        8.0,
        2,
    )
