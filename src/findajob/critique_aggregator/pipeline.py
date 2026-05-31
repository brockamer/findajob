"""Top-level orchestration for the critique aggregator (#265).

Ties the units together: load source files → scan + filter critiques → build
flags → aggregate → render. The thin CLI (``scripts/critique_review.py``) calls
``aggregate_corpus`` so the full path is exercised by tests, not just the script.
"""

from __future__ import annotations

import re
from pathlib import Path

from findajob.critique_aggregator.analyze import AggregateResult, aggregate
from findajob.critique_aggregator.corpus import (
    build_flagged_items,
    iter_critique_files,
    load_source_lines,
)
from findajob.critique_aggregator.report import render_report

# Trailing ``YYYYMMDD-HHMMSS`` stamp in a critique filename.
_STAMP_RE = re.compile(r"(\d{8})-\d{6}\.md$")


def _file_yyyymmdd(path: Path) -> str | None:
    match = _STAMP_RE.search(path.name)
    return match.group(1) if match else None


def default_source_files(base: Path) -> list[tuple[Path, str]]:
    """The (path, label) source files to anchor against, under ``base``.

    Shared by the CLI and the /tools/ web view so the anchor labels — which
    the report keys fixes on — cannot drift between the two entry points.
    """
    cc = base / "candidate_context"
    return [
        (cc / "master_resume.md", "master_resume.md"),
        (cc / "profile.md", "profile.md"),
    ]


def aggregate_corpus(
    companies_root: Path,
    source_files: list[tuple[Path, str]],
    *,
    generated_for: str,
    since: str | None = None,
    min_companies: int = 3,
    min_theme_companies: int | None = None,
) -> tuple[AggregateResult, str]:
    """Run the full aggregation and return ``(result, markdown_report)``.

    ``source_files`` is a list of ``(path, label)``; missing files are skipped.
    ``since`` is an inclusive ``YYYY-MM-DD`` floor on the critique's filename
    stamp — files without a parseable stamp are kept (never silently dropped).
    ``min_companies`` is the source-cluster floor; ``min_theme_companies``
    overrides the corpus-scaled themes floor when set (``None`` = scale, #932).
    """
    source_lines = []
    for path, label in source_files:
        if path.exists():
            source_lines.extend(load_source_lines(path, label))

    files = iter_critique_files(companies_root)
    if since:
        floor = since.replace("-", "")
        files = [f for f in files if (_file_yyyymmdd(f) or floor) >= floor]

    items = build_flagged_items(files, source_lines)
    result = aggregate(
        items,
        total_critiques=len(files),
        min_companies=min_companies,
        min_theme_companies=min_theme_companies,
    )
    report = render_report(result, generated_for=generated_for)
    return result, report
