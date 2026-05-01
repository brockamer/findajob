"""FastAPI app factory for the materials viewer."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from findajob.web.auth import install_basic_auth
from findajob.web.constants import FOLDER_STAGES
from findajob.web.helpers import (
    applied_age_bucket,
    filter_qs_with,
    filter_remove_qs,
    remote_cell_class,
    stage_row_class,
)
from findajob.web.onboarding_guard import require_onboarding_complete
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

    app.state.companies_root = companies_root
    app.state.db_path = db_path
    app.state.base_root = base_root if base_root is not None else Path(os.environ.get("JSP_BASE", "/app"))
    app.state.templates = templates

    def get_db() -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(db_path))
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
    # In-app onboarding interview routes (#336): only registered when the
    # operator opts in by setting OPENROUTER_OPERATOR_KEY. When unset, the
    # in-app interview is unavailable and /onboarding/ falls back to
    # paste-back only — no broken affordance (acceptance criterion #6).
    if (os.environ.get("OPENROUTER_OPERATOR_KEY") or "").strip():
        from findajob.web.routes import onboarding_interview

        app.include_router(onboarding_interview.router)
    install_basic_auth(app)
    return app


def default_app() -> FastAPI:
    """Factory used by uvicorn at container start.

    Reads COMPANIES_ROOT and DB_PATH from env. Defaults match the
    in-container layout.
    """
    companies_root = Path(os.environ.get("COMPANIES_ROOT", "/app/companies"))
    db_path = Path(os.environ.get("DB_PATH", "/app/data/pipeline.db"))
    base_root = Path(os.environ.get("JSP_BASE", "/app"))
    return create_app(companies_root=companies_root, db_path=db_path, base_root=base_root)
