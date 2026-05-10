"""Synthetic clicker for staging soak (#565).

Exercises M6 launcher web routes that operator action would trigger on
production. Without it, staging would only catch triage / scoring / notify
bugs — exactly the M6 blind spot this tier is meant to close.

Modes:
  --mode prep        every ~6h: pick scored job, POST /board/jobs/{fp}/prep
  --mode interview   weekly: pick applied job, POST /board/jobs/{fp}/interview
  --mode speculative weekly: pick canned target, POST /ingest/speculative
  --mode advance     daily: age materials_drafted → applied (so interview
                     cron has things to find)

Errors propagate via process exit code AND a sentinel file consumed by
findajob.staging.green.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from findajob.db import connect
from findajob.paths import BASE

DEFAULT_DB = Path(BASE) / "data" / "pipeline.db"
DEFAULT_SENTINEL = Path(BASE) / "data" / ".staging_clicker_last_status"
DEFAULT_SPECULATIVE_TARGETS = Path(BASE) / "config" / "speculative_targets.txt"
DEFAULT_ADVANCE_THRESHOLD_HOURS = 12
DEFAULT_BASE_URL = os.environ.get("FINDAJOB_STAGING_BASE_URL", "http://127.0.0.1:8000")


def _pick_for_prep(db: Path) -> str | None:
    conn = connect(db, ro=True)
    rows = conn.execute("SELECT fingerprint FROM jobs WHERE stage = 'scored' ORDER BY RANDOM() LIMIT 1").fetchall()
    conn.close()
    return rows[0][0] if rows else None


def _pick_for_interview(db: Path) -> str | None:
    conn = connect(db, ro=True)
    rows = conn.execute("SELECT fingerprint FROM jobs WHERE stage = 'applied' ORDER BY RANDOM() LIMIT 1").fetchall()
    conn.close()
    return rows[0][0] if rows else None


def _pick_for_advance(db: Path, threshold_hours: int) -> str | None:
    cutoff = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=threshold_hours)).isoformat()
    conn = connect(db, ro=True)
    rows = conn.execute(
        "SELECT fingerprint FROM jobs "
        "WHERE stage = 'materials_drafted' AND stage_updated < ? "
        "ORDER BY RANDOM() LIMIT 1",
        (cutoff,),
    ).fetchall()
    conn.close()
    return rows[0][0] if rows else None


def _pick_speculative_target(target_file: Path) -> str | None:
    if not target_file.exists():
        return None
    candidates = [
        line.strip() for line in target_file.read_text().splitlines() if line.strip() and not line.startswith("#")
    ]
    return random.choice(candidates) if candidates else None


def _post(url: str, data: bytes | None = None) -> int:
    """POST with optional HTTP Basic Auth from env. Returns HTTP status."""
    req = urllib.request.Request(url, data=data or b"", method="POST")
    if data:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    auth_user = os.environ.get("FINDAJOB_AUTH_USER")
    auth_pass = os.environ.get("FINDAJOB_AUTH_PASS")
    if auth_user and auth_pass:
        import base64

        token = base64.b64encode(f"{auth_user}:{auth_pass}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def _write_sentinel(path: Path, exit_code: int, mode: str) -> None:
    payload = {
        "exit_code": exit_code,
        "mode": mode,
        "timestamp": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _run_prep(base_url: str, db: Path) -> int:
    fp = _pick_for_prep(db)
    if fp is None:
        return 0  # no scored jobs → no-op, not an error
    status = _post(f"{base_url}/board/jobs/{fp}/prep")
    return 0 if 200 <= status < 400 else 1


def _run_interview(base_url: str, db: Path) -> int:
    fp = _pick_for_interview(db)
    if fp is None:
        return 0
    status = _post(f"{base_url}/board/jobs/{fp}/interview")
    return 0 if 200 <= status < 400 else 1


def _run_speculative(base_url: str, target_file: Path) -> int:
    target = _pick_speculative_target(target_file)
    if target is None:
        return 0
    body = f"company={urllib.parse.quote(target)}".encode()
    status = _post(f"{base_url}/ingest/speculative", data=body)
    return 0 if 200 <= status < 400 else 1


def _run_advance(base_url: str, db: Path, threshold_hours: int) -> int:
    fp = _pick_for_advance(db, threshold_hours)
    if fp is None:
        return 0
    status = _post(f"{base_url}/board/jobs/{fp}/apply")
    return 0 if 200 <= status < 400 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Staging synthetic clicker (#565)")
    parser.add_argument("--mode", required=True, choices=["prep", "interview", "speculative", "advance"])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--sentinel", type=Path, default=DEFAULT_SENTINEL)
    parser.add_argument("--speculative-targets", type=Path, default=DEFAULT_SPECULATIVE_TARGETS)
    parser.add_argument("--advance-threshold-hours", type=int, default=DEFAULT_ADVANCE_THRESHOLD_HOURS)
    args = parser.parse_args(argv)

    rc = 1
    try:
        if args.mode == "prep":
            rc = _run_prep(args.base_url, args.db)
        elif args.mode == "interview":
            rc = _run_interview(args.base_url, args.db)
        elif args.mode == "speculative":
            rc = _run_speculative(args.base_url, args.speculative_targets)
        else:  # advance
            rc = _run_advance(args.base_url, args.db, args.advance_threshold_hours)
    finally:
        _write_sentinel(args.sentinel, exit_code=rc, mode=args.mode)
    return rc


if __name__ == "__main__":
    sys.exit(main())
