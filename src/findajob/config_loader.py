"""Loads prefilter rules and companies-of-interest from gitignored configs.

Reads from BASE/config/:
  - prefilter_rules.yaml        (hard_rejects + context_suppressors)
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
_EXCLUDED_EMPLOYERS_PATH = Path(BASE) / "config" / "excluded_employers.yaml"
_REJECT_REASONS_PATH = Path(BASE) / "config" / "reject_reasons.yaml"
_SPEND_CEILING_PATH = Path(BASE) / "config" / "spend_ceiling.txt"
_PROFILE_PATH = Path(BASE) / "candidate_context" / "profile.md"
_RANKING_BOOST_TERMS_PATH = Path(BASE) / "config" / "ranking_boost_terms.yaml"

# Field-agnostic fallback used when `config/reject_reasons.yaml` is missing or
# `reasons:` is empty. Operator stacks override via the file (interview-emitted
# in a follow-up to #429); fresh installs and new deployments see this default.
_DEFAULT_REJECT_REASONS: tuple[str, ...] = (
    "Not relevant",
    "Skills Mismatch",
    "Geography",
    "Compensation",
    "Company Not a Fit",
    "Stale/Closed",
    "Already Applied",
    "Low Fit Score",
    "Company passed",
    "Other",
)
_DEFAULT_TITLE_SIGNAL_REASONS: frozenset[str] = frozenset({"Skills Mismatch", "Low Fit Score"})

# Field-agnostic default for the outreach contact-ranking domain boost: empty.
# Domain vocabulary is operator/field-specific, so it lives ONLY in gitignored
# config/ranking_boost_terms.yaml — never in tracked code (#964). An operator
# who wants a domain boost seeds that file; everyone else gets no domain tier.
_DEFAULT_RANKING_BOOST_TERMS: frozenset[str] = frozenset()

# Tier 1 parser — moved from findajob.onboarding.injector (#211).
_TIER1_HEADING_RE = re.compile(r"^##\s+tier\s*1\b[^\n]*", re.IGNORECASE | re.MULTILINE)
_NEXT_H2_RE = re.compile(r"^##\s+\S", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*(?:[-*]\s+|\d+\.\s+)(.*)")
_SPLIT_COMMENTARY_RE = re.compile(r"\s+[—-]\s+|\s+\(")
# Markdown table support: header row precedes a separator row of `---` cells;
# body rows have a non-empty first cell holding the company name.
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
# Tier 1 multi-name joiner: an operator writing "Google / DeepMind" as one
# bullet or table row means "match either name". Without splitting, the
# substring check `t in c` fails for both ("google / deepmind" is a substring
# of nothing real). Requires surrounding whitespace so legitimate single-name
# uses like "S&P/TSX" or "Boeing/Northrop" — written without spaces around
# the slash — pass through untouched. ` & ` is deliberately NOT split — many
# real company names contain ampersand (Procter & Gamble, Johnson & Johnson,
# Black & Decker), so splitting on ` & ` would corrupt valid entries.
_NAME_JOINER_RE = re.compile(r"\s+/\s+")

# Sentinel regex that never matches anything. Used when a config is missing
# or empty. Returned in place of None so callers don't need a None-check.
_NEVER_MATCH = re.compile(r"(?!x)x")

# Caches
_hard_reject_cache: tuple[re.Pattern[str], re.Pattern[str] | None] | None = None
_in_domain_cache: tuple[re.Pattern[str], re.Pattern[str] | None] | None = None
_companies_cache: frozenset[str] | None = None
# Note: reject_reasons and excluded_employers are intentionally NOT cached so
# /settings/reject-reasons/ (#490) and /settings/excluded-employers/ (#729)
# saves take effect on the next request without a process restart. Cost is
# negligible — small YAML, parse-per-call is microseconds.

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
    bullets OR markdown table body rows through the next `##` heading or EOF,
    strips bullet markers (`-`, `*`, `1.`), and trims trailing parenthetical
    or em-dash commentary.

    Markdown tables: when a `| ... |` row is followed by a separator row
    (`|---|---|`), the first row is treated as the header and skipped along
    with the separator; subsequent body rows contribute their first-cell
    value as the company name. Mixed bullet+table content within one section
    is supported.

    Multi-name entries: a name field containing ` / ` (with surrounding
    whitespace) is split into multiple entries — `"Google / DeepMind"` emits
    both `"Google"` and `"DeepMind"`. This rescues the common "either of
    these" shorthand from `is_company_of_interest`'s substring check, which
    would otherwise silently fail to match either name on its own. Slashes
    without surrounding whitespace (`"S&P/TSX"`, `"Boeing/Northrop"`) and
    `&`-joined names (`"Procter & Gamble"`) pass through untouched — the
    former is treated as a single intentional name; the latter is too common
    in real company names to risk splitting.

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
    lines = section.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        bullet = _BULLET_RE.match(line)
        if bullet:
            _emit_tier1_names(bullet.group(1), companies)
            i += 1
            continue
        table_row = _TABLE_ROW_RE.match(line)
        if table_row and _TABLE_SEPARATOR_RE.match(line):
            # Stray separator with no preceding header row — just skip it.
            i += 1
            continue
        if table_row and i + 1 < len(lines) and _TABLE_SEPARATOR_RE.match(lines[i + 1]):
            # Header row followed by separator — skip both, body starts after.
            i += 2
            continue
        if table_row:
            cells = [c.strip() for c in table_row.group(1).split("|")]
            if cells and cells[0]:
                _emit_tier1_names(cells[0], companies)
        i += 1
    return companies


def _emit_tier1_names(raw: str, companies: list[str]) -> None:
    """Strip trailing commentary, split on ` / ` joiners, append non-empty names."""
    parts = _SPLIT_COMMENTARY_RE.split(raw.strip(), maxsplit=1)
    head = parts[0].strip()
    if not head:
        return
    for sub in _NAME_JOINER_RE.split(head):
        sub = sub.strip()
        if sub:
            companies.append(sub)


def load_companies_of_interest() -> frozenset[str]:
    """Lowercase Tier 1 company names from `config/target_companies.md` (#211).

    Reads the Tier 1 section directly. Used for case-insensitive substring
    matching by `is_company_of_interest()` (consumed by `notify.py` mis-score
    health check).
    """
    global _companies_cache
    if _companies_cache is not None:
        return _companies_cache

    try:
        raw = _TARGET_COMPANIES_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        _warn_once("config/target_companies.md missing — notify mis-score check will be disabled")
        _companies_cache = frozenset()
        return _companies_cache

    names = parse_target_companies_tier1(raw)
    if not names:
        _warn_once(
            "config/target_companies.md has no '## Tier 1' section (or it's empty) — "
            "notify mis-score check will be disabled"
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


def load_excluded_employers() -> tuple[frozenset[str], re.Pattern[str] | None]:
    """`(exact_set, regex_re)` from `config/excluded_employers.yaml`.

    `exact_set` is a frozenset of lowercased company names for case-insensitive
    exact match. `regex_re` is a compiled case-insensitive alternation, or
    `None` if no regex patterns are configured.

    Missing file, empty file, or empty lists → `(frozenset(), None)` —
    company-exclusion stage becomes a no-op. Malformed entries raise
    `ConfigError`.

    Returns fresh values on every call so `/settings/excluded-employers/`
    saves take effect on the next request without a process restart (#729).
    """
    data = _safe_load_yaml(_EXCLUDED_EMPLOYERS_PATH, "excluded_employers.yaml")
    if data is None:
        return (frozenset(), None)

    exact = data.get("exact", []) or []
    if not isinstance(exact, list):
        raise ConfigError(f"excluded_employers.yaml: 'exact' must be a list, got {type(exact).__name__}")
    for e in exact:
        if not isinstance(e, str):
            raise ConfigError(f"excluded_employers.yaml: exact entry is not a string: {e!r}")

    regex = data.get("regex", []) or []
    if not isinstance(regex, list):
        raise ConfigError(f"excluded_employers.yaml: 'regex' must be a list, got {type(regex).__name__}")
    for p in regex:
        if not isinstance(p, str):
            raise ConfigError(f"excluded_employers.yaml: regex pattern is not a string: {p!r}")

    exact_set = frozenset(e.strip().lower() for e in exact if e.strip())
    regex_re: re.Pattern[str] | None = None
    if regex:
        regex_re = _compile_patterns(regex, _EXCLUDED_EMPLOYERS_PATH, "regex")

    return (exact_set, regex_re)


def load_reject_reasons() -> tuple[tuple[str, ...], frozenset[str]]:
    """`(reasons, title_signal_reasons)` from `config/reject_reasons.yaml`.

    `reasons` is the ordered tuple of reject-reason labels — powers the
    dropdown in `_reject_cell.html`, the canonical-order display in
    `routes/stats.py`, and the filter-chip values in `web/filters/registry.py`.
    Single source of truth: same values everywhere fixes the silent-filtering
    drift bug surfaced by the #301 audit (§2.1).

    `title_signal_reasons` is the subset of `reasons` that signals the SCORER
    MISSED the title — used by `findajob.analyze_feedback._prefilter_candidates`
    to identify prefilter candidates. Reasons not in this set are excluded from that
    analysis. May be empty (analysis becomes a no-op).

    Missing file or empty `reasons:` → returns the field-agnostic defaults
    (`_DEFAULT_REJECT_REASONS` / `_DEFAULT_TITLE_SIGNAL_REASONS`). Malformed
    entries raise `ConfigError`.

    Returns fresh values on every call — small file, parse-per-call cost is
    negligible (~µs), and lets `/settings/reject-reasons/` saves take effect
    on the next request without a process restart (#490).
    """
    data = _safe_load_yaml(_REJECT_REASONS_PATH, "reject_reasons.yaml")
    if data is None:
        return (_DEFAULT_REJECT_REASONS, _DEFAULT_TITLE_SIGNAL_REASONS)

    raw_reasons = data.get("reasons", [])
    if not isinstance(raw_reasons, list):
        raise ConfigError(f"reject_reasons.yaml: 'reasons' must be a list, got {type(raw_reasons).__name__}")
    for r in raw_reasons:
        if not isinstance(r, str):
            raise ConfigError(f"reject_reasons.yaml: reasons entry is not a string: {r!r}")
    if not raw_reasons:
        return (_DEFAULT_REJECT_REASONS, _DEFAULT_TITLE_SIGNAL_REASONS)

    raw_title = data.get("title_signal_reasons", []) or []
    if not isinstance(raw_title, list):
        raise ConfigError(f"reject_reasons.yaml: 'title_signal_reasons' must be a list, got {type(raw_title).__name__}")
    for t in raw_title:
        if not isinstance(t, str):
            raise ConfigError(f"reject_reasons.yaml: title_signal_reasons entry is not a string: {t!r}")

    return (tuple(raw_reasons), frozenset(raw_title))


def load_ranking_boost_terms() -> frozenset[str]:
    """Lowercased domain-vocabulary terms that boost a contact in the outreach
    ranker (`find_contacts.rank_contacts`), from `config/ranking_boost_terms.yaml`.

    Operator-curated and field-specific by nature, so the terms live in
    gitignored config rather than tracked code — the repo stays field-agnostic
    (#964). A missing or empty file returns the empty default
    (`_DEFAULT_RANKING_BOOST_TERMS`); the domain-boost tier simply contributes
    nothing. That empty state is the intended field-neutral default, NOT a
    degradation, so — unlike the prefilter configs — no warning is emitted on a
    missing file. Malformed content raises `ConfigError`.

    Returns fresh values on every call (no cache) so a `/config/` edit takes
    effect on the next run without a process restart.
    """
    try:
        text = _RANKING_BOOST_TERMS_PATH.read_text()
    except FileNotFoundError:
        return _DEFAULT_RANKING_BOOST_TERMS
    if not text.strip():
        return _DEFAULT_RANKING_BOOST_TERMS
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"ranking_boost_terms.yaml: YAML parse error: {e}") from e
    if data is None:
        return _DEFAULT_RANKING_BOOST_TERMS
    if not isinstance(data, dict):
        raise ConfigError(f"ranking_boost_terms.yaml: top level must be a mapping, got {type(data).__name__}")
    raw = data.get("terms", []) or []
    if not isinstance(raw, list):
        raise ConfigError(f"ranking_boost_terms.yaml: 'terms' must be a list, got {type(raw).__name__}")
    for t in raw:
        if not isinstance(t, str):
            raise ConfigError(f"ranking_boost_terms.yaml: terms entry is not a string: {t!r}")
    return frozenset(t.strip().lower() for t in raw if t.strip())


_SPEND_CEILING_DISABLED = frozenset({"disabled", "none", "off", "0"})


def load_spend_ceiling() -> float | None:
    """Monthly LLM spend ceiling in USD from ``config/spend_ceiling.txt``.

    Returns ``None`` (gate is a no-op) when the file is absent, empty, or
    contains a disabled sentinel (``disabled``, ``none``, ``off``, ``0`` —
    case-insensitive). Returns a positive float when a valid ceiling is set.
    Returns ``None`` and logs a warning on malformed content.

    No cache — parse-per-call so saves take effect on the next request
    without a process restart (mirrors ``load_reject_reasons`` pattern).
    """
    try:
        raw = _SPEND_CEILING_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None

    if not raw:
        return None

    if raw.lower() in _SPEND_CEILING_DISABLED:
        return None

    try:
        value = float(raw)
    except ValueError:
        _warn_once(f"config/spend_ceiling.txt: unrecognised value {raw!r} — ceiling disabled")
        return None

    if value <= 0:
        return None

    return value


def save_reject_reasons(
    reasons: tuple[str, ...],
    title_signal_reasons: frozenset[str],
) -> None:
    """Atomically write `config/reject_reasons.yaml` (#490).

    Validates input before writing. On validation failure, raises ConfigError
    and does not touch the file. On write failure, the original file is left
    intact (atomic os.replace).

    Validation:
      - `reasons` must be non-empty (after stripping whitespace)
      - Each reason: non-empty after strip, no comma (URL filter contract)
      - No duplicate reasons (post-strip)
      - `title_signal_reasons` ⊆ set(reasons) (post-strip)
    """
    import os
    import tempfile

    cleaned_reasons = tuple(r.strip() for r in reasons if r.strip())
    if not cleaned_reasons:
        raise ConfigError("reject_reasons: 'reasons' must be non-empty")
    for r in cleaned_reasons:
        if "," in r:
            raise ConfigError(
                f"reject_reasons: reason {r!r} contains comma; the URL filter contract uses comma as separator"
            )
    if len(set(cleaned_reasons)) != len(cleaned_reasons):
        raise ConfigError("reject_reasons: duplicate entries in 'reasons'")

    cleaned_title = frozenset(t.strip() for t in title_signal_reasons if t.strip())
    extra = cleaned_title - set(cleaned_reasons)
    if extra:
        raise ConfigError(f"reject_reasons: title_signal entries not in reasons: {sorted(extra)}")

    lines = ["reasons:"]
    for r in cleaned_reasons:
        lines.append(f"  - {r}")
    if cleaned_title:
        lines.append("title_signal_reasons:")
        for r in cleaned_reasons:
            if r in cleaned_title:
                lines.append(f"  - {r}")
    body = "\n".join(lines) + "\n"

    _REJECT_REASONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=_REJECT_REASONS_PATH.name + ".",
        suffix=".tmp",
        dir=str(_REJECT_REASONS_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(body)
        os.replace(tmp_name, _REJECT_REASONS_PATH)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def save_excluded_employers(exact: tuple[str, ...], regex: tuple[str, ...]) -> None:
    """Atomically write `config/excluded_employers.yaml` (#729).

    Validates input before writing. On validation failure, raises ConfigError
    and does not touch the file. On write failure, the original file is left
    intact (atomic os.replace).

    Validation:
      - Each `exact` entry: non-empty after strip
      - No duplicate `exact` entries (case-insensitive, post-strip)
      - Each `regex` entry: non-empty after strip, compiles via re.compile
      - No duplicate `regex` entries (post-strip)

    Empty `exact` and empty `regex` are both valid — yields an explicit
    `exact: []\\nregex: []\\n` no-op file (operator-configured "no exclusions").
    """
    import os
    import tempfile

    cleaned_exact = tuple(e.strip() for e in exact if e.strip())
    lowered = [e.lower() for e in cleaned_exact]
    if len(set(lowered)) != len(lowered):
        raise ConfigError("excluded_employers: duplicate entries in 'exact' (case-insensitive)")

    cleaned_regex = tuple(p.strip() for p in regex if p.strip())
    if len(set(cleaned_regex)) != len(cleaned_regex):
        raise ConfigError("excluded_employers: duplicate entries in 'regex'")
    for p in cleaned_regex:
        try:
            re.compile(p)
        except re.error as e:
            raise ConfigError(f"excluded_employers: invalid regex {p!r} — {e}") from e

    lines = []
    if cleaned_exact:
        lines.append("exact:")
        for entry in cleaned_exact:
            lines.append(f"  - {_yaml_scalar(entry)}")
    else:
        lines.append("exact: []")
    if cleaned_regex:
        lines.append("regex:")
        for pattern in cleaned_regex:
            lines.append(f"  - {_yaml_scalar(pattern)}")
    else:
        lines.append("regex: []")
    body = "\n".join(lines) + "\n"

    _EXCLUDED_EMPLOYERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=_EXCLUDED_EMPLOYERS_PATH.name + ".",
        suffix=".tmp",
        dir=str(_EXCLUDED_EMPLOYERS_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(body)
        os.replace(tmp_name, _EXCLUDED_EMPLOYERS_PATH)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def add_prefilter_title_pattern(pattern: str, category: str = "operator_added") -> None:
    """Append a single title regex to `hard_rejects[category]` in prefilter_rules.yaml (#653).

    Append-one semantics (not replace-all): the per-row 'Add exclusion rule'
    affordance commits exactly one new pattern. Loading the whole file just to
    round-trip through `save_excluded_employers`-style replace would be churn.

    Validation:
      - pattern: non-empty after strip
      - pattern compiles via re.compile
      - pattern not already present in the target category (case-sensitive — regex)

    Preserves other categories and context_suppressors. Creates the file (and
    the category) if absent. Atomic write — on os.replace failure the original
    file is left intact.

    YAML `#` comments in the file are not preserved across writes (same as
    `save_excluded_employers` — yaml.safe_load + hand-rolled emit). The first
    save will drop the guidance comments in `prefilter_rules.yaml.example`.
    """
    import os
    import tempfile

    cleaned = pattern.strip()
    if not cleaned:
        raise ConfigError("prefilter_rules: pattern must be non-empty")
    try:
        re.compile(cleaned)
    except re.error as e:
        raise ConfigError(f"prefilter_rules: invalid regex {cleaned!r} — {e}") from e

    if _RULES_PATH.exists():
        try:
            data = yaml.safe_load(_RULES_PATH.read_text()) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"prefilter_rules.yaml: YAML parse error: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"prefilter_rules.yaml: top-level must be a mapping, got {type(data).__name__}")
    else:
        data = {}

    hard_rejects = data.get("hard_rejects") or {}
    if not isinstance(hard_rejects, dict):
        raise ConfigError(f"prefilter_rules.yaml: 'hard_rejects' must be a mapping, got {type(hard_rejects).__name__}")
    cat_list = hard_rejects.get(category) or []
    if not isinstance(cat_list, list):
        raise ConfigError(
            f"prefilter_rules.yaml: hard_rejects['{category}'] must be a list, got {type(cat_list).__name__}"
        )
    if cleaned in cat_list:
        raise ConfigError(f"prefilter_rules: duplicate pattern {cleaned!r} in category '{category}'")
    new_cat_list = list(cat_list) + [cleaned]
    new_hard_rejects = {**hard_rejects, category: new_cat_list}

    context_suppressors = data.get("context_suppressors") or []
    if not isinstance(context_suppressors, list):
        raise ConfigError(
            f"prefilter_rules.yaml: 'context_suppressors' must be a list, got {type(context_suppressors).__name__}"
        )

    lines: list[str] = []
    if new_hard_rejects:
        lines.append("hard_rejects:")
        for cat_name, patterns in new_hard_rejects.items():
            lines.append(f"  {cat_name}:")
            for p in patterns:
                lines.append(f"    - {_yaml_scalar(p)}")
    if context_suppressors:
        lines.append("context_suppressors:")
        for p in context_suppressors:
            lines.append(f"  - {_yaml_scalar(p)}")
    body = "\n".join(lines) + "\n"

    _RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=_RULES_PATH.name + ".",
        suffix=".tmp",
        dir=str(_RULES_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(body)
        os.replace(tmp_name, _RULES_PATH)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def append_profile_excluded_category(entry: str) -> None:
    """Append a prose entry to the ## Excluded Categories section of profile.md (#653).

    The section is the JD-content-signal destination for the per-row 'Add
    exclusion rule' affordance. New entries land at the end of the section
    (immediately before the next ## H2 or EOF), separated by a blank line from
    existing content. Atomic write.

    Validation:
      - entry: non-empty after strip
      - profile.md exists (operator has completed onboarding)
      - ## Excluded Categories section is present in profile.md
      - entry text not already present in the section (substring match)
    """
    import os
    import tempfile

    cleaned = entry.strip()
    if not cleaned:
        raise ConfigError("profile excluded_categories: entry must be non-empty")
    if not _PROFILE_PATH.exists():
        raise ConfigError(f"profile.md not found at {_PROFILE_PATH}")

    text = _PROFILE_PATH.read_text()
    lines = text.split("\n")

    start: int | None = None
    for i, line in enumerate(lines):
        if line.rstrip() == "## Excluded Categories":
            start = i
            break
    if start is None:
        raise ConfigError("profile.md: '## Excluded Categories' section not found")

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break

    section_body = "\n".join(lines[start + 1 : end])
    if cleaned in section_body:
        raise ConfigError(f"profile excluded_categories: entry already present (duplicate): {cleaned!r}")

    body_lines = lines[start + 1 : end]
    while body_lines and body_lines[-1].strip() == "":
        body_lines.pop()

    new_body = body_lines + ["", cleaned] if body_lines else ["", cleaned]
    if end < len(lines):
        new_body.append("")

    new_lines = lines[: start + 1] + new_body + lines[end:]
    new_text = "\n".join(new_lines)
    if text.endswith("\n") and not new_text.endswith("\n"):
        new_text += "\n"

    fd, tmp_name = tempfile.mkstemp(
        prefix=_PROFILE_PATH.name + ".",
        suffix=".tmp",
        dir=str(_PROFILE_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_text)
        os.replace(tmp_name, _PROFILE_PATH)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def _yaml_scalar(s: str) -> str:
    """Quote a YAML scalar safely. Single-quoted form covers regex
    metachars + names with colons / leading dashes / etc. Embedded
    single quotes are doubled per YAML spec."""
    return "'" + s.replace("'", "''") + "'"


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
