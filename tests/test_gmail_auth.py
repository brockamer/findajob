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
