"""End-to-end integration tests for the materials viewer.

Spins up a FastAPI TestClient against a tmpdir `companies/` tree and a
scratch SQLite. Validates all routes together.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app


@pytest.fixture
def world(tmp_path: Path) -> dict:
    companies = tmp_path / "companies"
    companies.mkdir()

    active = companies / "Meta_SWE_2026-04-20_120000"
    active.mkdir()
    (active / "tailored_resume.docx").write_bytes(b"PK\x03\x04fake")
    (active / "cover_letter.md").write_text("# Cover\n\nBody.\n")

    applied = companies / "_applied" / "Google_PM_2026-04-15_100000"
    applied.mkdir(parents=True)
    (applied / "notes.txt").write_text("applied on 2026-04-15\n")

    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT DEFAULT '',
    source TEXT NOT NULL,
    stage TEXT DEFAULT 'discovered',
    stage_updated TEXT,
    prep_folder_path TEXT,
    fit_score REAL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    synthetic INTEGER NOT NULL DEFAULT 0
)
"""
    )
    cols = "id, fingerprint, url, title, company, source, stage, prep_folder_path, fit_score, created_at, stage_updated"
    conn.executemany(
        f"INSERT INTO jobs ({cols}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "fp-active",
                "fp-active",
                "https://x/a",
                "SWE",
                "Meta",
                "test",
                "materials_drafted",
                str(active),
                8.0,
                "2026-04-20",
                None,
            ),
            (
                "fp-applied",
                "fp-applied",
                "https://x/b",
                "PM",
                "Google",
                "test",
                "applied",
                str(applied),
                7.0,
                "2026-04-15",
                "2026-04-15",
            ),
        ],
    )
    conn.commit()
    conn.close()

    mark_complete(tmp_path)
    app = create_app(companies_root=companies, db_path=db, base_root=tmp_path)
    return {"client": TestClient(app), "companies": companies, "db": db}


def test_full_flow(world: dict) -> None:
    client = world["client"]

    r = client.get("/healthz")
    assert r.status_code == 200

    r = client.get("/materials/")
    assert r.status_code == 200
    assert "Meta" in r.text
    assert "Google" in r.text

    r = client.get("/materials/fp-active")
    assert r.status_code == 200
    assert "tailored_resume.docx" in r.text
    assert "cover_letter.md" in r.text

    r = client.get("/materials/fp-active/cover_letter.md")
    assert r.status_code == 200
    assert "<h1>Cover</h1>" in r.text

    r = client.get("/materials/fp-active/tailored_resume.docx")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    assert r.headers.get("content-type", "").startswith("application/octet-stream")

    r = client.get("/materials/fp-applied/notes.txt")
    assert r.status_code == 200
    assert "applied on 2026-04-15" in r.text
