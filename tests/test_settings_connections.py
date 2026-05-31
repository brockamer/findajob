"""Tests for /settings/connections/ — returning-user maintenance UI (#614).

The settings surface complements the onboarding gate (#571): both share
``findajob.web.connections_upload`` for validation + atomic write. These
tests cover the maintenance-specific surface (state rendering, refresh,
remove + confirm/cancel) plus a parity check that the onboarding sentinel
is NEVER touched by any settings action.
"""

from __future__ import annotations

import io
import os
import sqlite3
import time
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

_VALID_HEADER = "First Name,Last Name,Company,Position,Connected On,URL\n"
_ROW_A = "Ada,Lovelace,Meta,Director,01 Jan 2020,https://example.com/ada\n"
_ROW_B = "Linus,Torvalds,Linux Foundation,Fellow,01 Jan 2021,https://example.com/linus\n"
_ROW_C = "Grace,Hopper,Navy,Admiral,01 Jan 1985,https://example.com/grace\n"


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
    # Pretend onboarding is complete so the guard doesn't redirect every
    # request to /onboarding/. The sentinel is the canonical signal.
    (tmp_path / "data" / ".onboarding-complete").touch()
    return tmp_path


@pytest.fixture
def client(base_root: Path) -> TestClient:
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    return TestClient(app, follow_redirects=False)


def _seed_connections(base_root: Path, *rows: str, mtime_seconds_ago: float = 0) -> Path:
    """Write a connections.csv with the given rows + optional mtime override.

    Default mtime is now; pass ``mtime_seconds_ago`` to age the file for
    relative-timestamp tests ("3 weeks ago" etc.).
    """
    path = base_root / "data" / "connections.csv"
    payload = _VALID_HEADER + "".join(rows)
    path.write_text(payload, encoding="utf-8")
    if mtime_seconds_ago:
        age = time.time() - mtime_seconds_ago
        os.utime(path, (age, age))
    return path


# -----------------------------------------------------------------------------
# GET / — empty state and present state
# -----------------------------------------------------------------------------


def test_get_empty_state_shows_upload_and_explainer_no_remove(client: TestClient) -> None:
    """Without a connections.csv the page renders the upload form + explainer
    but NO remove affordance — there's nothing to remove."""
    response = client.get("/settings/connections/")
    assert response.status_code == 200
    body = response.text
    assert "LinkedIn connections" in body
    assert "No connections file in your findajob yet" in body
    assert 'action="/settings/connections/upload"' in body
    # Explainer included from the shared partial.
    assert "linkedin.com/mypreferences/d/download-my-data" in body
    # Remove zone is suppressed when there's no file.
    assert "Remove file" not in body


def test_get_present_state_shows_row_count_and_last_imported(client: TestClient, base_root: Path) -> None:
    """With a connections.csv the page renders row count + last-imported
    (absolute PT timestamp + relative humanized age) + remove zone."""
    _seed_connections(base_root, _ROW_A, _ROW_B, _ROW_C, mtime_seconds_ago=3 * 7 * 86400)
    response = client.get("/settings/connections/")
    assert response.status_code == 200
    body = response.text
    # Row count surfaces (header excluded — 3 data rows).
    assert "Rows:" in body
    assert ">3<" in body
    # Last-imported has both absolute (PT timezone strftime) and relative parts.
    assert "Last imported:" in body
    # PT timestamp format: "YYYY-MM-DD HH:MM PST" or "...PDT" — assert on the
    # canonical zone-abbrev shape that strftime("%Z") emits.
    assert "PT" in body or "PST" in body or "PDT" in body
    # Relative form for a 3-week-old file lands in the "X weeks ago" branch.
    assert "weeks ago" in body
    # Remove zone is visible because the file exists.
    assert "Remove file" in body


# -----------------------------------------------------------------------------
# POST /upload — refresh / replace, success and validation errors
# -----------------------------------------------------------------------------


def test_post_upload_replaces_existing_file_atomically(client: TestClient, base_root: Path) -> None:
    """A refresh upload overwrites the existing file via atomic replace and
    renders the page with save_success surfaced."""
    _seed_connections(base_root, _ROW_A)
    new_payload = (_VALID_HEADER + _ROW_B + _ROW_C).encode("utf-8")
    response = client.post(
        "/settings/connections/upload",
        files={"connections_csv": ("Connections.csv", io.BytesIO(new_payload), "text/csv")},
    )
    assert response.status_code == 200
    assert "File refreshed" in response.text
    # The file on disk reflects the new payload, not the old single row.
    dest = base_root / "data" / "connections.csv"
    assert dest.read_bytes() == new_payload


