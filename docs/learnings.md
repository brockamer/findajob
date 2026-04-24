# Learnings — What We Know About the System

Living reference document. Things discovered through operation that aren't obvious from the code.
Updated as we learn. Not a task list — a knowledge base.

---

## Job Source Quality

| Source | Signal | Noise | Notes |
|--------|--------|-------|-------|
| Greenhouse ATS feeds | HIGH | LOW | Direct from company career pages. Full JDs. Best source overall — 78 of 159 7+ scored jobs came from 15 feeds. |
| LinkedIn job alert emails (Gmail) | HIGH | LOW | Pre-filtered by LinkedIn's algorithm to match profile. High relevance when JD is fetched via API. |
| LinkedIn search (RapidAPI) | MEDIUM | MEDIUM | Good for specific title queries. 3-4 word natural phrases only — keyword-heavy strings (5+) return zero results. |
| Indeed search (RapidAPI) | LOW | HIGH | 1,107 jobs/week, 91% hard-rejected by prefilter. Broad matching, lots of irrelevant results. Still worth running — captures companies not on Greenhouse. |
| Ashby ATS feeds | HIGH | LOW | Similar quality to Greenhouse. Fewer companies use it but the ones that do (OpenAI, Fluidstack) are high-value. |
| Lever ATS feeds | MEDIUM | LOW | Smaller coverage but clean data. |
| Google Form (manual entry) | HIGHEST | NONE | User-curated. Auto-scored at 8. Use for jobs found through networking or browsing. |

## Scorer Behavior

- **DeepSeek v3.2 via OpenRouter** is cheap ($0.001/job) and good enough. Not worth upgrading.
- The scorer's biggest failure mode is **false positives on "engineer" titles** — IC hardware roles (mechanical, electrical, systems dev) score high because the candidate's background includes "engineer" titles. Fixed via ENGINEER TITLE CALIBRATION in the scorer prompt.
- **Remote-Friendly and similar non-enum values** cause JSON schema validation failures. The `_normalize_llm_output()` function maps these to valid enums before validation.
- Scoring 348 jobs takes ~50 minutes sequentially. Parallelization could bring this to ~15 min.
- The **feedback loop** (`analyze_feedback.py`) is the most important quality signal. First run showed 80.6% false positive rate on score 8+. Led to prefilter expansion and scorer prompt calibration.

## Infrastructure

- **SQLite busy_timeout** must be set on every connection (30s). Without it, concurrent access from triage + poller crashes with "database is locked."
- **systemd `OnUnitActiveSec` timers** lose their re-arm chain after boot. Use `OnCalendar` instead.
- ~~**`rclone bisync`** fights with any other process that writes to Drive.~~ *(removed 2026-04-20: all Drive sync removed; local folders are now source of truth)*
- ~~**Drive-side `rclone move`** preserves file content during folder transitions.~~ *(removed 2026-04-20: local folder moves are final; no Drive push)*
- **Greenhouse public API** (`boards-api.greenhouse.io`) has no auth, no documented rate limits, and is designed for programmatic use. Safe to poll daily.
- **LinkedIn direct curl** always returns auth wall. Must use RapidAPI `/v2/linkedin/get?id=` for JD fetching.

## Prefilter Design

- Hard-reject patterns catch 91% of Indeed noise at zero LLM cost.
- **Never relax "mechanical engineer" or "electrical engineer"** hard-rejects. The false negative rate (a good job incorrectly rejected) is near zero. The alternative — relaxing the pattern — lets through thousands of pure ME/EE roles from Indeed.
- **`data center` context override** (`_DC_CONTEXT_RE`): titles containing "data center" bypass hard-reject patterns, because a "data center supply chain manager" is relevant even though "supply chain manager" alone is not.

## Google Sheets Architecture

- The **Dashboard filter** (score≥7 + scored/manual_review, or materials_drafted) is the user's daily decision queue. Everything else is noise.
- **REJECT_REASON dropdown** drives the feedback loop. Reason categories should be specific enough to signal prefilter improvements ("Too TPM-Heavy" → add TPM patterns).
- **Company rejections should NOT use REJECT_REASON** — they contaminate the scorer feedback loop. See issue #5 (not_selected stage).
