---
**Archived 2026-04-19. shipped — strip_jd_boilerplate() and JD_MAX_CHARS present in src/findajob/.**
---

# JD Quality Improvement — Design Spec

**Date:** 2026-04-10
**Status:** Approved
**Priority:** C (improve own results) — first initiative

---

## Problem

447 JDs (16.6% of 2,691 jobs) are truncated at exactly 8,000 characters due to a hardcoded `[:8000]` cap in 6 fetch paths across `triage.py` and `backfill_jd.py`. Truncated JDs are cut mid-sentence, losing requirements, qualifications, and compensation details.

Compounding this: 57% of JDs contain trailing EEO/legal boilerplate consuming ~17% of text (~900 chars average). Within the 8k cap, boilerplate displaces actual role content.

An additional 180 JDs are garbage (169 gmail_linkedin placeholders from failed fetches, 11 gmail_google expired postings). These are mostly already rejected and are not in scope for this work.

### Impact on scoring

The scorer sees incomplete JDs for 16.6% of jobs. Responsibilities are present but qualifications, location details, and compensation are truncated. This biases scoring toward intro-heavy signals and away from requirements-based fit assessment.

---

## Solution: Strip boilerplate, raise cap, backfill truncated JDs

Three components, applied in order:

### 1. `strip_jd_boilerplate()` function

**Location:** `scripts/utils.py`

**Strategy:** Work backwards from the end of the JD, paragraph by paragraph. If a paragraph matches a boilerplate pattern, remove it. Stop trimming when a paragraph looks like real role content. This preserves mid-JD diversity statements (adjacent to real content) while stripping the trailing legal/EEO/benefits tail.

**Paragraph splitting:** Split on double-newline (`\n\n`) or lines that look like section headers (all-caps, or short line ending with `:`).

**Boilerplate signals** (case-insensitive paragraph matching):

- **EEO:** "equal opportunity employer", "equal employment opportunity", "we do not discriminate", "without regard to race", "affirmative action"
- **Legal:** "reasonable accommodation", "E-Verify", "employment eligibility verification", "right to work"
- **Benefits headers:** paragraphs starting with "Benefits:", "What we offer:", "Our benefits include", "Perks & benefits"
- **Disclaimers:** "this posting is not", "salary ranges may vary", "the above is intended to describe", "nothing in this job posting"
- **Application boilerplate:** "how to apply", "to apply, please", "apply now at"

**Not in scope:** "About [Company]" sections — too many false positives.

**Safety rails:**
- Never strip more than 40% of the JD. If detection would remove more, return original text.
- Minimum retained length of 200 chars.
- Log a warning if stripping removes more than 30% (trend monitoring via `log_event`).

### 2. Raise the hard cap: `JD_MAX_CHARS = 16000`

**Location:** Constant defined in `scripts/utils.py`.

**Rationale:** After boilerplate stripping, the longest real JDs are ~6-7k chars. 16k provides 2x headroom while preventing runaway content (malformed HTML dumps, concatenated pages). 16k chars is ~4k tokens — well within all model context windows, keeps scorer cost proportional.

**Order of operations in every fetch path:**
1. Fetch raw text (no cap at fetch time)
2. `strip_jd_boilerplate(text)`
3. `text[:JD_MAX_CHARS]` (safety cap)
4. Return

**Truncation sites to update (6 total):**

| File | Line | Current | New |
|------|------|---------|-----|
| `triage.py` | 122 | `text[:8000]` in `fetch_jd_curl()` | `strip_jd_boilerplate(text)[:JD_MAX_CHARS]` |
| `triage.py` | 163 | `description[:8000]` in `fetch_linkedin_job_data()` | `strip_jd_boilerplate(description)[:JD_MAX_CHARS]` |
| `triage.py` | 193 | `desc[:8000]` in `fetch_jd()` Indeed path | `strip_jd_boilerplate(desc)[:JD_MAX_CHARS]` |
| `triage.py` | 205 | `.stdout[:8000]` in `fetch_jd()` Greenhouse pandoc | `strip_jd_boilerplate(plain)[:JD_MAX_CHARS]` |
| `triage.py` | 208 | `desc[:8000]` in `fetch_jd()` Greenhouse fallback | `strip_jd_boilerplate(desc)[:JD_MAX_CHARS]` |
| `backfill_jd.py` | 69 | `description[:8000]` | `strip_jd_boilerplate(description)[:JD_MAX_CHARS]` |
| `prep_application.py` | 103 | `[:8000]` fallback curl path | `[:JD_MAX_CHARS]` (no stripping — fallback only) |

