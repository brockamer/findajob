"""Integration tests for /onboarding/ routes (#148)."""

from __future__ import annotations

import shutil
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
    created_at TEXT DEFAULT (datetime('now'))
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


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(_MINIMAL_SCHEMA)
    conn.close()
    (tmp_path / "companies").mkdir()
    # Copy the real onboarding_interviewer role into the tmpdir so
    # /onboarding/prompt can read it. GET /onboarding/prompt is a
    # real filesystem read of {base_root}/config/roles/onboarding_interviewer.md.
    (tmp_path / "config" / "roles").mkdir(parents=True)
    repo_role = Path(__file__).parent.parent / "config" / "roles" / "onboarding_interviewer.md"
    shutil.copy(repo_role, tmp_path / "config" / "roles" / "onboarding_interviewer.md")

    app = create_app(
        companies_root=tmp_path / "companies",
        db_path=db_path,
        base_root=tmp_path,
    )
    return TestClient(app, follow_redirects=False)


def test_onboarding_index_returns_200(client: TestClient) -> None:
    resp = client.get("/onboarding/")
    assert resp.status_code == 200
    body = resp.text
    assert "onboarding" in body.lower()
    assert 'name="emission"' in body  # paste textarea
    assert "copy the prompt" in body.lower() or "Copy the prompt" in body


def test_rerun_mode_shows_backup_warning(client: TestClient) -> None:
    resp = client.get("/onboarding/?mode=rerun")
    assert resp.status_code == 200
    assert ".backups/" in resp.text
    assert "/config/" in resp.text  # pointer to editor for partial updates


def test_first_run_hides_backup_warning(client: TestClient) -> None:
    resp = client.get("/onboarding/")
    assert resp.status_code == 200
    assert "Existing config will be backed up" not in resp.text


def test_onboarding_prompt_endpoint_returns_role_text(client: TestClient) -> None:
    resp = client.get("/onboarding/prompt")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    # The interview role begins with this heading line
    assert "Onboarding Interviewer v2" in resp.text


# ---------------------------------------------------------------------------
# POST /onboarding/inject
# ---------------------------------------------------------------------------

from pathlib import Path as _Path  # noqa: E402

_FIXTURE_DIR = _Path(__file__).parent / "fixtures" / "onboarding"


def _read_fixture(name: str) -> str:
    return (_FIXTURE_DIR / name).read_text(encoding="utf-8")


def test_inject_clean_emission_redirects_to_board(client: TestClient, tmp_path: _Path) -> None:
    blob = _read_fixture("alice-doe-clean-emission.txt")
    resp = client.post("/onboarding/inject", data={"emission": blob})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/board/dashboard"
    # Files on disk under the TestClient's base_root (tmp_path)
    assert (tmp_path / "candidate_context" / "profile.md").is_file()
    assert (tmp_path / "config" / "target_companies.md").is_file()
    assert (tmp_path / "config" / "companies_of_interest.txt").is_file()
    assert (tmp_path / "data" / ".onboarding-complete").is_file()


def test_inject_missing_block_rerenders_with_error(client: TestClient, tmp_path: _Path) -> None:
    blob = _read_fixture("alice-doe-clean-emission.txt")
    # Strip one block
    lines = blob.splitlines(keepends=True)
    stripped = []
    skip = False
    for line in lines:
        if "<<<FILE: in_domain_patterns.yaml>>>" in line:
            skip = True
        if not skip:
            stripped.append(line)
        if "<<<END FILE: in_domain_patterns.yaml>>>" in line:
            skip = False
    broken = "".join(stripped)

    resp = client.post("/onboarding/inject", data={"emission": broken})
    assert resp.status_code == 400
    body = resp.text
    assert "in_domain_patterns.yaml" in body
    # Textarea content preserved
    assert "Metro Continuum of Care" in body
    # No sentinel written
    assert not (tmp_path / "data" / ".onboarding-complete").exists()
    # No files written
    assert not (tmp_path / "candidate_context" / "profile.md").exists()


def test_inject_empty_paste_rerenders_with_error(client: TestClient, tmp_path: _Path) -> None:
    resp = client.post("/onboarding/inject", data={"emission": ""})
    assert resp.status_code == 400
    body = resp.text
    assert "missing" in body.lower()
    assert not (tmp_path / "data" / ".onboarding-complete").exists()


def test_inject_populates_companies_of_interest_from_tier1(client: TestClient, tmp_path: _Path) -> None:
    blob = _read_fixture("alice-doe-clean-emission.txt")
    resp = client.post("/onboarding/inject", data={"emission": blob})
    assert resp.status_code == 303
    coi = (tmp_path / "config" / "companies_of_interest.txt").read_text()
    assert "Metro Health Authority" in coi
    assert "Sample Benefit Corporation" in coi
    assert "Community First Coalition" in coi
    # Tier 2 NOT included
    assert "Regional Care Network" not in coi


def test_tools_page_links_to_onboarding_rerun(client: TestClient) -> None:
    resp = client.get("/tools/")
    assert resp.status_code == 200
    body = resp.text
    assert "/onboarding/?mode=rerun" in body
    assert "Run onboarding interview" in body
