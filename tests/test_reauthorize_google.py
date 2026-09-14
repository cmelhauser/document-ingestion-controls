from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import reauthorize_google


def test_write_document_ai_env_is_restricted_and_sourceable(tmp_path: Path) -> None:
    target = tmp_path / "google.env"

    reauthorize_google.write_document_ai_env(target, "token value")

    assert target.read_text(encoding="utf-8").endswith(
        "export GOOGLE_DOCUMENT_AI_ACCESS_TOKEN='token value'\n"
    )
    assert target.stat().st_mode & 0o777 == 0o600


def test_prompt_and_command_helpers_cover_success_and_failure(tmp_path: Path) -> None:
    with patch("builtins.input", return_value=""):
        assert reauthorize_google.ask("Project", "default") == "default"
    with patch("builtins.input", return_value="yes"):
        assert reauthorize_google.ask_yes_no("Continue") is True
    with patch("builtins.input", return_value=""):
        assert reauthorize_google.ask_yes_no("Continue", True) is True
    with patch("builtins.input", return_value="no"):
        assert reauthorize_google.ask_yes_no("Continue", True) is False
    with patch("builtins.input", return_value="maybe"), pytest.raises(ValueError):
        reauthorize_google.ask_yes_no("Continue")

    with patch.object(reauthorize_google.subprocess, "run") as subprocess_run:
        subprocess_run.return_value.returncode = 0
        subprocess_run.return_value.stdout = "value\n"
        assert reauthorize_google.run(("gcloud", "config"), capture=True) == "value"
        subprocess_run.return_value.returncode = 1
        with pytest.raises(RuntimeError):
            reauthorize_google.run(("gcloud", "bad"))

    with patch.object(reauthorize_google, "run", side_effect=RuntimeError("not configured")):
        assert reauthorize_google.current_project() == ""
    bad_path = tmp_path / "directory"
    bad_path.mkdir()
    with pytest.raises(ValueError, match="regular file"):
        reauthorize_google.write_document_ai_env(bad_path, "token")


def test_main_refreshes_both_auth_flows_without_api_enablement() -> None:
    commands: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_run(command: tuple[str, ...], **kwargs: object) -> str:
        commands.append((command, kwargs))
        return "access-token" if kwargs.get("capture") else ""

    with (
        patch.object(reauthorize_google.shutil, "which", return_value="/usr/bin/gcloud"),
        patch("builtins.input", return_value="n"),
    ):
        assert (
            reauthorize_google.main(
                ["--project", "demo-project", "--skip-api-enable"], command_runner=fake_run
            )
            == 0
        )

    command_list = [command for command, _ in commands]
    assert ("gcloud", "auth", "login") in command_list
    assert ("gcloud", "auth", "application-default", "login") in command_list
    assert (
        "gcloud",
        "auth",
        "application-default",
        "set-quota-project",
        "demo-project",
    ) in command_list
    assert ("gcloud", "auth", "print-access-token") in command_list
    assert ("gcloud", "auth", "application-default", "print-access-token") in command_list


def test_main_returns_two_when_gcloud_is_missing() -> None:
    with patch.object(reauthorize_google.shutil, "which", return_value=None):
        assert reauthorize_google.main(["--project", "demo-project"]) == 2


