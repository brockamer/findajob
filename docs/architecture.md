# Architecture

The pipeline runs as a single Docker container (`ghcr.io/brockamer/findajob`)
with supercronic and uvicorn as co-processes. For setup, see
[`operations/install-docker.md`](operations/install-docker.md).

## Scheduler

Schedules live in **`ops/scheduled-jobs.yaml`** (canonical, repo-tracked).
`scripts/render_crontab.py` renders that YAML to `/app/crontab` at entrypoint,
and **supercronic** runs the resulting cron file in the foreground of the
container. Per-job overrides (`FINDAJOB_<JOB>_SCHEDULE`,
`FINDAJOB_<JOB>_ENABLED`) are consumed by the crontab renderer. See
CLAUDE.md§"Container Context" for the full env-override surface.

## Overview

Two distinct workflows, both scheduler-driven:

| Workflow | Trigger | Duration | Output |
|---|---|---|---|
| **Daily Triage** | 00:00 daily (supercronic) | 30–60 min | 100–500 jobs scored and written to SQLite |
| **Prep** | User flags a job in the Dashboard | 5–10 min | Folder with resume, cover letter, briefing, outreach drafts |

Everything between them is mediated by SQLite.

---

## Daily Triage Pipeline

```
┌─────────────────────────────────────────────────────────┐
│  triage.py (00:00 daily — supercronic)  │
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
          │  OpenRouter wrapper         │
          │  Role: job_scorer           │
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

The prep pipeline is **seven sequential LLM stages plus an outreach sidecar**,
orchestrated by `src/findajob/prep/orchestrator.py`. Each stage's output
becomes explicit input to the next — no vector embeddings, no RAG retrieval.
This makes failures diagnosable (you can literally read what context each
stage saw) and quality predictable. See [Key Design Decisions](#key-design-decisions)
for the rationale behind "direct injection, not RAG".

### Entry path

```
User clicks "Flag for Prep" in /board/dashboard
          │
          ▼
POST /board/jobs/{fp}/prep  (findajob.web.routes.board_actions)
  idempotency guard: no-op if stage in (prep_in_progress, materials_drafted)
  concurrency cap: 429 if 3 preps already in flight
  stage → prep_in_progress; spawns scripts/prep_application.py via Popen
          │
          ▼
scripts/prep_application.py (45-line entry-point shim, detached subprocess)
  → findajob.prep.orchestrator.main() → _run_prep()
  Loads JD from DB (never re-curls — LinkedIn etc. require auth)
  Loads profile.md + master_resume.md (direct injection, no RAG)
  Builds shared cached_prefix blocks for cross-stage Anthropic prompt caching
```

### Sequential LLM stages

| # | Stage | Model | Consumes | Produces | Cache prefix |
|---|---|---|---|---|---|
| 1 | `company_researcher` | Perplexity sonar-reasoning-pro | company, title, JD | raw research notes | none (Perplexity) |
| 2 | `briefing_writer` | Claude Opus 4.7 | profile + master + JD + raw research | structured briefing ending with `## Overall Recommendation` | `shared_candidate_jd` |
| 3 | `fit_analyst` | Perplexity sonar-reasoning-pro | profile + master + JD + briefing | Fit Matrix + Probability Assessment (6+3 percent dimensions) | none |
| — | **Merge** | (deterministic) | briefing + fit_analysis | `briefing.md` — fit spliced **BEFORE** the Overall Recommendation so the doc reads detail → synthesis → verdict | — |
| 4 | `resume_tailor` | Claude Opus 4.7 | profile + master + JD + merged briefing | `resume.md` (tailored) | `shared_candidate_jd` |
| 5 | `resume_change_reviewer` | Gemini Flash | master resume + tailored resume + JD | `CHANGES.md` — diff/justification | none (cheap diff) |
| 6 | `cover_letter_writer` | Claude Opus 4.7 | profile + master + JD + voice samples + merged briefing + tailored resume | `cover.md` | `shared_with_voice` |
| 7 | `recruiter_critic` | Claude Opus 4.7 | company + title + JD + tailored resume + cover (**not** profile/briefing/fit) | `critique.md` — skeptical outside read | `jd-only` |

