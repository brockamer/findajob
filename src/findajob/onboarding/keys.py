"""This instance's API keys: one writer and one reader.

Onboarding Step 1 saves the keys to ``data/.env`` and to the process
environment. Nothing stores them in the database. Every reader goes through
:func:`current_keys`, so the web app sees what the scheduled pipeline sees
(the pipeline reloads ``data/.env`` through ``paths.load_env``).
"""

from __future__ import annotations

import os
from pathlib import Path

from findajob.env_file import set_vars
from findajob.onboarding.key_validation import validate_openrouter_format

ENV_RELPATH = "data/.env"
OPENROUTER = "OPENROUTER_API_KEY"
RAPIDAPI = "RAPIDAPI_KEY"
GEMINI = "GEMINI_API_KEY"


def save_keys(
    base_root: Path,
    *,
    openrouter_api_key: str,
    rapidapi_key: str = "",
    gemini_api_key: str = "",
) -> None:
    """Write the keys to ``data/.env`` (atomic, mode 0600), then to ``os.environ``.

    The OpenRouter key is required. A blank RapidAPI or Gemini value leaves the
    saved value as it is. Raises :exc:`ValueError` when the OpenRouter key is
    blank or a value cannot be stored as one env line; then neither the file
    nor the environment changes.
    """
    updates = {OPENROUTER: openrouter_api_key.strip()}
    if not updates[OPENROUTER]:
        raise ValueError("an OpenRouter key is required")
    for name, value in ((RAPIDAPI, rapidapi_key), (GEMINI, gemini_api_key)):
        if value.strip():
            updates[name] = value.strip()
    set_vars(base_root / ENV_RELPATH, updates)
    os.environ.update(updates)


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def current_keys() -> tuple[str, str, str]:
    """Return ``(openrouter, rapidapi, gemini)`` from the environment; ``""`` when unset.

    An OpenRouter value that is not a well-formed key counts as unset.
    ``data/.env.example`` ships ``OPENROUTER_API_KEY=your_key_here`` and the
    Docker install seeds ``data/.env`` from it, so a fresh instance can start
    with that placeholder in its environment.
    """
    openrouter = _env(OPENROUTER)
    if not validate_openrouter_format(openrouter)[0]:
        openrouter = ""
    return (openrouter, _env(RAPIDAPI), _env(GEMINI))
