# Board Filter+Sort Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a generic per-column filter+sort framework on `findajob.web.filters` that powers all 6 board tabs (dashboard, applied, review, waitlist, rejected, archive) — replacing today's hand-coded one-off WHERE clauses and single `?q=` text input with type-aware filters (TEXT / SCORE / INTEGER / ENUM / DATE), sort that's independent of filter, URL-querystring state with `hx-push-url`, and a per-tab Copy-link button.

**Architecture:** Declarative `ColumnSpec` registry per tab; type-suffixed flat URL params; HTMX-driven `/rows` fragment refresh; Layout B (inline inputs for text/numeric, popover for enums/dates); Alpine.js for ephemeral popover state only. Schema support for column-visibility (`?cols=`) ships in v1; UI + persistence land in #277.

**Tech Stack:** FastAPI + Jinja2 + SQLite + HTMX + Tailwind CDN + Alpine.js + vanilla JS (clipboard API). Tests: pytest. Lint: ruff. Types: mypy.

**Branch:** `feat/273-dashboard-filter-sort` (already off `origin/main`).

---

## Goal + scope

**Building:** A typed, declarative filter framework that every board tab uses identically. Each tab declares its `ColumnSpec` list once; the framework handles URL parsing, SQL clause construction, header rendering, popover UI, chip-strip rendering, and Copy-link, with no per-tab special-casing beyond a `base_where` SQL string and (for Applied/Rejected) a small `tab_source()` JOIN-builder.

