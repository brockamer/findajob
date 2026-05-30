#!/usr/bin/env python3
"""Aggregate recurring recruiter_critic findings into a source-fix report (#265).

The recruiter_critic writes a sharp ≤150-word read into every prep folder, but
the same weaknesses recur across applications — which means they live at the
source (master_resume.md / profile.md / role prompts), not the per-prep tailor.
This scans every ``* Critique - *.md`` artifact, anchors each flagged line to a
source line, and reports the lines flagged across ≥N companies so they can be
fixed once instead of re-flagged forever.

Read-only over the corpus; writes a dated markdown report to the gitignored
``candidate_context/critique_aggregate/`` (the report embeds real resume lines).

Usage:
    python scripts/critique_review.py [--since YYYY-MM-DD] [--min-companies N] [--print]
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from findajob.critique_aggregator.pipeline import aggregate_corpus, default_source_files
from findajob.paths import BASE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", help="Inclusive YYYY-MM-DD floor on critique date.")
    parser.add_argument(
        "--min-companies",
        type=int,
        default=3,
        help="Recurrence floor — distinct companies a line must be flagged by (default 3).",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="Also echo the rendered report to stdout.",
    )
    args = parser.parse_args()

    base = Path(BASE)
    companies_root = base / "companies"
    today = datetime.now().strftime("%Y-%m-%d")

    result, report = aggregate_corpus(
        companies_root,
        default_source_files(base),
        generated_for=today,
        since=args.since,
        min_companies=args.min_companies,
    )

    outdir = base / "candidate_context" / "critique_aggregate"
    outdir.mkdir(parents=True, exist_ok=True)
    outpath = outdir / f"{today}.md"
    outpath.write_text(report)

    print(f"Scanned {result.total_critiques} critiques across {result.total_companies} companies.")
    print(
        f"  {len(result.source_clusters)} source-level fix cluster(s), "
        f"{len(result.recurring_themes)} recurring theme(s), "
        f"{result.oneoff_lines} one-off line(s)."
    )
    print(f"Report: {outpath}")
    if args.print:
        print("\n" + report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
