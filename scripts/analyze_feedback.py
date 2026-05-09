#!/usr/bin/env python3
"""Feedback loop analysis CLI shim.

Real logic lives in ``findajob.analyze_feedback``. This entry point is
spawned as a subprocess from ``findajob.notifications.feedback_review``
with ``--json``, and is also runnable directly for ad-hoc reports
(``--notify`` sends an ntfy summary).

Usage:
    python3 scripts/analyze_feedback.py           # print report
    python3 scripts/analyze_feedback.py --notify  # also send via ntfy
    python3 scripts/analyze_feedback.py --json    # output JSON
"""

from findajob.analyze_feedback import main

if __name__ == "__main__":
    main()
