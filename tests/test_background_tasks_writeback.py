"""#554 — Subprocess writeback contract test (audit 4 §2 contract #6).

The M6 contract: a launcher inserts a ``background_tasks`` row with
``status='running'`` *before* spawning the subprocess. The subprocess
reads ``FINDAJOB_BG_TASK_ID`` from env and writes back
``status='succeeded'`` / ``'failed'`` on exit via
:func:`findajob.background_tasks.writeback_subprocess`. SIGKILL paths
leave the row at ``running`` — watchdog reaps via per-kind timeout.

These tests exercise the contract end-to-end:

1. **Clean exit** → row transitions ``running`` → ``succeeded``.
2. **Unhandled exception** → row transitions ``running`` → ``failed``
   with the exception class+message in ``error_message``.
3. **Missing env var** (manual CLI invocation) → no row writeback;
   any pre-existing row is untouched.
4. **Already-finished row** (race scenario: watchdog reaped before
   the subprocess could write back) → row stays at terminal state;
   late writeback is a no-op.

The launcher-side contract (insert before Popen, mark failed on Popen
error) is exercised in route-level tests for board_actions and
speculative.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from findajob.background_tasks import (
    TASK_ID_ENV_VAR,
    fetch_by_id,
    record_failed,
    record_start,
    writeback_subprocess,
)
from findajob.db.migrate import apply_pending


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Build a tmp pipeline.db via the production migration runner."""
    path = tmp_path / "writeback.db"
    conn = sqlite3.connect(str(path))
    apply_pending(conn)
    conn.close()
    return path


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    return conn


def test_writeback_clean_exit_marks_succeeded(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The orchestrator pattern: ``with writeback_subprocess(DB_PATH): ...``
    on normal completion → ``status='succeeded'`` + ``finished_at`` set."""
    conn = _open(db_path)
    task_id = record_start(conn, job_id="job-clean", kind="prep")
    conn.close()

    monkeypatch.setenv(TASK_ID_ENV_VAR, str(task_id))

    with writeback_subprocess(str(db_path)):
        # Simulate orchestrator work — no exception.
        pass

    conn = _open(db_path)
    row = fetch_by_id(conn, task_id)
    conn.close()
    assert row is not None
    assert row["status"] == "succeeded"
    assert row["finished_at"] is not None


def test_writeback_unhandled_exception_marks_failed(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unhandled exception inside the ``with`` block → ``status='failed'``
    with ``f"{type(exc).__name__}: {exc}"`` in error_message; the
    exception still propagates so the subprocess exit code is preserved."""
    conn = _open(db_path)
    task_id = record_start(conn, job_id="job-crash", kind="prep")
    conn.close()

    monkeypatch.setenv(TASK_ID_ENV_VAR, str(task_id))

    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        with writeback_subprocess(str(db_path)):
            raise _Boom("OpenRouter timed out")

    conn = _open(db_path)
    row = fetch_by_id(conn, task_id)
    conn.close()
    assert row is not None
    assert row["status"] == "failed"
    assert row["finished_at"] is not None
    assert row["error_message"] is not None
    assert "_Boom" in row["error_message"]
    assert "OpenRouter timed out" in row["error_message"]


def test_writeback_no_env_is_noop(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Manual CLI invocation: ``FINDAJOB_BG_TASK_ID`` unset → context
    manager runs the inner block but writes nothing back. Any
    pre-existing row stays untouched."""
    conn = _open(db_path)
    task_id = record_start(conn, job_id="job-no-env", kind="prep")
    conn.close()

    monkeypatch.delenv(TASK_ID_ENV_VAR, raising=False)

    with writeback_subprocess(str(db_path)):
        pass

    # Row stays at status='running' because the orchestrator wasn't
    # told which row to update.
    conn = _open(db_path)
    row = fetch_by_id(conn, task_id)
    conn.close()
    assert row is not None
    assert row["status"] == "running"
    assert row["finished_at"] is None


def test_writeback_against_already_finished_row_is_noop(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Race scenario: watchdog reaped the row before the subprocess
    could write back. The late writeback's UPDATE has
    ``WHERE status='running'`` so it's a no-op — the watchdog's
    earlier ``failed`` stamp survives."""
    conn = _open(db_path)
    task_id = record_start(conn, job_id="job-raced", kind="prep")
    # Watchdog reaped the row before we got here
    record_failed(conn, task_id, error_message="watchdog: subprocess > 60min")
    conn.close()

    monkeypatch.setenv(TASK_ID_ENV_VAR, str(task_id))

    # Subprocess exits cleanly — would normally call record_succeeded
    with writeback_subprocess(str(db_path)):
        pass

    conn = _open(db_path)
    row = fetch_by_id(conn, task_id)
    conn.close()
    assert row is not None
    # Watchdog's stamp wins: row stays at failed with watchdog's
    # error message; the late record_succeeded call hit the
    # WHERE status='running' guard.
    assert row["status"] == "failed"
    assert row["error_message"] is not None
    assert "watchdog" in row["error_message"]


def test_writeback_propagates_systemexit(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``SystemExit`` (raised by sys.exit) is a BaseException — the
    context manager records failed and re-raises so the exit code
    propagates. Covers ``signal.signal(SIGTERM, ...)`` paths that
    raise SystemExit on shutdown."""
    conn = _open(db_path)
    task_id = record_start(conn, job_id="job-sigterm", kind="prep")
    conn.close()

    monkeypatch.setenv(TASK_ID_ENV_VAR, str(task_id))

    with pytest.raises(SystemExit) as exc_info:
        with writeback_subprocess(str(db_path)):
            sys.exit(143)
    assert exc_info.value.code == 143

    conn = _open(db_path)
    row = fetch_by_id(conn, task_id)
    conn.close()
    assert row is not None
    assert row["status"] == "failed"
