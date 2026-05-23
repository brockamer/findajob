"""Tests for #653 — per-row 'Add exclusion rule' affordance.

GET /board/jobs/{fp}/exclude/modal returns a cell-shaped modal form with locus
(title-only vs JD-content) radios and a textarea pre-populated with a draft.
POST /board/jobs/{fp}/exclude validates locus + payload and calls the right
config_loader saver. GET /board/jobs/{fp}/exclude/cell restores the icon cell
on Cancel — mirrors the regenerate-confirm restore pattern (#700).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from findajob import config_loader
from findajob.onboarding import mark_complete
from findajob.web.app import create_app


@pytest.fixture
def prefilter_path(tmp_path: Path) -> Path:
    return tmp_path / "config" / "prefilter_rules.yaml"


@pytest.fixture
def profile_path(tmp_path: Path) -> Path:
    p = tmp_path / "candidate_context" / "profile.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("## Identity\nA person.\n\n## Excluded Categories\nExisting categories.\n\n## Next Section\nTail.\n")
    return p


@pytest.fixture
def client(
    tmp_path: Path,
    prefilter_path: Path,
    profile_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    monkeypatch.setattr(config_loader, "_RULES_PATH", prefilter_path)
    monkeypatch.setattr(config_loader, "_PROFILE_PATH", profile_path)

    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs ("
        "id TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, company TEXT, "
        "stage TEXT, reject_reason TEXT, relevance_score INTEGER, "
        "fit_score REAL, probability_score REAL, interview_likelihood INTEGER, "
        "location TEXT, remote_status TEXT, known_contacts TEXT, "
        "comp_estimate TEXT, ai_notes TEXT, created_at TEXT, "
        "stage_updated TEXT, url TEXT, prep_folder_path TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE audit_log ("
        "id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT"
        ")"
    )
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, title, company, stage) "
        "VALUES ('j1', 'fp1', 'Senior Sales Engineer', 'AcmeCo', 'scored')"
    )
    conn.commit()
    conn.close()

    companies = tmp_path / "companies"
    companies.mkdir()

    mark_complete(tmp_path)

    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_get_modal_renders_form_with_locus_radios(client: TestClient) -> None:
    """GET /board/jobs/{fp}/exclude/modal returns a form with both locus options."""
    resp = client.get("/board/jobs/fp1/exclude/modal")
    assert resp.status_code == 200
    body = resp.text
    assert 'name="locus"' in body
    assert 'value="title"' in body
    assert 'value="jd"' in body
    # Form posts back to the same fingerprint's exclude endpoint
    assert "/board/jobs/fp1/exclude" in body
    # Title context visible so operator knows which row this is
    assert "Senior Sales Engineer" in body


def test_get_modal_404_for_unknown_fingerprint(client: TestClient) -> None:
    resp = client.get("/board/jobs/missing/exclude/modal")
    assert resp.status_code == 404


def test_get_cell_renders_icon_button(client: TestClient) -> None:
    """GET /board/jobs/{fp}/exclude/cell — Cancel-restore endpoint."""
    resp = client.get("/board/jobs/fp1/exclude/cell")
    assert resp.status_code == 200
    # Restore-cell payload is the button that opens the modal
    assert "/board/jobs/fp1/exclude/modal" in resp.text


def test_get_cell_404_for_unknown_fingerprint(client: TestClient) -> None:
    resp = client.get("/board/jobs/missing/exclude/cell")
    assert resp.status_code == 404


def test_post_title_locus_writes_to_prefilter(client: TestClient, prefilter_path: Path) -> None:
    """POST with locus=title appends pattern to prefilter_rules.yaml hard_rejects.operator_added."""
    resp = client.post(
        "/board/jobs/fp1/exclude",
        data={"locus": "title", "pattern": r"\bsales\s+engineer\b"},
    )
    assert resp.status_code == 200
    data = yaml.safe_load(prefilter_path.read_text())
    assert data["hard_rejects"]["operator_added"] == [r"\bsales\s+engineer\b"]
    # Response is the icon-cell partial so HTMX can restore the cell
    assert "/board/jobs/fp1/exclude/modal" in resp.text


def test_post_jd_locus_writes_to_profile(client: TestClient, profile_path: Path) -> None:
    """POST with locus=jd appends prose entry to profile.md ## Excluded Categories."""
    resp = client.post(
        "/board/jobs/fp1/exclude",
        data={"locus": "jd", "entry": "Reject roles requiring on-call rotation."},
    )
    assert resp.status_code == 200
    assert "Reject roles requiring on-call rotation." in profile_path.read_text()


def test_post_invalid_regex_does_not_write(client: TestClient, prefilter_path: Path) -> None:
    resp = client.post(
        "/board/jobs/fp1/exclude",
        data={"locus": "title", "pattern": "[unclosed"},
    )
    # HTMX error partial returns 200 with the error inline (matches the
    # existing settings_excluded_employers pattern, not 422)
    assert resp.status_code == 200
    assert "invalid regex" in resp.text.lower()
    assert not prefilter_path.exists() or "[unclosed" not in prefilter_path.read_text()


def test_post_missing_locus_returns_error(client: TestClient) -> None:
    resp = client.post(
        "/board/jobs/fp1/exclude",
        data={"pattern": r"\bfoo\b"},
    )
    assert resp.status_code == 200
    assert "locus" in resp.text.lower()


def test_post_invalid_locus_returns_error(client: TestClient) -> None:
    resp = client.post(
        "/board/jobs/fp1/exclude",
        data={"locus": "bogus", "pattern": r"\bfoo\b"},
    )
    assert resp.status_code == 200
    assert "locus" in resp.text.lower()


def test_post_404_for_unknown_fingerprint(client: TestClient) -> None:
    resp = client.post(
        "/board/jobs/missing/exclude",
        data={"locus": "title", "pattern": r"\bfoo\b"},
    )
    assert resp.status_code == 404


def test_post_title_empty_pattern_returns_error(client: TestClient) -> None:
    resp = client.post(
        "/board/jobs/fp1/exclude",
        data={"locus": "title", "pattern": "   "},
    )
    assert resp.status_code == 200
    assert "non-empty" in resp.text.lower() or "empty" in resp.text.lower()


def test_post_jd_missing_section_in_profile_surfaces_error(client: TestClient, profile_path: Path) -> None:
    """If the operator's profile.md is missing ## Excluded Categories, surface
    the error inline rather than 500ing."""
    profile_path.write_text("## Identity\nA person.\n")
    resp = client.post(
        "/board/jobs/fp1/exclude",
        data={"locus": "jd", "entry": "Some entry."},
    )
    assert resp.status_code == 200
    assert "section" in resp.text.lower()
