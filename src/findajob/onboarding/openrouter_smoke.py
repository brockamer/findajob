"""Smoke-check a user-supplied OpenRouter API key (#328).

Single-purpose module: makes a 1-token chat-completions call against
OpenRouter and reports whether the call succeeded. Used by the
onboarding injector to refuse to write the sentinel when the user's
collected key is invalid — failing loudly here is far better than
letting the pipeline silently fail on first scheduled triage.

Pure stdlib (urllib + json) — no new dependencies.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# The pipeline's existing default-model choice. Cheap (Gemini 3 Flash) +
# already in operators' aichat-ng config so we know it works through
# OpenRouter's routing.
SMOKE_MODEL = "google/gemini-3-flash-preview"
SMOKE_TIMEOUT_S = 30


def verify_openrouter_key(api_key: str) -> tuple[bool, str | None]:
    """Verify the key by issuing a 1-token completion.

    Returns ``(True, None)`` on success, ``(False, error_message)`` on
    failure. Never raises — every failure mode returns a human-readable
    string suitable for rendering directly in the onboarding UI.
    """
    if not api_key or not api_key.strip():
        return False, "OpenRouter API key is empty."

    payload = json.dumps(
        {
            "model": SMOKE_MODEL,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
    ).encode("utf-8")

    req = urllib.request.Request(  # noqa: S310 — POSTing to a fixed https URL
        OPENROUTER_API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/brockamer/findajob",
            "X-Title": "findajob onboarding smoke check",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=SMOKE_TIMEOUT_S) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            error_body = ""
        if e.code == 401:
            return False, (
                "OpenRouter rejected the key (401 Unauthorized). Check that the key is "
                "valid and was copied without extra whitespace."
            )
        if e.code == 402:
            return False, (
                "OpenRouter rejected the request (402 Payment Required). Add prepaid "
                "credit to your OpenRouter account at https://openrouter.ai/credits and "
                "re-paste."
            )
        if e.code == 429:
            return False, ("OpenRouter rate-limited the verification request. Wait a minute and re-paste.")
        return False, f"OpenRouter returned HTTP {e.code}: {error_body[:200]}"
    except urllib.error.URLError as e:
        return False, (
            f"Could not reach OpenRouter ({e.reason}). Check that the container has network access and re-paste."
        )
    except Exception as e:  # noqa: BLE001
        return False, f"Unexpected error verifying OpenRouter key: {type(e).__name__}: {str(e)[:200]}"

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return False, f"OpenRouter returned non-JSON response: {body[:200]}"

    if not isinstance(data, dict) or not data.get("choices"):
        return False, f"OpenRouter returned unexpected response shape: {body[:200]}"

    return True, None


class OnboardingSmokeCheckFailed(Exception):
    """Raised by the injector when verify_openrouter_key returns False.

    Carries the human-readable error so the onboarding route can render it
    verbatim to the user.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.user_message = message
