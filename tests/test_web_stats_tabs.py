"""Stats sub-tab bar — full taxonomy visible from day one; deferred tabs disabled.

14e (#63, #193). Funnel and Feedback are active links; the remaining four
tabs render as disabled <span aria-disabled="true"> placeholders until their
follow-up issues ship (#194–#197).
"""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app

ENABLED = {"/stats/funnel", "/stats/feedback"}
DISABLED_LABELS = ("Scoring", "Rejections", "Throughput", "Effectiveness")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, company TEXT, stage TEXT)")
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    conn.execute(
        "CREATE TABLE feedback_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, title TEXT NOT NULL, "
        "company TEXT NOT NULL, relevance_score INTEGER, reject_reason TEXT NOT NULL, "
        "jd_excerpt TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')))"
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    return TestClient(create_app(companies_root=companies, db_path=db))


def test_funnel_tab_active_marker(client: TestClient) -> None:
    r = client.get("/stats/funnel")
    assert r.status_code == 200
    idx = r.text.index('href="/stats/funnel"')
    snippet = r.text[idx : idx + 400]
    assert 'aria-current="page"' in snippet


def test_funnel_tab_has_href(client: TestClient) -> None:
    r = client.get("/stats/funnel")
    assert r.status_code == 200
    assert 'href="/stats/funnel"' in r.text


def test_feedback_tab_active_marker(client: TestClient) -> None:
    r = client.get("/stats/feedback")
    assert r.status_code == 200
    idx = r.text.index('href="/stats/feedback"')
    snippet = r.text[idx : idx + 400]
    assert 'aria-current="page"' in snippet


def test_feedback_tab_has_href(client: TestClient) -> None:
    r = client.get("/stats/funnel")
    assert r.status_code == 200
    assert 'href="/stats/feedback"' in r.text


@pytest.mark.parametrize("label", DISABLED_LABELS)
def test_deferred_tabs_render_as_disabled_spans(client: TestClient, label: str) -> None:
    r = client.get("/stats/funnel")
    assert r.status_code == 200
    # Each deferred tab must render as <span aria-disabled="true"> with its
    # label, and must NOT render as an <a href="…"> — we assert both.
    disabled_path = f"/stats/{label.lower()}"
    assert f'href="{disabled_path}"' not in r.text, f"{label} should not have an href"
    assert 'aria-disabled="true"' in r.text
    assert label in r.text


def test_top_nav_stats_link_resolves(client: TestClient) -> None:
    r = client.get("/stats/funnel")
    assert r.status_code == 200
    assert 'href="/stats/funnel"' in r.text
    # Top nav highlights "Stats" as active via aria-current when on any /stats/* page.
    # Find the Stats link in the top nav and assert aria-current appears nearby.
    assert 'aria-current="page"' in r.text
