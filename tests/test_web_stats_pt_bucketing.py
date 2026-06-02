"""Stats windows bucket on the operator's PT calendar, not UTC (#967).

audit_log timestamps are naïve UTC; the operator's calendar is PT. A late-evening
PT transition is stored as the *next* UTC day, so naïve ``date(changed_at)``
buckets it one day late. This drives the real funnel route through a TestClient
and asserts the row lands on the PT day.
"""

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.timeutil import today_local
from findajob.web.app import create_app

PT = "America/Los_Angeles"


def _chart_data(html: str) -> dict:
    m = re.search(r'id="funnel-chart-data"[^>]*>(.*?)</script>', html, re.DOTALL)
    assert m, "funnel-chart-data script payload not found"
    return json.loads(m.group(1))


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TZ", PT)
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, company TEXT, stage TEXT)")
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS config_changes ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT, lever TEXT NOT NULL, "
        "  changed_at TEXT DEFAULT (datetime('now')), changed_by TEXT DEFAULT 'manual', "
        "  change_summary TEXT, content_hash TEXT, diff_summary TEXT)"
    )
    # 23:30 PT yesterday -> stored as today's UTC date. PT bucketing must place
    # it on PT-yesterday; UTC bucketing wrongly places it on the UTC next day.
    pt_yesterday = today_local(PT) - timedelta(days=1)
    local_dt = datetime(pt_yesterday.year, pt_yesterday.month, pt_yesterday.day, 23, 30, tzinfo=ZoneInfo(PT))
    stored = local_dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, old_value, new_value, changed_at, changed_by) "
        "VALUES ('j1', 'stage', 'materials_drafted', 'applied', ?, 'system')",
        (stored,),
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_late_pt_transition_buckets_onto_pt_day(client: TestClient) -> None:
    pt_yesterday = (today_local(PT) - timedelta(days=1)).isoformat()
    pt_today = today_local(PT).isoformat()
    data = _chart_data(client.get("/stats/funnel").text)
    applied = next(d["data"] for d in data["datasets"] if d["label"] == "applied")
    by_day = dict(zip(data["labels"], applied, strict=False))
    assert by_day.get(pt_yesterday) == 1, "late-PT 'applied' must bucket onto the PT day"
    assert by_day.get(pt_today, 0) == 0, "must not leak onto the UTC next day"
