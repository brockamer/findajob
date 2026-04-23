# Architecture

> **Docker users:** This document describes system design using native-install
> terminology. Pipeline structure is identical under Docker; the scheduler is
> supercronic reading `ops/crontab` rather than systemd timers. The container
> also runs uvicorn alongside supercronic as a co-process, serving the
> materials viewer on `FINDAJOB_MATERIALS_PORT`.
> Docker-specific refresh tracked in #76.
>

## Overview

Two distinct workflows, both scheduler-driven:

| Workflow | Trigger | Duration | Output |
|---|---|---|---|
| **Daily Triage** | 00:00 daily via scheduler | 30–60 min | 100–500 jobs scored and written to SQLite |
| **Prep** | User flags a job in the Dashboard | 5–10 min | Folder with resume, cover letter, briefing, outreach drafts |

Everything between them is mediated by SQLite. The Google Sheet is a synced view — not the source of truth.

---

## Daily Triage Pipeline

```
┌─────────────────────────────────────────────────────────┐
│                    triage.py (00:00 daily)              │
└──────────────────────┬──────────────────────────────────┘
                       │
          ┌────────────┴────────────┐
          │                         │
  ┌───────▼──────────┐   ┌──────────▼──────────┐
  │  Gmail OAuth2    │   │  jobs-api14 RapidAPI │
  │  - LinkedIn jobs │   │  - LinkedIn search   │
  │  - Indeed digest │   │  - Indeed search     │
  │  - Recruiter msg │   │  - /v2/linkedin/get  │
  └───────┬──────────┘   └──────────┬──────────┘
          └────────────┬────────────┘
                       │
          ┌────────────▼────────────────┐
          │  Greenhouse JSON API        │
          │  (per slug in feed_urls.txt)│
          └────────────┬────────────────┘
                       │
          ┌────────────▼────────────────┐
          │  clean_title() + clean_company() │
          │  SHA-256 fingerprint dedup  │
          └────────────┬────────────────┘
                       │
          ┌────────────▼────────────────────────────┐
          │  scorer_prefilter.py                    │
          │  Stage 1: title regex → score 1, skip   │
          │  Stage 2: in-domain, no JD → score 5/6  │
          └────────────┬────────────────────────────┘
                       │  (jobs that pass both stages)
          ┌────────────▼────────────────┐
          │  aichat-ng job_scorer       │
          │  Model: DeepSeek v3.2       │
          │  Profile injected directly  │
          │  Output: JSON + validation  │
          └────────────┬────────────────┘
                       │
          ┌────────────▼────────────────┐
          │  SQLite write (pipeline.db) │
          │  Audit log per transition   │
          └────────────┬────────────────┘
                       │
          ┌────────────▼────────────────┐
          │  sync_sheet.py              │
          │  Sheet1 (archive)           │
          │  Dashboard (actionable)     │
          └─────────────────────────────┘
```

---

## Prep Workflow

```
User clicks "Flag for Prep" in /board/dashboard
          │
          ▼
POST /board/jobs/{fp}/prep  (findajob.web.routes.board_actions)
  idempotency guard: no-op if stage in (prep_in_progress, materials_drafted)
  concurrency cap: 429 if 3 preps already in flight
  stage → prep_in_progress; spawns prep_application.py via Popen
          │
          ▼
prep_application.py (detached subprocess, start_new_session=True)
  Loads JD from DB (never re-curls)
  Loads profile.md + master_resume.md (direct injection, no RAG)
          │
    ┌─────┴──────────────────────────────────────┐
    │  Sequential LLM calls (aichat-ng):          │
    │  1. resume_tailor → tailored_resume_DRAFT.md │
    │  2. resume_change_reviewer → CHANGES.md     │
    │  3. cover_letter_writer → cover_letter_DRAFT.md │
    │  4. company_researcher (Perplexity sonar-reasoning-pro) │
    │  5. briefing_writer → company_briefing.md   │
    │  6. find_contacts.py → outreach_*.txt       │
    └─────┬──────────────────────────────────────┘
          │
    pandoc converts .md → .docx for each document
          │
    DB updated: stage = materials_drafted
    sync_sheet.py runs at prep end (one-way DB → Sheet)
    ntfy notification sent
    Materials viewer reflects new folder immediately (no sync required)

scripts/watchdog.py (runs every 10 min): resets any job stuck in
  prep_in_progress > 60 min back to scored so the operator can re-flag.
```

---

## Data Model

### SQLite Tables

