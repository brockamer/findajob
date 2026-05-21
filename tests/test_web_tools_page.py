"""Integration tests for the extended /tools/ page (#650)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import init_test_db


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    from findajob import audit

    db_path = tmp_path / "pipeline.db"
    init_test_db(db_path)
    (tmp_path / "companies").mkdir()
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "logs" / "pipeline.jsonl"))
    mark_complete(tmp_path)
    app = create_app(
        companies_root=tmp_path / "companies",
        db_path=db_path,
        base_root=tmp_path,
    )
    return TestClient(app, follow_redirects=False), tmp_path


def test_tools_page_renders_all_seven_trigger_tiles(client: tuple[TestClient, Path]) -> None:
    c, _ = client
    resp = c.get("/tools/")
    assert resp.status_code == 200
    for slug in (
        "triage",
        "detect-rejections",
        "discover",
        "notify-health",
        "notify-stats",
        "watchdog",
        "notify-scoreboard",
    ):
        assert f'data-cron-slug="{slug}"' in resp.text, f"Missing tile for {slug}"


def test_tools_page_renders_disabled_state_for_notify_scoreboard(client: tuple[TestClient, Path]) -> None:
    c, _ = client
    resp = c.get("/tools/")
    assert resp.status_code == 200
    assert 'data-cron-slug="notify-scoreboard"' in resp.text
    # Tile-specific copy from _trigger_tile.html — narrow assertion that
    # avoids false-positive from any "disabled" class elsewhere in the page.
    assert "Disabled in scheduled-jobs.yaml" in resp.text


def test_tools_page_renders_running_state_for_unmatched_cron_started(client: tuple[TestClient, Path]) -> None:
    from datetime import UTC, datetime

    c, tmp_path = client
    # Use the current timestamp so is_currently_running's age check fires
    # (triage max_runtime_minutes=120; a hardcoded past date would age out).
    with (tmp_path / "logs" / "pipeline.jsonl").open("w") as f:
        f.write(
            json.dumps(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "event": "cron_started",
                    "cron": "triage",
                }
            )
            + "\n"
        )
    resp = c.get("/tools/")
    assert resp.status_code == 200
    # The triage tile must render as running
    assert 'data-cron-slug="triage"' in resp.text
    assert "Running" in resp.text  # tile copy includes "Running…"


def test_tools_page_still_renders_legacy_prompt_tiles(client: tuple[TestClient, Path]) -> None:
    """Phase 1 / #150 surface preserved alongside the new triggers."""
    c, _ = client
    resp = c.get("/tools/")
    assert resp.status_code == 200
    assert "Refresh your profile" in resp.text or "profile_refresh" in resp.text
