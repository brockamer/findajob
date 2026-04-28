"""Per-tab integration tests for the new filter framework.

Each test seeds a small set of jobs (with jobs.id set per
feedback_test_fixtures_jobs_id), hits a /board/{tab}/rows endpoint
with various URL params, and asserts the right rows are returned.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app


@pytest.fixture
def app_with_db(tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS jobs (
      id TEXT PRIMARY KEY,
      fingerprint TEXT UNIQUE NOT NULL,
      title TEXT, company TEXT, location TEXT, remote_status TEXT,
      known_contacts TEXT, comp_estimate TEXT, ai_notes TEXT,
      relevance_score INTEGER, fit_score REAL, probability_score REAL,
      interview_likelihood REAL,
      stage TEXT, created_at TEXT, stage_updated TEXT,
      url TEXT, prep_folder_path TEXT, source TEXT,
      score_flag_reason TEXT, reject_reason TEXT, user_notes TEXT,
      synthetic INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS audit_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      job_id TEXT, field_changed TEXT, old_value TEXT, new_value TEXT,
      changed_at TEXT, changed_by TEXT
    );
    """)
    conn.commit()
    conn.close()

    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    app = create_app(companies_root=companies, db_path=db_path, base_root=tmp_path)
    yield TestClient(app), db_path


def _insert_job(
    db_path: Path,
    *,
    id: str,
    fingerprint: str,
    stage: str = "scored",
    relevance_score: int = 7,
    title: str = "Engineer",
    company: str = "Acme",
    location: str = "SF",
    source: str = "manual",
    created_at: str | None = None,
    **kw: object,
) -> None:
    conn = sqlite3.connect(db_path)
    cols: dict[str, object] = {
        "id": id,
        "fingerprint": fingerprint,
        "title": title,
        "company": company,
        "location": location,
        "stage": stage,
        "relevance_score": relevance_score,
        "source": source,
        "remote_status": "Remote",
        "created_at": created_at or datetime.now(UTC).isoformat(),
        **kw,
    }
    placeholders = ", ".join("?" * len(cols))
    conn.execute(
        f"INSERT INTO jobs ({', '.join(cols.keys())}) VALUES ({placeholders})",
        tuple(cols.values()),
    )
    conn.commit()
    conn.close()


def _audit_log(db_path: Path, *, job_id: str, new_value: str, changed_at: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, new_value, changed_at, changed_by) "
        "VALUES (?, 'stage', ?, ?, 'test')",
        (job_id, new_value, changed_at),
    )
    conn.commit()
    conn.close()


# ─── Dashboard ────────────────────────────────────────────────────────────────


def test_dashboard_default_landing_shows_score_7_plus_only(
    app_with_db: tuple[TestClient, Path],
) -> None:
    client, db_path = app_with_db
    _insert_job(db_path, id="a1", fingerprint="fp-a1", relevance_score=8, title="High")
    _insert_job(db_path, id="a2", fingerprint="fp-a2", relevance_score=6, title="Mid")
    _insert_job(db_path, id="a3", fingerprint="fp-a3", relevance_score=5, title="Low")

    r = client.get("/board/dashboard")
    assert r.status_code == 200
    assert "fp-a1" in r.text
    assert "fp-a2" not in r.text
    assert "fp-a3" not in r.text


def test_archive_score_min_5_surfaces_buried_gems(
    app_with_db: tuple[TestClient, Path],
) -> None:
    """relevance_score_min=5 on Archive (no base score gate) surfaces 5+6 not 4."""
    client, db_path = app_with_db
    _insert_job(db_path, id="b1", fingerprint="fp-b1", relevance_score=6, title="Six")
    _insert_job(db_path, id="b2", fingerprint="fp-b2", relevance_score=5, title="Five")
    _insert_job(db_path, id="b3", fingerprint="fp-b3", relevance_score=4, title="Four")

    r = client.get("/board/archive/rows?relevance_score_min=5")
    assert r.status_code == 200
    assert "fp-b1" in r.text
    assert "fp-b2" in r.text
    assert "fp-b3" not in r.text


def test_dashboard_score_min_5_surfaces_buried_gems_on_dashboard(
    app_with_db: tuple[TestClient, Path],
) -> None:
    """The score-7+ default on Dashboard is route-level, not baked into the
    base WHERE — so an explicit ?relevance_score_min=5 surfaces 5/6 jobs that
    were hidden by the cold-load 7+ floor."""
    client, db_path = app_with_db
    _insert_job(db_path, id="bg1", fingerprint="fp-bg1", relevance_score=8, title="Eight", stage="scored")
    _insert_job(db_path, id="bg2", fingerprint="fp-bg2", relevance_score=6, title="Six", stage="scored")
    _insert_job(db_path, id="bg3", fingerprint="fp-bg3", relevance_score=5, title="Five", stage="scored")
    _insert_job(db_path, id="bg4", fingerprint="fp-bg4", relevance_score=4, title="Four", stage="scored")

    # Cold load: only the 8.
    r1 = client.get("/board/dashboard")
    assert r1.status_code == 200
    assert "fp-bg1" in r1.text
    assert "fp-bg2" not in r1.text
    assert "fp-bg3" not in r1.text

    # ?relevance_score_min=5 explicitly: 5, 6, 8 — but NOT 4.
    r2 = client.get("/board/dashboard?relevance_score_min=5")
    assert r2.status_code == 200
    assert "fp-bg1" in r2.text
    assert "fp-bg2" in r2.text
    assert "fp-bg3" in r2.text
    assert "fp-bg4" not in r2.text


