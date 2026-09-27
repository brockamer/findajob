"""Cross-site request rejection on the findajob web UI.

Browsers replay HTTP Basic Auth on cross-site form posts and ``fetch``
calls, so an internet-exposed instance behind ``BasicAuthMiddleware`` is
only as safe as its cross-site request check. ``CrossSiteRequestMiddleware``
rejects state-changing requests (POST, PUT, PATCH, DELETE) that a browser
marks as coming from another site.

Canary tests: the auth-enabled cases pin the middleware order (cross-site
check *inside* the auth gate). If anyone reorders middleware in ``app.py``
and either gate stops firing, these fail. Removing or weakening these
tests requires a deliberate review.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import init_test_db

TRIAGE = "/board/trigger-triage"
EVIL = "https://evil.example"


def _build_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from findajob import audit

    db_path = tmp_path / "pipeline.db"
    init_test_db(db_path)
    (tmp_path / "companies").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(audit, "LOG_PATH", str(tmp_path / "logs" / "pipeline.jsonl"))
    mark_complete(tmp_path)
    return create_app(companies_root=tmp_path / "companies", db_path=db_path, base_root=tmp_path)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("FINDAJOB_AUTH_USER", raising=False)
    monkeypatch.delenv("FINDAJOB_AUTH_PASS", raising=False)
    return TestClient(_build_app(tmp_path, monkeypatch), follow_redirects=False)


@pytest.fixture()
def auth_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FINDAJOB_AUTH_USER", "tester")
    monkeypatch.setenv("FINDAJOB_AUTH_PASS", "s3cret-token-xyz")
    return TestClient(_build_app(tmp_path, monkeypatch), follow_redirects=False)


def _basic(user: str, pw: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode("ascii")


def _post_triage(client: TestClient, **headers: str):
    """POST the dashboard triage trigger with Popen stubbed; return (response, popen)."""
    with patch("subprocess.Popen") as popen:
        resp = client.post(TRIAGE, headers=headers)
    return resp, popen


# --- Acceptance criteria ---------------------------------------------------


def test_cross_site_origin_post_is_rejected_without_state_change(client: TestClient) -> None:
    resp, popen = _post_triage(client, Origin=EVIL)
    assert resp.status_code == 403
    assert "cross-site" in resp.text.lower()
    popen.assert_not_called()


def test_same_origin_post_succeeds(client: TestClient) -> None:
    resp, popen = _post_triage(client, Origin="http://testserver")
    assert resp.status_code == 303
    popen.assert_called_once()


# --- Sec-Fetch-Site is authoritative when the browser sends it -------------


def test_sec_fetch_site_cross_site_is_rejected_even_with_matching_origin(client: TestClient) -> None:
    headers = {"Sec-Fetch-Site": "cross-site", "Origin": "http://testserver"}
    resp, popen = _post_triage(client, **headers)
    assert resp.status_code == 403
    popen.assert_not_called()


def test_sec_fetch_site_same_site_is_rejected(client: TestClient) -> None:
    """A sibling subdomain is not this app; no legitimate flow posts from one."""
    resp, popen = _post_triage(client, **{"Sec-Fetch-Site": "same-site"})
    assert resp.status_code == 403
    popen.assert_not_called()


def test_sec_fetch_site_none_is_allowed(client: TestClient) -> None:
    """``none`` is a user-initiated navigation (typed URL, bookmark), not a page."""
    resp, popen = _post_triage(client, **{"Sec-Fetch-Site": "none"})
    assert resp.status_code == 303
    popen.assert_called_once()


def test_sec_fetch_site_same_origin_wins_over_a_host_rewritten_by_a_proxy(client: TestClient) -> None:
    """A reverse proxy that rewrites ``Host`` to its upstream name must not lock
    the operator out: the browser's own same-origin verdict is authoritative."""
    headers = {"Sec-Fetch-Site": "same-origin", "Origin": "https://findajob.example.com"}
    resp, popen = _post_triage(client, **headers)
    assert resp.status_code == 303
    popen.assert_called_once()


# --- Origin fallback for browsers without Sec-Fetch-Site -------------------


def test_null_origin_is_rejected(client: TestClient) -> None:
    """``Origin: null`` is what a cross-origin redirect chain or a sandboxed
    frame sends — exactly the shapes a CSRF page produces."""
    resp, popen = _post_triage(client, Origin="null")
    assert resp.status_code == 403
    popen.assert_not_called()


