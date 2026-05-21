"""Tests for GET + POST /settings/excluded-employers/ (#729).

Covers the AC: GET renders current values, POST writes via the validated
save_excluded_employers path, regex-compile errors don't touch the file,
malformed-file load surfaces an inline banner rather than 500, and the
Alpine seed JSON lives in <script> blocks (regression test for the #490
inline-x-data attribute-collision bug class).
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
def yaml_path(tmp_path: Path) -> Path:
    p = tmp_path / "excluded_employers.yaml"
    p.write_text("exact:\n  - 'Apple'\n  - 'PriorCo'\nregex:\n  - '^state\\s+of\\s+\\w+$'\n")
    return p


@pytest.fixture
def client(tmp_path: Path, yaml_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(config_loader, "_EXCLUDED_EMPLOYERS_PATH", yaml_path)

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

    mark_complete(tmp_path)

    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_get_renders_current_values(client: TestClient) -> None:
    """GET /settings/excluded-employers/ shows seeded exact + regex entries."""
    resp = client.get("/settings/excluded-employers/")
    assert resp.status_code == 200
    body = resp.text
    assert "Apple" in body
    assert "PriorCo" in body
    assert r"^state\s+of\s+\w+$" in body


def test_get_missing_file_renders_empty_editor(client: TestClient, yaml_path: Path) -> None:
    """Missing file → empty editor, no banner (this is the fresh-install path)."""
    yaml_path.unlink()
    resp = client.get("/settings/excluded-employers/")
    assert resp.status_code == 200
    assert "File could not be loaded" not in resp.text


def test_post_happy_path_writes_yaml(client: TestClient, yaml_path: Path) -> None:
    resp = client.post(
        "/settings/excluded-employers/",
        data={
            "exact_count": "2",
            "exact_0": "Apple",
            "exact_1": "OldCo",
            "regex_count": "1",
            "regex_0": r"\b(parent|holdings|group)\b",
        },
    )
    assert resp.status_code == 200
    assert "Saved" in resp.text

    data = yaml.safe_load(yaml_path.read_text())
    assert data["exact"] == ["Apple", "OldCo"]
    assert data["regex"] == [r"\b(parent|holdings|group)\b"]


def test_post_strips_empty_rows(client: TestClient, yaml_path: Path) -> None:
    """Empty rows in the form (user clicked Add but didn't type) are dropped."""
    resp = client.post(
        "/settings/excluded-employers/",
        data={
            "exact_count": "3",
            "exact_0": "RealCo",
            "exact_1": "",
            "exact_2": "  ",
            "regex_count": "0",
        },
    )
    assert resp.status_code == 200
    assert "Saved" in resp.text

    data = yaml.safe_load(yaml_path.read_text())
    assert data["exact"] == ["RealCo"]
    assert data["regex"] == []


def test_post_invalid_regex_does_not_write(client: TestClient, yaml_path: Path) -> None:
    """A regex that doesn't compile surfaces an error and leaves the file alone."""
    original = yaml_path.read_text()
    resp = client.post(
        "/settings/excluded-employers/",
        data={
            "exact_count": "0",
            "regex_count": "1",
            "regex_0": "[unclosed",
        },
    )
    assert resp.status_code == 200  # HTMX error partial returns 200
    assert "Could not save" in resp.text
    assert "invalid regex" in resp.text.lower()
    assert yaml_path.read_text() == original  # File unchanged


def test_post_duplicate_exact_does_not_write(client: TestClient, yaml_path: Path) -> None:
    """Duplicate exact entries (case-insensitive) surface an error."""
    original = yaml_path.read_text()
    resp = client.post(
        "/settings/excluded-employers/",
        data={
            "exact_count": "2",
            "exact_0": "Apple",
            "exact_1": "apple",
            "regex_count": "0",
        },
    )
    assert resp.status_code == 200
    assert "Could not save" in resp.text
    assert "duplicate" in resp.text.lower()
    assert yaml_path.read_text() == original


def test_post_empty_save_writes_explicit_empty_lists(client: TestClient, yaml_path: Path) -> None:
    """Operator removes every row + saves → file becomes explicit empty
    lists, not absent and not malformed."""
    resp = client.post(
        "/settings/excluded-employers/",
        data={"exact_count": "0", "regex_count": "0"},
    )
    assert resp.status_code == 200
    assert "Saved" in resp.text
    data = yaml.safe_load(yaml_path.read_text())
    assert data == {"exact": [], "regex": []}


def test_post_then_get_roundtrip(client: TestClient, yaml_path: Path) -> None:
    """POST writes a regex pattern; subsequent GET shows it (loader is now
    read-per-call, so the next request sees the new file without process
    restart — the load_excluded_employers cache refactor)."""
    resp = client.post(
        "/settings/excluded-employers/",
        data={
            "exact_count": "0",
            "regex_count": "1",
            "regex_0": r"\bAcmeCorp\b",
        },
    )
    assert resp.status_code == 200
    assert "Saved" in resp.text

    get_resp = client.get("/settings/excluded-employers/")
    assert get_resp.status_code == 200
    assert "AcmeCorp" in get_resp.text


def test_get_malformed_file_shows_banner(client: TestClient, yaml_path: Path) -> None:
    """Malformed YAML structure (e.g. 'exact' as a string) → banner, no 500."""
    yaml_path.write_text("exact: not-a-list\n")
    resp = client.get("/settings/excluded-employers/")
    assert resp.status_code == 200
    assert "File could not be loaded" in resp.text


def test_initial_rows_in_script_blocks_not_inline_x_data(client: TestClient) -> None:
    """Regression test for the Alpine x-data attribute-collision bug class
    (see #490 / tests/test_settings_reject_reasons.py for the original).

    Pattern: <script type="application/json">{{ rows|tojson }}</script>
    then x-data="{ rows: JSON.parse(...) }". Inlining the JSON in x-data's
    double-quoted attribute value would close the attribute at the first
    " inside the JSON.
    """
    resp = client.get("/settings/excluded-employers/")
    assert resp.status_code == 200
    body = resp.text

    assert '<script id="initial-exact" type="application/json">' in body
    assert '<script id="initial-regex" type="application/json">' in body

    import re

    match = re.search(r'x-data\s*=\s*"([^"]*)"', body)
    assert match, "x-data attribute not found"
    x_data_value = match.group(1)
    assert "[{" not in x_data_value, f"x-data must not contain inline JSON array. Got: {x_data_value!r}"
