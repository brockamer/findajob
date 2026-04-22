#!/usr/bin/env python3
# ~/JobSearchPipeline/scripts/poll_flags.py
"""Poll Google Sheet for APPLY_FLAG + REJECT_REASON changes. Mirror to SQLite. Trigger prep."""

import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from findajob.paths import BASE
from findajob.utils import is_valid_company, log_event, write_audit

DB_PATH = f"{BASE}/data/pipeline.db"
SA_FILE = f"{BASE}/config/gsheets_creds.json"
with open(f"{BASE}/config/sheet_id.txt") as f:
    SHEET_ID = f.read().strip()

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def handle_rejection(conn, job, reason):
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


def handle_not_selected(conn, job, reason):
    """Company rejected the application. Sets stage=not_selected, drops a marker file.
    Does NOT write to feedback_log — company rejections should not feed the scorer.
    Folder stays in _applied/ (no move). Returns False (no folder moved)."""
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
    write_audit(conn, job["id"], "stage", old_stage, "not_selected")
    write_audit(conn, job["id"], "reject_reason", "", reason)
    log_event("job_not_selected", job_id=job["id"], company=job["company"], title=job["title"], reason=reason)
    return False


def handle_waitlist(conn, job):
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


def handle_reactivate(conn, job):
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


def notify_waitlist_resurface(conn, company):
    """If there are waitlisted jobs at this company, send a notification."""
    rows = conn.execute("SELECT title FROM jobs WHERE company = ? AND stage = 'waitlisted'", (company,)).fetchall()
    if not rows:
        return
    titles = [r["title"] for r in rows]
    title = f"Waitlisted jobs at {company}"
    body = "You just rejected/withdrew from this company. Waitlisted roles:\n" + "\n".join(f"• {t}" for t in titles)
    subprocess.Popen([sys.executable, f"{BASE}/scripts/notify.py", "send-raw", title, body], start_new_session=True)
    log_event("waitlist_resurface", company=company, count=len(titles))


