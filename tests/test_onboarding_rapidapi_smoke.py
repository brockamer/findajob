"""Tests for findajob.onboarding.rapidapi_smoke (#689).

verify_rapidapi_key returns user-friendly error strings for every
failure mode and never raises. Tests stub urllib.request.urlopen at the
module level so no real network calls happen.
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from findajob.onboarding.rapidapi_smoke import verify_rapidapi_key


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
    ok, err = verify_rapidapi_key("")
    assert ok is False
    assert err is not None
    assert "empty" in err.lower()


def test_whitespace_only_key_returns_false() -> None:
    ok, err = verify_rapidapi_key("   \t\n  ")
    assert ok is False
    assert err is not None


def test_successful_call_returns_true_none() -> None:
    fake_body = {"data": [], "meta": {}}
    with patch(
        "findajob.onboarding.rapidapi_smoke.urllib.request.urlopen",
        return_value=_ok_response(fake_body),
    ):
        ok, err = verify_rapidapi_key("fake-rapidapi-key-good")
    assert ok is True
    assert err is None


def test_401_returns_user_friendly_unauthorized_message() -> None:
    err_resp = HTTPError("u", 401, "Unauthorized", {}, fp=io.BytesIO(b'{"error": "bad key"}'))  # type: ignore[arg-type]
    with patch(
        "findajob.onboarding.rapidapi_smoke.urllib.request.urlopen",
        side_effect=err_resp,
    ):
        ok, err = verify_rapidapi_key("fake-rapidapi-key-bad")
    assert ok is False
    assert err is not None
    assert "401" in err
    assert "developer/security" in err  # AC #2 — point user at key dashboard


def test_403_disambiguates_invalid_key_subscription_and_ip_block() -> None:
    """#689 + #679: 403 from jobs-api14 has three causes (invalid key, no
    subscription, region/IP-block on Fly egress IPs). Message must surface
    all three so testers don't burn time chasing the wrong cause."""
    err_resp = HTTPError("u", 403, "Forbidden", {}, fp=io.BytesIO(b'{"message":"Forbidden"}'))  # type: ignore[arg-type]
    with patch(
        "findajob.onboarding.rapidapi_smoke.urllib.request.urlopen",
        side_effect=err_resp,
    ):
        ok, err = verify_rapidapi_key("fake-rapidapi-key-403")
    assert ok is False
    assert err is not None
    assert "403" in err
    # All three failure modes named:
    assert "developer/security" in err  # invalid-key path
    assert "jobs-api14" in err  # subscription path
    assert "Fly" in err or "egress" in err or "region" in err  # IP-block path (#679)
    # Body excerpt surfaced (parity with adapter's jobsapi_403 log event):
    assert "Forbidden" in err


def test_429_returns_rate_limit_message() -> None:
    err_resp = HTTPError("u", 429, "Too Many Requests", {}, fp=io.BytesIO(b""))  # type: ignore[arg-type]
    with patch(
        "findajob.onboarding.rapidapi_smoke.urllib.request.urlopen",
        side_effect=err_resp,
    ):
        ok, err = verify_rapidapi_key("fake-rapidapi-key-throttled")
    assert ok is False
    assert err is not None
    assert "rate" in err.lower() or "429" in err


def test_500_returns_server_error_message() -> None:
    err_resp = HTTPError("u", 500, "Internal Server Error", {}, fp=io.BytesIO(b"upstream down"))  # type: ignore[arg-type]
    with patch(
        "findajob.onboarding.rapidapi_smoke.urllib.request.urlopen",
        side_effect=err_resp,
    ):
        ok, err = verify_rapidapi_key("fake-rapidapi-key-good")
    assert ok is False
    assert err is not None
    assert "500" in err or "server" in err.lower()


def test_url_error_returns_network_message() -> None:
    with patch(
        "findajob.onboarding.rapidapi_smoke.urllib.request.urlopen",
        side_effect=URLError("Name or service not known"),
    ):
        ok, err = verify_rapidapi_key("fake-rapidapi-key-good")
    assert ok is False
    assert err is not None
    assert "RapidAPI" in err  # message references the service


def test_has_error_in_200_body_returns_false() -> None:
    """jobs-api14 sometimes returns 200 with hasError=true for subscription
    problems — mirror the live_test branch."""
    fake_body = {"hasError": True, "errors": ["not subscribed"]}
    with patch(
        "findajob.onboarding.rapidapi_smoke.urllib.request.urlopen",
        return_value=_ok_response(fake_body),
    ):
        ok, err = verify_rapidapi_key("fake-rapidapi-key-no-sub")
    assert ok is False
    assert err is not None
    assert "subscribe" in err.lower() or "error" in err.lower()


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
        "findajob.onboarding.rapidapi_smoke.urllib.request.urlopen",
        return_value=_Resp(),
    ):
        ok, err = verify_rapidapi_key("fake-rapidapi-key-good")
    assert ok is False
    assert err is not None
    assert "non-JSON" in err or "JSON" in err