def test_dashboard_score_min_0_disables_default_floor(
    app_with_db: tuple[TestClient, Path],
) -> None:
    """An explicit ?relevance_score_min=0 lets the operator see everything in
    the dashboard's stage gate, including very low scores."""
    client, db_path = app_with_db
    _insert_job(db_path, id="z1", fingerprint="fp-z1", relevance_score=2, title="Two", stage="scored")
    _insert_job(db_path, id="z2", fingerprint="fp-z2", relevance_score=8, title="Eight", stage="scored")

    r = client.get("/board/dashboard?relevance_score_min=0")
    assert r.status_code == 200
    assert "fp-z1" in r.text
    assert "fp-z2" in r.text


def test_dashboard_text_filter_on_title(
    app_with_db: tuple[TestClient, Path],
) -> None:
    client, db_path = app_with_db
    _insert_job(db_path, id="c1", fingerprint="fp-c1", relevance_score=8, title="Director of NPI")
    _insert_job(db_path, id="c2", fingerprint="fp-c2", relevance_score=8, title="VP Engineering")

    r = client.get("/board/dashboard/rows?title=director")
    assert r.status_code == 200
    assert "fp-c1" in r.text
    assert "fp-c2" not in r.text


def test_dashboard_sort_changes_preserve_filter(
    app_with_db: tuple[TestClient, Path],
) -> None:
    client, db_path = app_with_db
    _insert_job(db_path, id="d1", fingerprint="fp-d1", relevance_score=8, title="A")
    _insert_job(db_path, id="d2", fingerprint="fp-d2", relevance_score=9, title="A2")

    r = client.get("/board/dashboard/rows?relevance_score_min=7&sort=created_at&desc=0")
    assert r.status_code == 200
    assert "fp-d1" in r.text
    assert "fp-d2" in r.text


def test_dashboard_cols_replaces_default_set(
    app_with_db: tuple[TestClient, Path],
) -> None:
    client, db_path = app_with_db
    _insert_job(
        db_path,
        id="e1",
        fingerprint="fp-e1",
        relevance_score=8,
        title="Director",
        ai_notes="Long detailed notes here",
    )

    # Default landing renders ai_notes column (default_visible=True in DASHBOARD_COLUMNS).
    r1 = client.get("/board/dashboard")
    assert "Long detailed notes here" in r1.text

    # ?cols=title only: ai_notes column is dropped.
    r2 = client.get("/board/dashboard?cols=title")
    assert r2.status_code == 200
    assert "Director" in r2.text
    assert "Long detailed notes here" not in r2.text


# ─── Applied ──────────────────────────────────────────────────────────────────


def test_applied_filter_by_days_since_applied(
    app_with_db: tuple[TestClient, Path],
) -> None:
    client, db_path = app_with_db
    old = (datetime.now(UTC) - timedelta(days=21)).strftime("%Y-%m-%d %H:%M:%S")
    fresh = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    _insert_job(db_path, id="ap1", fingerprint="fp-ap1", stage="applied", title="Old App")
    _audit_log(db_path, job_id="ap1", new_value="applied", changed_at=old)
    _insert_job(db_path, id="ap2", fingerprint="fp-ap2", stage="applied", title="Fresh App")
    _audit_log(db_path, job_id="ap2", new_value="applied", changed_at=fresh)

    r = client.get("/board/applied/rows?days_since_applied_min=14")
    assert r.status_code == 200
    assert "fp-ap1" in r.text
    assert "fp-ap2" not in r.text


# ─── Rejected ─────────────────────────────────────────────────────────────────


def test_rejected_filter_by_reason(
    app_with_db: tuple[TestClient, Path],
) -> None:
    client, db_path = app_with_db
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    _insert_job(
        db_path,
        id="rj1",
        fingerprint="fp-rj1",
        stage="rejected",
        title="Bad Comp",
        reject_reason="Compensation",
    )
    _audit_log(db_path, job_id="rj1", new_value="rejected", changed_at=now)
    _insert_job(
        db_path,
        id="rj2",
        fingerprint="fp-rj2",
        stage="rejected",
        title="Bad Loc",
        reject_reason="Location",
    )
    _audit_log(db_path, job_id="rj2", new_value="rejected", changed_at=now)

    r = client.get("/board/rejected/rows?reject_reason=Compensation")
    assert r.status_code == 200
    assert "fp-rj1" in r.text
    assert "fp-rj2" not in r.text


# ─── Archive ──────────────────────────────────────────────────────────────────


def test_archive_filter_by_source(
    app_with_db: tuple[TestClient, Path],
) -> None:
    client, db_path = app_with_db
    _insert_job(db_path, id="ar1", fingerprint="fp-ar1", source="greenhouse_json")
    _insert_job(db_path, id="ar2", fingerprint="fp-ar2", source="jobsapi_indeed")

    r = client.get("/board/archive/rows?source=greenhouse_json")
    assert r.status_code == 200
    assert "fp-ar1" in r.text
    assert "fp-ar2" not in r.text


# ─── Cross-cutting ────────────────────────────────────────────────────────────


def test_invalid_param_silently_dropped(
    app_with_db: tuple[TestClient, Path],
) -> None:
    client, _ = app_with_db
    r = client.get("/board/dashboard/rows?bogus=value&title__bad=x")
    assert r.status_code == 200


def test_filter_input_attrs_render_for_htmx_include(
    app_with_db: tuple[TestClient, Path],
) -> None:
    """The header partial must emit data-filter-input on every filter input
    so HTMX hx-include picks them up. Smoke check on the dashboard."""
    client, db_path = app_with_db
    _insert_job(db_path, id="z1", fingerprint="fp-z1", relevance_score=8)

    r = client.get("/board/dashboard")
    assert r.status_code == 200
    assert "data-filter-input" in r.text
    assert 'hx-push-url="true"' in r.text
