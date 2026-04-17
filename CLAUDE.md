# findajob — CLAUDE.md

Read by Claude Code at the start of every session. Authoritative context for this codebase.
Personal identifiers (name, targets, API topic, form URLs) live in `CLAUDE.local.md` (gitignored).

---

## Self-Governance — Check Before Every Command

Before writing any command, path, binary call, or file location:

- [ ] Python: use `sys.executable` in scripts; check `config/paths.env` for the platform path
- [ ] aichat-ng: get path from `AICHAT` in `findajob.paths` — never hardcode, never call bare `aichat`
- [ ] pandoc: get path from `PANDOC` in `findajob.paths`
- [ ] aichat-ng config dir: `~/.config/aichat_ng/`
- [ ] Roles dir: `<repo>/config/roles/` — `.md` files only, never `.yaml`
- [ ] Master resume: `candidate_context/master_resume.md` — never `config/master_resume.md` or `rag_sources/master_resume.md`
- [ ] Anthropic client in aichat-ng config: `type: claude` — never `type: anthropic`; prefix `claude:` not `anthropic:`
- [ ] RAG never passed to scorer, cover letter writer, or outreach drafter
- [ ] All binary paths come from `findajob.paths` (`src/findajob/paths.py`) — never hardcode platform paths in scripts

**If uncertain about any value: say so. Do not guess.**

---

## PII and Domain-Neutrality — HARD RULES

This repo is intended to eventually be public and useful for job seekers in any field.
**Never commit personal identifiers or field-specific hardcoded content to tracked files.**

### Never commit to tracked files
- [ ] Real names, email addresses, phone numbers, physical addresses, LinkedIn handles
- [ ] Real employer names from the user's career history
- [ ] Real certification names, project names, or internal program names from the user's history
- [ ] Specific city/region ties to the user (e.g., "based in LA")
- [ ] The user's ntfy topic, Google Form URL, or other personal service handles
- [ ] Git email addresses or usernames that contain the user's real name (handled by git config, not code)

If personal content must exist in the pipeline (e.g., name enforcement in a role prompt),
move it to a **gitignored** file such as `candidate_context/profile.md`, `CLAUDE.local.md`, or
`config/` (credentials), and have the tracked file reference it instead.

### Never hardcode field-specific content in tracked files
- [ ] Company lists (Meta, Google, OpenAI, etc.) — belong in `config/target_companies.md` or `config/tier1.txt` (gitignored)
- [ ] Job title patterns specific to one field (software engineer, data center operations, nurse, teacher) — belong in `config/prefilter_rules.yaml` or similar (gitignored)
- [ ] Industry vocabulary in role prompts ("NPI", "IC6", "Tier 1 company") — rewrite to reference the candidate profile
- [ ] Hard-reject categories enumerated in `config/roles/job_scorer.md` — should reference profile categories, not enumerate tech/healthcare/finance/etc. inline
- [ ] Example files (`*.example`) should show **multiple fields** or use abstract placeholders, not just tech examples

### Pre-commit hook
A local pre-commit hook at `.git/hooks/pre-commit` blocks commits containing the user's
personal identifiers. The hook is **not tracked** — each clone must install its own. See
`docs/setup/configure.md` for setup. When adding new personal identifiers (new ntfy topic,
new form URL), extend the hook's `PATTERNS` array.

### Self-check before any commit

Before staging any change to a tracked file, ask:
1. Does this introduce any personal identifier (name, employer, cert, city, email, URL)?
2. Does this hardcode any company name, job title category, or industry vocabulary?
3. Would this change make the pipeline harder to use for someone in a different field (social work, education, healthcare, finance, skilled trades)?

If YES to any: put the content in a gitignored config file and reference it from the tracked
file. If you're refactoring an old hardcoded section, add a note to `docs/GENERALIZATION.md`.

