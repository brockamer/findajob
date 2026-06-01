"""Regression tests for #957 — atomic prep-launch claim.

The prep-launching handlers in ``board_actions`` previously did a non-atomic
check-then-act: read the job's stage, separately count in-flight preps, then
write ``prep_in_progress`` and spawn a subprocess. Two near-simultaneous
requests (a double-click, an HTMX retry) both pass the guards before either
writes, so both launch ``prep_application.py`` for one job — doubling Phase-A
LLM spend — and the cross-job concurrency cap can be exceeded under burst.

``_claim_prep_slot`` collapses the check and the write into a single
conditional ``UPDATE`` so a concurrent double-click / burst wins at most one
slot. These tests exercise the claim directly because ``TestClient`` cannot
produce true request concurrency (it pins every call to one anyio portal —
see ``test_dashboard_concurrent``); a thread + ``Barrier`` against separate
per-request connections reconstructs the race deterministically (SQLite
serializes the writes on the RESERVED lock).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Barrier, Thread

import pytest

from findajob.web.routes.board_actions import MAX_CONCURRENT_PREPS, _claim_prep_slot


def _build_pipeline_db(db_path: Path) -> None:
    from findajob.db.migrate import apply_pending

    conn = sqlite3.connect(db_path)
    try:
        apply_pending(conn)
    finally:
        conn.close()


def _insert_job(conn: sqlite3.Connection, *, job_id: str, stage: str) -> None:
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, relevance_score) "
        "VALUES (?, ?, 'https://example.com/job', 'Senior Ops', 'Acme Corp', 'test', ?, 8)",
        (job_id, f"fp_{job_id}", stage),
    )
    conn.commit()


def _connect(db_path: Path) -> sqlite3.Connection:
    # Mirror the production per-request connection from create_app's get_db:
    # check_same_thread=False so a connection can hand off across threads, and
    # the default 5s busy timeout so a second writer waits for the RESERVED
    # lock rather than raising "database is locked".
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "pipeline.db"
    _build_pipeline_db(path)
    return path


def _stage(db_path: Path, job_id: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT stage FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
    finally:
        conn.close()


# ── single-caller semantics ─────────────────────────────────────────────────


def test_claim_succeeds_from_valid_stage(db_path: Path) -> None:
    conn = _connect(db_path)
    _insert_job(conn, job_id="j1", stage="scored")

    outcome = _claim_prep_slot(
        conn, "j1", from_stages=("prep_in_progress", "materials_drafted"), exclude=True, audit_old_value="scored"
    )

    assert outcome == "claimed"
    assert _stage(db_path, "j1") == "prep_in_progress"


def test_second_claim_on_in_progress_is_invalid_stage(db_path: Path) -> None:
    """A job already in flight cannot be re-claimed — the WHERE guard rejects it."""
    conn = _connect(db_path)
    _insert_job(conn, job_id="j1", stage="prep_in_progress")

    outcome = _claim_prep_slot(
        conn,
        "j1",
        from_stages=("prep_in_progress", "materials_drafted"),
        exclude=True,
        audit_old_value="prep_in_progress",
    )

    assert outcome == "invalid_stage"


def test_inclusive_from_stages_rejects_other_stages(db_path: Path) -> None:
    """continue_prep claims only from briefing_ready (inclusive form)."""
    conn = _connect(db_path)
    _insert_job(conn, job_id="ready", stage="briefing_ready")
    _insert_job(conn, job_id="scored", stage="scored")

    assert (
        _claim_prep_slot(conn, "ready", from_stages=("briefing_ready",), audit_old_value="briefing_ready") == "claimed"
    )
    assert (
        _claim_prep_slot(conn, "scored", from_stages=("briefing_ready",), audit_old_value="scored") == "invalid_stage"
    )
    assert _stage(db_path, "scored") == "scored"


def test_claim_denied_when_cap_reached(db_path: Path) -> None:
    conn = _connect(db_path)
    for i in range(MAX_CONCURRENT_PREPS):
        _insert_job(conn, job_id=f"busy{i}", stage="prep_in_progress")
    _insert_job(conn, job_id="waiting", stage="scored")

    outcome = _claim_prep_slot(
        conn, "waiting", from_stages=("prep_in_progress", "materials_drafted"), exclude=True, audit_old_value="scored"
    )

    assert outcome == "queue_full"
    assert _stage(db_path, "waiting") == "scored"


# ── concurrency (the actual #957 race) ───────────────────────────────────────


def test_concurrent_claims_same_job_single_winner(db_path: Path) -> None:
    """N simultaneous claims for ONE job → exactly one wins, rest invalid_stage."""
    seed = _connect(db_path)
    _insert_job(seed, job_id="j1", stage="scored")
    seed.close()

    n = 8
    barrier = Barrier(n)
    outcomes: list[str] = []

    def worker() -> None:
        conn = _connect(db_path)
        try:
            barrier.wait(timeout=5)
            outcomes.append(
                _claim_prep_slot(
                    conn,
                    "j1",
                    from_stages=("prep_in_progress", "materials_drafted"),
                    exclude=True,
                    audit_old_value="scored",
                )
            )
        finally:
            conn.close()

    threads = [Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert all(not t.is_alive() for t in threads), "a worker thread hung"
    assert outcomes.count("claimed") == 1, f"expected exactly one winner, got {outcomes}"
    assert _stage(db_path, "j1") == "prep_in_progress"


def test_concurrent_burst_respects_cap(db_path: Path) -> None:
    """With one free slot, a burst across distinct jobs fills exactly one slot."""
    seed = _connect(db_path)
    # Saturate all but one slot, then offer more scored jobs than free slots.
    for i in range(MAX_CONCURRENT_PREPS - 1):
        _insert_job(seed, job_id=f"busy{i}", stage="prep_in_progress")
    job_ids = [f"scored{i}" for i in range(5)]
    for jid in job_ids:
        _insert_job(seed, job_id=jid, stage="scored")
    seed.close()

    barrier = Barrier(len(job_ids))
    outcomes: list[str] = []

    def worker(jid: str) -> None:
        conn = _connect(db_path)
        try:
            barrier.wait(timeout=5)
            outcomes.append(
                _claim_prep_slot(
                    conn,
                    jid,
                    from_stages=("prep_in_progress", "materials_drafted"),
                    exclude=True,
                    audit_old_value="scored",
                )
            )
        finally:
            conn.close()

    threads = [Thread(target=worker, args=(jid,)) for jid in job_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert all(not t.is_alive() for t in threads), "a worker thread hung"
    assert outcomes.count("claimed") == 1, f"cap exceeded: {outcomes}"
    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage='prep_in_progress'").fetchone()[0]
    finally:
        conn.close()
    assert count == MAX_CONCURRENT_PREPS
