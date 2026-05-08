"""Characterization test: importing `findajob.interview.*` is side-effect-free.

Pre-extraction `scripts/interview_prep.py` ran `load_env()` at module
top-level. After M3 PR #4 the call lives inside `main()`.

Per the test-ordering lesson from PR #542: only reimport
`orchestrator.py` (the module that had the original `load_env()`).
Reimporting `sentinel.py` would invalidate
`from findajob.interview.sentinel import _sentinel_blocks_run`
references taken at collection by other tests.

The earlier `test_run_role_duplication_acknowledged` bytecode guard
(against the prep copy) is removed — its job ended when the cleanup PR
deleted both `findajob.{prep,interview}.role_runner` and consolidated
into `findajob.llm.role_runner`.
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

    import findajob.paths

    monkeypatch.setattr(findajob.paths, "load_env", lambda *a, **kw: calls.append(("load_env", a, kw)) or {})

    _reimport("findajob.interview.orchestrator")

    assert calls == [], f"importing findajob.interview.orchestrator called load_env() {len(calls)} time(s); expected 0"


def test_orchestrator_exposes_main_and_helpers():
    """`main`, `_latest`, `_read_or_empty` are the deliberate public symbols.

    `notify` was removed from this module by the M3 cleanup PR — callers
    now import `quick_notify` from `findajob.notifications.ntfy`.
    """
    from findajob.interview.orchestrator import _latest, _read_or_empty, main

    assert callable(main)
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
