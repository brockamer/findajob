"""Staging green-check (#565).

Exits 0 iff all four predicates hold:
  1. pipeline_complete event present in last 26h
  2. zero ERROR-level events between the most recent pipeline_started and pipeline_complete
  3. findajob.web.verify_auth exits 0 (delegated to subprocess)
  4. clicker sentinel last invocation exited 0

Designed to be invoked from inside the staging container by operator
during the pre-tag checklist.

Event names match what `scripts/triage.py` emits via `findajob.audit.log_event`
(``pipeline_started`` / ``pipeline_complete``). The earlier draft of this module
named them ``triage_*`` which never appeared in pipeline.jsonl — fixed in #611.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

from findajob.paths import BASE

DEFAULT_LOG = Path(BASE) / "data" / "pipeline.jsonl"
DEFAULT_SENTINEL = Path(BASE) / "data" / ".staging_clicker_last_status"
DEFAULT_TRIAGE_MAX_AGE_HOURS = 26


def _read_events(log: Path) -> list[dict]:
    if not log.exists():
        return []
    out: list[dict] = []
    for raw in log.read_text().splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def _parse_ts(s: str) -> dt.datetime:
    s = s.replace("Z", "+00:00")
    return dt.datetime.fromisoformat(s)


def _predicate_triage_recent(log: Path, max_age_hours: int) -> bool:
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(hours=max_age_hours)
    for ev in reversed(_read_events(log)):
        if ev.get("event") == "pipeline_complete":
            try:
                ts = _parse_ts(ev["ts"])
            except (KeyError, ValueError):
                return False
            return ts >= cutoff
    return False


def _predicate_no_errors_during_last_triage(log: Path) -> bool:
    events = _read_events(log)
    last_started: int | None = None
    last_completed: int | None = None
    for i, ev in enumerate(events):
        name = ev.get("event")
        if name == "pipeline_started":
            last_started = i
        elif name == "pipeline_complete":
            last_completed = i
    if last_started is None or last_completed is None or last_completed < last_started:
        return False
    window = events[last_started : last_completed + 1]
    return not any(ev.get("level") == "ERROR" for ev in window)


def _predicate_clicker_last_zero(sentinel: Path) -> bool:
    if not sentinel.exists():
        return False
    try:
        payload = json.loads(sentinel.read_text())
    except json.JSONDecodeError:
        return False
    return payload.get("exit_code") == 0


def _predicate_verify_auth_zero() -> bool:
    rc = subprocess.run(
        [sys.executable, "-m", "findajob.web.verify_auth"],
        capture_output=True,
        check=False,
    ).returncode
    return rc == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Staging green-check (#565)")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--sentinel", type=Path, default=DEFAULT_SENTINEL)
    parser.add_argument("--triage-max-age-hours", type=int, default=DEFAULT_TRIAGE_MAX_AGE_HOURS)
    args = parser.parse_args(argv)

    results = {
        "triage_recent": _predicate_triage_recent(args.log, args.triage_max_age_hours),
        "no_errors_during_triage": _predicate_no_errors_during_last_triage(args.log),
        "verify_auth_zero": _predicate_verify_auth_zero(),
        "clicker_last_zero": _predicate_clicker_last_zero(args.sentinel),
    }
    failed = [k for k, v in results.items() if not v]
    if failed:
        print(f"FAIL: {', '.join(failed)}", file=sys.stderr)
        for k, v in results.items():
            print(f"  {k}: {'OK' if v else 'FAIL'}", file=sys.stderr)
        return 1
    print("OK: all 4 predicates green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
