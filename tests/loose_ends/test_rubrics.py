"""Tests for src/findajob/loose_ends/rubrics.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from findajob.loose_ends.rubrics import (
    evaluate_empty_state_no_guidance,
    evaluate_flow_without_exit,
    exclusion_key,
    is_excluded,
    load_exclusions,
)


def test_exclusion_key_format():
    assert (
        exclusion_key(persona="nux_user", route="/board/applied", rubric="flow_without_exit")
        == "nux_user::/board/applied::flow_without_exit"
    )


def test_load_exclusions_parses_yaml(tmp_path: Path):
    path = tmp_path / "loose_ends_walkthrough_exclusions.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "exclusions": [
                    {
                        "persona": "established_user",
                        "route": "/admin/stacks/",
                        "rubric": "flow_without_exit",
                        "rationale": "Operator-only.",
                    },
                    {
                        "persona": "nux_user",
                        "route": "/board/applied",
                        "rubric": "empty_state_no_guidance",
                        "rationale": "Correctly empty for NUX.",
                    },
                ]
            }
        )
    )
    exclusions = load_exclusions(path)
    assert exclusions == {
        "established_user::/admin/stacks/::flow_without_exit": "Operator-only.",
        "nux_user::/board/applied::empty_state_no_guidance": "Correctly empty for NUX.",
    }


def test_load_exclusions_empty_when_no_section(tmp_path: Path):
    path = tmp_path / "loose_ends_walkthrough_exclusions.yaml"
    path.write_text("{}")
    assert load_exclusions(path) == {}


def test_load_exclusions_fatal_when_missing(tmp_path: Path):
    path = tmp_path / "does_not_exist.yaml"
    with pytest.raises(FileNotFoundError):
        load_exclusions(path)


def test_is_excluded_matches_exact_tuple():
    exclusions = {
        "established_user::/admin/stacks/::flow_without_exit": "Operator-only.",
    }
    assert is_excluded(
        persona="established_user",
        route="/admin/stacks/",
        rubric="flow_without_exit",
        exclusions=exclusions,
    )
    assert not is_excluded(
        persona="nux_user",
        route="/admin/stacks/",
        rubric="flow_without_exit",
        exclusions=exclusions,
    )
    assert not is_excluded(
        persona="established_user",
        route="/admin/stacks/",
        rubric="empty_state_no_guidance",
        exclusions=exclusions,
    )


def test_evaluate_flow_without_exit_calls_llm_and_returns_finding():
    fake_llm = MagicMock()
    fake_llm.text = (
        '{"is_loose_end": true, "confidence": "high", '
        '"rationale": "No back button.", '
        '"suggested_surface": "Add Back to Applied."}'
    )
    fake_llm.cost_usd = 0.03
    with patch(
        "findajob.loose_ends.rubrics.openrouter.complete",
        return_value=fake_llm,
    ) as mock_complete:
        f, cost = evaluate_flow_without_exit(
            persona="established_user",
            walkthrough_name="applied_undo_exits",
            current_url="/board/applied",
            context_hint="User just transitioned applied→interviewing",
            visible_button_labels=["Filter", "Reset"],
            form_action_targets=[],
            dom_snippet="<html>...</html>",
            exclusions={},
        )
    assert mock_complete.called
    assert f.is_loose_end is True
    assert f.confidence == "high"
    assert f.category == 2
    assert f.excluded is False
    assert f.exclusion_key is None
    assert cost == 0.03


def test_evaluate_flow_without_exit_handles_fenced_json():
    """Haiku occasionally wraps JSON in markdown fences — must tolerate."""
    fake_llm = MagicMock()
    fake_llm.text = (
        "```json\n"
        '{"is_loose_end": false, "confidence": "high", '
        '"rationale": "Has back button.", "suggested_surface": ""}\n'
        "```"
    )
    fake_llm.cost_usd = 0.02
    with patch(
        "findajob.loose_ends.rubrics.openrouter.complete",
        return_value=fake_llm,
    ):
        f, _ = evaluate_flow_without_exit(
            persona="established_user",
            walkthrough_name="x",
            current_url="/",
            context_hint="",
            visible_button_labels=[],
            form_action_targets=[],
            dom_snippet="",
            exclusions={},
        )
    assert f.is_loose_end is False
    assert f.confidence == "high"


def test_evaluate_flow_without_exit_returns_excluded_without_llm():
    """Exclusions filtered BEFORE LLM call — non-negotiable."""
    exclusions = {
        "established_user::/admin/stacks/::flow_without_exit": "Operator-only.",
    }
    with patch("findajob.loose_ends.rubrics.openrouter.complete") as mock_complete:
        f, cost = evaluate_flow_without_exit(
            persona="established_user",
            walkthrough_name="x",
            current_url="/admin/stacks/",
            context_hint="",
            visible_button_labels=[],
            form_action_targets=[],
            dom_snippet="",
            exclusions=exclusions,
        )
    mock_complete.assert_not_called()
    assert f.excluded is True
    assert f.exclusion_key == "established_user::/admin/stacks/::flow_without_exit"
    assert f.is_loose_end is False
    assert cost == 0.0


def test_evaluate_flow_without_exit_low_confidence_on_bad_json():
    fake_llm = MagicMock()
    fake_llm.text = "this is not json"
    fake_llm.cost_usd = 0.01
    with patch(
        "findajob.loose_ends.rubrics.openrouter.complete",
        return_value=fake_llm,
    ):
        f, _ = evaluate_flow_without_exit(
            persona="established_user",
            walkthrough_name="x",
            current_url="/",
            context_hint="",
            visible_button_labels=[],
            form_action_targets=[],
            dom_snippet="",
            exclusions={},
        )
    assert f.confidence == "low"
    assert "non-JSON" in f.rationale or "not json" in f.rationale.lower()


def test_evaluate_empty_state_calls_llm_and_returns_finding():
    fake_llm = MagicMock()
    fake_llm.text = (
        '{"is_loose_end": true, "confidence": "high", '
        '"rationale": "Empty dashboard, no CTA.", '
        '"suggested_surface": "Add a CTA"}'
    )
    fake_llm.cost_usd = 0.04
    with patch(
        "findajob.loose_ends.rubrics.openrouter.complete",
        return_value=fake_llm,
    ) as mock_complete:
        f, cost = evaluate_empty_state_no_guidance(
            persona="nux_user",
            walkthrough_name="dashboard_first_load",
            current_url="/board/dashboard",
            collection_container_ids=["job-rows-table"],
            visible_button_labels=["Filter"],
            dom_snippet="<html><table id='job-rows-table'></table></html>",
            exclusions={},
        )
    assert mock_complete.called
    assert f.is_loose_end is True
    assert f.category == 3
    assert cost == 0.04


def test_evaluate_empty_state_returns_excluded_without_llm():
    exclusions = {
        "nux_user::/board/applied::empty_state_no_guidance": "Correctly empty for NUX.",
    }
    with patch("findajob.loose_ends.rubrics.openrouter.complete") as mock_complete:
        f, cost = evaluate_empty_state_no_guidance(
            persona="nux_user",
            walkthrough_name="x",
            current_url="/board/applied",
            collection_container_ids=[],
            visible_button_labels=[],
            dom_snippet="",
            exclusions=exclusions,
        )
    mock_complete.assert_not_called()
    assert f.excluded is True
    assert f.exclusion_key == "nux_user::/board/applied::empty_state_no_guidance"
    assert cost == 0.0
