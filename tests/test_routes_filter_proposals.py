"""Route tests for the /board/filter-proposals/ surface (#1055).

Covers index render, apply happy path, apply danger→confirm firewall,
and skip. Built on the same apply_pending-based fixture other route tests
use, with the _RULES_PATH monkeypatch from the lib-level apply tests so
add_prefilter_title_pattern can write without touching real config.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from findajob import config_loader
from findajob.db.migrate import apply_pending
from findajob.onboarding import mark_complete
from findajob.web.app import create_app


def _make_client(tmp_path: Path, monkeypatch) -> tuple[sqlite3.Connection, TestClient]:
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


def _seed(conn: sqlite3.Connection, pattern: str = r"\bfleet\s+readiness\b") -> int:
    cur = conn.execute(
        "INSERT INTO filter_proposals (pattern, pattern_norm, category, status, preview_count, preview_danger_count) "
        "VALUES (?, ?, 'auto_added', 'pending', 2, 1)",
        (pattern, pattern),
    )
    conn.commit()
    return cur.lastrowid


# ── index ─────────────────────────────────────────────────────────────────────


def test_index_lists_pending(tmp_path, monkeypatch):
    conn, client = _make_client(tmp_path, monkeypatch)
    _seed(conn)
    r = client.get("/board/filter-proposals/")
    assert r.status_code == 200
    assert "fleet" in r.text


# ── apply ─────────────────────────────────────────────────────────────────────


def test_apply_writes_rule(tmp_path, monkeypatch):
    conn, client = _make_client(tmp_path, monkeypatch)
    pid = _seed(conn)
    r = client.post(
        f"/board/filter-proposals/{pid}/apply",
        data={"pattern": r"\bfleet\s+readiness\b"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert (
        conn.execute(
            "SELECT status FROM filter_proposals WHERE id=?", (pid,)
        ).fetchone()["status"]
        == "applied"
    )


def test_apply_danger_shows_confirm_then_applies(tmp_path, monkeypatch):
    conn, client = _make_client(tmp_path, monkeypatch)
    pid = _seed(conn)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, source, title, company, relevance_score, scored_by, stage) "
        "VALUES ('j1','fp1','http://x/1','test','Fleet Readiness Director','Beta',8,'llm','scored')"
    )
    conn.commit()

    # First apply → danger check fires, returns confirm prompt
    r = client.post(
        f"/board/filter-proposals/{pid}/apply",
        data={"pattern": r"\bfleet\s+readiness\b"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "Apply anyway" in r.text
    assert (
        conn.execute(
            "SELECT status FROM filter_proposals WHERE id=?", (pid,)
        ).fetchone()["status"]
        == "pending"
    )

    # Second apply with confirm=1 → actually applies
    r2 = client.post(
        f"/board/filter-proposals/{pid}/apply",
        data={"pattern": r"\bfleet\s+readiness\b", "confirm": "1"},
        headers={"HX-Request": "true"},
    )
    assert r2.status_code == 200
    assert (
        conn.execute(
            "SELECT status FROM filter_proposals WHERE id=?", (pid,)
        ).fetchone()["status"]
        == "applied"
    )


# ── skip ──────────────────────────────────────────────────────────────────────


def test_skip(tmp_path, monkeypatch):
    conn, client = _make_client(tmp_path, monkeypatch)
    pid = _seed(conn)
    r = client.post(
        f"/board/filter-proposals/{pid}/skip",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert (
        conn.execute(
            "SELECT status FROM filter_proposals WHERE id=?", (pid,)
        ).fetchone()["status"]
        == "skipped"
    )


# ── applied section + revert ──────────────────────────────────────────────────


def test_applied_section_and_revert(tmp_path, monkeypatch):
    conn, client = _make_client(tmp_path, monkeypatch)
    cur = conn.execute(
        "INSERT INTO filter_proposals (pattern, pattern_norm, category, status, affected_jobs) "
        "VALUES (?, ?, 'auto_added', 'applied', '[]')",
        (r"\bfleet\s+readiness\b", r"\bfleet\s+readiness\b"),
    )
    conn.commit()
    pid = cur.lastrowid
    # Applied section renders with a Revert button.
    r = client.get("/board/filter-proposals/")
    assert r.status_code == 200 and "Applied rules" in r.text and "Revert" in r.text
    # Revert works via the route.
    r2 = client.post(f"/board/filter-proposals/{pid}/revert", headers={"HX-Request": "true"})
    assert r2.status_code == 200
    assert conn.execute("SELECT status FROM filter_proposals WHERE id=?", (pid,)).fetchone()["status"] == "reverted"


def test_apply_error_is_html_escaped(tmp_path, monkeypatch):
    conn, client = _make_client(tmp_path, monkeypatch)
    pid = _seed(conn)
    # An invalid regex containing HTML must come back escaped, not raw.
    r = client.post(
        f"/board/filter-proposals/{pid}/apply",
        data={"pattern": "<img src=x>["},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "<img src=x>" not in r.text  # escaped
    assert "&lt;img" in r.text
