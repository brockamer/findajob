"""Characterization test: importing `findajob.prep.*` is side-effect-free.

Pre-extraction (PR #539-era), `scripts/prep_application.py` ran
`load_env()` at module import — silent file I/O on every test that
imported the script.

After this PR (M3 PR #3), env loading lives inside `main()`. This test
fails if any module re-introduces module-load env reads.

Also locks the cross-script duplication invariant: `run_role()` in
`findajob.prep.role_runner` is byte-equivalent to the copy in
`scripts/interview_prep.py` until the cleanup PR consolidates them
into `findajob.llm.role_runner`.
"""

from __future__ import annotations

import importlib
import sys


def _reimport(name: str):
    sys.modules.pop(name, None)
    return importlib.import_module(name)


def test_orchestrator_loads_without_env_read(monkeypatch):
    """Importing `findajob.prep.orchestrator` must not call load_env().

    The original `scripts/prep_application.py` had `load_env()` at module
    top-level. After this PR the call lives inside `main()`. Re-importing
    only the orchestrator is the right scope: `role_runner.py` and
    `docx_postprocess.py` never had module-load env reads, and reimporting
    them would invalidate `from findajob.prep.role_runner import run_role`
    references already taken by other tests in the suite (the closure
    points at the old module's globals).
    """
    calls: list[object] = []

    import findajob.utils

    monkeypatch.setattr(findajob.utils, "load_env", lambda *a, **kw: calls.append(("load_env", a, kw)) or {})

    _reimport("findajob.prep.orchestrator")

    assert calls == [], f"importing findajob.prep.orchestrator called load_env() {len(calls)} time(s); expected 0"


def test_orchestrator_exposes_main_and_helpers():
    """`main`, `abbrev_title`, and `notify` are deliberate public symbols."""
    from findajob.prep.orchestrator import abbrev_title, main, notify

    assert callable(main)
    assert callable(abbrev_title)
    assert callable(notify)


def test_abbrev_title_behavior():
    """Behavior preserved from scripts/prep_application.py.

    Folder convention: `{Company}_{AbbrevTitle}_{YYYY-MM-DD}_{HHMMSS}` —
    title abbreviated to first 3 words, underscored. CLAUDE.md "Output
    Folder Format" rule.
    """
    from findajob.prep.orchestrator import abbrev_title

    assert abbrev_title("Senior Software Engineer") == "Senior_Software_Engineer"
    assert abbrev_title("Senior Software Engineer III") == "Senior_Software_Engineer"
    assert abbrev_title("Engineer (Backend)") == "Engineer"
    # Non-word punctuation stripped; whitespace collapsed.
    assert abbrev_title("Sr. Eng., Distributed Systems") == "Sr_Eng_Distributed"
    # Empty / whitespace-only / pure-punctuation falls through to "Job"
    assert abbrev_title("") == "Job"
    assert abbrev_title("   ") == "Job"
    assert abbrev_title("()") == "Job"


def test_run_role_module_callable():
    """`findajob.prep.role_runner.run_role` is the public symbol the orchestrator imports."""
    from findajob.prep.role_runner import run_role

    assert callable(run_role)


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
