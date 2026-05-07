"""Tests for findajob.onboarding.interview_runner (#336 Task 3, Phase 2 refactor #471).

Phase 2: interview_runner is a thin delegate around findajob.llm.openrouter.complete().
All HTTP-boundary mocking now patches findajob.llm.openrouter.urllib.request.urlopen.

Tests verify:
- Pre-flight empty-key guard (still in run_turn)
- Happy path returns (assistant_text, usage_dict)
- Error translation: every OpenRouterError.kind → InterviewRunnerError with
  byte-identical user_message (the route layer renders this verbatim)
- INTERVIEW_MODEL / INTERVIEW_MAX_TOKENS constants still importable (existing callers)
"""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

from findajob.onboarding.interview_runner import (
    INTERVIEW_MAX_TOKENS,
    INTERVIEW_MODEL,
    InterviewRunnerError,
    run_turn,
)

# Patch point — the wrapper's HTTP boundary.
_URLOPEN = "findajob.llm.openrouter.urllib.request.urlopen"


class _FakeResp:
    """urlopen() context-manager-compatible fake."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self._body


def _ok_resp(body: dict) -> _FakeResp:
    return _FakeResp(json.dumps(body).encode("utf-8"))


def _success_body(text: str = "hello", usage: dict | None = None) -> dict:
    body: dict = {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "id": "gen-test-id",
    }
    if usage is not None:
        body["usage"] = usage
    return body


# ── Pre-flight validation ───────────────────────────────────────────────


def test_empty_api_key_raises_immediately() -> None:
    """No network call when the API key is empty."""
    with patch(_URLOPEN) as mock_urlopen:
        with pytest.raises(InterviewRunnerError) as exc:
            run_turn("", [], "hi")
        mock_urlopen.assert_not_called()
    assert "Step 1" in exc.value.user_message or "onboarding" in exc.value.user_message.lower()
    assert exc.value.kind == "config"


def test_whitespace_api_key_raises_immediately() -> None:
    with patch(_URLOPEN) as mock_urlopen:
        with pytest.raises(InterviewRunnerError) as exc:
            run_turn("   \t\n  ", [], "hi")
        mock_urlopen.assert_not_called()
    assert exc.value.kind == "config"


# ── Happy path ───────────────────────────────────────────────────────────


def test_successful_turn_returns_text_and_usage() -> None:
    body = _success_body(
        text="Hi! What role are you targeting?",
        usage={
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cost": 0.003,
            "prompt_tokens_details": {"cached_tokens": 0},
        },
    )
    with patch(_URLOPEN, return_value=_ok_resp(body)):
        text, usage = run_turn("sk-or-v1-operator", [], "begin")
    assert text == "Hi! What role are you targeting?"
    assert usage["prompt_tokens"] == 100
    assert usage["completion_tokens"] == 20
    assert usage["cost"] == 0.003
    assert "cached_tokens" in usage
    assert "generation_id" in usage


def test_usage_has_expected_keys() -> None:
    """Phase 2 usage dict shape: prompt_tokens, completion_tokens, cached_tokens, cost, generation_id."""
    body = _success_body(
        text="ok",
        usage={
            "prompt_tokens": 50,
            "completion_tokens": 10,
            "cost": 0.001,
            "prompt_tokens_details": {"cached_tokens": 5},
        },
    )
    body["id"] = "gen-abc"
    with patch(_URLOPEN, return_value=_ok_resp(body)):
        _, usage = run_turn("sk-or-v1-operator", [], "hi")
    assert set(usage.keys()) == {"prompt_tokens", "completion_tokens", "cached_tokens", "cost", "generation_id"}
    assert usage["generation_id"] == "gen-abc"
    assert usage["cached_tokens"] == 5


def test_empty_history_works_for_first_turn() -> None:
    body = _success_body("Welcome.")
    with patch(_URLOPEN, return_value=_ok_resp(body)):
        text, _ = run_turn("sk-or-v1-operator", [], "begin the interview")
    assert text == "Welcome."


# ── Model pin constant ───────────────────────────────────────────────────


def test_interview_model_is_sonnet_4_6() -> None:
    """Model pin: see issue #336 'Decisions adopted'."""
    assert INTERVIEW_MODEL == "anthropic/claude-sonnet-4-6"


def test_interview_max_tokens_is_4096() -> None:
    assert INTERVIEW_MAX_TOKENS == 4096


# ── Error translation — kind + user_message byte-identical to Phase 1 ──


