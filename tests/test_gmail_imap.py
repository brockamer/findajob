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


def _select_response(uidvalidity: int):
    """Build a fake SELECT response that imaplib.IMAP4.untagged_responses uses."""
    return ("OK", [b"1234"]), {"UIDVALIDITY": [str(uidvalidity).encode()]}


def _make_fake_imap_client(*, uidvalidity: int, search_results: dict[str, list[int]], messages: dict[int, bytes]):
    """Build a MagicMock IMAP client that simulates SELECT/SEARCH/FETCH.

    ``search_results`` maps sender → list of UIDs. ``messages`` maps UID → raw bytes.
    """
    client = MagicMock()
    client.login.return_value = ("OK", [])
    client.logout.return_value = ("OK", [])

    def select_side_effect(mailbox, **kwargs):
        client.untagged_responses = {"UIDVALIDITY": [str(uidvalidity).encode()]}
        return ("OK", [b"1234"])

    client.select = MagicMock(side_effect=select_side_effect)

    def uid_side_effect(verb, *args):
        if verb == "SEARCH":
            search_str = " ".join(a.decode() if isinstance(a, bytes) else a for a in args)
            for sender, uids in search_results.items():
                if sender in search_str:
                    return ("OK", [b" ".join(str(u).encode() for u in uids)])
            return ("OK", [b""])
        if verb == "FETCH":
            uid = int(args[0])
            raw = messages[uid]
            return ("OK", [(b"1 (UID %d BODY.PEEK[]" % uid, raw)])
        return ("OK", [])

    client.uid = MagicMock(side_effect=uid_side_effect)
    return client


def test_fetch_uses_uid_search_with_last_uid_plus_one(fake_config, state_path):
    state = gmail_imap.GmailState(last_uid=12345, last_uidvalidity=67890)
    fake_client = _make_fake_imap_client(
        uidvalidity=67890,
        search_results={"jobalerts-noreply@linkedin.com": []},
        messages={},
    )
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        gmail_imap.fetch_new_messages(fake_config, state)
    search_calls = [c for c in fake_client.uid.call_args_list if c.args[0] == "SEARCH"]
    assert len(search_calls) == 1
    args = " ".join(a.decode() if isinstance(a, bytes) else a for a in search_calls[0].args[1:])
    assert "12346:*" in args
    assert "jobalerts-noreply@linkedin.com" in args


def test_fetch_uses_body_peek_not_body(fake_config, state_path):
    state = gmail_imap.GmailState(last_uid=0, last_uidvalidity=67890)
    fake_client = _make_fake_imap_client(
        uidvalidity=67890,
        search_results={"jobalerts-noreply@linkedin.com": [100]},
        messages={100: b"From: jobalerts-noreply@linkedin.com\r\n\r\nbody"},
    )
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        gmail_imap.fetch_new_messages(fake_config, state)
    fetch_calls = [c for c in fake_client.uid.call_args_list if c.args[0] == "FETCH"]
    assert all("BODY.PEEK[]" in str(c.args) for c in fetch_calls)
    assert not any("BODY[]" in str(c.args) and "PEEK" not in str(c.args) for c in fetch_calls)


def test_fetch_iterates_all_senders_in_allowlist(fake_config, state_path):
    cfg = gmail_imap.GmailConfig(
        address="user@gmail.com",
        app_password="abcdefghijklmnop",
        sender_allowlist=["a@x.com", "b@y.com", "c@z.com"],
        configured_at="2026-04-30T00:00:00Z",
    )
    state = gmail_imap.GmailState(last_uid=0, last_uidvalidity=67890)
    fake_client = _make_fake_imap_client(
        uidvalidity=67890,
        search_results={"a@x.com": [], "b@y.com": [], "c@z.com": []},
        messages={},
    )
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        gmail_imap.fetch_new_messages(cfg, state)
    search_calls = [c for c in fake_client.uid.call_args_list if c.args[0] == "SEARCH"]
    assert len(search_calls) == 3


def test_fetch_logout_called_even_on_exception(fake_config, state_path):
    fake_client = MagicMock()
    fake_client.login.return_value = ("OK", [])
    fake_client.select.side_effect = RuntimeError("boom")
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        outcome = gmail_imap.fetch_new_messages(fake_config, gmail_imap.GmailState())
    fake_client.logout.assert_called_once()
    assert outcome.result == gmail_imap.TestResult.CONNECTION_ERROR


