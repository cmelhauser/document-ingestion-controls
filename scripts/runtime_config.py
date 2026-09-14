#!/usr/bin/env python3
"""Load non-secret project configuration from a root ``.env`` file.

The loader deliberately implements only the small, auditable subset this project
uses: uppercase names, optional ``export``, quoted values, and inline comments.
The process environment takes precedence, so deployment secrets supplied by a
shell, CI, or secret manager are never replaced by a repository-local file.
"""

import os
import re
from pathlib import Path

ENV_ASSIGNMENT = re.compile(r"(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*)")
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
LLM_PROVIDERS = {"openai", "google", "openrouter", "anthropic"}
# `consensus.py` has always admitted a third consensus lane. Until now no
# adapter could produce one: `consensus_tiebreaker` was accepted by the consumer
# and absent from the producer, so the only way to read a corpus with a third
# vendor was to mislabel it as the secondary. A lane a control accepts and no
# command can emit is a lane that does not exist in practice.
LLM_LANES = (
    "extraction",
    "consensus_primary",
    "consensus_secondary",
    "consensus_tiebreaker",
    "reasoning",
)
UNRESOLVED_MODEL_VENDOR = "openrouter:unresolved"

# A lane declares who read the source bytes. ``model`` means the named model
# read them itself, so its reading is its own. Any other value names an engine
# interposed between the source and the model -- OpenRouter's default PDF
# handling, for instance, OCRs with ``mistral-ocr`` and hands the text on. Two
# lanes that inherit one interposed reading share its errors exactly, which is
# corroboration that is not corroboration. An absent declaration is unknown and
# must be treated as interposed rather than assumed safe.
SOURCE_READ_BY_MODEL = "model"


def project_env_path():
    """Return the root-local environment file without using the working directory.

    ``BUSINESS_DOCUMENT_IGNORE_PROJECT_ENV`` set true switches the file off for
    this process, and ``None`` is returned. The switch is process-only -- a line
    in the file cannot turn the file off -- and a child process inherits it. That
    is how the test suite keeps the operator's ``.env`` out of every script it
    starts: each one resolves this path in its own process, where no patch in
    the suite reaches.
    """
    if env_bool("BUSINESS_DOCUMENT_IGNORE_PROJECT_ENV", False):
        return None
    return Path(__file__).resolve().parents[1] / ".env"


def parse_env_value(value, path, line_number):
    """Parse a value while rejecting ambiguous unterminated quoted strings."""
    value = value.strip()
    if value[:1] in (chr(39), chr(34)):
        quote = value[0]
        if len(value) < 2 or not value.endswith(quote):
            raise ValueError(f"{path}:{line_number}: unterminated quoted value")
        return value[1:-1]
    return value.split(" #", 1)[0].rstrip()


def load_project_env(path=None, environ=None):
    """Load root ``.env`` values that are absent from the process environment."""
    path = Path(path) if path is not None else project_env_path()
    environ = os.environ if environ is None else environ
    if path is None or not path.exists():
        return {}
    values = {}
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = ENV_ASSIGNMENT.fullmatch(stripped)
        if not match:
            raise ValueError(f"{path}:{line_number}: expected UPPERCASE_NAME=value")
        name, raw_value = match.groups()
        value = parse_env_value(raw_value, path, line_number)
        values[name] = value
        environ.setdefault(name, value)
    return values


def env_value(name, default):
    """Read a non-empty configured value, otherwise return its documented default."""
    value = os.environ.get(name, "").strip()
    return value or default


def env_bool(name, default):
    """Read a strict boolean value suitable for an explicit automation flag."""
    value = env_value(name, "")
    if not value:
        return default
    normalized = value.lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be one of: true, false, yes, no, on, off, 1, 0")


def env_int(name, default):
    """Read an integer setting with a clear configuration-boundary error."""
    value = env_value(name, str(default))
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def env_float(name, default):
    """Read a floating-point setting with a clear configuration-boundary error."""
    value = env_value(name, str(default))
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def model_vendor(provider, model):
    """Resolve the vendor that actually produced a reading, seeing through a router.

    Independence is a property of the model, not the transport. OpenRouter is a
    router: a lane configured as ``openrouter`` running ``openai/gpt-5.6`` is the
    same weights as an OpenAI lane running ``gpt-5.6``. Counting those as two
    independent groups would defeat the control consensus exists to provide, so
    the routed vendor prefix is resolved and compared instead of the router name.

    Two catalogue forms would otherwise slip through as their own group:

    * An alias slug carries a ``~`` prefix, so ``~openai/gpt-mini-latest`` would
      compare unequal to ``openai`` and pass as independent of it. The prefix is
      stripped before comparison.
    * An auto-routing slug such as ``openrouter/auto`` names the router itself and
      selects a model at request time. It cannot promise a vendor, so it resolves
      to the unresolved sentinel rather than to ``openrouter``.

    A routed slug with no resolvable vendor returns the ``openrouter:unresolved``
    sentinel; callers must treat it as unknown rather than as its own group.
    """
    provider = str(provider or "").strip().casefold()
    if provider != "openrouter":
        return provider
    slug = str(model or "").strip().casefold()
    vendor = slug.split("/", 1)[0] if "/" in slug else ""
    vendor = vendor.lstrip("~")
    if vendor in {"", "openrouter"}:
        return UNRESOLVED_MODEL_VENDOR
    return vendor


