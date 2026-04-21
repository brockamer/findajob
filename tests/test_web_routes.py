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


_JOBS_SCHEMA_SQL = """
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
    updated_at TEXT DEFAULT (datetime('now'))
)
"""


def _insert_job(conn: sqlite3.Connection, fp: str, **kwargs: object) -> None:
    """Insert a minimal jobs row, filling NOT NULL columns with sensible defaults.

    Callers pass fingerprint-as-id via `fp` and override any of: title, company,
    url, source, stage, prep_folder_path, fit_score, created_at, stage_updated.
    """
    fields = {
        "id": fp,
        "fingerprint": fp,
        "url": f"https://example.com/{fp}",
        "title": "Untitled",
        "company": "Unknown",
        "source": "test",
    }
    fields.update(kwargs)
    cols = ", ".join(fields.keys())
    placeholders = ", ".join("?" * len(fields))
    conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({placeholders})", tuple(fields.values()))


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "pipeline.db"
    conn = sqlite3.connect(p)
    conn.executescript(_JOBS_SCHEMA_SQL)
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


def test_folder_route_lists_files(client: TestClient, companies_root: Path, db_path: Path) -> None:
    folder = companies_root / "Meta_SWE_2026-04-20_120000"
    folder.mkdir()
    (folder / "tailored_resume.docx").write_bytes(b"docx-bytes")
    (folder / "cover_letter.md").write_text("# Hello\n")

    conn = sqlite3.connect(db_path)
    _insert_job(conn, "fp-1", prep_folder_path=str(folder), stage="materials_drafted", title="SWE", company="Meta")
    conn.commit()
    conn.close()

    r = client.get("/materials/fp-1")
    assert r.status_code == 200
    assert "tailored_resume.docx" in r.text
    assert "cover_letter.md" in r.text


def test_folder_route_404_on_unknown_fingerprint(client: TestClient) -> None:
    r = client.get("/materials/fp-does-not-exist")
    assert r.status_code == 404


def test_file_serve_markdown_rendered_inline(client: TestClient, companies_root: Path, db_path: Path) -> None:
    folder = companies_root / "Company_X_2026-04-20_130000"
    folder.mkdir()
    (folder / "notes.md").write_text("# Hello\n\n```python\nprint('hi')\n```\n")

    conn = sqlite3.connect(db_path)
    _insert_job(conn, "fp-md", prep_folder_path=str(folder), stage="materials_drafted")
    conn.commit()
    conn.close()

    r = client.get("/materials/fp-md/notes.md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<h1>Hello</h1>" in r.text
    assert "<code>print" in r.text


def test_file_serve_docx_as_attachment(client: TestClient, companies_root: Path, db_path: Path) -> None:
    folder = companies_root / "Company_Y_2026-04-20_140000"
    folder.mkdir()
    (folder / "resume.docx").write_bytes(b"PK\x03\x04fake-docx-bytes")

    conn = sqlite3.connect(db_path)
    _insert_job(conn, "fp-docx", prep_folder_path=str(folder), stage="materials_drafted")
    conn.commit()
    conn.close()

    r = client.get("/materials/fp-docx/resume.docx")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    assert "resume.docx" in r.headers.get("content-disposition", "")


def test_file_serve_txt_inline(client: TestClient, companies_root: Path, db_path: Path) -> None:
    folder = companies_root / "Company_Z_2026-04-20_150000"
    folder.mkdir()
    (folder / "raw.txt").write_text("plain text body\n")

    conn = sqlite3.connect(db_path)
    _insert_job(conn, "fp-txt", prep_folder_path=str(folder), stage="materials_drafted")
    conn.commit()
    conn.close()

    r = client.get("/materials/fp-txt/raw.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "plain text body" in r.text


def test_file_serve_404_on_unknown_filename(client: TestClient, companies_root: Path, db_path: Path) -> None:
    folder = companies_root / "Company_W_2026-04-20_160000"
    folder.mkdir()
    conn = sqlite3.connect(db_path)
    _insert_job(conn, "fp-empty", prep_folder_path=str(folder), stage="materials_drafted")
    conn.commit()
    conn.close()

    r = client.get("/materials/fp-empty/nonexistent.md")
    assert r.status_code == 404


def test_file_serve_rejects_traversal(client: TestClient, companies_root: Path, db_path: Path) -> None:
    folder = companies_root / "Company_T_2026-04-20_170000"
    folder.mkdir()
    conn = sqlite3.connect(db_path)
    _insert_job(conn, "fp-t", prep_folder_path=str(folder), stage="materials_drafted")
    conn.commit()
    conn.close()

    r = client.get("/materials/fp-t/..%2Fescape")
    assert r.status_code == 404


def test_file_serve_markdown_escapes_raw_html(client: TestClient, companies_root: Path, db_path: Path) -> None:
    folder = companies_root / "Company_XSS_2026-04-20_180000"
    folder.mkdir()
    (folder / "bad.md").write_text("# Title\n\n<script>alert('x')</script>\n")

    conn = sqlite3.connect(db_path)
    _insert_job(conn, "fp-xss", prep_folder_path=str(folder), stage="materials_drafted")
    conn.commit()
    conn.close()

    r = client.get("/materials/fp-xss/bad.md")
    assert r.status_code == 200
    assert "<script>" not in r.text


def test_index_groups_jobs_by_stage(client: TestClient, companies_root: Path, db_path: Path) -> None:
    for folder_name in ("M1", "_applied/M2", "_waitlisted/M3", "_rejected/M4"):
        (companies_root / folder_name).mkdir(parents=True)

    conn = sqlite3.connect(db_path)
    _insert_job(
        conn,
        "fp-a",
        prep_folder_path=str(companies_root / "M1"),
        stage="materials_drafted",
        title="SWE",
        company="InFlightCo",
        fit_score=8.0,
        created_at="2026-04-20",
    )
    _insert_job(
        conn,
        "fp-b",
        prep_folder_path=str(companies_root / "_applied" / "M2"),
        stage="applied",
        title="PM",
        company="AppliedCo",
        fit_score=7.0,
        created_at="2026-04-15",
        stage_updated="2026-04-15",
    )
    _insert_job(
        conn,
        "fp-c",
        prep_folder_path=str(companies_root / "_waitlisted" / "M3"),
        stage="waitlisted",
        title="DE",
        company="WaitCo",
        fit_score=6.0,
        created_at="2026-04-10",
    )
    _insert_job(
        conn,
        "fp-d",
        prep_folder_path=str(companies_root / "_rejected" / "M4"),
        stage="rejected",
        title="SRE",
        company="RejCo",
        fit_score=5.0,
        created_at="2026-04-01",
    )
    conn.commit()
    conn.close()

    r = client.get("/materials/")
    assert r.status_code == 200
    assert "In flight" in r.text
    assert "InFlightCo" in r.text
    assert "Applied" in r.text
    assert "AppliedCo" in r.text
    assert "Waitlisted" in r.text
    assert "WaitCo" in r.text
    # Rejected is in a <details>; content is still rendered in HTML
    assert "<details>" in r.text
    assert "RejCo" in r.text


def test_default_app_uses_env_vars(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from findajob.web.app import default_app

    companies = tmp_path / "companies"
    companies.mkdir()
    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_JOBS_SCHEMA_SQL)
    conn.commit()
    conn.close()

    monkeypatch.setenv("COMPANIES_ROOT", str(companies))
    monkeypatch.setenv("DB_PATH", str(db_path))

    app = default_app()
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
