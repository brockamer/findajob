"""FastAPI app factory for the materials viewer."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from findajob.audit import log_event
from findajob.db import connect
from findajob.web.auth import install_basic_auth
from findajob.web.constants import FOLDER_STAGES
from findajob.web.helpers import (
    applied_age_bucket,
    filter_qs_with,
    filter_remove_qs,
    remote_cell_class,
    stage_row_class,
)
from findajob.web.onboarding_guard import onboarding_complete, require_onboarding_complete
from findajob.web.routes import materials as _materials_routes
from findajob.web.routes import router as _aggregated_router


def create_app(
    *,
    companies_root: Path,
    db_path: Path,
    base_root: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="findajob materials viewer", docs_url=None, redoc_url=None)

    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    templates.env.globals["folder_stages"] = set(FOLDER_STAGES)
    templates.env.globals["applied_age_bucket"] = applied_age_bucket
    templates.env.globals["remote_cell_class"] = remote_cell_class
    templates.env.globals["stage_row_class"] = stage_row_class
    templates.env.globals["filter_remove_qs"] = filter_remove_qs
    templates.env.globals["filter_qs_with"] = filter_qs_with
    templates.env.globals["operator_mode"] = os.environ.get("FINDAJOB_OPERATOR_MODE") == "1"

    def _reject_reason_options() -> tuple[str, ...]:
        from findajob.config_loader import load_reject_reasons

        reasons, _title_signal = load_reject_reasons()
        return reasons

    templates.env.globals["reject_reason_options"] = _reject_reason_options

    # Nav chip — current calendar-month spend. Wrapped in a function so each
    # request re-queries; cost_log rows arrive whenever the wrapper writes one.
    def _spend_this_month_for_template() -> float:
        try:
            conn = connect(db_path, timeout=5)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error:
            return 0.0
        try:
            from findajob.cost_rollups import spend_this_month

            return spend_this_month(conn)
        except sqlite3.Error:
            return 0.0
        finally:
            conn.close()

    templates.env.globals["spend_this_month_for_template"] = _spend_this_month_for_template
    templates.env.globals["onboarding_complete"] = onboarding_complete

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Browsers default-request /favicon.ico at the document root regardless
    # of <link rel="icon">, producing a 404 on every page load (#138). Serve
    # the existing SVG from that path; modern browsers accept SVG in the
    # .ico slot.
    favicon_path = static_dir / "favicon.svg"

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse(favicon_path, media_type="image/svg+xml")

    # #628: surface route-raised HTTPException(>=500) in pipeline.jsonl.
    # Without this, uvicorn's access log shows only "500 Internal Server
    # Error" and the detail string is buried in the response body.
    @app.exception_handler(HTTPException)
    async def _log_5xx_then_default(request: Request, exc: HTTPException) -> Response:
        if exc.status_code >= 500:
            log_event(
                "http_5xx",
                path=request.url.path,
                status=exc.status_code,
                detail=str(exc.detail),
            )
        return await http_exception_handler(request, exc)

    app.state.companies_root = companies_root
    app.state.db_path = db_path
    app.state.base_root = base_root if base_root is not None else Path(os.environ.get("JSP_BASE", "/app"))
    app.state.templates = templates

    # Schema migrations run from scripts/init_db.py at container start
    # (ops/entrypoint.sh) via findajob.db.migrate.apply_pending. By the
    # time create_app runs the DB is already at the head migration
    # version, so app.py does no schema work — single migration entry
    # point per M5.

    def get_db() -> Generator[sqlite3.Connection, None, None]:
        # check_same_thread=False is required because BaseHTTPMiddleware (used by
        # findajob.web.auth) wraps the inner app in a separate anyio task — so
        # FastAPI's Depends resolution and the route handler can land on
        # different threadpool workers under concurrent load. Per-request
        # connection + serialized SQLite mode = safe to disable the thread guard.
        # See #486.
        conn = connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides.setdefault(_materials_routes.get_db, get_db)
    app.include_router(_aggregated_router)
    if os.environ.get("FINDAJOB_OPERATOR_MODE") == "1":
        from findajob.web.routes import admin_stacks

        app.include_router(
            admin_stacks.router,
            dependencies=[Depends(require_onboarding_complete)],
        )
    # In-app onboarding interview routes (#336 + #339): registered
    # unconditionally. The runtime gate is per-request via
    # ``_resolved_chat_key`` — the tester must have collected their own
    # OpenRouter key at /onboarding/ Step 1 (#339). When no key is on
    # file the routes surface a 503 with a pointer back to /onboarding/.
    # The previous import-time gate (which 404'd on stacks with no
    # operator key) made self-deploy impossible.
    from findajob.web.routes import onboarding_interview

    app.include_router(onboarding_interview.router)
    install_basic_auth(app)
    return app


def default_app() -> FastAPI:
    """Factory used by uvicorn at container start.

    Reads COMPANIES_ROOT and DB_PATH from env. Defaults are derived from
    JSP_BASE so single-volume deploys (Fly, k8s) with JSP_BASE=/app/state
    resolve to /app/state/companies and /app/state/data/pipeline.db
    without needing per-stack env overrides.
    """
    jsp_base = os.environ.get("JSP_BASE", "/app")
    companies_root = Path(os.environ.get("COMPANIES_ROOT", f"{jsp_base}/companies"))
    db_path = Path(os.environ.get("DB_PATH", f"{jsp_base}/data/pipeline.db"))
    base_root = Path(jsp_base)
    return create_app(companies_root=companies_root, db_path=db_path, base_root=base_root)
