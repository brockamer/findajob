"""Tests for findajob.speculative.parser — validates LLM-output role cards."""

from __future__ import annotations

import json

import pytest

from findajob.speculative.parser import parse_role_cards


def test_parses_valid_array():
    raw = json.dumps(
        [
            {
                "title": "Critical Infrastructure Engineer",
                "description": "Own deployment of GPU clusters at new sites.",
                "why_this_fits_candidate": "Candidate landed FTW Lab in a single half.",
                "likely_team_or_org": "Site Operations",
                "suggested_contact_type": "hiring_manager",
            }
        ]
    )
    cards = parse_role_cards(raw)
    assert len(cards) == 1
    assert cards[0].title == "Critical Infrastructure Engineer"
    assert cards[0].suggested_contact_type == "hiring_manager"


def test_strips_leading_markdown_fence():
    """LLMs sometimes wrap JSON in ```json fences despite instructions."""
    raw = (
        "```json\n"
        + json.dumps(
            [
                {
                    "title": "X",
                    "description": "Y",
                    "why_this_fits_candidate": "Z",
                    "likely_team_or_org": "T",
                    "suggested_contact_type": "recruiter",
                }
            ]
        )
        + "\n```"
    )
    cards = parse_role_cards(raw)
    assert len(cards) == 1


def test_caps_at_five_cards():
    raw = json.dumps(
        [
            {
                "title": f"Role {i}",
                "description": "D",
                "why_this_fits_candidate": "W",
                "likely_team_or_org": "T",
                "suggested_contact_type": "recruiter",
            }
            for i in range(8)
        ]
    )
    cards = parse_role_cards(raw)
    assert len(cards) == 5  # surplus dropped


def test_rejects_invalid_contact_type():
    raw = json.dumps(
        [
            {
                "title": "X",
                "description": "Y",
                "why_this_fits_candidate": "Z",
                "likely_team_or_org": "T",
                "suggested_contact_type": "ceo",  # invalid
            }
        ]
    )
    with pytest.raises(ValueError, match="suggested_contact_type"):
        parse_role_cards(raw)


def test_rejects_missing_required_field():
    raw = json.dumps([{"title": "X"}])
    with pytest.raises(ValueError, match="missing"):
        parse_role_cards(raw)


def test_rejects_non_array():
    raw = json.dumps({"title": "X"})  # object, not array
    with pytest.raises(ValueError, match="array"):
        parse_role_cards(raw)


def test_rejects_empty_array():
    """Spec requires 1-5 cards — empty is a synthesis failure, not silent success."""
    raw = json.dumps([])
    with pytest.raises(ValueError, match="empty|at least one"):
        parse_role_cards(raw)