### See also
- `docs/GENERALIZATION.md` — tracks every remaining piece of domain-locked content and the plan to neutralize it
- `docs/setup/configure.md` — how to configure the pre-commit hook and set up personal config files

---

## Pipeline Context Table

| Item | Value |
|------|-------|
| Default model | `gemini:gemini-3-flash-preview` |
| Embedding model | `gemini-embed:gemini-embedding-001` — dedicated named client, never touched by `--sync-models` |
| `job_scorer` | `openrouter:deepseek/deepseek-v3.2` — profile.md injected directly; `--rag` NEVER used |
| `resume_tailor` / `cover_letter_writer` | `claude:claude-opus-4-6:thinking`, `max_tokens: 4096` |
| `company_researcher` | `perplexity:sonar-reasoning-pro` |
| `briefing_writer` | `claude:claude-sonnet-4-6:thinking` |
| `outreach_drafter` | `claude:claude-sonnet-4-6` — profile injected directly |
| `fit_analyst` | `perplexity:sonar-reasoning-pro` — appended to company briefing |
| `resume_change_reviewer` / `network_analyst` | `gemini:gemini-3-flash-preview` |
| Job ingestion | jobs-api14 (RapidAPI) — LinkedIn (`datePosted: 'day'`) + Indeed; Gmail OAuth2 |
| pip | `pip3 install --break-system-packages` (no venv) |
| Path resolution | `src/findajob/paths.py` — reads `config/paths.env`; BASE derived from `__file__` |
| Roles dir | `config/roles/` |
| Master resume | `candidate_context/master_resume.md` |
| Profile | `candidate_context/profile.md` |
| DB | `data/pipeline.db` |
| Pre-filter | `src/findajob/scorer_prefilter.py` — Stage 1 regex hard reject, Stage 2 no-JD default |
| RAG index | `job_search_rag` — never passed to scorer/CL/outreach |
| Scheduler | systemd user services (see docs/setup/install-linux.md) |
| ntfy topic | in `data/.env` as `NTFY_TOPIC`; also in `CLAUDE.local.md` |
| Google Form | URL and response sheet ID in `CLAUDE.local.md` and `config/form_responses_sheet_id.txt` |

---

## Container Context (when running from the findajob Docker image)

When the pipeline runs inside the `ghcr.io/brockamer/findajob` image, paths shift:

| Thing | Native install | Container |
|---|---|---|
| `BASE` (from `findajob.paths`) | Repo clone path | `/app` (set via `JSP_BASE=/app` in compose) |
| `data/pipeline.db` | `<repo>/data/pipeline.db` | `/app/data/pipeline.db` (bind-mounted from `./state/data/`) |
| `config/roles/` | `<repo>/config/roles/` | `/app/config/roles/` (baked into image — NOT from bind mount) |
| Personal config (`config/*.yaml|.txt|.json`) | `<repo>/config/` | `/app/config/` (bind-mounted from `./state/config/`) |
| `candidate_context/` | `<repo>/candidate_context/` | `/app/candidate_context/` (bind-mount) |
| `companies/` | `<repo>/companies/` | `/app/companies/` (bind-mount) |
| `aichat-ng` | `/usr/local/bin/aichat-ng` | `/usr/local/bin/aichat-ng` (blob42/aichat-ng prebuilt) |
| aichat-ng config dir | `~/.config/aichat_ng/` | `/root/.config/aichat_ng/` (bind-mount from `./state/aichat_ng/`) |
| Scheduler | systemd user services | supercronic inside the container |

**When authoring new scripts or tests:**
- Always use `findajob.paths.BASE` — never hardcode `/home/...` or `/app/`.
- Binary subprocess calls go through `AICHAT`/`PANDOC`/`RCLONE` from `findajob.paths`.
- Tests must not depend on absolute paths — use tmpdirs or `BASE`-relative paths.

---

## Key File Locations

