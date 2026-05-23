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
from urllib.parse import urlencode

from findajob.web.filters.url import ParsedFilters

ALLOWED_TABS: frozenset[str] = frozenset(
    {"dashboard", "applied", "review", "waitlist", "rejected", "not_selected", "archive"}
)


def serialize(parsed: ParsedFilters, *, default_cols: tuple[str, ...] | None = None) -> str:
    """Rebuild a canonical, allowlisted query string from ParsedFilters.

    Only emits keys produced by the per-column filter framework — sort,
    desc (only when False, since True is the default), text, numeric
    range (_min/_max), enum, date range (_from/_to), cols. Anything
    outside this set is filtered by construction.

    When ``default_cols`` is provided and ``parsed.cols`` equals it
    (set-equal, ordering ignored), the ``cols=`` clause is dropped.
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
    if parsed.cols and (default_cols is None or set(parsed.cols) != set(default_cols)):
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
    _ensure_tab(tab)
    row = conn.execute("SELECT query_string FROM view_prefs WHERE tab=?", (tab,)).fetchone()
    return row[0] if row else None


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
        "INSERT INTO view_prefs(tab, query_string, updated_at) "
        "VALUES(?, ?, datetime('now')) "
        "ON CONFLICT(tab) DO UPDATE SET "
        "  query_string=excluded.query_string, "
        "  updated_at=datetime('now')",
        (tab, query_string),
    )
    conn.commit()


def reset(conn: sqlite3.Connection, tab: str) -> None:
    _ensure_tab(tab)
    conn.execute("DELETE FROM view_prefs WHERE tab=?", (tab,))
    conn.commit()