**`jobs`** — one row per unique job posting
```
id TEXT PRIMARY KEY          -- generated UUID or "manual-{hex}"
fingerprint TEXT UNIQUE      -- SHA-256[:16] of normalized title+company+url
source TEXT                  -- linkedin_jobsapi | indeed | greenhouse | gmail_linkedin | gmail_google | manual | manual_form
title TEXT
company TEXT
location TEXT
remote_status TEXT           -- Remote | Hybrid | On-site | Unknown
url TEXT
raw_jd_text TEXT             -- full JD text fetched at ingest time
relevance_score INTEGER      -- 1–10 from LLM scorer
interview_likelihood INTEGER -- 1–10 from LLM scorer
ai_notes TEXT                -- LLM rationale
score_status TEXT            -- scored | manual_review | prefiltered
stage TEXT                   -- discovered | enriched | scored | manual_review |
                             --   prep_in_progress | materials_drafted | waitlisted |
                             --   applied | interview | offer | withdrawn | rejected
apply_flag INTEGER           -- 0/1, mirrors Dashboard STATUS
reject_reason TEXT
comp_estimate TEXT
known_contacts TEXT
user_notes TEXT              -- free text set via Applied tab col I
stage_updated TEXT           -- ISO timestamp of last stage change
prep_folder_path TEXT        -- absolute path to companies/ subfolder
gdrive_folder_url TEXT       -- legacy column (Drive sync removed; unused since v0.2)
fit_score REAL               -- 0-100% avg from fit_analyst
probability_score REAL       -- 0-100% avg from fit_analyst
created_at TEXT
updated_at TEXT
```

**`audit_log`** — immutable record of every field change
```
id INTEGER PRIMARY KEY AUTOINCREMENT
job_id TEXT
field_changed TEXT
old_value TEXT
new_value TEXT
changed_at TEXT DEFAULT (datetime('now'))
```

**`feedback_log`** — rejection history for pattern analysis
```
id INTEGER PRIMARY KEY AUTOINCREMENT
job_id TEXT
title TEXT
company TEXT
relevance_score INTEGER
reject_reason TEXT
jd_excerpt TEXT              -- first 500 chars of JD
logged_at TEXT DEFAULT (datetime('now'))
```

---

## Google Sheet Layout

> **One-way synced view.** As of #61 PR-B, `sync_sheet.py` writes DB state to
> the Sheet but never reads from it. Operators drive every STATUS and
> REJECT_REASON transition through the web UI at `/board/*`; edits made
> directly in the Sheet are overwritten on the next sync cycle.

### Sheet1 — Full Archive (A–N)
All jobs that passed dedup. Read-only reference view.

| Col | Field | Notes |
|---|---|---|
| A | fingerprint | Hidden — matches board_actions.py POST targets |
| B | APPLY_FLAG | Checkbox (TRUE/FALSE) |
| C | relevance_score | |
| D | title | |
| E | company | |
| F | location | |
| G | remote_status | |
| H | stage | |
| I | known_contacts | |
| J | comp_estimate | |
| K | ai_notes | |
| L | date_found | |
| M | source | |
| N | url | |

### Dashboard — Pre-Application Queue (A–N)
Filter: `(score >= 7 AND stage IN (scored, manual_review))` OR `stage IN (prep_in_progress, materials_drafted)`.
The web UI at `/board/dashboard` renders the same rows live from the DB. `POST /board/jobs/{fp}/apply` transitions stage to `applied`; the row disappears from Dashboard and appears on `/board/applied` on the next render.

| Col | Field | Notes |
|---|---|---|
| A | STATUS | Dropdown — user sets this |
| B | REJECT_REASON | Dropdown — triggers rejection workflow |
| C | fingerprint | Hidden |
| D | fit_score | LLM-assigned fit score |
| E | probability_score | LLM-assigned interview probability |
| F | relevance_score | 1–10 composite score |
| G | title | Hyperlink to job URL |
| H | company | Company name |
| I | location | |
| J | remote_status | Color-coded |
| K | known_contacts | Amber when non-empty |
| L | comp_estimate | |
| M | ai_notes | |
| N | date_found | |

**STATUS dropdown options (Dashboard — pre-application):** `Flag for Prep` → `Prep in Progress` *(system)* → `Ready to Apply` *(system)* → `Applied` *(user)*. Also: `Regenerate` (re-runs prep), `Waitlist` (defers the job). Once marked `Applied`, the row moves to the Applied tab where `Interviewing` / `Offer` / `Not Selected` / `Withdrew` are set.

