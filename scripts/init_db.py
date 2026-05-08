#!/usr/bin/env python3
"""Initialize / migrate the pipeline DB.

Thin entry-point shim — the schema lives in ``migrations/0001_initial.sql``
and the runner lives in ``findajob.db.migrate``. This script is invoked
by ``ops/entrypoint.sh`` at every container start; it is a no-op when
the DB is already at the head migration version.

Usage:
    python3 scripts/init_db.py [DB_PATH]

If no DB_PATH is given, defaults to ``$BASE/data/pipeline.db``.
"""

from __future__ import annotations

import sys

from findajob.db import connect
from findajob.db.migrate import apply_pending
from findajob.paths import BASE

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else f"{BASE}/data/pipeline.db"

conn = connect(DB_PATH, timeout=30)
try:
    applied = apply_pending(conn)
finally:
    conn.close()

if applied:
    for m in applied:
        print(f"applied {m.version:04d}_{m.name}")
print("Database initialized:", DB_PATH)
