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


def test_interview_max_tokens_supports_voice_samples_emit() -> None:
    """#632: voice-samples emit truncated at ~17.7K chars under the prior
    4096-token cap. Bumped to 16384 so a 50K-char voice-samples block
    (the AC target) fits comfortably — Claude Sonnet 4.6 supports up to
    64K output tokens, so headroom remains for further bumps if needed."""
    assert INTERVIEW_MAX_TOKENS >= 16384


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


# #917: all malformed sub-cases surface one plain user message; the
# upstream-shape detail moves to a logged diagnostic (see _map_malformed).
_MALFORMED_USER_MSG = "Something unexpected happened. Try again."


def test_translates_malformed_non_json() -> None:
    """malformed (non-JSON) → kind=malformed + plain user message (#917)."""
    with patch(_URLOPEN, return_value=_FakeResp(b"<html>500 Bad Gateway</html>")):
        with pytest.raises(InterviewRunnerError) as excinfo:
            run_turn(api_key="sk-or-v1-test", history=[], user_message="hi")
    e = excinfo.value
    assert e.kind == "malformed"
    assert e.user_message == _MALFORMED_USER_MSG


def test_translates_malformed_unexpected_shape() -> None:
    """malformed (no choices) → kind=malformed + plain user message (#917)."""
    with patch(_URLOPEN, return_value=_ok_resp({"unexpected": "shape"})):
        with pytest.raises(InterviewRunnerError) as excinfo:
            run_turn(api_key="sk-or-v1-test", history=[], user_message="hi")
    e = excinfo.value
    assert e.kind == "malformed"
    assert e.user_message == _MALFORMED_USER_MSG


def test_translates_malformed_empty_choices() -> None:
    with patch(_URLOPEN, return_value=_ok_resp({"choices": []})):
        with pytest.raises(InterviewRunnerError) as excinfo:
            run_turn(api_key="sk-or-v1-test", history=[], user_message="hi")
    e = excinfo.value
    assert e.kind == "malformed"
    assert e.user_message == _MALFORMED_USER_MSG


def test_translates_malformed_missing_message_content() -> None:
    """malformed (content parse fail) → kind=malformed + plain user message (#917)."""
    body = {"choices": [{"index": 0, "finish_reason": "stop"}]}
    with patch(_URLOPEN, return_value=_ok_resp(body)):
        with pytest.raises(InterviewRunnerError) as excinfo:
            run_turn(api_key="sk-or-v1-test", history=[], user_message="hi")
    e = excinfo.value
    assert e.kind == "malformed"
    assert e.user_message == _MALFORMED_USER_MSG


def test_translates_malformed_non_string_content() -> None:
    """malformed (content not a string) → kind=malformed + plain user message (#917)."""
    body = {"choices": [{"message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}}]}
    with patch(_URLOPEN, return_value=_ok_resp(body)):
        with pytest.raises(InterviewRunnerError) as excinfo:
            run_turn(api_key="sk-or-v1-test", history=[], user_message="hi")
    e = excinfo.value
    assert e.kind == "malformed"
    assert e.user_message == _MALFORMED_USER_MSG


def test_map_malformed_preserves_diagnostic_detail() -> None:
    """The plain user message hides the shape, but _map_malformed still
    expands each upstream prefix into a distinct diagnostic for the log
    line (#917) — so operator-facing debuggability is retained."""
    from findajob.onboarding.interview_runner import _map_malformed

    assert "non-JSON response" in _map_malformed("Non-JSON response: <html>")
    assert "unexpected response shape" in _map_malformed("Unexpected shape: {}")
    assert "Could not parse assistant content" in _map_malformed("Could not parse content: x")
    assert "not a string" in _map_malformed("Content not a string: list")


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


# ── #632: length-finish detection ────────────────────────────────────────


def test_run_turn_raises_on_length_finish_reason() -> None:
    """When OpenRouter returns ``finish_reason='length'``, the LLM hit the
    max_tokens cap mid-output. The user-visible failure mode pre-#632:
    block-emit truncated mid-block → parser silently doesn't capture it →
    user clicks Finalize → "missing blocks" without context. After #632:
    surface ``InterviewRunnerError(kind='length', ...)`` with a clear
    "your input is too long" recovery message so the user can trim and
    retry instead of guessing what went wrong."""
    body = _success_body(text="<<<FILE: voice-samples.md>>>\nlong content truncated mid-")
    body["choices"][0]["finish_reason"] = "length"
    with patch(_URLOPEN, return_value=_ok_resp(body)):
        with pytest.raises(InterviewRunnerError) as excinfo:
            run_turn("sk-or-v1-operator", [], "emit voice samples now")
    e = excinfo.value
    assert e.kind == "length"
    assert e.status_code is None
    # The recovery copy must name the cap and a concrete next action
    assert "too long" in e.user_message.lower() or "truncated" in e.user_message.lower()
    assert "trim" in e.user_message.lower() or "shorter" in e.user_message.lower()


def test_run_turn_passes_through_finish_reason_stop() -> None:
    """Normal (non-truncated) completion: finish_reason='stop' does NOT
    raise — usage dict is returned with the LLM's text intact."""
    body = _success_body(text="hi", usage={"prompt_tokens": 5, "completion_tokens": 1, "cost": 0.0})
    body["choices"][0]["finish_reason"] = "stop"
    with patch(_URLOPEN, return_value=_ok_resp(body)):
        text, _usage = run_turn("sk-or-v1-operator", [], "hi")
    assert text == "hi"


def test_run_turn_passes_through_missing_finish_reason() -> None:
    """Defensive: providers that omit finish_reason are treated as normal
    completion (no spurious length-error raised)."""
    body = _success_body(text="ok")
    # explicitly omit choices[0].finish_reason
    with patch(_URLOPEN, return_value=_ok_resp(body)):
        text, _usage = run_turn("sk-or-v1-operator", [], "ok")
    assert text == "ok"
