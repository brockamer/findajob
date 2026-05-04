# Architecture

The same Python codebase runs identically under Docker and native installs.
What differs is the **scheduler layer** (supercronic in the container vs.
systemd timers natively) and the **process model** (uvicorn co-process inside
the same container under Docker; separate user service natively). Everything
described below — fetchers, prefilter, scorer, prep, DB schema — is platform-
agnostic.

For setup, see [`setup/install-docker.md`](setup/install-docker.md) (canonical)
or [`setup/install-linux.md`](setup/install-linux.md) (fallback).

## Scheduler

Schedules live in **`ops/scheduled-jobs.yaml`** (canonical, repo-tracked). Under
Docker, `scripts/render_crontab.py` renders that YAML to `/app/crontab` at
entrypoint, and **supercronic** runs the resulting cron file in the foreground
of the container. Under native installs, the same YAML is materialized into
`systemctl --user` timers. Per-job overrides (`FINDAJOB_<JOB>_SCHEDULE`,
`FINDAJOB_<JOB>_ENABLED`) work in either mode — they're consumed by the
crontab renderer / unit-file generator. See CLAUDE.md§"Container Context" for
the full env-override surface.

## Overview

Two distinct workflows, both scheduler-driven:

| Workflow | Trigger | Duration | Output |
|---|---|---|---|
| **Daily Triage** | 00:00 daily (supercronic / systemd timer) | 30–60 min | 100–500 jobs scored and written to SQLite |
| **Prep** | User flags a job in the Dashboard | 5–10 min | Folder with resume, cover letter, briefing, outreach drafts |

Everything between them is mediated by SQLite. The Google Sheet is a synced view — not the source of truth (sync path slated for retirement, #331).

---

## Daily Triage Pipeline

```
┌─────────────────────────────────────────────────────────┐
│  triage.py (00:00 daily — supercronic / systemd timer)  │
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
          │  Web UI (/board/*)          │
          │  Dashboard, Applied,        │
          │  Review, Waitlist tabs      │
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

## Key Design Decisions

| Decision | Why |
|---|---|
| SQLite as canonical store | The DB is ACID, queryable, and zero-config. No external dependencies for the core data model. |
| Direct profile injection (not RAG) | RAG chunking drops contact info, employer names, and dates. Profile is short enough to inject raw. |
| Two-stage prefilter before LLM | Hard rejects (wrong domain, title-deterministic) don't need LLM calls. Saves ~$0.10/day and speeds triage. |
| JSON output validation | `jsonschema` validates every LLM scoring response. Malformed output → manual_review, not a crash. |
| Web materials viewer (not Drive) | Prep folders are served locally via uvicorn/FastAPI — no cloud sync dependency. Markdown rendered inline; `.docx` offered as download. Eliminates rclone auth complexity and Drive quota issues. |
| Web POST handlers are the sole write surface | Every handler in `findajob.web.routes.board_actions` calls straight into `findajob.actions` and responds in the same request — no poll cycle, no mirror table, single source of truth in SQLite. |
| `abbrev_title()` in folder names | Same-day preps for the same company would overwrite each other without title disambiguation. HHMMSS suffix prevents same-title same-day overwrites. |
