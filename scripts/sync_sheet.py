#!/usr/bin/env python3
# ~/JobSearchPipeline/scripts/sync_sheet.py
"""
Sync SQLite → Google Sheets. One-way after #61 PR-B — no reads from Sheets.
  Dashboard: Actionable queue (score>=7 scored/manual_review, or materials_drafted).
  Review:    Manual review triage queue (stage=manual_review, null-score scorer failures).
  Waitlist:  Deferred jobs (stage=waitlisted).
  Applied:   Post-application queue (stage in applied/interview/offer).
  Rejected Applications: Jobs rejected/not-selected after applying.
STATUS + REJECT_REASON writes live in findajob.web.routes.board_actions.
"""

import os
import sqlite3
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

from findajob.paths import BASE
from findajob.utils import load_env, log_event
from findajob.web.constants import FOLDER_STAGES as _CANONICAL_FOLDER_STAGES

load_env()

DB_PATH = f"{BASE}/data/pipeline.db"
SA_FILE = f"{BASE}/config/gsheets_creds.json"
with open(f"{BASE}/config/sheet_id.txt") as f:
    SHEET_ID = f.read().strip()

# Base URL for the materials viewer that hyperlinks company cells into.
# Set per stack (e.g., http://docker.lan:8090 matching FINDAJOB_MATERIALS_PORT).
# Unset → company cells render as plain text.
MATERIALS_BASE_URL = os.getenv("FINDAJOB_MATERIALS_BASE_URL", "").strip()

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _assert_full_write(result: dict, expected_rows: int, tab_name: str) -> None:
    """Verify Sheets `values().update()` actually wrote every row we sent.

    The API call returns HTTP 200 with a dict that includes ``updatedRows``;
    nothing in the success path cross-references it against the body. A
    server-side partial write — observed 2026-04-22 with Applied reporting
    31 rows synced locally but 0 on the sheet — looks identical to success
    until someone opens the tab. Raise on mismatch so triage.py picks up the
    non-zero exit via its ``triage_sync_failed`` handler (#145) and the
    notify.py health-check fires (#171).
    """
    actual = result.get("updatedRows") or 0
    if actual != expected_rows:
        log_event(
            "sync_partial_write",
            tab=tab_name,
            expected_rows=expected_rows,
            actual_rows=actual,
            updated_range=result.get("updatedRange", ""),
        )
        raise RuntimeError(
            f"Sheets partial write on {tab_name}: expected {expected_rows} rows "
            f"(header+data), Google reported {actual} "
            f"(range={result.get('updatedRange')})"
        )


# ── Dashboard: actionable queue ───────────────────────────────────────────────
# Col A: APPLY_FLAG (checkbox), Col B: REJECT_REASON (dropdown), Col C: fingerprint (hidden).
# Title column is rendered as =HYPERLINK(url, title) — no separate URL column.
DASH_HEADERS = [
    "APPLY_FLAG",
    "REJECT_REASON",
    "fingerprint",
    "fit_score",
    "probability_score",
    "relevance_score",
    "title",
    "company",
    "location",
    "remote_status",
    "known_contacts",
    "comp_estimate",
    "ai_notes",
    "date_found",
]
DASH_COL_MAP = {
    "apply_flag": "APPLY_FLAG",
    "reject_reason": "REJECT_REASON",
    "fingerprint": "fingerprint",
    "fit_score": "fit_score",
    "probability_score": "probability_score",
    "relevance_score": "relevance_score",
    "title": "title",
    "company": "company",
    "location": "location",
    "remote_status": "remote_status",
    "known_contacts": "known_contacts",
    "comp_estimate": "comp_estimate",
    "ai_notes": "ai_notes",
    "created_at": "date_found",
}
DASH_LOOKUP = {sh: sc for sc, sh in DASH_COL_MAP.items()}


def hyperlink(url, label):
    """Return a Sheets HYPERLINK formula. Escapes double quotes in both args."""
    safe_url = str(url or "").replace('"', "%22")
    safe_label = str(label or "").replace('"', '""')
    if not safe_url:
        return safe_label
    return f'=HYPERLINK("{safe_url}","{safe_label}")'


# Stages where a companies/ folder exists on disk and the materials viewer
# can render it. Used to decide whether the Sheet's company cell should
# hyperlink into the viewer. Canonical list lives in findajob.web.constants
# (imported at top of file) so this helper and
# src/findajob/web/templates/_job_row.html can't drift.
_FOLDER_STAGES = frozenset(_CANONICAL_FOLDER_STAGES)


