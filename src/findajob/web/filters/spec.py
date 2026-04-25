"""Column-spec data model: ColumnSpec dataclass + Kind enum + validator."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

# Reserved URL-param suffixes the framework consumes for SCORE / INTEGER / DATE
# range filters. A column whose name ends in one of these would collide with the
# auto-suffix scheme — validate_specs() rejects such registries at import time.
_RESERVED_SUFFIXES = ("_min", "_max", "_from", "_to")


class Kind(StrEnum):
    TEXT = "text"
    SCORE = "score"
    INTEGER = "integer"
    ENUM = "enum"
    DATE = "date"
    COMPUTED = "computed"


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    label: str
    kind: Kind
    sortable: bool = True
    filterable: bool = True
    default_visible: bool = True
    enum_values: tuple[str, ...] | None = None
    db_expr: str | None = None

    def __post_init__(self) -> None:
        if self.kind is Kind.ENUM:
            if not self.enum_values:
                raise ValueError(f"ColumnSpec {self.name!r}: kind=ENUM requires enum_values")
            for v in self.enum_values:
                if "," in v:
                    raise ValueError(
                        f"ColumnSpec {self.name!r}: enum_values must not contain comma "
                        f"(got {v!r}); the URL contract uses comma as the separator."
                    )

    @property
    def sql_ref(self) -> str:
        """SQL expression used in WHERE / ORDER BY clauses for this column."""
        return self.db_expr if self.db_expr is not None else self.name


def validate_specs(specs: Iterable[ColumnSpec]) -> None:
    """Assert that a tab's spec list is well-formed.

    Raises ValueError on:
      - duplicate names
      - any name ending in a reserved URL-param suffix (_min, _max, _from, _to)
    """
    seen: set[str] = set()
    for s in specs:
        if s.name in seen:
            raise ValueError(f"duplicate column name {s.name!r} in spec list")
        seen.add(s.name)
        for suf in _RESERVED_SUFFIXES:
            if s.name.endswith(suf):
                raise ValueError(
                    f"ColumnSpec {s.name!r} uses reserved suffix {suf!r}; "
                    f"would collide with the framework's range-filter URL params."
                )
