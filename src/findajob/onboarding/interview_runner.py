"""Multi-turn LLM interview runner for the in-app onboarding flow (#336 Task 3).

Extends the ``openrouter_smoke.py`` urllib pattern to multi-turn chat
completions. Pure stdlib — no new dependencies.

Used by ``routes/onboarding_interview.py`` (Task 4) — one ``run_turn``
call per HTMX-posted user turn from the chat UI.

Every non-success path raises :class:`InterviewRunnerError` with a
``user_message`` attribute suitable for verbatim render in the chat
UI's error banner (Task 6). Never raises a generic exception.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

INTERVIEW_MODEL = "anthropic/claude-sonnet-4-6"
INTERVIEW_TIMEOUT_S = 120
INTERVIEW_MAX_TOKENS = 4096


class InterviewRunnerError(Exception):
    """Raised by :func:`run_turn` on any non-success path.

    ``user_message`` is rendered verbatim in the chat UI's error banner.
    ``kind`` classifies the failure so the route layer (#336 Task 7) can
    pick a UX variant (auth/payment/rate-limit/upstream/network/malformed/
    config) without re-parsing the message string. ``status_code`` is the
    HTTP status from OpenRouter when available.
    """

    def __init__(
        self,
        user_message: str,
        *,
        kind: str = "unknown",
        status_code: int | None = None,
    ) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.kind = kind
        self.status_code = status_code


def run_turn(
    operator_key: str,
    system_prompt: str,
    history: list[dict[str, str]],
    user_message: str,
) -> tuple[str, dict]:
    """Submit one user turn + receive the assistant turn.

    Args:
        operator_key: ``OPENROUTER_OPERATOR_KEY`` — operator-funded, distinct
            from the per-tester key collected at finalize.
        system_prompt: full role system prompt (typically the contents of
            ``config/roles/onboarding_interviewer.md``).
        history: prior turns as ``[{"role":"user"|"assistant","content":"..."}, ...]``.
            May be an empty list for the first turn — caller supplies a
            synthetic kick-off via ``user_message``.
        user_message: the new user turn text.

    Returns:
        ``(assistant_text, usage_dict)``. ``usage_dict`` carries OpenRouter's
        reported token + cost fields when present (empty dict otherwise).

    Raises:
        InterviewRunnerError: every non-success path — empty key, network,
            auth, payment, rate-limit, model 5xx, malformed response.
    """
    if not operator_key or not operator_key.strip():
        raise InterviewRunnerError(
            "No OpenRouter key resolved for this stack. Provide your API keys "
            "at /onboarding/ Step 1, or have the administrator set "
            "OPENROUTER_OPERATOR_KEY to subsidize the interview.",
            kind="config",
        )

    # Mark the system prompt cacheable via OpenRouter's `cache_control`
    # breakpoint. Anthropic providers honor this and bill cached system
    # tokens at ~10% on subsequent turns — the system prompt is ~25KB and
    # is re-sent every turn, so caching is the difference between a ~$3
    # interview and a ~$0.50 one.
    messages: list[dict] = [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    ]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    payload = json.dumps(
        {
            "model": INTERVIEW_MODEL,
            "messages": messages,
            "max_tokens": INTERVIEW_MAX_TOKENS,
        }
    ).encode("utf-8")

    req = urllib.request.Request(  # noqa: S310 — POSTing to a fixed https URL
        OPENROUTER_API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {operator_key.strip()}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/brockamer/findajob",
            "X-Title": "findajob in-app onboarding",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=INTERVIEW_TIMEOUT_S) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            error_body = ""
        if e.code == 401:
            raise InterviewRunnerError(
                "OpenRouter rejected the operator key (401 Unauthorized). The "
                "administrator of this deployment needs to update "
                "OPENROUTER_OPERATOR_KEY.",
                kind="auth",
                status_code=401,
            ) from e
        if e.code == 402:
            raise InterviewRunnerError(
                "Operator's OpenRouter account is out of credit (402 Payment "
                "Required). The administrator needs to add prepaid credit at "
                "https://openrouter.ai/credits.",
                kind="payment",
                status_code=402,
            ) from e
        if e.code == 429:
            raise InterviewRunnerError(
                "OpenRouter rate-limited the request (429). Wait a moment and try again.",
                kind="rate_limit",
                status_code=429,
            ) from e
        if 500 <= e.code < 600:
            raise InterviewRunnerError(
                f"OpenRouter or the upstream model returned a server error "
                f"({e.code}). Try again in a moment; the issue is on their side.",
                kind="upstream",
                status_code=e.code,
            ) from e
        raise InterviewRunnerError(
            f"OpenRouter returned HTTP {e.code}: {error_body[:200]}",
            kind="upstream",
            status_code=e.code,
        ) from e
    except urllib.error.URLError as e:
        raise InterviewRunnerError(
            f"Could not reach OpenRouter ({e.reason}). Check the deployment's network connectivity and try again.",
            kind="network",
        ) from e
    except Exception as e:  # noqa: BLE001
        raise InterviewRunnerError(
            f"Unexpected error talking to OpenRouter: {type(e).__name__}: {str(e)[:200]}",
            kind="unknown",
        ) from e

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise InterviewRunnerError(
            f"OpenRouter returned non-JSON response: {body[:200]}",
            kind="malformed",
        ) from e

    if not isinstance(data, dict) or not data.get("choices"):
        raise InterviewRunnerError(
            f"OpenRouter returned unexpected response shape: {body[:200]}",
            kind="malformed",
        )

    try:
        assistant_text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise InterviewRunnerError(
            f"Could not parse assistant content from OpenRouter response: {body[:200]}",
            kind="malformed",
        ) from e

    if not isinstance(assistant_text, str):
        raise InterviewRunnerError(
            f"Assistant content was not a string: {type(assistant_text).__name__}",
            kind="malformed",
        )

    usage_raw = data.get("usage")
    usage: dict = usage_raw if isinstance(usage_raw, dict) else {}

    return assistant_text, usage
