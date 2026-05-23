"""Post-extract verifier (#816).

Reads an extracted state tree's bundled ``manifest.json``, computes the
same metrics the exporter computed at export time, and asserts they
match. Pure Python — runs locally on the operator box (against a
tarball extracted into a tmpdir for spot-checks) and inside the Fly
container (against the live ``/app/state`` after import).

The Fly-side invocation is ``python -m findajob.migrate verify
--state-dir /app/state``, dispatched by ``__main__``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from findajob.migrate import exporter
from findajob.migrate import manifest as mf


@dataclass
class VerifyResult:
    manifest_path: Path
    failures: list[str] = field(default_factory=list)
    observed: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failures


def verify(*, state_dir: Path) -> VerifyResult:
    """Verify an extracted state tree against its bundled manifest.

    Raises :class:`FileNotFoundError` if ``manifest.json`` is missing.
    Returns a :class:`VerifyResult` whose ``failures`` is empty on
    success. ``observed`` is populated either way so the runbook can
    surface what was checked.
    """
    manifest_path = state_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.json not found at {manifest_path}")

    manifest = mf.read_from_path(manifest_path)

    db_path = state_dir / "data" / "pipeline.db"
    observed_row_counts = exporter.row_counts(db_path) if db_path.exists() else {}
    observed_sha = exporter.sha256_of_file(db_path) if db_path.exists() else ""
    companies_count, companies_size = exporter.count_and_size(state_dir / "companies")
    ctx_count, _ = exporter.count_and_size(state_dir / "candidate_context")

    cmp = mf.compare(
        manifest,
        observed_db_row_counts=observed_row_counts,
        observed_pipeline_db_sha256=observed_sha,
        observed_companies_file_count=companies_count,
        observed_companies_total_size_bytes=companies_size,
        observed_candidate_context_file_count=ctx_count,
    )

    return VerifyResult(
        manifest_path=manifest_path,
        failures=cmp.failures,
        observed={
            "db_row_counts": observed_row_counts,
            "pipeline_db_sha256": observed_sha,
            "companies_file_count": companies_count,
            "companies_total_size_bytes": companies_size,
            "candidate_context_file_count": ctx_count,
        },
    )
