"""Tests for the POST /materials/{fp}/files/{filename} edit-and-save route (#210)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob import audit
from findajob.onboarding import mark_complete
from findajob.web.app import create_app


def _build_pipeline_db(db_path: Path) -> None:
    from findajob.db.migrate import apply_pending

    conn = sqlite3.connect(db_path)
    try:
        apply_pending(conn)
    finally:
        conn.close()


def _seed_job(
    conn: sqlite3.Connection,
    *,
    fingerprint: str,
    stage: str,
    prep_folder_path: str,
    job_id: str | None = None,
    company: str = "Acme Corp",
    title: str = "Senior Ops",
) -> str:
    job_id = job_id or fingerprint.replace("fp", "id")
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, prep_folder_path) "
        "VALUES (?, ?, 'https://example.com', ?, ?, 'test', ?, ?)",
        (job_id, fingerprint, title, company, stage, prep_folder_path),
    )
    conn.commit()
    return job_id


@pytest.fixture()
def pandoc_calls(monkeypatch) -> list[tuple]:
    """Capture render_md_to_docx invocations from the materials route."""
    calls: list[tuple] = []

    def _stub(md_path, docx_path, *, has_yaml_frontmatter: bool = False) -> None:
        calls.append((str(md_path), str(docx_path), has_yaml_frontmatter))
        # Touch the docx so file_serve / subsequent .is_file() checks see it.
        Path(docx_path).write_bytes(b"FAKE-DOCX")

    monkeypatch.setattr("findajob.web.routes.materials.render_md_to_docx", _stub)
    return calls


@pytest.fixture()
def client_factory(tmp_path: Path, monkeypatch):
    """Build a TestClient with a real DB + on-disk materials folder for a given job."""
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))

    def _make(
        *,
        fingerprint: str = "fp_edit",
        stage: str = "materials_drafted",
        folder_name: str = "Acme_Eng_2026-05-13_120000",
        files: dict[str, str] | None = None,
    ) -> TestClient:
        companies = tmp_path / "companies"
        companies.mkdir(exist_ok=True)
        folder = companies / folder_name
        folder.mkdir(exist_ok=True)
        for name, body in (files or {}).items():
            (folder / name).write_text(body, encoding="utf-8")

        db_path = tmp_path / "pipeline.db"
        if not db_path.exists():
            _build_pipeline_db(db_path)

        conn = sqlite3.connect(db_path)
        _seed_job(conn, fingerprint=fingerprint, stage=stage, prep_folder_path=str(folder))
        conn.close()

        mark_complete(tmp_path)
        app = create_app(companies_root=companies, db_path=db_path, base_root=tmp_path)
        client = TestClient(app)
        client._folder = folder
        client._fingerprint = fingerprint
        return client

    return _make


# ─── happy paths ─────────────────────────────────────────────────────────────


def test_save_cover_letter_writes_md_and_regens_docx(client_factory, pandoc_calls):
    client = client_factory(
        files={
            "Brock Cover - Acme - Senior Ops - 20260513-120000.md": "OLD",
            "Brock Cover - Acme - Senior Ops - 20260513-120000.docx": "OLD-DOCX",
        },
    )
    md_name = "Brock Cover - Acme - Senior Ops - 20260513-120000.md"

    resp = client.post(
        f"/materials/{client._fingerprint}/files/{md_name}",
        data={"content": "NEW COVER BODY"},
    )

    assert resp.status_code == 200, resp.text
    assert "Saved " in resp.text and md_name in resp.text
    assert (client._folder / md_name).read_text() == "NEW COVER BODY"
    assert len(pandoc_calls) == 1
    md_path, docx_path, yaml = pandoc_calls[0]
    assert md_path == str(client._folder / md_name)
    assert docx_path.endswith(".docx")
    assert yaml is False  # cover letter does NOT use YAML frontmatter


def test_save_briefing_passes_yaml_frontmatter_flag(client_factory, pandoc_calls):
    client = client_factory(
        files={
            "Brock Briefing - Acme - Senior Ops - 20260513-120000.md": "OLD",
            "Brock Briefing - Acme - Senior Ops - 20260513-120000.docx": "OLD-DOCX",
        },
    )
    md_name = "Brock Briefing - Acme - Senior Ops - 20260513-120000.md"

    resp = client.post(
        f"/materials/{client._fingerprint}/files/{md_name}",
        data={"content": "---\ntitle: x\n---\n\n# Body"},
    )

    assert resp.status_code == 200
    _md, _docx, yaml = pandoc_calls[0]
    assert yaml is True


def test_save_resume_linkifies_contact_info(client_factory, pandoc_calls):
    client = client_factory(
        files={
            "Brock Resume - Acme - Senior Ops - 20260513-120000.md": "OLD",
            "Brock Resume - Acme - Senior Ops - 20260513-120000.docx": "OLD-DOCX",
        },
    )
    md_name = "Brock Resume - Acme - Senior Ops - 20260513-120000.md"
    raw_content = "Jordan Smith\njordan@example.com  •  linkedin.com/in/jordansmith"

    resp = client.post(
        f"/materials/{client._fingerprint}/files/{md_name}",
        data={"content": raw_content},
    )

    assert resp.status_code == 200
    on_disk = (client._folder / md_name).read_text()
    assert "[jordan@example.com](mailto:jordan@example.com)" in on_disk
    assert "[linkedin.com/in/jordansmith](https://linkedin.com/in/jordansmith)" in on_disk


def test_one_shot_bak_created_on_first_edit(client_factory, pandoc_calls):
    client = client_factory(
        files={
            "Brock Cover - Acme - Senior Ops - 20260513-120000.md": "ORIGINAL",
            "Brock Cover - Acme - Senior Ops - 20260513-120000.docx": "OLD-DOCX",
        },
    )
    md_name = "Brock Cover - Acme - Senior Ops - 20260513-120000.md"

    client.post(f"/materials/{client._fingerprint}/files/{md_name}", data={"content": "EDIT 1"})
    client.post(f"/materials/{client._fingerprint}/files/{md_name}", data={"content": "EDIT 2"})

    bak = client._folder / f"{md_name}.bak"
    assert bak.exists()
    assert bak.read_text() == "ORIGINAL"  # First edit's pre-edit content; not overwritten.


def test_docx_absent_skips_regen(client_factory, pandoc_calls):
    """Resume Changes / Review Checklist have no .docx companion — save succeeds, no pandoc."""
    client = client_factory(
        files={
            "Brock Resume Changes - Acme - Senior Ops - 20260513-120000.md": "OLD",
        },
    )
    md_name = "Brock Resume Changes - Acme - Senior Ops - 20260513-120000.md"

    resp = client.post(
        f"/materials/{client._fingerprint}/files/{md_name}",
        data={"content": "NEW DIFF"},
    )

    assert resp.status_code == 200
    assert (client._folder / md_name).read_text() == "NEW DIFF"
    assert pandoc_calls == []


def test_pandoc_binary_missing_saves_md_and_surfaces_error(client_factory, monkeypatch):
    """Real codepath: render_md_to_docx raises FileNotFoundError when PANDOC
    points at a missing binary. The route must catch it (not 500) and surface
    the infra error in the partial. Caught a real bug during Step E smoke."""
    client = client_factory(
        files={
            "Brock Cover - Acme - Senior Ops - 20260513-120000.md": "OLD",
            "Brock Cover - Acme - Senior Ops - 20260513-120000.docx": "OLD-DOCX",
        },
    )
    md_name = "Brock Cover - Acme - Senior Ops - 20260513-120000.md"

    # Point findajob.prep.docx_render at a non-existent pandoc binary so the
    # real subprocess.run raises FileNotFoundError. This exercises the real
    # codepath, not a mock — the mock-based test_pandoc_failure variant below
    # only covers the CalledProcessError branch.
    monkeypatch.setattr("findajob.prep.docx_render.PANDOC", "/nonexistent/pandoc")

    resp = client.post(
        f"/materials/{client._fingerprint}/files/{md_name}",
        data={"content": "NEW CONTENT"},
    )

    assert resp.status_code == 200
    assert 'data-outcome="error"' in resp.text
    assert ".docx regen failed" in resp.text
    assert "pandoc binary not found" in resp.text
    # .md was still saved despite the missing binary
    assert (client._folder / md_name).read_text() == "NEW CONTENT"


def test_pandoc_failure_saves_md_and_surfaces_error(client_factory, monkeypatch):
    client = client_factory(
        files={
            "Brock Cover - Acme - Senior Ops - 20260513-120000.md": "OLD",
            "Brock Cover - Acme - Senior Ops - 20260513-120000.docx": "OLD-DOCX",
        },
    )
    md_name = "Brock Cover - Acme - Senior Ops - 20260513-120000.md"

    def _fail(*_a, **_kw):
        raise subprocess.CalledProcessError(
            returncode=99, cmd=["/usr/bin/pandoc"], stderr=b"pandoc: weird input on line 12"
        )

    monkeypatch.setattr("findajob.web.routes.materials.render_md_to_docx", _fail)

    resp = client.post(
        f"/materials/{client._fingerprint}/files/{md_name}",
        data={"content": "NEW CONTENT"},
    )

    assert resp.status_code == 200
    assert 'data-outcome="error"' in resp.text
    assert ".docx regen failed" in resp.text
    assert "pandoc: weird input on line 12" in resp.text
    # .md was still saved despite the pandoc failure
    assert (client._folder / md_name).read_text() == "NEW CONTENT"


# ─── validation rejections ───────────────────────────────────────────────────


def test_reject_snapshot_filename(client_factory, pandoc_calls):
    """Snapshots are read-only audit artifacts."""
    client = client_factory(
        files={
            "Brock Resume - Acme - Senior Ops - 20260513-120000.md": "live",
            "Brock Resume - Acme - Senior Ops - 20260513-120000.applied-2026-05-13.md": "snapshot",
        },
    )
    snap = "Brock Resume - Acme - Senior Ops - 20260513-120000.applied-2026-05-13.md"

    resp = client.post(
        f"/materials/{client._fingerprint}/files/{snap}",
        data={"content": "ATTEMPTED EDIT"},
    )

    assert resp.status_code == 403
    assert (client._folder / snap).read_text() == "snapshot"  # untouched


def test_reject_bak_filename(client_factory, pandoc_calls):
    client = client_factory(
        files={
            "Brock Resume - Acme - Senior Ops - 20260513-120000.md": "live",
            "Brock Resume - Acme - Senior Ops - 20260513-120000.md.bak": "backup",
        },
    )
    bak = "Brock Resume - Acme - Senior Ops - 20260513-120000.md.bak"

    resp = client.post(
        f"/materials/{client._fingerprint}/files/{bak}",
        data={"content": "ATTEMPTED"},
    )

    assert resp.status_code == 400  # .bak doesn't end in .md → fails the .md gate first


def test_reject_non_md_extension(client_factory, pandoc_calls):
    client = client_factory(
        files={"Brock Resume - Acme - Senior Ops - 20260513-120000.docx": "binary"},
    )

    resp = client.post(
        f"/materials/{client._fingerprint}/files/Brock Resume - Acme - Senior Ops - 20260513-120000.docx",
        data={"content": "nope"},
    )

    assert resp.status_code == 400


def test_reject_unclassified_md(client_factory, pandoc_calls):
    """random.md doesn't match any _GROUP_RULES substring."""
    client = client_factory(files={"random.md": "x"})

    resp = client.post(
        f"/materials/{client._fingerprint}/files/random.md",
        data={"content": "y"},
    )

    assert resp.status_code == 403


