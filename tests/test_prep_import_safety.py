"""Characterization test: importing `findajob.prep.*` is side-effect-free.

Pre-extraction `scripts/prep_application.py` ran `load_env()` at module
import. After M3 PR #3 the call lives inside `main()`. This test fails
if any module re-introduces module-load env reads.

The duplication invariant for `run_role()` is now obsolete: M3's
cleanup PR consolidated the two copies (prep + interview) into
`findajob.llm.role_runner`. The orchestrator imports from there.
"""

from __future__ import annotations

import importlib
import sys


def _reimport(name: str):
    sys.modules.pop(name, None)
    return importlib.import_module(name)


def test_orchestrator_loads_without_env_read(monkeypatch):
    """Importing `findajob.prep.orchestrator` must not call load_env().

    Re-importing only the orchestrator is the right scope:
    `docx_postprocess.py` never had module-load env reads, and
    reimporting it would invalidate
    `from findajob.prep.docx_postprocess import _linkify_contact_info`
    references already taken by other tests in the suite (the closure
    points at the old module's globals).
    """
    calls: list[object] = []

    import findajob.paths

    monkeypatch.setattr(findajob.paths, "load_env", lambda *a, **kw: calls.append(("load_env", a, kw)) or {})

    _reimport("findajob.prep.orchestrator")

    assert calls == [], f"importing findajob.prep.orchestrator called load_env() {len(calls)} time(s); expected 0"


def test_orchestrator_exposes_main():
    """`main` is the deliberate public symbol of the orchestrator.

    `abbrev_title` was consolidated to `findajob.prep_naming` in #556 —
    callers (this module + `scripts/rename_folders.py`) now import from
    there. `notify` was removed in the M3 cleanup PR — callers now import
    `quick_notify` from `findajob.notifications.ntfy`.
    """
    from findajob.prep.orchestrator import main

    assert callable(main)


def test_abbrev_title_behavior():
    """Behavior preserved across the M3+ consolidation (#556).

    Canonical home is `findajob.prep_naming` — co-located with
    `safe_filename_part`. Folder convention from CLAUDE.md:
    `{Company}_{AbbrevTitle}_{YYYY-MM-DD}_{HHMMSS}`.
    """
    from findajob.prep_naming import abbrev_title

    assert abbrev_title("Senior Software Engineer") == "Senior_Software_Engineer"
    assert abbrev_title("Senior Software Engineer III") == "Senior_Software_Engineer"
    assert abbrev_title("Engineer (Backend)") == "Engineer"
    # Non-word punctuation stripped; whitespace collapsed.
    assert abbrev_title("Sr. Eng., Distributed Systems") == "Sr_Eng_Distributed"
    # Empty / whitespace-only / pure-punctuation falls through to "Job"
    assert abbrev_title("") == "Job"
    assert abbrev_title("   ") == "Job"
    assert abbrev_title("()") == "Job"


def test_docx_postprocess_helpers_callable():
    """`_add_cover_letter_spacing` and `_linkify_contact_info` are the public API."""
    from findajob.prep.docx_postprocess import _add_cover_letter_spacing, _linkify_contact_info

    assert callable(_add_cover_letter_spacing)
    assert callable(_linkify_contact_info)


def test_linkify_contact_info_behavior():
    """Bare emails + LinkedIn URLs become Markdown hyperlinks; existing links untouched."""
    from findajob.prep.docx_postprocess import _linkify_contact_info

    # Bare email → mailto link
    assert _linkify_contact_info("Email: alice@example.com") == "Email: [alice@example.com](mailto:alice@example.com)"

    # Bare LinkedIn URL → https link
    assert _linkify_contact_info("LinkedIn: linkedin.com/in/alice") == (
        "LinkedIn: [linkedin.com/in/alice](https://linkedin.com/in/alice)"
    )

    # Already-linked content untouched
    untouched = "[alice@example.com](mailto:alice@example.com)"
    assert _linkify_contact_info(untouched) == untouched
