"""Integration tests for the /tools/critique-review/ page (#933).

Computes the recruiter-critique aggregate live from app.state paths and renders
it. Exercises the real route → pipeline → template path against a synthetic
corpus injected via ``create_app(base_root=...)``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app

_MINIMAL_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    stage TEXT DEFAULT 'discovered',
    created_at TEXT DEFAULT (datetime('now')),
    synthetic INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    field_changed TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at TEXT DEFAULT (datetime('now'))
);
"""

_CRITIQUE = '**Weak:** "the glue across the lab and ops teams" — hearsay, cut it.\n'


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _make_client(base: Path, tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.executescript(_MINIMAL_SCHEMA)
    conn.close()
    app = create_app(
        companies_root=base / "companies",
        db_path=db,
        base_root=base,
    )
    return TestClient(app)


@pytest.fixture()
def client_with_corpus(tmp_path: Path) -> TestClient:
    base = tmp_path / "state"
    _write(
        base / "candidate_context" / "master_resume.md",
        "Widely known as the glue across the lab and ops teams here.\n",
    )
    for i, co in enumerate(("Acme", "Beta", "Gamma"), start=1):
        _write(
            base / "companies" / f"{co}_R" / f"C Critique - {co} - R - 2026010{i}-000000.md",
            _CRITIQUE,
        )
    return _make_client(base, tmp_path)


def test_page_renders_source_cluster_with_location_and_companies(client_with_corpus):
    resp = client_with_corpus.get("/tools/critique-review/")

    assert resp.status_code == 200
    body = resp.text
    assert "glue across the lab and ops teams" in body  # the source line / quote
    assert "master_resume.md:1" in body  # anchored location
    assert "Acme" in body and "Beta" in body and "Gamma" in body  # distinct companies


def test_empty_corpus_renders_gracefully(tmp_path: Path):
    base = tmp_path / "state"
    (base / "companies").mkdir(parents=True)
    (base / "candidate_context").mkdir(parents=True)

    resp = _make_client(base, tmp_path).get("/tools/critique-review/")

    assert resp.status_code == 200
    assert "critique" in resp.text.lower()  # page renders, no crash on empty


def test_linked_from_tools_index(client_with_corpus):
    resp = client_with_corpus.get("/tools/")

    assert resp.status_code == 200
    assert "/tools/critique-review/" in resp.text
