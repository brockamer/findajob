"""Tests for GET + POST /settings/reject-reasons/ (#490).

Covers Task 6 (GET renders current values), Task 7 (full UX — structural
presence checked via the stub/full template), and Task 8 (POST validate+save).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob import config_loader
from findajob.onboarding import mark_complete
from findajob.web.app import create_app


@pytest.fixture
def yaml_path(tmp_path: Path) -> Path:
    p = tmp_path / "reject_reasons.yaml"
    p.write_text("reasons:\n  - Skills Mismatch\n  - Geography\n")
    return p


@pytest.fixture
def client(tmp_path: Path, yaml_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # 1. Point _REJECT_REASONS_PATH at the tmpdir YAML before create_app().
    monkeypatch.setattr(config_loader, "_REJECT_REASONS_PATH", yaml_path)

    # 2. Minimal schema — settings routes don't query jobs/audit_log, but
    #    create_app wires the full router which may reference them transitively
    #    (e.g., the nav spend chip). Use the same minimal schema as the
    #    cache_and_chips fixture to avoid surprises.
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
    conn.commit()
    conn.close()

    companies = tmp_path / "companies"
    companies.mkdir()

    # mark_complete must run BEFORE create_app so the onboarding flag is set.
    mark_complete(tmp_path)

    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_get_renders_current_reasons(client: TestClient) -> None:
    """GET /settings/reject-reasons/ shows seeded reasons from the YAML."""
    resp = client.get("/settings/reject-reasons/")
    assert resp.status_code == 200
    assert "Skills Mismatch" in resp.text
    assert "Geography" in resp.text


def test_post_happy_path_writes_yaml_and_returns_partial(client: TestClient, yaml_path: Path) -> None:
    resp = client.post(
        "/settings/reject-reasons/",
        data={
            "row_count": "2",
            "reason_0": "Skills Mismatch",
            "title_signal_0": "on",
            "reason_1": "Wrong Domain",
        },
    )
    assert resp.status_code == 200
    assert "Saved" in resp.text
    body = yaml_path.read_text()
    assert "Skills Mismatch" in body
    assert "Wrong Domain" in body
    assert "title_signal_reasons" in body


def test_post_strips_empty_rows(client: TestClient, yaml_path: Path) -> None:
    """Empty rows in the form (user clicked Add but didn't type) are dropped."""
    resp = client.post(
        "/settings/reject-reasons/",
        data={
            "row_count": "3",
            "reason_0": "Real Reason",
            "reason_1": "",
            "reason_2": "  ",  # whitespace-only
        },
    )
    assert resp.status_code == 200
    assert "Saved" in resp.text
    body = yaml_path.read_text()
    assert "Real Reason" in body


def test_post_validation_error_does_not_write(client: TestClient, yaml_path: Path) -> None:
    original = yaml_path.read_text()
    resp = client.post(
        "/settings/reject-reasons/",
        data={"row_count": "1", "reason_0": "with,comma"},
    )
    assert resp.status_code == 200  # HTMX error partial returns 200
    assert "Could not save" in resp.text
    assert "comma" in resp.text.lower()
    assert yaml_path.read_text() == original  # File unchanged


def test_post_empty_after_strip_returns_validation_error(client: TestClient, yaml_path: Path) -> None:
    original = yaml_path.read_text()
    resp = client.post(
        "/settings/reject-reasons/",
        data={"row_count": "1", "reason_0": ""},
    )
    assert resp.status_code == 200
    assert "Could not save" in resp.text
    assert yaml_path.read_text() == original


def test_initial_rows_in_script_block_not_inline_x_data(client: TestClient) -> None:
    """Regression test for the Add Reason broken-on-first-deploy bug.

    The seed JSON for Alpine's `rows` array MUST live in a
    <script id="initial-rows" type="application/json"> block, NOT inline
    inside the x-data="..." attribute. tojson emits unescaped " inside
    the JSON; if that lands inline in a double-quoted x-data attribute,
    the browser closes the attribute at the first " of "text" and Alpine
    never sees addRow/removeRow — the Add Reason button does nothing.

    The script-block pattern is:
        <script id="initial-rows" type="application/json">{{ rows|tojson }}</script>
        <div x-data="{ rows: JSON.parse(document.getElementById('initial-rows').textContent), ... }">

    This test catches a regression to the broken inline form by asserting:
      1. The script block is present
      2. The x-data attribute does not contain a literal `[{` (which is
         the start of the inlined JSON array — the broken-form smoking gun)
    """
    resp = client.get("/settings/reject-reasons/")
    assert resp.status_code == 200
    body = resp.text

    # Pin: JSON seed lives in a script block.
    assert '<script id="initial-rows" type="application/json">' in body, (
        "Initial rows must be in a <script type=application/json> block, "
        "not inline in x-data — see test docstring for the bug class."
    )
    # Pin: x-data does not contain inlined JSON array.
    # Find the x-data attribute and verify no [{ in its value.
    import re

    match = re.search(r'x-data\s*=\s*"([^"]*)"', body)
    assert match, "x-data attribute not found"
    x_data_value = match.group(1)
    assert "[{" not in x_data_value, f"x-data must not contain inline JSON array. Got: {x_data_value!r}"