def test_post_upload_rejects_missing_required_column(client: TestClient, base_root: Path) -> None:
    """The shared validator rejects bad headers identically to the onboarding
    path — invalid uploads 400 with the column-missing message and do NOT
    overwrite the existing good file."""
    _seed_connections(base_root, _ROW_A)
    before = (base_root / "data" / "connections.csv").read_bytes()

    bad_header = "Given Name,Last Name,Company,Position,Connected On,URL\n"
    payload = (bad_header + _ROW_A).encode("utf-8")
    response = client.post(
        "/settings/connections/upload",
        files={"connections_csv": ("Connections.csv", io.BytesIO(payload), "text/csv")},
    )
    assert response.status_code == 400
    assert "First Name" in response.text
    # Existing file untouched (validation failed before atomic_write_connections).
    after = (base_root / "data" / "connections.csv").read_bytes()
    assert before == after


def test_post_upload_handles_utf8_bom(client: TestClient, base_root: Path) -> None:
    """UTF-8 BOM is stripped by the shared validator (parity with the
    onboarding path's BOM test)."""
    payload = ("﻿" + _VALID_HEADER + _ROW_A).encode("utf-8")
    response = client.post(
        "/settings/connections/upload",
        files={"connections_csv": ("Connections.csv", io.BytesIO(payload), "text/csv")},
    )
    assert response.status_code == 200
    assert (base_root / "data" / "connections.csv").is_file()


# -----------------------------------------------------------------------------
# Remove confirm / cancel / commit
# -----------------------------------------------------------------------------


def test_get_remove_confirm_renders_confirm_zone(client: TestClient, base_root: Path) -> None:
    """The confirm endpoint returns a partial that surfaces the row count +
    LinkedIn re-export warning. Does NOT delete the file."""
    _seed_connections(base_root, _ROW_A, _ROW_B)
    response = client.get("/settings/connections/remove/confirm")
    assert response.status_code == 200
    body = response.text
    assert "Remove connections.csv from your findajob?" in body
    assert "Current rows: 2" in body
    assert "re-export from LinkedIn" in body
    # File still on disk — confirm is a UI-only swap.
    assert (base_root / "data" / "connections.csv").is_file()


def test_get_remove_cancel_restores_initial_zone(client: TestClient, base_root: Path) -> None:
    """Cancel re-renders the initial Remove-file zone partial. File untouched."""
    _seed_connections(base_root, _ROW_A)
    response = client.get("/settings/connections/remove/cancel")
    assert response.status_code == 200
    body = response.text
    assert "Remove file" in body
    assert (base_root / "data" / "connections.csv").is_file()


def test_post_remove_deletes_file_and_renders_success(client: TestClient, base_root: Path) -> None:
    """POST /remove deletes the file and renders the page with remove_success
    surfaced and the empty-state copy shown."""
    _seed_connections(base_root, _ROW_A, _ROW_B)
    response = client.post("/settings/connections/remove")
    assert response.status_code == 200
    body = response.text
    assert "Removed." in body
    assert "No connections file in your findajob yet" in body
    assert not (base_root / "data" / "connections.csv").exists()


def test_post_remove_is_idempotent_on_missing_file(client: TestClient, base_root: Path) -> None:
    """Removing an already-missing file is a no-op (no 500) — find_contacts
    already tolerates the missing-file case, and the user shouldn't see a
    crash because a parallel session beat them to the delete."""
    response = client.post("/settings/connections/remove")
    assert response.status_code == 200
    assert "Removed." in response.text


# -----------------------------------------------------------------------------
# Sentinel guard: settings_connections must NOT mark onboarding complete
# -----------------------------------------------------------------------------


def test_settings_never_touches_onboarding_sentinel(client: TestClient, base_root: Path) -> None:
    """The onboarding sentinel is one-shot — written by /onboarding/connections/
    at first-run completion only. None of the /settings/connections/ endpoints
    should write or touch it. Regression guard: silently re-marking would let
    a stuck-mid-onboarding stack escape the guard via a settings click.

    Test must leave the sentinel present so the onboarding guard doesn't
    short-circuit the handler with a 307 to /onboarding/ — without that,
    the assertion below is vacuous (the handler never runs, of course it
    doesn't touch the file). Instead: snapshot mtime before each call,
    assert it's unchanged after. Inverting the production code
    (temporarily adding ``mark_complete(base)`` to a handler) must fail
    this test for it to be load-bearing.
    """
    sentinel = base_root / "data" / ".onboarding-complete"
    assert sentinel.exists(), "fixture must seed the sentinel"
    mtime_before = sentinel.stat().st_mtime_ns

    payload = (_VALID_HEADER + _ROW_A).encode("utf-8")
    client.post(
        "/settings/connections/upload",
        files={"connections_csv": ("Connections.csv", io.BytesIO(payload), "text/csv")},
    )
    assert sentinel.stat().st_mtime_ns == mtime_before

    client.get("/settings/connections/remove/confirm")
    assert sentinel.stat().st_mtime_ns == mtime_before

    client.get("/settings/connections/remove/cancel")
    assert sentinel.stat().st_mtime_ns == mtime_before

    client.post("/settings/connections/remove")
    assert sentinel.stat().st_mtime_ns == mtime_before
