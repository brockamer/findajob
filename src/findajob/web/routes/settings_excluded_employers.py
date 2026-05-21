"""#729: /settings/excluded-employers/ — rich editor for config/excluded_employers.yaml.

Surface for the deterministic employer-rejection list at
config/excluded_employers.yaml, consumed by findajob.scorer_prefilter
(Stage 1, after the title-reject branch). Before this route, operators
edited the file via /config/'s raw text editor with no validation
feedback — a regex typo would surface only at next pipeline run.

GET renders current `exact` + `regex` entries as two editable sections.
POST validates via findajob.config_loader.save_excluded_employers and
returns an HTMX partial with success/error feedback.

The /config/ raw editor remains available as the operator fallback
(config_files.EDITABLE_CATEGORIES still lists config/excluded_employers.yaml).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from findajob import config_loader
from findajob.config_loader import ConfigError, save_excluded_employers

router = APIRouter(prefix="/settings/excluded-employers", tags=["settings"])


def _load_raw_lists() -> tuple[list[str], list[str]]:
    """Read current exact + regex entries as ordered lists (UI shape).

    Distinct from load_excluded_employers() which returns the compiled
    `(frozenset, re.Pattern)` consumed by the scorer. The editor needs
    the operator's original ordering + casing preserved, so we parse the
    raw YAML directly.

    Returns ([], []) if file missing/empty. Malformed → raises ConfigError.

    Module-attr lookup (not import-time binding) for `_EXCLUDED_EMPLOYERS_PATH`
    + `_safe_load_yaml` so test monkeypatches on `config_loader._EXCLUDED_EMPLOYERS_PATH`
    propagate to the route at call time — mirrors the pattern in
    settings_active_sources.py.
    """
    data = config_loader._safe_load_yaml(config_loader._EXCLUDED_EMPLOYERS_PATH, "excluded_employers.yaml")
    if data is None:
        return ([], [])

    exact = data.get("exact", []) or []
    if not isinstance(exact, list):
        raise ConfigError(f"excluded_employers.yaml: 'exact' must be a list, got {type(exact).__name__}")
    exact_strs = [str(e) for e in exact if isinstance(e, str)]

    regex = data.get("regex", []) or []
    if not isinstance(regex, list):
        raise ConfigError(f"excluded_employers.yaml: 'regex' must be a list, got {type(regex).__name__}")
    regex_strs = [str(p) for p in regex if isinstance(p, str)]

    return (exact_strs, regex_strs)


@router.get("/", response_class=HTMLResponse)
def get_excluded_employers_editor(request: Request) -> HTMLResponse:
    """Render the excluded-employers editor with current values."""
    load_error: str | None = None
    try:
        exact, regex = _load_raw_lists()
    except ConfigError as e:
        # Surface malformed-file state rather than 500ing. Operator can
        # fix via /config/ raw editor if the structured form can't render.
        exact, regex = [], []
        load_error = str(e)

    exact_rows = [{"text": e} for e in exact]
    regex_rows = [{"text": p} for p in regex]

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="settings/excluded_employers.html",
        context={
            "exact_rows": exact_rows,
            "regex_rows": regex_rows,
            "load_error": load_error,
        },
    )


@router.post("/", response_class=HTMLResponse)
async def post_excluded_employers_editor(request: Request) -> HTMLResponse:
    """Validate and save submitted exact + regex lists."""
    form = await request.form()

    exact = _read_rows(form, "exact_count", "exact_")
    if exact is None:
        return _render_save_result(request, "error", "Malformed form payload")
    regex = _read_rows(form, "regex_count", "regex_")
    if regex is None:
        return _render_save_result(request, "error", "Malformed form payload")

    try:
        save_excluded_employers(tuple(exact), tuple(regex))
    except ConfigError as e:
        return _render_save_result(request, "error", str(e))

    return _render_save_result(request, "success", "")


def _read_rows(form, count_key: str, row_prefix: str) -> list[str] | None:
    """Pull `row_prefix{i}` entries from form according to `count_key`.

    Returns None on malformed count (caller surfaces as error). Empty/whitespace
    rows are dropped silently (operator clicked Add but didn't type).
    """
    raw_count = form.get(count_key, "0")
    if not isinstance(raw_count, str):
        return None
    try:
        count = int(raw_count)
    except ValueError:
        return None

    rows: list[str] = []
    for i in range(count):
        raw = form.get(f"{row_prefix}{i}", "")
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if not text:
            continue
        rows.append(text)
    return rows


def _render_save_result(request: Request, outcome: str, message: str) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="settings/_excluded_employers_save_result.html",
        context={"outcome": outcome, "message": message},
    )
