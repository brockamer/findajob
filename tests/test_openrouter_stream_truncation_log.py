"""Streaming-path regression guard for the centralized ``openrouter_truncated``
emission (#798 — follow-up to #737).

The direct ``complete()`` path emits ``openrouter_truncated`` in two branches:
partial truncation (success-with-length) and null-content (failure-with-length).
See ``tests/test_openrouter_truncation_log.py``.

The streaming ``complete_stream()`` path has only one length-finish shape: SSE
delta accumulation always produces partial content before the terminal chunk
carries ``finish_reason='length'``. The route layer
(``findajob.web.routes.onboarding_interview._stream_turn``) translates the
``StreamFinish`` with length-finish into a user-facing error; the emission
itself happens inside the generator BEFORE the terminal yield so the
diagnostic survives any caller-side translation.

These tests drive ``complete_stream()`` end-to-end by stubbing
``urllib.request.urlopen`` at the module level, mirroring the fixture pattern
in ``tests/test_openrouter_stream.py``.

Event schema (same as the wrapper-level emission for ``complete()``):

- ``role`` — the role= kwarg passed to ``complete_stream()``.
- ``job_id`` — always ``None`` today. ``complete_stream()`` does not currently
  take a job_id kwarg (no streaming consumer has job context).
- ``completion_tokens`` — from the terminal chunk's ``usage.completion_tokens``.
- ``content_chars`` — ``len("".join(accumulated))``. Always ``> 0`` for the
  streaming path (delta-by-delta accumulation precludes the null-content shape).
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

from findajob.llm.openrouter import complete_stream

# ---------------------------------------------------------------------------
# Fixture helpers — mirrored from tests/test_openrouter_stream.py
# ---------------------------------------------------------------------------


class _FakeStreamResp:
    """Fake urllib response that yields SSE lines from raw bytes."""

    def __init__(self, raw: bytes) -> None:
        self._stream = io.BytesIO(raw)
        self.close_called = False

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        line = self._stream.readline()
        if not line:
            raise StopIteration
        return line

    def close(self) -> None:
        self.close_called = True


def _make_sse_bytes(*data_payloads: str) -> bytes:
    """Build an SSE byte stream terminated with a ``[DONE]`` sentinel."""
    lines: list[bytes] = []
    for payload in data_payloads:
        lines.append(f"data: {payload}\n\n".encode())
    lines.append(b"data: [DONE]\n\n")
    return b"".join(lines)


def _delta_chunk(
    content: str,
    *,
    gen_id: str = "gen-test-trunc",
    finish_reason: str | None = None,
    usage: dict | None = None,
) -> str:
    """Build an SSE data-chunk JSON string."""
    chunk: dict = {
        "id": gen_id,
        "object": "chat.completion.chunk",
        "choices": [
            {
                "index": 0,
                "delta": {"content": content, "role": "assistant"},
                "finish_reason": finish_reason,
                "native_finish_reason": None,
            }
        ],
    }
    if usage is not None:
        chunk["usage"] = usage
    return json.dumps(chunk)


def _setup_role(tmp_path: Path, model: str = "openrouter:anthropic/claude-haiku-4-5") -> Path:
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "test_role.md").write_text(f"---\nmodel: {model}\n---\nYou are a test assistant.\n")
    return roles


def _captured_events(events: list[tuple[str, dict]], name: str) -> list[dict]:
    return [kw for e, kw in events if e == name]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_emits_on_length_finish_partial_stream(monkeypatch, tmp_path: Path) -> None:
    """finish_reason='length' with accumulated partial content — emission fires.

    The emission must land BEFORE the generator yields the terminal
    ``StreamFinish`` chunk so a downstream consumer that translates the
    length-finish into an error (the onboarding-interview route does this)
    still leaves the diagnostic in pipeline.jsonl.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    roles = _setup_role(tmp_path)
    payloads = [
        _delta_chunk("partial output "),
        _delta_chunk("before the cap"),
        _delta_chunk(
            "",
            finish_reason="length",
            usage={
                "prompt_tokens": 12,
                "completion_tokens": 4096,
                "prompt_tokens_details": {"cached_tokens": 0},
                "cost": 0.002,
            },
        ),
    ]
    raw = _make_sse_bytes(*payloads)
    fake_resp = _FakeStreamResp(raw)
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kw: object) -> None:
        events.append((event, kw))

    with (
        patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=fake_resp),
        patch("findajob.llm.openrouter.log_event", side_effect=_capture),
        patch("findajob.llm.openrouter._check_call_gate", return_value=None),
    ):
        chunks = list(complete_stream(role="test_role", prompt="test", roles_dir=roles))

    # The generator still yields the terminal StreamFinish — emission does not
    # replace or suppress it.
    assert len(chunks) == 1
    finish = chunks[0]
    assert finish["type"] == "finish"
    assert finish["finish_reason"] == "length"
    assert finish["text"] == "partial output before the cap"

    truncated = _captured_events(events, "openrouter_truncated")
    assert len(truncated) == 1
    payload = truncated[0]
    assert payload["role"] == "test_role"
    assert payload["job_id"] is None
    assert payload["completion_tokens"] == 4096
    assert payload["content_chars"] == len("partial output before the cap")
    assert payload["content_chars"] > 0  # streaming-path discriminator


