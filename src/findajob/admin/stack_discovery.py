"""Glob /opt/stacks/findajob-*/state/ to enumerate operator-visible stacks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StackPath:
    """Filesystem locator for one findajob stack on the deployment host."""

    handle: str
    root: Path
    db_path: Path
    jsonl_path: Path


def discover_stacks(stacks_root: Path) -> list[StackPath]:
    """Return a sorted list of `StackPath` for every `findajob-*/state/`
    directory under `stacks_root`.

    Skips siblings that don't match the prefix (e.g. `dozzle`,
    `archivebox`). Skips `findajob-*` directories without a `state/`
    subdir (mid-onboarding or broken installs).

    Returns an empty list when `stacks_root` is missing, empty, or
    unreadable.

    Path semantics:
      - `Path.is_dir()` follows symlinks. A symlinked stack directory
        (e.g. `findajob-alice` → `/srv/stacks/alice`) is enumerated as
        its target. Broken symlinks return False from `is_dir()` and
        are silently skipped.
      - A `PermissionError` on `iterdir()` (operator container reading
        a stacks_root with restrictive parent perms, or a foreign-uid
        host bind-mount) is caught and logged: the dashboard renders
        with no stacks rather than 500. This matches the upstream-of-
        gather() position — `StackHealth.error` only catches per-stack
        failures, so the discovery layer must handle root-level OS
        errors itself or the whole page goes blank.
    """
    if not stacks_root.is_dir():
        return []

    try:
        entries = sorted(stacks_root.iterdir())
    except OSError as e:
        logger.warning("admin_stacks.discover_stacks: cannot list %s: %s", stacks_root, e)
        return []

    out: list[StackPath] = []
    for entry in entries:
        if not entry.is_dir():
            continue
        if not entry.name.startswith("findajob-"):
            continue
        state = entry / "state"
        if not state.is_dir():
            continue
        handle = entry.name[len("findajob-") :]
        out.append(
            StackPath(
                handle=handle,
                root=entry,
                db_path=state / "data" / "pipeline.db",
                jsonl_path=state / "logs" / "pipeline.jsonl",
            )
        )
    return out
