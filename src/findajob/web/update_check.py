"""In-app 'update available' check (#1016).

Anonymous GitHub ``releases/latest`` lookup vs the running CHANGELOG version,
tuple-compared, cached in-memory ~daily, fail-open, never blocking render.
Refresh is driven by FastAPI ``BackgroundTasks`` from the dashboard route, so
the network call runs after the response is sent."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from findajob.version import findajob_version, is_newer

_RELEASES_URL = "https://api.github.com/repos/brockamer/findajob/releases/latest"
_CHANGELOG_URL = "https://github.com/brockamer/findajob/blob/main/CHANGELOG.md"
_HTTP_TIMEOUT_S = 4
_CACHE_TTL = timedelta(hours=24)

# Module-level cache. Single-process uvicorn; a restart re-fetches once (one
# anonymous GitHub call — the 60/hr/IP anon limit is never approached).
_cache: dict[str, object] = {"checked_at": None, "latest": None}


def _now() -> datetime:
    return datetime.now(UTC)


def fetch_latest_release() -> str | None:
    """GET the latest release tag from GitHub (anonymous). Returns the version
    with any leading ``v`` stripped (tags are ``v0.33.0``; CHANGELOG is
    ``0.33.0``), or ``None`` on any error/timeout — fail-open, never raises."""
    req = urllib.request.Request(  # noqa: S310 — fixed https URL
        _RELEASES_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "findajob-update-check",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    if not isinstance(data, dict):  # non-dict JSON (proxy/captive portal) → skip
        return None
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag:
        return None
    return tag.lstrip("v")


def get_cached_latest() -> str | None:
    """The last successfully-fetched latest version, or ``None`` if never fetched."""
    return _cache["latest"]  # type: ignore[return-value]


def _is_stale() -> bool:
    checked = _cache["checked_at"]
    if checked is None:
        return True
    return _now() - checked > _CACHE_TTL  # type: ignore[operator]


def refresh_cache() -> None:
    """Fetch + store the latest version. Always stamps ``checked_at`` (even on
    failure) so a GitHub outage doesn't re-trigger a fetch on every render.
    Fail-open: a failed fetch leaves the prior ``latest`` intact."""
    latest = fetch_latest_release()
    _cache["checked_at"] = _now()
    if latest is not None:
        _cache["latest"] = latest


def maybe_schedule_refresh(background_tasks) -> None:
    """Enqueue a post-response refresh when the cache is stale. Never blocks the
    current render — the fresh result surfaces on a later load."""
    if _is_stale():
        background_tasks.add_task(refresh_cache)


def detect_substrate() -> str:
    """``"fly"`` when running on Fly (``FLY_APP_NAME`` set), else ``"docker"``.
    Watchtower is not detectable in-container, so the Docker CTA mentions it
    generically and the 'Update now' button is explicit opt-in (#1017)."""
    return "fly" if os.environ.get("FLY_APP_NAME") else "docker"


@dataclass(frozen=True)
class UpdateBanner:
    current: str
    latest: str
    substrate: str  # "fly" | "docker"
    changelog_url: str
    watchtower_enabled: bool = False  # set in Phase 2 (#1017)


def update_banner_state(request, background_tasks) -> UpdateBanner | None:
    """View-model for the dashboard update banner, or ``None`` when nothing to
    show. Schedules a background refresh when the cache is stale, then decides
    from whatever is currently cached. Hidden when: no cached latest yet, not
    newer than the running version, or the operator dismissed this exact latest
    version (cookie ``update_banner_dismissed=<latest>`` — version-keyed so a
    later release re-surfaces)."""
    maybe_schedule_refresh(background_tasks)
    latest = get_cached_latest()
    if latest is None:
        return None
    current = findajob_version()
    if not is_newer(latest, current):
        return None
    if request.cookies.get("update_banner_dismissed") == latest:
        return None
    return UpdateBanner(
        current=current,
        latest=latest,
        substrate=detect_substrate(),
        changelog_url=_CHANGELOG_URL,
        watchtower_enabled=False,  # Phase 2 sets this in Task 10
    )
