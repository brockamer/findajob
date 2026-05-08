"""De-jargon guard for findajob.notifications user-facing subcommands (#151).

Three subcommands are direct user nudges (notifications a non-technical
beta tester reads): daily-stats, apply-reminder, feedback-review. Their
body strings must avoid pipeline-internal jargon. Operator diagnostics
(health-check, ci-check, scoreboard) keep their technical detail; only
the titles are branded.

Post-#537: per-command modules live in `findajob.notifications.*`; the
test monkeypatches the `send` binding inside each command module's
namespace (which is where the `from .ntfy import send` reference lives).
"""

from __future__ import annotations

import inspect
import re
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NOTIFY_PKG = REPO / "src" / "findajob" / "notifications"

# Pipeline-internal terms that must not appear in user-facing notification bodies.
# Operator-facing subcommands (health-check, ci-check, scoreboard) are exempt.
USER_FACING_JARGON = (
    "manual_review",
    "feedback_log",
    "Sheet1",
    "ntfy",
    "DeepSeek",
    "GPT-",
    "Materials drafted",
    "False positives",
    "False positive",
    "Top FP",
    "Prefilter candidates",
    "triage backlog",
    "Flag for Prep",
    "Dashboard",  # web /board page name; surface as "matches" / "review" instead
)

BRAND = "💼 findajob"


@pytest.fixture
def fixtured_db(tmp_path, monkeypatch):
    """Set up a tmp pipeline.db, point ntfy.DB_PATH at it, and capture send() calls."""
    db = tmp_path / "pipeline.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            company TEXT NOT NULL DEFAULT '',
            stage TEXT DEFAULT 'scored',
            relevance_score INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            stage_updated TEXT,
            apply_flag INTEGER DEFAULT 0,
            dupe_of TEXT DEFAULT '',
            prep_folder_path TEXT,
            synthetic INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE feedback_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            reason TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        INSERT INTO jobs (id, title, stage, relevance_score) VALUES ('a','t','scored',8);
        INSERT INTO jobs (id, title, stage, relevance_score, apply_flag)
            VALUES ('b','t','scored',9,1);
        INSERT INTO jobs (id, title, stage) VALUES ('c','t','materials_drafted');
        INSERT INTO jobs (id, title, stage) VALUES ('d','t','applied');
        INSERT INTO jobs (id, title, stage) VALUES ('e','t','rejected');
        INSERT INTO jobs (id, title, stage) VALUES ('f','t','waitlisted');
        INSERT INTO jobs (id, title, stage) VALUES ('g','t','manual_review');
        """
    )
    conn.commit()
    conn.close()

    from findajob.notifications import ntfy

    monkeypatch.setattr(ntfy, "DB_PATH", str(db))

    sent: list[dict] = []

    def fake_send(title, body, **kw):
        sent.append({"title": title, "body": body, **kw})

    return db, sent, fake_send


def _assert_no_jargon(body: str, label: str):
    for token in USER_FACING_JARGON:
        assert token not in body, f"{label} body contains pipeline jargon {token!r}: {body!r}"


def test_daily_stats_is_branded_and_plain_english(fixtured_db, monkeypatch):
    _db, sent, fake_send = fixtured_db
    from findajob.notifications import daily_stats

    monkeypatch.setattr(daily_stats, "send", fake_send)
    daily_stats.cmd_daily_stats()

    assert sent, "send() not called"
    msg = sent[0]
    assert msg["title"].startswith(BRAND), f"title not branded: {msg['title']!r}"
    _assert_no_jargon(msg["body"], "daily-stats")


def test_apply_reminder_is_branded_and_plain_english(fixtured_db, monkeypatch):
    _db, sent, fake_send = fixtured_db
    from findajob.notifications import apply_reminder

    monkeypatch.setattr(apply_reminder, "send", fake_send)
    apply_reminder.cmd_apply_reminder()

    assert sent, "send() not called"
    msg = sent[0]
    assert msg["title"].startswith(BRAND), f"title not branded: {msg['title']!r}"
    _assert_no_jargon(msg["body"], "apply-reminder")


def test_apply_reminder_quips_have_no_tech_specific_jokes():
    quips_to_keep = (
        "The perfect resume is the enemy of the submitted one. Go click Apply.",
        "Your resume can't apply to itself. We checked. Open a tab.",
    )
    from findajob.notifications import apply_reminder

    src = inspect.getsource(apply_reminder)
    for token in ("DeepSeek", "GPT-", "ChatGPT", "Claude ", "the pipeline", "the Dashboard"):
        assert token not in src, f"apply_reminder source contains tech-specific token {token!r}"
    for q in quips_to_keep:
        assert q in src, f"expected quip missing: {q!r}"


def test_feedback_review_is_branded_and_plain_english(fixtured_db, monkeypatch):
    db, sent, fake_send = fixtured_db
    from findajob.notifications import feedback_review

    monkeypatch.setattr(feedback_review, "send", fake_send)

    # cmd_feedback_review only sends when feedback_log has >= 10 entries.
    conn = sqlite3.connect(db)
    for i in range(15):
        conn.execute(
            "INSERT INTO feedback_log (job_id, reason) VALUES (?, ?)",
            (f"j{i}", "too senior"),
        )
    conn.commit()
    conn.close()

    feedback_review.cmd_feedback_review()
    assert sent, "send() not called for >=10 feedback entries"
    msg = sent[0]
    assert msg["title"].startswith(BRAND), f"title not branded: {msg['title']!r}"
    _assert_no_jargon(msg["body"], "feedback-review")


def test_all_send_titles_are_branded():
    """Every send() call site (except the passthrough send-raw) must use the
    💼 findajob brand prefix on its title literal.

    Post-extraction: scan all per-command modules in
    `src/findajob/notifications/`. send_raw.py is exempt (passthrough).
    """
    titles: list[str] = []
    for module_path in NOTIFY_PKG.glob("*.py"):
        if module_path.name in {"__init__.py", "cli.py", "ntfy.py", "send_raw.py"}:
            continue
        text = module_path.read_text()
        # Match send("Title", ...) or send(\n    "Title",\n    ...)
        titles.extend(re.findall(r'send\(\s*["\']([^"\']+)["\']', text))

    assert titles, "regex failed to find any send() title literals across notification modules"
    unbranded = [t for t in titles if not t.startswith(BRAND)]
    assert not unbranded, f"unbranded send() titles: {unbranded}"
