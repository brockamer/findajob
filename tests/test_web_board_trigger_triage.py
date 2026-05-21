"""Regression test for /board/trigger-triage after #650 refactor.

The route now delegates to dispatch_cron but must preserve the
banner's redirect destination (#752 contract).
"""

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
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "logs" / "pipeline.jsonl"))
    mark_complete(tmp_path)
    app = create_app(
        companies_root=tmp_path / "companies",
        db_path=db_path,
        base_root=tmp_path,
    )
    return TestClient(app, follow_redirects=False)


def test_banner_route_still_redirects_to_dashboard(client: TestClient) -> None:
    """#752 contract preserved: /board/trigger-triage 303s to /board/dashboard?triage_launched=1."""
    with patch("subprocess.Popen") as popen:
        resp = client.post("/board/trigger-triage")
    popen.assert_called_once()
    assert resp.status_code == 303
    assert resp.headers["location"] == "/board/dashboard?triage_launched=1"


def test_banner_route_emits_dispatched_event_with_banner_source(client: TestClient, tmp_path: Path) -> None:
    """The audit event must carry source='dashboard_banner' so /tools/ logs
    can distinguish banner clicks from tools-panel clicks.
    """
    with patch("subprocess.Popen"):
        client.post("/board/trigger-triage")
    log = (tmp_path / "logs" / "pipeline.jsonl").read_text()
    assert '"event": "web_cron_dispatched"' in log
    assert '"cron": "triage"' in log
    assert '"source": "dashboard_banner"' in log
