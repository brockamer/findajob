"""Regression (#1033): the Fly-substrate update banner must NOT claim the Fly
dashboard 'Deploy' button delivers a new release.

Clicking Deploy on a web-launched Fly app does not pull a new upstream findajob
release — a 'Use a public repo' launch has no upstream-connected Deploy button,
and a fork's main is frozen until the user syncs it. The pre-#1033 banner said
'click Deploy to redeploy the latest', a false confirmation that strands users
on stale code. The banner must instead point to the update how-to.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web import update_check
from findajob.web.app import create_app
from tests.conftest import init_test_db


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)  # onboarding sentinel so /board/dashboard renders
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def _force_update_available(monkeypatch) -> None:
    """Make update_banner_state emit a banner: patch the running version low and
    seed a fresh (non-stale) cache with a higher latest. The conftest autouse
    fixture stamps latest=None to suppress the banner by default; override it."""
    monkeypatch.setattr(update_check, "findajob_version", lambda: "1.0.0")
    update_check._cache["checked_at"] = update_check._now()
    update_check._cache["latest"] = "2.0.0"


def test_fly_banner_does_not_claim_dashboard_deploy_updates(client, monkeypatch) -> None:
    monkeypatch.setenv("FLY_APP_NAME", "findajob-test")  # detect_substrate() -> "fly"
    _force_update_available(monkeypatch)

    r = client.get("/board/dashboard")
    assert r.status_code == 200
    # The banner is actually showing (guards against a vacuous pass).
    assert "Update available" in r.text
    assert "v1.0.0" in r.text and "v2.0.0" in r.text
    # Honest fly guidance + the how-to pointer are present.
    assert "pull a new release" in r.text  # "...Deploy button alone won't pull a new release"
    assert "how to update" in r.text
    # The misleading pre-#1033 copy must be gone (the load-bearing assertion).
    assert "redeploy the latest" not in r.text


def test_docker_banner_branch_unbroken(client, monkeypatch) -> None:
    monkeypatch.delenv("FLY_APP_NAME", raising=False)  # detect_substrate() -> "docker"
    _force_update_available(monkeypatch)

    r = client.get("/board/dashboard")
    assert r.status_code == 200
    assert "Update available" in r.text
    assert "docker compose pull" in r.text
    # Fly-only phrasing must not leak into the docker branch.
    assert "pull a new release" not in r.text
