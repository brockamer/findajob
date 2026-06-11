"""Rejection-driven prefilter rule proposals.

Reads analyze_feedback's already-computed prefilter candidates, computes a
read-only preview against currently-active jobs (with a danger subset =
unactioned high-score jobs the rule would reject), and queues pending rows in
the filter_proposals table. Apply/skip/revert live below.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime  # noqa: F401 — used by the apply/skip/revert task that appends to this module

from findajob.analyze_feedback import analyze

# Stages a job sits in while still surfaced and un-actioned by the operator.
_ACTIVE_STAGES = ("scored", "manual_review")
_DANGER_SCORE_MIN = 7
_PREVIEW_SAMPLE_MAX = 8


def _active_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, title, company, relevance_score, scored_by, stage
        FROM jobs
        WHERE (dupe_of = '' OR dupe_of IS NULL)
          AND stage IN ({})
        """.format(",".join("?" * len(_ACTIVE_STAGES))),
        _ACTIVE_STAGES,
    ).fetchall()


def _preview(regex: str, jobs: list[sqlite3.Row]) -> tuple[int, int, list[dict]]:
    """Return (match_count, danger_count, sample) for a regex over active jobs.

    danger = matched jobs whose relevance_score >= 7 (unactioned high-scorers).
    """
    try:
        rx = re.compile(regex, re.IGNORECASE)
    except re.error:
        return (0, 0, [])
    matches = [j for j in jobs if rx.search(j["title"] or "")]
    danger = [j for j in matches if (j["relevance_score"] or 0) >= _DANGER_SCORE_MIN]
    sample = [
        {"title": j["title"], "company": j["company"], "score": j["relevance_score"]}
        for j in matches[:_PREVIEW_SAMPLE_MAX]
    ]
    return (len(matches), len(danger), sample)


def generate_proposals(conn: sqlite3.Connection) -> int:
    """Mine candidates, compute previews, queue new pending proposals.

    Idempotent: INSERT OR IGNORE on the UNIQUE pattern_norm index means an
    already-queued/decided pattern is never re-proposed. Returns the number of
    rows newly inserted.
    """
    candidates = analyze(conn).get("prefilter_candidates", [])
    if not candidates:
        return 0
    jobs = _active_jobs(conn)
    inserted = 0
    for c in candidates:
        regex = c["proposed_regex"]
        match_count, danger_count, sample = _preview(regex, jobs)
        support_sample = json.dumps(c.get("examples", [])[:3])
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO filter_proposals
                (pattern, pattern_norm, category, source_reason, support_count,
                 support_sample, preview_count, preview_sample, preview_danger_count, status)
            VALUES (?, ?, 'auto_added', ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                regex,
                regex,
                c.get("dominant_reason"),
                c.get("count", 0),
                support_sample,
                match_count,
                json.dumps(sample),
                danger_count,
            ),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted
