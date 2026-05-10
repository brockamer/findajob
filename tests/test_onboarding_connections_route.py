"""Tests for /onboarding/connections/{session_id}/* (#571).

The connections gate is the terminal step in the onboarding flow. Every path
through finalize_interview → gmail-config now ends here. The sentinel is
written exactly once: on either /upload (header-valid CSV accepted) or /skip.
"""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app

_SCHEMA = """
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


@pytest.fixture
def base_root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "companies").mkdir()
    (tmp_path / "candidate_context").mkdir()
    (tmp_path / "config").mkdir()
    db_path = tmp_path / "data" / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.close()
    return tmp_path


@pytest.fixture
def client(base_root: Path) -> TestClient:
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    return TestClient(app, follow_redirects=False)


SID = "test-session-id"

# Canonical LinkedIn-export header per findajob.find_contacts. Diverging from
# this contract (column rename, missing column) is the documented failure mode
# we validate against; see tests/test_find_contacts.py for the consumer side.
_VALID_HEADER = "First Name,Last Name,Company,Position,Connected On,URL\n"
_VALID_ROW = "Ada,Lovelace,Meta,Director,01 Jan 2020,https://example.com/ada\n"


def test_get_renders_explainer_and_both_forms(client: TestClient) -> None:
    """The gate page surfaces the LinkedIn-export instructions and posts to
    both the upload and skip endpoints under the session id."""
    response = client.get(f"/onboarding/connections/{SID}/")
    assert response.status_code == 200
    body = response.text
    assert "Upload your LinkedIn connections export" in body
    assert "linkedin.com/mypreferences/d/download-my-data" in body
    assert f"/onboarding/connections/{SID}/upload" in body
    assert f"/onboarding/connections/{SID}/skip" in body
    # Privacy framing must be present — important given the perimeter-only auth
    # model and the sensitivity of the file's contents. Match on a single
    # phrase that survives Jinja whitespace handling rather than asserting on
    # a multi-line copy substring.
    assert "never gets uploaded anywhere" in body


def test_post_skip_writes_sentinel_and_redirects(client: TestClient, base_root: Path) -> None:
    """Skip is always allowed — writes the sentinel and redirects to the
    dashboard regardless of whether a file was ever uploaded."""
    assert not (base_root / "data" / ".onboarding-complete").exists()
    response = client.post(f"/onboarding/connections/{SID}/skip")
    assert response.status_code == 303
    assert response.headers["location"] == "/board/dashboard"
    assert (base_root / "data" / ".onboarding-complete").is_file()
    # No connections.csv was written on skip.
    assert not (base_root / "data" / "connections.csv").exists()


def test_post_upload_happy_path_writes_file_and_sentinel(client: TestClient, base_root: Path) -> None:
    """A header-valid CSV is written to data/connections.csv atomically and
    the sentinel is written, then redirect to dashboard."""
    payload = (_VALID_HEADER + _VALID_ROW).encode("utf-8")
    response = client.post(
        f"/onboarding/connections/{SID}/upload",
        files={"connections_csv": ("Connections.csv", io.BytesIO(payload), "text/csv")},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/board/dashboard"
    dest = base_root / "data" / "connections.csv"
    assert dest.is_file()
    assert dest.read_bytes() == payload
    assert (base_root / "data" / ".onboarding-complete").is_file()


def test_post_upload_rejects_missing_required_column(client: TestClient, base_root: Path) -> None:
    """Header missing 'First Name' (the canonical failure mode that crashes
    the find_contacts consumer with KeyError) is rejected at the gate. No
    file write, no sentinel."""
    bad_header = "Given Name,Last Name,Company,Position,Connected On,URL\n"
    payload = (bad_header + "Ada,Lovelace,Meta,Director,01 Jan 2020,https://example.com/ada\n").encode("utf-8")
    response = client.post(
        f"/onboarding/connections/{SID}/upload",
        files={"connections_csv": ("Connections.csv", io.BytesIO(payload), "text/csv")},
    )
    assert response.status_code == 400
    assert "First Name" in response.text
    assert not (base_root / "data" / "connections.csv").exists()
    assert not (base_root / "data" / ".onboarding-complete").exists()


def test_post_upload_rejects_linkedin_preamble(client: TestClient, base_root: Path) -> None:
    """LinkedIn occasionally adds a 'Notes:' preamble above the header row.
    Validation must reject this with guidance — silent acceptance would
    write a file that produces zero matches for every prep run."""
    preamble = (
        'Notes:\n"When exporting your connections, you may notice that some of the email addresses are missing."\n\n'
    )
    payload = (preamble + _VALID_HEADER + _VALID_ROW).encode("utf-8")
    response = client.post(
        f"/onboarding/connections/{SID}/upload",
        files={"connections_csv": ("Connections.csv", io.BytesIO(payload), "text/csv")},
    )
    assert response.status_code == 400
    # Error message must mention the preamble and how to fix it.
    assert "Notes:" in response.text
    assert not (base_root / "data" / "connections.csv").exists()
    assert not (base_root / "data" / ".onboarding-complete").exists()


def test_post_upload_rejects_empty_file(client: TestClient, base_root: Path) -> None:
    """An empty upload (zero bytes) is rejected with a clear pointer back to
    LinkedIn — silent acceptance would land an empty file."""
    response = client.post(
        f"/onboarding/connections/{SID}/upload",
        files={"connections_csv": ("Connections.csv", io.BytesIO(b""), "text/csv")},
    )
    assert response.status_code == 400
    assert "empty" in response.text.lower()
    assert not (base_root / "data" / "connections.csv").exists()
    assert not (base_root / "data" / ".onboarding-complete").exists()


def test_post_upload_handles_utf8_bom(client: TestClient, base_root: Path) -> None:
    """LinkedIn occasionally ships the CSV with a UTF-8 BOM. The decoder must
    swallow it so header validation still passes — this is the documented
    failure mode of utf-8 (vs utf-8-sig) decoding."""
    payload = ("﻿" + _VALID_HEADER + _VALID_ROW).encode("utf-8")
    response = client.post(
        f"/onboarding/connections/{SID}/upload",
        files={"connections_csv": ("Connections.csv", io.BytesIO(payload), "text/csv")},
    )
    assert response.status_code == 303
    assert (base_root / "data" / "connections.csv").is_file()
    assert (base_root / "data" / ".onboarding-complete").is_file()
