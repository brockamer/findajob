# Scripts Reference

All scripts live in `scripts/`. Diag scripts live in `scripts/diag/` and are run manually only.

All scripts import `BASE`, `AICHAT`, and/or `PANDOC` from `findajob.paths` (`src/findajob/paths.py`).
Never hardcode binary paths in scripts — add overrides to `config/paths.env` instead.

Each entry below carries a **Manual run** line in the Docker form
(`docker compose exec scheduler …`). For native installs, drop that prefix and
run from the repo root with the project venv active (`uv run python3 …`).

---

## Core Pipeline Scripts

### `triage.py`
**Run by:** scheduler (daily 00:00 PT)
**No arguments.**
**Manual run:** `docker compose exec scheduler python3 scripts/triage.py`

Fetches jobs from all sources, deduplicates, enriches with JD text, then scores with LLM in parallel (6 concurrent threads), writes to SQLite.

**Sources:**
- LinkedIn / Indeed via RapidAPI jobs-api14 + JSearch (per `config/active_sources.txt`)
- Gmail IMAP (LinkedIn job alerts, Indeed digests, recruiter messages — config at `/config/gmail/`)
- Greenhouse / Lever / Ashby JSON APIs (slugs / URLs in `config/feed_urls.txt`)

**Key events logged:** `triage_started`, `job_ingested`, `job_deduplicated`, `job_scored`, `pipeline_complete`

---

### `prep_application.py`
**Run by:** `POST /board/jobs/{fp}/prep` or `/regenerate` (detached subprocess); also callable manually
**Args:** `company title url job_id`
**Manual run:** `docker compose exec scheduler python3 scripts/prep_application.py "Acme" "Engineer" "https://..." "<job_id>"`

Generates a full application package for one job. LLM calls run sequentially.

**Outputs (in `companies/{Company}_{AbbrevTitle}_{date}_{time}/`):**
- `tailored_resume_DRAFT.md` + `.docx`
- `tailored_resume_CHANGES.md`
- `cover_letter_DRAFT.md` + `.docx`
- `company_briefing.md` + `.docx`
- `outreach_*.txt` (one per matching contact, if any)
- `job_description.txt`
- `REVIEW_CHECKLIST.md`

**After completion:** updates DB to `stage=materials_drafted`, sends ntfy notification.

---

### `watchdog.py`
**Run by:** scheduler (every 10 min)
**No arguments.**
**Manual run:** `docker compose exec scheduler python3 scripts/watchdog.py`

Single responsibility: resets any job stuck in `stage='prep_in_progress'` for more than 60 minutes back to `scored`. Calls `findajob.actions.reset_prep_to_scored()` which writes an `audit_log` row and emits `prep_failed_reset`. Emits a `watchdog_run` summary event at the end of each run.

Replaced `poll_flags.py` in #61 PR-B — transition logic now lives in `findajob.actions` and is called from the web POST handlers in `findajob.web.routes.board_actions`.

---

### `notify.py`
**Run by:** scheduler (5 scheduled subcommands) + 2 manual-only; also all callable manually
**Args:** one subcommand
**Manual run:** `docker compose exec scheduler python3 scripts/notify.py <subcommand>`

| Subcommand | What it sends |
|---|---|
| `daily-stats` | Queue depth, today's new jobs, last triage timestamp |
| `health-check` | Errors from last 25h of logs, last `pipeline_complete` event, stuck prep_in_progress jobs |
| `issues-ping` | Open issues from GitHub board |
| `apply-reminder` | Rotating motivational nudge to submit an application |
| `feedback-review` | Alert when `feedback_log` has ≥ 10 entries to analyze |
| `send-raw` | Send an arbitrary notification: `notify.py send-raw <title> <body>` |
| `ci-check` | Check latest GitHub Actions CI run; alert with high priority if failed |
| `scoreboard` | Regenerate pipeline funnel scoreboard and update GitHub issue #31 |

ntfy topic is read from `NTFY_TOPIC` in `data/.env`.

---

### `find_contacts.py`
**Run by:** `prep_application.py` (step 5)
**Args:** `company jd_text_excerpt outdir`
**Manual run:** `docker compose exec scheduler python3 scripts/find_contacts.py "Acme" "<jd-excerpt>" companies/<folder>`

Reads `data/connections.csv`, finds LinkedIn connections at the target company, and generates personalized outreach drafts for each match via the OpenRouter wrapper.

**Output:** `{outdir}/outreach_{FirstName}_{LastName}.txt` for each match.

**Key guard:** `company_match()` always checks `if not s or not c: return False` — blank company strings would otherwise match everything.

---

### `ingest_form.py` (retired)
**Run by:** manually, only. Scheduled timer removed in #62.
**No arguments.**
**Manual run:** `docker compose exec scheduler python3 scripts/ingest_form.py`

Superseded by the `/ingest/` web form (`src/findajob/web/routes/ingest.py`), which is now the operator write surface for manual job submissions. New submissions use `source='web_manual'`.

The script is kept in place as a manual-run fallback in case any Google Form responses need to be drained after the web form ships; it still polls the Form responses sheet and writes rows with `source='manual_form'`. It can be removed once the Google Form is fully decommissioned.

---

### `manual_prep.py`
**Run by:** manually (when you have a job outside the pipeline)
**Args:** optional path to job file (default: `manual_job.txt`)
**Manual run:** `docker compose exec scheduler python3 scripts/manual_prep.py [path/to/job.txt]`

File format:
```
company: CompanyName
title: Job Title
url: https://...
---
Full JD text below this line
```

Inserts job into DB and calls `prep_application.py` immediately.

---

### `scorer_prefilter.py`
**Run by:** imported by `triage.py` and `rescore_all.py` — not called directly
**Function:** `prefilter_score(title, jd_text, company) → (score, reason) or None`

Two-stage deterministic filter:
- Stage 1: title regex → score 1 (hard reject domains: healthcare, pure SWE, sales, security, etc.)
- Stage 2: in-domain title + no JD → score 5 or 6 (reasonable guess without content)

Returns `None` if the job should proceed to LLM scoring.

---

### `rescore_all.py`
**Run by:** manually (after model or prompt changes)
**No arguments.**
**Manual run:** `docker compose exec scheduler python3 scripts/rescore_all.py`

Re-runs the scorer on all jobs that have JD text. Use after changing `job_scorer` role or switching scorer model.

---

### `rename_folders.py`
**Run by:** manually (idempotent)
**No arguments.**
**Manual run:** `docker compose exec scheduler python3 scripts/rename_folders.py`

Renames `companies/` folders from old format (`{Company}_{date}_{time}`) to new format (`{Company}_{AbbrevTitle}_{date}_{time}`). Looks up DB for title. Updates `prep_folder_path` in DB. Safe to re-run — skips already-renamed folders.

---

### `init_db.py`
**Run by:** once on new install
**No arguments.**
**Manual run:** `docker compose exec scheduler python3 scripts/init_db.py`

Creates `data/pipeline.db` with all tables: `jobs`, `audit_log`, `feedback_log`. Safe to re-run — uses `CREATE TABLE IF NOT EXISTS`.

---

## Diag Scripts (`scripts/diag/`)

Run manually for debugging. Not part of normal pipeline operation.

### `debug_contacts.py`
Shows contact matching diagnostics for a batch of jobs. Useful for debugging false positive/negative company name matches.
**Manual run:** `docker compose exec scheduler python3 scripts/diag/debug_contacts.py`
