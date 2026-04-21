"""End-to-end integration tests for the materials viewer.

Spins up a FastAPI TestClient against a tmpdir `companies/` tree and a
scratch SQLite. Validates all routes together.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
    conn.executemany(
        "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("fp-active", str(active), "materials_drafted", "SWE", "Meta", 8, "2026-04-20", None),
            ("fp-applied", str(applied), "applied", "PM", "Google", 7, "2026-04-15", "2026-04-15"),
        ],
    )
    conn.commit()
    conn.close()

    app = create_app(companies_root=companies, db_path=db)
    return {"client": TestClient(app), "companies": companies, "db": db}


def test_full_flow(world: dict) -> None:
    client = world["client"]

    r = client.get("/healthz")
    assert r.status_code == 200

    r = client.get("/")
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

    r = client.get("/materials/fp-applied/notes.txt")
    assert r.status_code == 200
    assert "applied on 2026-04-15" in r.text
