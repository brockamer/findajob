"""Unit tests for the web viewer routes."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app


@pytest.fixture
def companies_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "pipeline.db"
    conn = sqlite3.connect(p)
    conn.execute(
        """CREATE TABLE jobs (
            fingerprint TEXT PRIMARY KEY,
            prep_folder_path TEXT,
            stage TEXT,
            title TEXT,
            company TEXT,
            score INTEGER,
            created_at TEXT,
            applied_date TEXT
        )"""
    )
    conn.commit()
    conn.close()
    return p


@pytest.fixture
def client(companies_root: Path, db_path: Path) -> TestClient:
    app = create_app(companies_root=companies_root, db_path=db_path)
    return TestClient(app)


def test_healthz_returns_ok(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.text == "ok"
