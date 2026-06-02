"""#554 — Tests for ``findajob.background_tasks`` helper module.

Verification approach mirrors ``test_db_migrate.py`` (per advisor's
"observable schema state" framing): each test snapshots the relevant
``background_tasks`` rows before/after a helper call and asserts the
expected delta. Mutation testing on the helper is via the integration
test (writeback contract).

Tests run against an in-memory SQLite DB seeded with the production
schema from ``apply_pending`` so the fixture matches reality.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from findajob.background_tasks import (
    KIND_TIMEOUT_MINUTES,
    TASK_ID_ENV_VAR,
    fetch_by_id,
    find_active_for_subject,
    find_stuck,
    record_failed,
    record_start,
    record_succeeded,
    task_id_from_env,
    writeback_sync,
)
from findajob.db.migrate import apply_pending


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """Open an in-memory DB seeded by the production migration runner."""
    path = tmp_path / "bg.db"
    conn = sqlite3.connect(str(path))
    apply_pending(conn)
    return conn


def test_record_start_inserts_running_row(db: sqlite3.Connection) -> None:
    task_id = record_start(db, job_id="job-uuid-1", kind="prep")
    assert task_id > 0

    row = fetch_by_id(db, task_id)
    assert row is not None
    assert row["job_id"] == "job-uuid-1"
    assert row["kind"] == "prep"
    assert row["status"] == "running"
    assert row["finished_at"] is None
    assert row["error_message"] is None


def test_record_start_pid_optional(db: sqlite3.Connection) -> None:
    task_id = record_start(db, job_id="job-2", kind="interview_prep", pid=42)
    row = fetch_by_id(db, task_id)
    assert row is not None
    assert row["pid"] == 42

    task_id_no_pid = record_start(db, job_id="job-3", kind="interview_prep")
    row_no_pid = fetch_by_id(db, task_id_no_pid)
    assert row_no_pid is not None
    assert row_no_pid["pid"] is None


def test_record_succeeded_closes_row(db: sqlite3.Connection) -> None:
    task_id = record_start(db, job_id="job-4", kind="prep")
    record_succeeded(db, task_id)

    row = fetch_by_id(db, task_id)
    assert row is not None
    assert row["status"] == "succeeded"
    assert row["finished_at"] is not None


def test_record_failed_stores_error_message(db: sqlite3.Connection) -> None:
    task_id = record_start(db, job_id="job-5", kind="speculative_research")
    record_failed(db, task_id, error_message="OpenRouter 429 — rate limit")

    row = fetch_by_id(db, task_id)
    assert row is not None
    assert row["status"] == "failed"
    assert row["finished_at"] is not None
    assert row["error_message"] == "OpenRouter 429 — rate limit"


def test_record_failed_truncates_huge_error_messages(db: sqlite3.Connection) -> None:
    """A 50 KB traceback in a DB row helps no one — truncate to 4000 chars."""
    task_id = record_start(db, job_id="job-6", kind="prep")
    huge = "x" * 100_000
    record_failed(db, task_id, error_message=huge)

    row = fetch_by_id(db, task_id)
    assert row is not None
    assert row["error_message"] is not None
    assert len(row["error_message"]) == 4000


def test_double_record_succeeded_is_idempotent(db: sqlite3.Connection) -> None:
    """Calling record_succeeded twice on the same row is safe — only the
    first call writes. Guards against duplicate writeback paths in
    crash-recovery scenarios."""
    task_id = record_start(db, job_id="job-7", kind="prep")
    record_succeeded(db, task_id)
    finished_first = fetch_by_id(db, task_id)["finished_at"]

    record_succeeded(db, task_id)
    finished_second = fetch_by_id(db, task_id)["finished_at"]

    assert finished_first == finished_second


def test_record_failed_does_not_overwrite_succeeded(db: sqlite3.Connection) -> None:
    """A late record_failed call after record_succeeded must NOT clobber
    the success state. The UPDATE guard ``WHERE status='running'`` enforces
    this — once finished, the row is immutable."""
    task_id = record_start(db, job_id="job-8", kind="prep")
    record_succeeded(db, task_id)
    record_failed(db, task_id, error_message="late panic")

    row = fetch_by_id(db, task_id)
    assert row["status"] == "succeeded"
    assert row["error_message"] is None


def test_fetch_by_id_returns_none_for_missing(db: sqlite3.Connection) -> None:
    assert fetch_by_id(db, 9999) is None


def test_find_stuck_returns_running_rows_past_timeout(db: sqlite3.Connection) -> None:
    """The watchdog query: rows in 'running' state older than the
    per-kind timeout. Test exercises the time-window filter directly by
    overwriting started_at on the test rows."""
    fresh_task = record_start(db, job_id="fresh-job", kind="prep")
    stuck_task = record_start(db, job_id="stuck-job", kind="prep")

    # Backdate the stuck row by 90 minutes — past the prep timeout (60min).
    cutoff = (datetime.now(UTC) - timedelta(minutes=90)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE background_tasks SET started_at=? WHERE id=?", (cutoff, stuck_task))
    db.commit()

    stuck = find_stuck(db, kind="prep", max_age_minutes=60)
    stuck_ids = [r["id"] for r in stuck]
    assert stuck_task in stuck_ids
    assert fresh_task not in stuck_ids


def test_find_stuck_filters_by_kind(db: sqlite3.Connection) -> None:
    """Backdating a prep row does not surface it under the
    interview_prep timeout query."""
    task_id = record_start(db, job_id="job-9", kind="prep")
    cutoff = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE background_tasks SET started_at=? WHERE id=?", (cutoff, task_id))
    db.commit()

    assert any(r["id"] == task_id for r in find_stuck(db, kind="prep", max_age_minutes=60))
    assert not any(r["id"] == task_id for r in find_stuck(db, kind="interview_prep", max_age_minutes=30))


def test_find_stuck_excludes_finished_rows(db: sqlite3.Connection) -> None:
    """A succeeded or failed row never surfaces in find_stuck regardless
    of how old started_at is."""
    succeeded = record_start(db, job_id="job-10", kind="prep")
    record_succeeded(db, succeeded)
    failed = record_start(db, job_id="job-11", kind="prep")
    record_failed(db, failed, error_message="x")

    cutoff = (datetime.now(UTC) - timedelta(hours=10)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE background_tasks SET started_at=? WHERE id IN (?, ?)", (cutoff, succeeded, failed))
    db.commit()

    stuck_ids = [r["id"] for r in find_stuck(db, kind="prep", max_age_minutes=60)]
    assert succeeded not in stuck_ids
    assert failed not in stuck_ids


def test_find_active_for_subject_returns_latest_running(db: sqlite3.Connection) -> None:
    """When two tasks exist for the same (job_id, kind), the most-recent
    one wins. Lets a re-click status page surface the new run."""
    first = record_start(db, job_id="same-job", kind="prep")
    # Sleep one second so the second row's default started_at differs.
    time.sleep(1)
    second = record_start(db, job_id="same-job", kind="prep")

    row = find_active_for_subject(db, job_id="same-job", kind="prep")
    assert row is not None
    assert row["id"] == second
    assert row["id"] != first


def test_find_active_for_subject_returns_none_for_finished(db: sqlite3.Connection) -> None:
    task_id = record_start(db, job_id="finished-job", kind="prep")
    record_succeeded(db, task_id)

    assert find_active_for_subject(db, job_id="finished-job", kind="prep") is None


def test_find_active_for_subject_isolates_by_kind(db: sqlite3.Connection) -> None:
    """Two tasks on the same job_id with different kinds don't collide."""
    prep_task = record_start(db, job_id="dual-job", kind="prep")
    interview_task = record_start(db, job_id="dual-job", kind="interview_prep")

    prep_row = find_active_for_subject(db, job_id="dual-job", kind="prep")
    interview_row = find_active_for_subject(db, job_id="dual-job", kind="interview_prep")

    assert prep_row["id"] == prep_task
    assert interview_row["id"] == interview_task


