"""Integration tests for POST /tools/trigger-cron/{slug} (#650)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import init_test_db


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from findajob import audit

    db_path = tmp_path / "pipeline.db"
    init_test_db(db_path)
    (tmp_path / "companies").mkdir()
    (tmp_path / "logs").mkdir()
    # Redirect log_event writes to tmp_path so dispatch_cron's audit events
    # don't pollute the global LOG_PATH and the test can assert against them.
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "logs" / "pipeline.jsonl"))
    mark_complete(tmp_path)
    app = create_app(
        companies_root=tmp_path / "companies",
        db_path=db_path,
        base_root=tmp_path,
    )
    return TestClient(app, follow_redirects=False)


def test_post_trigger_cron_happy_path_303(client: TestClient) -> None:
    with patch("subprocess.Popen") as popen:
        resp = client.post("/tools/trigger-cron/notify-stats")
    popen.assert_called_once()
    assert resp.status_code == 303
    assert resp.headers["location"] == "/tools/?triggered=notify-stats"


def test_post_trigger_cron_unknown_slug_404(client: TestClient) -> None:
    resp = client.post("/tools/trigger-cron/no-such-cron")
    assert resp.status_code == 404


def test_post_trigger_cron_disabled_slug_409(client: TestClient) -> None:
    resp = client.post("/tools/trigger-cron/notify-scoreboard")
    assert resp.status_code == 409


def test_post_trigger_cron_get_returns_405(client: TestClient) -> None:
    """GET on the trigger endpoint must 405 — POST-only."""
    resp = client.get("/tools/trigger-cron/notify-stats")
    assert resp.status_code == 405
