"""Per-rubric LLM evaluators for cat-2, cat-3, and cat-4 loose ends (#572 Phase 2).

Three evaluators:
  - evaluate_flow_without_exit: did the user reach a state with no UI exit?
  - evaluate_empty_state_no_guidance: is a collection empty with no CTA?
  - evaluate_action_without_confirmation: did a state-changing action complete
    without any visible feedback that it took effect?

Exclusions are filtered BEFORE any LLM call. Each (persona, route, rubric)
tuple is matched against config/loose_ends_walkthrough_exclusions.yaml;
matched tuples skip the LLM and return excluded=True.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from findajob.llm import openrouter
from findajob.loose_ends.classifier import strip_json_fences
from findajob.loose_ends.finding import Finding


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


def _parse_judgment(text: str) -> dict[str, Any]:
    """Parse the LLM's JSON judgment, tolerating fences. Returns low-confidence shape on failure."""
    try:
        return json.loads(strip_json_fences(text))
    except json.JSONDecodeError:
        return {
            "is_loose_end": False,
            "confidence": "low",
            "rationale": f"LLM returned non-JSON: {text[:120]}",
            "suggested_surface": "",
        }


def evaluate_flow_without_exit(
    *,
    persona: str,
    walkthrough_name: str,
    current_url: str,
    context_hint: str,
    visible_button_labels: list[str],
    form_action_targets: list[str],
    dom_snippet: str,
    exclusions: dict[str, str],
) -> tuple[Finding, float]:
    """Cat 2 rubric evaluator. Returns (Finding, llm_cost_usd).

    Excluded tuples short-circuit before any LLM call. The LLM judges the
    redacted DOM + structured hints against the rubric in the role prompt.
    """
    rubric = "flow_without_exit"
    key = exclusion_key(persona=persona, route=current_url, rubric=rubric)
    if key in exclusions:
        return (
            Finding(
                persona=persona,
                walkthrough_name=walkthrough_name,
                current_url=current_url,
                category=2,
                is_loose_end=False,
                confidence="low",
                rationale=f"Excluded: {exclusions[key]}",
                suggested_surface="",
                excluded=True,
                exclusion_key=key,
            ),
            0.0,
        )

    prompt = json.dumps(
        {
            "current_url": current_url,
            "context_hint": context_hint,
            "visible_button_labels": visible_button_labels,
            "form_action_targets": form_action_targets,
            "dom_snippet": dom_snippet,
        }
    )
    result = openrouter.complete(role=f"loose_ends_{rubric}", prompt=prompt)
    cost = float(getattr(result, "cost_usd", 0.0) or 0.0)
    parsed = _parse_judgment(result.text)
    return (
        Finding(
            persona=persona,
            walkthrough_name=walkthrough_name,
            current_url=current_url,
            category=2,
            is_loose_end=bool(parsed.get("is_loose_end", False)),
            confidence=str(parsed.get("confidence", "low")),
            rationale=str(parsed.get("rationale", "")),
            suggested_surface=str(parsed.get("suggested_surface", "")),
            excluded=False,
            exclusion_key=None,
        ),
        cost,
    )


def evaluate_empty_state_no_guidance(
    *,
    persona: str,
    walkthrough_name: str,
    current_url: str,
    collection_container_ids: list[str],
    visible_button_labels: list[str],
    dom_snippet: str,
    exclusions: dict[str, str],
) -> tuple[Finding, float]:
    """Cat 3 rubric evaluator. Returns (Finding, llm_cost_usd)."""
    rubric = "empty_state_no_guidance"
    key = exclusion_key(persona=persona, route=current_url, rubric=rubric)
    if key in exclusions:
        return (
            Finding(
                persona=persona,
                walkthrough_name=walkthrough_name,
                current_url=current_url,
                category=3,
                is_loose_end=False,
                confidence="low",
                rationale=f"Excluded: {exclusions[key]}",
                suggested_surface="",
                excluded=True,
                exclusion_key=key,
            ),
            0.0,
        )

    prompt = json.dumps(
        {
            "current_url": current_url,
            "collection_container_ids": collection_container_ids,
            "visible_button_labels": visible_button_labels,
            "dom_snippet": dom_snippet,
        }
    )
    result = openrouter.complete(role=f"loose_ends_{rubric}", prompt=prompt)
    cost = float(getattr(result, "cost_usd", 0.0) or 0.0)
    parsed = _parse_judgment(result.text)
    return (
        Finding(
            persona=persona,
            walkthrough_name=walkthrough_name,
            current_url=current_url,
            category=3,
            is_loose_end=bool(parsed.get("is_loose_end", False)),
            confidence=str(parsed.get("confidence", "low")),
            rationale=str(parsed.get("rationale", "")),
            suggested_surface=str(parsed.get("suggested_surface", "")),
            excluded=False,
            exclusion_key=None,
        ),
        cost,
    )


def evaluate_action_without_confirmation(
    *,
    persona: str,
    walkthrough_name: str,
    current_url: str,
    context_hint: str,
    visible_button_labels: list[str],
    dom_snippet: str,
    exclusions: dict[str, str],
) -> tuple[Finding, float]:
    """Cat 4 rubric evaluator. Returns (Finding, llm_cost_usd).

    Judges whether a state-changing action (named in context_hint) produced
    any visible feedback in the rendered DOM — toast, banner, human-readable
    cell text. Invisible signals (data-* attributes, CSS-only class shifts,
    dropdown option enabling) do not count.
    """
    rubric = "action_without_confirmation"
    key = exclusion_key(persona=persona, route=current_url, rubric=rubric)
    if key in exclusions:
        return (
            Finding(
                persona=persona,
                walkthrough_name=walkthrough_name,
                current_url=current_url,
                category=4,
                is_loose_end=False,
                confidence="low",
                rationale=f"Excluded: {exclusions[key]}",
                suggested_surface="",
                excluded=True,
                exclusion_key=key,
            ),
            0.0,
        )

    prompt = json.dumps(
        {
            "current_url": current_url,
            "context_hint": context_hint,
            "visible_button_labels": visible_button_labels,
            "dom_snippet": dom_snippet,
        }
    )
    result = openrouter.complete(role=f"loose_ends_{rubric}", prompt=prompt)
    cost = float(getattr(result, "cost_usd", 0.0) or 0.0)
    parsed = _parse_judgment(result.text)
    return (
        Finding(
            persona=persona,
            walkthrough_name=walkthrough_name,
            current_url=current_url,
            category=4,
            is_loose_end=bool(parsed.get("is_loose_end", False)),
            confidence=str(parsed.get("confidence", "low")),
            rationale=str(parsed.get("rationale", "")),
            suggested_surface=str(parsed.get("suggested_surface", "")),
            excluded=False,
            exclusion_key=None,
        ),
        cost,
    )
