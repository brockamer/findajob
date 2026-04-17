# Generalize Prefilter Config — Design

**Issue:** [#10](https://github.com/brockamer/findajob/issues/10) — Generalize config layer: externalize TIER1, prefilter, in-domain patterns
**Scope:** Items 1–3 from the issue (prefilter externalization). Items 4–5 (prompt rewrites) deferred to a follow-up.
**Date:** 2026-04-17

---

## Goal

Move domain-specific rule data out of `scorer_prefilter.py` into gitignored config files, so a new user in a different field can run the pipeline without editing tracked Python code.

## Non-goals

- Rewriting `config/roles/job_scorer.md` hard-reject / engineer-calibration sections (items 4–5 of the issue — follow-up).
- Building a guided `setup_profile.py` onboarding flow (that is issue #12).
- Changing the prefilter's Stage 1 / Stage 2 control flow. Only the *source* of the data moves.

## Behavior change inventory

| Component | Before | After |
|---|---|---|
| Tier-1 prefilter bonus (in-domain + no JD → score 6) | Scores 6 for Tier-1 companies, 5 otherwise | Always scores 5. Bonus deleted. |
| `_is_tier1()` / `TIER1` frozenset | Public in `scorer_prefilter.py` | Removed entirely |
| Archival exception in `sync_sheet.py` | Driven by `_is_tier1()` | Driven by `is_company_of_interest()` from new config |
| Mis-score health check in `notify.py` | Driven by `TIER1` | Driven by `is_company_of_interest()` from new config |
| `_HARD_REJECT_PATTERNS` literal | ~150 regex in code | Loaded from `config/prefilter_rules.yaml` |
| `_DC_CONTEXT_RE` override | Hardcoded in module | Loaded as `context_suppressors` from `config/prefilter_rules.yaml` |
| `_IN_DOMAIN_PATTERNS` + `_IN_DOMAIN_POISON` | In-code | Loaded from `config/in_domain_patterns.yaml` (`positive` + `poison` keys) |

All other prefilter behavior is preserved bit-for-bit.

## Architecture

### New module: `src/findajob/config_loader.py`

Single module responsible for reading the three config files, validating, compiling regexes, caching.

Public API:

```python
def load_hard_reject_rules() -> tuple[re.Pattern[str], re.Pattern[str] | None]:
    """(reject_re, suppressor_re). suppressor_re is None if no suppressors configured."""

def load_in_domain_rules() -> tuple[re.Pattern[str], re.Pattern[str] | None]:
    """(in_domain_re, poison_re). poison_re is None if no poison configured."""

def load_companies_of_interest() -> frozenset[str]:
    """Lowercase company names. Used for case-insensitive substring matching."""

def is_company_of_interest(company: str) -> bool:
    """Case-insensitive substring check. False for empty/None inputs."""

class ConfigError(Exception):
    """Raised when a config file is malformed (bad YAML, bad regex, wrong shape)."""

def _reset_cache() -> None:
    """Test-only. Clears module-level caches."""
```

- **Caching:** each `load_*` computes once, stashes in module-level global, returns cached result on subsequent calls.
- **Empty/missing file:** returns a sentinel regex that never matches (`re.compile(r"(?!x)x")`) for regex loaders; empty `frozenset()` for companies. Emits `warnings.warn(UserWarning, ...)` once per affected file per process.
- **Malformed:** raises `ConfigError` naming the file and the offending input (bad YAML, bad regex, wrong top-level shape). Strict — partially working prefilters are worse than loud failures.
- **File locations:** via `findajob.paths.BASE`. No new path constants.

### Refactored module: `src/findajob/scorer_prefilter.py`

Regex objects now come from the loader at import time:

```python
from findajob.config_loader import load_hard_reject_rules, load_in_domain_rules

_HARD_REJECT_RE, _SUPPRESSOR_RE = load_hard_reject_rules()
_IN_DOMAIN_RE, _POISON_RE = load_in_domain_rules()
```

Functions `_hard_reject_match()`, `_in_domain_match()`, and `prefilter_score()` keep their signatures. Stage 2 simplifies: no Tier-1 branch, score is always 5 when matched. All `TIER1`, `_is_tier1`, and literal pattern lists are deleted.

### Consumer updates

- **`scripts/sync_sheet.py:18,195`** — replace `from findajob.scorer_prefilter import _is_tier1` with `from findajob.config_loader import is_company_of_interest`; update the list comprehension.
- **`scripts/notify.py:407,423`** — replace `from findajob.scorer_prefilter import TIER1` with `from findajob.config_loader import is_company_of_interest`; replace the substring loop with a call per row.

## Config file shapes

### `config/prefilter_rules.yaml` (gitignored)

```yaml
# Hard-reject title patterns. If any matches, score=1 (no LLM).
# Exception: if any context_suppressor also matches, the reject is skipped.
hard_rejects:
  software_engineering:
    - '\bsoftware\s+engineer(ing)?\b'
    - '\b(swe|sde)\b'
  healthcare:
    - '\bnurs(e|ing)\b'
  # ... more categories (grouping is presentational; loader flattens)

context_suppressors:
  - '\bdata\s*center\b|\bdatacenter\b|\bdc\s+(ops|operations|site)\b'
```

Top-level keys: `hard_rejects` (required, dict of category → list of regex) and `context_suppressors` (optional, list of regex). Unknown keys raise `ConfigError`.

### `config/in_domain_patterns.yaml` (gitignored)

```yaml
positive:
  - '\bdata\s*center\s+(operations|site|manager|lead|technician|engineer)\b'
  - '\bnpi\s+(manager|lead|engineer|program\s+manager)\b'

poison:
  - '\b(workplace\s+services|custodial|janitorial|facilities\s+only|office\s+services)\b'
```

Top-level keys: `positive` (required, list), `poison` (optional, list).

### `config/companies_of_interest.txt` (gitignored)

```
# One company per line. Case-insensitive substring match.
# Used by sync_sheet.py (archival exception) and notify.py (mis-score health check).
# NOT used by the prefilter.
meta
google
# ...
```

Blank lines and lines starting with `#` are skipped. Each entry lowercased at load time. No validation beyond that.

## Example files (tracked)

Per CLAUDE.md generalization rules, `.example` files must not hardcode one-field content. Each example is a **minimal stub with multi-field commentary**:

- `config/prefilter_rules.yaml.example` — one sample category with one pattern (field-agnostic, e.g., a generic "job alert" spam filter), plus a commented-out block per field (tech / healthcare / education / social work) showing suggested categories for that field so a new user can uncomment and edit the one that fits them.
- `config/in_domain_patterns.yaml.example` — one sample positive + one sample poison using field-agnostic placeholders, commentary on how the two interact.
- `config/companies_of_interest.txt.example` — empty, with a comment block explaining the purpose and showing a few generic placeholder entries.

The actual `.yaml` and `.txt` files on Brock's machine are populated by hand during the PR (see Migration).

## Error handling

| Condition | Behavior |
|---|---|
| File missing | `warnings.warn(UserWarning)`, return empty / no-op. |
| File exists, empty or whitespace-only | Warn, return empty / no-op. |
| File exists, YAML parses but wrong top-level shape (e.g., `hard_rejects` is a list not a dict) | `ConfigError` with path + expected shape. |
| File exists, YAML parse error | `ConfigError` with path + parser error. |
| Valid shape, one regex fails to compile | `ConfigError` naming the file, the category (if applicable), and the pattern. Do NOT continue with partial config. |
| `companies_of_interest.txt` has invalid chars | No validation — anything goes (substring match is forgiving). |

The warning surface uses `warnings.warn(UserWarning)` so it appears in pytest output, CLI stderr, and systemd journal without requiring a logger.

## Migration

**One-time, manual, done as part of the PR:**

1. Hand-translate the current Python literals in `scorer_prefilter.py` into `config/prefilter_rules.yaml` and `config/in_domain_patterns.yaml` on Brock's machine. Preserve the category comments as YAML keys under `hard_rejects`.
2. Hand-create `config/companies_of_interest.txt` from the current `TIER1` set (49 entries).
3. Verify via a one-off diff-check: on a branch with both the old `scorer_prefilter.py` and the new loader-backed version reachable, pull a sample of ~200 recent jobs from `data/pipeline.db`, run each title through both implementations, assert identical `(score_status, relevance_score)` outputs — modulo the intentional Tier-1 bonus drop (old score=6 → new score=5 when `_is_tier1(company)` was true). The diff-check runs once on Brock's laptop and is not committed.

Git history preserves the old literals for reference.

## Test strategy

### Fixtures

New `tests/fixtures/config/` directory containing:
- `prefilter_rules.yaml` — representative subset of Brock's current rules, enough to exercise every code path (reject hit, suppressor override, miss).
- `in_domain_patterns.yaml` — subset of positive + poison.
- `companies_of_interest.txt` — handful of well-known company names matching existing test assertions.

### `conftest.py` autouse fixture

Monkeypatches `config_loader`'s path constants to point at `tests/fixtures/config/`. Calls `_reset_cache()` before each test. Every existing `test_scorer_prefilter.py` test continues to pass because the fixture patterns include the strings those tests assert against.

### Existing test file: `tests/test_scorer_prefilter.py`

- `TestIsTier1` class → **deleted** (underlying function is gone).
- Stage 2 tests asserting `score == 6` for Tier-1 companies → updated to assert `score == 5`.
- All other test classes unchanged.

### New test file: `tests/test_config_loader.py`

Covers:
- Valid YAML → correct regex objects + correct sentinel regex on empty lists.
- Missing file → no-op regex + one `UserWarning`.
- Empty file / empty list → no-op + warning.
- Malformed YAML → `ConfigError`.
- Bad regex → `ConfigError` naming file + pattern.
- Top-level shape violations (wrong type, unknown keys) → `ConfigError`.
- `companies_of_interest.txt`: comment handling, case normalization, empty file → empty + warning.
- `is_company_of_interest`: substring match, empty input guard, None input guard.

### New test file: `tests/test_companies_of_interest_consumers.py`

- Asserts `scripts/sync_sheet.py` and `scripts/notify.py` import `is_company_of_interest` (regression guard against re-introducing `_is_tier1`).

### Manual validation after landing

- Run `triage.py` on today's fetched jobs. Compare prefilter decisions to the previous day's log.
- `SELECT COUNT(*) FROM jobs WHERE relevance_score=6 AND ai_notes LIKE '%Tier 1%'` before and after to quantify the dropped-bonus impact.

## Deletion inventory

From `src/findajob/scorer_prefilter.py`:
- `TIER1` frozenset
- `_is_tier1()`
- `_HARD_REJECT_PATTERNS` list
- `_HARD_REJECT_RE` compiled pattern (replaced by loader output)
- `_DC_CONTEXT_RE` compiled pattern (replaced by `_SUPPRESSOR_RE` from loader)
- `_IN_DOMAIN_PATTERNS` list
- `_IN_DOMAIN_RE` compiled pattern (replaced by loader output)
- `_IN_DOMAIN_POISON` compiled pattern (replaced by loader output)
- Stage 2 Tier-1 branch in `prefilter_score()` (the `tier1 = _is_tier1(company)` line and the `score = 6 if tier1 else 5` split)

From `tests/test_scorer_prefilter.py`:
- `TestIsTier1` class
- Stage 2 Tier-1 score assertions (updated, not fully deleted)

## Build sequence inside the PR

1. Add `src/findajob/config_loader.py` (with empty-sentinel stubs, no consumers yet).
2. Add `tests/test_config_loader.py` + `tests/fixtures/config/*`.
3. Add `conftest.py` autouse fixture pointing the loader at fixtures.
4. Create `.example` files (tracked).
5. Hand-create real `config/prefilter_rules.yaml`, `in_domain_patterns.yaml`, `companies_of_interest.txt` on Brock's machine (gitignored).
6. Refactor `scorer_prefilter.py` to read from loader; delete the literals.
7. Update `tests/test_scorer_prefilter.py` (delete `TestIsTier1`, update Stage-2 assertions).
8. Update `scripts/sync_sheet.py` and `scripts/notify.py` to use `is_company_of_interest`.
9. Add `tests/test_companies_of_interest_consumers.py`.
10. Run diff-check: sample jobs through old vs. new prefilter, confirm identical decisions modulo the dropped bonus.
11. Update `docs/GENERALIZATION.md`: mark items 1–3 of "Scorer prefilter" done; note items 4–5 deferred to a follow-up issue.
12. Update `.gitignore` if `companies_of_interest.txt` / `prefilter_rules.yaml` / `in_domain_patterns.yaml` aren't already covered.

## Documentation updates (Docs audit gate)

Per CLAUDE.md Definition of Done, before closing this issue:

- [ ] `docs/GENERALIZATION.md` — items 1–3 under "Scorer prefilter" marked `[x]`, with a note that `TIER1` was dropped rather than externalized and `companies_of_interest.txt` now serves the archival + health-check use cases.
- [ ] `docs/architecture.md` — if it references the prefilter rules, update.
- [ ] `docs/operations.md` — if it mentions `TIER1` or the Tier-1 bonus, update.
- [ ] Grep check: `grep -r 'TIER1\|_is_tier1' docs/` — should return zero after this PR.

## Follow-up issue to file

Items 4–5 of #10 (prompt rewrites) deferred to a new issue titled "Neutralize job_scorer.md prompt — reference profile categories, move engineer calibration to profile.md". Blocks: #11, #12, #13, #20.

## Open questions resolved in brainstorming

- Q1 → no `tier1` file; `target_companies.md` untouched.
- Q2 → items 1–3 only, not 4–5.
- Q3 → YAML for both rule files, txt for companies_of_interest.
- Q4 → empty defaults + warning.
- Q5 → minimal stub `.example` with multi-field comments.
- Q6 → drop Tier-1 prefilter bonus entirely (Option X-a). New `companies_of_interest.txt` for sync_sheet + notify consumers.
