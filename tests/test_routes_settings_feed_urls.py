from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import init_test_db


@pytest.fixture
def feed_urls_path(tmp_path: Path) -> Path:
    return tmp_path / "config" / "feed_urls.txt"


@pytest.fixture
def client(tmp_path: Path, feed_urls_path: Path) -> TestClient:
    feed_urls_path.parent.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)  # before create_app so the onboarding guard lets us in
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_get_lists_configured_feeds(client: TestClient, feed_urls_path: Path) -> None:
    feed_urls_path.write_text("https://boards.greenhouse.io/anthropic\nhttps://jobs.lever.co/zoox  # Zoox\n")
    resp = client.get("/settings/feed-urls/")
    assert resp.status_code == 200
    assert "Anthropic" in resp.text  # greenhouse titlecased slug
    assert "Zoox" in resp.text  # lever inline comment
    assert "boards.greenhouse.io/anthropic" in resp.text


def test_get_absent_file_shows_friendly_notice(client: TestClient, feed_urls_path: Path) -> None:
    assert not feed_urls_path.exists()
    resp = client.get("/settings/feed-urls/")
    assert resp.status_code == 200
    assert "feed_urls.txt" in resp.text


def test_settings_nav_includes_feed_urls(client: TestClient) -> None:
    resp = client.get("/settings/feed-urls/")
    assert "/settings/feed-urls/" in resp.text
    assert "Feed URLs" in resp.text


def test_verify_renders_per_row_status(client: TestClient, feed_urls_path: Path) -> None:
    feed_urls_path.write_text(
        "https://boards.greenhouse.io/liveco\n"
        "https://jobs.lever.co/deadco\n"
        "https://acme.wd1.myworkdayjobs.com/careers\n"
    )

    def fake_get(url: str, **kwargs: object) -> MagicMock:
        return MagicMock(status_code=200 if "liveco" in url else 404)

    with patch("findajob.fetchers.feed_probe.requests.get", side_effect=fake_get):
        resp = client.post("/settings/feed-urls/verify")
    assert resp.status_code == 200
    assert "dead" in resp.text
    assert "404" in resp.text
    assert "200" in resp.text  # http_code span rendered (live row's hint is empty)
    assert "unsupported" in resp.text  # unsupported badge rendered for the Workday URL


def test_verify_all_unreachable_shows_offline_banner(client: TestClient, feed_urls_path: Path) -> None:
    feed_urls_path.write_text("https://boards.greenhouse.io/anyco\n")
    with patch("findajob.fetchers.feed_probe.requests.get", side_effect=requests.ConnectionError("x")):
        resp = client.post("/settings/feed-urls/verify")
    assert resp.status_code == 200
    assert "offline" in resp.text.lower()
