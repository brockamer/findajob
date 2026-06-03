"""Cost-logging tests for the run_role() helper.

Post-#537 (M3 cleanup PR): `run_role()` was consolidated into
`findajob.llm.role_runner` from byte-equivalent copies that lived in
`findajob.{prep,interview}.role_runner` after the import-only extractions.
The test surface is unchanged — only the import path and the patch
target moved.

HTTP-mocked tests for the wrapper-driven run_role() helper after the Phase 2 port.
Each call to run_role() writes a cost_log row with API-authoritative cost_usd
from response.usage.cost. Each call IS a separate billable LLM hit, so the
briefing_writer retry path must produce 2 rows, not 1.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
from io import BytesIO
from unittest.mock import patch

from findajob.llm.openrouter import OpenRouterError
from findajob.llm.role_runner import run_role

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
    content="# briefing body",
    cost=0.012345,
    prompt_tokens=2000,
    completion_tokens=500,
    cached_tokens=0,
    finish_reason=None,
):
    body = json.dumps(
        {
            "id": "gen-test-1",
            "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
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


def test_run_role_writes_cost_log_with_api_cost():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)

    with (
        patch.dict(os.environ, _FAKE_API_KEY),
        patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            return_value=_stub_openrouter_response(cost=0.04),
        ),
    ):
        out = run_role("briefing_writer", "format this", conn=conn, job_id="job-abc")

    assert out == "# briefing body"
    rows = conn.execute(
        "SELECT operation, cost_usd, input_tokens, output_tokens, success, job_id FROM cost_log"
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["operation"] == "briefing_writer"
    assert row["cost_usd"] == 0.04
    assert row["input_tokens"] == 2000
    assert row["output_tokens"] == 500
    assert row["success"] == 1
    assert row["job_id"] == "job-abc"


def test_run_role_briefing_retry_writes_two_rows():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)
    with (
        patch.dict(os.environ, _FAKE_API_KEY),
        patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            return_value=_stub_openrouter_response(),
        ),
    ):
        run_role("briefing_writer", "format this", conn=conn, job_id="job-xyz")
    with (
        patch.dict(os.environ, _FAKE_API_KEY),
        patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            return_value=_stub_openrouter_response(),
        ),
    ):
        run_role("briefing_writer", "format this", conn=conn, job_id="job-xyz")
    rows = conn.execute("SELECT operation FROM cost_log").fetchall()
    assert len(rows) == 2


def test_run_role_does_not_write_on_wrapper_error():
    """When openrouter.complete() raises, no cost_log row is written and the helper returns ''."""
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
        out = run_role("briefing_writer", "format this", conn=conn, job_id="job-abc")
    assert out == ""
    rows = conn.execute("SELECT operation FROM cost_log").fetchall()
    assert len(rows) == 0


def test_run_role_logs_cost_on_empty_output():
    """#955: a billed-but-empty response (content="" → text strips to "")
    still incurred cost. The cost_log row must be written even though the
    returned text is falsy — the old ``if conn is not None and text:`` guard
    silently dropped these rows, under-counting spend_this_month()."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)

    with (
        patch.dict(os.environ, _FAKE_API_KEY),
        patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            return_value=_stub_openrouter_response(content="", cost=0.02),
        ),
    ):
        out = run_role("briefing_writer", "format this", conn=conn, job_id="job-empty")

    assert out == ""
    rows = conn.execute("SELECT cost_usd, success FROM cost_log").fetchall()
    assert len(rows) == 1, "billed-but-empty response must still write a cost_log row"
    assert rows[0]["cost_usd"] == 0.02
    assert rows[0]["success"] == 1  # the HTTP call succeeded; it just returned empty content


