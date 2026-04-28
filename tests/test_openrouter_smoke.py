"""Tests for findajob.onboarding.openrouter_smoke (#328).

Verify_openrouter_key returns user-friendly error strings for every failure
mode and never raises. Tests stub urllib.request.urlopen at the module level
so no real network calls happen.
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from findajob.onboarding.openrouter_smoke import verify_openrouter_key


def _ok_response(body: dict) -> io.BytesIO:
    """Build a fake urlopen() context-manager-compatible response."""

    class _Resp:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return self._payload

    return _Resp(json.dumps(body).encode("utf-8"))  # type: ignore[return-value]


def test_empty_key_returns_false_with_friendly_message() -> None:
    ok, err = verify_openrouter_key("")
    assert ok is False
    assert err is not None
    assert "empty" in err.lower()


def test_whitespace_only_key_returns_false() -> None:
    ok, err = verify_openrouter_key("   \t\n  ")
    assert ok is False
    assert err is not None


def test_successful_call_returns_true_none() -> None:
    fake_body = {"id": "abc", "choices": [{"message": {"content": "hi"}}]}
    with patch(
        "findajob.onboarding.openrouter_smoke.urllib.request.urlopen",
        return_value=_ok_response(fake_body),
    ):
        ok, err = verify_openrouter_key("sk-or-v1-good")
    assert ok is True
    assert err is None


def test_401_returns_user_friendly_unauthorized_message() -> None:
    err_resp = HTTPError("u", 401, "Unauthorized", {}, fp=io.BytesIO(b'{"error": "bad key"}'))  # type: ignore[arg-type]
    with patch(
        "findajob.onboarding.openrouter_smoke.urllib.request.urlopen",
        side_effect=err_resp,
    ):
        ok, err = verify_openrouter_key("sk-or-v1-bad")
    assert ok is False
    assert err is not None
    assert "401" in err
    assert "Unauthorized" in err or "rejected" in err.lower()


def test_402_returns_payment_required_message() -> None:
    err_resp = HTTPError("u", 402, "Payment Required", {}, fp=io.BytesIO(b'{"error": "no credit"}'))  # type: ignore[arg-type]
    with patch(
        "findajob.onboarding.openrouter_smoke.urllib.request.urlopen",
        side_effect=err_resp,
    ):
        ok, err = verify_openrouter_key("sk-or-v1-no-credit")
    assert ok is False
    assert err is not None
    assert "402" in err or "credit" in err.lower() or "Payment" in err


def test_429_returns_rate_limit_message() -> None:
    err_resp = HTTPError("u", 429, "Too Many Requests", {}, fp=io.BytesIO(b""))  # type: ignore[arg-type]
    with patch(
        "findajob.onboarding.openrouter_smoke.urllib.request.urlopen",
        side_effect=err_resp,
    ):
        ok, err = verify_openrouter_key("sk-or-v1-throttled")
    assert ok is False
    assert err is not None
    assert "rate" in err.lower() or "429" in err


def test_url_error_returns_network_message() -> None:
    with patch(
        "findajob.onboarding.openrouter_smoke.urllib.request.urlopen",
        side_effect=URLError("Name or service not known"),
    ):
        ok, err = verify_openrouter_key("sk-or-v1-good")
    assert ok is False
    assert err is not None
    assert "OpenRouter" in err  # message references the service


def test_unexpected_response_shape_returns_false() -> None:
    """OpenRouter returned 200 but body has no 'choices' field."""
    fake_body = {"some": "other-shape"}
    with patch(
        "findajob.onboarding.openrouter_smoke.urllib.request.urlopen",
        return_value=_ok_response(fake_body),
    ):
        ok, err = verify_openrouter_key("sk-or-v1-good")
    assert ok is False
    assert err is not None


def test_non_json_response_returns_false() -> None:
    """Server returned 200 but body is HTML or garbage."""

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b"<html>upstream proxy error</html>"

    with patch(
        "findajob.onboarding.openrouter_smoke.urllib.request.urlopen",
        return_value=_Resp(),
    ):
        ok, err = verify_openrouter_key("sk-or-v1-good")
    assert ok is False
    assert err is not None
    assert "non-JSON" in err or "JSON" in err
