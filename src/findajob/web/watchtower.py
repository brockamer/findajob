# src/findajob/web/watchtower.py
"""Opt-in Watchtower HTTP-API trigger for the dashboard 'Update now' button (#1017).

Enabled only when both ``FINDAJOB_WATCHTOWER_HTTP_URL`` and
``FINDAJOB_WATCHTOWER_HTTP_TOKEN`` are set. Watchtower runs OUTSIDE this
container and pulls + recreates the findajob image — a single container cannot
recreate itself, so the button delegates to Watchtower's documented HTTP API
scoped to the findajob image only."""

from __future__ import annotations

import os
import urllib.error
import urllib.request

_IMAGE = "ghcr.io/brockamer/findajob"
_HTTP_TIMEOUT_S = 10


def _config() -> tuple[str, str] | None:
    url = os.environ.get("FINDAJOB_WATCHTOWER_HTTP_URL", "").strip()
    token = os.environ.get("FINDAJOB_WATCHTOWER_HTTP_TOKEN", "").strip()
    if url and token:
        return url, token
    return None


def watchtower_button_enabled() -> bool:
    """True when both the Watchtower HTTP URL and token are configured."""
    return _config() is not None


def trigger_watchtower_update() -> bool:
    """POST to Watchtower's ``/v1/update`` scoped to the findajob image. Returns
    True on a 2xx response, False on any error/misconfig — never raises."""
    cfg = _config()
    if cfg is None:
        return False
    base, token = cfg
    url = f"{base.rstrip('/')}/v1/update?image={_IMAGE}"
    req = urllib.request.Request(  # noqa: S310 — operator-configured Watchtower URL
        url,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "findajob-update"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:  # noqa: S310
            return 200 <= resp.status < 300
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return False
