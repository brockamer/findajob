#!/usr/bin/env python3
# ~/JobSearchPipeline/scripts/notify.py
"""
ntfy push notification suite for the JobSearchPipeline.

Usage:
  notify.py daily-stats      — morning stats: queue depth, recent activity
  notify.py health-check     — surface errors from logs, confirm automations ran
  notify.py issues-ping      — open GitHub issues (run every other day)
  notify.py apply-reminder   — humorous daily nudge to submit at least one application
  notify.py feedback-review  — alert when feedback_log has enough data to be useful

ntfy topic is read from NTFY_TOPIC in data/.env, or falls back to NTFY_TOPIC env var.
"""

import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from findajob.paths import BASE
from findajob.utils import load_env

DB_PATH = f"{BASE}/data/pipeline.db"
LOG_PATH = f"{BASE}/logs/pipeline.jsonl"
REPO_SLUG = "brockamer/findajob"

# ── Load ntfy topic ────────────────────────────────────────────────────────────
_env = load_env()
NTFY_TOPIC = _env.get("NTFY_TOPIC") or os.environ.get("NTFY_TOPIC", "jobsearch-pipeline")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"


def send(title, body, priority="default", tags=None):
    """Send a push notification via ntfy.sh."""
    headers = [
        "-H",
        f"Title: {title}",
        "-H",
        f"Priority: {priority}",
    ]
    if tags:
        headers += ["-H", f"Tags: {tags}"]
    subprocess.run(
        ["curl", "-s", "-X", "POST", NTFY_URL, "-H", "Content-Type: text/plain; charset=utf-8", *headers, "-d", body],
        check=False,
        capture_output=True,
    )


# ── Helpers ────────────────────────────────────────────────────────────────────
def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def recent_log_events(hours=25):
    """Return log entries from the last N hours."""
    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    events = []
    try:
        with open(LOG_PATH) as f:
            for line in f:
                try:
                    e = json.loads(line)
                    if e.get("ts", "") >= cutoff:
                        events.append(e)
                except json.JSONDecodeError:
                    pass
    except FileNotFoundError:
        pass
    return events


def open_issues():
    """Fetch open issues from GitHub via `gh issue list`."""
    try:
        rc = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                REPO_SLUG,
                "--state",
                "open",
                "--json",
                "number,title,labels",
                "--limit",
                "50",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if rc.returncode != 0:
            return []
        import json as _json

        items = _json.loads(rc.stdout)
        results = []
        for item in items:
            labels = ", ".join(lbl["name"] for lbl in item.get("labels", []))
            tag = f" [{labels}]" if labels else ""
            results.append(f"#{item['number']}: {item['title']}{tag}")
        return results
    except Exception:
        return []


# ── Commands ───────────────────────────────────────────────────────────────────
def cmd_daily_stats():
    conn = db_connect()

    # Dashboard queue (score>=7 unprepped, plus all materials_drafted)
    queue_count = conn.execute("""
        SELECT COUNT(*) FROM jobs
        WHERE (dupe_of = '' OR dupe_of IS NULL)
          AND (
            (relevance_score >= 7 AND stage IN ('scored', 'manual_review'))
            OR stage = 'materials_drafted'
          )
    """).fetchone()[0]

    # Jobs flagged but not yet prepped
    flagged_unprepped = conn.execute("""
        SELECT COUNT(*) FROM jobs
        WHERE apply_flag = 1
          AND stage NOT IN ('materials_drafted', 'applied', 'rejected', 'withdrawn')
    """).fetchone()[0]

    # Jobs prepped
    prepped = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'materials_drafted'").fetchone()[0]

    # Jobs applied
    applied = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'applied'").fetchone()[0]

    # Jobs rejected via dashboard (user) vs not selected (company)
    rejected = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'rejected'").fetchone()[0]
    not_selected = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'not_selected'").fetchone()[0]

    # New jobs scored in last 24h
    cutoff_24h = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
    new_today = conn.execute(
        """
        SELECT COUNT(*) FROM jobs
        WHERE relevance_score IS NOT NULL
          AND updated_at >= ?
          AND stage IN ('scored', 'manual_review')
    """,
        (cutoff_24h,),
    ).fetchone()[0]

    # Total in DB
    total = conn.execute("SELECT COUNT(*) FROM jobs WHERE dupe_of = '' OR dupe_of IS NULL").fetchone()[0]

    conn.close()

    lines = [
        f"Queue (score≥7, unprepped): {queue_count} jobs",
        f"Flagged, awaiting prep:     {flagged_unprepped}",
        f"Materials drafted:          {prepped}",
        f"Applied:                    {applied}",
        f"Rejected (user):            {rejected}",
        f"Not selected (company):     {not_selected}",
        f"New jobs scored today:      {new_today}",
        f"Total in pipeline:          {total}",
    ]
    body = "\n".join(lines)
    send("JSP Daily Stats", body, priority="default", tags="bar_chart")


