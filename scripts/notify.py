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

Cron subcommands also emit paired `cron_started`/`cron_finished` events
via `cron_event_span` (#650) so the /tools/ trigger panel can detect
running state and the log viewer can render the run. Non-cron
subcommands (`send-raw`, `ci-check` when invoked manually) run without
a span.
"""

import sys

from findajob.audit import cron_event_span
from findajob.notifications.cli import main as _inner_main

# Map notify.py subcommand → cron slug declared in CRON_TILES + ops/scheduled-jobs.yaml.
# Subcommands not in this map run without a cron span (send-raw / ci-check / ad-hoc).
_SUBCMD_TO_CRON_SLUG: dict[str, str] = {
    "apply-reminder": "notify-apply",
    "daily-stats": "notify-stats",
    "health-check": "notify-health",
    "issues-ping": "notify-issues",
    "scoreboard": "notify-scoreboard",
    "feedback-review": "notify-feedback",
}


def main() -> None:
    subcmd = sys.argv[1] if len(sys.argv) > 1 else ""
    slug = _SUBCMD_TO_CRON_SLUG.get(subcmd)
    if slug is None:
        _inner_main()
        return
    with cron_event_span(slug):
        _inner_main()


if __name__ == "__main__":
    main()