def llm_provider():
    """Return the one configured run-level LLM provider."""
    provider = env_value("LLM_EXTRACT_PROVIDER", "openai")
    if provider not in LLM_PROVIDERS:
        raise ValueError("LLM_EXTRACT_PROVIDER must be one of: " + ", ".join(sorted(LLM_PROVIDERS)))
    return provider


def llm_model(provider=None):
    """Resolve the configured provider's model from the matching environment setting."""
    provider = llm_provider() if provider is None else provider
    if provider == "openai":
        return env_value("OPENAI_MODEL", "gpt-5.6-terra")
    if provider == "google":
        return env_value("GOOGLE_VERTEX_AI_MODEL", "gemini-2.5-pro")
    if provider == "openrouter":
        return env_value("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
    if provider == "anthropic":
        return env_value("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    raise ValueError("provider must be one of: " + ", ".join(sorted(LLM_PROVIDERS)))


def lane_provider(lane):
    """Resolve a named lane to one supported provider without mixing lanes."""
    if lane not in LLM_LANES:
        raise ValueError(f"lane must be one of: {', '.join(LLM_LANES)}")
    setting = {
        "extraction": "LLM_EXTRACT_PROVIDER",
        "consensus_primary": "LLM_CONSENSUS_PRI_PROVIDER",
        "consensus_secondary": "LLM_CONSENSUS_SEC_PROVIDER",
        "consensus_tiebreaker": "LLM_CONSENSUS_TIE_PROVIDER",
        "reasoning": "LLM_REASONING_PROVIDER",
    }[lane]
    defaults = {
        "extraction": "openai",
        "consensus_primary": "openai",
        "consensus_secondary": "google",
        # No default third vendor. A tiebreaker that quietly resolves to a
        # provider already reading another lane is not a third reading, and
        # defaulting one is how that happens without anyone choosing it.
        "consensus_tiebreaker": "",
        "reasoning": "openai",
    }
    provider = env_value(setting, defaults[lane])
    if not provider:
        raise ValueError(f"{setting} must be set to use the {lane} lane")
    if provider not in LLM_PROVIDERS:
        raise ValueError(f"{setting} must be one of: {', '.join(sorted(LLM_PROVIDERS))}")
    return provider


def lane_model(lane, provider=None):
    """Resolve the model for a lane, falling back to the provider default."""
    provider = lane_provider(lane) if provider is None else provider
    if provider not in LLM_PROVIDERS:
        raise ValueError("provider must be one of: " + ", ".join(sorted(LLM_PROVIDERS)))
    if lane == "extraction":
        return llm_model(provider)
    suffix = "CONSENSUS" if lane.startswith("consensus_") else "REASONING"
    prefix = {
        "google": "GOOGLE_VERTEX_AI",
        "openai": "OPENAI",
        "openrouter": "OPENROUTER",
        "anthropic": "ANTHROPIC",
    }[provider]
    return env_value(f"{prefix}_{suffix}_MODEL", llm_model(provider))


def lane_configuration(lane):
    """Return auditable provider/model settings for one pipeline lane."""
    provider = lane_provider(lane)
    return {"lane": lane, "provider": provider, "model": lane_model(lane, provider)}


def provider_credential_env(provider):
    """Resolve the provider's credential variable unless a lane overrides it."""
    if provider == "openai":
        return env_value("OPENAI_CREDENTIAL_ENV", "OPENAI_API_KEY")
    if provider == "google":
        return env_value("GOOGLE_VERTEX_AI_CREDENTIAL_ENV", "GOOGLE_APPLICATION_CREDENTIALS")
    if provider == "openrouter":
        return env_value("OPENROUTER_CREDENTIAL_ENV", "OPENROUTER_API_KEY")
    if provider == "anthropic":
        return env_value("ANTHROPIC_CREDENTIAL_ENV", "ANTHROPIC_API_KEY")
    raise ValueError("provider must be one of: " + ", ".join(sorted(LLM_PROVIDERS)))


# A connection string hides a password where no secret-sounding name warns of
# it. Matched on the value rather than the setting name so a future setting
# holding a URL is covered without anyone remembering to add it to a list.
EMBEDDED_CREDENTIAL = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<userinfo>[^/?#@\s]*:[^/?#@\s]*)@"
)


def redact_embedded_credential(value):
    """Return a setting value with any credential embedded in a URL removed."""
    return EMBEDDED_CREDENTIAL.sub(lambda match: f"{match.group('scheme')}[redacted]@", value)


def effective_settings_snapshot():
    """Return non-secret runtime settings actually visible to this process.

    The snapshot is for manifests and audits only. Credential values, tokens,
    keys, and passwords are represented by their configured variable names and
    are never copied into an artifact.

    A name-based rule alone is not enough. ``CANONICAL_DATABASE_URL`` carries no
    secret token in its name, yet a PostgreSQL connection string embeds its
    password in the authority: ``postgresql://user:password@host/db``. The
    snapshot reaches ``analytics.py`` and the ``operations.py`` manifest, both of
    which are shared, so the value is redacted by its shape as well as its name.
    The host and database survive, because identifying the target is the audit
    value; the credential is what must not travel.
    """
    load_project_env()
    secret_tokens = ("KEY", "TOKEN", "PASSWORD", "SECRET")
    prefixes = (
        "LLM_",
        "OPENAI_",
        "OPENROUTER_",
        "GOOGLE_",
        "TABLE_COMPREHENSION_",
        "CLIENT_REVIEW_",
        "ANALYTICS_",
        "ALLOCATION_POLICY_",
        "SCHEMA_DISCOVERY_",
        "RETRIEVAL_",
        "CANONICAL_",
        "HANDWRITING_",
        "ARITHMETIC_",
        "REASSEMBLY_",
        "PREPROCESS_",
    )
    snapshot = {}
    for name in sorted(os.environ):
        if not name.startswith(prefixes):
            continue
        if any(token in name for token in secret_tokens):
            if name.endswith("_ENV") or name.endswith("_CREDENTIAL_ENV"):
                snapshot[name] = os.environ[name]
            else:
                snapshot[name] = "[redacted]"
            continue
        snapshot[name] = redact_embedded_credential(os.environ[name])
    return snapshot


# Vertex publishes hard per-request limits for the served model. They are
# recorded as settings so a capacity plan can cite them, but a recorded limit
# that nothing compares against is indistinguishable from no limit: an operator
# who raises GOOGLE_VERTEX_AI_MAX_PDF_BYTES past the model's per-file ceiling
# would learn about it from a provider error mid-run instead of before it.
# Each entry pairs an operator cap with the model capability that bounds it.
GOOGLE_MODEL_CAPABILITY_BOUNDS = (
    (
        "GOOGLE_VERTEX_AI_MAX_PAGES",
        1000,
        "GOOGLE_VERTEX_AI_MODEL_MAX_FILES_PER_REQUEST",
        3000,
        "each retained page is submitted as its own file",
    ),
    (
        "GOOGLE_VERTEX_AI_MAX_PDF_BYTES",
        10_000_000,
        "GOOGLE_VERTEX_AI_MODEL_MAX_FILE_BYTES",
        50_000_000,
        "a submitted page PDF is one API-uploaded file",
    ),
    (
        "GOOGLE_VERTEX_AI_MAX_PDF_BYTES",
        10_000_000,
        "GOOGLE_VERTEX_AI_MODEL_MAX_INPUT_BYTES",
        524_288_000,
        "a submitted page PDF must fit one request payload",
    ),
)


def google_capability_errors():
    """Return every operator cap that exceeds the served model's declared limit.

    Checked before work begins, in keeping with the rule that a numeric limit is
    a hard bound rather than an aspiration. ``GOOGLE_VERTEX_AI_MODEL_MAX_PAGES_PER_FILE``
    is deliberately absent: intake bursts every source PDF into one-page masters,
    so a submitted file always carries exactly one page and that ceiling is
    satisfied by construction rather than by comparison.
    """
    errors = []
    for cap_name, cap_default, limit_name, limit_default, reason in GOOGLE_MODEL_CAPABILITY_BOUNDS:
        cap = env_int(cap_name, cap_default)
        limit = env_int(limit_name, limit_default)
        if cap > limit:
            errors.append(
                f"{cap_name}={cap} exceeds {limit_name}={limit} ({reason}); "
                "lower the operator cap or correct the recorded model limit"
            )
    return errors


def require_google_capability_limits():
    """Refuse a Google run whose configured caps exceed the model's own limits."""
    errors = google_capability_errors()
    if errors:
        raise ValueError("; ".join(errors))