```
# ── Package (pip install -e .) ──────────────────────────────────────────────
<repo>/src/findajob/paths.py                # central path resolver — from findajob.paths import BASE, AICHAT, PANDOC, RCLONE
<repo>/src/findajob/utils.py                # shared utilities: log_event(), write_audit(), load_env()
<repo>/src/findajob/cleaning.py             # normalize, fingerprint, clean_title, clean_company
<repo>/src/findajob/fetchers.py             # Greenhouse, RapidAPI, Gmail job fetching
<repo>/src/findajob/scoring.py              # score_job(), _build_feedback_block()
<repo>/src/findajob/scorer_prefilter.py     # deterministic pre-filter (Stage 1 + 2)

# ── Entry point scripts (called by systemd / CLI) ──────────────────────────
<repo>/scripts/triage.py                    # daily ingest → score → DB
<repo>/scripts/poll_flags.py                # reads Dashboard + Applied + Review + Waitlist tabs (STATUS, REJECT_REASON, fingerprint)
<repo>/scripts/sync_sheet.py                # SQLite → Sheet1 + Dashboard + Applied + Review + Waitlist + Rejected Applications tabs
<repo>/scripts/setup_sheets.py             # one-time sheet formatting (idempotent)
<repo>/scripts/prep_application.py          # on-demand LLM material generation
<repo>/scripts/find_contacts.py             # LinkedIn contact matching + outreach drafts
<repo>/scripts/ingest_form.py               # Google Form → DB ingestion
<repo>/scripts/notify.py                    # ntfy push notifications (8 subcommands incl. send-raw, scoreboard)
<repo>/scripts/rename_folders.py            # rename company folders to new format (idempotent)

# ── Candidate content (all gitignored — fill these in after cloning) ────────
<repo>/candidate_context/profile.md         # candidate profile — injected into scoring, resume, CL, outreach
<repo>/candidate_context/master_resume.md   # master resume — injected into prep; also indexed for REPL RAG
<repo>/candidate_context/voice_samples/     # writing samples for CL voice calibration (REPL RAG only)

# ── Config (pipeline operation — mostly gitignored) ──────────────────────────
<repo>/config/paths.env                     # binary path overrides (gitignored; see paths.env.example)
<repo>/config/roles/                        # role .md files (8 roles)
<repo>/config/scoring_schema.json           # JSON schema for LLM scorer output validation
<repo>/config/jsearch_queries.txt           # LinkedIn/Indeed search queries (gitignored)
<repo>/config/feed_urls.txt                 # Greenhouse company slugs (gitignored)
<repo>/config/gmail_oauth_client.json       # Gmail OAuth2 credentials (gitignored)
<repo>/config/gmail_token.json              # Gmail token cache (gitignored)
<repo>/data/.env                            # API keys (chmod 600; gitignored)
<repo>/data/pipeline.db                     # SQLite — source of truth
<repo>/data/connections.csv                 # LinkedIn connections export (gitignored)

# ── Output & logs ───────────────────────────────────────────────────────────
<repo>/companies/                           # prep output folders ({Company}_{AbbrevTitle}_{date}_{time})
<repo>/companies/_applied/                   # applied job folders
<repo>/companies/_waitlisted/                # waitlisted job folders (deferred, not rejected)
<repo>/companies/_rejected/                  # rejected job folders (with marker files)
<repo>/logs/pipeline.jsonl                  # structured event log

# ── Quality ─────────────────────────────────────────────────────────────────
<repo>/pyproject.toml                       # deps, pytest, ruff, mypy config
<repo>/tests/                               # 430 unit tests (pytest)
<repo>/.github/workflows/ci.yml            # CI: ruff + mypy + pytest on every push
```

---

## Critical Architecture Rules

