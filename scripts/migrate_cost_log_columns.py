#!/usr/bin/env python3
"""One-shot migration: extend cost_log with input_tokens, output_tokens, cost_usd.

Part of #32 — LLM cost tracking. Adds columns for token counts and cost
without breaking existing cost_log inserts (old callers will leave the
new columns NULL until they're updated).

Idempotent — safe to re-run.

As of v0.1.1 this migration's columns are folded into scripts/init_db.py.
Fresh deploys get the columns from init_db on first container start;
this script is retained as a no-op for legacy stacks that were migrated
at the time of #32 landing. Safe to delete this file in v0.2.x once all
known stacks have been verified on v0.1.1+.

Usage:  python3 scripts/migrate_cost_log_columns.py
"""

import sqlite3
import sys
from pathlib import Path

from findajob.paths import BASE

DB_PATH = Path(BASE) / "data" / "pipeline.db"

NEW_COLUMNS = [
    ("input_tokens", "INTEGER"),
    ("output_tokens", "INTEGER"),
    ("cost_usd", "REAL"),
]


def migrate() -> None:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    existing = {row[1] for row in conn.execute("PRAGMA table_info(cost_log)").fetchall()}
    added = 0
    for name, coltype in NEW_COLUMNS:
        if name in existing:
            continue
        conn.execute(f"ALTER TABLE cost_log ADD COLUMN {name} {coltype}")
        added += 1
        print(f"  added cost_log.{name} {coltype}")
    conn.commit()
    conn.close()
    if added == 0:
        print("All columns already exist — nothing to do.")
    else:
        print(f"Migration complete — {added} columns added.")


if __name__ == "__main__":
    migrate()
