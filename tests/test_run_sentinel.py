"""Hold the sentinel to catching a broken lane while there is still a run to save.

A full trial spent a corpus before anyone noticed Google Document AI returning
HTTP 404 on all 18 pages. The evidence was on disk the whole time; nothing read
it until the run was over. These tests are written from that failure.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

sentinel = importlib.import_module("run_sentinel")

LANES = (
    ("optional lane", "A_LANE_ENABLED", "A_LANE_CREDENTIAL_ENV", "A_LANE_PROVIDER"),
    ("google lane", "G_LANE_ENABLED", "G_LANE_CREDENTIAL_ENV", None),
    ("off lane", "OFF_ENABLED", "OFF_CREDENTIAL_ENV", None),
)


def clear(monkeypatch):
    """Read only what a test sets, never the operator's own .env."""
    monkeypatch.setattr(sentinel, "load_project_env", lambda: {})
    for name in (
        "A_LANE_ENABLED",
        "A_LANE_CREDENTIAL_ENV",
        "A_LANE_PROVIDER",
        "A_KEY",
        "G_LANE_ENABLED",
        "G_LANE_CREDENTIAL_ENV",
        "G_ACCESS_TOKEN",
        "OFF_ENABLED",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_a_failure_that_will_not_get_better_is_told_apart_from_one_that_might():
    """A wrong processor id and an expired token both stop a lane; only one retry helps."""
    assert sentinel.classify_failure("document_ai_http_404") == sentinel.CONFIGURATION
    assert sentinel.classify_failure("invalid_model") == sentinel.CONFIGURATION
    assert sentinel.classify_failure("HTTP 401 Unauthorized") == sentinel.CREDENTIAL
    assert sentinel.classify_failure("expired api_key") == sentinel.CREDENTIAL
    assert sentinel.classify_failure("http 503") == sentinel.TRANSIENT
    assert sentinel.classify_failure("connection timed out") == sentinel.TRANSIENT
    assert sentinel.classify_failure("something else entirely") == sentinel.UNCLASSIFIED
    assert "read the retained raw response" in sentinel.REMEDIES[sentinel.UNCLASSIFIED]


def test_preflight_separates_a_missing_key_from_a_token_minted_at_run_time(monkeypatch):
    """An unset Google token is the documented pre-run step, not a misconfiguration.

    Reporting both as "no credential" trains an operator to ignore the check.
    """
    clear(monkeypatch)
    monkeypatch.setenv("A_LANE_ENABLED", "true")
    monkeypatch.setenv("A_LANE_CREDENTIAL_ENV", "A_KEY")
    monkeypatch.setenv("G_LANE_ENABLED", "TRUE")
    monkeypatch.setenv("G_LANE_CREDENTIAL_ENV", "G_ACCESS_TOKEN")
    monkeypatch.setenv("OFF_ENABLED", "false")
    monkeypatch.setattr(sentinel, "lane_provider", lambda lane: "openai")
    monkeypatch.setattr(sentinel, "lane_model", lambda lane, provider: "a-model")
    monkeypatch.setattr(sentinel, "provider_credential_env", lambda provider: "OPENAI_API_KEY")

    results = sentinel.preflight(LANES, ("consensus_primary",))
    by_lane = {item["lane"]: item for item in results}
    assert "off lane" not in by_lane, "a lane nobody enabled is not this check's business"
    assert by_lane["optional lane"]["credential_present"] is False
    assert by_lane["optional lane"]["minted_at_run_time"] is False
    assert by_lane["google lane"]["minted_at_run_time"] is True
    assert by_lane["consensus_primary"]["provider"] == "openai/a-model"

    # A lane whose credential setting names nothing falls back to its provider's.
    monkeypatch.delenv("A_LANE_CREDENTIAL_ENV")
    monkeypatch.setenv("A_LANE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "present")
    resolved = {item["lane"]: item for item in sentinel.preflight(LANES, ())}
    assert resolved["optional lane"]["credential_variable"] == "OPENAI_API_KEY"
    assert resolved["optional lane"]["credential_present"] is True
    # With no provider either, the unresolved setting is named rather than blank.
    monkeypatch.delenv("A_LANE_PROVIDER")
    unresolved = {item["lane"]: item for item in sentinel.preflight(LANES, ())}
    assert unresolved["optional lane"]["credential_variable"] == "(unset: A_LANE_CREDENTIAL_ENV)"


def test_preflight_refuses_before_the_run_spends_anything(monkeypatch, tmp_path, capsys):
    """The point of preflight: five seconds now instead of a corpus of failures."""
    clear(monkeypatch)
    monkeypatch.setenv("A_LANE_ENABLED", "true")
    monkeypatch.setenv("A_LANE_CREDENTIAL_ENV", "A_KEY")
    monkeypatch.setenv("G_LANE_ENABLED", "true")
    monkeypatch.setenv("G_LANE_CREDENTIAL_ENV", "G_ACCESS_TOKEN")
    monkeypatch.setattr(sentinel, "PROVIDER_LANES", LANES)
    monkeypatch.setattr(sentinel, "EXTRACTION_LANES", ())
    monkeypatch.setattr(
        sentinel, "access_token", lambda variable: (_ for _ in ()).throw(ValueError(variable))
    )

    monkeypatch.setattr(sys, "argv", ["run_sentinel.py", "preflight"])
    with pytest.raises(SystemExit) as refusal:
        sentinel.main()
    message = str(refusal.value)
    assert "no credential for optional lane" in message
    assert "no run-time token for google lane" in message
    assert sentinel.MINT_COMMAND in message
    printed = capsys.readouterr().out
    assert "NO CREDENTIAL  optional lane" in printed
    assert "NEEDS TOKEN    google lane" in printed

    # Every credential present is a clean pass that writes its report once.
    monkeypatch.setenv("A_KEY", "value")
    monkeypatch.setenv("G_ACCESS_TOKEN", "value")
    out = tmp_path / "preflight.json"
    argv = ["run_sentinel.py", "preflight", "--out", str(out), "--quiet"]
    monkeypatch.setattr(sys, "argv", argv)
    assert sentinel.main() == 0
    assert capsys.readouterr().out == ""
    assert json.loads(out.read_text())["summary"]["lanes_without_a_credential"] == 0
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        sentinel.main()

    # Only a missing long-lived key: the refusal says nothing about minting.
    capsys.readouterr()
    monkeypatch.delenv("A_KEY")
    monkeypatch.setattr(sys, "argv", ["run_sentinel.py", "preflight"])
    with pytest.raises(SystemExit) as key_only:
        sentinel.main()
    assert sentinel.MINT_COMMAND not in str(key_only.value)
    printed = capsys.readouterr().out
    assert "ok             google lane" in printed
    assert sentinel.MINT_COMMAND not in printed

    # Only an unminted token: the refusal is the documented pre-run step alone.
    monkeypatch.setenv("A_KEY", "value")
    monkeypatch.delenv("G_ACCESS_TOKEN")
    monkeypatch.setattr(sys, "argv", ["run_sentinel.py", "preflight", "--quiet"])
    with pytest.raises(SystemExit) as token_only:
        sentinel.main()
    assert "no credential for" not in str(token_only.value)
    assert sentinel.MINT_COMMAND in str(token_only.value)


def test_preflight_accepts_google_application_default_credentials(monkeypatch, capsys):
    """A Google adapter mints from ADC; preflight must accept that documented path."""
    clear(monkeypatch)
    monkeypatch.setenv("G_LANE_ENABLED", "true")
    monkeypatch.setenv("G_LANE_CREDENTIAL_ENV", "G_ACCESS_TOKEN")
    monkeypatch.setattr(sentinel, "PROVIDER_LANES", (LANES[1],))
    monkeypatch.setattr(sentinel, "EXTRACTION_LANES", ())
    seen = []

    def minted(variable):
        seen.append(variable)
        return "short-lived-token-not-retained"

    monkeypatch.setattr(sentinel, "access_token", minted)
    monkeypatch.setattr(sys, "argv", ["run_sentinel.py", "preflight", "--quiet"])
    assert sentinel.main() == 0
    assert seen == ["G_ACCESS_TOKEN"]
    assert capsys.readouterr().out == ""


def run_with(tmp_path, files):
    """Write a run directory containing the given retained artifacts."""
    for name, payload in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return tmp_path


def test_watch_reads_the_cause_a_failure_already_recorded(tmp_path):
    """The adapter's exception says a page failed; the raw response says why."""
    directory = run_with(
        tmp_path,
        {
            "providers/raw/docai/p1.json": {"error_type": "document_ai_http_404"},
            "providers/raw/docai/p2.json": {"error_type": "document_ai_http_404"},
            "providers/docai_exceptions.json": {"provider_error_type": "document_ai_http_404"},
            "htr/raw/p1.json": {"error_type": "HTTP 503"},
            "controls/consensus.json": {"documents": []},
            "notes.txt": "error_type is not a JSON field here",
            "broken.json": "{not json",
            "list.json": [1, 2],
        },
    )
    report = sentinel.health(directory)
    assert report["summary"]["retained_provider_failures"] == 4
    lanes = {item["lane_directory"]: item for item in report["lanes"]}
    assert lanes["providers"]["retained_failures"] == 3
    assert lanes["providers"]["dominant_classification"] == sentinel.CONFIGURATION
    assert lanes["providers"]["stop_the_run"] is True
    # A transient failure is what the lanes' own bounded retries already handle.
    assert lanes["htr"]["dominant_classification"] == sentinel.TRANSIENT
    assert lanes["htr"]["stop_the_run"] is False
    assert report["summary"]["lanes_worth_stopping_for"] == 1

    # An artifact too large to be a provider response is not read at all.
    huge = directory / "providers" / "huge.json"
    huge.write_text(json.dumps({"error_type": "x", "pad": "y" * 2_000_001}))
    assert sentinel.health(directory)["summary"]["retained_provider_failures"] == 4


def test_watch_stops_a_run_only_for_a_fault_that_will_repeat(tmp_path, monkeypatch, capsys):
    """Stopping for a transient blip would make the sentinel the outage."""
    transient = run_with(tmp_path / "t", {"htr/raw/p1.json": {"error_type": "HTTP 503"}})
    monkeypatch.setattr(
        sys, "argv", ["run_sentinel.py", "watch", str(transient), "--once", "--fail-fast"]
    )
    assert sentinel.main() == 0
    assert "transient" in capsys.readouterr().out

    broken = run_with(tmp_path / "b", {"providers/raw/p1.json": {"error_type": "http 404"}})
    monkeypatch.setattr(
        sys, "argv", ["run_sentinel.py", "watch", str(broken), "--once", "--fail-fast"]
    )
    with pytest.raises(SystemExit, match="stop the run"):
        sentinel.main()

    # Without --fail-fast the same run is reported and not stopped.
    capsys.readouterr()
    out = tmp_path / "health.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_sentinel.py", "watch", str(broken), "--once", "--out", str(out), "--quiet"],
    )
    assert sentinel.main() == 0
    assert capsys.readouterr().out == ""
    assert json.loads(out.read_text())["summary"]["lanes_worth_stopping_for"] == 1