SHEET1_ROW_WARN = 5000  # warn if Sheet1 would sync more than this many rows
REVIEW_BACKLOG_WARN = 100  # warn if manual_review backlog exceeds this
TARGET_LOWSCORE_DAYS = 7  # check for mis-scored target company jobs within this window


def cmd_health_check():
    events = recent_log_events(hours=25)

    # Check triage ran (triage.py logs 'pipeline_complete')
    triage_events = [e for e in events if e.get("event") == "pipeline_complete"]
    triage_ok = bool(triage_events)

    # Check if triage was terminated (SIGTERM from systemd timeout or manual stop)
    triage_terminated = [e for e in events if e.get("event") == "pipeline_terminated"]

    # Check poller ran
    poll_events = [e for e in events if e.get("event") == "poll_flags"]
    poll_ok = bool(poll_events)

    # Check sync_sheet ran
    sync_events = [e for e in events if e.get("event") == "sync_complete"]
    sync_ok = bool(sync_events)
    sync_failures = [e for e in events if e.get("event") == "sync_failed"]

    # Error events
    error_events = [
        e for e in events if any(k in e for k in ("error", "exception", "failed")) or "error" in e.get("event", "")
    ]

    # score=None events
    null_score = [e for e in events if e.get("score") is None and "job_scored" in e.get("event", "")]

    issues = []
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
        issues.append("WARN: poll_flags not seen in last 25h")
    if not sync_ok:
        issues.append("WARN: sync_complete not seen in last 25h")
    if sync_failures:
        issues.append(f"ERROR: {len(sync_failures)} sync_sheet failure(s) in last 25h")
        for e in sync_failures[:2]:
            issues.append(f"  • {e.get('error', 'unknown')}")
    if error_events:
        issues.append(f"ERRORS: {len(error_events)} error events in log")
        for e in error_events[:3]:
            issues.append(f"  • [{e.get('event', '?')}] {e.get('error', e.get('note', ''))}")
    if null_score:
        issues.append(f"INFO: {len(null_score)} jobs scored None (likely LLM timeout)")

    # ── Dead feed detection ─────────────────────────────────────────────
    # Warn if any source returned 0 jobs in the latest triage but had >0 in
    # the last 7 days — indicates a feed silently broke.
    # Sources monitored: keys in the jobs_fetched event.
    SOURCE_KEYS = ("greenhouse", "ashby", "lever", "jobsapi", "gmail")
    latest_fetch = None
    for e in reversed(events):
        if e.get("event") == "jobs_fetched":
            latest_fetch = e
            break
    if latest_fetch:
        # Look back 7 days for the baseline — the source had to have produced
        # jobs at some point recently for its zero today to be suspicious.
        week_events = recent_log_events(hours=24 * 7)
        week_max_per_source = {k: 0 for k in SOURCE_KEYS}
        for e in week_events:
            if e.get("event") != "jobs_fetched":
                continue
            for k in SOURCE_KEYS:
                v = e.get(k)
                if isinstance(v, int) and v > week_max_per_source[k]:
                    week_max_per_source[k] = v
        dead_feeds = [k for k in SOURCE_KEYS if latest_fetch.get(k, 0) == 0 and week_max_per_source[k] > 0]
        if dead_feeds:
            issues.append(
                f"WARN: {len(dead_feeds)} source(s) returned 0 jobs in latest triage "
                f"despite producing jobs in the last 7d — likely silent feed failure:"
            )
            for k in dead_feeds:
                issues.append(f"  • {k}: 0 today, peak {week_max_per_source[k]} in last 7d")

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

    # ── Sheet / queue health checks ──────────────────────────────────────
    conn = db_connect()

    # Sheet1 row count (approximate — same filter as sync_sheet.py)
    sheet1_count = conn.execute("""
        SELECT COUNT(*) FROM jobs
        WHERE (dupe_of = '' OR dupe_of IS NULL)
          AND (
            relevance_score >= 5
            OR stage IN ('manual_review', 'materials_drafted', 'waitlisted',
                         'applied', 'interview', 'offer', 'not_selected', 'withdrawn')
            OR julianday('now') - julianday(created_at) <= 14
          )
    """).fetchone()[0]
    if sheet1_count > SHEET1_ROW_WARN:
        issues.append(f"WARN: Sheet1 has ~{sheet1_count} rows (threshold: {SHEET1_ROW_WARN})")

    # Manual review backlog — split by cause for operator clarity
    null_score_count = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage = 'manual_review' AND relevance_score IS NULL"
    ).fetchone()[0]
    real_review_count = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage = 'manual_review' AND relevance_score IS NOT NULL"
    ).fetchone()[0]
    if null_score_count > 0:
        issues.append(f"WARN: {null_score_count} null-score jobs in manual_review (scorer failure — check aichat-ng)")
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
        d for d in os.listdir(companies_dir) if not d.startswith("_") and os.path.isdir(os.path.join(companies_dir, d))
    ]
    # Strip timestamp suffix to find duplicates (same company_title_date, different HHMMSS)
    from collections import Counter

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
        body = "All systems nominal.\nTriage ran. Poller ran. Sheet synced. No errors in last 25h."
        priority = "low"
        tags = "white_check_mark"
    else:
        body = "\n".join(issues)
        priority = "high" if any("ERROR" in i for i in issues) else "default"
        tags = "warning"

    send("JSP Health Check", body, priority=priority, tags=tags)


