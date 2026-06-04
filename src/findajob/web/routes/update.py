# src/findajob/web/routes/update.py
"""Dashboard 'Update now' button → delegate to Watchtower (#1017).

A single container cannot recreate itself; this proxies to the operator's
opt-in Watchtower HTTP API and redirects back to the dashboard with a result
flag the banner surfaces as a one-line confirmation."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from findajob.web import watchtower

router = APIRouter()


@router.post("/update/now")
def update_now() -> RedirectResponse:
    ok = watchtower.trigger_watchtower_update()
    flag = "update_triggered" if ok else "update_failed"
    return RedirectResponse(url=f"/board/dashboard?{flag}=1", status_code=303)
