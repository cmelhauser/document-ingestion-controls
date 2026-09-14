"""Run-level provider client selection for constrained LLM proposal lanes."""

from openai_adapter import build_client as build_openai_client
from runtime_config import env_int, env_value, llm_provider


def build_client(credential_env, timeout_seconds=120.0, max_retries=2, provider=None):
    """Return the configured provider client for this immutable LLM run."""
    selected = provider or llm_provider()
    if selected == "openai":
        return build_openai_client(credential_env, timeout_seconds, max_retries)
    if selected == "openrouter":
        from openrouter_adapter import build_client

        return build_client(credential_env, timeout_seconds, max_retries)
    if selected == "anthropic":
        from anthropic_adapter import build_client

        return build_client(credential_env, timeout_seconds, max_retries)
    from google_genai_adapter import build_responses_client

    return build_responses_client(
        env_value("GOOGLE_VERTEX_AI_PROJECT_ID", ""),
        env_value("GOOGLE_VERTEX_AI_LOCATION", "us-central1"),
        timeout_seconds,
        max_retries,
        env_int("GOOGLE_VERTEX_AI_OUTPUT_TOKEN_RESERVE", 8192),
    )