### Path Resolution
All binary paths (AICHAT, PANDOC, RCLONE) come from `findajob.paths` (`src/findajob/paths.py`), which reads `config/paths.env`.
Never hardcode platform paths in scripts. `BASE` is derived from `__file__` — the repo can live anywhere.
For subprocess calls to other pipeline scripts, always use `sys.executable`, not a hardcoded Python path.
Library code lives in `src/findajob/` (installed via `pip install -e .`). Entry point scripts in `scripts/` import via `from findajob.* import ...`. No `sys.path.insert` hacks.

### RAG Policy
RAG (`--rag job_search_rag`) is NEVER passed to `job_scorer`, `cover_letter_writer`,
`outreach_drafter`, or any role needing candidate-specific context. RAG chunking drops
contact info, employer names, and dates. All candidate context injected directly via
`candidate_context/profile.md` and `candidate_context/master_resume.md` string interpolation.
RAG indexes `candidate_context/` but is used only in REPL mode.

### Hard Rejects are Code
`scorer_prefilter.py` handles hard rejects deterministically before any LLM call.
Stage 1: title regex → score 1, no LLM. Stage 2: in-domain + no JD → score 5/6, no LLM.
Never rely on LLM prompt instructions alone for boolean classification tasks.

### Abbreviation Clarifications
Any internally-branded teams, programs, or org names with ambiguous abbreviations must be
spelled out explicitly in role prompts and CLAUDE.local.md. LLMs will misinterpret
abbreviations if context is not given. See CLAUDE.local.md for this installation's specifics.

### Output Folder Format
`{Company}_{AbbrevTitle}_{YYYY-MM-DD}_{HHMMSS}` — title abbreviated to first 3 words, underscored.
The HHMMSS suffix is required to prevent same-day overwrites.
`abbrev_title()` is defined in `prep_application.py` and `rename_folders.py`.

### JD at Prep Time
`prep_application.py` reads JD from the database. Never re-curls the URL at prep time.

### company_match() Blank String Guard
`connections.csv` may have blank-company rows. `'' in 'anything'` is True in Python.
Every `company_match()` function must guard: `if not s or not c: return False`

### Title/Company Cleaning
API title and company fields contain appended metadata (location, salary, recency flags).
`clean_title()` and `clean_company()` must be applied at every ingest path before storing.

### LinkedIn JD Fetch
Direct curl to LinkedIn always returns auth wall. Always use RapidAPI `/v2/linkedin/get?id=`.
This applies to both `linkedin_jobsapi` and `gmail_linkedin` sources.

### LinkedIn Query Format
`jsearch_queries.txt`: 3-4 word natural phrases only. Keyword-heavy strings (5+ words)
return zero LinkedIn results. Validate each query manually before committing.

### Google Sheet Architecture

**Sheet1** — filtered archive (A–N), archival filter:
Jobs appear if: `score>=5` OR `stage in lifecycle stages` OR `age < 14 days` OR `target company`.
Low-score old jobs from non-target companies stay in DB only.
`fingerprint(hidden) | APPLY_FLAG(checkbox) | score | title | company | location | remote | stage | contacts | comp | notes | date | source | url`

**Dashboard** — pre-application queue (A–N), filter: `(score>=7 AND stage IN (scored,manual_review))` OR `stage IN (prep_in_progress, materials_drafted)`. Once user marks STATUS=Applied the poller sets stage=applied and the row moves off Dashboard to the Applied tab:
`STATUS(dropdown) | REJECT_REASON(dropdown) | fingerprint(hidden) | fit_score | probability_score | relevance_score | title(hyperlink) | company | location | remote | contacts | comp | notes | date`

