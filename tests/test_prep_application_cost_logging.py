"""Cost-logging tests for the aichat() helper in scripts/prep_application.py.

The helper is one of 5 production aichat-ng wrap targets in #48. When called
with ``conn`` and ``job_id``, it writes a cost_log row after each successful
subprocess return. Each call IS a separate billable LLM hit, so the
briefing_writer retry path (line 291) must produce 2 rows, not 1.
"""

from __future__ import annotations

import sqlite3
import subprocess
from unittest.mock import MagicMock, patch

from scripts.prep_application import aichat

COST_LOG_SCHEMA = """
CREATE TABLE cost_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    operation TEXT NOT NULL,
    model TEXT NOT NULL,
    latency_ms INTEGER,
    success INTEGER DEFAULT 1,
    error_message TEXT,
    logged_at TEXT DEFAULT (datetime('now')),
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL
);
"""


def _stub_subprocess(stdout: str, returncode: int = 0):
    completed = MagicMock(spec=subprocess.CompletedProcess)
    completed.stdout = stdout
    completed.stderr = ""
    completed.returncode = returncode
    return MagicMock(return_value=completed)


def test_aichat_writes_cost_log_row_on_success() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)

    with patch("scripts.prep_application.subprocess.run", _stub_subprocess("# tailored resume body")):
        aichat("resume_tailor", "draft a resume", conn=conn, job_id="job-abc")

    rows = conn.execute("SELECT operation, model, cost_usd, success, job_id FROM cost_log").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["operation"] == "resume_tailor"
    assert row["model"].startswith("openrouter:")
    assert row["cost_usd"] is not None and row["cost_usd"] > 0
    assert row["success"] == 1
    assert row["job_id"] == "job-abc"


def test_aichat_briefing_retry_writes_two_rows() -> None:
    """The briefing_writer retry path at prep_application.py:291 calls aichat()
    twice for the same role. Each call is a billable LLM hit, so we want 2
    cost_log rows — never deduplicate.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)

    with patch("scripts.prep_application.subprocess.run", _stub_subprocess("# briefing body")):
        # Simulate the retry: call aichat twice with the same role + prompt
        aichat("briefing_writer", "format this", conn=conn, job_id="job-xyz")
        aichat("briefing_writer", "format this", conn=conn, job_id="job-xyz")

    rows = conn.execute("SELECT operation FROM cost_log").fetchall()
    assert len(rows) == 2
    assert all(r["operation"] == "briefing_writer" for r in rows)


def test_aichat_does_not_write_on_subprocess_failure() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)

    with patch("scripts.prep_application.subprocess.run", _stub_subprocess("", returncode=1)):
        aichat("resume_tailor", "prompt", conn=conn, job_id="job-fail")

    count = conn.execute("SELECT COUNT(*) FROM cost_log").fetchone()[0]
    assert count == 0


def test_aichat_without_conn_skips_cost_logging() -> None:
    """Backwards-compat: callers that don't pass conn (diag scripts, future
    callers) must not crash on the missing parameter.
    """
    with patch("scripts.prep_application.subprocess.run", _stub_subprocess("# body")):
        result = aichat("resume_tailor", "prompt")
    assert "body" in result
