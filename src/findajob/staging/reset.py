"""Staging reset (#565).

Wipes staging bind-mount data/ and copies the persona fixture into place.
Operator stops + restarts the container around this call:

  docker compose stop <stack-name>
  docker exec -u 1000 <stack-name> python -m findajob.staging.reset
  docker compose start <stack-name>

Stop/start are operator-side; this module only does the file work. Skipping
the stop risks rmtree hitting EBUSY on open SQLite WAL/shm sidecars held
by the running uvicorn/supercronic processes — half-wiped data/ + traceback.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from findajob.paths import BASE

DEFAULT_FIXTURE = Path(__file__).parent / "persona_fixture"
DEFAULT_TARGET = Path(BASE)


def _wipe_contents(d: Path) -> None:
    """Wipe contents of ``d`` but leave ``d`` itself intact.

    Bind-mount safe: ``shutil.rmtree`` on a bind-mount root fails inside the
    container with ``PermissionError`` because you can't remove the mount
    point. This helper iterates children and removes each, leaving the
    directory entry alone (#610).
    """
    for child in d.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def reset_to_persona(fixture: Path, target: Path) -> None:
    """Wipe target/data/ contents and copy fixture/* into target/.

    Subdirs other than data/ are replaced only when the fixture supplies them
    (wipe contents → re-copy from fixture). Pre-existing target subdirs not
    present in the fixture survive. Callers must ensure the fixture is complete.

    Bind-mount safety (#610): wipes ``data/`` (and other subdirs the fixture
    supplies) by removing CONTENTS, not the directory itself — so this works
    when target subdirectories are Docker bind mounts.

    Raises FileNotFoundError if fixture missing,
    NotADirectoryError if target exists but is not a directory.
    """
    if not fixture.exists() or not fixture.is_dir():
        raise FileNotFoundError(f"Persona fixture not found at {fixture}")
    if target.exists() and not target.is_dir():
        raise NotADirectoryError(f"Target {target} exists and is not a directory")
    target.mkdir(parents=True, exist_ok=True)

    target_data = target / "data"
    if target_data.exists():
        _wipe_contents(target_data)

    for entry in fixture.iterdir():
        dst = target / entry.name
        if entry.is_dir():
            if dst.exists():
                _wipe_contents(dst)
            else:
                dst.mkdir(parents=True)
            for inner in entry.iterdir():
                inner_dst = dst / inner.name
                if inner.is_dir():
                    shutil.copytree(inner, inner_dst)
                else:
                    shutil.copy2(inner, inner_dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, dst)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Staging reset to persona fixture (#565)")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    args = parser.parse_args(argv)
    reset_to_persona(fixture=args.fixture, target=args.target)
    print(f"OK: reset {args.target} from {args.fixture}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
