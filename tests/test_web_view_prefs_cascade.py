"""Integration tests for #277 — view_prefs cascade through board routes.

Verifies the three-tier cascade: URL ?cols= > persisted view_prefs >
ColumnSpec.default_visible. Auto-save fires on every page + /rows GET
that carries allowlisted filter state; density and other unrelated
params are excluded by construction.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web import view_prefs
from findajob.web.app import create_app
from tests.conftest import init_test_db


@pytest.fixture
def client_and_db(tmp_path: Path) -> tuple[TestClient, Path]:
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    conn = sqlite3.connect(db)
    for i, (fp, title, company) in enumerate(
        [
            ("fp1", "NPI PM", "Meta"),
            ("fp2", "Staff Eng", "Anthropic"),
            ("fp3", "TPM", "Meta"),
        ]
    ):
        conn.execute(
            "INSERT INTO jobs (id, fingerprint, url, title, company, source, stage, relevance_score) "
            "VALUES (?, ?, ?, ?, ?, 'test', 'scored', 8)",
            (f"jid-{i}", fp, f"https://x.test/{fp}", title, company),
        )
    conn.commit()
    conn.close()
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path)), db


def _read_view_prefs(db: Path, tab: str) -> str | None:
    conn = sqlite3.connect(db)
    try:
        return view_prefs.load(conn, tab)
    finally:
        conn.close()


def _write_view_prefs(db: Path, tab: str, qs: str) -> None:
    conn = sqlite3.connect(db)
    try:
        view_prefs.save(conn, tab, qs)
    finally:
        conn.close()


# ── Auto-save on URL settle ─────────────────────────────────────────────


def test_page_get_with_filter_persists_to_view_prefs(client_and_db: tuple[TestClient, Path]) -> None:
    client, db = client_and_db
    r = client.get("/board/dashboard?company=meta")
    assert r.status_code == 200
    assert _read_view_prefs(db, "dashboard") == "company=meta"


def test_rows_get_with_filter_persists_to_view_prefs(client_and_db: tuple[TestClient, Path]) -> None:
    client, db = client_and_db
    r = client.get("/board/dashboard/rows?company=meta")
    assert r.status_code == 200
    assert _read_view_prefs(db, "dashboard") == "company=meta"


def test_density_param_alone_does_not_persist(client_and_db: tuple[TestClient, Path]) -> None:
    """Density is deliberately excluded from #277 — it's URL-param only.
    A page visit carrying only ?density= must not clear existing prefs
    AND must not persist density itself.
    """
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "company=meta")
    r = client.get("/board/dashboard?density=expanded", follow_redirects=False)
    # has_filter_state(parsed) is False -> cold-load redirect fires.
    assert r.status_code == 303
    # The persisted prefs are unchanged — density did not clobber them.
    assert _read_view_prefs(db, "dashboard") == "company=meta"


def test_cols_param_persists_with_url_encoding(client_and_db: tuple[TestClient, Path]) -> None:
    client, db = client_and_db
    r = client.get("/board/dashboard?cols=title,company,relevance_score")
    assert r.status_code == 200
    assert _read_view_prefs(db, "dashboard") == "cols=title%2Ccompany%2Crelevance_score"


def test_each_tab_persists_independently(client_and_db: tuple[TestClient, Path]) -> None:
    """Two tabs persist independently — clearing one leaves the other intact.

    Exercise this at the data layer rather than via two distinct route
    hits to avoid pulling in cost_log / audit_log fixture deps that
    aren't relevant to #277.
    """
    client, db = client_and_db
    client.get("/board/dashboard?company=meta")
    _write_view_prefs(db, "review", "company=anthropic")
    assert _read_view_prefs(db, "dashboard") == "company=meta"
    assert _read_view_prefs(db, "review") == "company=anthropic"
    client.post("/board/dashboard/reset-view", follow_redirects=False)
    assert _read_view_prefs(db, "dashboard") is None
    assert _read_view_prefs(db, "review") == "company=anthropic"


# ── Cold-load redirect ──────────────────────────────────────────────────


def test_cold_load_with_persisted_state_redirects(client_and_db: tuple[TestClient, Path]) -> None:
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "company=meta")
    r = client.get("/board/dashboard", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/board/dashboard?company=meta"


def test_cold_load_with_no_persisted_state_renders_defaults(client_and_db: tuple[TestClient, Path]) -> None:
    client, db = client_and_db
    r = client.get("/board/dashboard")
    assert r.status_code == 200
    # No row written either — nothing to persist.
    assert _read_view_prefs(db, "dashboard") is None


def test_cold_load_redirect_landing_renders_persisted_filters(
    client_and_db: tuple[TestClient, Path],
) -> None:
    """Full round-trip: cold load -> 303 -> follow -> filtered rows."""
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "company=meta")
    r = client.get("/board/dashboard")
    assert r.status_code == 200
    assert "NPI PM" in r.text
    assert "TPM" in r.text
    assert "Staff Eng" not in r.text


# ── URL wins over persisted ─────────────────────────────────────────────


def test_url_param_overrides_persisted_state(client_and_db: tuple[TestClient, Path]) -> None:
    """Bookmark / deep-link must surface its own state, not the persisted one."""
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "company=meta")
    r = client.get("/board/dashboard?company=anthropic", follow_redirects=False)
    # No redirect — the URL has filter state already.
    assert r.status_code == 200
    # Overwrite-on-deep-link semantics: persistence updates to URL.
    assert _read_view_prefs(db, "dashboard") == "company=anthropic"


def test_url_with_only_density_still_triggers_cold_load_redirect(
    client_and_db: tuple[TestClient, Path],
) -> None:
    """Density doesn't count as filter state for the cold-load check."""
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "company=meta")
    r = client.get("/board/dashboard?density=expanded", follow_redirects=False)
    assert r.status_code == 303
    # The redirect target carries persisted state, but DROPS density (out of scope).
    assert r.headers["location"] == "/board/dashboard?company=meta"


