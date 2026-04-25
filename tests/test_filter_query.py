from __future__ import annotations

from findajob.web.filters.query import build_filter_clauses
from findajob.web.filters.spec import ColumnSpec, Kind
from findajob.web.filters.url import ParsedFilters

SPECS = (
    ColumnSpec(name="title", label="Title", kind=Kind.TEXT),
    ColumnSpec(name="company", label="Company", kind=Kind.TEXT),
    ColumnSpec(name="relevance_score", label="Rel", kind=Kind.SCORE),
    ColumnSpec(
        name="days_since_applied",
        label="Days",
        kind=Kind.INTEGER,
        db_expr="CAST((julianday('now') - julianday(al.applied_date)) AS INTEGER)",
    ),
    ColumnSpec(
        name="stage",
        label="Stage",
        kind=Kind.ENUM,
        enum_values=("scored", "manual_review", "applied"),
    ),
    ColumnSpec(
        name="applied_date",
        label="Applied",
        kind=Kind.DATE,
        db_expr="al.applied_date",
    ),
)


def test_empty_filters_returns_empty_clause() -> None:
    sql, params = build_filter_clauses(SPECS, ParsedFilters())
    assert sql == ""
    assert params == []


def test_text_clause() -> None:
    p = ParsedFilters(text={"title": "director"})
    sql, params = build_filter_clauses(SPECS, p)
    assert sql == " AND LOWER(title) LIKE LOWER(?)"
    assert params == ["%director%"]


def test_score_range_both_bounds() -> None:
    p = ParsedFilters(numeric_range={"relevance_score": (5, 10)})
    sql, params = build_filter_clauses(SPECS, p)
    assert sql == " AND relevance_score >= ? AND relevance_score <= ?"
    assert params == [5, 10]


def test_score_range_only_min() -> None:
    p = ParsedFilters(numeric_range={"relevance_score": (5, None)})
    sql, params = build_filter_clauses(SPECS, p)
    assert sql == " AND relevance_score >= ?"
    assert params == [5]


def test_score_range_only_max() -> None:
    p = ParsedFilters(numeric_range={"relevance_score": (None, 10)})
    sql, params = build_filter_clauses(SPECS, p)
    assert sql == " AND relevance_score <= ?"
    assert params == [10]


def test_integer_uses_db_expr() -> None:
    p = ParsedFilters(numeric_range={"days_since_applied": (14, None)})
    sql, params = build_filter_clauses(SPECS, p)
    assert "CAST((julianday('now') - julianday(al.applied_date)) AS INTEGER) >= ?" in sql
    assert params == [14]


def test_enum_clause() -> None:
    p = ParsedFilters(enum={"stage": ("scored", "manual_review")})
    sql, params = build_filter_clauses(SPECS, p)
    assert sql == " AND stage IN (?, ?)"
    assert params == ["scored", "manual_review"]


def test_enum_single_value() -> None:
    p = ParsedFilters(enum={"stage": ("applied",)})
    sql, params = build_filter_clauses(SPECS, p)
    assert sql == " AND stage IN (?)"
    assert params == ["applied"]


def test_date_range_uses_db_expr() -> None:
    p = ParsedFilters(date_range={"applied_date": ("2026-04-01", "2026-04-25")})
    sql, params = build_filter_clauses(SPECS, p)
    assert sql == " AND al.applied_date >= ? AND al.applied_date <= ?"
    assert params == ["2026-04-01", "2026-04-25"]


def test_combined_filters_compose_in_stable_order() -> None:
    p = ParsedFilters(
        text={"title": "director", "company": "spacex"},
        numeric_range={"relevance_score": (5, 10)},
        enum={"stage": ("scored",)},
    )
    sql, params = build_filter_clauses(SPECS, p)
    # Order follows spec-declaration order so the test is stable.
    assert sql == (
        " AND LOWER(title) LIKE LOWER(?)"
        " AND LOWER(company) LIKE LOWER(?)"
        " AND relevance_score >= ? AND relevance_score <= ?"
        " AND stage IN (?)"
    )
    assert params == ["%director%", "%spacex%", 5, 10, "scored"]
