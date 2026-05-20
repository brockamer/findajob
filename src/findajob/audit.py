"""Audit-log writes and structured event logging.

Two related concerns landed here together because they share the same
operational role: persisting a record of what the pipeline did.

- :func:`log_event` appends one JSON-line to ``logs/pipeline.jsonl``.
  Used everywhere — fetchers, scoring, prep, web routes — for any
  observable event worth surfacing to the operator's tail / health-check
  pipeline. Size-bounded via a custom inline rotator (see below) — the
  file caps at 5 MB, rotates to gzipped ``pipeline.jsonl.N.gz`` siblings
  keeping the most recent 6, with anything older than 90 days swept.
- :func:`write_audit` inserts one row into ``audit_log`` for every
  ``jobs.*`` field transition. Used by ``findajob.actions`` (every web
  POST handler) and the watchdog. Provides the durable trail that the
  ``/audit/`` page renders.

Rotation design (#8) — custom inline rather than
``logging.handlers.RotatingFileHandler``. Three of the four features we
need (gzip on rotation, 90-day mtime sweep, test-monkeypatch-compatible
``LOG_PATH`` resolution) require custom hooks anyway, and stdlib's
per-process open file handle would introduce a multi-process race the
existing open-append-close pattern avoids. The hot path stays
open-append-close so POSIX ``O_APPEND`` atomicity guarantees no lock is
needed there; the rare rotation block is serialized across processes
with a non-blocking ``fcntl.flock`` on a sidecar lock file.

Extracted from ``utils.py`` in M4.E2.I2 (#550). #8 added rotation in 2026-05.
"""

from __future__ import annotations

import fcntl
import gzip
import json
import os
import shutil
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from findajob.paths import BASE

LOG_PATH: str = f"{BASE}/logs/pipeline.jsonl"

_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 6
_RETENTION_SECONDS = 90 * 24 * 60 * 60


def _rotate(path: str) -> None:
    """Shift existing ``.N.gz`` backups up, gzip the current file to
    ``.1.gz``, then sweep any backup older than 90 days.

    Caller must hold the rotation flock. Safe to re-enter — if another
    process rotated between the caller's size check and lock
    acquisition, ``_maybe_rotate`` re-checks size under the lock and
    short-circuits before calling this.
    """
    for i in range(_BACKUP_COUNT, 0, -1):
        src = f"{path}.{i}.gz"
        if i == _BACKUP_COUNT:
            if os.path.exists(src):
                os.remove(src)
            continue
        dst = f"{path}.{i + 1}.gz"
        if os.path.exists(src):
            os.replace(src, dst)
    staging = f"{path}.1"
    try:
        os.replace(path, staging)
    except FileNotFoundError:
        return
    target = f"{path}.1.gz"
    with open(staging, "rb") as f_in, gzip.open(target, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.remove(staging)
    cutoff = time.time() - _RETENTION_SECONDS
    log_dir = Path(path).parent
    base_name = Path(path).name
    for entry in log_dir.glob(f"{base_name}.*.gz"):
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            continue


def _maybe_rotate(path: str) -> None:
    """Size-check ``path`` and rotate under a non-blocking flock.

    Returns immediately if size is under threshold, the file is missing,
    or another process is already rotating. The flock is sidecar
    (``<path>.rotate.lock``) so the hot append path never contends.
    """
    try:
        if os.path.getsize(path) < _MAX_BYTES:
            return
    except OSError:
        return
    lock_path = f"{path}.rotate.lock"
    try:
        lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o644)
    except OSError:
        return
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            if os.path.getsize(path) < _MAX_BYTES:
                return
        except OSError:
            return
        _rotate(path)
    finally:
        os.close(lock_fd)


def log_event(event_type: str, **kwargs: object) -> None:
    entry = {"ts": datetime.now(UTC).isoformat(), "event": event_type, **kwargs}
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    _maybe_rotate(LOG_PATH)
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
    commit: bool = True,
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
    if commit:
        conn.commit()
