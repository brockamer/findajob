"""Monthly LLM spend-ceiling enforcement helpers (#671).

Two surfaces:

- :func:`check_call_gate` — called inside ``openrouter.complete()`` before
  every HTTP request. Opens a short-lived DB connection, checks the current
  calendar-month spend, and raises :class:`LLMSpendCeilingExceeded` if the
  ceiling is met or exceeded. No-op when the ceiling is disabled or the DB
  is unavailable (so unit tests that don't build a pipeline.db are unaffected).

- :func:`check_launch_gate` — called by the 5 LLM-spawning route handlers
  before they launch a subprocess. Returns a :class:`LaunchGateRefusal`
  dataclass if the ceiling is exceeded, or ``None`` if OK. Takes a caller-
  supplied ``sqlite3.Connection`` so it participates in the route's existing
  DB session.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from findajob.config_loader import load_spend_ceiling
from findajob.cost_rollups import spend_this_month
from findajob.db import connect
from findajob.llm.openrouter import LLMSpendCeilingExceeded
from findajob.paths import BASE

_DB_PATH = Path(BASE) / "data" / "pipeline.db"


@dataclass(frozen=True)
class LaunchGateRefusal:
    """Returned by :func:`check_launch_gate` when the ceiling is exceeded."""

    ceiling_usd: float
    current_sum_usd: float


def check_call_gate() -> None:
    """Raise :class:`LLMSpendCeilingExceeded` if the monthly ceiling is exceeded.

    Called inside ``openrouter.complete()`` before any HTTP work. Opens its
    own DB connection and closes it immediately so it doesn't interfere with
    callers that manage their own connections. Silently returns when:

    - Ceiling is disabled (``load_spend_ceiling()`` returns ``None``).
    - ``pipeline.db`` doesn't exist (fresh install, unit-test environment).
    - Any DB error — spending data is best-effort; don't block LLM calls on
      a transient DB failure.
    """
    ceiling = load_spend_ceiling()
    if ceiling is None:
        return

    try:
        conn = connect(_DB_PATH)
    except Exception:  # noqa: BLE001
        return

    try:
        current = spend_this_month(conn)
    except Exception:  # noqa: BLE001
        return
    finally:
        conn.close()

    if current >= ceiling:
        raise LLMSpendCeilingExceeded(ceiling_usd=ceiling, current_sum_usd=current)


def check_launch_gate(conn: sqlite3.Connection) -> LaunchGateRefusal | None:
    """Return a :class:`LaunchGateRefusal` if the ceiling is exceeded, else ``None``.

    Takes a caller-supplied connection so the route handler's existing DB
    session is reused — no extra connection overhead.
    """
    ceiling = load_spend_ceiling()
    if ceiling is None:
        return None

    current = spend_this_month(conn)
    if current >= ceiling:
        return LaunchGateRefusal(ceiling_usd=ceiling, current_sum_usd=current)

    return None
