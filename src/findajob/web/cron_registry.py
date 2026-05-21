"""Curated registry of cron tiles for the `/tools/` operator panel (#650).

A *cron tile* is a manually-triggerable scheduled job. The registry is a
hand-curated list, NOT auto-derived from `ops/scheduled-jobs.yaml` —
the yaml owns the schedule, this module owns "which crons get a button
and how does the button behave."

Adding a new tile = appending a `CronTile(...)` entry below. The new
tile gets a button at `/tools/` for free; the dispatcher in
`findajob.web.cron_dispatch` handles spawn + concurrency + gate.

See spec §2.3 for the coverage rationale (which crons are in, which
are deliberately excluded).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from findajob.admin.jsonl_tail import tail_events


@dataclass(frozen=True)
class CronTile:
    slug: str = ""
    label: str = ""
    description: str = ""
    script_path: str = ""  # relative to BASE; the dispatcher prepends sys.executable + BASE/.
    args: tuple[str, ...] = ()  # extra argv after the script path (e.g. notify.py subcommand)
    enabled: bool = True
    confirm_required: bool = False
    gated_by_spend_ceiling: bool = False
    max_runtime_minutes: int = 10
    cost_estimate_fn: Callable[[sqlite3.Connection], str] | None = None


# Placeholder cost-estimate functions — return literal strings for v1.
# Real implementations can be wired in a follow-up (issue body asks for
# "est. $X based on last run" but the math lives in cost_rollups; the
# stubs unblock the UI without coupling this PR to cost_rollups changes).
def _triage_cost_estimate(_conn: sqlite3.Connection) -> str:
    return "—"


def _discover_cost_estimate(_conn: sqlite3.Connection) -> str:
    return "—"


CRON_TILES: list[CronTile] = [
    CronTile(
        slug="triage",
        label="Run daily triage",
        description="Fetch jobs from all active sources, score, persist.",
        script_path="scripts/triage.py",
        enabled=True,
        confirm_required=True,
        gated_by_spend_ceiling=True,
        max_runtime_minutes=120,
        cost_estimate_fn=_triage_cost_estimate,
    ),
    CronTile(
        slug="detect-rejections",
        label="Scan Gmail for rejections",
        description="Pull new rejection emails into /board/rejections-review/.",
        script_path="scripts/detect_rejections.py",
        enabled=True,
        confirm_required=False,
        gated_by_spend_ceiling=False,
        max_runtime_minutes=10,
        cost_estimate_fn=None,
    ),
    CronTile(
        slug="discover",
        label="Re-run company discovery",
        description="Competency-driven company discovery (writes candidate_context/discovered_companies.*).",
        script_path="scripts/discover_companies.py",
        enabled=True,
        confirm_required=True,
        gated_by_spend_ceiling=True,
        max_runtime_minutes=10,
        cost_estimate_fn=_discover_cost_estimate,
    ),
    CronTile(
        slug="notify-health",
        label="Send health-check ntfy",
        description="Backlog + silent-source warnings to your phone.",
        script_path="scripts/notify.py",
        args=("health-check",),
        enabled=True,
        confirm_required=False,
        gated_by_spend_ceiling=False,
        max_runtime_minutes=2,
        cost_estimate_fn=None,
    ),
    CronTile(
        slug="notify-stats",
        label="Send daily-stats ntfy",
        description="Preview today's stats push on demand.",
        script_path="scripts/notify.py",
        args=("daily-stats",),
        enabled=True,
        confirm_required=False,
        gated_by_spend_ceiling=False,
        max_runtime_minutes=2,
        cost_estimate_fn=None,
    ),
    CronTile(
        slug="watchdog",
        label="Sweep stuck prep jobs",
        description="Reset stages stuck in prep_in_progress > 60 min.",
        script_path="scripts/watchdog.py",
        enabled=True,
        confirm_required=False,
        gated_by_spend_ceiling=False,
        max_runtime_minutes=15,
        cost_estimate_fn=None,
    ),
    CronTile(
        slug="notify-scoreboard",
        label="Send weekly scoreboard ntfy",
        description="Currently disabled in scheduled-jobs.yaml — re-enable there first.",
        script_path="scripts/notify.py",
        args=("scoreboard",),
        enabled=False,
        confirm_required=False,
        gated_by_spend_ceiling=False,
        max_runtime_minutes=2,
        cost_estimate_fn=None,
    ),
]


CRON_TILES_BY_SLUG: dict[str, CronTile] = {t.slug: t for t in CRON_TILES}


def _log_path(base_root: Path) -> Path:
    return base_root / "logs" / "pipeline.jsonl"


def _parse_ts(raw: str) -> datetime | None:
    """Parse an event timestamp. `findajob.audit.log_event` writes
    `datetime.now(UTC).isoformat()` — fromisoformat (3.11+) accepts
    both `+00:00` offsets and `Z` suffixes.
    """
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def is_currently_running(slug: str, base_root: Path) -> bool:
    """True when a `cron_started` event for `slug` has no matching
    `cron_finished` after it AND is within `max_runtime_minutes` for
    that slug. See spec §4.3.
    """
    tile = CRON_TILES_BY_SLUG.get(slug)
    if tile is None:
        return False

    # tail_events yields newest-first. Walk it: the first cron event we
    # see for this slug determines state.
    for ev in tail_events(_log_path(base_root)):
        if ev.get("cron") != slug:
            continue
        if ev.get("event") == "cron_finished":
            return False
        if ev.get("event") == "cron_started":
            ts = _parse_ts(ev.get("ts", ""))
            if ts is None:
                return False
            age = datetime.now(UTC) - ts
            return age < timedelta(minutes=tile.max_runtime_minutes)
    return False


def last_run_at(slug: str, base_root: Path) -> str | None:
    """Timestamp of the most recent `cron_finished` event for `slug`,
    or None when the cron has never finished.
    """
    for ev in tail_events(_log_path(base_root)):
        if ev.get("event") == "cron_finished" and ev.get("cron") == slug:
            return ev.get("ts")
    return None
