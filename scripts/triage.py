#!/usr/bin/env python3
"""Daily triage pipeline — entry-point shim.

Logic lives in `findajob.triage.orchestrator`. This script:
1. Installs the SIGTERM handler (so systemd timeouts log a termination event)
2. Parses CLI flags
3. Calls `orchestrator.main(...)` and converts any unhandled exception into
   a `pipeline_crash` log event before re-raising.

Keeping this surface ≤50 LOC is a soft architectural rule (CLAUDE.md). All
behavior beyond entry-point glue belongs in `findajob.triage.*`.
"""

import argparse
import signal
import traceback

from findajob.triage.orchestrator import _on_sigterm, main
from findajob.utils import log_event

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _on_sigterm)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--gmail-since-days",
        type=int,
        default=None,
        metavar="N",
        help="fetch Gmail messages from the past N days instead of incrementally (diagnostic/backfill)",
    )
    args = parser.parse_args()
    try:
        main(gmail_since_days=args.gmail_since_days)
    except Exception as e:
        log_event("pipeline_crash", error=str(e), traceback=traceback.format_exc())
        raise
