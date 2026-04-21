"""FastAPI app factory for the materials viewer."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from pathlib import Path

from fastapi import Depends, FastAPI  # noqa: F401 — Depends used in Task 6
from fastapi.templating import Jinja2Templates

from findajob.web import routes


def create_app(*, companies_root: Path, db_path: Path) -> FastAPI:
    app = FastAPI(title="findajob materials viewer", docs_url=None, redoc_url=None)

    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    app.state.companies_root = companies_root
    app.state.db_path = db_path
    app.state.templates = templates

    def get_db() -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides.setdefault(routes.get_db, get_db)
    app.include_router(routes.router)
    return app


def default_app() -> FastAPI:
    """Factory used by uvicorn at container start.

    Reads COMPANIES_ROOT and DB_PATH from env. Defaults match the
    in-container layout.
    """
    companies_root = Path(os.environ.get("COMPANIES_ROOT", "/app/companies"))
    db_path = Path(os.environ.get("DB_PATH", "/app/data/pipeline.db"))
    return create_app(companies_root=companies_root, db_path=db_path)
