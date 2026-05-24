"""Unit tests for the materials folder file-grouping helper.

Locks in:
  - workflow ordering (JD → Briefing → Resume → Resume Changes → Cover →
    Outreach → Critique → Review → Other)
  - md/docx pair detection
  - .md and .txt classified is_view=True; .docx classified is_view=False
  - outreach recipient extraction
  - speculative briefing.md special case
"""

from __future__ import annotations

from pathlib import Path

import pytest

from findajob.web.routes.materials import _classify_file, _group_files


def test_classify_jd():
    assert _classify_file("JD - Acme - Senior Engineer.txt") == ("Job Description", 1)


def test_classify_briefing_md_and_docx():
    assert _classify_file("Candidate Briefing - Acme - Senior Engineer - 20260429-101515.md")[0] == "Briefing"
    assert _classify_file("Candidate Briefing - Acme - Senior Engineer - 20260429-101515.docx")[0] == "Briefing"


def test_classify_resume_vs_resume_changes_disambiguation():
    """The Resume Changes rule must match BEFORE the bare Resume rule —
    otherwise " Resume - " wins by substring search and Changes ends up in
    the Resume bucket."""
    assert _classify_file("Candidate Resume Changes - Acme - Senior Engineer - 20260429-101515.md") == (
        "Resume Changes",
        4,
    )
    assert _classify_file("Candidate Resume - Acme - Senior Engineer - 20260429-101515.md") == ("Resume", 3)


def test_classify_speculative_briefing():
    """Speculative submission folders contain a single bare briefing.md
    that doesn't match the regular " Briefing - " pattern."""
    assert _classify_file("briefing.md") == ("Briefing (speculative)", 2)


def test_classify_outreach():
    assert _classify_file("Candidate Outreach to Jane Recruiter - Acme - 20260429-101515.txt")[0] == "Outreach"


def test_classify_interview_prep():
    """Interview Prep artifacts (.md + .docx pair, written by
    findajob.interview.orchestrator on POST /board/jobs/{fp}/interview) must
    classify into their own group — not fall to Other. Regression guard for
    #666 parity with #210's edit + copy-MD surface."""
    md = _classify_file("Candidate Interview Prep - Acme - Senior Engineer - 20260514-101515.md")
    docx = _classify_file("Candidate Interview Prep - Acme - Senior Engineer - 20260514-101515.docx")
    assert md[0] == "Interview Prep"
    assert docx[0] == "Interview Prep"
    # Sort order must be > Review Checklist (8) so Interview Prep sits at the
    # end of the per-folder view — it's the last artifact generated, and only
    # appears for jobs that have advanced to the Interviewing stage.
    assert md[1] > 8


def test_classify_unrecognized_falls_to_other():
    label, order = _classify_file("random.md")
    assert label == "Other"
    assert order == 99


def test_group_files_orders_workflow_sections(tmp_path: Path):
    """Real prep folder filenames produce the expected workflow ordering."""
    files = [
        "Candidate Resume - Acme - Sr Eng - 20260429-101515.docx",
        "Candidate Cover - Acme - Sr Eng - 20260429-101515.md",
        "JD - Acme - Sr Eng.txt",
        "Candidate Briefing - Acme - Sr Eng - 20260429-101515.md",
        "Candidate Critique - Acme - Sr Eng - 20260429-101515.md",
        "Review Checklist - Acme - Sr Eng.md",
        "Candidate Outreach to Jane Recruiter - Acme - 20260429-101515.txt",
        "Candidate Resume Changes - Acme - Sr Eng - 20260429-101515.md",
        "Candidate Resume - Acme - Sr Eng - 20260429-101515.md",
        "Candidate Cover - Acme - Sr Eng - 20260429-101515.docx",
        "Candidate Briefing - Acme - Sr Eng - 20260429-101515.docx",
        "Candidate Interview Prep - Acme - Sr Eng - 20260514-101515.md",
    ]
    for name in files:
        (tmp_path / name).write_text("x")

    groups = _group_files(tmp_path)
    labels = [g["label"] for g in groups]

    # Workflow order: JD → Briefing → Resume → Resume Changes → Cover →
    # Outreach → Critique → Review → Interview Prep.
    # Interview Prep sits at the tail — it's only generated post-application
    # when the user advances to the Interviewing stage.
    assert labels == [
        "Job Description",
        "Briefing",
        "Resume",
        "Resume Changes",
        "Cover Letter",
        "Outreach",
        "Recruiter Critique",
        "Review Checklist",
        "Interview Prep",
    ]


