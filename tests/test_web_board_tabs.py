"""Secondary tab bar on every /board/* view (issue #191).

Each board page includes board/_tabs.html which renders six tabs
(Dashboard, Applied, Waitlist, Review, Rejected, Archive). The active tab
is visually distinct via aria-current="page".
"""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app

TAB_LINKS = [
    "/board/dashboard",
    "/board/applied",
    "/board/waitlist",
    "/board/review",
    "/board/rejected",
    "/board/archive",
]


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, company TEXT, "
        "stage TEXT, fit_score REAL, probability_score REAL, relevance_score INTEGER, "
        "interview_likelihood INTEGER, location TEXT, remote_status TEXT, known_contacts TEXT, "
        "comp_estimate TEXT, ai_notes TEXT, user_notes TEXT, score_flag_reason TEXT, "
        "source TEXT, reject_reason TEXT, url TEXT, created_at TEXT, stage_updated TEXT, "
        "prep_folder_path TEXT)"
    )
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, job_id TEXT, field_changed TEXT, "
        "old_value TEXT, new_value TEXT, changed_at TEXT, changed_by TEXT)"
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


@pytest.mark.parametrize("path", TAB_LINKS)
def test_every_board_page_renders_full_tab_bar(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200, f"{path} returned {r.status_code}"
    for href in TAB_LINKS:
        assert f'href="{href}"' in r.text, f"{path} missing tab link to {href}"


@pytest.mark.parametrize("path", TAB_LINKS)
def test_active_tab_marked_aria_current(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    # The active tab renders href="<path>" followed shortly by aria-current="page".
    # Pull out the substring starting at the active href and verify the marker
    # appears before the next </a> tag.
    idx = r.text.index(f'href="{path}"')
    snippet = r.text[idx : idx + 400]
    assert 'aria-current="page"' in snippet, f"{path} did not mark itself active"
