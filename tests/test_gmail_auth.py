# tests/test_gmail_auth.py
"""
Tests for scripts/gmail_auth.py — the standalone OAuth helper.

Scope: argparse + mode dispatch. Actual OAuth calls are mocked — we don't
exercise Google's endpoints from unit tests.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make scripts/ importable
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

# tests/test_sync_sheet.py stubs google.oauth2 in sys.modules at import time.
# When that file is collected before this one, the polling-loop tests below
# can't lazy-import google.oauth2.credentials. Pre-seed a stub of our own so
# the from-import in run_device resolves regardless of test ordering.
sys.modules.setdefault("google.oauth2.credentials", MagicMock())


@pytest.fixture
def fake_client_secrets(tmp_path):
    """Write a minimal but structurally-valid OAuth client JSON."""
    p = tmp_path / "gmail_oauth_client.json"
    p.write_text(
        '{"installed": {"client_id": "x.apps.googleusercontent.com", '
        '"client_secret": "abc", "redirect_uris": ["http://localhost"]}}'
    )
    return p


def test_default_mode_is_device():
    """Running with no --mode flag should default to device flow."""
    import gmail_auth

    parser = gmail_auth.build_parser()
    args = parser.parse_args([])
    assert args.mode == "device"


def test_mode_flag_accepts_device_and_local():
    import gmail_auth

    parser = gmail_auth.build_parser()
    assert parser.parse_args(["--mode", "device"]).mode == "device"
    assert parser.parse_args(["--mode", "local"]).mode == "local"


def test_mode_flag_rejects_unknown():
    import gmail_auth

    parser = gmail_auth.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--mode", "magic"])


def test_run_dispatches_to_device_mode(fake_client_secrets, tmp_path):
    """--mode=device should call run_device and write the token file."""
    import gmail_auth

    token_path = tmp_path / "gmail_token.json"
    mock_creds = MagicMock()
    mock_creds.to_json.return_value = '{"token": "fake"}'

    with (
        patch.object(gmail_auth, "run_device", return_value=mock_creds) as m_device,
        patch.object(gmail_auth, "run_local") as m_local,
    ):
        gmail_auth.main(
            [
                "--mode",
                "device",
                "--client-secrets",
                str(fake_client_secrets),
                "--token-out",
                str(token_path),
            ]
        )

    m_device.assert_called_once()
    m_local.assert_not_called()
    assert token_path.read_text() == '{"token": "fake"}'
    # Token file is the long-lived OAuth credential — must be 0600 to keep
    # other container users from reading it.
    assert (token_path.stat().st_mode & 0o777) == 0o600


def test_run_dispatches_to_local_mode(fake_client_secrets, tmp_path):
    """--mode=local should call run_local, not run_device."""
    import gmail_auth

    token_path = tmp_path / "gmail_token.json"
    mock_creds = MagicMock()
    mock_creds.to_json.return_value = '{"token": "fake-local"}'

    with (
        patch.object(gmail_auth, "run_local", return_value=mock_creds) as m_local,
        patch.object(gmail_auth, "run_device") as m_device,
    ):
        gmail_auth.main(
            [
                "--mode",
                "local",
                "--client-secrets",
                str(fake_client_secrets),
                "--token-out",
                str(token_path),
                "--port",
                "8080",
            ]
        )

    m_local.assert_called_once()
    m_device.assert_not_called()


def _device_code_response():
    """Mock response for the initial /device/code POST."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "device_code": "dev-xyz",
        "user_code": "ABCD-EFGH",
        "verification_url": "https://www.google.com/device",
        "expires_in": 600,
        "interval": 0,  # zero so test doesn't actually sleep
    }
    return resp


def _token_response(ok: bool, body: dict):
    resp = MagicMock()
    resp.ok = ok
    resp.json.return_value = body
    return resp


def test_device_flow_retries_on_authorization_pending(fake_client_secrets, tmp_path):
    """Polling continues while Google returns authorization_pending, succeeds when granted."""
    import gmail_auth

    token_path = tmp_path / "gmail_token.json"
    success_body = {"access_token": "tok", "refresh_token": "ref"}

    with (
        patch("requests.post") as m_post,
        patch("google.oauth2.credentials.Credentials") as m_creds_cls,
    ):
        m_post.side_effect = [
            _device_code_response(),
            _token_response(False, {"error": "authorization_pending"}),
            _token_response(True, success_body),
        ]
        mock_creds = MagicMock()
        mock_creds.to_json.return_value = '{"token": "tok"}'
        m_creds_cls.return_value = mock_creds

        gmail_auth.main(
            [
                "--mode",
                "device",
                "--client-secrets",
                str(fake_client_secrets),
                "--token-out",
                str(token_path),
            ]
        )

    # 1 device-code call + 2 token polls = 3 POSTs
    assert m_post.call_count == 3
    assert token_path.read_text() == '{"token": "tok"}'


def test_device_flow_bumps_interval_on_slow_down(fake_client_secrets, tmp_path):
    """slow_down response should increase the polling interval."""
    import gmail_auth

    token_path = tmp_path / "gmail_token.json"
    success_body = {"access_token": "tok", "refresh_token": "ref"}
    sleep_calls: list[float] = []

    with (
        patch("requests.post") as m_post,
        patch("time.sleep", side_effect=sleep_calls.append),
        patch("google.oauth2.credentials.Credentials") as m_creds_cls,
    ):
        m_post.side_effect = [
            _device_code_response(),
            _token_response(False, {"error": "slow_down"}),
            _token_response(True, success_body),
        ]
        mock_creds = MagicMock()
        mock_creds.to_json.return_value = '{"token": "tok"}'
        m_creds_cls.return_value = mock_creds

        gmail_auth.main(
            [
                "--mode",
                "device",
                "--client-secrets",
                str(fake_client_secrets),
                "--token-out",
                str(token_path),
            ]
        )

    # Two sleeps: first at base interval (0), second after slow_down bumps it (+5)
    assert sleep_calls == [0, 5]


def test_missing_client_secrets_errors(tmp_path):
    """If client-secrets file doesn't exist, should exit non-zero with a clear error."""
    import gmail_auth

    token_path = tmp_path / "gmail_token.json"
    missing_client = tmp_path / "nonexistent.json"

    with pytest.raises(SystemExit) as exc_info:
        gmail_auth.main(
            [
                "--mode",
                "device",
                "--client-secrets",
                str(missing_client),
                "--token-out",
                str(token_path),
            ]
        )
    assert exc_info.value.code != 0
