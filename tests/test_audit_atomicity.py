"""Regression tests for #958 — atomic stage+audit commit and deterministic
prior-stage restoration.

Two defects:

1. **Split commit.** A stage ``UPDATE`` committed *before* its ``audit_log`` row
   (``write_audit`` is a separate INSERT+commit). A crash between the two commits
   leaves a job durably at the new stage with no audit trail — which then breaks
   reverse handlers (they fall back to ``applied``) and the un-apply 30s window
   (no ``… -> applied`` row to find). The fix writes the stage change and its
   audit row in a single transaction (one commit). For the #957 prep-claim path
   this means the audit INSERT rides inside ``_claim_prep_slot``'s claim
   transaction, under the same RESERVED lock, to one commit.

2. **Non-deterministic restoration.** Prior-stage lookups used
   ``ORDER BY changed_at DESC LIMIT 1`` with no ``id`` tiebreaker; ``changed_at``
   is second-resolution naive text, so two transitions to the same target within
   one clock-second could restore the wrong ``old_value``. The fix adds
   ``, id DESC`` so the most recent row (highest autoincrement id) wins on a tie.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from findajob import actions
from findajob.web.routes import board_actions


def _build_pipeline_db(db_path: Path) -> None:
    from findajob.db.migrate import apply_pending

    conn = sqlite3.connect(db_path)
    try:
        apply_pending(conn)
    finally:
        conn.close()


def _insert_job(conn: sqlite3.Connection, *, job_id: str, stage: str, reject_reason: str = "") -> None:
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, relevance_score, reject_reason) "
        "VALUES (?, ?, 'https://example.com/job', 'Senior Ops', 'Acme Corp', 'test', ?, 8, ?)",
        (job_id, f"fp_{job_id}", stage, reject_reason),
    )
    conn.commit()


def _insert_audit(conn: sqlite3.Connection, *, job_id: str, old: str, new: str, changed_at: str) -> None:
    """Insert an audit_log row with an explicit (tie-able) changed_at; id autoincrements."""
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at) VALUES (?, 'stage', ?, ?, ?)",
        (job_id, old, new, changed_at),
    )
    conn.commit()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _fresh_stage(db_path: Path, job_id: str) -> str:
    """Read stage on a brand-new connection — sees only committed state."""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT stage FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
    finally:
        conn.close()


def _job_row(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    return conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "pipeline.db"
    _build_pipeline_db(path)
    return path


# ── _claim_prep_slot folds the audit row into the claim transaction ──────────


def test_claim_writes_exactly_one_stage_audit_row(db_path: Path) -> None:
    """A successful claim writes its own stage audit row — exactly one, no caller
    duplicate (membership would miss a double; assert the count)."""
    conn = _connect(db_path)
    _insert_job(conn, job_id="j1", stage="scored")

    outcome = board_actions._claim_prep_slot(
        conn,
        "j1",
        from_stages=("prep_in_progress", "materials_drafted"),
        exclude=True,
        audit_old_value="scored",
    )

    assert outcome == "claimed"
    assert _fresh_stage(db_path, "j1") == "prep_in_progress"
    count = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE job_id='j1' AND field_changed='stage' AND new_value='prep_in_progress'"
    ).fetchone()[0]
    assert count == 1, "claim must write exactly one stage audit row"
    row = conn.execute(
        "SELECT old_value FROM audit_log WHERE job_id='j1' AND field_changed='stage' AND new_value='prep_in_progress'"
    ).fetchone()
    assert row["old_value"] == "scored"


def test_claim_leaves_stage_unchanged_when_audit_write_fails(db_path: Path, monkeypatch) -> None:
    """If the audit INSERT raises, the stage change must NOT be durable — the claim
    UPDATE and the audit row commit together (one transaction)."""
    conn = _connect(db_path)
    _insert_job(conn, job_id="j1", stage="scored")

    def _boom(*_a, **_k):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(board_actions, "write_audit", _boom)

    with pytest.raises(RuntimeError):
        board_actions._claim_prep_slot(
            conn,
            "j1",
            from_stages=("prep_in_progress", "materials_drafted"),
            exclude=True,
            audit_old_value="scored",
        )
    conn.close()  # discard the uncommitted transaction
    assert _fresh_stage(db_path, "j1") == "scored", "stage committed without its audit row"


# ── reverse handlers: atomic stage+audit ─────────────────────────────────────


def test_un_not_selected_leaves_stage_unchanged_when_audit_write_fails(db_path: Path, monkeypatch) -> None:
    conn = _connect(db_path)
    _insert_job(conn, job_id="j1", stage="not_selected", reject_reason="Position closed")
    _insert_audit(conn, job_id="j1", old="applied", new="not_selected", changed_at="2026-06-01 10:00:00")

    def _boom(*_a, **_k):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(actions, "write_audit", _boom)

    with pytest.raises(RuntimeError):
        actions.un_not_selected_job(conn, _job_row(conn, "j1"))
    conn.close()
    assert _fresh_stage(db_path, "j1") == "not_selected", "stage committed without its audit row"


# ── deterministic restoration via id DESC tiebreaker ─────────────────────────


def test_un_not_selected_restores_highest_id_on_changed_at_tie(db_path: Path) -> None:
    """Two not_selected transitions in the same clock-second: restoration must pick
    the most recent (highest id), not whichever the engine happens to scan first."""
    conn = _connect(db_path)
    _insert_job(conn, job_id="j1", stage="not_selected")
    tie = "2026-06-01 10:00:00"
    _insert_audit(conn, job_id="j1", old="interview", new="not_selected", changed_at=tie)  # lower id
    _insert_audit(conn, job_id="j1", old="offer", new="not_selected", changed_at=tie)  # higher id = most recent

    restored = actions.un_not_selected_job(conn, _job_row(conn, "j1"))

    assert restored == "offer"
    assert _fresh_stage(db_path, "j1") == "offer"


def test_promote_from_fallback_restores_highest_id_on_changed_at_tie(db_path: Path) -> None:
    """Two-hop handler: same-second tie on the withdrawn_fallback lookup resolves to
    the most recent transition (highest id)."""
    conn = _connect(db_path)
    _insert_job(conn, job_id="j1", stage="withdrawn_fallback", reject_reason="Better opportunity")
    tie = "2026-06-01 10:00:00"
    _insert_audit(conn, job_id="j1", old="applied", new="withdrawn_fallback", changed_at=tie)  # lower id
    _insert_audit(conn, job_id="j1", old="interview", new="withdrawn_fallback", changed_at=tie)  # higher id

    restored = actions.promote_from_fallback(conn, _job_row(conn, "j1"))

    assert restored == "interview"
    assert _fresh_stage(db_path, "j1") == "interview"
