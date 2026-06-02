#!/usr/bin/env python3
"""Stage-transition actions for the job pipeline.

All state transitions that move a job between stages, move its prep folder,
or drop marker files live here. Invoked from `scripts/watchdog.py` and from
the web POST handlers in `findajob.web.routes.board_actions`.

Every function takes an open `sqlite3.Connection` as its first argument and,
by default, commits itself. Helpers that touch the filesystem (folder moves,
marker file creation/deletion) accept a kwarg-only ``deferred_fs`` list so
callers can compose multiple writes into one atomic transaction:

- When ``deferred_fs is None`` (default, single-call), the helper runs its
  DB writes, calls ``conn.commit()``, and then executes its filesystem ops
  inline. Failure to commit leaves the filesystem untouched; failure of an
  fs op leaves the DB authoritative (cosmetic disk artifact at worst).
- When ``deferred_fs`` is a list, the helper appends its filesystem ops as
  zero-arg closures to that list and does NOT commit. The caller owns the
  transaction: call ``conn.commit()`` first, then execute each closure.
  Used by ``reattribute_from_archive`` for atomic source-and-target updates.

This replaces the older ``commit=False`` kwarg from #707. The two concerns
(transaction ownership + fs deferral) collapsed into one kwarg because every
caller that wanted ``commit=False`` also needed fs deferral to be meaningful
in a rollback (#709).

Functions retain their existing return values (`bool` for folder-moved,
`str` for restored-stage, `None` elsewhere).
"""

import glob
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from findajob.audit import log_event, write_audit
from findajob.paths import BASE, IMAGE_ROOT

FsOp = Callable[[], None]

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


def handle_rejection(
    conn: sqlite3.Connection,
    job: Any,
    reason: str,
    *,
    deferred_fs: list[FsOp] | None = None,
) -> bool:
    """Store rejection in DB, write to feedback_log, and move company folder to _rejected.

    Drops a marker file named ``REJECTED_{reason}_{date}.txt`` inside the moved
    folder. Returns True if a folder will be (or was) moved.

    Args:
        deferred_fs: When ``None`` (default), the helper commits the DB writes
            and then executes filesystem ops inline. When a list is passed,
            the helper appends fs ops to it as closures and does NOT commit;
            the caller must commit and then execute each closure. See module
            docstring.
    """
    own_transaction = deferred_fs is None
    fs_ops: list[FsOp] = []

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

    # Stage folder move + marker write as deferred fs ops; DB row gets the
    # post-move path eagerly so it commits atomically with the stage change.
    folder_moved = False
    folder = jd["prep_folder_path"] if jd else None
    if folder and os.path.isdir(folder):
        assert isinstance(folder, str)  # mypy narrowing — prep_folder_path is TEXT
        # Derive companies_root from the folder being moved rather than the
        # process-global `BASE`. See #716 for the BASE-relative isolation bug.
        rejected_dir = str(Path(folder).parent / "_rejected")
        folder_name = os.path.basename(folder)
        dest = os.path.join(rejected_dir, folder_name)
        safe_reason = re.sub(r"[^\w\s-]", "", reason).strip().replace(" ", "_")[:60]
        date_str = datetime.now().strftime("%Y-%m-%d")
        marker_path = os.path.join(dest, f"REJECTED_{safe_reason}_{date_str}.txt")
        src_folder: str = folder
        job_id: str = job["id"]

        def _move_and_mark(
            src: str = src_folder,
            d: str = dest,
            rd: str = rejected_dir,
            mp: str = marker_path,
            jid: str = job_id,
            fname: str = folder_name,
            r: str = reason,
        ) -> None:
            os.makedirs(rd, exist_ok=True)
            shutil.move(src, d)
            open(mp, "w").close()
            log_event("folder_moved_to_rejected", job_id=jid, folder=fname, reason=r)

        fs_ops.append(_move_and_mark)
        conn.execute("UPDATE jobs SET prep_folder_path=? WHERE id=?", (dest, job["id"]))
        folder_moved = True

    # Stage UPDATE + audit rows commit as one transaction (#958): no pre-commit, and
    # only the last write_audit commits, so the stage change is never durable without
    # its audit rows.
    write_audit(conn, job["id"], "stage", old_stage, "rejected", commit=False)
    write_audit(conn, job["id"], "reject_reason", "", reason, commit=own_transaction)
    log_event("job_rejected", job_id=job["id"], company=job["company"], title=job["title"], reason=reason)

    if deferred_fs is None:
        for op in fs_ops:
            op()
    else:
        deferred_fs.extend(fs_ops)
    return folder_moved


