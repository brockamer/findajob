# Generalize Prefilter Config — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Externalize `scorer_prefilter.py`'s hardcoded domain rules into gitignored YAML/txt config files, drop the Tier-1 prefilter bonus, and route two other consumers (`sync_sheet.py`, `notify.py`) through a new `companies_of_interest` config.

**Architecture:** New `src/findajob/config_loader.py` reads three configs (`prefilter_rules.yaml`, `in_domain_patterns.yaml`, `companies_of_interest.txt`) from `BASE/config/`, validates them, compiles regexes, and caches at import. `scorer_prefilter.py` becomes a thin consumer. Missing files emit warnings and return no-op sentinels; malformed files raise `ConfigError`.

**Tech Stack:** Python 3.12, PyYAML (already a dep via `cost_tracking.py`), pytest, existing `findajob.paths` resolver.

**Spec reference:** `docs/superpowers/specs/2026-04-17-generalize-prefilter-config-design.md`

---

## File Structure

**New files (tracked):**
- `src/findajob/config_loader.py` — loader module, public API.
- `tests/conftest.py` — autouse fixture redirecting the loader at test fixtures, resetting cache per test.
- `tests/test_config_loader.py` — unit tests for the loader.
- `tests/test_companies_of_interest_consumers.py` — regression guard on consumer imports.
- `tests/fixtures/config/prefilter_rules.yaml` — test fixture (minimal but covers every code path).
- `tests/fixtures/config/in_domain_patterns.yaml` — test fixture.
- `tests/fixtures/config/companies_of_interest.txt` — test fixture.
- `config/prefilter_rules.yaml.example` — field-agnostic template.
- `config/in_domain_patterns.yaml.example` — field-agnostic template.
- `config/companies_of_interest.txt.example` — template.

