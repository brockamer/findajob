"""Regression fence for #771: IMAGE_ROOT must be computed from this file's
location, never from ``JSP_BASE`` — that's the load-bearing property the
docs route and every web-spawned subprocess depend on.

Runs via subprocess so the import happens with a freshly-set environment;
``importlib.reload(findajob.paths)`` would leave closures in other test
files (e.g. ``from findajob.paths import BASE``) pointing at the old module
state per ``feedback_reimport_invalidates_closures``.
"""

from __future__ import annotations

import subprocess
import sys


def test_image_root_diverges_from_base_when_jsp_base_set(tmp_path) -> None:
    """When JSP_BASE points at a volume-style state root (e.g. /app/state on
    Fly), IMAGE_ROOT must stay anchored at the repo / image root (derived from
    findajob.paths' file location) — that's how `/docs/{slug}` and every
    subprocess launched from a web route still find their image-bound files.
    """
    fake_state = tmp_path / "fake_state"
    fake_state.mkdir()

    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from findajob.paths import BASE, IMAGE_ROOT; print(BASE); print(IMAGE_ROOT)",
        ],
        env={"JSP_BASE": str(fake_state), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    base, image_root, _ = out.stdout.split("\n", 2)
    assert base == str(fake_state)
    assert image_root != base
    # IMAGE_ROOT comes from src/findajob/paths.py's location (.../<repo>/src/findajob/paths.py)
    # so it must end with the repo's own basename — never tmp_path.
    assert "/fake_state" not in image_root


def test_image_root_equals_base_when_jsp_base_unset() -> None:
    """Single-substrate deploys (compose default, dev VM) must keep
    IMAGE_ROOT == BASE — no behavior change for stacks that don't set
    JSP_BASE.
    """
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from findajob.paths import BASE, IMAGE_ROOT; print(BASE); print(IMAGE_ROOT)",
        ],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    base, image_root, _ = out.stdout.split("\n", 2)
    assert base == image_root