def cmd_issues_ping():
    issues = open_issues()
    if not issues:
        body = "No open issues on GitHub."
        tags = "white_check_mark"
    else:
        lines = [f"{len(issues)} open issue(s):"]
        for i, iss in enumerate(issues, 1):
            lines.append(f"{i}. {iss}")
        body = "\n".join(lines)
        tags = "memo"
    send("JSP Open Issues", body, priority="default", tags=tags)


def cmd_apply_reminder():
    QUIPS = [
        "The perfect resume is the enemy of the submitted one. Go click Apply.",
        "GPT-6 isn't submitting your application. You are. Open a tab.",
        "Somewhere a hiring manager is waiting for your resume. Don't keep them waiting.",
        "Every application you don't submit is a job you definitely didn't get.",
        "The pipeline doesn't apply for you. That's still a manual step. Do it.",
        "Your future self is staring at you. They look annoyed. Apply to something.",
        "DeepSeek scored it a 9. Your mouse is scored a 0. Click Apply.",
        "Reject the fear of rejection. Apply anyway. Preferably today.",
        "Fun fact: 0% of jobs you don't apply to result in interviews.",
        "The Dashboard is not an art installation. It has checkboxes for a reason.",
    ]
    # Rotate by day-of-year in PT so the quip changes at midnight Pacific
    from zoneinfo import ZoneInfo

    day_index = datetime.now(ZoneInfo("America/Los_Angeles")).timetuple().tm_yday % len(QUIPS)
    quip = QUIPS[day_index]

    # Pull real counts for the daily checklist
    conn = db_connect()
    n_dashboard = conn.execute("""
        SELECT COUNT(*) FROM jobs
        WHERE (dupe_of = '' OR dupe_of IS NULL)
          AND relevance_score >= 7 AND stage IN ('scored', 'manual_review')
    """).fetchone()[0]
    n_ready = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'materials_drafted'").fetchone()[0]
    n_review = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'manual_review'").fetchone()[0]
    n_applied = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'applied'").fetchone()[0]
    n_waitlisted = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'waitlisted'").fetchone()[0]
    conn.close()

    checklist = (
        f"\n---\n"
        f"1. Dashboard: {n_dashboard} high-score jobs to Flag for Prep or Reject\n"
        f"2. Ready to Apply: {n_ready} jobs with materials drafted — review and submit\n"
        f"3. Review tab: {n_review} jobs in manual review — Promote or Reject\n"
        f"4. Scan Sheet1 for mis-scored target company jobs\n"
        f"5. Check ntfy health notification for pipeline warnings\n"
        f"---\n"
        f"Applied so far: {n_applied}\n"
        f"Waitlisted: {n_waitlisted} deferred jobs"
    )

    send("Apply To Something Today", quip + checklist, priority="default", tags="rocket")


