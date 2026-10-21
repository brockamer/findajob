"""Display order of the board's reject-reason dropdown (#1220).

Decision recorded on #1220: one flat list, no headings. Fit reasons come
first, the most-used first (``feedback_log`` counts; ties keep the
``reject_reasons.yaml`` order). The bookkeeping reasons — which say
nothing about fit — always come last, in YAML order. A stack with no
rejections yet shows the YAML order with the bookkeeping reasons moved
to the end.

The bookkeeping set is matched case-insensitively against the operator's
labels. It names the field-neutral defaults from
``config_loader._DEFAULT_REJECT_REASONS``; an operator who renames them
gets plain count order for the renamed labels, which is harmless.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from findajob.db import connect

BOOKKEEPING_REASONS: frozenset[str] = frozenset({"already applied", "stale/closed", "company passed", "other"})

# The dropdown renders once per board row, so the counts are memoised
# briefly per database. A reject made in the last 30 seconds can sort one
# page load late; the reasons themselves are never cached (#490).
_COUNTS_TTL_SECONDS = 30.0
_counts_cache: dict[str, tuple[float, dict[str, int]]] = {}


def order_reject_reasons(reasons: Sequence[str], counts: Mapping[str, int]) -> tuple[str, ...]:
    """Fit reasons by use count (desc, stable), then bookkeeping reasons."""
    fit = [r for r in reasons if r.lower() not in BOOKKEEPING_REASONS]
    bookkeeping = [r for r in reasons if r.lower() in BOOKKEEPING_REASONS]
    fit.sort(key=lambda r: -counts.get(r, 0))
    return (*fit, *bookkeeping)


def reject_reason_counts(db_path: Path | str) -> dict[str, int]:
    """``feedback_log`` rejections per reason; empty on any database error."""
    key = str(db_path)
    now = time.monotonic()
    hit = _counts_cache.get(key)
    if hit is not None and now - hit[0] < _COUNTS_TTL_SECONDS:
        return hit[1]
    try:
        conn = connect(db_path, timeout=5)
        try:
            rows = conn.execute("SELECT reject_reason, COUNT(*) FROM feedback_log GROUP BY reject_reason").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    counts = {reason: n for reason, n in rows if reason}
    _counts_cache[key] = (now, counts)
    return counts
