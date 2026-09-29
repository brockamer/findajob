#!/usr/bin/env python3
"""Application-materials prep — entry-point shim.

Logic lives in `findajob.prep.orchestrator`. This script:
1. Calls `orchestrator.main()` (which reads sys.argv: company, title, url, job_id)
2. Logs `prep_failed` and resets the job to `scored` on any unhandled exception

Launched as a detached subprocess from POST /board/jobs/{fp}/prep (see
findajob.web.routes.board_actions).
"""

from findajob.actions import reset_prep_to_scored
from findajob.audit import log_event
from findajob.cliargs import positional_argv
from findajob.db import connect
from findajob.paths import BASE
from findajob.prep.orchestrator import main

DB_PATH = f"{BASE}/data/pipeline.db"


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Recover from any unhandled error: log failure and reset stage so
        # the job can be retried on the next poll cycle.
        fields = positional_argv()
        job_id = fields[3] if len(fields) > 3 else "unknown"
        company = fields[0] if len(fields) > 0 else "unknown"
        title = fields[1] if len(fields) > 1 else "unknown"
        log_event(
            "prep_failed",
            job_id=job_id,
            company=company,
            title=title,
            error=f"{type(exc).__name__}: {exc}",
        )
        try:
            conn = connect(DB_PATH, timeout=30)
            reset_prep_to_scored(conn, job_id, reason=f"exception:{type(exc).__name__}")
            conn.close()
        except Exception:
            pass  # DB recovery is best-effort
        raise
