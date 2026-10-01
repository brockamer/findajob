"""Onboarding session_store (#336 Task 2).

CRUD wrapper for the ``onboarding_sessions`` table. Pure DB layer — no
FastAPI, no LLM client. Routes (Task 4) and the interview_runner (Task 3)
compose this into the full chat-session lifecycle.

Conventions:
- All write functions commit before returning.
- Reads return a frozen :class:`Session` dataclass, or ``None`` when missing.
- Connection lifecycle is owned by the caller (the routes layer); this
  module never opens its own connection so it stays trivially testable
  against tmp-path DBs.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Session:
    id: str
    history: list[dict[str, str]]
    captured_blocks: dict[str, str]
    started_at: str
    last_turn_at: str
    completed_at: str | None
    error_state: str | None
    cumulative_cost_usd: float = 0.0


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_session(db: sqlite3.Connection) -> str:
    session_id = str(uuid.uuid4())
    now = _utcnow_iso()
    db.execute(
        """
        INSERT INTO onboarding_sessions
            (id, history_json, captured_blocks_json, started_at, last_turn_at)
        VALUES (?, '[]', '{}', ?, ?)
        """,
        (session_id, now, now),
    )
    db.commit()
    return session_id


_SESSION_COLUMNS = (
    "id, history_json, captured_blocks_json, started_at, "
    "last_turn_at, completed_at, error_state, "
    "COALESCE(cumulative_cost_usd, 0)"
)


def _row_to_session(row: tuple) -> Session:
    return Session(
        id=row[0],
        history=json.loads(row[1]),
        captured_blocks=json.loads(row[2]),
        started_at=row[3],
        last_turn_at=row[4],
        completed_at=row[5],
        error_state=row[6],
        cumulative_cost_usd=row[7] if row[7] is not None else 0.0,
    )


def get_session(db: sqlite3.Connection, session_id: str) -> Session | None:
    row = db.execute(
        f"SELECT {_SESSION_COLUMNS} FROM onboarding_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_session(row)


def append_turn(db: sqlite3.Connection, session_id: str, role: str, content: str) -> None:
    if role not in ("user", "assistant"):
        raise ValueError(f"role must be 'user' or 'assistant', got {role!r}")
    sess = get_session(db, session_id)
    if sess is None:
        raise KeyError(session_id)
    new_history = sess.history + [{"role": role, "content": content}]
    db.execute(
        "UPDATE onboarding_sessions SET history_json = ?, last_turn_at = ? WHERE id = ?",
        (json.dumps(new_history), _utcnow_iso(), session_id),
    )
    db.commit()


def update_captured_blocks(db: sqlite3.Connection, session_id: str, blocks: dict[str, str]) -> None:
    db.execute(
        "UPDATE onboarding_sessions SET captured_blocks_json = ? WHERE id = ?",
        (json.dumps(blocks), session_id),
    )
    db.commit()


def mark_complete(db: sqlite3.Connection, session_id: str) -> None:
    db.execute(
        "UPDATE onboarding_sessions SET completed_at = ? WHERE id = ?",
        (_utcnow_iso(), session_id),
    )
    db.commit()


def set_error(db: sqlite3.Connection, session_id: str, message: str) -> None:
    db.execute(
        "UPDATE onboarding_sessions SET error_state = ? WHERE id = ?",
        (message, session_id),
    )
    db.commit()


def clear_error(db: sqlite3.Connection, session_id: str) -> None:
    db.execute(
        "UPDATE onboarding_sessions SET error_state = NULL WHERE id = ?",
        (session_id,),
    )
    db.commit()


def add_turn_cost(db: sqlite3.Connection, session_id: str, usage: dict) -> None:
    """Add this turn's cost to ``cumulative_cost_usd``.

    OpenRouter returns ``usage.cost`` in credits (1:1 with USD) on every
    chat-completions response — already discounted for cache reads and
    inclusive of any provider markup, so it matches the dashboard. Tester
    BYOK responses sometimes report it under
    ``cost_details.upstream_inference_cost`` instead; treat either as
    authoritative and pick whichever is present.

    Silently no-ops on a missing/zero cost field — some local-mock test
    paths return `usage={}` and we don't want test fixtures to need
    updating just because they didn't synthesise this field.
    """
    if not isinstance(usage, dict):
        return
    cost = usage.get("cost")
    if cost is None:
        details = usage.get("cost_details")
        if isinstance(details, dict):
            cost = details.get("upstream_inference_cost")
    try:
        cost_f = float(cost) if cost is not None else 0.0
    except (TypeError, ValueError):
        cost_f = 0.0
    if cost_f <= 0:
        return
    db.execute(
        "UPDATE onboarding_sessions SET cumulative_cost_usd = COALESCE(cumulative_cost_usd, 0) + ? WHERE id = ?",
        (cost_f, session_id),
    )
    db.commit()


def find_active(db: sqlite3.Connection, *, max_age_hours: int = 24) -> Session | None:
    """Return the most recently active un-completed session, or ``None``.

    Used by the ``/onboarding/`` index handler (#336 Task 8) to surface a
    "Resume your interview" affordance when the user closes the tab and
    comes back. Filters:

    - ``completed_at IS NULL`` — finalized sessions don't need resuming
    - ``last_turn_at >= now - max_age_hours`` — stale sessions are dropped
      so a user who walked away days ago doesn't see an outdated affordance

    Sessions with ``error_state`` set are still returned: the user can
    retry from the chat page. Empty-history sessions are also returned —
    they may have a /start error worth seeing.
    """
    cutoff = "datetime('now', ?)"
    row = db.execute(
        f"""SELECT {_SESSION_COLUMNS}
            FROM onboarding_sessions
            WHERE completed_at IS NULL
              AND last_turn_at >= {cutoff}
            ORDER BY last_turn_at DESC
            LIMIT 1""",
        (f"-{max_age_hours} hours",),
    ).fetchone()
    if row is None:
        return None
    return _row_to_session(row)


# ── Keys stored by older versions ────────────────────────────────────────────


def clear_stored_keys(db: sqlite3.Connection) -> None:
    """Set every session's stored API key columns to NULL, then VACUUM.

    Used on the copy of ``pipeline.db`` that goes into a backup, and on a
    restored DB, so one instance's keys never travel to another. Freed space
    must not keep the old values: ``secure_delete`` is on by default only in
    some SQLite builds, so it is set here, and the VACUUM rebuilds the file. A
    DB without the table is left as it is.
    """
    db.execute("PRAGMA secure_delete = ON")
    try:
        db.execute(
            """UPDATE onboarding_sessions
               SET user_openrouter_key = NULL,
                   user_rapidapi_key   = NULL,
                   user_gemini_api_key = NULL"""
        )
    except sqlite3.OperationalError:
        return
    db.commit()
    db.execute("VACUUM")


def lifetime_cost_usd(db: sqlite3.Connection) -> float:
    """Return the all-time onboarding-chat cost on this stack.

    Sums ``cumulative_cost_usd`` across every row in ``onboarding_sessions``
    — onboarding sessions are the only source of LLM cost we track per-stack
    today. Returns 0.0 on a fresh DB or if the column hasn't been migrated
    in yet (older stacks before 2026-05-02).
    """
    try:
        row = db.execute("SELECT COALESCE(SUM(cumulative_cost_usd), 0) FROM onboarding_sessions").fetchone()
    except sqlite3.OperationalError:
        return 0.0
    if row is None or row[0] is None:
        return 0.0
    try:
        return float(row[0])
    except (TypeError, ValueError):
        return 0.0
