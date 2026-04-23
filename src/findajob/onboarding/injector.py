"""Onboarding injector (#148).

Turns a parsed emission into seven files on disk plus a derived
``companies_of_interest.txt``, with backup-then-overwrite and a
sentinel file that gates the NUX redirect.

All writes are atomic: every tempfile is staged first, then
``os.replace`` commits them in order. Any staging failure rolls back
cleanly — zero mutations to existing files, no partial backup residue.

Pure module: imports ``os``, ``re``, ``shutil``, ``tempfile``,
``datetime``, ``pathlib``. No FastAPI import.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from findajob.onboarding.parser import ALLOWED_FILENAMES

# Maps emission filename -> destination relative path (relative to base_root).
_ALL_DESTINATIONS: dict[str, str] = {
    "profile.md": "candidate_context/profile.md",
    "master_resume.md": "candidate_context/master_resume.md",
    "target_companies.md": "config/target_companies.md",
    "business_sector_employers_reference.md": "config/business_sector_employers_reference.md",
    "jsearch_queries.txt": "config/jsearch_queries.txt",
    "prefilter_rules.yaml": "config/prefilter_rules.yaml",
    "in_domain_patterns.yaml": "config/in_domain_patterns.yaml",
}

_COMPANIES_OF_INTEREST_DEST = "config/companies_of_interest.txt"
_SENTINEL_RELPATH = "data/.onboarding-complete"
_BACKUP_ROOT = ".backups"

_TIER1_HEADING_RE = re.compile(r"^##\s+tier\s*1\b[^\n]*", re.IGNORECASE | re.MULTILINE)
_NEXT_H2_RE = re.compile(r"^##\s+\S", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*(?:[-*]\s+|\d+\.\s+)(.*)")
_SPLIT_COMMENTARY_RE = re.compile(r"\s+[—-]\s+|\s+\(")


def is_complete(base_root: Path) -> bool:
    """True iff the sentinel file exists under ``base_root``."""
    return (base_root / _SENTINEL_RELPATH).is_file()


def mark_complete(base_root: Path) -> None:
    """Write the sentinel file with the current UTC timestamp."""
    sentinel = base_root / _SENTINEL_RELPATH
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    sentinel.write_text(ts + "\n", encoding="utf-8")


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _backup_relpaths() -> list[str]:
    paths = list(_ALL_DESTINATIONS.values())
    paths.append(_COMPANIES_OF_INTEREST_DEST)
    paths.append(_SENTINEL_RELPATH)
    return paths


def backup_existing(base_root: Path, stamp: str) -> Path:
    """Copy any existing destinations to ``{base_root}/.backups/{stamp}/``.

    Returns the backup directory path (possibly empty). Preserves the
    relative path structure of every copied file.
    """
    dest_root = base_root / _BACKUP_ROOT / stamp
    dest_root.mkdir(parents=True, exist_ok=True)
    for relpath in _backup_relpaths():
        src = base_root / relpath
        if not src.is_file():
            continue
        target = dest_root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    return dest_root


def derive_companies_of_interest(target_companies_md: str) -> str:
    """Extract Tier 1 company names from ``target_companies.md``.

    Returns one company per line, trailing newline. Empty string if no
    ``## Tier 1`` section is present.
    """
    match = _TIER1_HEADING_RE.search(target_companies_md)
    if not match:
        return ""
    section_start = match.end()
    remainder = target_companies_md[section_start:]
    next_h2 = _NEXT_H2_RE.search(remainder)
    section = remainder[: next_h2.start()] if next_h2 else remainder
    companies: list[str] = []
    for line in section.splitlines():
        bullet = _BULLET_RE.match(line)
        if not bullet:
            continue
        raw = bullet.group(1).strip()
        # Strip trailing commentary (everything from the first " — " or " - " or " (")
        parts = _SPLIT_COMMENTARY_RE.split(raw, maxsplit=1)
        name = parts[0].strip()
        if name:
            companies.append(name)
    if not companies:
        return ""
    return "\n".join(companies) + "\n"


def inject(base_root: Path, found: dict[str, str]) -> Path:
    """Backup, stage, commit. Returns the (possibly empty) backup dir.

    ``found`` must contain every filename in :data:`ALLOWED_FILENAMES`;
    otherwise raises :class:`ValueError` without touching disk.

    On any staging or commit error, all tempfiles and the backup dir
    created this run are removed, and the exception propagates.
    """
    missing = [n for n in ALLOWED_FILENAMES if n not in found]
    if missing:
        raise ValueError(f"inject(): parsed emission is missing: {missing}")

    # Ensure target directories exist
    for relpath in list(_ALL_DESTINATIONS.values()) + [_COMPANIES_OF_INTEREST_DEST]:
        (base_root / relpath).parent.mkdir(parents=True, exist_ok=True)
    (base_root / _SENTINEL_RELPATH).parent.mkdir(parents=True, exist_ok=True)

    stamp = _utc_stamp()
    backup_dir = backup_existing(base_root, stamp)

    tempfiles: list[tuple[str, Path]] = []  # (tmp_name, final_dest)
    try:
        # Stage the seven parsed files
        for name in ALLOWED_FILENAMES:
            dest = base_root / _ALL_DESTINATIONS[name]
            fd, tmp_name = tempfile.mkstemp(prefix=dest.name + ".", suffix=".tmp", dir=str(dest.parent))
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(found[name])
            tempfiles.append((tmp_name, dest))

        # Stage the derived companies_of_interest.txt
        coi_body = derive_companies_of_interest(found["target_companies.md"])
        coi_dest = base_root / _COMPANIES_OF_INTEREST_DEST
        fd, tmp_name = tempfile.mkstemp(prefix=coi_dest.name + ".", suffix=".tmp", dir=str(coi_dest.parent))
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(coi_body)
        tempfiles.append((tmp_name, coi_dest))

        # Commit: os.replace every staged tempfile into place
        for tmp_name, dest in tempfiles:
            os.replace(tmp_name, dest)
        tempfiles = []  # all committed

        # Finally, the sentinel
        mark_complete(base_root)
    except Exception:
        # Roll back: delete any remaining tempfiles + the backup dir created this run
        for tmp_name, _dest in tempfiles:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise

    return backup_dir
