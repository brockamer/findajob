"""Landing page shows stage counts."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app


def _seed_db(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "fit_score REAL, created_at TEXT, stage_updated TEXT)"
    )
    for stage, n in [("scored", 5), ("applied", 2), ("rejected", 3)]:
        for i in range(n):
            conn.execute(
                "INSERT INTO jobs (fingerprint, title, company, stage, created_at) "
                "VALUES (?, 't', 'c', ?, '2026-01-01')",
                (f"fp-{stage}-{i}", stage),
            )
    conn.commit()
    conn.close()


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """Default fixture: onboarded stack (sentinel present)."""
    db = tmp_path / "pipeline.db"
    _seed_db(db)
    companies = tmp_path / "companies"
    companies.mkdir()
    # Seed the sentinel so the new #339-Task-9 onboarding guard on the
    # landing route lets the request through.
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / ".onboarding-complete").write_text("2026-01-01T00:00:00Z\n")
    app = create_app(companies_root=companies, db_path=db, base_root=tmp_path)
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def client_no_sentinel(tmp_path: Path) -> TestClient:
    """Fresh-stack fixture: no sentinel, exercises the redirect guard."""
    db = tmp_path / "pipeline.db"
    _seed_db(db)
    companies = tmp_path / "companies"
    companies.mkdir()
    (tmp_path / "data").mkdir()
    app = create_app(companies_root=companies, db_path=db, base_root=tmp_path)
    return TestClient(app, follow_redirects=False)


def test_landing_shows_stage_counts(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert ">5<" in r.text and "scored" in r.text
    assert ">2<" in r.text and "applied" in r.text
    assert ">3<" in r.text and "rejected" in r.text


def test_landing_nav_home_active(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert 'aria-current="page"' in r.text


def test_first_run_redirects_to_onboarding(client_no_sentinel: TestClient) -> None:
    """#339 Task 9: a fresh stack with no onboarding sentinel sends visitors
    of `/` directly into the onboarding flow rather than rendering the
    marketing-style landing page (where the next step would be invisible).
    """
    r = client_no_sentinel.get("/")
    assert r.status_code == 307
    assert r.headers["location"] == "/onboarding/"


def test_onboarding_page_remains_reachable_from_redirected_state(
    client_no_sentinel: TestClient,
) -> None:
    """#339 Task 9 (exitable property): after the redirect lands on
    /onboarding/, the user can still navigate to other top-nav routes —
    /onboarding/ itself is not guarded, and visiting it doesn't trap the
    user (they can click any nav link to leave). Sanity-check that GET
    /onboarding/ returns 200, not another redirect."""
    r = client_no_sentinel.get("/onboarding/")
    assert r.status_code == 200


# Placeholders retired:
# - /ingest/ promoted to a real route in #62 — covered by tests/test_web_ingest.py.
# - /config/ promoted to a real route in #149 — covered by tests/test_web_config_editor.py.
# - /tools/ promoted to a stub in #149 — covered by tests/test_web_config_editor.py.
# - /docs/ promoted to a real route in #224 — covered by tests/test_web_docs.py.
