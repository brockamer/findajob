"""Cost-logging regression test for scripts/rescore_all.py (#470 update).

After #470 the local score_job duplicate was removed; rescore_all.py
imports the canonical findajob.scoring.score_job which returns the
3-tuple (parsed, latency_ms, completion). cost_log rows now populate
cost_usd via cost_usd_override=completion.cost_usd on the LLM path
(authoritative usage.cost from the wrapper) and a real zero on the
prefilter path (completion=None) — not a chars/4 estimate (#1145).
"""

from __future__ import annotations

import sqlite3
import sys
from unittest.mock import patch

import scripts.rescore_all as rescore_all
from scripts.rescore_all import score_job
from tests.conftest import init_test_db

VALID_LLM_JSON = (
    '{"relevance_score": 7, "interview_likelihood": 6, '
    '"strengths_alignment": "good fit", "industry_sector": "infra", '
    '"comp_estimate": "competitive", "ai_notes": "ok", '
    '"remote_status": "Remote", "score_status": "scored"}'
)


class _MockCompletion:
    """Minimal CompletionResult double — only fields the score_job path reads."""

    def __init__(self, text: str, cost_usd: float = 0.001234) -> None:
        self.text = text
        self.cost_usd = cost_usd
        self.prompt_tokens = 100
        self.completion_tokens = 20
        self.cached_tokens = 0
        self.generation_id = "gen-test"


def test_score_job_returns_three_tuple_on_llm_path() -> None:
    """LLM path: score_job returns (parsed, latency_ms, completion).

    completion is non-None so callers can write cost_usd_override.
    """
    with patch(
        "findajob.scoring.complete",
        return_value=_MockCompletion(VALID_LLM_JSON),
    ):
        result = score_job(
            "Senior Engineer",
            "Acme",
            "Remote",
            "Strong JD with detailed responsibilities " * 50,
            candidate_profile="profile",
        )
    assert len(result) == 3
    parsed, latency_ms, completion = result
    assert parsed.get("relevance_score") == 7
    assert latency_ms >= 0
    assert completion is not None
    assert completion.cost_usd == 0.001234


def test_score_job_returns_three_tuple_on_prefilter_path() -> None:
    """Prefilter path: completion is None (no LLM call was made).

    The caller (rescore_all.py's scoring loop) passes a real 0.0 / 0 / 0
    to log_call's overrides for this case — not a heuristic estimate (#1145).
    """
    with patch("findajob.scoring.complete") as mock_complete:
        # A title that hits the hard-reject prefilter — wrapper not called.
        result = score_job("Software Engineer", "Acme", "Remote", "", candidate_profile="")
    assert len(result) == 3
    parsed, latency_ms, completion = result
    assert latency_ms == 0
    assert completion is None
    assert not mock_complete.called


def test_prefilter_zero_cost_rescore(tmp_path, monkeypatch) -> None:
    """A prefilter hit (completion=None) through rescore_all.main() logs cost_usd=0.0,
    input_tokens=0, output_tokens=0 — not a chars/4 estimate (#1145)."""
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO jobs (id, fingerprint, url, title, company, location, source, raw_jd_text, stage, "
        "scored_by, company_tier) VALUES ('j1', 'fp1', 'https://example.com/1', 'Office Manager', 'Acme', "
        "'Remote', 'manual', 'A usable job description.', 'scored', 'llm', 'unknown')"
    )
    conn.commit()
    conn.close()
    profile = tmp_path / "profile.md"
    profile.write_text("Test profile.\n")
    scored = {
        "score_status": "scored",
        "relevance_score": 1,
        "interview_likelihood": 1,
        "strengths_alignment": "Hard reject — title is outside candidate domain.",
        "industry_sector": None,
        "comp_estimate": None,
        "ai_notes": "Pre-filter hard reject",
        "score_flag_reason": "Pre-filter hard reject",
        "remote_status": "Unknown",
        "scored_by": "prefilter_stage1",
        "company_tier": "unknown",
    }
    monkeypatch.setattr(rescore_all, "DB_PATH", str(db))
    monkeypatch.setattr(rescore_all, "PROFILE_PATH", str(profile))
    monkeypatch.setattr(rescore_all, "score_job", lambda *a, **kw: (scored, 0, None))
    monkeypatch.setattr(sys, "argv", ["rescore_all.py"])

    rescore_all.main()

    row = (
        sqlite3.connect(db)
        .execute("SELECT cost_usd, input_tokens, output_tokens FROM cost_log WHERE job_id='j1' AND operation='rescore'")
        .fetchone()
    )
    assert row == (0.0, 0, 0)