### 3. Backfill truncated JDs

**File:** `scripts/backfill_jd.py` — extended with `--truncated` flag.

**Target:** Jobs where `LENGTH(raw_jd_text) BETWEEN 7900 AND 8000` (~447 jobs).

**Fetch strategy by source:**

| Source | Count | Method | Cost |
|--------|------:|--------|------|
| `greenhouse_json` | 178 | Re-fetch from `boards-api.greenhouse.io`, pandoc HTML-to-plain | Free |
| `jobsapi_linkedin` | 55 | RapidAPI `/v2/linkedin/get?id=` | ~$0.55 |
| `gmail_linkedin` | 46 | Same RapidAPI endpoint | ~$0.46 |
| `gmail_google` | 3 | Direct curl to Google Careers URL | Free |
| `jobsapi_indeed` | 164 | **Skip** — no re-fetch path | N/A |
| `manual` | 1 | Skip | N/A |

**Total paid API cost:** ~$1.00 (98 LinkedIn requests at ~$0.01 each)

**Flow per job:**
1. Fetch full text via source-appropriate method
2. Apply `strip_jd_boilerplate()`
3. Cap at `JD_MAX_CHARS`
4. If new text is longer than existing, update `raw_jd_text` and `updated_at`
5. If new text is shorter or same length, skip (original wasn't actually truncated, or re-fetch returned less)

**CLI flags:**
- `backfill_jd.py` (no args) — existing behavior: missing gmail_linkedin JDs
- `backfill_jd.py --truncated` — re-fetch truncated JDs across all recoverable sources
- `backfill_jd.py --rescore` — rescore affected jobs after backfill (combinable)
- `backfill_jd.py --dry-run` — report what would be fetched without doing it

**Rate limiting:** 0.3s between paid API calls (existing). 0.1s between free curl/Greenhouse fetches.

**Logging:** `backfill_truncated_started` and `backfill_truncated_complete` events with counts by source.

---

## What does NOT change

- **Scorer prompts** — scorer receives `raw_jd_text` from DB; cleaner JDs flow through automatically
- **`prep_application.py` main path** — reads JD from DB, benefits automatically
- **Google Sheets** — JD text is never synced to sheets
- **Database schema** — `raw_jd_text` is TEXT with no length constraint, no migration needed
- **`sync_sheet.py`, `poll_flags.py`, `notify.py`** — none touch JD text

---

## Testing plan

1. Run `backfill_jd.py --truncated --dry-run` — verify it identifies ~447 jobs, categorizes by source, skips Indeed
2. Pick 3 truncated Greenhouse JDs, re-fetch manually, confirm new text is longer and boilerplate-stripped
3. Pick 1 truncated LinkedIn JD, re-fetch via API, confirm same
4. Run `backfill_jd.py --truncated` for real
5. Spot-check 5 backfilled JDs: compare old (8k) vs new length, verify gained content is role requirements/quals
6. Run a triage cycle, confirm new ingests get stripped JDs with higher cap

---

## Future work (not in scope)

- **A: Scoring accuracy analysis** — use the improved JDs to assess false negative rates across the ~2,400 scored jobs. Enabled by this work.
- **B: Source coverage** — evaluate alternative job APIs. Independent of JD quality.
- **E: Feedback loop** — systematic learning from rejection patterns. Enabled by cleaner JD data.

These are captured in `docs/IDEAS.md`.
