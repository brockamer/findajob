"""scripts/rescore_all.py stores the scorer's provenance the way triage does.

Triage writes ``jobs.scored_by`` and ``jobs.company_tier`` with every score
(``findajob.triage.orchestrator``). rescore_all wrote neither, so a re-scored
row kept the previous run's values — a location-gate reject (#1217) re-scored
by rescore_all still showed its old ``scored_by``.
"""

from __future__ import annotations

import sqlite3
import sys

import scripts.rescore_all as rescore_all
from tests.conftest import init_test_db


def test_rescore_writes_scored_by_and_company_tier(tmp_path, monkeypatch):
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, location, source, raw_jd_text, stage, "
        "scored_by, company_tier) VALUES ('j1', 'fp1', 'https://example.com/1', 'Product Manager', 'Acme', "
        "'Seoul, South Korea', 'manual', 'A usable job description.', 'scored', 'llm', 'unknown')"
    )
    conn.commit()
    conn.close()
    profile = tmp_path / "profile.md"
    profile.write_text("Test profile.\n")
    scored = {
        "score_status": "scored",
        "relevance_score": 2,
        "interview_likelihood": 1,
        "strengths_alignment": "x",
        "industry_sector": None,
        "comp_estimate": None,
        "ai_notes": "location mismatch",
        "score_flag_reason": "location_mismatch",
        "remote_status": "Unknown",
        "scored_by": "prefilter_location",
        "company_tier": "tier_1",
    }
    monkeypatch.setattr(rescore_all, "DB_PATH", str(db))
    monkeypatch.setattr(rescore_all, "PROFILE_PATH", str(profile))
    monkeypatch.setattr(rescore_all, "score_job", lambda *a, **kw: (scored, 0, None))
    monkeypatch.setattr(sys, "argv", ["rescore_all.py"])

    rescore_all.main()

    row = (
        sqlite3.connect(db)
        .execute("SELECT relevance_score, scored_by, company_tier FROM jobs WHERE id='j1'")
        .fetchone()
    )
    assert row == (2, "prefilter_location", "tier_1")
