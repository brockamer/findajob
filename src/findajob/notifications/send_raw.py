"""Passthrough notification — `notify-py send-raw <title> <body>`."""

import sys

from findajob.notifications.ntfy import send


def cmd_send_raw() -> None:
    """Send a raw notification.

    Usage:
        notify.py send-raw <title> <body> [--kind <kind>]

    `--kind` defaults to 'send_raw' — pass through one of NOTIFICATION_KINDS
    when calling from a known internal site (e.g. discoverer, fetchers).
    """
    if len(sys.argv) < 4:
        print("Usage: notify.py send-raw <title> <body> [--kind <kind>]")
        sys.exit(1)
    title = sys.argv[2]
    body = sys.argv[3]
    kind = "send_raw"
    if "--kind" in sys.argv:
        idx = sys.argv.index("--kind")
        if idx + 1 < len(sys.argv):
            kind = sys.argv[idx + 1]
    send(title, body, priority="default", tags="hourglass_flowing_sand", kind=kind)
