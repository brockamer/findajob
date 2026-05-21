"""Analysis of jobs the user has passed on — user-facing."""

import json
import subprocess
import sys

from findajob.notifications.ntfy import _runtime, db_connect, send
from findajob.paths import IMAGE_ROOT


def cmd_feedback_review() -> None:
    THRESHOLD = 10

    conn = db_connect()
    count = conn.execute("SELECT COUNT(*) FROM feedback_log").fetchone()[0]
    conn.close()

    if count < THRESHOLD:
        print(f"feedback_log has {count} entries — below threshold ({THRESHOLD}). No ping sent.")
        return

    web_base_url = _runtime()["web_base_url"]

    # Run analyze_feedback.py and capture key stats for the notification
    try:
        result = subprocess.run(
            [sys.executable, f"{IMAGE_ROOT}/scripts/analyze_feedback.py", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        data = json.loads(result.stdout) if result.stdout else {}
    except Exception:
        data = {}

    if data and not data.get("error"):
        fp = data.get("false_positives", 0)
        fp_pct = data.get("fp_pct", 0)
        top_reason = data["by_reason"][0] if data.get("by_reason") else ("?", 0)
        top_company = data["company_fp_counts"][0] if data.get("company_fp_counts") else ("?", 0)
        bad_kws = [
            kw["keyword"] for kw in data.get("keyword_signals", []) if kw["ratio"] < 0.4 and kw["rejected_n"] >= 3
        ][:4]
        body = (
            f"You've passed on {count} jobs so far.\n"
            f"{fp} of those were strong matches the ranker recommended ({fp_pct}%) — these are where it got it wrong.\n"
            f"Most common reason for passing: {top_reason[0]} ({top_reason[1]} times)\n"
            f"Company you keep passing on: {top_company[0]} ({top_company[1]} times)\n"
            + (f"Words that often signal a pass: {', '.join(bad_kws)}\n" if bad_kws else "")
            + f"See the trends: {web_base_url}/stats/feedback"
        )
    else:
        body = f"You've passed on {count} jobs so far.\nSee the trends: {web_base_url}/stats/feedback"

    send("💼 findajob — feedback check", body, priority="default", tags="magnifying", kind="feedback_review")
