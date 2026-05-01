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


def get_session(db: sqlite3.Connection, session_id: str) -> Session | None:
    row = db.execute(
        """SELECT id, history_json, captured_blocks_json, started_at,
                  last_turn_at, completed_at, error_state
           FROM onboarding_sessions WHERE id = ?""",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return Session(
        id=row[0],
        history=json.loads(row[1]),
        captured_blocks=json.loads(row[2]),
        started_at=row[3],
        last_turn_at=row[4],
        completed_at=row[5],
        error_state=row[6],
    )


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


def find_active(db: sqlite3.Connection, *, max_age_hours: int = 24) -> Session | None:
    """Return the most recently active un-completed session, or ``None``.

    Used by the ``/onboarding/`` index handler (#336 Task 8) to surface a
    "Resume your interview" affordance when the user closes the tab and
    comes back. Filters:

    - ``completed_at IS NULL`` — finalized sessions don't need resuming
    - ``last_turn_at >= now - max_age_hours`` — stale sessions are dropped
      so a tester who walked away days ago doesn't see an outdated affordance

    Sessions with ``error_state`` set are still returned: the user can
    retry from the chat page. Empty-history sessions are also returned —
    they may have a /start error worth seeing.
    """
    cutoff = "datetime('now', ?)"
    row = db.execute(
        f"""SELECT id, history_json, captured_blocks_json, started_at,
                   last_turn_at, completed_at, error_state
            FROM onboarding_sessions
            WHERE completed_at IS NULL
              AND last_turn_at >= {cutoff}
            ORDER BY last_turn_at DESC
            LIMIT 1""",
        (f"-{max_age_hours} hours",),
    ).fetchone()
    if row is None:
        return None
    return Session(
        id=row[0],
        history=json.loads(row[1]),
        captured_blocks=json.loads(row[2]),
        started_at=row[3],
        last_turn_at=row[4],
        completed_at=row[5],
        error_state=row[6],
    )
