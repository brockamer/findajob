"""Operator diagnostic — surface errors and stale automations."""

import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from findajob.notifications.ntfy import db_connect, recent_log_events, send
from findajob.paths import BASE

REVIEW_BACKLOG_WARN = 100  # warn if manual_review backlog exceeds this
TARGET_LOWSCORE_DAYS = 7  # check for mis-scored target company jobs within this window

# Legacy top-level keys on jobs_fetched events (pre-2026-05-04). After the
# adapter-extraction migration (#410.5), per-source counts moved into a nested
# ``adapters: {...}`` dict. _extract_source_counts handles both shapes so the
# 7-day baseline window (which spans the migration boundary) keeps working.
_LEGACY_TOP_LEVEL_SOURCE_KEYS: tuple[str, ...] = ("greenhouse", "ashby", "lever", "jobsapi", "gmail")


def _extract_source_counts(event: dict) -> dict[str, int]:
    """Return per-source counts from a jobs_fetched event, handling shape drift.

    Three shapes seen in the wild:
    - New (post-2026-05-10): counts entirely inside ``event["adapters"]``.
    - Legacy (pre-2026-05-04): counts as top-level keys (greenhouse/ashby/lever/jobsapi/gmail).
    - Hybrid (2026-05-04→2026-05-09): some keys top-level, some inside adapters.

    Takes the max when both locations carry a count for the same source —
    defensive against the hybrid window where one location may be stale.
    """
    counts: dict[str, int] = {}
    adapters = event.get("adapters")
    if isinstance(adapters, dict):
        for name, v in adapters.items():
            if isinstance(v, int):
                counts[name] = v
    for k in _LEGACY_TOP_LEVEL_SOURCE_KEYS:
        v = event.get(k)
        if isinstance(v, int):
            counts[k] = max(counts.get(k, 0), v)
    return counts


def _detect_dead_feeds(window_events: list[dict], baseline_events: list[dict]) -> list[str]:
    """Return source names present in the 25h window with 0 max but >0 in baseline.

    Source names are enumerated dynamically from event content — new adapters
    (e.g., #617 WorkdayCXS, future ones) are auto-covered without code changes.
    A source is "dead" only when it's CONFIGURED (present in window events,
    even at 0) AND PRODUCING ZERO across all window runs AND had >0 in
    baseline. A source absent from every window event is treated as
    deliberately disabled (e.g., dropped from active_sources.txt), not dead.

    This correctly tolerates gmail oscillation (some runs return 0, some
    return 30 — window_max sees the 30 and skips the warning).
    """
    window_max: dict[str, int] = {}
    window_seen: set[str] = set()
    for e in window_events:
        if e.get("event") != "jobs_fetched":
            continue
        for name, v in _extract_source_counts(e).items():
            window_seen.add(name)
            if v > window_max.get(name, 0):
                window_max[name] = v
    baseline_max: dict[str, int] = {}
    for e in baseline_events:
        if e.get("event") != "jobs_fetched":
            continue
        for name, v in _extract_source_counts(e).items():
            if v > baseline_max.get(name, 0):
                baseline_max[name] = v
    return sorted(
        name for name, peak in baseline_max.items() if peak > 0 and name in window_seen and window_max.get(name, 0) == 0
    )