def materials_company_cell(company, fingerprint, stage, base_url):
    """Return either a =HYPERLINK formula to the materials viewer or plain text.

    Hyperlinks only when `base_url` is set AND `stage` is one where a folder
    exists on disk. Missing base_url → plain text (no viewer configured for
    this stack). Non-folder stage → plain text (viewer would 404).
    """
    if not base_url or stage not in _FOLDER_STAGES:
        return safe_str(company)
    url = f"{base_url.rstrip('/')}/materials/{fingerprint}"
    return hyperlink(url, company)


def safe_str(val):
    """Escape leading formula trigger characters to prevent formula injection.

    Google Sheets interprets strings starting with =, +, -, or @ as formulas
    when valueInputOption='USER_ENTERED'. Prefix with a single quote to force
    literal text storage. The apostrophe is consumed by Sheets and not displayed.
    """
    s = "" if val is None else str(val)
    if s and s[0] in ("=", "+", "-", "@"):
        return "'" + s
    return s


def build_row(row, headers, lookup, status_override=None, reject_override=None):
    sheet_row = []
    for header in headers:
        sqlite_col = lookup.get(header)
        val = row[sqlite_col] if sqlite_col and sqlite_col in row.keys() else ""
        if header == "APPLY_FLAG":
            # Derive status from DB state; user overrides preserved via status_override
            if status_override is not None:
                sheet_row.append(status_override)
            elif row["stage"] == "materials_drafted":
                sheet_row.append("Ready to Apply")
            elif row["stage"] == "prep_in_progress":
                sheet_row.append("Prep in Progress")
            elif row["stage"] == "applied":
                sheet_row.append("Applied")
            elif row["stage"] == "interview":
                sheet_row.append("Interviewing")
            elif row["stage"] == "offer":
                sheet_row.append("Offer")
            elif bool(val) and row["stage"] in ("scored", "manual_review", "enriched"):
                sheet_row.append("Flag for Prep")
            else:
                sheet_row.append("")
        elif header == "REJECT_REASON":
            sheet_row.append(safe_str(reject_override if reject_override is not None else (val or "")))
        else:
            sheet_row.append(safe_str(val))
    return sheet_row


def sync_dashboard(svc, conn):
    rows = conn.execute("""
        SELECT * FROM jobs
        WHERE (dupe_of = '' OR dupe_of IS NULL)
          AND (
            (relevance_score >= 7 AND stage IN ('scored', 'manual_review'))
            OR stage IN ('prep_in_progress', 'materials_drafted')
          )
        ORDER BY
            CASE stage WHEN 'materials_drafted' THEN 0 ELSE 1 END,
            CASE WHEN probability_score IS NOT NULL THEN probability_score ELSE 0 END DESC,
            CASE WHEN fit_score IS NOT NULL THEN fit_score ELSE 0 END DESC,
            CASE WHEN relevance_score IS NOT NULL THEN relevance_score ELSE 0 END DESC,
            created_at DESC
    """).fetchall()

    sheet_rows = [DASH_HEADERS]
    for row in rows:
        # Skip materials_drafted jobs whose folder no longer exists on disk
        # (moved to _applied/_rejected without DB update, or manually deleted)
        if row["stage"] == "materials_drafted":
            folder = row["prep_folder_path"]
            if not folder or not Path(folder).is_dir():
                continue
        sheet_row = build_row(row, DASH_HEADERS, DASH_LOOKUP)
        # Replace plain title with a HYPERLINK formula pointing to the JD URL
        title_idx = DASH_HEADERS.index("title")
        sheet_row[title_idx] = hyperlink(row["url"], row["title"])
        # Replace plain company with a HYPERLINK into the materials viewer
        # (only hyperlinks when a folder exists on disk and base URL is set)
        company_idx = DASH_HEADERS.index("company")
        sheet_row[company_idx] = materials_company_cell(
            row["company"], row["fingerprint"], row["stage"], MATERIALS_BASE_URL
        )
        sheet_rows.append(sheet_row)

    svc.spreadsheets().values().clear(spreadsheetId=SHEET_ID, range="Dashboard!A2:N10000").execute()
    result = (
        svc.spreadsheets()
        .values()
        .update(
            spreadsheetId=SHEET_ID, range="Dashboard!A1", valueInputOption="USER_ENTERED", body={"values": sheet_rows}
        )
        .execute()
    )
    _assert_full_write(result, len(sheet_rows), "Dashboard")
    n_prepped = sum(1 for r in rows if r["stage"] == "materials_drafted")
    n_queued = len(rows) - n_prepped
    n_dash = len(sheet_rows) - 1
    print(f"Dashboard: {n_dash} jobs ({n_queued} queued, {n_prepped} prepped/pending apply)")
    return n_dash


