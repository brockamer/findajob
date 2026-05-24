"""Tests for the config-drift detector."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import findajob.paths as _findajob_paths


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    base = tmp_path / "repo"
    (base / "data").mkdir(parents=True)
    (base / "config").mkdir()
    (base / "candidate_context").mkdir()

    env = os.environ.copy()
    env["JSP_BASE"] = str(base)
    repo_root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "init_db.py")],
        env=env,
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(_findajob_paths, "BASE", str(base))
    conn = sqlite3.connect(str(base / "data" / "pipeline.db"))
    return conn, base


def test_first_call_records_existing_levers(fresh_db):
    conn, base = fresh_db
    from findajob.metrics.config_changes import detect_and_record

    (base / "candidate_context" / "profile.md").write_text("PROFILE V1")
    (base / "config" / "jsearch_queries.txt").write_text("query one\n")

    detect_and_record(conn, changed_by="test")
    rows = conn.execute("SELECT lever, changed_by FROM config_changes").fetchall()
    levers = {r[0] for r in rows}
    assert "profile" in levers
    assert "queries" in levers
    assert all(r[1] == "test" for r in rows)


def test_unchanged_content_no_new_row(fresh_db):
    conn, base = fresh_db
    from findajob.metrics.config_changes import detect_and_record

    (base / "candidate_context" / "profile.md").write_text("PROFILE V1")

    detect_and_record(conn, changed_by="test")
    detect_and_record(conn, changed_by="test")

    n = conn.execute("SELECT COUNT(*) FROM config_changes WHERE lever='profile'").fetchone()[0]
    assert n == 1


def test_changed_content_inserts_new_row(fresh_db):
    conn, base = fresh_db
    from findajob.metrics.config_changes import detect_and_record

    profile = base / "candidate_context" / "profile.md"
    profile.write_text("PROFILE V1")
    detect_and_record(conn, changed_by="test")

    profile.write_text("PROFILE V2")
    detect_and_record(conn, changed_by="test")

    rows = conn.execute("SELECT content_hash FROM config_changes WHERE lever='profile' ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0][0] != rows[1][0]


def test_missing_file_skipped_silently(fresh_db):
    conn, base = fresh_db
    from findajob.metrics.config_changes import detect_and_record

    (base / "candidate_context" / "profile.md").write_text("x")

    detect_and_record(conn, changed_by="test")
    levers = {r[0] for r in conn.execute("SELECT lever FROM config_changes").fetchall()}
    assert "profile" in levers
    assert "queries" not in levers


def test_changed_by_propagates(fresh_db):
    conn, base = fresh_db
    from findajob.metrics.config_changes import detect_and_record

    (base / "candidate_context" / "profile.md").write_text("x")

    detect_and_record(conn, changed_by="onboarding")
    row = conn.execute("SELECT changed_by FROM config_changes WHERE lever='profile'").fetchone()
    assert row[0] == "onboarding"
