"""Atomic temp+replace writer for the discoverer output pair.

Mirrors the pattern in `findajob.onboarding.injector` (atomic staging,
rollback on failure, rolling backup of pre-existing destinations).

Pure-ish module: stdlib only (os, json, shutil, tempfile, datetime,
pathlib). No FastAPI, no findajob imports.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

_MD_RELPATH = "candidate_context/discovered_companies.md"
_JSON_RELPATH = "candidate_context/discovered_companies.json"
_BACKUP_ROOT = ".backups"
# tempfile.mkstemp() defaults to 0o600 (owner-only). Outputs land in
# bind-mounted candidate_context/ and must be readable by the web server
# even when the writer ran as a different user (e.g., manual `docker exec`
# as root vs. the FastAPI process running as `lad`).
_OUTPUT_FILE_MODE = 0o644


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _backup_existing(base_root: Path, stamp: str) -> Path | None:
    """Copy any pre-existing output pair to `.backups/{stamp}/`.

    Returns the backup directory path if a backup was made, else None.
    """
    paths = [base_root / _MD_RELPATH, base_root / _JSON_RELPATH]
    if not any(p.is_file() for p in paths):
        return None
    dest_root = base_root / _BACKUP_ROOT / stamp
    dest_root.mkdir(parents=True, exist_ok=True)
    for src in paths:
        if not src.is_file():
            continue
        target = dest_root / src.relative_to(base_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    return dest_root


def commit_atomically(
    base_root: Path,
    markdown: str,
    json_payload: dict,
) -> Path:
    """Write the markdown + JSON sidecar atomically.

    Pre-existing files at the destinations are backed up to
    ``base_root/.backups/{utc_stamp}/`` before any write. Each file is
    staged via :func:`tempfile.mkstemp` in the destination directory then
    committed via :func:`os.replace`.

    On any staging failure, all temp files created by this run are
    cleaned up and the exception propagates. Pre-existing destination
    files are not modified by a staging failure.

    Returns the absolute path of the markdown file on success.
    """
    md_dest = base_root / _MD_RELPATH
    json_dest = base_root / _JSON_RELPATH
    md_dest.parent.mkdir(parents=True, exist_ok=True)

    stamp = _utc_stamp()
    _backup_existing(base_root, stamp)

    tempfiles: list[tuple[str, Path]] = []
    try:
        # Stage markdown
        fd, tmp_md = tempfile.mkstemp(prefix=md_dest.name + ".", suffix=".tmp", dir=str(md_dest.parent))
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(markdown)
        os.chmod(tmp_md, _OUTPUT_FILE_MODE)
        tempfiles.append((tmp_md, md_dest))

        # Stage JSON
        fd, tmp_json = tempfile.mkstemp(prefix=json_dest.name + ".", suffix=".tmp", dir=str(json_dest.parent))
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            json.dump(json_payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.chmod(tmp_json, _OUTPUT_FILE_MODE)
        tempfiles.append((tmp_json, json_dest))

        # Commit
        for tmp_name, dest in tempfiles:
            os.replace(tmp_name, dest)
        tempfiles = []
    except Exception:
        for tmp_name, _dest in tempfiles:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise

    return md_dest
