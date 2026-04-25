from __future__ import annotations

from findajob.web.filters.spec import ColumnSpec, Kind
from findajob.web.filters.url import ParsedFilters, parse_filter_params

SPECS = (
    ColumnSpec(name="title", label="Title", kind=Kind.TEXT),
    ColumnSpec(name="company", label="Company", kind=Kind.TEXT),
    ColumnSpec(name="relevance_score", label="Rel", kind=Kind.SCORE),
    ColumnSpec(name="days_since_applied", label="Days", kind=Kind.INTEGER),
    ColumnSpec(
        name="stage",
        label="Stage",
        kind=Kind.ENUM,
        enum_values=("scored", "manual_review", "applied"),
    ),
    ColumnSpec(name="created_at", label="Date", kind=Kind.DATE),
    ColumnSpec(
        name="company_history",
        label="History",
        kind=Kind.COMPUTED,
        sortable=False,
        filterable=False,
    ),
)


def _parse(qs: dict[str, str]) -> ParsedFilters:
    return parse_filter_params(SPECS, qs)


def test_empty_querystring_yields_empty_filters() -> None:
    p = _parse({})
    assert p.text == {}
    assert p.numeric_range == {}
    assert p.enum == {}
    assert p.date_range == {}
    assert p.cols is None
    assert p.sort is None
    assert p.desc is True


def test_text_filter_round_trips() -> None:
    p = _parse({"title": "director"})
    assert p.text == {"title": "director"}


def test_text_filter_strips_whitespace_and_drops_empty() -> None:
    p = _parse({"title": "   ", "company": "  spacex  "})
    assert "title" not in p.text
    assert p.text == {"company": "spacex"}


def test_score_range_both_bounds() -> None:
    p = _parse({"relevance_score_min": "5", "relevance_score_max": "10"})
    assert p.numeric_range == {"relevance_score": (5, 10)}


def test_score_range_only_min() -> None:
    p = _parse({"relevance_score_min": "5"})
    assert p.numeric_range == {"relevance_score": (5, None)}


def test_score_range_only_max() -> None:
    p = _parse({"relevance_score_max": "8"})
    assert p.numeric_range == {"relevance_score": (None, 8)}


def test_score_range_invalid_int_dropped() -> None:
    p = _parse({"relevance_score_min": "abc"})
    assert p.numeric_range == {}


def test_integer_range_works_same_as_score() -> None:
    p = _parse({"days_since_applied_min": "14"})
    assert p.numeric_range == {"days_since_applied": (14, None)}


def test_enum_multi_select() -> None:
    p = _parse({"stage": "scored,manual_review"})
    assert p.enum == {"stage": ("scored", "manual_review")}


def test_enum_drops_unknown_values() -> None:
    p = _parse({"stage": "scored,bogus,applied"})
    assert p.enum == {"stage": ("scored", "applied")}


def test_enum_drops_empty_segments_and_strips_whitespace() -> None:
    p = _parse({"stage": "scored,, applied ,"})
    assert p.enum == {"stage": ("scored", "applied")}


def test_enum_empty_string_yields_no_filter() -> None:
    p = _parse({"stage": ""})
    assert p.enum == {}


def test_date_range_both_bounds() -> None:
    p = _parse({"created_at_from": "2026-04-01", "created_at_to": "2026-04-25"})
    assert p.date_range == {"created_at": ("2026-04-01", "2026-04-25")}


def test_invalid_param_silently_dropped() -> None:
    p = _parse({"bogus_column": "x", "title__bad": "y"})
    assert p.text == {}
    assert p.numeric_range == {}


def test_cols_explicit_set() -> None:
    p = _parse({"cols": "title,company,relevance_score"})
    assert p.cols == ("title", "company", "relevance_score")


def test_cols_empty_treated_as_missing() -> None:
    p = _parse({"cols": ""})
    assert p.cols is None


def test_cols_drops_unknown_names() -> None:
    p = _parse({"cols": "title,bogus,relevance_score"})
    assert p.cols == ("title", "relevance_score")


def test_sort_and_desc_parsed() -> None:
    p = _parse({"sort": "relevance_score", "desc": "0"})
    assert p.sort == "relevance_score"
    assert p.desc is False


def test_sort_unknown_column_silently_dropped() -> None:
    p = _parse({"sort": "bogus"})
    assert p.sort is None


def test_sort_unsortable_column_silently_dropped() -> None:
    p = _parse({"sort": "company_history"})
    assert p.sort is None


def test_filterable_false_columns_ignored() -> None:
    # company_history is filterable=False — even if someone passed a value,
    # it must not produce a filter.
    p = _parse({"company_history": "anything"})
    assert p.text == {}
