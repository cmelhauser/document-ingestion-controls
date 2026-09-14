#!/usr/bin/env python3
"""Run one selected LLM extraction provider against an immutable intake manifest.

``LLM_EXTRACT_PROVIDER`` is an optional extraction-lane choice in the root ``.env``:
``openai`` uses
the existing Responses adapter and ``google`` uses Gemini through Google Vertex
AI. Named ``consensus_primary`` and ``consensus_secondary`` lanes resolve their
own provider/model settings so two separately retained artifacts can be passed
to ``consensus.py``. The ``reasoning`` lane selects the provider/model used by
proposal-only review agents. Each invocation remains single-provider and the
provider adapter registry is the only place that needs extension for a new SDK.
"""

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - controlled production runs use POSIX hosts.
    fcntl = None

import anthropic_adapter
import google_genai_adapter
import openai_adapter
import openrouter_adapter
from cli_help import apply_shared_help
from llm_response import INPUT_MODES
from runtime_config import (
    LLM_LANES,
    env_float,
    env_int,
    env_value,
    lane_configuration,
    llm_model,
    llm_provider,
    load_project_env,
    require_google_capability_limits,
)

PROVIDERS = ("openai", "google", "openrouter", "anthropic")


@contextmanager
def lane_execution_lock(lane, run_root=None):
    """Hold one contained run's lane lock until its adapter invocation returns.

    A recovery overlay has different output paths, so output no-clobber checks
    alone cannot prevent it from sending the same corpus while an earlier
    invocation is still live.  The workspace wrapper supplies the run root;
    POSIX advisory locking makes this single-flight guard process-safe and the
    kernel releases it if the worker terminates unexpectedly.
    """
    root_value = run_root or os.environ.get("BUSINESS_DOC_RUN_ROOT")
    if not root_value:
        # Direct developer/unit-test invocation is outside the controlled-run
        # boundary. Live pipeline invocations are required to use the workspace
        # wrapper, which supplies this variable.
        yield
        return
    if fcntl is None:
        raise ValueError("contained LLM lane locking requires a POSIX host")
    root = Path(root_value).resolve()
    lock_path = root / "runtime" / "locks" / f"llm_adapter_{lane}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    stream = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                f"another {lane} extraction invocation is already active in this run; "
                "wait for its terminal handoff/exceptions before starting recovery"
            ) from exc
        stream.seek(0)
        stream.truncate()
        stream.write(json.dumps({"lane": lane, "pid": os.getpid()}) + "\n")
        stream.flush()
        yield
    finally:
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def provider_defaults(provider, selected_model=None):
    """Return the selected provider's non-secret command defaults."""
    if provider == "openai":
        return {
            "model": selected_model or llm_model("openai"),
            "credential_env": env_value("OPENAI_CREDENTIAL_ENV", "OPENAI_API_KEY"),
            "input_mode": env_value("OPENAI_INPUT_MODE", "auto"),
            "max_pages": env_int("OPENAI_MAX_PAGES", 1000),
            "max_pdf_bytes": env_int("OPENAI_MAX_PDF_BYTES", 10_000_000),
            "max_text_chars": env_int("OPENAI_MAX_TEXT_CHARS", 100_000),
            "timeout_seconds": env_float("OPENAI_TIMEOUT_SECONDS", 120.0),
            "max_retries": env_int("OPENAI_MAX_RETRIES", 2),
            "reasoning_effort": env_value("OPENAI_REASONING_EFFORT", "medium"),
            "max_workers": env_int("OPENAI_MAX_WORKERS", 8),
            "cache_dir": env_value("LLM_CACHE_DIR", ".llm-cache"),
            "retry_backoff_seconds": env_float("OPENAI_RETRY_BACKOFF_SECONDS", 1.0),
            "max_backoff_seconds": env_float("OPENAI_MAX_BACKOFF_SECONDS", 120.0),
        }
    if provider == "google":
        require_google_capability_limits()
        return {
            "model": selected_model or llm_model("google"),
            "credential_env": env_value(
                "GOOGLE_VERTEX_AI_CREDENTIAL_ENV", "GOOGLE_APPLICATION_CREDENTIALS"
            ),
            "input_mode": env_value("GOOGLE_VERTEX_AI_INPUT_MODE", "auto"),
            "max_pages": env_int("GOOGLE_VERTEX_AI_MAX_PAGES", 1000),
            "max_pdf_bytes": env_int("GOOGLE_VERTEX_AI_MAX_PDF_BYTES", 10_000_000),
            "max_text_chars": env_int("GOOGLE_VERTEX_AI_MAX_TEXT_CHARS", 100_000),
            "timeout_seconds": env_float("GOOGLE_VERTEX_AI_TIMEOUT_SECONDS", 180.0),
            "max_retries": env_int("GOOGLE_VERTEX_AI_MAX_RETRIES", 4),
            "project_id": env_value("GOOGLE_VERTEX_AI_PROJECT_ID", ""),
            "location": env_value("GOOGLE_VERTEX_AI_LOCATION", "us-central1"),
            "max_workers": env_int("GOOGLE_VERTEX_AI_MAX_WORKERS", 8),
            "cache_dir": env_value("LLM_CACHE_DIR", ".llm-cache"),
            "retry_backoff_seconds": env_float("GOOGLE_VERTEX_AI_RETRY_BACKOFF_SECONDS", 1.0),
            "max_backoff_seconds": env_float("GOOGLE_VERTEX_AI_MAX_BACKOFF_SECONDS", 120.0),
            "max_output_tokens": env_int("GOOGLE_VERTEX_AI_MODEL_MAX_OUTPUT_TOKENS", 65536),
        }
    if provider == "openrouter":
        return {
            "model": selected_model or llm_model("openrouter"),
            "credential_env": env_value("OPENROUTER_CREDENTIAL_ENV", "OPENROUTER_API_KEY"),
            "input_mode": env_value("OPENROUTER_INPUT_MODE", "text"),
            # Resolution of the local render used by image input mode. It is
            # part of what the model was shown, so it is retained per page.
            "image_dpi": env_int("OPENROUTER_IMAGE_DPI", openai_adapter.DEFAULT_IMAGE_DPI),
            "max_pages": env_int("OPENROUTER_MAX_PAGES", 1000),
            "max_pdf_bytes": env_int("OPENROUTER_MAX_PDF_BYTES", 10_000_000),
            "max_text_chars": env_int("OPENROUTER_MAX_TEXT_CHARS", 100_000),
            "timeout_seconds": env_float("OPENROUTER_TIMEOUT_SECONDS", 120.0),
            "max_retries": env_int("OPENROUTER_MAX_RETRIES", 2),
            "reasoning_effort": env_value("OPENROUTER_REASONING_EFFORT", "medium"),
            "max_workers": env_int("OPENROUTER_MAX_WORKERS", 8),
            "cache_dir": env_value("LLM_CACHE_DIR", ".llm-cache"),
            "retry_backoff_seconds": env_float("OPENROUTER_RETRY_BACKOFF_SECONDS", 1.0),
            "max_backoff_seconds": env_float("OPENROUTER_MAX_BACKOFF_SECONDS", 120.0),
            # Empty means OpenRouter routes as it sees fit, which is the
            # documented behaviour and stays the default.
            "provider_only": env_value("OPENROUTER_PROVIDER_ONLY", ""),
        }
    if provider == "anthropic":
        return {
            "model": selected_model or llm_model("anthropic"),
            "credential_env": env_value("ANTHROPIC_CREDENTIAL_ENV", "ANTHROPIC_API_KEY"),
            # Anthropic reads a retained page PDF natively as a document block,
            # so the recorded vendor is the one that read the glyphs.
            "input_mode": env_value("ANTHROPIC_INPUT_MODE", "pdf"),
            "max_pages": env_int("ANTHROPIC_MAX_PAGES", 1000),
            "max_pdf_bytes": env_int("ANTHROPIC_MAX_PDF_BYTES", 10_000_000),
            "max_text_chars": env_int("ANTHROPIC_MAX_TEXT_CHARS", 100_000),
            "timeout_seconds": env_float("ANTHROPIC_TIMEOUT_SECONDS", 120.0),
            "max_retries": env_int("ANTHROPIC_MAX_RETRIES", 2),
            "reasoning_effort": env_value("ANTHROPIC_REASONING_EFFORT", "medium"),
            "max_workers": env_int("ANTHROPIC_MAX_WORKERS", 8),
            "cache_dir": env_value("LLM_CACHE_DIR", ".llm-cache"),
            "retry_backoff_seconds": env_float("ANTHROPIC_RETRY_BACKOFF_SECONDS", 1.0),
            "max_backoff_seconds": env_float("ANTHROPIC_MAX_BACKOFF_SECONDS", 120.0),
        }
    raise ValueError("provider must be one of: " + ", ".join(PROVIDERS))


