"""Tests for the post-applied banner + applied_date derivation on the folder view (#210)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob import audit
from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from findajob.web.routes.materials import _latest_applied_date

# ─── unit: _latest_applied_date ───────────────────────────────────────────────


def test_latest_applied_date_picks_most_recent(tmp_path: Path):
    (tmp_path / "Resume.md").write_text("x")
    (tmp_path / "Resume.applied-2026-04-01.md").write_text("x")
    (tmp_path / "Resume.applied-2026-05-13.md").write_text("x")
    (tmp_path / "Cover.applied-2026-05-12.md").write_text("x")

    assert _latest_applied_date(tmp_path) == "2026-05-13"


def test_latest_applied_date_none_when_no_snapshots(tmp_path: Path):
    (tmp_path / "Resume.md").write_text("x")
    (tmp_path / "Cover.md").write_text("x")
    (tmp_path / "Resume.md.bak").write_text("x")  # .bak does not count

    assert _latest_applied_date(tmp_path) is None


# ─── integration: folder view renders the banner ─────────────────────────────


def _build_pipeline_db(db_path: Path) -> None:
    from findajob.db.migrate import apply_pending

    conn = sqlite3.connect(db_path)
    try:
        apply_pending(conn)
    finally:
        conn.close()


@pytest.fixture()
def folder_client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))

    def _make(*, stage: str, snapshot_dates: list[str]) -> TestClient:
        companies = tmp_path / "companies"
        companies.mkdir(exist_ok=True)
        folder = companies / "Acme_Eng_2026-05-13_120000"
        folder.mkdir(exist_ok=True)
        (folder / "Brock Resume - Acme - Sr Ops - 20260513-120000.md").write_text("R")
        (folder / "Brock Cover - Acme - Sr Ops - 20260513-120000.md").write_text("C")
        for d in snapshot_dates:
            (folder / f"Brock Resume - Acme - Sr Ops - 20260513-120000.applied-{d}.md").write_text("snap")

        db_path = tmp_path / "pipeline.db"
        if not db_path.exists():
            _build_pipeline_db(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, prep_folder_path) "
            "VALUES ('jid', 'fp', 'https://x.test', 'Sr Ops', 'Acme', 'test', ?, ?)",
            (stage, str(folder)),
        )
        conn.commit()
        conn.close()

        mark_complete(tmp_path)
        app = create_app(companies_root=companies, db_path=db_path, base_root=tmp_path)
        return TestClient(app)

    return _make


def test_banner_renders_with_apply_date_when_post_applied(folder_client):
    client = folder_client(stage="applied", snapshot_dates=["2026-05-13"])
    resp = client.get("/materials/fp")
    assert resp.status_code == 200
    assert "Already sent" in resp.text
    assert "Sent to the employer on" in resp.text
    assert "2026-05-13" in resp.text


def test_banner_renders_without_date_when_no_snapshot(folder_client):
    """Backwards-compat: jobs applied before #210 shipped have no snapshot file."""
    client = folder_client(stage="applied", snapshot_dates=[])
    resp = client.get("/materials/fp")
    assert resp.status_code == 200
    assert "Already sent" in resp.text
    assert "No as-sent snapshot" in resp.text


def test_no_banner_when_pre_applied_stage(folder_client):
    client = folder_client(stage="materials_drafted", snapshot_dates=[])
    resp = client.get("/materials/fp")
    assert resp.status_code == 200
    assert "Already sent" not in resp.text


def test_banner_uses_latest_when_multiple_snapshots(folder_client):
    client = folder_client(stage="interview", snapshot_dates=["2026-04-15", "2026-05-13"])
    resp = client.get("/materials/fp")
    assert "2026-05-13" in resp.text
    assert "2026-04-15" not in resp.text  # only the latest shows


def test_edit_button_present_on_md_rows(folder_client):
    client = folder_client(stage="materials_drafted", snapshot_dates=[])
    resp = client.get("/materials/fp")
    # Edit button is wired with HTMX + Alpine; the ✎ Edit affordance must be present.
    assert "✎ Edit" in resp.text
    # The HTMX form for the editor posts to the new edit route.
    assert 'hx-post="/materials/fp/files/' in resp.text


def test_save_result_span_ids_are_unique_across_groups(folder_client):
    """Regression: smoke caught that ``loop.index`` inside Jinja always refers
    to the innermost loop, so first-file-in-group IDs collided ('save-result-0-1')
    across Resume and Cover Letter — HTMX swapped Cover's response into Resume's
    span. The fix uses a group_slug + loop.index pair."""
    import re

    client = folder_client(stage="materials_drafted", snapshot_dates=[])
    resp = client.get("/materials/fp")
    span_ids = re.findall(r'id="(save-result-[^"]+)"', resp.text)
    target_ids = re.findall(r'hx-target="#(save-result-[^"]+)"', resp.text)
    # Both .md rows (Resume + Cover Letter) produce IDs — there must be >=2 distinct ones.
    assert len(span_ids) >= 2
    assert len(set(span_ids)) == len(span_ids), f"duplicate span IDs: {span_ids}"
    # And hx-target IDs must match the span IDs exactly (else HTMX swaps into the wrong spot).
    assert set(target_ids) == set(span_ids)


def test_save_button_does_not_toggle_saving_on_click(folder_client):
    """Regression: a click-time @click="saving = true" makes Alpine flip
    :disabled before the form's default-submit fires, killing the HTMX request
    entirely. `saving` must flip on htmx:before-request (after the request has
    already been initiated), not on the click event."""
    client = folder_client(stage="materials_drafted", snapshot_dates=[])
    resp = client.get("/materials/fp")
    # The submit button has :disabled binding but MUST NOT have an @click toggle.
    # If you reintroduce @click="saving = true", this regression test will fail.
    assert '@click="saving = true"' not in resp.text
    # The form binding for setting saving=true must live on htmx:before-request.
    assert '@htmx:before-request="saving = true"' in resp.text