# ── Review: manual_review triage queue ────────────────────────────────────────
# Col A: STATUS (Promote / blank), Col B: REJECT_REASON, Col C: fingerprint (hidden).
REVIEW_HEADERS = [
    "STATUS",
    "REJECT_REASON",
    "fingerprint",
    "title",
    "company",
    "score_flag_reason",
    "source",
    "date_found",
]
REVIEW_LOOKUP = {
    "STATUS": None,
    "REJECT_REASON": "reject_reason",
    "fingerprint": "fingerprint",
    "title": "title",
    "company": "company",
    "score_flag_reason": "score_flag_reason",
    "source": "source",
    "date_found": "created_at",
}

# ── Waitlist: deferred jobs ──────────────────────────────────────────────────
# Col A: STATUS (Reactivate / blank), Col B: REJECT_REASON, Col C: fingerprint (hidden).
WAITLIST_HEADERS = [
    "STATUS",
    "REJECT_REASON",
    "fingerprint",
    "title",
    "company",
    "relevance_score",
    "location",
    "remote_status",
    "ai_notes",
    "date_found",
    "blocking_app",
]
WAITLIST_LOOKUP = {
    "STATUS": None,
    "REJECT_REASON": "reject_reason",
    "fingerprint": "fingerprint",
    "title": "title",
    "company": "company",
    "relevance_score": "relevance_score",
    "location": "location",
    "remote_status": "remote_status",
    "ai_notes": "ai_notes",
    "date_found": "created_at",
    "blocking_app": None,  # computed at sync time
}


def sync_review(svc, conn):
    """Sync stage=manual_review jobs to the Review tab for human triage."""
    rows = conn.execute("""
        SELECT * FROM jobs
        WHERE (dupe_of = '' OR dupe_of IS NULL)
          AND stage = 'manual_review'
        ORDER BY
            CASE WHEN company IS NOT NULL AND company != '' THEN 0 ELSE 1 END,
            company, created_at DESC
    """).fetchall()

    sheet_rows = [REVIEW_HEADERS]
    for row in rows:
        sheet_row = []
        for header in REVIEW_HEADERS:
            sqlite_col = REVIEW_LOOKUP.get(header)
            if header == "STATUS":
                sheet_row.append("")
            elif header == "REJECT_REASON":
                sheet_row.append(safe_str(row["reject_reason"] or ""))
            elif header == "title":
                sheet_row.append(hyperlink(row["url"], row["title"]))
            else:
                val = row[sqlite_col] if sqlite_col and sqlite_col in row.keys() else ""
                sheet_row.append(safe_str(val))
        sheet_rows.append(sheet_row)

    svc.spreadsheets().values().clear(spreadsheetId=SHEET_ID, range="Review!A2:H10000").execute()
    result = (
        svc.spreadsheets()
        .values()
        .update(spreadsheetId=SHEET_ID, range="Review!A1", valueInputOption="USER_ENTERED", body={"values": sheet_rows})
        .execute()
    )
    _assert_full_write(result, len(sheet_rows), "Review")
    n_review = len(sheet_rows) - 1
    print(f"Review: {n_review} manual_review jobs synced")
    return n_review


