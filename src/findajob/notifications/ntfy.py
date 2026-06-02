"""Shared infrastructure for the notification suite.

- `send()` — persist to `notifications` table, then POST to ntfy.sh
- `quick_notify()` — fire-and-forget ntfy push; no DB persistence,
  dual-source topic lookup. Used by background scripts (triage, prep,
  interview) where the audit trail is pipeline.jsonl, not the
  notifications table.
- `_persist_notification()` — DB write that survives ntfy outages
- `db_connect()` — pipeline DB connection
- `recent_log_events()` — pipeline.jsonl tail
- `_p()` — pluralization helper
- `NOTIFICATION_KINDS` — closed-set taxonomy

Module-load side effect (the original `_env = load_env()` + NTFY_TOPIC /
NTFY_URL / WEB_BASE_URL globals) is replaced with a `functools.cache`-d
`_runtime()` accessor so the file read happens on first call, not at
import time.
"""

import functools
import json
import os
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta

from findajob.db import connect
from findajob.paths import BASE, load_env

DB_PATH = f"{BASE}/data/pipeline.db"
LOG_PATH = f"{BASE}/logs/pipeline.jsonl"


@functools.cache
def _runtime() -> dict[str, str]:
    """Lazy runtime config — env loaded on first call, not at import.

    `data/.env` may not exist on a brand-new stack; `load_env()` is a
    graceful no-op there. The defaults match the pre-extraction module
    globals literally.
    """
    env = load_env()
    web_url = env.get("FINDAJOB_WEB_URL") or os.environ.get("FINDAJOB_WEB_URL")
    if not web_url:
        fly_app = os.environ.get("FLY_APP_NAME")
        web_url = f"https://{fly_app}.fly.dev" if fly_app else "http://localhost:8090"
    return {
        "ntfy_topic": env.get("NTFY_TOPIC") or os.environ.get("NTFY_TOPIC", "jobsearch-pipeline"),
        "web_base_url": web_url.rstrip("/"),
    }


def _ntfy_url() -> str:
    return f"https://ntfy.sh/{_runtime()['ntfy_topic']}"


# Closed-set kind taxonomy (#440). Adding a new kind = update this tuple AND
# the per-kind color/label in templates/notifications/_kind.html.
NOTIFICATION_KINDS: tuple[str, ...] = (
    "daily_stats",
    "apply_reminder",
    "feedback_review",
    "health_check",
    "send_raw",
    "discovery_run",
    "gmail_auth_failure",
    "rejection_detected",
    "waitlist_resurface",
    "prep_briefing_ready",
    "prep_drafts_ready",
    "prep_failure",
    "interview_prep_ready",
    "interview_prep_failed",
    "study_guide_failed",
    "flashcard_failed",
    "podcast_ready",
    "podcast_failed",
    "recall_audit_alert",
    "drift_alert",
    "spend_ceiling_warning",
    "spend_ceiling_reached",
)


def _persist_notification(
    kind: str,
    title: str,
    body: str,
    priority: str,
    tags: str | None,
    delivery_status: str,
    delivery_error: str | None,
    cta_url: str | None,
) -> int | None:
    """Insert a row into the notifications table. Returns row id, or None on error.

    Persistence failures must NOT crash the caller — ntfy delivery and audit
    persistence are independent. A missing table on a brand-new stack with no
    init_db run is the only realistic failure; in that case we skip silently.
    """
    try:
        conn = db_connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO notifications
                    (kind, title, body, priority, tags, delivery_status, delivery_error, cta_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (kind, title, body, priority, tags, delivery_status, delivery_error, cta_url),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def send(title, body, priority="default", tags=None, kind="send_raw", cta_url=None):
    """Persist a notification row, then push via ntfy.sh.

    The DB row is the source of truth for the in-app notification dashboard
    (#440). We insert FIRST so the audit trail captures even ntfy outages,
    then attempt delivery. If ntfy.sh fails (network, 5xx), the row stays
    with `delivery_status='failed'` and `delivery_error` populated — never
    deleted. Returns the row id (or None if persistence itself failed).

    `kind` must be one of NOTIFICATION_KINDS; ValueError on unknown kind.
    """
    if kind not in NOTIFICATION_KINDS:
        raise ValueError(f"Unknown notification kind {kind!r}; expected one of {NOTIFICATION_KINDS}")
    headers = [
        "-H",
        f"Title: {title}",
        "-H",
        f"Priority: {priority}",
    ]
    if tags:
        headers += ["-H", f"Tags: {tags}"]
    result = subprocess.run(
        [
            "curl",
            "-s",
            "-X",
            "POST",
            _ntfy_url(),
            "-H",
            "Content-Type: text/plain; charset=utf-8",
            *headers,
            "-d",
            body,
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode == 0:
        delivery_status = "sent"
        delivery_error = None
    else:
        delivery_status = "failed"
        delivery_error = (result.stderr or b"").decode("utf-8", errors="replace")[:500] or "curl exited non-zero"
    return _persist_notification(
        kind=kind,
        title=title,
        body=body,
        priority=priority,
        tags=tags,
        delivery_status=delivery_status,
        delivery_error=delivery_error,
        cta_url=cta_url,
    )


def _p(n: int, singular: str, plural: str | None = None) -> str:
    """Pluralize for user-facing notification strings (#151)."""
    return f"{n} {singular}" if n == 1 else f"{n} {plural or singular + 's'}"


def db_connect() -> sqlite3.Connection:
    conn = connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def recent_log_events(hours: int = 25) -> list[dict]:
    """Return log entries from the last N hours."""
    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    events: list[dict] = []
    try:
        with open(LOG_PATH) as f:
            for line in f:
                try:
                    e = json.loads(line)
                    if e.get("ts", "") >= cutoff:
                        events.append(e)
                except json.JSONDecodeError:
                    pass
    except FileNotFoundError:
        pass
    return events


def quick_notify(message: str) -> None:
    """Fire-and-forget ntfy push for background scripts.

    Used by `findajob.{triage,prep,interview}.orchestrator` to surface
    pipeline events (completion, failure, abort) to the operator's
    phone. Distinct from `send()`:

    - **Dual-source topic lookup.** Reads ``config/ntfy_topic.txt``
      first, falling back to ``data/.env``'s ``NTFY_TOPIC``. The legacy
      ntfy_topic.txt path predates the .env convention; both are still
      supported on existing stacks.
    - **No DB persistence.** Skips the `notifications` table write.
      Background scripts have their own structured audit trail in
      `pipeline.jsonl` via ``log_event``; the in-app notification
      dashboard is a separate concern (and on a brand-new stack the
      table may not exist yet).
    - **Silent failure.** Both topic-load paths and the curl shell-out
      swallow exceptions — alerts are best-effort.

    Consolidates the byte-equivalent copies that lived in each of the
    three orchestrators after the M3 import-only extractions (#537).
    """
    topic = None
    try:
        with open(f"{BASE}/config/ntfy_topic.txt") as f:
            topic = f.read().strip()
    except FileNotFoundError:
        pass
    if not topic:
        # Fall back to data/.env NTFY_TOPIC
        try:
            with open(f"{BASE}/data/.env") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("NTFY_TOPIC") and "=" in line:
                        topic = line.split("=", 1)[1].strip().strip("'\"")
                        break
        except Exception:
            pass
    if not topic:
        return
    try:
        subprocess.run(
            ["curl", "-s", "-d", message, f"https://ntfy.sh/{topic}"],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        pass