After stage 7, the outreach sidecar runs as a blocking subprocess:

```
Step 5: scripts/find_contacts.py (subprocess.run, check=True)
  → findajob.find_contacts entry → outreach_drafter role (Claude Opus 4.7)
  Reads LinkedIn connections.csv, finds known contacts at the company,
  drafts personalized outreach messages → outreach_*.txt
```

### Retry / validation gates (inline)

- **Stage 2 retry** — `briefing_writer` output is checked for `## Overall Recommendation`. If missing, the stage runs once more with the same prompt before continuing. Emits `briefing_missing_recommendation` (#636 family).
- **Stage 3 retry** — `fit_analyst` output passes through `_fit_analysis_is_complete()`, which requires a `Fit Matrix` section, exactly one `## 🎯 Probability Assessment` heading, and at least one `:NN%` percent-score on each side of that heading. Retry once on failure. Mirrors the briefing retry. Emits `fit_analyst_retry`. Perplexity `sonar-reasoning-pro` intermittently returns `content=null` — the retry handles that gracefully.
- **Step 7 validation** — after retries, if the merged briefing still lacks an Overall Recommendation, prep fails cleanly with stage rolled back to `scored` rather than shipping a malformed briefing.

### Per-stage model selection rationale

The full per-role model table lives in [`maintainers/pipeline-context.md`](maintainers/pipeline-context.md).
The judgment behind each pick:

- **Opus 4.7 for high-voice creative outputs** (`briefing_writer`, `resume_tailor`, `cover_letter_writer`, `recruiter_critic`, `outreach_drafter`) — these stages own the voice; Opus produces materials that read like they were written by someone who actually researched the company.
- **Perplexity sonar-reasoning-pro for web-research roles** (`company_researcher`, `fit_analyst`) — built-in web grounding with citations, lower cost than running a coding-model + tool-call loop.
- **Gemini Flash for simple diffs** (`resume_change_reviewer`) — the model only needs to produce a structured comparison; spending Opus tokens on this is wasteful.
- **DeepSeek v3.2 for high-volume scoring** (`job_scorer`, separate triage pipeline) — 100–500 calls per daily triage; cost-sensitive; DeepSeek's structured-output reliability is sufficient at scoring depth.

### Speculative cold-outreach branch

For synthetic rows (cold-outreach via `/ingest/speculative/`), Stages 1–2 are
**skipped**. The deep-research briefing produced by `candidate_led_briefing` at
ingest time and approved by the operator on the review page is reused directly
(#320). If the speculative briefing.md is missing or empty, the orchestrator
falls back to the regular Stages 1–2 flow. The `<<SPECULATIVE_MODE>>` marker
is prepended to the cover-letter prompt so the role-prompt branches into
cold-outreach voice.

### Pandoc + storage

```
pandoc converts each .md → .docx (briefing, resume, cover)
DB updated: stage = materials_drafted; fit_score + probability_score persisted
ntfy notification sent
Materials viewer reflects new folder immediately (no sync required)
```

### Failure handling

`scripts/watchdog.py` (runs every 10 min): resets any job stuck in
`prep_in_progress > 60 min` back to `scored` so the operator can re-flag.

If a pandoc or `find_contacts` subprocess fails (non-zero exit), the
orchestrator immediately rolls stage back to `scored`, writes a
`.failed_subprocess` sentinel into the prep folder (cmd / returncode /
stderr tail) and emits `prep_subprocess_failed` — no waiting for the
60-min watchdog (#495).

If `profile.md` or `master_resume.md` are missing, prep aborts before any
LLM call, rolls stage back to `scored`, and notifies via ntfy.

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
gdrive_folder_url TEXT       -- legacy column, unused (Drive sync retired)
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