# ── Reset endpoint ──────────────────────────────────────────────────────


def test_reset_view_clears_persistence_and_redirects(client_and_db: tuple[TestClient, Path]) -> None:
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "company=meta")
    r = client.post("/board/dashboard/reset-view", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/board/dashboard"
    assert _read_view_prefs(db, "dashboard") is None


def test_reset_view_handles_hyphenated_url_tab(client_and_db: tuple[TestClient, Path]) -> None:
    """/board/not-selected URL must map to view_prefs key 'not_selected'."""
    client, db = client_and_db
    _write_view_prefs(db, "not_selected", "company=meta")
    r = client.post("/board/not-selected/reset-view", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/board/not-selected"
    assert _read_view_prefs(db, "not_selected") is None


def test_reset_view_unknown_tab_returns_404(client_and_db: tuple[TestClient, Path]) -> None:
    client, _ = client_and_db
    r = client.post("/board/bogus/reset-view")
    assert r.status_code == 404


def test_reset_view_idempotent_on_no_existing_row(client_and_db: tuple[TestClient, Path]) -> None:
    client, db = client_and_db
    assert _read_view_prefs(db, "dashboard") is None
    r = client.post("/board/dashboard/reset-view", follow_redirects=False)
    assert r.status_code == 303
    assert _read_view_prefs(db, "dashboard") is None


# ── /reset-filter/{name} — per-chip clears without snap-back (#844) ─────


def test_reset_filter_clears_named_text_filter(
    client_and_db: tuple[TestClient, Path],
) -> None:
    """POST /reset-filter/{name} removes just that key from persistence."""
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "company=meta&relevance_score_min=5")
    r = client.post("/board/dashboard/reset-filter/company", follow_redirects=False)
    assert r.status_code == 303
    # Remaining filters carried in URL so cold-load redirect won't snap back.
    assert r.headers["location"] == "/board/dashboard?relevance_score_min=5"
    assert _read_view_prefs(db, "dashboard") == "relevance_score_min=5"


def test_reset_filter_clears_only_filter_and_resets_persistence(
    client_and_db: tuple[TestClient, Path],
) -> None:
    """When the cleared chip was the only filter, the persisted row
    is deleted entirely so the cold-load redirect renders defaults."""
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "company=meta")
    r = client.post("/board/dashboard/reset-filter/company", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/board/dashboard"
    assert _read_view_prefs(db, "dashboard") is None


def test_reset_filter_then_cold_load_renders_defaults_no_snap_back(
    client_and_db: tuple[TestClient, Path],
) -> None:
    """The regression-loop guard. Pre-fix, GET-anchor ✕ landed in
    _maybe_redirect_to_persisted's cold-load branch and snapped back.
    With POST + reset, the next GET sees no persisted row and renders
    defaults."""
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "company=meta")
    client.post("/board/dashboard/reset-filter/company", follow_redirects=False)
    r = client.get("/board/dashboard", follow_redirects=False)
    # No redirect — the cold-load snap-back path is dead because no row exists.
    assert r.status_code == 200


