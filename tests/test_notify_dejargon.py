"""De-jargon guard for notify.py user-facing subcommands (#151).

Three subcommands are direct user nudges (notifications a non-technical
beta tester reads): daily-stats, apply-reminder, feedback-review. Their
body strings must avoid pipeline-internal jargon. Operator diagnostics
(health-check, ci-check, scoreboard) keep their technical detail; only
the titles are branded.

This test imports scripts/notify.py with DB_PATH and send() monkeypatched,
calls each subcommand, and asserts on the captured send() args.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NOTIFY_PATH = REPO / "scripts" / "notify.py"

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


def _load_notify_module(db_path: Path):
    """Import scripts/notify.py as a module so we can monkeypatch its globals."""
    spec = importlib.util.spec_from_file_location("notify_under_test", NOTIFY_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["notify_under_test"] = mod
    spec.loader.exec_module(mod)
    mod.DB_PATH = str(db_path)
    return mod


@pytest.fixture
def notify_module(tmp_path, monkeypatch):
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

    mod = _load_notify_module(db)
    sent: list[dict] = []

    def fake_send(title, body, **kw):
        sent.append({"title": title, "body": body, **kw})

    monkeypatch.setattr(mod, "send", fake_send)
    mod._sent = sent  # type: ignore[attr-defined]
    return mod


def _assert_no_jargon(body: str, label: str):
    for token in USER_FACING_JARGON:
        assert token not in body, f"{label} body contains pipeline jargon {token!r}: {body!r}"


def test_daily_stats_is_branded_and_plain_english(notify_module):
    notify_module.cmd_daily_stats()
    assert notify_module._sent, "send() not called"
    msg = notify_module._sent[0]
    assert msg["title"].startswith(BRAND), f"title not branded: {msg['title']!r}"
    _assert_no_jargon(msg["body"], "daily-stats")


def test_apply_reminder_is_branded_and_plain_english(notify_module):
    notify_module.cmd_apply_reminder()
    assert notify_module._sent, "send() not called"
    msg = notify_module._sent[0]
    assert msg["title"].startswith(BRAND), f"title not branded: {msg['title']!r}"
    _assert_no_jargon(msg["body"], "apply-reminder")


def test_apply_reminder_quips_have_no_tech_specific_jokes(notify_module):
    quips = [
        "The perfect resume is the enemy of the submitted one. Go click Apply.",
        "Your resume can't apply to itself. We checked. Open a tab.",
    ]
    # Fetch the actual QUIPS list from the module to guard against any new entry
    # sneaking in tech-specific terms.
    import inspect

    src = inspect.getsource(notify_module.cmd_apply_reminder)
    for token in ("DeepSeek", "GPT-", "ChatGPT", "Claude ", "the pipeline", "the Dashboard"):
        assert token not in src, f"cmd_apply_reminder source contains tech-specific token {token!r}"
    # Sanity: at least the kept quips are still present.
    for q in quips:
        assert q in src, f"expected quip missing: {q!r}"


def test_feedback_review_is_branded_and_plain_english(notify_module):
    # cmd_feedback_review only sends when feedback_log has >= 10 entries.
    conn = sqlite3.connect(notify_module.DB_PATH)
    for i in range(15):
        conn.execute(
            "INSERT INTO feedback_log (job_id, reason) VALUES (?, ?)",
            (f"j{i}", "too senior"),
        )
    conn.commit()
    conn.close()

    notify_module.cmd_feedback_review()
    assert notify_module._sent, "send() not called for >=10 feedback entries"
    msg = notify_module._sent[0]
    assert msg["title"].startswith(BRAND), f"title not branded: {msg['title']!r}"
    _assert_no_jargon(msg["body"], "feedback-review")


def test_all_send_titles_are_branded():
    """Every send() call site (except the passthrough send-raw) must use the
    💼 findajob brand prefix on its title literal."""
    import re

    text = NOTIFY_PATH.read_text()
    # Capture the first string-literal arg to send(...) calls; matches both
    # `send("Title", ...)` and `send(\n    "Title",\n    ...)`.
    titles = re.findall(r'send\(\s*["\']([^"\']+)["\']', text)
    assert titles, "regex failed to find any send() title literals"
    unbranded = [t for t in titles if not t.startswith(BRAND)]
    assert not unbranded, f"unbranded send() titles: {unbranded}"
