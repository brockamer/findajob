"""send() must not deliver anywhere when no ntfy topic is configured.

Before this, an unset NTFY_TOPIC fell back to a fixed literal topic on the
public ntfy service, so notification content went to a topic anyone could
subscribe to. With no topic, delivery is skipped, the dashboard row is still
written as ``in_app_only``, and one ``ntfy_unconfigured`` event is logged per
process.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from findajob.notifications import ntfy


def _build_db(db_path: Path) -> None:
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
def no_topic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "pipeline.db"
    _build_db(db)
    monkeypatch.setattr(ntfy, "DB_PATH", str(db))
    monkeypatch.setattr(ntfy, "BASE", str(tmp_path))
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    monkeypatch.setattr(ntfy, "load_env", lambda: {})
    monkeypatch.setattr(ntfy, "_unconfigured_logged", False, raising=False)
    ntfy._runtime.cache_clear()
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(ntfy, "log_event", lambda name, **kw: events.append((name, kw)), raising=False)
    calls: list[tuple] = []

    def _fail_run(*a, **kw):
        calls.append(a)
        raise AssertionError("no HTTP request may be made when NTFY_TOPIC is unset")

    monkeypatch.setattr(subprocess, "run", _fail_run)
    yield db, events, calls
    ntfy._runtime.cache_clear()


def _rows(db: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM notifications ORDER BY id").fetchall()
    conn.close()
    return rows


def test_send_without_topic_makes_no_request(no_topic):
    db, _events, calls = no_topic
    ntfy.send("Job at Acme", "/prep/acme", kind="prep_briefing_ready")
    assert calls == []


def test_send_without_topic_persists_in_app_only_row(no_topic):
    db, _events, _calls = no_topic
    ntfy.send("Job at Acme", "/prep/acme", kind="prep_briefing_ready")
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["delivery_status"] == "in_app_only"
    assert rows[0]["title"] == "Job at Acme"


def test_send_without_topic_logs_once_per_process(no_topic):
    _db, events, _calls = no_topic
    ntfy.send("one", "a")
    ntfy.send("two", "b")
    assert [name for name, _ in events].count("ntfy_unconfigured") == 1


def test_blank_topic_counts_as_unset(no_topic, monkeypatch):
    _db, _events, calls = no_topic
    monkeypatch.setenv("NTFY_TOPIC", "   ")
    ntfy._runtime.cache_clear()
    ntfy.send("t", "b")
    assert calls == []


def test_send_with_topic_still_posts(no_topic, monkeypatch):
    db, events, _calls = no_topic
    monkeypatch.setenv("NTFY_TOPIC", "my-private-topic-123")
    ntfy._runtime.cache_clear()
    seen: list[list[str]] = []

    class _Result:
        returncode = 0
        stderr = b""

    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: seen.append(argv) or _Result())
    ntfy.send("t", "b")
    assert len(seen) == 1
    assert "https://ntfy.sh/my-private-topic-123" in seen[0]
    assert _rows(db)[0]["delivery_status"] == "sent"
    assert events == []
