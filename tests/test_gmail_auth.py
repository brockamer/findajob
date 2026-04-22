# tests/test_gmail_auth.py
"""
Tests for scripts/gmail_auth.py — the standalone OAuth helper.

Scope: argparse + flow dispatch. Actual OAuth calls are mocked.
Device flow was removed in #115 — Gmail scopes are excluded from
Google's device authorization grant (invalid_scope).
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


def test_default_port_is_8080():
    import gmail_auth

    parser = gmail_auth.build_parser()
    args = parser.parse_args([])
    assert args.port == 8080


def test_port_flag_overrides_default():
    import gmail_auth

    parser = gmail_auth.build_parser()
    args = parser.parse_args(["--port", "9090"])
    assert args.port == 9090


def test_mode_arg_no_longer_accepted():
    """--mode=device is gone; passing it should error."""
    import gmail_auth

    parser = gmail_auth.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--mode", "device"])


def test_run_calls_local_flow(fake_client_secrets, tmp_path):
    """main() should call run_local and write the token file."""
    import gmail_auth

    token_path = tmp_path / "gmail_token.json"
    mock_creds = MagicMock()
    mock_creds.to_json.return_value = '{"token": "fake-local"}'

    with patch.object(gmail_auth, "run_local", return_value=mock_creds) as m_local:
        gmail_auth.main(
            [
                "--client-secrets",
                str(fake_client_secrets),
                "--token-out",
                str(token_path),
                "--port",
                "8080",
            ]
        )

    m_local.assert_called_once_with(str(fake_client_secrets), 8080)
    assert token_path.read_text() == '{"token": "fake-local"}'
    assert (token_path.stat().st_mode & 0o777) == 0o600


def test_missing_client_secrets_errors(tmp_path):
    """If client-secrets file doesn't exist, should exit non-zero with a clear error."""
    import gmail_auth

    token_path = tmp_path / "gmail_token.json"
    missing_client = tmp_path / "nonexistent.json"

    with pytest.raises(SystemExit) as exc_info:
        gmail_auth.main(
            [
                "--client-secrets",
                str(missing_client),
                "--token-out",
                str(token_path),
            ]
        )
    assert exc_info.value.code != 0
