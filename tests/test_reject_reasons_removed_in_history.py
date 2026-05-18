"""AC #3 from #490: a reason that exists in `feedback_log` historical
rows but has been removed from `reject_reasons.yaml` MUST NOT break
/board/dashboard/, /stats/, or /board/rejected/.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob import config_loader
from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import ensure_view_prefs_table


@pytest.fixture
def client_with_orphan_feedback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # 1. reject_reasons.yaml WITHOUT "Orphaned Reason"
    yaml_path = tmp_path / "reject_reasons.yaml"
    yaml_path.write_text("reasons:\n  - Skills Mismatch\n  - Wrong Domain\n")
    monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", yaml_path)

    # 2. SQLite with the schemas the three views need.
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            fingerprint TEXT UNIQUE NOT NULL,
            loose_fingerprint TEXT,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT DEFAULT '',
            source TEXT NOT NULL,
            raw_jd_text TEXT,
            remote_status TEXT DEFAULT 'Unknown',
            known_contacts TEXT DEFAULT '',
            ai_notes TEXT,
            relevance_score INTEGER,
            fit_score REAL,
            probability_score REAL,
            interview_likelihood INTEGER,
            stage TEXT DEFAULT 'discovered',
            apply_flag INTEGER DEFAULT 0,
            reject_reason TEXT DEFAULT '',
            prep_folder_path TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            stage_updated TEXT,
            dupe_of TEXT DEFAULT '',
            synthetic INTEGER NOT NULL DEFAULT 0,
            score_flag_reason TEXT,
            comp_estimate TEXT,
            user_notes TEXT
        );
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            field_changed TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            changed_at TEXT DEFAULT (datetime('now')),
            changed_by TEXT DEFAULT 'system'
        );
        CREATE TABLE feedback_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            relevance_score INTEGER,
            reject_reason TEXT NOT NULL,
            jd_excerpt TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE cost_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            cost_usd REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    ensure_view_prefs_table(conn)

    # 3. Insert one rejected job + a feedback_log row whose reason is
    #    NOT in the current YAML — this is the orphan condition.
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage, reject_reason, "
        "source, url, created_at) "
        "VALUES ('fp-orphan', 'fp-orphan', 'Some Job', 'Acme', 'rejected', "
        "'Orphaned Reason', 'test', 'https://example.com/j1', '2026-04-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO feedback_log (job_id, title, company, relevance_score, reject_reason, "
        "created_at) "
        "VALUES ('fp-orphan', 'Some Job', 'Acme', 5, 'Orphaned Reason', "
        "'2026-04-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, "
        "changed_at, changed_by) "
        "VALUES ('fp-orphan', 'stage', 'scored', 'rejected', '2026-04-01 00:00:00', 'user')"
    )
    conn.commit()
    conn.close()

    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)

    return TestClient(
        create_app(companies_root=companies, db_path=db, base_root=tmp_path),
        follow_redirects=True,
    )


@pytest.mark.parametrize(
    "path",
    [
        "/board/dashboard/",
        "/stats/",
        "/board/rejected/",
    ],
)
def test_orphan_reason_does_not_break_view(client_with_orphan_feedback: TestClient, path: str) -> None:
    resp = client_with_orphan_feedback.get(path)
    assert resp.status_code == 200, f"{path} returned {resp.status_code}: {resp.text[:500]}"
