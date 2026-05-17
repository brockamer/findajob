"""Tests for GET + POST /settings/active-sources/ + dashboard banner (#603).

Covers:
- GET renders the registry as checkboxes, pre-checked from current state
- POST persists the selection atomically to config/active_sources.txt
- POST validates submitted names against REGISTERED_ADAPTERS
- Dashboard banner appears when file absent + no dismissed cookie
- Banner suppressed when file present OR cookie set

The settings route mirrors the established `/settings/reject-reasons/`
pattern (#490). Tests use the same fixture shape as
`tests/test_settings_reject_reasons.py`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.fetchers.adapters import registry
from findajob.onboarding import mark_complete
from findajob.web.app import create_app


@pytest.fixture
def active_sources_path(tmp_path: Path) -> Path:
    """Tmp location for config/active_sources.txt — file does NOT exist yet
    so tests can choose to write it or not. Routes resolve via
    `_active_sources_path()`; we monkeypatch that in `client` fixture."""
    return tmp_path / "config" / "active_sources.txt"


@pytest.fixture
def client(tmp_path: Path, active_sources_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Create config/ dir but leave active_sources.txt unwritten — tests opt in.
    active_sources_path.parent.mkdir(parents=True, exist_ok=True)

    # Point registry._active_sources_path() at our tmp file.
    monkeypatch.setattr(registry, "_active_sources_path", lambda: active_sources_path)
    # Point _onboarding_complete_path() at a nonexistent tmp path too (#681) — without
    # this, tests run in the "active_sources.txt absent + sentinel present" cell on
    # any environment where BASE/data/.onboarding-complete exists, which would flip
    # several banner/render expectations. The dedicated mark_complete() call below
    # writes its own sentinel under tmp_path/data/.onboarding-complete.
    monkeypatch.setattr(registry, "_onboarding_complete_path", lambda: tmp_path / "data" / ".onboarding-complete")

    # Minimal pipeline.db schema for the dashboard route.
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs ("
        "id TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, company TEXT, "
        "stage TEXT, reject_reason TEXT, relevance_score INTEGER, "
        "fit_score REAL, probability_score REAL, interview_likelihood INTEGER, "
        "location TEXT, remote_status TEXT, known_contacts TEXT, user_notes TEXT, "
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

    # Onboarding-complete sentinel must exist BEFORE create_app so the
    # _guard dependency lets settings routes through.
    mark_complete(tmp_path)

    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


# ───────────────────── GET /settings/active-sources/ ─────────────────────


def test_get_renders_every_registered_adapter(client: TestClient) -> None:
    """GET shows one row per `REGISTERED_ADAPTERS` entry, including future
    adapters added after this PR — no hardcoded list."""
    resp = client.get("/settings/active-sources/")
    assert resp.status_code == 200
    for cls in registry.REGISTERED_ADAPTERS:
        assert cls.name in resp.text, f"adapter '{cls.name}' missing from settings page"
        assert cls.display_name in resp.text


def _checked_adapters(html: str) -> set[str]:
    """Parse rendered HTML and return the set of adapter names whose
    `<input name="adapter" value="...">` checkbox has `checked` set."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    checked = set()
    for box in soup.find_all("input", attrs={"type": "checkbox", "name": "adapter"}):
        if box.has_attr("checked"):
            checked.add(box["value"])
    return checked


def test_get_pre_checks_default_when_file_absent(client: TestClient, active_sources_path: Path) -> None:
    """When config/active_sources.txt doesn't exist, the page pre-checks
    every adapter in `_DEFAULT_ACTIVE_SOURCES`."""
    assert not active_sources_path.exists()
    resp = client.get("/settings/active-sources/")
    assert resp.status_code == 200
    checked = _checked_adapters(resp.text)
    assert checked == set(registry._DEFAULT_ACTIVE_SOURCES)


def test_get_pre_checks_custom_set_when_file_present(client: TestClient, active_sources_path: Path) -> None:
    """When the file exists, only the listed names are pre-checked — the
    explicit file overrides the default."""
    active_sources_path.write_text("jobs-api14\nashby\n")
    resp = client.get("/settings/active-sources/")
    assert resp.status_code == 200
    checked = _checked_adapters(resp.text)
    assert checked == {"jobs-api14", "ashby"}


def test_get_renders_per_adapter_configured_badge(client: TestClient) -> None:
    """Every adapter row shows a Fetchable / Not configured badge."""
    resp = client.get("/settings/active-sources/")
    assert resp.status_code == 200
    body = resp.text
    # At least one of these badge phrases must appear (most adapters will
    # be Not configured in a test env without env vars / config files set).
    assert "Fetchable" in body or "Not configured" in body, "is_configured badge missing from settings page"


# ───────────────────── POST /settings/active-sources/ ─────────────────────


