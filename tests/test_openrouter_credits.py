"""Tests for findajob.openrouter_credits (#665).

Live OpenRouter `/auth/key` lookup for the nav credit chip. Failure-open
contract: any HTTP error, missing key, missing limit, or schema mismatch
returns None — the template hides the chip when None.

Mocks `urllib.request.urlopen` at the module under test — no real network.
Each test resets the in-process cache via `reset_cache_for_tests()` to
avoid cross-test bleed.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

from findajob.openrouter_credits import (
    CreditInfo,
    credit_remaining,
    reset_cache_for_tests,
)

FIXTURES = Path(__file__).parent / "fixtures" / "llm"


class _FakeResp:
    """urlopen() context-manager-compatible fake."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self._body


def _load_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache_for_tests()
    yield
    reset_cache_for_tests()


def test_healthy_response_returns_credit_info(monkeypatch):
    """Live `limit_remaining` from /auth/key flows through to CreditInfo."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    body = _load_fixture("openrouter_auth_key_healthy.json")

    with patch(
        "findajob.openrouter_credits.urllib.request.urlopen",
        return_value=_FakeResp(body),
    ):
        info = credit_remaining()

    assert isinstance(info, CreditInfo)
    # Fixture has limit_remaining = 12.154125374000003 — > $5, so "normal"
    assert info.remaining_usd == pytest.approx(12.154125374000003)
    assert info.state == "normal"


def test_unlimited_or_free_tier_key_returns_none(monkeypatch):
    """limit_remaining=null (no-limit or free-tier key) hides the chip."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    body = _load_fixture("openrouter_auth_key_unlimited.json")

    with patch(
        "findajob.openrouter_credits.urllib.request.urlopen",
        return_value=_FakeResp(body),
    ):
        info = credit_remaining()

    assert info is None


def test_http_401_invalid_key_returns_none(monkeypatch):
    """A 401 (revoked/invalid key) hides the chip silently — no 500."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-bad")
    err = HTTPError(
        url="https://openrouter.ai/api/v1/auth/key",
        code=401,
        msg="Unauthorized",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b'{"error":"invalid_api_key"}'),
    )

    with patch(
        "findajob.openrouter_credits.urllib.request.urlopen",
        side_effect=err,
    ):
        info = credit_remaining()

    assert info is None


def test_http_5xx_returns_none(monkeypatch):
    """OpenRouter outage hides the chip silently."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    err = HTTPError(
        url="https://openrouter.ai/api/v1/auth/key",
        code=503,
        msg="Service Unavailable",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b""),
    )

    with patch(
        "findajob.openrouter_credits.urllib.request.urlopen",
        side_effect=err,
    ):
        info = credit_remaining()

    assert info is None


def test_network_timeout_returns_none(monkeypatch):
    """urllib URLError (DNS / connect timeout / etc.) hides the chip."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")

    with patch(
        "findajob.openrouter_credits.urllib.request.urlopen",
        side_effect=URLError("connection refused"),
    ):
        info = credit_remaining()

    assert info is None


def test_unexpected_schema_returns_none(monkeypatch):
    """Schema drift (missing `data` envelope) hides the chip silently."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    body = json.dumps({"unexpected": "shape"}).encode("utf-8")

    with patch(
        "findajob.openrouter_credits.urllib.request.urlopen",
        return_value=_FakeResp(body),
    ):
        info = credit_remaining()

    assert info is None


def test_malformed_json_returns_none(monkeypatch):
    """Non-JSON body (e.g., HTML error page) hides the chip silently."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")

    with patch(
        "findajob.openrouter_credits.urllib.request.urlopen",
        return_value=_FakeResp(b"<html>504 Gateway Timeout</html>"),
    ):
        info = credit_remaining()

    assert info is None


def test_missing_api_key_env_returns_none_without_http_call(monkeypatch):
    """No OPENROUTER_API_KEY env → return None without ever touching the network."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with patch(
        "findajob.openrouter_credits.urllib.request.urlopen",
        side_effect=AssertionError("must not be called"),
    ):
        info = credit_remaining()

    assert info is None


