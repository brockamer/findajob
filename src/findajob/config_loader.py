"""Loads prefilter rules and companies-of-interest from gitignored configs.

Reads from BASE/config/:
  - prefilter_rules.yaml        (hard_rejects + context_suppressors + indeed_title_allow)
  - in_domain_patterns.yaml     (positive + poison)
  - target_companies.md         (Tier 1 section parsed for case-insensitive match)

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
_TARGET_COMPANIES_PATH = Path(BASE) / "config" / "target_companies.md"

# Tier 1 parser — moved from findajob.onboarding.injector (#211).
_TIER1_HEADING_RE = re.compile(r"^##\s+tier\s*1\b[^\n]*", re.IGNORECASE | re.MULTILINE)
_NEXT_H2_RE = re.compile(r"^##\s+\S", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*(?:[-*]\s+|\d+\.\s+)(.*)")
_SPLIT_COMMENTARY_RE = re.compile(r"\s+[—-]\s+|\s+\(")

# Sentinel regex that never matches anything. Used when a config is missing
# or empty. Returned in place of None so callers don't need a None-check.
_NEVER_MATCH = re.compile(r"(?!x)x")

# Caches
_hard_reject_cache: tuple[re.Pattern[str], re.Pattern[str] | None] | None = None
_in_domain_cache: tuple[re.Pattern[str], re.Pattern[str] | None] | None = None
_companies_cache: frozenset[str] | None = None
_indeed_title_allow_cache: re.Pattern[str] | None = None
_indeed_title_allow_loaded: bool = False  # distinguishes "cached None" from "not yet loaded"

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


def parse_target_companies_tier1(target_companies_md: str) -> list[str]:
    """Extract Tier 1 company names from `target_companies.md` content.

    Parses the `## Tier 1` section (case-insensitive heading match), reads
    bullets through the next `##` heading or EOF, strips bullet markers
    (`-`, `*`, `1.`), and trims trailing parenthetical or em-dash commentary.

    Returns an ordered list (de-duplication and case-folding are caller
    concerns). Empty list if no Tier 1 section is present.
    """
    match = _TIER1_HEADING_RE.search(target_companies_md)
    if not match:
        return []
    section_start = match.end()
    remainder = target_companies_md[section_start:]
    next_h2 = _NEXT_H2_RE.search(remainder)
    section = remainder[: next_h2.start()] if next_h2 else remainder
    companies: list[str] = []
    for line in section.splitlines():
        bullet = _BULLET_RE.match(line)
        if not bullet:
            continue
        raw = bullet.group(1).strip()
        parts = _SPLIT_COMMENTARY_RE.split(raw, maxsplit=1)
        name = parts[0].strip()
        if name:
            companies.append(name)
    return companies


def load_companies_of_interest() -> frozenset[str]:
    """Lowercase Tier 1 company names from `config/target_companies.md` (#211).

    Reads the Tier 1 section directly — replaces the prior derived
    `companies_of_interest.txt` path. Used for case-insensitive substring
    matching by `is_company_of_interest()` (consumed by `notify.py` mis-score
    health check + sync_sheet archival exception).
    """
    global _companies_cache
    if _companies_cache is not None:
        return _companies_cache

    try:
        raw = _TARGET_COMPANIES_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        _warn_once(
            "config/target_companies.md missing — "
            "sync_sheet archival exception and notify mis-score check will be disabled"
        )
        _companies_cache = frozenset()
        return _companies_cache

    names = parse_target_companies_tier1(raw)
    if not names:
        _warn_once(
            "config/target_companies.md has no '## Tier 1' section (or it's empty) — "
            "sync_sheet archival exception and notify mis-score check will be disabled"
        )

    _companies_cache = frozenset(n.lower() for n in names)
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


def load_indeed_title_allow_rules() -> re.Pattern[str] | None:
    """Compiled inclusion regex for JobsApi14IndeedAdapter, or None if unconfigured.

    Reads the optional `indeed_title_allow` top-level key in
    `config/prefilter_rules.yaml`. Returns:

    - `None` if the file is missing, the key is absent, or the list is empty.
      The adapter treats `None` as "allow all titles" (no post-filter).
    - A compiled case-insensitive alternation regex when one or more patterns
      are configured.

    Raises ConfigError on shape errors (non-list, non-string entries, invalid
    regex). #417 lifted this from a hardcoded engineering-tuned regex in
    `JobsApi14IndeedAdapter` so non-engineering testers can configure their
    own allowlist via the `/config/` editor or onboarding picker.
    """
    global _indeed_title_allow_cache, _indeed_title_allow_loaded
    if _indeed_title_allow_loaded:
        return _indeed_title_allow_cache

    data = _safe_load_yaml(_RULES_PATH, "prefilter_rules.yaml")
    if data is None:
        _indeed_title_allow_cache = None
        _indeed_title_allow_loaded = True
        return None

    patterns = data.get("indeed_title_allow", []) or []
    if not isinstance(patterns, list):
        raise ConfigError(f"prefilter_rules.yaml: 'indeed_title_allow' must be a list, got {type(patterns).__name__}")
    for p in patterns:
        if not isinstance(p, str):
            raise ConfigError(f"prefilter_rules.yaml: indeed_title_allow pattern is not a string: {p!r}")

    if not patterns:
        _indeed_title_allow_cache = None
        _indeed_title_allow_loaded = True
        return None

    _indeed_title_allow_cache = _compile_patterns(patterns, _RULES_PATH, "indeed_title_allow")
    _indeed_title_allow_loaded = True
    return _indeed_title_allow_cache


def _reset_cache() -> None:
    """Test-only. Clears module-level caches and warning dedup."""
    global _hard_reject_cache, _in_domain_cache, _companies_cache
    global _indeed_title_allow_cache, _indeed_title_allow_loaded
    _hard_reject_cache = None
    _in_domain_cache = None
    _companies_cache = None
    _indeed_title_allow_cache = None
    _indeed_title_allow_loaded = False
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
