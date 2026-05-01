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
    created_at TEXT DEFAULT (datetime('now')),
    synthetic INTEGER NOT NULL DEFAULT 0
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


@pytest.fixture(autouse=True)
def _stub_openrouter_smoke_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Onboarding now smoke-checks the user-supplied OpenRouter key (#328).
    All web-route tests must stub this to avoid real network calls.
    """
    import findajob.onboarding.injector as inj_mod

    monkeypatch.setattr(inj_mod, "verify_openrouter_key", lambda _k: (True, None))


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


_TEST_OPENROUTER_KEY = "sk-or-v1-test-key"


def test_onboarding_index_returns_200(client: TestClient) -> None:
    resp = client.get("/onboarding/")
    assert resp.status_code == 200
    body = resp.text
    assert "onboarding" in body.lower()
    assert 'name="emission"' in body  # paste textarea
    assert "copy the prompt" in body.lower() or "Copy the prompt" in body


def test_paste_form_opts_out_of_hx_boost(client: TestClient) -> None:
    """Paste form must opt out of base.html's body-level hx-boost.

    The /onboarding/inject route returns 400 on validation errors
    (missing paste blocks, missing API key, OpenRouter smoke-check failed).
    HTMX by default does NOT swap 4xx responses — silently drops them — so
    a boosted form on a 400 leaves the page unchanged with no user feedback.
    Native form submit handles 4xx HTML responses correctly.
    """
    resp = client.get("/onboarding/")
    assert resp.status_code == 200
    # The exact form tag must contain hx-boost="false". A loose substring
    # check would pass if the attribute lived anywhere on the page; this
    # constrains it to the form opening tag.
    import re

    form_tag = re.search(
        r'<form\s+[^>]*action="/onboarding/inject"[^>]*>',
        resp.text,
    )
    assert form_tag is not None, "paste form not found"
    assert 'hx-boost="false"' in form_tag.group(0), (
        'Paste form must include hx-boost="false" so 400 responses from '
        "/onboarding/inject render natively. Without this, HTMX silently "
        "drops 4xx responses and the user sees no feedback."
    )


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


def test_inject_clean_emission_renders_completion_page(client: TestClient, tmp_path: _Path) -> None:
    blob = _read_fixture("alice-doe-clean-emission.txt")
    resp = client.post(
        "/onboarding/inject",
        data={"emission": blob, "openrouter_api_key": _TEST_OPENROUTER_KEY},
    )
    assert resp.status_code == 200
    assert "Onboarding complete" in resp.text
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

    resp = client.post(
        "/onboarding/inject",
        data={"emission": broken, "openrouter_api_key": _TEST_OPENROUTER_KEY},
    )
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
    resp = client.post(
        "/onboarding/inject",
        data={"emission": "", "openrouter_api_key": _TEST_OPENROUTER_KEY},
    )
    assert resp.status_code == 400
    body = resp.text
    assert "missing" in body.lower()
    assert not (tmp_path / "data" / ".onboarding-complete").exists()


def test_inject_missing_openrouter_key_rerenders_with_error(client: TestClient, tmp_path: _Path) -> None:
    """#328: posting a clean emission with no API key returns 400 with explanatory message."""
    blob = _read_fixture("alice-doe-clean-emission.txt")
    resp = client.post("/onboarding/inject", data={"emission": blob, "openrouter_api_key": ""})
    assert resp.status_code == 400
    assert "OpenRouter" in resp.text
    assert not (tmp_path / "data" / ".onboarding-complete").exists()


def test_inject_smoke_check_failure_rerenders_with_error(
    client: TestClient, tmp_path: _Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#328: when the smoke check rejects the key, render the error and don't write the sentinel."""
    import findajob.onboarding.injector as inj_mod

    monkeypatch.setattr(inj_mod, "verify_openrouter_key", lambda _k: (False, "401 Unauthorized"))

    blob = _read_fixture("alice-doe-clean-emission.txt")
    resp = client.post(
        "/onboarding/inject",
        data={"emission": blob, "openrouter_api_key": "bad-key"},
    )
    assert resp.status_code == 400
    assert "401 Unauthorized" in resp.text
    # Files committed (next paste-back will overwrite); sentinel NOT written
    assert (tmp_path / "candidate_context" / "profile.md").is_file()
    assert not (tmp_path / "data" / ".onboarding-complete").exists()


def test_inject_populates_companies_of_interest_from_tier1(client: TestClient, tmp_path: _Path) -> None:
    blob = _read_fixture("alice-doe-clean-emission.txt")
    resp = client.post(
        "/onboarding/inject",
        data={"emission": blob, "openrouter_api_key": _TEST_OPENROUTER_KEY},
    )
    assert resp.status_code == 200
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
