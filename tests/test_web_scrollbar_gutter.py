"""Regression test for the global scrollbar-gutter rule (#280).

The rule lives in static/app.css and reserves the vertical-scrollbar gutter
on every page. Without it, tabs whose content overflows vertically (Archive
with thousands of jobs) render ~15px narrower than tabs that don't, and
switching between them via hx-boost causes a horizontal layout shift.

Asserting on the served CSS catches the case where the rule is moved out
of `html { ... }` or otherwise lost in a future refactor; the bug only
reproduces visually so a unit test on the asset is the cheapest proxy.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY)")
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    return TestClient(create_app(companies_root=companies, db_path=db_path, base_root=tmp_path))


def test_app_css_reserves_scrollbar_gutter(client: TestClient) -> None:
    r = client.get("/static/app.css")
    assert r.status_code == 200
    # The rule must apply to <html>, not just <body> — applying it to <body>
    # leaves the document scroller (the html element) free to shift the
    # viewport when content overflows.
    assert re.search(
        r"html\s*\{[^}]*scrollbar-gutter:\s*stable",
        r.text,
        re.DOTALL,
    ), "html { scrollbar-gutter: stable } missing from app.css"
