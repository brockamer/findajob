"""
Central path and base-directory resolver for all pipeline scripts.

BASE is derived from this file's location — works wherever the repo is cloned,
regardless of directory name or home folder. No hardcoded paths.

Binary paths (AICHAT, PANDOC, RCLONE) are read from config/paths.env.
Override defaults via config/paths.env if your binaries live elsewhere.

Usage:
    from findajob.paths import BASE, AICHAT, PANDOC, RCLONE

Use sys.executable (not a PYTHON constant) for subprocess calls to other pipeline scripts.

Containerized deploys:
    When running in the findajob Docker image, the app is installed at /app
    (not the repo's filesystem location). The compose file sets
    JSP_BASE=/app to pin BASE correctly. See docs/setup/install-docker.md
    and docs/superpowers/specs/2026-04-17-docker-compose-design.md for
    the container architecture.
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
AICHAT: str = _cfg.get("AICHAT_NG", "/usr/local/bin/aichat-ng")
PANDOC: str = _cfg.get("PANDOC", "/usr/bin/pandoc")
RCLONE: str = _cfg.get("RCLONE", "/usr/bin/rclone")
