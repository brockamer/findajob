# Architecture

findajob is a single-container pipeline that turns raw job postings into
tailored application materials. Two scheduler-driven workflows do the work: a
nightly **triage** pass that ingests, deduplicates, and LLM-scores hundreds of
postings, and an on-demand **prep** pass that researches a company and drafts a
tailored resume, cover letter, briefing, and outreach messages for a single
role. SQLite sits between them as the only source of truth.

The whole system runs as one Docker image (`ghcr.io/brockamer/findajob`) with
**supercronic** (cron) and **uvicorn** (the web UI) as co-processes — no
external database, message broker, or object store. State is a single SQLite
file plus a tree of generated folders on a mounted volume, so the entire
deployment reduces to "pull the image, mount a volume, set a few API keys."
For setup, see [`operations/install-docker.md`](operations/install-docker.md).

If you're evaluating the design, the thing to notice is *where the determinism
lives*. LLM calls are expensive and non-deterministic, so they are fenced on
both sides: hard rejects happen in code **before** any model is called, every
scoring response is schema-validated **after**, and prompt context is injected
directly rather than retrieved through a vector store. Every state change flows
through a single web write-surface and is recorded in an append-only audit log.
The payoff is a system whose failures are diagnosable by reading — you can see
exactly what each stage received and produced — rather than by re-running a
black box.

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
          ┌────────────▼─────────────────────────────────┐
          │  Source adapters — JobSourceAdapter registry  │
          │  (src/findajob/fetchers/adapters/)            │
          │    RapidAPI: jobs-api14 · -indeed · -bing ·   │
          │              jsearch                          │
          │    ATS:      greenhouse · ashby · lever ·     │
          │              workday-cxs · gem                │
          │    Boards:   remote-ok · remotive ·           │
          │              we-work-remotely · himalayas ·   │
          │              jobicy · algora · hn             │
          │    Gmail IMAP (LinkedIn / Indeed alerts)      │
          │  active set ← config/active_sources.txt       │
          └────────────┬─────────────────────────────────┘
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

Every source implements one `JobSourceAdapter` protocol
(`src/findajob/fetchers/adapters/base.py`); adding a feed is one new adapter
file plus one entry in `REGISTERED_ADAPTERS`. `triage.py` iterates the
registry — there are no per-source branches in the triage path. Which adapters
actually run on a given stack is gated by `config/active_sources.txt` and each
adapter's own `is_configured()` check, so an unconfigured source is skipped
rather than erroring.

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
| 2 | `briefing_writer` | Claude Opus 4.8 | profile + master + JD + raw research | structured briefing ending with `## Overall Recommendation` | `shared_candidate_jd` |
| 3 | `fit_analyst` | Perplexity sonar-reasoning-pro | profile + master + JD + briefing | Fit Matrix + Probability Assessment (6+3 percent dimensions) | none |
| — | **Merge** | (deterministic) | briefing + fit_analysis | `briefing.md` — fit spliced **BEFORE** the Overall Recommendation so the doc reads detail → synthesis → verdict | — |
| 4 | `resume_tailor` | Claude Opus 4.8 | profile + master + JD + merged briefing | `resume.md` (tailored) | `shared_candidate_jd` |
| 5 | `resume_change_reviewer` | Gemini Flash | master resume + tailored resume + JD | `CHANGES.md` — diff/justification | none (cheap diff) |
| 6 | `cover_letter_writer` | Claude Opus 4.8 | profile + master + JD + voice samples + merged briefing + tailored resume | `cover.md` | `shared_with_voice` |
| 7 | `recruiter_critic` | Claude Opus 4.8 | company + title + JD + tailored resume + cover (**not** profile/briefing/fit) | `critique.md` — skeptical outside read | `jd-only` |

After stage 7, the outreach sidecar runs as a blocking subprocess:

```
Step 5: scripts/find_contacts.py (subprocess.run, check=True)
  → findajob.find_contacts entry → outreach_drafter role (Claude Opus 4.8)
  Reads LinkedIn connections.csv, finds known contacts at the company,
  drafts personalized outreach messages → outreach_*.txt
```

### Retry / validation gates (inline)

- **Stage 2 retry** — `briefing_writer` output is checked for `## Overall Recommendation`. If missing, the stage runs once more with the same prompt before continuing. Emits `briefing_missing_recommendation` (#636 family).
- **Stage 3 retry** — `fit_analyst` output passes through `_fit_analysis_is_complete()`, which requires a `Fit Matrix` section, exactly one `## 🎯 Probability Assessment` heading, and at least one `:NN%` percent-score on each side of that heading. Retry once on failure. Mirrors the briefing retry. Emits `fit_analyst_retry`. Perplexity `sonar-reasoning-pro` intermittently returns `content=null` — the retry handles that gracefully.
- **Step 7 validation** — after retries, if the merged briefing still lacks an Overall Recommendation, prep fails cleanly with stage rolled back to `scored` rather than shipping a malformed briefing.

### Per-stage model selection rationale

The full per-role model table lives in [`CLAUDE.md` § Per-Role Model Assignments](../CLAUDE.md#per-role-model-assignments).
The judgment behind each pick:

- **Opus 4.8 for high-voice creative outputs** (`briefing_writer`, `resume_tailor`, `cover_letter_writer`, `recruiter_critic`, `outreach_drafter`) — these stages own the voice; Opus produces materials that read like they were written by someone who actually researched the company.
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

### Post-apply: interview prep

Distinct from the prep pipeline above, an interview-prep generator fires on the
`applied → interview` transition (`scripts/interview_prep.py`): it produces a
study guide, an Anki flashcard deck, and a two-speaker audio podcast for the
role. Re-running it (e.g. after a recruiter shares panel details) is a button
on the materials page. These long-running generators, along with prep and
speculative research, are tracked in the `background_tasks` table so the UI can
show progress and a watchdog can reap stalled runs.

---

## Data Model

SQLite is the single source of truth. The schema is defined by the numbered
migrations in **`src/findajob/migrations/`**, applied in order at startup —
that directory, not this document, is the canonical, column-level reference.
The tables below are summarized for orientation; consult the migrations for
exact types, defaults, and `CHECK` constraints.

### Core tables

The four tables a contributor reads or queries most often:

**`jobs`** — one row per unique job posting; the spine of the whole system
```
id TEXT PRIMARY KEY              -- UUID, or "manual-{hex}" for hand-added rows
fingerprint TEXT UNIQUE          -- SHA-256[:16] of normalized title+company+url (Tier-1 dedup)
loose_fingerprint TEXT           -- title+company only (Tier-2 cross-source dedup)
source TEXT                      -- adapter id: jobs-api14 | greenhouse | ashby | gmail_linkedin | web_speculative | ...
title / company / location TEXT
url TEXT
raw_jd_text TEXT                 -- full JD captured at ingest (prep reads this, never re-fetches)
remote_status TEXT               -- Remote | Hybrid | On-site | Unknown
relevance_score INTEGER          -- 1–10 from LLM scorer
interview_likelihood INTEGER     -- 1–10 from LLM scorer
strengths_alignment TEXT
industry_sector TEXT
ai_notes TEXT                    -- LLM rationale
score_status TEXT                -- scored | manual_review
score_flag_reason TEXT
company_tier TEXT                -- target-company signal (tuning loop)
scored_by TEXT                   -- scorer model/version provenance
stage TEXT                       -- lifecycle; full enum in 0001_initial.sql's CHECK:
                                 --   discovered → scored → prep_in_progress → briefing_ready →
                                 --   materials_drafted → applied → interview → offer, plus
                                 --   waitlisted / rejected / not_selected / withdrawn[_fallback]
status TEXT                      -- coarse legacy status (active | applied | rejected | ...)
apply_flag INTEGER               -- 0/1, mirrors Dashboard STATUS
reject_reason TEXT
comp_estimate TEXT
network_depth INTEGER            -- connection proximity from connections.csv
known_contacts TEXT
user_notes TEXT                  -- free text from the board Notes column
fit_score / probability_score REAL  -- 0–100% averages from fit_analyst
prep_folder_path TEXT            -- absolute path to the companies/ subfolder
synthetic INTEGER                -- 1 for speculative cold-outreach rows
speculative_briefing_folder TEXT -- reused briefing for synthetic rows
dupe_of TEXT
stage_updated / created_at / updated_at TEXT
```

**`audit_log`** — append-only record of every field change
```
id INTEGER PRIMARY KEY AUTOINCREMENT
job_id TEXT
field_changed TEXT
old_value TEXT
new_value TEXT
changed_at TEXT DEFAULT (datetime('now'))
changed_by TEXT DEFAULT 'system' -- 'user' | 'outreach_button' | 'gmail_rejection_detector' | ...
```

**`cost_log`** — one row per LLM call; the authoritative spend ledger
```
id INTEGER PRIMARY KEY AUTOINCREMENT
job_id TEXT                      -- nullable (some calls aren't job-scoped)
operation TEXT                   -- role name, e.g. job_scorer / briefing_writer
model TEXT
latency_ms INTEGER
success INTEGER
error_message TEXT
input_tokens / output_tokens INTEGER
cost_usd REAL                    -- from OpenRouter's response.usage.cost — no heuristic
logged_at TEXT DEFAULT (datetime('now'))
```

**`feedback_log`** — rejection history that feeds scorer tuning
```
id INTEGER PRIMARY KEY AUTOINCREMENT
job_id TEXT
title / company TEXT
relevance_score INTEGER
reject_reason TEXT
jd_excerpt TEXT
created_at TEXT DEFAULT (datetime('now'))
```

> Only *user* rejections write here; company rejections (`not_selected`) and
> waitlist/fallback transitions deliberately do not, so they never contaminate
> the scorer's feedback loop.

### Supporting tables

| Table | Purpose |
|---|---|
| `notifications` | In-app + ntfy notification log — kind, priority, delivery status, read state |
| `background_tasks` | Tracks detached generators (`prep`, `prep_phase_b`, `interview_prep`, `speculative_research`, `podcast`) — pid, status, timing |
| `speculative_requests` | Cold-outreach research requests and their async lifecycle (`researching → ready_for_review → approved`) |
| `rejection_suggestions` | Gmail-detected rejection emails awaiting operator confirmation |
| `notes_history` | Audit trail of edits to a job's `user_notes` |
| `view_prefs` | Per-board-tab persisted filter / column / sort state |
| `duplicate_groups` | Maps duplicate job ids to their canonical fingerprint |
| `onboarding_sessions` | First-run chat-interview state, captured profile blocks, per-user API keys, cumulative cost |
| `config_changes` | Tuning-loop ledger of scorer-config edits |
| `recall_audit` | Tuning-loop re-scoring audit — original vs. audited score per job |

---

## Key Design Decisions

| Decision | Why |
|---|---|
| SQLite as canonical store | The DB is ACID, queryable, and zero-config. No external dependencies for the core data model. |
| Direct profile injection (not RAG) | RAG chunking drops contact info, employer names, and dates. Profile is short enough to inject raw. |
| Two-stage prefilter before LLM | Hard rejects (wrong domain, title-deterministic) don't need LLM calls. Saves ~$0.10/day and speeds triage. |
| JSON output validation | `jsonschema` validates every LLM scoring response. Malformed output → manual_review, not a crash. |
| Web materials viewer (not Drive) | Prep folders are served locally via uvicorn/FastAPI — no cloud sync dependency. Markdown rendered inline; `.docx` offered as download. |
| Web POST handlers are the sole write surface | Every handler in `findajob.web.routes.board_actions` calls straight into `findajob.actions` and responds in the same request — no poll cycle, no mirror table, single source of truth in SQLite. |
| `abbrev_title()` in folder names | Same-day preps for the same company would overwrite each other without title disambiguation. HHMMSS suffix prevents same-title same-day overwrites. |

### Why these choices hang together

These decisions aren't independent — they reinforce one principle: **keep the
non-deterministic parts small, fenced, and observable.**

SQLite as the canonical store is what makes the single-container deployment
possible at all. With no external database to provision, "the system" is one
image and one volume, and every other component — the web write-surface, the
audit log, the cost ledger — is a table away rather than a network hop away.
That proximity is also why the write-surface can answer in the same request
instead of reconciling a mirror table later.

The fences around the LLM follow from the same instinct. A two-stage prefilter
in code means the model never sees the easy rejects, which saves money and
keeps the nightly run fast; JSON-schema validation means a malformed response
degrades to `manual_review` instead of crashing a several-hundred-job batch;
and direct context injection rather than RAG means a stage's input is something
a maintainer can read in full, not a similarity-ranked guess. Each fence trades
a little flexibility for a lot of diagnosability.

Finally, routing every state change through one web handler and recording it in
an append-only audit log means the question "why is this job in this state?"
always has an answer on disk. Combined with the local materials viewer — which
removes the last external dependency, cloud file sync — the whole system stays
inspectable end to end by one person reading a SQLite file and a folder tree.
For a project meant to be self-hosted and forked, that inspectability *is* a
feature.
