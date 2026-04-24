"""Archive tab: pagination, HTMX sentinel."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "relevance_score INTEGER, fit_score REAL, probability_score REAL, location TEXT, "
        "remote_status TEXT, source TEXT, url TEXT, created_at TEXT, stage_updated TEXT)"
    )
    for i in range(150):
        conn.execute(
            "INSERT INTO jobs (fingerprint, title, company, stage, relevance_score, "
            "fit_score, created_at) "
            "VALUES (?, ?, ?, 'scored', ?, ?, '2026-01-01')",
            (f"fp-{i:03}", f"Role {i}", f"Co {i}", 1 + i % 10, 1.0 + i % 10),
        )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_archive_first_page_100_rows_plus_sentinel(client: TestClient) -> None:
    r = client.get("/board/archive")
    assert r.status_code == 200
    # 100 data rows + header row + sentinel row
    assert r.text.count("<tr") >= 101
    assert "offset=100" in r.text


def test_archive_second_page_via_rows_endpoint(client: TestClient) -> None:
    r = client.get("/board/archive/rows?offset=100")
    assert r.status_code == 200
    # Remaining 50 rows, no sentinel (end reached)
    assert r.text.count("<tr") == 50
    assert "offset=" not in r.text


def test_archive_rows_endpoint_returns_fragment_not_full_page(client: TestClient) -> None:
    r = client.get("/board/archive/rows?offset=0")
    assert r.status_code == 200
    assert "<body" not in r.text.lower()


@pytest.fixture
def scored_client(tmp_path: Path) -> TestClient:
    """Small hand-crafted fixture with varied relevance_score + stage for filter/promote tests."""
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (id TEXT, fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "relevance_score INTEGER, fit_score REAL, probability_score REAL, location TEXT, "
        "remote_status TEXT, source TEXT, url TEXT, created_at TEXT, stage_updated TEXT)"
    )
    # Two score-6 rows at stage='scored' — should appear with min_score=6 and have Promote button
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage, relevance_score, created_at) "
        "VALUES ('id-6a','fp-6a','Six Alpha','Co6A','scored',6,'2026-04-24')"
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage, relevance_score, created_at) "
        "VALUES ('id-6b','fp-6b','Six Bravo','Co6B','scored',6,'2026-04-24')"
    )
    # One score-3 row (below min_score=6, should be filtered out)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage, relevance_score, created_at) "
        "VALUES ('id-3','fp-3','Three Only','Co3','scored',3,'2026-04-24')"
    )
    # One score-9 row at stage='applied' — appears with min_score=6 but NO Promote button (already applied)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage, relevance_score, created_at) "
        "VALUES ('id-9','fp-9','Nine Applied','Co9','applied',9,'2026-04-24')"
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_archive_min_score_filters_rows(scored_client: TestClient) -> None:
    """#238: /board/archive?min_score=6 hides rows with relevance_score < 6."""
    r = scored_client.get("/board/archive?min_score=6")
    assert r.status_code == 200
    assert "Six Alpha" in r.text
    assert "Six Bravo" in r.text
    assert "Nine Applied" in r.text  # score=9 passes filter
    assert "Three Only" not in r.text  # score=3 filtered out


def test_archive_max_score_filters_rows(scored_client: TestClient) -> None:
    """#238: /board/archive?max_score=5 hides rows with relevance_score > 5."""
    r = scored_client.get("/board/archive?max_score=5")
    assert r.status_code == 200
    assert "Three Only" in r.text  # score=3 passes
    assert "Six Alpha" not in r.text
    assert "Nine Applied" not in r.text


def test_archive_promote_button_on_scored_rows(scored_client: TestClient) -> None:
    """#238: rows at stage='scored' render a Promote button (POST to /board/jobs/.../promote)."""
    r = scored_client.get("/board/archive?min_score=6")
    assert r.status_code == 200
    # Promote button for fp-6a (stage=scored)
    assert "/board/jobs/fp-6a/promote" in r.text


def test_archive_no_promote_button_on_non_scored_rows(scored_client: TestClient) -> None:
    """#238: rows at stages other than 'scored' do NOT render a Promote button."""
    r = scored_client.get("/board/archive?min_score=6")
    assert r.status_code == 200
    # fp-9 is stage='applied' — no Promote button (already applied; promoting makes no sense)
    assert "/board/jobs/fp-9/promote" not in r.text


def test_archive_score6_preset_link_in_header(scored_client: TestClient) -> None:
    """#238: Archive page has a one-click link to the score-6 review queue."""
    r = scored_client.get("/board/archive")
    assert r.status_code == 200
    # Preset link with min_score=6 is the one-click entrypoint for weekend-dip triage
    assert "min_score=6" in r.text


def test_archive_rows_htmx_respects_min_score(scored_client: TestClient) -> None:
    """#238: HTMX partial /board/archive/rows also filters on min_score for infinite-scroll consistency."""
    r = scored_client.get("/board/archive/rows?offset=0&min_score=6")
    assert r.status_code == 200
    assert "Six Alpha" in r.text
    assert "Three Only" not in r.text


def test_archive_shows_relevance_score_column(scored_client: TestClient) -> None:
    """#238: relevance_score is queried and rendered so operators can sort/filter by it."""
    r = scored_client.get("/board/archive?min_score=6")
    assert r.status_code == 200
    # The sort link for relevance_score proves the column is in the header.
    assert "sort=relevance_score" in r.text
