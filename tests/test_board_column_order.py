"""#1220 — board column order: status, title, company, location, scores, rest.

Covers the default ColumnSpec order on every tab, the user's own order from
the Columns dropdown (``?cols=`` list order) in the header, the rows and the
dropdown itself, order-aware persistence, and the single-row re-render after
a board action.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web import view_prefs
from findajob.web.app import create_app
from findajob.web.filters import registry
from findajob.web.filters.spec import Kind
from findajob.web.filters.url import ParsedFilters, ordered_specs
from tests.conftest import init_test_db

_ALL_TABS = {
    "dashboard": registry.DASHBOARD_COLUMNS,
    "applied": registry.APPLIED_COLUMNS,
    "review": registry.REVIEW_COLUMNS,
    "waitlist": registry.WAITLIST_COLUMNS,
    "rejected": registry.REJECTED_COLUMNS,
    "not_selected": registry.NOT_SELECTED_COLUMNS,
    "fallback": registry.FALLBACK_COLUMNS,
    "archive": registry.ARCHIVE_COLUMNS,
}


def _rank(name: str, kind: Kind) -> int:
    fixed = {"title": 0, "company": 1, "location": 2}
    if name in fixed:
        return fixed[name]
    return 3 if kind is Kind.SCORE else 4


# ── Default order (registry) ────────────────────────────────────────────


def test_dashboard_default_order_is_title_company_location_then_scores() -> None:
    names = [s.name for s in registry.DASHBOARD_COLUMNS if s.default_visible]
    assert names[:7] == [
        "title",
        "company",
        "location",
        "relevance_score",
        "interview_likelihood",
        "fit_score",
        "probability_score",
    ]


@pytest.mark.parametrize("tab", sorted(_ALL_TABS))
def test_every_tab_follows_the_order_rule(tab: str) -> None:
    ranks = [_rank(s.name, s.kind) for s in _ALL_TABS[tab]]
    assert ranks == sorted(ranks), [s.name for s in _ALL_TABS[tab]]


# ── ordered_specs ───────────────────────────────────────────────────────


def test_ordered_specs_without_cols_keeps_spec_order() -> None:
    specs = registry.DASHBOARD_COLUMNS
    assert ordered_specs(specs, None) == specs


def test_ordered_specs_puts_cols_first_in_cols_order_then_the_rest() -> None:
    specs = registry.DASHBOARD_COLUMNS
    out = ordered_specs(specs, ("company", "title"))
    names = [s.name for s in out]
    assert names[:2] == ["company", "title"]
    assert sorted(names) == sorted(s.name for s in specs)
    rest = [n for n in names[2:]]
    assert rest == [s.name for s in specs if s.name not in ("company", "title")]


# ── Persistence ─────────────────────────────────────────────────────────


def test_serialize_keeps_a_reordered_default_set() -> None:
    parsed = ParsedFilters(cols=("company", "title"))
    assert view_prefs.serialize(parsed, default_cols=("title", "company")) == "cols=company%2Ctitle"


def test_serialize_still_drops_cols_equal_to_defaults_in_default_order() -> None:
    parsed = ParsedFilters(cols=("title", "company"))
    assert view_prefs.serialize(parsed, default_cols=("title", "company")) == ""


# ── Rendering ───────────────────────────────────────────────────────────


@pytest.fixture
def client_and_db(tmp_path: Path) -> tuple[TestClient, Path]:
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, location, source, stage, relevance_score) "
        "VALUES ('jid-1', 'fp1', 'https://x.test/1', 'TitleAlpha', 'CompanyBeta', 'CityGamma', "
        "'test', 'scored', 8)"
    )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path)), db


def _header_labels(html: str) -> list[str]:
    thead = html.split("<thead", 1)[1].split("</tr>", 1)[0]
    cells = re.findall(r"<th[^>]*>(.*?)</th>", thead, flags=re.S)
    return [re.sub(r"<[^>]+>|[▲▼●]", "", c).strip() for c in cells]


def test_dashboard_header_starts_with_status_then_title_company_location(
    client_and_db: tuple[TestClient, Path],
) -> None:
    client, _ = client_and_db
    labels = _header_labels(client.get("/board/dashboard").text)
    assert labels[:6] == ["Status", "Reject", "Title", "Company", "Location", "Rel"]
    # The exclusion-rule cell moved to the end of the row (#1220).
    assert labels[-1] == "Rule"


def test_dashboard_row_cells_follow_the_header(client_and_db: tuple[TestClient, Path]) -> None:
    client, _ = client_and_db
    html = client.get("/board/dashboard").text
    row = html.split('data-fingerprint="fp1"', 1)[1].split("</tr>", 1)[0]
    assert row.index("TitleAlpha") < row.index("CompanyBeta") < row.index("CityGamma")
    assert row.index("CityGamma") < row.rindex("/exclude/modal")


def test_cols_order_drives_header_and_rows(client_and_db: tuple[TestClient, Path]) -> None:
    client, _ = client_and_db
    html = client.get("/board/dashboard?cols=location,company,title").text
    assert _header_labels(html)[2:5] == ["Location", "Company", "Title"]
    row = html.split('data-fingerprint="fp1"', 1)[1].split("</tr>", 1)[0]
    assert row.index("CityGamma") < row.index("CompanyBeta") < row.index("TitleAlpha")


def test_columns_dropdown_lists_columns_in_current_order_with_move_buttons(
    client_and_db: tuple[TestClient, Path],
) -> None:
    client, _ = client_and_db
    html = client.get("/board/dashboard?cols=location,company,title").text
    boxes = re.findall(r'value="([a-z_]+)"\s+data-popover-checkbox="cols"', html)
    assert boxes[:3] == ["location", "company", "title"]
    assert 'data-col-move="up"' in html
    assert 'data-col-move="down"' in html


def test_reordered_cols_persist_and_reload(client_and_db: tuple[TestClient, Path]) -> None:
    client, db = client_and_db
    defaults = [s.name for s in registry.DASHBOARD_COLUMNS if s.default_visible]
    reordered = [defaults[1], defaults[0], *defaults[2:]]
    client.get("/board/dashboard?cols=" + ",".join(reordered))
    conn = sqlite3.connect(db)
    try:
        persisted = view_prefs.load(conn, "dashboard")
    finally:
        conn.close()
    assert persisted is not None and persisted.startswith("cols=company%2Ctitle")
    r = client.get("/board/dashboard")  # cold load → 303 to persisted → 200
    assert _header_labels(r.text)[2:4] == ["Company", "Title"]


def test_action_row_rerender_uses_the_persisted_column_order(
    client_and_db: tuple[TestClient, Path],
) -> None:
    client, db = client_and_db
    client.get("/board/dashboard?cols=location,company,title")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE jobs SET stage='prep_in_progress' WHERE fingerprint='fp1'")
    conn.commit()
    conn.close()
    # Idempotent branch: an in-flight row comes back re-rendered, not removed.
    r = client.post("/board/jobs/fp1/prep", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert r.text.index("CityGamma") < r.text.index("CompanyBeta") < r.text.index("TitleAlpha")


# ── Views saved before #1220 (migration 0013) ───────────────────────────
# The old Columns dropdown listed checkboxes in registry order, so every
# saved ``cols`` holds the old order (scores first). Such rows carry
# ``cols_ordered=0`` and are put in the new registry order once, on load.


def _legacy_row(db: Path, tab: str, qs: str) -> None:
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO view_prefs(tab, query_string, cols_ordered) VALUES (?, ?, 0)", (tab, qs))
    conn.commit()
    conn.close()


def _stored(db: Path, tab: str) -> tuple[str, int]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT query_string, cols_ordered FROM view_prefs WHERE tab=?", (tab,)).fetchone()
    finally:
        conn.close()


def test_migration_marks_existing_rows_as_legacy(tmp_path: Path) -> None:
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO view_prefs(tab, query_string) VALUES ('dashboard', 'cols=title')")
    assert conn.execute("SELECT cols_ordered FROM view_prefs").fetchone() == (0,)
    conn.close()


def test_legacy_cols_are_put_in_registry_order_once(client_and_db: tuple[TestClient, Path]) -> None:
    _, db = client_and_db
    _legacy_row(db, "dashboard", "company=x&cols=relevance_score%2Ctitle%2Ccompany")
    conn = sqlite3.connect(db)
    try:
        assert view_prefs.load(conn, "dashboard") == "company=x&cols=title%2Ccompany%2Crelevance_score"
    finally:
        conn.close()
    assert _stored(db, "dashboard") == ("company=x&cols=title%2Ccompany%2Crelevance_score", 1)


def test_legacy_cols_equal_to_defaults_are_dropped(client_and_db: tuple[TestClient, Path]) -> None:
    _, db = client_and_db
    defaults = [s.name for s in registry.DASHBOARD_COLUMNS if s.default_visible]
    old_order = sorted(defaults, key=lambda n: n not in {"relevance_score", "fit_score"})
    _legacy_row(db, "dashboard", "sort=title&cols=" + "%2C".join(old_order))
    conn = sqlite3.connect(db)
    try:
        assert view_prefs.load(conn, "dashboard") == "sort=title"
    finally:
        conn.close()


def test_marked_rows_keep_the_user_order(client_and_db: tuple[TestClient, Path]) -> None:
    _, db = client_and_db
    conn = sqlite3.connect(db)
    try:
        view_prefs.save(conn, "dashboard", "cols=company%2Ctitle")
        assert view_prefs.load(conn, "dashboard") == "cols=company%2Ctitle"
    finally:
        conn.close()
    assert _stored(db, "dashboard") == ("cols=company%2Ctitle", 1)


def test_save_marks_a_legacy_row(client_and_db: tuple[TestClient, Path]) -> None:
    _, db = client_and_db
    _legacy_row(db, "dashboard", "cols=title")
    conn = sqlite3.connect(db)
    try:
        view_prefs.save(conn, "dashboard", "cols=company%2Ctitle")
    finally:
        conn.close()
    assert _stored(db, "dashboard") == ("cols=company%2Ctitle", 1)


def test_cold_load_of_a_legacy_view_shows_the_new_order(client_and_db: tuple[TestClient, Path]) -> None:
    client, db = client_and_db
    _legacy_row(db, "dashboard", "cols=relevance_score%2Ctitle%2Ccompany%2Clocation")
    labels = _header_labels(client.get("/board/dashboard").text)
    assert labels[2:6] == ["Title", "Company", "Location", "Rel"]