def test_reset_filter_cols_drops_cols_clause(
    client_and_db: tuple[TestClient, Path],
) -> None:
    """The ✕ on the blue cols pill maps to /reset-filter/cols."""
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "cols=title%2Ccompany%2Cstage")
    r = client.post("/board/dashboard/reset-filter/cols", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/board/dashboard"
    assert _read_view_prefs(db, "dashboard") is None


def test_reset_filter_cols_preserves_other_filters(
    client_and_db: tuple[TestClient, Path],
) -> None:
    """Other filters survive a cols-only reset."""
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "company=meta&cols=title%2Cstage")
    r = client.post("/board/dashboard/reset-filter/cols", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/board/dashboard?company=meta"
    assert _read_view_prefs(db, "dashboard") == "company=meta"


def test_reset_filter_sort_drops_sort_clause(
    client_and_db: tuple[TestClient, Path],
) -> None:
    """Symmetry — sort is removable through the same mechanism even
    though no UI chip exposes it today. Keeps the route's contract
    consistent with the URL framework's vocabulary."""
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "sort=title&desc=0&company=meta")
    r = client.post("/board/dashboard/reset-filter/sort", follow_redirects=False)
    assert r.status_code == 303
    assert _read_view_prefs(db, "dashboard") == "company=meta"


def test_reset_filter_unknown_name_is_noop(
    client_and_db: tuple[TestClient, Path],
) -> None:
    """Stale forms after a rename mustn't 500 — they should redirect
    back unchanged so the operator can re-render and try again."""
    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "company=meta")
    r = client.post("/board/dashboard/reset-filter/bogus_column", follow_redirects=False)
    assert r.status_code == 303
    assert _read_view_prefs(db, "dashboard") == "company=meta"


def test_reset_filter_unknown_tab_returns_404(
    client_and_db: tuple[TestClient, Path],
) -> None:
    client, _ = client_and_db
    r = client.post("/board/bogus/reset-filter/company")
    assert r.status_code == 404