def test_main_prompts_project_enables_apis_and_writes_token(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []

    def fake_run(command: tuple[str, ...], **kwargs: object) -> str:
        commands.append(command)
        return "token" if kwargs.get("capture") else ""

    answers = iter(["", "yes"])
    with (
        patch.object(reauthorize_google.shutil, "which", return_value="gcloud"),
        patch.object(reauthorize_google, "current_project", return_value="configured-project"),
        patch("builtins.input", side_effect=lambda _prompt: next(answers)),
    ):
        assert (
            reauthorize_google.main(
                ["--token-env-out", str(tmp_path / "token.env")], command_runner=fake_run
            )
            == 0
        )
    assert ("gcloud", "services", "enable", *reauthorize_google.DEFAULT_APIS) in commands


def test_main_can_prompt_for_temporary_token_path(tmp_path: Path) -> None:
    answers = iter(["n", "yes", str(tmp_path / "token.env")])

    def fake_run(_command: tuple[str, ...], **kwargs: object) -> str:
        return "token" if kwargs.get("capture") else ""

    with (
        patch.object(reauthorize_google.shutil, "which", return_value="gcloud"),
        patch("builtins.input", side_effect=lambda _prompt: next(answers)),
    ):
        assert reauthorize_google.main(["--project", "demo-project"], command_runner=fake_run) == 0


def test_main_returns_two_for_missing_project_and_one_for_command_failure() -> None:
    with (
        patch.object(reauthorize_google.shutil, "which", return_value="gcloud"),
        patch.object(reauthorize_google, "current_project", return_value=""),
        patch.dict(reauthorize_google.os.environ, {"GOOGLE_VERTEX_AI_PROJECT_ID": ""}),
        patch("builtins.input", return_value=""),
    ):
        assert reauthorize_google.main([]) == 2

    def fail_run(_command: tuple[str, ...], **_kwargs: object) -> str:
        raise RuntimeError("expired")

    with patch.object(reauthorize_google.shutil, "which", return_value="gcloud"):
        assert reauthorize_google.main(["--project", "demo"], command_runner=fail_run) == 1


def test_an_exported_token_is_used_without_calling_gcloud():
    """CI and secret managers export the token; that must win untouched."""
    calls = []

    def runner(command, *, capture=False):
        calls.append(command)
        return "ya29.minted"

    token = reauthorize_google.access_token(
        "GOOGLE_DOCUMENT_AI_ACCESS_TOKEN",
        environ={"GOOGLE_DOCUMENT_AI_ACCESS_TOKEN": "ya29.exported"},
        command_runner=runner,
    )
    assert token == "ya29.exported"
    assert calls == []


def test_a_missing_token_is_minted_from_application_default_credentials(monkeypatch):
    """No token slot exists in .env, so the lane mints a fresh one at run time."""
    monkeypatch.setattr(reauthorize_google.shutil, "which", lambda name: "/usr/bin/gcloud")
    seen = []

    def runner(command, *, capture=False):
        seen.append(tuple(command))
        return "  ya29.minted  "

    token = reauthorize_google.access_token(
        "GOOGLE_HANDWRITING_OCR_ACCESS_TOKEN", environ={}, command_runner=runner
    )
    assert token == "ya29.minted"
    assert seen == [reauthorize_google.ADC_TOKEN_COMMAND]


def test_a_blank_exported_token_still_mints(monkeypatch):
    monkeypatch.setattr(reauthorize_google.shutil, "which", lambda name: "/usr/bin/gcloud")
    token = reauthorize_google.access_token(
        "GOOGLE_DOCUMENT_AI_ACCESS_TOKEN",
        environ={"GOOGLE_DOCUMENT_AI_ACCESS_TOKEN": "   "},
        command_runner=lambda command, *, capture=False: "ya29.fresh",
    )
    assert token == "ya29.fresh"


def test_a_missing_gcloud_names_the_fix(monkeypatch):
    monkeypatch.setattr(reauthorize_google.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="reauthorize_google.py"):
        reauthorize_google.access_token("GOOGLE_DOCUMENT_AI_ACCESS_TOKEN", environ={})


def test_unavailable_adc_fails_closed_naming_the_fix(monkeypatch):
    monkeypatch.setattr(reauthorize_google.shutil, "which", lambda name: "/usr/bin/gcloud")

    def runner(command, *, capture=False):
        raise RuntimeError("Command failed (1)")

    with pytest.raises(ValueError, match="Application Default Credentials"):
        reauthorize_google.access_token(
            "GOOGLE_DOCUMENT_AI_ACCESS_TOKEN", environ={}, command_runner=runner
        )


def test_an_empty_minted_token_is_refused(monkeypatch):
    monkeypatch.setattr(reauthorize_google.shutil, "which", lambda name: "/usr/bin/gcloud")
    with pytest.raises(ValueError, match="returned\nno token|returned no token"):
        reauthorize_google.access_token(
            "GOOGLE_DOCUMENT_AI_ACCESS_TOKEN",
            environ={},
            command_runner=lambda command, *, capture=False: "   ",
        )


def test_access_token_defaults_to_the_process_environment(monkeypatch):
    monkeypatch.setenv("GOOGLE_DOCUMENT_AI_ACCESS_TOKEN", "ya29.from-process-env")
    assert (
        reauthorize_google.access_token("GOOGLE_DOCUMENT_AI_ACCESS_TOKEN")
        == "ya29.from-process-env"
    )


def test_a_refreshing_token_mints_once_and_caches(monkeypatch):
    """Holding the source must not mint a token per request."""
    monkeypatch.setattr(reauthorize_google.shutil, "which", lambda name: "/usr/bin/gcloud")
    minted = []

    def runner(command, *, capture=False):
        minted.append(tuple(command))
        return f"ya29.minted-{len(minted)}"

    source = reauthorize_google.RefreshingToken(
        "GOOGLE_DOCUMENT_AI_ACCESS_TOKEN", environ={}, command_runner=runner
    )
    assert source() == "ya29.minted-1"
    assert source() == "ya29.minted-1"
    assert len(minted) == 1


def test_a_refreshing_token_mints_again_after_refresh(monkeypatch):
    """An hour-old token is replaced rather than failing the rest of the corpus."""
    monkeypatch.setattr(reauthorize_google.shutil, "which", lambda name: "/usr/bin/gcloud")
    minted = []

    def runner(command, *, capture=False):
        minted.append(tuple(command))
        return f"ya29.minted-{len(minted)}"

    source = reauthorize_google.RefreshingToken(
        "GOOGLE_HANDWRITING_OCR_ACCESS_TOKEN", environ={}, command_runner=runner
    )
    assert source() == "ya29.minted-1"
    assert source.refresh() == "ya29.minted-2"
    assert source() == "ya29.minted-2"
    assert len(minted) == 2


def test_a_refreshing_token_keeps_an_exported_value():
    """The run shell that exported a token owns its lifetime, so never re-mint it."""
    source = reauthorize_google.RefreshingToken(
        "GOOGLE_DOCUMENT_AI_ACCESS_TOKEN",
        environ={"GOOGLE_DOCUMENT_AI_ACCESS_TOKEN": "ya29.exported"},
        command_runner=lambda command, *, capture=False: "ya29.minted",
    )
    assert source() == "ya29.exported"
    assert source.refresh() == "ya29.exported"


def test_a_refreshing_token_defaults_to_the_process_environment(monkeypatch):
    monkeypatch.setenv("GOOGLE_DOCUMENT_AI_ACCESS_TOKEN", "ya29.from-process-env")
    source = reauthorize_google.RefreshingToken("GOOGLE_DOCUMENT_AI_ACCESS_TOKEN")
    assert source() == "ya29.from-process-env"


def test_token_value_resolves_both_a_string_and_a_source():
    """Adapters accept a fixed token or a refreshing source without branching."""
    assert reauthorize_google.token_value("ya29.fixed") == "ya29.fixed"
    assert reauthorize_google.token_value(lambda: "ya29.callable") == "ya29.callable"
