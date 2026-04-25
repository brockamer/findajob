"""Parse a request's query-string into a typed ParsedFilters object.

The ParsedFilters object is the framework's canonical request shape — every
downstream consumer (SQL builder, header renderer, chip-strip renderer) reads
from this single struct, so URL parsing is the single source of truth.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from findajob.web.filters.spec import ColumnSpec, Kind


@dataclass(frozen=True)
class ParsedFilters:
    text: dict[str, str] = field(default_factory=dict)
    numeric_range: dict[str, tuple[int | None, int | None]] = field(default_factory=dict)
    enum: dict[str, tuple[str, ...]] = field(default_factory=dict)
    date_range: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    cols: tuple[str, ...] | None = None
    sort: str | None = None
    desc: bool = True

    def is_empty(self) -> bool:
        return not (self.text or self.numeric_range or self.enum or self.date_range)


def _to_int_or_none(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def parse_filter_params(specs: Sequence[ColumnSpec], params: Mapping[str, str]) -> ParsedFilters:
    by_name = {s.name: s for s in specs}
    sortable = {s.name for s in specs if s.sortable}

    text: dict[str, str] = {}
    numeric: dict[str, tuple[int | None, int | None]] = {}
    enum: dict[str, tuple[str, ...]] = {}
    date_range: dict[str, tuple[str | None, str | None]] = {}

    for spec in specs:
        if not spec.filterable:
            continue
        name = spec.name
        if spec.kind is Kind.TEXT:
            v = (params.get(name) or "").strip()
            if v:
                text[name] = v
        elif spec.kind in (Kind.SCORE, Kind.INTEGER):
            lo = _to_int_or_none(params.get(f"{name}_min"))
            hi = _to_int_or_none(params.get(f"{name}_max"))
            if lo is not None or hi is not None:
                numeric[name] = (lo, hi)
        elif spec.kind is Kind.ENUM:
            raw = params.get(name)
            if raw is None or raw == "":
                continue
            allowed = set(spec.enum_values or ())
            picks = tuple(p for p in (s.strip() for s in raw.split(",")) if p and p in allowed)
            if picks:
                enum[name] = picks
        elif spec.kind is Kind.DATE:
            d_from = (params.get(f"{name}_from") or "").strip() or None
            d_to = (params.get(f"{name}_to") or "").strip() or None
            if d_from is not None or d_to is not None:
                date_range[name] = (d_from, d_to)

    cols_raw = params.get("cols")
    cols: tuple[str, ...] | None = None
    if cols_raw:
        wanted = [c.strip() for c in cols_raw.split(",") if c.strip()]
        cols_clean = tuple(c for c in wanted if c in by_name)
        if cols_clean:
            cols = cols_clean

    sort_raw = (params.get("sort") or "").strip()
    sort = sort_raw if sort_raw in sortable else None
    desc_raw = params.get("desc", "1")
    desc = desc_raw != "0"

    return ParsedFilters(
        text=text,
        numeric_range=numeric,
        enum=enum,
        date_range=date_range,
        cols=cols,
        sort=sort,
        desc=desc,
    )
