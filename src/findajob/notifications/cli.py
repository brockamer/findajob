"""Dispatch table + entry point for the notifications CLI.

The script shim at `scripts/notify.py` imports `main` from here.
"""

import sys
from collections.abc import Callable

from findajob.notifications.apply_reminder import cmd_apply_reminder
from findajob.notifications.ci_check import cmd_ci_check
from findajob.notifications.daily_stats import cmd_daily_stats
from findajob.notifications.feedback_review import cmd_feedback_review
from findajob.notifications.health_check import cmd_health_check
from findajob.notifications.issues_ping import cmd_issues_ping
from findajob.notifications.scoreboard import cmd_scoreboard
from findajob.notifications.send_raw import cmd_send_raw

COMMANDS: dict[str, Callable[[], None]] = {
    "daily-stats": cmd_daily_stats,
    "health-check": cmd_health_check,
    "issues-ping": cmd_issues_ping,
    "apply-reminder": cmd_apply_reminder,
    "feedback-review": cmd_feedback_review,
    "send-raw": cmd_send_raw,
    "ci-check": cmd_ci_check,
    "scoreboard": cmd_scoreboard,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: notify.py [{'|'.join(COMMANDS)}]")
        sys.exit(1)
    COMMANDS[sys.argv[1]]()