**New files (gitignored, hand-populated on Brock's machine only):**
- `config/prefilter_rules.yaml`
- `config/in_domain_patterns.yaml`
- `config/companies_of_interest.txt`

**Modified files:**
- `src/findajob/scorer_prefilter.py` — deletes literals, reads from loader, drops Tier-1 bonus.
- `scripts/sync_sheet.py` — swaps `_is_tier1` for `is_company_of_interest`.
- `scripts/notify.py` — swaps `TIER1` for `is_company_of_interest`.
- `tests/test_scorer_prefilter.py` — removes `TestIsTier1`, updates Stage-2 score assertions.
- `.gitignore` — adds the three new gitignored configs.
- `docs/GENERALIZATION.md` — marks items 1–3 under "Scorer prefilter" done; notes items 4–5 deferred.

---

## Task 1: Loader skeleton + test fixtures + conftest

Stand up the empty loader module, test fixtures, and the conftest that points the loader at fixtures. No loader logic yet — just the import surface and the test harness. Downstream tasks implement behavior TDD-style.

**Files:**
- Create: `src/findajob/config_loader.py`
- Create: `tests/conftest.py`
- Create: `tests/fixtures/config/prefilter_rules.yaml`
- Create: `tests/fixtures/config/in_domain_patterns.yaml`
- Create: `tests/fixtures/config/companies_of_interest.txt`

- [ ] **Step 1: Create the loader skeleton**

Create `src/findajob/config_loader.py`:

```python
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
    raise NotImplementedError


def is_company_of_interest(company: str) -> bool:
    """Case-insensitive substring check. False for empty/None inputs."""
    raise NotImplementedError


def _reset_cache() -> None:
    """Test-only. Clears module-level caches and warning dedup."""
    global _hard_reject_cache, _in_domain_cache, _companies_cache
    _hard_reject_cache = None
    _in_domain_cache = None
    _companies_cache = None
    _warned.clear()
```

- [ ] **Step 2: Create the three test fixtures**

Create `tests/fixtures/config/prefilter_rules.yaml`:

```yaml
# Test fixture — small but covers hard_rejects, grouping, and suppressors.
hard_rejects:
  software:
    - '\bsoftware\s+engineer(ing)?\b'
    - '\b(swe|sde)\b'
  healthcare:
    - '\bnurs(e|ing)\b'
  sales:
    - '\baccount\s+executive\b'

context_suppressors:
  - '\bdata\s*center\b|\bdatacenter\b'
```

Create `tests/fixtures/config/in_domain_patterns.yaml`:

```yaml
positive:
  - '\bdata\s*center\s+(operations|technician|engineer)\b'
  - '\bnpi\s+(manager|lead|engineer)\b'
  - '\boperational\s+readiness\b'

poison:
  - '\b(workplace\s+services|custodial|janitorial)\b'
```

Create `tests/fixtures/config/companies_of_interest.txt`:

```
# Test fixture
meta
google
openai
anthropic
aws
microsoft
```

- [ ] **Step 3: Create `tests/conftest.py`**

```python
"""Global test fixtures.

Redirects findajob.config_loader to read from tests/fixtures/config/
instead of the production config directory, and resets its cache before
each test so a test's config edits don't leak into the next test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from findajob import config_loader

FIXTURES = Path(__file__).parent / "fixtures" / "config"


@pytest.fixture(autouse=True)
def _use_fixture_configs(monkeypatch):
    monkeypatch.setattr(config_loader, "_RULES_PATH", FIXTURES / "prefilter_rules.yaml")
    monkeypatch.setattr(config_loader, "_IN_DOMAIN_PATH", FIXTURES / "in_domain_patterns.yaml")
    monkeypatch.setattr(config_loader, "_COMPANIES_PATH", FIXTURES / "companies_of_interest.txt")
    config_loader._reset_cache()
    yield
    config_loader._reset_cache()
```

- [ ] **Step 4: Verify the skeleton imports cleanly**

Run:
```bash
cd /home/brockamer/Code/findajob && python3 -c "from findajob.config_loader import ConfigError, load_hard_reject_rules, load_in_domain_rules, load_companies_of_interest, is_company_of_interest, _reset_cache; print('imports OK')"
```

Expected output:
```
imports OK
```

- [ ] **Step 5: Commit**

```bash
git add src/findajob/config_loader.py tests/conftest.py tests/fixtures/
git commit -m "$(cat <<'EOF'
feat(config-loader): skeleton module + test fixtures (#10)

Empty public API (raises NotImplementedError), conftest redirects the
loader to tests/fixtures/config/ and resets cache per test. Downstream
tasks implement behavior TDD-style.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Implement `load_companies_of_interest` + `is_company_of_interest` (TDD)

Start with the simplest loader: a flat text file. Covers happy path, comment/blank line handling, case normalization, and the substring-match helper.

**Files:**
- Modify: `src/findajob/config_loader.py`
- Create: `tests/test_config_loader.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_loader.py`:

```python
"""Tests for findajob.config_loader."""

from __future__ import annotations

import pytest

from findajob import config_loader
from findajob.config_loader import (
    ConfigError,
    is_company_of_interest,
    load_companies_of_interest,
)


class TestLoadCompaniesOfInterest:
    def test_loads_from_fixture(self):
        result = load_companies_of_interest()
        assert isinstance(result, frozenset)
        assert "meta" in result
        assert "google" in result
        assert "openai" in result

    def test_lowercases_entries(self):
        result = load_companies_of_interest()
        assert all(c == c.lower() for c in result)

    def test_caches_result(self):
        result1 = load_companies_of_interest()
        result2 = load_companies_of_interest()
        assert result1 is result2  # same object — cache hit


class TestIsCompanyOfInterest:
    @pytest.mark.parametrize(
        "company",
        ["Meta", "meta", "META", "Meta Platforms, Inc.", "Google Cloud", "Amazon Web Services"],
    )
    def test_positive_substring(self, company):
        # "aws" is in the fixture; "Amazon Web Services" contains "aws" lowercased? No —
        # the fixture lists "aws" and "Amazon Web Services" lowercased is "amazon web services"
        # which contains "aws"? "aws" is 3 chars; "amazon web services" has "aws" as substring
        # in "web services" → yes. But this is fragile. Let's keep the tests focused on
        # clear substring cases:
        pass  # replaced by the narrower cases below

    @pytest.mark.parametrize(
        "company",
        ["Meta", "meta", "META", "Meta Platforms, Inc.", "Google Cloud", "OpenAI Research"],
    )
    def test_positive_substring_clear(self, company):
        assert is_company_of_interest(company) is True

    @pytest.mark.parametrize(
        "company",
        ["Walmart", "Starbucks", "Acme Corp", "Random Startup LLC"],
    )
    def test_negative(self, company):
        assert is_company_of_interest(company) is False

    def test_empty_string(self):
        assert is_company_of_interest("") is False

    def test_none(self):
        # Typed as str but guard handles falsy
        assert is_company_of_interest(None) is False  # type: ignore[arg-type]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/brockamer/Code/findajob && python3 -m pytest tests/test_config_loader.py -v 2>&1 | head -40
```

Expected: every test fails with `NotImplementedError`.

- [ ] **Step 3: Implement the two functions**

In `src/findajob/config_loader.py`, replace the two `NotImplementedError` stubs:

```python
def load_companies_of_interest() -> frozenset[str]:
    global _companies_cache
    if _companies_cache is not None:
        return _companies_cache

    try:
        raw = _COMPANIES_PATH.read_text()
    except FileNotFoundError:
        _warn_once(f"config/companies_of_interest.txt missing — sync_sheet archival exception and notify mis-score check will be disabled")
        _companies_cache = frozenset()
        return _companies_cache

    entries: set[str] = set()
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.add(stripped.lower())

    if not entries:
        _warn_once(f"config/companies_of_interest.txt is empty — sync_sheet archival exception and notify mis-score check will be disabled")

    _companies_cache = frozenset(entries)
    return _companies_cache


def is_company_of_interest(company: str) -> bool:
    if not company:
        return False
    c = company.lower()
    return any(t in c for t in load_companies_of_interest())


def _warn_once(msg: str) -> None:
    if msg in _warned:
        return
    _warned.add(msg)
    warnings.warn(msg, UserWarning, stacklevel=3)
```

Remove the two stub functions that raised `NotImplementedError`.

- [ ] **Step 4: Trim the broken parametrize in the test**

In `tests/test_config_loader.py`, delete the `test_positive_substring` method that has `pass` inside it (the narrower `test_positive_substring_clear` replaces it).

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
cd /home/brockamer/Code/findajob && python3 -m pytest tests/test_config_loader.py -v 2>&1 | head -40
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/findajob/config_loader.py tests/test_config_loader.py
git commit -m "$(cat <<'EOF'
feat(config-loader): companies_of_interest loader + substring helper (#10)

Reads config/companies_of_interest.txt (comments + blanks skipped,
lowercased). Missing/empty file → empty frozenset + UserWarning.
is_company_of_interest does case-insensitive substring match with
empty-input guard.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Implement `load_hard_reject_rules` happy path (TDD)

YAML → compiled regex pair. Covers grouped categories, suppressors, and the no-suppressor case.

**Files:**
- Modify: `src/findajob/config_loader.py`
- Modify: `tests/test_config_loader.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config_loader.py`:

```python
class TestLoadHardRejectRules:
    def test_returns_two_regexes(self):
        reject_re, suppressor_re = config_loader.load_hard_reject_rules()
        assert reject_re.search("Software Engineer") is not None
        assert suppressor_re is not None  # fixture has suppressors

    def test_matches_across_categories(self):
        reject_re, _ = config_loader.load_hard_reject_rules()
        # software category
        assert reject_re.search("Senior Software Engineer") is not None
        assert reject_re.search("SWE II") is not None
        # healthcare category
        assert reject_re.search("Registered Nurse") is not None
        # sales category
        assert reject_re.search("Enterprise Account Executive") is not None

    def test_no_match_for_in_domain_title(self):
        reject_re, _ = config_loader.load_hard_reject_rules()
        assert reject_re.search("Data Center Operations Engineer") is None

    def test_suppressor_compiled(self):
        _, suppressor_re = config_loader.load_hard_reject_rules()
        assert suppressor_re.search("Data Center Security Analyst") is not None
        assert suppressor_re.search("Datacenter NOC") is not None
        assert suppressor_re.search("Security Analyst") is None  # no DC context

    def test_caches_result(self):
        r1 = config_loader.load_hard_reject_rules()
        r2 = config_loader.load_hard_reject_rules()
        assert r1 is r2  # cache hit returns same tuple
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/brockamer/Code/findajob && python3 -m pytest tests/test_config_loader.py::TestLoadHardRejectRules -v 2>&1 | head -30
```

Expected: all fail with `NotImplementedError`.

- [ ] **Step 3: Implement `load_hard_reject_rules`**

In `src/findajob/config_loader.py`, replace the `NotImplementedError` stub:

```python
def load_hard_reject_rules() -> tuple[re.Pattern[str], Optional[re.Pattern[str]]]:
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
            f"prefilter_rules.yaml: 'hard_rejects' must be a mapping of category→list, got {type(hard_rejects).__name__}"
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

    suppressor_re: Optional[re.Pattern[str]] = None
    if suppressors:
        suppressor_re = _compile_patterns(suppressors, _RULES_PATH, "context_suppressors")

    _hard_reject_cache = (reject_re, suppressor_re)
    return _hard_reject_cache


def _safe_load_yaml(path: Path, label: str) -> Optional[dict]:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /home/brockamer/Code/findajob && python3 -m pytest tests/test_config_loader.py::TestLoadHardRejectRules -v 2>&1 | head -30
```

Expected: all 5 tests in `TestLoadHardRejectRules` pass. Earlier tests still pass.

Run full file:
```bash
cd /home/brockamer/Code/findajob && python3 -m pytest tests/test_config_loader.py -v 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/findajob/config_loader.py tests/test_config_loader.py
git commit -m "$(cat <<'EOF'
feat(config-loader): hard_reject_rules loader with suppressors (#10)

Compiles grouped YAML category→patterns into a single alternation regex.
Optional context_suppressors compiled separately. Bad shape or bad regex
raises ConfigError naming the file + offending pattern.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Implement `load_in_domain_rules` (TDD)

Mirrors `load_hard_reject_rules` but with `positive` + optional `poison` top-level keys.

**Files:**
- Modify: `src/findajob/config_loader.py`
- Modify: `tests/test_config_loader.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config_loader.py`:

```python
class TestLoadInDomainRules:
    def test_positive_matches(self):
        in_domain_re, _ = config_loader.load_in_domain_rules()
        assert in_domain_re.search("Data Center Operations Engineer") is not None
        assert in_domain_re.search("NPI Manager") is not None
        assert in_domain_re.search("Operational Readiness Lead") is not None

    def test_positive_misses_out_of_domain(self):
        in_domain_re, _ = config_loader.load_in_domain_rules()
        assert in_domain_re.search("Software Engineer") is None
        assert in_domain_re.search("Account Executive") is None

    def test_poison_compiled(self):
        _, poison_re = config_loader.load_in_domain_rules()
        assert poison_re is not None
        assert poison_re.search("Data Center Workplace Services Manager") is not None
        assert poison_re.search("Custodial Lead") is not None
        assert poison_re.search("Data Center Operations") is None  # no poison term

    def test_caches_result(self):
        r1 = config_loader.load_in_domain_rules()
        r2 = config_loader.load_in_domain_rules()
        assert r1 is r2
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/brockamer/Code/findajob && python3 -m pytest tests/test_config_loader.py::TestLoadInDomainRules -v 2>&1 | head -20
```

Expected: all fail with `NotImplementedError`.

- [ ] **Step 3: Implement `load_in_domain_rules`**

In `src/findajob/config_loader.py`, replace the `NotImplementedError` stub:

```python
def load_in_domain_rules() -> tuple[re.Pattern[str], Optional[re.Pattern[str]]]:
    global _in_domain_cache
    if _in_domain_cache is not None:
        return _in_domain_cache

    data = _safe_load_yaml(_IN_DOMAIN_PATH, "in_domain_patterns.yaml")
    if data is None:
        _in_domain_cache = (_NEVER_MATCH, None)
        return _in_domain_cache

    positive = data.get("positive", [])
    if not isinstance(positive, list):
        raise ConfigError(
            f"in_domain_patterns.yaml: 'positive' must be a list, got {type(positive).__name__}"
        )
    for p in positive:
        if not isinstance(p, str):
            raise ConfigError(f"in_domain_patterns.yaml: positive pattern is not a string: {p!r}")

    positive_re = _compile_patterns(positive, _IN_DOMAIN_PATH, "positive")

    poison = data.get("poison", []) or []
    if not isinstance(poison, list):
        raise ConfigError(
            f"in_domain_patterns.yaml: 'poison' must be a list, got {type(poison).__name__}"
        )
    for p in poison:
        if not isinstance(p, str):
            raise ConfigError(f"in_domain_patterns.yaml: poison pattern is not a string: {p!r}")

    poison_re: Optional[re.Pattern[str]] = None
    if poison:
        poison_re = _compile_patterns(poison, _IN_DOMAIN_PATH, "poison")

    _in_domain_cache = (positive_re, poison_re)
    return _in_domain_cache
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /home/brockamer/Code/findajob && python3 -m pytest tests/test_config_loader.py -v 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/findajob/config_loader.py tests/test_config_loader.py
git commit -m "$(cat <<'EOF'
feat(config-loader): in_domain_rules loader with poison patterns (#10)

Mirrors hard_reject_rules shape — positive (required) + poison (optional).
Same validation + caching semantics.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Missing-file + malformed-file behavior tests

Lock in the graceful-degrade and ConfigError paths that the previous tasks implemented but didn't explicitly test through their observable behavior.

**Files:**
- Modify: `tests/test_config_loader.py`

- [ ] **Step 1: Write tests for missing files**

Append to `tests/test_config_loader.py`:

```python
class TestMissingFiles:
    def test_missing_rules_file_returns_never_match(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config_loader, "_RULES_PATH", tmp_path / "does-not-exist.yaml")
        config_loader._reset_cache()
        with pytest.warns(UserWarning, match="prefilter_rules.yaml missing"):
            reject_re, suppressor_re = config_loader.load_hard_reject_rules()
        assert reject_re.search("Software Engineer") is None
        assert reject_re is config_loader._NEVER_MATCH
        assert suppressor_re is None

    def test_missing_in_domain_file_returns_never_match(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config_loader, "_IN_DOMAIN_PATH", tmp_path / "does-not-exist.yaml")
        config_loader._reset_cache()
        with pytest.warns(UserWarning, match="in_domain_patterns.yaml missing"):
            in_domain_re, poison_re = config_loader.load_in_domain_rules()
        assert in_domain_re.search("Data Center Operations") is None
        assert poison_re is None

    def test_missing_companies_file_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config_loader, "_COMPANIES_PATH", tmp_path / "does-not-exist.txt")
        config_loader._reset_cache()
        with pytest.warns(UserWarning, match="companies_of_interest.txt missing"):
            result = config_loader.load_companies_of_interest()
        assert result == frozenset()
        assert config_loader.is_company_of_interest("Meta") is False

    def test_empty_rules_file(self, monkeypatch, tmp_path):
        empty = tmp_path / "prefilter_rules.yaml"
        empty.write_text("")
        monkeypatch.setattr(config_loader, "_RULES_PATH", empty)
        config_loader._reset_cache()
        with pytest.warns(UserWarning, match="prefilter_rules.yaml is empty"):
            reject_re, _ = config_loader.load_hard_reject_rules()
        assert reject_re.search("anything") is None


class TestMalformedFiles:
    def test_bad_yaml_raises_config_error(self, monkeypatch, tmp_path):
        bad = tmp_path / "prefilter_rules.yaml"
        bad.write_text("hard_rejects: {unclosed")
        monkeypatch.setattr(config_loader, "_RULES_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match="YAML parse error"):
            config_loader.load_hard_reject_rules()

    def test_top_level_list_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "prefilter_rules.yaml"
        bad.write_text("- just\n- a\n- list\n")
        monkeypatch.setattr(config_loader, "_RULES_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match="top level must be a mapping"):
            config_loader.load_hard_reject_rules()

    def test_hard_rejects_as_list_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "prefilter_rules.yaml"
        bad.write_text("hard_rejects:\n  - '\\bfoo\\b'\n")
        monkeypatch.setattr(config_loader, "_RULES_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match="'hard_rejects' must be a mapping"):
            config_loader.load_hard_reject_rules()

    def test_bad_regex_raises_with_pattern(self, monkeypatch, tmp_path):
        bad = tmp_path / "prefilter_rules.yaml"
        bad.write_text("hard_rejects:\n  broken:\n    - '(unclosed'\n")
        monkeypatch.setattr(config_loader, "_RULES_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match=r"invalid regex.*\(unclosed"):
            config_loader.load_hard_reject_rules()

    def test_non_string_pattern_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "prefilter_rules.yaml"
        bad.write_text("hard_rejects:\n  bad:\n    - 42\n")
        monkeypatch.setattr(config_loader, "_RULES_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match="pattern in 'bad' is not a string"):
            config_loader.load_hard_reject_rules()

    def test_in_domain_positive_as_dict_raises(self, monkeypatch, tmp_path):
        bad = tmp_path / "in_domain_patterns.yaml"
        bad.write_text("positive:\n  nested: value\n")
        monkeypatch.setattr(config_loader, "_IN_DOMAIN_PATH", bad)
        config_loader._reset_cache()
        with pytest.raises(ConfigError, match="'positive' must be a list"):
            config_loader.load_in_domain_rules()
```

- [ ] **Step 2: Run tests to verify they pass**

Run:
```bash
cd /home/brockamer/Code/findajob && python3 -m pytest tests/test_config_loader.py -v 2>&1 | tail -30
```

Expected: all tests pass, including the new `TestMissingFiles` and `TestMalformedFiles` classes. No production code changes needed — this task locks in behavior already implemented in Tasks 2–4.

- [ ] **Step 3: Commit**

```bash
git add tests/test_config_loader.py
git commit -m "$(cat <<'EOF'
test(config-loader): missing + malformed file coverage (#10)

Locks in graceful degrade (warn + no-op sentinels) and ConfigError
surface for bad YAML / wrong shape / invalid regex / non-string items.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Refactor `scorer_prefilter.py` to use the loader

Delete the literals, wire up the loader, drop the Tier-1 bonus. Update the existing 444-line prefilter test file in the same commit.

**Files:**
- Modify: `src/findajob/scorer_prefilter.py`
- Modify: `tests/test_scorer_prefilter.py`

- [ ] **Step 1: Rewrite `src/findajob/scorer_prefilter.py`**

Replace the entire file with:

```python
#!/usr/bin/env python3
"""
Deterministic pre-filter for job scoring.
Runs BEFORE any LLM call. Two stages:

  Stage 1 — Hard reject by title regex → score 1, scored, no LLM
            (context_suppressors in prefilter_rules.yaml can override)
  Stage 2 — In-domain title + no usable JD → score 5, no LLM

If neither stage fires, returns (None, None) and caller should invoke the LLM.

Rules are loaded from config/prefilter_rules.yaml and config/in_domain_patterns.yaml
(both gitignored). See src/findajob/config_loader.py.

Usage:
    from findajob.scorer_prefilter import prefilter_score
    result, reason = prefilter_score(title, company, jd_is_usable)
    if result is not None:
        return result, 0   # latency=0, no subprocess
    # ... LLM path
"""

from __future__ import annotations

from findajob.config_loader import load_hard_reject_rules, load_in_domain_rules

# Compile once at import. Loader handles missing/empty configs with sentinels.
_HARD_REJECT_RE, _SUPPRESSOR_RE = load_hard_reject_rules()
_IN_DOMAIN_RE, _POISON_RE = load_in_domain_rules()


def _hard_reject_match(title: str) -> str | None:
    """Return the matched pattern string, or None."""
    m = _HARD_REJECT_RE.search(title)
    if not m:
        return None
    # If a context suppressor also matches, don't reject.
    if _SUPPRESSOR_RE is not None and _SUPPRESSOR_RE.search(title):
        return None
    return m.group(0).strip()


def _in_domain_match(title: str) -> bool:
    if _POISON_RE is not None and _POISON_RE.search(title):
        return False
    return bool(_IN_DOMAIN_RE.search(title))


def prefilter_score(title: str, company: str, jd_usable: bool) -> tuple[dict[str, object] | None, str | None]:
    """
    Returns (result_dict, reason_str) if a deterministic decision can be made,
    or (None, None) if the LLM should be invoked.
    """
    t = (title or "").strip()

    # ── Stage 1: Hard reject ──────────────────────────────────────────────────
    match = _hard_reject_match(t)
    if match:
        reason = f'Pre-filter hard reject: title matched "{match}"'
        return {
            "score_status": "scored",
            "relevance_score": 1,
            "interview_likelihood": 1,
            "strengths_alignment": "Hard reject — title is outside candidate domain.",
            "industry_sector": None,
            "comp_estimate": None,
            "ai_notes": reason,
            "score_flag_reason": reason,
            "remote_status": "Unknown",
        }, reason

    # ── Stage 2: In-domain title, JD absent → score 5 ─────────────────────────
    if not jd_usable and _in_domain_match(t):
        reason = "Pre-filter in-domain/no-JD: scored 5"
        return {
            "score_status": "scored",
            "relevance_score": 5,
            "interview_likelihood": 4,
            "strengths_alignment": "Title is directionally in-domain. JD unavailable — scored 5 per policy.",
            "industry_sector": None,
            "comp_estimate": None,
            "ai_notes": reason,
            "score_flag_reason": None,
            "remote_status": "Unknown",
        }, reason

    return None, None
```

Note: `company` parameter is retained in the signature even though it's no longer used, to preserve the public API for existing callers (`scoring.py`). It's a no-op for now.

- [ ] **Step 2: Update `tests/test_scorer_prefilter.py`**

Two changes:

(a) Remove the `TestIsTier1` class entirely and the import of `_is_tier1`. Edit the import at the top:

Change:
```python
from findajob.scorer_prefilter import _hard_reject_match, _in_domain_match, _is_tier1, prefilter_score
```

To:
```python
from findajob.scorer_prefilter import _hard_reject_match, _in_domain_match, prefilter_score
```

Delete the entire `class TestIsTier1:` block and its decorators.

(b) Inside `TestPrefilterScore` (and any other class that asserts score=6 for Tier-1 companies), update the Stage-2 assertions so `relevance_score == 5` in all cases. The easiest approach: grep for any assertion of `relevance_score == 6` or `"relevance_score": 6` in that file and change it to 5. Any `interview_likelihood` derived from it should become 4 (score - 1).

Run:
```bash
cd /home/brockamer/Code/findajob && grep -n "relevance_score.*6\|== 6\b" tests/test_scorer_prefilter.py
```

For each hit where the test is exercising Stage 2 with a Tier-1 company, change the expected score from 6 to 5 and, if the test also checked `interview_likelihood`, from 5 to 4.

If the existing `TestPrefilterScore` class has any test that asserted the Tier-1 bonus specifically (e.g., "in-domain title at a Tier-1 company scores higher than the same title elsewhere"), delete that test — the behavior no longer exists.

- [ ] **Step 3: Run the prefilter tests**

Run:
```bash
cd /home/brockamer/Code/findajob && python3 -m pytest tests/test_scorer_prefilter.py -v 2>&1 | tail -40
```

Expected: all remaining tests pass. If tests reference patterns not in the fixture (e.g., `"aircraft mechanic"`), they'll fail on the hard-reject assertion — add those patterns to `tests/fixtures/config/prefilter_rules.yaml` under an appropriate category (e.g., `aviation:`). Same for in-domain patterns not in the fixture.

- [ ] **Step 4: Run the full test suite**

Run:
```bash
cd /home/brockamer/Code/findajob && python3 -m pytest tests/ 2>&1 | tail -10
```

Expected: all tests pass. If `tests/test_scoring.py` or others import from `scorer_prefilter` and break, the autouse conftest fixture will already have redirected the loader paths — so failures are unexpected and should be diagnosed individually. The most likely break is a test elsewhere that imports `_is_tier1` or `TIER1` — fix by deleting the import and the dependent assertion.

- [ ] **Step 5: Commit**

```bash
git add src/findajob/scorer_prefilter.py tests/test_scorer_prefilter.py tests/fixtures/config/
git commit -m "$(cat <<'EOF'
refactor(prefilter): load rules from config, drop Tier-1 bonus (#10)

scorer_prefilter.py now reads hard_rejects + context_suppressors +
positive/poison patterns from gitignored configs via config_loader.
Stage 2 in-domain/no-JD always scores 5 — the Tier-1 +1 bonus is
removed. TIER1 frozenset and _is_tier1 deleted.

Tests updated: TestIsTier1 removed, Stage-2 Tier-1 assertions updated
from 6→5.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Swap `sync_sheet.py` and `notify.py` consumers

Route them through `is_company_of_interest` instead of `_is_tier1` / `TIER1`.

**Files:**
- Modify: `scripts/sync_sheet.py`
- Modify: `scripts/notify.py`
- Create: `tests/test_companies_of_interest_consumers.py`

- [ ] **Step 1: Update `scripts/sync_sheet.py`**

In `scripts/sync_sheet.py`:

Change line 18 from:
```python
from findajob.scorer_prefilter import _is_tier1
```
to:
```python
from findajob.config_loader import is_company_of_interest
```

Change line 195 from:
```python
    target_extras = [r for r in all_rows if r["id"] not in target_ids and _is_tier1(r["company"])]
```
to:
```python
    target_extras = [r for r in all_rows if r["id"] not in target_ids and is_company_of_interest(r["company"])]
```

- [ ] **Step 2: Update `scripts/notify.py`**

In `scripts/notify.py`:

Change line 407 from:
```python
    from findajob.scorer_prefilter import TIER1
```
to:
```python
    from findajob.config_loader import is_company_of_interest
```

Change the mis-scored filter (lines ~419–424) from:
```python
    # Filter in Python since TIER1 check is a substring match
    mis_scored = [
        (r["title"], r["company"], r["relevance_score"])
        for r in low_target
        if r["company"] and any(t in r["company"].lower() for t in TIER1)
    ]
```
to:
```python
    mis_scored = [
        (r["title"], r["company"], r["relevance_score"])
        for r in low_target
        if is_company_of_interest(r["company"])
    ]
```

- [ ] **Step 3: Write the regression test**

Create `tests/test_companies_of_interest_consumers.py`:

```python
"""Regression guard: sync_sheet and notify must consume companies_of_interest
via the config_loader, not via the now-deleted _is_tier1 / TIER1 from the
prefilter module.
"""

from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text()


def test_sync_sheet_uses_is_company_of_interest():
    src = _read("scripts/sync_sheet.py")
    assert "from findajob.config_loader import is_company_of_interest" in src
    assert "is_company_of_interest(" in src
    assert "_is_tier1" not in src
    assert "from findajob.scorer_prefilter import TIER1" not in src


def test_notify_uses_is_company_of_interest():
    src = _read("scripts/notify.py")
    assert "from findajob.config_loader import is_company_of_interest" in src
    assert "is_company_of_interest(" in src
    assert "from findajob.scorer_prefilter import TIER1" not in src
    assert "_is_tier1" not in src
```

- [ ] **Step 4: Run the regression test + full suite**

Run:
```bash
cd /home/brockamer/Code/findajob && python3 -m pytest tests/test_companies_of_interest_consumers.py tests/test_sync_sheet.py -v 2>&1 | tail -20
```

Expected: regression tests pass. `test_sync_sheet.py` tests pass (behavior unchanged — just the membership-check source changed).

Run full suite:
```bash
cd /home/brockamer/Code/findajob && python3 -m pytest tests/ 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/sync_sheet.py scripts/notify.py tests/test_companies_of_interest_consumers.py
git commit -m "$(cat <<'EOF'
refactor: route sync_sheet + notify through is_company_of_interest (#10)

Replaces _is_tier1 / TIER1 usage with config_loader.is_company_of_interest.
Behavior unchanged — same substring semantics, new config source.

Regression test asserts neither script imports the deleted prefilter
symbols.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Ship `.example` files + update `.gitignore`

Tracked templates for a new user. Gitignore entries for the real configs.

**Files:**
- Create: `config/prefilter_rules.yaml.example`
- Create: `config/in_domain_patterns.yaml.example`
- Create: `config/companies_of_interest.txt.example`
- Modify: `.gitignore`

- [ ] **Step 1: Create `config/prefilter_rules.yaml.example`**

```yaml
# prefilter_rules.yaml — deterministic hard rejects for the title-only prefilter.
# Copy this file to prefilter_rules.yaml and edit. This file is gitignored.
#
# SHAPE:
#   hard_rejects:          (required; mapping of category → list of title regex)
#     <category_name>:
#       - '<regex>'
#   context_suppressors:   (optional; list of regex that override a reject)
#     - '<regex>'
#
# If any hard_reject pattern matches a job's title, the job scores 1
# with no LLM call. If a context_suppressor also matches, the reject
# is skipped and the title goes on to Stage 2 / LLM scoring.
#
# Missing file → prefilter hard-reject stage becomes a no-op (warn).

hard_rejects:
  spam:
    # Job-board noise that isn't a real posting. Usually safe to keep this.
    - '^manage\s+job\s+alerts?\b'
    - '^your\s+job\s+alert\s+for\b'
    - '\bjoin\s+our\s+talent\s+network\b'

# ── Suggested categories to customize ──────────────────────────────────────────
# Uncomment the block that fits your field, edit the patterns, delete the rest.
#
# Tech / data center operations candidate:
#   software_engineering:
#     - '\bsoftware\s+engineer(ing)?\b'
#     - '\b(swe|sde)\b'
#   sales:
#     - '\baccount\s+executive\b'
#     - '\bsales\s+representative\b'
#
# Healthcare / nursing candidate:
#   non_clinical_admin:
#     - '\bbilling\s+specialist\b'
#     - '\bpatient\s+access\b'
#
# Education / teaching candidate:
#   non_instructional:
#     - '\bbus\s+driver\b'
#     - '\bcafeteria\s+manager\b'
#
# Social work / human services candidate:
#   unrelated_social:
#     - '\bcase\s+manager\b.*\binsurance\b'

# ── Context suppressors ──────────────────────────────────────────────────────
# Title fragments that override a hard-reject match. Example: a candidate in DC
# ops wants to see "Data Center Security Engineer" (a real job) even though
# "security" is in their hard-reject list. Adding "data center" as a suppressor
# lets that title through.
#
# context_suppressors:
#   - '\bdata\s*center\b|\bdatacenter\b'
```

- [ ] **Step 2: Create `config/in_domain_patterns.yaml.example`**

```yaml
# in_domain_patterns.yaml — positive signal + poison for Stage 2 prefilter.
# Copy this file to in_domain_patterns.yaml and edit. This file is gitignored.
#
# SHAPE:
#   positive: (required; list of title regex — in-domain signal)
#     - '<regex>'
#   poison:   (optional; list of regex — negates an otherwise in-domain match)
#     - '<regex>'
#
# When a job has no usable JD (auth wall, under 30 words, etc.), the prefilter
# falls back to title-only signal. If a positive pattern matches AND no poison
# pattern matches, the job scores 5 and bypasses the LLM.
#
# Missing file → Stage 2 becomes a no-op (warn); unknown-title no-JD jobs
# just go to the LLM.

positive:
  # Placeholder — replace with patterns that identify your target roles by title.
  - '\b__replace_me_with_a_title_regex__\b'

# ── Suggested positive patterns per field ────────────────────────────────────
# Tech / DC operations:
#   - '\bdata\s*center\s+(operations|site|manager|lead|technician|engineer)\b'
#   - '\bnpi\s+(manager|lead|engineer|program\s+manager)\b'
#   - '\boperational\s+readiness\b'
#
# Healthcare / nursing:
#   - '\b(registered|charge|ICU|PACU)\s+nurse\b'
#   - '\bnurse\s+(manager|director|educator)\b'
#
# Teaching:
#   - '\b(elementary|middle|high)\s+school\s+teacher\b'
#   - '\bteach(er|ing)\s+(assistant|aide)\b'
#
# Social work:
#   - '\bsocial\s+worker\b'
#   - '\bclinical\s+social\s+worker\b'
#   - '\bcase\s+manager\b'

# ── Poison patterns ──────────────────────────────────────────────────────────
# Title fragments that cancel an otherwise-positive match. Example: a DC ops
# candidate's positive pattern for "site operations manager" would otherwise
# match "workplace services site operations manager" — which is janitorial.
# Adding "workplace services" as poison prevents that false positive.
#
# poison:
#   - '\b(workplace\s+services|custodial|janitorial|facilities\s+only)\b'
```

- [ ] **Step 3: Create `config/companies_of_interest.txt.example`**

```
# companies_of_interest.txt — employers worth tracking closely.
# Copy this file to companies_of_interest.txt and edit. This file is gitignored.
#
# One company per line. Lines starting with # and blank lines are skipped.
# Matching is case-insensitive substring, so "meta" matches "Meta Platforms, Inc."
#
# Used by:
#   - scripts/sync_sheet.py — a job from one of these companies stays on Sheet1
#     even if it scored low and is past the archive threshold.
#   - scripts/notify.py — a job from one of these companies scored 3-6 shows up
#     in the daily health check as a potential mis-score worth reviewing.
#
# NOT used by the prefilter — scoring is the same whether a company is on this
# list or not.
#
# Missing file → both features degrade gracefully (warn).

# Tech / AI infrastructure example:
# meta
# google
# openai
# anthropic

# Healthcare example:
# kaiser permanente
# cedars-sinai

# Education example:
# lausd
# san diego unified

# Social services example:
# la county department of mental health
# st. jude children's research hospital
```

- [ ] **Step 4: Update `.gitignore`**

Append to `.gitignore`:

```
config/prefilter_rules.yaml
config/in_domain_patterns.yaml
config/companies_of_interest.txt
```

Run:
```bash
cd /home/brockamer/Code/findajob && grep -nE "^config/(prefilter_rules|in_domain_patterns|companies_of_interest)" .gitignore
```

Expected: all three lines appear.

- [ ] **Step 5: Commit**

```bash
git add config/prefilter_rules.yaml.example config/in_domain_patterns.yaml.example config/companies_of_interest.txt.example .gitignore
git commit -m "$(cat <<'EOF'
feat(config): add .example templates for prefilter + companies config (#10)

Field-agnostic stubs with commented-out per-field examples (tech,
healthcare, education, social work). .gitignore blocks the real files.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Hand-populate Brock's real configs + parity check

Transcribe the literals that were deleted in Task 6 into the real, gitignored config files. Run a diff-check against the pre-refactor behavior to confirm no unintended changes.

**Files (local-only, gitignored):**
- Create: `config/prefilter_rules.yaml`
- Create: `config/in_domain_patterns.yaml`
- Create: `config/companies_of_interest.txt`

- [ ] **Step 1: Create `config/prefilter_rules.yaml`** with Brock's full rule set

Transcribe the old `_HARD_REJECT_PATTERNS` list from `scorer_prefilter.py` (retrievable via `git show HEAD~3:src/findajob/scorer_prefilter.py`) into YAML, preserving the category grouping that lives in code comments today:

```yaml
hard_rejects:
  software_engineering:
    - '\bsoftware\s+engineer(ing)?\b'
    - '\bsoftware\s+developer\b'
    - '\bsoftware\s+architect\b'
    - '\bsoftware\s+development\s+engineer\b'
    - '\b(swe|sde)\b'
  security:
    - '\bsecurity\s+analyst\b'
    - '\bsoc\s+analyst\b'
    - '\bthreat\s+(detection|intelligence|hunting)\b'
    - '\bcyber\s*security\b'
    - '\binformation\s+security\b'
    - '\bsecurity\s+sales\b'
    - '\bsecurity\s+site\s+(operations|manager)\b'
    - '\bsecurity\s+operations\s+center\b'
  sales_bd:
    - '\baccount\s+executive\b'
    - '\bsales\s+specialist\b'
    # ... and so on, one category per comment header from the old file
  # continue until all ~150 patterns are transcribed
context_suppressors:
  - '\bdata\s*center\b|\bdatacenter\b|\bdc\s+(ops|operations|site)\b'
```

Use `git show` to fetch the pre-refactor file if needed:
```bash
cd /home/brockamer/Code/findajob && git log --oneline --follow src/findajob/scorer_prefilter.py | head -5
```

Find the commit just before Task 6's refactor, then:
```bash
cd /home/brockamer/Code/findajob && git show <commit_sha>:src/findajob/scorer_prefilter.py | less
```

Transcribe every comment-delimited block into its own `hard_rejects:<category>:` key. Suggested category names match the comment headers: `software_engineering`, `security`, `sales_bd`, `it_service_management`, `general_it_management`, `supply_chain`, `networking`, `hardware_design`, `healthcare`, `finance_legal_hr`, `construction_trades`, `aviation`, `av_events`, `food_service`, `landscaping`, `property_management`, `chemical`, `manufacturing`, `quality_process`, `systems_development`, `transportation_warehouse`, `childcare_education`, `digital_signage`, `systems_admin`, `software_ops`, `data_engineering`, `general_management`, `spam`, `facilities`.

- [ ] **Step 2: Create `config/in_domain_patterns.yaml`**

From the old `_IN_DOMAIN_PATTERNS` list and `_IN_DOMAIN_POISON`:

```yaml
positive:
  - '\bdata\s*center\s+(operations|site|manager|lead|technician|engineer)\b'
  - '\bdatacenter\s+(operations|site|manager|lead)\b'
  - '\bdc\s+(ops|operations|site\s+manager)\b'
  - '\bnpi\s+(manager|lead|engineer|program\s+manager)\b'
  - '\bhardware\s+(ops|operations|bring.up|npi|program\s+manager)\b'
  - '\binfrastructure\s+operations\s+(manager|lead|director)\b'
  - '\boperational\s+readiness\b'
  - '\blab\s+operations\s+(manager|lead)\b'
  - '\bsite\s+manager,?\s+datacenter\b'
  - '\bdatacenter.*\boperations\s+manager\b'
  - '\bdata\s+center.*\boperations\s+(area\s+)?manager\b'
  - '\bsite\s+operations\s+manager\b'
  - '\bengineering\s+operations\s+manager\b'
  - '\bfield\s+operations\s+(manager|lead)\b'

poison:
  - '\b(workplace\s+services|custodial|janitorial|facilities\s+only|office\s+services)\b'
```

- [ ] **Step 3: Create `config/companies_of_interest.txt`**

From the old `TIER1` frozenset:

```
# Brock's companies-of-interest set, migrated from TIER1 on 2026-04-17.
meta
google
alphabet
microsoft
amazon
aws
openai
anthropic
xai
etched
nscale
cerebras
groq
tenstorrent
sambanova
nebius
coreweave
crusoe
astera
spacex
together ai
runpod
fireworks
edgeconnex
hut 8
core scientific
fluidstack
aetherflux
cleanspark
t5 data
figure ai
figureai
agility robotics
apptronik
sanctuary ai
collaborative robotics
serve robotics
locus robotics
waymo
zoox
motional
nuro
helion
```

- [ ] **Step 4: Parity check — run the pipeline's prefilter against a sample**

Create a one-off script (not committed — can live in `/tmp`):

```bash
cat > /tmp/parity_check.py <<'EOF'
"""One-off: compare new loader-backed prefilter against the pre-refactor
hardcoded implementation. Run after configs are populated. NOT committed."""
import sqlite3
import subprocess
import sys

from findajob.scorer_prefilter import prefilter_score as new_score

# Fetch the pre-refactor implementation via git
pre_ref_commit = sys.argv[1]  # pass the commit sha right before Task 6
old_code = subprocess.check_output(
    ["git", "show", f"{pre_ref_commit}:src/findajob/scorer_prefilter.py"], text=True
)
old_module = {}
exec(old_code, old_module)
old_score = old_module["prefilter_score"]

conn = sqlite3.connect("/home/brockamer/Code/findajob/data/pipeline.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT title, company, COALESCE(jd_text,'') AS jd FROM jobs ORDER BY created_at DESC LIMIT 500"
).fetchall()

diffs_non_tier1 = 0
diffs_tier1_bonus = 0
for r in rows:
    jd_usable = len(r["jd"].split()) > 30
    o, _ = old_score(r["title"], r["company"], jd_usable)
    n, _ = new_score(r["title"], r["company"], jd_usable)

    if (o is None) != (n is None):
        print(f"STAGE MISMATCH: {r['title']!r} @ {r['company']!r}  old={o} new={n}")
        diffs_non_tier1 += 1
        continue

    if o is None:
        continue  # both went to LLM

    if o["relevance_score"] != n["relevance_score"]:
        # Expected diff: Tier 1 bonus dropped (old=6 → new=5)
        if o["relevance_score"] == 6 and n["relevance_score"] == 5:
            diffs_tier1_bonus += 1
        else:
            print(f"SCORE MISMATCH: {r['title']!r} @ {r['company']!r}  old={o['relevance_score']} new={n['relevance_score']}")
            diffs_non_tier1 += 1

print(f"\nTotal rows: {len(rows)}")
print(f"Expected diffs (Tier-1 bonus dropped, 6→5): {diffs_tier1_bonus}")
print(f"Unexpected diffs: {diffs_non_tier1}")
EOF

cd /home/brockamer/Code/findajob && git log --oneline src/findajob/scorer_prefilter.py | head -5
# Note the commit sha just BEFORE Task 6's refactor commit, then:
python3 /tmp/parity_check.py <commit_sha>
```

Expected output:
```
Total rows: 500
Expected diffs (Tier-1 bonus dropped, 6→5): <small number, maybe 5-30>
Unexpected diffs: 0
```

**Acceptance criteria:** `Unexpected diffs` must be 0. If not, the YAML transcription missed a pattern — diff the YAML vs the old Python literals and reconcile.

- [ ] **Step 5: No commit**

These files are gitignored — nothing to commit. Run `git status` to confirm:

```bash
cd /home/brockamer/Code/findajob && git status
```

Expected: `working tree clean` (or only unrelated untracked files from earlier sessions). The three `config/*.yaml` / `.txt` files should NOT appear.

---

## Task 10: Update docs + close #10

Mark the generalization items done, file the follow-up issue, perform the Docs audit, and close.

**Files:**
- Modify: `docs/GENERALIZATION.md`

- [ ] **Step 1: Update `docs/GENERALIZATION.md`**

Under "Scorer prefilter — `scripts/scorer_prefilter.py`":

Change the `[ ]` checkboxes for `TIER1`, `_HARD_REJECT_PATTERNS`, `_IN_DOMAIN_PATTERNS`, `_IN_DOMAIN_POISON` to `[x]`.

Append a note under that section:

```markdown
**Resolved in PR for #10 (2026-04-17):**
- `TIER1` was dropped entirely rather than externalized. The Tier-1 prefilter bonus (+1 score at the in-domain / no-JD floor) was removed.
- The "companies I care about" concept lives on via `config/companies_of_interest.txt`, consumed by `sync_sheet.py` (archival exception) and `notify.py` (mis-score health check). It does NOT feed the prefilter.
- Hard-rejects and in-domain/poison patterns now load from `config/prefilter_rules.yaml` and `config/in_domain_patterns.yaml` via `src/findajob/config_loader.py`.
- Items 4 and 5 (scorer prompt neutralization, engineer-calibration move to profile.md) are deferred — they change LLM behavior and need a separate validation loop. See follow-up issue.
```

- [ ] **Step 2: Docs audit grep**

```bash
cd /home/brockamer/Code/findajob && grep -rn "TIER1\|_is_tier1" docs/ src/ scripts/ tests/
```

Expected output: references only in (a) `docs/GENERALIZATION.md` change log entries you just added, (b) `docs/superpowers/specs/2026-04-17-generalize-prefilter-config-design.md` (the spec we committed), (c) `docs/superpowers/plans/2026-04-17-generalize-prefilter-config.md` (this plan), and (d) possibly `docs/superpowers/specs/2026-04-15-search-expansion-design.md` (prior spec — leave untouched; specs are historical). Zero hits in `src/`, `scripts/`, `tests/` (except `tests/test_companies_of_interest_consumers.py`, which asserts the absence — that's fine).

If there are unexpected hits in live code, fix them before closing.

- [ ] **Step 3: File the follow-up issue**

```bash
cd /home/brockamer/Code/findajob && gh issue create \
  --title "Neutralize job_scorer.md prompt: move domain enumerations + engineer calibration to profile" \
  --body "$(cat <<'EOF'
## Summary
Follow-up to #10. Items 4 and 5 from #10 were deferred because changing LLM scoring behavior needs its own validation loop.

## Scope
- Rewrite \`HARD REJECT RULES\` section of \`config/roles/job_scorer.md\` to reference profile categories instead of enumerating tech job types inline.
- Move \`ENGINEER TITLE CALIBRATION\` section from \`job_scorer.md\` into \`candidate_context/profile.md\` as per-candidate scoring guidance.
- Rewrite \`TIER 1 COMPANY EXCEPTION\` in-domain title enumeration to reference profile.md's \`Target Roles\` section.

## Validation
- Re-score a sample of ~50 recent jobs with old vs. new prompt. Compare scores side-by-side. Document any drift.
- Monitor feedback_log for 7 days post-deploy.

## Blocks
- #11 (user docs)
- #12 (guided onboarding)
- #13 (Docker containerization)
- #20 (Amy beta test)

## Depends on
- #10 (config externalization — needed for profile-referencing prompts to work on a generalized install)
EOF
)" --label enhancement,open-source
```

Note the issue number, then add it to the project board:

```bash
cd /home/brockamer/Code/findajob && gh project item-add 1 --owner brockamer --url <new_issue_url>
```

Set priority and work stream on the new issue per the conventions in `docs/project-board.md`:

```bash
# Get the project item ID for the new issue
NEW_ISSUE=<new_issue_number>
ITEM_ID=$(gh project item-list 1 --owner brockamer --format json --limit 100 | jq -r ".items[] | select(.content.number == $NEW_ISSUE) | .id")

# Priority: Medium (follow-up, not blocking anything today)
gh project item-edit --project-id PVT_kwHOAgGulc4BUtxZ --id "$ITEM_ID" \
  --field-id PVTSSF_lAHOAgGulc4BUtxZzhCWZ08 --single-select-option-id 4e8ef0ac

# Work Stream: Generalization
gh project item-edit --project-id PVT_kwHOAgGulc4BUtxZ --id "$ITEM_ID" \
  --field-id PVTSSF_lAHOAgGulc4BUtxZzhCWa0Y --single-select-option-id 506d8256
```

- [ ] **Step 4: Commit docs update**

```bash
git add docs/GENERALIZATION.md
git commit -m "$(cat <<'EOF'
docs(generalization): mark prefilter externalization done (#10)

Items 1-3 of #10 shipped. Items 4-5 (prompt neutralization) moved to
a follow-up issue — they change LLM behavior and need separate
validation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Close #10**

```bash
cd /home/brockamer/Code/findajob && gh issue close 10 --comment "$(cat <<'EOF'
Completed in the prefilter-externalization branch.

**What shipped:**
- \`src/findajob/config_loader.py\` — loader for \`prefilter_rules.yaml\`, \`in_domain_patterns.yaml\`, \`companies_of_interest.txt\`.
- Prefilter reads rules from gitignored YAML instead of Python literals.
- Tier-1 bonus dropped (score 5 instead of 6 for in-domain/no-JD at bonus-eligible companies). The "companies I care about" concept lives on as \`companies_of_interest.txt\`, consumed by sync_sheet (archival exception) and notify (mis-score health check).
- \`.example\` templates ship field-agnostic stubs with per-field commentary.
- 444-line prefilter test suite preserved via \`tests/fixtures/config/\` + autouse conftest.

**What deferred:** items 4 and 5 (scorer prompt rewrite, engineer calibration move to profile) — these change LLM behavior and need their own validation loop. Filed as #<NEW_ISSUE>.

**Parity check:** 500 recent jobs scored via old vs. new prefilter — zero unexpected diffs; only the intentional Tier-1 bonus drops (6→5).

**Docs audit:** \`docs/GENERALIZATION.md\` updated — items 1–3 under "Scorer prefilter" marked \`[x]\` with a changelog entry explaining the TIER1 deletion and the companies_of_interest consumer routing. Grep of \`docs/ src/ scripts/ tests/\` for \`TIER1\`/\`_is_tier1\` shows zero live-code hits (specs/plans in \`docs/superpowers/\` contain historical references, as expected).

**Board state:** #10 → Done (auto). Follow-up issue #<NEW_ISSUE> → Backlog, Priority: Medium, Work Stream: Generalization. In Progress count stays ≤ 3.
EOF
)"
```

Replace `<NEW_ISSUE>` with the actual follow-up issue number from Step 3.

- [ ] **Step 6: Verify #10 moved to Done on the board**

```bash
cd /home/brockamer/Code/findajob && gh project item-list 1 --owner brockamer --format json --limit 100 | jq '.items[] | select(.content.number == 10) | {number: .content.number, status: .status}'
```

Expected: `"status": "Done"`. If it didn't auto-move, edit manually:

```bash
ITEM_ID=$(gh project item-list 1 --owner brockamer --format json --limit 100 | jq -r '.items[] | select(.content.number == 10) | .id')
gh project item-edit --project-id PVT_kwHOAgGulc4BUtxZ --id "$ITEM_ID" \
  --field-id PVTSSF_lAHOAgGulc4BUtxZzhCOoMM --single-select-option-id a2d5723e
```

---

## Self-review checklist (run after plan is complete)

- [x] **Spec coverage:** every behavior change in the spec maps to a task:
  - Config files shape → Tasks 8, 9 (templates + real configs)
  - `config_loader.py` API → Tasks 1–5
  - Prefilter refactor + Tier-1 bonus drop → Task 6
  - Consumer swap → Task 7
  - Tests → Tasks 2–5 (unit), Task 6 (integration), Task 7 (regression), Task 9 (parity)
  - Docs audit → Task 10
  - Follow-up issue → Task 10

- [x] **Placeholder scan:** no "TBD", "TODO", "fill in", or unnamed functions.

- [x] **Type consistency:** `load_hard_reject_rules` return type `tuple[re.Pattern[str], Optional[re.Pattern[str]]]` consistent across skeleton (Task 1), implementation (Task 3), and consumer (Task 6). Same for `load_in_domain_rules`. `is_company_of_interest(company: str) -> bool` consistent across skeleton (Task 1), implementation (Task 2), and consumers (Task 7).
