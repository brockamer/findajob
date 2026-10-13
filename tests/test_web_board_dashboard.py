"""Board Dashboard tab."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import init_test_db


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, relevance_score) "
        "VALUES ('jid1','fp1','https://example.com/meta-dc-ops','Senior DC Ops','Meta','test','scored',8)"
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, relevance_score) "
        "VALUES ('jid2','fp2','https://example.com/google-npi','NPI PM','Google','test','materials_drafted',9)"
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, relevance_score) "
        "VALUES ('jid3','fp3','https://example.com/acme-jr','Junior','Acme','test','scored',3)"
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_dashboard_shows_in_scope_jobs(client: TestClient) -> None:
    r = client.get("/board/dashboard")
    assert r.status_code == 200
    assert "Senior DC Ops" in r.text
    assert "NPI PM" in r.text
    # fp3 (score=3) is filtered out by score<7. Check by fingerprint — the
    # reject-reason dropdown now contains substrings like "Too Junior" for
    # every rendered row, so bare-word "Junior" is a false-positive signal.
    assert 'data-fingerprint="fp3"' not in r.text


def test_dashboard_defaults_to_compact_density(client: TestClient) -> None:
    r = client.get("/board/dashboard")
    assert r.status_code == 200
    assert "density-compact" in r.text
    # Active density button carries the filled style
    assert "bg-slate-900 text-white" in r.text


def test_dashboard_expanded_density_param(client: TestClient) -> None:
    r = client.get("/board/dashboard?density=expanded")
    assert r.status_code == 200
    assert "density-expanded" in r.text
    assert "density-compact" not in r.text


def test_dashboard_rejects_invalid_density(client: TestClient) -> None:
    """Unknown density value falls back to the default (compact)."""
    r = client.get("/board/dashboard?density=nonsense")
    assert r.status_code == 200
    assert "density-compact" in r.text
    assert "density-nonsense" not in r.text


def test_dashboard_rows_have_cell_text_wrapper_with_title(client: TestClient) -> None:
    """Each text cell wraps its content in .cell-text-wrap with a title tooltip."""
    r = client.get("/board/dashboard")
    assert "cell-text-wrap" in r.text
    # At least one cell has a title attribute populated from the row data
    assert 'title="Senior DC Ops"' in r.text


def test_dashboard_title_links_to_job_url(client: TestClient) -> None:
    """Title cell on each row hyperlinks to the original job URL, opens in new tab."""
    r = client.get("/board/dashboard")
    assert 'href="https://example.com/meta-dc-ops"' in r.text
    assert 'target="_blank"' in r.text
    assert 'rel="noopener noreferrer"' in r.text


def test_speculative_title_links_to_internal_jd_viewer(tmp_path: Path) -> None:
    """[SPEC]-prefixed rows must NOT render the speculative:// sentinel as an href.
    Regression for the title-click-goes-nowhere bug."""
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, "
        "stage, relevance_score, raw_jd_text, synthetic) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "jid-spec1",
            "spec1",
            "speculative://Anthropic/0/42",
            "[SPEC] Director of Capacity",
            "Anthropic",
            "web_speculative",
            "scored",
            7,
            "Lead capacity planning for clusters.",
            1,
        ),
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    client = TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))

    r = client.get("/board/dashboard")
    assert r.status_code == 200
    assert "speculative://" not in r.text  # sentinel must not leak as href
    assert 'href="/jobs/spec1/jd"' in r.text


def test_jd_viewer_renders_raw_jd_text(tmp_path: Path) -> None:
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, "
        "stage, raw_jd_text, synthetic) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "jid-spec1",
            "spec1",
            "speculative://Anthropic/0/42",
            "[SPEC] Director of Capacity",
            "Anthropic",
            "web_speculative",
            "scored",
            "Lead capacity planning for clusters.",
            1,
        ),
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    client = TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))

    r = client.get("/jobs/spec1/jd")
    assert r.status_code == 200
    assert "Director of Capacity" in r.text
    assert "Anthropic" in r.text
    assert "Lead capacity planning for clusters." in r.text


def test_materials_redirects_synthetic_row_to_jd_viewer(tmp_path: Path) -> None:
    """Synthetic rows with no spec_briefing_folder fall back to the JD viewer
    (the role-card description). Real (non-synthetic) rows with no prep folder
    still 404. Per #485 the synthetic-with-spec-folder path is covered
    separately by test_materials_serves_spec_folder_when_synthetic_pre_prep.
    """
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, "
        "stage, raw_jd_text, synthetic) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "jid-spec1",
            "spec1",
            "speculative://Anthropic/0/42",
            "[SPEC] Director",
            "Anthropic",
            "web_speculative",
            "scored",
            "Lead capacity.",
            1,
        ),
    )
    # Real job with no prep folder — must still 404, not redirect.
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, "
        "stage, raw_jd_text, synthetic) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("jid-real1", "real1", "https://x.test/real1", "Director", "Acme", "test", "scored", "Real JD.", 0),
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    client = TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))

    r = client.get("/materials/spec1", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/jobs/spec1/jd"

    r = client.get("/materials/real1", follow_redirects=False)
    assert r.status_code == 404


def test_materials_serves_spec_folder_when_synthetic_pre_prep(tmp_path: Path) -> None:
    """Synthetic row with a populated speculative_briefing_folder pointing to
    a real on-disk folder containing briefing.md serves that folder via the
    materials view — operator can read the deep-research brief before
    flag-for-prep (#485)."""
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    conn = sqlite3.connect(db)
    spec_folder_name = "Acme_SPECULATIVE_2026-05-07_103045"
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, "
        "stage, raw_jd_text, synthetic, speculative_briefing_folder) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "jid1",
            "spec1",
            "speculative://Acme/0/0",
            "[SPEC] Lead",
            "Acme",
            "web_speculative",
            "scored",
            "desc.",
            1,
            spec_folder_name,
        ),
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    spec_folder = companies / spec_folder_name
    spec_folder.mkdir(parents=True)
    (spec_folder / "briefing.md").write_text("# Acme deep-research briefing\n\nBody.\n")
    mark_complete(tmp_path)
    client = TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))

    r = client.get("/materials/spec1", follow_redirects=False)
    # Renders the materials folder view (200), not a 303 to JD viewer.
    assert r.status_code == 200, r.text[:300]
    # The spec briefing's bare `briefing.md` filename gets classified as
    # "Briefing (speculative)" by _classify_file — check the group label.
    assert "Briefing (speculative)" in r.text
    assert "briefing.md" in r.text


