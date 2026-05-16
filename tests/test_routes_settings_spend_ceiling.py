"""Tests for GET + POST /settings/spend-ceiling/ (#671).

Covers:
- GET shows the form with current ceiling or "No ceiling set"
- POST with applies_per_week recommendation writes computed value
- POST with override writes the numeric value
- POST with action=disable writes "disabled" sentinel
- Atomic write via tmp + os.replace
- Recommendation formula matches SCORING_FLOOR_USD + n * 4.3 * PER_PREP_USD
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob import config_loader
from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from findajob.web.routes.settings_spend_ceiling import (
    _APPLIES_PER_WEEK_OPTIONS,
    PER_PREP_USD,
    SCORING_FLOOR_USD,
    _recommended_ceiling,
)


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """Minimal app with a real (migration-applied) DB and onboarding complete."""
    from findajob.db.migrate import apply_pending

    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    try:
        apply_pending(conn)
    finally:
        conn.close()

    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db_path, base_root=tmp_path))


def _ceiling_file(tmp_path: Path) -> Path:
    return tmp_path / "config" / "spend_ceiling.txt"


# ── GET /settings/spend-ceiling/ ─────────────────────────────────────────────


def test_get_shows_no_ceiling_when_absent(client: TestClient) -> None:
    """No spend_ceiling.txt → page shows 'No ceiling set'."""
    # conftest already redirects _SPEND_CEILING_PATH to a non-existent fixture
    resp = client.get("/settings/spend-ceiling/")
    assert resp.status_code == 200
    assert "No ceiling set" in resp.text


def test_get_shows_current_ceiling_when_set(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When spend_ceiling.txt contains a value, current_ceiling is displayed."""
    p = tmp_path / "spend_ceiling.txt"
    p.write_text("75.00")
    monkeypatch.setattr(config_loader, "_SPEND_CEILING_PATH", p)

    resp = client.get("/settings/spend-ceiling/")
    assert resp.status_code == 200
    assert "75.00" in resp.text


def test_get_renders_applies_per_week_options(client: TestClient) -> None:
    """Recommendation form includes all applies_per_week options."""
    resp = client.get("/settings/spend-ceiling/")
    assert resp.status_code == 200
    for n in _APPLIES_PER_WEEK_OPTIONS:
        assert str(n) in resp.text


# ── POST with recommendation ──────────────────────────────────────────────────


def test_post_recommendation_writes_computed_ceiling(client: TestClient, tmp_path: Path) -> None:
    """POSTing applies_per_week=3 writes SCORING_FLOOR + 3*4.3*PER_PREP to file."""
    resp = client.post(
        "/settings/spend-ceiling/",
        data={"action": "save", "ceiling_override": "", "applies_per_week": "3"},
    )
    assert resp.status_code == 200
    assert "Ceiling set" in resp.text

    f = _ceiling_file(tmp_path)
    assert f.exists()
    written = float(f.read_text().strip())
    expected = _recommended_ceiling(3)
    assert abs(written - expected) < 0.01


def test_recommendation_formula_matches_constants() -> None:
    """Formula: SCORING_FLOOR_USD + n * 4.3 * PER_PREP_USD (unit test, no HTTP)."""
    for n in _APPLIES_PER_WEEK_OPTIONS:
        expected = SCORING_FLOOR_USD + n * 4.3 * PER_PREP_USD
        assert abs(_recommended_ceiling(n) - expected) < 0.001


# ── POST with override ────────────────────────────────────────────────────────


def test_post_override_writes_numeric_value(client: TestClient, tmp_path: Path) -> None:
    """ceiling_override=42.50 writes 42.50 to the file."""
    resp = client.post(
        "/settings/spend-ceiling/",
        data={"action": "save", "ceiling_override": "42.50", "applies_per_week": "3"},
    )
    assert resp.status_code == 200
    assert "42.50" in resp.text

    f = _ceiling_file(tmp_path)
    assert f.exists()
    assert float(f.read_text().strip()) == pytest.approx(42.50)


def test_post_invalid_override_returns_error(client: TestClient, tmp_path: Path) -> None:
    """Non-numeric override → error message, file not written."""
    resp = client.post(
        "/settings/spend-ceiling/",
        data={"action": "save", "ceiling_override": "abc", "applies_per_week": "3"},
    )
    assert resp.status_code == 200
    assert "Could not save" in resp.text or "Invalid" in resp.text
    assert not _ceiling_file(tmp_path).exists()


def test_post_zero_override_returns_error(client: TestClient, tmp_path: Path) -> None:
    """Zero override → error message (must be positive)."""
    resp = client.post(
        "/settings/spend-ceiling/",
        data={"action": "save", "ceiling_override": "0", "applies_per_week": "3"},
    )
    assert resp.status_code == 200
    assert "Could not save" in resp.text or "greater than zero" in resp.text


# ── POST disable ─────────────────────────────────────────────────────────────


def test_post_disable_writes_disabled_sentinel(client: TestClient, tmp_path: Path) -> None:
    """action=disable writes 'disabled' to the file."""
    resp = client.post(
        "/settings/spend-ceiling/",
        data={"action": "disable"},
    )
    assert resp.status_code == 200
    assert "disabled" in resp.text.lower()

    f = _ceiling_file(tmp_path)
    assert f.exists()
    # load_spend_ceiling must interpret this as None
    import findajob.config_loader as cl
    from findajob.config_loader import load_spend_ceiling

    original = cl._SPEND_CEILING_PATH
    cl._SPEND_CEILING_PATH = f
    try:
        assert load_spend_ceiling() is None
    finally:
        cl._SPEND_CEILING_PATH = original


# ── Atomic write ──────────────────────────────────────────────────────────────


def test_post_uses_atomic_write(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Write goes through tmp + os.replace (not direct open)."""
    import os

    rename_calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def _spy(src: str, dst: str) -> None:
        rename_calls.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _spy)
    client.post(
        "/settings/spend-ceiling/",
        data={"action": "save", "ceiling_override": "30.00", "applies_per_week": "3"},
    )
    assert rename_calls, "atomic write must use os.replace"
    _, dst = rename_calls[0]
    assert dst == str(_ceiling_file(tmp_path))
