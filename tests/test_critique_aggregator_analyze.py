"""Aggregation-orchestrator tests for ``findajob.critique_aggregator.analyze`` (#265).

``aggregate`` turns a flat flag list into the report's three surfaces:
source-level clusters (anchored, ≥3 companies), recurring themes (unanchored
terms recurring across ≥3 companies — derived from the data, NOT a hardcoded
field vocabulary), and one-off stats. Field-neutrality is a hard requirement:
the public repo must not field-lock to one career's jargon.
"""

from __future__ import annotations

import math

from findajob.critique_aggregator.analyze import aggregate, default_theme_floor
from findajob.critique_aggregator.anchor import SourceLine
from findajob.critique_aggregator.cluster import FlaggedItem

GLUE = SourceLine("master_resume.md", 360, '"acts as the glue across teams"')
RACK = SourceLine("master_resume.md", 160, "rack volume grew 2x")


def _item(company, anchor=None, quote="q", sentence="s", section="weak"):
    return FlaggedItem(company, section, quote, sentence, anchor)


def _corpus(company_count, *, extra=None):
    """A list of unanchored items spanning ``company_count`` distinct companies.

    Each filler carries an empty quote so it adds to the corpus-wide company
    count without contributing any 5+-letter token — no manufactured theme to
    confound the assertions. ``extra`` is an optional list of additional
    FlaggedItems layered on top.
    """
    items = [_item(f"C{i:03d}", quote="") for i in range(company_count)]
    if extra:
        items.extend(extra)
    return items


def test_anchored_line_across_three_companies_is_a_source_cluster():
    items = [_item(c, GLUE) for c in ("Acme", "Beta", "Gamma")]

    result = aggregate(items, total_critiques=3, min_companies=3)

    assert len(result.source_clusters) == 1
    assert result.source_clusters[0].anchor.line_no == 360


def test_recurring_unanchored_term_becomes_a_theme():
    # Three companies' generated openers share the word "reshaped" — no source
    # line, but a recurring prompt-level pattern worth surfacing. An explicit
    # low theme floor isolates the detection mechanism from the corpus-scaled
    # default (which would require ≥8 companies — see #932 tests below).
    items = [
        _item(c, anchor=None, quote="the market is being reshaped in real time") for c in ("Acme", "Beta", "Gamma")
    ]

    result = aggregate(items, total_critiques=3, min_companies=3, min_theme_companies=3)

    themes = {t.term for t in result.recurring_themes}
    assert "reshaped" in themes
    assert all(t.company_count >= 3 for t in result.recurring_themes)


def test_single_company_term_is_not_a_theme():
    items = [
        _item("Acme", quote="idiosyncratic phrasing nobody else used"),
        _item("Acme", quote="idiosyncratic phrasing nobody else used"),
    ]

    result = aggregate(items, total_critiques=2, min_companies=3, min_theme_companies=3)

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


# --- #932: themes floor scales with corpus size -----------------------------


def test_default_theme_floor_formula_scales_with_corpus():
    # max(8, ceil(0.15 * n)) — a small floor that grows on big corpora.
    assert default_theme_floor(0) == 8
    assert default_theme_floor(3) == 8  # tiny corpus: floor stays at the 8 minimum
    assert default_theme_floor(62) == max(8, math.ceil(0.15 * 62))  # == 10
    assert default_theme_floor(62) == 10
    assert default_theme_floor(200) == 30  # large corpus: floor scales up


def test_theme_floor_scales_on_large_corpus_drops_low_recurrence():
    # On a 62-company corpus the scaled floor is 10. A term across 12 companies
    # is a real theme; a term across 9 (cleared the old flat-3 floor) is noise.
    stakeholder = [_item(f"C{i:03d}", quote="stakeholder alignment") for i in range(12)]
    outlier = [_item(f"D{i:03d}", quote="outlier phrasing here") for i in range(9)]
    items = _corpus(62, extra=stakeholder + outlier)

    result = aggregate(items, total_critiques=83)

    terms = {t.term for t in result.recurring_themes}
    assert "stakeholder" in terms  # 12 companies ≥ floor 10
    assert "outlier" not in terms  # 9 companies < floor 10 — dropped as noise


def test_explicit_min_theme_companies_overrides_scaled_default():
    # Operator can override the scaled floor downward to inspect the long tail.
    outlier = [_item(f"D{i:03d}", quote="outlier phrasing here") for i in range(9)]
    items = _corpus(62, extra=outlier)

    result = aggregate(items, total_critiques=71, min_theme_companies=5)

    terms = {t.term for t in result.recurring_themes}
    assert "outlier" in terms  # 9 companies ≥ explicit floor 5


def test_theme_floor_change_does_not_touch_source_clusters():
    # Source clusters keep the flat min_companies=3 floor even when the scaled
    # theme floor (10 on a 62-company corpus) is much higher. Acceptance #2.
    anchored = [_item(c, GLUE) for c in ("Acme", "Beta", "Gamma")]  # 3 companies
    items = _corpus(62, extra=anchored)

    result = aggregate(items, total_critiques=65, min_companies=3)

    assert len(result.source_clusters) == 1
    assert result.source_clusters[0].anchor.line_no == 360


def test_theme_floor_reads_corpus_wide_company_count():
    # The same 9-company term clears the floor in a small corpus but drops out
    # once the corpus grows — even when the growth comes entirely from ANCHORED
    # companies (which add to the corpus-wide count + a source line, but never an
    # unanchored theme term). This pins the floor to the full corpus population,
    # not just the unanchored slice — a regression guard for the wiring.
    term9 = [_item(f"T{i:03d}", quote="borderline phrasing here") for i in range(9)]

    # 11 companies → floor max(8, ceil(0.15*11)) = 8 → the 9-company term is in.
    small = aggregate(_corpus(0, extra=term9 + [_item(f"F{i}") for i in range(2)]), total_critiques=11)
    assert "borderline" in {t.term for t in small.recurring_themes}

    # +60 anchored-only companies → 69 distinct → floor max(8, ceil(0.15*69)) = 11
    # → the same 9-company term now falls below the raised floor and drops out.
    anchored = [_item(f"A{i:03d}", anchor=RACK, quote="zzz") for i in range(60)]
    big = aggregate(_corpus(0, extra=term9 + anchored), total_critiques=69)
    assert "borderline" not in {t.term for t in big.recurring_themes}