def test_kind_check_constraint_rejects_unknown_kind(db: sqlite3.Connection) -> None:
    """The CHECK(kind IN (...)) constraint catches typos at the SQL
    layer. Belt-and-suspenders to type checking on the Python helper."""
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        db.execute("INSERT INTO background_tasks (job_id, kind) VALUES ('j', 'unknown_kind')")


def test_status_check_constraint_rejects_unknown_status(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        db.execute("INSERT INTO background_tasks (job_id, kind, status) VALUES ('j', 'prep', 'weird')")


def test_kind_timeout_minutes_covers_all_kinds() -> None:
    """The CHECK constraint's allowed kinds and KIND_TIMEOUT_MINUTES
    must agree. This test fails if a new kind lands without a
    corresponding watchdog timeout — which would cause find_stuck to
    silently never reap that kind."""
    expected_kinds = {
        "prep",
        "prep_phase_b",
        "interview_prep",
        "speculative_research",
        "podcast",
        "study_guide",
        "flashcards",
    }
    assert set(KIND_TIMEOUT_MINUTES.keys()) == expected_kinds


def test_task_id_from_env_returns_int_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TASK_ID_ENV_VAR, "42")
    assert task_id_from_env() == 42


def test_task_id_from_env_returns_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TASK_ID_ENV_VAR, raising=False)
    assert task_id_from_env() is None


