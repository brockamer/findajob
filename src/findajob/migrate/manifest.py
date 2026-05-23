"""Migration manifest — the data contract between export and import (#816).

The manifest captures everything we need to assert that what was exported
is what was imported: per-table row counts on the SQLite DB, the SHA-256
of the post-checkpoint ``pipeline.db`` file, and aggregate file-count /
total-size figures for the ``companies/`` and ``candidate_context/``
trees. Lives inside the tarball at top level as ``manifest.json``.

Tolerance model (per the issue body's AC):

- DB row counts: **exact match** required. Off-by-one is a corruption.
- ``pipeline_db_sha256``: **exact match** required. The file is opaque
  bytes; either it round-tripped intact or it didn't.
- ``companies_total_size_bytes``: **within 1%** of source. Filesystem
  block sizes can differ across hosts (ext4 vs Fly's volume FS), so a
  tarred-and-extracted tree can shift slightly. 1% is generous enough
  to absorb that without hiding real loss.
- ``companies_file_count``: **exact match** required. File count is FS-
  independent and should never drift.
- ``candidate_context_file_count``: **exact match** AND ``> 0`` required.
  Empty candidate_context is always a broken export — the dir holds the
  persona profile, role archetypes, and interview artifacts that the
  scorer/prep pipeline can't run without.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1


class ManifestVersionError(ValueError):
    """Raised when a manifest's schema_version doesn't match what we know."""


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    export_time_utc: str
    source_stack_tag: str
    findajob_version: str
    db_row_counts: dict[str, int]
    pipeline_db_sha256: str
    companies_file_count: int
    companies_total_size_bytes: int
    candidate_context_file_count: int

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> Manifest:
        d = json.loads(s)
        version = d.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ManifestVersionError(f"manifest schema_version={version}, expected {SCHEMA_VERSION}")
        return cls(**d)


@dataclass
class ComparisonResult:
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


# Tolerance for companies_total_size_bytes — see module docstring.
SIZE_TOLERANCE_FRACTION = 0.01


def compare(
    manifest: Manifest,
    *,
    observed_db_row_counts: dict[str, int],
    observed_pipeline_db_sha256: str,
    observed_companies_file_count: int,
    observed_companies_total_size_bytes: int,
    observed_candidate_context_file_count: int,
) -> ComparisonResult:
    """Compare a manifest's claims against what we observe post-import.

    Returns a :class:`ComparisonResult` whose ``failures`` list is empty on
    success. Each failure is a single human-readable string suitable for
    surfacing directly to the operator.
    """
    result = ComparisonResult()

    # DB row counts — exact, per table claimed by the manifest.
    for table, expected in manifest.db_row_counts.items():
        observed = observed_db_row_counts.get(table)
        if observed is None:
            result.failures.append(f"db_row_counts[{table}]: expected {expected}, table missing in observed")
        elif observed != expected:
            result.failures.append(f"db_row_counts[{table}]: expected {expected}, observed {observed}")

    if observed_pipeline_db_sha256 != manifest.pipeline_db_sha256:
        result.failures.append(
            f"pipeline_db_sha256: expected {manifest.pipeline_db_sha256}, observed {observed_pipeline_db_sha256}"
        )

    if observed_companies_file_count != manifest.companies_file_count:
        result.failures.append(
            f"companies_file_count: expected {manifest.companies_file_count}, observed {observed_companies_file_count}"
        )

    size_delta = abs(observed_companies_total_size_bytes - manifest.companies_total_size_bytes)
    tolerance = manifest.companies_total_size_bytes * SIZE_TOLERANCE_FRACTION
    if size_delta > tolerance:
        result.failures.append(
            f"companies_total_size_bytes: expected {manifest.companies_total_size_bytes} "
            f"(±1%), observed {observed_companies_total_size_bytes}, "
            f"delta={size_delta} exceeds tolerance={int(tolerance)}"
        )

    if observed_candidate_context_file_count != manifest.candidate_context_file_count:
        result.failures.append(
            f"candidate_context_file_count: expected {manifest.candidate_context_file_count}, "
            f"observed {observed_candidate_context_file_count}"
        )
    if observed_candidate_context_file_count == 0:
        result.failures.append("candidate_context_file_count: 0 — empty candidate_context is never a valid export")

    return result


def write_to_path(manifest: Manifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.to_json())


def read_from_path(path: Path) -> Manifest:
    return Manifest.from_json(path.read_text())
