"""Read / write FINDAJOB_AUTH_USER + FINDAJOB_AUTH_PASS in ``data/.env``.

Follows the same read-modify-write pattern as
:mod:`findajob.web.routes.settings_gemini` — parse existing lines, replace
or append, write back, then mirror into ``os.environ`` so in-process code
sees the change immediately.

Separated from :mod:`findajob.web.auth` because auth.py is middleware
(imported early, minimal deps); this module handles file I/O for the
onboarding and settings layers.
"""

from __future__ import annotations

import os
from pathlib import Path

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
    """Persist auth credentials to ``data/.env`` and ``os.environ``."""
    env_path = base_root / _ENV_FILE
    lines: list[str] = []
    found_keys: set[str] = set()

    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            key_name = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
            if key_name in _AUTH_KEYS or (stripped.startswith("#") and any(k in stripped for k in _AUTH_KEYS)):
                if key_name == "FINDAJOB_AUTH_USER" or "FINDAJOB_AUTH_USER" in stripped:
                    lines.append(f"FINDAJOB_AUTH_USER={username}")
                    found_keys.add("FINDAJOB_AUTH_USER")
                elif key_name == "FINDAJOB_AUTH_PASS" or "FINDAJOB_AUTH_PASS" in stripped:
                    lines.append(f"FINDAJOB_AUTH_PASS={password}")
                    found_keys.add("FINDAJOB_AUTH_PASS")
            else:
                lines.append(line)

    if "FINDAJOB_AUTH_USER" not in found_keys:
        lines.append(f"FINDAJOB_AUTH_USER={username}")
    if "FINDAJOB_AUTH_PASS" not in found_keys:
        lines.append(f"FINDAJOB_AUTH_PASS={password}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    os.environ["FINDAJOB_AUTH_USER"] = username
    os.environ["FINDAJOB_AUTH_PASS"] = password
