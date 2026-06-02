"""Daily nudge with quip + checklist — user-facing."""

from datetime import datetime

from findajob.notifications.ntfy import _p, db_connect, send
from findajob.timeutil import local_zoneinfo

QUIPS = (
    "The perfect resume is the enemy of the submitted one. Go click Apply.",
    "Your resume can't apply to itself. We checked. Open a tab.",
    "Somewhere a hiring manager is waiting for your resume. Don't keep them waiting.",
    "Every application you don't submit is a job you definitely didn't get.",
    "Today's to-do list: 1) breathe, 2) hydrate, 3) submit one application. You've already crushed two of three.",
    "Your future self is staring at you. They look annoyed. Apply to something.",
    "What did the cover letter say to the resume? 'I've got you covered.' Now go give them something to cover.",
    "Reject the fear of rejection. Apply anyway. Preferably today.",
    "Fun fact: 0% of jobs you don't apply to result in interviews.",
    (
        "Why did the job seeker bring a ladder to the interview? "
        "Heard there were openings on a higher floor. "
        "Speaking of openings — apply to one."
    ),
)


def cmd_apply_reminder() -> None:
    # Rotate by day-of-year in the deployment's timezone so the quip changes at
    # the operator's local midnight, not a hardcoded zone.
    day_index = datetime.now(local_zoneinfo()).timetuple().tm_yday % len(QUIPS)
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
        f"1. {_p(n_dashboard, 'strong match', 'strong matches')} waiting — "
        f"flag the keepers, pass on the rest\n"
        f"2. {_p(n_ready, 'application')} ready to send — review and submit\n"
        f"3. {_p(n_review, 'job')} for you to review — promote or pass\n"
        f"---\n"
        f"Applications submitted to date: {n_applied}\n"
        f"Set aside for later: {n_waitlisted}"
    )

    send(
        "💼 findajob — apply to something today!",
        quip + checklist,
        priority="default",
        tags="rocket",
        kind="apply_reminder",
    )
