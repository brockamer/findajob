#!/usr/bin/env python3
"""Interview-prep — entry-point shim.

Logic lives in `findajob.interview.orchestrator`. This script:
1. Calls `orchestrator.main()` (which reads sys.argv: company, title, job_id)
2. Logs `interview_prep_failed` on any unhandled exception before re-raising.

Launched as a detached subprocess from POST /board/jobs/{fp}/interview (see
findajob.web.routes.board_actions). Re-clicking "Interviewing" on the board
regenerates a fresh artifact with a new timestamp.
"""

import sys

from findajob.audit import log_event
from findajob.interview.orchestrator import main

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        job_id = sys.argv[3] if len(sys.argv) > 3 else "unknown"
        company = sys.argv[1] if len(sys.argv) > 1 else "unknown"
        title = sys.argv[2] if len(sys.argv) > 2 else "unknown"
        log_event(
            "interview_prep_failed",
            job_id=job_id,
            company=company,
            title=title,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
