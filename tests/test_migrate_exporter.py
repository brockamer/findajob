"""Exporter unit tests (#816)."""

from __future__ import annotations

import sqlite3
import tarfile
from pathlib import Path

import pytest

from findajob.migrate import exporter
from findajob.migrate import manifest as mf


def _build_fake_stack(state_root: Path, jobs_rows: int = 3, audit_rows: int = 5) -> None:
    """Build a state/ tree that looks like a real stack:

    state/
      data/pipeline.db   (WAL-mode, with `jobs` + `audit_log` tables)
      data/.onboarding-complete
      data/.env
      companies/foo-corp/briefing.md
      companies/_applied/bar-co/cover.md
      candidate_context/profile.md
      aichat_ng/sessions/x.json   <- skipped
      logs/pipeline.jsonl         <- skipped
    """
    (state_root / "data").mkdir(parents=True)
    (state_root / "companies" / "foo-corp").mkdir(parents=True)
    (state_root / "companies" / "_applied" / "bar-co").mkdir(parents=True)
    (state_root / "candidate_context").mkdir(parents=True)
    (state_root / "aichat_ng" / "sessions").mkdir(parents=True)
    (state_root / "logs").mkdir(parents=True)

    # Build a small SQLite DB in WAL mode with the tables the manifest cares about.
    db = state_root / "data" / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, fingerprint TEXT)")
    conn.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, msg TEXT)")
    conn.execute("CREATE TABLE feedback_log (id INTEGER PRIMARY KEY, reason TEXT)")
    conn.execute("CREATE TABLE cost_log (id INTEGER PRIMARY KEY, cost_usd REAL)")
    for i in range(jobs_rows):
        conn.execute("INSERT INTO jobs (fingerprint) VALUES (?)", (f"fp{i}",))
    for i in range(audit_rows):
        conn.execute("INSERT INTO audit_log (msg) VALUES (?)", (f"row{i}",))
    conn.commit()
    conn.close()

    (state_root / "data" / ".onboarding-complete").write_text("")
    (state_root / "data" / ".env").write_text("OPENROUTER_API_KEY=fake\n")
    (state_root / "companies" / "foo-corp" / "briefing.md").write_text("# foo briefing\n")
    (state_root / "companies" / "_applied" / "bar-co" / "cover.md").write_text("# bar cover\n")
    (state_root / "candidate_context" / "profile.md").write_text("# persona\n")
    (state_root / "aichat_ng" / "sessions" / "x.json").write_text("{}")
    (state_root / "logs" / "pipeline.jsonl").write_text('{"event":"x"}\n')


