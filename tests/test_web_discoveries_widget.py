"""Dashboard discoveries widget — #288 Section B.

Covers the helper's edge cases (missing/malformed JSON) and the five visual
states the widget renders on /board/dashboard.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from findajob.web.discoveries import STALE_THRESHOLD_DAYS, load_discoveries_summary


def _write_json(base_root: Path, payload: dict) -> Path:
    cc = base_root / "candidate_context"
    cc.mkdir(parents=True, exist_ok=True)
    p = cc / "discovered_companies.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_load_returns_none_when_file_missing(tmp_path: Path) -> None:
    assert load_discoveries_summary(tmp_path) is None


def test_load_returns_none_on_malformed_json(tmp_path: Path) -> None:
    cc = tmp_path / "candidate_context"
    cc.mkdir()
    (cc / "discovered_companies.json").write_text("{not valid json", encoding="utf-8")
    assert load_discoveries_summary(tmp_path) is None


def test_load_returns_none_when_required_fields_missing(tmp_path: Path) -> None:
    _write_json(tmp_path, {"companies": [], "model": "x"})  # no generated_at
    assert load_discoveries_summary(tmp_path) is None


def test_load_returns_none_on_unparseable_date(tmp_path: Path) -> None:
    _write_json(tmp_path, {"generated_at": "not-a-date", "companies": []})
    assert load_discoveries_summary(tmp_path) is None


def test_load_fresh_summary(tmp_path: Path) -> None:
    _write_json(
        tmp_path,
        {
            "generated_at": "2026-04-26",
            "companies": [
                {"name": "Alpha Co"},
                {"name": "Beta Inc"},
                {"name": "Gamma LLC"},
            ],
        },
    )
    s = load_discoveries_summary(tmp_path, today=date(2026, 4, 28))
    assert s is not None
    assert s.count == 3
    assert s.generated_at_date == "2026-04-26"
    assert s.days_since == 2
    assert s.is_stale is False
    assert s.top_names == ["Alpha Co", "Beta Inc", "Gamma LLC"]


def test_load_caps_top_names_at_5(tmp_path: Path) -> None:
    _write_json(
        tmp_path,
        {
            "generated_at": "2026-04-26",
            "companies": [{"name": f"Co{i}"} for i in range(8)],
        },
    )
    s = load_discoveries_summary(tmp_path, today=date(2026, 4, 26))
    assert s is not None
    assert s.count == 8
    assert s.top_names == ["Co0", "Co1", "Co2", "Co3", "Co4"]


def test_load_marks_stale_past_threshold(tmp_path: Path) -> None:
    _write_json(
        tmp_path,
        {"generated_at": "2026-04-12", "companies": [{"name": "X"}]},
    )
    s = load_discoveries_summary(tmp_path, today=date(2026, 4, 28))
    assert s is not None
    assert s.days_since == 16
    assert s.days_since > STALE_THRESHOLD_DAYS
    assert s.is_stale is True


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (id TEXT, fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "relevance_score INTEGER, fit_score REAL, probability_score REAL, interview_likelihood INTEGER, "
        "location TEXT, remote_status TEXT, known_contacts TEXT, comp_estimate TEXT, "
        "ai_notes TEXT, created_at TEXT, stage_updated TEXT, url TEXT, prep_folder_path TEXT)"
    )
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_dashboard_widget_empty_never_run(client: TestClient) -> None:
    r = client.get("/board/dashboard")
    assert r.status_code == 200
    assert "cron runs weekly Sundays" in r.text


def test_dashboard_widget_empty_zero_hits(client: TestClient, tmp_path: Path) -> None:
    today = date.today().isoformat()
    _write_json(tmp_path, {"generated_at": today, "companies": []})
    r = client.get("/board/dashboard")
    assert r.status_code == 200
    assert "0 companies this run" in r.text
    assert f"updated {today}" in r.text
    assert "/config/files/candidate_context/discovered_companies.md" in r.text


def test_dashboard_widget_fresh(client: TestClient, tmp_path: Path) -> None:
    today = date.today().isoformat()
    _write_json(
        tmp_path,
        {
            "generated_at": today,
            "companies": [{"name": "Lightmatter"}, {"name": "Lambda Labs"}],
        },
    )
    r = client.get("/board/dashboard")
    assert r.status_code == 200
    assert "2 companies" in r.text
    assert f"updated {today}" in r.text
    assert "weekly cron may have skipped" not in r.text


def test_dashboard_widget_stale(client: TestClient, tmp_path: Path) -> None:
    """Past-threshold runs render the stale variant with the warning text."""
    from datetime import timedelta

    stale_date = (date.today() - timedelta(days=STALE_THRESHOLD_DAYS + 5)).isoformat()
    _write_json(
        tmp_path,
        {"generated_at": stale_date, "companies": [{"name": "Old Co"}]},
    )
    r = client.get("/board/dashboard")
    assert r.status_code == 200
    assert "weekly cron may have skipped" in r.text
    assert stale_date in r.text


def test_dashboard_widget_link_target(client: TestClient, tmp_path: Path) -> None:
    """All non-empty states render the canonical config-editor link."""
    _write_json(
        tmp_path,
        {"generated_at": date.today().isoformat(), "companies": [{"name": "X"}]},
    )
    r = client.get("/board/dashboard")
    assert "/config/files/candidate_context/discovered_companies.md" in r.text
