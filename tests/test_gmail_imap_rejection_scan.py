"""Tests for ``fetch_new_messages_for_rejection_scan`` and the additive
``GmailConfig`` / ``GmailState`` extensions for #362."""

from __future__ import annotations

import json
import stat
from unittest.mock import MagicMock, patch

import pytest

from findajob import gmail_imap


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "gmail.json"
    monkeypatch.setattr(gmail_imap, "GMAIL_CONFIG_PATH", str(p))
    return p


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    p = tmp_path / "gmail_state.json"
    monkeypatch.setattr(gmail_imap, "GMAIL_STATE_PATH", str(p))
    return p


@pytest.fixture
def fake_config():
    return gmail_imap.GmailConfig(
        address="user@gmail.com",
        app_password="abcdefghijklmnop",
        sender_allowlist=["jobalerts-noreply@linkedin.com"],
        configured_at="2026-04-30T00:00:00Z",
        rejection_sender_allowlist=["no-reply@us.greenhouse-mail.io", "no-reply@ashbyhq.com"],
    )


def _make_fake_imap_client(*, uidvalidity, search_results, messages):
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


def test_default_rejection_allowlist_matches_spec_3_1() -> None:
    """The DEFAULT_REJECTION_ALLOWLIST tracks the ATS senders enumerated in
    spec §3.1 that issue distinct rejection emails (Greenhouse, Ashby, Lever).
    Workday-style ``talent.{company}.com`` is matched at the rejection_detector
    layer via suffix and intentionally not enumerated here.
    """
    expected = {
        "no-reply@us.greenhouse-mail.io",
        "no-reply@eu.greenhouse-mail.io",
        "no-reply@ashbyhq.com",
        "no-reply@hire.lever.co",
    }
    assert set(gmail_imap.DEFAULT_REJECTION_ALLOWLIST) == expected


def test_load_config_backwards_compatible_without_rejection_field(cfg_path) -> None:
    """A pre-#362 gmail.json (no rejection_sender_allowlist field) must still load."""
    cfg_path.write_text(
        json.dumps(
            {
                "_schema": 1,
                "address": "user@gmail.com",
                "app_password": "abcdefghijklmnop",
                "sender_allowlist": ["jobalerts-noreply@linkedin.com"],
                "configured_at": "2026-04-30T00:00:00Z",
            }
        )
    )
    cfg = gmail_imap.load_config()
    assert cfg is not None
    assert list(cfg.rejection_sender_allowlist) == list(gmail_imap.DEFAULT_REJECTION_ALLOWLIST)


def test_load_config_with_rejection_field(cfg_path) -> None:
    cfg_path.write_text(
        json.dumps(
            {
                "_schema": 1,
                "address": "user@gmail.com",
                "app_password": "abcdefghijklmnop",
                "sender_allowlist": ["jobalerts-noreply@linkedin.com"],
                "configured_at": "2026-04-30T00:00:00Z",
                "rejection_sender_allowlist": ["no-reply@custom.com"],
            }
        )
    )
    cfg = gmail_imap.load_config()
    assert cfg is not None
    assert cfg.rejection_sender_allowlist == ["no-reply@custom.com"]


def test_load_config_rejects_invalid_rejection_field(cfg_path) -> None:
    cfg_path.write_text(
        json.dumps(
            {
                "_schema": 1,
                "address": "user@gmail.com",
                "app_password": "abcdefghijklmnop",
                "sender_allowlist": ["jobalerts-noreply@linkedin.com"],
                "configured_at": "2026-04-30T00:00:00Z",
                "rejection_sender_allowlist": "not-a-list",
            }
        )
    )
    assert gmail_imap.load_config() is None


def test_save_config_writes_rejection_allowlist(cfg_path) -> None:
    cfg = gmail_imap.GmailConfig(
        address="user@gmail.com",
        app_password="abcdefghijklmnop",
        sender_allowlist=["jobalerts-noreply@linkedin.com"],
        configured_at="2026-04-30T00:00:00Z",
        rejection_sender_allowlist=["no-reply@custom.com"],
    )
    gmail_imap.save_config(cfg)
    payload = json.loads(cfg_path.read_text())
    assert payload["rejection_sender_allowlist"] == ["no-reply@custom.com"]
    mode = stat.S_IMODE(cfg_path.stat().st_mode)
    assert mode == 0o600


def test_load_state_backwards_compatible_without_rejection_keys(state_path) -> None:
    """A pre-#362 gmail_state.json (without rejection keys) must still load."""
    state_path.write_text(
        json.dumps(
            {
                "_schema": 1,
                "last_uid": 100,
                "last_uidvalidity": 200,
                "auth_failure_streak": 0,
            }
        )
    )
    s = gmail_imap.load_state()
    assert s.last_uid == 100
    assert s.rejection_last_uid == 0
    assert s.rejection_backlog_scan_complete is False
    assert s.rejection_backlog_window_days == 0


def test_state_round_trip_preserves_rejection_keys(state_path) -> None:
    s = gmail_imap.GmailState(
        last_uid=12345,
        last_uidvalidity=67890,
        rejection_last_uid=4242,
        rejection_backlog_scan_complete=True,
        rejection_backlog_window_days=60,
    )
    gmail_imap.save_state(s)
    loaded = gmail_imap.load_state()
    assert loaded == s


