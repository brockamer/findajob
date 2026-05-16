"""Tests for run_role() re-raise behavior around LLMSpendCeilingExceeded (#671).

run_role() must:
- RE-RAISE LLMSpendCeilingExceeded (not swallow to "")
- Continue to swallow OpenRouterError (existing contract preserved)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from findajob.llm.openrouter import LLMSpendCeilingExceeded, OpenRouterError
from findajob.llm.role_runner import run_role


class TestRunRoleSpendCeiling:
    def test_lsce_propagates(self):
        """run_role() must re-raise LLMSpendCeilingExceeded, not return ''."""
        lsce = LLMSpendCeilingExceeded(ceiling_usd=50.0, current_sum_usd=55.0)

        with patch("findajob.llm.role_runner.complete", side_effect=lsce):
            with pytest.raises(LLMSpendCeilingExceeded) as exc_info:
                run_role("any_role", "any_prompt")

        exc = exc_info.value
        assert exc.ceiling_usd == 50.0
        assert exc.current_sum_usd == 55.0

    def test_lsce_is_not_swallowed_to_empty_string(self):
        """Negative assertion: the return value is never '' on LSCE."""
        lsce = LLMSpendCeilingExceeded(ceiling_usd=10.0, current_sum_usd=10.0)

        with patch("findajob.llm.role_runner.complete", side_effect=lsce):
            with pytest.raises(LLMSpendCeilingExceeded):
                result = run_role("any_role", "any_prompt")
                # If we reach here, run_role swallowed the exception — that's wrong
                assert result != "", "run_role swallowed LLMSpendCeilingExceeded and returned ''"

    def test_openrouter_error_still_returns_empty_string(self):
        """Existing OpenRouterError contract: swallowed, run_role returns ''."""
        ore = OpenRouterError("auth fail", kind="auth")

        with patch("findajob.llm.role_runner.complete", side_effect=ore):
            result = run_role("any_role", "any_prompt")

        assert result == ""
