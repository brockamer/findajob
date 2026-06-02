"""Unit tests for scripts/watchdog.py — stuck-task cleanup.

After M6 the watchdog reads ``background_tasks`` rows instead of
``jobs.stage_updated`` heuristics. Per-kind timeouts come from
:data:`findajob.background_tasks.KIND_TIMEOUT_MINUTES`.
"""

import json
import sqlite3
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from findajob import actions, audit
from findajob.background_tasks import KIND_TIMEOUT_MINUTES, record_start
from findajob.db.migrate import apply_pending

# scripts/ isn't on sys.path by default; tests need watchdog importable.
SCRIPTS = Path(__file__).parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import watchdog  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    """Open a tmp DB seeded by the production migration runner.

    Pre-M6 this fixture used a hand-written schema that lacked
    ``background_tasks``. Post-M6 we use the same runner production
    uses so the watchdog sees the real schema.
    """
    db_path = tmp_path / "wd.db"
    conn = sqlite3.connect(str(db_path))
    apply_pending(conn)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _patch_log(tmp_path, monkeypatch):
    log_path = tmp_path / "events.jsonl"
    monkeypatch.setattr(audit, "LOG_PATH", str(log_path))
    monkeypatch.setattr(actions, "BASE", str(tmp_path))
    return log_path


