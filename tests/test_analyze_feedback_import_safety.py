"""Characterization test: importing ``findajob.analyze_feedback`` is side-effect-free.

Pre-extraction ``scripts/analyze_feedback.py`` ran ``load_reject_reasons()``
at module-load (line 240 of the original) to populate
``_TITLE_SIGNAL_REASONS`` at module scope. After M3+ #558 the call lives
inside ``_prefilter_candidates()``. This test fails if anything
re-introduces module-load YAML reads.

``load_env()`` was already inside ``main()`` pre-extraction; this test
locks that as well to keep the contract symmetric with other M3+ extractions.

Mirrors the M3 import-safety pattern from ``test_prep_import_safety.py``,
``test_triage_import_safety.py``, and ``test_find_contacts_import_safety.py``.
"""

from __future__ import annotations

import importlib
import sys


def _reimport(name: str):
    sys.modules.pop(name, None)
    return importlib.import_module(name)


def test_analyze_feedback_loads_without_yaml_read(monkeypatch):
    """Importing ``findajob.analyze_feedback`` must not call load_reject_reasons().

    The pre-extraction module-scope ``_, _TITLE_SIGNAL_REASONS = load_reject_reasons()``
    is gone — moved into ``_prefilter_candidates()``. Callers that import
    ``analyze`` (e.g. ``findajob.notifications.scoreboard``) get
    side-effect-free behavior.
    """
    calls: list[object] = []

    import findajob.config_loader

    monkeypatch.setattr(
        findajob.config_loader,
        "load_reject_reasons",
        lambda *a, **kw: calls.append(("load_reject_reasons", a, kw)) or ([], frozenset()),
    )

    _reimport("findajob.analyze_feedback")

    assert calls == [], (
        f"importing findajob.analyze_feedback called load_reject_reasons() {len(calls)} time(s); expected 0"
    )


def test_analyze_feedback_loads_without_env_read(monkeypatch):
    """Importing ``findajob.analyze_feedback`` must not call load_env().

    ``load_env()`` is only used inside ``main()`` for the --notify flag's
    ``NTFY_TOPIC`` lookup. Module-load env reads were never present in
    the script's history, but locking the property prevents future drift.
    """
    calls: list[object] = []

    import findajob.paths

    monkeypatch.setattr(findajob.paths, "load_env", lambda *a, **kw: calls.append(("load_env", a, kw)) or {})

    _reimport("findajob.analyze_feedback")

    assert calls == [], f"importing findajob.analyze_feedback called load_env() {len(calls)} time(s); expected 0"


def test_analyze_feedback_exposes_main():
    """``main`` is the deliberate public entry point for the subprocess shim."""
    from findajob.analyze_feedback import main

    assert callable(main)


def test_analyze_feedback_exposes_analyze():
    """``analyze`` is the deliberate public symbol for in-process callers
    (notifications.scoreboard imports it directly post-#558).
    """
    from findajob.analyze_feedback import analyze, format_report

    assert callable(analyze)
    assert callable(format_report)


def test_scoreboard_imports_from_canonical_location():
    """``findajob.notifications.scoreboard`` imports ``analyze`` from the
    canonical location, not via ``sys.path.insert + from analyze_feedback``.

    Locks the no-re-export-theater discipline: post-#558 the dynamic-import
    hack from #544 is gone. Future readers can't accidentally reintroduce it.
    """
    import findajob.notifications.scoreboard as scoreboard_mod

    # `feedback_analyze` is the local alias used in scoreboard.py
    assert hasattr(scoreboard_mod, "feedback_analyze")

    # Prove it's the canonical reference, not a re-import
    from findajob.analyze_feedback import analyze as canonical_analyze

    assert scoreboard_mod.feedback_analyze is canonical_analyze
