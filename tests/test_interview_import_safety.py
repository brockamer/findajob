"""Characterization test: importing `findajob.interview.*` is side-effect-free.

Pre-extraction `scripts/interview_prep.py` ran `load_env()` at module
top-level. After this PR (M3 PR #4) the call lives inside `main()`.

Per the test-ordering lesson learned in PR #542: only reimport
`orchestrator.py` (the module that had the original `load_env()`).
Reimporting `role_runner.py` would invalidate
`from findajob.interview.role_runner import run_role` references taken
at collection by other tests.
"""

from __future__ import annotations

import importlib
import sys


def _reimport(name: str):
    sys.modules.pop(name, None)
    return importlib.import_module(name)


def test_orchestrator_loads_without_env_read(monkeypatch):
    """Importing `findajob.interview.orchestrator` must not call load_env()."""
    calls: list[object] = []

    import findajob.utils

    monkeypatch.setattr(findajob.utils, "load_env", lambda *a, **kw: calls.append(("load_env", a, kw)) or {})

    _reimport("findajob.interview.orchestrator")

    assert calls == [], f"importing findajob.interview.orchestrator called load_env() {len(calls)} time(s); expected 0"


def test_orchestrator_exposes_main_and_helpers():
    """`main`, `notify`, `_latest`, `_read_or_empty` are deliberate public symbols."""
    from findajob.interview.orchestrator import _latest, _read_or_empty, main, notify

    assert callable(main)
    assert callable(notify)
    assert callable(_latest)
    assert callable(_read_or_empty)


def test_sentinel_module_loads_without_env_or_db():
    """`findajob.interview.sentinel` must import cleanly without env or DB setup."""
    from findajob.interview.sentinel import (
        SENTINEL_NAME,
        SENTINEL_STALE_AFTER_SECONDS,
        _sentinel_blocks_run,
    )

    assert SENTINEL_NAME == ".interview_prep_in_progress"
    assert SENTINEL_STALE_AFTER_SECONDS == 600
    assert callable(_sentinel_blocks_run)


def test_role_runner_module_callable():
    """`findajob.interview.role_runner.run_role` is the public symbol the orchestrator imports."""
    from findajob.interview.role_runner import run_role

    assert callable(run_role)


def test_run_role_duplication_acknowledged():
    """`run_role` in interview is behaviorally identical to the prep copy.

    This is deliberate: the M3 import-only discipline forbids consolidation
    in the same PR as the move. The cleanup PR (M3+ or M3's 6th child)
    folds both into `findajob.llm.role_runner`.

    Compare bytecode rather than source text — `co_code` ignores
    docstrings, comments, and whitespace, so the two module-level
    docstrings can describe their respective contexts while this test
    still catches real drift in the logic. Function signature is checked
    separately so a parameter rename or type-hint change also fails.
    """
    import inspect

    from findajob.interview import role_runner as interview_role_runner
    from findajob.prep import role_runner as prep_role_runner

    interview_fn = interview_role_runner.run_role
    prep_fn = prep_role_runner.run_role

    assert interview_fn.__code__.co_code == prep_fn.__code__.co_code, (
        "run_role() bytecode in findajob.interview.role_runner has drifted from "
        "findajob.prep.role_runner. Both must stay behavior-equivalent until "
        "the cleanup PR consolidates them into findajob.llm.role_runner."
    )

    assert inspect.signature(interview_fn) == inspect.signature(prep_fn), (
        "run_role() signature differs between findajob.interview.role_runner and "
        "findajob.prep.role_runner. Both must stay equivalent until consolidation."
    )
