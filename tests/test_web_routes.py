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


def test_folder_route_lists_files(client: TestClient, companies_root: Path, db_path: Path) -> None:
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


def test_file_serve_markdown_rendered_inline(client: TestClient, companies_root: Path, db_path: Path) -> None:
    folder = companies_root / "Company_X_2026-04-20_130000"
    folder.mkdir()
    (folder / "notes.md").write_text("# Hello\n\n```python\nprint('hi')\n```\n")

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (fingerprint, prep_folder_path, stage) VALUES ('fp-md', ?, 'materials_drafted')",
        (str(folder),),
    )
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
    conn.execute(
        "INSERT INTO jobs (fingerprint, prep_folder_path, stage) VALUES ('fp-docx', ?, 'materials_drafted')",
        (str(folder),),
    )
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
    conn.execute(
        "INSERT INTO jobs (fingerprint, prep_folder_path, stage) VALUES ('fp-txt', ?, 'materials_drafted')",
        (str(folder),),
    )
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
    conn.execute(
        "INSERT INTO jobs (fingerprint, prep_folder_path, stage) VALUES ('fp-empty', ?, 'materials_drafted')",
        (str(folder),),
    )
    conn.commit()
    conn.close()

    r = client.get("/materials/fp-empty/nonexistent.md")
    assert r.status_code == 404


def test_file_serve_rejects_traversal(client: TestClient, companies_root: Path, db_path: Path) -> None:
    folder = companies_root / "Company_T_2026-04-20_170000"
    folder.mkdir()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (fingerprint, prep_folder_path, stage) VALUES ('fp-t', ?, 'materials_drafted')",
        (str(folder),),
    )
    conn.commit()
    conn.close()

    r = client.get("/materials/fp-t/..%2Fescape")
    assert r.status_code == 404


def test_file_serve_markdown_escapes_raw_html(client: TestClient, companies_root: Path, db_path: Path) -> None:
    folder = companies_root / "Company_XSS_2026-04-20_180000"
    folder.mkdir()
    (folder / "bad.md").write_text("# Title\n\n<script>alert('x')</script>\n")

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (fingerprint, prep_folder_path, stage) VALUES ('fp-xss', ?, 'materials_drafted')",
        (str(folder),),
    )
    conn.commit()
    conn.close()

    r = client.get("/materials/fp-xss/bad.md")
    assert r.status_code == 200
    assert "<script>" not in r.text


def test_index_groups_jobs_by_stage(client: TestClient, companies_root: Path, db_path: Path) -> None:
    for folder_name in ("M1", "_applied/M2", "_waitlisted/M3", "_rejected/M4"):
        (companies_root / folder_name).mkdir(parents=True)

    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT INTO jobs (fingerprint, prep_folder_path, stage, title, company, score, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("fp-a", str(companies_root / "M1"), "materials_drafted", "SWE", "InFlightCo", 8, "2026-04-20"),
            ("fp-b", str(companies_root / "_applied" / "M2"), "applied", "PM", "AppliedCo", 7, "2026-04-15"),
            ("fp-c", str(companies_root / "_waitlisted" / "M3"), "waitlisted", "DE", "WaitCo", 6, "2026-04-10"),
            ("fp-d", str(companies_root / "_rejected" / "M4"), "rejected", "SRE", "RejCo", 5, "2026-04-01"),
        ],
    )
    conn.commit()
    conn.close()

    r = client.get("/")
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
    conn.execute(
        "CREATE TABLE jobs (fingerprint TEXT, prep_folder_path TEXT, stage TEXT, "
        "title TEXT, company TEXT, score INTEGER, created_at TEXT, applied_date TEXT)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("COMPANIES_ROOT", str(companies))
    monkeypatch.setenv("DB_PATH", str(db_path))

    app = default_app()
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
