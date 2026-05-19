"""Per-rubric LLM evaluators for cat-2 and cat-3 loose ends (#572 Phase 2).

Two evaluators:
  - evaluate_flow_without_exit: did the user reach a state with no UI exit?
  - evaluate_empty_state_no_guidance: is a collection empty with no CTA?

Exclusions are filtered BEFORE any LLM call. Each (persona, route, rubric)
tuple is matched against config/loose_ends_walkthrough_exclusions.yaml;
matched tuples skip the LLM and return excluded=True.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def exclusion_key(*, persona: str, route: str, rubric: str) -> str:
    """Flatten an exclusion tuple to its lookup key."""
    return f"{persona}::{route}::{rubric}"


def load_exclusions(path: Path) -> dict[str, str]:
    """Load the exclusions yaml into a key → rationale dict.

    Raises FileNotFoundError if the file is missing — recovery is documented
    in the shim's startup check.
    """
    if not path.exists():
        raise FileNotFoundError(f"Exclusions yaml missing: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        exclusion_key(persona=e["persona"], route=e["route"], rubric=e["rubric"]): e.get("rationale", "")
        for e in raw.get("exclusions", [])
    }


def is_excluded(
    *,
    persona: str,
    route: str,
    rubric: str,
    exclusions: dict[str, str],
) -> bool:
    """Exact-tuple lookup; no wildcards (deliberate — operators amend by adding entries)."""
    return exclusion_key(persona=persona, route=route, rubric=rubric) in exclusions
