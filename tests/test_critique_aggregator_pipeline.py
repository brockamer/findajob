"""End-to-end pipeline test for ``findajob.critique_aggregator.pipeline`` (#265).

Exercises the real codepath — scan a synthetic company tree, load synthetic
source files, anchor, cluster, render — and asserts the report names the
recurring source line. This is the regression guard that the units compose
correctly, not just in isolation.
"""

from __future__ import annotations

from pathlib import Path

from findajob.critique_aggregator.pipeline import aggregate_corpus


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _critique(company: str) -> str:
    return (
        '**Weak:** "the glue across the lab and ops teams" — hearsay, cut it.\n\n'
        '**Generic:** "the market is being reshaped in real time" is filler.\n'
    )


def test_full_pipeline_surfaces_recurring_source_line(tmp_path):
    companies = tmp_path / "companies"
    # Same defect quoted (paraphrased identically here) across three companies +
    # buckets, so it must clear the recurrence floor.
    _write(companies / "Acme_R" / "C Critique - Acme - R - 20260501-000000.md", _critique("Acme"))
    _write(companies / "_applied" / "Beta_R" / "C Critique - Beta - R - 20260502-000000.md", _critique("Beta"))
    _write(companies / "_rejected" / "Gamma_R" / "C Critique - Gamma - R - 20260503-000000.md", _critique("Gamma"))

    master = tmp_path / "master_resume.md"
    master.write_text("Widely known as the glue across the lab and ops teams here.\n")

    result, md = aggregate_corpus(
        companies,
        [(master, "master_resume.md"), (tmp_path / "missing_profile.md", "profile.md")],
        generated_for="2026-05-30",
        min_companies=3,
    )

    assert result.total_critiques == 3
    assert len(result.source_clusters) == 1
    assert "master_resume.md:1" in md
    assert "glue across the lab and ops teams" in md
    # The generated opener recurred but anchored to nothing → theme, not a cluster.
    assert "reshaped" in md


def test_since_filter_excludes_older_critiques(tmp_path):
    companies = tmp_path / "companies"
    _write(companies / "Old_R" / "C Critique - Old - R - 20260101-000000.md", _critique("Old"))
    _write(companies / "New_R" / "C Critique - New - R - 20260601-000000.md", _critique("New"))

    result, _ = aggregate_corpus(companies, [], generated_for="2026-06-02", since="2026-03-01", min_companies=1)

    assert result.total_critiques == 1
    assert result.total_companies == 1
