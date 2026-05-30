"""Corpus-scan tests for ``findajob.critique_aggregator.corpus`` (#265).

Covers the I/O layer: locating critique files across the four company buckets,
deriving the company from the filename, de-duplicating ``.applied-*`` apply-time
snapshots, loading source lines from master/profile, and assembling the flat
``FlaggedItem`` list the clusterer consumes. All fixture content is synthetic.
"""

from __future__ import annotations

from pathlib import Path

from findajob.critique_aggregator.corpus import (
    build_flagged_items,
    company_from_critique_path,
    iter_critique_files,
    load_source_lines,
)


def test_company_parsed_from_filename_at_stable_delimiter():
    # Company is the field after " Critique - "; role may itself contain " - ".
    p = Path("Cand Critique - Globex Systems - Ops Manager - AI Lab Lead - 20260524-055541.md")
    assert company_from_critique_path(p) == "Globex Systems"


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_iter_critique_files_spans_buckets_and_dedups_snapshots(tmp_path):
    root = tmp_path / "companies"
    # Live folder
    _write(root / "Acme_Role_2026-01-01_0000" / "Cand Critique - Acme - Role - 20260101-0000.md")
    # Archived + an apply-time snapshot duplicate that must be excluded
    _write(root / "_applied" / "Beta_Role_2026-02-02_0000" / "Cand Critique - Beta - Role - 20260202-0000.md")
    _write(
        root
        / "_applied"
        / "Beta_Role_2026-02-02_0000"
        / "Cand Critique - Beta - Role - 20260202-0000.applied-2026-02-03.md"
    )
    # A .docx sibling that must be excluded
    _write(root / "_rejected" / "Gamma_Role_2026-03-03_0000" / "Cand Critique - Gamma - Role - 20260303-0000.docx")
    _write(root / "_rejected" / "Gamma_Role_2026-03-03_0000" / "Cand Critique - Gamma - Role - 20260303-0000.md")
    _write(root / "_waitlisted" / "Delta_Role_2026-04-04_0000" / "Cand Critique - Delta - Role - 20260404-0000.md")

    files = iter_critique_files(root)
    companies = sorted(company_from_critique_path(f) for f in files)

    assert companies == ["Acme", "Beta", "Delta", "Gamma"]
    assert not any(".applied-" in f.name for f in files)
    assert not any(f.suffix == ".docx" for f in files)


def test_load_source_lines_numbers_from_one_and_skips_blanks(tmp_path):
    f = tmp_path / "master_resume.md"
    f.write_text("First line\n\n   \nFourth line\n")

    lines = load_source_lines(f, "master_resume.md")

    assert [(sl.line_no, sl.text) for sl in lines] == [
        (1, "First line"),
        (4, "Fourth line"),
    ]
    assert all(sl.file == "master_resume.md" for sl in lines)


def test_build_flagged_items_anchors_quotes_to_source(tmp_path):
    root = tmp_path / "companies"
    critique = (
        '**Weak:** "the glue across the lab and ops teams" — hearsay, cut it.\n\n'
        '**Generic:** "the cloud is being reshaped in real time" is filler.\n'
    )
    _write(
        root / "Acme_Role" / "Cand Critique - Acme - Role - 20260101-0000.md",
        critique,
    )
    master = tmp_path / "master_resume.md"
    master.write_text("Known as the glue across the lab and ops teams over the years.\n")
    source_lines = load_source_lines(master, "master_resume.md")

    items = build_flagged_items(iter_critique_files(root), source_lines)

    anchored = [it for it in items if it.anchor is not None]
    unanchored = [it for it in items if it.anchor is None]
    # The "glue" quote anchors to master; the generated opener does not.
    assert len(anchored) == 1
    assert anchored[0].company == "Acme"
    assert anchored[0].anchor.file == "master_resume.md"
    assert "glue" in anchored[0].recruiter_sentence
    assert len(unanchored) == 1
    assert "reshaped" in unanchored[0].quote
