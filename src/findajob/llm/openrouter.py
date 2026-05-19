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
import re
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

from findajob.paths import BASE


# Deferred import to break the import cycle:
# spend_ceiling → cost_rollups → (no LLM dep)
# spend_ceiling → config_loader → (no LLM dep)
# spend_ceiling → openrouter (this module, for LLMSpendCeilingExceeded)
# Importing spend_ceiling at module-load time would work because
# LLMSpendCeilingExceeded is defined before the import, but a function-
# level import is cleaner and avoids any future ordering fragility.
def _check_call_gate() -> None:
    from findajob.spend_ceiling import check_call_gate  # noqa: PLC0415

    check_call_gate()


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
    cache-miss, or non-Anthropic provider). ``finish_reason`` is the
    OpenAI/OpenRouter completion-reason flag — typically ``"stop"`` on a
    clean completion, ``"length"`` when ``max_tokens`` capped the output
    mid-stream (#632), or ``None`` for providers that omit it.
    """

    text: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cost_usd: float
    generation_id: str | None
    finish_reason: str | None = None


class OpenRouterError(Exception):
    """Raised by :func:`complete` on every non-success path.

    ``kind`` classifies the failure for callers that need a UX/retry
    decision without re-parsing the message string. Values:
    ``auth | payment | rate_limit | upstream | network | malformed | config``.

    ``finish_reason`` is set when the malformed-response shape is "content
    is null because the model hit max_tokens" — i.e. ``finish_reason="length"``
    in the OpenRouter response. Callers can detect a budget-exhausted truncation
    via ``e.finish_reason == "length"`` instead of grepping ``str(e)`` for the
    same substring (which is brittle to message-format drift). #678.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = "unknown",
        status_code: int | None = None,
        finish_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.finish_reason = finish_reason


class LLMSpendCeilingExceeded(Exception):
    """Raised by :func:`complete` when the monthly LLM spend ceiling is exceeded.

    Intentionally NOT a subclass of :class:`OpenRouterError` — callers that
    catch ``OpenRouterError`` and return ``""`` (e.g. ``role_runner.run_role``)
    must NOT swallow this; it signals an operator-configured hard stop.
    """

    def __init__(self, *, ceiling_usd: float, current_sum_usd: float) -> None:
        super().__init__(f"Monthly LLM spend ceiling exceeded: ${current_sum_usd:.2f} / ${ceiling_usd:.2f}")
        self.ceiling_usd = ceiling_usd
        self.current_sum_usd = current_sum_usd


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

    # Raises LLMSpendCeilingExceeded if monthly ceiling is met. No-op when
    # ceiling is disabled or pipeline.db is unavailable.
    _check_call_gate()

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
            delay = _compute_backoff_delay(attempt)
            time.sleep(delay)
    assert last_err is not None
    raise last_err


# ---------------------------------------------------------------------------
# Streaming chunk types for complete_stream()
# ---------------------------------------------------------------------------


class StreamCaptured(TypedDict):
    """Emitted when a <<<END FILE: name.md>>> close marker arrives."""

    type: Literal["captured"]
    name: str  # e.g. "voice_samples_a.md"


class StreamUsage(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cost_usd: float


class StreamFinish(TypedDict):
    """Emitted exactly once at end-of-stream."""

    type: Literal["finish"]
    text: str  # accumulated assistant text across the entire stream
    finish_reason: str | None  # "stop", "length", etc.
    usage: StreamUsage
    generation_id: str | None


class StreamError(TypedDict):
    """Emitted on failure; always the final chunk when yielded."""

    type: Literal["error"]
    kind: str  # OpenRouterError.kind values
    message: str


StreamChunk = StreamCaptured | StreamFinish | StreamError

# Regex for FILE block close markers in accumulated text.
_END_FILE_RE = re.compile(r"<<<END FILE:\s*([^>\s]+)\s*>>>")


def complete_stream(
    *,
    role: str,
    prompt: str,
    history: list[dict] | None = None,
    cache_system: bool = False,
    cached_prefix: str | None = None,
    pin_provider: str | None = None,
    api_key: str | None = None,
    max_tokens: int | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    roles_dir: Path | None = None,
) -> Iterator[StreamChunk]:
    """Streaming variant of :func:`complete`.

    Yields :data:`StreamChunk` typed dicts progressively as OpenRouter emits
    SSE events. Use this for long-running onboarding turns where the user
    needs intermediate progress (file-capture badges) before the full response
    arrives.

    Chunk sequence:

    - Zero or more :class:`StreamCaptured` chunks — one per
      ``<<<END FILE: name.md>>>`` close marker seen in the accumulated buffer.
    - Exactly one :class:`StreamFinish` chunk at end-of-stream (on success).
    - OR exactly one :class:`StreamError` chunk (on failure). If the error
      occurs before the first yield, it is the only chunk. If it occurs after
      a ``captured`` event, it is the final chunk; the generator then closes.

    Spend ceiling:
        :func:`_check_call_gate` runs before any HTTP work. If
        :class:`LLMSpendCeilingExceeded` is raised it propagates immediately
        (not yielded as an error chunk). The route layer returns 402 before
        opening the SSE response — same contract as :func:`complete`.

    Retry boundary:
        Before the first yield: up to ``DEFAULT_MAX_ATTEMPTS`` retries on
        transient failures (rate_limit, upstream, network) with exponential
        backoff — same as :func:`complete`. After the first yield: no retries;
        any failure yields an :class:`StreamError` and closes.

    Cleanup:
        The underlying HTTP response is closed in a ``try/finally`` block.
        ``GeneratorExit`` (e.g. client disconnect) triggers the ``finally``
        without needing an explicit close call on the caller's side.

    TODO(#740 route): SSE route handler POST /onboarding/interview/turn-stream
    TODO(#740 frontend): vanilla JS EventSource consumer
    """
    key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")
    if not key or not key.strip():
        raise OpenRouterError(
            "OPENROUTER_API_KEY not set. Add a key in /onboarding/ Step 1 or in data/.env.",
            kind="config",
        )

    # Raises LLMSpendCeilingExceeded if monthly ceiling is met — caller catches
    # this BEFORE opening the SSE response (route returns 402).
    _check_call_gate()

    base_dir = roles_dir if roles_dir is not None else _DEFAULT_ROLES_DIR
    front, system_prompt = _read_role_file(base_dir / f"{role}.md")
    model = str(front.get("model", ""))
    if model.startswith("openrouter:"):
        model = model[len("openrouter:") :]
    if not model:
        raise OpenRouterError(
            f"Role '{role}' has no model: in frontmatter.",
            kind="config",
        )
    raw_max = max_tokens if max_tokens is not None else front.get("max_tokens", DEFAULT_MAX_TOKENS)
    effective_max_tokens = int(raw_max)
    temperature = front.get("temperature")

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
        "max_tokens": effective_max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if temperature is not None:
        payload["temperature"] = float(temperature)
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

    # --- Pre-first-yield retry loop (mirrors complete()) ---
    last_err: OpenRouterError | None = None
    resp = None
    for attempt in range(DEFAULT_MAX_ATTEMPTS):
        last_err = None
        try:
            resp = urllib.request.urlopen(req, timeout=timeout_s)  # noqa: S310
            break  # success — exit retry loop, proceed to streaming read
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
        except Exception as e:  # noqa: BLE001
            last_err = OpenRouterError(
                f"Unexpected error: {type(e).__name__}: {str(e)[:200]}",
                kind="unknown",
            )

        assert last_err is not None
        if last_err.kind not in RETRY_KINDS:
            yield StreamError(type="error", kind=last_err.kind, message=str(last_err))
            return
        if attempt < DEFAULT_MAX_ATTEMPTS - 1:
            import logging  # noqa: PLC0415

            logging.getLogger(__name__).warning(
                "complete_stream: transient error (attempt %d/%d): %s",
                attempt + 1,
                DEFAULT_MAX_ATTEMPTS,
                last_err,
            )
            time.sleep(_compute_backoff_delay(attempt))
        else:
            # Final attempt failed
            assert last_err is not None
            yield StreamError(type="error", kind=last_err.kind, message=str(last_err))
            return

    if resp is None:
        # Defensive: should not be reachable — loop above either breaks or returns.
        yield StreamError(type="error", kind="unknown", message="Failed to open connection.")
        return

    # --- Streaming read — no retries after this point ---
    accumulated: list[str] = []
    last_capture_pos = 0
    last_finish_reason: str | None = None
    last_usage: StreamUsage = StreamUsage(prompt_tokens=0, completion_tokens=0, cached_tokens=0, cost_usd=0.0)
    generation_id: str | None = None

    try:
        for raw_line_bytes in resp:
            raw_line = raw_line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")

            # SSE comment lines (e.g. ": OPENROUTER PROCESSING") — skip.
            if raw_line.startswith(":"):
                continue
            # Blank line = event boundary in SSE; nothing to parse.
            if not raw_line:
                continue
            # Only parse data: lines.
            if not raw_line.startswith("data: "):
                continue

            payload_str = raw_line[len("data: ") :]

            # Terminal sentinel — stop reading.
            if payload_str.strip() == "[DONE]":
                break

            try:
                chunk = json.loads(payload_str)
            except json.JSONDecodeError:
                continue

            # Extract generation id from any data chunk.
            if generation_id is None:
                generation_id = chunk.get("id")

            # Extract delta content.
            try:
                delta_content = chunk["choices"][0]["delta"].get("content") or ""
            except (KeyError, IndexError, TypeError):
                delta_content = ""

            if delta_content:
                accumulated.append(delta_content)

            # Extract finish_reason (accumulate — terminal chunk has it).
            try:
                fr = chunk["choices"][0].get("finish_reason")
                if fr is not None:
                    last_finish_reason = fr
            except (KeyError, IndexError, TypeError):
                pass

            # Extract usage (accumulate — terminal chunk has it).
            usage_raw = chunk.get("usage")
            if usage_raw:
                ptd = usage_raw.get("prompt_tokens_details") or {}
                last_usage = StreamUsage(
                    prompt_tokens=int(usage_raw.get("prompt_tokens", 0)),
                    completion_tokens=int(usage_raw.get("completion_tokens", 0)),
                    cached_tokens=int(ptd.get("cached_tokens", 0)),
                    cost_usd=float(usage_raw.get("cost", 0.0)),
                )

            # Scan accumulated text for new END FILE markers.
            if delta_content:
                full_so_far = "".join(accumulated)
                for m in _END_FILE_RE.finditer(full_so_far, last_capture_pos):
                    last_capture_pos = m.end()
                    yield StreamCaptured(type="captured", name=m.group(1))

    except Exception as e:  # noqa: BLE001
        # Mid-stream failure — no retries.
        if isinstance(e, (ConnectionResetError, OSError)):
            err_kind = "network"
        elif isinstance(e, urllib.error.URLError):
            err_kind = "network"
        elif isinstance(e, urllib.error.HTTPError):
            err_kind = "upstream"
        else:
            err_kind = "unknown"
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass
        yield StreamError(type="error", kind=err_kind, message=f"{type(e).__name__}: {str(e)[:200]}")
        return
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass

    # Emit the finish event.
    yield StreamFinish(
        type="finish",
        text="".join(accumulated),
        finish_reason=last_finish_reason,
        usage=last_usage,
        generation_id=generation_id,
    )


def _compute_backoff_delay(attempt: int) -> float:
    """Exponential backoff delay for retry attempt (0-indexed).

    Returns a value in ``[RETRY_BASE_DELAY_S, RETRY_MAX_DELAY_S]`` with
    ±0.5s jitter. Shared by :func:`complete` and :func:`complete_stream`.
    """
    return min(
        RETRY_MAX_DELAY_S,
        RETRY_BASE_DELAY_S * (2**attempt) + random.random() * 0.5,
    )


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
        # Surface finish_reason in the error so pipeline.jsonl shows what
        # actually went wrong. "Content not a string: NoneType;
        # finish_reason=length" tells the operator immediately that
        # max_tokens was exhausted (reasoning model ate the budget) —
        # the fix is raising max_tokens, not retrying with the same cap.
        # Also exposed as a structured attribute (#678) so callers can
        # branch on it without grepping the message.
        try:
            finish_reason_for_err = data["choices"][0].get("finish_reason")
        except (KeyError, IndexError, TypeError):
            finish_reason_for_err = None
        raise OpenRouterError(
            f"Content not a string: {type(text).__name__}; finish_reason={finish_reason_for_err}",
            kind="malformed",
            finish_reason=finish_reason_for_err,
        )
    usage = data.get("usage") or {}
    # OpenRouter nests cached_tokens under usage.prompt_tokens_details.cached_tokens
    # (matches Anthropic's native shape). A top-level usage.cached_tokens does not
    # exist in real responses — only the nested form returns from production.
    ptd = usage.get("prompt_tokens_details") or {}
    # #632: finish_reason lives at choices[0].finish_reason in the OpenAI/
    # OpenRouter shape. ``"length"`` signals the response was capped by
    # max_tokens — the caller decides how to handle (interview_runner
    # surfaces it as a typed error so the user can trim and retry).
    finish_reason = data["choices"][0].get("finish_reason") if data.get("choices") else None
    return CompletionResult(
        text=text,
        prompt_tokens=int(usage.get("prompt_tokens", 0)),
        completion_tokens=int(usage.get("completion_tokens", 0)),
        cached_tokens=int(ptd.get("cached_tokens", 0)),
        cost_usd=float(usage.get("cost", 0.0)),
        generation_id=data.get("id"),
        finish_reason=finish_reason,
    )
