"""Aggregation-orchestrator tests for ``findajob.critique_aggregator.analyze`` (#265).

``aggregate`` turns a flat flag list into the report's three surfaces:
source-level clusters (anchored, ≥3 companies), recurring themes (unanchored
terms recurring across ≥3 companies — derived from the data, NOT a hardcoded
field vocabulary), and one-off stats. Field-neutrality is a hard requirement:
the public repo must not field-lock to one career's jargon.
"""

from __future__ import annotations

from findajob.critique_aggregator.analyze import aggregate
from findajob.critique_aggregator.anchor import SourceLine
from findajob.critique_aggregator.cluster import FlaggedItem

GLUE = SourceLine("master_resume.md", 360, '"acts as the glue across teams"')
RACK = SourceLine("master_resume.md", 160, "rack volume grew 2x")


def _item(company, anchor=None, quote="q", sentence="s", section="weak"):
    return FlaggedItem(company, section, quote, sentence, anchor)


def test_anchored_line_across_three_companies_is_a_source_cluster():
    items = [_item(c, GLUE) for c in ("Acme", "Beta", "Gamma")]

    result = aggregate(items, total_critiques=3, min_companies=3)

    assert len(result.source_clusters) == 1
    assert result.source_clusters[0].anchor.line_no == 360


def test_recurring_unanchored_term_becomes_a_theme():
    # Three companies' generated openers share the word "reshaped" — no source
    # line, but a recurring prompt-level pattern worth surfacing.
    items = [
        _item(c, anchor=None, quote="the market is being reshaped in real time") for c in ("Acme", "Beta", "Gamma")
    ]

    result = aggregate(items, total_critiques=3, min_companies=3)

    themes = {t.term for t in result.recurring_themes}
    assert "reshaped" in themes
    assert all(t.company_count >= 3 for t in result.recurring_themes)


def test_single_company_term_is_not_a_theme():
    items = [
        _item("Acme", quote="idiosyncratic phrasing nobody else used"),
        _item("Acme", quote="idiosyncratic phrasing nobody else used"),
    ]

    result = aggregate(items, total_critiques=2, min_companies=3)

    assert result.recurring_themes == []


def test_anchored_line_below_threshold_counts_as_oneoff_not_cluster():
    items = [_item("Acme", GLUE), _item("Beta", GLUE)]  # only 2 companies

    result = aggregate(items, total_critiques=2, min_companies=3)

    assert result.source_clusters == []
    assert result.oneoff_lines == 1  # one distinct line, below the floor


def test_stats_reflect_corpus_size():
    items = [_item(c, GLUE) for c in ("Acme", "Beta", "Gamma")]

    result = aggregate(items, total_critiques=3, min_companies=3)

    assert result.total_critiques == 3
    assert result.total_companies == 3
    assert result.total_flags == 3