def test_reject_when_stage_prep_in_progress(client_factory, pandoc_calls):
    client = client_factory(
        stage="prep_in_progress",
        files={"Brock Cover - Acme - Senior Ops - 20260513-120000.md": "ORIGINAL"},
    )
    md_name = "Brock Cover - Acme - Senior Ops - 20260513-120000.md"

    resp = client.post(
        f"/materials/{client._fingerprint}/files/{md_name}",
        data={"content": "RACE"},
    )

    assert resp.status_code == 409
    assert (client._folder / md_name).read_text() == "ORIGINAL"


def test_404_on_unknown_fingerprint(client_factory, pandoc_calls):
    client = client_factory(files={"Brock Cover - Acme - Senior Ops - 20260513-120000.md": "x"})

    resp = client.post(
        "/materials/fp_unknown/files/Brock Cover - Acme - Senior Ops - 20260513-120000.md",
        data={"content": "y"},
    )

    assert resp.status_code == 404


def test_path_traversal_rejected(client_factory, pandoc_calls):
    client = client_factory(files={"Brock Cover - Acme - Senior Ops - 20260513-120000.md": "x"})

    # Backslash path-traversal attempt — FastAPI route matches verbatim filename
    resp = client.post(
        f"/materials/{client._fingerprint}/files/..%2Fevil.md",
        data={"content": "y"},
    )

    # Will be rejected by classify (Other) or by relative_to guard — either path is fine.
    assert resp.status_code in (400, 403, 404)
    # No file written outside the folder
    assert not (client._folder.parent.parent / "evil.md").exists()


def test_atomic_write_leaves_no_tmp_on_success(client_factory, pandoc_calls):
    client = client_factory(
        files={
            "Brock Cover - Acme - Senior Ops - 20260513-120000.md": "OLD",
            "Brock Cover - Acme - Senior Ops - 20260513-120000.docx": "OLD-DOCX",
        },
    )
    md_name = "Brock Cover - Acme - Senior Ops - 20260513-120000.md"

    resp = client.post(
        f"/materials/{client._fingerprint}/files/{md_name}",
        data={"content": "NEW"},
    )

    assert resp.status_code == 200
    leftover_tmps = [p for p in os.listdir(client._folder) if p.endswith(".tmp")]
    assert leftover_tmps == []