def test_run_role_logs_cost_on_partial_truncation_with_content():
    """#955 (AC #1 coverage): a partial truncation — finish_reason=length WITH
    non-empty content — returns via the success path and must write a cost_log
    row with its billed cost. (The null-content truncation is the error-path
    case covered separately.)"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)

    with (
        patch.dict(os.environ, _FAKE_API_KEY),
        patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            return_value=_stub_openrouter_response(content="partial answer", cost=0.03, finish_reason="length"),
        ),
    ):
        out = run_role("briefing_writer", "format this", conn=conn, job_id="job-trunc")

    assert out == "partial answer"
    rows = conn.execute("SELECT cost_usd, success FROM cost_log").fetchall()
    assert len(rows) == 1, "a truncated-but-non-empty response must still write a cost_log row"
    assert rows[0]["cost_usd"] == 0.03
    assert rows[0]["success"] == 1


def test_run_role_logs_cost_on_null_content_error():
    """#955: a null-content (finish_reason=length) response raises
    OpenRouterError, but OpenRouter still billed for the consumed tokens.
    run_role must record that billed cost (success=0) instead of returning
    "" silently and dropping the spend."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)

    with (
        patch.dict(os.environ, _FAKE_API_KEY),
        patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            return_value=_stub_openrouter_response(content=None, cost=0.05),
        ),
    ):
        out = run_role("briefing_writer", "format this", conn=conn, job_id="job-null")

    assert out == ""
    rows = conn.execute("SELECT cost_usd, success, job_id FROM cost_log").fetchall()
    assert len(rows) == 1, "billed-but-failed (null content) response must write a cost_log row"
    assert rows[0]["cost_usd"] == 0.05
    assert rows[0]["success"] == 0  # the call failed (unusable content) but was billed
    assert rows[0]["job_id"] == "job-null"


def test_run_role_no_cost_row_when_error_has_no_usage():
    """#955 negative assertion: an OpenRouterError that never billed
    (cost_usd is None — e.g. a network/auth failure before any usage
    envelope) must NOT write a cost_log row. Logging a heuristic estimate
    here would over-count spend for genuinely free failures."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)

    ore = OpenRouterError("network down", kind="network")  # cost_usd defaults to None
    with patch("findajob.llm.role_runner.complete", side_effect=ore):
        out = run_role("briefing_writer", "format this", conn=conn, job_id="job-net")

    assert out == ""
    rows = conn.execute("SELECT operation FROM cost_log").fetchall()
    assert len(rows) == 0, "a no-usage failure must not write a phantom cost row"


def test_spend_this_month_reflects_billed_but_failed_call():
    """#955 AC #3 (literal): the billed cost of a failed prep call flows all the
    way through to spend_this_month() — the number the spend ceiling reads — not
    just into a cost_log row in isolation."""
    from findajob.cost_rollups import spend_this_month

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)
    assert spend_this_month(conn) == 0.0

    with (
        patch.dict(os.environ, _FAKE_API_KEY),
        patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            return_value=_stub_openrouter_response(content=None, cost=0.05),
        ),
    ):
        run_role("briefing_writer", "format this", conn=conn, job_id="job-spend")

    assert abs(spend_this_month(conn) - 0.05) < 1e-9, "billed-but-failed prep cost must reach spend_this_month()"


def test_run_role_passes_cached_prefix_to_wrapper():
    """The 4 Opus invocations pass cached_prefix + pin_provider; verify the helper threads them."""
    captured = {}

    def _fake_complete(role, prompt, **kwargs):
        captured.update(role=role, prompt=prompt, **kwargs)
        from findajob.llm.openrouter import CompletionResult

        return CompletionResult(
            text="ok",
            prompt_tokens=10,
            completion_tokens=5,
            cached_tokens=0,
            cost_usd=0.001,
            generation_id="g",
        )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)
    with patch("findajob.llm.role_runner.complete", _fake_complete):
        run_role(
            "resume_tailor",
            "tailor this",
            conn=conn,
            job_id="j-1",
            cached_prefix="PROFILE+MASTER+JD",
            pin_provider="anthropic",
        )
    assert captured["role"] == "resume_tailor"
    assert captured["cached_prefix"] == "PROFILE+MASTER+JD"
    assert captured["pin_provider"] == "anthropic"
