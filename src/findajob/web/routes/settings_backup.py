"""#841: /settings/backup/ — one-click backup tarball download.

Every stack (operator, tester, Fly, Docker) gets a self-service backup.
Not operator-mode-gated: every tester needs their own backup.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from findajob.web.backup import stream_backup_tarball

router = APIRouter(prefix="/settings/backup", tags=["settings"])


@router.get("/", response_class=HTMLResponse)
def get_backup_page(request: Request) -> HTMLResponse:
    base = Path(request.app.state.base_root)
    templates = request.app.state.templates
    db_path: Path = request.app.state.db_path
    db_exists = db_path.is_file()
    db_size_mb = round(db_path.stat().st_size / (1024 * 1024), 1) if db_exists else 0
    state_dirs = []
    for name in ("data", "config", "candidate_context", "companies", "logs"):
        d = base / name
        if d.is_dir():
            state_dirs.append(name)
    return templates.TemplateResponse(
        request=request,
        name="settings/backup.html",
        context={
            "db_exists": db_exists,
            "db_size_mb": db_size_mb,
            "state_dirs": state_dirs,
        },
    )


@router.post("/download")
def post_download(request: Request) -> StreamingResponse:
    base = Path(request.app.state.base_root)
    db_path: Path = request.app.state.db_path
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"findajob-backup-{ts}.tar.gz"
    return StreamingResponse(
        stream_backup_tarball(base, db_path),
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
