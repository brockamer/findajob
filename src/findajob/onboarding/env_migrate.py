"""Idempotent migration of legacy env-var names in data/.env (#408).

Runs at every app startup. Safe to call multiple times — it only
rewrites the file when there's a stale name to remove.
"""

from __future__ import annotations

from pathlib import Path

from findajob.utils import log_event


def migrate_rapidapi_key_env(env_path: Path) -> None:
    """Rename RAPIDAPI_KEY → JOBS_API14_KEY in data/.env.

    No-op if:
    - The file doesn't exist.
    - JOBS_API14_KEY is already set (we don't overwrite a tester's later edit).
    - RAPIDAPI_KEY isn't present.

    If both are set, JOBS_API14_KEY wins and RAPIDAPI_KEY is removed.
    """
    if not env_path.exists():
        return

    lines = env_path.read_text().splitlines(keepends=True)
    has_old = any(_is_assignment_for(line, "RAPIDAPI_KEY") for line in lines)
    has_new = any(_is_assignment_for(line, "JOBS_API14_KEY") for line in lines)

    if not has_old:
        return  # nothing to migrate

    new_lines: list[str] = []
    captured_value: str | None = None
    for line in lines:
        if _is_assignment_for(line, "RAPIDAPI_KEY"):
            if not has_new and captured_value is None:
                captured_value = _value_of(line)
            continue  # always drop the old line
        new_lines.append(line)

    if captured_value is not None:
        ending = "\n" if not new_lines or new_lines[-1].endswith("\n") else ""
        new_lines.append(f"JOBS_API14_KEY={captured_value}{ending}")

    env_path.write_text("".join(new_lines))
    log_event(
        "env_migrate_rapidapi_key",
        renamed=captured_value is not None,
        had_both=has_old and has_new,
    )


def _is_assignment_for(line: str, var: str) -> bool:
    stripped = line.lstrip()
    if stripped.startswith("#"):
        return False
    return stripped.startswith(f"{var}=")


def _value_of(line: str) -> str:
    stripped = line.lstrip().rstrip("\n").rstrip("\r")
    _, _, value = stripped.partition("=")
    return value
