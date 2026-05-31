"""Report-rendering tests for ``findajob.critique_aggregator.report`` (#265).

The report is the product. Anti-slop guarantees verified here: every rendered
claim is a verbatim quote/sentence + a count + a ``file:line`` + a fixed-vocab
action verb — no model-generated prose. The skeptical-recruiter voice survives
via verbatim sentences (the operator's explicit steer).
"""

from __future__ import annotations

from findajob.critique_aggregator.analyze import AggregateResult, RecurringTheme
from findajob.critique_aggregator.anchor import SourceLine
from findajob.critique_aggregator.cluster import Cluster, FlaggedItem
from findajob.critique_aggregator.report import DEFAULT_MAX_THEMES, render_report

GLUE = SourceLine("master_resume.md", 360, "Known as the glue across the lab and ops teams.")


def _cluster():
    items = [
        FlaggedItem(
            company="Acme",
            section="weak",
            quote="the glue across lab and ops teams",
            recruiter_sentence='"the glue across lab and ops teams" — unattributable hearsay, cut it.',
            anchor=GLUE,
        ),
        FlaggedItem("Gamma", "weak", "glue", "glue line is hearsay.", GLUE),
        FlaggedItem("Delta", "weak", "glue", "drop the glue quote.", GLUE),
    ]
    return Cluster(anchor=GLUE, items=items)


def _result():
    return AggregateResult(
        source_clusters=[_cluster()],
        recurring_themes=[RecurringTheme(term="reshaped", companies=["A", "B", "C"])],
        oneoff_lines=7,
        total_critiques=41,
        total_companies=38,
        total_flags=120,
    )


def test_report_surfaces_source_line_with_location_and_count():
    md = render_report(_result(), generated_for="2026-05-30")

    assert "master_resume.md:360" in md
    assert "Known as the glue across the lab and ops teams." in md
    assert "3" in md  # distinct-company count
    assert "Acme" in md and "Gamma" in md and "Delta" in md


def test_report_preserves_verbatim_recruiter_voice():
    md = render_report(_result(), generated_for="2026-05-30")

    # The punchy recruiter sentence rides along verbatim, not a paraphrase.
    assert "unattributable hearsay, cut it." in md


def test_report_uses_fixed_action_vocabulary():
    md = render_report(_result(), generated_for="2026-05-30")

    # A controlled verb tags the weak-section content fix.
    assert any(verb in md for verb in ("WEAKEN", "CUT", "DELETE"))


def test_report_includes_themes_oneoffs_and_stats():
    md = render_report(_result(), generated_for="2026-05-30")

    assert "reshaped" in md
    assert "7" in md  # one-off count
    assert "41" in md and "38" in md  # corpus stats


def test_empty_corpus_renders_gracefully():
    empty = AggregateResult([], [], 0, 0, 0, 0)

    md = render_report(empty, generated_for="2026-05-30")

    assert "No recurring" in md or "0" in md


# --- #932: themes display cap with surfaced truncation count -----------------


def _many_themes(n):
    # Distinct terms, descending company_count so ordering is deterministic.
    return [RecurringTheme(term=f"term{i:03d}", companies=[f"C{j}" for j in range(n - i + 1)]) for i in range(n)]


def _result_with_themes(themes):
    return AggregateResult(
        source_clusters=[],
        recurring_themes=themes,
        oneoff_lines=0,
        total_critiques=99,
        total_companies=80,
        total_flags=300,
    )


def test_themes_below_cap_render_in_full_without_truncation_notice():
    md = render_report(_result_with_themes(_many_themes(DEFAULT_MAX_THEMES)), generated_for="2026-05-30")

    assert "term000" in md
    assert f"term{DEFAULT_MAX_THEMES - 1:03d}" in md  # the last theme still shown
    assert "more" not in md.lower().split("one-off")[0]  # no truncation notice in themes section


def test_themes_above_cap_truncate_with_explicit_count():
    total = DEFAULT_MAX_THEMES + 7
    md = render_report(_result_with_themes(_many_themes(total)), generated_for="2026-05-30")

    # Top theme shown, an over-cap theme hidden.
    assert "term000" in md
    assert f"term{total - 1:03d}" not in md
    # The hidden count is surfaced verbatim — never a silent drop.
    assert "7" in md
    assert "more" in md.lower()


def test_explicit_max_themes_override_is_honored():
    md = render_report(_result_with_themes(_many_themes(10)), generated_for="2026-05-30", max_themes=4)

    assert "term000" in md
    assert "term009" not in md
    assert "6" in md  # 10 - 4 hidden
