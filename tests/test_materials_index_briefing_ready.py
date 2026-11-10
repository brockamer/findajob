"""Tests for the 'Briefing ready' group on /materials/ (#1273).

The group lists jobs whose research is done but whose resume/cover letter
haven't been generated yet: stage='briefing_ready' rows, plus 'scored' rows
the decision-window reaper reset (#1222) that still hold a briefing in their
prep folder. It renders above 'In flight' so a briefing isn't invisible
until Phase B starts.

Coverage:

1. A briefing_ready row is listed and links to the folder page's
   Continue-prep form.
2. A reset scored row with a saved briefing is listed.
3. A scored row with no folder (or no briefing) is skipped.
4. The group appears above In flight; a prep_in_progress row stays in
   In flight only.
5. Following the row's link reaches the Continue-prep form.
6. The cap applies to the merged, filtered list — not before the
   find_briefing filter (regression guard for the #1273 send-back: a
   wall of newer scored-no-briefing rows must not push an older
   scored-with-briefing row off the page).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob import audit
from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import init_test_db


@pytest.fixture()
def index_client(tmp_path: Path, monkeypatch):
    """Build a TestClient over a fresh DB + companies root for /materials/."""
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))
    companies = tmp_path / "companies"
    companies.mkdir(exist_ok=True)
    db_path = tmp_path / "pipeline.db"
    init_test_db(db_path)
    mark_complete(tmp_path)
    app = create_app(companies_root=companies, db_path=db_path, base_root=tmp_path)
    return TestClient(app), db_path, companies


def _make_folder(companies: Path, name: str, *, briefing: bool) -> Path:
    folder = companies / name
    folder.mkdir(exist_ok=True)
    if briefing:
        (folder / "Candidate Briefing - Globex - Widget Analyst - 20260501-120000.md").write_text("# Briefing\n\nBody.")
    (folder / "JD - Globex - Widget Analyst.txt").write_text("JD body.")
    return folder


def _insert_job(
    db_path: Path,
    *,
    fingerprint: str,
    title: str,
    company: str,
    stage: str,
    prep_folder_path: str | None = None,
    created_at: str = "2026-05-01T12:00:00",
    stage_updated: str | None = None,
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, prep_folder_path, "
        "created_at, stage_updated) VALUES (?, ?, 'https://x.test', ?, ?, 'test', ?, ?, ?, ?)",
        (fingerprint, fingerprint, title, company, stage, prep_folder_path, created_at, stage_updated),
    )
    conn.commit()
    conn.close()


# ── 1. briefing_ready row listed + links to Continue-prep form ────────────


def test_briefing_ready_row_listed_and_links_to_continue_prep_form(index_client):
    client, db_path, companies = index_client
    folder = _make_folder(companies, "Globex_WidgetAnalyst_2026-05-01_120000", briefing=True)
    _insert_job(
        db_path,
        fingerprint="fp-ready",
        title="Widget Analyst",
        company="Globex",
        stage="briefing_ready",
        prep_folder_path=str(folder),
    )

    resp = client.get("/materials/")
    assert resp.status_code == 200
    assert "Briefing ready" in resp.text
    assert "Globex — Widget Analyst" in resp.text
    assert 'href="/materials/fp-ready"' in resp.text

    follow = client.get("/materials/fp-ready")
    assert follow.status_code == 200
    assert 'action="/materials/fp-ready/continue-prep"' in follow.text


# ── 2. reset scored row with a saved briefing is listed ───────────────────


def test_reset_scored_row_with_briefing_is_listed(index_client):
    client, db_path, companies = index_client
    folder = _make_folder(companies, "Initech_PaperworkCoordinator_2026-04-20_090000", briefing=True)
    _insert_job(
        db_path,
        fingerprint="fp-reset",
        title="Paperwork Coordinator",
        company="Initech",
        stage="scored",
        prep_folder_path=str(folder),
    )

    resp = client.get("/materials/")
    assert resp.status_code == 200
    briefing_section = resp.text.split("Briefing ready", 1)[1].split("<h2>", 1)[0]
    assert "Initech — Paperwork Coordinator" in briefing_section


# ── 3. scored row with no folder / no briefing is skipped ─────────────────


def test_scored_row_without_briefing_is_skipped(index_client):
    client, db_path, companies = index_client
    folder = _make_folder(companies, "Umbrella_FieldTech_2026-04-22_090000", briefing=False)
    _insert_job(
        db_path,
        fingerprint="fp-no-briefing",
        title="Field Technician",
        company="Umbrella",
        stage="scored",
        prep_folder_path=str(folder),
    )
    _insert_job(
        db_path,
        fingerprint="fp-no-folder",
        title="Loading Dock Lead",
        company="Vandelay",
        stage="scored",
        prep_folder_path=None,
    )

    resp = client.get("/materials/")
    assert resp.status_code == 200
    briefing_section = resp.text.split("Briefing ready", 1)[1].split("<h2>", 1)[0]
    assert "Umbrella — Field Technician" not in briefing_section
    assert "Vandelay — Loading Dock Lead" not in briefing_section


# ── 4. group sits above In flight; prep_in_progress stays in In flight ────


def test_group_above_in_flight_and_prep_in_progress_stays_in_flight(index_client):
    client, db_path, companies = index_client
    ready_folder = _make_folder(companies, "Hooli_DataWrangler_2026-05-02_120000", briefing=True)
    _insert_job(
        db_path,
        fingerprint="fp-ready-2",
        title="Data Wrangler",
        company="Hooli",
        stage="briefing_ready",
        prep_folder_path=str(ready_folder),
    )
    inprog_folder = _make_folder(companies, "Pied_Piper_Compressor_2026-05-02_120500", briefing=True)
    _insert_job(
        db_path,
        fingerprint="fp-inprogress",
        title="Compression Engineer",
        company="Pied Piper",
        stage="prep_in_progress",
        prep_folder_path=str(inprog_folder),
    )

    resp = client.get("/materials/")
    assert resp.status_code == 200
    ready_idx = resp.text.index("Briefing ready")
    in_flight_idx = resp.text.index("In flight")
    assert ready_idx < in_flight_idx

    briefing_section = resp.text[ready_idx:in_flight_idx]
    in_flight_section = resp.text[in_flight_idx:]
    assert "Hooli — Data Wrangler" in briefing_section
    assert "Pied Piper — Compression Engineer" not in briefing_section
    assert "Pied Piper — Compression Engineer" in in_flight_section


# ── 5. following the row's link reaches the Continue-prep form ────────────


def test_following_link_reaches_continue_prep_form_for_reset_row(index_client):
    client, db_path, companies = index_client
    folder = _make_folder(companies, "Stark_ReactorTech_2026-04-18_090000", briefing=True)
    _insert_job(
        db_path,
        fingerprint="fp-reset-2",
        title="Reactor Technician",
        company="Stark",
        stage="scored",
        prep_folder_path=str(folder),
    )

    resp = client.get("/materials/")
    assert 'href="/materials/fp-reset-2"' in resp.text

    follow = client.get("/materials/fp-reset-2")
    assert follow.status_code == 200
    assert 'action="/materials/fp-reset-2/continue-prep"' in follow.text


# ── 6. cap applies after the find_briefing filter, not before ─────────────


def test_cap_after_briefing_filter(index_client):
    """Regression guard for the #1273 send-back: more than 51 newer scored
    rows with a prep folder but no briefing must not push an older reset
    row that still has a briefing off the 'Briefing ready' group."""
    client, db_path, companies = index_client

    old_folder = _make_folder(companies, "OldCo_LegacyAnalyst_2026-01-01_090000", briefing=True)
    _insert_job(
        db_path,
        fingerprint="fp-old-with-briefing",
        title="Legacy Analyst",
        company="OldCo",
        stage="scored",
        prep_folder_path=str(old_folder),
        created_at="2026-01-01T09:00:00",
    )

    for n in range(51):
        folder = _make_folder(companies, f"NewCo{n}_RoleX_2026-06-01_090000", briefing=False)
        _insert_job(
            db_path,
            fingerprint=f"fp-newer-no-briefing-{n}",
            title=f"Role X {n}",
            company=f"NewCo{n}",
            stage="scored",
            prep_folder_path=str(folder),
            created_at="2026-06-01T09:00:00",
        )

    resp = client.get("/materials/")
    assert resp.status_code == 200
    briefing_section = resp.text.split("Briefing ready", 1)[1].split("<h2>", 1)[0]
    assert "OldCo — Legacy Analyst" in briefing_section
