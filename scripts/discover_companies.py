#!/usr/bin/env python3
"""scripts/discover_companies.py — entry point for the weekly discovery cron.

Calls findajob.discoverer.run(base_root) and exits 0/1 by RunResult.success.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from findajob.audit import log_event
from findajob.discoverer import run as run_discovery
from findajob.onboarding import is_complete as _onboarding_is_complete
from findajob.paths import BASE


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover companies for the candidate profile.")
    parser.add_argument(
        "--profile",
        type=Path,
        default=None,
        help="Path to profile.md (default: BASE/candidate_context/profile.md)",
    )
    parser.add_argument(
        "--ntfy",
        dest="ntfy",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable ntfy alerts on failure / cost-threshold breach (default: enabled).",
    )
    args = parser.parse_args()
    base_root = Path(BASE)
    if not _onboarding_is_complete(base_root):
        log_event("discovery_skipped", reason="not_onboarded")
        return 0
    result = run_discovery(base_root, profile_path=args.profile, ntfy_enabled=args.ntfy)
    if result.success:
        print(f"discovery: wrote {result.count} companies (cost={result.cost_usd or 'unknown'})")
        return 0
    print(f"discovery: FAILED — {result.error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
