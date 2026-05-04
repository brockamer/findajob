#!/usr/bin/env python3
"""One-shot cleanup: normalize jobs.reject_reason to the canonical vocabulary.

Operator stacks accumulate non-canonical values in `jobs.reject_reason` from
two sources:

1.  Pre-#429 free-text era — operators typed values like "Wrong Niche" or
    "Too Software/Systems" into a free-text field before the dropdown was
    constrained to a YAML-driven vocabulary.

2.  Pipeline-internal markers — earlier code paths (since deleted) wrote
    diagnostic strings like "duplicate_entry" or "Ingest noise (...)" into
    the same column the user-facing dropdown reads from.

This script normalizes every non-canonical value to a canonical one. The
mapping is explicit for known stale values, with "Other" as the fallback
for anything unmapped. Every change writes an audit_log entry with
`changed_by='reject_reason_cleanup_445'` so the original value is recoverable.

Idempotent — safe to re-run. Rows already at canonical values are no-ops.

The companion code fixes (`scripts/triage.py:437` writing "Other" instead
of "Blank Company"; `_DEFAULT_REJECT_REASONS` adding "Company passed") ship
in the same PR (#445) — without those, this script's effect recurs on every
triage run. See: GitHub issue #445.

Usage: python3 scripts/cleanup_reject_reasons.py [--dry-run]
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from findajob.config_loader import load_reject_reasons
from findajob.paths import BASE
from findajob.utils import write_audit

DB_PATH = Path(BASE) / "data" / "pipeline.db"
CHANGED_BY = "reject_reason_cleanup_445"

# Explicit mapping for known stale values. Anything not listed here and not
# in the canonical config falls back to "Other" (with a warning).
EXPLICIT_MAPPING: dict[str, str] = {
    # Pre-#429 free-text operator vocabulary — semantic kin of "Skills Mismatch"
    "Wrong Niche": "Skills Mismatch",
    "Too Software/Systems": "Skills Mismatch",
    "Too Facilities/MEP": "Skills Mismatch",
    "Too Manufacturing/Test": "Skills Mismatch",
    # Triage's blank-company writer (since changed to "Other" in this PR)
    "Blank Company": "Other",
    "Blank company": "Other",
    # Pipeline-internal markers from deleted code paths
    "duplicate_entry": "Other",
    "Ingest noise (aggregator_company)": "Other",
    "Stuck in discovered": "Other",
    "Not a Real Job": "Other",
}

FALLBACK = "Other"


def cleanup(dry_run: bool = False) -> int:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        return 1

    canonical_reasons, _title_signal = load_reject_reasons()
    canonical = frozenset(canonical_reasons)

    if FALLBACK not in canonical:
        print(
            f"ERROR: fallback '{FALLBACK}' not in canonical reasons {canonical_reasons}; "
            f"check config/reject_reasons.yaml",
            file=sys.stderr,
        )
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, reject_reason FROM jobs WHERE reject_reason IS NOT NULL AND reject_reason != ''"
    ).fetchall()

    print(f"Canonical reasons: {sorted(canonical)}")
    print(f"Scanning {len(rows)} rows with non-empty reject_reason...")

    changes: dict[str, dict[str, int]] = {}
    unmapped_warnings: set[str] = set()
    total_changed = 0

    for row in rows:
        old = row["reject_reason"]
        if old in canonical:
            continue

        if old in EXPLICIT_MAPPING:
            new = EXPLICIT_MAPPING[old]
        else:
            new = FALLBACK
            if old not in unmapped_warnings:
                print(f"  WARNING: '{old}' not in canonical and not in EXPLICIT_MAPPING — falling back to '{new}'")
                unmapped_warnings.add(old)

        changes.setdefault(old, {}).setdefault(new, 0)
        changes[old][new] += 1
        total_changed += 1

        if not dry_run:
            conn.execute(
                "UPDATE jobs SET reject_reason=? WHERE id=?",
                (new, row["id"]),
            )
            write_audit(
                conn,
                row["id"],
                "reject_reason",
                old,
                new,
                changed_by=CHANGED_BY,
            )

    if not dry_run:
        conn.commit()

    print()
    print(f"{'(DRY RUN) ' if dry_run else ''}Summary:")
    if not changes:
        print("  All rows already canonical — no changes needed.")
    else:
        for old in sorted(changes):
            for new, count in sorted(changes[old].items()):
                print(f"  '{old}' → '{new}': {count} row(s)")
        print(f"  Total rows changed: {total_changed}")

    conn.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing",
    )
    args = parser.parse_args()
    return cleanup(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