def test_reset_filter_handles_hyphenated_url_tab(
    client_and_db: tuple[TestClient, Path],
) -> None:
    """/board/not-selected URL must map to view_prefs key 'not_selected'."""
    client, db = client_and_db
    _write_view_prefs(db, "not_selected", "company=meta")
    r = client.post("/board/not-selected/reset-filter/company", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/board/not-selected"
    assert _read_view_prefs(db, "not_selected") is None


# ── Default-aware cols persistence (#844) ───────────────────────────────


def test_cols_matching_defaults_not_persisted(
    client_and_db: tuple[TestClient, Path],
) -> None:
    """Toggling the Columns dropdown to exactly the default-visible set
    should not persist a cols clause. Pre-fix, ?cols=<defaults> would
    persist, redirect-snap on every cold load, and render the cols pill
    on a perceived default view (Symptom C in #844)."""
    client, db = client_and_db
    # Dashboard default-visible cols include title + company among others.
    # Build a cols= param matching the spec defaults.
    from findajob.web.filters import registry

    defaults = ",".join(s.name for s in registry.DASHBOARD_COLUMNS if s.default_visible)
    r = client.get(f"/board/dashboard?cols={defaults}")
    assert r.status_code == 200
    assert _read_view_prefs(db, "dashboard") is None


def test_cols_subset_of_defaults_is_persisted(
    client_and_db: tuple[TestClient, Path],
) -> None:
    """A real customization (hiding a default column) still persists."""
    client, db = client_and_db
    r = client.get("/board/dashboard?cols=title,company")
    assert r.status_code == 200
    persisted = _read_view_prefs(db, "dashboard")
    assert persisted is not None
    assert "cols=title%2Ccompany" in persisted


# ── Cols pill rendering (#844) ──────────────────────────────────────────


def test_cols_pill_does_not_render_when_cols_match_defaults(
    client_and_db: tuple[TestClient, Path],
) -> None:
    """Symptom C — the blue 'cols: …' pill listing every default column
    should not appear on a perceived default view."""
    client, db = client_and_db
    from findajob.web.filters import registry

    defaults = ",".join(s.name for s in registry.DASHBOARD_COLUMNS if s.default_visible)
    r = client.get(f"/board/dashboard?cols={defaults}")
    assert r.status_code == 200
    # The blue-bg cols pill marker.
    assert "bg-blue-50" not in r.text or "cols:" not in r.text
    # Also assert it's specifically the cols chip that's absent — check
    # for the literal 'cols:' label which only the cols pill renders.
    chip_marker = '<span class="font-semibold">cols:</span>'
    assert chip_marker not in r.text


def test_cols_pill_renders_when_cols_off_default(
    client_and_db: tuple[TestClient, Path],
) -> None:
    """A real customization (hiding columns) does show the pill so the
    operator has an obvious affordance for getting back to defaults."""
    client, db = client_and_db
    r = client.get("/board/dashboard?cols=title,company")
    assert r.status_code == 200
    chip_marker = '<span class="font-semibold">cols:</span>'
    assert chip_marker in r.text


# ── Chip ✕ buttons are POST forms (#844) ───────────────────────────────


def test_chip_close_buttons_are_post_forms_not_get_anchors(
    client_and_db: tuple[TestClient, Path],
) -> None:
    """Defense against regression to GET anchors. The bug class is
    'GET anchor lands in _maybe_redirect_to_persisted's cold-load
    branch and snaps back'. Asserting POST form keeps the regression
    test self-evident — anyone re-introducing an anchor will see this
    fail."""
    client, _ = client_and_db
    # Prime a state that surfaces a text chip.
    r = client.get("/board/dashboard?company=meta")
    assert r.status_code == 200
    # The chip ✕ is now a form POSTing to /reset-filter/company.
    assert "/board/dashboard/reset-filter/company" in r.text
    # And Clear-all is a form POSTing to /reset-view.
    assert "/board/dashboard/reset-view" in r.text


# ── Update-flash param survives the cold-load redirect (#1017) ──────────


def test_update_flash_renders_and_preserves_persisted_view(
    monkeypatch: pytest.MonkeyPatch, client_and_db: tuple[TestClient, Path]
) -> None:
    """#1017: the POST /update/now result-flash param must survive even when a
    dashboard view is persisted. Without the guard, the view-prefs cold-load
    redirect 303s to the persisted querystring and strips ?update_triggered,
    swallowing the flash. The flash render skips that redirect so the banner +
    flash render directly; the persisted view is asserted intact (it re-applies
    on the next navigation regardless).
    """
    from findajob.web import update_check

    client, db = client_and_db
    _write_view_prefs(db, "dashboard", "company=meta")
    # The flash lives inside the banner card, so the banner must be present:
    # latest must be newer than the running version.
    monkeypatch.setattr(update_check, "findajob_version", lambda: "0.33.0")
    update_check._cache["latest"] = "0.34.0"
    update_check._cache["checked_at"] = update_check._now()

    r = client.get("/board/dashboard?update_triggered=1", follow_redirects=False)
    # No cold-load redirect — the flash renders directly (not a 303 that drops it).
    assert r.status_code == 200
    assert "Update requested" in r.text
    # Saved filters untouched — not clobbered by the bare flash-view.
    assert _read_view_prefs(db, "dashboard") == "company=meta"
