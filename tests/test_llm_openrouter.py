"""Tests for findajob.llm.openrouter (#470).

Stub urllib.request.urlopen at the module level — no real network calls.
Mirrors the test pattern in tests/test_onboarding_interview_runner.py.
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

from findajob.llm.openrouter import (
    CompletionResult,
    OpenRouterError,
    complete,
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


def _success_body(
    text: str = "ok",
    *,
    prompt_tokens: int = 100,
    completion_tokens: int = 20,
    cached_tokens: int = 0,
    cost: float = 0.001234,
    generation_id: str = "gen-abc-123",
) -> dict:
    """Build a fixture matching OpenRouter's real response shape.

    cached_tokens lives under usage.prompt_tokens_details (same as Anthropic
    native), not at usage top level — earlier versions of this fixture had it
    top-level, which mirrored a parser bug; corrected after #470 review.
    """
    return {
        "id": generation_id,
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "prompt_tokens_details": {"cached_tokens": cached_tokens},
            "cost": cost,
        },
    }


def test_complete_happy_path_returns_completion_result(monkeypatch, tmp_path):
    """complete() returns CompletionResult with text + cost from usage.cost."""
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "test_role.md").write_text("---\nmodel: openrouter:anthropic/claude-sonnet-4-6\n---\nSYSTEM\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")

    with patch(
        "findajob.llm.openrouter.urllib.request.urlopen",
        return_value=_ok_resp(_success_body(text="hi")),
    ):
        result = complete(
            role="test_role",
            prompt="say hi",
            roles_dir=roles,
        )

    assert isinstance(result, CompletionResult)
    assert result.text == "hi"
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 20
    assert result.cached_tokens == 0
    assert result.cost_usd == pytest.approx(0.001234)
    assert result.generation_id == "gen-abc-123"


def test_role_frontmatter_strips_openrouter_prefix(monkeypatch, tmp_path):
    """model: openrouter:foo/bar -> request payload uses 'foo/bar'."""
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "scorer.md").write_text(
        "---\nmodel: openrouter:deepseek/deepseek-v3.2\ntemperature: 0.1\nmax_tokens: 2048\n---\nSYSTEM PROMPT BODY\n"
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    captured: dict = {}

    def _capture(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _ok_resp(_success_body())

    with patch("findajob.llm.openrouter.urllib.request.urlopen", side_effect=_capture):
        complete(role="scorer", prompt="hi", roles_dir=roles)

    assert captured["body"]["model"] == "deepseek/deepseek-v3.2"
    assert captured["body"]["temperature"] == 0.1
    assert captured["body"]["max_tokens"] == 2048
    sys_msg = captured["body"]["messages"][0]
    assert sys_msg["content"] == "SYSTEM PROMPT BODY"


def test_role_frontmatter_overrides_via_kwargs(monkeypatch, tmp_path):
    """**overrides win over frontmatter."""
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "scorer.md").write_text("---\nmodel: openrouter:foo/bar\nmax_tokens: 1024\n---\nbody\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    captured: dict = {}

    def _capture(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _ok_resp(_success_body())

    with patch("findajob.llm.openrouter.urllib.request.urlopen", side_effect=_capture):
        complete(
            role="scorer",
            prompt="hi",
            roles_dir=roles,
            max_tokens=512,
            model="zzz/qq",
        )

    assert captured["body"]["model"] == "zzz/qq"
    assert captured["body"]["max_tokens"] == 512


def test_missing_model_in_frontmatter_raises_config(monkeypatch, tmp_path):
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "broken.md").write_text("---\ntemperature: 0.1\n---\nbody\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    with pytest.raises(OpenRouterError) as exc:
        complete(role="broken", prompt="hi", roles_dir=roles)
    assert exc.value.kind == "config"


# ── Cache-control plumbing (#470 AC #1, #5) ─────────────────────────────


def test_cached_prefix_emits_two_block_user_message(monkeypatch, tmp_path):
    """cached_prefix=<text> -> user message is [cached_block, prompt_block]."""
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:anthropic/claude-opus-4-7\n---\nSYS\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    captured: dict = {}

    def _capture(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _ok_resp(_success_body())

    with patch("findajob.llm.openrouter.urllib.request.urlopen", side_effect=_capture):
        complete(
            role="r",
            prompt="job-specific tail",
            cached_prefix="CANDIDATE PROFILE: ...",
            roles_dir=roles,
        )

    msgs = captured["body"]["messages"]
    assert msgs[0] == {"role": "system", "content": "SYS"}
    user_msg = msgs[1]
    assert user_msg["role"] == "user"
    assert isinstance(user_msg["content"], list)
    assert user_msg["content"][0] == {
        "type": "text",
        "text": "CANDIDATE PROFILE: ...",
        "cache_control": {"type": "ephemeral"},
    }
    assert user_msg["content"][1] == {"type": "text", "text": "job-specific tail"}


def test_cache_system_attaches_breakpoint_to_system(monkeypatch, tmp_path):
    """cache_system=True -> system message wrapped with cache_control."""
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:anthropic/claude-sonnet-4-6\n---\nSYS\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    captured: dict = {}

    def _capture(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _ok_resp(_success_body())

    with patch("findajob.llm.openrouter.urllib.request.urlopen", side_effect=_capture):
        complete(role="r", prompt="hi", cache_system=True, roles_dir=roles)

    sys_msg = captured["body"]["messages"][0]
    assert sys_msg["role"] == "system"
    assert isinstance(sys_msg["content"], list)
    assert sys_msg["content"][0] == {
        "type": "text",
        "text": "SYS",
        "cache_control": {"type": "ephemeral"},
    }


def test_both_axes_emit_two_breakpoints(monkeypatch, tmp_path):
    """cache_system=True + cached_prefix=<text> -> two breakpoints in payload."""
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:anthropic/claude-opus-4-7\n---\nSYSTEM PROMPT\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    captured: dict = {}

    def _capture(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _ok_resp(_success_body())

    with patch("findajob.llm.openrouter.urllib.request.urlopen", side_effect=_capture):
        complete(
            role="r",
            prompt="tail",
            cached_prefix="SHARED PREFIX",
            cache_system=True,
            roles_dir=roles,
        )

    msgs = captured["body"]["messages"]
    assert msgs[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert msgs[1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert msgs[1]["content"][1] == {"type": "text", "text": "tail"}


def test_default_no_cache_emits_plain_strings(monkeypatch, tmp_path):
    """No cache flags -> system + user messages are plain strings."""
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:foo/bar\n---\nSYS\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    captured: dict = {}

    def _capture(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _ok_resp(_success_body())

    with patch("findajob.llm.openrouter.urllib.request.urlopen", side_effect=_capture):
        complete(role="r", prompt="hi", roles_dir=roles)

    assert captured["body"]["messages"][0] == {"role": "system", "content": "SYS"}
    assert captured["body"]["messages"][1] == {"role": "user", "content": "hi"}


# ── Provider pinning ────────────────────────────────────────────────────


def test_pin_provider_adds_provider_only_block(monkeypatch, tmp_path):
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:anthropic/claude-sonnet-4-6\n---\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    captured: dict = {}

    def _capture(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _ok_resp(_success_body())

    with patch("findajob.llm.openrouter.urllib.request.urlopen", side_effect=_capture):
        complete(role="r", prompt="hi", pin_provider="Anthropic", roles_dir=roles)

    assert captured["body"]["provider"] == {"only": ["Anthropic"]}


def test_no_pin_provider_omits_provider_block(monkeypatch, tmp_path):
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:foo/bar\n---\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    captured: dict = {}

    def _capture(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _ok_resp(_success_body())

    with patch("findajob.llm.openrouter.urllib.request.urlopen", side_effect=_capture):
        complete(role="r", prompt="hi", roles_dir=roles)

    assert "provider" not in captured["body"]


# ── Error taxonomy ──────────────────────────────────────────────────────


def _http_error(code: int, body: str = "") -> HTTPError:
    return HTTPError(
        url="https://openrouter.ai/api/v1/chat/completions",
        code=code,
        msg="error",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(body.encode("utf-8")),
    )


@pytest.mark.parametrize(
    "code,kind",
    [
        (401, "auth"),
        (402, "payment"),
        (429, "rate_limit"),
        (500, "upstream"),
        (502, "upstream"),
        (503, "upstream"),
        (418, "upstream"),
    ],
)
def test_http_error_maps_to_kind(monkeypatch, tmp_path, code, kind):
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:foo/bar\n---\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr("findajob.llm.openrouter.time.sleep", lambda _: None)
    with patch(
        "findajob.llm.openrouter.urllib.request.urlopen",
        side_effect=_http_error(code, "{}"),
    ):
        with pytest.raises(OpenRouterError) as exc:
            complete(role="r", prompt="hi", roles_dir=roles)
    assert exc.value.kind == kind
    assert exc.value.status_code == code


def test_url_error_maps_to_network(monkeypatch, tmp_path):
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:foo/bar\n---\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr("findajob.llm.openrouter.time.sleep", lambda _: None)
    with patch(
        "findajob.llm.openrouter.urllib.request.urlopen",
        side_effect=URLError("DNS failure"),
    ):
        with pytest.raises(OpenRouterError) as exc:
            complete(role="r", prompt="hi", roles_dir=roles)
    assert exc.value.kind == "network"


def test_non_json_response_maps_to_malformed(monkeypatch, tmp_path):
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:foo/bar\n---\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    with patch(
        "findajob.llm.openrouter.urllib.request.urlopen",
        return_value=_FakeResp(b"<html>nope</html>"),
    ):
        with pytest.raises(OpenRouterError) as exc:
            complete(role="r", prompt="hi", roles_dir=roles)
    assert exc.value.kind == "malformed"


def test_missing_choices_maps_to_malformed(monkeypatch, tmp_path):
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:foo/bar\n---\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    with patch(
        "findajob.llm.openrouter.urllib.request.urlopen",
        return_value=_ok_resp({"id": "x", "usage": {}}),
    ):
        with pytest.raises(OpenRouterError) as exc:
            complete(role="r", prompt="hi", roles_dir=roles)
    assert exc.value.kind == "malformed"


def test_empty_api_key_maps_to_config(monkeypatch, tmp_path):
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:foo/bar\n---\n")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(OpenRouterError) as exc:
        complete(role="r", prompt="hi", roles_dir=roles)
    assert exc.value.kind == "config"


# ── Retry layer ─────────────────────────────────────────────────────────


def test_retry_succeeds_on_second_attempt_after_429(monkeypatch, tmp_path):
    """429 -> wait -> retry -> success."""
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:foo/bar\n---\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr("findajob.llm.openrouter.time.sleep", lambda _: None)

    calls: list[int] = []

    def _attempt(req, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(429, "{}")
        return _ok_resp(_success_body(text="recovered"))

    with patch("findajob.llm.openrouter.urllib.request.urlopen", side_effect=_attempt):
        result = complete(role="r", prompt="hi", roles_dir=roles)

    assert result.text == "recovered"
    assert len(calls) == 2


def test_retry_succeeds_on_second_attempt_after_5xx(monkeypatch, tmp_path):
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:foo/bar\n---\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr("findajob.llm.openrouter.time.sleep", lambda _: None)

    calls: list[int] = []

    def _attempt(req, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(503, "{}")
        return _ok_resp(_success_body())

    with patch("findajob.llm.openrouter.urllib.request.urlopen", side_effect=_attempt):
        complete(role="r", prompt="hi", roles_dir=roles)
    assert len(calls) == 2


def test_retry_does_not_retry_auth(monkeypatch, tmp_path):
    """401 fails fast — retrying would exhaust quota for no reason."""
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:foo/bar\n---\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr("findajob.llm.openrouter.time.sleep", lambda _: None)

    calls: list[int] = []

    def _attempt(req, timeout=None):
        calls.append(1)
        raise _http_error(401, "{}")

    with patch("findajob.llm.openrouter.urllib.request.urlopen", side_effect=_attempt):
        with pytest.raises(OpenRouterError):
            complete(role="r", prompt="hi", roles_dir=roles)
    assert len(calls) == 1


def test_retry_exhausted_after_max_attempts(monkeypatch, tmp_path):
    """Persistent 429 -> 3 attempts then raise."""
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:foo/bar\n---\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr("findajob.llm.openrouter.time.sleep", lambda _: None)

    calls: list[int] = []

    def _attempt(req, timeout=None):
        calls.append(1)
        raise _http_error(429, "{}")

    with patch("findajob.llm.openrouter.urllib.request.urlopen", side_effect=_attempt):
        with pytest.raises(OpenRouterError) as exc:
            complete(role="r", prompt="hi", roles_dir=roles)
    assert exc.value.kind == "rate_limit"
    assert len(calls) == 3


# ── Cost extraction edge cases + history passthrough ────────────────────


def test_response_without_usage_returns_zero_cost(monkeypatch, tmp_path):
    """No usage dict in response -> cost_usd=0.0, tokens=0."""
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:foo/bar\n---\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    body = {"id": "g", "choices": [{"message": {"role": "assistant", "content": "x"}}]}
    with patch(
        "findajob.llm.openrouter.urllib.request.urlopen",
        return_value=_ok_resp(body),
    ):
        result = complete(role="r", prompt="hi", roles_dir=roles)
    assert result.cost_usd == 0.0
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0
    assert result.cached_tokens == 0


def test_response_with_cached_tokens_populates_field(monkeypatch, tmp_path):
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:foo/bar\n---\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    body = _success_body(cached_tokens=8500, prompt_tokens=10000, cost=0.0042)
    with patch(
        "findajob.llm.openrouter.urllib.request.urlopen",
        return_value=_ok_resp(body),
    ):
        result = complete(role="r", prompt="hi", roles_dir=roles)
    assert result.cached_tokens == 8500
    assert result.prompt_tokens == 10000
    assert result.cost_usd == pytest.approx(0.0042)


def test_history_passed_through_to_payload(monkeypatch, tmp_path):
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:foo/bar\n---\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    captured: dict = {}

    def _capture(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _ok_resp(_success_body())

    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ok"},
    ]
    with patch("findajob.llm.openrouter.urllib.request.urlopen", side_effect=_capture):
        complete(role="r", prompt="next", history=history, roles_dir=roles)

    msgs = captured["body"]["messages"]
    assert msgs[1] == {"role": "user", "content": "first"}
    assert msgs[2] == {"role": "assistant", "content": "ok"}
    assert msgs[3] == {"role": "user", "content": "next"}


# ── #632: finish_reason exposed on CompletionResult ─────────────────────


def test_parse_response_extracts_finish_reason_stop(monkeypatch, tmp_path):
    """Normal completion: ``finish_reason='stop'`` on CompletionResult.

    Pre-#632 this field was dropped at parse time. Onboarding interview
    needs it to detect mid-block truncation (max_tokens cap) and surface
    a clear error rather than emit a malformed block that fails to parse
    downstream.
    """
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:anthropic/claude-sonnet-4-6\n---\nSYSTEM\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")

    body = _success_body(text="ok")
    body["choices"][0]["finish_reason"] = "stop"

    with patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=_ok_resp(body)):
        result = complete(role="r", prompt="hi", roles_dir=roles)
    assert result.finish_reason == "stop"


def test_parse_response_extracts_finish_reason_length(monkeypatch, tmp_path):
    """Cap hit: ``finish_reason='length'`` propagates through to the caller.

    This is the case interview_runner watches for so it can raise an
    ``InterviewRunnerError(kind='length', ...)`` instead of returning a
    truncated emit that breaks downstream block-capture.
    """
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:anthropic/claude-sonnet-4-6\n---\nSYSTEM\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")

    body = _success_body(text="truncated mid-block...")
    body["choices"][0]["finish_reason"] = "length"

    with patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=_ok_resp(body)):
        result = complete(role="r", prompt="hi", roles_dir=roles)
    assert result.finish_reason == "length"


def test_parse_response_missing_finish_reason_is_none(monkeypatch, tmp_path):
    """Defensive: providers that omit ``finish_reason`` yield ``None`` (not KeyError).

    OpenRouter normalizes most providers to populate it, but the parser
    must not crash on the rare miss.
    """
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:anthropic/claude-sonnet-4-6\n---\nSYSTEM\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")

    body = _success_body(text="ok")
    # explicitly do NOT set choices[0].finish_reason
    with patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=_ok_resp(body)):
        result = complete(role="r", prompt="hi", roles_dir=roles)
    assert result.finish_reason is None


def test_openrouter_error_carries_finish_reason_on_null_content(monkeypatch, tmp_path):
    """When content is null and finish_reason=length, OpenRouterError must
    surface finish_reason as a structured attribute (#678).

    Pre-#678, callers had to grep ``str(e)`` for ``finish_reason=length`` to
    detect a max_tokens cap from the failure path — brittle to message-format
    drift. With the attribute, ``e.finish_reason == "length"`` is a
    contract callers can rely on.
    """
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "r.md").write_text("---\nmodel: openrouter:anthropic/claude-sonnet-4-6\n---\nSYSTEM\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")

    body = _success_body(text="placeholder")
    # Real production failure shape: content is null + finish_reason="length".
    body["choices"][0]["message"]["content"] = None
    body["choices"][0]["finish_reason"] = "length"

    with patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=_ok_resp(body)):
        with pytest.raises(OpenRouterError) as exc_info:
            complete(role="r", prompt="hi", roles_dir=roles)
    assert exc_info.value.kind == "malformed"
    assert exc_info.value.finish_reason == "length"
