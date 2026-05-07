"""Canonical OpenRouter HTTP wrapper for findajob (#470, parent epic #469).

Stdlib-only HTTP client. Promotes the pattern from
``findajob.onboarding.interview_runner`` into a general-purpose
``complete()`` function. Reads role frontmatter, supports Anthropic
``cache_control`` breakpoints, surfaces ``response.usage.cost`` directly,
and raises a typed :class:`OpenRouterError` on every non-success path.

Cache_control plumbing exposes two axes:

- ``cached_prefix``: stable shared content placed as a cache_control-marked
  block at the start of the user message. Cache hits when the prefix
  matches between calls (Anthropic minimum cache size: 1024 tokens).
- ``cache_system``: wrap the system message itself. Useful when the role
  file is large enough to be cacheable on its own.

Anthropic-only billing benefit: ``cache_control`` is honored only by
Anthropic providers (Opus 4.7, Sonnet 4.6, Haiku 4.5). The parameter is
correct for the OpenRouter API on any provider; non-Anthropic providers
silently ignore it.
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from findajob.paths import BASE

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_TIMEOUT_S = 120
DEFAULT_MAX_TOKENS = 4096
DEFAULT_MAX_ATTEMPTS = 3
RETRY_KINDS = frozenset({"rate_limit", "upstream", "network"})
RETRY_BASE_DELAY_S = 0.5
RETRY_MAX_DELAY_S = 8.0
_DEFAULT_ROLES_DIR = Path(BASE) / "config" / "roles"


@dataclass(frozen=True)
class CompletionResult:
    """Return shape of :func:`complete`.

    ``cost_usd`` is from ``response.usage.cost`` — same number that hits
    ``/api/v1/credits`` (1 credit = 1 USD). ``cached_tokens`` is the
    Anthropic prompt-cache hit count; 0 means no cache (cold call,
    cache-miss, or non-Anthropic provider).
    """

    text: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cost_usd: float
    generation_id: str | None


class OpenRouterError(Exception):
    """Raised by :func:`complete` on every non-success path.

    ``kind`` classifies the failure for callers that need a UX/retry
    decision without re-parsing the message string. Values:
    ``auth | payment | rate_limit | upstream | network | malformed | config``.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = "unknown",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


