"""HX-Request gate on HTMX-only state-changing routes.

Second line of defence behind ``CrossSiteRequestMiddleware``. A cross-site
HTML form cannot set a custom header at all, and cross-site JavaScript can
only send one after a CORS preflight this app never approves — so requiring
``HX-Request`` on routes that only HTMX ever calls stops a cross-site post
even from a browser too old to send ``Sec-Fetch-Site`` or ``Origin``.

The inventory test is the contract: every unsafe route either carries
``require_htmx`` or is named in ``PLAIN_FORM_ROUTES`` below. Adding a route
means classifying it. A route named here must NOT carry the gate — the list
records handlers reached by a plain ``<form method="post">``, a ``fetch``
call, or an HTMX-aware handler with a deliberate non-HTMX fallback.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.timeutil import read_timezone_file
from findajob.web.app import create_app
from findajob.web.htmx_guard import require_htmx
from tests.conftest import init_test_db

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# (method, path) pairs of unsafe routes that are reached without HTMX.
PLAIN_FORM_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        # <form method="post"> in templates
        ("POST", "/board/trigger-triage"),
        ("POST", "/board/{tab}/reset-view"),
        ("POST", "/board/{tab}/reset-filter/{name}"),
        ("POST", "/ingest/speculative"),
        ("POST", "/speculative/approve/{request_id}"),
        ("POST", "/speculative/regenerate/{request_id}"),
        ("POST", "/speculative/trash/{request_id}"),
        ("POST", "/materials/{fingerprint}/podcast/{podcast_format}"),
        ("POST", "/materials/{fingerprint}/study-guide"),
        ("POST", "/materials/{fingerprint}/flashcards"),
        ("POST", "/materials/{fingerprint}/regenerate"),
        ("POST", "/materials/{fingerprint}/rerun-interview-prep"),
        ("POST", "/materials/{fingerprint}/continue-prep"),
        ("POST", "/materials/{fingerprint}/reject"),
        ("POST", "/onboarding/auth"),
        ("POST", "/onboarding/keys"),
        ("POST", "/onboarding/interview/start"),
        ("POST", "/onboarding/interview/{session_id}/finalize"),
        ("POST", "/onboarding/connections/{session_id}/upload"),
        ("POST", "/onboarding/connections/{session_id}/skip"),
        ("POST", "/onboarding/feed-config/{session_id}"),
        ("POST", "/onboarding/feed-config/{session_id}/finish"),
        ("POST", "/onboarding/gmail-config/{session_id}/finish"),
        ("POST", "/onboarding/gmail-config/{session_id}/skip"),
        ("POST", "/onboarding/restore/upload"),
        ("POST", "/onboarding/spend-ceiling/{session_id}/"),
        ("POST", "/onboarding/timezone/{session_id}/"),
        ("POST", "/settings/backup/download"),
        ("POST", "/settings/connections/upload"),
        ("POST", "/settings/connections/remove"),
        ("POST", "/settings/gemini/"),
        ("POST", "/tools/trigger-cron/{slug}"),
        ("POST", "/update/now"),
        # fetch() from static/onboarding-stream.js
        ("POST", "/onboarding/interview/turn-stream"),
        # HTMX-aware handlers with a deliberate non-HTMX (redirect) fallback
        ("POST", "/notifications/{notif_id}/read"),
        ("POST", "/notifications/mark-all-read"),
        ("POST", "/board/filter-proposals/{proposal_id}/apply"),
        ("POST", "/board/filter-proposals/{proposal_id}/skip"),
        ("POST", "/board/filter-proposals/{proposal_id}/revert"),
        ("POST", "/board/rejections-review/{suggestion_id}/confirm"),
        ("POST", "/board/rejections-review/{suggestion_id}/dismiss"),
        ("POST", "/board/rejections-review/{suggestion_id}/reattribute"),
    }
)


def _build_app(tmp_path: Path):
    db_path = tmp_path / "pipeline.db"
    init_test_db(db_path)
    (tmp_path / "companies").mkdir()
    mark_complete(tmp_path)
    return create_app(companies_root=tmp_path / "companies", db_path=db_path, base_root=tmp_path)


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return TestClient(_build_app(tmp_path), follow_redirects=False)


def _carries_gate(route: APIRoute) -> bool:
    return any(dep.call is require_htmx for dep in route.dependant.dependencies)


# --- Behaviour --------------------------------------------------------------


def test_htmx_only_route_without_header_is_rejected_without_state_change(client: TestClient, tmp_path: Path) -> None:
    resp = client.post("/settings/timezone/", data={"timezone": "Europe/Berlin"})
    assert resp.status_code == 403
    assert resp.json() == {"detail": "This action is only available from the findajob UI."}
    assert read_timezone_file(tmp_path) is None


def test_htmx_only_route_with_header_proceeds(client: TestClient, tmp_path: Path) -> None:
    resp = client.post("/settings/timezone/", data={"timezone": "Europe/Berlin"}, headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert read_timezone_file(tmp_path) == "Europe/Berlin"


def test_get_on_a_gated_router_is_unaffected(client: TestClient) -> None:
    resp = client.get("/settings/timezone/")
    assert resp.status_code == 200


def test_plain_form_route_does_not_need_the_header(client: TestClient) -> None:
    from unittest.mock import patch

    with patch("subprocess.Popen"):
        resp = client.post("/board/trigger-triage")
    assert resp.status_code == 303


# --- Inventory contract -----------------------------------------------------


def _unsafe_routes(tmp_path: Path) -> dict[tuple[str, str], APIRoute]:
    app = _build_app(tmp_path)
    found: dict[tuple[str, str], APIRoute] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods & UNSAFE_METHODS:
            found[(method, route.path)] = route
    return found


def test_every_unsafe_route_is_classified(tmp_path: Path) -> None:
    """Every POST/PUT/PATCH/DELETE route carries ``require_htmx`` or is listed
    as a plain-form route. An unclassified route is a new route nobody has
    decided about yet."""
    routes = _unsafe_routes(tmp_path)
    unclassified = sorted(
        key for key, route in routes.items() if not _carries_gate(route) and key not in PLAIN_FORM_ROUTES
    )
    assert unclassified == [], f"unsafe routes with neither require_htmx nor a PLAIN_FORM_ROUTES entry: {unclassified}"


def test_plain_form_routes_do_not_carry_the_gate(tmp_path: Path) -> None:
    """A route reached by a plain form or fetch must not demand HX-Request,
    or the browser gets a 403 on a legitimate submit."""
    routes = _unsafe_routes(tmp_path)
    gated_but_listed = sorted(key for key in PLAIN_FORM_ROUTES if key in routes and _carries_gate(routes[key]))
    assert gated_but_listed == [], f"PLAIN_FORM_ROUTES entries that carry require_htmx: {gated_but_listed}"


def test_plain_form_routes_list_has_no_stale_entries(tmp_path: Path) -> None:
    routes = _unsafe_routes(tmp_path)
    stale = sorted(key for key in PLAIN_FORM_ROUTES if key not in routes)
    assert stale == [], f"PLAIN_FORM_ROUTES entries that no longer exist: {stale}"