def sync_waitlist(svc, conn):
    """Sync stage=waitlisted jobs to the Waitlist tab."""
    rows = conn.execute("""
        SELECT * FROM jobs
        WHERE (dupe_of = '' OR dupe_of IS NULL)
          AND stage = 'waitlisted'
        ORDER BY company, created_at DESC
    """).fetchall()

    # Build blocking_app lookup: active applications by company
    active_rows = conn.execute("""
        SELECT title, company, stage FROM jobs
        WHERE (dupe_of = '' OR dupe_of IS NULL)
          AND stage IN ('prep_in_progress', 'materials_drafted', 'applied', 'interview', 'offer')
        ORDER BY created_at DESC
    """).fetchall()
    active_by_company = {}
    for ar in active_rows:
        co = (ar["company"] or "").strip().lower()
        if co and co not in active_by_company:
            active_by_company[co] = f"{ar['title']} ({ar['stage']})"

    sheet_rows = [WAITLIST_HEADERS]
    for row in rows:
        sheet_row = []
        for header in WAITLIST_HEADERS:
            sqlite_col = WAITLIST_LOOKUP.get(header)
            if header == "STATUS":
                sheet_row.append("")
            elif header == "REJECT_REASON":
                sheet_row.append(safe_str(row["reject_reason"] or ""))
            elif header == "title":
                sheet_row.append(hyperlink(row["url"], row["title"]))
            elif header == "company":
                sheet_row.append(
                    materials_company_cell(row["company"], row["fingerprint"], row["stage"], MATERIALS_BASE_URL)
                )
            elif header == "blocking_app":
                co = (row["company"] or "").strip().lower()
                sheet_row.append(safe_str(active_by_company.get(co, "")))
            else:
                val = row[sqlite_col] if sqlite_col and sqlite_col in row.keys() else ""
                sheet_row.append(safe_str(val))
        sheet_rows.append(sheet_row)

    svc.spreadsheets().values().clear(spreadsheetId=SHEET_ID, range="Waitlist!A2:K10000").execute()
    result = (
        svc.spreadsheets()
        .values()
        .update(
            spreadsheetId=SHEET_ID, range="Waitlist!A1", valueInputOption="USER_ENTERED", body={"values": sheet_rows}
        )
        .execute()
    )
    _assert_full_write(result, len(sheet_rows), "Waitlist")
    n_waitlist = len(sheet_rows) - 1
    print(f"Waitlist: {n_waitlist} waitlisted jobs synced")
    return n_waitlist


# ── Applied: post-application queue (stage in applied/interview/offer) ───────
# Col A: STATUS (Interviewing/Offer/Not Selected/Withdrew),
# Col B: REJECT_REASON, Col C: fingerprint (hidden).
# Title → hyperlink to JD; Company → hyperlink to materials viewer folder
# (when FINDAJOB_MATERIALS_BASE_URL is set and the row's stage has a folder).
# Col F: applied_date, Col G: days_since_applied (live TODAY() formula).
APPLIED_HEADERS = [
    "STATUS",
    "REJECT_REASON",
    "fingerprint",
    "title",
    "company",
    "applied_date",
    "days_since_applied",
    "stage",
    "user_notes",
    "known_contacts",
    "location",
    "remote_status",
    "comp_estimate",
    "ai_notes",
]


def sync_applied(svc, conn):
    """Sync post-application jobs (stage in applied/interview/offer) to Applied tab.

    Read-only view of DB state. STATUS dropdown (Interviewing/Offer/Not
    Selected/Withdrew) and user_notes are edited via the web UI at
    /board/applied; transitions happen in findajob.actions, not via Sheet
    readback.
    """
    rows = conn.execute("""
        SELECT * FROM jobs
        WHERE (dupe_of = '' OR dupe_of IS NULL)
          AND stage IN ('applied', 'interview', 'offer')
        ORDER BY
            CASE stage WHEN 'offer' THEN 0 WHEN 'interview' THEN 1 ELSE 2 END,
            updated_at DESC
    """).fetchall()

    # applied_date from audit log — earliest transition INTO a post-application
    # stage. Some jobs skip 'applied' (e.g., recruiter contacts user first and
    # they jump straight to 'interview'), so we can't require new_value='applied'.
    applied_dates = {}
    for row in rows:
        entry = conn.execute(
            "SELECT changed_at FROM audit_log WHERE job_id=? "
            "AND field_changed='stage' AND new_value IN ('applied', 'interview', 'offer') "
            "ORDER BY changed_at ASC LIMIT 1",
            (row["id"],),
        ).fetchone()
        if entry:
            applied_dates[row["id"]] = entry["changed_at"][:10]

    sheet_rows = [APPLIED_HEADERS]
    for i, row in enumerate(rows, start=2):  # row index on sheet (row 1 = header)
        fp = row["fingerprint"]
        # STATUS derived purely from DB stage (web UI is the write surface).
        if row["stage"] == "offer":
            status = "Offer"
        elif row["stage"] == "interview":
            status = "Interviewing"
        else:
            status = ""  # stage=applied — user hasn't changed it yet
        reject = row["reject_reason"] or ""
        user_notes = row["user_notes"] or ""
        applied_date = applied_dates.get(row["id"], "")
        # Live formula so "days_since_applied" updates without re-sync.
        days_formula = f'=IF(F{i}="","",TODAY()-F{i})' if applied_date else ""
        title_cell = hyperlink(row["url"], row["title"]) if row["url"] else safe_str(row["title"])
        company_cell = materials_company_cell(row["company"], row["fingerprint"], row["stage"], MATERIALS_BASE_URL)
        sheet_rows.append(
            [
                status,
                safe_str(reject),
                safe_str(fp),
                title_cell,
                company_cell,
                safe_str(applied_date),
                days_formula,
                safe_str(row["stage"]),
                safe_str(user_notes),
                safe_str(row["known_contacts"] or ""),
                safe_str(row["location"] or ""),
                safe_str(row["remote_status"] or ""),
                safe_str(row["comp_estimate"] or ""),
                safe_str(row["ai_notes"] or ""),
            ]
        )

    svc.spreadsheets().values().clear(spreadsheetId=SHEET_ID, range="Applied!A2:N10000").execute()
    result = (
        svc.spreadsheets()
        .values()
        .update(
            spreadsheetId=SHEET_ID,
            range="Applied!A1",
            valueInputOption="USER_ENTERED",
            body={"values": sheet_rows},
        )
        .execute()
    )
    _assert_full_write(result, len(sheet_rows), "Applied")
    n = len(sheet_rows) - 1
    print(f"Applied: {n} post-application jobs synced")
    return n


