"""#841: Core restore logic — tarball validation, extraction, atomic swap.

Restore flow:
1. Validate: tarball has ``state/`` top-level, contains ``data/pipeline.db``
   and ``data/.onboarding-complete``.
2. Extract to a staging directory under BASE with path-traversal protection.
3. Atomic swap: rename existing state dirs to a rollback dir, move staging
   dirs into place.
4. Fix permissions on secrets files.
5. Write the onboarding sentinel.

On failure mid-swap, the rollback dir allows recovery.
"""

from __future__ import annotations

import os
import shutil
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path

MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # 512 MB

_TARBALL_PREFIX = "state/"

_REQUIRED_ENTRIES = frozenset(
    {
        "state/data/pipeline.db",
        "state/data/.onboarding-complete",
    }
)

_STATE_DIRS = ("data", "config", "candidate_context", "companies", "logs")

_SECRETS_FILES = ("data/.env", "config/gmail.json")


@dataclass(frozen=True)
class RestoreResult:
    success: bool
    error: str | None = None


def validate_tarball(raw: bytes) -> str | None:
    """Check tarball structure without extracting. Returns error or None."""
    if len(raw) > MAX_UPLOAD_BYTES:
        return f"File too large ({len(raw) // (1024 * 1024)} MB). Maximum is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."

    import io

    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            names = set(tar.getnames())
    except (tarfile.TarError, EOFError, OSError):
        return "Not a valid gzipped tar archive."

    if not any(n.startswith(_TARBALL_PREFIX) or n == "state" for n in names):
        return "Tarball must have a top-level state/ directory."

    missing = []
    for req in _REQUIRED_ENTRIES:
        if req not in names:
            missing.append(req.removeprefix(_TARBALL_PREFIX))
    if missing:
        return f"Tarball is missing required files: {', '.join(sorted(missing))}"

    return None


def _is_path_safe(member: tarfile.TarInfo, extract_root: Path) -> bool:
    """Reject path-traversal attacks (../ sequences, absolute paths)."""
    target = (extract_root / member.name).resolve()
    return str(target).startswith(str(extract_root.resolve()))


def restore_from_tarball(raw: bytes, base: Path) -> RestoreResult:
    """Extract and atomically swap tarball contents into the state directory.

    Steps:
    1. Extract to staging dir with path-traversal protection.
    2. Swap existing dirs to rollback, move staged dirs in.
    3. Fix permissions on secrets files.
    """
    import io
    from datetime import UTC, datetime

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    staging = base / f".restore-staging-{ts}"
    rollback = base / f".restore-rollback-{ts}"

    try:
        staging.mkdir(parents=True, exist_ok=True)

        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            for member in tar.getmembers():
                if not member.name.startswith(_TARBALL_PREFIX) and member.name != "state":
                    continue

                stripped = member.name.removeprefix(_TARBALL_PREFIX)
                if not stripped:
                    continue

                if not _is_path_safe(
                    tarfile.TarInfo(name=stripped),
                    staging,
                ):
                    return RestoreResult(
                        success=False,
                        error=f"Unsafe path in tarball: {member.name}",
                    )

                member_copy = tarfile.TarInfo(name=stripped)
                member_copy.size = member.size
                member_copy.mode = member.mode
                member_copy.type = member.type
                member_copy.linkname = member.linkname
                member_copy.mtime = member.mtime

                if member.isdir():
                    (staging / stripped).mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    dest = staging / stripped
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    f = tar.extractfile(member)
                    if f is not None:
                        dest.write_bytes(f.read())
                elif member.issym():
                    dest = staging / stripped
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(member.linkname, dest)

        rollback.mkdir(parents=True, exist_ok=True)

        for dirname in _STATE_DIRS:
            staged = staging / dirname
            if not staged.exists():
                continue

            existing = base / dirname
            if existing.exists():
                rollback_dest = rollback / dirname
                existing.rename(rollback_dest)

            staged.rename(existing)

        for secret_rel in _SECRETS_FILES:
            secret_path = base / secret_rel
            if secret_path.is_file():
                secret_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

        db_path = base / "data" / "pipeline.db"
        if db_path.is_file():
            migrate_error = _run_schema_migration(db_path)
            if migrate_error:
                return RestoreResult(success=False, error=migrate_error)

        _cleanup(staging)
        _cleanup(rollback)

        return RestoreResult(success=True)

    except Exception as exc:
        _attempt_rollback(base, rollback)
        _cleanup(staging)
        return RestoreResult(success=False, error=str(exc))


def _run_schema_migration(db_path: Path) -> str | None:
    """Run pending schema migrations on the restored DB. Returns error or None."""
    from findajob.db import connect as db_connect
    from findajob.db.migrate import apply_pending

    conn = db_connect(db_path)
    try:
        apply_pending(conn)
        return None
    except Exception as exc:
        return (
            f"Schema migration failed after restore: {exc}. "
            "The backup may be from an incompatible older version. "
            "Pull a newer image and retry."
        )
    finally:
        conn.close()


def _attempt_rollback(base: Path, rollback: Path) -> None:
    """Best-effort reversal of a partial swap."""
    if not rollback.exists():
        return
    for dirname in _STATE_DIRS:
        rb = rollback / dirname
        if not rb.exists():
            continue
        target = base / dirname
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        try:
            rb.rename(target)
        except OSError:
            pass


def _cleanup(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