def handle_not_selected(
    conn: sqlite3.Connection,
    job: Any,
    reason: str,
    *,
    changed_by: str | None = None,
    deferred_fs: list[FsOp] | None = None,
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
        deferred_fs: When ``None`` (default), commits and runs the marker
            write inline. When a list, appends the marker write as a closure
            and does NOT commit. See module docstring.
    """
    own_transaction = deferred_fs is None
    fs_ops: list[FsOp] = []

    now = datetime.now(UTC).isoformat()
    old_stage = job["stage"]
    conn.execute(
        "UPDATE jobs SET stage=?, reject_reason=?, updated_at=? WHERE id=?",
        ("not_selected", reason, now, job["id"]),
    )

    # Stage marker write as a deferred fs op (folder stays in _applied/).
    jd = conn.execute("SELECT prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
    folder = jd["prep_folder_path"] if jd else None
    if folder and os.path.isdir(folder):
        assert isinstance(folder, str)  # mypy narrowing — prep_folder_path is TEXT
        safe_reason = re.sub(r"[^\w\s-]", "", reason).strip().replace(" ", "_")[:60]
        date_str = datetime.now().strftime("%Y-%m-%d")
        marker_path = os.path.join(folder, f"NOT_SELECTED_{safe_reason}_{date_str}.txt")
        folder_name = os.path.basename(folder)
        job_id: str = job["id"]

        def _write_marker(
            mp: str = marker_path,
            jid: str = job_id,
            fname: str = folder_name,
            r: str = reason,
        ) -> None:
            open(mp, "w").close()
            log_event("marker_added_not_selected", job_id=jid, folder=fname, reason=r)

        fs_ops.append(_write_marker)

    # Stage UPDATE + audit rows commit as one transaction (#958).
    write_audit(conn, job["id"], "stage", old_stage, "not_selected", changed_by=changed_by, commit=False)
    write_audit(conn, job["id"], "reject_reason", "", reason, changed_by=changed_by, commit=own_transaction)
    log_event("job_not_selected", job_id=job["id"], company=job["company"], title=job["title"], reason=reason)

    if deferred_fs is None:
        for op in fs_ops:
            op()
    else:
        deferred_fs.extend(fs_ops)
    return False


def handle_waitlist(
    conn: sqlite3.Connection,
    job: Any,
    *,
    deferred_fs: list[FsOp] | None = None,
) -> bool:
    """Move job to waitlisted stage and folder to _waitlisted/.

    Returns True if a folder will be (or was) moved, False otherwise. Does
    NOT write to feedback_log — waitlisting is not rejection.

    Args:
        deferred_fs: See module docstring.
    """
    own_transaction = deferred_fs is None
    fs_ops: list[FsOp] = []

    now = datetime.now(UTC).isoformat()
    old_stage = job["stage"]
    conn.execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=?", ("waitlisted", now, job["id"]))

    folder_moved = False
    jd = conn.execute("SELECT prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
    folder = jd["prep_folder_path"] if jd else None
    if folder and os.path.isdir(folder):
        assert isinstance(folder, str)  # mypy narrowing — prep_folder_path is TEXT
        waitlisted_dir = os.path.join(BASE, "companies", "_waitlisted")
        dest = os.path.join(waitlisted_dir, os.path.basename(folder))
        src_folder: str = folder
        folder_name = os.path.basename(folder)
        job_id: str = job["id"]

        def _move_to_waitlisted(
            src: str = src_folder,
            d: str = dest,
            wd: str = waitlisted_dir,
            jid: str = job_id,
            fname: str = folder_name,
        ) -> None:
            os.makedirs(wd, exist_ok=True)
            shutil.move(src, d)
            log_event("folder_moved_to_waitlisted", job_id=jid, folder=fname)

        fs_ops.append(_move_to_waitlisted)
        conn.execute("UPDATE jobs SET prep_folder_path=? WHERE id=?", (dest, job["id"]))
        folder_moved = True

    # Stage UPDATE + audit row commit as one transaction (#958): the lone write_audit
    # is the single commit (own_transaction), so the stage change carries its audit row.
    write_audit(conn, job["id"], "stage", old_stage, "waitlisted", commit=own_transaction)
    log_event("job_waitlisted", job_id=job["id"], company=job["company"], title=job["title"])

    if deferred_fs is None:
        for op in fs_ops:
            op()
    else:
        deferred_fs.extend(fs_ops)
    return folder_moved


def handle_reactivate(
    conn: sqlite3.Connection,
    job: Any,
    *,
    deferred_fs: list[FsOp] | None = None,
) -> bool:
    """Restore a waitlisted job to scored or materials_drafted.

    Returns True if a folder will be (or was) moved back.

    Args:
        deferred_fs: See module docstring.
    """
    own_transaction = deferred_fs is None
    fs_ops: list[FsOp] = []

    now = datetime.now(UTC).isoformat()
    jd = conn.execute("SELECT prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
    folder = jd["prep_folder_path"] if jd else None
    folder_moved = False

    if folder and os.path.isdir(folder):
        assert isinstance(folder, str)  # mypy narrowing — prep_folder_path is TEXT
        dest = os.path.join(BASE, "companies", os.path.basename(folder))
        src_folder: str = folder

        def _move_from_waitlisted(src: str = src_folder, d: str = dest) -> None:
            shutil.move(src, d)

        fs_ops.append(_move_from_waitlisted)
        conn.execute(
            "UPDATE jobs SET stage=?, prep_folder_path=?, updated_at=? WHERE id=?",
            ("materials_drafted", dest, now, job["id"]),
        )
        new_stage = "materials_drafted"
        folder_moved = True
    else:
        conn.execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=?", ("scored", now, job["id"]))
        new_stage = "scored"

    # Stage UPDATE + audit row commit as one transaction (#958).
    write_audit(conn, job["id"], "stage", "waitlisted", new_stage, commit=own_transaction)
    log_event("job_reactivated", job_id=job["id"], company=job["company"], title=job["title"], stage=new_stage)

    if deferred_fs is None:
        for op in fs_ops:
            op()
    else:
        deferred_fs.extend(fs_ops)
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
        [sys.executable, f"{IMAGE_ROOT}/scripts/notify.py", "send-raw", title, body, "--kind", "waitlist_resurface"],
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
    if cur.rowcount == 0:
        conn.commit()  # release the no-op transaction
        return False
    # Stage UPDATE + audit row commit together (#958): write_audit's commit flushes both.
    write_audit(conn, job_id, "stage", "prep_in_progress", "scored")
    log_event("prep_failed_reset", job_id=job_id, reason=reason)
    return True


def reset_prep_to_briefing_ready(
    conn: sqlite3.Connection,
    job_id: str,
    reason: str,
) -> bool:
    """Roll a failed Phase B attempt back from prep_in_progress to briefing_ready.

    Companion to :func:`reset_prep_to_scored` for the #691 briefing-first
    gate. The operator has already paid for Phase A — the briefing exists
    on disk and the fit scores are stored. A Phase B subprocess crash
    that reset to ``scored`` would discard both and force a re-run of the
    whole pipeline. Resetting to ``briefing_ready`` instead preserves the
    Phase A work so the operator can retry Phase B (or reject from the
    briefing) without re-paying ~$0.33.

    Guards on stage='prep_in_progress' to avoid clobbering a Phase B that
    raced ahead to materials_drafted. Critically does NOT null
    ``prep_folder_path`` — that's how Phase B re-entry knows where to
    re-read the briefing.

    Returns True if the reset actually happened.
    """
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        "UPDATE jobs SET stage='briefing_ready', stage_updated=?, updated_at=? WHERE id=? AND stage='prep_in_progress'",
        (now, now, job_id),
    )
    if cur.rowcount == 0:
        conn.commit()  # release the no-op transaction
        return False
    # Stage UPDATE + audit row commit together (#958).
    write_audit(conn, job_id, "stage", "prep_in_progress", "briefing_ready")
    log_event("prep_phase_b_failed_reset", job_id=job_id, reason=reason)
    return True


def reset_briefing_ready_to_scored(
    conn: sqlite3.Connection,
    job_id: str,
    reason: str,
) -> bool:
    """Roll a stuck briefing_ready job back to scored after the decision-window expires.

    For the #691 48h watchdog reaper. A briefing the operator never
    decided on isn't dead — the briefing folder remains on disk so the
    operator can re-flag the job (via the standard /prep route) and the
    folder link will resurface the existing briefing rather than
    re-paying Phase A. The DB row drops out of the "awaiting decision"
    section without forfeiting the Phase A work.

    Guards on stage='briefing_ready' to avoid clobbering a job that the
    operator continued in the meantime. Critically does NOT null
    ``prep_folder_path`` — that's the resurface path for re-flagged jobs.

    Returns True if the reset actually happened.
    """
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        "UPDATE jobs SET stage='scored', stage_updated=?, updated_at=? WHERE id=? AND stage='briefing_ready'",
        (now, now, job_id),
    )
    if cur.rowcount == 0:
        conn.commit()  # release the no-op transaction
        return False
    # Stage UPDATE + audit row commit together (#958).
    write_audit(conn, job_id, "stage", "briefing_ready", "scored")
    log_event("briefing_ready_stale_reset", job_id=job_id, reason=reason)
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
    # Stage UPDATE + audit row commit together (#958): write_audit's commit flushes both.
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


def un_reject_job(
    conn: sqlite3.Connection,
    job: Any,
    overwrite_fields: dict[str, str],
    *,
    deferred_fs: list[FsOp] | None = None,
) -> None:
    """Reverse a user rejection: restore to scored, delete feedback_log rows.

    Clears reject_reason, sets relevance_score=8, overwrites non-blank
    submitted fields, moves prep folder from _rejected/ back to companies/.
    Deletes feedback_log rows so the scorer's feedback loop stays clean.

    Args:
        deferred_fs: See module docstring.
    """
    own_transaction = deferred_fs is None
    fs_ops: list[FsOp] = []

    now = datetime.now(UTC).isoformat()

    set_parts = ["stage='scored'", "reject_reason=''", "relevance_score=8", "updated_at=?"]
    params: list = [now]
    _apply_overwrite_fields(set_parts, params, overwrite_fields)
    params.append(job["id"])

    conn.execute(f"UPDATE jobs SET {', '.join(set_parts)} WHERE id=?", params)
    conn.execute("DELETE FROM feedback_log WHERE job_id=?", (job["id"],))

    folder = job["prep_folder_path"] if job["prep_folder_path"] else None
    if folder and os.path.isdir(folder):
        assert isinstance(folder, str)  # mypy narrowing — prep_folder_path is TEXT
        dest = os.path.join(BASE, "companies", os.path.basename(folder))
        src_folder: str = folder
        folder_name = os.path.basename(folder)
        job_id: str = job["id"]

        def _move_from_rejected(
            src: str = src_folder,
            d: str = dest,
            jid: str = job_id,
            fname: str = folder_name,
        ) -> None:
            shutil.move(src, d)
            log_event("folder_moved_from_rejected", job_id=jid, folder=fname)

        fs_ops.append(_move_from_rejected)
        conn.execute("UPDATE jobs SET prep_folder_path=? WHERE id=?", (dest, job["id"]))

    # Stage UPDATE + audit rows commit as one transaction (#958).
    write_audit(conn, job["id"], "stage", "rejected", "scored", commit=False)
    write_audit(conn, job["id"], "reject_reason", job["reject_reason"] or "", "", commit=own_transaction)
    log_event("job_un_rejected", job_id=job["id"], company=job["company"], title=job["title"])

    if deferred_fs is None:
        for op in fs_ops:
            op()
    else:
        deferred_fs.extend(fs_ops)


def un_not_selected_job(
    conn: sqlite3.Connection,
    job: Any,
    *,
    deferred_fs: list[FsOp] | None = None,
) -> str:
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

    Args:
        deferred_fs: See module docstring.
    """
    own_transaction = deferred_fs is None
    fs_ops: list[FsOp] = []

    now = datetime.now(UTC).isoformat()

    prior = conn.execute(
        "SELECT old_value FROM audit_log "
        "WHERE job_id=? AND field_changed='stage' AND new_value='not_selected' "
        "ORDER BY changed_at DESC, id DESC LIMIT 1",
        (job["id"],),
    ).fetchone()
    restored_stage = prior[0] if prior and prior[0] else "applied"

    conn.execute(
        "UPDATE jobs SET stage=?, reject_reason='', updated_at=? WHERE id=?",
        (restored_stage, now, job["id"]),
    )

    folder = job["prep_folder_path"] if job["prep_folder_path"] else None
    if folder and os.path.isdir(folder):
        assert isinstance(folder, str)  # mypy narrowing — prep_folder_path is TEXT
        # Snapshot the marker list at planning time so a deferred caller
        # gets the same set of markers it would have deleted today. Each
        # closure binds its own ``mp`` via default-arg trick — without it,
        # lazy capture would bind every closure to the final iteration's
        # ``marker_path``.
        folder_name = os.path.basename(folder)
        job_id: str = job["id"]
        for marker_path in glob.glob(os.path.join(folder, "NOT_SELECTED_*.txt")):

            def _remove_marker(
                mp: str = marker_path,
                jid: str = job_id,
                fname: str = folder_name,
            ) -> None:
                os.remove(mp)
                log_event(
                    "marker_removed_not_selected",
                    job_id=jid,
                    folder=fname,
                    marker=os.path.basename(mp),
                )

            fs_ops.append(_remove_marker)

    # Stage UPDATE + audit rows commit as one transaction (#958).
    write_audit(conn, job["id"], "stage", "not_selected", restored_stage, changed_by="user", commit=False)
    write_audit(
        conn, job["id"], "reject_reason", job["reject_reason"] or "", "", changed_by="user", commit=own_transaction
    )
    log_event(
        "job_un_not_selected",
        job_id=job["id"],
        company=job["company"],
        title=job["title"],
        restored_stage=restored_stage,
    )

    if deferred_fs is None:
        for op in fs_ops:
            op()
    else:
        deferred_fs.extend(fs_ops)
    return restored_stage


def un_withdraw_job(
    conn: sqlite3.Connection,
    job: Any,
    *,
    deferred_fs: list[FsOp] | None = None,
) -> str:
    """Reverse a withdraw: restore the prior stage from audit_log
    (typically applied/interview/offer).

    Returns the restored stage. Falls back to 'applied' if no audit_log
    row found — the audit write inside the inline _transition_stage call
    in board_actions.py guarantees coverage for in-system transitions, so
    the fallback is belt-and-suspenders.

    Unlike un_not_selected_job, there is no marker file to delete (withdraw
    never wrote one) and no folder to touch (withdraw doesn't move folders).
    Unlike un_reject_job, no feedback_log row exists (withdraw doesn't
    write to feedback_log). Pure stage restoration — ``deferred_fs`` exists
    only for caller-composition uniformity; the list stays empty.

    Args:
        deferred_fs: See module docstring. Always appends zero closures for
            this helper (no fs ops); accepted for API parity.
    """
    own_transaction = deferred_fs is None

    now = datetime.now(UTC).isoformat()

    prior = conn.execute(
        "SELECT old_value FROM audit_log "
        "WHERE job_id=? AND field_changed='stage' AND new_value='withdrawn' "
        "ORDER BY changed_at DESC, id DESC LIMIT 1",
        (job["id"],),
    ).fetchone()
    restored_stage = prior[0] if prior and prior[0] else "applied"

    conn.execute(
        "UPDATE jobs SET stage=?, updated_at=? WHERE id=?",
        (restored_stage, now, job["id"]),
    )
    # Stage UPDATE + audit row commit as one transaction (#958).
    write_audit(conn, job["id"], "stage", "withdrawn", restored_stage, changed_by="user", commit=own_transaction)
    log_event(
        "job_un_withdrawn", job_id=job["id"], company=job["company"], title=job["title"], restored_stage=restored_stage
    )
    return restored_stage


def un_interview_job(
    conn: sqlite3.Connection,
    job: Any,
    *,
    deferred_fs: list[FsOp] | None = None,
) -> str:
    """Reverse an interview stage: restore the prior stage from audit_log
    (typically applied).

    Pure stage restoration — no marker files, no folder moves, no
    feedback_log. ``deferred_fs`` accepted for API parity; the list
    stays empty.
    """
    own_transaction = deferred_fs is None

    now = datetime.now(UTC).isoformat()

    prior = conn.execute(
        "SELECT old_value FROM audit_log "
        "WHERE job_id=? AND field_changed='stage' AND new_value='interview' "
        "ORDER BY changed_at DESC, id DESC LIMIT 1",
        (job["id"],),
    ).fetchone()
    restored_stage = prior[0] if prior and prior[0] else "applied"

    conn.execute(
        "UPDATE jobs SET stage=?, updated_at=? WHERE id=?",
        (restored_stage, now, job["id"]),
    )
    # Stage UPDATE + audit row commit as one transaction (#958).
    write_audit(conn, job["id"], "stage", "interview", restored_stage, changed_by="user", commit=own_transaction)
    log_event(
        "job_un_interview", job_id=job["id"], company=job["company"], title=job["title"], restored_stage=restored_stage
    )
    return restored_stage


def un_apply_job(
    conn: sqlite3.Connection,
    job: Any,
    *,
    deferred_fs: list[FsOp] | None = None,
) -> None:
    """Reverse a recent /apply (#699): move folder back from companies/_applied/
    to companies/, delete the *.applied-YYYY-MM-DD.md snapshot siblings written
    by snapshot_applied_md_files, restore the prior stage from audit_log (the most
    recent '… → applied' row's old_value; fallback materials_drafted), clear
    apply_flag, and write an audit_log row tagged 'web_un_apply'.

    Caller (the /un-apply route) is responsible for the 30s time-window guard;
    this helper is unconditional — if the row's stage is 'applied', it reverses.

    Note on apply_flag: the spec clears it back to 0 even though /prep-completed
    materials_drafted rows have apply_flag=1. The asymmetry is intentional — an
    un-applied row is "draft I had decided to send, then changed my mind", not
    "draft pending dispatch", so the flag-zero state is correct.

    Args:
        deferred_fs: See module docstring. The single closure performs the
            folder move AND the snapshot deletes in one pass; the snapshot
            glob iterates at execution time (at the post-move destination),
            so there's no closure-capture trap from collecting per-file
            closures inside a loop (#709 pattern).
    """
    own_transaction = deferred_fs is None
    fs_ops: list[FsOp] = []

    now = datetime.now(UTC).isoformat()

    # Re-fetch prep_folder_path from the DB so callers can pass any row shape
    # that has at least .id — matches handle_rejection's pattern.
    jd = conn.execute("SELECT prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
    folder = jd["prep_folder_path"] if jd and jd["prep_folder_path"] else None
    new_path = folder  # default: keep whatever path is on the row
    if folder and os.path.isdir(folder):
        assert isinstance(folder, str)  # mypy narrowing — prep_folder_path is TEXT
        dest = os.path.join(BASE, "companies", os.path.basename(folder))
        new_path = dest
        src_folder: str = folder
        dest_folder: str = dest
        folder_name = os.path.basename(folder)
        job_id: str = job["id"]

        def _move_and_clean_snapshots(
            src: str = src_folder,
            d: str = dest_folder,
            jid: str = job_id,
            fname: str = folder_name,
        ) -> None:
            # Single closure: glob iteration happens at execution time on the
            # post-move folder, so collapsing the per-file deletes inside the
            # loop into one closure body sidesteps the lazy-capture trap that
            # bit un_not_selected_job in #709.
            shutil.move(src, d)
            log_event("folder_moved_from_applied", job_id=jid, folder=fname)
            for md in Path(d).glob("*.md"):
                if _APPLIED_SNAPSHOT_RE.search(md.name):
                    md.unlink()
                    log_event("snapshot_removed_for_un_apply", job_id=jid, snapshot=md.name)

        fs_ops.append(_move_and_clean_snapshots)

    # Restore the actual prior stage from audit_log — apply() is reachable from
    # briefing_ready (Phase B skipped) as well as materials_drafted, so a hardcoded
    # materials_drafted would land a briefing-only job in a stage it was never in
    # (#959). Matches the un-interview / un-withdraw / promote-from-fallback pattern.
    # The '… → applied' row is reliably present because #958 made apply's stage
    # change + audit row commit atomically; materials_drafted stays the final
    # belt-and-suspenders fallback. id DESC breaks same-second ties on re-apply.
    prior = conn.execute(
        "SELECT old_value FROM audit_log "
        "WHERE job_id=? AND field_changed='stage' AND new_value='applied' "
        "ORDER BY changed_at DESC, id DESC LIMIT 1",
        (job["id"],),
    ).fetchone()
    restored_stage = prior[0] if prior and prior[0] else "materials_drafted"

    conn.execute(
        "UPDATE jobs SET stage=?, apply_flag=0, prep_folder_path=?, stage_updated=?, updated_at=? WHERE id=?",
        (restored_stage, new_path, now, now, job["id"]),
    )
    # Stage UPDATE + audit row commit as one transaction (#958).
    write_audit(conn, job["id"], "stage", "applied", restored_stage, changed_by="web_un_apply", commit=own_transaction)
    log_event(
        "job_un_applied",
        job_id=job["id"],
        company=job["company"],
        title=job["title"],
    )

    if deferred_fs is None:
        for op in fs_ops:
            op()
    else:
        deferred_fs.extend(fs_ops)


def handle_withdraw_as_fallback(
    conn: sqlite3.Connection,
    job: Any,
    reason: str,
) -> None:
    """Withdraw a post-application job as fallback-eligible.

    Sets stage='withdrawn_fallback', stores the withdraw reason in
    reject_reason (stage-disjoint from rejected/not_selected).
    No folder move — the row stays in _applied/.
    """
    now = datetime.now(UTC).isoformat()
    old_stage = job["stage"]
    conn.execute(
        "UPDATE jobs SET stage=?, reject_reason=?, updated_at=? WHERE id=?",
        ("withdrawn_fallback", reason, now, job["id"]),
    )
    # Stage UPDATE + audit rows commit as one transaction (#958).
    write_audit(conn, job["id"], "stage", old_stage, "withdrawn_fallback", commit=False)
    write_audit(conn, job["id"], "reject_reason", "", reason)
    log_event(
        "job_withdrawn_as_fallback",
        job_id=job["id"],
        company=job["company"],
        title=job["title"],
        reason=reason,
    )


def mark_as_fallback(
    conn: sqlite3.Connection,
    job: Any,
) -> None:
    """Convert an existing withdrawn row to withdrawn_fallback.

    No folder move, no reason change — the original withdraw context
    stays intact. Used from the Archive tab to opt existing withdrawn
    rows into the fallback surface.
    """
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE jobs SET stage=?, updated_at=? WHERE id=?",
        ("withdrawn_fallback", now, job["id"]),
    )
    # Stage UPDATE + audit row commit as one transaction (#958).
    write_audit(conn, job["id"], "stage", "withdrawn", "withdrawn_fallback")
    log_event(
        "job_marked_as_fallback",
        job_id=job["id"],
        company=job["company"],
        title=job["title"],
    )


def promote_from_fallback(
    conn: sqlite3.Connection,
    job: Any,
) -> str:
    """Restore a withdrawn_fallback job to its pre-withdraw stage.

    Two entry paths into withdrawn_fallback:
    1. Direct: applied → withdrawn_fallback (withdraw-as-fallback route).
       Audit chain: old='applied', new='withdrawn_fallback'.
    2. Indirect: applied → withdrawn → withdrawn_fallback (mark-as-fallback
       from Archive). Audit chain has two hops.

    For path 2, the immediate old_value is 'withdrawn' — restoring to
    that would send the row back to Archive, defeating the purpose. When
    the lookup yields 'withdrawn', chase one more hop to find the
    pre-withdraw stage. Falls back to 'applied'.

    Clears reject_reason. No folder move. Returns the restored stage.
    """
    now = datetime.now(UTC).isoformat()

    prior = conn.execute(
        "SELECT old_value FROM audit_log "
        "WHERE job_id=? AND field_changed='stage' AND new_value='withdrawn_fallback' "
        "ORDER BY changed_at DESC, id DESC LIMIT 1",
        (job["id"],),
    ).fetchone()
    restored_stage = prior[0] if prior and prior[0] else "applied"

    if restored_stage == "withdrawn":
        pre_withdraw = conn.execute(
            "SELECT old_value FROM audit_log "
            "WHERE job_id=? AND field_changed='stage' AND new_value='withdrawn' "
            "ORDER BY changed_at DESC, id DESC LIMIT 1",
            (job["id"],),
        ).fetchone()
        restored_stage = pre_withdraw[0] if pre_withdraw and pre_withdraw[0] else "applied"

    conn.execute(
        "UPDATE jobs SET stage=?, reject_reason='', updated_at=? WHERE id=?",
        (restored_stage, now, job["id"]),
    )
    # Stage UPDATE + audit row commit as one transaction (#958).
    write_audit(conn, job["id"], "stage", "withdrawn_fallback", restored_stage, changed_by="user")
    log_event(
        "job_promoted_from_fallback",
        job_id=job["id"],
        company=job["company"],
        title=job["title"],
        restored_stage=restored_stage,
    )
    return restored_stage


def reactivate_from_ingest(
    conn: sqlite3.Connection,
    job: Any,
    overwrite_fields: dict[str, str],
    *,
    deferred_fs: list[FsOp] | None = None,
) -> None:
    """Reactivate a waitlisted job via manual ingest.

    Sets stage=scored, relevance_score=8, overwrites non-blank submitted
    fields, moves prep folder from _waitlisted/ back to companies/.

    Args:
        deferred_fs: See module docstring.
    """
    own_transaction = deferred_fs is None
    fs_ops: list[FsOp] = []

    now = datetime.now(UTC).isoformat()

    set_parts = ["stage='scored'", "relevance_score=8", "updated_at=?"]
    params: list = [now]
    _apply_overwrite_fields(set_parts, params, overwrite_fields)
    params.append(job["id"])

    conn.execute(f"UPDATE jobs SET {', '.join(set_parts)} WHERE id=?", params)

    folder = job["prep_folder_path"] if job["prep_folder_path"] else None
    if folder and os.path.isdir(folder):
        assert isinstance(folder, str)  # mypy narrowing — prep_folder_path is TEXT
        dest = os.path.join(BASE, "companies", os.path.basename(folder))
        src_folder: str = folder
        folder_name = os.path.basename(folder)
        job_id: str = job["id"]

        def _move_from_waitlisted(
            src: str = src_folder,
            d: str = dest,
            jid: str = job_id,
            fname: str = folder_name,
        ) -> None:
            shutil.move(src, d)
            log_event("folder_moved_from_waitlisted", job_id=jid, folder=fname)

        fs_ops.append(_move_from_waitlisted)
        conn.execute("UPDATE jobs SET prep_folder_path=? WHERE id=?", (dest, job["id"]))

    # Stage UPDATE + audit row commit as one transaction (#958).
    write_audit(conn, job["id"], "stage", "waitlisted", "scored", commit=own_transaction)
    log_event("job_reactivated_via_ingest", job_id=job["id"], company=job["company"], title=job["title"])

    if deferred_fs is None:
        for op in fs_ops:
            op()
    else:
        deferred_fs.extend(fs_ops)


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

    # When the stage changes, its audit row commits in the same transaction as the
    # UPDATE (#958); with no stage change there's no audit row, so commit directly.
    if new_stage != old_stage:
        write_audit(conn, job["id"], "stage", old_stage, new_stage)
    else:
        conn.commit()
    log_event(
        "job_refreshed_via_ingest",
        job_id=job["id"],
        company=job["company"],
        title=job["title"],
        old_stage=old_stage,
    )
