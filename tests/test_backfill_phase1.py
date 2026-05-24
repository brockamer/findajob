"""Tests for the Phase 1 backfill scripts."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def seeded_db(tmp_path):
    base = tmp_path / "repo"
    (base / "data").mkdir(parents=True)
    (base / "config").mkdir()
    (base / "config" / "companies_of_interest.txt").write_text("Acme Corp\nGlobex\n")

    env = os.environ.copy()
    env["JSP_BASE"] = str(base)
    repo_root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "init_db.py")],
        env=env,
        check=True,
        capture_output=True,
    )

    conn = sqlite3.connect(str(base / "data" / "pipeline.db"))
    conn.executemany(
        """INSERT INTO jobs (id, fingerprint, url, title, company, source, ai_notes, score_flag_reason, relevance_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("j1", "fp1", "u", "Program Manager", "Acme Corp", "greenhouse_json", "Normal LLM notes", None, 7),
            (
                "j2",
                "fp2",
                "u",
                "SWE",
                "Globex",
                "jobsapi_indeed",
                "Hard reject — title is outside candidate domain.",
                'Pre-filter hard reject: title matched "SWE"',
                1,
            ),
            (
                "j3",
                "fp3",
                "u",
                "DC Technician",
                "Unknown Co",
                "jsearch",
                "Title is directionally in-domain. JD unavailable — scored 5 per policy.",
                "Pre-filter in-domain/no-JD: scored 5",
                5,
            ),
        ],
    )
    conn.commit()
    conn.close()
    return base, env


def test_backfill_company_tier(seeded_db):
    base, env = seeded_db
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "backfill_company_tier.py")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    conn = sqlite3.connect(str(base / "data" / "pipeline.db"))
    rows = {r[0]: r[1] for r in conn.execute("SELECT id, company_tier FROM jobs").fetchall()}
    conn.close()
    assert rows["j1"] == "tier1"
    assert rows["j2"] == "tier1"
    assert rows["j3"] == "other"


def test_backfill_scored_by(seeded_db):
    base, env = seeded_db
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "backfill_scored_by.py")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    conn = sqlite3.connect(str(base / "data" / "pipeline.db"))
    rows = {r[0]: r[1] for r in conn.execute("SELECT id, scored_by FROM jobs").fetchall()}
    conn.close()
    assert rows["j1"] == "llm"
    assert rows["j2"] == "prefilter_stage1"
    assert rows["j3"] == "prefilter_stage2"


def test_backfill_idempotent(seeded_db):
    base, env = seeded_db
    repo_root = Path(__file__).resolve().parents[1]
    for _ in range(2):
        subprocess.run(
            [sys.executable, str(repo_root / "scripts" / "backfill_company_tier.py")],
            env=env,
            check=True,
            capture_output=True,
        )
    conn = sqlite3.connect(str(base / "data" / "pipeline.db"))
    count = conn.execute("SELECT COUNT(*) FROM jobs WHERE company_tier='tier1'").fetchone()[0]
    conn.close()
    assert count == 2
