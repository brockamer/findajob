"""Dashboard banner wiring for the filter-proposals pending count (#1055).

Tests:
- _filter_proposals_pending_count tolerates a pre-migration DB (no crash, returns 0)
- _filter_proposals_pending_count returns the correct count when rows exist
- The dashboard actually renders the widget link when pending rows are present
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from findajob.db.migrate import apply_pending

# ── unit: helper function ─────────────────────────────────────────────────────


def test_count_tolerates_missing_table():
    c = sqlite3.connect(":memory:")
    from findajob.web.routes.board import _filter_proposals_pending_count

    assert _filter_proposals_pending_count(c) == 0  # pre-migration → 0, no crash


def test_count_returns_pending(tmp_path):
    c = sqlite3.connect(tmp_path / "p.db")
    c.row_factory = sqlite3.Row
    apply_pending(c)
    c.execute("INSERT INTO filter_proposals (pattern, pattern_norm, status) VALUES ('x','x','pending')")
    c.commit()

    from findajob.web.routes.board import _filter_proposals_pending_count

    assert _filter_proposals_pending_count(c) == 1


# ── integration: dashboard route surfaces the banner ─────────────────────────


def _make_client(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from findajob import config_loader
    from findajob.onboarding import mark_complete
    from findajob.web.app import create_app

    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    apply_pending(conn)
    conn.commit()

    monkeypatch.setattr(config_loader, "_RULES_PATH", tmp_path / "prefilter_rules.yaml")
    config_loader._reset_cache()

    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    client = TestClient(create_app(companies_root=companies, db_path=db_path, base_root=tmp_path))
    return conn, client


def test_dashboard_surfaces_filter_proposals_banner(tmp_path, monkeypatch):
    conn, client = _make_client(tmp_path, monkeypatch)
    conn.execute("INSERT INTO filter_proposals (pattern, pattern_norm, status) VALUES ('x','x','pending')")
    conn.commit()
    r = client.get("/board/dashboard")
    assert r.status_code == 200
    assert "/board/filter-proposals/" in r.text  # the review link is rendered
