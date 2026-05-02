"""Tests for findajob.onboarding.interview_runner (#336 Task 3).

Stub urllib.request.urlopen at the module level — no real network calls.
Mirrors the test pattern in test_openrouter_smoke.py but validates the
multi-turn semantics: payload includes the full prior history, every
non-success path raises InterviewRunnerError with a user_message, and
the (assistant_text, usage) tuple round-trips cleanly.
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

from findajob.onboarding.interview_runner import (
    INTERVIEW_MAX_TOKENS,
    INTERVIEW_MODEL,
    InterviewRunnerError,
    run_turn,
)


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
    body = {"choices": [{"message": {"role": "assistant", "content": text}}]}
    if usage is not None:
        body["usage"] = usage
    return body


# ── Pre-flight validation ───────────────────────────────────────────────


def test_empty_operator_key_raises_immediately() -> None:
    """No network call when the operator key is empty."""
    with patch("findajob.onboarding.interview_runner.urllib.request.urlopen") as mock_urlopen:
        with pytest.raises(InterviewRunnerError) as exc:
            run_turn("", "system", [], "hi")
        mock_urlopen.assert_not_called()
    # User-facing message points the user back to /onboarding/ Step 1
    # (and mentions the operator-funded fallback for completeness).
    assert "Step 1" in exc.value.user_message or "onboarding" in exc.value.user_message.lower()


def test_whitespace_operator_key_raises_immediately() -> None:
    with patch("findajob.onboarding.interview_runner.urllib.request.urlopen") as mock_urlopen:
        with pytest.raises(InterviewRunnerError):
            run_turn("   \t\n  ", "system", [], "hi")
        mock_urlopen.assert_not_called()


# ── Happy path + payload contract ───────────────────────────────────────


def test_successful_turn_returns_text_and_usage() -> None:
    body = _success_body(
        text="Hi! What role are you targeting?",
        usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    )
    with patch(
        "findajob.onboarding.interview_runner.urllib.request.urlopen",
        return_value=_ok_resp(body),
    ):
        text, usage = run_turn("sk-or-v1-operator", "ROLE PROMPT", [], "begin")
    assert text == "Hi! What role are you targeting?"
    assert usage == {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}


def test_payload_contains_system_history_and_user_in_order() -> None:
    """Multi-turn contract: system + full prior history + new user_message."""
    history = [
        {"role": "user", "content": "begin"},
        {"role": "assistant", "content": "What role?"},
        {"role": "user", "content": "DC ops"},
        {"role": "assistant", "content": "Tell me about your team."},
    ]
    captured: dict = {}

    def _capture(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _ok_resp(_success_body("Got it."))

    with patch(
        "findajob.onboarding.interview_runner.urllib.request.urlopen",
        side_effect=_capture,
    ):
        run_turn("sk-or-v1-operator", "SYSTEM", history, "Founded RTP Labs at Meta.")

    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["body"]["model"] == INTERVIEW_MODEL
    assert captured["body"]["max_tokens"] == INTERVIEW_MAX_TOKENS
    # System message uses the cache-control breakpoint shape so OpenRouter
    # bills cached system tokens at ~10% on subsequent turns. Subsequent
    # messages are still the simple {role, content} form.
    assert captured["body"]["messages"] == [
        {
            "role": "system",
            "content": [{"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}],
        },
        {"role": "user", "content": "begin"},
        {"role": "assistant", "content": "What role?"},
        {"role": "user", "content": "DC ops"},
        {"role": "assistant", "content": "Tell me about your team."},
        {"role": "user", "content": "Founded RTP Labs at Meta."},
    ]


def test_authorization_header_uses_operator_key() -> None:
    captured: dict = {}

    def _capture(req, timeout=None):
        captured["headers"] = dict(req.headers)
        return _ok_resp(_success_body())

    with patch(
        "findajob.onboarding.interview_runner.urllib.request.urlopen",
        side_effect=_capture,
    ):
        run_turn("  sk-or-v1-operator-with-spaces  ", "SYSTEM", [], "hi")

    # urllib title-cases header keys.
    assert captured["headers"]["Authorization"] == "Bearer sk-or-v1-operator-with-spaces"
    assert captured["headers"]["Content-type"] == "application/json"
    assert "findajob" in captured["headers"]["X-title"].lower()


def test_empty_history_works_for_first_turn() -> None:
    """First turn: history=[] + user kick-off → still valid payload."""
    captured: dict = {}

    def _capture(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _ok_resp(_success_body("Welcome."))

    with patch(
        "findajob.onboarding.interview_runner.urllib.request.urlopen",
        side_effect=_capture,
    ):
        text, _ = run_turn("sk-or-v1-operator", "SYSTEM", [], "begin the interview")
    assert text == "Welcome."
    assert captured["body"]["messages"] == [
        {
            "role": "system",
            "content": [{"type": "text", "text": "SYSTEM", "cache_control": {"type": "ephemeral"}}],
        },
        {"role": "user", "content": "begin the interview"},
    ]


def test_usage_defaults_to_empty_dict_when_omitted() -> None:
    body = _success_body("ok")  # no usage field
    with patch(
        "findajob.onboarding.interview_runner.urllib.request.urlopen",
        return_value=_ok_resp(body),
    ):
        _, usage = run_turn("sk-or-v1-operator", "SYSTEM", [], "hi")
    assert usage == {}


def test_usage_defaults_to_empty_dict_when_non_dict() -> None:
    """Defensive: OpenRouter returns 'usage': null or some unexpected shape."""
    body = {"choices": [{"message": {"content": "ok"}}], "usage": None}
    with patch(
        "findajob.onboarding.interview_runner.urllib.request.urlopen",
        return_value=_ok_resp(body),
    ):
        _, usage = run_turn("sk-or-v1-operator", "SYSTEM", [], "hi")
    assert usage == {}


# ── HTTP error paths ────────────────────────────────────────────────────


def test_401_raises_with_friendly_admin_message() -> None:
    err = HTTPError("u", 401, "Unauthorized", {}, fp=io.BytesIO(b'{"error":"bad key"}'))  # type: ignore[arg-type]
    with patch(
        "findajob.onboarding.interview_runner.urllib.request.urlopen",
        side_effect=err,
    ):
        with pytest.raises(InterviewRunnerError) as exc:
            run_turn("sk-or-v1-operator", "SYSTEM", [], "hi")
    msg = exc.value.user_message
    assert "401" in msg
    assert "OPENROUTER_OPERATOR_KEY" in msg


def test_402_raises_with_credit_message() -> None:
    err = HTTPError("u", 402, "Payment Required", {}, fp=io.BytesIO(b""))  # type: ignore[arg-type]
    with patch(
        "findajob.onboarding.interview_runner.urllib.request.urlopen",
        side_effect=err,
    ):
        with pytest.raises(InterviewRunnerError) as exc:
            run_turn("sk-or-v1-operator", "SYSTEM", [], "hi")
    msg = exc.value.user_message
    assert "credit" in msg.lower()
    assert "https://openrouter.ai/credits" in msg


def test_429_raises_with_rate_limit_message() -> None:
    err = HTTPError("u", 429, "Too Many Requests", {}, fp=io.BytesIO(b""))  # type: ignore[arg-type]
    with patch(
        "findajob.onboarding.interview_runner.urllib.request.urlopen",
        side_effect=err,
    ):
        with pytest.raises(InterviewRunnerError) as exc:
            run_turn("sk-or-v1-operator", "SYSTEM", [], "hi")
    assert "429" in exc.value.user_message or "rate" in exc.value.user_message.lower()


@pytest.mark.parametrize("code", [500, 502, 503, 504, 599])
def test_5xx_raises_with_server_error_message(code: int) -> None:
    err = HTTPError("u", code, "Server Error", {}, fp=io.BytesIO(b""))  # type: ignore[arg-type]
    with patch(
        "findajob.onboarding.interview_runner.urllib.request.urlopen",
        side_effect=err,
    ):
        with pytest.raises(InterviewRunnerError) as exc:
            run_turn("sk-or-v1-operator", "SYSTEM", [], "hi")
    msg = exc.value.user_message
    assert str(code) in msg
    assert "server error" in msg.lower() or "their side" in msg.lower()


def test_other_4xx_raises_with_generic_http_message() -> None:
    err = HTTPError("u", 418, "I'm a teapot", {}, fp=io.BytesIO(b'{"detail":"teapot"}'))  # type: ignore[arg-type]
    with patch(
        "findajob.onboarding.interview_runner.urllib.request.urlopen",
        side_effect=err,
    ):
        with pytest.raises(InterviewRunnerError) as exc:
            run_turn("sk-or-v1-operator", "SYSTEM", [], "hi")
    assert "418" in exc.value.user_message


# ── Network + parse error paths ─────────────────────────────────────────


def test_network_error_raises_with_connectivity_message() -> None:
    with patch(
        "findajob.onboarding.interview_runner.urllib.request.urlopen",
        side_effect=URLError("Name or service not known"),
    ):
        with pytest.raises(InterviewRunnerError) as exc:
            run_turn("sk-or-v1-operator", "SYSTEM", [], "hi")
    assert "OpenRouter" in exc.value.user_message
    assert "network" in exc.value.user_message.lower()


def test_unexpected_exception_raises_friendly_wrapper() -> None:
    """If urlopen raises something completely unexpected, we still wrap it."""
    with patch(
        "findajob.onboarding.interview_runner.urllib.request.urlopen",
        side_effect=RuntimeError("disk on fire"),
    ):
        with pytest.raises(InterviewRunnerError) as exc:
            run_turn("sk-or-v1-operator", "SYSTEM", [], "hi")
    assert "Unexpected error" in exc.value.user_message
    assert "RuntimeError" in exc.value.user_message


def test_non_json_response_raises() -> None:
    with patch(
        "findajob.onboarding.interview_runner.urllib.request.urlopen",
        return_value=_FakeResp(b"<html>500 Bad Gateway</html>"),
    ):
        with pytest.raises(InterviewRunnerError) as exc:
            run_turn("sk-or-v1-operator", "SYSTEM", [], "hi")
    assert "non-JSON" in exc.value.user_message


def test_response_missing_choices_raises() -> None:
    with patch(
        "findajob.onboarding.interview_runner.urllib.request.urlopen",
        return_value=_ok_resp({"unexpected": "shape"}),
    ):
        with pytest.raises(InterviewRunnerError) as exc:
            run_turn("sk-or-v1-operator", "SYSTEM", [], "hi")
    assert "unexpected response shape" in exc.value.user_message.lower()


def test_response_with_empty_choices_raises() -> None:
    with patch(
        "findajob.onboarding.interview_runner.urllib.request.urlopen",
        return_value=_ok_resp({"choices": []}),
    ):
        with pytest.raises(InterviewRunnerError):
            run_turn("sk-or-v1-operator", "SYSTEM", [], "hi")


def test_response_missing_message_content_raises() -> None:
    """choices[0] exists but has no 'message' field."""
    body = {"choices": [{"index": 0, "finish_reason": "stop"}]}
    with patch(
        "findajob.onboarding.interview_runner.urllib.request.urlopen",
        return_value=_ok_resp(body),
    ):
        with pytest.raises(InterviewRunnerError) as exc:
            run_turn("sk-or-v1-operator", "SYSTEM", [], "hi")
    assert "Could not parse assistant content" in exc.value.user_message


def test_assistant_content_non_string_raises() -> None:
    """Defensive: some models return content as a list of blocks."""
    body = {"choices": [{"message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}}]}
    with patch(
        "findajob.onboarding.interview_runner.urllib.request.urlopen",
        return_value=_ok_resp(body),
    ):
        with pytest.raises(InterviewRunnerError) as exc:
            run_turn("sk-or-v1-operator", "SYSTEM", [], "hi")
    assert "not a string" in exc.value.user_message


# ── Model pin sanity ────────────────────────────────────────────────────


def test_interview_model_is_sonnet_4_6() -> None:
    """Model pin: see issue #336 'Decisions adopted'."""
    assert INTERVIEW_MODEL == "anthropic/claude-sonnet-4-6"
