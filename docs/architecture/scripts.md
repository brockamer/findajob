# Scripts reference

Reference for coding agents and maintainers, linked from [`AGENTS.md`](../../AGENTS.md) § Key File Locations. Operator-facing manual commands are in [`docs/operations/README.md`](../operations/README.md).

All scripts live in `scripts/`. Diag scripts live in `scripts/diag/` and are run manually only. All scripts import `BASE` and `PANDOC` from `findajob.paths` (`src/findajob/paths.py`). Never hardcode binary paths in scripts — add overrides to `config/paths.env` instead.

**Docker vs Fly:** Docker: `docker compose exec scheduler <cmd>` · Fly: `fly ssh console --app <app> --command "<cmd>"`

## Core pipeline scripts

### `triage.py`
**Run by:** scheduler (daily 00:00 in the container's `TZ`). No arguments.
**Manual run:** `docker compose exec scheduler python3 scripts/triage.py`

Fetches jobs from all sources, deduplicates, enriches with JD text, then scores with LLM in parallel (6 concurrent threads), writes to SQLite.

**Sources:**
- LinkedIn / Indeed via RapidAPI jobs-api14 + JSearch (per `config/active_sources.txt`).
- Gmail IMAP (LinkedIn job alerts, Indeed digests, recruiter messages — config at `/config/gmail/`).
- Greenhouse / Lever / Ashby JSON APIs (slugs / URLs in `config/feed_urls.txt`).

**Key events logged:** `triage_started`, `job_ingested`, `job_deduplicated`, `job_scored`, `pipeline_complete`.

### `scripts/prep_application.py` (entry-point shim)
*Entry-point shim; implementation in `src/findajob/prep/orchestrator.py`.*

**Run by:** `POST /board/jobs/{fp}/prep` or `/regenerate` (detached subprocess); also callable manually. Args: `company title url job_id`.
**Manual run:** `docker compose exec scheduler python3 scripts/prep_application.py "Acme" "Engineer" "https://..." "<job_id>"`

Generates a full application package for one job. LLM calls run sequentially.

**Outputs (in `companies/{Company}_{AbbrevTitle}_{date}_{time}/`):**
- `{Prefix} Resume - {Company} - {Title} - {timestamp}.md` + `.docx`
- `{Prefix} Resume Changes - {Company} - {Title} - {timestamp}.md` (checked change report; unfound quotes marked `[UNVERIFIED]`)
- `{Prefix} Cover - {Company} - {Title} - {timestamp}.md` + `.docx`
- `{Prefix} Briefing - {Company} - {Title} - {timestamp}.md` + `.docx`
- `{Prefix} Critique - {Company} - {Title} - {timestamp}.md`
- `{Prefix} Outreach to {Contact} - {Company} - {timestamp}.txt` (one per matching contact, if any)
- `JD - {Company} - {Title}.txt`
- `Review Checklist - {Company} - {Title}.md`

Names come from `findajob.prep_naming.build_prep_filenames`.

After completion: updates DB to `stage=materials_drafted`, sends ntfy notification.

### `watchdog.py`
**Run by:** scheduler (every 10 min). No arguments.
**Manual run:** `docker compose exec scheduler python3 scripts/watchdog.py`

Resets any job stuck in `stage='prep_in_progress'` for more than 60 minutes: Phase A rows back to `scored` via `findajob.actions.reset_prep_to_scored()` (writes an `audit_log` row and emits `prep_failed_reset`), Phase B rows back to `briefing_ready` via `reset_prep_to_briefing_ready`. Returns `briefing_ready` jobs older than the decision window (`FINDAJOB_BRIEFING_READY_WINDOW_DAYS`, default 14 days, `0` = off) to `scored`, keeping `prep_folder_path` (`briefing_ready_stale_reset`). Emits a `watchdog_run` summary event at the end of each run.

### `notify.py` (entry-point shim)
*Entry-point shim; implementation in `src/findajob/notifications/`.*

**Run by:** scheduler (4 subcommands; see `docs/operations/README.md` → Notifications for the per-subcommand schedule and content).
**Manual run:** `docker compose exec scheduler python3 scripts/notify.py <subcommand>`

### `scripts/find_contacts.py` (entry-point shim)
*Entry-point shim; implementation in `src/findajob/find_contacts.py`.*

**Run by:** `scripts/prep_application.py` (step 5). Args: `company jd_text_excerpt outdir`.
**Manual run:** `docker compose exec scheduler python3 scripts/find_contacts.py "Acme" "<jd-excerpt>" companies/<folder>`

Reads `data/connections.csv`, finds LinkedIn connections at the target company, generates personalized outreach drafts via the OpenRouter wrapper.

**Output:** `{outdir}/outreach_{FirstName}_{LastName}.txt` for each match.

**Key guard:** `company_match()` always checks `if not s or not c: return False` — blank company strings would otherwise match everything.

### `manual_prep.py`
**Run by:** manually (when you have a job outside the pipeline). Args: optional path to a job file (default: `manual_job.txt`).
**Manual run:** `docker compose exec scheduler python3 scripts/manual_prep.py [path/to/job.txt]`

File format:
```
company: CompanyName
title: Job Title
url: https://...
---
Full JD text below this line
```

Inserts the job into DB and calls `scripts/prep_application.py` immediately.

### `rescore_all.py`
**Run by:** manually (after model or prompt changes). No arguments.
**Manual run:** `docker compose exec scheduler python3 scripts/rescore_all.py`

Re-runs the scorer on all jobs that have JD text.

### `rename_folders.py`
**Run by:** manually. No arguments.
**Manual run:** `docker compose exec scheduler python3 scripts/rename_folders.py`

Renames `companies/` folders from old format (`{Company}_{date}_{time}`) to new format (`{Company}_{AbbrevTitle}_{date}_{time}`). Looks up DB for title, updates `prep_folder_path` in DB. Safe to re-run.

### `init_db.py`
**Run by:** once on new install. No arguments.
**Manual run:** `docker compose exec scheduler python3 scripts/init_db.py`

Creates `data/pipeline.db` with all tables. Safe to re-run — uses `CREATE TABLE IF NOT EXISTS`.

## Diag scripts (`scripts/diag/`)

Run manually for debugging. Not part of normal pipeline operation.

### `debug_contacts.py`
Shows contact matching diagnostics for a batch of jobs. Useful for debugging false positive/negative company-name matches.
**Manual run:** `docker compose exec scheduler python3 scripts/diag/debug_contacts.py`