def _read_events(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def _insert_job(conn, *, stage: str = "prep_in_progress") -> str:
    """Insert a jobs row and return its id."""
    job_id = str(uuid.uuid4())[:8]
    stage_updated = datetime.now(UTC).isoformat()
    conn.execute(
        """INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, stage_updated)
           VALUES (?, ?, ?, 'Ops', 'Acme', 'manual', ?, ?)""",
        (job_id, f"fp_{job_id}", f"https://example.com/{job_id}", stage, stage_updated),
    )
    conn.commit()
    return job_id


def _backdate_task(conn, task_id: int, *, minutes_ago: int) -> None:
    """Push a background_tasks row's started_at into the past so find_stuck sees it."""
    cutoff = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE background_tasks SET started_at=? WHERE id=?", (cutoff, task_id))
    conn.commit()


# ── reap_prep ────────────────────────────────────────────────────────────


def test_reap_prep_marks_failed_and_resets_jobs_stage(db, _patch_log):
    """A stuck prep background_tasks row past the kind timeout → both
    the row is marked failed AND the corresponding jobs.stage rolls
    back to scored via reset_prep_to_scored."""
    job_id = _insert_job(db, stage="prep_in_progress")
    task_id = record_start(db, job_id=job_id, kind="prep", pid=99)
    _backdate_task(db, task_id, minutes_ago=KIND_TIMEOUT_MINUTES["prep"] + 1)

    count = watchdog.reap_prep(db)

    assert count == 1
    job_stage = db.execute("SELECT stage FROM jobs WHERE id=?", (job_id,)).fetchone()["stage"]
    assert job_stage == "scored"
    task_status = db.execute("SELECT status FROM background_tasks WHERE id=?", (task_id,)).fetchone()["status"]
    assert task_status == "failed"


def test_reap_prep_leaves_fresh_rows_alone(db, _patch_log):
    """A prep row inside the timeout window is not reaped."""
    job_id = _insert_job(db, stage="prep_in_progress")
    task_id = record_start(db, job_id=job_id, kind="prep")
    # default started_at = now → fresh

    count = watchdog.reap_prep(db)

    assert count == 0
    assert db.execute("SELECT status FROM background_tasks WHERE id=?", (task_id,)).fetchone()["status"] == "running"
    assert db.execute("SELECT stage FROM jobs WHERE id=?", (job_id,)).fetchone()["stage"] == "prep_in_progress"


def test_reap_prep_does_not_touch_other_kinds(db, _patch_log):
    """A stuck interview_prep row is not reaped by reap_prep."""
    job_id = _insert_job(db)
    task_id = record_start(db, job_id=job_id, kind="interview_prep")
    _backdate_task(db, task_id, minutes_ago=KIND_TIMEOUT_MINUTES["interview_prep"] + 1)

    count = watchdog.reap_prep(db)

    assert count == 0
    assert db.execute("SELECT status FROM background_tasks WHERE id=?", (task_id,)).fetchone()["status"] == "running"


def test_reap_prep_writes_audit_log_entry(db, _patch_log):
    """The stage transition still routes through findajob.actions, so
    the audit_log entry is written. Ensures the operator-visible
    history continues to record watchdog-driven resets."""
    job_id = _insert_job(db, stage="prep_in_progress")
    task_id = record_start(db, job_id=job_id, kind="prep")
    _backdate_task(db, task_id, minutes_ago=120)

    watchdog.reap_prep(db)

    audit_row = db.execute(
        "SELECT old_value, new_value FROM audit_log WHERE job_id=? AND field_changed='stage'",
        (job_id,),
    ).fetchone()
    assert audit_row is not None
    assert audit_row["old_value"] == "prep_in_progress"
    assert audit_row["new_value"] == "scored"


# ── reap_interview_prep ──────────────────────────────────────────────────


def test_reap_interview_prep_marks_failed_only(db, _patch_log):
    """interview_prep doesn't move jobs.stage to a transient state, so
    the watchdog only marks the row failed; jobs.stage is untouched."""
    job_id = _insert_job(db, stage="interview")
    task_id = record_start(db, job_id=job_id, kind="interview_prep")
    _backdate_task(db, task_id, minutes_ago=KIND_TIMEOUT_MINUTES["interview_prep"] + 1)

    count = watchdog.reap_interview_prep(db)

    assert count == 1
    task_status = db.execute("SELECT status FROM background_tasks WHERE id=?", (task_id,)).fetchone()["status"]
    assert task_status == "failed"
    job_stage = db.execute("SELECT stage FROM jobs WHERE id=?", (job_id,)).fetchone()["stage"]
    assert job_stage == "interview"


# ── reap_speculative_research ────────────────────────────────────────────


def test_reap_speculative_research_marks_both_surfaces(db, _patch_log):
    """The two-surface update: background_tasks row → failed AND
    speculative_requests row → status='failed' with error_message."""
    cur = db.execute("INSERT INTO speculative_requests (company, status) VALUES ('AcmeCorp', 'researching')")
    request_id = cur.lastrowid
    db.commit()

    task_id = record_start(db, job_id=str(request_id), kind="speculative_research")
    _backdate_task(db, task_id, minutes_ago=KIND_TIMEOUT_MINUTES["speculative_research"] + 1)

    count = watchdog.reap_speculative_research(db)

    assert count == 1
    assert db.execute("SELECT status FROM background_tasks WHERE id=?", (task_id,)).fetchone()["status"] == "failed"
    spec_row = db.execute("SELECT status, error_message FROM speculative_requests WHERE id=?", (request_id,)).fetchone()
    assert spec_row["status"] == "failed"
    assert "timed out" in (spec_row["error_message"] or "").lower()


def test_reap_speculative_research_skips_already_finished(db, _patch_log):
    """A speculative_requests row that completed naturally
    (status='ready_for_review') is not clobbered even if its
    background_tasks row is somehow stuck."""
    cur = db.execute("INSERT INTO speculative_requests (company, status) VALUES ('Done', 'ready_for_review')")
    request_id = cur.lastrowid
    db.commit()

    task_id = record_start(db, job_id=str(request_id), kind="speculative_research")
    _backdate_task(db, task_id, minutes_ago=120)

    watchdog.reap_speculative_research(db)

    # background_tasks row is marked failed (the row is genuinely stuck),
    # but speculative_requests stays at ready_for_review (the WHERE guard).
    assert db.execute("SELECT status FROM background_tasks WHERE id=?", (task_id,)).fetchone()["status"] == "failed"
    assert (
        db.execute("SELECT status FROM speculative_requests WHERE id=?", (request_id,)).fetchone()["status"]
        == "ready_for_review"
    )


# ── main() ────────────────────────────────────────────────────────────────


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
    """main() drives all three reapers + the orphan sweep, emits one
    watchdog_run event with per-kind counts."""
    job_id = _insert_job(db, stage="prep_in_progress")
    task_id = record_start(db, job_id=job_id, kind="prep")
    _backdate_task(db, task_id, minutes_ago=120)

    monkeypatch.setattr(watchdog, "connect", lambda *a, **kw: _ConnWrapper(db))

    watchdog.main()

    events = _read_events(_patch_log)
    watchdog_events = [e for e in events if e["event"] == "watchdog_run"]
    assert len(watchdog_events) == 1
    e = watchdog_events[0]
    assert e["stale_reset"] == 1
    assert e["interview_failed"] == 0
    assert e["speculative_failed"] == 0


def test_empty_db_emits_zero_count(db, monkeypatch, _patch_log):
    monkeypatch.setattr(watchdog, "connect", lambda *a, **kw: _ConnWrapper(db))

    watchdog.main()

    events = _read_events(_patch_log)
    watchdog_events = [e for e in events if e["event"] == "watchdog_run"]
    assert len(watchdog_events) == 1
    assert watchdog_events[0]["stale_reset"] == 0


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
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, prep_folder_path) "
        "VALUES (?, 'fp1', 'http://x', 'Director', 'Acme', 'manual', 'materials_drafted', ?)",
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


