"""Tests for spend-ceiling handling in findajob.onboarding.interview_runner (#671).

Verifies that LLMSpendCeilingExceeded raised by complete() is caught by
run_turn() and translated to InterviewRunnerError(kind="spend_ceiling_exceeded")
so the chat UI renders a user-actionable message rather than a 500.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from findajob.llm.openrouter import LLMSpendCeilingExceeded
from findajob.onboarding.interview_runner import InterviewRunnerError, run_turn


class TestRunTurnSpendCeiling:
    def test_lsce_translates_to_interview_runner_error(self):
        """run_turn() catches LSCE from complete() and raises InterviewRunnerError."""
        lsce = LLMSpendCeilingExceeded(ceiling_usd=50.0, current_sum_usd=55.0)

        with patch("findajob.onboarding.interview_runner.complete", side_effect=lsce):
            with pytest.raises(InterviewRunnerError) as exc_info:
                run_turn(api_key="sk-test", history=[], user_message="hello")

        exc = exc_info.value
        assert exc.kind == "spend_ceiling_exceeded"
        # User-facing message must include both dollar figures
        assert "50.00" in exc.user_message
        assert "55.00" in exc.user_message

    def test_lsce_message_is_user_actionable(self):
        """The translated message tells the user what to do, not just what failed."""
        lsce = LLMSpendCeilingExceeded(ceiling_usd=100.0, current_sum_usd=100.01)

        with patch("findajob.onboarding.interview_runner.complete", side_effect=lsce):
            with pytest.raises(InterviewRunnerError) as exc_info:
                run_turn(api_key="sk-test", history=[], user_message="hello")

        msg = exc_info.value.user_message
        # Must mention a remediation path
        assert "/settings/" in msg or "ceiling" in msg.lower()
