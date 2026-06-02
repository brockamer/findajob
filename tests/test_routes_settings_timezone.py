"""Tests for GET + POST /settings/timezone/ (#988).

Covers:
- GET shows the current active TZ and renders the IANA zone picker
- GET surfaces a picked-but-pending zone when data/timezone differs from TZ
- POST with a valid zone writes data/timezone (round-trips via read_timezone_file)
- POST with an invalid zone returns an error and writes nothing (no partial write)
- POST with a blank zone returns an error
- Write goes through timeutil.write_timezone_file's atomic os.replace
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.timeutil import read_timezone_file
from findajob.web.app import create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """Minimal app with a real (migration-applied) DB and onboarding complete."""
    from findajob.db.migrate import apply_pending

    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    try:
        apply_pending(conn)
    finally:
        conn.close()

    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db_path, base_root=tmp_path))


def _tz_file(tmp_path: Path) -> Path:
    return tmp_path / "data" / "timezone"


# ── GET /settings/timezone/ ───────────────────────────────────────────────────


def test_get_shows_active_tz(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Page renders and shows the currently active TZ."""
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    resp = client.get("/settings/timezone/")
    assert resp.status_code == 200
    assert "America/Los_Angeles" in resp.text


def test_get_renders_zone_picker_options(client: TestClient) -> None:
    """The IANA zone picker lists real zones to choose from."""
    resp = client.get("/settings/timezone/")
    assert resp.status_code == 200
    assert "Europe/Berlin" in resp.text
    assert "Asia/Tokyo" in resp.text


def test_get_surfaces_pending_pick(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A picked zone different from the active TZ shows as pending (restart needed)."""
    monkeypatch.setenv("TZ", "UTC")
    (tmp_path / "data").mkdir(exist_ok=True)
    _tz_file(tmp_path).write_text("Asia/Tokyo\n", encoding="utf-8")
    resp = client.get("/settings/timezone/")
    assert resp.status_code == 200
    assert "Asia/Tokyo" in resp.text


# ── POST /settings/timezone/ ──────────────────────────────────────────────────


def test_post_valid_zone_writes_file(client: TestClient, tmp_path: Path) -> None:
    """A valid IANA zone is written to data/timezone and round-trips."""
    resp = client.post("/settings/timezone/", data={"timezone": "Europe/Berlin"})
    assert resp.status_code == 200
    assert read_timezone_file(tmp_path) == "Europe/Berlin"


def test_post_valid_zone_reports_restart_to_apply(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Picking a zone different from active TZ surfaces the restart-to-apply affordance."""
    monkeypatch.setenv("TZ", "UTC")
    resp = client.post("/settings/timezone/", data={"timezone": "Asia/Tokyo"})
    assert resp.status_code == 200
    assert "restart" in resp.text.lower()


def test_post_invalid_zone_returns_error_no_write(client: TestClient, tmp_path: Path) -> None:
    """Garbage input → error partial, nothing written."""
    resp = client.post("/settings/timezone/", data={"timezone": "Not/AZone"})
    assert resp.status_code == 200
    assert "valid" in resp.text.lower() or "error" in resp.text.lower()
    assert read_timezone_file(tmp_path) is None


def test_post_invalid_zone_preserves_existing(client: TestClient, tmp_path: Path) -> None:
    """A bad submission must not clobber an already-valid data/timezone."""
    (tmp_path / "data").mkdir(exist_ok=True)
    _tz_file(tmp_path).write_text("Asia/Tokyo\n", encoding="utf-8")
    resp = client.post("/settings/timezone/", data={"timezone": "Not/AZone"})
    assert resp.status_code == 200
    assert read_timezone_file(tmp_path) == "Asia/Tokyo"


def test_post_blank_zone_returns_error(client: TestClient, tmp_path: Path) -> None:
    """Empty submission → error, nothing written."""
    resp = client.post("/settings/timezone/", data={"timezone": "   "})
    assert resp.status_code == 200
    assert read_timezone_file(tmp_path) is None


# ── Shared settings sub-nav (#988) ────────────────────────────────────────────


def test_settings_subnav_links_all_pages(client: TestClient) -> None:
    """Every /settings/ page is reachable from the timezone page's sub-nav."""
    resp = client.get("/settings/timezone/")
    assert resp.status_code == 200
    for href in (
        "/settings/reject-reasons/",
        "/settings/active-sources/",
        "/settings/excluded-employers/",
        "/settings/connections/",
        "/settings/spend-ceiling/",
        "/settings/gemini/",
        "/settings/backup/",
    ):
        assert href in resp.text, f"settings sub-nav missing link to {href}"


def test_existing_settings_page_links_to_timezone(client: TestClient) -> None:
    """The shared sub-nav is included on existing settings pages too (not just
    the new one), so timezone is reachable from anywhere in /settings/."""
    resp = client.get("/settings/reject-reasons/")
    assert resp.status_code == 200
    assert "/settings/timezone/" in resp.text


def test_post_uses_atomic_write(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Write goes through os.replace (atomic), targeting data/timezone."""
    import os

    rename_calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def _spy(src, dst):  # type: ignore[no-untyped-def]
        rename_calls.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _spy)
    client.post("/settings/timezone/", data={"timezone": "Europe/Berlin"})
    assert rename_calls, "atomic write must use os.replace"
    _, dst = rename_calls[-1]
    assert dst == str(_tz_file(tmp_path))
