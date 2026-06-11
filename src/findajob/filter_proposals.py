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
from datetime import UTC, datetime

from findajob.analyze_feedback import analyze
from findajob.audit import log_event, write_audit
from findajob.config_loader import (
    ConfigError,
    add_prefilter_title_pattern,
    remove_prefilter_title_pattern,
)

# Stages a job sits in while still surfaced and un-actioned by the operator.
_ACTIVE_STAGES = ("scored", "manual_review")
_DANGER_SCORE_MIN = 7
_PREVIEW_SAMPLE_MAX = 8
_AUTO_TUNER = "auto_tuner"
_AUTO_CATEGORY = "auto_added"


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


def _load_pending(conn: sqlite3.Connection, proposal_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM filter_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()


def apply_proposal(
    conn: sqlite3.Connection, proposal_id: int, final_pattern: str, *, force: bool = False
) -> dict:
    """Apply a proposal's (possibly operator-edited) regex to the live filter and
    re-filter matching active jobs to score 1. Records provenance. NEVER writes
    feedback_log.

    Returns one of:
      {"result": "noop"}                      — proposal not pending (idempotent)
      {"result": "needs_confirm", "danger_count": N, "danger_sample": [...]}
                                              — would hard-reject N UNACTIONED 7+ jobs
      {"result": "applied", "affected": N}    — applied (no danger, or force=True)

    FIREWALL: the danger check is recomputed for the FINAL pattern at apply time
    (covers operator-edited regexes). The YAML rule is written LAST so a DB failure
    can't leave an orphan rule with no record.
    """
    prop = _load_pending(conn, proposal_id)
    if prop is None or prop["status"] != "pending":
        return {"result": "noop"}

    pattern = (final_pattern or prop["pattern"]).strip()
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise ConfigError(f"prefilter_rules: invalid regex {pattern!r} — {e}") from e

    active = _active_jobs(conn)
    matches = [j for j in active if rx.search(j["title"] or "")]
    danger = [j for j in matches if (j["relevance_score"] or 0) >= _DANGER_SCORE_MIN]
    if danger and not force:
        return {
            "result": "needs_confirm",
            "danger_count": len(danger),
            "danger_sample": [
                {"title": j["title"], "score": j["relevance_score"]} for j in danger[:_PREVIEW_SAMPLE_MAX]
            ],
        }

    now = datetime.now(UTC).isoformat()
    affected: list[dict] = []
    for j in matches:
        affected.append(
            {"job_id": j["id"], "old_score": j["relevance_score"], "old_scored_by": j["scored_by"]}
        )
        conn.execute(
            "UPDATE jobs SET relevance_score=1, scored_by='prefilter_stage1', "
            "score_status='scored', updated_at=? WHERE id=?",
            (now, j["id"]),
        )
        write_audit(
            conn, j["id"], "relevance_score", j["relevance_score"], 1,
            changed_by=_AUTO_TUNER, commit=False,
        )

    cur = conn.execute(
        "INSERT INTO config_changes (lever, changed_by, change_summary) VALUES (?, ?, ?)",
        ("prefilter_rules", _AUTO_TUNER, f"auto_added prefilter rule: {pattern}"),
    )
    config_change_id = cur.lastrowid
    conn.execute(
        "UPDATE filter_proposals SET status='applied', decided_at=?, "
        "config_change_id=?, affected_jobs=? WHERE id=?",
        (now, config_change_id, json.dumps(affected), proposal_id),
    )

    # Write the live rule LAST — if it raises (e.g. duplicate/invalid), roll back so
    # we never leave a DB-recorded apply without the rule.
    try:
        add_prefilter_title_pattern(pattern, category=_AUTO_CATEGORY)
    except Exception:
        conn.rollback()
        raise

    conn.commit()
    log_event("filter_proposal_applied", proposal_id=proposal_id, pattern=pattern, affected=len(affected))
    return {"result": "applied", "affected": len(affected)}


def skip_proposal(conn: sqlite3.Connection, proposal_id: int) -> bool:
    prop = _load_pending(conn, proposal_id)
    if prop is None or prop["status"] != "pending":
        return False
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE filter_proposals SET status='skipped', decided_at=? WHERE id=?",
        (now, proposal_id),
    )
    conn.commit()
    log_event("filter_proposal_skipped", proposal_id=proposal_id)
    return True


def revert_proposal(conn: sqlite3.Connection, proposal_id: int) -> bool:
    """Remove an applied rule and restore the scores it overwrote."""
    prop = _load_pending(conn, proposal_id)
    if prop is None or prop["status"] != "applied":
        return False

    try:
        remove_prefilter_title_pattern(prop["pattern"], category=_AUTO_CATEGORY)
    except Exception:
        # Rule already gone (manual edit) — proceed to restore + mark reverted.
        pass

    now = datetime.now(UTC).isoformat()
    affected = json.loads(prop["affected_jobs"] or "[]")
    for a in affected:
        conn.execute(
            "UPDATE jobs SET relevance_score=?, scored_by=?, updated_at=? WHERE id=?",
            (a["old_score"], a["old_scored_by"], now, a["job_id"]),
        )
        write_audit(
            conn, a["job_id"], "relevance_score", 1, a["old_score"],
            changed_by="auto_tuner_revert", commit=False,
        )

    conn.execute(
        "INSERT INTO config_changes (lever, changed_by, change_summary) VALUES (?, ?, ?)",
        ("prefilter_rules", "auto_tuner_revert", f"reverted prefilter rule: {prop['pattern']}"),
    )
    conn.execute(
        "UPDATE filter_proposals SET status='reverted', decided_at=? WHERE id=?",
        (now, proposal_id),
    )
    conn.commit()
    log_event("filter_proposal_reverted", proposal_id=proposal_id, restored=len(affected))
    return True