def complete(
    role: str,
    prompt: str,
    *,
    cached_prefix: str | None = None,
    cache_system: bool = False,
    pin_provider: str | None = None,
    history: list[dict] | None = None,
    roles_dir: Path | None = None,
    api_key: str | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    **overrides: object,
) -> CompletionResult:
    """Submit a chat-completion request to OpenRouter and return the result.

    Args:
        role: Name of the role file in ``config/roles/<role>.md``. Frontmatter
            ``model:`` (required), ``temperature:``, ``max_tokens:`` are read.
            An ``openrouter:`` prefix on ``model:`` is stripped before the
            OpenRouter API call.
        prompt: Role-specific user-message tail. Comes AFTER ``cached_prefix``
            in the assembled user message when caching is on.
        cached_prefix: Stable shared content placed as a cache_control-marked
            block at the start of the user message. None means "no cached
            prefix" — user message is just ``prompt`` as a plain string.
            **For cross-call cache hits on Anthropic models, also pass
            pin_provider="anthropic"** — sticky routing isn't auto-triggered
            by cache writes alone (verified empirically 2026-05-06 against
            Opus 4.7: without pinning, second call routes to a different
            edge with cold cache, hit rate falls to 0%).
        cache_system: When True, wrap the system message in a cache_control-
            marked block.
        pin_provider: When set (e.g. ``"anthropic"`` — lowercase slug),
            payload includes ``provider: {"only": [pin_provider]}``. Required
            companion to ``cached_prefix`` for Anthropic cache hits.
        history: Prior ``[{"role":..., "content":...}, ...]`` turns.
        roles_dir: Override for tests; production callers omit.
        api_key: Override; default reads ``OPENROUTER_API_KEY`` env.
        timeout_s: Per-attempt HTTP timeout. Default 120s.
        **overrides: ``model``, ``temperature``, ``max_tokens`` overrides
            (frontmatter values used otherwise).

    Returns:
        :class:`CompletionResult` with text, token counts, cost_usd from
        ``response.usage.cost``, and the generation id.

    Retries:
        Transient failures (``kind`` in ``{rate_limit, upstream, network}``)
        retry up to 3 attempts with exponential backoff (~0.5s → 8s cap),
        so a flapping upstream can stretch a single call to ~10–20s wall
        time before the final ``OpenRouterError`` bubbles. Other ``kind``s
        raise on the first failure with no retry. Synchronous callers
        whose UX depends on quick failure (loading spinners, chat turns)
        should account for this added perceived latency.

    Raises:
        OpenRouterError: every non-success path. ``.kind`` classifies.
    """
    key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")
    if not key or not key.strip():
        raise OpenRouterError(
            "OPENROUTER_API_KEY not set. Add a key in /onboarding/ Step 1 or in data/.env.",
            kind="config",
        )

    base_dir = roles_dir if roles_dir is not None else _DEFAULT_ROLES_DIR
    front, system_prompt = _read_role_file(base_dir / f"{role}.md")
    model = str(overrides.get("model", front.get("model", "")))
    if model.startswith("openrouter:"):
        model = model[len("openrouter:") :]
    if not model:
        raise OpenRouterError(
            f"Role '{role}' has no model: in frontmatter.",
            kind="config",
        )
    raw_max = overrides.get("max_tokens", front.get("max_tokens", DEFAULT_MAX_TOKENS))
    max_tokens = int(raw_max)  # type: ignore[call-overload]
    temperature = overrides.get("temperature", front.get("temperature"))

    if cache_system:
        system_message: dict = {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    else:
        system_message = {"role": "system", "content": system_prompt}
    messages: list[dict] = [system_message]
    if history:
        messages.extend(history)
    if cached_prefix is not None:
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": cached_prefix,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        )
    else:
        messages.append({"role": "user", "content": prompt})

    payload: dict[str, object] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        payload["temperature"] = float(temperature)  # type: ignore[arg-type]
    if pin_provider:
        payload["provider"] = {"only": [pin_provider]}

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 — fixed https URL
        OPENROUTER_API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {key.strip()}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/brockamer/findajob",
            "X-Title": "findajob LLM wrapper",
        },
        method="POST",
    )

    last_err: OpenRouterError | None = None
    for attempt in range(DEFAULT_MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8")
            return _parse_response(raw)
        except urllib.error.HTTPError as e:
            try:
                _raise_for_http_error(e)
            except OpenRouterError as oe:
                last_err = oe
        except urllib.error.URLError as e:
            last_err = OpenRouterError(
                f"Could not reach OpenRouter ({e.reason}).",
                kind="network",
            )
        except OpenRouterError:
            # _parse_response can raise OpenRouterError directly (malformed/etc).
            # Don't retry those; re-raise.
            raise
        except Exception as e:  # noqa: BLE001
            raise OpenRouterError(
                f"Unexpected error: {type(e).__name__}: {str(e)[:200]}",
                kind="unknown",
            ) from e

        assert last_err is not None  # set in every except branch above
        if last_err.kind not in RETRY_KINDS:
            raise last_err
        if attempt < DEFAULT_MAX_ATTEMPTS - 1:
            delay = min(
                RETRY_MAX_DELAY_S,
                RETRY_BASE_DELAY_S * (2**attempt) + random.random() * 0.5,
            )
            time.sleep(delay)
    assert last_err is not None
    raise last_err


def _read_role_file(path: Path) -> tuple[dict, str]:
    """Return ``(frontmatter_dict, system_prompt_body)``.

    Tiny ``key: value`` parser — no full YAML; matches the role-file
    frontmatter shape used across ``config/roles/``. Returns empty dict +
    empty body if the file is missing. Limitation: only flat ``key: value``
    lines; nested mappings or lists aren't parsed (no role file currently
    uses them).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}, ""

    front: dict[str, str] = {}
    body_lines: list[str] = []
    in_front = False
    front_done = False
    for line in text.splitlines():
        if line.strip() == "---":
            if not in_front and not front_done:
                in_front = True
                continue
            if in_front:
                in_front = False
                front_done = True
                continue
        if in_front:
            if ":" in line:
                k, v = line.split(":", 1)
                front[k.strip()] = v.strip()
        elif front_done:
            body_lines.append(line)
        else:
            body_lines.append(line)
    return front, "\n".join(body_lines).lstrip("\n")


def _raise_for_http_error(e: urllib.error.HTTPError) -> None:
    """Map HTTPError to typed OpenRouterError.kind."""
    try:
        body = e.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        body = ""
    if e.code == 401:
        raise OpenRouterError(
            "OpenRouter rejected the API key (401).",
            kind="auth",
            status_code=401,
        ) from e
    if e.code == 402:
        raise OpenRouterError(
            "OpenRouter account out of credit (402). Add credit at https://openrouter.ai/credits.",
            kind="payment",
            status_code=402,
        ) from e
    if e.code == 429:
        raise OpenRouterError(
            "OpenRouter rate-limited (429).",
            kind="rate_limit",
            status_code=429,
        ) from e
    if 500 <= e.code < 600:
        raise OpenRouterError(
            f"OpenRouter/upstream server error ({e.code}).",
            kind="upstream",
            status_code=e.code,
        ) from e
    raise OpenRouterError(
        f"OpenRouter returned HTTP {e.code}: {body[:200]}",
        kind="upstream",
        status_code=e.code,
    ) from e


def _parse_response(raw: str) -> CompletionResult:
    """Parse a successful chat-completions response into CompletionResult."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise OpenRouterError(
            f"Non-JSON response: {raw[:200]}",
            kind="malformed",
        ) from e
    if not isinstance(data, dict) or not data.get("choices"):
        raise OpenRouterError(
            f"Unexpected shape: {raw[:200]}",
            kind="malformed",
        )
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise OpenRouterError(
            f"Could not parse content: {raw[:200]}",
            kind="malformed",
        ) from e
    if not isinstance(text, str):
        raise OpenRouterError(
            f"Content not a string: {type(text).__name__}",
            kind="malformed",
        )
    usage = data.get("usage") or {}
    # OpenRouter nests cached_tokens under usage.prompt_tokens_details.cached_tokens
    # (matches Anthropic's native shape). A top-level usage.cached_tokens does not
    # exist in real responses — only the nested form returns from production.
    ptd = usage.get("prompt_tokens_details") or {}
    return CompletionResult(
        text=text,
        prompt_tokens=int(usage.get("prompt_tokens", 0)),
        completion_tokens=int(usage.get("completion_tokens", 0)),
        cached_tokens=int(ptd.get("cached_tokens", 0)),
        cost_usd=float(usage.get("cost", 0.0)),
        generation_id=data.get("id"),
    )