def test_jd_viewer_404_for_unknown_fingerprint(tmp_path: Path) -> None:
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    client = TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))

    r = client.get("/jobs/nope/jd")
    assert r.status_code == 404


# ── #1222: a briefing outlives the decision window ─────────────────────────


@pytest.fixture
def briefing_client(tmp_path: Path) -> TestClient:
    """Rows around the briefing link: one reset to scored with its briefing
    on disk, one reset whose folder is gone, one still briefing_ready."""
    import os
    from datetime import UTC, datetime, timedelta

    db = tmp_path / "pipeline.db"
    init_test_db(db)
    companies = tmp_path / "companies"
    companies.mkdir()

    expired = companies / "Expired_Co_Ops_2026-10-01_120000"
    expired.mkdir()
    briefing = expired / "Cand Briefing - Expired Co - Ops - 20261001-120000.md"
    briefing.write_text("# Briefing\n")
    ten_days_ago = (datetime.now(UTC) - timedelta(days=10, hours=1)).timestamp()
    os.utime(briefing, (ten_days_ago, ten_days_ago))

    fresh = companies / "Fresh_Co_Ops_2026-10-13_120000"
    fresh.mkdir()
    (fresh / "Cand Briefing - Fresh Co - Ops - 20261013-120000.md").write_text("# Briefing\n")

    reset_yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    conn = sqlite3.connect(db)
    rows = [
        ("jidE", "fpE", "Expired Co", "scored", str(expired)),
        ("jidG", "fpG", "Gone Co", "scored", str(companies / "Gone_Co_Ops_2026-09-01_120000")),
        ("jidF", "fpF", "Fresh Co", "briefing_ready", str(fresh)),
    ]
    for jid, fp, company, stage, folder in rows:
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, relevance_score, "
            "prep_folder_path, stage_updated) VALUES (?, ?, 'https://example.com/j', 'Ops', ?, 'test', ?, 8, ?, ?)",
            (jid, fp, company, stage, folder, reset_yesterday),
        )
    conn.commit()
    conn.close()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_scored_row_with_briefing_links_it_with_its_age(briefing_client: TestClient) -> None:
    """The reset row must not look like a bare scored row: it links the
    briefing, aged from the briefing file, not from the reset's stage_updated."""
    r = briefing_client.get("/board/dashboard")

    assert r.status_code == 200
    assert 'href="/materials/fpE"' in r.text
    assert "Briefing ready (10 days)" in r.text


def test_scored_row_whose_folder_is_gone_shows_no_briefing_link(briefing_client: TestClient) -> None:
    r = briefing_client.get("/board/dashboard")

    assert 'data-fingerprint="fpG"' in r.text
    assert 'href="/materials/fpG"' not in r.text


def test_briefing_ready_row_shows_briefing_link_with_age(briefing_client: TestClient) -> None:
    r = briefing_client.get("/board/dashboard")

    assert 'href="/materials/fpF"' in r.text
    assert "Briefing ready (today)" in r.text
