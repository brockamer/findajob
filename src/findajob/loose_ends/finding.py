"""Finding dataclass + JSONL I/O for the #572 Phase 2 loose-end walkthrough.

Extracted from walkthrough.py to break a circular import: rubrics.py needs
Finding to construct return values, and walkthrough.py needs the rubric
evaluators to dispatch evaluate_dom steps. Putting Finding in its own
zero-dep module severs the cycle.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    """One classified loose-end candidate from a single evaluate_dom step.

    Different shape from phase 1's Finding — Phase 2 carries persona,
    walkthrough provenance, and the (persona, route, rubric) exclusion key.
    """

    persona: str  # "nux_user" | "established_user"
    walkthrough_name: str  # matches config/loose_ends_walkthroughs.yaml
    current_url: str  # path the walker was on when it evaluated
    category: int  # 2 or 3
    is_loose_end: bool  # LLM's judgment (false if excluded)
    confidence: str  # "high" | "medium" | "low" | "review"
    rationale: str
    suggested_surface: str
    excluded: bool  # true if filtered before LLM call
    exclusion_key: str | None  # filled when excluded=True


def write_finding(target: Path, finding: Finding) -> None:
    """Append one JSONL row. Creates the file if needed. Single-writer."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(finding)) + "\n")


def read_findings(source: Path) -> list[Finding]:
    """Read a JSONL file into a list of Finding."""
    if not source.exists():
        return []
    out: list[Finding] = []
    for raw in source.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        out.append(Finding(**json.loads(raw)))
    return out