def test_group_files_md_before_docx_within_section(tmp_path: Path):
    """Within each group, .md (View) sorts before .docx (Download) so
    the 'preview in browser' option is the first thing the user sees."""
    (tmp_path / "Candidate Briefing - Acme - Sr Eng - 20260429-101515.docx").write_text("x")
    (tmp_path / "Candidate Briefing - Acme - Sr Eng - 20260429-101515.md").write_text("y")
    groups = _group_files(tmp_path)
    briefing = next(g for g in groups if g["label"] == "Briefing")
    assert [f["ext"] for f in briefing["files"]] == ["md", "docx"]


def test_group_files_view_vs_download_affordance(tmp_path: Path):
    """is_view drives the View-vs-Download button choice in the template.
    .md and .txt are View; .docx is Download."""
    (tmp_path / "Candidate Briefing - Acme - Sr Eng - 20260429-101515.md").write_text("x")
    (tmp_path / "Candidate Briefing - Acme - Sr Eng - 20260429-101515.docx").write_text("x")
    (tmp_path / "JD - Acme - Sr Eng.txt").write_text("x")

    groups = _group_files(tmp_path)
    by_ext = {f["ext"]: f["is_view"] for g in groups for f in g["files"]}
    assert by_ext == {"md": True, "docx": False, "txt": True}


def test_group_files_extracts_outreach_recipient(tmp_path: Path):
    (tmp_path / "Candidate Outreach to Jane Recruiter - Acme - 20260429-101515.txt").write_text("x")
    groups = _group_files(tmp_path)
    outreach = next(g for g in groups if g["label"] == "Outreach")
    assert outreach["files"][0]["recipient"] == "Jane Recruiter"


def test_group_files_handles_speculative_only_folder(tmp_path: Path):
    """Speculative submission folders contain only briefing.md."""
    (tmp_path / "briefing.md").write_text("# briefing")
    groups = _group_files(tmp_path)
    assert len(groups) == 1
    assert groups[0]["label"] == "Briefing (speculative)"
    assert groups[0]["files"][0]["name"] == "briefing.md"


def test_group_files_includes_size_and_mtime(tmp_path: Path):
    p = tmp_path / "JD - Acme - Sr Eng.txt"
    p.write_text("hello")
    groups = _group_files(tmp_path)
    f = groups[0]["files"][0]
    assert f["size"]  # non-empty (e.g., "5 B")
    assert "UTC" in f["mtime"]


def test_group_files_skips_subdirectories(tmp_path: Path):
    """Folders like .interview_prep_in_progress sentinels would otherwise
    pollute the listing — grouping is files-only."""
    (tmp_path / "JD - Acme.txt").write_text("x")
    (tmp_path / "subfolder").mkdir()
    (tmp_path / "subfolder" / "nested.md").write_text("x")
    groups = _group_files(tmp_path)
    names = [f["name"] for g in groups for f in g["files"]]
    assert names == ["JD - Acme.txt"]


@pytest.mark.parametrize(
    "filename,expected_label",
    [
        ("JD - Acme - Sr Eng.txt", "Job Description"),
        ("Review Checklist - Acme - Sr Eng.md", "Review Checklist"),
        ("Alpha Briefing - Acme - Manager - 20260429-101515.md", "Briefing"),
        ("Beta Cover - Acme - Engineer - 20260429-101515.docx", "Cover Letter"),
        ("Gamma Interview Prep - Acme - Director - 20260514-101515.md", "Interview Prep"),
    ],
)
def test_classify_works_across_display_names(filename: str, expected_label: str):
    """display_name is configurable per-user (#335) — the classifier
    keys on document-type substring, not the leading display_name."""
    assert _classify_file(filename)[0] == expected_label


def test_group_description_interpolates_title_and_company(tmp_path: Path):
    """Resume / Cover descriptions should name the specific role + employer
    so the user knows at a glance which submission these go with — not a
    generic 'Word document' blurb."""
    (tmp_path / "Candidate Resume - Acme - Sr Eng - 20260429-101515.docx").write_text("x")
    (tmp_path / "Candidate Cover - Acme - Sr Eng - 20260429-101515.docx").write_text("x")
    groups = _group_files(tmp_path, title="Senior Engineer", company="Acme")
    descs = {g["label"]: g["description"] for g in groups}
    assert "Senior Engineer" in descs["Resume"]
    assert "Acme" in descs["Resume"]
    assert "submit" in descs["Resume"].lower()
    assert "Senior Engineer" in descs["Cover Letter"]
    assert "Acme" in descs["Cover Letter"]


