"""Stack exporter — produces a single tarball of a stack's state (#816).

Operator-side preconditions (NOT enforced here, enforced by the
:func:`~findajob.migrate.wal.verify_wal_empty` check after checkpoint):

1. ``docker compose stop`` the source stack so nothing holds the SQLite
   WAL. The exporter will refuse to run if the WAL sidecar is non-empty
   after ``PRAGMA wal_checkpoint(TRUNCATE)``.
2. The state directory should be the bind-mount root of
   ``/opt/stacks/findajob-<handle>/state/`` — the same shape that ends up
   on Fly's ``/app/state`` volume.

The tarball layout mirrors the state directory:

    manifest.json
    data/...           (SQLite + sentinels + .env — secrets are NOT in the
                        tarball at the runtime layer; the operator hands
                        them off via ``fly secrets import`` per the
                        runbook, but the source-side ``.env`` is tarred
                        for backup completeness)
    companies/...
    candidate_context/...

Explicitly excluded: ``aichat_ng/`` (regenerable LLM chat state) and
``logs/`` (rebuildable from pipeline events; would inflate the tarball).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import tarfile
from dataclasses import dataclass
from pathlib import Path

from findajob.db import connect
from findajob.migrate import manifest as mf
from findajob.migrate import wal

INCLUDED_SUBDIRS = ("data", "companies", "candidate_context")
EXCLUDED_SUBDIRS = ("aichat_ng", "logs")

# Tables we track in the manifest. Other tables exist (notes_history,
# view_prefs, onboarding_sessions, ...) but these four are the
# operator-meaningful ones the AC names explicitly. The CHECKSUM of
# pipeline.db catches drift on anything else.
TRACKED_TABLES = ("jobs", "audit_log", "feedback_log", "cost_log")


@dataclass
class ExportResult:
    tarball_path: Path
    manifest: mf.Manifest
    dry_run: bool


def export(
    *,
    state_dir: Path,
    tarball_path: Path,
    source_stack_tag: str,
    dry_run: bool = False,
) -> ExportResult:
    """Export a stack's state to ``tarball_path``.

    Refuses if ``state_dir`` is missing, ``data/pipeline.db`` is missing,
    ``tarball_path`` already exists, or the WAL sidecar is non-empty
    after checkpoint. Returns an :class:`ExportResult` with the manifest
    that was bundled.

    ``dry_run=True`` does all the I/O reads (checkpoint, count, checksum)
    but does not write the tarball — useful for the AC's "dry-run on
    findajob-clean" smoke step.
    """
    if not state_dir.exists() or not state_dir.is_dir():
        raise FileNotFoundError(f"state directory not found: {state_dir}")
    db_path = state_dir / "data" / "pipeline.db"
    if not db_path.exists():
        raise FileNotFoundError(f"pipeline.db not found at expected path: {db_path}")
    if tarball_path.exists():
        raise FileExistsError(f"refusing to overwrite existing tarball: {tarball_path}")

    wal.checkpoint_and_verify(db_path)

    counts = row_counts(db_path)
    db_sha = sha256_of_file(db_path)
    companies_count, companies_size = count_and_size(state_dir / "companies")
    ctx_count, _ = count_and_size(state_dir / "candidate_context")

    manifest = mf.Manifest(
        schema_version=mf.SCHEMA_VERSION,
        export_time_utc=dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
        source_stack_tag=source_stack_tag,
        findajob_version=_findajob_version(),
        db_row_counts=counts,
        pipeline_db_sha256=db_sha,
        companies_file_count=companies_count,
        companies_total_size_bytes=companies_size,
        candidate_context_file_count=ctx_count,
    )

    if dry_run:
        return ExportResult(tarball_path=tarball_path, manifest=manifest, dry_run=True)

    tarball_path.parent.mkdir(parents=True, exist_ok=True)
    _write_tarball(state_dir, tarball_path, manifest)
    return ExportResult(tarball_path=tarball_path, manifest=manifest, dry_run=False)


def row_counts(db_path: Path) -> dict[str, int]:
    """Return per-table row counts for the tracked tables. Missing
    tables resolve to 0 so the function is forward-compatible with
    a future schema slim-down."""
    counts: dict[str, int] = {}
    conn = connect(db_path)
    try:
        existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in TRACKED_TABLES:
            if table in existing:
                row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                counts[table] = int(row[0])
            else:
                counts[table] = 0
    finally:
        conn.close()
    return counts


def sha256_of_file(path: Path) -> str:
    """SHA-256 of a file's bytes, in lowercase hex. Streams in 1 MiB
    chunks so it stays bounded for large pipeline.db files."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def count_and_size(root: Path) -> tuple[int, int]:
    """Count files (regular files only — not dirs) and sum their sizes
    under ``root``. Returns (0, 0) if root is missing."""
    if not root.exists():
        return (0, 0)
    count = 0
    size = 0
    for p in root.rglob("*"):
        if p.is_file():
            count += 1
            size += p.stat().st_size
    return (count, size)


def _write_tarball(state_dir: Path, tarball_path: Path, manifest: mf.Manifest) -> None:
    """Write the tarball with manifest.json + included subdirs.

    The tarball is gzipped (``.tar.gz``). Members are added with their
    path relative to ``state_dir`` so extraction into a fresh volume
    root recreates the same shape.
    """
    with tarfile.open(tarball_path, "w:gz") as tar:
        # manifest.json at top level
        manifest_text = manifest.to_json().encode("utf-8")
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_text)
        info.mtime = int(dt.datetime.now(dt.UTC).timestamp())
        tar.addfile(info, io.BytesIO(manifest_text))

        for sub in INCLUDED_SUBDIRS:
            src = state_dir / sub
            if not src.exists():
                continue
            tar.add(src, arcname=sub, recursive=True)


def _findajob_version() -> str:
    """Return findajob's current version from CHANGELOG.md, or
    ``"unknown"`` if not parseable. Informational — never load-bearing
    for the verification logic, so a missing/malformed CHANGELOG must
    not break exports."""
    try:
        from findajob.paths import BASE

        changelog = Path(BASE) / "CHANGELOG.md"
        if not changelog.exists():
            return "unknown"
        for line in changelog.read_text().splitlines():
            # Format: `## [0.27.10] - 2026-05-23`
            if line.startswith("## [") and "]" in line:
                return line.split("[", 1)[1].split("]", 1)[0]
    except Exception:
        return "unknown"
    return "unknown"
