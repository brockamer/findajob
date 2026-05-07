"""#490: /settings/reject-reasons/ — rich editor for config/reject_reasons.yaml.

The first occupant of the /settings/ namespace. /settings/ is the home for
domain-aware config editors (vs. /config/'s raw text editor); future similar
editors (e.g., for prefilter_rules.yaml) live next to this one.

GET renders the current reasons + title_signal flags as editable rows.
POST validates and writes via findajob.config_loader.save_reject_reasons,
returning an HTMX partial with success/error feedback.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from findajob.config_loader import ConfigError, load_reject_reasons, save_reject_reasons

router = APIRouter(prefix="/settings/reject-reasons", tags=["settings"])


@router.get("/", response_class=HTMLResponse)
def get_reject_reasons_editor(request: Request) -> HTMLResponse:
    """Render the reject-reasons editor with current values."""
    reasons, title_signal = load_reject_reasons()
    rows = [{"text": r, "title_signal": r in title_signal} for r in reasons]

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="settings/reject_reasons.html",
        context={"rows": rows},
    )


@router.post("/", response_class=HTMLResponse)
async def post_reject_reasons_editor(request: Request) -> HTMLResponse:
    """Validate and save submitted reasons + title_signal flags."""
    form = await request.form()

    raw_count = form.get("row_count", "0")
    if not isinstance(raw_count, str):
        return _render_save_result(request, "error", "Malformed form payload")
    try:
        row_count = int(raw_count)
    except ValueError:
        return _render_save_result(request, "error", "Malformed form payload")

    reasons: list[str] = []
    title_signal: set[str] = set()
    for i in range(row_count):
        raw = form.get(f"reason_{i}", "")
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if not text:
            continue  # skip empty rows
        reasons.append(text)
        if form.get(f"title_signal_{i}"):
            title_signal.add(text)

    try:
        save_reject_reasons(tuple(reasons), frozenset(title_signal))
    except ConfigError as e:
        return _render_save_result(request, "error", str(e))

    return _render_save_result(request, "success", "")


def _render_save_result(request: Request, outcome: str, message: str) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="settings/_reject_reasons_save_result.html",
        context={"outcome": outcome, "message": message},
    )
