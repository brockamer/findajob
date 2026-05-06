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
