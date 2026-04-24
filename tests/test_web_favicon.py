"""GET /favicon.ico returns the SVG favicon, suppressing the legacy 404 (#138).

Browsers default-request /favicon.ico at the document root regardless of any
<link rel="icon"> tag, so the route must exist even though base.html points
modern browsers at /static/favicon.svg.
"""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE jobs (id TEXT)")
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    app = create_app(companies_root=companies, db_path=db, base_root=tmp_path)
    return TestClient(app)


def test_favicon_ico_returns_svg(client: TestClient) -> None:
    r = client.get("/favicon.ico")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert r.text.lstrip().startswith("<svg")
