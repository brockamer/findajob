"""Tests for the spend-ceiling dashboard banner (#671).

Banner appears on /board/dashboard when config/spend_ceiling.txt is absent
AND the `spend_ceiling_banner_dismissed` cookie is not set.

Tests exercise the real load_spend_ceiling() codepath (not a mock) by
writing / omitting the actual config/spend_ceiling.txt file in a tmp
base_root, per feedback_test_real_codepath_when_extracting.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob import config_loader
from findajob.onboarding import mark_complete
from findajob.web.app import create_app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Minimal app with migration-applied DB, onboarding complete.

    The config_loader._SPEND_CEILING_PATH is pointed at
    tmp_path/config/spend_ceiling.txt so that creating or removing that file
    controls what load_spend_ceiling() returns in this test session.
    The active_sources registry path is similarly isolated so the
    active-sources banner doesn't interfere.
    """
    from findajob.db.migrate import apply_pending
    from findajob.fetchers.adapters import registry

    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    try:
        apply_pending(conn)
    finally:
        conn.close()

    companies = tmp_path / "companies"
    companies.mkdir()

    # Point ceiling path at tmp location (file absent by default).
    ceiling_path = tmp_path / "config" / "spend_ceiling.txt"
    ceiling_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_loader, "_SPEND_CEILING_PATH", ceiling_path)

    # Isolate active_sources so its banner never fires in these tests.
    active_path = tmp_path / "config" / "active_sources.txt"
    active_path.write_text("jobs-api14\n")
    monkeypatch.setattr(registry, "_active_sources_path", lambda: active_path)
    monkeypatch.setattr(registry, "_onboarding_complete_path", lambda: tmp_path / "data" / ".onboarding-complete")

    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db_path, base_root=tmp_path))


def _ceiling_file(tmp_path: Path) -> Path:
    return tmp_path / "config" / "spend_ceiling.txt"


# ── banner present when ceiling absent ───────────────────────────────────────


def test_banner_appears_when_ceiling_absent(client: TestClient, tmp_path: Path) -> None:
    """No spend_ceiling.txt → banner surfaces on /board/dashboard."""
    assert not _ceiling_file(tmp_path).exists()
    resp = client.get("/board/dashboard")
    assert resp.status_code == 200
    assert "/settings/spend-ceiling/" in resp.text
    assert "No monthly LLM spend ceiling" in resp.text


# ── banner absent when ceiling is set ────────────────────────────────────────


def test_banner_absent_when_ceiling_set(client: TestClient, tmp_path: Path) -> None:
    """When spend_ceiling.txt contains a value, banner is suppressed."""
    _ceiling_file(tmp_path).write_text("50.00\n")
    resp = client.get("/board/dashboard")
    assert resp.status_code == 200
    assert "No monthly LLM spend ceiling" not in resp.text


# ── banner absent when dismissed cookie set ───────────────────────────────────


def test_banner_absent_when_dismissed_cookie(client: TestClient, tmp_path: Path) -> None:
    """spend_ceiling_banner_dismissed=1 cookie suppresses even when file absent."""
    assert not _ceiling_file(tmp_path).exists()
    # TestClient cookies must be set on the client instance (not per-request)
    client.cookies.set("spend_ceiling_banner_dismissed", "1")
    resp = client.get("/board/dashboard")
    assert resp.status_code == 200
    assert "No monthly LLM spend ceiling" not in resp.text


# ── dismiss redirect sets cookie ─────────────────────────────────────────────


def test_dismiss_redirect_sets_cookie(client: TestClient, tmp_path: Path) -> None:
    """?dismiss_spend_ceiling_banner=1 redirects and sets the cookie."""
    assert not _ceiling_file(tmp_path).exists()
    resp = client.get("/board/dashboard?dismiss_spend_ceiling_banner=1", follow_redirects=False)
    assert resp.status_code == 303
    assert "spend_ceiling_banner_dismissed" in resp.headers.get("set-cookie", "")