**Not in scope (deferred):**
- **#277:** Columns ▾ dropdown UI for show/hide and per-tab pref persistence (the cascade hook is in v1; only the UI + storage layers are deferred).
- Saved filter presets / "named views".
- Drag-to-reorder columns.
- Performance: query-result memoization. Add only if a tab exceeds 50ms render.
- Scorer-side noise reduction at score 5/6 (#276 covers IC-vs-manager mis-scoring; this plan surfaces score 5/6 to the operator regardless).

---

## File structure

**New files:**
- `src/findajob/web/filters/__init__.py` — re-exports `ColumnSpec`, `Kind`, `parse_filter_params`, `build_filter_query`.
- `src/findajob/web/filters/spec.py` — `ColumnSpec` dataclass + `Kind` enum + suffix-collision validator.
- `src/findajob/web/filters/url.py` — `ParsedFilters` + `parse_filter_params(specs, multi_dict) -> ParsedFilters`.
- `src/findajob/web/filters/query.py` — `build_filter_clauses(specs, parsed) -> (sql_fragment, params)` — composes WHERE-clause-AND-fragment + bind params from `ParsedFilters`. Route handlers compose this with their own SELECT/FROM/base_where/sort.
- `src/findajob/web/filters/registry.py` — `DASHBOARD_COLUMNS`, `APPLIED_COLUMNS`, `REVIEW_COLUMNS`, `WAITLIST_COLUMNS`, `REJECTED_COLUMNS`, `ARCHIVE_COLUMNS` — each a tuple of `ColumnSpec`. Module import asserts no suffix collisions.
- `src/findajob/web/templates/_active_filters.html` — chip strip + Clear-all link + 🔗 Copy link button.
- `src/findajob/web/templates/_table_header.html` — renders `<thead>` (column titles, sort links, inline filter inputs, popover triggers).
- `src/findajob/web/static/filters.js` — popover Apply/Cancel/Clear, clipboard write for Copy-link, dot-indicator toggle.
- `tests/test_filter_spec.py` — `ColumnSpec` and `Kind` invariants.
- `tests/test_filter_url.py` — `parse_filter_params` round-trips for every Kind.
- `tests/test_filter_query.py` — `build_filter_clauses` parameterized SQL.
- `tests/test_web_board_filters.py` — integration: per-tab `/rows` fragment behavior under filter+sort+cols combinations.

**Modified files:**
- `src/findajob/web/routes/board.py` — every route handler refactored to call the framework; remove `_filter_clause`, `_archive_score_where`, `_archive_select_sql`. Add tiny per-tab `_<tab>_source()` helpers that return the FROM/JOIN string.
- `src/findajob/web/templates/_filters.html` — repurposed: top filter bar (density toggle + active-filters chip strip include). Old `?q=` text input is removed; per-column inputs now render via `_table_header.html` inside each tab's `<table>`.
- `src/findajob/web/templates/board/dashboard.html`, `applied.html`, `review.html`, `waitlist.html`, `rejected.html`, `archive.html` — switch to use `_table_header.html` for `<thead>` and `_filters.html` for the filter bar; pass `specs` and `parsed` from the route.
- `tests/test_web_board_*.py` — update assertions/fixtures for the new URL params (drop `?q=foo` references, swap to per-column param names).
- `tests/test_web_board_sort.py` — same.
- `CLAUDE.md` — Web Frontend Architecture section gets a paragraph on the filter framework + URL contract.
- `CHANGELOG.md` — `[Unreleased]` entry under Added.
- `docs/usage.md` — board tab sections updated to describe filter UI + Copy-link.

---

## Tasks

### Task 1: ColumnSpec dataclass + Kind enum + suffix-collision validator

**Files:**
- Create: `src/findajob/web/filters/__init__.py`
- Create: `src/findajob/web/filters/spec.py`
- Create: `tests/test_filter_spec.py`

- [ ] **Step 1: Write the failing test for `Kind` and `ColumnSpec`.**

```python
# tests/test_filter_spec.py
from __future__ import annotations

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
    with pytest.raises(Exception):
        s.label = "Other"  # type: ignore[misc]


def test_enum_kind_requires_enum_values() -> None:
    with pytest.raises(ValueError, match="enum_values"):
        ColumnSpec(name="stage", label="Stage", kind=Kind.ENUM, enum_values=None)


def test_enum_values_must_not_contain_commas() -> None:
    with pytest.raises(ValueError, match="comma"):
        ColumnSpec(name="stage", label="Stage", kind=Kind.ENUM, enum_values=("a,b", "c"))


def test_computed_columns_are_not_filterable_or_sortable() -> None:
    s = ColumnSpec(
        name="company_history", label="History", kind=Kind.COMPUTED,
        sortable=False, filterable=False,
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
```

- [ ] **Step 2: Run the test to verify it fails.**

Run: `uv run pytest tests/test_filter_spec.py -v`
Expected: ImportError or ModuleNotFoundError on `findajob.web.filters.spec`.

- [ ] **Step 3: Implement `Kind` and `ColumnSpec`.**

```python
# src/findajob/web/filters/__init__.py
"""Per-column filter framework for the board tabs.

See docs/superpowers/specs/2026-04-25-board-filter-framework-design.md.
"""
from findajob.web.filters.spec import ColumnSpec, Kind, validate_specs

__all__ = ["ColumnSpec", "Kind", "validate_specs"]
```

```python
# src/findajob/web/filters/spec.py
"""Column-spec data model: ColumnSpec dataclass + Kind enum + validator."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

# Reserved URL-param suffixes the framework consumes for SCORE / INTEGER / DATE
# range filters. A column whose name ends in one of these would collide with the
# auto-suffix scheme — validate_specs() rejects such registries at import time.
_RESERVED_SUFFIXES = ("_min", "_max", "_from", "_to")


class Kind(str, Enum):
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
                raise ValueError(
                    f"ColumnSpec {self.name!r}: kind=ENUM requires enum_values"
                )
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
```

- [ ] **Step 4: Run tests to verify they pass.**

Run: `uv run pytest tests/test_filter_spec.py -v`
Expected: 9 tests pass.

- [ ] **Step 5: Commit.**

```bash
git add src/findajob/web/filters/__init__.py src/findajob/web/filters/spec.py tests/test_filter_spec.py
git commit -m "$(cat <<'EOF'
feat(filters): ColumnSpec dataclass + Kind enum + suffix-collision validator (#273)

First slice of the board filter framework. ColumnSpec is a frozen
dataclass with name/label/kind/sortable/filterable/default_visible/
enum_values/db_expr. Kind covers TEXT/SCORE/INTEGER/ENUM/DATE/COMPUTED.
validate_specs() rejects duplicate names and reserved-suffix collisions
(_min/_max/_from/_to) at registry-load time.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: URL parsing — parse_filter_params + ParsedFilters

**Files:**
- Create: `src/findajob/web/filters/url.py`
- Create: `tests/test_filter_url.py`

- [ ] **Step 1: Write the failing test.**

```python
# tests/test_filter_url.py
from __future__ import annotations

from findajob.web.filters.spec import ColumnSpec, Kind
from findajob.web.filters.url import ParsedFilters, parse_filter_params


SPECS = (
    ColumnSpec(name="title", label="Title", kind=Kind.TEXT),
    ColumnSpec(name="company", label="Company", kind=Kind.TEXT),
    ColumnSpec(name="relevance_score", label="Rel", kind=Kind.SCORE),
    ColumnSpec(name="days_since_applied", label="Days", kind=Kind.INTEGER),
    ColumnSpec(
        name="stage", label="Stage", kind=Kind.ENUM,
        enum_values=("scored", "manual_review", "applied"),
    ),
    ColumnSpec(name="created_at", label="Date", kind=Kind.DATE),
    ColumnSpec(
        name="company_history", label="History", kind=Kind.COMPUTED,
        sortable=False, filterable=False,
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
```

- [ ] **Step 2: Run the test to verify it fails.**

Run: `uv run pytest tests/test_filter_url.py -v`
Expected: ModuleNotFoundError on `findajob.web.filters.url`.

- [ ] **Step 3: Implement `ParsedFilters` and `parse_filter_params`.**

```python
# src/findajob/web/filters/url.py
"""Parse a request's query-string into a typed ParsedFilters object.

The ParsedFilters object is the framework's canonical request shape — every
downstream consumer (SQL builder, header renderer, chip-strip renderer) reads
from this single struct, so URL parsing is the single source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

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


def parse_filter_params(
    specs: Sequence[ColumnSpec], params: Mapping[str, str]
) -> ParsedFilters:
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
            picks = tuple(
                p for p in (s.strip() for s in raw.split(",")) if p and p in allowed
            )
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
        text=text, numeric_range=numeric, enum=enum, date_range=date_range,
        cols=cols, sort=sort, desc=desc,
    )
```

- [ ] **Step 4: Run tests to verify they pass.**

Run: `uv run pytest tests/test_filter_url.py -v`
Expected: 21 tests pass.

- [ ] **Step 5: Commit.**

```bash
git add src/findajob/web/filters/url.py tests/test_filter_url.py
git commit -m "$(cat <<'EOF'
feat(filters): URL parser — parse_filter_params + ParsedFilters (#273)

ParsedFilters is the framework's canonical request shape: text /
numeric_range / enum / date_range / cols / sort / desc. parse_filter_params
walks the spec list and only honors filterable columns; invalid params
silently drop. Type-suffixed param convention: _min/_max for
SCORE/INTEGER, _from/_to for DATE; ENUM is comma-list; cols= is an
explicit replacement set.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: SQL clause builder — build_filter_clauses

**Files:**
- Create: `src/findajob/web/filters/query.py`
- Create: `tests/test_filter_query.py`

- [ ] **Step 1: Write the failing test.**

```python
# tests/test_filter_query.py
from __future__ import annotations

from findajob.web.filters.query import build_filter_clauses
from findajob.web.filters.spec import ColumnSpec, Kind
from findajob.web.filters.url import ParsedFilters

SPECS = (
    ColumnSpec(name="title", label="Title", kind=Kind.TEXT),
    ColumnSpec(name="company", label="Company", kind=Kind.TEXT),
    ColumnSpec(name="relevance_score", label="Rel", kind=Kind.SCORE),
    ColumnSpec(
        name="days_since_applied", label="Days", kind=Kind.INTEGER,
        db_expr="CAST((julianday('now') - julianday(al.applied_date)) AS INTEGER)",
    ),
    ColumnSpec(
        name="stage", label="Stage", kind=Kind.ENUM,
        enum_values=("scored", "manual_review", "applied"),
    ),
    ColumnSpec(
        name="applied_date", label="Applied", kind=Kind.DATE,
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
```

- [ ] **Step 2: Run the test to verify it fails.**

Run: `uv run pytest tests/test_filter_query.py -v`
Expected: ModuleNotFoundError on `findajob.web.filters.query`.

- [ ] **Step 3: Implement `build_filter_clauses`.**

```python
# src/findajob/web/filters/query.py
"""Compose a parameterized WHERE-clause fragment from ParsedFilters.

Returns the fragment as `" AND <c1> AND <c2> ..."` (leading space + AND so
callers can drop it after their existing base_where without conditional logic)
and a list of bind params in the same order as the placeholders.

This module deliberately knows nothing about FROM / JOIN / SELECT / ORDER BY —
each route handler composes those itself, so the framework doesn't have to
model JOIN structure for tabs like Applied/Rejected that LEFT JOIN audit_log.
"""
from __future__ import annotations

from typing import Sequence

from findajob.web.filters.spec import ColumnSpec, Kind
from findajob.web.filters.url import ParsedFilters


def build_filter_clauses(
    specs: Sequence[ColumnSpec], parsed: ParsedFilters
) -> tuple[str, list[object]]:
    fragments: list[str] = []
    params: list[object] = []

    # Walk specs in declaration order so output is deterministic.
    for spec in specs:
        ref = spec.sql_ref

        if spec.kind is Kind.TEXT and spec.name in parsed.text:
            fragments.append(f"LOWER({ref}) LIKE LOWER(?)")
            params.append(f"%{parsed.text[spec.name]}%")

        elif (
            spec.kind in (Kind.SCORE, Kind.INTEGER)
            and spec.name in parsed.numeric_range
        ):
            lo, hi = parsed.numeric_range[spec.name]
            if lo is not None:
                fragments.append(f"{ref} >= ?")
                params.append(lo)
            if hi is not None:
                fragments.append(f"{ref} <= ?")
                params.append(hi)

        elif spec.kind is Kind.ENUM and spec.name in parsed.enum:
            picks = parsed.enum[spec.name]
            placeholders = ", ".join("?" * len(picks))
            fragments.append(f"{ref} IN ({placeholders})")
            params.extend(picks)

        elif spec.kind is Kind.DATE and spec.name in parsed.date_range:
            d_from, d_to = parsed.date_range[spec.name]
            if d_from is not None:
                fragments.append(f"{ref} >= ?")
                params.append(d_from)
            if d_to is not None:
                fragments.append(f"{ref} <= ?")
                params.append(d_to)

    if not fragments:
        return "", []
    return " AND " + " AND ".join(fragments), params
```

- [ ] **Step 4: Run tests to verify they pass.**

Run: `uv run pytest tests/test_filter_query.py -v`
Expected: 10 tests pass.

- [ ] **Step 5: Update package re-exports.**

```python
# src/findajob/web/filters/__init__.py — replace contents
"""Per-column filter framework for the board tabs."""
from findajob.web.filters.query import build_filter_clauses
from findajob.web.filters.spec import ColumnSpec, Kind, validate_specs
from findajob.web.filters.url import ParsedFilters, parse_filter_params

__all__ = [
    "ColumnSpec",
    "Kind",
    "ParsedFilters",
    "build_filter_clauses",
    "parse_filter_params",
    "validate_specs",
]
```

- [ ] **Step 6: Commit.**

```bash
git add src/findajob/web/filters/query.py src/findajob/web/filters/__init__.py tests/test_filter_query.py
git commit -m "$(cat <<'EOF'
feat(filters): build_filter_clauses parameterized SQL composer (#273)

Walks the spec list in declaration order and produces a fragment of
the form ' AND c1 AND c2 ...' plus a positional bind-param list. Knows
nothing about FROM/JOIN/SELECT/ORDER BY — each route handler composes
those itself, so the framework stays JOIN-agnostic for the
LEFT-JOIN-audit_log tabs (Applied/Rejected).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Registry — declare ColumnSpecs for all 6 tabs

**Files:**
- Create: `src/findajob/web/filters/registry.py`
- Modify: `tests/test_filter_spec.py` (add registry-level smoke test)

- [ ] **Step 1: Write the registry-level smoke test.**

Append to `tests/test_filter_spec.py`:

```python
def test_all_tab_registries_pass_validate() -> None:
    """Every registered tab spec list passes validate_specs at import time."""
    from findajob.web.filters import registry

    for name in (
        "DASHBOARD_COLUMNS", "APPLIED_COLUMNS", "REVIEW_COLUMNS",
        "WAITLIST_COLUMNS", "REJECTED_COLUMNS", "ARCHIVE_COLUMNS",
    ):
        specs = getattr(registry, name)
        validate_specs(specs)
        assert len(specs) > 0


def test_dashboard_visibility_defaults() -> None:
    from findajob.web.filters import registry

    visible = {s.name for s in registry.DASHBOARD_COLUMNS if s.default_visible}
    hidden = {s.name for s in registry.DASHBOARD_COLUMNS if not s.default_visible}

    # AI notes + Likelihood visible, Prob hidden, Stage hidden — per spec.
    assert "ai_notes" in visible
    assert "interview_likelihood" in visible
    assert "probability_score" in hidden
    assert "stage" in hidden


def test_waitlist_includes_likelihood_visible() -> None:
    from findajob.web.filters import registry

    visible = {s.name for s in registry.WAITLIST_COLUMNS if s.default_visible}
    assert "interview_likelihood" in visible
```

- [ ] **Step 2: Run the test to verify it fails.**

Run: `uv run pytest tests/test_filter_spec.py -v -k registry`
Expected: ModuleNotFoundError on `findajob.web.filters.registry`.

- [ ] **Step 3: Implement the registry.**

```python
# src/findajob/web/filters/registry.py
"""Per-tab ColumnSpec lists for the 6 board tabs.

Visibility defaults are tuned to what the operator needs to *decide* on each
tab — see docs/superpowers/specs/2026-04-25-board-filter-framework-design.md
"Per-tab visibility defaults". Hidden columns remain in the spec so the
?cols= URL override (and the future #277 Columns dropdown) can surface them.
"""
from __future__ import annotations

from findajob.web.filters.spec import ColumnSpec, Kind, validate_specs

# Source / stage / remote_status / reject_reason vocabularies. Single source of
# truth here; if these change, ENUM filters update without touching templates.
_SOURCE_VALUES = (
    "greenhouse_json",
    "ashby_json",
    "ashby",
    "lever_json",
    "jobsapi_linkedin",
    "jobsapi_indeed",
    "gmail_linkedin",
    "gmail_google",
    "manual",
)
_STAGE_VALUES = (
    "scored",
    "manual_review",
    "prep_in_progress",
    "materials_drafted",
    "applied",
    "interview",
    "offer",
    "waitlisted",
    "rejected",
    "not_selected",
    "withdrew",
)
_REMOTE_VALUES = ("Remote", "Hybrid", "On-site", "Unknown")
_REJECT_REASON_VALUES = (
    "Low Fit Score",
    "Not Interested",
    "Compensation",
    "Location",
    "Company Culture",
    "Role Mismatch",
    "Already Applied",
    "Stage Too Early",
    "Stage Too Late",
    "Recruiter Outreach",
    "Other",
)

# ─── Dashboard ────────────────────────────────────────────────────────────────
DASHBOARD_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec(name="relevance_score", label="Rel", kind=Kind.SCORE),
    ColumnSpec(name="fit_score", label="Fit", kind=Kind.SCORE),
    ColumnSpec(
        name="probability_score", label="Prob", kind=Kind.SCORE, default_visible=False,
    ),
    ColumnSpec(name="interview_likelihood", label="Likelihood", kind=Kind.SCORE),
    ColumnSpec(name="title", label="Title", kind=Kind.TEXT),
    ColumnSpec(name="company", label="Company", kind=Kind.TEXT),
    ColumnSpec(
        name="company_history", label="History", kind=Kind.COMPUTED,
        sortable=False, filterable=False,
    ),
    ColumnSpec(name="location", label="Location", kind=Kind.TEXT),
    ColumnSpec(
        name="remote_status", label="Remote", kind=Kind.ENUM, enum_values=_REMOTE_VALUES,
    ),
    ColumnSpec(name="known_contacts", label="Contacts", kind=Kind.TEXT),
    ColumnSpec(
        name="comp_estimate", label="Comp", kind=Kind.TEXT, default_visible=False,
    ),
    ColumnSpec(name="ai_notes", label="AI notes", kind=Kind.TEXT),
    ColumnSpec(name="created_at", label="Date", kind=Kind.DATE),
    # Stage is filterable but not visible by default — score-5/6 triage opt-in.
    ColumnSpec(
        name="stage", label="Stage", kind=Kind.ENUM, enum_values=_STAGE_VALUES,
        default_visible=False,
    ),
)
validate_specs(DASHBOARD_COLUMNS)

# ─── Applied ──────────────────────────────────────────────────────────────────
APPLIED_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec(name="title", label="Title", kind=Kind.TEXT, db_expr="j.title"),
    ColumnSpec(name="company", label="Company", kind=Kind.TEXT, db_expr="j.company"),
    ColumnSpec(
        name="applied_date", label="Applied", kind=Kind.DATE,
        db_expr="al.applied_date",
    ),
    ColumnSpec(
        name="days_since_applied", label="Days", kind=Kind.INTEGER,
        db_expr="CAST((julianday('now') - julianday(al.applied_date)) AS INTEGER)",
    ),
    ColumnSpec(
        name="stage", label="Stage", kind=Kind.ENUM,
        enum_values=("applied", "interview", "offer"), db_expr="j.stage",
    ),
    ColumnSpec(name="user_notes", label="Notes", kind=Kind.TEXT, db_expr="j.user_notes"),
    ColumnSpec(
        name="known_contacts", label="Contacts", kind=Kind.TEXT,
        db_expr="j.known_contacts",
    ),
    ColumnSpec(name="location", label="Location", kind=Kind.TEXT, db_expr="j.location"),
    ColumnSpec(
        name="remote_status", label="Remote", kind=Kind.ENUM, enum_values=_REMOTE_VALUES,
        db_expr="j.remote_status",
    ),
    ColumnSpec(
        name="comp_estimate", label="Comp", kind=Kind.TEXT,
        db_expr="j.comp_estimate", default_visible=False,
    ),
    ColumnSpec(
        name="ai_notes", label="AI notes", kind=Kind.TEXT,
        db_expr="j.ai_notes", default_visible=False,
    ),
)
validate_specs(APPLIED_COLUMNS)

# ─── Review ───────────────────────────────────────────────────────────────────
REVIEW_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec(name="title", label="Title", kind=Kind.TEXT),
    ColumnSpec(name="company", label="Company", kind=Kind.TEXT),
    ColumnSpec(name="score_flag_reason", label="Flag reason", kind=Kind.TEXT),
    ColumnSpec(
        name="source", label="Source", kind=Kind.ENUM, enum_values=_SOURCE_VALUES,
    ),
    ColumnSpec(name="created_at", label="Date", kind=Kind.DATE),
)
validate_specs(REVIEW_COLUMNS)

# ─── Waitlist ─────────────────────────────────────────────────────────────────
WAITLIST_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec(name="title", label="Title", kind=Kind.TEXT, db_expr="w.title"),
    ColumnSpec(name="company", label="Company", kind=Kind.TEXT, db_expr="w.company"),
    ColumnSpec(
        name="company_history", label="History", kind=Kind.COMPUTED,
        sortable=False, filterable=False,
    ),
    ColumnSpec(
        name="relevance_score", label="Rel", kind=Kind.SCORE, db_expr="w.relevance_score",
    ),
    ColumnSpec(name="fit_score", label="Fit", kind=Kind.SCORE, db_expr="w.fit_score"),
    ColumnSpec(
        name="probability_score", label="Prob", kind=Kind.SCORE,
        db_expr="w.probability_score", default_visible=False,
    ),
    ColumnSpec(
        name="interview_likelihood", label="Likelihood", kind=Kind.SCORE,
        db_expr="w.interview_likelihood",
    ),
    ColumnSpec(name="location", label="Location", kind=Kind.TEXT, db_expr="w.location"),
    ColumnSpec(
        name="remote_status", label="Remote", kind=Kind.ENUM, enum_values=_REMOTE_VALUES,
        db_expr="w.remote_status",
    ),
    ColumnSpec(
        name="ai_notes", label="AI notes", kind=Kind.TEXT,
        db_expr="w.ai_notes", default_visible=False,
    ),
    ColumnSpec(name="created_at", label="Date", kind=Kind.DATE, db_expr="w.created_at"),
    ColumnSpec(
        name="blocking_app", label="Blocking app", kind=Kind.COMPUTED,
        sortable=False, filterable=False,
    ),
)
validate_specs(WAITLIST_COLUMNS)

# ─── Rejected ─────────────────────────────────────────────────────────────────
REJECTED_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec(name="title", label="Title", kind=Kind.TEXT, db_expr="j.title"),
    ColumnSpec(name="company", label="Company", kind=Kind.TEXT, db_expr="j.company"),
    ColumnSpec(
        name="reject_reason", label="Reason", kind=Kind.ENUM,
        enum_values=_REJECT_REASON_VALUES, db_expr="j.reject_reason",
    ),
    ColumnSpec(
        name="rejected_date", label="Rejected", kind=Kind.DATE,
        db_expr="al.rejected_date",
    ),
    ColumnSpec(
        name="rejection_source", label="Source", kind=Kind.ENUM,
        enum_values=("user", "company"),
        db_expr="CASE j.stage WHEN 'not_selected' THEN 'company' ELSE 'user' END",
    ),
)
validate_specs(REJECTED_COLUMNS)

# ─── Archive ──────────────────────────────────────────────────────────────────
ARCHIVE_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec(name="relevance_score", label="Rel", kind=Kind.SCORE),
    ColumnSpec(name="title", label="Title", kind=Kind.TEXT),
    ColumnSpec(name="company", label="Company", kind=Kind.TEXT),
    ColumnSpec(
        name="stage", label="Stage", kind=Kind.ENUM, enum_values=_STAGE_VALUES,
    ),
    ColumnSpec(name="location", label="Location", kind=Kind.TEXT),
    ColumnSpec(
        name="remote_status", label="Remote", kind=Kind.ENUM, enum_values=_REMOTE_VALUES,
    ),
    ColumnSpec(name="created_at", label="Date", kind=Kind.DATE),
    ColumnSpec(
        name="source", label="Source", kind=Kind.ENUM, enum_values=_SOURCE_VALUES,
    ),
    ColumnSpec(name="url", label="URL", kind=Kind.TEXT, default_visible=False),
)
validate_specs(ARCHIVE_COLUMNS)


__all__ = [
    "DASHBOARD_COLUMNS",
    "APPLIED_COLUMNS",
    "REVIEW_COLUMNS",
    "WAITLIST_COLUMNS",
    "REJECTED_COLUMNS",
    "ARCHIVE_COLUMNS",
]
```

- [ ] **Step 4: Run tests to verify they pass.**

Run: `uv run pytest tests/test_filter_spec.py -v`
Expected: 12 tests pass (9 from Task 1 + 3 new).

- [ ] **Step 5: Commit.**

```bash
git add src/findajob/web/filters/registry.py tests/test_filter_spec.py
git commit -m "$(cat <<'EOF'
feat(filters): per-tab ColumnSpec registry for all 6 board tabs (#273)

Declares DASHBOARD/APPLIED/REVIEW/WAITLIST/REJECTED/ARCHIVE_COLUMNS as
tuples of ColumnSpec. Visibility defaults match the spec table:
Dashboard adds ai_notes + interview_likelihood as visible, hides
probability_score + stage; Waitlist adds interview_likelihood for
parity with Dashboard's scoring trio. Each registry runs through
validate_specs() at import time.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Active-filters chip strip + Copy-link partial

**Files:**
- Create: `src/findajob/web/templates/_active_filters.html`

This task is template-only — no Python tests. Visual / integration coverage comes via Task 16 (`test_web_board_filters.py`) once routes pass `parsed` into the template.

- [ ] **Step 1: Write the partial.**

```html
{# src/findajob/web/templates/_active_filters.html
   Inputs:
     parsed       — ParsedFilters
     specs        — sequence of ColumnSpec for the current tab (label lookup)
   Renders the chip strip + Clear-all link + Copy-link button.

   Hidden inputs that mirror every active filter live HERE so HTMX
   hx-include="[data-filter-input]" picks them up. The popover form fields
   write their committed values back into these inputs on Apply.
#}
{% set spec_label = {} %}
{% for s in specs %}{% if spec_label.update({s.name: s.label}) %}{% endif %}{% endfor %}
{% if not parsed.is_empty() or parsed.cols or parsed.sort %}
<div class="mt-2 mb-3 flex items-center gap-2 flex-wrap text-xs">
  {% for name, val in parsed.text.items() %}
    <span class="inline-flex items-center gap-1 px-2 py-1 bg-slate-100 rounded">
      <span class="font-semibold">{{ spec_label.get(name, name) }}:</span>
      <span>{{ val }}</span>
      <a href="?{{ filter_remove_qs(parsed, name) }}" class="text-slate-500 hover:text-slate-900" title="Clear">✕</a>
    </span>
  {% endfor %}
  {% for name, (lo, hi) in parsed.numeric_range.items() %}
    <span class="inline-flex items-center gap-1 px-2 py-1 bg-slate-100 rounded">
      <span class="font-semibold">{{ spec_label.get(name, name) }}:</span>
      <span>{{ lo if lo is not none else '−∞' }}–{{ hi if hi is not none else '+∞' }}</span>
      <a href="?{{ filter_remove_qs(parsed, name) }}" class="text-slate-500 hover:text-slate-900" title="Clear">✕</a>
    </span>
  {% endfor %}
  {% for name, picks in parsed.enum.items() %}
    <span class="inline-flex items-center gap-1 px-2 py-1 bg-slate-100 rounded">
      <span class="font-semibold">{{ spec_label.get(name, name) }}:</span>
      <span>{{ picks | join(', ') }}</span>
      <a href="?{{ filter_remove_qs(parsed, name) }}" class="text-slate-500 hover:text-slate-900" title="Clear">✕</a>
    </span>
  {% endfor %}
  {% for name, (d_from, d_to) in parsed.date_range.items() %}
    <span class="inline-flex items-center gap-1 px-2 py-1 bg-slate-100 rounded">
      <span class="font-semibold">{{ spec_label.get(name, name) }}:</span>
      <span>{{ d_from or '…' }} → {{ d_to or '…' }}</span>
      <a href="?{{ filter_remove_qs(parsed, name) }}" class="text-slate-500 hover:text-slate-900" title="Clear">✕</a>
    </span>
  {% endfor %}
  {% if parsed.cols %}
    <span class="inline-flex items-center gap-1 px-2 py-1 bg-blue-50 text-blue-800 rounded">
      <span class="font-semibold">cols:</span>
      <span>{{ parsed.cols | join(', ') }}</span>
      <a href="?{{ filter_remove_qs(parsed, 'cols') }}" class="hover:text-blue-900" title="Reset to default columns">✕</a>
    </span>
  {% endif %}
  {% if not parsed.is_empty() or parsed.cols %}
    <a href="{{ request.url.path }}" class="text-slate-600 hover:text-slate-900 underline">Clear all</a>
  {% endif %}
  <button type="button"
          data-copy-link
          class="ml-auto text-slate-600 hover:text-slate-900 px-2 py-1 border border-slate-300 rounded">
    🔗 <span data-copy-link-label>Copy link</span>
  </button>
</div>
{% else %}
<div class="mt-2 mb-3 flex justify-end">
  <button type="button"
          data-copy-link
          class="text-xs text-slate-600 hover:text-slate-900 px-2 py-1 border border-slate-300 rounded">
    🔗 <span data-copy-link-label>Copy link</span>
  </button>
</div>
{% endif %}
```

- [ ] **Step 2: Add the `filter_remove_qs` Jinja global to `app.py`.**

Modify `src/findajob/web/app.py`. Locate the section that registers Jinja globals (search for `templates.env.globals` or `state.templates.env.globals`). Add:

```python
from urllib.parse import urlencode

from findajob.web.filters import ParsedFilters


def _filter_remove_qs(parsed: ParsedFilters, drop_name: str) -> str:
    """Re-encode parsed filters as a querystring with `drop_name` removed.

    Used by the chip-strip ✕ links to drop a single filter without losing the
    others. `drop_name` may be a column name OR the literal "cols" sentinel.
    """
    pairs: list[tuple[str, str]] = []
    for name, val in parsed.text.items():
        if name != drop_name:
            pairs.append((name, val))
    for name, (lo, hi) in parsed.numeric_range.items():
        if name == drop_name:
            continue
        if lo is not None:
            pairs.append((f"{name}_min", str(lo)))
        if hi is not None:
            pairs.append((f"{name}_max", str(hi)))
    for name, picks in parsed.enum.items():
        if name != drop_name:
            pairs.append((name, ",".join(picks)))
    for name, (d_from, d_to) in parsed.date_range.items():
        if name == drop_name:
            continue
        if d_from is not None:
            pairs.append((f"{name}_from", d_from))
        if d_to is not None:
            pairs.append((f"{name}_to", d_to))
    if parsed.cols and drop_name != "cols":
        pairs.append(("cols", ",".join(parsed.cols)))
    if parsed.sort:
        pairs.append(("sort", parsed.sort))
        pairs.append(("desc", "1" if parsed.desc else "0"))
    return urlencode(pairs)
```

Then register it on the Jinja env:

```python
templates.env.globals["filter_remove_qs"] = _filter_remove_qs
```

(Place this alongside the existing global registrations such as `applied_age_bucket`, `stage_row_class`, `remote_cell_class`. Read `app.py` and locate the existing `templates.env.globals[...] = ...` block; add to it. If the helpers live in `findajob.web.helpers`, define `_filter_remove_qs` there instead and register from `app.py` for consistency.)

- [ ] **Step 3: Smoke-render the partial via a unit test.**

Create `tests/test_active_filters_render.py`:

```python
from __future__ import annotations

from findajob.web.filters import ColumnSpec, Kind, ParsedFilters


def test_active_filters_renders_chip_strip(tmp_path) -> None:
    from findajob.web.app import create_app

    app = create_app()
    templates = app.state.templates

    specs = (
        ColumnSpec(name="title", label="Title", kind=Kind.TEXT),
        ColumnSpec(name="relevance_score", label="Rel", kind=Kind.SCORE),
        ColumnSpec(
            name="stage", label="Stage", kind=Kind.ENUM,
            enum_values=("scored", "applied"),
        ),
    )
    parsed = ParsedFilters(
        text={"title": "director"},
        numeric_range={"relevance_score": (5, 10)},
        enum={"stage": ("scored",)},
    )

    class _StubReq:
        class url: path = "/board/dashboard"
    rendered = templates.get_template("_active_filters.html").render(
        request=_StubReq, parsed=parsed, specs=specs,
    )
    assert "Title:" in rendered
    assert "director" in rendered
    assert "Rel:" in rendered
    assert "5–10" in rendered
    assert "Stage:" in rendered
    assert "scored" in rendered
    assert "Clear all" in rendered
    assert "Copy link" in rendered
```

Run: `uv run pytest tests/test_active_filters_render.py -v`
Expected: PASS.

- [ ] **Step 4: Commit.**

```bash
git add src/findajob/web/templates/_active_filters.html src/findajob/web/app.py tests/test_active_filters_render.py
git commit -m "$(cat <<'EOF'
feat(web): _active_filters.html — chip strip + Clear-all + Copy-link (#273)

Shared partial that renders one chip per active filter (text / numeric
range / enum / date / cols), a "Clear all" link that drops the
querystring entirely, and a "Copy link" button (vanilla-JS clipboard
write attached in static/filters.js, Task 8). Adds filter_remove_qs
Jinja global to rebuild the URL with one filter dropped.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Table-header partial — `_table_header.html`

**Files:**
- Create: `src/findajob/web/templates/_table_header.html`

- [ ] **Step 1: Write the partial.**

```html
{# src/findajob/web/templates/_table_header.html
   Inputs:
     specs        — sequence of ColumnSpec for the tab
     parsed       — ParsedFilters with current values
     visible      — set[str] of column names currently shown (after ?cols= cascade)
     leading_cols — list of (label, css) tuples for non-spec columns rendered first
                    (e.g., dashboard renders Status + Reject cells before the spec
                     columns; archive renders a Promote cell)
   Renders the entire <thead>: column-title row + filter-input row.

   The filter inputs all carry data-filter-input so HTMX hx-include picks them
   up; popovers use Alpine.js (x-data/x-show) and write their committed values
   into hidden inputs that share the same data-filter-input attribute.
#}
{% set tab_path = request.url.path.rstrip('/') %}
{% set rows_url = tab_path + '/rows' %}
<thead class="bg-slate-100 text-left text-xs uppercase tracking-wide text-slate-600">
  <tr>
    {% for label, css in (leading_cols or []) %}
      <th class="px-3 py-2 {{ css or '' }}">{{ label }}</th>
    {% endfor %}
    {% for s in specs if s.name in visible %}
      {% set is_filtered = (
        s.name in parsed.text or s.name in parsed.numeric_range
        or s.name in parsed.enum or s.name in parsed.date_range
      ) %}
      <th class="px-3 py-2 {% if is_filtered %}bg-blue-50{% endif %}">
        {% if s.sortable %}
          <a href="?sort={{ s.name }}&desc={% if parsed.sort == s.name and parsed.desc %}0{% else %}1{% endif %}{% if parsed.cols %}&cols={{ parsed.cols | join(',') }}{% endif %}">
            {{ s.label }}{% if parsed.sort == s.name %}{% if parsed.desc %} ▼{% else %} ▲{% endif %}{% endif %}
          </a>
        {% else %}
          <span>{{ s.label }}</span>
        {% endif %}
        {% if is_filtered %} <span class="text-blue-700">●</span>{% endif %}
      </th>
    {% endfor %}
  </tr>
  <tr class="bg-slate-50 border-b border-slate-200 normal-case">
    {% for label, css in (leading_cols or []) %}
      <th class="px-1 py-1"><span class="text-slate-400">—</span></th>
    {% endfor %}
    {% for s in specs if s.name in visible %}
      <th class="px-1 py-1 align-top">
        {% if not s.filterable %}
          <span class="text-slate-400">—</span>
        {% elif s.kind.value == 'text' %}
          <input type="text"
                 name="{{ s.name }}"
                 data-filter-input
                 value="{{ parsed.text.get(s.name, '') }}"
                 placeholder="contains…"
                 class="border border-slate-300 rounded px-1 py-0.5 text-xs w-full"
                 hx-get="{{ rows_url }}"
                 hx-trigger="keyup changed delay:200ms"
                 hx-target="#rows"
                 hx-swap="innerHTML"
                 hx-include="[data-filter-input]"
                 hx-push-url="true">
        {% elif s.kind.value in ('score', 'integer') %}
          {% set lo, hi = parsed.numeric_range.get(s.name, (none, none)) %}
          <span class="inline-flex items-center gap-0.5">
            <input type="number"
                   name="{{ s.name }}_min"
                   data-filter-input
                   value="{{ lo if lo is not none else '' }}"
                   placeholder="≥"
                   class="border border-slate-300 rounded px-1 py-0.5 text-xs w-12"
                   hx-get="{{ rows_url }}"
                   hx-trigger="keyup changed delay:200ms"
                   hx-target="#rows"
                   hx-swap="innerHTML"
                   hx-include="[data-filter-input]"
                   hx-push-url="true">
            <span class="text-slate-400">–</span>
            <input type="number"
                   name="{{ s.name }}_max"
                   data-filter-input
                   value="{{ hi if hi is not none else '' }}"
                   placeholder="≤"
                   class="border border-slate-300 rounded px-1 py-0.5 text-xs w-12"
                   hx-get="{{ rows_url }}"
                   hx-trigger="keyup changed delay:200ms"
                   hx-target="#rows"
                   hx-swap="innerHTML"
                   hx-include="[data-filter-input]"
                   hx-push-url="true">
          </span>
        {% elif s.kind.value == 'enum' %}
          {% set picks = parsed.enum.get(s.name, ()) %}
          <div x-data="{ open: false }" class="relative">
            <button type="button"
                    @click="open = !open"
                    class="text-xs text-slate-700 hover:text-slate-900">
              {% if picks %}{{ picks | length }} selected{% else %}any{% endif %} ▾
            </button>
            <input type="hidden"
                   name="{{ s.name }}"
                   data-filter-input
                   value="{{ picks | join(',') }}"
                   data-popover-target="{{ s.name }}">
            <div x-show="open"
                 x-cloak
                 @click.outside="open = false"
                 class="absolute z-10 mt-1 bg-white border border-slate-300 rounded shadow-lg p-2 min-w-[10rem] text-xs">
              <div class="font-semibold uppercase text-slate-500 mb-1">{{ s.label }}</div>
              {% for v in s.enum_values %}
                <label class="block">
                  <input type="checkbox"
                         value="{{ v }}"
                         data-popover-checkbox="{{ s.name }}"
                         {% if v in picks %}checked{% endif %}>
                  {{ v }}
                </label>
              {% endfor %}
              <div class="mt-2 flex gap-1">
                <button type="button"
                        data-popover-apply="{{ s.name }}"
                        @click="open = false"
                        class="px-2 py-0.5 bg-slate-900 text-white rounded text-xs">Apply</button>
                <button type="button"
                        data-popover-clear="{{ s.name }}"
                        @click="open = false"
                        class="px-2 py-0.5 bg-slate-100 text-slate-700 rounded text-xs">Clear</button>
                <button type="button"
                        @click="open = false"
                        class="px-2 py-0.5 text-slate-500 text-xs">Cancel</button>
              </div>
            </div>
          </div>
        {% elif s.kind.value == 'date' %}
          {% set d_from, d_to = parsed.date_range.get(s.name, (none, none)) %}
          <div x-data="{ open: false }" class="relative">
            <button type="button"
                    @click="open = !open"
                    class="text-xs text-slate-700 hover:text-slate-900">
              {% if d_from or d_to %}{{ d_from or '…' }}–{{ d_to or '…' }}{% else %}any{% endif %} ▾
            </button>
            <input type="hidden"
                   name="{{ s.name }}_from"
                   data-filter-input
                   value="{{ d_from or '' }}"
                   data-popover-target="{{ s.name }}_from">
            <input type="hidden"
                   name="{{ s.name }}_to"
                   data-filter-input
                   value="{{ d_to or '' }}"
                   data-popover-target="{{ s.name }}_to">
            <div x-show="open"
                 x-cloak
                 @click.outside="open = false"
                 class="absolute z-10 mt-1 bg-white border border-slate-300 rounded shadow-lg p-2 text-xs">
              <div class="font-semibold uppercase text-slate-500 mb-1">{{ s.label }}</div>
              <label class="block mb-1">From: <input type="date" data-popover-date-from="{{ s.name }}" value="{{ d_from or '' }}" class="border border-slate-300 rounded px-1 py-0.5"></label>
              <label class="block mb-2">Until: <input type="date" data-popover-date-to="{{ s.name }}" value="{{ d_to or '' }}" class="border border-slate-300 rounded px-1 py-0.5"></label>
              <div class="flex gap-1">
                <button type="button"
                        data-popover-apply="{{ s.name }}"
                        @click="open = false"
                        class="px-2 py-0.5 bg-slate-900 text-white rounded text-xs">Apply</button>
                <button type="button"
                        data-popover-clear="{{ s.name }}"
                        @click="open = false"
                        class="px-2 py-0.5 bg-slate-100 text-slate-700 rounded text-xs">Clear</button>
                <button type="button"
                        @click="open = false"
                        class="px-2 py-0.5 text-slate-500 text-xs">Cancel</button>
              </div>
            </div>
          </div>
        {% else %}
          <span class="text-slate-400">—</span>
        {% endif %}
      </th>
    {% endfor %}
  </tr>
  {# Hidden sort inputs so HTMX hx-include picks them up regardless of column visibility #}
  {% if parsed.sort %}<input type="hidden" name="sort" value="{{ parsed.sort }}" data-filter-input>{% endif %}
  <input type="hidden" name="desc" value="{{ '1' if parsed.desc else '0' }}" data-filter-input>
  {% if parsed.cols %}<input type="hidden" name="cols" value="{{ parsed.cols | join(',') }}" data-filter-input>{% endif %}
</thead>
```

- [ ] **Step 2: Smoke-render the partial via a unit test.**

Create `tests/test_table_header_render.py`:

```python
from __future__ import annotations

from findajob.web.filters import ColumnSpec, Kind, ParsedFilters


def test_table_header_renders_filter_row() -> None:
    from findajob.web.app import create_app

    app = create_app()
    templates = app.state.templates

    specs = (
        ColumnSpec(name="title", label="Title", kind=Kind.TEXT),
        ColumnSpec(name="relevance_score", label="Rel", kind=Kind.SCORE),
        ColumnSpec(
            name="stage", label="Stage", kind=Kind.ENUM,
            enum_values=("scored", "applied"),
        ),
    )
    parsed = ParsedFilters(
        text={"title": "director"},
        numeric_range={"relevance_score": (5, 10)},
        sort="relevance_score",
        desc=True,
    )

    class _StubReq:
        class url: path = "/board/dashboard"
    rendered = templates.get_template("_table_header.html").render(
        request=_StubReq, specs=specs, parsed=parsed,
        visible={"title", "relevance_score", "stage"},
        leading_cols=[("Status", ""), ("Reject", "")],
    )
    # Sort link includes column name and a flip-direction querystring.
    assert "?sort=relevance_score&desc=0" in rendered
    # Active filter shows ● indicator on title and relevance_score columns.
    assert rendered.count("●") >= 2
    # Inline TEXT input present and pre-filled.
    assert 'name="title"' in rendered
    assert 'value="director"' in rendered
    # Range inputs present for SCORE.
    assert 'name="relevance_score_min"' in rendered
    assert 'name="relevance_score_max"' in rendered
    # ENUM popover hidden input present.
    assert 'name="stage"' in rendered and 'data-filter-input' in rendered
    # HTMX wiring present.
    assert 'hx-get="/board/dashboard/rows"' in rendered
    assert 'hx-push-url="true"' in rendered
```

Run: `uv run pytest tests/test_table_header_render.py -v`
Expected: PASS.

- [ ] **Step 3: Commit.**

```bash
git add src/findajob/web/templates/_table_header.html tests/test_table_header_render.py
git commit -m "$(cat <<'EOF'
feat(web): _table_header.html — renders thead from ColumnSpec (#273)

Two-row thead: column-title row (with sort links + active-filter dot
indicator) and per-kind filter input row. TEXT = inline <input>;
SCORE/INTEGER = inline range pair; ENUM/DATE = Alpine popover trigger
with hidden input data-filter-input that HTMX picks up. All filter
inputs carry data-filter-input + hx-push-url so URL state stays in
sync. leading_cols param keeps Status/Reject + Promote cells working.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Refactor `_filters.html` to be the top filter bar

**Files:**
- Modify: `src/findajob/web/templates/_filters.html`

- [ ] **Step 1: Replace the partial contents.**

```html
{# src/findajob/web/templates/_filters.html
   Top filter bar above each board tab's <table>.
   Inputs: parsed (ParsedFilters), specs (current tab's ColumnSpec list),
           density (string).
   Owns: density toggle, active-filter chip strip include, Copy-link button.
   Per-column inputs live inside the <table>'s <thead> via _table_header.html.
#}
{% set current_density = density | default('compact') %}
<div class="mb-1 flex items-center gap-3 flex-wrap">
  <div class="text-xs text-slate-600 flex items-center gap-1">
    <span>Rows:</span>
    {% set base_qs = request.url.query %}
    <a href="?{{ filter_qs_with(base_qs, 'density', 'compact') }}"
       class="rounded px-2 py-1 {% if current_density == 'compact' %}bg-slate-900 text-white{% else %}bg-slate-100 text-slate-700 hover:bg-slate-200{% endif %}">Compact</a>
    <a href="?{{ filter_qs_with(base_qs, 'density', 'expanded') }}"
       class="rounded px-2 py-1 {% if current_density == 'expanded' %}bg-slate-900 text-white{% else %}bg-slate-100 text-slate-700 hover:bg-slate-200{% endif %}">Expanded</a>
  </div>
</div>
{% include "_active_filters.html" %}
```

- [ ] **Step 2: Add `filter_qs_with` Jinja global** so the density toggle preserves the rest of the querystring instead of clobbering it.

In `src/findajob/web/app.py` (alongside `_filter_remove_qs`), add:

```python
def _filter_qs_with(existing: str, key: str, value: str) -> str:
    """Return a re-encoded querystring with `key` set to `value`.

    Preserves all other params. Used by the density toggle to switch
    compact/expanded without losing active filters or sort.
    """
    from urllib.parse import parse_qsl, urlencode

    pairs = [(k, v) for (k, v) in parse_qsl(existing, keep_blank_values=False) if k != key]
    pairs.append((key, value))
    return urlencode(pairs)
```

Register: `templates.env.globals["filter_qs_with"] = _filter_qs_with`.

- [ ] **Step 3: Run all template-render tests.**

Run: `uv run pytest tests/test_active_filters_render.py tests/test_table_header_render.py -v`
Expected: both PASS (no regressions from `_filters.html` rewrite — neither test imports `_filters.html`).

- [ ] **Step 4: Commit.**

```bash
git add src/findajob/web/templates/_filters.html src/findajob/web/app.py
git commit -m "$(cat <<'EOF'
feat(web): repurpose _filters.html as top filter bar (#273)

Top filter bar now owns the density toggle and includes the
_active_filters.html chip strip. Old ?q= text input is removed —
per-column TEXT inputs live in the <thead> via _table_header.html.
Density toggle preserves the rest of the querystring via the new
filter_qs_with Jinja global.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: filters.js — popover Apply/Cancel/Clear + clipboard write

**Files:**
- Create: `src/findajob/web/static/filters.js`
- Modify: `src/findajob/web/templates/base.html` to include the script

- [ ] **Step 1: Write `filters.js`.**

```js
// src/findajob/web/static/filters.js
//
// Popover Apply/Cancel/Clear handlers + clipboard-write for Copy-link.
//
// The header partial (_table_header.html) renders ENUM/DATE popovers as
// Alpine.js components for open/close. Apply/Clear/Cancel are wired here so
// they (1) write committed values back into the hidden inputs that HTMX
// includes in /rows requests and (2) trigger an htmx event so the URL +
// table refresh.

(function () {
  function $(sel, root) {
    return (root || document).querySelector(sel);
  }
  function $$(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  function commitEnumPopover(name) {
    var hidden = document.querySelector('input[type=hidden][data-popover-target="' + name + '"]');
    if (!hidden) return;
    var checked = $$('input[type=checkbox][data-popover-checkbox="' + name + '"]:checked');
    hidden.value = checked.map(function (cb) { return cb.value; }).join(',');
    htmx.trigger(hidden, 'change');
  }

  function clearEnumPopover(name) {
    var hidden = document.querySelector('input[type=hidden][data-popover-target="' + name + '"]');
    if (!hidden) return;
    hidden.value = '';
    $$('input[type=checkbox][data-popover-checkbox="' + name + '"]').forEach(function (cb) {
      cb.checked = false;
    });
    htmx.trigger(hidden, 'change');
  }

  function commitDatePopover(name) {
    var hiddenFrom = document.querySelector('input[type=hidden][data-popover-target="' + name + '_from"]');
    var hiddenTo = document.querySelector('input[type=hidden][data-popover-target="' + name + '_to"]');
    var dateFrom = document.querySelector('input[data-popover-date-from="' + name + '"]');
    var dateTo = document.querySelector('input[data-popover-date-to="' + name + '"]');
    if (hiddenFrom) hiddenFrom.value = dateFrom ? dateFrom.value : '';
    if (hiddenTo) hiddenTo.value = dateTo ? dateTo.value : '';
    if (hiddenFrom) htmx.trigger(hiddenFrom, 'change');
  }

  function clearDatePopover(name) {
    var hiddenFrom = document.querySelector('input[type=hidden][data-popover-target="' + name + '_from"]');
    var hiddenTo = document.querySelector('input[type=hidden][data-popover-target="' + name + '_to"]');
    var dateFrom = document.querySelector('input[data-popover-date-from="' + name + '"]');
    var dateTo = document.querySelector('input[data-popover-date-to="' + name + '"]');
    if (hiddenFrom) hiddenFrom.value = '';
    if (hiddenTo) hiddenTo.value = '';
    if (dateFrom) dateFrom.value = '';
    if (dateTo) dateTo.value = '';
    if (hiddenFrom) htmx.trigger(hiddenFrom, 'change');
  }

  document.addEventListener('click', function (e) {
    var t = e.target.closest('[data-popover-apply], [data-popover-clear], [data-copy-link]');
    if (!t) return;

    if (t.dataset.popoverApply) {
      var name = t.dataset.popoverApply;
      // Date popovers have separate from/to hidden inputs; ENUM has one.
      if (document.querySelector('input[data-popover-date-from="' + name + '"]')) {
        commitDatePopover(name);
      } else {
        commitEnumPopover(name);
      }
      return;
    }
    if (t.dataset.popoverClear) {
      var name2 = t.dataset.popoverClear;
      if (document.querySelector('input[data-popover-date-from="' + name2 + '"]')) {
        clearDatePopover(name2);
      } else {
        clearEnumPopover(name2);
      }
      return;
    }
    if (t.hasAttribute('data-copy-link')) {
      var label = t.querySelector('[data-copy-link-label]');
      var original = label ? label.textContent : null;
      function flash(msg) {
        if (label) {
          label.textContent = msg;
          setTimeout(function () { label.textContent = original; }, 1500);
        }
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(window.location.href).then(
          function () { flash('Copied!'); },
          function () { flash('Copy failed'); }
        );
      } else {
        flash('Clipboard unavailable');
      }
    }
  });
})();
```

- [ ] **Step 2: Wire `filters.js` into `base.html`.**

Modify `src/findajob/web/templates/base.html`. Locate the existing `<script>` tags (where HTMX and Alpine are already loaded). Add **after** Alpine.js and HTMX:

```html
<script src="{{ url_for('static', path='filters.js') }}" defer></script>
```

- [ ] **Step 3: Smoke-check via app boot.**

Run: `uv run python -c "from findajob.web.app import create_app; create_app()"`
Expected: clean exit, no exceptions.

- [ ] **Step 4: Commit.**

```bash
git add src/findajob/web/static/filters.js src/findajob/web/templates/base.html
git commit -m "$(cat <<'EOF'
feat(web): filters.js — popover commit/clear + clipboard Copy-link (#273)

Vanilla-JS click delegate handles three jobs:
  - data-popover-apply: read checkboxes (ENUM) or date inputs (DATE),
    write committed values into hidden inputs, trigger htmx change.
  - data-popover-clear: reset checkboxes/dates, clear hidden inputs,
    trigger htmx change.
  - data-copy-link: navigator.clipboard.writeText(location.href) with
    a 1.5s "Copied!" label flash; degrades gracefully on browsers
    without the clipboard API.

Loaded via base.html after Alpine + HTMX.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Wire Dashboard route to the framework + update template

**Files:**
- Modify: `src/findajob/web/routes/board.py` — replace `dashboard()` and `dashboard_rows()`.
- Modify: `src/findajob/web/templates/board/dashboard.html`.

This is the prototype. We migrate Dashboard fully (route + template), confirm it works end-to-end, then mirror the pattern in Tasks 10-14.

- [ ] **Step 1: Replace `dashboard()` and `dashboard_rows()` in `board.py`.**

Open `src/findajob/web/routes/board.py`. At the top, add the new imports:

```python
from findajob.web.filters import ParsedFilters, build_filter_clauses, parse_filter_params
from findajob.web.filters import registry as filter_registry
```

Replace the existing `_DASHBOARD_COLS`, `_DASHBOARD_SORTABLE`, `_DASHBOARD_DEFAULT_SORT`, `_DASHBOARD_WHERE`, `dashboard()`, and `dashboard_rows()` block with:

```python
_DASHBOARD_DEFAULT_SORT = "relevance_score"

# Base WHERE always intersects user filters (see spec "Default landings").
_DASHBOARD_BASE_WHERE = (
    "((relevance_score >= 7 AND stage IN ('scored','manual_review'))"
    " OR stage IN ('prep_in_progress','materials_drafted'))"
    " AND NOT EXISTS ("
    "  SELECT 1 FROM jobs sib"
    "  WHERE sib.id != jobs.id"
    "    AND LOWER(TRIM(sib.company)) = LOWER(TRIM(jobs.company))"
    "    AND LOWER(TRIM(sib.title)) = LOWER(TRIM(jobs.title))"
    "    AND sib.stage IN ('applied','interview','offer','not_selected')"
    " )"
)


def _resolve_visible(specs, parsed: ParsedFilters) -> set[str]:
    """Cascade: URL ?cols= > ColumnSpec.default_visible. (Persisted prefs in #277.)"""
    if parsed.cols:
        return set(parsed.cols)
    return {s.name for s in specs if s.default_visible}


def _dashboard_query(parsed: ParsedFilters) -> tuple[str, list[object]]:
    specs = filter_registry.DASHBOARD_COLUMNS
    clauses, params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or _DASHBOARD_DEFAULT_SORT
    order = "DESC" if parsed.desc else "ASC"
    sql = (
        "SELECT fingerprint, title, company, location, remote_status, known_contacts, "
        "comp_estimate, ai_notes, relevance_score, fit_score, probability_score, "
        "interview_likelihood, stage, created_at, stage_updated, url, prep_folder_path "
        f"FROM jobs WHERE ({_DASHBOARD_BASE_WHERE}){clauses} ORDER BY {sort} {order}"
    )
    return sql, params


@router.get("/board/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.DASHBOARD_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _dashboard_query(parsed)
    rows = db.execute(sql, params).fetchall()
    history_by_fp = build_history_by_fp(rows, fetch_company_history(db))
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/dashboard.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "history_by_fp": history_by_fp,
            "density": _normalize_density(density),
            "tab": "dashboard",
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/dashboard/rows", response_class=HTMLResponse)
def dashboard_rows(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.DASHBOARD_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _dashboard_query(parsed)
    rows = db.execute(sql, params).fetchall()
    history_by_fp = build_history_by_fp(rows, fetch_company_history(db))
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "specs": specs,
            "visible": visible,
            "rows": rows,
            "history_by_fp": history_by_fp,
            "tab": "dashboard",
            "materials_base_url": materials_base_url,
        },
    )
```

- [ ] **Step 2: Update `_job_rows_fragment.html` and `_job_row.html` to consume `specs`/`visible` instead of `columns`.**

Modify `src/findajob/web/templates/_job_rows_fragment.html`:

```html
{% for row in rows %}
  {% include "_job_row.html" %}
{% else %}
  {% set spec_count = (specs | selectattr('name', 'in', visible) | list | length) %}
  <tr><td colspan="{{ spec_count + 2 }}" class="px-3 py-4 text-slate-500">No matches.</td></tr>
{% endfor %}
```

Modify `src/findajob/web/templates/_job_row.html`. Replace the `{% for display, field in columns %}` loop with `{% for s in specs if s.name in visible %}` and reference `s.name` instead of `field`:

```html
{% set row_classes = [] %}
{% if tab == "applied" %}
  {% set age_class = applied_age_bucket(row.applied_date) %}
  {% if age_class %}{% set _ = row_classes.append(age_class) %}{% endif %}
  {% set stage_class = stage_row_class(row.stage) %}
  {% if stage_class %}{% set _ = row_classes.append(stage_class) %}{% endif %}
{% endif %}
<tr class="{{ row_classes | join(' ') }}" data-fingerprint="{{ row.fingerprint }}">
  {% if tab in ('dashboard', 'applied', 'review', 'waitlist') %}
    {% include "board/_status_cell.html" %}
    {% include "board/_reject_cell.html" %}
  {% elif tab == 'archive' %}
    {% include "board/_archive_promote_cell.html" %}
  {% endif %}
  {% for s in specs if s.name in visible %}
    {% set field = s.name %}
    {% if tab == 'applied' and field == 'user_notes' %}
      {% include "board/_notes_cell.html" %}
    {% elif field == 'company_history' %}
      {% include "board/_company_history_cell.html" %}
    {% else %}
    <td class="px-3 py-1 align-top text-sm
               {% if field == 'known_contacts' and row[field] %}cell-contact-amber{% endif %}
               {% if field == 'remote_status' %}{{ remote_cell_class(row[field]) }}{% endif %}">
      <div class="cell-text-wrap" title="{{ row[field] if row[field] is not none else '' }}">
      {% if field == 'title' and row.url %}
        <a class="underline" href="{{ row.url }}" target="_blank" rel="noopener noreferrer">{{ row[field] }}</a>
      {% elif field == 'company' and row.fingerprint and row.stage in folder_stages and materials_base_url %}
        <a class="underline" href="{{ materials_base_url }}/materials/{{ row.fingerprint }}">{{ row[field] }}</a>
      {% elif field in ('fit_score', 'probability_score', 'interview_likelihood') %}
        {{ row[field] | round | int if row[field] is not none else '—' }}
      {% else %}
        {{ row[field] if row[field] is not none else '' }}
      {% endif %}
      </div>
    </td>
    {% endif %}
  {% endfor %}
</tr>
```

- [ ] **Step 3: Update `dashboard.html` to use `_table_header.html`.**

Replace the contents of `src/findajob/web/templates/board/dashboard.html`:

```html
{% extends "base.html" %}
{% block title %}Dashboard — findajob{% endblock %}
{% block main_classes %}w-full px-4 py-6{% endblock %}
{% block content %}
<h1 class="text-2xl font-semibold mb-4">Dashboard</h1>
{% include "board/_tabs.html" %}
{% include "_filters.html" %}
<table class="min-w-full bg-white shadow-sm rounded-sm density-{{ density | default('compact') }}">
  {% include "_table_header.html" with context %}
  {% set _leading = [('Status',''), ('Reject','')] %}
  {# fall through to _table_header rendered above; leading_cols passed via Jinja-context-trick #}
  <tbody id="rows">
    {% include "_job_rows_fragment.html" %}
  </tbody>
</table>
{% endblock %}
```

The `leading_cols` variable needs to reach the included `_table_header.html`. Pass it via the template context from the route, OR set it in the template:

Replace the relevant lines:

```html
{% set leading_cols = [('Status', ''), ('Reject', '')] %}
{% include "_table_header.html" %}
```

(Jinja `set` is hoisted into the include's scope when `with context` is the default — verify locally.)

- [ ] **Step 4: Smoke-test the route end-to-end.**

Run the unit tests:

```bash
uv run pytest tests/test_web_board_tabs.py tests/test_web_board_sort.py -v
```

Expected: existing tests will need updates because the URL contract changed (`?q=` is gone, sort still works). Some failures expected — those go to Task 17. **Tab-load tests should still PASS** (the dashboard URL with no params still renders and returns 200).

Also run a manual local smoke:

```bash
uv run uvicorn findajob.web.app:create_app --factory --port 8090 --reload &
sleep 2
curl -s "http://localhost:8090/board/dashboard?relevance_score_min=5&stage=scored,manual_review" | grep -c "<tr"
kill %1
```

Expected: row count > 0 (assuming local dev DB has any score-5+ scored/manual_review rows). If the local DB is empty, just confirm a 200 with `curl -sI`.

- [ ] **Step 5: Commit.**

```bash
git add src/findajob/web/routes/board.py src/findajob/web/templates/board/dashboard.html src/findajob/web/templates/_job_row.html src/findajob/web/templates/_job_rows_fragment.html
git commit -m "$(cat <<'EOF'
feat(web): wire Dashboard to filter framework (#273)

Dashboard route now: parses ParsedFilters from query string, walks the
DASHBOARD_COLUMNS spec list, composes WHERE via build_filter_clauses,
resolves visible columns via the URL > spec-default cascade. Template
swaps the hand-coded thead for _table_header.html; row partial reads
specs+visible instead of (display, field) tuples.

Base WHERE preserved (the 7+ happy path is unchanged on cold load).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Wire Applied route + template

**Files:**
- Modify: `src/findajob/web/routes/board.py` — replace `applied()` and `applied_rows()`.
- Modify: `src/findajob/web/templates/board/applied.html`.

- [ ] **Step 1: Replace the Applied route block.**

In `board.py`, remove the existing `_APPLIED_COLS`, `_APPLIED_SORTABLE`, `_APPLIED_DEFAULT_SORT`, `applied()`, and `applied_rows()`. Add:

```python
_APPLIED_DEFAULT_SORT = "applied_date"
_APPLIED_BASE_WHERE = "j.stage IN ('applied','interview','offer')"


def _applied_source() -> str:
    """FROM/JOIN clause for Applied — LEFT JOIN audit_log for applied_date."""
    return (
        "FROM jobs j "
        "LEFT JOIN ("
        "  SELECT job_id, MIN(changed_at) AS applied_date "
        "  FROM audit_log "
        "  WHERE field_changed = 'stage' AND new_value IN ('applied','interview','offer') "
        "  GROUP BY job_id"
        ") al ON al.job_id = j.id"
    )


def _applied_query(parsed: ParsedFilters) -> tuple[str, list[object]]:
    specs = filter_registry.APPLIED_COLUMNS
    clauses, params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or _APPLIED_DEFAULT_SORT
    # sort by spec name → use db_expr if defined; else the bare name.
    sort_spec = next((s for s in specs if s.name == sort), None)
    sort_ref = sort_spec.sql_ref if sort_spec else _APPLIED_DEFAULT_SORT
    order = "DESC" if parsed.desc else "ASC"
    sql = (
        "SELECT j.fingerprint, j.title, j.company, j.stage, j.location, j.remote_status, "
        "       j.known_contacts, j.comp_estimate, j.ai_notes, j.user_notes, j.created_at, "
        "       j.url, "
        "       al.applied_date, "
        "       CAST((julianday('now') - julianday(al.applied_date)) AS INTEGER) AS days_since_applied "
        f"{_applied_source()} "
        f"WHERE ({_APPLIED_BASE_WHERE}){clauses} "
        f"ORDER BY {sort_ref} {order}"
    )
    return sql, params


@router.get("/board/applied", response_class=HTMLResponse)
def applied(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.APPLIED_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _applied_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/applied.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "density": _normalize_density(density),
            "tab": "applied",
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/applied/rows", response_class=HTMLResponse)
def applied_rows(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.APPLIED_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _applied_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "specs": specs,
            "visible": visible,
            "rows": rows,
            "tab": "applied",
            "materials_base_url": materials_base_url,
        },
    )
```

- [ ] **Step 2: Update `applied.html` to mirror `dashboard.html`.**

Replace contents of `src/findajob/web/templates/board/applied.html`:

```html
{% extends "base.html" %}
{% block title %}Applied — findajob{% endblock %}
{% block main_classes %}w-full px-4 py-6{% endblock %}
{% block content %}
<h1 class="text-2xl font-semibold mb-4">Applied</h1>
{% include "board/_tabs.html" %}
{% include "_filters.html" %}
<table class="min-w-full bg-white shadow-sm rounded-sm density-{{ density | default('compact') }}">
  {% set leading_cols = [('Status', ''), ('Reject', '')] %}
  {% include "_table_header.html" %}
  <tbody id="rows">
    {% include "_job_rows_fragment.html" %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 3: Run smoke tests.**

```bash
uv run pytest tests/test_web_board_applied.py -v
```

Expected: some test failures from URL-contract changes (handled in Task 17). Tab-renders-200 tests should pass.

- [ ] **Step 4: Commit.**

```bash
git add src/findajob/web/routes/board.py src/findajob/web/templates/board/applied.html
git commit -m "$(cat <<'EOF'
feat(web): wire Applied to filter framework (#273)

Applied route + template ride the same shape as Dashboard. Per-tab
_applied_source() helper holds the LEFT JOIN audit_log for applied_date
+ days_since_applied; framework's build_filter_clauses stays JOIN-agnostic.
Sort honors db_expr so days_since_applied / applied_date sort correctly
even though they live on the audit_log alias.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Wire Review route + template

**Files:**
- Modify: `src/findajob/web/routes/board.py` — replace `review()` and `review_rows()`.
- Modify: `src/findajob/web/templates/board/review.html`.

- [ ] **Step 1: Replace the Review route block.**

In `board.py`, remove existing `_REVIEW_COLS`, `_REVIEW_SORTABLE`, `_REVIEW_DEFAULT_SORT`, `review()`, `review_rows()`. Add:

```python
_REVIEW_DEFAULT_SORT = "created_at"
_REVIEW_BASE_WHERE = "stage = 'manual_review'"


def _review_query(parsed: ParsedFilters) -> tuple[str, list[object]]:
    specs = filter_registry.REVIEW_COLUMNS
    clauses, params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or _REVIEW_DEFAULT_SORT
    order = "DESC" if parsed.desc else "ASC"
    sql = (
        "SELECT fingerprint, title, company, score_flag_reason, source, created_at, stage, url "
        f"FROM jobs WHERE ({_REVIEW_BASE_WHERE}){clauses} "
        f"ORDER BY {sort} {order}"
    )
    return sql, params


@router.get("/board/review", response_class=HTMLResponse)
def review(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.REVIEW_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _review_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/review.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "density": _normalize_density(density),
            "tab": "review",
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/review/rows", response_class=HTMLResponse)
def review_rows(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.REVIEW_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _review_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "specs": specs,
            "visible": visible,
            "rows": rows,
            "tab": "review",
            "materials_base_url": materials_base_url,
        },
    )
```

- [ ] **Step 2: Replace `review.html`.**

```html
{% extends "base.html" %}
{% block title %}Review — findajob{% endblock %}
{% block main_classes %}w-full px-4 py-6{% endblock %}
{% block content %}
<h1 class="text-2xl font-semibold mb-4">Manual Review</h1>
{% include "board/_tabs.html" %}
{% include "_filters.html" %}
<table class="min-w-full bg-white shadow-sm rounded-sm density-{{ density | default('compact') }}">
  {% set leading_cols = [('Status', ''), ('Reject', '')] %}
  {% include "_table_header.html" %}
  <tbody id="rows">
    {% include "_job_rows_fragment.html" %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 3: Run smoke tests.**

```bash
uv run pytest tests/test_web_board_review.py -v
```

Expected: tab-renders-200 tests pass; URL-param tests covered in Task 17.

- [ ] **Step 4: Commit.**

```bash
git add src/findajob/web/routes/board.py src/findajob/web/templates/board/review.html
git commit -m "$(cat <<'EOF'
feat(web): wire Review to filter framework (#273)

Same shape as Dashboard/Applied; no JOINs needed for Review.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Wire Waitlist route + template

**Files:**
- Modify: `src/findajob/web/routes/board.py` — replace `waitlist()` and `waitlist_rows()`.
- Modify: `src/findajob/web/templates/board/waitlist.html`.

- [ ] **Step 1: Replace the Waitlist route block.**

In `board.py`, remove existing `_WAITLIST_COLS`, `_WAITLIST_SORTABLE`, `_WAITLIST_DEFAULT_SORT`, `waitlist()`, `waitlist_rows()`. Add:

```python
_WAITLIST_DEFAULT_SORT = "w.created_at"
_WAITLIST_BASE_WHERE = "w.stage = 'waitlisted'"


def _waitlist_query(parsed: ParsedFilters) -> tuple[str, list[object]]:
    specs = filter_registry.WAITLIST_COLUMNS
    clauses, params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or "created_at"
    sort_spec = next((s for s in specs if s.name == sort), None)
    sort_ref = sort_spec.sql_ref if sort_spec else _WAITLIST_DEFAULT_SORT
    order = "DESC" if parsed.desc else "ASC"
    sql = f"""
    SELECT w.fingerprint, w.title, w.company, w.relevance_score,
           w.fit_score, w.probability_score, w.interview_likelihood,
           w.location, w.remote_status,
           w.ai_notes, w.created_at, w.stage, w.url,
           (SELECT j2.title || ' (' || j2.stage || ')'
              FROM jobs j2
             WHERE j2.company = w.company
               AND j2.fingerprint != w.fingerprint
               AND j2.stage IN ('applied','interview','offer','materials_drafted','prep_in_progress')
             ORDER BY j2.stage_updated DESC
             LIMIT 1) AS blocking_app
    FROM jobs w
    WHERE ({_WAITLIST_BASE_WHERE}){clauses}
    ORDER BY {sort_ref} {order}
    """
    return sql, params


@router.get("/board/waitlist", response_class=HTMLResponse)
def waitlist(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.WAITLIST_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _waitlist_query(parsed)
    rows = db.execute(sql, params).fetchall()
    history_by_fp = build_history_by_fp(rows, fetch_company_history(db))
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/waitlist.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "history_by_fp": history_by_fp,
            "density": _normalize_density(density),
            "tab": "waitlist",
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/waitlist/rows", response_class=HTMLResponse)
def waitlist_rows(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.WAITLIST_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _waitlist_query(parsed)
    rows = db.execute(sql, params).fetchall()
    history_by_fp = build_history_by_fp(rows, fetch_company_history(db))
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "specs": specs,
            "visible": visible,
            "rows": rows,
            "history_by_fp": history_by_fp,
            "tab": "waitlist",
            "materials_base_url": materials_base_url,
        },
    )
```

Note: the SELECT now also pulls `w.interview_likelihood` since the registry made Likelihood visible by default for Waitlist.

- [ ] **Step 2: Replace `waitlist.html`.**

```html
{% extends "base.html" %}
{% block title %}Waitlist — findajob{% endblock %}
{% block main_classes %}w-full px-4 py-6{% endblock %}
{% block content %}
<h1 class="text-2xl font-semibold mb-4">Waitlist</h1>
{% include "board/_tabs.html" %}
{% include "_filters.html" %}
<table class="min-w-full bg-white shadow-sm rounded-sm density-{{ density | default('compact') }}">
  {% set leading_cols = [('Status', ''), ('Reject', '')] %}
  {% include "_table_header.html" %}
  <tbody id="rows">
    {% include "_job_rows_fragment.html" %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 3: Smoke-test.**

```bash
uv run pytest tests/test_web_board_tabs.py::test_waitlist_tab_renders -v
```

Expected: PASS (or update the test name to whatever exists; the goal is that the route returns 200).

- [ ] **Step 4: Commit.**

```bash
git add src/findajob/web/routes/board.py src/findajob/web/templates/board/waitlist.html
git commit -m "$(cat <<'EOF'
feat(web): wire Waitlist to filter framework + add Likelihood column (#273)

Waitlist now exposes the full scoring trio (Rel + Fit + Likelihood) by
default to match Dashboard's defaults, since Waitlist is exactly where
the original triage decision gets re-evaluated. Prob remains hidden
(redundant with Likelihood). SELECT updated to pull interview_likelihood.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Wire Rejected route + template

**Files:**
- Modify: `src/findajob/web/routes/board.py` — replace `rejected()` and `rejected_rows()`.
- Modify: `src/findajob/web/templates/board/rejected.html`.

- [ ] **Step 1: Replace the Rejected route block.**

In `board.py`, remove existing `_REJECTED_COLS`, `_REJECTED_SORTABLE`, `_REJECTED_DEFAULT_SORT`, `_REJECTED_SQL`, `rejected()`, `rejected_rows()`. Add:

```python
_REJECTED_DEFAULT_SORT = "rejected_date"
_REJECTED_BASE_WHERE = "j.stage IN ('rejected','not_selected')"


def _rejected_source() -> str:
    return (
        "FROM jobs j "
        "LEFT JOIN ("
        "  SELECT job_id, MAX(changed_at) AS rejected_date "
        "  FROM audit_log "
        "  WHERE field_changed = 'stage' AND new_value IN ('rejected','not_selected') "
        "  GROUP BY job_id"
        ") al ON al.job_id = j.id"
    )


def _rejected_query(parsed: ParsedFilters) -> tuple[str, list[object]]:
    specs = filter_registry.REJECTED_COLUMNS
    clauses, params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or _REJECTED_DEFAULT_SORT
    sort_spec = next((s for s in specs if s.name == sort), None)
    sort_ref = sort_spec.sql_ref if sort_spec else "al.rejected_date"
    order = "DESC" if parsed.desc else "ASC"
    sql = (
        "SELECT j.fingerprint, j.title, j.company, j.url, j.stage, j.reject_reason, "
        "       CASE j.stage WHEN 'not_selected' THEN 'company' ELSE 'user' END AS rejection_source, "
        "       al.rejected_date "
        f"{_rejected_source()} "
        f"WHERE ({_REJECTED_BASE_WHERE}){clauses} "
        f"ORDER BY {sort_ref} {order}"
    )
    return sql, params


@router.get("/board/rejected", response_class=HTMLResponse)
def rejected(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.REJECTED_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _rejected_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/rejected.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "density": _normalize_density(density),
            "tab": "rejected",
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/rejected/rows", response_class=HTMLResponse)
def rejected_rows(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.REJECTED_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _rejected_query(parsed)
    rows = db.execute(sql, params).fetchall()
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_job_rows_fragment.html",
        context={
            "specs": specs,
            "visible": visible,
            "rows": rows,
            "tab": "rejected",
            "materials_base_url": materials_base_url,
        },
    )
```

- [ ] **Step 2: Replace `rejected.html`.**

```html
{% extends "base.html" %}
{% block title %}Rejected — findajob{% endblock %}
{% block main_classes %}w-full px-4 py-6{% endblock %}
{% block content %}
<h1 class="text-2xl font-semibold mb-4">Rejected</h1>
{% include "board/_tabs.html" %}
{% include "_filters.html" %}
<table class="min-w-full bg-white shadow-sm rounded-sm density-{{ density | default('compact') }}">
  {% set leading_cols = [] %}
  {% include "_table_header.html" %}
  <tbody id="rows">
    {% include "_job_rows_fragment.html" %}
  </tbody>
</table>
{% endblock %}
```

(Note: Rejected has no Status/Reject leading cells in current dashboard.html — verify by reading the existing `rejected.html` first; if it does have leading cells, mirror them.)

- [ ] **Step 3: Smoke-test.**

```bash
uv run pytest tests/test_web_board_rejected.py -v
```

Expected: tab-renders-200 still passes.

- [ ] **Step 4: Commit.**

```bash
git add src/findajob/web/routes/board.py src/findajob/web/templates/board/rejected.html
git commit -m "$(cat <<'EOF'
feat(web): wire Rejected to filter framework (#273)

Per-tab _rejected_source() holds LEFT JOIN audit_log for rejected_date.
reject_reason is now a real ENUM filter; rejection_source filter slices
user-vs-company rejections.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: Wire Archive route + template (preserves pagination)

**Files:**
- Modify: `src/findajob/web/routes/board.py` — replace `archive()` and `archive_rows()`.
- Modify: `src/findajob/web/templates/board/archive.html`.

- [ ] **Step 1: Replace the Archive route block.**

In `board.py`, remove existing `_ARCHIVE_COLS`, `_ARCHIVE_SORTABLE`, `_ARCHIVE_DEFAULT_SORT`, `_ARCHIVE_PAGE_SIZE`, `_archive_score_where`, `_archive_select_sql`, `archive()`, `archive_rows()`. Add:

```python
_ARCHIVE_DEFAULT_SORT = "created_at"
_ARCHIVE_PAGE_SIZE = 100


def _archive_query(
    parsed: ParsedFilters, offset: int, page_size: int = _ARCHIVE_PAGE_SIZE
) -> tuple[str, list[object]]:
    specs = filter_registry.ARCHIVE_COLUMNS
    clauses, filter_params = build_filter_clauses(specs, parsed)
    sort = parsed.sort or _ARCHIVE_DEFAULT_SORT
    order = "DESC" if parsed.desc else "ASC"
    # Strip leading " AND " and prefix with " WHERE " — Archive has no base WHERE.
    where_sql = ""
    if clauses:
        where_sql = " WHERE " + clauses[len(" AND "):]
    sql = (
        "SELECT fingerprint, title, company, stage, relevance_score, fit_score, "
        "probability_score, location, remote_status, source, url, created_at, stage_updated "
        f"FROM jobs{where_sql} ORDER BY {sort} {order} LIMIT ? OFFSET ?"
    )
    params = [*filter_params, page_size, offset]
    return sql, params


@router.get("/board/archive", response_class=HTMLResponse)
def archive(
    request: Request,
    density: str = Query(default=_DEFAULT_DENSITY),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.ARCHIVE_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _archive_query(parsed, offset=0)
    rows = db.execute(sql, params).fetchall()
    has_more = len(rows) == _ARCHIVE_PAGE_SIZE
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/archive.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "density": _normalize_density(density),
            "tab": "archive",
            "next_offset": _ARCHIVE_PAGE_SIZE if has_more else None,
            "materials_base_url": materials_base_url,
        },
    )


@router.get("/board/archive/rows", response_class=HTMLResponse)
def archive_rows(
    request: Request,
    offset: int = Query(default=0),
    db: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> HTMLResponse:
    specs = filter_registry.ARCHIVE_COLUMNS
    parsed = parse_filter_params(specs, request.query_params)
    sql, params = _archive_query(parsed, offset=offset)
    rows = db.execute(sql, params).fetchall()
    has_more = len(rows) == _ARCHIVE_PAGE_SIZE
    materials_base_url = os.environ.get("FINDAJOB_MATERIALS_BASE_URL", "")
    visible = _resolve_visible(specs, parsed)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="board/_archive_rows.html",
        context={
            "specs": specs,
            "visible": visible,
            "parsed": parsed,
            "rows": rows,
            "tab": "archive",
            "next_offset": offset + _ARCHIVE_PAGE_SIZE if has_more else None,
            "materials_base_url": materials_base_url,
        },
    )
```

- [ ] **Step 2: Update `_archive_rows.html` to pass `specs` + `visible` to `_job_row.html`.**

Read the existing `_archive_rows.html` first (it has pagination cursor markup). Adjust the row loop to use `specs`/`visible` semantics — replace any `{% for ... in columns %}` references the same way Task 9 did for `_job_row.html` and `_job_rows_fragment.html`.

If the partial just renders `_job_row.html` per row, only the loader needs to pass through `specs` + `visible` from the route context, which is already in scope.

- [ ] **Step 3: Replace `archive.html`.**

```html
{% extends "base.html" %}
{% block title %}Archive — findajob{% endblock %}
{% block main_classes %}w-full px-4 py-6{% endblock %}
{% block content %}
<h1 class="text-2xl font-semibold mb-4">Archive</h1>
{% include "board/_tabs.html" %}
{% include "_filters.html" %}
<table class="min-w-full bg-white shadow-sm rounded-sm density-{{ density | default('compact') }}">
  {% set leading_cols = [('Promote', '')] %}
  {% include "_table_header.html" %}
  <tbody id="rows">
    {% include "board/_archive_rows.html" %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 4: Smoke-test.**

```bash
uv run pytest tests/test_web_board_tabs.py -v -k archive
```

Expected: tab-renders-200 passes.

- [ ] **Step 5: Commit.**

```bash
git add src/findajob/web/routes/board.py src/findajob/web/templates/board/archive.html src/findajob/web/templates/board/_archive_rows.html
git commit -m "$(cat <<'EOF'
feat(web): wire Archive to filter framework, preserve pagination (#273)

Archive now exposes Stage + Source as ENUM filters and a Date range
filter, replacing the bespoke _archive_score_where helper. Pagination
unchanged (LIMIT/OFFSET still inline). The framework's clauses fragment
is reshaped from " AND ..." to " WHERE ..." since Archive has no
base WHERE.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Remove subsumed helpers + verify no callers

**Files:**
- Modify: `src/findajob/web/routes/board.py` — delete `_filter_clause`, any `_archive_score_where`/`_archive_select_sql` leftovers.

- [ ] **Step 1: Delete leftover helpers.**

In `board.py`, find and delete:
- `def _filter_clause(...)` and any references.
- Any leftover `_archive_score_where` / `_archive_select_sql` / `_DASHBOARD_COLS` / `_APPLIED_COLS` / `_REVIEW_COLS` / `_WAITLIST_COLS` / `_REJECTED_COLS` / `_ARCHIVE_COLS` / their `_SORTABLE` siblings.

- [ ] **Step 2: Verify nothing else imports the deleted symbols.**

Run:
```bash
grep -nE "_filter_clause|_archive_score_where|_archive_select_sql|_DASHBOARD_COLS|_APPLIED_COLS|_REVIEW_COLS|_WAITLIST_COLS|_REJECTED_COLS|_ARCHIVE_COLS" -r src/ tests/
```
Expected: zero matches.

- [ ] **Step 3: Run the full board test suite.**

```bash
uv run pytest tests/test_web_board_tabs.py tests/test_web_board_applied.py tests/test_web_board_review.py tests/test_web_board_rejected.py tests/test_web_board_sort.py tests/test_web_board_formatting.py -v
```

Expected: failures only on tests that depend on the old `?q=` URL contract (handled in Task 17). Tab-renders-200 tests pass.

- [ ] **Step 4: Commit.**

```bash
git add src/findajob/web/routes/board.py
git commit -m "$(cat <<'EOF'
refactor(web): remove subsumed filter helpers (#273)

Drops _filter_clause, _archive_score_where, _archive_select_sql, and the
six per-tab _COLS/_SORTABLE constant blocks. Verified zero references
remain in src/ or tests/.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: Integration tests — `test_web_board_filters.py`

**Files:**
- Create: `tests/test_web_board_filters.py`

- [ ] **Step 1: Write the integration tests.**

```python
# tests/test_web_board_filters.py
"""Per-tab integration tests for the new filter framework.

Each test seeds a small set of jobs (with jobs.id set per
feedback_test_fixtures_jobs_id), hits a /board/{tab}/rows endpoint
with various URL params, and asserts the right rows are returned.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from findajob.web.app import create_app


@pytest.fixture
def app_with_db(tmp_path, monkeypatch) -> Iterator[TestClient]:
    db_path = tmp_path / "pipeline.db"
    monkeypatch.setenv("JSP_DB_PATH", str(db_path))
    # Init schema — seed minimum table from existing migrations or hand-wire.
    conn = sqlite3.connect(db_path)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS jobs (
      id TEXT PRIMARY KEY,
      fingerprint TEXT UNIQUE NOT NULL,
      title TEXT, company TEXT, location TEXT, remote_status TEXT,
      known_contacts TEXT, comp_estimate TEXT, ai_notes TEXT,
      relevance_score INTEGER, fit_score REAL, probability_score REAL,
      interview_likelihood REAL,
      stage TEXT, created_at TEXT, stage_updated TEXT,
      url TEXT, prep_folder_path TEXT, source TEXT,
      score_flag_reason TEXT, reject_reason TEXT, user_notes TEXT
    );
    CREATE TABLE IF NOT EXISTS audit_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      job_id TEXT, field_changed TEXT, old_value TEXT, new_value TEXT,
      changed_at TEXT, changed_by TEXT
    );
    """)
    conn.commit()
    conn.close()

    app = create_app()
    yield TestClient(app)


def _insert_job(
    db_path: str, *, id: str, fingerprint: str, stage: str = "scored",
    relevance_score: int = 7, title: str = "Engineer", company: str = "Acme",
    location: str = "SF", source: str = "manual",
    created_at: str | None = None, **kw,
) -> None:
    conn = sqlite3.connect(db_path)
    cols = {
        "id": id, "fingerprint": fingerprint, "title": title, "company": company,
        "location": location, "stage": stage, "relevance_score": relevance_score,
        "source": source, "remote_status": "Remote",
        "created_at": created_at or datetime.utcnow().isoformat(),
        **kw,
    }
    placeholders = ", ".join("?" * len(cols))
    conn.execute(
        f"INSERT INTO jobs ({', '.join(cols.keys())}) VALUES ({placeholders})",
        tuple(cols.values()),
    )
    conn.commit()
    conn.close()


def _audit_log(db_path: str, *, job_id: str, new_value: str, changed_at: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO audit_log (job_id, field_changed, new_value, changed_at, changed_by) "
        "VALUES (?, 'stage', ?, ?, 'test')",
        (job_id, new_value, changed_at),
    )
    conn.commit()
    conn.close()


# ─── Dashboard ────────────────────────────────────────────────────────────────

def test_dashboard_default_landing_shows_score_7_plus_only(
    app_with_db, tmp_path
) -> None:
    db_path = str(tmp_path / "pipeline.db")
    _insert_job(db_path, id="a1", fingerprint="fp-a1", relevance_score=8, title="High")
    _insert_job(db_path, id="a2", fingerprint="fp-a2", relevance_score=6, title="Mid")
    _insert_job(db_path, id="a3", fingerprint="fp-a3", relevance_score=5, title="Low")

    r = app_with_db.get("/board/dashboard")
    assert r.status_code == 200
    assert "fp-a1" in r.text
    assert "fp-a2" not in r.text
    assert "fp-a3" not in r.text


def test_dashboard_score_min_5_surfaces_buried_gems(app_with_db, tmp_path) -> None:
    db_path = str(tmp_path / "pipeline.db")
    _insert_job(db_path, id="b1", fingerprint="fp-b1", relevance_score=6, title="Six")
    _insert_job(db_path, id="b2", fingerprint="fp-b2", relevance_score=5, title="Five")
    _insert_job(db_path, id="b3", fingerprint="fp-b3", relevance_score=4, title="Four")

    r = app_with_db.get("/board/dashboard/rows?relevance_score_min=5&stage=scored")
    assert r.status_code == 200
    assert "fp-b1" in r.text
    assert "fp-b2" in r.text
    assert "fp-b3" not in r.text


def test_dashboard_text_filter_on_title(app_with_db, tmp_path) -> None:
    db_path = str(tmp_path / "pipeline.db")
    _insert_job(db_path, id="c1", fingerprint="fp-c1", relevance_score=8, title="Director of NPI")
    _insert_job(db_path, id="c2", fingerprint="fp-c2", relevance_score=8, title="VP Engineering")

    r = app_with_db.get("/board/dashboard/rows?title=director")
    assert r.status_code == 200
    assert "fp-c1" in r.text
    assert "fp-c2" not in r.text


def test_dashboard_sort_changes_preserve_filter(app_with_db, tmp_path) -> None:
    db_path = str(tmp_path / "pipeline.db")
    _insert_job(db_path, id="d1", fingerprint="fp-d1", relevance_score=8, title="A")
    _insert_job(db_path, id="d2", fingerprint="fp-d2", relevance_score=6, title="A2")

    r = app_with_db.get("/board/dashboard/rows?relevance_score_min=5&sort=created_at&desc=0")
    assert r.status_code == 200
    assert "fp-d1" in r.text
    assert "fp-d2" in r.text


def test_dashboard_cols_replaces_default_set(app_with_db, tmp_path) -> None:
    db_path = str(tmp_path / "pipeline.db")
    _insert_job(db_path, id="e1", fingerprint="fp-e1", relevance_score=8,
                title="Director", ai_notes="Long detailed notes here")

    # Default landing renders ai_notes column.
    r1 = app_with_db.get("/board/dashboard")
    assert "Long detailed notes here" in r1.text

    # ?cols=title only: ai_notes column is dropped.
    r2 = app_with_db.get("/board/dashboard?cols=title")
    assert r2.status_code == 200
    assert "Director" in r2.text
    assert "Long detailed notes here" not in r2.text


# ─── Applied ──────────────────────────────────────────────────────────────────

def test_applied_filter_by_days_since_applied(app_with_db, tmp_path) -> None:
    db_path = str(tmp_path / "pipeline.db")
    old = (datetime.utcnow() - timedelta(days=21)).isoformat(sep=" ", timespec="seconds")
    fresh = (datetime.utcnow() - timedelta(days=2)).isoformat(sep=" ", timespec="seconds")
    _insert_job(db_path, id="ap1", fingerprint="fp-ap1", stage="applied", title="Old App")
    _audit_log(db_path, job_id="ap1", new_value="applied", changed_at=old)
    _insert_job(db_path, id="ap2", fingerprint="fp-ap2", stage="applied", title="Fresh App")
    _audit_log(db_path, job_id="ap2", new_value="applied", changed_at=fresh)

    r = app_with_db.get("/board/applied/rows?days_since_applied_min=14")
    assert r.status_code == 200
    assert "fp-ap1" in r.text
    assert "fp-ap2" not in r.text


# ─── Rejected ─────────────────────────────────────────────────────────────────

def test_rejected_filter_by_reason(app_with_db, tmp_path) -> None:
    db_path = str(tmp_path / "pipeline.db")
    now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    _insert_job(db_path, id="rj1", fingerprint="fp-rj1", stage="rejected",
                title="Bad Comp", reject_reason="Compensation")
    _audit_log(db_path, job_id="rj1", new_value="rejected", changed_at=now)
    _insert_job(db_path, id="rj2", fingerprint="fp-rj2", stage="rejected",
                title="Bad Loc", reject_reason="Location")
    _audit_log(db_path, job_id="rj2", new_value="rejected", changed_at=now)

    r = app_with_db.get("/board/rejected/rows?reject_reason=Compensation")
    assert r.status_code == 200
    assert "fp-rj1" in r.text
    assert "fp-rj2" not in r.text


# ─── Archive ──────────────────────────────────────────────────────────────────

def test_archive_filter_by_source(app_with_db, tmp_path) -> None:
    db_path = str(tmp_path / "pipeline.db")
    _insert_job(db_path, id="ar1", fingerprint="fp-ar1", source="greenhouse_json")
    _insert_job(db_path, id="ar2", fingerprint="fp-ar2", source="jobsapi_indeed")

    r = app_with_db.get("/board/archive/rows?source=greenhouse_json")
    assert r.status_code == 200
    assert "fp-ar1" in r.text
    assert "fp-ar2" not in r.text


# ─── Cross-cutting ────────────────────────────────────────────────────────────

def test_invalid_param_silently_dropped(app_with_db) -> None:
    r = app_with_db.get("/board/dashboard/rows?bogus=value&title__bad=x")
    assert r.status_code == 200


def test_filter_input_attrs_render_for_htmx_include(app_with_db, tmp_path) -> None:
    """The header partial must emit data-filter-input on every filter input
    so HTMX hx-include picks them up. Smoke check on the dashboard."""
    db_path = str(tmp_path / "pipeline.db")
    _insert_job(db_path, id="z1", fingerprint="fp-z1", relevance_score=8)

    r = app_with_db.get("/board/dashboard")
    assert r.status_code == 200
    assert 'data-filter-input' in r.text
    assert 'hx-push-url="true"' in r.text
```

- [ ] **Step 2: Run the integration tests.**

Run: `uv run pytest tests/test_web_board_filters.py -v`
Expected: 11 tests pass.

- [ ] **Step 3: Commit.**

```bash
git add tests/test_web_board_filters.py
git commit -m "$(cat <<'EOF'
test(web): integration tests for filter framework on every tab (#273)

Per-tab tests cover: Dashboard default landing (7+ only); Dashboard
score_min=5 surfaces buried gems; text filter; sort+filter compose;
?cols= replaces default set; Applied days_since_applied range;
Rejected reject_reason; Archive source. Cross-cutting: invalid params
drop silently; data-filter-input + hx-push-url attrs render. All
fixtures include jobs.id so audit_log JOINs work.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 17: Migrate existing test_web_board_*.py for the new URL contract

**Files:**
- Modify: `tests/test_web_board_applied.py`, `tests/test_web_board_review.py`, `tests/test_web_board_rejected.py`, `tests/test_web_board_tabs.py`, `tests/test_web_board_sort.py`, `tests/test_web_board_formatting.py`.

- [ ] **Step 1: Run the suite and triage failures.**

```bash
uv run pytest tests/test_web_board_*.py -v 2>&1 | tee /tmp/board-tests.log
```

- [ ] **Step 2: For each failing test, identify the breakage.**

Common breakages:
- Tests that use `?q=foo` to filter: replace with `?title=foo` or `?company=foo` per the new contract.
- Tests that assert response context contains `columns`: replace with `specs` and `visible`.
- Tests that assert `_DASHBOARD_COLS` symbol: import `findajob.web.filters.registry.DASHBOARD_COLUMNS` instead.
- Tests that hit `/board/applied?sort=foo&desc=0`: still work (sort/desc unchanged).

- [ ] **Step 3: Apply the migrations file by file.**

Walk each test file and update the assertions/fixtures. The safe policy: change as little as possible. If a test was checking "filter by q returns matching rows", rewrite it as "filter by title returns matching rows" with the new URL.

- [ ] **Step 4: Run the suite again.**

```bash
uv run pytest tests/test_web_board_*.py -v
```

Expected: ALL PASS.

- [ ] **Step 5: Commit.**

```bash
git add tests/
git commit -m "$(cat <<'EOF'
test(web): migrate existing board tests to new URL contract (#273)

?q= -> ?title= / ?company= per the new framework's per-column TEXT
filters. Context expectations updated from `columns` to `specs` +
`visible`. _DASHBOARD_COLS / _APPLIED_COLS / etc. references switched
to the new registry module. No behavioral test changes; URL contract
adjustments only.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 18: Lint + format + type-check pass

**Files:** none (verification only)

- [ ] **Step 1: Run ruff check.**

```bash
uv run ruff check src/ tests/
```

Expected: zero errors. Fix anything reported.

- [ ] **Step 2: Run ruff format check.**

```bash
uv run ruff format --check src/ tests/
```

Expected: zero changes needed. If format issues are reported, run `uv run ruff format src/ tests/` and inspect the diff before committing.

- [ ] **Step 3: Run mypy.**

```bash
uv run mypy src/findajob/web/filters/ src/findajob/web/routes/board.py
```

Expected: zero errors. Fix any type issues.

- [ ] **Step 4: Run the full pytest suite.**

```bash
uv run pytest -q
```

Expected: all tests pass (or only pre-existing failures unrelated to this work — verify each).

- [ ] **Step 5: If any formatting was applied, commit.**

```bash
git add src/ tests/
git commit -m "$(cat <<'EOF'
style(filters): ruff format pass on new framework code (#273)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

(If no formatting changes were needed, skip the commit.)

---

### Task 19: Documentation Impact updates

**Files:**
- Modify: `CLAUDE.md` (Web Frontend Architecture section).
- Modify: `CHANGELOG.md` (`[Unreleased]` entry).
- Modify: `docs/usage.md` (board tab sections).
- Spec doc: amend with any decisions made during implementation.

- [ ] **Step 1: Update `CLAUDE.md` Web Frontend Architecture section.**

Locate the "Web Frontend Architecture" subsection. Append:

```markdown
### Per-column filter framework

All board tabs use a single declarative filter framework under
`findajob.web.filters`. Each tab declares a `tuple[ColumnSpec, ...]`
in `findajob.web.filters.registry`; the framework parses URL params
into `ParsedFilters`, builds parameterized SQL clauses via
`build_filter_clauses()`, and renders header inputs + popovers via
shared partials (`_table_header.html`, `_active_filters.html`).

URL contract is flat with type-suffixed param names:
- TEXT: `?col=substring` (case-insensitive contains)
- SCORE / INTEGER: `?col_min=&col_max=`
- ENUM: `?col=a,b,c` (multi-select via comma)
- DATE: `?col_from=&col_to=`
- Sort: `?sort=col&desc=1` (existing convention preserved)
- Visibility: `?cols=a,b,c` (explicit replacement set; default per spec)

State cascade: URL querystring > ColumnSpec.default_visible. Persisted
per-tab prefs are #277 — the cascade has the layer hook.

When adding a new board tab, declare its ColumnSpec list in
`registry.py`, add a base WHERE constant + `_<tab>_query()` builder
in `routes/board.py`, and create the tab template that includes
`_filters.html` + `_table_header.html`. No per-tab filter code needed.
```

- [ ] **Step 2: Add `[Unreleased]` CHANGELOG entry.**

Locate `## [Unreleased]` in `CHANGELOG.md`. Add (immediately under it):

```markdown
### Added

- **Per-column filter+sort framework on every board tab (#273).** Replaces
  the single `?q=` text input with type-aware filters: TEXT (substring),
  SCORE / INTEGER (min/max range), ENUM (multi-select via comma-separated
  values), DATE (from/to range). Sort changes preserve filter state and
  vice versa. All state lives in URL query params (`hx-push-url`), so any
  view is bookmarkable + shareable. A 🔗 Copy-link button on every tab
  writes the current URL to the clipboard. Column visibility supports
  explicit override via `?cols=a,b,c`. The framework lives in
  `findajob.web.filters` as a declarative `ColumnSpec` registry; new
  board tabs declare their column specs and the filter UI + SQL composer
  apply automatically. Per-tab default-visible columns retuned: Dashboard
  surfaces AI notes + Likelihood by default and hides Probability + Stage
  (filterable via `?stage=...` for score-5/6 triage); Waitlist gains
  Likelihood for parity with Dashboard's scoring trio.
- **Surfaceable score-5/6 jobs on the Dashboard.** Visit
  `/board/dashboard?relevance_score_min=5&stage=scored,manual_review` to
  see jobs the prior 7+ default hid (operator stack on 2026-04-25 had 208
  buried in the score-5/6 band). The 7+ happy path is unchanged on cold
  load. Followed by #277 (Columns dropdown UI + per-tab pref persistence)
  and #276 (scorer-side noise reduction at score 5/6).

### Removed

- **`?q=` text-search URL param on board tabs.** Superseded by the
  per-column TEXT filters under `?title=...&company=...`. Bookmarks using
  the old `?q=foo` will silently drop the filter — the bookmark scheme
  was internal to one feature.
```

- [ ] **Step 3: Update `docs/usage.md`.**

For each section that describes a board tab (search for `## The Dashboard`, `## The Review tab`, etc.), add a paragraph describing:
- The new per-column filter row under each header
- TEXT inputs, score range inputs, popover triggers (▾) for enums and dates
- The active-filter chip strip with ✕ to remove individual filters and "Clear all"
- The 🔗 Copy link button
- Example URL: `?relevance_score_min=5&stage=scored,manual_review` for the score-5/6 use case on Dashboard

Keep the existing prose intact; only add new paragraphs that describe the filter UI.

- [ ] **Step 4: (Optional) amend the spec doc with implementation decisions.**

If implementation revealed any spec gaps (e.g., the `_resolve_visible()` helper, the `_<tab>_source()` per-tab JOIN-builders, or the choice to hand-pass `leading_cols` via Jinja `set` rather than via the route context), append a "Decisions made during implementation" subsection to `docs/superpowers/specs/2026-04-25-board-filter-framework-design.md`.

- [ ] **Step 5: Commit.**

```bash
git add CLAUDE.md CHANGELOG.md docs/usage.md docs/superpowers/specs/2026-04-25-board-filter-framework-design.md
git commit -m "$(cat <<'EOF'
docs: filter framework — CLAUDE.md, CHANGELOG.md, usage.md (#273)

CLAUDE.md gets a "Per-column filter framework" subsection under Web
Frontend Architecture covering URL contract, cascade, and the
"declare a spec list to plug in a new tab" extension story.
CHANGELOG.md [Unreleased] gets the user-facing Added/Removed entries.
docs/usage.md per-tab sections describe the new filter row, chip
strip, and Copy link button.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 20: Whole-feature verification gate on docker.lan

**Files:** none (verification only)

This is the spec-mandated whole-feature gate. The branch is not opened as a PR until every check below passes.

- [ ] **Step 1: Push the branch (no PR yet).**

```bash
git push -u origin feat/273-dashboard-filter-sort
```

- [ ] **Step 2: Build a local image and load it on docker.lan.**

```bash
# On the laptop:
docker build -t findajob-273:test .
docker save findajob-273:test | ssh docker.lan 'sudo -u lad docker load'
```

- [ ] **Step 3: Stand up a scratch test stack on docker.lan using the new image.**

```bash
# OPERATOR_STACK_DIR is the operator's primary stack path (see CLAUDE.local.md).
# Example: /opt/stacks/findajob-<operator-tag>. The state copy gives the test
# stack realistic data without writing into production.

ssh docker.lan 'sudo -u lad bash -c "
  cd /tmp && rm -rf findajob-273-test && mkdir findajob-273-test && cd findajob-273-test &&
  cp -r ${OPERATOR_STACK_DIR}/state ./state &&
  cat > compose.yaml <<EOF
services:
  scheduler:
    image: findajob-273:test
    environment:
      - JSP_BASE=/app
      - FINDAJOB_MATERIALS_BASE_URL=http://docker.lan:8092
    ports:
      - 8092:8090
    volumes:
      - ./state/data:/app/data
      - ./state/config:/app/config
      - ./state/aichat_ng:/app/.config/aichat_ng
      - ./state/candidate_context:/app/candidate_context
      - ./state/companies:/app/companies
EOF
  docker compose up -d
"'
```

- [ ] **Step 4: Smoke-check each tab.**

```bash
for tab in dashboard applied review waitlist rejected archive; do
  status=$(curl -sI "http://docker.lan:8092/board/$tab" | head -1)
  echo "$tab: $status"
done
```

Expected: all `200 OK`.

- [ ] **Step 5: Smoke-check the score-5/6 surfacing use case (the original load-bearing requirement).**

```bash
curl -s "http://docker.lan:8092/board/dashboard?relevance_score_min=5&stage=scored" | grep -c "<tr"
```

Expected: a row count > 50 (the operator stack copy has 200+ score-5/6 in `scored`).

- [ ] **Step 6: Smoke-check filter UI rendering.**

```bash
curl -s "http://docker.lan:8092/board/dashboard" | grep -E "data-filter-input|hx-push-url|Copy link" | head -5
```

Expected: filter inputs and Copy-link affordances render.

- [ ] **Step 7: Manual click-test in a browser.**

Open `http://docker.lan:8092/board/dashboard` in a browser. Verify:
- The header has a filter row with text/range inputs and ▾ popover triggers.
- Typing in the title filter live-updates rows (debounced ~200ms).
- The URL updates as you type.
- Clicking ▾ on Stage opens a popover with checkboxes; Apply commits and closes.
- The active-filter chip strip appears below the header with the active filter; ✕ drops it; "Clear all" resets.
- The 🔗 Copy-link button writes the current URL to the clipboard ("Copied!" flashes).
- Reload preserves all filter state.
- Visit `/board/dashboard?cols=title,company,relevance_score,created_at`; only those columns render.

- [ ] **Step 8: Tear down the test stack.**

```bash
ssh docker.lan 'sudo -u lad bash -c "cd /tmp/findajob-273-test && docker compose down -v && cd /tmp && rm -rf findajob-273-test"'
```

- [ ] **Step 9: No commit — verification step.**

If any check fails, do not proceed to PR. Diagnose, fix, commit, re-run from Step 2.

---

### Task 21: Open the PR

**Files:** none (PR open)

- [ ] **Step 1: Confirm branch is pushed and clean.**

```bash
git status --short
git fetch origin && git log --oneline origin/main..HEAD
```

Expected: clean working tree; commit log shows the 19 task commits (some commits may be merged via Step 5 in tasks).

- [ ] **Step 2: Open PR.**

```bash
gh pr create --title "feat(web): per-column filter+sort framework across all board tabs (#273)" --body "$(cat <<'EOF'
## Summary

- Generic per-column filter+sort framework that applies to every board tab — dashboard, applied, review, waitlist, rejected, archive — replacing the bespoke per-tab WHERE clauses and single `?q=` text input.
- Type-aware filters: TEXT contains, SCORE/INTEGER range, ENUM multi-select, DATE range. URL-state-driven (hx-push-url) so every view is bookmarkable. Per-tab 🔗 Copy-link button.
- Visibility cascade: `?cols=` URL override > ColumnSpec.default_visible. Persistence layer for #277 plugs in via the cascade.
- Default visibility re-tuned per funnel-flow review: Dashboard surfaces AI notes + Likelihood by default, hides Prob; Waitlist gains Likelihood for parity with Dashboard's scoring trio. Stage added to Dashboard spec as opt-in for score-5/6 triage.

Spec: `docs/superpowers/specs/2026-04-25-board-filter-framework-design.md`
Plan: `docs/superpowers/plans/2026-04-25-board-filter-framework.md`
Follow-ups: #277 (Columns ▾ dropdown UI + per-tab persisted prefs), #276 (scorer-side IC-vs-manager noise reduction).

## Test plan

- [x] `uv run pytest -q` — full suite passes
- [x] `uv run ruff check && uv run ruff format --check` — clean
- [x] `uv run mypy src/findajob/web/filters/` — clean
- [x] All 6 board tabs return 200 on docker.lan test stack
- [x] `/board/dashboard?relevance_score_min=5&stage=scored,manual_review` surfaces score-5/6 buried gems (the original load-bearing requirement)
- [x] Manual click-test: text filter live-updates with debounce, popover Apply/Clear/Cancel, chip strip ✕ + Clear all, Copy-link clipboard write, reload preserves URL state, `?cols=` overrides default visibility

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Print PR URL** so the operator can review and merge.

---

## Documentation Impact

| Surface | Change |
|---|---|
| `CLAUDE.md` § Web Frontend Architecture | Add "Per-column filter framework" subsection covering ColumnSpec registry, URL contract, cascade, and the "new tab = declare a spec list" extension pattern. (Task 19 Step 1.) |
| `CHANGELOG.md` `[Unreleased]` | Add Added bullet for the framework + Copy-link, and Removed bullet for `?q=`. (Task 19 Step 2.) |
| `docs/usage.md` | Per-tab sections (Dashboard / Review / Applied / Waitlist / Archive / Rejected) gain a paragraph describing the new filter UI, popovers, chip strip, Copy-link. Example URL for the score-5/6 use case on Dashboard. (Task 19 Step 3.) |
| `README.md` | None — high-level only; no install path or tech-stack changes. |
| `docs/setup/*.md` | None — no install / configure / state-migration changes. |
| `docs/superpowers/specs/2026-04-25-board-filter-framework-design.md` | Optional "Decisions made during implementation" subsection if any spec gaps surface during execution (e.g., `leading_cols` Jinja-set pattern, `_resolve_visible()` helper location). (Task 19 Step 4.) |
| In-code docstrings | Module-level docstrings on `spec.py`, `url.py`, `query.py`, `registry.py` (written as part of Tasks 1–4); class docstring on `ColumnSpec`; one-line summaries on each public function. |
| `migration-required` label | **No.** Refactor of pure server-rendered code; no DB schema, no config, no compose, no crontab changes. Container rebuild + restart picks it up. (Per spec § "Migration path".) |

---

## Verification gate (whole-feature)

The PR opens only after **every** check below passes:

1. `uv run pytest -q` — full suite green.
2. `uv run ruff check src/ tests/` — clean.
3. `uv run ruff format --check src/ tests/` — clean.
4. `uv run mypy src/findajob/web/filters/ src/findajob/web/routes/board.py` — clean.
5. Local image built; loaded on docker.lan; scratch stack stood up against a copy of operator state.
6. All 6 tabs return `200 OK` on cold load.
7. `/board/dashboard?relevance_score_min=5&stage=scored` returns >50 rows on the operator-state copy (the original load-bearing requirement).
8. Manual click-test in browser: filter inputs live-update via HTMX; URL syncs via `hx-push-url`; popover Apply/Clear/Cancel work; chip strip ✕ + "Clear all" work; Copy-link writes clipboard with "Copied!" flash; reload preserves state; `?cols=...` overrides default visibility.
9. Test stack torn down; no leftover state on docker.lan.

If any check fails, do not open the PR. Diagnose, fix, push, re-run from Step 5.

---

## Self-review checklist

| Spec section | Tasks |
|---|---|
| Goals 1 (generic framework) | 1, 2, 3, 4, 9–14 |
| Goals 2 (per-column affordances) | 6, 8 |
| Goals 3 (sort independent of filter) | 2 (sort/desc parsing), 6 (sort link preserves filters), 9–14 (per-tab integration) |
| Goals 4 (URL state) | 2, 6 (`hx-push-url`), 7 (chip strip with `filter_remove_qs`), 8 (popover commits) |
| Goals 5 (server-rendered + HTMX) | 6, 7, 8 |
| Goals 6 (generalization-safe) | 4 (no operator-specific lists), 19 (CLAUDE.md doc) |
| Goals 7 (future-proof for #277) | 2 (`cols` parsing), 9 (`_resolve_visible` cascade), 19 (CLAUDE.md cascade doc) |
| ColumnSpec dataclass | 1 |
| Kind enum + URL/SQL semantics | 1 (`Kind`), 2 (URL), 3 (SQL) |
| ENUM comma assertion | 1 (`__post_init__`) |
| URL contract (suffixes, cols, sort) | 2 |
| Layout B (header row + popovers) | 6 (template), 8 (JS) |
| Per-tab visibility defaults table | 4 (registry), 19 (usage doc) |
| Default landings | 9 (Dashboard `_DASHBOARD_BASE_WHERE`), 10–14 (per-tab `_BASE_WHERE`) |
| Backend query builder | 3 |
| Cascade for state resolution | 9 (`_resolve_visible`), 19 (CLAUDE.md doc) |
| Performance | 16 (no perf regression in tests); doc-only — no work |
| Subsumed code | 15 |
| Testing strategy | 1 (spec), 2 (url), 3 (query), 16 (integration), 17 (migration) |
| Migration path | 19 (CHANGELOG.md notes the silent ?q= drop), 20 (no migration-required) |
| Decisions made during brainstorming | 19 Step 4 (optional amend) |

**Placeholder scan:** No `TBD` / `TODO` placeholders. Every step has the actual code or command. Type names (`ColumnSpec`, `Kind`, `ParsedFilters`, `build_filter_clauses`, `parse_filter_params`, `validate_specs`, `_resolve_visible`) are consistent across all task code blocks.
