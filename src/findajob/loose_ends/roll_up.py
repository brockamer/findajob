"""Finding aggregation + two-section markdown report writer (#572 Phase 2).

Reads findings.jsonl, groups by confidence (high/medium/low) and persona,
optionally calls the prose-writer LLM (temp=0) for the ## Findings prose,
deterministically renders the ## Exclusions fired this run section.
"""

from __future__ import annotations
