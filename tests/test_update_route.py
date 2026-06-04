# tests/test_update_route.py
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web import watchtower
from findajob.web.app import create_app
from tests.conftest import init_test_db


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(
        create_app(companies_root=companies, db_path=db, base_root=tmp_path),
        follow_redirects=False,
    )


def test_update_now_redirects_with_success(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(watchtower, "trigger_watchtower_update", lambda: True)
    resp = client.post("/update/now")
    assert resp.status_code == 303
    assert "update_triggered=1" in resp.headers["location"]


def test_update_now_redirects_with_failure(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(watchtower, "trigger_watchtower_update", lambda: False)
    resp = client.post("/update/now")
    assert resp.status_code == 303
    assert "update_failed=1" in resp.headers["location"]
