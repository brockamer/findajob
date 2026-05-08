"""Unit tests for scripts/watchdog.py — stale prep_in_progress cleanup."""

import json
import sqlite3
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from findajob import actions, audit

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
    updated_at TEXT DEFAULT (datetime('now')),
    synthetic INTEGER NOT NULL DEFAULT 0
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
    monkeypatch.setattr(audit, "LOG_PATH", str(log_path))
    monkeypatch.setattr(actions, "BASE", str(tmp_path))
    return log_path


def _insert(conn, *, stage, minutes_ago):
    """Insert a row with stage_updated in Python ISO format (the production writer format).

    Web handlers write stage_updated via datetime.now(UTC).isoformat(), producing
    "YYYY-MM-DDTHH:MM:SS+00:00". Tests must mirror that format — fixtures that
    used SQLite's datetime('now', ...) (space-separated, no TZ) previously hid
    format-mismatch bugs in reader queries.
    """
    job_id = str(uuid.uuid4())[:8]
    stage_updated = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    conn.execute(
        """INSERT INTO jobs (id, fingerprint, url, title, company, stage, stage_updated)
           VALUES (?, ?, ?, 'Ops', 'Acme', ?, ?)""",
        (job_id, f"fp_{job_id}", f"https://example.com/{job_id}", stage, stage_updated),
    )
    conn.commit()
    return job_id


def _read_events(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def test_resets_stale_row_only(db, _patch_log):
    stale_id = _insert(db, stage="prep_in_progress", minutes_ago=120)
    fresh_id = _insert(db, stage="prep_in_progress", minutes_ago=5)
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
    stale_id = _insert(db, stage="prep_in_progress", minutes_ago=120)

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
    _insert(db, stage="prep_in_progress", minutes_ago=120)
    _insert(db, stage="prep_in_progress", minutes_ago=90)
    _insert(db, stage="scored", minutes_ago=60 * 24)  # unrelated row

    monkeypatch.setattr(watchdog.sqlite3, "connect", lambda *a, **kw: _ConnWrapper(db))

    watchdog.main()

    events = _read_events(_patch_log)
    watchdog_events = [e for e in events if e["event"] == "watchdog_run"]
    assert len(watchdog_events) == 1
    assert watchdog_events[0]["stale_reset"] == 2


def test_resets_iso_t_timestamps_on_same_day(db, _patch_log):
    """Regression: stuck prep rows written in ISO-T format (the production format)
    must be reset even when they fall on the same calendar date as `now`.
    Pre-fix, `stage_updated < datetime('now', '-60 minutes')` was always false
    for same-day ISO-T rows because lexical T > space at char 10 of the string."""
    stale_id = _insert(db, stage="prep_in_progress", minutes_ago=75)
    fresh_id = _insert(db, stage="prep_in_progress", minutes_ago=10)

    count = watchdog.run_watchdog(db)

    assert count == 1
    assert db.execute("SELECT stage FROM jobs WHERE id=?", (stale_id,)).fetchone()["stage"] == "scored"
    assert db.execute("SELECT stage FROM jobs WHERE id=?", (fresh_id,)).fetchone()["stage"] == "prep_in_progress"


def test_empty_db_emits_zero_count(db, monkeypatch, _patch_log):
    monkeypatch.setattr(watchdog.sqlite3, "connect", lambda *a, **kw: _ConnWrapper(db))

    watchdog.main()

    events = _read_events(_patch_log)
    watchdog_events = [e for e in events if e["event"] == "watchdog_run"]
    assert len(watchdog_events) == 1
    assert watchdog_events[0]["stale_reset"] == 0


# ── B4.T31: speculative-research watchdog ───────────────────────────────


def test_fail_stuck_speculative_marks_failed(db, _patch_log):
    """Speculative requests stuck in 'researching' >10 min get marked failed.
    Fresh requests (<10 min) are untouched."""
    db.executescript("""
        CREATE TABLE speculative_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'researching',
            error_message TEXT,
            submitted_at TEXT NOT NULL
        );
    """)
    # 16 min ago — past the 15-min cutoff
    stale_at = (datetime.now(UTC) - timedelta(minutes=16)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "INSERT INTO speculative_requests (company, status, submitted_at) VALUES (?, 'researching', ?)",
        ("PSIQuantum", stale_at),
    )
    # 5 min ago — fresh, should not be touched
    fresh_at = (datetime.now(UTC) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "INSERT INTO speculative_requests (company, status, submitted_at) VALUES (?, 'researching', ?)",
        ("Recent Co", fresh_at),
    )
    db.commit()

    count = watchdog.fail_stuck_speculative(db)

    assert count == 1
    rows = db.execute("SELECT company, status, error_message FROM speculative_requests ORDER BY id").fetchall()
    stale, fresh = rows[0], rows[1]
    assert stale["status"] == "failed"
    assert "timed out" in (stale["error_message"] or "").lower()
    assert fresh["status"] == "researching"


# ── sweep_orphan_folders ──────────────────────────────────────────────────


def test_sweep_orphan_folders_moves_untracked_old_folder(db, tmp_path, monkeypatch):
    """Folder on disk with no jobs row pointing at it AND mtime > 2h → moved to .stale/."""
    monkeypatch.setattr(watchdog, "BASE", str(tmp_path))
    companies = tmp_path / "companies"
    companies.mkdir()
    orphan = companies / "Acme_Director_Of_Ops_2026-04-23_120000"
    orphan.mkdir()
    # backdate mtime to 3h ago
    old_ts = (datetime.now(UTC) - timedelta(hours=3)).timestamp()
    import os

    os.utime(orphan, (old_ts, old_ts))

    count = watchdog.sweep_orphan_folders(db)

    assert count == 1
    assert not orphan.exists()
    assert (companies / ".stale" / orphan.name).is_dir()


def test_sweep_orphan_folders_skips_in_flight_prep(db, tmp_path, monkeypatch):
    """Fresh folder (mtime < 2h) is left alone — could be an in-flight prep."""
    monkeypatch.setattr(watchdog, "BASE", str(tmp_path))
    companies = tmp_path / "companies"
    companies.mkdir()
    fresh = companies / "Acme_In_Flight_2026-04-30_120000"
    fresh.mkdir()
    # mtime is current time (just created) — well within the 2h grace

    count = watchdog.sweep_orphan_folders(db)

    assert count == 0
    assert fresh.is_dir()
    assert not (companies / ".stale").exists()


def test_sweep_orphan_folders_skips_db_tracked_folder(db, tmp_path, monkeypatch):
    """Folder whose path appears in jobs.prep_folder_path is NOT swept."""
    monkeypatch.setattr(watchdog, "BASE", str(tmp_path))
    companies = tmp_path / "companies"
    companies.mkdir()
    tracked = companies / "Acme_Tracked_2026-04-23_120000"
    tracked.mkdir()
    old_ts = (datetime.now(UTC) - timedelta(hours=3)).timestamp()
    import os

    os.utime(tracked, (old_ts, old_ts))

    db.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, stage, prep_folder_path) "
        "VALUES (?, 'fp1', 'http://x', 'Director', 'Acme', 'materials_drafted', ?)",
        (str(uuid.uuid4()), str(tracked)),
    )
    db.commit()

    count = watchdog.sweep_orphan_folders(db)

    assert count == 0
    assert tracked.is_dir()


