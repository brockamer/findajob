#!/usr/bin/env python3
"""One-shot migration: add jobs.loose_fingerprint column for Tier 2 dedup.

Part of #182 — two-tier dedup. Adds a (title, company)-only hash column
and index so cross-source syndication can be detected when one side has
a coarse location (e.g., Greenhouse "US" vs LinkedIn "Barstow, TX").

Backfills existing rows by recomputing loose_fingerprint from stored
(title, company). Rows stay intact — this is additive; no existing
duplicates are merged.

Idempotent — safe to re-run.

Fresh deploys get the column from scripts/init_db.py. This migration
exists for upgrading existing stacks from pre-#182 images.

Usage:  python3 scripts/migrate_add_loose_fingerprint.py
"""

import sqlite3
import sys
from pathlib import Path

from findajob.cleaning import loose_fingerprint
from findajob.paths import BASE

DB_PATH = Path(BASE) / "data" / "pipeline.db"


def migrate() -> None:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}

    if "loose_fingerprint" not in existing:
        conn.execute("ALTER TABLE jobs ADD COLUMN loose_fingerprint TEXT")
        print("  added jobs.loose_fingerprint TEXT")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_loose_fingerprint ON jobs(loose_fingerprint)")

    rows = conn.execute(
        "SELECT id, title, company FROM jobs WHERE loose_fingerprint IS NULL OR loose_fingerprint = ''"
    ).fetchall()
    backfilled = 0
    for job_id, title, company in rows:
        lfp = loose_fingerprint(title or "", company or "")
        conn.execute("UPDATE jobs SET loose_fingerprint = ? WHERE id = ?", (lfp, job_id))
        backfilled += 1

    conn.commit()
    conn.close()

    if backfilled == 0:
        print("All rows already have loose_fingerprint — nothing to backfill.")
    else:
        print(f"Migration complete — {backfilled} rows backfilled.")


if __name__ == "__main__":
    migrate()
