"""Static invariants for `config/roles/resume_tailor.md` (#1224).

The tailor's role prompt is instructions to an LLM, so whether a tailored
resume carries Education is behavioral and only verifiable with a live call.
These tests guard the structural cause of #1224: a rule written for one
candidate's master resume ("the master resume notes education as not worth
mentioning") applied to every candidate, and an output section list with no
slot for Education.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROLES_DIR = Path(__file__).parent.parent / "config" / "roles"
_ROLE_PATH = _ROLES_DIR / "resume_tailor.md"


def _read_role() -> str:
    return _ROLE_PATH.read_text(encoding="utf-8")


def _education_rule(text: str) -> str:
    match = re.search(r"^\d+\.\s+\*\*Education[^\n]*", text, re.MULTILINE)
    assert match is not None, "resume_tailor has no numbered Education rule; see #1224"
    return match.group(0)


def test_no_role_prompt_dismisses_education() -> None:
    """No tracked role prompt may describe one candidate's view of their education."""
    for path in sorted(_ROLES_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"education[^.\n]{0,80}not worth", text, re.IGNORECASE), (
            f"{path.name} says education is not worth mentioning; see #1224"
        )
        assert not re.search(r"education[^.\n]{0,40}omit unless required", text, re.IGNORECASE), (
            f"{path.name} omits education unless the JD requires it; see #1224"
        )


def test_section_order_includes_education_after_experience() -> None:
    match = re.search(r"^- Sections in order: ([^\n]+)", _read_role(), re.MULTILINE)
    assert match is not None, "resume_tailor output format lost its section-order line"
    sections = [s.strip() for s in match.group(1).split(".")[0].split(",")]
    assert "Education" in sections, "section order has no Education slot; see #1224"
    assert sections.index("Education") == sections.index("Experience") + 1, (
        "Education must follow Experience in the section order; see #1224"
    )


def test_education_rule_carries_master_section_regardless_of_jd() -> None:
    rule = _education_rule(_read_role())
    assert re.search(r"whether or not the JD", rule, re.IGNORECASE), (
        "Education rule must not make inclusion depend on the JD naming a degree; see #1224"
    )


def test_education_rule_names_where_an_omit_request_lives() -> None:
    """A candidate who wants Education left off says so in the master resume or profile."""
    rule = _education_rule(_read_role())
    assert "## Employer Formatting Rules" in rule, "Education rule must honor an omit request there; see #1224"
    assert "## Things to Avoid Mentioning" in rule, "Education rule must honor an omit request there; see #1224"


def test_education_rule_does_not_invent_a_section() -> None:
    rule = _education_rule(_read_role())
    assert re.search(r"no Education section, do not (add|create) one", rule, re.IGNORECASE), (
        "Education rule must forbid inventing Education for a master resume that has none; see #1224"
    )