def test_export_produces_tarball_with_manifest(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = tmp_path / "out" / "stack.tar.gz"
    _build_fake_stack(state)

    result = exporter.export(state_dir=state, tarball_path=out, source_stack_tag="findajob-staging")

    assert out.exists()
    assert result.tarball_path == out
    assert result.manifest.source_stack_tag == "findajob-staging"
    assert result.manifest.db_row_counts["jobs"] == 3
    assert result.manifest.db_row_counts["audit_log"] == 5
    assert result.manifest.db_row_counts["feedback_log"] == 0
    assert result.manifest.db_row_counts["cost_log"] == 0
    assert result.manifest.candidate_context_file_count == 1
    # companies/foo-corp/briefing.md + companies/_applied/bar-co/cover.md = 2
    assert result.manifest.companies_file_count == 2


def test_tarball_skips_aichat_ng_and_logs(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = tmp_path / "stack.tar.gz"
    _build_fake_stack(state)

    exporter.export(state_dir=state, tarball_path=out, source_stack_tag="findajob-staging")

    with tarfile.open(out, "r:gz") as tar:
        names = tar.getnames()
    # Expected paths present
    assert any("data/pipeline.db" in n for n in names)
    assert any("companies/foo-corp/briefing.md" in n for n in names)
    assert any("candidate_context/profile.md" in n for n in names)
    assert any("manifest.json" in n for n in names)
    # Skipped paths absent
    assert not any("aichat_ng" in n for n in names), f"aichat_ng leaked: {names}"
    assert not any("logs/" in n for n in names), f"logs/ leaked: {names}"


def test_tarball_includes_onboarding_sentinel_but_excludes_env(tmp_path: Path) -> None:
    """data/.onboarding-complete must be included — it's what flags a
    stack as already-onboarded so the migrated Fly app skips the
    onboarding gate. data/.env must NOT be included — credentials
    are handed off separately via `fly secrets import` per the
    runbook, and findajob's runtime reads credentials from env vars
    only (no load_dotenv), so a copy of .env on the Fly volume would
    be dormant + a secrets-at-rest hazard."""
    state = tmp_path / "state"
    out = tmp_path / "stack.tar.gz"
    _build_fake_stack(state)

    exporter.export(state_dir=state, tarball_path=out, source_stack_tag="findajob-staging")

    with tarfile.open(out, "r:gz") as tar:
        names = tar.getnames()
    assert any(n.endswith(".onboarding-complete") for n in names), names
    # data/.env is explicitly filtered out — see EXCLUDED_FILES in exporter.py
    assert not any(n.endswith("data/.env") for n in names), f".env leaked into tarball: {names}"


def test_manifest_inside_tarball_is_consistent(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = tmp_path / "stack.tar.gz"
    _build_fake_stack(state)

    exporter.export(state_dir=state, tarball_path=out, source_stack_tag="findajob-staging")

    extracted = tmp_path / "extracted"
    with tarfile.open(out, "r:gz") as tar:
        tar.extractall(extracted)

    m = mf.read_from_path(extracted / "manifest.json")
    assert m.source_stack_tag == "findajob-staging"
    assert m.db_row_counts["jobs"] == 3


def test_export_refuses_missing_state_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        exporter.export(
            state_dir=tmp_path / "nope",
            tarball_path=tmp_path / "stack.tar.gz",
            source_stack_tag="findajob-x",
        )


def test_export_refuses_missing_pipeline_db(tmp_path: Path) -> None:
    state = tmp_path / "state"
    (state / "data").mkdir(parents=True)
    (state / "companies").mkdir()
    (state / "candidate_context").mkdir()
    # No pipeline.db
    with pytest.raises(FileNotFoundError):
        exporter.export(
            state_dir=state,
            tarball_path=tmp_path / "stack.tar.gz",
            source_stack_tag="findajob-x",
        )


def test_dry_run_does_not_write_tarball(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = tmp_path / "stack.tar.gz"
    _build_fake_stack(state)

    result = exporter.export(
        state_dir=state,
        tarball_path=out,
        source_stack_tag="findajob-staging",
        dry_run=True,
    )
    assert not out.exists()
    assert result.manifest.db_row_counts["jobs"] == 3
    assert result.tarball_path == out  # the path it *would* have written


def test_export_refuses_to_overwrite_existing_tarball(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = tmp_path / "stack.tar.gz"
    out.write_bytes(b"pre-existing")
    _build_fake_stack(state)

    with pytest.raises(FileExistsError):
        exporter.export(
            state_dir=state,
            tarball_path=out,
            source_stack_tag="findajob-staging",
        )


def test_pipeline_db_sha256_matches_observed_post_extract(tmp_path: Path) -> None:
    """End-to-end checksum invariant: the manifest's claimed sha256 of
    pipeline.db matches the sha256 of the file once extracted."""
    state = tmp_path / "state"
    out = tmp_path / "stack.tar.gz"
    _build_fake_stack(state)

    result = exporter.export(state_dir=state, tarball_path=out, source_stack_tag="findajob-staging")

    extracted = tmp_path / "extracted"
    with tarfile.open(out, "r:gz") as tar:
        tar.extractall(extracted)

    observed = exporter.sha256_of_file(extracted / "data" / "pipeline.db")
    assert observed == result.manifest.pipeline_db_sha256
