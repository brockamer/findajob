"""Manifest unit tests (#816)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from findajob.migrate import manifest as mf


def test_manifest_roundtrips_json() -> None:
    """Manifest serializes to JSON and reads back equal."""
    m = mf.Manifest(
        schema_version=1,
        export_time_utc="2026-05-23T17:00:00Z",
        source_stack_tag="findajob-staging",
        findajob_version="0.27.10",
        db_row_counts={"jobs": 1234, "audit_log": 5678, "feedback_log": 42, "cost_log": 999},
        pipeline_db_sha256="a" * 64,
        companies_file_count=2500,
        companies_total_size_bytes=4_000_000,
        candidate_context_file_count=12,
    )
    s = m.to_json()
    parsed = json.loads(s)
    # Must be stable JSON; schema_version always present
    assert parsed["schema_version"] == 1
    assert parsed["source_stack_tag"] == "findajob-staging"
    back = mf.Manifest.from_json(s)
    assert back == m


def test_manifest_rejects_wrong_schema_version() -> None:
    payload = json.dumps({"schema_version": 99, "source_stack_tag": "x"})
    with pytest.raises(mf.ManifestVersionError):
        mf.Manifest.from_json(payload)


def test_compare_passes_when_post_import_matches() -> None:
    m = mf.Manifest(
        schema_version=1,
        export_time_utc="2026-05-23T17:00:00Z",
        source_stack_tag="findajob-staging",
        findajob_version="0.27.10",
        db_row_counts={"jobs": 100, "audit_log": 200},
        pipeline_db_sha256="b" * 64,
        companies_file_count=50,
        companies_total_size_bytes=1_000_000,
        candidate_context_file_count=3,
    )
    result = mf.compare(
        m,
        observed_db_row_counts={"jobs": 100, "audit_log": 200},
        observed_pipeline_db_sha256="b" * 64,
        observed_companies_file_count=50,
        observed_companies_total_size_bytes=1_000_000,
        observed_candidate_context_file_count=3,
    )
    assert result.ok
    assert result.failures == []


def test_compare_fails_on_row_count_mismatch() -> None:
    m = mf.Manifest(
        schema_version=1,
        export_time_utc="2026-05-23T17:00:00Z",
        source_stack_tag="findajob-staging",
        findajob_version="0.27.10",
        db_row_counts={"jobs": 100, "audit_log": 200},
        pipeline_db_sha256="c" * 64,
        companies_file_count=50,
        companies_total_size_bytes=1_000_000,
        candidate_context_file_count=3,
    )
    result = mf.compare(
        m,
        observed_db_row_counts={"jobs": 99, "audit_log": 200},  # one missing
        observed_pipeline_db_sha256="c" * 64,
        observed_companies_file_count=50,
        observed_companies_total_size_bytes=1_000_000,
        observed_candidate_context_file_count=3,
    )
    assert not result.ok
    assert any("jobs" in f and "99" in f and "100" in f for f in result.failures)


def test_compare_fails_on_pipeline_db_checksum_mismatch() -> None:
    m = mf.Manifest(
        schema_version=1,
        export_time_utc="2026-05-23T17:00:00Z",
        source_stack_tag="findajob-staging",
        findajob_version="0.27.10",
        db_row_counts={"jobs": 100},
        pipeline_db_sha256="d" * 64,
        companies_file_count=50,
        companies_total_size_bytes=1_000_000,
        candidate_context_file_count=3,
    )
    result = mf.compare(
        m,
        observed_db_row_counts={"jobs": 100},
        observed_pipeline_db_sha256="e" * 64,  # mismatch
        observed_companies_file_count=50,
        observed_companies_total_size_bytes=1_000_000,
        observed_candidate_context_file_count=3,
    )
    assert not result.ok
    assert any("pipeline_db_sha256" in f for f in result.failures)


def test_compare_tolerates_companies_size_within_one_percent() -> None:
    """AC: companies/ total size matches within 1%. File count must match exactly."""
    m = mf.Manifest(
        schema_version=1,
        export_time_utc="2026-05-23T17:00:00Z",
        source_stack_tag="findajob-staging",
        findajob_version="0.27.10",
        db_row_counts={"jobs": 1},
        pipeline_db_sha256="f" * 64,
        companies_file_count=100,
        companies_total_size_bytes=10_000_000,
        candidate_context_file_count=3,
    )
    # 0.5% smaller — within tolerance
    result = mf.compare(
        m,
        observed_db_row_counts={"jobs": 1},
        observed_pipeline_db_sha256="f" * 64,
        observed_companies_file_count=100,
        observed_companies_total_size_bytes=9_950_000,
        observed_candidate_context_file_count=3,
    )
    assert result.ok, result.failures


def test_compare_fails_when_companies_size_off_more_than_one_percent() -> None:
    m = mf.Manifest(
        schema_version=1,
        export_time_utc="2026-05-23T17:00:00Z",
        source_stack_tag="findajob-staging",
        findajob_version="0.27.10",
        db_row_counts={"jobs": 1},
        pipeline_db_sha256="f" * 64,
        companies_file_count=100,
        companies_total_size_bytes=10_000_000,
        candidate_context_file_count=3,
    )
    result = mf.compare(
        m,
        observed_db_row_counts={"jobs": 1},
        observed_pipeline_db_sha256="f" * 64,
        observed_companies_file_count=100,
        observed_companies_total_size_bytes=9_800_000,  # 2% off
        observed_candidate_context_file_count=3,
    )
    assert not result.ok
    assert any("companies_total_size_bytes" in f for f in result.failures)


def test_compare_fails_when_companies_file_count_mismatches_exactly() -> None:
    """File count is exact — no tolerance, unlike size."""
    m = mf.Manifest(
        schema_version=1,
        export_time_utc="2026-05-23T17:00:00Z",
        source_stack_tag="findajob-staging",
        findajob_version="0.27.10",
        db_row_counts={"jobs": 1},
        pipeline_db_sha256="f" * 64,
        companies_file_count=100,
        companies_total_size_bytes=10_000_000,
        candidate_context_file_count=3,
    )
    result = mf.compare(
        m,
        observed_db_row_counts={"jobs": 1},
        observed_pipeline_db_sha256="f" * 64,
        observed_companies_file_count=99,  # one off
        observed_companies_total_size_bytes=10_000_000,
        observed_candidate_context_file_count=3,
    )
    assert not result.ok
    assert any("companies_file_count" in f for f in result.failures)


def test_compare_fails_when_candidate_context_empty() -> None:
    """Sanity: candidate_context_file_count > 0 required."""
    m = mf.Manifest(
        schema_version=1,
        export_time_utc="2026-05-23T17:00:00Z",
        source_stack_tag="findajob-staging",
        findajob_version="0.27.10",
        db_row_counts={"jobs": 1},
        pipeline_db_sha256="f" * 64,
        companies_file_count=10,
        companies_total_size_bytes=10_000,
        candidate_context_file_count=0,  # source had 0 — that's a broken export
    )
    result = mf.compare(
        m,
        observed_db_row_counts={"jobs": 1},
        observed_pipeline_db_sha256="f" * 64,
        observed_companies_file_count=10,
        observed_companies_total_size_bytes=10_000,
        observed_candidate_context_file_count=0,
    )
    assert not result.ok
    assert any("candidate_context" in f for f in result.failures)


def test_write_and_read_path(tmp_path: Path) -> None:
    """Manifest writes/reads at an arbitrary path — exporter writes it
    into the tarball at top level as ``manifest.json``."""
    m = mf.Manifest(
        schema_version=1,
        export_time_utc="2026-05-23T17:00:00Z",
        source_stack_tag="findajob-staging",
        findajob_version="0.27.10",
        db_row_counts={"jobs": 1},
        pipeline_db_sha256="f" * 64,
        companies_file_count=1,
        companies_total_size_bytes=1,
        candidate_context_file_count=1,
    )
    mf_path = tmp_path / "manifest.json"
    mf.write_to_path(m, mf_path)
    assert mf_path.exists()
    back = mf.read_from_path(mf_path)
    assert back == m
