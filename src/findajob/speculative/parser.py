"""Validates LLM output from speculative_roles_synth into RoleCard objects.

Defensive against LLMs that wrap JSON in markdown fences despite instructions,
return more than 5 cards, omit required fields, or use invalid enums.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

ContactType = Literal["recruiter", "hiring_manager", "senior_ic"]
_VALID_CONTACT_TYPES: set[str] = {"recruiter", "hiring_manager", "senior_ic"}
_MAX_CARDS = 5


@dataclass(frozen=True)
class RoleCard:
    title: str
    description: str
    why_this_fits_candidate: str
    likely_team_or_org: str
    suggested_contact_type: ContactType


def parse_role_cards(raw: str) -> list[RoleCard]:
    """Parse a speculative_roles_synth output into validated RoleCard objects.

    Raises ValueError on any structural problem. Caps at 5 cards.
    """
    cleaned = _strip_markdown_fence(raw).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"role-cards output is not valid JSON: {e}") from e

    if not isinstance(data, list):
        raise ValueError("role-cards output must be a JSON array, got: " + type(data).__name__)
    if not data:
        raise ValueError("role-cards output is empty — synthesis must produce at least one card")

    cards: list[RoleCard] = []
    for i, item in enumerate(data[:_MAX_CARDS]):
        if not isinstance(item, dict):
            raise ValueError(f"role card {i} is not a JSON object")
        for required in (
            "title",
            "description",
            "why_this_fits_candidate",
            "likely_team_or_org",
            "suggested_contact_type",
        ):
            if required not in item or not item[required]:
                raise ValueError(f"role card {i} missing required field: {required}")
        contact_type = item["suggested_contact_type"]
        if contact_type not in _VALID_CONTACT_TYPES:
            raise ValueError(
                f"role card {i} has invalid suggested_contact_type "
                f"{contact_type!r}; expected one of {sorted(_VALID_CONTACT_TYPES)}"
            )
        cards.append(
            RoleCard(
                title=str(item["title"]).strip(),
                description=str(item["description"]).strip(),
                why_this_fits_candidate=str(item["why_this_fits_candidate"]).strip(),
                likely_team_or_org=str(item["likely_team_or_org"]).strip(),
                suggested_contact_type=contact_type,
            )
        )
    return cards


def _strip_markdown_fence(s: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` fence wrapping if present."""
    fence_match = re.match(r"^```(?:json)?\s*\n(.+?)\n```\s*$", s.strip(), re.S)
    if fence_match:
        return fence_match.group(1)
    return s
