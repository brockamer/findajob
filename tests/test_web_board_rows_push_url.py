"""#1218 — a /rows route answers HTMX only, and pushes the page URL.

The filter inputs fetch ``/board/<tab>/rows``. Before #1218 they pushed that
request URL into the address bar, so a reload (or Copy link) returned the bare
rows fragment. Two rules now hold for every tab:

- An HTMX swap of ``#rows`` gets ``HX-Push-Url: /board/<tab>?<query>``, so the
  address bar holds the page URL. Archive load-more swaps its own ``<tr>`` and
  must not touch the address bar.
- A request that expects a page (no ``HX-Request``, a boosted link, or an htmx
  history restore) gets a 303 to the page with the same querystring.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import init_test_db

TABS = ("dashboard", "applied", "review", "waitlist", "fallback", "rejected", "not-selected", "archive")

QUERY = "company=acme&title=lead"

FILTER_SWAP = {"HX-Request": "true", "HX-Target": "rows"}

PAGE_REQUESTS = {
    "plain": {},
    "boosted": {"HX-Request": "true", "HX-Boosted": "true"},
    "history-restore": {"HX-Request": "true", "HX-History-Restore-Request": "true"},
}


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    db_path = tmp_path / "pipeline.db"
    init_test_db(db_path)
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    app = create_app(companies_root=companies, db_path=db_path, base_root=tmp_path)
    yield TestClient(app)


@pytest.mark.parametrize("tab", TABS)
def test_filter_swap_pushes_page_url(client: TestClient, tab: str) -> None:
    r = client.get(f"/board/{tab}/rows?{QUERY}", headers=FILTER_SWAP)
    assert r.status_code == 200
    assert r.headers["HX-Push-Url"] == f"/board/{tab}?{QUERY}"
    assert "<html" not in r.text


def test_filter_swap_without_query_pushes_bare_page_url(client: TestClient) -> None:
    r = client.get("/board/dashboard/rows", headers=FILTER_SWAP)
    assert r.status_code == 200
    assert r.headers["HX-Push-Url"] == "/board/dashboard"


def test_archive_load_more_does_not_push(client: TestClient) -> None:
    # The load-more <tr> has no id and no hx-target, so htmx sends no HX-Target.
    r = client.get(f"/board/archive/rows?{QUERY}&offset=100", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "HX-Push-Url" not in r.headers


@pytest.mark.parametrize("kind", sorted(PAGE_REQUESTS))
@pytest.mark.parametrize("tab", TABS)
def test_page_request_redirects_to_page(client: TestClient, tab: str, kind: str) -> None:
    r = client.get(f"/board/{tab}/rows?{QUERY}", headers=PAGE_REQUESTS[kind], follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/board/{tab}?{QUERY}"


def test_page_request_without_query_redirects_to_bare_page(client: TestClient) -> None:
    r = client.get("/board/dashboard/rows", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/board/dashboard"


def test_stale_rows_url_reload_renders_full_page(client: TestClient) -> None:
    """A bookmark or history entry saved before the fix still renders a page."""
    r = client.get("/board/dashboard/rows?cols=title,company")
    assert r.status_code == 200
    assert str(r.url).endswith("/board/dashboard?cols=title,company")
    assert "<html" in r.text
    assert "Columns" in r.text


@pytest.mark.parametrize("tab", TABS)
def test_page_renders_one_cols_input(client: TestClient, tmp_path: Path, tab: str) -> None:
    """The Columns dropdown's input is the only ``cols`` the filter inputs send.

    A second hidden ``cols`` in the table header sent the old value last, and
    Starlette's ``QueryParams.get`` keeps the last value: a column change was
    ignored, and the pushed URL reloaded into the old columns.
    """
    # The fallback page renders no table (so no header) until it has a row.
    conn = sqlite3.connect(tmp_path / "pipeline.db")
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, stage, relevance_score, source) "
        "VALUES ('fb1', 'fp-fb1', 'https://x.test/fb1', 'Engineer', 'Acme', 'withdrawn_fallback', 7, 'manual')"
    )
    conn.commit()
    conn.close()
    r = client.get(f"/board/{tab}?cols=title,company")
    assert r.status_code == 200
    assert r.text.count('name="cols"') == 1
