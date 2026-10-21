"""Page-content links are visibly links (#1225).

Tailwind's preflight (loaded from the CDN in base.html) resets every ``<a>`` to
``color: inherit; text-decoration: inherit``, so the bare anchors on
``/materials/`` looked like plain text. ``static/app.css`` restores a link
style for *unclassed* anchors inside ``<main>`` only. These tests pin the three
parts that must stay true together: the rule exists, the job entries are
unclassed anchors it matches, and the nav / Tailwind-styled links are classed
so the rule cannot change them.
"""

from __future__ import annotations

import re
import sqlite3
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from tests.conftest import init_test_db


class _AnchorCollector(HTMLParser):
    """Collect every ``<a>`` with its attributes, text, and whether it sits in
    ``<main>`` / ``<th>``."""

    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[dict] = []
        self._main = 0
        self._th = 0
        self._open: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "main":
            self._main += 1
        elif tag == "th":
            self._th += 1
        elif tag == "a":
            self._open = {"attrs": dict(attrs), "text": "", "in_main": self._main > 0, "in_th": self._th > 0}
            self.anchors.append(self._open)

    def handle_endtag(self, tag: str) -> None:
        if tag == "main":
            self._main -= 1
        elif tag == "th":
            self._th -= 1
        elif tag == "a":
            self._open = None

    def handle_data(self, data: str) -> None:
        if self._open is not None:
            self._open["text"] += data


def _anchors(html: str) -> list[dict]:
    p = _AnchorCollector()
    p.feed(html)
    return p.anchors


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    conn = sqlite3.connect(db)
    for fp, title, company, stage in (
        ("fp-flight", "Widget Engineer", "Acme Widgets", "materials_drafted"),
        ("fp-rejected", "Gadget Analyst", "Globex Gadgets", "rejected"),
    ):
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"id-{fp}", fp, "https://example.com/job", title, company, "test", stage),
        )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def _rules(css: str) -> list[tuple[str, dict[str, str]]]:
    """Parse flat ``selector { prop: value; ... }`` blocks (comments stripped)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        decls = {}
        for d in m.group(2).split(";"):
            if ":" in d:
                k, v = d.split(":", 1)
                decls[k.strip()] = v.strip()
        out.append((" ".join(m.group(1).split()), decls))
    return out


def test_app_css_styles_unclassed_anchors_in_main(client: TestClient) -> None:
    css = client.get("/static/app.css").text
    rules = _rules(css)
    base = [d for sel, d in rules if sel.startswith("main a:not([class])") and ":hover" not in sel]
    assert base, "no `main a:not([class])` link rule in app.css"
    assert "color" in base[0], "link rule sets no colour"
    assert base[0].get("text-decoration", "").startswith("underline"), "link rule sets no underline"
    state = [d for sel, d in rules if sel.startswith("main a:not([class])") and ":hover" in sel]
    assert state, "no hover/focus-visible state for the link rule"
    assert "color" in state[0]
    # Rendered markdown (.prose) styles its own anchors; the rule must stay out of it.
    assert ".prose a" in " ".join(sel for sel, _ in rules if sel.startswith("main a:not([class])"))


def test_materials_job_entries_are_unclassed_anchors_in_main(client: TestClient) -> None:
    r = client.get("/materials/")
    assert r.status_code == 200
    job_links = [a for a in _anchors(r.text) if a["attrs"].get("href", "").startswith("/materials/fp-")]
    entries = {a["text"].strip(): a for a in job_links}
    assert set(entries) == {"Acme Widgets — Widget Engineer", "Globex Gadgets — Gadget Analyst"}
    for text, a in entries.items():
        assert a["in_main"], f"{text!r} is outside <main>, the link rule would not reach it"
        assert "class" not in a["attrs"], f"{text!r} carries a class, so `a:not([class])` no longer matches it"


def test_nav_and_classed_links_are_outside_the_rule(client: TestClient) -> None:
    anchors = _anchors(client.get("/materials/").text)
    nav = [a for a in anchors if not a["in_main"]]
    assert nav, "no nav anchors found"
    for a in nav:
        assert not a["in_main"]
        assert a["attrs"].get("class"), f"nav link {a['attrs'].get('href')!r} has no class"


def test_table_header_sort_links_keep_their_plain_look(client: TestClient) -> None:
    # Column-header sort links are inside <main>; they carry a class so the
    # link rule leaves their look unchanged.
    r = client.get("/board/dashboard")
    assert r.status_code == 200
    sort_links = [a for a in _anchors(r.text) if a["in_th"]]
    assert sort_links, "no sortable column header rendered"
    for a in sort_links:
        assert a["attrs"].get("class"), f"sort link {a['attrs'].get('href')!r} has no class"
