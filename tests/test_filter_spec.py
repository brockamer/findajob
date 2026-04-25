from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from findajob.web.filters.spec import ColumnSpec, Kind, validate_specs


def test_kind_values_are_string_constants() -> None:
    assert Kind.TEXT.value == "text"
    assert Kind.SCORE.value == "score"
    assert Kind.INTEGER.value == "integer"
    assert Kind.ENUM.value == "enum"
    assert Kind.DATE.value == "date"
    assert Kind.COMPUTED.value == "computed"


def test_columnspec_minimum_fields() -> None:
    s = ColumnSpec(name="title", label="Title", kind=Kind.TEXT)
    assert s.sortable is True
    assert s.filterable is True
    assert s.default_visible is True
    assert s.enum_values is None
    assert s.db_expr is None


def test_columnspec_is_immutable() -> None:
    s = ColumnSpec(name="title", label="Title", kind=Kind.TEXT)
    with pytest.raises(FrozenInstanceError):
        s.label = "Other"  # type: ignore[misc]


def test_enum_kind_requires_enum_values() -> None:
    with pytest.raises(ValueError, match="enum_values"):
        ColumnSpec(name="stage", label="Stage", kind=Kind.ENUM, enum_values=None)


def test_enum_values_must_not_contain_commas() -> None:
    with pytest.raises(ValueError, match="comma"):
        ColumnSpec(name="stage", label="Stage", kind=Kind.ENUM, enum_values=("a,b", "c"))


def test_computed_columns_are_not_filterable_or_sortable() -> None:
    s = ColumnSpec(
        name="company_history",
        label="History",
        kind=Kind.COMPUTED,
        sortable=False,
        filterable=False,
    )
    assert s.sortable is False
    assert s.filterable is False


def test_validate_specs_rejects_reserved_suffix_collision() -> None:
    bad = (ColumnSpec(name="score_min", label="Min Score", kind=Kind.INTEGER),)
    with pytest.raises(ValueError, match="reserved suffix"):
        validate_specs(bad)


def test_validate_specs_rejects_duplicate_names() -> None:
    bad = (
        ColumnSpec(name="title", label="Title", kind=Kind.TEXT),
        ColumnSpec(name="title", label="Title2", kind=Kind.TEXT),
    )
    with pytest.raises(ValueError, match="duplicate"):
        validate_specs(bad)


def test_validate_specs_accepts_clean_list() -> None:
    ok = (
        ColumnSpec(name="title", label="Title", kind=Kind.TEXT),
        ColumnSpec(name="relevance_score", label="Rel", kind=Kind.SCORE),
        ColumnSpec(name="stage", label="Stage", kind=Kind.ENUM, enum_values=("a", "b")),
    )
    validate_specs(ok)  # no exception
