"""Check GitHub Actions CI and notify on the most recent main-branch failure.

Stays quiet if the latest run is green (prior failures are stale).
"""

import json
import subprocess

from findajob.notifications.ntfy import send


def cmd_ci_check() -> None:
    """Check GitHub Actions CI status and notify on the most recent failure.

    Only alerts if the latest completed main-branch run failed.
    Stays quiet if the latest run is green (prior failures are stale).
    """
    result = subprocess.run(
        ["gh", "run", "list", "--limit", "5", "--json", "conclusion,headBranch,displayTitle,url,status"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        send(
            "💼 findajob — CI check",
            f"gh run list failed: {result.stderr[:200]}",
            priority="default",
            tags="warning",
            kind="ci_check",
        )
        return

    try:
        runs = json.loads(result.stdout)
    except json.JSONDecodeError:
        return

    # Find the most recent completed run on main
    latest = next(
        (r for r in runs if r.get("headBranch") == "main" and r.get("status") == "completed"),
        None,
    )
    if not latest or latest.get("conclusion") != "failure":
        return  # latest is green or in progress, stay quiet

    lines = [
        "Latest CI run failed:",
        f"  {latest.get('displayTitle', '?')}",
        f"  {latest.get('url', '')}",
    ]
    send("💼 findajob — CI failed", "\n".join(lines), priority="high", tags="x", kind="ci_check")
