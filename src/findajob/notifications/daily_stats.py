"""Morning summary of pipeline state — user-facing notification."""

from datetime import UTC, datetime, timedelta

from findajob.notifications.ntfy import _p, db_connect, send


def cmd_daily_stats() -> None:
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
        "Good morning! Here's where things stand:",
        "",
        f"  {_p(new_today, 'new job')} ranked overnight",
        f"  {_p(queue_count, 'strong match', 'strong matches')} waiting for you",
        f"  {_p(flagged_unprepped, 'job')} you've flagged but haven't started yet",
        f"  {_p(prepped, 'application')} ready to send (resume and cover letter drafted)",
        f"  {_p(applied, 'application')} submitted overall",
        f"  {_p(rejected, 'job')} you've passed on",
        f"  {_p(not_selected, 'application')} where the company said no",
        f"  {_p(total, 'job')} tracked in total",
    ]
    body = "\n".join(lines)
    send("💼 findajob — good morning!", body, priority="default", tags="bar_chart", kind="daily_stats")
