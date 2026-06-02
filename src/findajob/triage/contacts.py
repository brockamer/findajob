"""LinkedIn-connections contact lookup for ingested jobs.

Thin adapter over the canonical matcher in ``findajob.find_contacts`` (#963).
The triage orchestrator and the ``known_contacts`` DB column expect a
``list[str]`` of ``"<name> (<title>)"`` strings, so this wraps the canonical
``find_contacts`` — which matches via the #497 word-boundary ``company_match``
— and reformats its structured dicts back to that string shape.

Consolidating ingest and prep onto one matcher fixes the substring-collision
bug the ingest path used to have ("Apple" matching "GreenApple", "AI" matching
"AIRBUS") and removes the drift trap of two divergent implementations.
Extracted from ``scripts/triage.py`` in M3 (#537); folded onto the canonical
matcher in #963.
"""

from __future__ import annotations


def find_contacts(company: str | None) -> list[str]:
    """Return ``"<name> (<title>)"`` for each LinkedIn connection at *company*.

    Guards blank/None *before* delegating: the canonical ``company_match`` has
    no None-guard, so delegating ``None`` would raise inside the reader and log
    a spurious ``find_contacts_error`` for a perfectly normal empty-company job
    (#963).
    """
    if not company or not company.strip():
        return []
    # Resolve the canonical matcher at call time, not via a module-level import.
    # The import-safety tests pop + reimport ``findajob.find_contacts``; a
    # module-level binding would then point at a stale, orphaned module object
    # whose ``CONNECTIONS`` a test's monkeypatch can no longer reach (#963).
    # Call-time resolution always goes through the live ``sys.modules`` entry,
    # and keeps this triage submodule's import surface minimal — no transitive
    # LLM/db imports at module load.
    from findajob.find_contacts import find_contacts as canonical

    return [f"{c['name']} ({c['title']})" for c in canonical(company)]
