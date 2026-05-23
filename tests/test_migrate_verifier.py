"""Verifier unit tests (#816).

The verifier reads an extracted state tree, computes observed metrics,
and compares against the bundled manifest.json. Pure Python — same code
runs locally on the operator box and inside the Fly container via
``python -m findajob.migrate verify --state-dir /app/state``.
"""

from __future__ import annotations

import sqlite3
import tarfile
from pathlib import Path

import pytest

from findajob.migrate import exporter, verifier
from findajob.migrate import manifest as mf


def _build_and_export(tmp_path: Path) -> tuple[Path, Path]:
    """Build a fake stack, export it, return (extract_dir, manifest_path)."""
    state = tmp_path / "state"
    (state / "data").mkdir(parents=True)
    (state / "companies" / "foo").mkdir(parents=True)
    (state / "candidate_context").mkdir(parents=True)

    db = state / "data" / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, fp TEXT)")
    conn.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE feedback_log (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE cost_log (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO jobs (fp) VALUES ('a'), ('b'), ('c')")
    conn.commit()
    conn.close()

    (state / "companies" / "foo" / "x.md").write_text("# x\n")
    (state / "candidate_context" / "profile.md").write_text("# persona\n")

    tarball = tmp_path / "stack.tar.gz"
    exporter.export(state_dir=state, tarball_path=tarball, source_stack_tag="findajob-test")

    extracted = tmp_path / "extracted"
    with tarfile.open(tarball, "r:gz") as tar:
        tar.extractall(extracted)
    return extracted, extracted / "manifest.json"


def test_verify_passes_on_clean_extract(tmp_path: Path) -> None:
    extracted, _ = _build_and_export(tmp_path)
    result = verifier.verify(state_dir=extracted)
    assert result.ok, result.failures


def test_verify_fails_when_manifest_missing(tmp_path: Path) -> None:
    state = tmp_path / "state"
    (state / "data").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        verifier.verify(state_dir=state)


def test_verify_fails_when_pipeline_db_tampered_post_extract(tmp_path: Path) -> None:
    extracted, _ = _build_and_export(tmp_path)
    # Tamper with the extracted DB — checksum should fail.
    conn = sqlite3.connect(extracted / "data" / "pipeline.db")
    conn.execute("INSERT INTO jobs (fp) VALUES ('tamper')")
    conn.commit()
    conn.close()
    result = verifier.verify(state_dir=extracted)
    assert not result.ok
    # Row count failure AND checksum failure — both ride on a single INSERT.
    assert any("pipeline_db_sha256" in f for f in result.failures)


def test_verify_fails_when_companies_file_removed_post_extract(tmp_path: Path) -> None:
    extracted, _ = _build_and_export(tmp_path)
    target = extracted / "companies" / "foo" / "x.md"
    target.unlink()
    result = verifier.verify(state_dir=extracted)
    assert not result.ok
    assert any("companies_file_count" in f for f in result.failures)


def test_verify_returns_observed_alongside_comparison(tmp_path: Path) -> None:
    """Verifier surfaces the observed numbers alongside pass/fail, so
    the runbook output can show what was checked, not just whether it
    passed."""
    extracted, _ = _build_and_export(tmp_path)
    result = verifier.verify(state_dir=extracted)
    assert result.observed["db_row_counts"]["jobs"] == 3
    assert result.observed["companies_file_count"] == 1


def test_verify_writes_manifest_path_to_result(tmp_path: Path) -> None:
    extracted, manifest_path = _build_and_export(tmp_path)
    result = verifier.verify(state_dir=extracted)
    assert result.manifest_path == manifest_path


def test_manifest_compare_picks_up_unexpected_extra_row_count(tmp_path: Path) -> None:
    """A second invariant: observed > expected is also a failure
    (someone wrote rows into the DB post-export but pre-verify)."""
    extracted, _ = _build_and_export(tmp_path)

    # Read the manifest and lower the jobs claim — simulate an
    # observed > expected drift without tampering with the DB file.
    m = mf.read_from_path(extracted / "manifest.json")
    bad = mf.Manifest(
        schema_version=m.schema_version,
        export_time_utc=m.export_time_utc,
        source_stack_tag=m.source_stack_tag,
        findajob_version=m.findajob_version,
        db_row_counts={"jobs": 1, "audit_log": 0, "feedback_log": 0, "cost_log": 0},  # claim 1
        pipeline_db_sha256=m.pipeline_db_sha256,
        companies_file_count=m.companies_file_count,
        companies_total_size_bytes=m.companies_total_size_bytes,
        candidate_context_file_count=m.candidate_context_file_count,
    )
    mf.write_to_path(bad, extracted / "manifest.json")
    result = verifier.verify(state_dir=extracted)
    assert not result.ok
    assert any("jobs" in f and "3" in f and "1" in f for f in result.failures)