def test_does_not_emit_on_stop(monkeypatch, tmp_path: Path) -> None:
    """finish_reason='stop' — normal completion. No truncation event."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    roles = _setup_role(tmp_path)
    payloads = [
        _delta_chunk("full response"),
        _delta_chunk(
            "",
            finish_reason="stop",
            usage={
                "prompt_tokens": 12,
                "completion_tokens": 200,
                "prompt_tokens_details": {"cached_tokens": 0},
                "cost": 0.001,
            },
        ),
    ]
    raw = _make_sse_bytes(*payloads)
    fake_resp = _FakeStreamResp(raw)
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kw: object) -> None:
        events.append((event, kw))

    with (
        patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=fake_resp),
        patch("findajob.llm.openrouter.log_event", side_effect=_capture),
        patch("findajob.llm.openrouter._check_call_gate", return_value=None),
    ):
        chunks = list(complete_stream(role="test_role", prompt="test", roles_dir=roles))

    assert chunks[0]["finish_reason"] == "stop"
    assert _captured_events(events, "openrouter_truncated") == []


def test_does_not_emit_on_missing_finish_reason(monkeypatch, tmp_path: Path) -> None:
    """No terminal finish_reason at all — must not synthesize a truncation event."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    roles = _setup_role(tmp_path)
    payloads = [
        _delta_chunk("some content"),
        # Terminal chunk carries usage but no finish_reason.
        _delta_chunk(
            "",
            usage={
                "prompt_tokens": 12,
                "completion_tokens": 50,
                "prompt_tokens_details": {"cached_tokens": 0},
                "cost": 0.0005,
            },
        ),
    ]
    raw = _make_sse_bytes(*payloads)
    fake_resp = _FakeStreamResp(raw)
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kw: object) -> None:
        events.append((event, kw))

    with (
        patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=fake_resp),
        patch("findajob.llm.openrouter.log_event", side_effect=_capture),
        patch("findajob.llm.openrouter._check_call_gate", return_value=None),
    ):
        chunks = list(complete_stream(role="test_role", prompt="test", roles_dir=roles))

    assert chunks[0]["finish_reason"] is None
    assert _captured_events(events, "openrouter_truncated") == []


def test_does_not_emit_on_mid_stream_network_error(monkeypatch, tmp_path: Path) -> None:
    """Mid-stream failure yields StreamError(kind='network'); no truncation event.

    The truncation emission lives in the success-finish branch only. A network
    drop during streaming should not synthesize a length diagnostic.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    roles = _setup_role(tmp_path)

    class _ExplodingResp:
        """Yields one delta, then raises ConnectionResetError on the next read."""

        def __init__(self) -> None:
            self._lines = iter(
                [
                    f"data: {_delta_chunk('partial')}\n\n".encode(),
                ]
            )
            self.close_called = False

        def __iter__(self):
            return self

        def __next__(self) -> bytes:
            try:
                return next(self._lines)
            except StopIteration:
                raise ConnectionResetError("simulated mid-stream drop") from None

        def close(self) -> None:
            self.close_called = True

    fake_resp = _ExplodingResp()
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kw: object) -> None:
        events.append((event, kw))

    with (
        patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=fake_resp),
        patch("findajob.llm.openrouter.log_event", side_effect=_capture),
        patch("findajob.llm.openrouter._check_call_gate", return_value=None),
    ):
        chunks = list(complete_stream(role="test_role", prompt="test", roles_dir=roles))

    # The mid-stream failure should yield a StreamError, not a StreamFinish.
    assert any(c.get("type") == "error" for c in chunks)
    assert not any(c.get("type") == "finish" for c in chunks)
    assert _captured_events(events, "openrouter_truncated") == []
