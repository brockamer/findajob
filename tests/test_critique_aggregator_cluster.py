"""Clustering tests for ``findajob.critique_aggregator.cluster`` (#265).

The aggregation core: group anchored flags by source line, count *distinct
companies*, and keep only lines flagged across ≥3 companies. Distinct-company
counting is what separates a real source-level defect (same line panned by
many different recruiters) from per-prep noise (one company, flagged twice).
"""

from __future__ import annotations

from findajob.critique_aggregator.anchor import SourceLine
from findajob.critique_aggregator.cluster import FlaggedItem, cluster_by_source_line

GLUE = SourceLine("master_resume.md", 360, '"acts as the glue across teams"')
RACK = SourceLine("master_resume.md", 160, "Lab rack volume grew 2x")


def _item(company, anchor, quote="q", sentence="s", section="weak"):
    return FlaggedItem(
        company=company,
        section=section,
        quote=quote,
        recruiter_sentence=sentence,
        anchor=anchor,
    )


def test_line_flagged_by_three_companies_forms_one_cluster():
    items = [
        _item("Acme", GLUE),
        _item("Beta", GLUE),
        _item("Gamma", GLUE),
    ]

    clusters = cluster_by_source_line(items, min_companies=3)

    assert len(clusters) == 1
    assert clusters[0].anchor.line_no == 360
    assert clusters[0].company_count == 3
    assert clusters[0].companies == ["Acme", "Beta", "Gamma"]


def test_below_three_distinct_companies_is_filtered_as_noise():
    items = [_item("Acme", GLUE), _item("Beta", GLUE)]

    assert cluster_by_source_line(items, min_companies=3) == []


def test_same_company_flagged_twice_counts_once():
    # Two preps at the same company must not inflate a line to "recurring".
    items = [
        _item("Acme", GLUE),
        _item("Acme", GLUE),
        _item("Gamma", GLUE),
    ]

    assert cluster_by_source_line(items, min_companies=3) == []


def test_unanchored_items_do_not_form_source_clusters():
    # Generated openers anchor to nothing; they belong to the prompt-fix bucket,
    # never a phantom content-line cluster.
    items = [_item(c, None) for c in ("A", "B", "C", "D")]

    assert cluster_by_source_line(items, min_companies=3) == []


def test_clusters_sorted_by_company_count_descending():
    items = [
        _item("A", GLUE),
        _item("B", GLUE),
        _item("C", GLUE),
        _item("D", GLUE),
        _item("A", RACK),
        _item("B", RACK),
        _item("C", RACK),
    ]

    clusters = cluster_by_source_line(items, min_companies=3)

    assert [c.anchor.line_no for c in clusters] == [360, 160]
    assert [c.company_count for c in clusters] == [4, 3]