def main():
    creds = service_account.Credentials.from_service_account_file(SA_FILE, scopes=SCOPES)
    svc = build("sheets", "v4", credentials=creds)

    # Read STATUS (col A), REJECT_REASON (col B), fingerprint (col C) from
    # Dashboard (pre-application queue) and Applied (post-application queue).
    # Both tabs use the same col A/B/C layout so one processing loop handles them.
    try:
        result = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="Dashboard!A2:C10000").execute()
        rows = result.get("values", [])
    except HttpError as e:
        if e.resp.status == 400:
            log_event("poll_flags", found=0, note="sheet_empty_or_range_exceeded")
            return
        raise
    try:
        applied_result = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="Applied!A2:C10000").execute()
        rows += applied_result.get("values", [])
    except HttpError as e:
        if e.resp.status != 400:
            raise

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row

    flagged_jobs = []
    rejected_count = 0
    not_selected_count = 0
    applied_count = 0
    waitlisted_count = 0
    reactivated_count = 0
    folders_moved = 0

    for row in rows:
        if len(row) < 3:
            # Need at least col A, B, C. Rows with < 3 cols have no fingerprint — skip.
            # But check if len==1 or 2 for APPLY_FLAG with missing reject/fp
            if len(row) < 1:
                continue
            flag_val = row[0] if len(row) >= 1 else ""
            reject_val = row[1] if len(row) >= 2 else ""
            fp = row[2] if len(row) >= 3 else ""
            if not fp:
                continue
        else:
            flag_val = row[0]
            reject_val = row[1]
            fp = row[2]

        if not fp:
            continue

        # STATUS dropdown: "Flag for Prep" triggers prep; others update DB stage
        is_flagged = flag_val == "Flag for Prep"
        is_rejected = bool(reject_val and reject_val.strip())

        STATUS_STAGE_MAP = {
            "Applied": "applied",
            "Interviewing": "interview",
            "Offer": "offer",
            "Withdrew": "withdrawn",
        }

        job = conn.execute(
            """
            SELECT id, title, company, url, stage, apply_flag, reject_reason,
                   relevance_score, prep_folder_path
            FROM jobs WHERE fingerprint = ?
        """,
            (fp,),
        ).fetchone()

        if not job:
            continue

        # ── Regenerate: delete existing prep folder and re-run prep ──────
        if flag_val == "Regenerate":
            folder = job["prep_folder_path"]
            if folder and os.path.isdir(folder):
                shutil.rmtree(folder)
                folders_moved += 1
            now = datetime.now(UTC).isoformat()
            conn.execute(
                """UPDATE jobs SET stage='prep_in_progress', prep_folder_path=NULL,
                   gdrive_folder_url=NULL, apply_flag=1, stage_updated=?, updated_at=?
                   WHERE id=?""",
                (now, now, job["id"]),
            )
            conn.commit()
            write_audit(conn, job["id"], "stage", job["stage"], "prep_in_progress")
            log_event("regen_requested", job_id=job["id"], company=job["company"], title=job["title"])
            flagged_jobs.append({"id": job["id"], "title": job["title"], "company": job["company"], "url": job["url"]})
            continue

        # ── Not Selected (company rejection) — BEFORE generic rejection ──
        if flag_val == "Not Selected" and job["stage"] not in ("not_selected", "rejected"):
            if job["stage"] in ("applied", "interview", "offer"):
                reason = reject_val.strip() if reject_val and reject_val.strip() else "Company passed"
                handle_not_selected(conn, job, reason)
                not_selected_count += 1
                notify_waitlist_resurface(conn, job["company"])
            else:
                log_event(
                    "not_selected_skipped",
                    job_id=job["id"],
                    stage=job["stage"],
                    reason="Not Selected only valid for applied/interview/offer",
                )
            continue

        # ── Rejection takes priority ─────────────────────────────────────
        if is_rejected and job["stage"] != "rejected":
            if handle_rejection(conn, job, reject_val.strip()):
                folders_moved += 1
            rejected_count += 1
            notify_waitlist_resurface(conn, job["company"])
            continue  # don't also trigger prep for this job

        # ── Post-application status updates (Applied / Interviewing / Offer / Withdrew) ──
        if flag_val in STATUS_STAGE_MAP:
            new_stage = STATUS_STAGE_MAP[flag_val]
            if job["stage"] != new_stage:
                now = datetime.now(UTC).isoformat()
                conn.execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=?", (new_stage, now, job["id"]))
                conn.commit()
                write_audit(conn, job["id"], "stage", job["stage"], new_stage)
                log_event(
                    "job_stage_updated", job_id=job["id"], company=job["company"], title=job["title"], stage=new_stage
                )

                if new_stage == "withdrawn":
                    notify_waitlist_resurface(conn, job["company"])

                # Move prep folder to _applied when marked Applied
                if new_stage == "applied":
                    jd = conn.execute("SELECT prep_folder_path FROM jobs WHERE id=?", (job["id"],)).fetchone()
                    folder = jd["prep_folder_path"] if jd else None
                    if folder and os.path.isdir(folder):
                        applied_dir = os.path.join(BASE, "companies", "_applied")
                        os.makedirs(applied_dir, exist_ok=True)
                        dest = os.path.join(applied_dir, os.path.basename(folder))
                        shutil.move(folder, dest)
                        conn.execute("UPDATE jobs SET prep_folder_path=? WHERE id=?", (dest, job["id"]))
                        conn.commit()
                        log_event("folder_moved_to_applied", job_id=job["id"], folder=os.path.basename(folder))
                        folders_moved += 1
                    applied_count += 1

            continue  # don't trigger prep for these statuses

        # ── Waitlist handling ────────────────────────────────────────────
        if flag_val == "Waitlist" and job["stage"] != "waitlisted":
            if handle_waitlist(conn, job):
                folders_moved += 1
            waitlisted_count += 1
            continue

        # ── Flag for Prep handling ───────────────────────────────────────
        if is_flagged and not job["apply_flag"]:
            conn.execute(
                "UPDATE jobs SET apply_flag=1, updated_at=? WHERE id=?", (datetime.now(UTC).isoformat(), job["id"])
            )
            conn.commit()
            write_audit(conn, job["id"], "apply_flag", "0", "1")

        if is_flagged and job["stage"] in ("scored", "manual_review", "enriched"):
            if not is_valid_company(job["company"]):
                log_event(
                    "poll_flags_skipped",
                    reason="invalid_company",
                    company=job["company"],
                    title=job["title"],
                    job_id=job["id"],
                )
                continue
            # Guard: set stage immediately so next poll cycle won't re-trigger
            now = datetime.now(UTC).isoformat()
            conn.execute(
                "UPDATE jobs SET stage=?, stage_updated=?, updated_at=? WHERE id=?",
                ("prep_in_progress", now, now, job["id"]),
            )
            conn.commit()
            write_audit(conn, job["id"], "stage", job["stage"], "prep_in_progress")
            flagged_jobs.append({"id": job["id"], "title": job["title"], "company": job["company"], "url": job["url"]})

    # ── Review tab: manual_review triage ────────────────────────────────────
    # Col A = STATUS (Promote / blank), Col B = REJECT_REASON, Col C = fingerprint
    review_promoted = 0
    review_rejected = 0
    try:
        review_result = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="Review!A2:C10000").execute()
        review_rows = review_result.get("values", [])
    except HttpError:
        review_rows = []

    for row in review_rows:
        if len(row) < 3:
            continue
        status_val = row[0].strip()
        reject_val = row[1].strip()
        fp = row[2].strip()
        if not fp:
            continue

        job = conn.execute(
            """
            SELECT id, title, company, url, stage, apply_flag, reject_reason, relevance_score
            FROM jobs WHERE fingerprint = ?
        """,
            (fp,),
        ).fetchone()
        if not job or job["stage"] != "manual_review":
            continue

        now = datetime.now(UTC).isoformat()

        # Rejection takes priority
        if reject_val:
            if handle_rejection(conn, job, reject_val):
                folders_moved += 1
            review_rejected += 1
            rejected_count += 1
            continue

        # Promote: set score=7, stage=scored → lands on Dashboard for Flag for Prep
        if status_val == "Promote":
            conn.execute(
                """
                UPDATE jobs SET relevance_score=7, stage='scored',
                       score_status='scored', score_flag_reason='Promoted from Review tab',
                       stage_updated=?, updated_at=?
                WHERE id=?
            """,
                (now, now, job["id"]),
            )
            conn.commit()
            write_audit(conn, job["id"], "stage", "manual_review", "scored")
            log_event("review_promoted", job_id=job["id"], company=job["company"], title=job["title"])
            review_promoted += 1

    if review_promoted or review_rejected:
        log_event("poll_review", promoted=review_promoted, rejected=review_rejected)

    # ── Waitlist tab: reactivate or reject waitlisted jobs ─────────────────
    # Col A = STATUS (Reactivate / blank), Col B = REJECT_REASON, Col C = fingerprint
    try:
        waitlist_result = svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="Waitlist!A2:C10000").execute()
        waitlist_rows = waitlist_result.get("values", [])
    except HttpError:
        waitlist_rows = []

    for row in waitlist_rows:
        if len(row) < 3:
            continue
        status_val = row[0].strip()
        reject_val = row[1].strip()
        fp = row[2].strip()
        if not fp:
            continue

        job = conn.execute(
            """
            SELECT id, title, company, url, stage, apply_flag, reject_reason,
                   relevance_score, prep_folder_path
            FROM jobs WHERE fingerprint = ?
        """,
            (fp,),
        ).fetchone()
        if not job or job["stage"] != "waitlisted":
            continue

        # Rejection takes priority
        if reject_val:
            if handle_rejection(conn, job, reject_val):
                folders_moved += 1
            rejected_count += 1
            continue

        # Reactivate: restore to scored or materials_drafted
        if status_val == "Reactivate":
            if handle_reactivate(conn, job):
                folders_moved += 1
            reactivated_count += 1

    conn.close()

    need_sync = False

    if rejected_count:
        log_event("poll_flags_rejections", count=rejected_count, folders_moved=folders_moved)
        need_sync = True

    if not_selected_count:
        log_event("poll_flags_not_selected", count=not_selected_count)
        need_sync = True

    if applied_count:
        log_event("poll_flags_applied", count=applied_count, folders_moved=folders_moved)
        need_sync = True

    if review_promoted:
        need_sync = True

    if waitlisted_count:
        log_event("poll_flags_waitlisted", count=waitlisted_count)
        need_sync = True

    if reactivated_count:
        log_event("poll_flags_reactivated", count=reactivated_count)
        need_sync = True

    if flagged_jobs:
        need_sync = True

    if folders_moved:
        log_event("folder_moves_completed", count=folders_moved)

    if not flagged_jobs:
        log_event(
            "poll_flags",
            found=0,
            rejections=rejected_count,
            not_selected=not_selected_count,
            waitlisted=waitlisted_count,
            reactivated=reactivated_count,
            review_promoted=review_promoted,
            review_rejected=review_rejected,
        )
        if need_sync:
            proc = subprocess.Popen([sys.executable, f"{BASE}/scripts/sync_sheet.py"])
            try:
                proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                log_event("child_timeout", pid=proc.pid)
                proc.kill()
        return

    log_event(
        "poll_flags",
        found=len(flagged_jobs),
        rejections=rejected_count,
        not_selected=not_selected_count,
        waitlisted=waitlisted_count,
        reactivated=reactivated_count,
        review_promoted=review_promoted,
        review_rejected=review_rejected,
        jobs=[f"{j['company']} - {j['title']}" for j in flagged_jobs],
    )

    # Launch prep for each flagged job in parallel, then wait for all to finish.
    # Cap at 3 per cycle to avoid exhausting API rate limits / quotas.
    # --no-sync suppresses per-prep sync_sheet calls; one consolidated sync runs below.
    MAX_CONCURRENT_PREPS = 3
    children = []  # Popen handles for prep children
    for job in flagged_jobs[:MAX_CONCURRENT_PREPS]:
        children.append(
            subprocess.Popen(
                [
                    sys.executable,
                    f"{BASE}/scripts/prep_application.py",
                    job["company"],
                    job["title"],
                    job["url"],
                    job["id"],
                    "--no-sync",
                ],
            )
        )
    if len(flagged_jobs) > MAX_CONCURRENT_PREPS:
        # Reset deferred jobs back to scored so next poll cycle picks them up
        deferred = flagged_jobs[MAX_CONCURRENT_PREPS:]
        deferred_conn = sqlite3.connect(DB_PATH, timeout=30)
        now = datetime.now(UTC).isoformat()
        for job in deferred:
            deferred_conn.execute(
                "UPDATE jobs SET stage='scored', stage_updated=?, updated_at=? WHERE id=?",
                (now, now, job["id"]),
            )
        deferred_conn.commit()
        deferred_conn.close()
        log_event(
            "prep_deferred",
            count=len(deferred),
            jobs=[f"{j['company']} - {j['title']}" for j in deferred],
        )

    # Wait for all prep children before syncing, so the sheet sees all stage updates.
    for proc in children:
        try:
            proc.wait(timeout=600)
        except subprocess.TimeoutExpired:
            log_event("child_timeout", pid=proc.pid)
            proc.kill()

    # Single consolidated sync after all preps finish — eliminates last-write-wins race.
    sync_proc = subprocess.Popen([sys.executable, f"{BASE}/scripts/sync_sheet.py"])
    try:
        sync_proc.wait(timeout=120)
    except subprocess.TimeoutExpired:
        log_event("child_timeout", pid=sync_proc.pid)
        sync_proc.kill()

    db_count_conn = sqlite3.connect(DB_PATH, timeout=10)
    dashboard_rows = db_count_conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE "
        "(relevance_score >= 7 AND stage IN ('scored','manual_review')) "
        "OR stage IN ('prep_in_progress','materials_drafted')"
    ).fetchone()[0]
    db_count_conn.close()
    log_event("sync_complete", dashboard_db_rows=dashboard_rows)


if __name__ == "__main__":
    main()
