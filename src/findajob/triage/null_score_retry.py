"""Re-score manual_review rows whose `relevance_score` is NULL (prior scorer failure).

Extracted from `scripts/triage.py` in M3 (#537). Behavior preserved verbatim.
"""

import sqlite3
from datetime import UTC, datetime, timedelta

from findajob.audit import log_event, write_audit
from findajob.scoring import score_job

NULL_SCORE_RETRY_LIMIT = 50  # max null-score rows retried per triage run
NULL_SCORE_RETRY_DAYS = 7  # rows older than this are skipped (genuinely broken JD)


def score_null_manual_review_rows(
    conn: sqlite3.Connection,
    candidate_profile: str,
    feedback_block: str,
    limit: int = NULL_SCORE_RETRY_LIMIT,
) -> int:
    """Re-score manual_review rows that have relevance_score=NULL (prior scorer failure).

    Only considers rows updated within NULL_SCORE_RETRY_DAYS to avoid retrying
    genuinely unparseable JDs forever. Returns the count of successfully rescored rows.

    stage_updated is stored as Python ISO format ("2026-04-23T17:19:57+00:00");
    compute the cutoff in Python so both sides of the comparison use the same format.
    SQLite's datetime('now', ...) returns space-separated form which misorders against
    ISO-T on same-day rows (T > space at pos 10).
    """
    cutoff = (datetime.now(UTC) - timedelta(days=NULL_SCORE_RETRY_DAYS)).isoformat()
    rows = conn.execute(
        """
        SELECT id, title, company, location, raw_jd_text FROM jobs
        WHERE stage = 'manual_review'
          AND relevance_score IS NULL
          AND stage_updated > ?
          AND (dupe_of = '' OR dupe_of IS NULL)
        LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()

    rescored = 0
    for row in rows:
        job_id = row["id"]
        try:
            scored, _, _completion = score_job(
                row["title"],
                row["company"] or "",
                row["location"] or "",
                row["raw_jd_text"] or "",
                candidate_profile,
                feedback_block=feedback_block,
            )
        except Exception as e:
            log_event("null_score_retry_error", job_id=job_id, error=str(e))
            continue

        now = datetime.now(UTC).isoformat()
        stage = "manual_review" if scored.get("score_status") == "manual_review" else "scored"
        conn.execute(
            """
            UPDATE jobs SET
                relevance_score=?, interview_likelihood=?, strengths_alignment=?,
                industry_sector=?, comp_estimate=?, ai_notes=?,
                score_status=?, score_flag_reason=?, remote_status=?,
                stage=?, stage_updated=?, updated_at=?
            WHERE id=?
            """,
            (
                scored.get("relevance_score"),
                scored.get("interview_likelihood"),
                scored.get("strengths_alignment"),
                scored.get("industry_sector", ""),
                scored.get("comp_estimate", ""),
                scored.get("ai_notes", ""),
                scored.get("score_status", "manual_review"),
                scored.get("score_flag_reason", ""),
                scored.get("remote_status", "Unknown"),
                stage,
                now,
                now,
                job_id,
            ),
        )
        conn.commit()
        write_audit(conn, job_id, "stage", "manual_review", stage)
        rescored += 1

    return rescored