def cmd_health_check() -> None:
    events = recent_log_events(hours=25)

    # Check triage ran (triage.py logs 'pipeline_complete')
    triage_events = [e for e in events if e.get("event") == "pipeline_complete"]
    triage_ok = bool(triage_events)

    # Check if triage was terminated (SIGTERM from systemd timeout or manual stop)
    triage_terminated = [e for e in events if e.get("event") == "pipeline_terminated"]

    # Check watchdog ran
    poll_events = [e for e in events if e.get("event") == "watchdog_run"]
    poll_ok = bool(poll_events)

    # Error events
    error_events = [
        e for e in events if any(k in e for k in ("error", "exception", "failed")) or "error" in e.get("event", "")
    ]

    # score=None events
    null_score = [e for e in events if e.get("score") is None and "job_scored" in e.get("event", "")]

    issues: list[str] = []
    if triage_terminated:
        issues.append(
            "ERROR: triage was terminated (SIGTERM) — likely systemd timeout. "
            "Check TimeoutStartSec on findajob-triage.service."
        )
        for e in triage_terminated[:2]:
            issues.append(f"  • {e.get('ts', '?')}: {e.get('note', '?')}")
    elif not triage_ok:
        issues.append("WARN: pipeline_complete not seen in last 25h")
    if not poll_ok:
        issues.append("WARN: watchdog_run not seen in last 25h")
    if error_events:
        issues.append(f"ERRORS: {len(error_events)} error events in log")
        for e in error_events[:3]:
            issues.append(f"  • [{e.get('event', '?')}] {e.get('error', e.get('reason', e.get('note', '')))}")
    if null_score:
        issues.append(f"INFO: {len(null_score)} jobs scored None (likely LLM timeout)")

    # ── Dead feed detection ─────────────────────────────────────────────
    # Warn if a source returned 0 jobs across ALL runs in the 25h window but
    # had >0 in the last 7 days — indicates a feed silently broke.
    # Using the window max (not latest-only) avoids false positives when a
    # mid-day manual run follows a healthy scheduled run: if any run in the
    # window produced jobs, the source is not dead. Source names are enumerated
    # dynamically from each event's adapters dict (and legacy top-level keys
    # for events older than the 2026-05-10 schema migration) — new adapters
    # auto-get health-check coverage without a code edit (#637).
    fetch_events = [e for e in events if e.get("event") == "jobs_fetched"]
    if fetch_events:
        week_events = recent_log_events(hours=24 * 7)
        dead_feeds = _detect_dead_feeds(fetch_events, week_events)
        if dead_feeds:
            # Recompute baseline-max for the operator-facing message
            baseline_max: dict[str, int] = {}
            for e in week_events:
                if e.get("event") != "jobs_fetched":
                    continue
                for name, v in _extract_source_counts(e).items():
                    if v > baseline_max.get(name, 0):
                        baseline_max[name] = v
            issues.append(
                f"WARN: {len(dead_feeds)} source(s) returned 0 jobs across all runs in the last 25h "
                f"despite producing jobs in the last 7d — likely silent feed failure:"
            )
            for k in dead_feeds:
                issues.append(f"  • {k}: 0 across all runs today, peak {baseline_max[k]} in last 7d")

    # ── System resource checks ──────────────────────────────────────────
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
        mem_total_mb = meminfo.get("MemTotal", 0) // 1024
        mem_avail_mb = meminfo.get("MemAvailable", 0) // 1024
        swap_total_mb = meminfo.get("SwapTotal", 0) // 1024
        swap_free_mb = meminfo.get("SwapFree", 0) // 1024
        swap_used_mb = swap_total_mb - swap_free_mb

        if mem_avail_mb < 256:
            issues.append(f"WARN: low memory — {mem_avail_mb} MB available of {mem_total_mb} MB")
        if swap_total_mb > 0 and swap_used_mb > swap_total_mb * 0.5:
            issues.append(f"WARN: high swap usage — {swap_used_mb}/{swap_total_mb} MB used")
    except Exception:
        pass  # /proc/meminfo unavailable — skip

    # ── Queue health checks ──────────────────────────────────────────────
    conn = db_connect()

    # Manual review backlog — split by cause for operator clarity
    null_score_count = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage = 'manual_review' AND relevance_score IS NULL"
    ).fetchone()[0]
    real_review_count = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage = 'manual_review' AND relevance_score IS NOT NULL"
    ).fetchone()[0]
    if null_score_count > 0:
        issues.append(
            f"WARN: {null_score_count} null-score jobs in manual_review "
            "(scorer failure — check OpenRouter / pipeline.jsonl)"
        )
    if real_review_count > REVIEW_BACKLOG_WARN:
        issues.append(
            f"WARN: {real_review_count} real-flag jobs in manual_review backlog (threshold: {REVIEW_BACKLOG_WARN})"
        )

    # Target company jobs scored 3-6 in the last N days (potential mis-scores worth reviewing).
    # Score 1-2 are excluded — prefilter hard rejects or clear mismatches, not actionable.
    from findajob.config_loader import is_company_of_interest

    cutoff = (datetime.now(UTC) - timedelta(days=TARGET_LOWSCORE_DAYS)).isoformat()
    low_target = conn.execute(
        """
        SELECT title, company, relevance_score FROM jobs
        WHERE relevance_score BETWEEN 3 AND 6
          AND created_at >= ?
          AND stage IN ('scored', 'manual_review')
    """,
        (cutoff,),
    ).fetchall()
    mis_scored = [
        (r["title"], r["company"], r["relevance_score"]) for r in low_target if is_company_of_interest(r["company"])
    ]
    if mis_scored:
        issues.append(f"REVIEW: {len(mis_scored)} target-company job(s) scored 3-6 in last {TARGET_LOWSCORE_DAYS}d:")
        for title, company, score in mis_scored[:5]:
            issues.append(f"  • {company}: {title} (score={score})")

    # ── Duplicate company folders ────────────────────────────────────────────
    companies_dir = os.path.join(BASE, "companies")
    folder_names = [
        d
        for d in os.listdir(companies_dir)
        if not d.startswith(("_", ".")) and os.path.isdir(os.path.join(companies_dir, d))
    ]
    # Strip timestamp suffix to find duplicates (same company_title_date, different HHMMSS)
    prefixes = [name.rsplit("_", 1)[0] for name in folder_names]
    dupes = {p: n for p, n in Counter(prefixes).items() if n > 1}
    if dupes:
        issues.append(f"WARN: {len(dupes)} duplicate company folder set(s):")
        for prefix, count in list(dupes.items())[:5]:
            issues.append(f"  • {prefix} ({count} copies)")

    # ── Orphan folders (on disk but no DB record points to them) ────────────
    db_paths = {
        r[0]
        for r in conn.execute(
            "SELECT prep_folder_path FROM jobs WHERE prep_folder_path IS NOT NULL AND prep_folder_path != ''"
        ).fetchall()
    }
    orphan_folders = [name for name in folder_names if os.path.join(companies_dir, name) not in db_paths]
    if orphan_folders:
        issues.append(f"WARN: {len(orphan_folders)} folder(s) in companies/ with no matching DB record:")
        for name in orphan_folders[:5]:
            issues.append(f"  • {name}")

    # ── Stuck prep_in_progress jobs ──────────────────────────────────────────
    stuck_cutoff = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    stuck = conn.execute(
        """
        SELECT title, company, stage_updated FROM jobs
        WHERE stage = 'prep_in_progress' AND stage_updated < ?
    """,
        (stuck_cutoff,),
    ).fetchall()
    if stuck:
        issues.append(f"WARN: {len(stuck)} job(s) stuck in prep_in_progress >1h:")
        for r in stuck[:5]:
            issues.append(f"  • {r['company']}: {r['title']}")

    # ── Orphaned prep_folder_path (DB points to missing dir) ─────────────────
    prepped = conn.execute("""
        SELECT title, company, prep_folder_path FROM jobs
        WHERE prep_folder_path IS NOT NULL AND prep_folder_path != ''
          AND stage NOT IN ('rejected', 'withdrawn')
          AND (dupe_of = '' OR dupe_of IS NULL)
    """).fetchall()
    orphaned = [r for r in prepped if not Path(r["prep_folder_path"]).is_dir()]
    if orphaned:
        issues.append(f"WARN: {len(orphaned)} job(s) have prep_folder_path pointing to missing dir:")
        for r in orphaned[:5]:
            issues.append(f"  • {r['company']}: {r['title']}")

    # ── Stage/folder location mismatches ─────────────────────────────────────
    mismatches_rows = conn.execute("""
        SELECT title, company, stage, prep_folder_path FROM jobs
        WHERE prep_folder_path IS NOT NULL AND prep_folder_path != ''
          AND (dupe_of = '' OR dupe_of IS NULL)
    """).fetchall()
    mismatch_count = 0
    for r in mismatches_rows:
        path = r["prep_folder_path"]
        stage = r["stage"]
        if stage == "applied" and "/_applied/" not in path:
            mismatch_count += 1
        elif stage == "not_selected" and "/_applied/" not in path:
            mismatch_count += 1
        elif stage == "waitlisted" and "/_waitlisted/" not in path:
            mismatch_count += 1
        elif stage == "rejected" and path and "/_rejected/" not in path:
            mismatch_count += 1
    if mismatch_count:
        issues.append(f"WARN: {mismatch_count} job(s) with stage/folder location mismatch")

    conn.close()

    if not issues:
        return

    body = "\n".join(issues)
    priority = "high" if any("ERROR" in i for i in issues) else "default"
    send("💼 findajob — health check", body, priority=priority, tags="warning", kind="health_check")