def test_briefing_description_does_not_mention_submit(tmp_path: Path):
    """The briefing is internal prep — it must NOT read like a submission
    artifact. Regression guard against the prior 'best for applications'
    blurb that appeared on every .docx including the briefing."""
    (tmp_path / "Candidate Briefing - Acme - Sr Eng - 20260429-101515.docx").write_text("x")
    (tmp_path / "Candidate Briefing - Acme - Sr Eng - 20260429-101515.md").write_text("x")
    groups = _group_files(tmp_path, title="Sr Eng", company="Acme")
    briefing = next(g for g in groups if g["label"] == "Briefing")
    assert "submit" not in briefing["description"].lower()
    assert "for your eyes only" in briefing["description"].lower()
    # Per-file hints should also not mention "submit" for briefing
    for f in briefing["files"]:
        assert "submit" not in f["hint"].lower()


def test_internal_prep_groups_marked_for_your_eyes_only(tmp_path: Path):
    """Resume Changes, Recruiter Critique, and Interview Prep are internal-only
    diagnostics/notes that the user should never paste into an application —
    flag explicitly."""
    (tmp_path / "Candidate Resume Changes - Acme - Sr Eng - 20260429-101515.md").write_text("x")
    (tmp_path / "Candidate Critique - Acme - Sr Eng - 20260429-101515.md").write_text("x")
    (tmp_path / "Candidate Interview Prep - Acme - Sr Eng - 20260514-101515.md").write_text("x")
    groups = {g["label"]: g for g in _group_files(tmp_path, title="Sr Eng", company="Acme")}
    assert "for your eyes only" in groups["Resume Changes"]["description"].lower()
    assert "for your eyes only" in groups["Recruiter Critique"]["description"].lower()
    assert "for your eyes only" in groups["Interview Prep"]["description"].lower()


def test_outreach_hint_mentions_paste_destination(tmp_path: Path):
    """Outreach .txt rows should tell the user where to paste them — that's
    the whole point of the file. Generic 'Plain text' isn't useful."""
    (tmp_path / "Candidate Outreach to Jane Recruiter - Acme - 20260429-101515.txt").write_text("x")
    groups = _group_files(tmp_path, title="Sr Eng", company="Acme")
    outreach = next(g for g in groups if g["label"] == "Outreach")
    f = outreach["files"][0]
    assert "linkedin" in f["hint"].lower() or "email" in f["hint"].lower()


def test_group_files_default_args_still_work(tmp_path: Path):
    """Title/company are optional kwargs — calling without them should not
    raise, and descriptions should still render with sensible fallbacks."""
    (tmp_path / "Candidate Resume - Acme - Sr Eng - 20260429-101515.docx").write_text("x")
    groups = _group_files(tmp_path)
    desc = next(g["description"] for g in groups if g["label"] == "Resume")
    assert desc  # non-empty
    assert "{title}" not in desc and "{company}" not in desc


def test_group_files_hides_applied_snapshots_and_bak(tmp_path: Path):
    """*.applied-YYYY-MM-DD.md (audit snapshots from #210) and *.bak (edit
    backups) must not surface in the per-folder UI — they exist for
    audit/recovery, not day-to-day display."""
    # Live materials — these must surface.
    (tmp_path / "Candidate Resume - Acme - Sr Eng - 20260429-101515.md").write_text("x")
    (tmp_path / "Candidate Cover - Acme - Sr Eng - 20260429-101515.md").write_text("x")
    # Hidden — apply-time snapshots.
    (tmp_path / "Candidate Resume - Acme - Sr Eng - 20260429-101515.applied-2026-05-13.md").write_text("x")
    (tmp_path / "Candidate Cover - Acme - Sr Eng - 20260429-101515.applied-2026-05-13.md").write_text("x")
    # Hidden — edit backups.
    (tmp_path / "Candidate Resume - Acme - Sr Eng - 20260429-101515.md.bak").write_text("x")

    groups = _group_files(tmp_path)
    names_by_group = {g["label"]: [f["name"] for f in g["files"]] for g in groups}

    # Resume group has the live .md only — not the snapshot, not the .bak.
    assert "Resume" in names_by_group
    resume_names = names_by_group["Resume"]
    assert any(n.endswith(".md") and ".applied-" not in n and not n.endswith(".bak") for n in resume_names)
    assert not any(".applied-" in n for n in resume_names)
    assert not any(n.endswith(".bak") for n in resume_names)
    # Same for Cover.
    cover_names = names_by_group["Cover Letter"]
    assert not any(".applied-" in n for n in cover_names)
