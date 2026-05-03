"""Shared RapidAPI key resolver (#414).

Both `JobsApi14Adapter` and `JSearchAdapter` (and any future RapidAPI-flavored
adapter) accept the SAME account-level X-RapidAPI-Key — the per-adapter env
var separation in #408 was based on a wrong premise (that each API has its
own credential). This helper looks up a list of candidate env vars and
returns the first non-empty value, treating whitespace-only as unset.

Convention: callers pass the canonical name (`RAPIDAPI_KEY`) first, then any
legacy per-adapter names as fallbacks (e.g. `JOBS_API14_KEY`). New
onboardings write only the canonical; existing tester stacks keep working
via fallback without code-side migration.
"""

from __future__ import annotations

import os


def resolve_rapidapi_key(*candidate_env_vars: str) -> str:
    """Return the first non-empty value among the candidate env vars.

    Whitespace-only values are treated as unset. Returns "" if none set.
    """
    for var in candidate_env_vars:
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return ""
