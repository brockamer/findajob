"""Tests for the 'Briefing ready' group on /materials/ (#1273).

Lists jobs at stage='briefing_ready', plus 'scored' jobs whose prep folder
still holds a briefing (the decision-window reaper's reset path, #1222),
merged into one group above 'In flight'. Order:
COALESCE(stage_updated, created_at) DESC, same 50-row cap as the other
sections on the page.
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
    """Build a TestClient against a fresh companies/ + pipeline.db, with a
    helper to seed jobs rows (each optionally backed by a prep folder with
    or without a saved briefing file)."""
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "events.jsonl"))
    companies = tmp_path / "companies"
    companies.mkdir()
    db_path = tmp_path / "pipeline.db"
    init_test_db(db_path)
    mark_complete(tmp_path)

    def _insert_row(
        conn: sqlite3.Connection,
        fp: str,
        *,
        stage: str,
        title: str,
        company: str,
        prep_folder: str | None = None,
        briefing: bool = False,
        created_at: str = "2026-01-01",
        stage_updated: str | None = None,
    ) -> None:
        folder_path = None
        if prep_folder is not None:
            folder = companies / prep_folder
            folder.mkdir(exist_ok=True)
            if briefing:
                (folder / f"Tester Briefing - {company} - {title} - 20260101-000000.md").write_text(
                    "# Briefing\n\nBody."
                )
            folder_path = str(folder)
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, "
            "prep_folder_path, created_at, stage_updated) "
            "VALUES (?, ?, 'https://x.test', ?, ?, 'test', ?, ?, ?, ?)",
            (fp, fp, title, company, stage, folder_path, created_at, stage_updated),
        )

    def _make(rows: list[dict]) -> TestClient:
        conn = sqlite3.connect(db_path)
        for row in rows:
            _insert_row(conn, **row)
        conn.commit()
        conn.close()
        return TestClient(create_app(companies_root=companies, db_path=db_path, base_root=tmp_path))

    return _make


def test_briefing_ready_row_listed_and_links_to_continue_prep_form(index_client):
    client = index_client(
        [
            dict(
                fp="fp-br",
                stage="briefing_ready",
                title="Orbit Analyst",
                company="Nimbus Robotics",
                prep_folder="Nimbus_Orbit_1",
                briefing=True,
                created_at="2026-01-05",
            ),
        ]
    )
    resp = client.get("/materials/")
    assert resp.status_code == 200
    assert "Nimbus Robotics" in resp.text
    assert 'href="/materials/fp-br"' in resp.text


def test_reset_scored_row_with_saved_briefing_is_listed(index_client):
    client = index_client(
        [
            dict(
                fp="fp-reset",
                stage="scored",
                title="Cold-Chain Technician",
                company="Vertex Logistics",
                prep_folder="Vertex_ColdChain_1",
                briefing=True,
                created_at="2026-01-03",
            ),
        ]
    )
    resp = client.get("/materials/")
    assert resp.status_code == 200
    assert "Vertex Logistics" in resp.text


def test_scored_row_without_folder_or_without_briefing_is_skipped(index_client):
    client = index_client(
        [
            dict(
                fp="fp-no-folder",
                stage="scored",
                title="Harvest Planner",
                company="Greenfield Farms",
                prep_folder=None,
                created_at="2026-01-02",
            ),
            dict(
                fp="fp-no-briefing",
                stage="scored",
                title="Kiln Operator",
                company="Ashgrove Ceramics",
                prep_folder="Ashgrove_Kiln_1",
                briefing=False,
                created_at="2026-01-02",
            ),
        ]
    )
    resp = client.get("/materials/")
    assert resp.status_code == 200
    assert "Greenfield Farms" not in resp.text
    assert "Ashgrove Ceramics" not in resp.text


def test_group_appears_above_in_flight_and_prep_in_progress_stays_in_flight_only(index_client):
    client = index_client(
        [
            dict(
                fp="fp-br",
                stage="briefing_ready",
                title="Orbit Analyst",
                company="Nimbus Robotics",
                prep_folder="Nimbus_Orbit_1",
                briefing=True,
                created_at="2026-01-05",
            ),
            dict(
                fp="fp-inflight",
                stage="prep_in_progress",
                title="Loom Technician",
                company="Driftwood Textiles",
                created_at="2026-01-04",
            ),
        ]
    )
    resp = client.get("/materials/")
    assert resp.status_code == 200
    text = resp.text
    briefing_heading_pos = text.index("Briefing ready")
    in_flight_heading_pos = text.index("In flight")
    assert briefing_heading_pos < in_flight_heading_pos

    nimbus_pos = text.index("Nimbus Robotics")
    driftwood_pos = text.index("Driftwood Textiles")

    # The briefing_ready row sits between the two headings; the
    # prep_in_progress row sits after the In flight heading, not before it.
    assert briefing_heading_pos < nimbus_pos < in_flight_heading_pos
    assert driftwood_pos > in_flight_heading_pos

    # And it must not also be duplicated inside the Briefing ready group.
    assert text.count("Driftwood Textiles") == 1


def test_following_briefing_ready_row_link_reaches_continue_prep_form(index_client):
    client = index_client(
        [
            dict(
                fp="fp-br",
                stage="briefing_ready",
                title="Orbit Analyst",
                company="Nimbus Robotics",
                prep_folder="Nimbus_Orbit_1",
                briefing=True,
                created_at="2026-01-05",
            ),
        ]
    )
    index_resp = client.get("/materials/")
    assert 'href="/materials/fp-br"' in index_resp.text

    folder_resp = client.get("/materials/fp-br")
    assert folder_resp.status_code == 200
    assert 'action="/materials/fp-br/continue-prep"' in folder_resp.text
