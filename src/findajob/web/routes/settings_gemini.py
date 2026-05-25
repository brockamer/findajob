"""GET + POST /settings/gemini/ — Gemini API key management (#870).

View, update, or remove the GEMINI_API_KEY used for interview-prep
podcast generation. The key lives in data/.env (same file as
OPENROUTER_API_KEY); reads/writes use the env-file merge helpers.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(prefix="/settings/gemini", tags=["settings"])

_ENV_FILE = "data/.env"


def _read_gemini_key(base_root: Path) -> str:
    """Read GEMINI_API_KEY from data/.env, or '' if absent."""
    env_path = base_root / _ENV_FILE
    if not env_path.is_file():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("GEMINI_API_KEY=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip()
    return ""


def _write_gemini_key(base_root: Path, key: str) -> None:
    """Write or remove GEMINI_API_KEY in data/.env atomically."""
    env_path = base_root / _ENV_FILE
    lines: list[str] = []
    found = False

    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("GEMINI_API_KEY=") or (stripped.startswith("# GEMINI_API_KEY=")):
                found = True
                if key:
                    lines.append(f"GEMINI_API_KEY={key}")
                else:
                    lines.append("# GEMINI_API_KEY=")
            else:
                lines.append(line)

    if not found and key:
        lines.append(f"GEMINI_API_KEY={key}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if key:
        os.environ["GEMINI_API_KEY"] = key
    else:
        os.environ.pop("GEMINI_API_KEY", None)


def _last4(value: str) -> str:
    if not value:
        return ""
    return value[-4:]


@router.get("/", response_class=HTMLResponse)
def settings_gemini(request: Request) -> HTMLResponse:
    base_root: Path = request.app.state.base_root
    current_key = _read_gemini_key(base_root)
    env_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    active_key = env_key or current_key

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="settings/gemini.html",
        context={
            "has_key": bool(active_key),
            "key_last4": _last4(active_key),
            "saved": request.query_params.get("saved") == "1",
            "removed": request.query_params.get("removed") == "1",
        },
    )


@router.post("/", response_class=RedirectResponse)
def settings_gemini_save(
    request: Request,
    gemini_api_key: str = Form(default=""),
    action: str = Form(default="save"),
) -> RedirectResponse:
    base_root: Path = request.app.state.base_root

    if action == "remove":
        _write_gemini_key(base_root, "")
        return RedirectResponse(url="/settings/gemini/?removed=1", status_code=303)

    key = gemini_api_key.strip()
    if key:
        _write_gemini_key(base_root, key)
        return RedirectResponse(url="/settings/gemini/?saved=1", status_code=303)

    return RedirectResponse(url="/settings/gemini/", status_code=303)
