"""Shared bounded execution, retry, and response-cache controls for LLM lanes."""

import hashlib
import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - supported deployment targets provide fcntl.
    fcntl = None


def cache_key(payload):
    """Return a stable key for immutable page/provider/schema request inputs."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def adaptive_byte_batches(items, packet_bytes, max_items, max_batches=0):
    """Greedily pack whole items without exceeding an exact packet-byte budget.

    ``packet_bytes`` returns the remaining serialized-byte budget for a proposed
    packet. Individually oversized and batch-cap-deferred items are returned so
    a caller can retain explicit exceptions rather than silently truncating work.
    """
    if not isinstance(max_items, int) or max_items < 1:
        raise ValueError("max_items must be positive")
    if not isinstance(max_batches, int) or max_batches < 0:
        raise ValueError("max_batches must be non-negative")
    batches, oversized, deferred, current = [], [], [], []
    for item in items:
        candidate = [*current, item]
        if len(candidate) <= max_items and packet_bytes(candidate) >= 0:
            current = candidate
            continue
        if current:
            batches.append(current)
            current = []
        if packet_bytes([item]) < 0:
            oversized.append(item)
        else:
            current = [item]
    if current:
        batches.append(current)
    if max_batches:
        deferred = [item for batch in batches[max_batches:] for item in batch]
        batches = batches[:max_batches]
    return batches, oversized, deferred


class ResponseCache:
    """Content-addressed JSON cache; cache contents never replace source evidence."""

    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else None
        if self.directory:
            self.directory.mkdir(parents=True, exist_ok=True)

    def read(self, key):
        """Return a retained response for this key, or None.

        A hit replays a retained response instead of calling the provider, which
        the artifact records: a genuinely fresh call needs an unset cache.
        """
        if not self.directory:
            return None
        path = self.directory / f"{key}.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text())

    def write(self, key, value):
        """Write a cache entry atomically, retaining when it was written."""
        if not self.directory:
            return
        path = self.directory / f"{key}.json"
        if path.exists():
            return
        value = {**value, "written_at": datetime.now(UTC).isoformat()}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2) + "\n")
        temporary.replace(path)


RETRYABLE_ERROR_NAMES = {
    "APITimeoutError",
    "ConnectError",
    "ConnectTimeout",
    "HTTPError",
    "ReadTimeout",
    "RateLimitError",
    "TimeoutError",
    "URLError",
    "WriteTimeout",
}


class ProviderQuotaExhausted(RuntimeError):
    """A provider-wide quota circuit is open; no further request was attempted."""


_quota_circuits = set()
_quota_circuits_lock = threading.Lock()


def fail_fast_on_provider_quota():
    """Return the global, explicitly configurable quota-circuit setting."""
    value = os.environ.get("LLM_FAIL_FAST_ON_PROVIDER_QUOTA", "true").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError("LLM_FAIL_FAST_ON_PROVIDER_QUOTA must be a boolean")


def provider_quota_error(error):
    """Identify account/model quota exhaustion rather than a transient 429."""
    if getattr(error, "status_code", None) != 429 and type(error).__name__ != "RateLimitError":
        return False
    text = str(error).casefold()
    return any(
        marker in text
        for marker in (
            "quota",
            "free-models-per-day",
            "insufficient_quota",
            "exceeded your current quota",
            "credit limit",
        )
    )


def provider_quota_circuit_open(provider):
    """Return whether this provider's quota circuit is open in this process."""
    with _quota_circuits_lock:
        return provider in _quota_circuits


def open_provider_quota_circuit(provider):
    """Stop calling a provider that has reported a quota refusal.

    Unsent work stays an explicit provider exception rather than a gap: the
    circuit prevents wasted calls, it does not decide the work was unnecessary.
    """
    with _quota_circuits_lock:
        _quota_circuits.add(provider)


def reset_provider_quota_circuits():
    """Reset process-local circuits at the start of an independent run."""
    with _quota_circuits_lock:
        _quota_circuits.clear()


