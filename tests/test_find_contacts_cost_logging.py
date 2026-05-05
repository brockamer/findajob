"""Cost-logging test for the outreach_drafter call in scripts/find_contacts.py."""

from __future__ import annotations

import sqlite3
import subprocess
from unittest.mock import MagicMock, patch

from scripts.find_contacts import generate_outreach

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


def test_generate_outreach_writes_cost_log_row(tmp_path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)

    contact = {
        "name": "Pat Smith",
        "title": "Director of Infra",
        "company": "Acme",
        "url": "https://linkedin.com/in/x",
        "connected_on": "2024-09-01",
    }
    outdir = str(tmp_path / "outreach")
    with patch("scripts.find_contacts.subprocess.run", _stub_subprocess("draft body text")):
        generate_outreach(
            contact,
            "Acme",
            "JD body",
            outdir,
            "Candidate Name",
            "PR",
            "20260505-120000",
            "Candidate Name",
            "voice samples body",
            is_synthetic=False,
            conn=conn,
            job_id="job-fc-1",
        )

    rows = conn.execute("SELECT operation, model, cost_usd, job_id FROM cost_log").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["operation"] == "outreach_drafter"
    assert row["model"].startswith("openrouter:")
    assert row["cost_usd"] is not None and row["cost_usd"] > 0
    assert row["job_id"] == "job-fc-1"


def test_generate_outreach_without_conn_works_normally(tmp_path) -> None:
    contact = {
        "name": "X",
        "title": "Y",
        "company": "Z",
        "url": "https://example.com",
        "connected_on": "2024-01-01",
    }
    outdir = str(tmp_path / "out")
    with patch("scripts.find_contacts.subprocess.run", _stub_subprocess("draft")):
        result = generate_outreach(contact, "Z", "JD", outdir, "B", "PR", "T", "B", "")
    # Returns a path string (not None) — backwards-compat preserved
    assert result and result.endswith(".txt")