def cmd_feedback_review():
    import subprocess as sp

    THRESHOLD = 10

    conn = db_connect()
    count = conn.execute("SELECT COUNT(*) FROM feedback_log").fetchone()[0]
    conn.close()

    if count < THRESHOLD:
        print(f"feedback_log has {count} entries — below threshold ({THRESHOLD}). No ping sent.")
        return

    # Run analyze_feedback.py and capture key stats for the notification
    try:
        result = sp.run(
            [sys.executable, f"{BASE}/scripts/analyze_feedback.py", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        data = json.loads(result.stdout) if result.stdout else {}
    except Exception:
        data = {}

    if data and not data.get("error"):
        fp = data.get("false_positives", 0)
        fp_pct = data.get("fp_pct", 0)
        top_reason = data["by_reason"][0] if data.get("by_reason") else ("?", 0)
        top_company = data["company_fp_counts"][0] if data.get("company_fp_counts") else ("?", 0)
        bad_kws = [
            kw["keyword"] for kw in data.get("keyword_signals", []) if kw["ratio"] < 0.4 and kw["rejected_n"] >= 3
        ][:4]
        body = (
            f"{count} rejections in feedback_log\n"
            f"False positives (score 8+): {fp} ({fp_pct}%)\n"
            f"Top reason: {top_reason[0]} ({top_reason[1]})\n"
            f"Top FP company: {top_company[0]} ({top_company[1]} rejections)\n"
            + (f"Prefilter candidates: {', '.join(bad_kws)}\n" if bad_kws else "")
            + "Run: python3 scripts/analyze_feedback.py"
        )
    else:
        body = f"feedback_log has {count} rejection entries.\nRun: python3 scripts/analyze_feedback.py"

    send("JSP Feedback Analysis", body, priority="default", tags="magnifying")


def cmd_send_raw():
    """Send a raw notification. Usage: notify.py send-raw <title> <body>"""
    if len(sys.argv) < 4:
        print("Usage: notify.py send-raw <title> <body>")
        sys.exit(1)
    send(sys.argv[2], sys.argv[3], priority="default", tags="hourglass_flowing_sand")


def cmd_ci_check():
    """Check GitHub Actions CI status and notify on the most recent failure.

    Only alerts if the latest completed main-branch run failed.
    Stays quiet if the latest run is green (prior failures are stale).
    """
    result = subprocess.run(
        ["gh", "run", "list", "--limit", "5", "--json", "conclusion,headBranch,displayTitle,url,status"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        send("JSP CI Check", f"gh run list failed: {result.stderr[:200]}", priority="default", tags="warning")
        return

    import json as _json

    try:
        runs = _json.loads(result.stdout)
    except _json.JSONDecodeError:
        return

    # Find the most recent completed run on main
    latest = next(
        (r for r in runs if r.get("headBranch") == "main" and r.get("status") == "completed"),
        None,
    )
    if not latest or latest.get("conclusion") != "failure":
        return  # latest is green or in progress, stay quiet

    lines = [
        "Latest CI run failed:",
        f"  {latest.get('displayTitle', '?')}",
        f"  {latest.get('url', '')}",
    ]
    send("JSP CI Failure", "\n".join(lines), priority="high", tags="x")


SCOREBOARD_ISSUE = 31
SCOREBOARD_REPO = "brockamer/findajob"


def cmd_scoreboard():
    """Regenerate the pipeline funnel scoreboard and update issue #31."""
    conn = db_connect()
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    # ── Funnel counts ──
    total = conn.execute("SELECT COUNT(*) FROM jobs WHERE dupe_of = '' OR dupe_of IS NULL").fetchone()[0]
    scored = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE relevance_score IS NOT NULL AND (dupe_of = '' OR dupe_of IS NULL)"
    ).fetchone()[0]
    s7 = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE relevance_score >= 7 AND (dupe_of = '' OR dupe_of IS NULL)"
    ).fetchone()[0]
    prepped = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE prep_folder_path IS NOT NULL AND prep_folder_path != ''"
    ).fetchone()[0]
    applied = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage IN ('applied','interview','offer','not_selected')"
    ).fetchone()[0]
    interview = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage IN ('interview','offer')").fetchone()[0]
    offer = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'offer'").fetchone()[0]

    # ── Conversion rates ──
    hit_rate = f"{s7 / scored * 100:.1f}" if scored else "0"
    prep_rate = f"{prepped / s7 * 100:.0f}" if s7 else "0"
    apply_rate = f"{applied / prepped * 100:.0f}" if prepped else "0"
    interview_rate = f"{interview / applied * 100:.0f}" if applied else "0"

    # ── Current queue ──
    ready = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage = 'materials_drafted' AND (dupe_of = '' OR dupe_of IS NULL)"
    ).fetchone()[0]
    waitlisted = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'waitlisted'").fetchone()[0]
    user_rejected = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'rejected'").fetchone()[0]
    feedback_entries = conn.execute("SELECT COUNT(*) FROM feedback_log").fetchone()[0]

    # ── Score distribution ──
    dist_rows = conn.execute("""
        SELECT relevance_score, COUNT(*) as cnt FROM jobs
        WHERE relevance_score IS NOT NULL AND (dupe_of = '' OR dupe_of IS NULL)
        GROUP BY relevance_score ORDER BY relevance_score
    """).fetchall()

    # ── Attrition ──
    score1 = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE relevance_score = 1 AND (dupe_of = '' OR dupe_of IS NULL)"
    ).fetchone()[0]
    score2_6 = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE relevance_score BETWEEN 2 AND 6 AND (dupe_of = '' OR dupe_of IS NULL)"
    ).fetchone()[0]
    rejected_after_prep = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage = 'rejected' AND prep_folder_path IS NOT NULL AND prep_folder_path != ''"
    ).fetchone()[0]
    waitlisted_after_prep = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE stage = 'waitlisted'"
        " AND prep_folder_path IS NOT NULL AND prep_folder_path != ''"
    ).fetchone()[0]
    not_selected = conn.execute("SELECT COUNT(*) FROM jobs WHERE stage = 'not_selected'").fetchone()[0]

    # ── Low-signal feeds (last 7d) ──
    # Companies producing ≥20 scored jobs in 7 days with 0 scoring 7+ are strong
    # candidates for removal from feed_urls.txt. The scorer is working correctly
    # for these — the feed is genuinely off-target for the user's profile.
    low_signal_rows = conn.execute("""
        SELECT
            company,
            COUNT(*) AS total,
            GROUP_CONCAT(DISTINCT source) AS sources
        FROM jobs
        WHERE julianday('now') - julianday(created_at) <= 7
          AND (dupe_of = '' OR dupe_of IS NULL)
          AND relevance_score IS NOT NULL
          AND company != ''
        GROUP BY company
        HAVING total >= 20 AND SUM(CASE WHEN relevance_score >= 7 THEN 1 ELSE 0 END) = 0
        ORDER BY total DESC
    """).fetchall()

    conn.close()

    # ── Build markdown ──
    dist_table = "| Score | Count | % |\n|-------|-------|---|\n"
    for r in dist_rows:
        pct = f"{r['cnt'] / scored * 100:.1f}" if scored else "0"
        dist_table += f"| {r['relevance_score']} | {r['cnt']:,} | {pct}% |\n"

    hit_health = "healthy" if 2 <= float(hit_rate) <= 5 else "review needed"

    if low_signal_rows:
        low_signal_section = (
            "Companies with ≥20 scored jobs in the last 7d and 0 scoring 7+. "
            "Strong candidates for removal from `feed_urls.txt` — the scorer is "
            "correctly rejecting these as off-profile; the feed just adds noise.\n\n"
            "| Company | Jobs (7d) | Source(s) |\n"
            "|---|---|---|\n"
        )
        for r in low_signal_rows:
            low_signal_section += f"| {r['company']} | {r['total']} | {r['sources']} |\n"
    else:
        low_signal_section = "_None — every active feed produced at least one 7+ job in the last 7 days._"

    # ── Prefilter expansion candidates ──
    # Title n-grams that recur in score-7+ rejections with title-related reject
    # reasons. Each one is a concrete candidate to add to scorer_prefilter.py.
    # Human-approved — this is a proposal list, nothing is auto-applied.
    try:
        from analyze_feedback import analyze as feedback_analyze

        fb_conn = db_connect()
        fb = feedback_analyze(fb_conn)
        fb_conn.close()
        candidates = fb.get("prefilter_candidates", [])[:10]
    except Exception:
        candidates = []
    # ── LLM spend (last 7d, populated cost_log rows only) ──
    spend_conn = db_connect()
    spend_rows = spend_conn.execute("""
        SELECT
            operation,
            COUNT(*) AS n_calls,
            SUM(cost_usd) AS total_cost,
            SUM(input_tokens) AS in_tok,
            SUM(output_tokens) AS out_tok
        FROM cost_log
        WHERE cost_usd IS NOT NULL
          AND julianday('now') - julianday(logged_at) <= 7
        GROUP BY operation
        ORDER BY total_cost DESC
    """).fetchall()
    total_7d = sum((r["total_cost"] or 0) for r in spend_rows)
    total_calls_7d = sum((r["n_calls"] or 0) for r in spend_rows)
    spend_conn.close()

    if spend_rows:
        monthly_proj = total_7d * (30 / 7)
        spend_section = (
            f"**Total: ${total_7d:.2f}** across {total_calls_7d:,} calls "
            f"(estimated monthly burn: **${monthly_proj:.0f}**).\n\n"
            "Estimates from char-based token heuristic × model pricing; accurate to ~±30% vs actual bills.\n\n"
            "| Operation | Calls | Input tok | Output tok | Cost (7d) |\n"
            "|---|---|---|---|---|\n"
        )
        for r in spend_rows:
            spend_section += (
                f"| {r['operation']} | {r['n_calls']:,} | "
                f"{(r['in_tok'] or 0):,} | {(r['out_tok'] or 0):,} | "
                f"${(r['total_cost'] or 0):.2f} |\n"
            )
        spend_section += (
            "\n_Prep-stage calls (resume, cover letter, briefing, fit analysis) "
            "not yet instrumented — see follow-up. Scoring only for now._"
        )
    else:
        spend_section = "_No cost data in the last 7d (cost_log rows need the cost_usd column populated by #32)._"

    if candidates:
        prefilter_candidates_section = (
            "Title n-grams recurring in score-7+ rejections (3+ times, title-related reasons only, "
            "not in applied-job titles). Each is a candidate to add to `scorer_prefilter.py` Stage 1. "
            "Review and add the patterns that consistently waste scoring budget.\n\n"
            "| Count | Reason | N-gram | Proposed regex | Example |\n"
            "|---|---|---|---|---|\n"
        )
        for c in candidates:
            ngram = " ".join(c["ngram"])
            example = (c["examples"][0] if c["examples"] else "")[:60]
            prefilter_candidates_section += (
                f"| {c['count']} | {c['dominant_reason']} | `{ngram}` | `{c['proposed_regex']}` | {example} |\n"
            )
    else:
        prefilter_candidates_section = (
            "_No recurring patterns (need ≥3 rejections at score 7+ with title-related reason)._"
        )

    body = f"""\
> **This is a living scoreboard, not a task.** Auto-updated weekly by `notify.py scoreboard`.

## The Funnel (as of {today})

Cumulative counts — how many jobs ever reached each stage, not just current state.

```
  Ingested    {total:,} jobs
      │
  Scored      {scored:,} ({scored / total * 100:.0f}%)
      │
  Score 7+      {s7:,} ({hit_rate}% of scored)     ← pipeline signal quality
      │
  Prepped        {prepped:,} ({prep_rate}% of 7+)          ← materials generated
      │
  Applied        {applied:,} ({apply_rate}% of prepped)     ← applications submitted
      │
  Interview       {interview} ({interview_rate}% of applied)      ← active interviews
      │
  Offer           {offer}                       ← pending
```

### Conversion Rates

| Step | Rate | Interpretation |
|------|------|---------------|
| Scored → 7+ | {hit_rate}% | Selectivity. Too low = queries too broad. Too high = scorer too generous. |
| 7+ → Prepped | {prep_rate}% | User triage. Rest rejected before prep (user filter working). |
| Prepped → Applied | {apply_rate}% | User action bottleneck. Materials exist but applications require human effort. |
| Applied → Interview | {interview_rate}% | Market signal. Low = resume/targeting needs work. |

### Current Queue

| Status | Count | Note |
|--------|-------|------|
| Ready to Apply (`materials_drafted`) | {ready} | |
| Waitlisted | {waitlisted} | Deferred, not rejected |
| User rejected | {user_rejected} | {feedback_entries} in feedback_log feeding back to scorer |

### Attrition Detail

| Exit Point | Count | Note |
|------------|-------|------|
| Hard reject (score 1) | {score1:,} | {score1 / scored * 100:.0f}% — prefilter working as intended |
| Score 2–6 | {score2_6:,} | Filtered by Dashboard threshold |
| User rejected after prep | {rejected_after_prep} | Prepped but user decided not to apply |
| Waitlisted after prep | {waitlisted_after_prep} | Good fit but timing/competing apps |
| Not selected (company) | {not_selected} | Company rejections |

## Score Distribution

{dist_table}

## Low-Signal Feeds (last 7d)

{low_signal_section}

## Prefilter Expansion Candidates

{prefilter_candidates_section}

## LLM Spend (last 7d)

{spend_section}

## What to Watch

- **Score 7+ hit rate** should be 2–5%. Currently {hit_rate}% — {hit_health}.
- **Prepped → Applied conversion ({apply_rate}%)** is the user bottleneck.
- **Applied → Interview rate ({interview_rate}%)** — needs 50+ applications before this metric is meaningful.

---

📌 Pinned — not a task to complete. Auto-updated weekly by `notify.py scoreboard`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"""

    # Update the issue
    rc = subprocess.run(
        ["gh", "issue", "edit", str(SCOREBOARD_ISSUE), "--repo", SCOREBOARD_REPO, "--body", body],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if rc.returncode == 0:
        msg = f"Pipeline funnel scoreboard (#31) updated for {today}."
        send("Scoreboard Updated", msg, priority="low", tags="bar_chart")
    else:
        send("Scoreboard Update Failed", f"gh issue edit failed: {rc.stderr[:200]}", priority="high", tags="warning")


# ── Dispatch ───────────────────────────────────────────────────────────────────
COMMANDS = {
    "daily-stats": cmd_daily_stats,
    "health-check": cmd_health_check,
    "issues-ping": cmd_issues_ping,
    "apply-reminder": cmd_apply_reminder,
    "feedback-review": cmd_feedback_review,
    "send-raw": cmd_send_raw,
    "ci-check": cmd_ci_check,
    "scoreboard": cmd_scoreboard,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: notify.py [{'|'.join(COMMANDS)}]")
        sys.exit(1)
    COMMANDS[sys.argv[1]]()
