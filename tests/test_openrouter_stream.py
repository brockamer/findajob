"""Tests for complete_stream() in findajob.llm.openrouter (#740).

10 required test cases — see CLAUDE.md / issue #740 brief.
Stubs urllib.request.urlopen at the module level — no real network calls.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from findajob.llm.openrouter import (
    LLMSpendCeilingExceeded,
    complete_stream,
)

# ---------------------------------------------------------------------------
# Fixture path
# ---------------------------------------------------------------------------

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "llm" / "openrouter_sse_haiku_streaming.txt"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeStreamResp:
    """Fake urllib response that yields SSE lines from raw bytes.

    Supports iteration (line-by-line, like http.client.HTTPResponse) and
    records close() calls so tests can assert cleanup.
    """

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


def _make_sse_bytes(*data_payloads: str, extra_after_done: bytes = b"") -> bytes:
    """Build a minimal SSE byte stream from JSON payload strings.

    Each string is wrapped in ``data: <payload>\\n\\n``. A ``data: [DONE]\\n\\n``
    sentinel is appended at the end, followed by any ``extra_after_done`` bytes.
    """
    lines: list[bytes] = []
    for payload in data_payloads:
        lines.append(f"data: {payload}\n\n".encode())
    lines.append(b"data: [DONE]\n\n")
    if extra_after_done:
        lines.append(extra_after_done)
    return b"".join(lines)


def _delta_chunk(
    content: str,
    *,
    gen_id: str = "gen-test-001",
    finish_reason: str | None = None,
    usage: dict | None = None,
) -> str:
    """Build a minimal SSE data-chunk JSON string."""
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
    """Create a minimal roles directory for tests."""
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "test_role.md").write_text(f"---\nmodel: {model}\n---\nYou are a test assistant.\n")
    return roles


# ---------------------------------------------------------------------------
# Test 1: Happy path against real fixture
# ---------------------------------------------------------------------------


def test_happy_path_real_fixture(monkeypatch, tmp_path):
    """Generator yields one finish event with correct token counts from the real fixture."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    roles = _setup_role(tmp_path)
    raw = _FIXTURE_PATH.read_bytes()
    fake_resp = _FakeStreamResp(raw)

    with patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=fake_resp):
        chunks = list(complete_stream(role="test_role", prompt="test", roles_dir=roles))

    # Only finish event (no captured events — the fixture has no FILE markers).
    assert len(chunks) == 1
    finish = chunks[0]
    assert finish["type"] == "finish"
    assert isinstance(finish["text"], str)
    assert len(finish["text"]) > 0
    assert finish["finish_reason"] == "stop"
    usage = finish["usage"]
    assert abs(usage["cost_usd"] - 0.001466) < 1e-6
    assert usage["prompt_tokens"] == 31
    assert usage["completion_tokens"] == 287
    assert usage["cached_tokens"] == 0
    assert finish["generation_id"] == "gen-1779204529-Dk0tDXm4Zg8b2fbXArEp"


# ---------------------------------------------------------------------------
# Test 2: Marker detection with two voice_a.md + one voice_b.md
# ---------------------------------------------------------------------------


def test_marker_detection_yields_captured_events(monkeypatch, tmp_path):
    """Generator yields captured events for each <<<END FILE:...>>> marker in order."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    roles = _setup_role(tmp_path)

    # Synthesize fixture with two voice_a.md blocks and one voice_b.md block.
    payloads = [
        _delta_chunk("<<<FILE: voice_a.md>>>"),
        _delta_chunk("content of voice_a"),
        _delta_chunk("<<<END FILE: voice_a.md>>>"),
        _delta_chunk("some more text"),
        _delta_chunk("<<<FILE: voice_a.md>>>"),
        _delta_chunk("second voice_a block"),
        _delta_chunk("<<<END FILE: voice_a.md>>>"),
        _delta_chunk("between blocks"),
        _delta_chunk("<<<FILE: voice_b.md>>>"),
        _delta_chunk("content of voice_b"),
        _delta_chunk("<<<END FILE: voice_b.md>>>"),
        _delta_chunk(
            "",
            finish_reason="stop",
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 50,
                "prompt_tokens_details": {"cached_tokens": 0},
                "cost": 0.0005,
            },
        ),
    ]
    raw = _make_sse_bytes(*payloads)
    fake_resp = _FakeStreamResp(raw)

    with patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=fake_resp):
        chunks = list(complete_stream(role="test_role", prompt="test", roles_dir=roles))

    captured_events = [c for c in chunks if c["type"] == "captured"]
    finish_events = [c for c in chunks if c["type"] == "finish"]

    # Three captured events, one finish event.
    assert len(captured_events) == 3
    assert len(finish_events) == 1

    captured_names = [c["name"] for c in captured_events]
    # Positive assertions — correct names in order.
    assert captured_names[0] == "voice_a.md"
    assert captured_names[1] == "voice_a.md"
    assert captured_names[2] == "voice_b.md"

    # Negative assertions — no lazy-regex overshoot (feedback_negative_test_assertions).
    assert "voice_a" not in captured_names  # must have .md extension
    assert "voice_b" not in captured_names


# ---------------------------------------------------------------------------
# Test 3: Length cap finish_reason preserved
# ---------------------------------------------------------------------------


def test_length_finish_reason_preserved(monkeypatch, tmp_path):
    """Generator emits finish event with finish_reason='length' when capped."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    roles = _setup_role(tmp_path)

    payloads = [
        _delta_chunk("partial output before cap"),
        _delta_chunk(
            "",
            finish_reason="length",
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 4096,
                "prompt_tokens_details": {"cached_tokens": 0},
                "cost": 0.002,
            },
        ),
    ]
    raw = _make_sse_bytes(*payloads)
    fake_resp = _FakeStreamResp(raw)

    with patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=fake_resp):
        chunks = list(complete_stream(role="test_role", prompt="test", roles_dir=roles))

    assert len(chunks) == 1
    finish = chunks[0]
    assert finish["type"] == "finish"
    assert finish["finish_reason"] == "length"


