"""Cost-logging regression test for scripts/rescore_all.py (#470 update).

After #470 the local score_job duplicate was removed; rescore_all.py
imports the canonical findajob.scoring.score_job which returns the
3-tuple (parsed, latency_ms, completion). cost_log rows now populate
cost_usd via cost_usd_override=completion.cost_usd on the LLM path
(authoritative usage.cost from the wrapper) and via the heuristic on
the prefilter path (completion=None, exactly 0.0 from empty inputs).
"""

from __future__ import annotations

from unittest.mock import patch

from scripts.rescore_all import score_job

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

    Caller passes cost_usd_override=None to log_call which falls back
    to the heuristic — for empty inputs that computes 0.0.
    """
    with patch("findajob.scoring.complete") as mock_complete:
        # A title that hits the hard-reject prefilter — wrapper not called.
        result = score_job("Software Engineer", "Acme", "Remote", "", candidate_profile="")
    assert len(result) == 3
    parsed, latency_ms, completion = result
    assert latency_ms == 0
    assert completion is None
    assert not mock_complete.called
