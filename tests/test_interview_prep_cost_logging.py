"""Cost-logging test for the aichat() helper in scripts/interview_prep.py."""

from __future__ import annotations

import sqlite3
import subprocess
from unittest.mock import MagicMock, patch

from scripts.interview_prep import aichat

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


def test_interview_prep_aichat_writes_cost_log_row() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)

    with patch("scripts.interview_prep.subprocess.run", _stub_subprocess("# panel prep body")):
        aichat("interview_prep", "draft prep", conn=conn, job_id="job-iv")

    rows = conn.execute("SELECT operation, model, cost_usd, job_id FROM cost_log").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["operation"] == "interview_prep"
    assert row["model"].startswith("openrouter:")
    assert row["cost_usd"] is not None and row["cost_usd"] > 0
    assert row["job_id"] == "job-iv"


def test_interview_prep_aichat_without_conn_skips_logging() -> None:
    with patch("scripts.interview_prep.subprocess.run", _stub_subprocess("# body")):
        result = aichat("interview_prep", "p")
    assert "body" in result
