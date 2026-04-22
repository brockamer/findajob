#!/usr/bin/env python3
"""One-time migration: add user_notes column to jobs.

Free-text field the user edits on the Applied tab (web UI /board/applied
→ POST /board/jobs/{fp}/notes). Idempotent — ALTER TABLE ADD COLUMN
IF NOT EXISTS via column-presence check.

As of v0.1.1 this migration's column is folded into scripts/init_db.py.
Fresh deploys get the column from init_db on first container start;
this script is retained as a no-op for legacy stacks that were migrated
at the time of user_notes landing. Safe to delete this file in v0.2.x
once all known stacks have been verified on v0.1.1+.

Usage:  python3 scripts/migrate_add_user_notes.py
"""

import sqlite3
import sys
from pathlib import Path

from findajob.paths import BASE

DB_PATH = Path(BASE) / "data" / "pipeline.db"


def migrate() -> None:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "user_notes" in cols:
        print("user_notes column already exists — nothing to do.")
        conn.close()
        return

    conn.execute("ALTER TABLE jobs ADD COLUMN user_notes TEXT DEFAULT ''")
    conn.commit()
    conn.close()
    print("Added user_notes column to jobs table.")


if __name__ == "__main__":
    migrate()
