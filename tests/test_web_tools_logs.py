"""Integration tests for GET /tools/logs/pipeline/ (#650)."""

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
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / "pipeline.jsonl"
    # Even though these tests write JSONL fixtures directly, redirect LOG_PATH
    # so any incidental log_event call during the request lands in tmp_path.
    monkeypatch.setattr(audit, "LOG_PATH", str(log_path))
    mark_complete(tmp_path)
    app = create_app(
        companies_root=tmp_path / "companies",
        db_path=db_path,
        base_root=tmp_path,
    )
    return TestClient(app, follow_redirects=False), log_path


def _write_events(path: Path, events: list[dict]) -> None:
    with path.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def test_log_viewer_returns_200_when_log_missing(client: tuple[TestClient, Path]) -> None:
    """Fresh stack has no pipeline.jsonl yet — viewer renders an empty table."""
    c, _ = client
    resp = c.get("/tools/logs/pipeline/")
    assert resp.status_code == 200
    assert "<table" in resp.text


def test_log_viewer_renders_events(client: tuple[TestClient, Path]) -> None:
    c, log_path = client
    _write_events(
        log_path,
        [
            {"ts": "2026-05-20T12:00:00Z", "event": "cron_started", "cron": "triage"},
            {"ts": "2026-05-20T12:05:00Z", "event": "cron_finished", "cron": "triage", "status": "succeeded"},
        ],
    )
    resp = c.get("/tools/logs/pipeline/")
    assert resp.status_code == 200
    assert "cron_started" in resp.text
    assert "cron_finished" in resp.text


def test_log_viewer_filter_by_event_narrows(client: tuple[TestClient, Path]) -> None:
    c, log_path = client
    _write_events(
        log_path,
        [
            {"ts": "2026-05-20T12:00:00Z", "event": "cron_started", "cron": "triage"},
            {"ts": "2026-05-20T12:05:00Z", "event": "cron_finished", "cron": "triage"},
            {"ts": "2026-05-20T12:10:00Z", "event": "scoring_complete"},
        ],
    )
    resp = c.get("/tools/logs/pipeline/?event=cron_started,cron_finished")
    assert resp.status_code == 200
    assert "cron_started" in resp.text
    assert "cron_finished" in resp.text
    assert "scoring_complete" not in resp.text


def test_log_viewer_malformed_line_silently_skipped(client: tuple[TestClient, Path]) -> None:
    """tail_events skips malformed lines — viewer must not 500."""
    c, log_path = client
    with log_path.open("w") as f:
        f.write('{"event": "good"}\n')
        f.write("not valid json at all\n")
        f.write('{"event": "also good"}\n')
    resp = c.get("/tools/logs/pipeline/")
    assert resp.status_code == 200
    assert "good" in resp.text


def test_log_viewer_typeahead_dropdown_lists_observed_events(client: tuple[TestClient, Path]) -> None:
    c, log_path = client
    _write_events(
        log_path,
        [
            {"ts": "t1", "event": "cron_started", "cron": "triage"},
            {"ts": "t2", "event": "pipeline_complete"},
        ],
    )
    resp = c.get("/tools/logs/pipeline/")
    assert resp.status_code == 200
    # Both observed names appear inside the dropdown markup
    assert 'value="cron_started"' in resp.text
    assert 'value="pipeline_complete"' in resp.text
