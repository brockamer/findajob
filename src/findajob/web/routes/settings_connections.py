"""#614: /settings/connections/ — returning-user maintenance UI for connections.csv.

Third occupant of the /settings/ namespace, joining /settings/reject-reasons/
(#490) and /settings/active-sources/ (#603). The onboarding gate at
/onboarding/connections/ (#571) handles first-run upload; this page handles
the return visits — see when the file was last imported, view row count,
refresh with a newer export, or remove the file entirely.

Validation + atomic write share :mod:`findajob.web.connections_upload` with
the onboarding gate so the two paths can't drift on what counts as a valid
header / size / encoding.

Remove is gated by a confirm step — destructive and unrecoverable from the
UI (user must re-export from LinkedIn to undo), mirroring the #700
regenerate-confirm pattern.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import HTMLResponse

from findajob.timeutil import local_zoneinfo
from findajob.web.connections_upload import (
    MAX_BYTES,
    atomic_write_connections,
    connections_path,
    count_connections_rows,
    validate_connections_csv,
)

router = APIRouter(prefix="/settings/connections", tags=["settings"])


def _humanize_age(seconds: float) -> str:
    """Render an "X ago" string for the connections.csv mtime.

    Day / week / month grain — the file is point-in-time and refreshed
    on a weeks-to-months cadence, so finer precision isn't meaningful.
    """
    minutes = int(seconds // 60)
    if minutes < 60:
        return "just now"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    if days < 14:
        return f"{days} day{'s' if days != 1 else ''} ago"
    weeks = days // 7
    if weeks < 8:
        return f"{weeks} weeks ago"
    months = days // 30
    return f"{months} month{'s' if months != 1 else ''} ago"


def _build_state(base: Path) -> dict:
    path = connections_path(base)
    if not path.exists():
        return {
            "file_present": False,
            "last_imported_local": None,
            "last_imported_relative": None,
            "row_count": 0,
        }
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    age_seconds = (datetime.now(UTC) - mtime).total_seconds()
    return {
        "file_present": True,
        "last_imported_local": mtime.astimezone(local_zoneinfo()).strftime("%Y-%m-%d %H:%M %Z"),
        "last_imported_relative": _humanize_age(age_seconds),
        "row_count": count_connections_rows(path),
    }


@router.get("/", response_class=HTMLResponse)
def get_connections_editor(request: Request) -> HTMLResponse:
    """Render the connections-maintenance page with current file state."""
    base = Path(request.app.state.base_root)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="settings/connections.html",
        context=_build_state(base),
    )


@router.post("/upload", response_model=None)
async def post_upload(
    request: Request,
    connections_csv: UploadFile,
) -> HTMLResponse:
    """Refresh / replace the existing connections.csv with a new upload.

    Shares :func:`validate_connections_csv` with the onboarding gate so
    the accept/reject behavior is byte-identical. Atomic replace via
    tempfile + ``os.replace`` keeps a racing prep run from seeing a
    half-written file.

    Always returns the full page (not a partial) so the post-save state
    — new mtime, new row count — surfaces immediately. Success and
    validation-error responses both 200 with the rendered page; the error
    banner is conditional on a context flag.
    """
    base = Path(request.app.state.base_root)
    templates = request.app.state.templates

    raw = await connections_csv.read(MAX_BYTES + 1)
    error, _ = validate_connections_csv(raw)
    if error is not None:
        context = _build_state(base)
        context["validation_error"] = error
        return templates.TemplateResponse(
            request=request,
            name="settings/connections.html",
            context=context,
            status_code=400,
        )

    atomic_write_connections(base, raw)
    context = _build_state(base)
    context["save_success"] = True
    return templates.TemplateResponse(
        request=request,
        name="settings/connections.html",
        context=context,
    )


@router.get("/remove/confirm", response_class=HTMLResponse)
def get_remove_confirm(request: Request) -> HTMLResponse:
    """Render the remove-confirm zone in place of the initial Remove button.

    Mirrors the #700 regenerate-confirm pattern: GET swaps a confirm-or-cancel
    pair into the page, POST performs the destructive action. The cancel
    endpoint restores the original Remove button.
    """
    base = Path(request.app.state.base_root)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="settings/_connections_remove_confirm.html",
        context=_build_state(base),
    )


@router.get("/remove/cancel", response_class=HTMLResponse)
def get_remove_cancel(request: Request) -> HTMLResponse:
    """Restore the initial Remove button after a Cancel click on the
    confirm zone. Re-renders the same zone partial in its default shape."""
    base = Path(request.app.state.base_root)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="settings/_connections_remove_zone.html",
        context=_build_state(base),
    )


@router.post("/remove", response_class=HTMLResponse)
def post_remove(request: Request) -> HTMLResponse:
    """Delete connections.csv from disk and re-render the full page.

    ``find_contacts`` already handles a missing file gracefully (returns
    [] without logging an error per :mod:`findajob.find_contacts`), so
    deletion has no follow-on cleanup. Idempotent: removing a missing
    file succeeds and renders the empty state.
    """
    base = Path(request.app.state.base_root)
    templates = request.app.state.templates

    path = connections_path(base)
    if path.exists():
        path.unlink()

    context = _build_state(base)
    context["remove_success"] = True
    return templates.TemplateResponse(
        request=request,
        name="settings/connections.html",
        context=context,
    )
