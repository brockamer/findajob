"""
Central path and base-directory resolver for all pipeline scripts.

BASE is derived from this file's location — works wherever the repo is cloned,
regardless of directory name or home folder. No hardcoded paths.

The PANDOC binary path is read from config/paths.env. Override the default via
config/paths.env if your binary lives elsewhere.

Usage:
    from findajob.paths import BASE, PANDOC

Use sys.executable (not a PYTHON constant) for subprocess calls to other pipeline scripts.

Containerized deploys:
    When running in the findajob Docker image, the app is installed at /app
    (not the repo's filesystem location). The compose file sets
    JSP_BASE=/app to pin BASE correctly. See docs/getting-started/install-docker.md
    for the container architecture.
"""

import os
import pathlib

# Repo root: src/findajob/paths.py → findajob/ → src/ → repo root
BASE: str = str(pathlib.Path(__file__).parent.parent.parent.resolve())

# Allow env-var override for non-standard install locations or testing
if "JSP_BASE" in os.environ:
    BASE = str(pathlib.Path(os.environ["JSP_BASE"]).resolve())

# Load binary paths from config/paths.env
_cfg: dict = {}
_penv = pathlib.Path(BASE) / "config" / "paths.env"
if _penv.exists():
    for _line in _penv.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            _cfg[_k.strip()] = os.path.expanduser(_v.strip().strip('"').strip("'"))

# Binary paths — defaults are Linux-appropriate.
# Override via config/paths.env if your install is non-standard.
PANDOC: str = _cfg.get("PANDOC", "/usr/bin/pandoc")


def load_env(path: str | None = None) -> dict[str, str]:
    """Load key=value pairs from a .env file into os.environ. Returns dict.

    Lives here because path/config resolution is the same domain — both
    derive from BASE and read from disk to populate runtime state.
    Default path is ``data/.env`` under BASE; override via the ``path``
    arg or set ``JSP_BASE`` to redirect. Missing file is a silent no-op
    so tests and partial-init environments don't crash.
    """
    if path is None:
        path = f"{BASE}/data/.env"
    env: dict[str, str] = {}
    try:
        with open(os.path.expanduser(path)) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    os.environ[key] = val
                    env[key] = val
    except FileNotFoundError:
        pass
    return env