def test_translates_auth_401() -> None:
    """auth kind → exact Phase 1 user_message string."""
    err = HTTPError(url="x", code=401, msg="Unauthorized", hdrs=None, fp=BytesIO(b""))  # type: ignore[arg-type]
    with patch(_URLOPEN, side_effect=err):
        with pytest.raises(InterviewRunnerError) as excinfo:
            run_turn(api_key="sk-or-v1-test", history=[], user_message="hi")
    e = excinfo.value
    assert e.kind == "auth"
    assert e.status_code == 401
    assert e.user_message == (
        "OpenRouter rejected the API key (401 Unauthorized). Visit /onboarding/ to update your OpenRouter key."
    )


def test_translates_payment_402() -> None:
    """payment kind → exact Phase 1 user_message string."""
    err = HTTPError(url="x", code=402, msg="Payment Required", hdrs=None, fp=BytesIO(b""))  # type: ignore[arg-type]
    with patch(_URLOPEN, side_effect=err):
        with pytest.raises(InterviewRunnerError) as excinfo:
            run_turn(api_key="sk-or-v1-test", history=[], user_message="hi")
    e = excinfo.value
    assert e.kind == "payment"
    assert e.status_code == 402
    assert e.user_message == (
        "Your OpenRouter account is out of credit (402 Payment "
        "Required). Add prepaid credit at "
        "https://openrouter.ai/credits, then continue the interview."
    )


def test_translates_rate_limit_429() -> None:
    """rate_limit kind → exact Phase 1 user_message string."""
    err = HTTPError(url="x", code=429, msg="Too Many Requests", hdrs=None, fp=BytesIO(b""))  # type: ignore[arg-type]
    with patch(_URLOPEN, side_effect=err):
        with pytest.raises(InterviewRunnerError) as excinfo:
            run_turn(api_key="sk-or-v1-test", history=[], user_message="hi")
    e = excinfo.value
    assert e.kind == "rate_limit"
    assert e.status_code == 429
    assert e.user_message == ("OpenRouter rate-limited the request (429). Wait a moment and try again.")


@pytest.mark.parametrize("code", [500, 502, 503, 504, 599])
def test_translates_upstream_5xx(code: int) -> None:
    """upstream kind (5xx) → Phase 1's f-string with the specific status code."""
    err = HTTPError(url="x", code=code, msg="Server Error", hdrs=None, fp=BytesIO(b""))  # type: ignore[arg-type]
    with patch(_URLOPEN, side_effect=err):
        with pytest.raises(InterviewRunnerError) as excinfo:
            run_turn(api_key="sk-or-v1-test", history=[], user_message="hi")
    e = excinfo.value
    assert e.kind == "upstream"
    assert e.status_code == code
    assert e.user_message == (
        f"OpenRouter or the upstream model returned a server error "
        f"({code}). Try again in a moment; the issue is on their side."
    )
    # Backward-compat substring checks from old tests
    assert str(code) in e.user_message
    assert "server error" in e.user_message.lower() or "their side" in e.user_message.lower()


def test_translates_upstream_other_4xx() -> None:
    """upstream kind (other 4xx, e.g. 418) → Phase 1-style message with HTTP code."""
    err = HTTPError(url="x", code=418, msg="I'm a teapot", hdrs=None, fp=BytesIO(b'{"detail":"teapot"}'))  # type: ignore[arg-type]
    with patch(_URLOPEN, side_effect=err):
        with pytest.raises(InterviewRunnerError) as excinfo:
            run_turn(api_key="sk-or-v1-test", history=[], user_message="hi")
    e = excinfo.value
    assert e.kind == "upstream"
    assert e.status_code == 418
    assert "418" in e.user_message


def test_translates_network_urlerror() -> None:
    """network kind → Phase 1's connectivity message with reason embedded."""
    with patch(_URLOPEN, side_effect=URLError("Name or service not known")):
        with pytest.raises(InterviewRunnerError) as excinfo:
            run_turn(api_key="sk-or-v1-test", history=[], user_message="hi")
    e = excinfo.value
    assert e.kind == "network"
    assert e.status_code is None
    assert "Could not reach OpenRouter" in e.user_message
    assert "network" in e.user_message.lower()
    # Matches Phase 1: "Could not reach OpenRouter ({reason}). Check the deployment's..."
    assert "Check the deployment's network connectivity and try again." in e.user_message