def test_rejection_scan_uses_rejection_allowlist_not_jobs(fake_config) -> None:
    """The rejection scan must search rejection_sender_allowlist senders, not job-fetch ones."""
    state = gmail_imap.GmailState(rejection_last_uid=0)
    fake_client = _make_fake_imap_client(
        uidvalidity=99,
        search_results={"no-reply@us.greenhouse-mail.io": [], "no-reply@ashbyhq.com": []},
        messages={},
    )
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        gmail_imap.fetch_new_messages_for_rejection_scan(fake_config, state)
    search_calls = [c for c in fake_client.uid.call_args_list if c.args[0] == "SEARCH"]
    args_concat = " ".join(
        " ".join(a.decode() if isinstance(a, bytes) else a for a in c.args[1:]) for c in search_calls
    )
    assert "no-reply@us.greenhouse-mail.io" in args_concat
    assert "no-reply@ashbyhq.com" in args_concat
    assert "jobalerts-noreply@linkedin.com" not in args_concat
    assert len(search_calls) == 2


def test_rejection_scan_uses_uid_search_with_rejection_last_uid_plus_one(fake_config) -> None:
    state = gmail_imap.GmailState(rejection_last_uid=500)
    fake_client = _make_fake_imap_client(
        uidvalidity=99,
        search_results={"no-reply@us.greenhouse-mail.io": [], "no-reply@ashbyhq.com": []},
        messages={},
    )
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        gmail_imap.fetch_new_messages_for_rejection_scan(fake_config, state)
    search_calls = [c for c in fake_client.uid.call_args_list if c.args[0] == "SEARCH"]
    for call in search_calls:
        args_str = " ".join(a.decode() if isinstance(a, bytes) else a for a in call.args[1:])
        assert "501:*" in args_str  # rejection_last_uid + 1


def test_rejection_scan_uses_body_peek(fake_config) -> None:
    state = gmail_imap.GmailState(rejection_last_uid=0)
    raw = b"From: no-reply@us.greenhouse-mail.io\r\n\r\nbody"
    fake_client = _make_fake_imap_client(
        uidvalidity=99,
        search_results={"no-reply@us.greenhouse-mail.io": [10], "no-reply@ashbyhq.com": []},
        messages={10: raw},
    )
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        gmail_imap.fetch_new_messages_for_rejection_scan(fake_config, state)
    fetch_calls = [c for c in fake_client.uid.call_args_list if c.args[0] == "FETCH"]
    assert all("BODY.PEEK[]" in str(c.args) for c in fetch_calls)


def test_rejection_scan_advances_max_uid_monotonically(fake_config) -> None:
    state = gmail_imap.GmailState(rejection_last_uid=10)
    raw = b"From: x\r\n\r\n"
    fake_client = _make_fake_imap_client(
        uidvalidity=99,
        search_results={"no-reply@us.greenhouse-mail.io": [50, 80], "no-reply@ashbyhq.com": [70]},
        messages={50: raw, 70: raw, 80: raw},
    )
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        outcome = gmail_imap.fetch_new_messages_for_rejection_scan(fake_config, state)
    assert outcome.new_uid == 80


def test_rejection_scan_with_since_days_uses_since_clause(fake_config) -> None:
    """First-run backlog scan from M-stage 4 passes ``since_days`` to widen the window."""
    state = gmail_imap.GmailState(rejection_last_uid=0)
    fake_client = _make_fake_imap_client(
        uidvalidity=99,
        search_results={"no-reply@us.greenhouse-mail.io": [], "no-reply@ashbyhq.com": []},
        messages={},
    )
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        gmail_imap.fetch_new_messages_for_rejection_scan(fake_config, state, since_days=60)
    search_calls = [c for c in fake_client.uid.call_args_list if c.args[0] == "SEARCH"]
    args_concat = " ".join(
        " ".join(a.decode() if isinstance(a, bytes) else a for a in c.args[1:]) for c in search_calls
    )
    assert "SINCE" in args_concat


def test_rejection_scan_logout_called_on_exception(fake_config) -> None:
    fake_client = MagicMock()
    fake_client.login.return_value = ("OK", [])
    fake_client.select.side_effect = RuntimeError("boom")
    with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
        outcome = gmail_imap.fetch_new_messages_for_rejection_scan(fake_config, gmail_imap.GmailState())
    fake_client.logout.assert_called_once()
    assert outcome.result == gmail_imap.TestResult.CONNECTION_ERROR


def test_rejection_scan_emits_pipeline_events(fake_config) -> None:
    """Each scanned message logs ``rejection_email_scanned``; cycle ends with
    ``rejection_scan_completed``."""
    state = gmail_imap.GmailState(rejection_last_uid=0)
    raw = b"From: no-reply@us.greenhouse-mail.io\r\n\r\nbody"
    fake_client = _make_fake_imap_client(
        uidvalidity=99,
        search_results={"no-reply@us.greenhouse-mail.io": [10, 20], "no-reply@ashbyhq.com": []},
        messages={10: raw, 20: raw},
    )

    captured: list[tuple[str, dict]] = []

    def fake_log(event, **kwargs):
        captured.append((event, kwargs))

    with patch("findajob.gmail_imap.log_event", side_effect=fake_log):
        with patch("findajob.gmail_imap.imaplib.IMAP4_SSL", return_value=fake_client):
            gmail_imap.fetch_new_messages_for_rejection_scan(fake_config, state)

    event_names = [e for e, _ in captured]
    assert event_names.count("rejection_email_scanned") == 2
    assert "rejection_scan_completed" in event_names
    completed_kwargs = next(kw for e, kw in captured if e == "rejection_scan_completed")
    assert completed_kwargs["count"] == 2
    assert completed_kwargs["max_uid"] == 20