def run_openai(args, defaults):
    """Execute the OpenAI implementation with the common CLI contract."""
    return openai_adapter.run_adapter(
        args.manifest,
        args.out,
        args.adapter_out,
        args.exceptions,
        args.raw_dir,
        args.model or defaults["model"],
        openai_adapter.build_client(
            args.credential_env or defaults["credential_env"],
            args.timeout_seconds
            if args.timeout_seconds is not None
            else defaults["timeout_seconds"],
            args.max_retries if args.max_retries is not None else defaults["max_retries"],
        ),
        args.input_mode or defaults["input_mode"],
        args.max_pages if args.max_pages is not None else defaults["max_pages"],
        args.max_pdf_bytes if args.max_pdf_bytes is not None else defaults["max_pdf_bytes"],
        args.max_text_chars if args.max_text_chars is not None else defaults["max_text_chars"],
        args.timeout_seconds if args.timeout_seconds is not None else defaults["timeout_seconds"],
        args.max_retries if args.max_retries is not None else defaults["max_retries"],
        args.reasoning_effort or defaults["reasoning_effort"],
        args.credential_env or defaults["credential_env"],
        getattr(args, "max_workers", None)
        if getattr(args, "max_workers", None) is not None
        else defaults["max_workers"],
        getattr(args, "cache_dir", None)
        if getattr(args, "cache_dir", None) is not None
        else defaults["cache_dir"] or None,
        getattr(args, "retry_backoff_seconds", None)
        if getattr(args, "retry_backoff_seconds", None) is not None
        else defaults["retry_backoff_seconds"],
        max_backoff_seconds=defaults.get("max_backoff_seconds", 120.0),
        lane=getattr(args, "lane", "extraction"),
    )


