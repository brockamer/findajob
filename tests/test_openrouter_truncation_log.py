"""Wrapper-level regression guard for the centralized ``openrouter_truncated``
emission (#737).

Pre-#737 history: the same fix shape (raise max_tokens + emit
``openrouter_truncated``) recurred three times — #639 (fit_analyst), #666
(interview_prep), #678 (company_discoverer). Each fix mirrored the emission
in the caller's own code path because the emission lived in
``findajob.llm.role_runner.run_role`` (added by #666) and direct callers of
``findajob.llm.openrouter.complete`` got nothing. #737 centralizes the
emission in the wrapper so every direct ``complete()`` caller — current and
future — inherits the diagnostic uniformly.

These tests drive ``complete()`` end-to-end by mocking the urllib HTTP
boundary, not by mocking ``complete()`` itself. That choice matters: a test
that mocks ``complete()`` to return a ``CompletionResult(finish_reason='length')``
would silently bypass the very emission this guard exists to catch. Drive
the boundary the wrapper actually crosses.

Event schema (consistent across both branches):

- ``role`` (str) — the role= kwarg passed to ``complete()``.
- ``job_id`` (str | None) — the optional job_id= kwarg.
- ``completion_tokens`` (int) — ``response.usage.completion_tokens``.
  Always int, never null (pre-#737 the null-content branch reported null).
- ``content_chars`` (int) — length of the returned text. ``0`` on the
  null-content branch — this is the discriminator between partial truncation
  (``> 0``) and hard truncation (``== 0``).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from findajob.llm.openrouter import OpenRouterError, complete


@pytest.fixture
def roles_dir(tmp_path: Path) -> Path:
    """Minimal role-file directory the wrapper can read frontmatter from."""
    role_file = tmp_path / "test_role.md"
    role_file.write_text(
        "---\nmodel: anthropic/claude-sonnet-4-6\nmax_tokens: 4096\n---\nsystem prompt body\n",
        encoding="utf-8",
    )
    return tmp_path


def _make_response(*, text: str | None, finish_reason: str | None, completion_tokens: int = 4096) -> bytes:
    """Build a raw OpenRouter chat-completion response body."""
    return json.dumps(
        {
            "id": "gen-test-trunc",
            "choices": [
                {
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": 500,
                "completion_tokens": completion_tokens,
                "cost": 0.04,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        }
    ).encode("utf-8")


class _Resp:
    """Minimal urlopen-context-manager double."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        pass

    def read(self) -> bytes:
        return self._body


def _captured_events(events: list[tuple[str, dict]], name: str) -> list[dict]:
    return [kw for e, kw in events if e == name]


def test_emits_on_partial_truncation_success_path(roles_dir: Path) -> None:
    """finish_reason='length' with non-null content — partial truncation.

    The wrapper returns a CompletionResult with the truncated text, and the
    event fires BEFORE the return so a downstream consumer that fails on the
    truncated text still leaves the diagnostic in pipeline.jsonl.
    """
    body = _make_response(text="partial output", finish_reason="length")
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kw: object) -> None:
        events.append((event, kw))

    with (
        patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=_Resp(body)),
        patch("findajob.llm.openrouter.log_event", side_effect=_capture),
        patch("findajob.llm.openrouter._check_call_gate", return_value=None),
    ):
        result = complete(
            role="test_role",
            prompt="prompt",
            api_key="sk-test",
            roles_dir=roles_dir,
            job_id="job-partial",
        )

    assert result.text == "partial output"
    assert result.finish_reason == "length"

    truncated = _captured_events(events, "openrouter_truncated")
    assert len(truncated) == 1
    payload = truncated[0]
    assert payload["role"] == "test_role"
    assert payload["job_id"] == "job-partial"
    assert payload["completion_tokens"] == 4096
    assert payload["content_chars"] == len("partial output")
    assert payload["content_chars"] > 0  # the discriminator from the null-content branch


def test_emits_on_null_content_plus_length_failure_path(roles_dir: Path) -> None:
    """content=null + finish_reason='length' — hard truncation.

    The wrapper raises OpenRouterError. Emission fires BEFORE the raise so the
    diagnostic survives even if the caller swallows the exception (e.g.
    run_role returns "" on OpenRouterError).
    """
    body = _make_response(text=None, finish_reason="length")
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kw: object) -> None:
        events.append((event, kw))

    with (
        patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=_Resp(body)),
        patch("findajob.llm.openrouter.log_event", side_effect=_capture),
        patch("findajob.llm.openrouter._check_call_gate", return_value=None),
        pytest.raises(OpenRouterError) as excinfo,
    ):
        complete(
            role="test_role",
            prompt="prompt",
            api_key="sk-test",
            roles_dir=roles_dir,
            job_id="job-null",
        )

    assert excinfo.value.finish_reason == "length"
    assert excinfo.value.completion_tokens == 4096

    truncated = _captured_events(events, "openrouter_truncated")
    assert len(truncated) == 1
    payload = truncated[0]
    assert payload["role"] == "test_role"
    assert payload["job_id"] == "job-null"
    # Critical schema change vs pre-#737: completion_tokens is int here, not null.
    # Discoverer's pre-#737 null-content branch reported null; that's gone now.
    assert payload["completion_tokens"] == 4096
    assert payload["content_chars"] == 0  # the discriminator from the partial-truncation branch


def test_does_not_emit_on_stop(roles_dir: Path) -> None:
    """finish_reason='stop' — normal completion. No emission."""
    body = _make_response(text="full output", finish_reason="stop")
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kw: object) -> None:
        events.append((event, kw))

    with (
        patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=_Resp(body)),
        patch("findajob.llm.openrouter.log_event", side_effect=_capture),
        patch("findajob.llm.openrouter._check_call_gate", return_value=None),
    ):
        result = complete(role="test_role", prompt="prompt", api_key="sk-test", roles_dir=roles_dir)

    assert result.text == "full output"
    assert result.finish_reason == "stop"
    assert _captured_events(events, "openrouter_truncated") == []


def test_does_not_emit_on_missing_finish_reason(roles_dir: Path) -> None:
    """Backwards-compat: older OpenRouter responses without finish_reason
    must not synthesize a truncation event."""
    body = _make_response(text="full output", finish_reason=None)
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kw: object) -> None:
        events.append((event, kw))

    with (
        patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=_Resp(body)),
        patch("findajob.llm.openrouter.log_event", side_effect=_capture),
        patch("findajob.llm.openrouter._check_call_gate", return_value=None),
    ):
        complete(role="test_role", prompt="prompt", api_key="sk-test", roles_dir=roles_dir)

    assert _captured_events(events, "openrouter_truncated") == []


def test_job_id_defaults_to_none_when_omitted(roles_dir: Path) -> None:
    """Direct callers without job context (e.g. discoverer) omit job_id;
    the event payload must carry None, not raise or substitute a placeholder.
    """
    body = _make_response(text="partial", finish_reason="length")
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kw: object) -> None:
        events.append((event, kw))

    with (
        patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=_Resp(body)),
        patch("findajob.llm.openrouter.log_event", side_effect=_capture),
        patch("findajob.llm.openrouter._check_call_gate", return_value=None),
    ):
        # No job_id= kwarg passed — verifies the default.
        complete(role="test_role", prompt="prompt", api_key="sk-test", roles_dir=roles_dir)

    truncated = _captured_events(events, "openrouter_truncated")
    assert len(truncated) == 1
    assert truncated[0]["job_id"] is None
