"""Unit tests for the web viewer routes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
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
    updated_at TEXT DEFAULT (datetime('now')),
    synthetic INTEGER NOT NULL DEFAULT 0,
    speculative_briefing_folder TEXT
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
def client(tmp_path: Path, companies_root: Path, db_path: Path) -> TestClient:
    mark_complete(tmp_path)
    app = create_app(companies_root=companies_root, db_path=db_path, base_root=tmp_path)
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


def test_file_serve_md_raw_returns_byte_identical_source(
    client: TestClient, companies_root: Path, db_path: Path
) -> None:
    """The Copy MD button on the folder page fetches ?raw=1 and pipes the
    response into navigator.clipboard.writeText(). The user's promise is:
    what lands on the clipboard is byte-identical to the file on disk —
    no markdown→HTML→back-to-text round-trip via the rendered prose view.
    Test that ?raw=1 returns the exact source bytes."""
    folder = companies_root / "Company_RAW_2026-04-29_120000"
    folder.mkdir()
    source = "# Heading\n\n- bullet **bold**\n\n```python\nprint('hi')\n```\n"
    (folder / "doc.md").write_text(source, encoding="utf-8")

    conn = sqlite3.connect(db_path)
    _insert_job(conn, "fp-raw-md", prep_folder_path=str(folder), stage="materials_drafted")
    conn.commit()
    conn.close()

    r = client.get("/materials/fp-raw-md/doc.md?raw=1")
    assert r.status_code == 200
    # Source bytes verbatim — no rendered HTML, no transformation.
    assert r.text == source
    # Plain-text content type so the browser doesn't try to render markdown.
    assert r.headers["content-type"].startswith("text/plain")
    # The default (no ?raw) still returns the rendered HTML view.
    r2 = client.get("/materials/fp-raw-md/doc.md")
    assert r2.headers["content-type"].startswith("text/html")
    assert "<h1>Heading</h1>" in r2.text


def test_file_serve_txt_raw_returns_byte_identical_source(
    client: TestClient, companies_root: Path, db_path: Path
) -> None:
    """?raw=1 also covers .txt for completeness (outreach drafts use .txt)."""
    folder = companies_root / "Company_RAW_TXT_2026-04-29_120000"
    folder.mkdir()
    source = "Subject: x\n\nBody line 1\nBody line 2\n"
    (folder / "out.txt").write_text(source, encoding="utf-8")

    conn = sqlite3.connect(db_path)
    _insert_job(conn, "fp-raw-txt", prep_folder_path=str(folder), stage="materials_drafted")
    conn.commit()
    conn.close()

    r = client.get("/materials/fp-raw-txt/out.txt?raw=1")
    assert r.status_code == 200
    assert r.text == source


def test_file_serve_raw_ignored_for_non_text_extensions(
    client: TestClient, companies_root: Path, db_path: Path
) -> None:
    """?raw=1 is a no-op on .docx — falls through to the FileResponse
    download path. Prevents accidentally serving binary .docx as plain text
    (which would corrupt the bytes via UTF-8 errors='replace')."""
    folder = companies_root / "Company_RAW_DOCX_2026-04-29_120000"
    folder.mkdir()
    (folder / "doc.docx").write_bytes(b"PK\x03\x04binary")

    conn = sqlite3.connect(db_path)
    _insert_job(conn, "fp-raw-docx", prep_folder_path=str(folder), stage="materials_drafted")
    conn.commit()
    conn.close()

    r = client.get("/materials/fp-raw-docx/doc.docx?raw=1")
    assert r.status_code == 200
    # Still served as attachment; ?raw=1 does NOT bypass the binary download path.
    assert "attachment" in r.headers.get("content-disposition", "")


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
    assert r.headers.get("content-type", "").startswith("application/octet-stream")


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


def test_file_serve_speculative_briefing_renders_for_synthetic_row(
    client: TestClient, companies_root: Path, db_path: Path
) -> None:
    """Synthetic rows have no prep_folder_path until flag-for-prep, but the
    speculative briefing folder exists on disk. file_serve must mirror the
    folder_view fallback (#485) and serve briefing.md from
    speculative_briefing_folder. Regression for #589."""
    spec_folder_name = "Locus_Robotics_SPECULATIVE_2026-05-09_084936"
    spec_folder = companies_root / spec_folder_name
    spec_folder.mkdir()
    (spec_folder / "briefing.md").write_text("# Locus Robotics Brief\n\nDeep research follows.\n")

    conn = sqlite3.connect(db_path)
    _insert_job(
        conn,
        "fp-spec-render",
        synthetic=1,
        speculative_briefing_folder=spec_folder_name,
        stage="speculative_review",
        title="[SPEC] Locus Robotics",
        company="Locus Robotics",
        source="web_speculative",
    )
    conn.commit()
    conn.close()

    r = client.get("/materials/fp-spec-render/briefing.md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<h1>Locus Robotics Brief</h1>" in r.text


def test_file_serve_speculative_briefing_raw_returns_source(
    client: TestClient, companies_root: Path, db_path: Path
) -> None:
    """?raw=1 must work for the speculative-fallback path so the Copy MD
    button on the speculative briefing folder page returns byte-identical
    source. Regression for #589."""
    spec_folder_name = "Acme_SPECULATIVE_2026-05-09_120000"
    spec_folder = companies_root / spec_folder_name
    spec_folder.mkdir()
    source = "# Acme\n\n- bullet **bold**\n\nResearch summary.\n"
    (spec_folder / "briefing.md").write_text(source, encoding="utf-8")

    conn = sqlite3.connect(db_path)
    _insert_job(
        conn,
        "fp-spec-raw",
        synthetic=1,
        speculative_briefing_folder=spec_folder_name,
        stage="speculative_review",
        title="[SPEC] Acme",
        company="Acme",
        source="web_speculative",
    )
    conn.commit()
    conn.close()

    r = client.get("/materials/fp-spec-raw/briefing.md?raw=1")
    assert r.status_code == 200
    assert r.text == source
    assert r.headers["content-type"].startswith("text/plain")


def test_file_serve_speculative_traversal_still_rejected(
    client: TestClient, companies_root: Path, db_path: Path
) -> None:
    """Path-traversal guard must remain effective once the speculative
    fallback activates — `../escape` resolves outside the briefing folder
    and must 404, not leak adjacent files. Regression for #589."""
    spec_folder_name = "Acme_SPECULATIVE_2026-05-09_130000"
    spec_folder = companies_root / spec_folder_name
    spec_folder.mkdir()
    (spec_folder / "briefing.md").write_text("# Inside\n")
    # A sibling file that traversal might try to reach.
    (companies_root / "secret.md").write_text("# Outside\n")

    conn = sqlite3.connect(db_path)
    _insert_job(
        conn,
        "fp-spec-trav",
        synthetic=1,
        speculative_briefing_folder=spec_folder_name,
        stage="speculative_review",
        title="[SPEC] Acme",
        company="Acme",
        source="web_speculative",
    )
    conn.commit()
    conn.close()

    r = client.get("/materials/fp-spec-trav/..%2Fsecret.md")
    assert r.status_code == 404


def test_file_serve_speculative_404_when_briefing_folder_missing(
    client: TestClient, companies_root: Path, db_path: Path
) -> None:
    """Synthetic row with speculative_briefing_folder pointing to a
    non-existent dir on disk must 404 — the helper returns None and
    file_serve has no folder to serve from. (folder_view redirects to /jd
    in this case; file_serve does not — different UX contract.)"""
    conn = sqlite3.connect(db_path)
    _insert_job(
        conn,
        "fp-spec-missing",
        synthetic=1,
        speculative_briefing_folder="Never_Created_2026-05-09",
        stage="speculative_review",
        title="[SPEC] Ghost",
        company="Ghost",
        source="web_speculative",
    )
    conn.commit()
    conn.close()

    r = client.get("/materials/fp-spec-missing/briefing.md")
    assert r.status_code == 404


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
