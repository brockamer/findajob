"""Unit tests for GmailLinkedInAdapter (#410.4).

Migrated from tests/test_gmail_imap.py:436-543 (the four `fetch_gmail_jobs`
integration tests) to exercise the adapter directly per AC #8. The IMAP
auth-streak counter at AUTH_FAILED is the hot-zone behavior — extra
regression coverage included for the streak=3 transition AND the
streak=4 no-re-fire invariant.

Fixtures construct GmailConfig / GmailState dataclass instances and use
gmail_imap.save_config / save_state rather than writing JSON literals,
so the operator's local pre-commit PII hook (which guards against
config/gmail.json content leaks via JSON-key-shape regex) does not flag
this file. The on-disk JSON written by save_* is identical to the
literal-JSON approach the original tests used.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from findajob import gmail_imap
from findajob.fetchers.adapters.gmail import GmailLinkedInAdapter


@pytest.fixture
def cfg_path(tmp_path: Path, monkeypatch):
    p = tmp_path / "gmail.json"
    monkeypatch.setattr(gmail_imap, "GMAIL_CONFIG_PATH", str(p))
    return p


@pytest.fixture
def state_path(tmp_path: Path, monkeypatch):
    p = tmp_path / "gmail_state.json"
    monkeypatch.setattr(gmail_imap, "GMAIL_STATE_PATH", str(p))
    return p


def _save_valid_config() -> None:
    """Persist a minimal valid GmailConfig. App-password value is a bogus
    16-char alphanumeric placeholder — load_config requires exactly 16 chars."""
    gmail_imap.save_config(
        gmail_imap.GmailConfig(
            address="user@gmail.com",
            app_password="abcdefghijklmnop",
            sender_allowlist=["jobalerts-noreply@linkedin.com"],
            configured_at="2026-04-30T00:00:00Z",
        )
    )


def _save_state(*, streak: int = 0, uid: int = 0) -> None:
    gmail_imap.save_state(
        replace(
            gmail_imap.GmailState(),
            auth_failure_streak=streak,
            last_uid=uid,
        )
    )


# ───────────────────── is_configured ─────────────────────


def test_is_configured_true_when_config_file_exists(cfg_path) -> None:
    _save_valid_config()
    assert GmailLinkedInAdapter().is_configured() is True


def test_is_configured_false_when_config_file_missing(cfg_path) -> None:
    assert not cfg_path.exists()
    assert GmailLinkedInAdapter().is_configured() is False


# ───────────────────── fetch() — preserved from test_gmail_imap.py:436-543 ─────────────────────


def test_fetch_returns_empty_when_unconfigured(cfg_path, monkeypatch) -> None:
    """Off state: no config file → adapter.fetch() returns []. No exception."""
    assert not cfg_path.exists()
    assert GmailLinkedInAdapter().fetch([]) == []


def test_fetch_logs_skipped_when_unconfigured(cfg_path, monkeypatch) -> None:
    """Patch target is the adapter's `log_event` binding, not `fetchers.log_event`
    — adapter imports log_event directly from `findajob.audit` so the previous
    test's patch site no longer applies."""
    from findajob.fetchers.adapters import gmail as _gmail_mod

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(_gmail_mod, "log_event", lambda evt, **kw: events.append((evt, kw)))
    GmailLinkedInAdapter().fetch([])
    assert any(e == "gmail_skipped" for e, _ in events)


def test_fetch_increments_streak_on_auth_failure_and_ntfys_at_three(cfg_path, state_path, monkeypatch) -> None:
    """AUTH_FAILED at streak=2 → streak goes to 3; ntfy fires once."""
    _save_valid_config()
    _save_state(streak=2)

    fake_outcome = gmail_imap.FetchOutcome(result=gmail_imap.TestResult.AUTH_FAILED)
    monkeypatch.setattr(gmail_imap, "fetch_new_messages", lambda *a, **k: fake_outcome)

    sent: list[str] = []
    monkeypatch.setattr(
        "findajob.fetchers.notify_send_raw",
        lambda msg: sent.append(msg),
        raising=False,
    )

    GmailLinkedInAdapter().fetch([])
    new_state = gmail_imap.load_state()
    assert new_state.auth_failure_streak == 3
    assert len(sent) == 1
    assert "Gmail login failed" in sent[0]


