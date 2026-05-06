"""Cost-logging tests for generate_outreach() in scripts/find_contacts.py.

HTTP-mocked tests for the wrapper-driven generate_outreach() helper after the Phase 2 port.
Each call to generate_outreach() writes a cost_log row with API-authoritative cost_usd
from response.usage.cost.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
from io import BytesIO
from unittest.mock import patch

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

_CONTACT = {
    "name": "Pat Smith",
    "first": "Pat",
    "title": "Director of Infra",
    "company": "Acme",
    "url": "https://linkedin.com/in/x",
    "connected_on": "2024-09-01",
}


def _stub_openrouter_response(
    *,
    content="Hi Pat, reaching out about the role...",
    cost=0.025,
    prompt_tokens=2000,
    completion_tokens=300,
    cached_tokens=0,
):
    body = json.dumps(
        {
            "id": "gen-test-fc-1",
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


def test_generate_outreach_writes_cost_log_with_api_cost(tmp_path) -> None:
    """generate_outreach via wrapper writes cost_usd from response.usage.cost."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)

    from scripts.find_contacts import generate_outreach

    outdir = str(tmp_path / "outreach")
    with (
        patch.dict(os.environ, _FAKE_API_KEY),
        patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            return_value=_stub_openrouter_response(cost=0.025),
        ),
    ):
        generate_outreach(
            _CONTACT,
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
    assert row["cost_usd"] == 0.025
    assert row["job_id"] == "job-fc-1"


def test_generate_outreach_without_conn_works_normally(tmp_path) -> None:
    """generate_outreach with no conn still writes the outreach file."""
    from scripts.find_contacts import generate_outreach

    outdir = str(tmp_path / "out")
    with (
        patch.dict(os.environ, _FAKE_API_KEY),
        patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            return_value=_stub_openrouter_response(content="draft text"),
        ),
    ):
        result = generate_outreach(
            _CONTACT,
            "Acme",
            "JD body",
            outdir,
            "Candidate Name",
            "PR",
            "20260505-120000",
            "Candidate Name",
            "",
        )
    # Returns a path string (not None) — backwards-compat preserved
    assert result and result.endswith(".txt")


def test_generate_outreach_passes_cached_prefix() -> None:
    """Verify profile + voice samples are sent as cached_prefix with pin_provider='anthropic'."""
    captured = {}

    def _fake_complete(role, prompt, **kwargs):
        captured.update(role=role, prompt=prompt, **kwargs)
        from findajob.llm.openrouter import CompletionResult

        return CompletionResult(
            text="Hi Pat, ...",
            prompt_tokens=10,
            completion_tokens=5,
            cached_tokens=0,
            cost_usd=0.001,
            generation_id="g",
        )

    from scripts.find_contacts import generate_outreach

    with patch("scripts.find_contacts.complete", _fake_complete):
        generate_outreach(
            _CONTACT,
            "Acme",
            "JD body",
            "/tmp/test-outreach-dir-not-used",
            "Candidate Name",
            "PR",
            "20260505-120000",
            "Candidate Name",
            "voice samples content",
            conn=None,
        )

    assert captured["role"] == "outreach_drafter"
    assert captured["pin_provider"] == "anthropic"
    prefix = captured.get("cached_prefix", "")
    assert "VOICE SAMPLES" in prefix
    assert "voice samples content" in prefix


def test_generate_outreach_does_not_write_on_openrouter_error(tmp_path) -> None:
    """When openrouter.complete() raises, no cost_log row is written."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(COST_LOG_SCHEMA)

    from scripts.find_contacts import generate_outreach

    outdir = str(tmp_path / "outreach-err")
    with (
        patch.dict(os.environ, _FAKE_API_KEY),
        patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(url="x", code=401, msg="auth", hdrs=None, fp=BytesIO(b"")),
        ),
    ):
        result = generate_outreach(
            _CONTACT,
            "Acme",
            "JD body",
            outdir,
            "Candidate Name",
            "PR",
            "20260505-120000",
            "Candidate Name",
            "",
            conn=conn,
            job_id="job-fc-err",
        )

    # No cost_log row on failure
    rows = conn.execute("SELECT operation FROM cost_log").fetchall()
    assert len(rows) == 0
    # Returns None (or empty string) on error — caller gets no path
    assert not result
