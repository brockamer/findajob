#!/usr/bin/env python3
"""Backfill jobs.company_tier for existing rows. Idempotent."""

from __future__ import annotations

from findajob.db import connect
from findajob.paths import BASE
from findajob.tiers import load_tier1_companies, resolve_tier

DB_PATH = f"{BASE}/data/pipeline.db"


def main() -> None:
    load_tier1_companies.cache_clear()
    conn = connect(DB_PATH, timeout=30)
    rows = conn.execute("SELECT id, company FROM jobs").fetchall()
    for job_id, company in rows:
        conn.execute("UPDATE jobs SET company_tier=? WHERE id=?", (resolve_tier(company), job_id))
    conn.commit()
    conn.close()
    print(f"Backfilled company_tier on {len(rows)} rows.")


if __name__ == "__main__":
    main()
