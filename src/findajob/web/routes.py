"""Route handlers for the materials viewer."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Request, Response

router = APIRouter()


def get_db() -> sqlite3.Connection:  # pragma: no cover — overridden in app factory
    raise NotImplementedError("DB dependency must be overridden by create_app()")


@router.get("/healthz", response_class=Response)
def healthz(request: Request) -> Response:
    root: Path = request.app.state.companies_root
    if not root.is_dir():
        return Response(content="companies/ missing", status_code=503, media_type="text/plain")
    return Response(content="ok", status_code=200, media_type="text/plain")
