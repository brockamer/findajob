"""End-to-end regression: save_reject_reasons() → next request reflects.

Pins the contract: after save_reject_reasons() is called the next HTTP
request against both the board reject-reason dropdown
(Jinja global reject_reason_options, /board/dashboard) and the
/board/rejected/ filter chip values (ColumnSpec.resolved_enum_values)
returns the updated reasons — with NO restart, cache reset, or
_reset_cache() call. (#490)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # 1. Point _REJECT_REASONS_PATH at a tmpdir YAML before create_app().
    yaml_path = tmp_path / "reject_reasons.yaml"
    yaml_path.write_text("reasons:\n  - Initial Reason\n")
    from findajob import config_loader

    monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", yaml_path)

    # 2. Minimal schema sufficient for /board/dashboard and /board/rejected.
    #    Dashboard requires: id, fingerprint, title, company, stage,
    #      relevance_score, fit_score, probability_score, interview_likelihood,
    #      location, remote_status, known_contacts, comp_estimate, ai_notes,
    #      created_at, stage_updated, url, prep_folder_path
    #    Rejected additionally requires: reject_reason; both routes LEFT JOIN
    #      audit_log.
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs ("
        "id TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, company TEXT, "
        "stage TEXT, reject_reason TEXT, relevance_score INTEGER, "
        "fit_score REAL, probability_score REAL, interview_likelihood INTEGER, "
        "location TEXT, remote_status TEXT, known_contacts TEXT, user_notes TEXT, "
        "comp_estimate TEXT, ai_notes TEXT, created_at TEXT, "
        "stage_updated TEXT, url TEXT, prep_folder_path TEXT, "
        "synthetic INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    conn.execute(
        "CREATE TABLE audit_log ("
        "id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT"
        ")"
    )
    # A high-scoring dashboard row (score >= 7 passes the default floor).
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage, relevance_score, url) "
        "VALUES ('id-dash','fp-dash','Staff Eng','Meta','scored',9,"
        "'https://example.com/j1')"
    )
    # A rejected row — ensures /board/rejected/ renders filter chips even with data.
    # Use a reject_reason value that is NOT one of the YAML reasons being tested
    # so that row-data text doesn't interfere with chip-value assertions.
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage, reject_reason, url) "
        "VALUES ('id-rej','fp-rej','Old Role','Acme','rejected','Geography',"
        "'https://example.com/j2')"
    )
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, "
        "changed_at, changed_by) "
        "VALUES ('id-rej','stage','scored','rejected','2026-01-01 00:00:00','user')"
    )
    conn.commit()
    conn.close()

    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)

    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_dropdown_reflects_save_without_restart(client: TestClient, tmp_path: Path) -> None:
    """Save → board reject-reason dropdown reflects on next request."""
    resp = client.get("/board/dashboard")
    assert resp.status_code == 200
    assert "Initial Reason" in resp.text

    from findajob.config_loader import save_reject_reasons

    save_reject_reasons(("Brand New Reason",), frozenset())

    # Same client, no restart, no _reset_cache. Next request must reflect.
    resp = client.get("/board/dashboard")
    assert resp.status_code == 200
    assert "Brand New Reason" in resp.text
    assert "Initial Reason" not in resp.text


def test_filter_chips_reflect_save_without_restart(client: TestClient, tmp_path: Path) -> None:
    """Save → /board/rejected filter chip values reflect on next request."""
    resp = client.get("/board/rejected")
    assert resp.status_code == 200
    assert "Initial Reason" in resp.text

    from findajob.config_loader import save_reject_reasons

    save_reject_reasons(("Updated Reason",), frozenset())

    resp = client.get("/board/rejected")
    assert resp.status_code == 200
    assert "Updated Reason" in resp.text
    assert "Initial Reason" not in resp.text