def test_task_id_from_env_returns_none_for_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    """A subprocess invoked outside a launcher (manual CLI) might inherit
    a stale env value — still safer to return None than crash."""
    monkeypatch.setenv(TASK_ID_ENV_VAR, "not-a-number")
    assert task_id_from_env() is None


# ── writeback_sync ────────────────────────────────────────────────────


def test_writeback_sync_records_succeeded_on_clean_exit(db: sqlite3.Connection) -> None:
    task_id = record_start(db, job_id="sync-ok", kind="podcast")
    with writeback_sync(db, task_id):
        pass
    row = fetch_by_id(db, task_id)
    assert row is not None
    assert row["status"] == "succeeded"
    assert row["finished_at"] is not None


def test_writeback_sync_records_failed_on_exception(db: sqlite3.Connection) -> None:
    task_id = record_start(db, job_id="sync-fail", kind="podcast")
    with pytest.raises(ValueError, match="boom"):
        with writeback_sync(db, task_id):
            raise ValueError("boom")
    row = fetch_by_id(db, task_id)
    assert row is not None
    assert row["status"] == "failed"
    assert "ValueError: boom" in (row["error_message"] or "")


def test_writeback_sync_yields_task_id(db: sqlite3.Connection) -> None:
    task_id = record_start(db, job_id="sync-yield", kind="podcast")
    with writeback_sync(db, task_id) as yielded:
        assert yielded == task_id


# ── podcast kind ──────────────────────────────────────────────────────


def test_podcast_kind_accepted_by_check_constraint(db: sqlite3.Connection) -> None:
    task_id = record_start(db, job_id="podcast-job-1", kind="podcast")
    row = fetch_by_id(db, task_id)
    assert row is not None
    assert row["kind"] == "podcast"


def test_find_active_podcast_isolates_from_other_kinds(db: sqlite3.Connection) -> None:
    record_start(db, job_id="same-job", kind="prep")
    record_start(db, job_id="same-job", kind="podcast")
    assert find_active_for_subject(db, job_id="same-job", kind="podcast") is not None
    # Finishing the podcast task should not affect the prep task.
    podcast_row = find_active_for_subject(db, job_id="same-job", kind="podcast")
    record_succeeded(db, podcast_row["id"])
    assert find_active_for_subject(db, job_id="same-job", kind="podcast") is None
    assert find_active_for_subject(db, job_id="same-job", kind="prep") is not None
