"""GET + POST /onboarding/connections/{session_id}/ — terminal gate (#571).

Final onboarding step: prompt for the LinkedIn ``Connections.csv`` export that
drives :mod:`findajob.find_contacts`. The user either uploads a valid CSV or
explicitly skips. Either way, the sentinel is written exactly here, replacing
gmail-config as the terminal gate that ends every onboarding flow.

Scope is intentionally narrow per the trimmed-scope decision recorded in the
2026-05-10 start-time backstop comment on #571: this PR ships the onboarding
upload step only. The maintenance UI at ``/settings/connections/`` (refresh,
last-imported, row count, remove) is filed as a follow-up (#614).
"""

from __future__ import annotations

import csv
import io
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from findajob.onboarding.injector import mark_complete

router = APIRouter(prefix="/onboarding/connections", tags=["onboarding"])

# Mirrors the columns read by findajob.find_contacts. First Name / Last Name
# are hard-required (KeyError without them — see tests/test_find_contacts.py
# test_malformed_csv_still_logs_error); the rest are .get()'d but contribute
# no signal if absent. Validating all six up front gives a clear error before
# the user discovers prep produces zero outreach drafts.
_REQUIRED_COLUMNS = ("First Name", "Last Name", "Company", "Position", "Connected On", "URL")

# Bound the multipart read so an oversize or wrong-content upload can't OOM
# the worker. A LinkedIn connections export of 30,000 connections fits well
# inside 16 MiB; this ceiling is comfortably above any realistic real-user file.
_MAX_BYTES = 16 * 1024 * 1024


def _ctx(session_id: str, *, validation_error: str | None = None) -> dict:
    return {
        "session_id": session_id,
        "validation_error": validation_error,
    }


@router.get("/{session_id}/", response_class=HTMLResponse)
def get_connections_gate(session_id: str, request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="onboarding_connections/index.html",
        context=_ctx(session_id),
    )


@router.post("/{session_id}/skip")
def post_skip(session_id: str, request: Request) -> Response:
    """Skip the connections upload; write sentinel; redirect to dashboard.

    Skipping is always allowed — find_contacts handles a missing file
    silently. The user can return via ``/onboarding/?mode=rerun``.
    """
    base = Path(request.app.state.base_root)
    mark_complete(base)
    return RedirectResponse("/board/dashboard", status_code=303)


@router.post("/{session_id}/upload", response_model=None)
async def post_upload(
    session_id: str,
    request: Request,
    connections_csv: UploadFile,
) -> HTMLResponse | Response:
    """Validate the uploaded CSV header, write the file atomically, write
    sentinel, redirect to dashboard.

    Strict header validation: the first row of the CSV must contain every
    column in :data:`_REQUIRED_COLUMNS`. Anything else (LinkedIn preamble,
    column renames, partial exports) is rejected with an inline error and
    no sentinel write. The user can re-export from LinkedIn and retry, or
    fall back to Skip.

    Atomic write: payload lands in a tempfile in the same directory as the
    destination, then ``os.replace`` swaps it in. Prevents partial-file
    visibility to a prep run that races the upload.
    """
    templates = request.app.state.templates
    base = Path(request.app.state.base_root)

    raw = await connections_csv.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        return templates.TemplateResponse(
            request=request,
            name="onboarding_connections/index.html",
            context=_ctx(
                session_id,
                validation_error=(
                    f"Upload exceeds the {_MAX_BYTES // (1024 * 1024)} MiB ceiling. "
                    "Either re-export with the Connections-only option (smaller) "
                    "or use Skip for now."
                ),
            ),
            status_code=400,
        )

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return templates.TemplateResponse(
            request=request,
            name="onboarding_connections/index.html",
            context=_ctx(
                session_id,
                validation_error=(
                    "We couldn't read the file as UTF-8 text. Make sure you uploaded "
                    "the Connections.csv from inside the LinkedIn data-export ZIP, "
                    "not the ZIP itself."
                ),
            ),
            status_code=400,
        )

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return templates.TemplateResponse(
            request=request,
            name="onboarding_connections/index.html",
            context=_ctx(
                session_id,
                validation_error="The file is empty. Re-export from LinkedIn and try again.",
            ),
            status_code=400,
        )

    missing = [col for col in _REQUIRED_COLUMNS if col not in header]
    if missing:
        return templates.TemplateResponse(
            request=request,
            name="onboarding_connections/index.html",
            context=_ctx(
                session_id,
                validation_error=(
                    f"The first row of the CSV is missing required columns: {', '.join(missing)}. "
                    "Expected the LinkedIn Connections export header — "
                    "First Name, Last Name, Company, Position, Connected On, URL. "
                    "If your file has a 'Notes:' preamble at the top, delete those lines "
                    "(plus the blank line that follows) so the column headers are on row 1."
                ),
            ),
            status_code=400,
        )

    dest = base / "data" / "connections.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".connections_upload_", dir=str(dest.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        os.replace(tmp_path, dest)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    mark_complete(base)
    return RedirectResponse("/board/dashboard", status_code=303)
