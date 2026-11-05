"""Tests for the "Briefing ready" group on /materials/ (#1273).

Lists jobs at stage='briefing_ready' plus 'scored' jobs whose prep folder
still holds a briefing (the #1222 decision-window reset case), ordered by
COALESCE(stage_updated, created_at) DESC above "In flight". Every fixture
here uses invented, field-neutral company/title placeholders — never a real
employer or role name — matching the convention in the other materials-route
test fixtures (e.g. test_materials_briefing_gate.py's "Acme").
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
    """Build a TestClient plus helpers to seed jobs rows and prep folders."""
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))
    companies = tmp_path / "companies"
    companies.mkdir()
    db_path = tmp_path / "pipeline.db"
    init_test_db(db_path)
    mark_complete(tmp_path)
    app = create_app(companies_root=companies, db_path=db_path, base_root=tmp_path)
    client = TestClient(app)
    return client, companies, db_path


def _insert_job(db_path: Path, *, fingerprint: str, title: str, company: str, stage: str, **kwargs: object) -> None:
    fields = {
        "id": fingerprint,
        "fingerprint": fingerprint,
        "url": f"https://example.test/{fingerprint}",
        "title": title,
        "company": company,
        "source": "test",
        "stage": stage,
    }
    fields.update(kwargs)
    conn = sqlite3.connect(db_path)
    cols = ", ".join(fields.keys())
    placeholders = ", ".join("?" * len(fields))
    conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({placeholders})", tuple(fields.values()))
    conn.commit()
    conn.close()


def _make_folder_with_briefing(companies: Path, name: str, *, company: str, title: str) -> Path:
    folder = companies / name
    folder.mkdir()
    (folder / f"Candidate Briefing - {company} - {title} - 20260601-120000.md").write_text("# Briefing\n\nBody.")
    (folder / f"JD - {company} - {title}.txt").write_text("JD body.")
    return folder


def test_briefing_ready_row_listed_and_links_to_continue_prep_form(index_client):
    client, companies, db_path = index_client
    folder = _make_folder_with_briefing(
        companies, "Nimbus_Logistics_Ops_Lead", company="Nimbus Logistics", title="Ops Lead"
    )
    _insert_job(
        db_path,
        fingerprint="fp-gate",
        title="Ops Lead",
        company="Nimbus Logistics",
        stage="briefing_ready",
        prep_folder_path=str(folder),
        stage_updated="2026-06-01",
    )

    resp = client.get("/materials/")
    assert resp.status_code == 200
    assert "Briefing ready" in resp.text
    assert "Nimbus Logistics" in resp.text

    # Following the row's link reaches the folder page's Continue-prep form.
    follow = client.get("/materials/fp-gate")
    assert follow.status_code == 200
    assert 'action="/materials/fp-gate/continue-prep"' in follow.text


def test_reset_scored_row_with_saved_briefing_listed(index_client):
    """#1222: the decision-window reaper drops the row back to 'scored' but
    keeps the folder — such a row still belongs in the Briefing ready group."""
    client, companies, db_path = index_client
    folder = _make_folder_with_briefing(
        companies, "Driftwood_Supply_Analyst", company="Driftwood Supply", title="Analyst"
    )
    _insert_job(
        db_path,
        fingerprint="fp-reset",
        title="Analyst",
        company="Driftwood Supply",
        stage="scored",
        prep_folder_path=str(folder),
        stage_updated="2026-06-02",
    )

    resp = client.get("/materials/")
    assert resp.status_code == 200
    assert "Driftwood Supply" in resp.text

    follow = client.get("/materials/fp-reset")
    assert follow.status_code == 200
    assert 'action="/materials/fp-reset/continue-prep"' in follow.text


def test_scored_row_without_briefing_or_folder_skipped(index_client):
    client, companies, db_path = index_client
    # No folder at all.
    _insert_job(
        db_path,
        fingerprint="fp-nofolder",
        title="Coordinator",
        company="Halcyon Freight",
        stage="scored",
    )
    # Folder exists but holds no briefing.
    folder = companies / "Quillfeather_Textiles_Coordinator"
    folder.mkdir()
    (folder / "JD - Quillfeather Textiles - Coordinator.txt").write_text("JD body.")
    _insert_job(
        db_path,
        fingerprint="fp-nobriefing",
        title="Coordinator",
        company="Quillfeather Textiles",
        stage="scored",
        prep_folder_path=str(folder),
    )

    resp = client.get("/materials/")
    assert resp.status_code == 200
    assert "Halcyon Freight" not in resp.text
    assert "Quillfeather Textiles" not in resp.text


def test_briefing_ready_group_above_in_flight_and_prep_in_progress_stays_in_flight(index_client):
    client, companies, db_path = index_client
    folder = _make_folder_with_briefing(companies, "Cobalt_Harbor_Planner", company="Cobalt Harbor", title="Planner")
    _insert_job(
        db_path,
        fingerprint="fp-gate2",
        title="Planner",
        company="Cobalt Harbor",
        stage="briefing_ready",
        prep_folder_path=str(folder),
        stage_updated="2026-06-03",
    )
    _insert_job(
        db_path,
        fingerprint="fp-inflight",
        title="Dispatcher",
        company="Marrowgate Transit",
        stage="prep_in_progress",
    )

    resp = client.get("/materials/")
    assert resp.status_code == 200
    briefing_ready_pos = resp.text.index("Briefing ready")
    in_flight_pos = resp.text.index("In flight")
    assert briefing_ready_pos < in_flight_pos

    # The prep_in_progress row shows up once, inside In flight — not duplicated
    # into Briefing ready.
    assert resp.text.count("Marrowgate Transit") == 1
    marrowgate_pos = resp.text.index("Marrowgate Transit")
    assert marrowgate_pos > in_flight_pos


def test_following_briefing_ready_link_reaches_continue_prep_form(index_client):
    client, companies, db_path = index_client
    folder = _make_folder_with_briefing(
        companies, "Thistledown_Provisions_Buyer", company="Thistledown Provisions", title="Buyer"
    )
    _insert_job(
        db_path,
        fingerprint="fp-link",
        title="Buyer",
        company="Thistledown Provisions",
        stage="briefing_ready",
        prep_folder_path=str(folder),
        stage_updated="2026-06-04",
    )

    index_resp = client.get("/materials/")
    assert "/materials/fp-link" in index_resp.text

    folder_resp = client.get("/materials/fp-link")
    assert folder_resp.status_code == 200
    assert 'action="/materials/fp-link/continue-prep"' in folder_resp.text
    assert "decide before continuing prep" in folder_resp.text