PROVIDER_LIMITS = {
    "openai": {
        "requests": "OPENAI_RATE_LIMIT_REQUESTS_PER_MINUTE",
        "tokens": "OPENAI_RATE_LIMIT_TOKENS_PER_MINUTE",
        "max_request_tokens": "OPENAI_MAX_REQUEST_TOKENS",
        "output_reserve": "OPENAI_OUTPUT_TOKEN_RESERVE",
    },
    "google": {
        "requests": "GOOGLE_VERTEX_AI_RATE_LIMIT_REQUESTS_PER_MINUTE",
        "tokens": "GOOGLE_VERTEX_AI_RATE_LIMIT_TOKENS_PER_MINUTE",
        "max_request_tokens": "GOOGLE_VERTEX_AI_MAX_REQUEST_TOKENS",
        "output_reserve": "GOOGLE_VERTEX_AI_OUTPUT_TOKEN_RESERVE",
        "context_tokens": "GOOGLE_VERTEX_AI_MODEL_CONTEXT_TOKENS",
        "model_max_output_tokens": "GOOGLE_VERTEX_AI_MODEL_MAX_OUTPUT_TOKENS",
    },
    "openrouter": {
        "requests": "OPENROUTER_RATE_LIMIT_REQUESTS_PER_MINUTE",
        "tokens": "OPENROUTER_RATE_LIMIT_TOKENS_PER_MINUTE",
        "max_request_tokens": "OPENROUTER_MAX_REQUEST_TOKENS",
        "output_reserve": "OPENROUTER_OUTPUT_TOKEN_RESERVE",
    },
    # Anthropic was the one configurable lane provider with no entry here, so
    # `request_throttle("anthropic")` fell through to a throttle carrying no
    # request budget at all: no pacing, and -- worse -- no request-size preflight.
    # A lane is supposed to behave the same whichever provider serves it, and an
    # oversized request that reaches the provider becomes a retained exception
    # needing its own recovery run rather than a refusal before the call.
    "anthropic": {
        "requests": "ANTHROPIC_RATE_LIMIT_REQUESTS_PER_MINUTE",
        "tokens": "ANTHROPIC_RATE_LIMIT_TOKENS_PER_MINUTE",
        "max_request_tokens": "ANTHROPIC_MAX_REQUEST_TOKENS",
        "output_reserve": "ANTHROPIC_OUTPUT_TOKEN_RESERVE",
    },
}


class RateLimitConfigurationError(ValueError):
    """Raised before a request when its estimated size cannot fit the budget."""


def retryable_error(error):
    """Identify transient HTTP/SDK failures safe for an idempotent retry."""
    if type(error).__name__ in RETRYABLE_ERROR_NAMES:
        return True
    return getattr(error, "status_code", None) in {408, 409, 425, 429, 500, 502, 503, 504}


