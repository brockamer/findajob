"""Unit tests for src/findajob/gmail_imap.py — Tier 2 of the test plan."""

from __future__ import annotations

import imaplib
import json
import os
import socket
import ssl
import stat
from unittest.mock import MagicMock, patch

import pytest

from findajob import gmail_imap


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "gmail.json"
    monkeypatch.setattr(gmail_imap, "GMAIL_CONFIG_PATH", str(p))
    return p


def test_load_config_missing_file_returns_none(cfg_path):
    assert not cfg_path.exists()
    assert gmail_imap.load_config() is None


def test_load_config_strips_password_spaces(cfg_path):
    cfg_path.write_text(
        json.dumps(
            {
                "_schema": 1,
                "address": "user@gmail.com",
                "app_password": "abcd efgh ijkl mnop",
                "sender_allowlist": ["jobalerts-noreply@linkedin.com"],
                "configured_at": "2026-04-30T00:00:00Z",
            }
        )
    )
    cfg = gmail_imap.load_config()
    assert cfg is not None
    assert cfg.app_password == "abcdefghijklmnop"
    assert len(cfg.app_password) == 16


def test_load_config_rejects_wrong_password_length(cfg_path):
    cfg_path.write_text(
        json.dumps(
            {
                "_schema": 1,
                "address": "user@gmail.com",
                "app_password": "abcdefghijklmno",  # 15 chars
                "sender_allowlist": ["jobalerts-noreply@linkedin.com"],
                "configured_at": "2026-04-30T00:00:00Z",
            }
        )
    )
    assert gmail_imap.load_config() is None


def test_load_config_rejects_invalid_email(cfg_path):
    cfg_path.write_text(
        json.dumps(
            {
                "_schema": 1,
                "address": "not-an-email",
                "app_password": "abcdefghijklmnop",
                "sender_allowlist": ["jobalerts-noreply@linkedin.com"],
                "configured_at": "2026-04-30T00:00:00Z",
            }
        )
    )
    assert gmail_imap.load_config() is None


def test_load_config_rejects_unknown_schema_version(cfg_path):
    cfg_path.write_text(
        json.dumps(
            {
                "_schema": 99,
                "address": "user@gmail.com",
                "app_password": "abcdefghijklmnop",
                "sender_allowlist": ["jobalerts-noreply@linkedin.com"],
                "configured_at": "2026-04-30T00:00:00Z",
            }
        )
    )
    assert gmail_imap.load_config() is None


def test_save_config_writes_atomically_and_chmod_600(cfg_path):
    cfg = gmail_imap.GmailConfig(
        address="user@gmail.com",
        app_password="abcdefghijklmnop",
        sender_allowlist=["jobalerts-noreply@linkedin.com"],
        configured_at="2026-04-30T00:00:00Z",
    )
    gmail_imap.save_config(cfg)
    assert cfg_path.exists()
    mode = stat.S_IMODE(cfg_path.stat().st_mode)
    assert mode == 0o600
    payload = json.loads(cfg_path.read_text())
    assert payload["_schema"] == 1
    assert payload["address"] == "user@gmail.com"
    assert payload["app_password"] == "abcdefghijklmnop"


def test_save_config_uses_temp_then_rename(cfg_path):
    """Save must go through .tmp + os.replace, never a direct overwrite."""
    cfg_path.write_text("{}")  # pre-existing
    cfg = gmail_imap.GmailConfig(
        address="user@gmail.com",
        app_password="abcdefghijklmnop",
        sender_allowlist=["jobalerts-noreply@linkedin.com"],
        configured_at="2026-04-30T00:00:00Z",
    )
    with patch("findajob.gmail_imap.os.replace", wraps=os.replace) as m:
        gmail_imap.save_config(cfg)
    m.assert_called_once()
    src, dst = m.call_args.args
    assert src.endswith(".tmp")
    assert dst == str(cfg_path)


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    p = tmp_path / "gmail_state.json"
    monkeypatch.setattr(gmail_imap, "GMAIL_STATE_PATH", str(p))
    return p


def test_load_state_missing_returns_zero_state(state_path):
    s = gmail_imap.load_state()
    assert s.last_uid == 0
    assert s.last_uidvalidity == 0
    assert s.auth_failure_streak == 0
    assert s.last_fetched_at is None
    assert s.last_login_at is None
    assert s.last_error is None