def test_fetch_returns_messages_with_sender_tuples(fake_config, state_path):
    state = gmail_imap.GmailState(last_uid=0, last_uidvalidity=67890)
    raw = b"From: jobalerts-noreply@linkedin.com\r\nSubject: x\r\n\r\nbody"
    fake_client = _make_fake_imap_client(
        uidvalidity=67890,
        search_results={"jobalerts-noreply@linkedin.com": [100, 101]},
        messages={100: raw, 101: raw},
    )
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        outcome = gmail_imap.fetch_new_messages(fake_config, state)
    assert outcome.result == gmail_imap.TestResult.SUCCESS
    assert len(outcome.messages) == 2
    assert outcome.messages[0][0] == "jobalerts-noreply@linkedin.com"
    assert outcome.new_uid == 101


def test_fetch_uidvalidity_change_triggers_cold_restart(fake_config, state_path):
    state = gmail_imap.GmailState(last_uid=12345, last_uidvalidity=11111)
    fake_client = _make_fake_imap_client(
        uidvalidity=22222,  # changed
        search_results={"jobalerts-noreply@linkedin.com": [50, 51]},
        messages={
            50: b"From: jobalerts-noreply@linkedin.com\r\n\r\n",
            51: b"From: jobalerts-noreply@linkedin.com\r\n\r\n",
        },
    )
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        outcome = gmail_imap.fetch_new_messages(fake_config, state)
    search_calls = [c for c in fake_client.uid.call_args_list if c.args[0] == "SEARCH"]
    args = " ".join(a.decode() if isinstance(a, bytes) else a for a in search_calls[0].args[1:])
    assert "SINCE" in args  # cold-start fallback uses SINCE
    assert outcome.new_uidvalidity == 22222


def test_fetch_coldstart_uses_30_day_window(fake_config, state_path):
    """Cold-start SINCE date must be exactly _COLDSTART_WINDOW_DAYS ago (#370).

    Bounds the initial sync so a long-lived inbox with years of LinkedIn /
    Indeed / ZipRecruiter alerts doesn't ingest thousands of stale rows on
    first authorize.
    """
    from datetime import UTC, datetime, timedelta

    state = gmail_imap.GmailState(last_uid=0, last_uidvalidity=0)  # cold
    fake_client = _make_fake_imap_client(
        uidvalidity=99999,
        search_results={"jobalerts-noreply@linkedin.com": []},
        messages={},
    )
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        gmail_imap.fetch_new_messages(fake_config, state)
    search_calls = [c for c in fake_client.uid.call_args_list if c.args[0] == "SEARCH"]
    args = " ".join(a.decode() if isinstance(a, bytes) else a for a in search_calls[0].args[1:])
    expected = (datetime.now(UTC) - timedelta(days=gmail_imap._COLDSTART_WINDOW_DAYS)).strftime("%d-%b-%Y")
    assert f'SINCE "{expected}"' in args
    assert gmail_imap._COLDSTART_WINDOW_DAYS == 30


def test_fetch_steady_state_does_not_use_since(fake_config, state_path):
    """Steady-state SEARCH must use UID range only, no SINCE — otherwise
    the cold-start widening would silently leak into normal operation."""
    state = gmail_imap.GmailState(last_uid=12345, last_uidvalidity=22222)
    fake_client = _make_fake_imap_client(
        uidvalidity=22222,  # matches → not cold-start
        search_results={"jobalerts-noreply@linkedin.com": []},
        messages={},
    )
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        gmail_imap.fetch_new_messages(fake_config, state)
    search_calls = [c for c in fake_client.uid.call_args_list if c.args[0] == "SEARCH"]
    args = " ".join(a.decode() if isinstance(a, bytes) else a for a in search_calls[0].args[1:])
    assert "SINCE" not in args
    assert "UID 12346:*" in args


# ── fetch_gmail_jobs integration tests migrated to tests/test_gmail_adapter.py
# under #410.4 — they now exercise GmailLinkedInAdapter directly. The thin
# `fetch_gmail_jobs(since_days=None)` wrapper kept for triage/orchestrator
# compat is covered indirectly by the adapter tests.
