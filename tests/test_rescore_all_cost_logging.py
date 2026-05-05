"""Cost-logging regression test for scripts/rescore_all.py.

The existing INSERT at line 271 (pre-#48) wrote rows with NULL cost_usd.
After #48, every rescore row should have cost_usd populated — non-zero
on the LLM path, exactly 0.0 on the prefilter path (no LLM call to bill).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from scripts.rescore_all import score_job

VALID_LLM_JSON = (
    '{"relevance_score": 7, "interview_likelihood": 6, '
    '"strengths_alignment": "good fit", "industry_sector": "infra", '
    '"comp_estimate": "competitive", "ai_notes": "ok", '
    '"remote_status": "Remote", "score_status": "scored"}'
)


def _stub_subprocess(stdout: str, returncode: int = 0):
    def _run(*args, **kwargs):
        completed = MagicMock()
        completed.stdout = stdout
        completed.stderr = ""
        completed.returncode = returncode
        return completed

    return _run


def test_score_job_returns_prompt_and_output_for_llm_path() -> None:
    """LLM path: score_job returns (parsed, latency_ms, prompt, raw_output)
    so callers can populate cost_log.cost_usd via log_call.
    """
    with patch("scripts.rescore_all.subprocess.run", _stub_subprocess(VALID_LLM_JSON)):
        result = score_job(
            "Senior Engineer",
            "Acme",
            "Remote",
            "Strong JD with detailed responsibilities " * 50,  # > 200 chars to be jd_is_usable
            candidate_profile="profile",
        )
    # Must return 4-tuple: parsed, latency_ms, prompt, raw_output
    assert len(result) == 4
    parsed, latency_ms, prompt, raw_output = result
    assert parsed.get("relevance_score") == 7
    assert latency_ms >= 0
    assert "CANDIDATE PROFILE" in prompt
    assert "relevance_score" in raw_output


def test_score_job_returns_empty_strings_on_prefilter_path() -> None:
    """Prefilter path: no LLM call was made, so prompt + raw_output are
    empty strings (caller still writes a cost_log row, but cost_usd will
    compute to 0.0 from the empty inputs).
    """
    with patch("scripts.rescore_all.subprocess.run") as mock_sub:
        # A title that hits the hard-reject prefilter — no subprocess call.
        # Fixture prefilter at tests/fixtures/config/prefilter_rules.yaml hard-
        # rejects "software engineer" patterns.
        result = score_job("Software Engineer", "Acme", "Remote", "", candidate_profile="")
    assert len(result) == 4
    parsed, latency_ms, prompt, raw_output = result
    # Prefilter returns latency 0
    assert latency_ms == 0
    assert prompt == ""
    assert raw_output == ""
    # subprocess was NOT called (prefilter short-circuited)
    assert not mock_sub.called