def run_vertex(args, defaults):
    """Execute the Gemini-on-Vertex implementation with the common CLI contract."""
    project_id = args.project_id or defaults["project_id"]
    location = args.location or defaults["location"]
    timeout_seconds = (
        args.timeout_seconds if args.timeout_seconds is not None else defaults["timeout_seconds"]
    )
    max_retries = args.max_retries if args.max_retries is not None else defaults["max_retries"]
    sdk = google_genai_adapter.load_sdk()
    return google_genai_adapter.run_adapter(
        args.manifest,
        args.out,
        args.adapter_out,
        args.exceptions,
        args.raw_dir,
        args.model or defaults["model"],
        google_genai_adapter.build_client(project_id, location, timeout_seconds, max_retries, sdk),
        args.input_mode or defaults["input_mode"],
        args.max_pages if args.max_pages is not None else defaults["max_pages"],
        args.max_pdf_bytes if args.max_pdf_bytes is not None else defaults["max_pdf_bytes"],
        args.max_text_chars if args.max_text_chars is not None else defaults["max_text_chars"],
        project_id,
        location,
        timeout_seconds,
        max_retries,
        args.credential_env or defaults["credential_env"],
        sdk[1],
        getattr(args, "max_workers", None)
        if getattr(args, "max_workers", None) is not None
        else defaults["max_workers"],
        getattr(args, "cache_dir", None)
        if getattr(args, "cache_dir", None) is not None
        else defaults["cache_dir"] or None,
        getattr(args, "retry_backoff_seconds", None)
        if getattr(args, "retry_backoff_seconds", None) is not None
        else defaults["retry_backoff_seconds"],
        defaults.get("max_backoff_seconds", 120.0),
        getattr(args, "lane", "extraction"),
        # A commission page carries fifty required columns over forty rows.
        # Without an explicit ceiling the response truncates mid-object, and a
        # truncated response is a lost page rather than a shorter one.
        defaults.get("max_output_tokens"),
    )


