"""Per-tenant target location reader (#372)."""

from __future__ import annotations

from pathlib import Path

from findajob.paths import BASE

_DEFAULT: list[str] = ["United States"]


def _path() -> Path:
    return Path(BASE) / "config" / "target_locations.txt"


def read_target_locations(path: Path | None = None) -> list[str]:
    """Return per-tenant target locations; fallback to ['United States'] if absent/empty."""
    target = path or _path()
    if not target.exists():
        return list(_DEFAULT)
    locs: list[str] = []
    for raw in target.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        locs.append(line)
    return locs if locs else list(_DEFAULT)
