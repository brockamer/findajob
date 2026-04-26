"""Whole-feature verification for onboarding NUX + inject (#148).

Simulates a fresh stack: empty state/, no sentinel, config_loader sees nothing.
After one paste + redirect, the pipeline has everything it needs.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app

_MINIMAL_SCHEMA = """
CREATE TABLE jobs (
    id TEXT,
    fingerprint TEXT,
    title TEXT,
    company TEXT,
    stage TEXT,
    relevance_score INTEGER,
    fit_score REAL,
    probability_score REAL,
    interview_likelihood INTEGER,
    location TEXT,
    remote_status TEXT,
    known_contacts TEXT,
    comp_estimate TEXT,
    ai_notes TEXT,
    created_at TEXT,
    stage_updated TEXT,
    url TEXT,
    prep_folder_path TEXT
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    field_changed TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at TEXT DEFAULT (datetime('now'))
);
"""

_FIXTURE = Path(__file__).parent / "fixtures" / "onboarding" / "alice-doe-clean-emission.txt"


def test_fresh_stack_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_MINIMAL_SCHEMA)
    conn.close()
    (tmp_path / "companies").mkdir()
    (tmp_path / "config" / "roles").mkdir(parents=True)
    role_src = Path(__file__).parent.parent / "config" / "roles" / "onboarding_interviewer.md"
    (tmp_path / "config" / "roles" / "onboarding_interviewer.md").write_text(
        role_src.read_text(encoding="utf-8"), encoding="utf-8"
    )

    app = create_app(
        companies_root=tmp_path / "companies",
        db_path=db_path,
        base_root=tmp_path,
    )
    client = TestClient(app, follow_redirects=False)

    r = client.get("/board/dashboard")
    assert r.status_code == 307
    assert r.headers["location"] == "/onboarding/"

    r = client.get("/onboarding/")
    assert r.status_code == 200
    assert 'name="emission"' in r.text

    blob = _FIXTURE.read_text(encoding="utf-8")
    r = client.post("/onboarding/inject", data={"emission": blob})
    assert r.status_code == 200
    assert "Onboarding complete" in r.text

    assert (tmp_path / "candidate_context" / "profile.md").is_file()
    assert (tmp_path / "candidate_context" / "master_resume.md").is_file()
    assert (tmp_path / "config" / "target_companies.md").is_file()
    assert (tmp_path / "config" / "business_sector_employers_reference.md").is_file()
    assert (tmp_path / "config" / "jsearch_queries.txt").is_file()
    assert (tmp_path / "config" / "prefilter_rules.yaml").is_file()
    assert (tmp_path / "config" / "in_domain_patterns.yaml").is_file()

    coi = (tmp_path / "config" / "companies_of_interest.txt").read_text()
    assert "Metro Health Authority" in coi

    sentinel = (tmp_path / "data" / ".onboarding-complete").read_text().strip()
    assert sentinel.endswith("Z")

    r = client.get("/board/dashboard")
    assert r.status_code != 307 or r.headers.get("location") != "/onboarding/"

    monkeypatch.setenv("JSP_BASE", str(tmp_path))
    if "findajob.config_loader" in sys.modules:
        del sys.modules["findajob.config_loader"]
    if "findajob.paths" in sys.modules:
        del sys.modules["findajob.paths"]
    import findajob.paths  # noqa: F401 — re-import with new BASE
    from findajob.config_loader import load_companies_of_interest

    companies = load_companies_of_interest()
    assert companies, "companies_of_interest must be populated after injection"
    assert any("metro" in c for c in companies)