def run_openrouter(args, defaults):
    """Execute the isolated OpenRouter Chat Completions implementation."""
    timeout_seconds = (
        args.timeout_seconds if args.timeout_seconds is not None else defaults["timeout_seconds"]
    )
    max_retries = args.max_retries if args.max_retries is not None else defaults["max_retries"]
    return openrouter_adapter.run_adapter(
        args.manifest,
        args.out,
        args.adapter_out,
        args.exceptions,
        args.raw_dir,
        args.model or defaults["model"],
        openrouter_adapter.build_client(
            args.credential_env or defaults["credential_env"],
            timeout_seconds,
            max_retries,
            provider_only=getattr(args, "provider_only", None) or defaults.get("provider_only", ""),
        ),
        args.input_mode or defaults["input_mode"],
        args.max_pages if args.max_pages is not None else defaults["max_pages"],
        args.max_pdf_bytes if args.max_pdf_bytes is not None else defaults["max_pdf_bytes"],
        args.max_text_chars if args.max_text_chars is not None else defaults["max_text_chars"],
        timeout_seconds,
        max_retries,
        args.reasoning_effort or defaults["reasoning_effort"],
        args.credential_env or defaults["credential_env"],
        getattr(args, "max_workers", None)
        if getattr(args, "max_workers", None) is not None
        else defaults["max_workers"],
        getattr(args, "cache_dir", None)
        if getattr(args, "cache_dir", None) is not None
        else defaults["cache_dir"] or None,
        getattr(args, "retry_backoff_seconds", None)
        if getattr(args, "retry_backoff_seconds", None) is not None
        else defaults["retry_backoff_seconds"],
        max_backoff_seconds=defaults.get("max_backoff_seconds", 120.0),
        lane=getattr(args, "lane", "extraction"),
        image_dpi=args.image_dpi
        if getattr(args, "image_dpi", None) is not None
        else defaults.get("image_dpi", openai_adapter.DEFAULT_IMAGE_DPI),
    )


def run_anthropic(args, defaults):
    """Execute the Anthropic Messages implementation with the common contract."""
    timeout_seconds = (
        args.timeout_seconds if args.timeout_seconds is not None else defaults["timeout_seconds"]
    )
    max_retries = args.max_retries if args.max_retries is not None else defaults["max_retries"]
    return anthropic_adapter.run_adapter(
        args.manifest,
        args.out,
        args.adapter_out,
        args.exceptions,
        args.raw_dir,
        args.model or defaults["model"],
        anthropic_adapter.build_client(
            args.credential_env or defaults["credential_env"], timeout_seconds, max_retries
        ),
        args.input_mode or defaults["input_mode"],
        args.max_pages if args.max_pages is not None else defaults["max_pages"],
        args.max_pdf_bytes if args.max_pdf_bytes is not None else defaults["max_pdf_bytes"],
        args.max_text_chars if args.max_text_chars is not None else defaults["max_text_chars"],
        timeout_seconds,
        max_retries,
        args.reasoning_effort or defaults["reasoning_effort"],
        args.credential_env or defaults["credential_env"],
        getattr(args, "max_workers", None)
        if getattr(args, "max_workers", None) is not None
        else defaults["max_workers"],
        getattr(args, "cache_dir", None)
        if getattr(args, "cache_dir", None) is not None
        else defaults["cache_dir"] or None,
        getattr(args, "retry_backoff_seconds", None)
        if getattr(args, "retry_backoff_seconds", None) is not None
        else defaults["retry_backoff_seconds"],
        max_backoff_seconds=defaults.get("max_backoff_seconds", 120.0),
        lane=getattr(args, "lane", "extraction"),
    )


