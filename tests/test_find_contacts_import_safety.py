"""Characterization test: importing ``findajob.find_contacts`` is side-effect-free.

Pre-extraction ``scripts/find_contacts.py`` ran ``load_env()`` at module
import (line 47 of the original). After M3+ #557 the call lives inside
``main()``. This test fails if anything re-introduces module-load env
reads.

Mirrors the M3 import-safety pattern from ``test_prep_import_safety.py``
and ``test_triage_import_safety.py``.
"""

from __future__ import annotations

import importlib
import sys


def _reimport(name: str):
    sys.modules.pop(name, None)
    return importlib.import_module(name)


def test_find_contacts_loads_without_env_read(monkeypatch):
    """Importing ``findajob.find_contacts`` must not call load_env().

    Per ``feedback_reimport_invalidates_closures``: only reimport modules
    that previously had module-load side effects. ``findajob.find_contacts``
    qualifies because its pre-extraction shape called ``load_env()`` at
    import. No other test files take ``from findajob.find_contacts import
    <fn>`` references at collection time, so the reimport is safe.
    """
    calls: list[object] = []

    import findajob.paths

    monkeypatch.setattr(findajob.paths, "load_env", lambda *a, **kw: calls.append(("load_env", a, kw)) or {})

    _reimport("findajob.find_contacts")

    assert calls == [], f"importing findajob.find_contacts called load_env() {len(calls)} time(s); expected 0"


def test_find_contacts_exposes_main():
    """``main`` is the deliberate public entry point for the subprocess shim."""
    from findajob.find_contacts import main

    assert callable(main)


def test_find_contacts_exposes_logic_helpers():
    """Public helpers used by tests + (in principle) any future direct caller."""
    from findajob.find_contacts import company_match, find_contacts, generate_outreach, rank_contacts

    assert callable(company_match)
    assert callable(find_contacts)
    assert callable(rank_contacts)
    assert callable(generate_outreach)


def test_company_match_blank_string_guard():
    """CLAUDE.md §"company_match() Blank String Guard" — `'' in 'anything'` is True in Python."""
    from findajob.find_contacts import company_match

    assert company_match("", "Acme") is False
    assert company_match("Acme", "") is False
    assert company_match("", "") is False
    # Sanity: real matches still work
    assert company_match("Acme", "Acme Inc") is True