def test_blank_api_key_env_returns_none_without_http_call(monkeypatch):
    """Empty-string OPENROUTER_API_KEY (common misconfiguration) also short-circuits."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "   ")

    with patch(
        "findajob.openrouter_credits.urllib.request.urlopen",
        side_effect=AssertionError("must not be called"),
    ):
        info = credit_remaining()

    assert info is None


def test_state_amber_when_below_amber_threshold(monkeypatch):
    """Remaining < $5 (default amber threshold) → state='amber'."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    body = json.dumps({"data": {"limit_remaining": 3.42}}).encode("utf-8")

    with patch(
        "findajob.openrouter_credits.urllib.request.urlopen",
        return_value=_FakeResp(body),
    ):
        info = credit_remaining()

    assert info is not None
    assert info.remaining_usd == pytest.approx(3.42)
    assert info.state == "amber"


def test_state_red_when_below_red_threshold(monkeypatch):
    """Remaining < $1 (default red threshold) → state='red'."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    body = json.dumps({"data": {"limit_remaining": 0.42}}).encode("utf-8")

    with patch(
        "findajob.openrouter_credits.urllib.request.urlopen",
        return_value=_FakeResp(body),
    ):
        info = credit_remaining()

    assert info is not None
    assert info.state == "red"


def test_thresholds_are_env_configurable(monkeypatch):
    """Operator can raise amber threshold via OPENROUTER_CREDIT_AMBER_USD."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setenv("OPENROUTER_CREDIT_AMBER_USD", "20")
    monkeypatch.setenv("OPENROUTER_CREDIT_RED_USD", "5")
    body = json.dumps({"data": {"limit_remaining": 15.0}}).encode("utf-8")

    with patch(
        "findajob.openrouter_credits.urllib.request.urlopen",
        return_value=_FakeResp(body),
    ):
        info = credit_remaining()

    assert info is not None
    # 15.0 is below the raised $20 amber but above the raised $5 red
    assert info.state == "amber"


def test_cache_avoids_repeat_http_within_ttl(monkeypatch):
    """Two calls within TTL = exactly one HTTP request."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    body = _load_fixture("openrouter_auth_key_healthy.json")
    call_count = {"n": 0}

    def _counter(req, timeout=None):
        call_count["n"] += 1
        return _FakeResp(body)

    with patch(
        "findajob.openrouter_credits.urllib.request.urlopen",
        side_effect=_counter,
    ):
        first = credit_remaining()
        second = credit_remaining()

    assert first is not None
    assert second is not None
    assert first.remaining_usd == second.remaining_usd
    assert call_count["n"] == 1


def test_cache_caches_none_results_too(monkeypatch):
    """Failure-open results are also cached — no retry storm on a bad key."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-bad")
    err = HTTPError(
        url="https://openrouter.ai/api/v1/auth/key",
        code=401,
        msg="Unauthorized",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b""),
    )
    call_count = {"n": 0}

    def _counter(req, timeout=None):
        call_count["n"] += 1
        raise err

    with patch(
        "findajob.openrouter_credits.urllib.request.urlopen",
        side_effect=_counter,
    ):
        assert credit_remaining() is None
        assert credit_remaining() is None

    assert call_count["n"] == 1


def test_authorization_header_sent_as_bearer(monkeypatch):
    """Request carries `Authorization: Bearer <key>` — verify wire format."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-xyz")
    captured: dict = {}
    body = _load_fixture("openrouter_auth_key_healthy.json")

    def _capture(req, timeout=None):
        captured["headers"] = dict(req.headers)
        captured["url"] = req.full_url
        return _FakeResp(body)

    with patch(
        "findajob.openrouter_credits.urllib.request.urlopen",
        side_effect=_capture,
    ):
        credit_remaining()

    assert captured["url"] == "https://openrouter.ai/api/v1/auth/key"
    # urllib normalizes header keys via .capitalize()
    assert captured["headers"].get("Authorization") == "Bearer sk-or-v1-test-xyz"
