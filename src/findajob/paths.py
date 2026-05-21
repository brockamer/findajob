"""
Central path and base-directory resolver for all pipeline scripts.

Two roots are exported because Fly's single-volume layout puts code and state
in different places:

- ``IMAGE_ROOT`` — directory containing scripts/, src/, docs/. Derived from
  ``__file__``, never from env. Equals ``/app`` in the Docker image, equals
  the repo root in editable installs on the dev VM.
- ``BASE`` — state root: data/, logs/, companies/, candidate_context/,
  config/, .backups/. Defaults to ``IMAGE_ROOT`` so single-substrate deploys
  (Docker, dev VM) keep working unchanged. On Fly, ``JSP_BASE=/app/state``
  overrides ``BASE`` to point at the single mounted volume; scripts/ and
  docs/ stay image-bound at ``/app/`` (= ``IMAGE_ROOT``).

Use ``IMAGE_ROOT`` for code-path resolution (subprocess script paths,
docs-route base, anything bundled in the image). Use ``BASE`` for
state-path resolution (DB, logs, generated artifacts, operator config).
Conflating the two is the root cause of #770 + #771.

The PANDOC binary path is read from ``config/paths.env``. Override the
default via ``config/paths.env`` if your binary lives elsewhere.

Usage::

    from findajob.paths import BASE, IMAGE_ROOT, PANDOC

Use ``sys.executable`` (not a PYTHON constant) for subprocess calls to
other pipeline scripts.

Containerized deploys:
    Docker compose sets ``JSP_BASE=/app`` (matches Dockerfile default), so
    ``BASE == IMAGE_ROOT == /app``. Fly sets ``JSP_BASE=/app/state`` so
    ``BASE = /app/state`` while ``IMAGE_ROOT = /app``. See
    ``docs/getting-started/install-docker.md`` and
    ``docs/operations/fly-deploy.md``.
"""

import os
import pathlib

# Image root: directory containing scripts/, src/, docs/. Computed from
# this file's location BEFORE any env override — load-bearing. Walks
# src/findajob/paths.py → findajob/ → src/ → repo (or /app) root.
IMAGE_ROOT: str = str(pathlib.Path(__file__).parent.parent.parent.resolve())

# State root: defaults to IMAGE_ROOT, override via JSP_BASE for split-substrate
# deploys (Fly's single volume).
BASE: str = IMAGE_ROOT
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
