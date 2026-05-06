"""Cost-logging tests for run_role() in scripts/interview_prep.py.

HTTP-mocked tests for the wrapper-driven run_role() helper after the Phase 2 port.
Each call to run_role() writes a cost_log row with API-authoritative cost_usd
from response.usage.cost.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
from io import BytesIO
from unittest.mock import patch

from scripts.interview_prep import run_role

# Fake key satisfies the OPENROUTER_API_KEY guard in openrouter.complete() without
# a real network call — used in conjunction with the urlopen mock.
_FAKE_API_KEY = {"OPENROUTER_API_KEY": "sk-or-v1-test"}

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


def _stub_openrouter_response(
    *,
    content="# interview prep body",
    cost=0.035,
    prompt_tokens=3000,
    completion_tokens=800,
    cached_tokens=0,
):
    body = json.dumps(
        {
            "id": "gen-test-iv-1",
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost": cost,
                "prompt_tokens_details": {"cached_tokens": cached_tokens},
            },
        }
    ).encode("utf-8")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return body

    return _Resp()


def test_interview_prep_writes_cost_log_with_api_cost():
    """run_role() writes a cost_log row with cost_usd from response.usage.cost."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)

    with (
        patch.dict(os.environ, _FAKE_API_KEY),
        patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            return_value=_stub_openrouter_response(cost=0.035),
        ),
    ):
        out = run_role("interview_prep", "prepare me for this interview", conn=conn, job_id="job-iv-1")

    assert out == "# interview prep body"
    rows = conn.execute(
        "SELECT operation, cost_usd, input_tokens, output_tokens, success, job_id FROM cost_log"
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["operation"] == "interview_prep"
    assert row["cost_usd"] == 0.035
    assert row["input_tokens"] == 3000
    assert row["output_tokens"] == 800
    assert row["success"] == 1
    assert row["job_id"] == "job-iv-1"


def test_interview_prep_passes_cached_prefix():
    """run_role() threads cached_prefix (profile+master) and pin_provider='anthropic'."""
    captured = {}

    def _fake_complete(role, prompt, **kwargs):
        captured.update(role=role, prompt=prompt, **kwargs)
        from findajob.llm.openrouter import CompletionResult

        return CompletionResult(
            text="# panel prep content",
            prompt_tokens=3000,
            completion_tokens=800,
            cached_tokens=1500,
            cost_usd=0.02,
            generation_id="g-iv",
        )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)

    profile_content = "PROFILE: experienced ops engineer"
    master_content = "MASTER RESUME: 20 years DC infra"
    cached_prefix = profile_content + "\n\n" + master_content

    with patch("scripts.interview_prep.complete", _fake_complete):
        run_role(
            "interview_prep",
            "Company: Acme\nTitle: Director\nJD: ...\nBRIEFING: ...",
            conn=conn,
            job_id="job-iv-2",
            cached_prefix=cached_prefix,
            pin_provider="anthropic",
        )

    assert captured["role"] == "interview_prep"
    assert captured["pin_provider"] == "anthropic"
    prefix = captured.get("cached_prefix", "")
    assert profile_content in prefix
    assert master_content in prefix
    # briefing and per-job context must NOT be in cached_prefix
    assert "BRIEFING" not in prefix
    assert "JD" not in prefix


def test_interview_prep_does_not_write_on_wrapper_error():
    """When openrouter.complete() raises HTTPError, no cost_log row is written."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)

    with (
        patch.dict(os.environ, _FAKE_API_KEY),
        patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(url="x", code=401, msg="auth", hdrs=None, fp=BytesIO(b"")),
        ),
    ):
        out = run_role("interview_prep", "prepare me", conn=conn, job_id="job-iv-err")

    assert out == ""
    rows = conn.execute("SELECT operation FROM cost_log").fetchall()
    assert len(rows) == 0
