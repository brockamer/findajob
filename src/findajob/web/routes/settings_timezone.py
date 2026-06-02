"""GET + POST /settings/timezone/ — change the operator's IANA timezone (#988).

Mirrors the /settings/spend-ceiling/ pattern: GET renders the currently active
``TZ`` plus any picked-but-pending zone; POST validates an IANA zone and writes
``data/timezone`` atomically via :func:`findajob.timeutil.write_timezone_file`.

The pick takes effect on the next app restart — identical semantics to the
onboarding pick and the dashboard restart-to-apply banner (#981). The write
itself is immediate and atomic; only the running process's ``TZ`` lags until a
restart re-reads ``data/timezone`` (see ``ops/entrypoint.sh``).
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import available_timezones

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from findajob.timeutil import (
    local_tz,
    pending_timezone,
    read_timezone_file,
    write_timezone_file,
)

router = APIRouter(prefix="/settings/timezone", tags=["settings"])


def _sorted_zones() -> list[str]:
    """All IANA zone names, sorted, for the picker. ``available_timezones`` reads
    the system tzdata; sorting gives a stable, scannable dropdown order."""
    return sorted(available_timezones())


@router.get("/", response_class=HTMLResponse)
def get_timezone_editor(request: Request) -> HTMLResponse:
    """Render the timezone editor: active TZ, picked zone, and the IANA picker."""
    base = Path(request.app.state.base_root)
    picked = read_timezone_file(base)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="settings/timezone.html",
        context={
            "active_tz": local_tz(),
            "picked_tz": picked,
            "pending_tz": pending_timezone(base),
            "zones": _sorted_zones(),
        },
    )


@router.post("/", response_class=HTMLResponse)
async def post_timezone_editor(
    request: Request,
    timezone: str = Form(default=""),
) -> HTMLResponse:
    """Validate and persist the submitted IANA zone.

    Invalid or blank input returns an error partial and writes nothing
    (:func:`write_timezone_file` raises before touching disk). A valid pick that
    differs from the running ``TZ`` surfaces the restart-to-apply affordance.
    """
    base = Path(request.app.state.base_root)
    templates = request.app.state.templates
    candidate = timezone.strip()

    def _render_result(outcome: str, message: str, pending: str | None = None) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="settings/_timezone_save_result.html",
            context={"outcome": outcome, "message": message, "pending_tz": pending},
        )

    try:
        write_timezone_file(base, candidate)
    except ValueError:
        return _render_result(
            "error",
            f"{candidate!r} isn't a valid timezone. Pick an IANA zone like America/Los_Angeles from the list.",
        )
    except OSError as e:
        return _render_result("error", f"Could not write timezone file: {e}")

    return _render_result("success", candidate, pending=pending_timezone(base))
