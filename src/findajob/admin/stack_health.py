"""Per-stack health aggregation: pipeline.db SQL + pipeline.jsonl tail."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from findajob.admin.jsonl_tail import tail_events
from findajob.admin.stack_discovery import StackPath
from findajob.db import connect

logger = logging.getLogger(__name__)

Freshness = Literal["fresh", "late", "stale", "unknown"]


@dataclass(frozen=True)
class StackHealth:
    handle: str
    last_triage_complete: datetime | None = None
    last_triage_failed: datetime | None = None
    last_discovery_failed: datetime | None = None
    last_prep_failed: datetime | None = None
    triage_success_24h: int = 0
    triage_failure_24h: int = 0
    stage_counts: dict[str, int] = field(default_factory=dict)
    stuck_prep_count: int = 0
    unread_notifications: int = 0
    db_missing: bool = False
    jsonl_missing: bool = False
    error: str | None = None
    freshness: Freshness = "unknown"


def gather(stack: StackPath, *, now: datetime | None = None) -> StackHealth:
    """Read `pipeline.db` (read-only) and tail `pipeline.jsonl`. Return one
    StackHealth dataclass with everything the dashboard template needs.

    All exceptions caught and surfaced via `StackHealth.error` so a single
    broken stack does not crash the page. `now` is injectable for tests.
    """
    now = now or datetime.now(UTC)
    db_missing = not stack.db_path.is_file()
    jsonl_missing = not stack.jsonl_path.is_file()

    error: str | None = None
    stage_counts: dict[str, int] = {}
    stuck_prep_count = 0
    unread_notifications = 0

    if not db_missing:
        try:
            # `cross_stack=True` selects the `mode=ro&immutable=1` URI form.
            # `immutable=1` treats the DB as a fixed snapshot — no journal /
            # WAL / shm sidecar reads, no locking. Cross-stack reads from the
            # operator container (uid 1000) against tester DBs owned by host
            # uid 1001 fail under default mode=ro because the producer's WAL
            # sidecar is unreadable to the foreign uid (#333 production smoke
            # 2026-04-30 surfaced "unable to open database file" until
            # immutable=1 was added). Tradeoff: the dashboard sees a snapshot
            # at the last checkpoint, missing in-flight WAL writes — fine for
            # a "is this stack alive" health view. The helper enforces the
            # invariant: passing `cross_stack=True` without `ro=True` raises.
            with connect(stack.db_path, ro=True, cross_stack=True) as conn:
                conn.row_factory = sqlite3.Row
                stage_counts = {
                    row["stage"]: row["n"]
                    for row in conn.execute("SELECT stage, COUNT(*) AS n FROM jobs GROUP BY stage")
                }
                # `stage_updated` is the canonical "when did stage last change"
                # column written by web handlers (and read by scripts/watchdog.py
                # for the same stuck-prep reset query). The earlier draft used
                # a fictional `prep_started_at` column — production smoke 2026-04-30
                # surfaced "no such column".
                cutoff = (now - timedelta(minutes=60)).isoformat()
                stuck_prep_count = conn.execute(
                    "SELECT COUNT(*) FROM jobs "
                    "WHERE stage = 'prep_in_progress' "
                    "AND stage_updated IS NOT NULL "
                    "AND stage_updated < ?",
                    (cutoff,),
                ).fetchone()[0]
                # Notifications table is post-#440; older stacks may lack it.
                # Wrap the lookup so a missing table is silently a 0.
                try:
                    unread_notifications = conn.execute(
                        "SELECT COUNT(*) FROM notifications WHERE read_at IS NULL"
                    ).fetchone()[0]
                except sqlite3.OperationalError:
                    unread_notifications = 0
        except sqlite3.Error as e:
            error = f"sqlite: {e}"
        except Exception as e:
            # Intentionally broad. The narrower sqlite3.Error arm above
            # covers DB-engine failures, but this gather() runs under a
            # TOCTOU race (the stack's container can rotate the DB out
            # from under us between is_file() and connect()) and against
            # foreign-uid bind mounts whose surface includes raw OSError,
            # PermissionError, and unicode failures from corrupted paths.
            # The "one broken stack must not crash the dashboard" invariant
            # is load-bearing here — do not narrow this arm without first
            # filing a per-source coroutine refactor.
            logger.warning("admin_stacks: gather failed for %s: %s", stack.handle, e)
            error = f"{type(e).__name__}: {e}"

    last_triage_complete: datetime | None = None
    last_triage_failed: datetime | None = None
    last_discovery: datetime | None = None
    last_prep: datetime | None = None
    success_24h = 0
    failure_24h = 0
    cutoff_24h = now - timedelta(hours=24)

    if not jsonl_missing:
        for event in tail_events(stack.jsonl_path):
            ts = _parse_ts(event.get("ts"))
            if ts is None:
                continue
            ev = event.get("event")
            # `ts > cutoff_24h` (strict) matches `_freshness` below, which
            # uses `age < 24h` (strict) — both treat exactly-24h-ago as
            # OUT of the window. Was `>=` before #359; the asymmetric
            # case was never hit in production but the convention drift
            # was real.
            if ev == "pipeline_complete":
                if last_triage_complete is None:
                    last_triage_complete = ts
                if ts > cutoff_24h:
                    success_24h += 1
            elif ev == "pipeline_terminated":
                if last_triage_failed is None:
                    last_triage_failed = ts
                if ts > cutoff_24h:
                    failure_24h += 1
            elif ev == "discovery_failed":
                if last_discovery is None:
                    last_discovery = ts
            elif ev in ("prep_failed", "prep_failed_reset"):
                if last_prep is None:
                    last_prep = ts

    return StackHealth(
        handle=stack.handle,
        last_triage_complete=last_triage_complete,
        last_triage_failed=last_triage_failed,
        last_discovery_failed=last_discovery,
        last_prep_failed=last_prep,
        triage_success_24h=success_24h,
        triage_failure_24h=failure_24h,
        stage_counts=stage_counts,
        stuck_prep_count=stuck_prep_count,
        unread_notifications=unread_notifications,
        db_missing=db_missing,
        jsonl_missing=jsonl_missing,
        error=error,
        freshness=_freshness(last_triage_complete, now),
    )


def _parse_ts(raw: object) -> datetime | None:
    """Parse an ISO-8601 timestamp; coerce naïve values to UTC.

    `findajob.utils.log_event` always emits tz-aware ISO strings, but a
    hand-edited or older log file may contain naïve timestamps. Comparing
    those against `cutoff_24h` (tz-aware) raises TypeError and crashes
    the dashboard render. Coerce here so the comparison is always valid.
    """
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _freshness(last: datetime | None, now: datetime) -> Freshness:
    if last is None:
        return "unknown"
    age = now - last
    if age < timedelta(hours=24):
        return "fresh"
    if age < timedelta(hours=36):
        return "late"
    return "stale"
