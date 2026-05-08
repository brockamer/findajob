"""GitHub open-issues digest — operator-facing."""

from findajob.notifications.ntfy import open_issues, send


def cmd_issues_ping() -> None:
    issues = open_issues()
    if not issues:
        body = "No open issues on GitHub."
        tags = "white_check_mark"
    else:
        lines = [f"{len(issues)} open issue(s):"]
        for i, iss in enumerate(issues, 1):
            lines.append(f"{i}. {iss}")
        body = "\n".join(lines)
        tags = "memo"
    send("💼 findajob — open issues", body, priority="default", tags=tags, kind="issues_ping")
