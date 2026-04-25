"""Compose a parameterized WHERE-clause fragment from ParsedFilters.

Returns the fragment as `" AND <c1> AND <c2> ..."` (leading space + AND so
callers can drop it after their existing base_where without conditional logic)
and a list of bind params in the same order as the placeholders.

This module deliberately knows nothing about FROM / JOIN / SELECT / ORDER BY —
each route handler composes those itself, so the framework doesn't have to
model JOIN structure for tabs like Applied/Rejected that LEFT JOIN audit_log.
"""

from __future__ import annotations

from collections.abc import Sequence

from findajob.web.filters.spec import ColumnSpec, Kind
from findajob.web.filters.url import ParsedFilters


def build_filter_clauses(specs: Sequence[ColumnSpec], parsed: ParsedFilters) -> tuple[str, list[object]]:
    fragments: list[str] = []
    params: list[object] = []

    # Walk specs in declaration order so output is deterministic.
    for spec in specs:
        ref = spec.sql_ref

        if spec.kind is Kind.TEXT and spec.name in parsed.text:
            fragments.append(f"LOWER({ref}) LIKE LOWER(?)")
            params.append(f"%{parsed.text[spec.name]}%")

        elif spec.kind in (Kind.SCORE, Kind.INTEGER) and spec.name in parsed.numeric_range:
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
