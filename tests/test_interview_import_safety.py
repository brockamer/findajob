"""Characterization test: importing `findajob.interview.*` is side-effect-free.

Pre-extraction `scripts/interview_prep.py` ran `load_env()` at module
top-level. After M3 PR #4 the call lives inside `main()`.

Per the test-ordering lesson from PR #542: only reimport
`orchestrator.py` (the module that had the original `load_env()`).

M6 (2026-05-08): the `findajob.interview.sentinel` module was deleted
along with its sentinel-file concurrency control — replaced by the
`background_tasks` row contract (see `findajob.background_tasks`). The
`test_sentinel_module_loads_without_env_or_db` test went with it.
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
