"""Next scheduled triage fire time for UI surfaces (#752).

Reads the triage cron from ``ops/scheduled-jobs.yaml``, with
``FINDAJOB_TRIAGE_SCHEDULE`` / ``FINDAJOB_TRIAGE_ENABLED`` env-var overrides
taking precedence (consistent with ``scripts/render_crontab.py`` — same
source of truth supercronic uses at container start).

Supports the simple cron forms findajob stacks use in practice: fields that
are either ``*`` or a single integer. Exotic forms (``*/N``, ranges, comma
lists) return ``None`` so the caller renders a generic fallback.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from findajob.paths import BASE


def _parse_field(field: str, lo: int, hi: int) -> set[int] | None:
    if field == "*":
        return set(range(lo, hi + 1))
    try:
        v = int(field)
    except ValueError:
        return None
    if v < lo or v > hi:
        return None
    return {v}


def _parse_cron(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int]] | None:
    parts = expr.strip().split()
    if len(parts) != 5:
        return None
    ranges = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
    parsed: list[set[int]] = []
    for field, (lo, hi) in zip(parts, ranges, strict=False):
        s = _parse_field(field, lo, hi)
        if s is None:
            return None
        parsed.append(s)
    return parsed[0], parsed[1], parsed[2], parsed[3], parsed[4]


def _load_schedule_from_yaml() -> tuple[str, bool] | None:
    path = Path(BASE) / "ops" / "scheduled-jobs.yaml"
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        triage = data["jobs"]["triage"]
        return str(triage["schedule"]), bool(triage.get("enabled", True))
    except (KeyError, TypeError, yaml.YAMLError):
        return None


def _resolve_schedule() -> tuple[str, bool] | None:
    """Effective (schedule, enabled) with env vars overriding yaml."""
    yaml_result = _load_schedule_from_yaml()
    env_schedule = os.environ.get("FINDAJOB_TRIAGE_SCHEDULE")
    env_enabled = os.environ.get("FINDAJOB_TRIAGE_ENABLED")

    schedule = env_schedule or (yaml_result[0] if yaml_result else None)
    if schedule is None:
        return None
    if env_enabled is not None:
        enabled = env_enabled.strip().lower() == "true"
    elif yaml_result is not None:
        enabled = yaml_result[1]
    else:
        enabled = True
    return schedule, enabled


def next_triage_time(now: datetime | None = None) -> datetime | None:
    """Next triage fire in the container's local TZ, or ``None``.

    Returns ``None`` when the schedule is disabled, unparseable in the
    supported subset, or unresolvable (yaml missing + no env override).
    ``now`` defaults to ``datetime.now()`` for testability.
    """
    resolved = _resolve_schedule()
    if resolved is None:
        return None
    schedule, enabled = resolved
    if not enabled:
        return None
    parsed = _parse_cron(schedule)
    if parsed is None:
        return None
    minutes, hours, mdays, months, dows = parsed
    # cron dow 0..6 = Sun..Sat; python weekday 0..6 = Mon..Sun.
    py_weekdays = {(d + 6) % 7 for d in dows}

    candidate = (now or datetime.now()).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if (
            candidate.minute in minutes
            and candidate.hour in hours
            and candidate.day in mdays
            and candidate.month in months
            and candidate.weekday() in py_weekdays
        ):
            return candidate
        candidate += timedelta(minutes=1)
    return None
