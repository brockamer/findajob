"""Board Dashboard tab."""

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
        "CREATE TABLE jobs (id TEXT, fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "relevance_score INTEGER, fit_score REAL, probability_score REAL, interview_likelihood INTEGER, "
        "location TEXT, remote_status TEXT, known_contacts TEXT, user_notes TEXT, comp_estimate TEXT, "
        "ai_notes TEXT, created_at TEXT, stage_updated TEXT, url TEXT, prep_folder_path TEXT)"
    )
    # #234 — /board/dashboard now LEFT JOINs audit_log for the company-history cell.
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    conn.execute(
        "INSERT INTO jobs (fingerprint, title, company, stage, relevance_score, url) "
        "VALUES ('fp1','Senior DC Ops','Meta','scored',8,'https://example.com/meta-dc-ops')"
    )
    conn.execute(
        "INSERT INTO jobs (fingerprint, title, company, stage, relevance_score, url) "
        "VALUES ('fp2','NPI PM','Google','materials_drafted',9,'https://example.com/google-npi')"
    )
    conn.execute(
        "INSERT INTO jobs (fingerprint, title, company, stage, relevance_score, url) "
        "VALUES ('fp3','Junior','Acme','scored',3,'https://example.com/acme-jr')"
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
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (id TEXT, fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "relevance_score INTEGER, fit_score REAL, probability_score REAL, interview_likelihood INTEGER, "
        "location TEXT, remote_status TEXT, known_contacts TEXT, user_notes TEXT, comp_estimate TEXT, "
        "ai_notes TEXT, raw_jd_text TEXT, created_at TEXT, stage_updated TEXT, url TEXT, "
        "prep_folder_path TEXT, synthetic INTEGER DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    conn.execute(
        "INSERT INTO jobs (fingerprint, title, company, stage, relevance_score, url, raw_jd_text, synthetic) "
        "VALUES ('spec1','[SPEC] Director of Capacity','Anthropic','scored',7,"
        "'speculative://Anthropic/0/42','Lead capacity planning for clusters.', 1)"
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
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (id TEXT, fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "relevance_score INTEGER, fit_score REAL, probability_score REAL, interview_likelihood INTEGER, "
        "location TEXT, remote_status TEXT, known_contacts TEXT, user_notes TEXT, comp_estimate TEXT, "
        "ai_notes TEXT, raw_jd_text TEXT, created_at TEXT, stage_updated TEXT, url TEXT, "
        "prep_folder_path TEXT, synthetic INTEGER DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    conn.execute(
        "INSERT INTO jobs (fingerprint, title, company, stage, raw_jd_text, synthetic) "
        "VALUES ('spec1','[SPEC] Director of Capacity','Anthropic','scored',"
        "'Lead capacity planning for clusters.', 1)"
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
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (id TEXT, fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "raw_jd_text TEXT, prep_folder_path TEXT, synthetic INTEGER DEFAULT 0, "
        "speculative_briefing_folder TEXT)"
    )
    conn.execute(
        "INSERT INTO jobs (fingerprint, title, company, stage, raw_jd_text, synthetic) "
        "VALUES ('spec1','[SPEC] Director','Anthropic','scored','Lead capacity.', 1)"
    )
    # Real job with no prep folder — must still 404, not redirect.
    conn.execute(
        "INSERT INTO jobs (fingerprint, title, company, stage, raw_jd_text, synthetic) "
        "VALUES ('real1','Director','Acme','scored','Real JD.', 0)"
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
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (id TEXT, fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "stage_updated TEXT, raw_jd_text TEXT, prep_folder_path TEXT, synthetic INTEGER DEFAULT 0, "
        "speculative_briefing_folder TEXT)"
    )
    spec_folder_name = "Acme_SPECULATIVE_2026-05-07_103045"
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage, raw_jd_text, "
        "synthetic, speculative_briefing_folder) "
        "VALUES ('jid1','spec1','[SPEC] Lead','Acme','scored','desc.', 1, ?)",
        (spec_folder_name,),
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
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (id TEXT, fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "raw_jd_text TEXT, synthetic INTEGER DEFAULT 0)"
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    client = TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))

    r = client.get("/jobs/nope/jd")
    assert r.status_code == 404
