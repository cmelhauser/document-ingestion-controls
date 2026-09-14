#!/usr/bin/env python3
"""Interactively refresh local gcloud and Application Default Credentials.

This helper never writes credentials to the repository or to ``.env``.  It
uses ``gcloud`` for the interactive OAuth flows, verifies both token paths,
    and can optionally write short-lived Document AI and Cloud Vision tokens to
    a user-selected 0600 shell-env file outside the repository.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

DEFAULT_APIS = (
    "aiplatform.googleapis.com",
    "documentai.googleapis.com",
    "vision.googleapis.com",
    "addressvalidation.googleapis.com",
    "places.googleapis.com",
)
DEFAULT_TOKEN_ENV_FILE = "/private/tmp/business-doc-ingestion-google.env"  # noqa: S105 - a file path, not a credential value


def run(command: Sequence[str], *, capture: bool = False) -> str:
    """Run a gcloud command and return captured stdout when requested."""

    print("+", shlex.join(command))
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        list(command),
        check=False,
        text=True,
        capture_output=capture,
    )
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {shlex.join(command)}")
    return result.stdout.strip() if capture else ""


def ask(prompt: str, default: str = "") -> str:
    """Read a trimmed answer, using the supplied default when blank."""

    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    """Read a yes/no answer."""

    marker = "Y/n" if default else "y/N"
    answer = input(f"{prompt} [{marker}]: ").strip().lower()
    if not answer:
        return default
    if answer in {"y", "yes"}:
        return True
    if answer in {"n", "no"}:
        return False
    raise ValueError("Please answer yes or no.")


def current_project() -> str:
    """Return the configured gcloud project, or an empty string."""

    try:
        return run(("gcloud", "config", "get-value", "project"), capture=True)
    except RuntimeError:
        return ""


def verify_token(
    label: str,
    command: Sequence[str],
    *,
    command_runner: Callable[..., str] = run,
) -> None:
    """Verify that a gcloud command can mint a token without displaying it."""

    command_runner(command, capture=True)
    print(f"OK: {label}")


def write_document_ai_env(path: Path, token: str) -> None:
    """Write a restricted shell environment file outside the repository."""

    path = path.expanduser().resolve()
    if path.exists() and not path.is_file():
        raise ValueError(f"Token output path is not a regular file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Short-lived token; delete after the run. Do not commit this file.\n"
        f"export GOOGLE_HANDWRITING_OCR_ACCESS_TOKEN={shlex.quote(token)}\n"
        f"export GOOGLE_DOCUMENT_AI_ACCESS_TOKEN={shlex.quote(token)}\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    print(f"Wrote short-lived Document AI token file: {path}")
    print(f"Source it in the run shell with: source {shlex.quote(str(path))}")


ADC_TOKEN_COMMAND = ("gcloud", "auth", "application-default", "print-access-token")


def access_token(credential_env, *, environ=None, command_runner=None):
    """Return the Google access token for a run without ever storing one.

    A short-lived ``ya29.`` token pasted into ``.env`` is stale within the hour
    and lives in the repository directory, so no token slot exists there. The
    token is resolved in this order:

    1. The named environment variable, when a run shell, CI job, or secret
       manager has already exported one.
    2. Otherwise a fresh token minted from the Application Default Credentials
       this script establishes. That call is non-interactive once ADC exists.

    The value is returned to the caller and never printed, logged, cached, or
    written to an artifact.
    """
    environ = os.environ if environ is None else environ
    supplied = environ.get(credential_env, "")
    if supplied.strip():
        return supplied
    runner = run if command_runner is None else command_runner
    if shutil.which("gcloud") is None:
        raise ValueError(
            f"{credential_env} is not set and gcloud was not found on PATH; "
            "install the Google Cloud CLI and run scripts/reauthorize_google.py"
        )
    try:
        token = runner(ADC_TOKEN_COMMAND, capture=True)
    except RuntimeError as exc:
        raise ValueError(
            f"{credential_env} is not set and no Application Default Credentials are "
            "available; run scripts/reauthorize_google.py before this lane"
        ) from exc
    if not token.strip():
        raise ValueError(
            f"{credential_env} is not set and Application Default Credentials returned "
            "no token; run scripts/reauthorize_google.py before this lane"
        )
    return token.strip()


class RefreshingToken:
    """Resolve a Google access token and mint a replacement when it expires.

    An ADC token lives about an hour and a corpus pass outlives it.  A 716-page
    Document AI run spent sixty minutes, and every request after that returned
    HTTP 401 from a token minted once at start-up, so 223 pages became provider
    exceptions that no retry could have fixed.  Holding the source rather than
    the string lets a lane re-mint after an expiry instead of failing the rest
    of the corpus.

    The token value is still never printed, logged, cached to disk, or written
    to an artifact; only the credential variable *name* is ever retained.  An
    externally supplied token is returned unchanged and never re-minted, because
    the run shell that exported it owns its lifetime.
    """

    def __init__(self, credential_env, *, environ=None, command_runner=None):
        self.credential_env = credential_env
        self._environ = environ
        self._command_runner = command_runner
        self._token = None

    def _mint(self):
        return access_token(
            self.credential_env,
            environ=self._environ,
            command_runner=self._command_runner,
        )

    def __call__(self):
        """Return the current token, minting one on first use."""
        if self._token is None:
            self._token = self._mint()
        return self._token

    def refresh(self):
        """Discard the held token and mint a fresh one."""
        self._token = None
        return self()


def token_value(source):
    """Resolve either a fixed token string or a refreshing token source."""
    return source() if callable(source) else source


def main(
    argv: Sequence[str] | None = None,
    *,
    command_runner: Callable[..., str] = run,
    input_fn: Callable[[str], str] = input,
) -> int:
    """Run the interactive credential refresh flow."""

    del input_fn  # Kept in the signature to make prompt wiring explicit in tests.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="Use this GCP project without prompting")
    parser.add_argument(
        "--skip-api-enable", action="store_true", help="Do not offer to enable required APIs"
    )
    parser.add_argument(
        "--token-env-out",
        help="Write the short-lived Document AI token to this 0600 file",
    )
    args = parser.parse_args(argv)

    if shutil.which("gcloud") is None:
        print("gcloud was not found on PATH. Install the Google Cloud CLI first.", file=sys.stderr)
        return 2

    configured = os.environ.get("GOOGLE_VERTEX_AI_PROJECT_ID")
    if not args.project and not configured:
        configured = current_project()
    project = args.project or ask("GCP project ID", configured)
    if not project:
        print("A GCP project ID is required.", file=sys.stderr)
        return 2

    try:
        command_runner(("gcloud", "config", "set", "project", project))
        command_runner(("gcloud", "auth", "login"))
        command_runner(("gcloud", "auth", "application-default", "login"))
        command_runner(("gcloud", "auth", "application-default", "set-quota-project", project))

        if not args.skip_api_enable and ask_yes_no("Enable required Google APIs now?", False):
            command_runner(("gcloud", "services", "enable", *DEFAULT_APIS))

        verify_token(
            "gcloud CLI credentials",
            ("gcloud", "auth", "print-access-token"),
            command_runner=command_runner,
        )
        verify_token(
            "Application Default Credentials for Vertex AI",
            ("gcloud", "auth", "application-default", "print-access-token"),
            command_runner=command_runner,
        )

        token_path = args.token_env_out
        if not token_path and ask_yes_no("Create a temporary Document AI token env file?", True):
            token_path = ask("Token env file", DEFAULT_TOKEN_ENV_FILE)
        if token_path:
            token = command_runner(
                ("gcloud", "auth", "application-default", "print-access-token"),
                capture=True,
            )
            write_document_ai_env(Path(token_path), token)
    except (RuntimeError, ValueError) as exc:
        print(f"Google credential setup failed: {exc}", file=sys.stderr)
        return 1

    print("Google authentication is ready for the current run shell.")
    print("If GOOGLE_APPLICATION_CREDENTIALS is set, verify that it points to the intended file;")
    print("otherwise the Vertex adapter will use the refreshed Application Default Credentials.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
