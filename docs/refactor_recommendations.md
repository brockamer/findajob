# JobSearchPipeline — Refactor Recommendations
*Generated: 2026-04-08*

---

## What's Actually Working Well

The core architecture is sound. Three-stage scoring (deterministic prefilter → LLM → JSON validation) is the right pattern. Direct profile injection instead of RAG for candidate context was the right call. The systemd-driven daily run is solid. The role system in aichat-ng gives you per-model tuning without code changes. The fingerprint deduplication works. These are all good decisions — don't touch them.

---

## Critical Issues (Fix Now)

**1. API keys in aichat-ng's config.yaml are plaintext**
Every key you use (Anthropic, Gemini, OpenAI, xAI, Groq, Perplexity, OpenRouter) is in a single unencrypted file. If you back up your home directory or run anything that can read `~/.config/`, those keys are exposed. The fix is to move them to a `chmod 600` env-var file and reference them via `env:VARIABLE_NAME` in the aichat-ng config, which it supports natively. See #67.

**~~2. No retry logic anywhere~~** *(fixed 2026-04-12)*
Fetch retry loop added to `triage.py main()`: 3 attempts with 120s gaps and connectivity probing. Covers the "DNS is down at 7 AM" failure mode that caused a total whiff on 2026-04-12.

**3. `prep_application.py` subprocess failures are silent**
`check=False` on the pandoc calls and the find_contacts subprocess means you can get a partial prep folder with no error surfaced. One aichat-ng timeout mid-run and you get a folder with a resume but no cover letter, with `materials_drafted` in the DB as if everything succeeded.

---

## High-Impact Refactors (Worth Doing)

**4. `prep_application.py` runs everything serially — it's very slow**
Six sequential LLM calls, each blocking on the previous. Resume tailor → change reviewer → cover letter → researcher → briefing writer → outreach — all in one long chain. The resume tailor, cover letter writer, company researcher, and find_contacts are fully independent. They could all run in parallel threads, cutting wall time roughly 4x. This is the single biggest quality-of-life improvement.

**5. `triage.py` has no transaction safety**
Jobs are committed one-by-one as they're processed. If triage crashes at job 400 out of 800, you have 400 partial records in inconsistent states. The fix is to wrap each job's ingest-enrich-score cycle in an explicit transaction and only commit on success.

**6. ~~`sync_sheet.py` has an O(n²) bug in COL_MAP lookup~~** *(fixed 2026-04-08)*
`S1_LOOKUP` and `DASH_LOOKUP` are now built once as reverse dicts. `build_row()` does a single dict lookup per cell.

**~~7. `rclone bisync` path is fragile and inconsistently referenced~~** *(resolved 2026-04-20)*
bisync and all rclone sync removed entirely. Local folder moves (`_applied/`, `_rejected/`, `_waitlisted/`) are now the source of truth; no longer synced to Google Drive. Users manage Drive content via the web viewer deployed on localhost:8080.

---

## Medium-Impact Issues (Fix When You Have Time)

**8. `company_match()` in `find_contacts.py` has false-positive risk**
Uses substring containment after stripping suffixes. "Apple" matches "GreenApple Inc." If `company` is a short or common string (like "AI"), it will match everything. Should use word-boundary regex instead of `in`.

**9. ~~The `company_signal` column exists everywhere but is never written~~ (resolved — deprecated)**
Column removed from init_db.py schema. Company intel surfaced via company_briefing.docx at prep time instead. Live DB column left in place (harmless, always empty).

**10. `needs_info` is a valid stage in the DB schema but not in `scoring_schema.json` and never used in code**
Dead code that adds confusion. Either implement it or remove it from the DB constraint.

**11. `clean_company.py` is a now-obsolete patch script**
The function it patched is already live in `triage.py`. Delete it.

**12. Log files grow forever**
`pipeline.jsonl` is already 1.6 MB after a few weeks. No rotation. The poller stderr log had 25 KB of the same error repeated. Add a `logrotate` entry or a weekly rotation cron.

---

## Lower Priority (Leave Alone or Defer)

- ~~**No unit tests**~~ *(superseded 2026-04-12)* — 302 unit tests now cover all pure functions in scorer_prefilter.py, utils.py, and triage.py. CI runs pytest on every push.
- **Hardcoded tool paths** — the CLAUDE.md governance prevents misuse; the paths are stable on this machine. Not worth abstracting.
- **RAG corpus quality** — acknowledged low priority ([#15](https://github.com/brockamer/findajob/issues/15)); RAG is only used in REPL mode.
- **No pagination on Greenhouse API** — Greenhouse returns full job lists by default; pagination isn't needed unless a single company has >250 openings.

---

## What Shipped in the 2026-04-12 Quality Sprint

| Item | Impact |
|---|---|
| **pyproject.toml** | Pinned deps, pytest/ruff/mypy config, `pip install -e .` |
| **Ruff linting + formatting** | 22 files normalized, 5 real bugs caught (shadowed fn, unused vars) |
| **302 unit tests** | All pure functions in scorer_prefilter, utils, cleaning covered. 0.22s. |
| **triage.py decomposed** | 1,167→448 lines. New: cleaning.py, fetchers.py, scoring.py |
| **GitHub Actions CI** | ruff check + format + mypy + pytest on every push |
| **Package restructuring** | Library code → `src/findajob/`, all sys.path.insert hacks removed |
| **Type annotations + mypy** | All 6 library modules fully annotated, mypy enforced in CI |
| **Fetch retry** | 3-attempt retry loop in triage.py with connectivity probing |
| **Timer fix** | OnCalendar replaces broken OnUnitActiveSec for poller/jobsync/form-ingest |

## Remaining Refactor Order

| Priority | Work | Impact |
|---|---|---|
| 1 | Move API keys out of aichat-ng config.yaml to env vars | Security |
| 2 | Parallelize prep_application.py LLM calls with threads | ~4x faster prep |
| 3 | Fix prep subprocess error handling (`check=True`, log failures) | No more silent failures |
| 4 | DB transaction safety in triage.py (wrap per-job cycle) | No partial state on crash |
| 5 | Integration tests (DB fixtures, mock API responses) | Catches "pieces don't fit" bugs |
| 6 | DB migration system (Alembic) | Schema versioning for multi-machine deploys |
| 7 | Log rotation via logrotate | Housekeeping |
| ~~8~~ | ~~Delete `scripts/clean_company.py`~~ | **Gitignored — harmless** |
