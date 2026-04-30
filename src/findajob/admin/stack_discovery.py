"""Glob /opt/stacks/findajob-*/state/ to enumerate operator-visible stacks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StackPath:
    """Filesystem locator for one findajob stack on docker.lan."""

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

    Returns an empty list when `stacks_root` is missing or empty.
    """
    if not stacks_root.is_dir():
        return []

    out: list[StackPath] = []
    for entry in sorted(stacks_root.iterdir()):
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
