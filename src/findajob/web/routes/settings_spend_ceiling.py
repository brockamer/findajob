"""GET + POST /settings/spend-ceiling/ — monthly LLM spend ceiling (#671).

Mirrors the /settings/active-sources/ pattern: GET renders current state,
POST validates and writes config/spend_ceiling.txt atomically.  Saves take
effect on the next request without a container restart (load_spend_ceiling
is no-cache by design).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from findajob.config_loader import load_spend_ceiling

router = APIRouter(prefix="/settings/spend-ceiling", tags=["settings"])

# ── recommendation constants ──────────────────────────────────────────────────
# Derived from cost_log analysis as of 2026-05.
# SCORING_FLOOR_USD: estimated monthly baseline (triage + scoring overhead).
# PER_PREP_USD:      estimated cost per full prep run (briefing + resume + cover).
SCORING_FLOOR_USD: float = 15.0
PER_PREP_USD: float = 0.25
_APPLIES_PER_WEEK_OPTIONS: tuple[int, ...] = (1, 3, 5, 10, 20)
_DEFAULT_APPLIES_PER_WEEK: int = 3


def _recommended_ceiling(applies_per_week: int) -> float:
    """Monthly ceiling recommendation given expected weekly applications."""
    return SCORING_FLOOR_USD + applies_per_week * 4.3 * PER_PREP_USD


def _spend_ceiling_path(base_root: Path) -> Path:
    """Resolve the canonical config/spend_ceiling.txt path.

    In production this matches _SPEND_CEILING_PATH from config_loader.
    In tests the base_root is a tmp_path so the route reads/writes an
    isolated file rather than the real config. We can't use _SPEND_CEILING_PATH
    directly because that's a module-level Path that tests monkeypatch on the
    config_loader module — we'd need an extra monkeypatch target on this module
    too. Instead, derive from base_root so tests that pass a tmp base_root
    automatically get isolation.
    """
    return base_root / "config" / "spend_ceiling.txt"


def _write_ceiling(path: Path, content: str) -> None:
    """Atomically write content to path via tmp + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(tmp_name, str(path))
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


@router.get("/", response_class=HTMLResponse)
def get_spend_ceiling(request: Request) -> HTMLResponse:
    """Render the spend-ceiling settings page with current value + recommendation."""
    current_ceiling = load_spend_ceiling()
    recommendations = {n: _recommended_ceiling(n) for n in _APPLIES_PER_WEEK_OPTIONS}
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="settings/spend_ceiling.html",
        context={
            "current_ceiling": current_ceiling,
            "applies_options": _APPLIES_PER_WEEK_OPTIONS,
            "default_applies": _DEFAULT_APPLIES_PER_WEEK,
            "recommendations": recommendations,
            "scoring_floor_usd": SCORING_FLOOR_USD,
            "per_prep_usd": PER_PREP_USD,
        },
    )


@router.post("/", response_class=HTMLResponse)
async def post_spend_ceiling(
    request: Request,
    action: str = Form(default="save"),
    ceiling_override: str = Form(default=""),
    applies_per_week: int = Form(default=_DEFAULT_APPLIES_PER_WEEK),
) -> Response:
    """Validate and persist the ceiling setting.

    Three actions:
      save      — write the numeric override (preferred) or the recommendation
      disable   — write "disabled" sentinel so load_spend_ceiling returns None
      recommend — POST from the recommendation form; same as save but uses
                  applies_per_week to compute the value
    """
    base = Path(request.app.state.base_root)
    path = _spend_ceiling_path(base)
    templates = request.app.state.templates

    def _render_result(outcome: str, message: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="settings/_spend_ceiling_save_result.html",
            context={"outcome": outcome, "message": message},
        )

    if action == "disable":
        try:
            _write_ceiling(path, "disabled\n")
        except OSError as e:
            return _render_result("error", f"Could not write file: {e}")
        return _render_result("success", "Spend ceiling disabled.")

    # Determine value: explicit override wins; else use recommendation.
    raw = ceiling_override.strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return _render_result("error", f"Invalid ceiling: {raw!r}. Enter a positive number (e.g. 50).")
        if value <= 0:
            return _render_result("error", "Ceiling must be greater than zero.")
    else:
        # No override supplied — compute from applies_per_week.
        if applies_per_week not in _APPLIES_PER_WEEK_OPTIONS:
            return _render_result("error", f"Invalid applies-per-week value: {applies_per_week!r}.")
        value = _recommended_ceiling(applies_per_week)

    try:
        _write_ceiling(path, f"{value:.2f}\n")
    except OSError as e:
        return _render_result("error", f"Could not write file: {e}")
    return _render_result("success", f"Ceiling set to ${value:.2f}/month.")