def retry_after_seconds(error):
    """Read numeric or HTTP-date Retry-After without retaining provider details."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {}) if response is not None else {}
    value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            from email.utils import parsedate_to_datetime

            return max(0.0, parsedate_to_datetime(str(value)).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


class RequestThrottle:
    """Coordinate conservative request and token budgets across local processes.

    The limiter deliberately spaces requests instead of attempting to predict a
    provider's exact rolling-window implementation.  A request whose estimated
    prompt plus output reserve exceeds the configured per-request ceiling fails
    closed before network I/O, avoiding a known 429 and wasted provider work.
    """

    def __init__(
        self,
        directory,
        provider="shared",
        min_interval=0.0,
        sleep=time.sleep,
        requests_per_minute=None,
        tokens_per_minute=None,
        max_request_tokens=None,
        output_reserve=0,
        context_tokens=0,
        model_max_output_tokens=0,
        safety_ratio=None,
    ):
        self.directory = Path(directory) if directory else None
        self.provider = provider or "shared"
        self.min_interval = float(min_interval)
        self.sleep = sleep
        self.requests_per_minute = float(requests_per_minute or 0)
        self.tokens_per_minute = float(tokens_per_minute or 0)
        self.max_request_tokens = float(max_request_tokens or 0)
        self.output_reserve = float(output_reserve or 0)
        self.context_tokens = float(context_tokens or 0)
        self.model_max_output_tokens = float(model_max_output_tokens or 0)
        self.safety_ratio = float(
            os.environ.get("LLM_RATE_LIMIT_SAFETY_RATIO", "0.8")
            if safety_ratio is None
            else safety_ratio
        )
        if self.min_interval < 0:
            raise ValueError("minimum request interval must be non-negative")
        if any(value < 0 for value in (self.requests_per_minute, self.tokens_per_minute)):
            raise ValueError("rate limits must be non-negative")
        if (
            self.context_tokens
            and self.max_request_tokens + self.output_reserve > self.context_tokens
        ):
            raise ValueError("request token ceiling plus output reserve exceeds model context")

        if self.model_max_output_tokens and self.output_reserve > self.model_max_output_tokens:
            raise ValueError("output reserve exceeds model maximum output tokens")
        if not 0 < self.safety_ratio <= 1:
            raise ValueError("rate-limit safety ratio must be greater than zero and at most one")

    def effective_prompt_ceiling(self):
        """Return the per-request prompt ceiling that actually binds.

        ``max_request_tokens`` is not the operative limit whenever the per-minute
        safety budget is smaller, which is why a configured ceiling can look
        generous and still be unreachable. Both bounds are reported together so a
        rejection names the number the operator can act on.
        """
        budget = self.tokens_per_minute * self.safety_ratio
        bounds = [value for value in (self.max_request_tokens, budget) if value]
        return max(0.0, min(bounds) - self.output_reserve) if bounds else 0.0

    def acquire(self, request_tokens=0):
        """Wait until this provider's next request slot is available."""
        if request_tokens < 0:
            raise ValueError("estimated request tokens must be non-negative")
        estimated = float(request_tokens) + self.output_reserve
        safe_tokens = self.tokens_per_minute * self.safety_ratio
        if self.max_request_tokens and estimated > self.max_request_tokens:
            raise RateLimitConfigurationError(
                f"estimated request size {int(estimated)} exceeds {self.provider} "
                f"max request tokens {int(self.max_request_tokens)}"
            )
        if safe_tokens and estimated > safe_tokens:
            raise RateLimitConfigurationError(
                f"estimated request size {int(estimated)} exceeds {self.provider} "
                f"safe token budget {int(safe_tokens)} "
                f"(tokens_per_minute x safety_ratio); the effective per-request "
                f"prompt ceiling is {int(self.effective_prompt_ceiling())}"
            )
        if self.directory is None:
            return 0.0
        if fcntl is None:
            return 0.0  # pragma: no cover - Windows uses provider SDK throttling.
        self.directory.mkdir(parents=True, exist_ok=True)
        lock_path = self.directory / f"{self.provider}.timestamp"
        # Reserve this request's slot while holding the lock, then release it and
        # sleep. Sleeping under the lock serialized every worker and every
        # concurrent process behind one back-off, so a configured worker count
        # bought no parallelism whenever any interval was non-zero. Writing the
        # reserved instant rather than the post-sleep time also keeps spacing
        # measured from the slot, not from when a slow request happened to finish.
        with lock_path.open("a+", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            stream.seek(0)
            raw = stream.read().strip()
            now = time.time()
            last = float(raw) if raw else 0.0
            intervals = [self.min_interval]
            if self.requests_per_minute:
                intervals.append(60.0 / (self.requests_per_minute * self.safety_ratio))
            if self.tokens_per_minute and estimated:
                intervals.append(60.0 * estimated / safe_tokens)
            start_at = max(now, last + max(intervals))
            stream.seek(0)
            stream.truncate()
            stream.write(str(start_at))
            stream.flush()
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        delay = max(0.0, start_at - time.time())
        if delay:
            self.sleep(delay)
        return delay


def request_throttle(provider="shared"):
    """Build the shared throttle from non-secret environment controls."""
    interval = float(os.environ.get("LLM_MIN_REQUEST_INTERVAL_SECONDS", "0"))
    directory = os.environ.get("LLM_THROTTLE_DIR", ".llm-rate-limit")
    settings = PROVIDER_LIMITS.get(provider)
    if not settings:
        return RequestThrottle(directory, provider, interval)
    return RequestThrottle(
        directory,
        provider,
        interval,
        requests_per_minute=float(os.environ.get(settings["requests"], "60")),
        tokens_per_minute=float(os.environ.get(settings["tokens"], "100000")),
        max_request_tokens=float(os.environ.get(settings["max_request_tokens"], "80000")),
        output_reserve=float(
            os.environ.get(settings["output_reserve"], "8192" if provider == "google" else "4096")
        ),
        context_tokens=float(os.environ.get(settings.get("context_tokens", ""), "0")),
        model_max_output_tokens=float(
            os.environ.get(settings.get("model_max_output_tokens", ""), "0")
        ),
    )


def estimate_tokens(value):
    """Estimate input tokens without a provider tokenizer, erring high.

    Characters divided by four is a reasonable average for English prose and
    wrong by two to four times for CJK text, dense JSON punctuation, and long
    identifiers. The error ran in the permissive direction, so an underestimate
    passed the request-size preflight and the provider rejected the call --
    turning a catchable configuration error into a retained provider exception
    that needs a separate no-clobber recovery run.
    """
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    data = text.encode("utf-8")
    wide = sum(1 for character in text if ord(character) > 0x2E80)
    estimate = (len(data) + 3) // 4 + wide
    return max(1, int(estimate * token_estimate_safety_factor()))


def token_estimate_safety_factor():
    """Return the configured multiplier applied to every token estimate."""
    value = float(os.environ.get("LLM_TOKEN_ESTIMATE_SAFETY_FACTOR", "1.15"))
    if not 1.0 <= value <= 4.0:
        raise ValueError("LLM_TOKEN_ESTIMATE_SAFETY_FACTOR must be from 1.0 through 4.0")
    return value


def retry_call(
    function,
    max_retries,
    backoff_seconds=1.0,
    sleep=time.sleep,
    *,
    max_backoff_seconds=30.0,
    jitter=False,
    uniform=random.uniform,
    retryable=None,
    on_retry=None,
    throttle=None,
    provider="shared",
    request_tokens=0,
):
    """Retry an idempotent provider call with capped exponential backoff.

    ``jitter=True`` uses full uniform jitter to avoid synchronized parallel
    runs. ``Retry-After`` is honored when supplied by the provider. The
    default remains compatible with deterministic local callers; production
    provider lanes opt into transient-error filtering and jitter.
    """
    if max_retries < 0 or backoff_seconds < 0:
        raise ValueError("retry count and backoff must be non-negative")
    if max_backoff_seconds <= 0 or backoff_seconds > max_backoff_seconds:
        raise ValueError("backoff must not exceed a positive maximum")
    should_retry = retryable or (lambda _error: True)
    throttle = throttle or request_throttle(provider)
    if fail_fast_on_provider_quota() and provider_quota_circuit_open(provider):
        raise ProviderQuotaExhausted(
            f"{provider} provider quota circuit is open; request was not attempted"
        )
    for attempt in range(max_retries + 1):  # pragma: no branch - every attempt returns or re-raises
        try:
            throttle.acquire(request_tokens=request_tokens)
            return function()
        except Exception as error:
            if fail_fast_on_provider_quota() and provider_quota_error(error):
                open_provider_quota_circuit(provider)
                raise ProviderQuotaExhausted(
                    f"{provider} provider quota exhausted; remaining requests deferred"
                ) from error
            if attempt >= max_retries or not should_retry(error):
                raise
            exponential = min(max_backoff_seconds, backoff_seconds * (2**attempt))
            requested = retry_after_seconds(error)
            cap = min(max_backoff_seconds, requested) if requested is not None else exponential
            delay = cap if requested is not None else uniform(0.0, cap) if jitter else cap
            if on_retry is not None:
                on_retry(attempt + 1, error, delay)
            sleep(delay)


def parallel_map(items, function, max_workers=1):
    """Apply a page function with bounded concurrency while preserving input order."""
    if not isinstance(max_workers, int) or max_workers < 1:
        raise ValueError("max workers must be a positive integer")
    if max_workers == 1:
        return [function(item) for item in items]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(function, items))
