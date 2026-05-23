"""Unit tests for findajob.web.view_prefs — serialize, load/save/reset, allowlist."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from findajob.db.migrate import apply_pending
from findajob.web import view_prefs
from findajob.web.filters.url import ParsedFilters


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "pipeline.db")
    apply_pending(conn)
    return conn


# ── serialize ───────────────────────────────────────────────────────────


def test_serialize_empty_returns_empty_string() -> None:
    assert view_prefs.serialize(ParsedFilters()) == ""


def test_serialize_sort_desc_only_emits_sort() -> None:
    # desc=True is the default — don't emit it.
    assert view_prefs.serialize(ParsedFilters(sort="relevance_score", desc=True)) == "sort=relevance_score"


def test_serialize_sort_asc_emits_desc_zero() -> None:
    assert view_prefs.serialize(ParsedFilters(sort="title", desc=False)) == "sort=title&desc=0"


def test_serialize_text_filter() -> None:
    parsed = ParsedFilters(text={"company": "Meta"})
    assert view_prefs.serialize(parsed) == "company=Meta"


def test_serialize_numeric_range_both_bounds() -> None:
    parsed = ParsedFilters(numeric_range={"relevance_score": (5, 9)})
    assert view_prefs.serialize(parsed) == "relevance_score_min=5&relevance_score_max=9"


def test_serialize_numeric_range_min_only() -> None:
    parsed = ParsedFilters(numeric_range={"relevance_score": (5, None)})
    assert view_prefs.serialize(parsed) == "relevance_score_min=5"


def test_serialize_enum() -> None:
    parsed = ParsedFilters(enum={"remote_status": ("Remote", "Hybrid")})
    assert view_prefs.serialize(parsed) == "remote_status=Remote%2CHybrid"


def test_serialize_date_range() -> None:
    parsed = ParsedFilters(date_range={"created_at": ("2026-05-01", "2026-05-17")})
    assert view_prefs.serialize(parsed) == "created_at_from=2026-05-01&created_at_to=2026-05-17"


def test_serialize_cols() -> None:
    parsed = ParsedFilters(cols=("title", "company", "relevance_score"))
    assert view_prefs.serialize(parsed) == "cols=title%2Ccompany%2Crelevance_score"


def test_serialize_composite_order_is_deterministic() -> None:
    parsed = ParsedFilters(
        sort="relevance_score",
        desc=True,
        numeric_range={"relevance_score": (5, None)},
        text={"company": "Meta"},
        cols=("title", "company"),
    )
    result = view_prefs.serialize(parsed)
    # Order: sort, then text, then numeric, then enum, then date, then cols.
    assert result == "sort=relevance_score&company=Meta&relevance_score_min=5&cols=title%2Ccompany"


# ── default_cols suppression (#844) ─────────────────────────────────────


def test_serialize_drops_cols_when_matches_default_cols() -> None:
    """cols=<defaults> is a no-op — persisting it brings back the cols
    pill on what the operator sees as a default view (#844)."""
    parsed = ParsedFilters(cols=("title", "company"))
    assert view_prefs.serialize(parsed, default_cols=("title", "company")) == ""


def test_serialize_drops_cols_when_set_equal_regardless_of_order() -> None:
    """Order ignored — operators toggle checkboxes in arbitrary order."""
    parsed = ParsedFilters(cols=("company", "title"))
    assert view_prefs.serialize(parsed, default_cols=("title", "company")) == ""


def test_serialize_keeps_cols_when_subset_of_defaults() -> None:
    """A strict subset is a customization — operator hid columns."""
    parsed = ParsedFilters(cols=("title",))
    assert view_prefs.serialize(parsed, default_cols=("title", "company")) == "cols=title"


def test_serialize_keeps_cols_when_superset_of_defaults() -> None:
    """A strict superset is a customization — operator surfaced a hidden column."""
    parsed = ParsedFilters(cols=("title", "company", "stage"))
    result = view_prefs.serialize(parsed, default_cols=("title", "company"))
    assert result == "cols=title%2Ccompany%2Cstage"


def test_serialize_without_default_cols_kwarg_preserves_legacy_behavior() -> None:
    """Default-cols argument is opt-in; callers that don't pass it keep
    the old emit-cols-when-present behavior. Critical for any caller
    that hasn't been updated (test fixtures, etc)."""
    parsed = ParsedFilters(cols=("title", "company"))
    assert view_prefs.serialize(parsed) == "cols=title%2Ccompany"


def test_serialize_combines_default_cols_drop_with_other_filters() -> None:
    """Other filters survive the cols-default drop."""
    parsed = ParsedFilters(
        text={"company": "Meta"},
        cols=("title", "company"),
    )
    result = view_prefs.serialize(parsed, default_cols=("title", "company"))
    assert result == "company=Meta"


# ── has_filter_state ────────────────────────────────────────────────────


def test_has_filter_state_empty_is_false() -> None:
    assert view_prefs.has_filter_state(ParsedFilters()) is False


def test_has_filter_state_text_is_true() -> None:
    assert view_prefs.has_filter_state(ParsedFilters(text={"company": "Meta"})) is True


def test_has_filter_state_sort_is_true() -> None:
    assert view_prefs.has_filter_state(ParsedFilters(sort="title")) is True


def test_has_filter_state_cols_is_true() -> None:
    assert view_prefs.has_filter_state(ParsedFilters(cols=("title",))) is True


# ── load / save / reset round-trip ──────────────────────────────────────


def test_load_returns_none_when_no_row(db: sqlite3.Connection) -> None:
    assert view_prefs.load(db, "dashboard") is None


def test_save_then_load_roundtrip(db: sqlite3.Connection) -> None:
    view_prefs.save(db, "dashboard", "cols=title%2Ccompany")
    assert view_prefs.load(db, "dashboard") == "cols=title%2Ccompany"


def test_save_upserts_on_repeat(db: sqlite3.Connection) -> None:
    view_prefs.save(db, "dashboard", "cols=title")
    view_prefs.save(db, "dashboard", "cols=company")
    assert view_prefs.load(db, "dashboard") == "cols=company"
    # Single row, not two.
    count = db.execute("SELECT COUNT(*) FROM view_prefs WHERE tab='dashboard'").fetchone()[0]
    assert count == 1


def test_save_empty_string_is_noop(db: sqlite3.Connection) -> None:
    view_prefs.save(db, "dashboard", "cols=title")
    view_prefs.save(db, "dashboard", "")
    # Prior persistence preserved.
    assert view_prefs.load(db, "dashboard") == "cols=title"


def test_reset_clears_row(db: sqlite3.Connection) -> None:
    view_prefs.save(db, "dashboard", "cols=title")
    view_prefs.reset(db, "dashboard")
    assert view_prefs.load(db, "dashboard") is None


def test_reset_when_no_row_is_noop(db: sqlite3.Connection) -> None:
    view_prefs.reset(db, "dashboard")
    assert view_prefs.load(db, "dashboard") is None


def test_each_tab_independent(db: sqlite3.Connection) -> None:
    view_prefs.save(db, "dashboard", "cols=title")
    view_prefs.save(db, "applied", "cols=company")
    assert view_prefs.load(db, "dashboard") == "cols=title"
    assert view_prefs.load(db, "applied") == "cols=company"
    view_prefs.reset(db, "dashboard")
    assert view_prefs.load(db, "dashboard") is None
    assert view_prefs.load(db, "applied") == "cols=company"


# ── allowlist enforcement ───────────────────────────────────────────────


def test_load_rejects_unknown_tab(db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="unknown tab"):
        view_prefs.load(db, "bogus")


def test_save_rejects_unknown_tab(db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="unknown tab"):
        view_prefs.save(db, "bogus", "cols=title")


def test_reset_rejects_unknown_tab(db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="unknown tab"):
        view_prefs.reset(db, "bogus")


def test_allowed_tabs_match_route_tab_values() -> None:
    # Mirror of the seven "tab" context values in src/findajob/web/routes/board.py.
    # If this list changes, update both the module and the 0005 migration's
    # CHECK constraint.
    assert view_prefs.ALLOWED_TABS == frozenset(
        {"dashboard", "applied", "review", "waitlist", "rejected", "not_selected", "archive"}
    )
