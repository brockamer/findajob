"""#1098 — master-resume sections marked "not for submitted resumes".

``split_marked_sections`` removes marked sections before the master resume
reaches ``resume_tailor``; ``find_marked_text`` detects marked text that
reached a tailored resume anyway (for example through the briefing).
"""

from __future__ import annotations

from findajob.prep.resume_markers import find_marked_text, split_marked_sections

MASTER = (
    "# Jane Candidate\n\n"
    "## Experience\n\n"
    "### Acme — Shift Lead\n"
    "- Led the night shift through a site move without missing a delivery date.\n\n"
    "## Selected Quotes\n"
    "*(For interview prep, cover letters, and outreach — not for submitted resumes)*\n\n"
    '- "She turned a struggling night shift into the steadiest team in the building."\n'
    "- — A. Colleague, Director\n\n"
    "### Older quotes\n"
    '- "Calm under pressure and generous with credit for every success."\n\n'
    "## Skills\n"
    "- Scheduling, inventory control\n"
)

QUOTE_LINE = '- "She turned a struggling night shift into the steadiest team in the building."'


def test_split_removes_marked_section_and_its_subsections() -> None:
    kept, marked = split_marked_sections(MASTER)

    for present in ("## Experience", "## Skills", "Led the night shift"):
        assert present in kept
    for absent in ("Selected Quotes", "steadiest team", "Older quotes", "generous with credit"):
        assert absent not in kept
    for present in ("## Selected Quotes", "steadiest team", "generous with credit"):
        assert present in marked
    assert "## Skills" not in marked


def test_marker_in_heading_marks_section() -> None:
    master = '## Quotes (not for submitted resumes)\n- "x y z"\n## Skills\n- a\n'

    kept, marked = split_marked_sections(master)

    assert '"x y z"' in marked
    assert '"x y z"' not in kept
    assert "## Skills" in kept


def test_marked_section_at_end_of_file() -> None:
    master = "## Skills\n- a\n## Quotes\n_(not for submitted resumes)_\n- tail line one\n- tail line two\n"

    kept, marked = split_marked_sections(master)

    assert kept == "## Skills\n- a\n"
    assert "tail line one" in marked
    assert "tail line two" in marked


def test_no_marker_returns_input_unchanged() -> None:
    master = MASTER.replace("not for submitted resumes", "for reference")

    kept, marked = split_marked_sections(master)

    assert kept == master
    assert marked == ""


def test_crlf_master_splits_like_lf() -> None:
    kept, marked = split_marked_sections(MASTER.replace("\n", "\r\n"))

    assert "steadiest team" in marked
    assert "steadiest team" not in kept
    assert "Led the night shift" in kept
    assert "## Skills" in kept


def test_heading_inside_code_fence_is_not_a_heading() -> None:
    master = (
        "## Quotes\n*(not for submitted resumes)*\n```\n## not a heading\n```\n- fenced section tail\n## Skills\n- a\n"
    )

    kept, marked = split_marked_sections(master)

    assert "fenced section tail" in marked
    assert "## not a heading" in marked
    assert "fenced section tail" not in kept
    assert "## Skills" in kept


def test_find_flags_copied_clause() -> None:
    kept, marked = split_marked_sections(MASTER)
    resume = "## Summary\nShift lead who turned a struggling night shift into the steadiest team.\n"

    assert find_marked_text(resume, marked, kept) == [QUOTE_LINE]


def test_find_ignores_runs_shorter_than_six_words() -> None:
    kept, marked = split_marked_sections(MASTER)
    # Shares exactly five consecutive words with the quote: "struggling night shift into the".
    resume = "## Summary\nRebuilt the struggling night shift into the best crew.\n"

    assert find_marked_text(resume, marked, kept) == []


def test_find_ignores_text_also_in_kept_master() -> None:
    master = MASTER.replace(
        "- — A. Colleague, Director\n",
        "- Led the night shift through a site move without missing a delivery date.\n",
    )
    kept, marked = split_marked_sections(master)
    resume = "## Experience\n- Led the night shift through a site move without missing a delivery date.\n"

    assert find_marked_text(resume, marked, kept) == []


def test_find_ignores_attribution_line() -> None:
    kept, marked = split_marked_sections(MASTER)
    resume = "## References\nA. Colleague, Director\n"

    assert find_marked_text(resume, marked, kept) == []


def test_find_matches_across_case_quotes_and_emphasis() -> None:
    kept, marked = split_marked_sections(MASTER)
    resume = "## Summary\n**Turned a struggling Night Shift into the “steadiest” team**\n"

    assert find_marked_text(resume, marked, kept) == [QUOTE_LINE]


def test_find_with_empty_marked_text() -> None:
    assert find_marked_text("anything at all goes in this resume text", "", MASTER) == []
