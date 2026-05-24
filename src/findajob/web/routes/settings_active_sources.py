"""#603: /settings/active-sources/ — operator UI for adapter selection.

Second occupant of the /settings/ namespace (after /settings/reject-reasons/).
Mirrors that pattern: GET renders the current state as editable rows, POST
validates and writes via `findajob.fetchers.adapters.registry._write_active_sources`,
returning an HTMX partial with success/error feedback.

Pre-#410.5 the orchestrator fired `fetch_greenhouse_jobs` /
`fetch_ashby_jobs` / `fetch_lever_jobs` / `fetch_gmail_jobs` unconditionally.
After #410.5 every adapter is registry-gated by `active_sources.txt`, and
existing deployments that predate #410.5 need to add the four formerly-unconditional adapters.
This UI lets each user self-serve that migration instead of operator
SSH'ing into every stack.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from findajob.fetchers.adapters import registry as _registry

router = APIRouter(prefix="/settings/active-sources", tags=["settings"])


def _build_rows() -> list[dict]:
    """Per-adapter row state for the template — checked + is_configured."""
    # Module-attr lookup (not import-time binding) so test monkeypatches on
    # `registry._active_sources_path` propagate to the route at call time.
    path = _registry._active_sources_path()
    if path.exists():
        active = set(_registry._read_active_sources(path))
    else:
        active = set(_registry._DEFAULT_ACTIVE_SOURCES)

    rows: list[dict] = []
    for cls in _registry.REGISTERED_ADAPTERS:
        try:
            configured = cls().is_configured()
        except Exception:
            # Defensive: a misbehaving is_configured() shouldn't 500 the
            # settings page. Surface as Not configured with a generic note.
            configured = False
        rows.append(
            {
                "name": cls.name,
                "display_name": cls.display_name,
                "checked": cls.name in active,
                "is_configured": configured,
            }
        )
    return rows


@router.get("/", response_class=HTMLResponse)
def get_active_sources_editor(request: Request) -> HTMLResponse:
    """Render the active-sources editor with current values + per-row badges."""
    rows = _build_rows()
    file_present = _registry._active_sources_path().exists()
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="settings/active_sources.html",
        context={"rows": rows, "file_present": file_present},
    )


@router.post("/", response_class=HTMLResponse)
async def post_active_sources_editor(request: Request) -> HTMLResponse:
    """Validate against REGISTERED_ADAPTERS and persist atomically."""
    form = await request.form()
    submitted_raw = form.getlist("adapter")
    submitted = [str(v) for v in submitted_raw if isinstance(v, str)]

    registered_names = {cls.name for cls in _registry.REGISTERED_ADAPTERS}
    unknown = [name for name in submitted if name not in registered_names]
    if unknown:
        return _render_save_result(
            request,
            "error",
            f"Unknown adapter name(s): {', '.join(unknown)}. Names must match an entry in REGISTERED_ADAPTERS.",
        )

    # Preserve registry order in the written file (deterministic + matches
    # the order operators see in the UI).
    ordered = [cls.name for cls in _registry.REGISTERED_ADAPTERS if cls.name in set(submitted)]
    try:
        _registry._write_active_sources(ordered)
    except OSError as e:
        return _render_save_result(request, "error", f"Could not write file: {e}")

    return _render_save_result(request, "success", "")


def _render_save_result(request: Request, outcome: str, message: str) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="settings/_active_sources_save_result.html",
        context={"outcome": outcome, "message": message},
    )