def test_fetch_does_not_refire_ntfy_after_streak_passes_three(cfg_path, state_path, monkeypatch) -> None:
    """Hot-zone regression test (advisor add): once streak hits 3 the ntfy fires
    exactly once. AUTH_FAILED at streak=3 → streak goes to 4 but ntfy MUST NOT
    re-fire. Locks in the `if new_streak == 3:` exact-equality intent so a
    future "fix" to `>=` fails CI loudly rather than silently spamming the
    operator every 30 minutes."""
    _save_valid_config()
    _save_state(streak=3)

    fake_outcome = gmail_imap.FetchOutcome(result=gmail_imap.TestResult.AUTH_FAILED)
    monkeypatch.setattr(gmail_imap, "fetch_new_messages", lambda *a, **k: fake_outcome)

    sent: list[str] = []
    monkeypatch.setattr(
        "findajob.fetchers.notify_send_raw",
        lambda msg: sent.append(msg),
        raising=False,
    )

    GmailLinkedInAdapter().fetch([])
    new_state = gmail_imap.load_state()
    assert new_state.auth_failure_streak == 4
    assert sent == []


def test_fetch_resets_streak_on_success(cfg_path, state_path, monkeypatch) -> None:
    _save_valid_config()
    _save_state(streak=2, uid=100)

    fake_outcome = gmail_imap.FetchOutcome(
        result=gmail_imap.TestResult.SUCCESS,
        messages=[],
        new_uid=200,
        new_uidvalidity=67890,
    )
    monkeypatch.setattr(gmail_imap, "fetch_new_messages", lambda *a, **k: fake_outcome)

    GmailLinkedInAdapter().fetch([])
    new_state = gmail_imap.load_state()
    assert new_state.auth_failure_streak == 0
    assert new_state.last_uid == 200
    assert new_state.last_error is None


def test_fetch_passes_since_days_constructor_arg_through_to_imap(cfg_path, state_path, monkeypatch) -> None:
    """The Protocol's `fetch(queries)` doesn't take since_days — adapter
    accepts it via constructor and forwards to gmail_imap.fetch_new_messages."""
    _save_valid_config()
    _save_state()

    captured: dict = {}

    def _fake_fetch(config, state, since_days=None):
        captured["since_days"] = since_days
        return gmail_imap.FetchOutcome(result=gmail_imap.TestResult.SUCCESS, messages=[], new_uid=1, new_uidvalidity=1)

    monkeypatch.setattr(gmail_imap, "fetch_new_messages", _fake_fetch)

    GmailLinkedInAdapter(since_days=14).fetch([])
    assert captured["since_days"] == 14


# ───────────────────── live_test() ─────────────────────


def test_live_test_success_bucket(cfg_path, monkeypatch) -> None:
    _save_valid_config()
    monkeypatch.setattr(gmail_imap, "test_login", lambda config: gmail_imap.TestResult.SUCCESS)
    result = GmailLinkedInAdapter().live_test([])
    assert result.ok is True
    assert result.bucket == "success"
    assert result.per_query[0].query == "gmail_imap_login"


def test_live_test_auth_bucket_on_auth_failed(cfg_path, monkeypatch) -> None:
    _save_valid_config()
    monkeypatch.setattr(gmail_imap, "test_login", lambda config: gmail_imap.TestResult.AUTH_FAILED)
    result = GmailLinkedInAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "auth"


def test_live_test_network_bucket_on_connection_error(cfg_path, monkeypatch) -> None:
    _save_valid_config()
    monkeypatch.setattr(gmail_imap, "test_login", lambda config: gmail_imap.TestResult.CONNECTION_ERROR)
    result = GmailLinkedInAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "network"


def test_live_test_auth_bucket_on_invalid_config(cfg_path, monkeypatch) -> None:
    """Advisor add: TestResult has 4 values but AC enumerates 3 buckets.
    INVALID_CONFIG (config parses but fails IMAP-side validation) maps to
    `auth` — same UX class as auth-failed; user must fix at /config/gmail/.
    Without an explicit mapping the bucket would fall through to a default
    that crashes or returns the wrong bucket."""
    _save_valid_config()
    monkeypatch.setattr(gmail_imap, "test_login", lambda config: gmail_imap.TestResult.INVALID_CONFIG)
    result = GmailLinkedInAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "auth"


def test_live_test_auth_bucket_when_not_configured(cfg_path) -> None:
    """No config file → live_test returns auth bucket with actionable error
    message, not a crash. Matches the Greenhouse/Ashby/Lever 'no slugs
    configured' UX shape."""
    assert not cfg_path.exists()
    result = GmailLinkedInAdapter().live_test([])
    assert result.ok is False
    assert result.bucket == "auth"
    assert "/config/gmail/" in (result.auth_error or "")
