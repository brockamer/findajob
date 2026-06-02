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

from findajob.find_contacts import find_contacts as _canonical_find_contacts


def find_contacts(company: str | None) -> list[str]:
    """Return ``"<name> (<title>)"`` for each LinkedIn connection at *company*.

    Guards blank/None *before* delegating: the canonical ``company_match`` has
    no None-guard, so ``_canonical_find_contacts(None)`` would raise inside the
    reader and log a spurious ``find_contacts_error`` for a perfectly normal
    empty-company job (#963).
    """
    if not company or not company.strip():
        return []
    return [f"{c['name']} ({c['title']})" for c in _canonical_find_contacts(company)]