def test_sweep_orphan_folders_ignores_underscore_and_dot_dirs(db, tmp_path, monkeypatch):
    """_applied/, _rejected/, .stale/ etc. are stage holders — never swept."""
    monkeypatch.setattr(watchdog, "BASE", str(tmp_path))
    companies = tmp_path / "companies"
    companies.mkdir()
    for name in ("_applied", "_rejected", "_waitlisted", ".stale"):
        (companies / name).mkdir()
        old_ts = (datetime.now(UTC) - timedelta(hours=3)).timestamp()
        import os

        os.utime(companies / name, (old_ts, old_ts))

    count = watchdog.sweep_orphan_folders(db)

    assert count == 0
    for name in ("_applied", "_rejected", "_waitlisted", ".stale"):
        assert (companies / name).is_dir()


def test_sweep_orphan_folders_does_not_clobber_existing_stale_entry(db, tmp_path, monkeypatch):
    """If .stale/ already has a folder with the same name (sweep ran before),
    don't overwrite — log and skip."""
    monkeypatch.setattr(watchdog, "BASE", str(tmp_path))
    companies = tmp_path / "companies"
    companies.mkdir()
    name = "Acme_Dup_2026-04-23_120000"
    orphan = companies / name
    orphan.mkdir()
    (orphan / "marker_new.txt").write_text("new")
    old_ts = (datetime.now(UTC) - timedelta(hours=3)).timestamp()
    import os

    os.utime(orphan, (old_ts, old_ts))
    # Pre-existing .stale entry with the same name
    stale_existing = companies / ".stale" / name
    stale_existing.mkdir(parents=True)
    (stale_existing / "marker_old.txt").write_text("old")

    count = watchdog.sweep_orphan_folders(db)

    assert count == 0  # skipped, not moved
    assert orphan.is_dir()
    # Existing .stale entry unchanged
    assert (stale_existing / "marker_old.txt").read_text() == "old"
    assert not (stale_existing / "marker_new.txt").exists()


def test_sweep_orphan_folders_handles_missing_companies_dir(db, tmp_path, monkeypatch):
    """If companies/ doesn't exist, return 0 without raising."""
    monkeypatch.setattr(watchdog, "BASE", str(tmp_path))
    # companies/ deliberately not created
    assert watchdog.sweep_orphan_folders(db) == 0