def main():
    lane_parser = argparse.ArgumentParser(add_help=False)
    lane_parser.add_argument(
        "--lane",
        choices=LLM_LANES,
        default="extraction",
        help="Pipeline lane to run. Each lane resolves its own provider and model from the environment.",
    )
    lane_args, _ = lane_parser.parse_known_args()
    try:
        load_project_env()
        configuration = (
            {"lane": "extraction", "provider": llm_provider(), "model": None}
            if lane_args.lane == "extraction"
            else lane_configuration(lane_args.lane)
        )
        provider = configuration["provider"]
        defaults = provider_defaults(provider, configuration["model"])
    except ValueError as exc:
        sys.exit(f"LLM adapter failed: {exc}")
    parser = argparse.ArgumentParser(
        description="Extract immutable intake pages with one selected LLM provider."
    )
    parser.add_argument(
        "manifest", help="ingestion_manifest.json created by scripts/ingest_pages.py"
    )
    parser.add_argument("--out", required=True, help="new normalized per-engine record list")
    parser.add_argument("--adapter-out", required=True, help="new provider handoff object")
    parser.add_argument("--exceptions", required=True, help="new provider/schema exception queue")
    parser.add_argument("--raw-dir", required=True, help="new or empty raw-response directory")
    parser.add_argument(
        "--lane",
        choices=LLM_LANES,
        default=lane_args.lane,
        help="provider/model lane; consensus lanes are run separately before consensus.py",
    )
    parser.add_argument("--model")
    parser.add_argument("--credential-env")
    parser.add_argument(
        "--input-mode",
        choices=INPUT_MODES,
        help=(
            "Submit native page text, the page PDF, a locally rendered page image, or auto to "
            "use text only on safely rule-classified pages. 'image' opens the vendors that read "
            "documents but do not accept a PDF; auto never selects it, and a provider adapter "
            "that has not been verified for a mode refuses it."
        ),
    )
    parser.add_argument(
        "--provider-only",
        help=(
            "Comma-separated OpenRouter provider slugs permitted to serve this lane, such as "
            "'coreweave,modal'. OpenRouter otherwise chooses the serving provider itself, and a "
            "degraded one returns a truncated stream that reads downstream as a model which could "
            "not read the page. Unset routes normally; an unknown slug is refused by OpenRouter, "
            "which answers with the providers actually serving the model."
        ),
    )
    parser.add_argument(
        "--image-dpi",
        type=int,
        help=(
            "Resolution of the local page render used by --input-mode image. Retained on each "
            "record with the image digest, because the same page at a different resolution is a "
            "different reading rather than the same one."
        ),
    )
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--max-pdf-bytes", type=int)
    parser.add_argument("--max-text-chars", type=int)
    parser.add_argument("--timeout-seconds", type=float)
    parser.add_argument("--max-retries", type=int)
    parser.add_argument("--max-workers", type=int)
    parser.add_argument(
        "--cache-dir",
        help="Directory of retained responses replayed instead of calling the provider. Clear it for a genuinely fresh reading.",
    )
    parser.add_argument("--retry-backoff-seconds", type=float)
    parser.add_argument("--reasoning-effort", choices=openai_adapter.REASONING_EFFORTS)
    parser.add_argument("--project-id", help="required when LLM_EXTRACT_PROVIDER=google")
    parser.add_argument("--location", help="Vertex region, for example us-central1")
    apply_shared_help(parser)
    args = parser.parse_args()
    try:
        with lane_execution_lock(args.lane):
            if provider == "openai":
                result = run_openai(args, defaults)
            elif provider == "google":
                result = run_vertex(args, defaults)
            elif provider == "anthropic":
                result = run_anthropic(args, defaults)
            else:
                result = run_openrouter(args, defaults)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.exit(f"LLM adapter failed: {exc}")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
