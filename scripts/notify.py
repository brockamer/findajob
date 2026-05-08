#!/usr/bin/env python3
"""ntfy push notification suite — entry-point shim.

Logic lives in `findajob.notifications.*`. Subcommands:

  daily-stats     — morning summary of pipeline state (user-facing)
  apply-reminder  — daily nudge with quip + checklist (user-facing)
  feedback-review — analysis of jobs you've passed on (user-facing)
  health-check    — operator diagnostic: surface errors and stale automations
  issues-ping     — open GitHub issues (operator)
  ci-check        — alert on latest main-branch CI failure (operator)
  scoreboard      — refresh the pipeline funnel issue (operator)
  send-raw        — passthrough; usage: notify.py send-raw <title> <body>

User-facing subcommand strings stay in plain English (#151). Operator
diagnostics keep their technical detail; only their titles are branded.
"""

from findajob.notifications.cli import main

if __name__ == "__main__":
    main()
