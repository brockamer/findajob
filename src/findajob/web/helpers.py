"""Pure helpers for board-row conditional formatting."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlencode


def applied_age_bucket(applied_date_iso: str | None) -> str:
    """Return the CSS class name for an Applied row's age bucket.

    0-6 days   -> row-applied-fresh (green)
    7-13 days  -> row-applied-week  (yellow)
    14-20 days -> row-applied-stale (red)
    21+ days   -> row-applied-cold  (gray)
    None / unparseable -> ""
    """
    if not applied_date_iso:
        return ""
    try:
        dt = datetime.fromisoformat(applied_date_iso.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    age_days = (datetime.now(UTC) - dt).days
    if age_days <= 6:
        return "row-applied-fresh"
    if age_days <= 13:
        return "row-applied-week"
    if age_days <= 20:
        return "row-applied-stale"
    return "row-applied-cold"


def stage_row_class(stage: str | None) -> str:
    """Row-level background class for special stages (Offer, Interview)."""
    if stage == "offer":
        return "row-offer"
    if stage == "interview":
        return "row-interviewing"
    return ""


def remote_cell_class(remote_status: str | None) -> str:
    """Text color class for the Remote column cell based on its value."""
    if not remote_status:
        return ""
    s = remote_status.strip().lower()
    if "remote" in s and "hybrid" not in s:
        return "text-green-700"
    if "hybrid" in s:
        return "text-amber-700"
    return "text-slate-600"


def filter_qs_with(existing: str, key: str, value: str) -> str:
    """Return a re-encoded querystring with `key` set to `value`.

    Preserves all other params. Used by the density toggle to switch
    compact/expanded without losing active filters or sort.
    """
    from urllib.parse import parse_qsl

    pairs = [(k, v) for (k, v) in parse_qsl(existing, keep_blank_values=False) if k != key]
    pairs.append((key, value))
    return urlencode(pairs)
