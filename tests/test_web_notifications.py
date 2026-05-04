"""/notifications/ page + /notifications/{id}/read + /notifications/badge (#440)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app


def _build_db(db_path: Path) -> sqlite3.Connection:
    """Build a minimal pipeline.db with the notifications schema #440 ships.

    Mirrors the relevant part of `scripts/init_db.py` — kept in sync because
    web tests must hit the same shape the production DB has.
    """
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE jobs (id TEXT PRIMARY KEY, stage TEXT);
        CREATE TABLE notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sent_at TEXT NOT NULL DEFAULT (datetime('now')),
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'default',
            tags TEXT,
            delivery_status TEXT NOT NULL DEFAULT 'sent',
            delivery_error TEXT,
            cta_url TEXT,
            read_at TEXT
        );
        """
    )
    conn.commit()
    return conn


def _seed(conn: sqlite3.Connection, rows: list[dict]) -> None:
    for r in rows:
        conn.execute(
            """
            INSERT INTO notifications
                (sent_at, kind, title, body, priority, tags, delivery_status,
                 delivery_error, cta_url, read_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r.get("sent_at", "2026-05-04 12:00:00"),
                r["kind"],
                r["title"],
                r["body"],
                r.get("priority", "default"),
                r.get("tags"),
                r.get("delivery_status", "sent"),
                r.get("delivery_error"),
                r.get("cta_url"),
                r.get("read_at"),
            ),
        )
    conn.commit()


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    conn = _build_db(db)
    _seed(
        conn,
        [
            {"kind": "daily_stats", "title": "Morning summary", "body": "5 new ranked"},
            {"kind": "apply_reminder", "title": "Apply!", "body": "today's nudge"},
            {
                "kind": "health_check",
                "title": "Health",
                "body": "warning",
                "delivery_status": "failed",
                "delivery_error": "ntfy 503",
            },
            {"kind": "scoreboard", "title": "Scoreboard", "body": "weekly", "read_at": "2026-05-04 12:30:00"},
        ],
    )
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_index_renders_recent_first(client: TestClient) -> None:
    r = client.get("/notifications/")
    assert r.status_code == 200
    assert "Notifications" in r.text
    assert "Morning summary" in r.text
    assert "Apply!" in r.text
    # Read row still appears (default = all states)
    assert "Scoreboard" in r.text
    # 3 unread out of 4 → header shows count
    assert "3</span> unread" in r.text or "3 unread" in r.text or ">3<" in r.text


def test_index_filter_unread(client: TestClient) -> None:
    r = client.get("/notifications/?read=unread")
    assert r.status_code == 200
    assert "Morning summary" in r.text
    # The read row's body ("weekly") shouldn't appear in the unread list — but
    # "Scoreboard" itself shows up in the kind filter dropdown, so test on body.
    assert "weekly" not in r.text


def test_index_filter_by_kind(client: TestClient) -> None:
    r = client.get("/notifications/?kind=health_check")
    assert r.status_code == 200
    assert "Health" in r.text
    assert "Morning summary" not in r.text


def test_failed_delivery_renders_warning(client: TestClient) -> None:
    r = client.get("/notifications/?kind=health_check")
    assert "ntfy delivery failed" in r.text


def test_mark_read_idempotent(client: TestClient) -> None:
    # First mark — flips read_at
    r1 = client.post("/notifications/1/read", follow_redirects=False)
    assert r1.status_code == 303

    # Badge now shows 2 unread (was 3)
    r_badge = client.get("/notifications/badge")
    assert ">2<" in r_badge.text

    # Second mark on same id — still 200/303, badge unchanged
    r2 = client.post("/notifications/1/read", follow_redirects=False)
    assert r2.status_code == 303
    r_badge2 = client.get("/notifications/badge")
    assert ">2<" in r_badge2.text


def test_mark_read_htmx_returns_row_fragment(client: TestClient) -> None:
    r = client.post("/notifications/2/read", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert 'id="notif-row-2"' in r.text
    # The "Mark read" button is gone for read rows
    assert "Mark read" not in r.text or "read 20" in r.text


def test_mark_all_read(client: TestClient) -> None:
    r = client.post("/notifications/mark-all-read", follow_redirects=False)
    assert r.status_code == 303

    r_badge = client.get("/notifications/badge")
    assert ">0<" not in r_badge.text  # badge collapses to empty span at 0
    assert "nav-notif-badge" in r_badge.text


def test_badge_empty_when_no_unread(tmp_path: Path) -> None:
    db = tmp_path / "pipeline.db"
    conn = _build_db(db)
    _seed(conn, [{"kind": "daily_stats", "title": "x", "body": "y", "read_at": "2026-05-04 12:30:00"}])
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    c = TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))

    r = c.get("/notifications/badge")
    assert r.status_code == 200
    # Empty badge = no number rendered
    assert "<span" in r.text  # span shell for HTMX swap target
    assert ">0<" not in r.text


def test_empty_state_with_no_rows(tmp_path: Path) -> None:
    db = tmp_path / "pipeline.db"
    _build_db(db).close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    c = TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))

    r = c.get("/notifications/")
    assert r.status_code == 200
    assert "No notifications yet" in r.text
    # Empty state points to /config/ (#440 AC)
    assert "/config/" in r.text


def test_nav_includes_bell_icon(client: TestClient) -> None:
    """The bell + badge surface on every page that includes _nav.html."""
    r = client.get("/notifications/")
    assert "🔔" in r.text
    assert 'id="nav-notif-badge"' in r.text
    assert "/notifications/badge" in r.text
