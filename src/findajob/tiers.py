"""Tier resolver for jobs.company_tier population.

Reads ``config/companies_of_interest.txt`` (one company per line,
case-insensitive) and classifies:

* ``'tier1'`` — name matches a line in the file
* ``'other'`` — file exists but no match
* ``'unknown'`` — file missing, empty company, or read error
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from findajob.paths import BASE


def _coi_path() -> Path:
    return Path(BASE) / "config" / "companies_of_interest.txt"


@lru_cache(maxsize=1)
def load_tier1_companies() -> frozenset[str]:
    """Lowercased frozenset of Tier 1 company names."""
    try:
        text = _coi_path().read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return frozenset()
    return frozenset(line.strip().lower() for line in text.splitlines() if line.strip())


def resolve_tier(company: str | None) -> str:
    """Return 'tier1', 'other', or 'unknown' for a company name."""
    if not company:
        return "unknown"
    if not _coi_path().is_file():
        return "unknown"
    names = load_tier1_companies()
    return "tier1" if company.strip().lower() in names else "other"
