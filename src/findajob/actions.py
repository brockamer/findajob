#!/usr/bin/env python3
"""Stage-transition actions for the job pipeline.

All state transitions that move a job between stages, move its prep folder,
or drop marker files live here. Invoked from `scripts/watchdog.py` and from
the web POST handlers in `findajob.web.routes.board_actions`.

Every function takes an open `sqlite3.Connection` as its first argument and
commits itself. Functions return a `bool` indicating whether a folder was
moved (or, for `reset_prep_to_scored`, whether the reset actually happened).
"""

import glob
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from findajob.audit import log_event, write_audit
from findajob.paths import BASE

_APPLIED_SNAPSHOT_RE = re.compile(r"\.applied-\d{4}-\d{2}-\d{2}\.md$")


def snapshot_applied_md_files(
    folder_path: str | os.PathLike[str],
    date: str | None = None,
) -> list[str]:
    """Copy every ``*.md`` in ``folder_path`` to ``{name}.applied-{date}.md`` siblings.

    Captures the as-sent state of generated materials at the moment of the
    apply transition (#210). The ``.applied-...`` siblings live alongside the
    original ``.md`` so later in-browser edits don't overwrite the snapshot.

    Args:
        folder_path: The materials folder (typically the moved-to-``_applied``
            destination). Returns ``[]`` if the path is not a directory.
        date: ``YYYY-MM-DD``. Defaults to today (UTC).

    Returns:
        List of created snapshot paths (absolute). Idempotent — existing
        ``*.applied-*.md`` files are never re-snapshotted, and existing
        snapshot targets for the given date are never overwritten.
    """
    folder = Path(folder_path)
    if not folder.is_dir():
        return []
    if date is None:
        date = datetime.now(UTC).strftime("%Y-%m-%d")

    created: list[str] = []
    for md in sorted(folder.glob("*.md")):
        if _APPLIED_SNAPSHOT_RE.search(md.name):
            continue
        snap = md.with_name(f"{md.stem}.applied-{date}.md")
        if snap.exists():
            continue
        shutil.copy2(md, snap)
        created.append(str(snap))
    return created