**REJECT_REASON:** behavior depends on STATUS. If STATUS = `Not Selected`: company rejection → `stage=not_selected`, no feedback_log, folder stays in `_applied/`. Otherwise: user rejection → `stage=rejected`, feedback_log entry, folder moved to `_rejected/`.

### Applied — Post-Application Queue (A–N)
Filter: `stage IN (applied, interview, offer)`. UI for managing jobs that have been submitted.

| Col | Field | Notes |
|---|---|---|
| A | STATUS | Dropdown: `Interviewing` / `Offer` / `Not Selected` / `Withdrew` |
| B | REJECT_REASON | Dropdown — same 11 options as Dashboard |
| C | fingerprint | Hidden |
| D | title | Hyperlink to job URL |
| E | company | Company name |
| F | applied_date | Date job was marked Applied (from `audit_log`) |
| G | days_since_applied | Live `=IF(F2="","",TODAY()-F2)` formula |
| H | stage | `applied` / `interview` / `offer` (read-only) |
| I | user_notes | Free text, syncs back to `jobs.user_notes` |
| J | known_contacts | |
| K | location | |
| L | remote_status | |
| M | comp_estimate | |
| N | ai_notes | Read-only (scorer output) |

Row-color priority (first match wins): Offer→gold, Interviewing→purple, `>=21 days`→gray (silent = likely ghosted), 14–20d→red, 7–13d→yellow, 0–6d→green. Flip to `Not Selected` when giving up on a silent row.

### Review — Manual Review Triage (A–H)
Filter: `stage = manual_review` (scorer flagged for human review, e.g., null scores or schema failures).

| Col | Field | Notes |
|---|---|---|
| A | STATUS | Dropdown: `Promote` |
| B | REJECT_REASON | Dropdown — same options as Dashboard |
| C | fingerprint | Hidden |
| D | title | Hyperlink to job URL |
| E | company | |
| F | score_flag_reason | Why the scorer flagged this job |
| G | source | |
| H | date_found | |

`Promote` sets score=7, stage=scored → job appears on Dashboard. REJECT_REASON rejects the job.

### Waitlist — Deferred Jobs (A–K)
Filter: `stage = waitlisted`.

| Col | Field | Notes |
|---|---|---|
| A | STATUS | Dropdown: `Reactivate` |
| B | REJECT_REASON | Dropdown — same options as Dashboard |
| C | fingerprint | Hidden |
| D | title | Hyperlink to job URL |
| E | company | |
| F | relevance_score | |
| G | location | |
| H | remote_status | |
| I | ai_notes | |
| J | date_found | |
| K | blocking_app | Active application at same company (computed at sync time) |

`Reactivate` restores to `scored` (no folder) or `materials_drafted` (has folder), moves folder back from `_waitlisted/`. When an active application at the same company is rejected/withdrawn, ntfy surfaces waitlisted jobs.

### Rejected Applications (A–H)
Jobs that were rejected after reaching `applied` stage. Read-only reference view.

| Col | Field | Notes |
|---|---|---|
| A | title | Hyperlink to job URL |
| B | company | Company name |
| C | reject_reason | |
| D | applied_date | Date the job was marked Applied |
| E | rejected_date | Date the rejection was recorded |
| F | fit_score | |
| G | probability_score | |
| H | ai_notes | |

---

## Key Design Decisions

| Decision | Why |
|---|---|
| SQLite as canonical store | Sheets-as-database creates race conditions and blocks programmatic queries. SQLite is ACID, queryable, and zero-config. |
| Direct profile injection (not RAG) | RAG chunking drops contact info, employer names, and dates. Profile is short enough to inject raw. |
| Two-stage prefilter before LLM | Hard rejects (wrong domain, title-deterministic) don't need LLM calls. Saves ~$0.10/day and speeds triage. |
| JSON output validation | `jsonschema` validates every LLM scoring response. Malformed output → manual_review, not a crash. |
| Web materials viewer (not Drive) | Prep folders are served locally via uvicorn/FastAPI — no cloud sync dependency. Markdown rendered inline; `.docx` offered as download. Eliminates rclone auth complexity and Drive quota issues. |
| Web POST handlers are the sole write surface | Operators used to edit the Google Sheet and wait up to 10 min for poll_flags.py to mirror it to the DB. Every handler in `findajob.web.routes.board_actions` calls straight into `findajob.actions` and responds in the same request, eliminating the poll-cycle race window. |
| `abbrev_title()` in folder names | Same-day preps for the same company would overwrite each other without title disambiguation. HHMMSS suffix prevents same-title same-day overwrites. |
