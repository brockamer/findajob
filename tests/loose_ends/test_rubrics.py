"""Tests for src/findajob/loose_ends/rubrics.py."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from findajob.loose_ends.rubrics import (
    exclusion_key,
    is_excluded,
    load_exclusions,
)


def test_exclusion_key_format():
    assert exclusion_key(persona="nux_user", route="/board/applied", rubric="flow_without_exit") == "nux_user::/board/applied::flow_without_exit"


def test_load_exclusions_parses_yaml(tmp_path: Path):
    path = tmp_path / "loose_ends_walkthrough_exclusions.yaml"
    path.write_text(yaml.safe_dump({
        "exclusions": [
            {"persona": "established_user", "route": "/admin/stacks/", "rubric": "flow_without_exit", "rationale": "Operator-only."},
            {"persona": "nux_user", "route": "/board/applied", "rubric": "empty_state_no_guidance", "rationale": "Correctly empty for NUX."},
        ]
    }))
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
