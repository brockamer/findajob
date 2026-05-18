"""Integration tests for #277 — view_prefs cascade through board routes.

Verifies the three-tier cascade: URL ?cols= > persisted view_prefs >
ColumnSpec.default_visible. Auto-save fires on every page + /rows GET
that carries allowlisted filter state; density and other unrelated
params are excluded by construction.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web import view_prefs
from findajob.web.app import create_app
from tests.conftest import ensure_view_prefs_table


@pytest.fixture
def client_and_db(tmp_path: Path) -> tuple[TestClient, Path]:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (id TEXT, fingerprint TEXT, title TEXT, company TEXT, stage TEXT, "
        "relevance_score INTEGER, fit_score REAL, probability_score REAL, interview_likelihood INTEGER, "
        "location TEXT, remote_status TEXT, known_contacts TEXT, user_notes TEXT, comp_estimate TEXT, "
        "ai_notes TEXT, url TEXT, created_at TEXT, stage_updated TEXT, prep_folder_path TEXT)"
    )
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    for fp, title, company in [
        ("fp1", "NPI PM", "Meta"),
        ("fp2", "Staff Eng", "Anthropic"),
        ("fp3", "TPM", "Meta"),
    ]:
        conn.execute(
            "INSERT INTO jobs (fingerprint, title, company, stage, relevance_score) VALUES (?, ?, ?, 'scored', 8)",
            (fp, title, company),
        )
    conn.commit()
    ensure_view_prefs_table(conn)
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path)), db


def _read_view_prefs(db: Path, tab: str) -> str | None:
    conn = sqlite3.connect(db)
    try:
        return view_prefs.load(conn, tab)
    finally:
        conn.close()


def _write_view_prefs(db: Path, tab: str, qs: str) -> None:
    conn = sqlite3.connect(db)
    try:
        view_prefs.save(conn, tab, qs)
    finally:
        conn.close()


# ── Auto-save on URL settle ─────────────────────────────────────────────


def test_page_get_with_filter_persists_to_view_prefs(client_and_db: tuple[TestClient, Path]) -> None:
    client, db = client_and_db
    r = client.get("/board/dashboard?company=meta")
    assert r.status_code == 200
    assert _read_view_prefs(db, "dashboard") == "company=meta"


def test_rows_get_with_filter_persists_to_view_prefs(client_and_db: tuple[TestClient, Path]) -> None:
    client, db = client_and_db
    r = client.get("/board/dashboard/rows?company=meta")
    assert r.status_code == 200
    assert _read_view_prefs(db, "dashboard") == "company=meta"


def test_density_param_alone_does_not_persist(client_and_db: tuple[TestClient, Path]) -> None:
    """Density is deliberately excluded from #277 — it's URL-param only.
    A page visit carrying only ?density= must not clear existing prefs
    AND must not persist density itself.
    """
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "company=meta")
    r = client.get("/board/dashboard?density=expanded", follow_redirects=False)
    # has_filter_state(parsed) is False -> cold-load redirect fires.
    assert r.status_code == 303
    # The persisted prefs are unchanged — density did not clobber them.
    assert _read_view_prefs(db, "dashboard") == "company=meta"


def test_cols_param_persists_with_url_encoding(client_and_db: tuple[TestClient, Path]) -> None:
    client, db = client_and_db
    r = client.get("/board/dashboard?cols=title,company,relevance_score")
    assert r.status_code == 200
    assert _read_view_prefs(db, "dashboard") == "cols=title%2Ccompany%2Crelevance_score"


def test_each_tab_persists_independently(client_and_db: tuple[TestClient, Path]) -> None:
    """Two tabs persist independently — clearing one leaves the other intact.

    Exercise this at the data layer rather than via two distinct route
    hits to avoid pulling in cost_log / audit_log fixture deps that
    aren't relevant to #277.
    """
    client, db = client_and_db
    client.get("/board/dashboard?company=meta")
    _write_view_prefs(db, "review", "company=anthropic")
    assert _read_view_prefs(db, "dashboard") == "company=meta"
    assert _read_view_prefs(db, "review") == "company=anthropic"
    client.post("/board/dashboard/reset-view", follow_redirects=False)
    assert _read_view_prefs(db, "dashboard") is None
    assert _read_view_prefs(db, "review") == "company=anthropic"


# ── Cold-load redirect ──────────────────────────────────────────────────


def test_cold_load_with_persisted_state_redirects(client_and_db: tuple[TestClient, Path]) -> None:
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "company=meta")
    r = client.get("/board/dashboard", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/board/dashboard?company=meta"


def test_cold_load_with_no_persisted_state_renders_defaults(client_and_db: tuple[TestClient, Path]) -> None:
    client, db = client_and_db
    r = client.get("/board/dashboard")
    assert r.status_code == 200
    # No row written either — nothing to persist.
    assert _read_view_prefs(db, "dashboard") is None


def test_cold_load_redirect_landing_renders_persisted_filters(
    client_and_db: tuple[TestClient, Path],
) -> None:
    """Full round-trip: cold load -> 303 -> follow -> filtered rows."""
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "company=meta")
    r = client.get("/board/dashboard")
    assert r.status_code == 200
    assert "NPI PM" in r.text
    assert "TPM" in r.text
    assert "Staff Eng" not in r.text


# ── URL wins over persisted ─────────────────────────────────────────────


def test_url_param_overrides_persisted_state(client_and_db: tuple[TestClient, Path]) -> None:
    """Bookmark / deep-link must surface its own state, not the persisted one."""
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "company=meta")
    r = client.get("/board/dashboard?company=anthropic", follow_redirects=False)
    # No redirect — the URL has filter state already.
    assert r.status_code == 200
    # Overwrite-on-deep-link semantics: persistence updates to URL.
    assert _read_view_prefs(db, "dashboard") == "company=anthropic"


def test_url_with_only_density_still_triggers_cold_load_redirect(
    client_and_db: tuple[TestClient, Path],
) -> None:
    """Density doesn't count as filter state for the cold-load check."""
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "company=meta")
    r = client.get("/board/dashboard?density=expanded", follow_redirects=False)
    assert r.status_code == 303
    # The redirect target carries persisted state, but DROPS density (out of scope).
    assert r.headers["location"] == "/board/dashboard?company=meta"


# ── Reset endpoint ──────────────────────────────────────────────────────


def test_reset_view_clears_persistence_and_redirects(client_and_db: tuple[TestClient, Path]) -> None:
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "company=meta")
    r = client.post("/board/dashboard/reset-view", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/board/dashboard"
    assert _read_view_prefs(db, "dashboard") is None


def test_reset_view_handles_hyphenated_url_tab(client_and_db: tuple[TestClient, Path]) -> None:
    """/board/not-selected URL must map to view_prefs key 'not_selected'."""
    client, db = client_and_db
    _write_view_prefs(db, "not_selected", "company=meta")
    r = client.post("/board/not-selected/reset-view", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/board/not-selected"
    assert _read_view_prefs(db, "not_selected") is None


def test_reset_view_unknown_tab_returns_404(client_and_db: tuple[TestClient, Path]) -> None:
    client, _ = client_and_db
    r = client.post("/board/bogus/reset-view")
    assert r.status_code == 404


def test_reset_view_idempotent_on_no_existing_row(client_and_db: tuple[TestClient, Path]) -> None:
    client, db = client_and_db
    assert _read_view_prefs(db, "dashboard") is None
    r = client.post("/board/dashboard/reset-view", follow_redirects=False)
    assert r.status_code == 303
    assert _read_view_prefs(db, "dashboard") is None