def test_translates_malformed_non_json() -> None:
    """malformed (non-JSON) → Phase 1's 'non-JSON response' string prefix."""
    with patch(_URLOPEN, return_value=_FakeResp(b"<html>500 Bad Gateway</html>")):
        with pytest.raises(InterviewRunnerError) as excinfo:
            run_turn(api_key="sk-or-v1-test", history=[], user_message="hi")
    e = excinfo.value
    assert e.kind == "malformed"
    assert "non-JSON" in e.user_message


def test_translates_malformed_unexpected_shape() -> None:
    """malformed (no choices) → Phase 1's 'unexpected response shape' string."""
    with patch(_URLOPEN, return_value=_ok_resp({"unexpected": "shape"})):
        with pytest.raises(InterviewRunnerError) as excinfo:
            run_turn(api_key="sk-or-v1-test", history=[], user_message="hi")
    e = excinfo.value
    assert e.kind == "malformed"
    assert "unexpected response shape" in e.user_message.lower()


def test_translates_malformed_empty_choices() -> None:
    with patch(_URLOPEN, return_value=_ok_resp({"choices": []})):
        with pytest.raises(InterviewRunnerError) as excinfo:
            run_turn(api_key="sk-or-v1-test", history=[], user_message="hi")
    assert excinfo.value.kind == "malformed"


def test_translates_malformed_missing_message_content() -> None:
    """malformed (content parse fail) → Phase 1's 'Could not parse assistant content' string."""
    body = {"choices": [{"index": 0, "finish_reason": "stop"}]}
    with patch(_URLOPEN, return_value=_ok_resp(body)):
        with pytest.raises(InterviewRunnerError) as excinfo:
            run_turn(api_key="sk-or-v1-test", history=[], user_message="hi")
    e = excinfo.value
    assert e.kind == "malformed"
    assert "Could not parse assistant content" in e.user_message


def test_translates_malformed_non_string_content() -> None:
    """malformed (content not a string) → Phase 1's 'not a string' string."""
    body = {"choices": [{"message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}}]}
    with patch(_URLOPEN, return_value=_ok_resp(body)):
        with pytest.raises(InterviewRunnerError) as excinfo:
            run_turn(api_key="sk-or-v1-test", history=[], user_message="hi")
    e = excinfo.value
    assert e.kind == "malformed"
    assert "not a string" in e.user_message


def test_translates_config_missing_key() -> None:
    """config kind (no key in env) → Phase 1's Step 1 redirect message."""
    # Simulate a state where api_key arg is non-empty but env key lookup fails
    # by passing a valid-looking key that gets accepted by run_turn's pre-flight
    # but then the wrapper raises config (e.g. role file missing model).
    # For this test, just verify the kind→message mapping for "config" directly.
    from findajob.llm.openrouter import OpenRouterError
    from findajob.onboarding.interview_runner import _translate

    oe = OpenRouterError("OPENROUTER_API_KEY not set.", kind="config")
    ie = _translate(oe)
    assert ie.kind == "config"
    assert ie.status_code is None
    assert "Step 1" in ie.user_message
    assert "onboarding" in ie.user_message.lower()
    assert ie.user_message == (
        "No OpenRouter key on file for this stack. Visit /onboarding/ "
        "Step 1 to provide your API keys, then return here to start "
        "the interview."
    )


def test_translates_unknown_kind_fallback() -> None:
    """unknown kind → fallback message with the wrapper's raw message snippet, no doubled prefix."""
    from findajob.llm.openrouter import OpenRouterError
    from findajob.onboarding.interview_runner import _translate

    # Simulate the wrapper's actual emit shape for kind=unknown:
    # openrouter.py emits "Unexpected error: {ClassName}: {detail}"
    oe = OpenRouterError("Unexpected error: TypeError: ssl handshake fail", kind="unknown")
    ie = _translate(oe)
    assert ie.kind == "unknown"
    # Must match Phase 1 byte-identical form — no doubled "Unexpected error:" prefix.
    assert ie.user_message == "Unexpected error talking to OpenRouter: TypeError: ssl handshake fail"


def test_translates_unknown_kind_fallback_no_wrapper_prefix() -> None:
    """unknown kind without wrapper prefix passes through unchanged."""
    from findajob.llm.openrouter import OpenRouterError
    from findajob.onboarding.interview_runner import _translate

    oe = OpenRouterError("something exploded", kind="unknown")
    ie = _translate(oe)
    assert ie.kind == "unknown"
    assert ie.user_message == "Unexpected error talking to OpenRouter: something exploded"
