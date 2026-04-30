"""Per-stack health aggregation: pipeline.db SQL + pipeline.jsonl tail."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from findajob.admin.jsonl_tail import tail_events
from findajob.admin.stack_discovery import StackPath

logger = logging.getLogger(__name__)

Freshness = Literal["fresh", "late", "stale", "unknown"]

_FAILURE_EVENTS = ("aichat_failure", "discovery_failed", "prep_failed", "prep_failed_reset")


@dataclass(frozen=True)
class StackHealth:
    handle: str
    last_triage_complete: datetime | None = None
    last_triage_failed: datetime | None = None
    last_aichat_failure: datetime | None = None
    last_discovery_failed: datetime | None = None
    last_prep_failed: datetime | None = None
    triage_success_24h: int = 0
    triage_failure_24h: int = 0
    stage_counts: dict[str, int] = field(default_factory=dict)
    stuck_prep_count: int = 0
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

    if not db_missing:
        try:
            # `immutable=1` treats the DB as a fixed snapshot — no journal /
            # WAL / shm sidecar reads, no locking. Cross-stack reads from the
            # operator container (uid 1000) against tester DBs owned by host
            # uid 1001 fail under default mode=ro because the producer's WAL
            # sidecar is unreadable to the foreign uid (#333 production smoke
            # 2026-04-30 surfaced "unable to open database file" until
            # immutable=1 was added). Tradeoff: the dashboard sees a snapshot
            # at the last checkpoint, missing in-flight WAL writes — fine for
            # a "is this stack alive" health view.
            uri = f"file:{stack.db_path}?mode=ro&immutable=1"
            with sqlite3.connect(uri, uri=True) as conn:
                conn.row_factory = sqlite3.Row
                stage_counts = {
                    row["stage"]: row["n"]
                    for row in conn.execute("SELECT stage, COUNT(*) AS n FROM jobs GROUP BY stage")
                }
                cutoff = (now - timedelta(minutes=60)).isoformat()
                stuck_prep_count = conn.execute(
                    "SELECT COUNT(*) FROM jobs "
                    "WHERE stage = 'prep_in_progress' "
                    "AND prep_started_at IS NOT NULL "
                    "AND prep_started_at < ?",
                    (cutoff,),
                ).fetchone()[0]
        except sqlite3.Error as e:
            error = f"sqlite: {e}"
        except Exception as e:  # defensive — don't let one stack crash the page
            logger.warning("admin_stacks: gather failed for %s: %s", stack.handle, e)
            error = f"{type(e).__name__}: {e}"

    last_triage_complete: datetime | None = None
    last_triage_failed: datetime | None = None
    last_aichat: datetime | None = None
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
            if ev == "pipeline_complete":
                if last_triage_complete is None:
                    last_triage_complete = ts
                if ts >= cutoff_24h:
                    success_24h += 1
            elif ev == "pipeline_terminated":
                if last_triage_failed is None:
                    last_triage_failed = ts
                if ts >= cutoff_24h:
                    failure_24h += 1
            elif ev == "aichat_failure":
                if last_aichat is None:
                    last_aichat = ts
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
        last_aichat_failure=last_aichat,
        last_discovery_failed=last_discovery,
        last_prep_failed=last_prep,
        triage_success_24h=success_24h,
        triage_failure_24h=failure_24h,
        stage_counts=stage_counts,
        stuck_prep_count=stuck_prep_count,
        db_missing=db_missing,
        jsonl_missing=jsonl_missing,
        error=error,
        freshness=_freshness(last_triage_complete, now),
    )


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _freshness(last: datetime | None, now: datetime) -> Freshness:
    if last is None:
        return "unknown"
    age = now - last
    if age < timedelta(hours=24):
        return "fresh"
    if age < timedelta(hours=36):
        return "late"
    return "stale"
