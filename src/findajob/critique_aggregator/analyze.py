"""Aggregate flagged items into the report's three surfaces (#265).

1. source_clusters  — anchored lines flagged across ≥N companies (act now)
2. recurring_themes — unanchored terms recurring across ≥N companies, derived
                      from term frequency, NOT a hardcoded field vocabulary
                      (keeps the public tool career-neutral)
3. one-off stats    — anchored lines below the recurrence floor (the noise tail)
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from findajob.critique_aggregator.cluster import (
    Cluster,
    FlaggedItem,
    cluster_by_source_line,
)

# Generic English stopwords + critique-frame filler. Deliberately career-neutral
# — no domain terms — so the themes that survive are the candidate's own.
_STOPWORDS = frozenset(
    """
    about above across after again against being below between cannot could
    doesn during every first from getting have having here itself just
    line like made many more most much never nothing other over really reads
    same should since some such than that their them then there these they
    thing think this those through under until very what when where which while
    will with would your yours yourself sounds looks filler
    """.split()
)
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'-]{4,}")


@dataclass
class RecurringTheme:
    term: str
    companies: list[str]

    @property
    def company_count(self) -> int:
        return len(self.companies)


@dataclass
class AggregateResult:
    source_clusters: list[Cluster]
    recurring_themes: list[RecurringTheme]
    oneoff_lines: int
    total_critiques: int
    total_companies: int
    total_flags: int


def _terms(text: str) -> set[str]:
    return {tok.lower() for tok in _TOKEN_RE.findall(text) if tok.lower() not in _STOPWORDS}


def aggregate(items: list[FlaggedItem], *, total_critiques: int, min_companies: int = 3) -> AggregateResult:
    source_clusters = cluster_by_source_line(items, min_companies=min_companies)

    # One-off lines: anchored, but their line cleared neither cluster nor floor.
    line_companies: dict[tuple[str, int], set[str]] = defaultdict(set)
    for it in items:
        if it.anchor is not None:
            line_companies[(it.anchor.file, it.anchor.line_no)].add(it.company)
    oneoff_lines = sum(1 for cos in line_companies.values() if len(cos) < min_companies)

    # Recurring themes from UNANCHORED text (quote + recruiter sentence), counted
    # by distinct company so one verbose critique can't manufacture a theme.
    term_companies: dict[str, set[str]] = defaultdict(set)
    for it in items:
        if it.anchor is not None:
            continue
        for term in _terms(f"{it.quote} {it.recruiter_sentence}"):
            term_companies[term].add(it.company)
    themes = [
        RecurringTheme(term=term, companies=sorted(cos))
        for term, cos in term_companies.items()
        if len(cos) >= min_companies
    ]
    themes.sort(key=lambda t: (-t.company_count, t.term))

    return AggregateResult(
        source_clusters=source_clusters,
        recurring_themes=themes,
        oneoff_lines=oneoff_lines,
        total_critiques=total_critiques,
        total_companies=len({it.company for it in items}),
        total_flags=len(items),
    )