def handle_rejection(conn: sqlite3.Connection, job: Any, reason: str) -> bool:
    """Store rejection in DB, write to feedback_log, and move company folder to _rejected.
    Drops a marker file named {reason}_{date}.txt inside the moved folder.
    Returns True if a folder was moved."""
    now = datetime.now(UTC).isoformat()
    old_stage = job["stage"]
    conn.execute(
        "UPDATE jobs SET stage=?, reject_reason=?, updated_at=? WHERE id=?", ("rejected", reason, now, job["id"])
    )
    # jd_excerpt: first 500 chars of raw_jd_text for post-hoc analysis
    jd = conn.execute("SELECT raw_jd_text, prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
    jd_excerpt = (jd["raw_jd_text"] or "")[:500] if jd and jd["raw_jd_text"] else ""
    from findajob.classification import is_synthetic_job  # local import to avoid circular at module load

    if not is_synthetic_job(job):
        conn.execute(
            """INSERT INTO feedback_log (job_id, title, company, relevance_score, reject_reason, jd_excerpt)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (job["id"], job["title"], job["company"], job["relevance_score"], reason, jd_excerpt),
        )

    # Move company folder to _rejected if it exists
    folder_moved = False
    folder = jd["prep_folder_path"] if jd else None
    if folder and os.path.isdir(folder):
        rejected_dir = os.path.join(BASE, "companies", "_rejected")
        os.makedirs(rejected_dir, exist_ok=True)
        dest = os.path.join(rejected_dir, os.path.basename(folder))
        shutil.move(folder, dest)
        # Drop a marker file: filesystem-safe reason + date
        safe_reason = re.sub(r"[^\w\s-]", "", reason).strip().replace(" ", "_")[:60]
        date_str = datetime.now().strftime("%Y-%m-%d")
        open(os.path.join(dest, f"REJECTED_{safe_reason}_{date_str}.txt"), "w").close()
        conn.execute("UPDATE jobs SET prep_folder_path=? WHERE id=?", (dest, job["id"]))
        log_event("folder_moved_to_rejected", job_id=job["id"], folder=os.path.basename(folder), reason=reason)
        folder_moved = True

    conn.commit()
    write_audit(conn, job["id"], "stage", old_stage, "rejected")
    write_audit(conn, job["id"], "reject_reason", "", reason)
    log_event("job_rejected", job_id=job["id"], company=job["company"], title=job["title"], reason=reason)
    return folder_moved


def handle_not_selected(
    conn: sqlite3.Connection,
    job: Any,
    reason: str,
    *,
    changed_by: str | None = None,
) -> bool:
    """Company rejected the application. Sets stage=not_selected, drops a marker file.

    Does NOT write to feedback_log — company rejections should not feed the scorer.
    Folder stays in _applied/ (no move). Returns False (no folder moved).

    Args:
        changed_by: audit_log changed_by tag. ``None`` preserves the existing
            manual-flow behavior (audit row inserted without the column,
            falling through to the table default ``'system'``). Pass
            ``'gmail_rejection_detector'`` from the rejections-review confirm
            endpoint per spec §4.5.2 so the audit trail distinguishes
            operator-confirmed Gmail-detected rejections from manual flips.
    """
    now = datetime.now(UTC).isoformat()
    old_stage = job["stage"]
    conn.execute(
        "UPDATE jobs SET stage=?, reject_reason=?, updated_at=? WHERE id=?",
        ("not_selected", reason, now, job["id"]),
    )

    # Drop marker file in existing folder (stays in _applied/)
    jd = conn.execute("SELECT prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
    folder = jd["prep_folder_path"] if jd else None
    if folder and os.path.isdir(folder):
        safe_reason = re.sub(r"[^\w\s-]", "", reason).strip().replace(" ", "_")[:60]
        date_str = datetime.now().strftime("%Y-%m-%d")
        open(os.path.join(folder, f"NOT_SELECTED_{safe_reason}_{date_str}.txt"), "w").close()
        log_event("marker_added_not_selected", job_id=job["id"], folder=os.path.basename(folder), reason=reason)

    conn.commit()
    write_audit(conn, job["id"], "stage", old_stage, "not_selected", changed_by=changed_by)
    write_audit(conn, job["id"], "reject_reason", "", reason, changed_by=changed_by)
    log_event("job_not_selected", job_id=job["id"], company=job["company"], title=job["title"], reason=reason)
    return False


def handle_waitlist(conn: sqlite3.Connection, job: Any) -> bool:
    """Move job to waitlisted stage and folder to _waitlisted/.
    Returns True if a folder was moved, False otherwise.
    Does NOT write to feedback_log — waitlisting is not rejection."""
    now = datetime.now(UTC).isoformat()
    old_stage = job["stage"]
    conn.execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=?", ("waitlisted", now, job["id"]))

    folder_moved = False
    jd = conn.execute("SELECT prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
    folder = jd["prep_folder_path"] if jd else None
    if folder and os.path.isdir(folder):
        waitlisted_dir = os.path.join(BASE, "companies", "_waitlisted")
        os.makedirs(waitlisted_dir, exist_ok=True)
        dest = os.path.join(waitlisted_dir, os.path.basename(folder))
        shutil.move(folder, dest)
        conn.execute("UPDATE jobs SET prep_folder_path=? WHERE id=?", (dest, job["id"]))
        log_event("folder_moved_to_waitlisted", job_id=job["id"], folder=os.path.basename(folder))
        folder_moved = True

    conn.commit()
    write_audit(conn, job["id"], "stage", old_stage, "waitlisted")
    log_event("job_waitlisted", job_id=job["id"], company=job["company"], title=job["title"])
    return folder_moved


def handle_reactivate(conn: sqlite3.Connection, job: Any) -> bool:
    """Restore a waitlisted job to scored or materials_drafted.
    Returns True if a folder was moved back."""
    now = datetime.now(UTC).isoformat()
    jd = conn.execute("SELECT prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
    folder = jd["prep_folder_path"] if jd else None
    folder_moved = False

    if folder and os.path.isdir(folder):
        # Move folder back from _waitlisted/ to companies/
        dest = os.path.join(BASE, "companies", os.path.basename(folder))
        shutil.move(folder, dest)
        conn.execute(
            "UPDATE jobs SET stage=?, prep_folder_path=?, updated_at=? WHERE id=?",
            ("materials_drafted", dest, now, job["id"]),
        )
        new_stage = "materials_drafted"
        folder_moved = True
    else:
        conn.execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=?", ("scored", now, job["id"]))
        new_stage = "scored"

    conn.commit()
    write_audit(conn, job["id"], "stage", "waitlisted", new_stage)
    log_event("job_reactivated", job_id=job["id"], company=job["company"], title=job["title"], stage=new_stage)
    return folder_moved


def notify_waitlist_resurface(conn: sqlite3.Connection, company: str) -> None:
    """If there are waitlisted jobs at this company, send a notification."""
    rows = conn.execute("SELECT title FROM jobs WHERE company = ? AND stage = 'waitlisted'", (company,)).fetchall()
    if not rows:
        return
    titles = [r["title"] for r in rows]
    title = f"Waitlisted jobs at {company}"
    body = "You just rejected/withdrew from this company. Waitlisted roles:\n" + "\n".join(f"• {t}" for t in titles)
    subprocess.Popen(
        [sys.executable, f"{BASE}/scripts/notify.py", "send-raw", title, body, "--kind", "send_raw"],
        start_new_session=True,
    )
    log_event("waitlist_resurface", company=company, count=len(titles))


def reset_prep_to_scored(
    conn: sqlite3.Connection,
    job_id: str,
    reason: str,
) -> bool:
    """Roll a failed prep attempt back from prep_in_progress to scored.

    Guards on stage='prep_in_progress' so a job that raced ahead to
    materials_drafted or was moved to applied isn't clobbered. Writes both an
    audit_log entry and a prep_failed_reset event — without the audit entry,
    the 60-min stale-prep reset can't distinguish real hangs from silent
    error-path resets (see #172).

    Returns True if the reset actually happened.
    """
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        "UPDATE jobs SET stage='scored', prep_folder_path=NULL, "
        "stage_updated=?, updated_at=? WHERE id=? AND stage='prep_in_progress'",
        (now, now, job_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        return False
    write_audit(conn, job_id, "stage", "prep_in_progress", "scored")
    log_event("prep_failed_reset", job_id=job_id, reason=reason)
    return True


def promote_to_scored(
    conn: sqlite3.Connection,
    job: Any,
    reason: str = "Promoted from web UI",
) -> None:
    """Promote a job to stage='scored' with relevance_score=7.

    Used when the operator marks a job as "Promote" from either:
    - Review tab (stage='manual_review' → 'scored'), or
    - Archive tab (stage='scored', score<7 → score=7).

    Bumps the score so it lands on the Dashboard's score>=7 filter for a
    Flag for Prep decision. The caller (web handler) is responsible for
    verifying the source stage is in the promotable set.
    """
    now = datetime.now(UTC).isoformat()
    old_stage = job["stage"]
    conn.execute(
        """UPDATE jobs SET relevance_score=7, stage='scored',
                score_status='scored', score_flag_reason=?,
                stage_updated=?, updated_at=?
           WHERE id=?""",
        (reason, now, now, job["id"]),
    )
    conn.commit()
    write_audit(conn, job["id"], "stage", old_stage, "scored")
    log_event("review_promoted", job_id=job["id"], company=job["company"], title=job["title"])


_OVERWRITE_FIELD_MAP: dict[str, str] = {
    "url": "url",
    "location": "location",
    "remote_status": "remote_status",
    "raw_jd_text": "raw_jd_text",
    "notes": "ai_notes",
    "known_contacts": "known_contacts",
}


def _apply_overwrite_fields(set_parts: list[str], params: list, overwrite_fields: dict[str, str]) -> None:
    """Append non-blank submitted fields to a SET clause builder."""
    for key, col in _OVERWRITE_FIELD_MAP.items():
        if overwrite_fields.get(key):
            set_parts.append(f"{col}=?")
            params.append(overwrite_fields[key])


def un_reject_job(conn: sqlite3.Connection, job: Any, overwrite_fields: dict[str, str]) -> None:
    """Reverse a user rejection: restore to scored, delete feedback_log rows.

    Clears reject_reason, sets relevance_score=8, overwrites non-blank
    submitted fields, moves prep folder from _rejected/ back to companies/.
    Deletes feedback_log rows so the scorer's feedback loop stays clean.
    """
    now = datetime.now(UTC).isoformat()

    set_parts = ["stage='scored'", "reject_reason=''", "relevance_score=8", "updated_at=?"]
    params: list = [now]
    _apply_overwrite_fields(set_parts, params, overwrite_fields)
    params.append(job["id"])

    conn.execute(f"UPDATE jobs SET {', '.join(set_parts)} WHERE id=?", params)
    conn.execute("DELETE FROM feedback_log WHERE job_id=?", (job["id"],))

    folder = job["prep_folder_path"] if job["prep_folder_path"] else None
    if folder and os.path.isdir(folder):
        dest = os.path.join(BASE, "companies", os.path.basename(folder))
        shutil.move(folder, dest)
        conn.execute("UPDATE jobs SET prep_folder_path=? WHERE id=?", (dest, job["id"]))
        log_event("folder_moved_from_rejected", job_id=job["id"], folder=os.path.basename(folder))

    conn.commit()
    write_audit(conn, job["id"], "stage", "rejected", "scored")
    write_audit(conn, job["id"], "reject_reason", job["reject_reason"] or "", "")
    log_event("job_un_rejected", job_id=job["id"], company=job["company"], title=job["title"])


def un_not_selected_job(conn: sqlite3.Connection, job: Any) -> str:
    """Reverse a company-not-selected stage: restore the prior stage from
    audit_log, clear the reject_reason, delete all NOT_SELECTED_*.txt marker
    files from the job's folder.

    Returns the restored stage (for the caller to surface in audit/log
    events). Fallback to 'applied' if no audit_log row found — the audit
    write inside handle_not_selected guarantees coverage for all in-system
    transitions, so the fallback is belt-and-suspenders.

    Unlike un_reject_job, there is no feedback_log row to delete (company
    rejections never wrote one) and no folder move (handle_not_selected
    keeps the folder in companies/_applied/).
    """
    now = datetime.now(UTC).isoformat()

    prior = conn.execute(
        "SELECT old_value FROM audit_log "
        "WHERE job_id=? AND field_changed='stage' AND new_value='not_selected' "
        "ORDER BY changed_at DESC LIMIT 1",
        (job["id"],),
    ).fetchone()
    restored_stage = prior[0] if prior and prior[0] else "applied"

    conn.execute(
        "UPDATE jobs SET stage=?, reject_reason='', updated_at=? WHERE id=?",
        (restored_stage, now, job["id"]),
    )

    folder = job["prep_folder_path"] if job["prep_folder_path"] else None
    if folder and os.path.isdir(folder):
        for marker_path in glob.glob(os.path.join(folder, "NOT_SELECTED_*.txt")):
            os.remove(marker_path)
            log_event(
                "marker_removed_not_selected",
                job_id=job["id"],
                folder=os.path.basename(folder),
                marker=os.path.basename(marker_path),
            )

    conn.commit()
    write_audit(conn, job["id"], "stage", "not_selected", restored_stage, changed_by="user")
    write_audit(conn, job["id"], "reject_reason", job["reject_reason"] or "", "", changed_by="user")
    log_event(
        "job_un_not_selected",
        job_id=job["id"],
        company=job["company"],
        title=job["title"],
        restored_stage=restored_stage,
    )
    return restored_stage


def un_withdraw_job(conn: sqlite3.Connection, job: Any) -> str:
    """Reverse a withdraw: restore the prior stage from audit_log
    (typically applied/interview/offer).

    Returns the restored stage. Falls back to 'applied' if no audit_log
    row found — the audit write inside the inline _transition_stage call
    in board_actions.py guarantees coverage for in-system transitions, so
    the fallback is belt-and-suspenders.

    Unlike un_not_selected_job, there is no marker file to delete (withdraw
    never wrote one) and no folder to touch (withdraw doesn't move folders).
    Unlike un_reject_job, no feedback_log row exists (withdraw doesn't
    write to feedback_log). Pure stage restoration.
    """
    now = datetime.now(UTC).isoformat()

    prior = conn.execute(
        "SELECT old_value FROM audit_log "
        "WHERE job_id=? AND field_changed='stage' AND new_value='withdrawn' "
        "ORDER BY changed_at DESC LIMIT 1",
        (job["id"],),
    ).fetchone()
    restored_stage = prior[0] if prior and prior[0] else "applied"

    conn.execute(
        "UPDATE jobs SET stage=?, updated_at=? WHERE id=?",
        (restored_stage, now, job["id"]),
    )
    conn.commit()
    write_audit(conn, job["id"], "stage", "withdrawn", restored_stage, changed_by="user")
    log_event(
        "job_un_withdrawn", job_id=job["id"], company=job["company"], title=job["title"], restored_stage=restored_stage
    )
    return restored_stage


def reactivate_from_ingest(conn: sqlite3.Connection, job: Any, overwrite_fields: dict[str, str]) -> None:
    """Reactivate a waitlisted job via manual ingest.

    Sets stage=scored, relevance_score=8, overwrites non-blank submitted
    fields, moves prep folder from _waitlisted/ back to companies/.
    """
    now = datetime.now(UTC).isoformat()

    set_parts = ["stage='scored'", "relevance_score=8", "updated_at=?"]
    params: list = [now]
    _apply_overwrite_fields(set_parts, params, overwrite_fields)
    params.append(job["id"])

    conn.execute(f"UPDATE jobs SET {', '.join(set_parts)} WHERE id=?", params)

    folder = job["prep_folder_path"] if job["prep_folder_path"] else None
    if folder and os.path.isdir(folder):
        dest = os.path.join(BASE, "companies", os.path.basename(folder))
        shutil.move(folder, dest)
        conn.execute("UPDATE jobs SET prep_folder_path=? WHERE id=?", (dest, job["id"]))
        log_event("folder_moved_from_waitlisted", job_id=job["id"], folder=os.path.basename(folder))

    conn.commit()
    write_audit(conn, job["id"], "stage", "waitlisted", "scored")
    log_event("job_reactivated_via_ingest", job_id=job["id"], company=job["company"], title=job["title"])


def refresh_active_job(conn: sqlite3.Connection, job: Any, overwrite_fields: dict[str, str]) -> None:
    """Refresh an already-visible job submitted again via ingest.

    Bumps relevance_score to 8 if below 8. Promotes manual_review → scored.
    Overwrites non-blank submitted fields. No folder moves.
    """
    now = datetime.now(UTC).isoformat()
    old_stage = job["stage"]
    new_stage = "scored" if old_stage == "manual_review" else old_stage

    set_parts = ["updated_at=?"]
    params: list = [now]

    if (job["relevance_score"] or 0) < 8:
        set_parts.append("relevance_score=8")
    if new_stage != old_stage:
        set_parts.append("stage=?")
        params.append(new_stage)

    _apply_overwrite_fields(set_parts, params, overwrite_fields)
    params.append(job["id"])

    conn.execute(f"UPDATE jobs SET {', '.join(set_parts)} WHERE id=?", params)
    conn.commit()

    if new_stage != old_stage:
        write_audit(conn, job["id"], "stage", old_stage, new_stage)
    log_event(
        "job_refreshed_via_ingest",
        job_id=job["id"],
        company=job["company"],
        title=job["title"],
        old_stage=old_stage,
    )
