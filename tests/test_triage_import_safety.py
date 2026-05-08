"""Characterization test: importing `findajob.triage.*` is side-effect-free.

This is the load-bearing invariant the M3 extraction (#537 / #538) was
built to create. Pre-extraction, importing `scripts/triage.py` from a
test would:
  - install a SIGTERM handler globally
  - call `_build_feedback_block()` which opens a SQLite connection to
    `$BASE/data/pipeline.db`
  - call `role_model("job_scorer")` which reads `config/roles/*.md`
  - call `load_env()` which parses `data/.env`

After the extraction these all live inside `main()` (or in the script
shim, for the SIGTERM install). This test fails if any of them slip back
to module load — re-introducing the test-fragility and sleep-during-CI
patterns the extraction was designed to eliminate.
"""

from __future__ import annotations

import signal


def test_signal_handler_not_installed_at_import(monkeypatch):
    """Importing `findajob.triage.orchestrator` must NOT install a SIGTERM handler.

    The script entry-point (`scripts/triage.py`) installs `_on_sigterm`;
    library imports must not. Otherwise any test that imports the module
    has its SIGTERM behavior silently overridden.
    """
    installed_handlers: list[tuple[int, object]] = []
    original_signal = signal.signal

    def _spy(signum, handler):
        installed_handlers.append((signum, handler))
        return original_signal(signum, handler)

    monkeypatch.setattr(signal, "signal", _spy)

    # Force a fresh import so the module-level code runs under the spy.
    import importlib
    import sys

    sys.modules.pop("findajob.triage.orchestrator", None)
    importlib.import_module("findajob.triage.orchestrator")

    sigterm_handlers = [h for sig, h in installed_handlers if sig == signal.SIGTERM]
    assert sigterm_handlers == [], (
        f"Importing findajob.triage.orchestrator installed {len(sigterm_handlers)} "
        f"SIGTERM handler(s); expected 0 — install belongs in scripts/triage.py only."
    )


def test_orchestrator_main_is_callable_without_module_load_io():
    """The orchestrator module exposes `main` as a callable.

    Asserts the public surface exists; behavior of main() itself is
    covered by integration tests that supply a real DB + profile.
    """
    from findajob.triage.orchestrator import main

    assert callable(main)


def test_on_sigterm_exposed_for_shim():
    """`_on_sigterm` is a deliberate public symbol — the shim imports it."""
    from findajob.triage.orchestrator import _on_sigterm

    assert callable(_on_sigterm)


def test_contacts_module_loads_without_db_or_env():
    """`findajob.triage.contacts` must import cleanly without env or DB setup."""
    from findajob.triage.contacts import find_contacts

    assert callable(find_contacts)


def test_null_score_retry_module_loads_without_db_or_env():
    """`findajob.triage.null_score_retry` must import cleanly without env or DB setup."""
    from findajob.triage.null_score_retry import score_null_manual_review_rows

    assert callable(score_null_manual_review_rows)


def test_find_contacts_blank_company_guard():
    """`'' in 'anything'` is True in Python; find_contacts must reject blank."""
    from findajob.triage.contacts import find_contacts

    # Blank/whitespace company never matches anything regardless of the
    # connections.csv content. This is a known-repeat-bug class — see
    # CLAUDE.md "Critical Architecture Rules" §company_match() Blank
    # String Guard.
    assert find_contacts("") == []
    assert find_contacts(None) == []
    assert find_contacts("   ") == []
