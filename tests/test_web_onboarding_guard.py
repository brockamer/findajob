"""Integration tests for the NUX guard dependency (#148)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import init_test_db


@pytest.fixture()
def unconfigured_client(tmp_path: Path) -> TestClient:
    """Stack with no sentinel = not yet onboarded."""
    db_path = tmp_path / "pipeline.db"
    init_test_db(db_path)
    (tmp_path / "companies").mkdir()
    app = create_app(
        companies_root=tmp_path / "companies",
        db_path=db_path,
        base_root=tmp_path,
    )
    return TestClient(app, follow_redirects=False)


@pytest.fixture()
def configured_client(tmp_path: Path) -> TestClient:
    """Stack with sentinel written = onboarded."""
    db_path = tmp_path / "pipeline.db"
    init_test_db(db_path)
    (tmp_path / "companies").mkdir()
    mark_complete(tmp_path)
    app = create_app(
        companies_root=tmp_path / "companies",
        db_path=db_path,
        base_root=tmp_path,
    )
    return TestClient(app, follow_redirects=False)


# ---- Gated routes redirect when unconfigured ----


@pytest.mark.parametrize("path", ["/", "/board/dashboard", "/materials/", "/stats/funnel"])
def test_gated_routes_redirect_without_sentinel(unconfigured_client: TestClient, path: str) -> None:
    """`/` joined the gated set in #339 Task 9 — a fresh stack drops the
    visitor straight into onboarding instead of the marketing landing page."""
    resp = unconfigured_client.get(path)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/onboarding/"


# ---- Gated routes pass through when configured ----


@pytest.mark.parametrize("path", ["/", "/board/dashboard", "/stats/funnel"])
def test_gated_routes_pass_with_sentinel(configured_client: TestClient, path: str) -> None:
    resp = configured_client.get(path)
    # 200 or a different redirect — anything NOT a 307 to /onboarding/
    assert not (resp.status_code == 307 and resp.headers.get("location") == "/onboarding/")


# ---- Ungated routes are always reachable ----


@pytest.mark.parametrize("path", ["/healthz", "/config/", "/tools/", "/ingest/"])
def test_ungated_routes_reachable_without_sentinel(unconfigured_client: TestClient, path: str) -> None:
    resp = unconfigured_client.get(path)
    assert not (resp.status_code == 307 and resp.headers.get("location") == "/onboarding/")


# ---- Nav widgets that poll guarded endpoints must not render pre-onboarding ----
#
# Regression: /notifications/badge is on the guarded notifications router. On
# the unonboarded onboarding page, the badge poll 307'd to /onboarding/; HTMX
# followed the redirect and outerHTML-swapped the full onboarding page body
# into the badge slot, which contained another _nav.html with another badge
# whose hx-trigger="load" fired again. Browsers logged a tight loop of
# alternating /onboarding/ 200 and /notifications/badge 307 requests.


# Parametrized over every ungated HTML-rendering route — the contract is "no
# ungated page polls a guarded endpoint pre-onboarding" so a future ungated
# route can't quietly regress this loop without tripping the test.
@pytest.mark.parametrize("path", ["/onboarding/", "/tools/", "/docs/", "/config/"])
def test_ungated_page_does_not_poll_guarded_badge(unconfigured_client: TestClient, path: str) -> None:
    resp = unconfigured_client.get(path)
    assert resp.status_code == 200
    assert 'id="nav-notif-badge"' not in resp.text
    assert "/notifications/badge" not in resp.text


def test_guarded_page_still_renders_badge_when_configured(configured_client: TestClient) -> None:
    resp = configured_client.get("/board/dashboard")
    assert resp.status_code == 200
    assert 'id="nav-notif-badge"' in resp.text
    assert 'hx-get="/notifications/badge"' in resp.text


# ---- #619: HX-Request-aware guard (defense-in-depth for any future nav widget) ----
#
# #618 fixed the acute redirect-loop bug by gating the badge `<li>` template-side
# so no ungated page emits an HTMX poll element. This block guards the same bug
# class at the boundary: if a future developer adds a new nav widget that polls
# a different guarded endpoint and forgets the template gate, the guard itself
# returns HTMX-native semantics (200 + HX-Redirect header) instead of a 307 the
# HTMX runtime would follow and outerHTML-swap into the trigger element.


@pytest.mark.parametrize(
    "path",
    ["/notifications/badge", "/board/dashboard", "/stats/funnel"],
)
def test_hx_request_to_guarded_endpoint_returns_hx_redirect(unconfigured_client: TestClient, path: str) -> None:
    resp = unconfigured_client.get(path, headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert resp.headers.get("HX-Redirect") == "/onboarding/"


@pytest.mark.parametrize(
    "path",
    ["/notifications/badge", "/board/dashboard", "/stats/funnel"],
)
def test_non_hx_request_to_guarded_endpoint_still_returns_307(unconfigured_client: TestClient, path: str) -> None:
    resp = unconfigured_client.get(path)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/onboarding/"
    assert "HX-Redirect" not in resp.headers