def test_origin_host_compare_is_case_insensitive(client: TestClient) -> None:
    resp, popen = _post_triage(client, Origin="HTTP://TestServer")
    assert resp.status_code == 303
    popen.assert_called_once()


def test_origin_host_compare_ignores_port(client: TestClient) -> None:
    """Basic Auth is not replayed across ports, and default-port elision
    differs between proxies; the hostname is the boundary we compare."""
    resp, popen = _post_triage(client, Origin="http://testserver:8090")
    assert resp.status_code == 303
    popen.assert_called_once()


def test_ipv6_origin_matches_bracketed_host(client: TestClient) -> None:
    headers = {"Origin": "http://[::1]:8090", "Host": "[::1]:8090"}
    resp, popen = _post_triage(client, **headers)
    assert resp.status_code == 303
    popen.assert_called_once()


def test_x_forwarded_host_is_accepted_as_the_expected_host(client: TestClient) -> None:
    """Behind a proxy that rewrites ``Host`` but forwards the original, an
    older browser (Origin only, no Sec-Fetch-Site) still gets through."""
    headers = {
        "Origin": "https://findajob.example.com",
        "X-Forwarded-Host": "findajob.example.com, proxy.internal",
    }
    resp, popen = _post_triage(client, **headers)
    assert resp.status_code == 303
    popen.assert_called_once()


def test_origin_mismatching_both_host_and_forwarded_host_is_rejected(client: TestClient) -> None:
    headers = {"Origin": EVIL, "X-Forwarded-Host": "findajob.example.com"}
    resp, popen = _post_triage(client, **headers)
    assert resp.status_code == 403
    popen.assert_not_called()


def test_no_browser_headers_is_allowed(client: TestClient) -> None:
    """Scripts, curl and the test client send neither header; they are not
    a browser replaying credentials and must keep working."""
    resp, popen = _post_triage(client)
    assert resp.status_code == 303
    popen.assert_called_once()


# --- Scope: methods and paths ----------------------------------------------


def test_get_with_cross_site_origin_is_unaffected(client: TestClient) -> None:
    resp = client.get("/board/dashboard", headers={"Origin": EVIL})
    assert resp.status_code != 403


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_other_unsafe_methods_are_covered_before_routing(client: TestClient, method: str) -> None:
    """The check runs before routing, so it covers methods no route uses today."""
    resp = client.request(method, "/no/such/route", headers={"Origin": EVIL})
    assert resp.status_code == 403


def test_cross_site_delete_of_connections_leaves_the_file_in_place(client: TestClient, tmp_path: Path) -> None:
    target = tmp_path / "data" / "connections.csv"
    target.write_text("First Name,Last Name,Company,Position\n")
    resp = client.post("/settings/connections/remove", headers={"Origin": EVIL})
    assert resp.status_code == 403
    assert target.exists()


def test_rejection_is_logged_with_the_request_shape(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="findajob.web.middleware.cross_site"):
        _post_triage(client, Origin=EVIL)
    msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("POST" in m and TRIAGE in m and EVIL in m for m in msgs), msgs


# --- Middleware order canaries (auth gate outside, cross-site check inside) --


def test_cross_site_post_with_valid_credentials_is_rejected(auth_client: TestClient) -> None:
    """The attack this middleware exists for: the browser replays Basic Auth
    on a cross-site post. Auth passes; the cross-site check must not."""
    headers = {"Origin": EVIL, "Authorization": _basic("tester", "s3cret-token-xyz")}
    resp, popen = _post_triage(auth_client, **headers)
    assert resp.status_code == 403
    popen.assert_not_called()


def test_cross_site_post_without_credentials_gets_the_auth_challenge_first(auth_client: TestClient) -> None:
    resp, popen = _post_triage(auth_client, Origin=EVIL)
    assert resp.status_code == 401
    popen.assert_not_called()


def test_same_origin_post_with_valid_credentials_succeeds(auth_client: TestClient) -> None:
    headers = {"Origin": "http://testserver", "Authorization": _basic("tester", "s3cret-token-xyz")}
    resp, popen = _post_triage(auth_client, **headers)
    assert resp.status_code == 303
    popen.assert_called_once()
