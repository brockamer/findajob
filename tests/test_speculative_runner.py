"""Tests for findajob.speculative.runner — orchestrates briefing + role-synth.

runner.run_research() calls openrouter.complete() directly. All mocks target
the wrapper or the HTTP boundary.

We assert the runner:
1. Reads the speculative_requests row and candidate context files
2. Calls the briefing role (candidate_led_briefing), then the synth role
   (speculative_roles_synth) with the briefing as input
3. Writes briefing.md to a freshly-created folder
4. Updates the request row to status='ready_for_review' with briefing_md +
   role_cards_json + briefing_folder + research_completed_at populated
5. On any failure, sets status='failed' + error_message
6. candidate_led_briefing has NO cached_prefix / pin_provider (Perplexity ignores cache_control)
7. speculative_roles_synth has cached_prefix=profile+resume AND pin_provider="anthropic"
"""

from __future__ import annotations

import json
import os
import sqlite3
from unittest.mock import patch

from findajob.llm.openrouter import CompletionResult, OpenRouterError
from findajob.speculative.runner import run_research

SCHEMA = """
CREATE TABLE speculative_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    hint TEXT,
    personal_notes TEXT,
    status TEXT NOT NULL DEFAULT 'researching',
    error_message TEXT,
    briefing_md TEXT,
    role_cards_json TEXT,
    briefing_folder TEXT,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
    research_completed_at TEXT,
    approved_at TEXT,
    approved_role_count INTEGER,
    briefing_prompt_version TEXT,
    synth_prompt_version TEXT
);

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

# Fake key satisfies the OPENROUTER_API_KEY guard in openrouter.complete() without
# a real network call — used in conjunction with the urlopen mock.
_FAKE_API_KEY = {"OPENROUTER_API_KEY": "sk-or-v1-test"}


def _seed(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "INSERT INTO speculative_requests (company, hint, personal_notes, status) VALUES (?, ?, ?, 'researching')",
        ("PSIQuantum", "advanced computing infrastructure", None),
    )
    conn.commit()
    return cur.lastrowid


def _ok_briefing() -> str:
    return "# briefing\n\n## 🏢 Company Snapshot\nbody"


def _ok_role_cards() -> str:
    return json.dumps(
        [
            {
                "title": "Critical Infrastructure Engineer",
                "description": "Own GPU cluster bring-up.",
                "why_this_fits_candidate": "FTW Lab analog.",
                "likely_team_or_org": "Site Operations",
                "suggested_contact_type": "hiring_manager",
            }
        ]
    )


def _make_completion_result(text: str, cost: float = 0.02) -> CompletionResult:
    return CompletionResult(
        text=text,
        prompt_tokens=500,
        completion_tokens=200,
        cached_tokens=0,
        cost_usd=cost,
        generation_id="gen-test-1",
    )


def _stub_openrouter_response(
    *,
    content: str,
    cost: float = 0.02,
    prompt_tokens: int = 500,
    completion_tokens: int = 200,
    cached_tokens: int = 0,
):
    """HTTP-level stub: returns an object that mimics an open urllib response."""
    body = json.dumps(
        {
            "id": "gen-test-1",
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


# ---------------------------------------------------------------------------
# Orchestration tests — patch openrouter.complete()
# ---------------------------------------------------------------------------


def test_run_research_happy_path(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    req_id = _seed(conn)

    profile = tmp_path / "profile.md"
    profile.write_text("candidate profile body")
    resume = tmp_path / "master_resume.md"
    resume.write_text("master resume body")

    call_log = []

    def _fake_complete(role, prompt, **kwargs):
        call_log.append(role)
        if role == "candidate_led_briefing":
            return _make_completion_result(_ok_briefing())
        return _make_completion_result(_ok_role_cards())

    with patch("findajob.speculative.runner.complete", _fake_complete):
        run_research(
            conn=conn,
            request_id=req_id,
            profile_path=profile,
            master_resume_path=resume,
            companies_dir=tmp_path / "companies",
        )

    assert call_log == ["candidate_led_briefing", "speculative_roles_synth"]

    # Row updated
    row = conn.execute("SELECT * FROM speculative_requests WHERE id=?", (req_id,)).fetchone()
    assert row["status"] == "ready_for_review"
    assert row["briefing_md"] == _ok_briefing()
    assert row["role_cards_json"] == _ok_role_cards()
    assert row["briefing_folder"] is not None
    assert row["research_completed_at"] is not None

    # Folder + briefing.md exist on disk
    folder = tmp_path / "companies" / row["briefing_folder"]
    assert folder.exists()
    assert (folder / "briefing.md").read_text() == _ok_briefing()


def test_run_research_briefing_failure_sets_status_failed(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    req_id = _seed(conn)

    profile = tmp_path / "profile.md"
    profile.write_text("p")
    resume = tmp_path / "master_resume.md"
    resume.write_text("r")

    with patch(
        "findajob.speculative.runner.complete",
        side_effect=OpenRouterError("rate limited", kind="rate_limit"),
    ):
        run_research(
            conn=conn,
            request_id=req_id,
            profile_path=profile,
            master_resume_path=resume,
            companies_dir=tmp_path / "companies",
        )

    row = conn.execute("SELECT * FROM speculative_requests WHERE id=?", (req_id,)).fetchone()
    assert row["status"] == "failed"
    assert "rate limited" in (row["error_message"] or "")
    assert row["briefing_md"] is None
    assert row["role_cards_json"] is None


def test_run_research_synth_failure_preserves_briefing(tmp_path):
    """If briefing succeeds but role-synth fails, briefing_md is preserved
    in the row so a retry only re-runs the synth step."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    req_id = _seed(conn)

    profile = tmp_path / "profile.md"
    profile.write_text("p")
    resume = tmp_path / "master_resume.md"
    resume.write_text("r")

    call_count = [0]

    def _fake_complete(role, prompt, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return _make_completion_result(_ok_briefing())
        raise OpenRouterError("synth failed: invalid JSON", kind="malformed")

    with patch("findajob.speculative.runner.complete", _fake_complete):
        run_research(
            conn=conn,
            request_id=req_id,
            profile_path=profile,
            master_resume_path=resume,
            companies_dir=tmp_path / "companies",
        )

    row = conn.execute("SELECT * FROM speculative_requests WHERE id=?", (req_id,)).fetchone()
    assert row["status"] == "failed"
    assert row["briefing_md"] == _ok_briefing()
    assert row["role_cards_json"] is None
    assert "synth failed" in (row["error_message"] or "")


# ---------------------------------------------------------------------------
# Cost-log tests — API-authoritative cost, not heuristic
# ---------------------------------------------------------------------------


def test_run_research_writes_cost_log_for_both_stages(tmp_path):
    """Successful run_research writes one cost_log row per LLM stage:
    operation='candidate_led_briefing' and operation='speculative_roles_synth',
    both with API-authoritative cost_usd (not the char-heuristic).
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    req_id = _seed(conn)

    profile = tmp_path / "profile.md"
    profile.write_text("p")
    resume = tmp_path / "master_resume.md"
    resume.write_text("r")

    def _fake_complete(role, prompt, **kwargs):
        if role == "candidate_led_briefing":
            return _make_completion_result(_ok_briefing(), cost=0.05)
        return _make_completion_result(_ok_role_cards(), cost=0.03)

    with patch("findajob.speculative.runner.complete", _fake_complete):
        run_research(
            conn=conn,
            request_id=req_id,
            profile_path=profile,
            master_resume_path=resume,
            companies_dir=tmp_path / "companies",
        )

    rows = conn.execute("SELECT operation, model, cost_usd, success FROM cost_log ORDER BY id").fetchall()
    operations = [r["operation"] for r in rows]
    assert operations == ["candidate_led_briefing", "speculative_roles_synth"]
    costs = {r["operation"]: r["cost_usd"] for r in rows}
    assert costs["candidate_led_briefing"] == 0.05
    assert costs["speculative_roles_synth"] == 0.03
    for r in rows:
        assert r["model"].startswith("openrouter:")
        assert r["cost_usd"] is not None and r["cost_usd"] > 0
        assert r["success"] == 1


# ---------------------------------------------------------------------------
# Kwarg-capture: candidate_led_briefing — no caching (Perplexity)
# ---------------------------------------------------------------------------


def test_candidate_led_briefing_no_cache_args(tmp_path):
    """candidate_led_briefing routes through complete() with NO cached_prefix or
    pin_provider — Perplexity (sonar-deep-research) ignores cache_control."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    req_id = _seed(conn)

    profile = tmp_path / "profile.md"
    profile.write_text("candidate profile content")
    resume = tmp_path / "master_resume.md"
    resume.write_text("master resume content")

    briefing_captured: dict = {}
    call_count = [0]

    def _fake_complete(role, prompt, **kwargs):
        call_count[0] += 1
        if role == "candidate_led_briefing":
            briefing_captured.update(role=role, **kwargs)
            return _make_completion_result(_ok_briefing(), cost=0.04)
        return _make_completion_result(_ok_role_cards(), cost=0.02)

    with patch("findajob.speculative.runner.complete", _fake_complete):
        run_research(
            conn=conn,
            request_id=req_id,
            profile_path=profile,
            master_resume_path=resume,
            companies_dir=tmp_path / "companies",
        )

    assert briefing_captured.get("role") == "candidate_led_briefing"
    # Perplexity doesn't honor cache_control — these must NOT be passed.
    assert briefing_captured.get("cached_prefix") is None
    assert briefing_captured.get("pin_provider") is None

    # Verify cost_log row written for briefing stage with API cost
    rows = conn.execute("SELECT operation, cost_usd FROM cost_log WHERE operation='candidate_led_briefing'").fetchall()
    assert len(rows) == 1
    assert rows[0]["cost_usd"] == 0.04


# ---------------------------------------------------------------------------
# Kwarg-capture: speculative_roles_synth — profile+resume caching (Anthropic)
# ---------------------------------------------------------------------------


def test_speculative_roles_synth_passes_cached_prefix(tmp_path):
    """speculative_roles_synth passes cached_prefix=profile+master_resume and
    pin_provider="anthropic". Briefing varies per request so is passed in the
    prompt body, not the cached prefix. Only profile+master_resume are stable
    across requests and eligible for cross-request cache hits on Sonnet.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    req_id = _seed(conn)

    profile_text = "candidate profile content"
    resume_text = "master resume content"
    profile = tmp_path / "profile.md"
    profile.write_text(profile_text)
    resume = tmp_path / "master_resume.md"
    resume.write_text(resume_text)

    synth_captured: dict = {}

    def _fake_complete(role, prompt, **kwargs):
        if role == "speculative_roles_synth":
            synth_captured.update(role=role, prompt=prompt, **kwargs)
            return _make_completion_result(_ok_role_cards(), cost=0.03)
        return _make_completion_result(_ok_briefing(), cost=0.05)

    with patch("findajob.speculative.runner.complete", _fake_complete):
        run_research(
            conn=conn,
            request_id=req_id,
            profile_path=profile,
            master_resume_path=resume,
            companies_dir=tmp_path / "companies",
        )

    assert synth_captured.get("role") == "speculative_roles_synth"
    # Anthropic Sonnet honors cache_control — must pass cached_prefix and pin_provider.
    cached_prefix = synth_captured.get("cached_prefix")
    assert cached_prefix is not None, "cached_prefix must be set for speculative_roles_synth"
    assert profile_text in cached_prefix
    assert resume_text in cached_prefix
    assert synth_captured.get("pin_provider") == "anthropic"

    # The per-request briefing must appear in the prompt body, not the prefix
    prompt = synth_captured.get("prompt", "")
    assert _ok_briefing().strip() in prompt

    # Verify cost_log row written for synth stage with API cost
    rows = conn.execute("SELECT operation, cost_usd FROM cost_log WHERE operation='speculative_roles_synth'").fetchall()
    assert len(rows) == 1
    assert rows[0]["cost_usd"] == 0.03


# ---------------------------------------------------------------------------
# HTTP-boundary test (full wrapper integration via urlopen mock)
# ---------------------------------------------------------------------------


def test_speculative_runner_http_boundary(tmp_path):
    """End-to-end wrapper integration mocked at urlopen — both roles exercise
    the full HTTP path and cost flows into cost_log via API-authoritative values."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    req_id = _seed(conn)

    profile = tmp_path / "profile.md"
    profile.write_text("candidate profile body")
    resume = tmp_path / "master_resume.md"
    resume.write_text("master resume body")

    # Stateful sequence: first urlopen call is briefing, second is synth
    call_seq = [
        _stub_openrouter_response(content=_ok_briefing(), cost=0.05),
        _stub_openrouter_response(content=_ok_role_cards(), cost=0.03),
    ]
    seq_iter = iter(call_seq)

    with (
        patch.dict(os.environ, _FAKE_API_KEY),
        patch(
            "findajob.llm.openrouter.urllib.request.urlopen",
            side_effect=lambda *a, **kw: next(seq_iter),
        ),
    ):
        run_research(
            conn=conn,
            request_id=req_id,
            profile_path=profile,
            master_resume_path=resume,
            companies_dir=tmp_path / "companies",
        )

    row = conn.execute("SELECT * FROM speculative_requests WHERE id=?", (req_id,)).fetchone()
    assert row["status"] == "ready_for_review"
    assert row["briefing_md"] == _ok_briefing()

    rows = conn.execute("SELECT operation, cost_usd, input_tokens, output_tokens FROM cost_log ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0]["operation"] == "candidate_led_briefing"
    assert rows[0]["cost_usd"] == 0.05
    assert rows[0]["input_tokens"] == 500
    assert rows[0]["output_tokens"] == 200
    assert rows[1]["operation"] == "speculative_roles_synth"
    assert rows[1]["cost_usd"] == 0.03