# ── Rejected Applications: jobs rejected after applying ──────────────────────
REJECTED_APPS_HEADERS = [
    "title",
    "company",
    "reject_reason",
    "applied_date",
    "rejected_date",
    "fit_score",
    "probability_score",
    "ai_notes",
]


def sync_rejected_apps(svc, conn):
    """Sync jobs that were rejected or not selected after being in 'applied' stage.

    Retirable: superseded by the web `/board/rejected` view (#191).
    """
    rows = conn.execute("""
        SELECT j.*, a.changed_at AS rejected_date
        FROM jobs j
        JOIN audit_log a ON a.job_id = j.id
        WHERE a.field_changed = 'stage'
          AND a.old_value IN ('applied', 'interview', 'offer')
          AND a.new_value IN ('rejected', 'not_selected')
        ORDER BY a.changed_at DESC
    """).fetchall()

    # Look up the date each job was marked applied
    applied_dates = {}
    for row in rows:
        applied_entry = conn.execute(
            "SELECT changed_at FROM audit_log WHERE job_id=? "
            "AND field_changed='stage' AND new_value='applied' "
            "ORDER BY changed_at DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        if applied_entry:
            applied_dates[row["id"]] = applied_entry["changed_at"][:10]

    sheet_rows = [REJECTED_APPS_HEADERS]
    for row in rows:
        sheet_row = [
            hyperlink(row["url"], row["title"]),
            materials_company_cell(row["company"], row["fingerprint"], row["stage"], MATERIALS_BASE_URL),
            safe_str(row["reject_reason"] or ""),
            safe_str(applied_dates.get(row["id"], "")),
            safe_str(row["rejected_date"][:10] if row["rejected_date"] else ""),
            safe_str(row["fit_score"] if row["fit_score"] else ""),
            safe_str(row["probability_score"] if row["probability_score"] else ""),
            safe_str(row["ai_notes"] or ""),
        ]
        sheet_rows.append(sheet_row)

    svc.spreadsheets().values().clear(spreadsheetId=SHEET_ID, range="Rejected Applications!A2:H10000").execute()
    result = (
        svc.spreadsheets()
        .values()
        .update(
            spreadsheetId=SHEET_ID,
            range="Rejected Applications!A1",
            valueInputOption="USER_ENTERED",
            body={"values": sheet_rows},
        )
        .execute()
    )
    _assert_full_write(result, len(sheet_rows), "Rejected Applications")
    n = len(sheet_rows) - 1
    print(f"Rejected Applications: {n} rejected-after-apply jobs synced")
    return n


def main():
    creds = service_account.Credentials.from_service_account_file(SA_FILE, scopes=SCOPES)
    svc = build("sheets", "v4", credentials=creds)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row

    try:
        n_dash = sync_dashboard(svc, conn)
        n_review = sync_review(svc, conn)
        n_waitlist = sync_waitlist(svc, conn)
        n_applied = sync_applied(svc, conn)
        sync_rejected_apps(svc, conn)
        log_event(
            "sync_complete",
            dashboard=n_dash,
            review=n_review,
            waitlist=n_waitlist,
            applied=n_applied,
        )
    except Exception as e:
        log_event("sync_failed", error=str(e))
        raise

    conn.close()


if __name__ == "__main__":
    main()
