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


def test_folder_route_lists_files(
    client: TestClient, companies_root: Path, db_path: Path
) -> None:
    folder = companies_root / "Meta_SWE_2026-04-20_120000"
    folder.mkdir()
    (folder / "tailored_resume.docx").write_bytes(b"docx-bytes")
    (folder / "cover_letter.md").write_text("# Hello\n")

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (fingerprint, prep_folder_path, stage, title, company) "
        "VALUES (?, ?, 'materials_drafted', 'SWE', 'Meta')",
        ("fp-1", str(folder)),
    )
    conn.commit()
    conn.close()

    r = client.get("/materials/fp-1")
    assert r.status_code == 200
    assert "tailored_resume.docx" in r.text
    assert "cover_letter.md" in r.text


def test_folder_route_404_on_unknown_fingerprint(client: TestClient) -> None:
    r = client.get("/materials/fp-does-not-exist")
    assert r.status_code == 404
