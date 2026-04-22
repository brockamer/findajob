"""Unit tests for scripts/watchdog.py — stale prep_in_progress cleanup."""

import json
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

from findajob import actions, utils

# scripts/ isn't on sys.path by default; tests need watchdog importable.
SCRIPTS = Path(__file__).parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import watchdog  # noqa: E402

SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    stage TEXT DEFAULT 'discovered',
    stage_updated TEXT,
    prep_folder_path TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    field_changed TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at TEXT DEFAULT (datetime('now')),
    changed_by TEXT DEFAULT 'system'
);
"""


@pytest.fixture()
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _patch_log(tmp_path, monkeypatch):
    log_path = tmp_path / "events.jsonl"
    monkeypatch.setattr(utils, "LOG_PATH", str(log_path))
    monkeypatch.setattr(actions, "BASE", str(tmp_path))
    return log_path


def _insert(conn, *, stage, stage_offset):
    """Insert a row with stage_updated = datetime('now', stage_offset)."""
    job_id = str(uuid.uuid4())[:8]
    conn.execute(
        """INSERT INTO jobs (id, fingerprint, url, title, company, stage, stage_updated)
           VALUES (?, ?, ?, 'Ops', 'Acme', ?, datetime('now', ?))""",
        (job_id, f"fp_{job_id}", f"https://example.com/{job_id}", stage, stage_offset),
    )
    conn.commit()
    return job_id


def _read_events(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def test_resets_stale_row_only(db, _patch_log):
    stale_id = _insert(db, stage="prep_in_progress", stage_offset="-2 hours")
    fresh_id = _insert(db, stage="prep_in_progress", stage_offset="-5 minutes")
    fresh_stage_before = db.execute("SELECT stage_updated FROM jobs WHERE id=?", (fresh_id,)).fetchone()[
        "stage_updated"
    ]

    count = watchdog.run_watchdog(db)

    assert count == 1
    assert db.execute("SELECT stage FROM jobs WHERE id=?", (stale_id,)).fetchone()["stage"] == "scored"
    fresh_row = db.execute("SELECT stage, stage_updated FROM jobs WHERE id=?", (fresh_id,)).fetchone()
    assert fresh_row["stage"] == "prep_in_progress"
    assert fresh_row["stage_updated"] == fresh_stage_before


def test_audit_log_records_transition(db):
    stale_id = _insert(db, stage="prep_in_progress", stage_offset="-2 hours")

    watchdog.run_watchdog(db)

    audit = db.execute(
        "SELECT old_value, new_value FROM audit_log WHERE job_id=? AND field_changed='stage'",
        (stale_id,),
    ).fetchone()
    assert audit["old_value"] == "prep_in_progress"
    assert audit["new_value"] == "scored"


class _ConnWrapper:
    """Proxy that forwards to the real connection but no-ops close() so the
    in-memory DB survives main()'s finally clause."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._conn, name)

    def close(self) -> None:
        pass


def test_main_emits_watchdog_run_event(db, monkeypatch, _patch_log):
    _insert(db, stage="prep_in_progress", stage_offset="-2 hours")
    _insert(db, stage="prep_in_progress", stage_offset="-90 minutes")
    _insert(db, stage="scored", stage_offset="-1 day")  # unrelated row

    monkeypatch.setattr(watchdog.sqlite3, "connect", lambda *a, **kw: _ConnWrapper(db))

    watchdog.main()

    events = _read_events(_patch_log)
    watchdog_events = [e for e in events if e["event"] == "watchdog_run"]
    assert len(watchdog_events) == 1
    assert watchdog_events[0]["stale_reset"] == 2


def test_empty_db_emits_zero_count(db, monkeypatch, _patch_log):
    monkeypatch.setattr(watchdog.sqlite3, "connect", lambda *a, **kw: _ConnWrapper(db))

    watchdog.main()

    events = _read_events(_patch_log)
    watchdog_events = [e for e in events if e["event"] == "watchdog_run"]
    assert len(watchdog_events) == 1
    assert watchdog_events[0]["stale_reset"] == 0
