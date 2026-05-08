"""Audit-log writes and structured event logging.

Two related concerns landed here together because they share the same
operational role: persisting a record of what the pipeline did.

- :func:`log_event` appends one JSON-line to ``logs/pipeline.jsonl``.
  Used everywhere — fetchers, scoring, prep, web routes — for any
  observable event worth surfacing to the operator's tail / health-check
  pipeline.
- :func:`write_audit` inserts one row into ``audit_log`` for every
  ``jobs.*`` field transition. Used by ``findajob.actions`` (every web
  POST handler) and the watchdog. Provides the durable trail that the
  ``/audit/`` page renders.

Extracted from ``utils.py`` in M4.E2.I2 (#550). No logic changes.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime

from findajob.paths import BASE

LOG_PATH: str = f"{BASE}/logs/pipeline.jsonl"


def log_event(event_type: str, **kwargs: object) -> None:
    entry = {"ts": datetime.now(UTC).isoformat(), "event": event_type, **kwargs}
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def write_audit(
    conn: sqlite3.Connection,
    job_id: str,
    field_changed: str,
    old_value: object,
    new_value: object,
    *,
    changed_by: str | None = None,
) -> None:
    if changed_by is not None:
        conn.execute(
            "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_by) VALUES (?, ?, ?, ?, ?)",
            (
                job_id,
                field_changed,
                str(old_value) if old_value is not None else None,
                str(new_value),
                changed_by,
            ),
        )
    else:
        conn.execute(
            "INSERT INTO audit_log (job_id, field_changed, old_value, new_value) VALUES (?, ?, ?, ?)",
            (job_id, field_changed, str(old_value) if old_value is not None else None, str(new_value)),
        )
    conn.commit()
