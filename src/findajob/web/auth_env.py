"""Read / write FINDAJOB_AUTH_USER + FINDAJOB_AUTH_PASS in ``data/.env``.

Writes go through :func:`findajob.env_file.set_vars` (atomic, mode 0600),
then mirror into ``os.environ`` so in-process code sees the change
immediately.

Separated from :mod:`findajob.web.auth` because auth.py is middleware
(imported early, minimal deps); this module handles file I/O for the
onboarding and settings layers.
"""

from __future__ import annotations

import os
from pathlib import Path

from findajob.env_file import set_vars

_ENV_FILE = "data/.env"
_AUTH_KEYS = ("FINDAJOB_AUTH_USER", "FINDAJOB_AUTH_PASS")


def is_auth_configured(base_root: Path) -> bool:
    """True when both auth credentials are present (env vars OR data/.env)."""
    user = os.environ.get("FINDAJOB_AUTH_USER", "").strip()
    pw = os.environ.get("FINDAJOB_AUTH_PASS", "").strip()
    if user and pw:
        return True
    env_path = base_root / _ENV_FILE
    if not env_path.is_file():
        return False
    found: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        key = key.strip()
        if key in _AUTH_KEYS:
            found[key] = val.strip()
    return bool(found.get("FINDAJOB_AUTH_USER")) and bool(found.get("FINDAJOB_AUTH_PASS"))


def write_auth_credentials(base_root: Path, username: str, password: str) -> None:
    """Persist auth credentials to ``data/.env`` and ``os.environ``.

    Raises ValueError, before anything is written, if either value contains a
    line break (see :func:`findajob.env_file.set_vars`).
    """
    set_vars(base_root / _ENV_FILE, {"FINDAJOB_AUTH_USER": username, "FINDAJOB_AUTH_PASS": password})

    os.environ["FINDAJOB_AUTH_USER"] = username
    os.environ["FINDAJOB_AUTH_PASS"] = password
