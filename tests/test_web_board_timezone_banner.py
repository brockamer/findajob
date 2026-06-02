"""Dashboard timezone banner (#981): shown when data/timezone differs from the
active TZ (a restart is needed to apply the pick); dismissible like the other
dashboard banners; auto-clears once TZ matches (post-restart)."""

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
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)  # creates tmp_path/data/ + sentinel
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def _write_tz(tmp_path: Path, zone: str) -> None:
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "timezone").write_text(zone + "\n", encoding="utf-8")


def test_banner_shows_when_pick_differs_from_active(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TZ", "America/New_York")
    _write_tz(tmp_path, "Asia/Tokyo")
    r = client.get("/board/dashboard")
    assert r.status_code == 200
    assert "Restart to apply your timezone" in r.text
    assert "Asia/Tokyo" in r.text


def test_banner_hidden_when_pick_matches_active(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    _write_tz(tmp_path, "Asia/Tokyo")
    r = client.get("/board/dashboard")
    assert "Restart to apply your timezone" not in r.text


def test_banner_hidden_when_no_pick(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TZ", "America/New_York")
    r = client.get("/board/dashboard")
    assert "Restart to apply your timezone" not in r.text


def test_dismiss_sets_cookie_and_hides_banner(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TZ", "America/New_York")
    _write_tz(tmp_path, "Asia/Tokyo")
    r = client.get("/board/dashboard?dismiss_timezone_banner=1", follow_redirects=False)
    assert r.status_code == 303
    assert "timezone_banner_dismissed" in r.headers.get("set-cookie", "")
    r2 = client.get("/board/dashboard")
    assert "Restart to apply your timezone" not in r2.text