# ---------------------------------------------------------------------------
# Test 4: Pre-first-yield retry — transient then success
# ---------------------------------------------------------------------------


def test_retry_transient_then_success(monkeypatch, tmp_path):
    """Generator retries twice on 503, succeeds on third attempt."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr("time.sleep", lambda _: None)
    roles = _setup_role(tmp_path)

    import urllib.error

    raw = _FIXTURE_PATH.read_bytes()
    fake_resp = _FakeStreamResp(raw)

    call_count = 0

    def _fake_urlopen(req, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise urllib.error.HTTPError(
                url=None,
                code=503,
                msg="Service Unavailable",
                hdrs=None,
                fp=None,  # type: ignore[arg-type]
            )
        return fake_resp

    with patch("findajob.llm.openrouter.urllib.request.urlopen", side_effect=_fake_urlopen):
        chunks = list(complete_stream(role="test_role", prompt="test", roles_dir=roles))

    assert call_count == 3
    assert len(chunks) == 1
    assert chunks[0]["type"] == "finish"
    # No error chunks.
    assert not any(c["type"] == "error" for c in chunks)


# ---------------------------------------------------------------------------
# Test 5: Retry exhaustion pre-first-yield
# ---------------------------------------------------------------------------


def test_retry_exhaustion_yields_single_error_chunk(monkeypatch, tmp_path):
    """After all 3 attempts fail with 503, generator yields one error chunk only."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setattr("time.sleep", lambda _: None)
    roles = _setup_role(tmp_path)

    import urllib.error

    def _always_503(req, timeout=None):
        raise urllib.error.HTTPError(
            url=None,
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=None,  # type: ignore[arg-type]
        )

    with patch("findajob.llm.openrouter.urllib.request.urlopen", side_effect=_always_503):
        chunks = list(complete_stream(role="test_role", prompt="test", roles_dir=roles))

    assert len(chunks) == 1
    err = chunks[0]
    assert err["type"] == "error"
    assert err["kind"] == "upstream"
    # No finish chunk.
    assert not any(c["type"] == "finish" for c in chunks)


# ---------------------------------------------------------------------------
# Test 6: Post-first-yield failure → captured events then error, no retries
# ---------------------------------------------------------------------------


def test_mid_stream_failure_after_captured(monkeypatch, tmp_path):
    """After a captured event is yielded, a ConnectionResetError produces error chunk, no retries."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    roles = _setup_role(tmp_path)

    class _PartialThenCrashResp:
        """Yields a few lines then raises ConnectionResetError."""

        def __init__(self) -> None:
            self.close_called = False
            # Lines: comment, then data with FILE marker, then crash.
            self._lines = [
                b": OPENROUTER PROCESSING\n",
                b"\n",
                (
                    b"data: "
                    + json.dumps(
                        {
                            "id": "gen-crash-test",
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "content": "<<<FILE: out.md>>>body<<<END FILE: out.md>>>",
                                        "role": "assistant",
                                    },
                                    "finish_reason": None,
                                }
                            ],
                        }
                    ).encode()
                    + b"\n"
                ),
                b"\n",
                b"CRASH",  # trigger — never actually parsed
            ]
            self._idx = 0

        def __iter__(self):
            return self

        def __next__(self) -> bytes:
            if self._idx >= len(self._lines):
                raise StopIteration
            line = self._lines[self._idx]
            self._idx += 1
            if line == b"CRASH":
                raise ConnectionResetError("connection reset by peer")
            return line

        def close(self) -> None:
            self.close_called = True

    fake_resp = _PartialThenCrashResp()
    call_count = 0

    def _urlopen(req, timeout=None):
        nonlocal call_count
        call_count += 1
        return fake_resp

    with patch("findajob.llm.openrouter.urllib.request.urlopen", side_effect=_urlopen):
        chunks = list(complete_stream(role="test_role", prompt="test", roles_dir=roles))

    # One captured event followed by one error chunk.
    captured = [c for c in chunks if c["type"] == "captured"]
    errors = [c for c in chunks if c["type"] == "error"]
    finish = [c for c in chunks if c["type"] == "finish"]

    assert len(captured) == 1
    assert captured[0]["name"] == "out.md"
    assert len(errors) == 1
    assert errors[0]["kind"] == "network"
    assert len(finish) == 0
    # No retries after first yield.
    assert call_count == 1


# ---------------------------------------------------------------------------
# Test 7: Spend ceiling raises LLMSpendCeilingExceeded (not yielded)
# ---------------------------------------------------------------------------


def test_spend_ceiling_raises_not_yields(monkeypatch, tmp_path):
    """LLMSpendCeilingExceeded propagates out of the generator — not an error chunk."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    roles = _setup_role(tmp_path)

    def _raise_ceiling():
        raise LLMSpendCeilingExceeded(ceiling_usd=10.0, current_sum_usd=10.5)

    monkeypatch.setattr("findajob.llm.openrouter._check_call_gate", _raise_ceiling)

    gen = complete_stream(role="test_role", prompt="test", roles_dir=roles)
    with pytest.raises(LLMSpendCeilingExceeded):
        next(gen)


