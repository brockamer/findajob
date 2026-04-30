"""Operator-mode visual cue + Admin nav link tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app


@pytest.fixture
def app_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def make(*, operator_mode: bool):
        if operator_mode:
            monkeypatch.setenv("FINDAJOB_OPERATOR_MODE", "1")
        else:
            monkeypatch.delenv("FINDAJOB_OPERATOR_MODE", raising=False)
        companies = tmp_path / "companies"
        companies.mkdir(exist_ok=True)
        db = tmp_path / "pipeline.db"
        db.touch()
        return create_app(companies_root=companies, db_path=db, base_root=tmp_path)

    return make


def test_nav_default_color_when_operator_mode_off(app_factory) -> None:
    app = app_factory(operator_mode=False)
    client = TestClient(app)
    r = client.get("/docs/")
    assert r.status_code == 200
    assert '<nav class="bg-slate-800' in r.text
    assert '<nav class="bg-rose-700' not in r.text
    assert 'href="/admin/stacks/"' not in r.text


def test_nav_red_bar_when_operator_mode_on(app_factory) -> None:
    app = app_factory(operator_mode=True)
    client = TestClient(app)
    r = client.get("/docs/")
    assert r.status_code == 200
    assert '<nav class="bg-rose-700' in r.text  # nav element itself, not random buttons
    assert "Admin" in r.text
    assert 'href="/admin/stacks/"' in r.text


def test_admin_link_points_to_stacks(app_factory) -> None:
    app = app_factory(operator_mode=True)
    client = TestClient(app)
    r = client.get("/docs/")
    assert r.status_code == 200
    assert 'href="/admin/stacks/"' in r.text
