"""Finding aggregation + two-section markdown report writer (#572 Phase 2).

Reads findings.jsonl, groups by confidence (high/medium/low) and persona,
optionally calls the prose-writer LLM (temp=0) for the ## Findings prose,
deterministically renders the ## Exclusions fired this run section.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from findajob.llm import openrouter
from findajob.loose_ends.finding import Finding, read_findings


def aggregate_findings(findings: list[Finding]) -> dict[str, list[Finding]]:
    """Group findings by confidence, dropping excluded and non-loose-ends.

    Returns a dict with keys 'high', 'medium', 'low' — empty list when the
    bucket has nothing. Excluded findings and is_loose_end=False findings
    do not appear in the buckets; they're report-side noise.
    """
    buckets: dict[str, list[Finding]] = {"high": [], "medium": [], "low": []}
    for f in findings:
        if f.excluded or not f.is_loose_end:
            continue
        if f.confidence in buckets:
            buckets[f.confidence].append(f)
    return buckets


def exclusions_fired(
    *,
    findings: list[Finding],
    exclusions: dict[str, str],
) -> list[tuple[str, str]]:
    """Return (key, rationale) for each exclusion that matched at least one finding."""
    used_keys = {f.exclusion_key for f in findings if f.excluded and f.exclusion_key}
    return [(key, exclusions[key]) for key in sorted(used_keys) if key in exclusions]


def write_report(
    *,
    findings_jsonl: Path,
    exclusions: dict[str, str],
    output_dir: Path,
    today: date | None = None,
) -> tuple[Path, float]:
    """Render findings + exclusions-fired into a dated markdown report.

    Uses the walkthrough prose-writer LLM (temp=0) for the ## Findings
    section. Returns (report_path, prose_cost_usd). REVIEW-confidence
    findings (walker timeouts) cluster in their own subsection inside
    the prose-writer output.
    """
    today = today or date.today()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{today.isoformat()}-walkthrough-audit.md"

    all_findings = read_findings(findings_jsonl)
    buckets = aggregate_findings(all_findings)

    # Build the JSON payload for the prose-writer.
    payload = [
        {
            "persona": f.persona,
            "walkthrough_name": f.walkthrough_name,
            "current_url": f.current_url,
            "category": f.category,
            "confidence": f.confidence,
            "rationale": f.rationale,
            "suggested_surface": f.suggested_surface,
        }
        for bucket in buckets.values()
        for f in bucket
    ]
    prose_result = openrouter.complete(
        role="loose_ends_walkthrough_prose_writer",
        prompt=(
            f"Findings (JSON): {json.dumps(payload)}\n\n"
            f"Write the ## Findings section as Markdown, grouped by confidence (### High → ### Medium → ### Low)."
        ),
    )
    findings_section = prose_result.text.strip()
    prose_cost = float(getattr(prose_result, "cost_usd", 0.0) or 0.0)

    # Exclusions-fired section: deterministic.
    fired = exclusions_fired(findings=all_findings, exclusions=exclusions)
    if fired:
        exclusions_section = "\n".join(f"- `{key}` — {rationale}" for key, rationale in fired)
    else:
        exclusions_section = "_No exclusions matched any walkthrough this run._"

    body = (
        f"# Dynamic UX walkthrough audit — {today.isoformat()}\n\n"
        f"## Findings\n\n{findings_section}\n\n"
        f"## Exclusions fired this run\n\n{exclusions_section}\n"
    )
    report_path.write_text(body, encoding="utf-8")
    return report_path, prose_cost