# ---------------------------------------------------------------------------
# Test 8: Client disconnect — response.close() called on GeneratorExit
# ---------------------------------------------------------------------------


def test_client_disconnect_closes_response(monkeypatch, tmp_path):
    """gen.close() (GeneratorExit) causes the underlying response to be closed."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    roles = _setup_role(tmp_path)

    # Build a stream with at least one data chunk so we get past the first yield.
    payloads = [
        _delta_chunk("<<<FILE: x.md>>>content<<<END FILE: x.md>>>"),
        _delta_chunk(
            "",
            finish_reason="stop",
            usage={
                "prompt_tokens": 5,
                "completion_tokens": 10,
                "prompt_tokens_details": {"cached_tokens": 0},
                "cost": 0.0001,
            },
        ),
    ]
    raw = _make_sse_bytes(*payloads)
    fake_resp = _FakeStreamResp(raw)

    with patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=fake_resp):
        gen = complete_stream(role="test_role", prompt="test", roles_dir=roles)
        # Iterate until we get the first captured event.
        first_chunk = next(gen)
        assert first_chunk["type"] == "captured"
        # Simulate client disconnect.
        gen.close()

    assert fake_resp.close_called, "response.close() must be called on GeneratorExit"


# ---------------------------------------------------------------------------
# Test 9: SSE comment lines skipped
# ---------------------------------------------------------------------------


def test_sse_comment_lines_skipped(monkeypatch, tmp_path):
    """Lines starting with ':' are skipped; no spurious events emitted."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    roles = _setup_role(tmp_path)

    # Three keepalive comments before any real data.
    keepalives = b": keepalive\n\n: keepalive\n\n: keepalive\n\n"
    real_payload = _delta_chunk(
        "hello",
        finish_reason="stop",
        usage={
            "prompt_tokens": 5,
            "completion_tokens": 3,
            "prompt_tokens_details": {"cached_tokens": 0},
            "cost": 0.0001,
        },
    ).encode()
    raw = keepalives + b"data: " + real_payload + b"\n\ndata: [DONE]\n\n"
    fake_resp = _FakeStreamResp(raw)

    with patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=fake_resp):
        chunks = list(complete_stream(role="test_role", prompt="test", roles_dir=roles))

    # No captured events; exactly one finish event; no error events.
    assert len(chunks) == 1
    assert chunks[0]["type"] == "finish"
    assert chunks[0]["text"] == "hello"


# ---------------------------------------------------------------------------
# Test 10: [DONE] terminates stream — bytes after [DONE] are not processed
# ---------------------------------------------------------------------------


def test_done_terminates_stream(monkeypatch, tmp_path):
    """data: [DONE] stops iteration; any bytes after it are never read."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    roles = _setup_role(tmp_path)

    # Craft a response where extra content after [DONE] would be a FILE capture
    # if mistakenly parsed — confirms termination is respected.
    payload_before_done = _delta_chunk(
        "legitimate content",
        finish_reason="stop",
        usage={
            "prompt_tokens": 5,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 0},
            "cost": 0.0001,
        },
    )
    # extra_after_done contains a FILE marker — must NOT be captured.
    after_done = (
        b"data: "
        + json.dumps(
            {
                "id": "gen-extra",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "content": "<<<FILE: leak.md>>>leaked<<<END FILE: leak.md>>>",
                            "role": "assistant",
                        },
                        "finish_reason": None,
                    }
                ],
            }
        ).encode()
        + b"\n\n"
    )
    raw = _make_sse_bytes(payload_before_done, extra_after_done=after_done)
    fake_resp = _FakeStreamResp(raw)

    with patch("findajob.llm.openrouter.urllib.request.urlopen", return_value=fake_resp):
        chunks = list(complete_stream(role="test_role", prompt="test", roles_dir=roles))

    captured = [c for c in chunks if c["type"] == "captured"]
    finish = [c for c in chunks if c["type"] == "finish"]

    # No leak.md captured — [DONE] stopped the read.
    assert len(captured) == 0
    assert len(finish) == 1
    assert finish[0]["text"] == "legitimate content"
    # Confirm "leak.md" is not in any captured name.
    assert not any(c.get("name") == "leak.md" for c in chunks)
