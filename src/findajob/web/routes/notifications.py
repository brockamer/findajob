"""In-app notification dashboard + history (#440).

Mirrors what `scripts/notify.py` already pushes to ntfy.sh, but persisted
server-side so the operator (and testers) can scan recent signals without
leaving the app. The DB row is written by `scripts/notify.py:send()` BEFORE
the ntfy POST — so the audit trail captures even ntfy outages.

Routes:
- ``GET /notifications/`` — reverse-chronological list with kind / read-state filters
- ``POST /notifications/{id}/read`` — mark a row read (idempotent)
- ``POST /notifications/mark-all-read`` — bulk mark
- ``GET /notifications/badge`` — HTMX fragment returning the unread count badge
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from findajob.web.routes.materials import get_db

router = APIRouter()

# Per-row pagination cap. The page is server-rendered so a hard ceiling is
# safer than streaming — older rows still load via the `?before=<id>` cursor.
_PAGE_SIZE = 50

# Kinds expected to render with badge styling. Unknown kinds still render,
# just with the default slate badge (forward-compat for #362's
# ``rejection_detected`` and any future kinds).
_KIND_LABELS: dict[str, str] = {
    "daily_stats": "Daily stats",
    "apply_reminder": "Apply reminder",
    "feedback_review": "Feedback review",
    "scoreboard": "Scoreboard",
    "health_check": "Health check",
    "issues_ping": "Issues ping",
    "ci_check": "CI check",
    "send_raw": "Manual send",
    "discovery_run": "Discovery",
    "gmail_auth_failure": "Gmail auth",
    "rejection_detected": "Rejection detected",
}


def _humanize_kind(kind: str) -> str:
    return _KIND_LABELS.get(kind, kind.replace("_", " ").title())


def _parse_kinds(raw: str) -> list[str]:
    return [k.strip() for k in raw.split(",") if k.strip()] if raw else []


@router.get("/notifications/", response_class=HTMLResponse)
def notifications_index(
    request: Request,
    kind: str = "",
    read: str = "",
    before: int = 0,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """Reverse-chronological list of notifications.

    Filters:
    - ``?kind=a,b,c`` — comma-separated kinds (any-of semantics)
    - ``?read=unread|read`` — restrict by read state (omit for all)
    - ``?before=<id>`` — pagination cursor (rows older than this id)
    """
    where_clauses: list[str] = []
    params: list[object] = []

    kinds = _parse_kinds(kind)
    if kinds:
        placeholders = ",".join("?" * len(kinds))
        where_clauses.append(f"kind IN ({placeholders})")
        params.extend(kinds)

    if read == "unread":
        where_clauses.append("read_at IS NULL")
    elif read == "read":
        where_clauses.append("read_at IS NOT NULL")

    if before > 0:
        where_clauses.append("id < ?")
        params.append(before)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    rows = db.execute(
        f"""
        SELECT id, sent_at, kind, title, body, priority, tags,
               delivery_status, delivery_error, cta_url, read_at
        FROM notifications
        {where_sql}
        ORDER BY id DESC
        LIMIT ?
        """,
        (*params, _PAGE_SIZE + 1),
    ).fetchall()

    has_more = len(rows) > _PAGE_SIZE
    rows = rows[:_PAGE_SIZE]

    next_before = rows[-1]["id"] if has_more and rows else 0

    unread_total = db.execute("SELECT COUNT(*) FROM notifications WHERE read_at IS NULL").fetchone()[0]

    kind_counts = db.execute(
        """
        SELECT kind, COUNT(*) AS n
        FROM notifications
        GROUP BY kind
        ORDER BY n DESC
        """
    ).fetchall()

    items = [
        {
            "id": r["id"],
            "sent_at": r["sent_at"],
            "kind": r["kind"],
            "kind_label": _humanize_kind(r["kind"]),
            "title": r["title"],
            "body": r["body"],
            "priority": r["priority"],
            "tags": r["tags"],
            "delivery_status": r["delivery_status"],
            "delivery_error": r["delivery_error"],
            "cta_url": r["cta_url"],
            "read_at": r["read_at"],
        }
        for r in rows
    ]

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="notifications/index.html",
        context={
            "items": items,
            "unread_total": unread_total,
            "selected_kinds": kinds,
            "selected_read": read,
            "kind_counts": [{"kind": r["kind"], "label": _humanize_kind(r["kind"]), "n": r["n"]} for r in kind_counts],
            "has_more": has_more,
            "next_before": next_before,
        },
    )


@router.post("/notifications/{notif_id}/read", response_model=None)
def mark_read(
    notif_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse | RedirectResponse:
    """Idempotent mark-as-read. HTMX returns the updated row; full-page POSTs redirect back."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "UPDATE notifications SET read_at = ? WHERE id = ? AND read_at IS NULL",
        (now, notif_id),
    )
    db.commit()

    if request.headers.get("HX-Request"):
        row = db.execute(
            """
            SELECT id, sent_at, kind, title, body, priority, tags,
                   delivery_status, delivery_error, cta_url, read_at
            FROM notifications WHERE id = ?
            """,
            (notif_id,),
        ).fetchone()
        if row is None:
            return HTMLResponse("", status_code=404)
        item = {
            "id": row["id"],
            "sent_at": row["sent_at"],
            "kind": row["kind"],
            "kind_label": _humanize_kind(row["kind"]),
            "title": row["title"],
            "body": row["body"],
            "priority": row["priority"],
            "tags": row["tags"],
            "delivery_status": row["delivery_status"],
            "delivery_error": row["delivery_error"],
            "cta_url": row["cta_url"],
            "read_at": row["read_at"],
        }
        templates = request.app.state.templates
        return templates.TemplateResponse(
            request=request,
            name="notifications/_row.html",
            context={"item": item},
        )

    return RedirectResponse(url="/notifications/", status_code=303)


@router.post("/notifications/mark-all-read")
def mark_all_read(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> RedirectResponse:
    """Mark every unread notification on this stack as read."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE notifications SET read_at = ? WHERE read_at IS NULL", (now,))
    db.commit()
    return RedirectResponse(url="/notifications/", status_code=303)


@router.get("/notifications/badge", response_class=HTMLResponse)
def badge(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    """HTMX-poll endpoint for the bell icon's unread count."""
    n = db.execute("SELECT COUNT(*) FROM notifications WHERE read_at IS NULL").fetchone()[0]
    if n == 0:
        return HTMLResponse('<span id="nav-notif-badge"></span>')
    return HTMLResponse(
        f'<span id="nav-notif-badge" '
        f'class="ml-1 inline-flex items-center justify-center text-xs font-bold '
        f'rounded-full bg-amber-500 text-slate-900 min-w-[1.25rem] h-5 px-1">{n}</span>'
    )
