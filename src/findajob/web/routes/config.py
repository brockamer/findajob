"""In-browser editor for pipeline config files (#149).

Three endpoints:

* ``GET /config/`` — index page, groups editable files by category.
* ``GET /config/files/{path:path}`` — editor view with current content in a textarea.
* ``POST /config/files/{path:path}`` — save handler, returns an HTMX result partial.

The allowlist lives in :mod:`findajob.web.config_files`.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from findajob import gmail_imap
from findajob.web.config_files import list_editable, resolve_editable

router = APIRouter()


def _gmail_status() -> str:
    """Mirror of gmail_config._derive_status, used for the /config/ summary pill."""
    config = gmail_imap.load_config()
    if config is None:
        return "off"
    state = gmail_imap.load_state()
    if state.last_error == "auth_failed":
        return "login_failed"
    if state.last_login_at:
        return "authorized"
    return "saved_untested"


@router.get("/config/", response_class=HTMLResponse)
def config_index(request: Request) -> HTMLResponse:
    base_root: Path = request.app.state.base_root
    categories = list_editable(base_root)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="config/index.html",
        context={
            "categories": categories,
            "gmail_status": _gmail_status(),
        },
    )


@router.get("/config/files/{relpath:path}", response_class=HTMLResponse)
def config_edit_form(relpath: str, request: Request) -> HTMLResponse:
    base_root: Path = request.app.state.base_root
    resolved = resolve_editable(relpath, base_root)
    if resolved is None:
        raise HTTPException(status_code=403, detail="file is not editable")

    content = ""
    exists = resolved.is_file()
    error: str | None = None
    if exists:
        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        except PermissionError:
            # File exists but is unreadable by the web process (e.g., it was
            # written by a different user via `docker exec` and has restrictive
            # mode). Render an inline error rather than 500ing into HTMX.
            error = (
                f"Cannot read {relpath}: permission denied. The file exists but "
                "is not readable by the web server. Check ownership and mode "
                "on the host (e.g., `chown $(id -u):$(id -g) <file>`, "
                "`chmod 644 <file>`)."
            )
        except OSError as exc:
            error = f"Cannot read {relpath}: {exc.strerror or type(exc).__name__}."

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="config/editor.html",
        context={"relpath": relpath, "content": content, "exists": exists, "error": error},
    )


@router.post("/config/files/{relpath:path}", response_class=HTMLResponse)
def config_save(
    relpath: str,
    request: Request,
    content: str = Form(...),
) -> HTMLResponse:
    base_root: Path = request.app.state.base_root
    resolved = resolve_editable(relpath, base_root)
    if resolved is None:
        raise HTTPException(status_code=403, detail="file is not editable")

    resolved.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=resolved.name + ".",
        suffix=".tmp",
        dir=str(resolved.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(tmp_name, resolved)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise

    try:
        from findajob.db import connect as _db_connect
        from findajob.metrics.config_changes import detect_and_record
        from findajob.paths import BASE as _BASE

        _conn = _db_connect(f"{_BASE}/data/pipeline.db", timeout=5)
        detect_and_record(_conn, changed_by="manual", change_summary=f"edit via /config/ — {relpath}")
        _conn.close()
    except Exception:
        pass

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="config/_save_result.html",
        context={"outcome": "success", "message": f"Saved {relpath}."},
    )
