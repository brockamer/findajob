"""Cluster anchored critique flags by source line (#265).

The aggregation core. A source line flagged across many *distinct* companies is
a source-level defect worth fixing once; the same line flagged twice at one
company is per-prep noise. Clusters are keyed by (file, line_no) and ranked by
distinct-company count, keeping only those at or above the recurrence floor.
"""

from __future__ import annotations

from dataclasses import dataclass

from findajob.critique_aggregator.anchor import SourceLine


@dataclass
class FlaggedItem:
    """One offending line flagged in one critique.

    ``recruiter_sentence`` is the verbatim critic text carried through to the
    report so the skeptical-recruiter voice survives aggregation. ``anchor`` is
    ``None`` when the quote traced to no source line (generated text).
    """

    company: str
    section: str
    quote: str
    recruiter_sentence: str
    anchor: SourceLine | None


@dataclass
class Cluster:
    """All flags that anchored to one source line."""

    anchor: SourceLine
    items: list[FlaggedItem]

    @property
    def companies(self) -> list[str]:
        """Distinct companies that flagged this line, sorted."""
        return sorted({item.company for item in self.items})

    @property
    def company_count(self) -> int:
        return len(self.companies)


def cluster_by_source_line(items: list[FlaggedItem], *, min_companies: int = 3) -> list[Cluster]:
    """Group anchored flags by source line; keep lines flagged across
    ``min_companies`` distinct companies, ranked by that count (descending,
    then by file/line for stable ordering)."""
    grouped: dict[tuple[str, int], Cluster] = {}
    for item in items:
        if item.anchor is None:
            continue
        key = (item.anchor.file, item.anchor.line_no)
        if key not in grouped:
            grouped[key] = Cluster(anchor=item.anchor, items=[])
        grouped[key].items.append(item)

    clusters = [c for c in grouped.values() if c.company_count >= min_companies]
    clusters.sort(key=lambda c: (-c.company_count, c.anchor.file, c.anchor.line_no))
    return clusters
