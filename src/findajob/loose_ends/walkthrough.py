"""Playwright walker + YAML loader + hint extractor (#572 Phase 2).

Walks every walkthrough in config/loose_ends_walkthroughs.yaml, executes
each step's primitive via Playwright sync API, and at evaluate_dom steps
hands the redacted DOM + structured hints to rubrics.py for LLM judgment.

The walker is itinerary-driven: it never asks the LLM where to go. The
LLM is only called at evaluate_dom steps to judge what's rendered.
"""

from __future__ import annotations