**Applied** — post-application queue (A–N), filter: `stage IN (applied, interview, offer)`. This is the UI for managing jobs you've submitted and are waiting to hear back on:
`STATUS(dropdown) | REJECT_REASON(dropdown) | fingerprint(hidden) | title(hyperlink) | company(Drive hyperlink) | applied_date | days_since_applied(formula) | stage | user_notes | known_contacts | location | remote | comp | ai_notes`
- `STATUS` options (col A): `Interviewing` / `Offer` / `Ghosted` / `Not Selected` / `Withdrew`
- `days_since_applied` = live `=IF(F2="","",TODAY()-F2)` formula — no re-sync needed
- Row color by priority: Offer→gold, Interviewing→purple, Ghosted OR >=21d→gray, 14–20d→red, 7–13d→yellow, 0–6d→green
- `user_notes` (col I) is free-text and syncs back to `jobs.user_notes` via `sync_sheet.py` on each run
- `applied_date` sourced from `audit_log` where `new_value='applied'` (first transition)
- `Ghosted` is visual-only: stage remains `applied`, row stays on tab — user flips to Not Selected when they give up

**Review** — manual review triage (A–H), filter: `stage=manual_review`:
`STATUS(dropdown:Promote) | REJECT_REASON(dropdown) | fingerprint(hidden) | title(hyperlink) | company | score_flag_reason | source | date`
- `Promote` = `poll_flags.py` sets `score=7, stage=scored` → job appears on Dashboard
- `REJECT_REASON` = same as Dashboard → `poll_flags.py` rejects the job

**Waitlist** — deferred jobs (A–K), filter: `stage=waitlisted`:
`STATUS(dropdown:Reactivate) | REJECT_REASON(dropdown) | fingerprint(hidden) | title(hyperlink) | company | relevance_score | location | remote | ai_notes | date | blocking_app`
- `Reactivate` = `poll_flags.py` restores to `scored` (no folder) or `materials_drafted` (has folder), moves folder back from `_waitlisted/`
- `REJECT_REASON` = same as Dashboard → `poll_flags.py` rejects the job from waitlist
- `blocking_app` = computed at sync time: title + stage of active application at same company

**STATUS dropdown options** differ by tab:

- **Dashboard col A** (pre-application): `Flag for Prep` → `Prep in Progress` *(system)* → `Ready to Apply` *(system)* → `Regenerate` → `Waitlist` → `Applied`
- **Applied col A** (post-application): `Interviewing` → `Offer` → `Ghosted` → `Not Selected` → `Withdrew`

Actions:
- `Flag for Prep` (Dashboard) = user action → triggers `prep_application.py` via `poll_flags.py`
- `Ready to Apply` (Dashboard, system) = set when `stage=materials_drafted` (prep done, folder exists)
- `Regenerate` (Dashboard) = user action → deletes prep folder, re-runs prep
- `Waitlist` (Dashboard) = user action → `poll_flags.py` sets `stage=waitlisted`, moves folder to `_waitlisted/`
- `Applied` (Dashboard) = user action → `poll_flags.py` sets `stage=applied`, moves folder to `_applied/`, row moves off Dashboard to Applied tab on next sync
- `Interviewing/Offer/Withdrew` (Applied) = user action → `poll_flags.py` updates DB stage
- `Ghosted` (Applied) = user-set flag, visual-only — no DB change; preserved across syncs; triggers gray row color
- `Not Selected` (Applied) = user action (company rejected) → `poll_flags.py` sets `stage=not_selected`, folder stays in `_applied/`, no `feedback_log` write

**REJECT_REASON dropdown** (col B): 11 options (includes "Low Fit Score"). Behavior depends on STATUS:
- If STATUS = `Not Selected`: company rejection → `stage=not_selected`, NO `feedback_log`, folder stays in `_applied/` with `NOT_SELECTED_` marker file
- Otherwise: user rejection → `stage=rejected`, writes `feedback_log`, moves folder to `_rejected/`, syncs to Drive

**poll_flags.py** reads `Dashboard!A2:C10000`, `Applied!A2:C10000`, `Review!A2:C10000`, and `Waitlist!A2:C10000`. "Not Selected" is checked before generic rejection to prevent routing errors. `Ghosted` STATUS on the Applied tab is a no-op in DB (visual only) but is preserved across syncs via pending_statuses.

