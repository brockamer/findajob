#!/usr/bin/env python3
"""Detached subprocess entry: run speculative research for a request_id.

Spawned from POST /ingest/speculative as a background process. Reads the
DB, runs run_research(), exits. Idempotent on re-spawn (e.g. for
regeneration) because run_research caches briefing_md across retries.

Usage:
    python scripts/run_speculative_research.py <request_id>
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from findajob.audit import log_event
from findajob.db import connect
from findajob.paths import BASE
from findajob.speculative.runner import run_research


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].isdigit():
        print("usage: run_speculative_research.py <request_id>", file=sys.stderr)
        return 2
    request_id = int(argv[1])

    db_path = Path(BASE) / "data" / "pipeline.db"
    profile = Path(BASE) / "candidate_context" / "profile.md"
    master_resume = Path(BASE) / "candidate_context" / "master_resume.md"
    companies_dir = Path(BASE) / "companies"

    conn = connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        run_research(
            conn=conn,
            request_id=request_id,
            profile_path=profile,
            master_resume_path=master_resume,
            companies_dir=companies_dir,
        )
    except Exception as e:
        log_event("speculative_research_uncaught_exception", request_id=request_id, error=str(e))
        # run_research best-efforts a status='failed' write on known errors;
        # this catches truly unexpected (e.g. DB connection errors) so the
        # process never exits without recording state.
        try:
            conn.execute(
                "UPDATE speculative_requests SET status='failed', error_message=? WHERE id=?",
                (f"unexpected: {e}", request_id),
            )
            conn.commit()
        except Exception:
            pass
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
