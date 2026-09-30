#!/usr/bin/env python3
"""Interview-prep — entry-point shim.

Logic lives in `findajob.interview.orchestrator`. This script:
1. Calls `orchestrator.main()` (which reads sys.argv: company, title, job_id)
2. Logs `interview_prep_failed` on any unhandled exception before re-raising.

Launched as a detached subprocess from POST /board/jobs/{fp}/interview (see
findajob.web.routes.board_actions). Re-clicking "Interviewing" on the board
regenerates a fresh artifact with a new timestamp.
"""

from findajob.audit import log_event
from findajob.cliargs import positional_argv
from findajob.interview.orchestrator import main

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        fields = positional_argv()
        job_id = fields[2] if len(fields) > 2 else "unknown"
        company = fields[0] if len(fields) > 0 else "unknown"
        title = fields[1] if len(fields) > 1 else "unknown"
        log_event(
            "interview_prep_failed",
            job_id=job_id,
            company=company,
            title=title,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
