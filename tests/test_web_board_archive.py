"""Archive tab: pagination, HTMX sentinel."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import ensure_view_prefs_table


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "relevance_score INTEGER, fit_score REAL, probability_score REAL, location TEXT, "
        "remote_status TEXT, source TEXT, url TEXT, created_at TEXT, stage_updated TEXT, "
        "user_notes TEXT)"
    )
    for i in range(150):
        conn.execute(
            "INSERT INTO jobs (fingerprint, title, company, stage, relevance_score, "
            "fit_score, created_at) "
            "VALUES (?, ?, ?, 'scored', ?, ?, '2026-01-01')",
            (f"fp-{i:03}", f"Role {i}", f"Co {i}", 1 + i % 10, 1.0 + i % 10),
        )
    ensure_view_prefs_table(conn)
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
        "remote_status TEXT, source TEXT, url TEXT, created_at TEXT, stage_updated TEXT, "
        "user_notes TEXT)"
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
    ensure_view_prefs_table(conn)
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_archive_min_score_filters_rows(scored_client: TestClient) -> None:
    """#238: /board/archive?relevance_score_min=6 hides rows with relevance_score < 6."""
    r = scored_client.get("/board/archive?relevance_score_min=6")
    assert r.status_code == 200
    assert "Six Alpha" in r.text
    assert "Six Bravo" in r.text
    assert "Nine Applied" in r.text  # score=9 passes filter
    assert "Three Only" not in r.text  # score=3 filtered out


def test_archive_max_score_filters_rows(scored_client: TestClient) -> None:
    """#238: /board/archive?relevance_score_max=5 hides rows with relevance_score > 5."""
    r = scored_client.get("/board/archive?relevance_score_max=5")
    assert r.status_code == 200
    assert "Three Only" in r.text  # score=3 passes
    assert "Six Alpha" not in r.text
    assert "Nine Applied" not in r.text


def test_archive_promote_button_on_scored_rows(scored_client: TestClient) -> None:
    """#238: rows at stage='scored' render a Promote button (POST to /board/jobs/.../promote)."""
    r = scored_client.get("/board/archive?relevance_score_min=6")
    assert r.status_code == 200
    # Promote button for fp-6a (stage=scored)
    assert "/board/jobs/fp-6a/promote" in r.text


def test_archive_no_promote_button_on_non_scored_rows(scored_client: TestClient) -> None:
    """#238: rows at stages other than 'scored' do NOT render a Promote button."""
    r = scored_client.get("/board/archive?relevance_score_min=6")
    assert r.status_code == 200
    # fp-9 is stage='applied' — no Promote button (already applied; promoting makes no sense)
    assert "/board/jobs/fp-9/promote" not in r.text


def test_archive_rows_htmx_respects_min_score(scored_client: TestClient) -> None:
    """#238: HTMX partial /board/archive/rows also filters on relevance_score_min for consistency."""
    r = scored_client.get("/board/archive/rows?offset=0&relevance_score_min=6")
    assert r.status_code == 200
    assert "Six Alpha" in r.text
    assert "Three Only" not in r.text


def test_archive_shows_relevance_score_column(scored_client: TestClient) -> None:
    """#238: relevance_score is queried and rendered so operators can sort/filter by it."""
    r = scored_client.get("/board/archive?min_score=6")
    assert r.status_code == 200
    # The sort link for relevance_score proves the column is in the header.
    assert "sort=relevance_score" in r.text


@pytest.fixture
def reject_mix_client(tmp_path: Path) -> TestClient:
    """Fixture with mixed scored + rejected rows for #281 default-exclude tests."""
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (id TEXT, fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "relevance_score INTEGER, fit_score REAL, probability_score REAL, location TEXT, "
        "remote_status TEXT, source TEXT, url TEXT, created_at TEXT, stage_updated TEXT, "
        "user_notes TEXT)"
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage, relevance_score, created_at) "
        "VALUES ('id-s','fp-s','Scored Job','CoS','scored',6,'2026-04-24')"
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage, relevance_score, created_at) "
        "VALUES ('id-r','fp-r','Rejected Job','CoR','rejected',5,'2026-04-24')"
    )
    ensure_view_prefs_table(conn)
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_archive_default_excludes_rejected_rows(reject_mix_client: TestClient) -> None:
    """#281: Archive default view excludes stage='rejected' so the score-5/6 triage
    workflow doesn't re-show already-rejected rows."""
    r = reject_mix_client.get("/board/archive")
    assert r.status_code == 200
    assert "Scored Job" in r.text
    assert "Rejected Job" not in r.text


def test_archive_explicit_stage_rejected_surfaces_rejected_rows(reject_mix_client: TestClient) -> None:
    """#281: ?stage=rejected explicitly overrides the default exclusion."""
    r = reject_mix_client.get("/board/archive?stage=rejected")
    assert r.status_code == 200
    assert "Rejected Job" in r.text
    assert "Scored Job" not in r.text


def test_archive_explicit_stage_scored_still_excludes_rejected(reject_mix_client: TestClient) -> None:
    """#281: ?stage=scored is the operator's filter — should hide rejected rows naturally,
    not because of the default-exclude (this verifies the default doesn't double-apply)."""
    r = reject_mix_client.get("/board/archive?stage=scored")
    assert r.status_code == 200
    assert "Scored Job" in r.text
    assert "Rejected Job" not in r.text


def test_archive_rows_htmx_default_excludes_rejected(reject_mix_client: TestClient) -> None:
    """#281: HTMX partial /board/archive/rows shares the same default-exclude behavior."""
    r = reject_mix_client.get("/board/archive/rows?offset=0")
    assert r.status_code == 200
    assert "Scored Job" in r.text
    assert "Rejected Job" not in r.text


def test_archive_scored_row_renders_hybrid_reject_affordance(scored_client: TestClient) -> None:
    """#281: scored-stage rows render both the ✕ single-click button (default reason)
    and the ▾ alternate-reason dropdown alongside Promote."""
    r = scored_client.get("/board/archive?relevance_score_min=6")
    assert r.status_code == 200
    # Single-click ✕ posts to /reject with the Not-relevant default reason
    assert "/board/jobs/fp-6a/reject" in r.text
    assert '"reason":"Not relevant"' in r.text
    # ▾ dropdown lists other reasons; "Not relevant" is excluded (it's the ✕ path)
    assert '<option value="Not relevant">' not in r.text
    # At least one alternate reason from the default list shows up in the dropdown
    assert '<option value="Skills Mismatch">' in r.text


def test_archive_non_scored_row_does_not_render_reject_affordance(scored_client: TestClient) -> None:
    """#281: the new reject affordance is on the scored branch only — applied rows
    keep their existing non-scored cell behavior (dash, in this fixture)."""
    r = scored_client.get("/board/archive?relevance_score_min=6")
    assert r.status_code == 200
    assert "/board/jobs/fp-9/reject" not in r.text
