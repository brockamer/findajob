"""#1099 — the resume change report is checked against the documents.

``resume_diff_summary`` gives the change reviewer ground truth;
``check_change_report`` verifies every passage the reviewer quotes and
renders the report, marking quotes it cannot find ``[UNVERIFIED]``.
"""

from __future__ import annotations

import json

from findajob.prep.change_report import PARSE_FAILED_NOTE, check_change_report, resume_diff_summary

MASTER = "# J\n## Summary\nSteady shift lead.\n## Experience\n- Ran inventory counts weekly.\n- Trained new staff.\n"
TAILORED = "# J\n## Experience\n- ran inventory counts weekly.\n## Summary\nShift lead focused on safety.\n"
EMPTY = {"reordered_sections": [], "added": [], "reworded": [], "removed": [], "missing_info": []}


def _check(**fields: object):
    return check_change_report(json.dumps({**EMPTY, **fields}), MASTER, TAILORED)


def test_diff_summary_sections_and_only_in_lines() -> None:
    out = resume_diff_summary(MASTER, TAILORED)

    assert "SECTION ORDER IN MASTER: Summary | Experience" in out
    assert "SECTION ORDER IN TAILORED: Experience | Summary" in out
    assert "+ Shift lead focused on safety." in out
    assert "- Steady shift lead." in out
    assert "- - Trained new staff." in out
    assert "inventory counts" not in out


def test_diff_summary_empty_lists_say_none() -> None:
    out = resume_diff_summary(MASTER, MASTER)

    assert "LINES ONLY IN TAILORED (new or reworded):\n(none)" in out
    assert "LINES ONLY IN MASTER (removed or reworded):\n(none)" in out


def test_added_claim_absent_from_tailored_is_unverified() -> None:
    report = _check(added=[{"text": "certified forklift trainer", "section": "Summary", "reason": "JD term"}])

    assert (report.claims, report.unsupported) == (1, 1)
    assert '- [UNVERIFIED] "certified forklift trainer"' in report.markdown


def test_added_claim_present_is_verified() -> None:
    report = _check(added=[{"text": "Shift lead focused on **safety**", "section": "Summary", "reason": "JD"}])

    assert (report.claims, report.unsupported) == (1, 0)
    assert '- "Shift lead focused on **safety**" (Summary): JD' in report.markdown
    assert "[UNVERIFIED]" not in report.markdown.split("## Reordered sections")[1]


def test_removed_claim_still_in_tailored_is_unverified() -> None:
    report = _check(removed=[{"text": "Ran inventory counts weekly.", "reason": "space"}])

    assert report.unsupported == 1


def test_removed_claim_not_in_master_is_unverified() -> None:
    report = _check(removed=[{"text": "Managed a fleet of trucks.", "reason": "space"}])

    assert report.unsupported == 1


def test_removed_claim_valid() -> None:
    report = _check(removed=[{"text": "Trained new staff.", "reason": "space"}])

    assert (report.claims, report.unsupported) == (1, 0)
    assert '- "Trained new staff.": space' in report.markdown


def test_reworded_needs_before_in_master_and_after_in_tailored() -> None:
    good = {"before": "Steady shift lead.", "after": "Shift lead focused on safety.", "section": "Summary"}
    bad = {"before": "Steady shift lead.", "after": "Shift lead focused on speed.", "section": "Summary"}

    report = _check(reworded=[good, bad])

    assert (report.claims, report.unsupported) == (2, 1)
    assert '- Before: "Steady shift lead." / After: "Shift lead focused on safety." (Summary)' in report.markdown
    assert '- [UNVERIFIED] Before: "Steady shift lead." / After: "Shift lead focused on speed."' in report.markdown


def test_missing_info_placeholder_must_be_in_tailored() -> None:
    report = _check(missing_info=[{"placeholder": "[MISSING: start date]", "needs": "the start date"}])

    assert report.unsupported == 1


def test_empty_lists_are_a_pass() -> None:
    report = _check()

    assert (report.claims, report.unsupported, report.parse_failed) == (0, 0, False)
    assert "## Added\n- None" in report.markdown


def test_fenced_json_is_accepted() -> None:
    raw = "```json\n" + json.dumps(EMPTY) + "\n```"

    assert check_change_report(raw, MASTER, TAILORED).parse_failed is False


def test_unparseable_output_falls_back() -> None:
    report = check_change_report("REORDERED_SECTIONS: none", MASTER, TAILORED)

    assert report.parse_failed is True
    assert report.markdown.startswith("# Resume Changes\n\n" + PARSE_FAILED_NOTE)
    assert "REORDERED_SECTIONS: none" in report.markdown


def test_non_dict_item_is_unverified() -> None:
    report = _check(added=["just a string"])

    assert report.unsupported == 1
    assert "- [UNVERIFIED] just a string" in report.markdown