def test_watch_polls_until_it_finds_something_worth_stopping_for(tmp_path, monkeypatch):
    """Left running beside a run, it reports each pass and stops on the first fault."""
    directory = run_with(tmp_path, {"controls/consensus.json": {"documents": []}})
    polls = []

    def poll(seconds):
        polls.append(seconds)
        (directory / "providers").mkdir(exist_ok=True)
        (directory / "providers" / "p1.json").write_text(json.dumps({"error_type": "401"}))

    monkeypatch.setattr(sentinel.time, "sleep", poll)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_sentinel.py", "watch", str(directory), "--fail-fast", "--interval", "5", "--quiet"],
    )
    with pytest.raises(SystemExit, match="credential reason"):
        sentinel.main()
    assert polls == [5.0], "the first pass saw a healthy run and waited exactly one interval"


def test_a_run_directory_that_is_not_there_is_named_not_traced(tmp_path, monkeypatch):
    """An operator gets a sentence, not a traceback."""
    monkeypatch.setattr(
        sys, "argv", ["run_sentinel.py", "watch", str(tmp_path / "absent"), "--once", "--quiet"]
    )
    assert sentinel.main() == 0, "an absent directory has no retained failures to report yet"


def test_every_setting_the_catalogue_names_actually_exists():
    """A catalogue naming a setting nothing reads is a false alarm generator.

    `GOOGLE_ADDRESS_VALIDATION_API_KEY_ENV` does not exist -- the lane reads
    `GOOGLE_MAPS_API_KEY_ENV` -- so preflight reported a fully configured lane as
    having no credential. An operator who sees one false alarm stops reading the
    check, which costs more than the check ever saves.
    """
    documented = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", (ROOT / ".env.example").read_text(), re.M))
    named = set()
    for _, enable_setting, credential_setting, provider_setting in sentinel.PROVIDER_LANES:
        named.update({enable_setting, credential_setting})
        if provider_setting:
            named.add(provider_setting)
    assert not named - documented, (
        f"settings absent from .env.example: {sorted(named - documented)}"
    )