def test_post_writes_selected_adapters_to_file(client: TestClient, active_sources_path: Path) -> None:
    """Submitted checkbox set is persisted to active_sources.txt."""
    resp = client.post(
        "/settings/active-sources/",
        data={"adapter": ["jobs-api14", "greenhouse"]},
    )
    assert resp.status_code == 200
    assert "Saved" in resp.text
    assert active_sources_path.exists()
    body = active_sources_path.read_text()
    assert "jobs-api14" in body
    assert "greenhouse" in body
    # Other adapter names must NOT appear (write replaces the file entirely).
    assert "ashby" not in body
    assert "gmail" not in body


def test_post_round_trips_through_read_active_sources(client: TestClient, active_sources_path: Path) -> None:
    """File written by the route parses cleanly via `_read_active_sources`.
    Lockstep regression: prevents drift between the writer's format and
    the reader's expectations."""
    client.post("/settings/active-sources/", data={"adapter": ["jobs-api14", "lever"]})
    parsed = registry._read_active_sources(active_sources_path)
    assert set(parsed) == {"jobs-api14", "lever"}


def test_post_validates_adapter_names_against_registry(client: TestClient, active_sources_path: Path) -> None:
    """Submitted name not in REGISTERED_ADAPTERS → 400 error, file unchanged."""
    active_sources_path.write_text("jobs-api14\n")
    original = active_sources_path.read_text()
    resp = client.post(
        "/settings/active-sources/",
        data={"adapter": ["jobs-api14", "made-up-adapter"]},
    )
    # HTMX error partials return 200 with error message (matches reject-reasons pattern).
    assert resp.status_code == 200
    assert "Could not save" in resp.text or "Unknown adapter" in resp.text
    assert "made-up-adapter" in resp.text  # error message names the offending value
    assert active_sources_path.read_text() == original  # File untouched


def test_post_empty_selection_writes_header_only(client: TestClient, active_sources_path: Path) -> None:
    """Empty checkbox set → file with only header comment. `_read_active_sources`
    treats this as empty → falls back to default → banner re-appears."""
    resp = client.post("/settings/active-sources/", data=[])
    assert resp.status_code == 200
    assert "Saved" in resp.text
    assert active_sources_path.exists()
    parsed = registry._read_active_sources(active_sources_path)
    # Empty file body (header only) falls back to default per existing behavior.
    assert parsed == list(registry._DEFAULT_ACTIVE_SOURCES)


def test_post_atomic_write_via_tmp_rename(
    client: TestClient, active_sources_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Save uses tmp + rename so a crash mid-write doesn't truncate the file.

    Verified by patching os.replace and asserting both that the .tmp file was
    created AND that the rename target is the canonical path.
    """
    import os

    rename_calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def _spy_replace(src: str, dst: str) -> None:
        rename_calls.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _spy_replace)
    client.post("/settings/active-sources/", data={"adapter": "jobs-api14"})

    assert rename_calls, "atomic write must use os.replace (tmp + rename)"
    src, dst = rename_calls[0]
    assert dst == str(active_sources_path)
    assert src.endswith(".tmp") or "tmp" in src.split("/")[-1]


# ───────────────────── Dashboard banner ─────────────────────


def test_dashboard_banner_appears_when_file_absent(client: TestClient, active_sources_path: Path) -> None:
    """Stack using default adapter selection → banner surfaces on dashboard."""
    assert not active_sources_path.exists()
    resp = client.get("/board/dashboard")
    assert resp.status_code == 200
    # The banner copy mentions the settings page so operators can navigate.
    assert "/settings/active-sources/" in resp.text
    # And mentions "default adapter selection" or similar phrasing.
    assert "default" in resp.text.lower()


def test_dashboard_banner_suppressed_when_file_present(client: TestClient, active_sources_path: Path) -> None:
    """File exists → operator made an explicit choice → banner suppressed."""
    active_sources_path.write_text("jobs-api14\n")
    resp = client.get("/board/dashboard")
    assert resp.status_code == 200
    # No prompt to /settings/active-sources/ on the dashboard once configured.
    # (The settings page itself remains accessible via direct nav.)
    body = resp.text
    # Look for the specific banner phrase, not the path in general (which
    # could appear in nav/footer in future).
    assert "default adapter selection" not in body.lower()


def test_dashboard_banner_suppressed_when_dismissed_cookie_set(client: TestClient, active_sources_path: Path) -> None:
    """Operator clicked 'Don't show again' → cookie suppresses even when file absent."""
    assert not active_sources_path.exists()
    resp = client.get(
        "/board/dashboard",
        cookies={"active_sources_banner_dismissed": "1"},
    )
    assert resp.status_code == 200
    assert "default adapter selection" not in resp.text.lower()
