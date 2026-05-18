"""Diff surface vs coverage maps, classify candidate gaps via LLM, emit report (#572).

Stages:
  1. candidate_gaps = surface_map.keys() - coverage_map.keys() - exclusions.keys()
     (Exclusions are subtracted BEFORE any LLM call — non-negotiable per spec
     decision 8; the classifier must never see an operator-asserted exclusion.)
  2. For each candidate, call openrouter.complete() with role=loose_ends_classifier
     at temperature=0 (cross-run determinism). Result is parsed into a Finding.
  3. Call openrouter.complete() with role=loose_ends_prose_writer at default
     temperature for the roll-up markdown body.
  4. Write report to docs/personal/audit-reports/YYYY-MM-DD-static-audit.md with
     two sections: ## Findings and ## Exclusions fired this run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from findajob.llm import openrouter
from findajob.loose_ends.coverage_map import SurfaceRef
from findajob.loose_ends.surface_map import CallSite


def _strip_json_fences(text: str) -> str:
    """Strip markdown code fences around an LLM's JSON response.

    Tolerates ```json ... ```, ``` ... ```, and bare JSON. Surfaced by #572
    Task 5: Haiku 4.5 fences JSON output despite role-prompt instructions.
    """
    s = text.strip()
    if s.startswith("```"):
        # Drop the opening fence line entirely (```json or just ```).
        first_newline = s.find("\n")
        if first_newline != -1:
            s = s[first_newline + 1 :]
        # Drop a trailing fence.
        if s.endswith("```"):
            s = s[:-3].rstrip()
    return s


@dataclass(frozen=True)
class Finding:
    """One classified loose-end candidate."""

    path: str
    confidence: str  # "high" | "medium" | "low"
    rationale: str
    suggested_surface: str
    call_sites: list[CallSite]


def classify_gaps(
    *,
    surface_map: dict[str, list[CallSite]],
    coverage_map: dict[str, list[SurfaceRef]],
    exclusions: dict[str, str],
) -> tuple[list[Finding], float]:
    """Diff + pre-filter + classify. Returns (findings, total_classifier_cost_usd).

    Exclusions are subtracted BEFORE the LLM loop — excluded paths never become
    candidates and never reach openrouter.complete().
    """
    candidate_paths = set(surface_map.keys()) - set(coverage_map.keys()) - set(exclusions.keys())
    findings: list[Finding] = []
    total_cost = 0.0
    for path in sorted(candidate_paths):
        sites = surface_map[path]
        # Build a minimal prompt — file path + a few call-site snippets.
        snippets = "\n".join(f"  {s.file}:{s.line}: {s.snippet}" for s in sites[:5])
        prompt = (
            f"File: {path}\n"
            f"Consumed at:\n{snippets}\n\n"
            f"Is this a real loose end (no UI surface), or intentional CLI-only? "
            f'Respond as JSON: {{"confidence": "high|medium|low", "rationale": "...", "suggested_surface": "..."}}'
        )
        result = openrouter.complete(role="loose_ends_classifier", prompt=prompt)
        total_cost += float(getattr(result, "cost_usd", 0.0) or 0.0)
        # result.text is a JSON string per the role prompt's contract.
        try:
            parsed = json.loads(_strip_json_fences(result.text))
        except json.JSONDecodeError:
            parsed = {
                "confidence": "low",
                "rationale": f"LLM returned non-JSON: {result.text[:120]}",
                "suggested_surface": "",
            }
        findings.append(
            Finding(
                path=path,
                confidence=parsed.get("confidence", "low"),
                rationale=parsed.get("rationale", ""),
                suggested_surface=parsed.get("suggested_surface", ""),
                call_sites=sites,
            )
        )
    return findings, total_cost


def _exclusions_fired(
    *,
    surface_map: dict[str, list[CallSite]],
    exclusions: dict[str, str],
) -> list[tuple[str, str]]:
    """Return (path, rationale) for each exclusion that matched at least one consumer."""
    return [(path, rationale) for path, rationale in exclusions.items() if path in surface_map]


def write_report(
    *,
    findings: list[Finding],
    surface_map: dict[str, list[CallSite]],
    exclusions: dict[str, str],
    output_dir: Path,
    today: date | None = None,
) -> tuple[Path, float]:
    """Render findings + exclusions-fired into a dated markdown report.

    Uses the prose-writer LLM to compose the ## Findings prose. Exclusions-fired
    section is rendered deterministically. Returns (report_path, prose_cost_usd).
    """
    today = today or date.today()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{today.isoformat()}-static-audit.md"

    # Findings prose via LLM (default temperature).
    findings_payload = json.dumps(
        [
            {
                "path": f.path,
                "confidence": f.confidence,
                "rationale": f.rationale,
                "suggested_surface": f.suggested_surface,
            }
            for f in findings
        ]
    )
    prose_result = openrouter.complete(
        role="loose_ends_prose_writer",
        prompt=(
            f"Findings (JSON): {findings_payload}\n\n"
            f"Write the ## Findings section as Markdown, grouping by confidence (high → medium → low)."
        ),
    )
    findings_section = prose_result.text.strip()
    prose_cost = float(getattr(prose_result, "cost_usd", 0.0) or 0.0)

    # Exclusions-fired section: deterministic.
    fired = _exclusions_fired(surface_map=surface_map, exclusions=exclusions)
    if fired:
        exclusions_section = "\n".join(f"- `{path}` — {rationale}" for path, rationale in sorted(fired))
    else:
        exclusions_section = "_No exclusions matched any consumer this run._"

    body = (
        f"# Static UX loose-end audit — {today.isoformat()}\n\n"
        f"## Findings\n\n{findings_section}\n\n"
        f"## Exclusions fired this run\n\n{exclusions_section}\n"
    )
    report_path.write_text(body, encoding="utf-8")
    return report_path, prose_cost
