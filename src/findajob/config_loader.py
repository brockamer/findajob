"""Loads prefilter rules and companies-of-interest from gitignored configs.

Reads from BASE/config/:
  - prefilter_rules.yaml        (hard_rejects + context_suppressors)
  - in_domain_patterns.yaml     (positive + poison)
  - companies_of_interest.txt   (one company per line; case-insensitive)

Missing files emit a UserWarning and return no-op sentinels so the pipeline
degrades gracefully on a fresh install. Malformed files raise ConfigError.
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path
from typing import Optional

import yaml

from findajob.paths import BASE

# Module-level paths (overridden in tests via conftest)
_RULES_PATH = Path(BASE) / "config" / "prefilter_rules.yaml"
_IN_DOMAIN_PATH = Path(BASE) / "config" / "in_domain_patterns.yaml"
_COMPANIES_PATH = Path(BASE) / "config" / "companies_of_interest.txt"

# Sentinel regex that never matches anything. Used when a config is missing
# or empty. Returned in place of None so callers don't need a None-check.
_NEVER_MATCH = re.compile(r"(?!x)x")

# Caches
_hard_reject_cache: Optional[tuple[re.Pattern[str], Optional[re.Pattern[str]]]] = None
_in_domain_cache: Optional[tuple[re.Pattern[str], Optional[re.Pattern[str]]]] = None
_companies_cache: Optional[frozenset[str]] = None

# Warnings emitted (dedup per process)
_warned: set[str] = set()


class ConfigError(Exception):
    """Raised when a config file is malformed (bad YAML, bad regex, wrong shape)."""


def load_hard_reject_rules() -> tuple[re.Pattern[str], Optional[re.Pattern[str]]]:
    """(reject_re, suppressor_re). suppressor_re is None if no suppressors configured."""
    raise NotImplementedError


def load_in_domain_rules() -> tuple[re.Pattern[str], Optional[re.Pattern[str]]]:
    """(in_domain_re, poison_re). poison_re is None if no poison configured."""
    raise NotImplementedError


def load_companies_of_interest() -> frozenset[str]:
    """Lowercase company names. Used for case-insensitive substring matching."""
    global _companies_cache
    if _companies_cache is not None:
        return _companies_cache

    try:
        raw = _COMPANIES_PATH.read_text()
    except FileNotFoundError:
        _warn_once("config/companies_of_interest.txt missing — sync_sheet archival exception and notify mis-score check will be disabled")
        _companies_cache = frozenset()
        return _companies_cache

    entries: set[str] = set()
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.add(stripped.lower())

    if not entries:
        _warn_once("config/companies_of_interest.txt is empty — sync_sheet archival exception and notify mis-score check will be disabled")

    _companies_cache = frozenset(entries)
    return _companies_cache


def is_company_of_interest(company: str) -> bool:
    """Case-insensitive substring check. False for empty/None inputs."""
    if not company:
        return False
    c = company.lower()
    return any(t in c for t in load_companies_of_interest())


def _warn_once(msg: str) -> None:
    """Emit a UserWarning only once per process. Deduped via _warned set."""
    if msg in _warned:
        return
    _warned.add(msg)
    warnings.warn(msg, UserWarning, stacklevel=3)


def _reset_cache() -> None:
    """Test-only. Clears module-level caches and warning dedup."""
    global _hard_reject_cache, _in_domain_cache, _companies_cache
    _hard_reject_cache = None
    _in_domain_cache = None
    _companies_cache = None
    _warned.clear()