**Stage `waitlisted`:** Set by `poll_flags.py` when user selects "Waitlist" on Dashboard. Folder moves to `companies/_waitlisted/`. Job disappears from Dashboard, appears on Waitlist tab. Not a rejection — does not write to feedback_log or contaminate scorer feedback loop. When active application at same company is rejected/withdrawn, ntfy notification surfaces waitlisted jobs.

**Stage `not_selected`:** Set by `poll_flags.py` when user selects "Not Selected" on the Applied tab. Only valid for post-application stages (`applied`, `interview`, `offer`). Folder stays in `companies/_applied/` with a `NOT_SELECTED_{reason}_{date}.txt` marker file. Does NOT write to `feedback_log` — company rejections must not contaminate the scorer's feedback loop. `notify_waitlist_resurface()` still fires (company rejection is a trigger to surface waitlisted jobs at that company). Appears on the Rejected Applications tab alongside user rejections.

**Stage `prep_in_progress`:** Set by `poll_flags.py` immediately before launching `prep_application.py` as a subprocess. Prevents duplicate prep runs across poll cycles. Cleared to `materials_drafted` on success. Health check warns if any job is stuck in this stage >1h.

**Health checks** (`notify.py health-check`): warns if Sheet1 > 1000 rows, manual_review backlog > 100, or any target-company job scored 3–6 in last 7 days (potential mis-scores).

---

## Project Board — Single Source of Truth

All work is tracked on the GitHub Project board at https://github.com/users/brockamer/projects/1. **Not on the board = not on the roadmap.** No markdown tracking files, no TODO lists.

Canonical conventions live in [`docs/project-board.md`](docs/project-board.md). Read it before any work that creates, moves, or reprioritizes issues. That doc covers columns, Priority field, Work Stream field, labels, triage checklist, and `gh project` CLI IDs.

Core rules (enforced — see the doc for detail):
- Creating an issue is **two steps**: `gh issue create` then `gh project item-add 1 --owner brockamer --url <url>`. New issues do not auto-add.
- Every open issue on the board must have **Priority** (High/Medium/Low) and **Work Stream** (Job Search / Generalization / Infrastructure) set.
- `priority: high/med/low` labels are legacy — the **Priority field** is canonical. Reconcile mismatches.
- In Progress should hold 1–3 items max. If more, focus is scattered.
- Status transitions: Backlog → Up Next → In Progress → Done. Closing an issue auto-moves to Done; verify after closing.
- Re-sync board state before changing it — other sessions may have updated it.

**When board usage evolves** (new column, new label, new workflow, new convention): update `docs/project-board.md` in the same change. The doc describes how the board actually works, not how it used to work. Behavior drifting ahead of docs is the main failure mode.

---

## Plan Conventions

Implementation plans live in `docs/superpowers/plans/`. Conventions are documented in [`docs/plan-conventions.md`](docs/plan-conventions.md).

**Hard requirements for every plan:**
- Numbered tasks with files, steps, verification commands, commit messages
- A **Documentation Impact** section enumerating every doc surface that needs to change (README, docs/setup/*, CLAUDE.md, CHANGELOG.md, spec doc, docstrings). If none, say "None" — never omit the section
- A whole-feature verification gate distinct from per-task checks
- A self-review checklist mapping every spec section to its implementing task(s)

A plan without Documentation Impact is incomplete — push back rather than execute it.

---

## Working Style

- Terse. User reports completion of each step before asking what's next.
- Read file contents before proposing changes. Never assume files match prior discussion.
- Diagnose root cause before fixing. No shotgun solutions.
- Copy-pasteable commands only. No placeholders. Use paths from `scripts/paths.py`. Platform-aware.
- Never confuse `aichat` with `aichat-ng`. Different binaries.
- Goal: working features first, polish later.
- Preserve the scheduler-driven daily run in all changes.

@CLAUDE.local.md
