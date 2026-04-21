"""Health check endpoint."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response

router = APIRouter()


@router.get("/healthz", response_class=Response)
def healthz(request: Request) -> Response:
    root: Path = request.app.state.companies_root
    if not root.is_dir():
        return Response(content="companies/ missing", status_code=503, media_type="text/plain")
    return Response(content="ok", status_code=200, media_type="text/plain")
