"""notify.py:send() persists notifications rows before/after ntfy POST (#440)."""

from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NOTIFY_PATH = REPO / "scripts" / "notify.py"


def _load_notify_module(db_path: Path):
    spec = importlib.util.spec_from_file_location("notify_persistence_under_test", NOTIFY_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["notify_persistence_under_test"] = mod
    spec.loader.exec_module(mod)
    mod.DB_PATH = str(db_path)
    return mod


def _build_notifications_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sent_at TEXT NOT NULL DEFAULT (datetime('now')),
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'default',
            tags TEXT,
            delivery_status TEXT NOT NULL DEFAULT 'sent',
            delivery_error TEXT,
            cta_url TEXT,
            read_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture
def notify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "pipeline.db"
    _build_notifications_db(db)
    mod = _load_notify_module(db)
    return mod


def _read_rows(db_path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM notifications ORDER BY id ASC").fetchall()
    conn.close()
    return rows


def test_send_persists_row_with_kind(notify, monkeypatch):
    """A successful ntfy POST persists a row tagged with kind + status='sent'."""

    class _Result:
        returncode = 0
        stderr = b""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result())

    notify.send("hello", "world", priority="low", tags="bell", kind="daily_stats")

    rows = _read_rows(notify.DB_PATH)
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "daily_stats"
    assert r["title"] == "hello"
    assert r["body"] == "world"
    assert r["priority"] == "low"
    assert r["tags"] == "bell"
    assert r["delivery_status"] == "sent"
    assert r["delivery_error"] is None


def test_send_persists_row_when_ntfy_fails(notify, monkeypatch):
    """ntfy.sh outage MUST NOT delete the audit row — it flips delivery_status."""

    class _Result:
        returncode = 7  # curl: failed to connect
        stderr = b"curl: (7) Failed to connect to ntfy.sh"

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result())

    notify.send("alert", "the thing happened", kind="health_check")

    rows = _read_rows(notify.DB_PATH)
    assert len(rows) == 1
    r = rows[0]
    assert r["delivery_status"] == "failed"
    assert r["delivery_error"] is not None
    assert "Failed to connect" in r["delivery_error"]


def test_send_default_kind_is_send_raw(notify, monkeypatch):
    class _Result:
        returncode = 0
        stderr = b""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result())

    notify.send("title", "body")

    rows = _read_rows(notify.DB_PATH)
    assert rows[0]["kind"] == "send_raw"


def test_send_returns_row_id(notify, monkeypatch):
    class _Result:
        returncode = 0
        stderr = b""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result())

    rid1 = notify.send("a", "b", kind="daily_stats")
    rid2 = notify.send("c", "d", kind="health_check")
    assert rid1 == 1
    assert rid2 == 2


def test_send_does_not_crash_when_table_missing(tmp_path, monkeypatch):
    """Brand-new stack with no init_db run yet: send() should not crash."""
    db = tmp_path / "pipeline.db"
    sqlite3.connect(db).close()  # empty DB, no tables
    mod = _load_notify_module(db)

    class _Result:
        returncode = 0
        stderr = b""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result())

    rid = mod.send("title", "body", kind="daily_stats")
    assert rid is None  # silent skip, no exception


def test_taxonomy_constant_includes_known_kinds(notify):
    """The closed-set taxonomy must list every kind referenced in production."""
    expected = {
        "daily_stats",
        "apply_reminder",
        "feedback_review",
        "scoreboard",
        "health_check",
        "issues_ping",
        "ci_check",
        "send_raw",
        "discovery_run",
        "gmail_auth_failure",
        "rejection_detected",
    }
    assert set(notify.NOTIFICATION_KINDS) >= expected