# ── reap_prep_phase_b (#691) ──────────────────────────────────────────────


def _insert_job_with_folder(conn, *, stage: str, prep_folder_path: str) -> str:
    """Insert a jobs row including prep_folder_path so the Phase B reaper's
    folder-preservation invariant can be exercised."""
    job_id = str(uuid.uuid4())[:8]
    stage_updated = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, stage_updated, prep_folder_path) "
        "VALUES (?, ?, ?, 'Ops', 'Acme', 'manual', ?, ?, ?)",
        (job_id, f"fp_{job_id}", f"https://example.com/{job_id}", stage, stage_updated, prep_folder_path),
    )
    conn.commit()
    return job_id


def test_reap_prep_phase_b_resets_to_briefing_ready(db, _patch_log):
    """A stuck prep_phase_b row past the kind timeout → background_tasks row
    marked failed AND jobs.stage rolls back to briefing_ready (NOT scored).
    Critically, prep_folder_path is preserved so the operator can re-try
    Phase B without re-paying Phase A."""
    job_id = _insert_job_with_folder(db, stage="prep_in_progress", prep_folder_path="/tmp/prep_folder_xyz")
    task_id = record_start(db, job_id=job_id, kind="prep_phase_b", pid=99)
    _backdate_task(db, task_id, minutes_ago=KIND_TIMEOUT_MINUTES["prep_phase_b"] + 1)

    count = watchdog.reap_prep_phase_b(db)

    assert count == 1
    row = db.execute("SELECT stage, prep_folder_path FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["stage"] == "briefing_ready"
    assert row["prep_folder_path"] == "/tmp/prep_folder_xyz", "Phase B reaper must preserve briefing folder"
    task_status = db.execute("SELECT status FROM background_tasks WHERE id=?", (task_id,)).fetchone()["status"]
    assert task_status == "failed"


def test_reap_prep_phase_b_leaves_fresh_rows_alone(db, _patch_log):
    """A prep_phase_b row inside the timeout window is not reaped."""
    job_id = _insert_job_with_folder(db, stage="prep_in_progress", prep_folder_path="/tmp/x")
    task_id = record_start(db, job_id=job_id, kind="prep_phase_b")
    # default started_at = now → fresh

    count = watchdog.reap_prep_phase_b(db)

    assert count == 0
    assert db.execute("SELECT status FROM background_tasks WHERE id=?", (task_id,)).fetchone()["status"] == "running"
    assert db.execute("SELECT stage FROM jobs WHERE id=?", (job_id,)).fetchone()["stage"] == "prep_in_progress"


def test_reap_prep_phase_b_does_not_touch_prep_kind(db, _patch_log):
    """A stuck kind='prep' row is left for reap_prep — different reset target.

    Regression cover for the bug this whole arc avoids: if reap_prep_phase_b
    swept up plain 'prep' rows too, Phase A subprocess crashes would land
    in briefing_ready (with an empty folder) instead of scored.
    """
    job_id = _insert_job_with_folder(db, stage="prep_in_progress", prep_folder_path="/tmp/y")
    task_id = record_start(db, job_id=job_id, kind="prep")
    _backdate_task(db, task_id, minutes_ago=KIND_TIMEOUT_MINUTES["prep"] + 1)

    count = watchdog.reap_prep_phase_b(db)

    assert count == 0
    assert db.execute("SELECT status FROM background_tasks WHERE id=?", (task_id,)).fetchone()["status"] == "running"
    # Stage still prep_in_progress (would become 'scored' under reap_prep, not 'briefing_ready' here)
    assert db.execute("SELECT stage FROM jobs WHERE id=?", (job_id,)).fetchone()["stage"] == "prep_in_progress"


def test_reap_prep_phase_b_writes_audit_log_entry(db, _patch_log):
    """The phase-B-specific transition is recorded so operator-visible
    history distinguishes 'Phase A crashed' from 'Phase B crashed'."""
    job_id = _insert_job_with_folder(db, stage="prep_in_progress", prep_folder_path="/tmp/z")
    task_id = record_start(db, job_id=job_id, kind="prep_phase_b")
    _backdate_task(db, task_id, minutes_ago=120)

    watchdog.reap_prep_phase_b(db)

    audit_row = db.execute(
        "SELECT old_value, new_value FROM audit_log WHERE job_id=? AND field_changed='stage'",
        (job_id,),
    ).fetchone()
    assert audit_row is not None
    assert audit_row["old_value"] == "prep_in_progress"
    assert audit_row["new_value"] == "briefing_ready"


# ── reap_briefing_ready_stale (#691) ──────────────────────────────────────


def _backdate_stage(conn, job_id: str, *, hours_ago: int) -> None:
    """Push a jobs row's stage_updated into the past so the briefing-stale
    reaper sees it as overdue."""
    past = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
    conn.execute("UPDATE jobs SET stage_updated=? WHERE id=?", (past, job_id))
    conn.commit()


def test_reap_briefing_ready_stale_resets_after_48h(db, _patch_log):
    """A briefing_ready job whose stage_updated is older than the 48h
    ceiling resets to scored, preserving prep_folder_path so a re-flag
    resurfaces the existing briefing."""
    job_id = _insert_job_with_folder(db, stage="briefing_ready", prep_folder_path="/tmp/briefing_xyz")
    _backdate_stage(db, job_id, hours_ago=watchdog.BRIEFING_READY_STALE_AGE_HOURS + 1)

    count = watchdog.reap_briefing_ready_stale(db)

    assert count == 1
    row = db.execute("SELECT stage, prep_folder_path FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["stage"] == "scored"
    assert row["prep_folder_path"] == "/tmp/briefing_xyz", "48h reaper must preserve the briefing folder"


def test_reap_briefing_ready_stale_leaves_fresh_rows_alone(db, _patch_log):
    """A briefing_ready job inside the 48h decision window is not reaped."""
    job_id = _insert_job_with_folder(db, stage="briefing_ready", prep_folder_path="/tmp/fresh")
    _backdate_stage(db, job_id, hours_ago=watchdog.BRIEFING_READY_STALE_AGE_HOURS - 2)

    count = watchdog.reap_briefing_ready_stale(db)

    assert count == 0
    assert db.execute("SELECT stage FROM jobs WHERE id=?", (job_id,)).fetchone()["stage"] == "briefing_ready"


def test_reap_briefing_ready_stale_ignores_other_stages(db, _patch_log):
    """Old jobs at other stages (scored, materials_drafted, etc.) are
    untouched — the 48h reset is specific to the awaiting-decision stage."""
    for stage in ("scored", "materials_drafted", "prep_in_progress"):
        job_id = _insert_job_with_folder(db, stage=stage, prep_folder_path=f"/tmp/{stage}")
        _backdate_stage(db, job_id, hours_ago=watchdog.BRIEFING_READY_STALE_AGE_HOURS + 24)

    count = watchdog.reap_briefing_ready_stale(db)

    assert count == 0


def test_reap_briefing_ready_stale_writes_audit_log_entry(db, _patch_log):
    job_id = _insert_job_with_folder(db, stage="briefing_ready", prep_folder_path="/tmp/audit")
    _backdate_stage(db, job_id, hours_ago=watchdog.BRIEFING_READY_STALE_AGE_HOURS + 1)

    watchdog.reap_briefing_ready_stale(db)

    audit_row = db.execute(
        "SELECT old_value, new_value FROM audit_log WHERE job_id=? AND field_changed='stage'",
        (job_id,),
    ).fetchone()
    assert audit_row is not None
    assert audit_row["old_value"] == "briefing_ready"
    assert audit_row["new_value"] == "scored"


def test_main_emits_phase_b_and_briefing_stale_counts(db, monkeypatch, _patch_log):
    """main()'s watchdog_run event carries per-kind counts so daily-health
    log scrubs can spot a runaway Phase B or operator-decision backlog."""
    monkeypatch.setattr(watchdog, "connect", lambda *a, **kw: _ConnWrapper(db))

    # Stuck Phase B
    job_b = _insert_job_with_folder(db, stage="prep_in_progress", prep_folder_path="/tmp/b")
    task_b = record_start(db, job_id=job_b, kind="prep_phase_b")
    _backdate_task(db, task_b, minutes_ago=KIND_TIMEOUT_MINUTES["prep_phase_b"] + 1)

    # Stale briefing_ready
    job_br = _insert_job_with_folder(db, stage="briefing_ready", prep_folder_path="/tmp/br")
    _backdate_stage(db, job_br, hours_ago=watchdog.BRIEFING_READY_STALE_AGE_HOURS + 1)

    watchdog.main()

    events = _read_events(_patch_log)
    e = next(ev for ev in events if ev["event"] == "watchdog_run")
    assert e["prep_phase_b_failed"] == 1
    assert e["briefing_ready_stale_reset"] == 1


# ── reap_podcast ────────────────────────────────────────────────────────


def test_reap_podcast_marks_failed_only(db, _patch_log):
    """Podcast doesn't move jobs.stage to a transient state, so the
    watchdog only marks the background_tasks row failed — unblocking the
    per-job duplicate guard for retry."""
    job_id = _insert_job(db, stage="interview")
    task_id = record_start(db, job_id=job_id, kind="podcast")
    _backdate_task(db, task_id, minutes_ago=KIND_TIMEOUT_MINUTES["podcast"] + 1)

    count = watchdog.reap_podcast(db)

    assert count == 1
    task_status = db.execute("SELECT status FROM background_tasks WHERE id=?", (task_id,)).fetchone()["status"]
    assert task_status == "failed"
    job_stage = db.execute("SELECT stage FROM jobs WHERE id=?", (job_id,)).fetchone()["stage"]
    assert job_stage == "interview"


def test_reap_podcast_leaves_fresh_rows_alone(db, _patch_log):
    job_id = _insert_job(db, stage="interview")
    task_id = record_start(db, job_id=job_id, kind="podcast")

    count = watchdog.reap_podcast(db)

    assert count == 0
    task_status = db.execute("SELECT status FROM background_tasks WHERE id=?", (task_id,)).fetchone()["status"]
    assert task_status == "running"


def test_reap_podcast_skips_already_finished(db, _patch_log):
    from findajob.background_tasks import record_succeeded

    job_id = _insert_job(db, stage="interview")
    task_id = record_start(db, job_id=job_id, kind="podcast")
    record_succeeded(db, task_id)
    _backdate_task(db, task_id, minutes_ago=KIND_TIMEOUT_MINUTES["podcast"] + 1)

    count = watchdog.reap_podcast(db)

    assert count == 0


# ── reap_study_materials (#873) ───────────────────────────────────────────


def test_reap_study_materials_marks_both_kinds_failed(db, _patch_log):
    """Stuck study_guide / flashcards rows are marked failed (no stage reset),
    unblocking the per-job duplicate guard so the materials-page button stops
    showing 'Generating…' forever after a worker dies mid-generation."""
    job_id = _insert_job(db, stage="interview")
    sg_task = record_start(db, job_id=job_id, kind="study_guide")
    fc_task = record_start(db, job_id=job_id, kind="flashcards")
    _backdate_task(db, sg_task, minutes_ago=KIND_TIMEOUT_MINUTES["study_guide"] + 1)
    _backdate_task(db, fc_task, minutes_ago=KIND_TIMEOUT_MINUTES["flashcards"] + 1)

    count = watchdog.reap_study_materials(db)

    assert count == 2
    for task_id in (sg_task, fc_task):
        status = db.execute("SELECT status FROM background_tasks WHERE id=?", (task_id,)).fetchone()["status"]
        assert status == "failed"
    job_stage = db.execute("SELECT stage FROM jobs WHERE id=?", (job_id,)).fetchone()["stage"]
    assert job_stage == "interview"


def test_reap_study_materials_leaves_fresh_rows_alone(db, _patch_log):
    job_id = _insert_job(db, stage="interview")
    sg_task = record_start(db, job_id=job_id, kind="study_guide")

    count = watchdog.reap_study_materials(db)

    assert count == 0
    status = db.execute("SELECT status FROM background_tasks WHERE id=?", (sg_task,)).fetchone()["status"]
    assert status == "running"
