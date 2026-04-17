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
_hard_reject_cache: tuple[re.Pattern[str], re.Pattern[str] | None] | None = None
_in_domain_cache: tuple[re.Pattern[str], re.Pattern[str] | None] | None = None
_companies_cache: frozenset[str] | None = None

# Warnings emitted (dedup per process)
_warned: set[str] = set()


class ConfigError(Exception):
    """Raised when a config file is malformed (bad YAML, bad regex, wrong shape)."""


def load_hard_reject_rules() -> tuple[re.Pattern[str], re.Pattern[str] | None]:
    """(reject_re, suppressor_re). suppressor_re is None if no suppressors configured."""
    global _hard_reject_cache
    if _hard_reject_cache is not None:
        return _hard_reject_cache

    data = _safe_load_yaml(_RULES_PATH, "prefilter_rules.yaml")
    if data is None:
        _hard_reject_cache = (_NEVER_MATCH, None)
        return _hard_reject_cache

    hard_rejects = data.get("hard_rejects", {})
    if not isinstance(hard_rejects, dict):
        raise ConfigError(
            f"prefilter_rules.yaml: 'hard_rejects' must be a mapping of category→list, "
            f"got {type(hard_rejects).__name__}"
        )

    reject_patterns: list[str] = []
    for category, patterns in hard_rejects.items():
        if not isinstance(patterns, list):
            raise ConfigError(
                f"prefilter_rules.yaml: hard_rejects['{category}'] must be a list, got {type(patterns).__name__}"
            )
        for p in patterns:
            if not isinstance(p, str):
                raise ConfigError(f"prefilter_rules.yaml: pattern in '{category}' is not a string: {p!r}")
            reject_patterns.append(p)

    reject_re = _compile_patterns(reject_patterns, _RULES_PATH, "hard_rejects")

    suppressors = data.get("context_suppressors", []) or []
    if not isinstance(suppressors, list):
        raise ConfigError(
            f"prefilter_rules.yaml: 'context_suppressors' must be a list, got {type(suppressors).__name__}"
        )
    for p in suppressors:
        if not isinstance(p, str):
            raise ConfigError(f"prefilter_rules.yaml: context_suppressor pattern is not a string: {p!r}")

    suppressor_re: re.Pattern[str] | None = None
    if suppressors:
        suppressor_re = _compile_patterns(suppressors, _RULES_PATH, "context_suppressors")

    _hard_reject_cache = (reject_re, suppressor_re)
    return _hard_reject_cache


def load_in_domain_rules() -> tuple[re.Pattern[str], re.Pattern[str] | None]:
    """(in_domain_re, poison_re). poison_re is None if no poison configured."""
    global _in_domain_cache
    if _in_domain_cache is not None:
        return _in_domain_cache

    data = _safe_load_yaml(_IN_DOMAIN_PATH, "in_domain_patterns.yaml")
    if data is None:
        _in_domain_cache = (_NEVER_MATCH, None)
        return _in_domain_cache

    positive = data.get("positive", [])
    if not isinstance(positive, list):
        raise ConfigError(f"in_domain_patterns.yaml: 'positive' must be a list, got {type(positive).__name__}")
    for p in positive:
        if not isinstance(p, str):
            raise ConfigError(f"in_domain_patterns.yaml: positive pattern is not a string: {p!r}")

    positive_re = _compile_patterns(positive, _IN_DOMAIN_PATH, "positive")

    poison = data.get("poison", []) or []
    if not isinstance(poison, list):
        raise ConfigError(f"in_domain_patterns.yaml: 'poison' must be a list, got {type(poison).__name__}")
    for p in poison:
        if not isinstance(p, str):
            raise ConfigError(f"in_domain_patterns.yaml: poison pattern is not a string: {p!r}")

    poison_re: re.Pattern[str] | None = None
    if poison:
        poison_re = _compile_patterns(poison, _IN_DOMAIN_PATH, "poison")

    _in_domain_cache = (positive_re, poison_re)
    return _in_domain_cache


def load_companies_of_interest() -> frozenset[str]:
    """Lowercase company names. Used for case-insensitive substring matching."""
    global _companies_cache
    if _companies_cache is not None:
        return _companies_cache

    try:
        raw = _COMPANIES_PATH.read_text()
    except FileNotFoundError:
        _warn_once(
            "config/companies_of_interest.txt missing — "
            "sync_sheet archival exception and notify mis-score check will be disabled"
        )
        _companies_cache = frozenset()
        return _companies_cache

    entries: set[str] = set()
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.add(stripped.lower())

    if not entries:
        _warn_once(
            "config/companies_of_interest.txt is empty — "
            "sync_sheet archival exception and notify mis-score check will be disabled"
        )

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


def _safe_load_yaml(path: Path, label: str) -> dict | None:
    """Read YAML. Returns None if file missing (with warning) or empty.
    Raises ConfigError on parse error or non-mapping top-level."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        _warn_once(f"config/{label} missing — prefilter will be a no-op for this config")
        return None

    if not text.strip():
        _warn_once(f"config/{label} is empty — prefilter will be a no-op for this config")
        return None

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"config/{label}: YAML parse error: {e}") from e

    if data is None:
        _warn_once(f"config/{label} parsed to null — prefilter will be a no-op for this config")
        return None

    if not isinstance(data, dict):
        raise ConfigError(f"config/{label}: top level must be a mapping, got {type(data).__name__}")

    return data


def _compile_patterns(patterns: list[str], path: Path, label: str) -> re.Pattern[str]:
    """Compile a list of regex strings into a single alternation. Bad patterns
    raise ConfigError with the offending pattern surfaced."""
    if not patterns:
        return _NEVER_MATCH
    for p in patterns:
        try:
            re.compile(p)
        except re.error as e:
            raise ConfigError(f"{path.name}: invalid regex in {label}: {p!r} — {e}") from e
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)
