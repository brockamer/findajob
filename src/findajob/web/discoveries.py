"""Helper for #288 Section B — read `discovered_companies.json` for the Dashboard widget.

The discoverer (#284) writes a JSON sidecar alongside the markdown each weekly run.
This module loads that sidecar into a simple summary tuple consumed by the Dashboard
template. The widget reads JSON, never markdown — parsing markdown is fragile.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NamedTuple

STALE_THRESHOLD_DAYS = 10
"""7-day cron interval + 3-day grace before flagging a stale weekly run."""


class DiscoveriesSummary(NamedTuple):
    count: int  # type: ignore[assignment]  # NamedTuple field shadows tuple.count method
    generated_at_date: str
    days_since: int
    is_stale: bool
    top_names: list[str]


def load_discoveries_summary(base_root: Path, *, today: date | None = None) -> DiscoveriesSummary | None:
    """Read `candidate_context/discovered_companies.json` into a summary.

    Returns None if the file is missing, unreadable, or malformed — the widget
    renders an empty-state on None instead of erroring on a bad weekly run.

    `today` is injected for tests; defaults to today in UTC.
    """
    json_path = base_root / "candidate_context" / "discovered_companies.json"
    if not json_path.is_file():
        return None
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    companies = payload.get("companies")
    generated_at = payload.get("generated_at")
    if not isinstance(companies, list) or not isinstance(generated_at, str):
        return None

    try:
        gen_date = date.fromisoformat(generated_at[:10])
    except ValueError:
        return None

    ref_today = today if today is not None else datetime.now(UTC).date()
    days_since = (ref_today - gen_date).days

    top_names: list[str] = []
    for c in companies[:5]:
        if isinstance(c, dict):
            name = c.get("name")
            if isinstance(name, str) and name.strip():
                top_names.append(name.strip())

    return DiscoveriesSummary(
        count=len(companies),
        generated_at_date=gen_date.isoformat(),
        days_since=days_since,
        is_stale=days_since > STALE_THRESHOLD_DAYS,
        top_names=top_names,
    )
