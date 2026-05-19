"""Per-rubric LLM evaluators for cat-2 and cat-3 loose ends (#572 Phase 2).

Two evaluators:
  - evaluate_flow_without_exit: did the user reach a state with no UI exit?
  - evaluate_empty_state_no_guidance: is a collection empty with no CTA?

Exclusions are filtered BEFORE any LLM call. Each (persona, route, rubric)
tuple is matched against config/loose_ends_walkthrough_exclusions.yaml;
matched tuples skip the LLM and return excluded=True.
"""

from __future__ import annotations
