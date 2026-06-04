"""Running-version lookup + dependency-free version comparison.

``findajob_version()`` reads the baked CHANGELOG (relocated from
``migrate/exporter.py`` so the web layer need not import the backup module).
``version_tuple()`` / ``is_newer()`` are pure, I/O-free, and back the
update-availability check (#1016)."""

from __future__ import annotations

from pathlib import Path


def findajob_version() -> str:
    """First SemVer-shaped ``## [N.N.N]`` heading in CHANGELOG.md; skips
    ``## [Unreleased]``. Returns ``"unknown"`` if missing/unparseable — never
    load-bearing, so a malformed CHANGELOG must not raise."""
    try:
        from findajob.paths import BASE

        changelog = Path(BASE) / "CHANGELOG.md"
        if not changelog.exists():
            return "unknown"
        for line in changelog.read_text().splitlines():
            if line.startswith("## [") and "]" in line:
                version = line.split("[", 1)[1].split("]", 1)[0]
                if version[:1].isdigit():
                    return version
    except Exception:
        return "unknown"
    return "unknown"


def version_tuple(v: str) -> tuple[int, ...] | None:
    """Parse a dotted numeric version (optional leading ``v``) to an int tuple.
    Returns ``None`` if any segment is non-numeric (``"unknown"``, a
    pre-release suffix) — callers treat ``None`` as "cannot compare"."""
    v = v.strip().lstrip("v")
    if not v:
        return None
    try:
        return tuple(int(p) for p in v.split("."))
    except ValueError:
        return None


def is_newer(latest: str, current: str) -> bool:
    """True iff ``latest`` is a strictly-greater version than ``current``.
    Fail-closed: any unparseable side → ``False`` (never nag on garbage).
    Pads to equal length so ``0.33`` and ``0.33.0`` compare equal."""
    lt = version_tuple(latest)
    ct = version_tuple(current)
    if lt is None or ct is None:
        return False
    n = max(len(lt), len(ct))
    lt = lt + (0,) * (n - len(lt))
    ct = ct + (0,) * (n - len(ct))
    return lt > ct
