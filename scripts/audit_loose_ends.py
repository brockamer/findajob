#!/usr/bin/env python3
"""Static UX loose-end audit entry point (#572 Phase 1).

Walks the source tree, diffs user-input-file consumers against UI surfaces,
writes a dated markdown report to docs/personal/audit-reports/.

Runs on the dev VM against findajob.paths.BASE — not in the container.

Usage:
    uv run python scripts/audit_loose_ends.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from findajob.loose_ends.classifier import classify_gaps, write_report
from findajob.loose_ends.coverage_map import walk_coverage_map
from findajob.loose_ends.surface_map import walk_surface_map
from findajob.paths import BASE


def main() -> int:
    repo_root = Path(BASE)
    exclusions_path = repo_root / "config" / "audit_exclusions.yaml"
    if not exclusions_path.exists():
        print(
            f"ERROR: {exclusions_path} missing. Recovery:\n  git checkout HEAD -- config/audit_exclusions.yaml",
            file=sys.stderr,
        )
        return 1

    raw = yaml.safe_load(exclusions_path.read_text(encoding="utf-8")) or {}
    exclusions: dict[str, str] = {e["path"]: e.get("rationale", "") for e in raw.get("exclusions", [])}

    surface = walk_surface_map(repo_root=repo_root)
    coverage = walk_coverage_map(repo_root=repo_root)
    findings, classifier_cost = classify_gaps(
        surface_map=surface,
        coverage_map=coverage,
        exclusions=exclusions,
    )

    output_dir = repo_root / "docs" / "personal" / "audit-reports"
    report, prose_cost = write_report(
        findings=findings,
        surface_map=surface,
        exclusions=exclusions,
        output_dir=output_dir,
    )

    high = sum(1 for f in findings if f.confidence == "high")
    medium = sum(1 for f in findings if f.confidence == "medium")
    low = sum(1 for f in findings if f.confidence == "low")
    total_cost = classifier_cost + prose_cost
    budget = 0.50

    print(f"Audit complete. Findings: high={high} medium={medium} low={low}")
    print(f"Report: {report}")
    print(f"Cost: ${total_cost:.4f} (budget ${budget:.2f})")

    if total_cost > budget:
        print(
            f"ERROR: cost ${total_cost:.4f} exceeded ${budget:.2f} budget. "
            f"Tune config/roles/loose_ends_classifier.md before next run.",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
