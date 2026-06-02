"""Tests for GET + POST /onboarding/timezone/{session_id}/ (#989).

The deterministic timezone-capture step, inserted between the interview
finalize and the spend-ceiling step. The browser-resolved IANA zone
(client-side Intl.DateTimeFormat) pre-selects the picker; the server-side
default falls back to whatever the LLM already wrote to data/timezone at
finalize, so the LLM-conversion path is preserved when no browser value is
available.

Covers:
- GET renders the zone picker + the browser-detection JS
- GET pre-selects the existing (LLM-derived) pick as the server-side default
- POST with a valid zone writes data/timezone + redirects to spend-ceiling
- POST propagates voice_redact_failed to the next step
- POST with blank/invalid input does NOT clobber the existing LLM value
  (graceful fallback) and still proceeds
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.timeutil import read_timezone_file
from findajob.web.app import create_app

SID = "test-onboarding-sid"
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
"""


@pytest.fixture()
def base_root(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "companies").mkdir()
    (tmp_path / "config").mkdir()
    db_path = tmp_path / "data" / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.close()
    return tmp_path


@pytest.fixture()
def client(base_root: Path) -> TestClient:
    app = create_app(
        companies_root=base_root / "companies",
        db_path=base_root / "data" / "pipeline.db",
        base_root=base_root,
    )
    return TestClient(app, follow_redirects=False)


def _tz_file(base_root: Path) -> Path:
    return base_root / "data" / "timezone"


def _seed_llm_value(base_root: Path, zone: str) -> None:
    """Simulate what the interview finalize already wrote to data/timezone."""
    _tz_file(base_root).write_text(zone + "\n", encoding="utf-8")


# ── GET ───────────────────────────────────────────────────────────────────────


def test_get_renders_picker_and_browser_detection_js(client: TestClient) -> None:
    resp = client.get(f"/onboarding/timezone/{SID}/")
    assert resp.status_code == 200
    # Picker lists real zones.
    assert "Europe/Berlin" in resp.text
    assert "Asia/Tokyo" in resp.text
    # Browser detection is wired client-side.
    assert "Intl.DateTimeFormat" in resp.text
    assert "resolvedOptions" in resp.text


def test_get_renders_hidden_voice_redact_field_when_flagged(client: TestClient) -> None:
    """The GET side of the voice_redact_failed propagation chain: when flagged,
    the page renders the hidden input the form resubmits on POST."""
    resp = client.get(f"/onboarding/timezone/{SID}/?voice_redact_failed=1")
    assert resp.status_code == 200
    assert '<input type="hidden" name="voice_redact_failed" value="1">' in resp.text


def test_get_omits_hidden_voice_redact_field_when_not_flagged(client: TestClient) -> None:
    """No flag → no hidden input, so the next step isn't spuriously warned."""
    resp = client.get(f"/onboarding/timezone/{SID}/")
    assert resp.status_code == 200
    assert 'name="voice_redact_failed"' not in resp.text


def test_get_preselects_existing_llm_value(client: TestClient, base_root: Path) -> None:
    """The LLM-derived pick from finalize is the server-side default selection."""
    _seed_llm_value(base_root, "Asia/Tokyo")
    resp = client.get(f"/onboarding/timezone/{SID}/")
    assert resp.status_code == 200
    assert 'value="Asia/Tokyo" selected' in resp.text


# ── POST ──────────────────────────────────────────────────────────────────────


def test_post_valid_zone_writes_and_redirects(client: TestClient, base_root: Path) -> None:
    resp = client.post(f"/onboarding/timezone/{SID}/", data={"timezone": "Europe/Berlin"})
    assert resp.status_code == 303
    assert f"/onboarding/spend-ceiling/{SID}/" in resp.headers["location"]
    assert read_timezone_file(base_root) == "Europe/Berlin"


def test_post_overrides_llm_value(client: TestClient, base_root: Path) -> None:
    """A confirmed browser/operator pick supersedes the LLM-written value."""
    _seed_llm_value(base_root, "Asia/Tokyo")
    client.post(f"/onboarding/timezone/{SID}/", data={"timezone": "Europe/Berlin"})
    assert read_timezone_file(base_root) == "Europe/Berlin"


def test_post_propagates_voice_redact_failed(client: TestClient) -> None:
    resp = client.post(
        f"/onboarding/timezone/{SID}/",
        data={"timezone": "Europe/Berlin", "voice_redact_failed": "1"},
    )
    assert resp.status_code == 303
    assert "voice_redact_failed=1" in resp.headers["location"]


def test_post_blank_preserves_llm_value_and_proceeds(client: TestClient, base_root: Path) -> None:
    """Graceful fallback: blank submission keeps the LLM pick, still advances."""
    _seed_llm_value(base_root, "Asia/Tokyo")
    resp = client.post(f"/onboarding/timezone/{SID}/", data={"timezone": "   "})
    assert resp.status_code == 303
    assert f"/onboarding/spend-ceiling/{SID}/" in resp.headers["location"]
    assert read_timezone_file(base_root) == "Asia/Tokyo"


def test_post_invalid_preserves_llm_value_and_proceeds(client: TestClient, base_root: Path) -> None:
    """Garbage submission must not clobber the LLM pick; flow continues."""
    _seed_llm_value(base_root, "Asia/Tokyo")
    resp = client.post(f"/onboarding/timezone/{SID}/", data={"timezone": "Not/AZone"})
    assert resp.status_code == 303
    assert read_timezone_file(base_root) == "Asia/Tokyo"