def test_load_state_rejects_unknown_schema_returns_zero_state(state_path):
    state_path.write_text(json.dumps({"_schema": 99, "last_uid": 1}))
    s = gmail_imap.load_state()
    assert s.last_uid == 0  # treats unknown schema as cold start


def test_save_state_round_trip(state_path):
    s = gmail_imap.GmailState(
        last_uid=12345,
        last_uidvalidity=67890,
        auth_failure_streak=2,
        last_fetched_at="2026-04-30T00:00:00Z",
        last_login_at="2026-04-30T00:00:00Z",
        last_error="auth_failed",
    )
    gmail_imap.save_state(s)
    loaded = gmail_imap.load_state()
    assert loaded == s


def test_save_state_atomic_replace(state_path):
    state_path.write_text("{}")
    s = gmail_imap.GmailState(last_uid=1)
    with patch("findajob.gmail_imap.os.replace", wraps=os.replace) as m:
        gmail_imap.save_state(s)
    m.assert_called_once()
    src, dst = m.call_args.args
    assert src.endswith(".tmp")
    assert dst == str(state_path)


@pytest.fixture
def fake_config():
    return gmail_imap.GmailConfig(
        address="user@gmail.com",
        app_password="abcdefghijklmnop",
        sender_allowlist=["jobalerts-noreply@linkedin.com"],
        configured_at="2026-04-30T00:00:00Z",
    )


def test_test_login_success(fake_config):
    fake_client = MagicMock()
    fake_client.login.return_value = ("OK", [b"LOGIN completed"])
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        result = gmail_imap.test_login(fake_config)
    assert result == gmail_imap.TestResult.SUCCESS
    fake_client.login.assert_called_once_with("user@gmail.com", "abcdefghijklmnop")
    fake_client.logout.assert_called_once()


def test_test_login_authentication_failed(fake_config):
    fake_client = MagicMock()
    fake_client.login.side_effect = imaplib.IMAP4.error(b"AUTHENTICATIONFAILED Invalid credentials")
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        result = gmail_imap.test_login(fake_config)
    assert result == gmail_imap.TestResult.AUTH_FAILED


def test_test_login_invalid_credentials_phrase(fake_config):
    """Some Gmail responses use 'Invalid credentials' instead of AUTHENTICATIONFAILED."""
    fake_client = MagicMock()
    fake_client.login.side_effect = imaplib.IMAP4.error(b"Invalid credentials abc123")
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        result = gmail_imap.test_login(fake_config)
    assert result == gmail_imap.TestResult.AUTH_FAILED


def test_test_login_socket_timeout(fake_config):
    with patch(
        "findajob.gmail_imap.imaplib.IMAP4_SSL",
        side_effect=TimeoutError("connection timed out"),
    ):
        result = gmail_imap.test_login(fake_config)
    assert result == gmail_imap.TestResult.CONNECTION_ERROR


def test_test_login_dns_failure(fake_config):
    with patch(
        "findajob.gmail_imap.imaplib.IMAP4_SSL",
        side_effect=socket.gaierror("nodename nor servname provided"),
    ):
        result = gmail_imap.test_login(fake_config)
    assert result == gmail_imap.TestResult.CONNECTION_ERROR


def test_test_login_ssl_error(fake_config):
    with patch(
        "findajob.gmail_imap.imaplib.IMAP4_SSL",
        side_effect=ssl.SSLError("ssl handshake failed"),
    ):
        result = gmail_imap.test_login(fake_config)
    assert result == gmail_imap.TestResult.CONNECTION_ERROR


def test_test_login_unknown_imap_error_is_connection_not_auth(fake_config):
    """Unknown IMAP errors must be treated as transient, not auth — must not trip ntfy."""
    fake_client = MagicMock()
    fake_client.login.side_effect = imaplib.IMAP4.error(b"some unrelated error")
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        result = gmail_imap.test_login(fake_config)
    assert result == gmail_imap.TestResult.CONNECTION_ERROR


def test_test_login_logs_out_on_exception(fake_config):
    """logout() must run even when login raises."""
    fake_client = MagicMock()
    fake_client.login.side_effect = imaplib.IMAP4.error(b"AUTHENTICATIONFAILED")
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        gmail_imap.test_login(fake_config)
    fake_client.logout.assert_called_once()


def test_test_login_uses_imap_gmail_com_993_with_timeout(fake_config):
    fake_client = MagicMock()
    fake_client.login.return_value = ("OK", [])
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client) as m:
        gmail_imap.test_login(fake_config)
    m.assert_called_once_with("imap.gmail.com", 993, timeout=10)
