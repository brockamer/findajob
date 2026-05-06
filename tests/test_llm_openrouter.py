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
    return {
        "id": generation_id,
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_tokens,
            "cost": cost,
        },
    }


def test_complete_happy_path_returns_completion_result(monkeypatch, tmp_path):
    """complete() returns CompletionResult with text + cost from usage.cost."""
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "test_role.md").write_text(
        "---\nmodel: openrouter:anthropic/claude-sonnet-4-6\n---\nSYSTEM\n"
    )
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
        "---\n"
        "model: openrouter:deepseek/deepseek-v3.2\n"
        "temperature: 0.1\n"
        "max_tokens: 2048\n"
        "---\n"
        "SYSTEM PROMPT BODY\n"
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
    (roles / "scorer.md").write_text(
        "---\nmodel: openrouter:foo/bar\nmax_tokens: 1024\n---\nbody\n"
    )
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
