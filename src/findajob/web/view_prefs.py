"""Per-tab persistence of filter / sort / cols state.

Keyed by tab in the view_prefs SQLite table (migration 0005). The
persisted query string is reconstructed from ParsedFilters after parsing
— never copied verbatim from request.url.query — so unrelated URL
params (e.g. ?density=, ?dismiss_active_sources_banner=) cannot leak
into persistence.

Cascade ownership: this module is the "persisted per-tab pref" tier
between URL params (top priority) and ColumnSpec.default_visible
(bottom). Resolution lives in route handlers via redirect-on-cold-load
— see findajob.web.routes.board.

Issue: #277
"""

from __future__ import annotations

import sqlite3
from urllib.parse import parse_qsl, urlencode

from findajob.web.filters.registry import STORAGE_TAB_COLUMNS
from findajob.web.filters.url import ParsedFilters

ALLOWED_TABS: frozenset[str] = frozenset(
    {"dashboard", "applied", "review", "waitlist", "fallback", "rejected", "not_selected", "archive"}
)


def serialize(parsed: ParsedFilters, *, default_cols: tuple[str, ...] | None = None) -> str:
    """Rebuild a canonical, allowlisted query string from ParsedFilters.

    Only emits keys produced by the per-column filter framework — sort,
    desc (only when False, since True is the default), text, numeric
    range (_min/_max), enum, date range (_from/_to), cols. Anything
    outside this set is filtered by construction.

    When ``default_cols`` is provided and ``parsed.cols`` equals it
    (same names in the same order), the ``cols=`` clause is dropped.
    Order counts since #1220: the ``cols`` order is the display order, so
    a reordered default set is a real preference and persists.
    Persisting "cols=<defaults>" would cause the redirect-on-cold-load
    cascade to bring back a no-op clause and render the chip-strip's
    cols pill on what the operator perceives as a default view (#844).

    Ordering is deterministic so byte-equal input yields byte-equal
    output.
    """
    pairs: list[tuple[str, str]] = []
    if parsed.sort:
        pairs.append(("sort", parsed.sort))
        if not parsed.desc:
            pairs.append(("desc", "0"))
    for name, v in parsed.text.items():
        pairs.append((name, v))
    for name, (lo, hi) in parsed.numeric_range.items():
        if lo is not None:
            pairs.append((f"{name}_min", str(lo)))
        if hi is not None:
            pairs.append((f"{name}_max", str(hi)))
    for name, picks in parsed.enum.items():
        pairs.append((name, ",".join(picks)))
    for name, (d_from, d_to) in parsed.date_range.items():
        if d_from is not None:
            pairs.append((f"{name}_from", d_from))
        if d_to is not None:
            pairs.append((f"{name}_to", d_to))
    if parsed.cols and (default_cols is None or tuple(parsed.cols) != tuple(default_cols)):
        pairs.append(("cols", ",".join(parsed.cols)))
    return urlencode(pairs)


def has_filter_state(parsed: ParsedFilters) -> bool:
    """True iff parsed carries any allowlisted state.

    Route handlers use this to detect cold-load — a page GET whose
    URL has no allowlisted params should hydrate from persistence via
    303 redirect regardless of unrelated params (?density=,
    ?dismiss_*_banner=).
    """
    return bool(parsed.text or parsed.numeric_range or parsed.enum or parsed.date_range or parsed.cols or parsed.sort)


def _ensure_tab(tab: str) -> None:
    if tab not in ALLOWED_TABS:
        raise ValueError(f"unknown tab: {tab!r}")


def load(conn: sqlite3.Connection, tab: str) -> str | None:
    """The tab's saved query string, or None.

    A row saved before #1220 (``cols_ordered=0``, migration 0013) holds its
    ``cols`` in the old registry order by accident. Its ``cols`` are put in
    the current registry order once, here, and the row is marked.
    """
    _ensure_tab(tab)
    row = conn.execute("SELECT query_string, cols_ordered FROM view_prefs WHERE tab=?", (tab,)).fetchone()
    if row is None:
        return None
    query_string, cols_ordered = row[0], row[1]
    if not cols_ordered:
        query_string = _registry_ordered_cols(tab, query_string)
        conn.execute(
            "UPDATE view_prefs SET query_string=?, cols_ordered=1 WHERE tab=?",
            (query_string, tab),
        )
        conn.commit()
    return query_string


def _registry_ordered_cols(tab: str, query_string: str) -> str:
    """``query_string`` with its ``cols`` names in registry order.

    The clause is dropped when the result equals the tab's default columns
    (#844), and every other pair is kept in place.
    """
    specs = STORAGE_TAB_COLUMNS[tab]
    index = {s.name: i for i, s in enumerate(specs)}
    defaults = [s.name for s in specs if s.default_visible]
    pairs: list[tuple[str, str]] = []
    for key, value in parse_qsl(query_string, keep_blank_values=True):
        if key == "cols":
            names = sorted((n for n in value.split(",") if n in index), key=index.__getitem__)
            if not names or names == defaults:
                continue
            value = ",".join(names)
        pairs.append((key, value))
    return urlencode(pairs)


def save(conn: sqlite3.Connection, tab: str, query_string: str) -> None:
    """Upsert the per-tab query string.

    No-op when ``query_string`` is empty — use :func:`reset` for explicit
    clears. The auto-save call site (page + /rows GETs) passes whatever
    :func:`serialize` produced; an empty string means the URL had no
    allowlisted filter state and there's nothing to remember.
    """
    _ensure_tab(tab)
    if not query_string:
        return
    conn.execute(
        "INSERT INTO view_prefs(tab, query_string, cols_ordered, updated_at) "
        "VALUES(?, ?, 1, datetime('now')) "
        "ON CONFLICT(tab) DO UPDATE SET "
        "  query_string=excluded.query_string, "
        "  cols_ordered=1, "
        "  updated_at=datetime('now')",
        (tab, query_string),
    )
    conn.commit()


def reset(conn: sqlite3.Connection, tab: str) -> None:
    _ensure_tab(tab)
    conn.execute("DELETE FROM view_prefs WHERE tab=?", (tab,))
    conn.commit()
