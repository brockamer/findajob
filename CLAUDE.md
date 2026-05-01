# findajob — CLAUDE.md

Read by Claude Code at the start of every session. Authoritative context for this codebase.
Personal identifiers (name, targets, API topic, form URLs) live in `CLAUDE.local.md` (gitignored).

---

## Self-Governance — Check Before Every Command

Before writing any command, path, binary call, or file location:

- [ ] All binary paths come from `findajob.paths` — `AICHAT`, `PANDOC`, `BASE`. Never hardcode, never call bare `aichat`.
- [ ] For subprocess calls to other pipeline scripts, use `sys.executable` (never a hardcoded Python path).
- [ ] Anthropic client in aichat-ng config: `type: claude` — never `type: anthropic`; prefix `claude:` not `anthropic:`.
- [ ] RAG never passed to scorer, cover letter writer, or outreach drafter.

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
| Default model | `openrouter:google/gemini-3-flash-preview` |
| Embedding model | `gemini-embed:gemini-embedding-001` — dedicated named client, never touched by `--sync-models` |
| `job_scorer` | `openrouter:deepseek/deepseek-v3.2` — profile.md injected directly; `--rag` NEVER used |
| `resume_tailor` / `cover_letter_writer` | `openrouter:anthropic/claude-opus-4.7`, `max_tokens: 4096` |
| `company_discoverer` | `openrouter:perplexity/sonar-reasoning-pro` — runs weekly Sun 02:00; emits `candidate_context/discovered_companies.md` + `.json`; field-agnostic, augments static `## Target Companies`. Surfaced to operator via Dashboard widget (banner showing count + last-run date) and a success ntfy on each run (#288). |
| `company_researcher` | `openrouter:perplexity/sonar-reasoning-pro` |
| `briefing_writer` | `openrouter:anthropic/claude-opus-4.7` — cascades into `resume_tailor` + `cover_letter_writer`, both Opus 4.7 |
| `outreach_drafter` | `openrouter:anthropic/claude-opus-4.7` — profile + voice samples injected directly |
| `fit_analyst` | `openrouter:perplexity/sonar-reasoning-pro` — appended to company briefing |
| `resume_change_reviewer` / `network_analyst` | `openrouter:google/gemini-3-flash-preview` |
| `recruiter_critic` | `openrouter:anthropic/claude-opus-4.7`, `max_tokens: 1024` — sees company, title, JD, tailored resume, cover; NOT profile/briefing/fit |
| `interview_prep` | `openrouter:anthropic/claude-opus-4.7`, `max_tokens: 4096` — fires on `applied → interview` transition; expands briefing's interview-questions + stories sections. |
| `candidate_led_briefing` | `openrouter:perplexity/sonar-deep-research` — async (1–5 min); drives the speculative briefing pass via `scripts/run_speculative_research.py`. |
| `speculative_roles_synth` | `openrouter:anthropic/claude-sonnet-4-6`, `max_tokens: 4096` — synthesizes 1–5 candidate-tailored role cards from the briefing. |
| Job ingestion | jobs-api14 (RapidAPI) — LinkedIn only (`datePosted: 'day'`, widened to `'month'` during the first 30d after onboarding via `_date_posted_for_install()`); direct Greenhouse/Ashby/Lever JSON; Gmail IMAP/app-password (LinkedIn + Indeed alerts) — configurable per-stack at /config/gmail/ |
| Package manager | `uv sync` for dev deps; `uv run` prefix for pytest/ruff/mypy/uvicorn |
| Path resolution | `src/findajob/paths.py` — reads `config/paths.env`; BASE derived from `__file__` |
| Roles dir | `config/roles/` |
| Master resume | `candidate_context/master_resume.md` |
| Profile | `candidate_context/profile.md` |
| DB | `data/pipeline.db` |
| Pre-filter | `src/findajob/scorer_prefilter.py` — Stage 1 regex hard reject, Stage 2 no-JD default |
| Board writes | `src/findajob/web/routes/board_actions.py` — every STATUS / REJECT_REASON transition is a POST handler calling `findajob.actions`. Sheet is read-only. |
| Watchdog | `scripts/watchdog.py` every 10 min — resets jobs stuck in `prep_in_progress` > 60 min. No Sheet reads. |
| RAG index | `job_search_rag` — never passed to scorer/CL/outreach |
| Scheduler | supercronic in-container; schedules declared in `ops/scheduled-jobs.yaml`, rendered to `/app/crontab` by `scripts/render_crontab.py` at entrypoint. Per-job env overrides: `FINDAJOB_<JOB>_SCHEDULE` / `FINDAJOB_<JOB>_ENABLED` (#344). |
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
| `discovered_companies.md/.json` | `<repo>/candidate_context/discovered_companies.{md,json}` (gitignored, generated) | `/app/candidate_context/discovered_companies.{md,json}` (generated into bind-mount) |
| `companies/` | `<repo>/companies/` | `/app/companies/` (bind-mount) |
| Cross-stack mount (operator-mode only) | n/a | `/opt/stacks/:/opt/stacks:ro` (added to operator's `compose.yaml` only) |
| `FINDAJOB_OPERATOR_MODE` env | n/a | `1` on operator's stack only; unset on testers' (#333) |
| `FINDAJOB_OPERATOR_HANDLE` env (optional) | n/a | Operator's stack handle (e.g. matches the trailing dir component of `/opt/stacks/findajob-{handle}`); when set, that row floats to the top of the `/admin/stacks/` table. Unset = pure alphabetical (#333). |
| Onboarding sentinel | `<repo>/data/.onboarding-complete` | `/app/data/.onboarding-complete` (bind-mount from `./state/data/`) |
| Onboarding backups | `<repo>/.backups/{UTC-stamp}/` | `/app/.backups/` (bind-mount from `./state/.backups/`) |
| `aichat-ng` | `/usr/local/bin/aichat-ng` | `/usr/local/bin/aichat-ng` (blob42/aichat-ng prebuilt) |
| aichat-ng config dir | `~/.config/aichat_ng/` | `/app/.config/aichat_ng/` (bind-mount from `./state/aichat_ng/`) |
| Scheduler | systemd user services | supercronic inside the container |
| Web viewer | `src/findajob/web/` (package) | uvicorn co-process on container port 8090 (mapped to `FINDAJOB_MATERIALS_PORT`) |

**When authoring new scripts or tests:**
- Always use `findajob.paths.BASE` — never hardcode `/home/...` or `/app/`.
- Binary subprocess calls go through `AICHAT`/`PANDOC` from `findajob.paths`.
- Tests must not depend on absolute paths — use tmpdirs or `BASE`-relative paths.

---

## Key File Locations

```
# ── Package (pip install -e .) ──────────────────────────────────────────────
<repo>/src/findajob/paths.py                # central path resolver — from findajob.paths import BASE, AICHAT, PANDOC
<repo>/src/findajob/utils.py                # shared utilities: log_event(), write_audit(), load_env()
<repo>/src/findajob/cleaning.py             # normalize, fingerprint, clean_title, clean_company
<repo>/src/findajob/ingest.py               # ingest_manual_job() — shared entry point for the /ingest/ web form
<repo>/src/findajob/fetchers.py             # Greenhouse, RapidAPI, Gmail job fetching
<repo>/src/findajob/scoring.py              # score_job(), _build_feedback_block()
<repo>/src/findajob/scorer_prefilter.py     # deterministic pre-filter (Stage 1 + 2)
<repo>/src/findajob/web/app.py               # FastAPI app factory (create_app)
<repo>/src/findajob/web/routes/ingest.py     # GET /ingest/ form + POST /ingest/manual handler
<repo>/src/findajob/web/routes/config.py     # GET /config/, GET/POST /config/files/{path} — in-browser config editor
<repo>/src/findajob/web/routes/gmail_config.py # GET/POST /config/gmail/ — IMAP/app-password integration setup (#330)
<repo>/src/findajob/web/routes/tools.py      # GET /tools/ — stub linking to /config/
<repo>/src/findajob/web/routes/docs.py       # GET /docs/ index + GET /docs/{slug} — user docs viewer
<repo>/src/findajob/web/markdown.py          # render_markdown() — shared MD→HTML helper for materials + docs viewers
<repo>/src/findajob/web/config_files.py      # allowlist + resolve_editable() for /config/ editor
<repo>/src/findajob/web/onboarding_guard.py # NUX guard dependency — 307s /board,/materials,/stats to /onboarding when sentinel missing
<repo>/src/findajob/web/routes/onboarding.py # GET /onboarding/, GET /onboarding/prompt, POST /onboarding/inject
<repo>/src/findajob/onboarding/parser.py    # parse interview emission into files to inject
<repo>/src/findajob/onboarding/injector.py  # atomic write + backup + Tier-1 derivation + sentinel
<repo>/src/findajob/discoverer/                # company discovery library — prompt, parser, runner, writer
<repo>/src/findajob/web/routes/admin_stacks.py # GET /admin/stacks/ — operator-only multi-tenant stack health (#333; loaded iff FINDAJOB_OPERATOR_MODE=1)
<repo>/src/findajob/web/routes/healthz.py    # GET /healthz
<repo>/src/findajob/web/routes/materials.py  # GET /materials/ — candidate materials viewer (uses folder_resolver)
<repo>/src/findajob/web/folder_resolver.py   # stage→filesystem resolver with path-traversal guards
<repo>/src/findajob/web/templates/           # Jinja2 templates — base.html + one subdir per route group + shared _*.html partials

# ── Entry point scripts (called by systemd / CLI) ──────────────────────────
<repo>/scripts/triage.py                    # daily ingest → score → DB
<repo>/scripts/watchdog.py                  # resets stuck prep_in_progress jobs > 60 min (every 10 min cron)
<repo>/scripts/sync_sheet.py                # SQLite → Dashboard + Applied + Review + Waitlist + Rejected Applications tabs (one-way, no Sheet reads)
<repo>/scripts/setup_sheets.py             # one-time sheet formatting (idempotent)
<repo>/scripts/prep_application.py          # on-demand LLM material generation
<repo>/scripts/find_contacts.py             # LinkedIn contact matching + outreach drafts
<repo>/scripts/ingest_form.py               # Google Form → DB ingestion (retired; kept for manual drains)
<repo>/scripts/notify.py                    # ntfy push notifications (8 subcommands incl. send-raw, scoreboard)
<repo>/scripts/rename_folders.py            # rename company folders to new format (idempotent)
<repo>/scripts/discover_companies.py            # weekly company discovery cron entry

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
<repo>/config/gmail.json                    # Gmail IMAP/app-password config (gitignored, chmod 600)
<repo>/config/gmail_state.json              # Gmail IMAP UID + auth-failure tracker (gitignored)
<repo>/data/.env                            # API keys (chmod 600; gitignored)
<repo>/data/pipeline.db                     # SQLite — source of truth
<repo>/data/connections.csv                 # LinkedIn connections export (gitignored)

# ── Output & logs ───────────────────────────────────────────────────────────
<repo>/companies/                           # prep output folders ({Company}_{AbbrevTitle}_{date}_{time})
<repo>/companies/_applied/                   # applied job folders
<repo>/companies/_waitlisted/                # waitlisted job folders (deferred, not rejected)
<repo>/companies/_rejected/                  # rejected job folders (with marker files)
<repo>/logs/pipeline.jsonl                  # structured event log

# ── Operations ──────────────────────────────────────────────────────────────
<repo>/docs/release-process.md              # Claude's release orchestration runbook — dogfood gate, tag cut, rollback
<repo>/docs/setup/install-docker.md         # external-user Docker install + operations guide

# ── Quality ─────────────────────────────────────────────────────────────────
<repo>/pyproject.toml                       # deps, pytest, ruff, mypy config
<repo>/tests/                               # ~900 unit tests (pytest)
<repo>/.github/workflows/ci.yml            # CI: ruff + mypy + pytest on every push
```

### Web Frontend Architecture

Lives at `src/findajob/web/`. One file per URL group in `routes/` (e.g. `routes/materials.py`, `routes/board.py`, `routes/landing.py`). Shared partials (`_nav.html`, `_job_row.html`) live in `templates/`.

Foundational decisions (from `docs/superpowers/specs/2026-04-21-web-frontend-14b-design.md`):
- Server-rendered HTML + HTMX (no SPA)
- Grouped URL IA — top-nav = `/`, `/board/`, `/materials/`, `/ingest/`, `/stats/`, `/tools/`, `/config/`, `/docs/`
- Tailwind via CDN + `static/app.css` design tokens
- URL query params for UI state (not cookies/localStorage)
- Alpine.js added only when ephemeral client state is needed

**`/config/`** — in-browser editor for editable pipeline config; allowlist in `findajob.web.config_files`. No per-user authorization inside findajob — perimeter is the boundary. Default perimeter is Wireguard; internet-exposed instances additionally require HTTP Basic Auth via `FINDAJOB_AUTH_USER` / `FINDAJOB_AUTH_PASS` (see `findajob.web.auth` and `docs/setup/internet-exposure.md`).

**`/onboarding/`** — first-run NUX + paste-back injector for the interview emitted by `config/roles/onboarding_interviewer.md`. The `findajob.web.onboarding_guard` dependency redirects `/board/*`, `/materials/*`, `/stats/*` to `/onboarding/` when `data/.onboarding-complete` is missing. Re-triggerable via `/onboarding/?mode=rerun`. The injector parses the interview blob, atomically writes ~10 canonical files under `candidate_context/`/`config/`/`data/`, backs up existing destinations to `.backups/{UTC-stamp}/`, optionally processes pasted `voice-samples.md` (markdown-strip + PII-generalization), verifies the operator's OpenRouter key with a 1-token live call, and only then writes the sentinel. See `findajob.onboarding.{parser,injector,voice_processor,openrouter_smoke}` for boundaries.

**`/docs/`** — renders `docs/usage.md`, `docs/troubleshooting.md`, `docs/setup/*` inline in the web UI. Slug allowlist in `findajob.web.routes.docs`; rendering via `findajob.web.markdown.render_markdown()` (handles `.md` cross-link rewriting + `target="_blank"` on external links).

**Operator mode** — gated by `FINDAJOB_OPERATOR_MODE=1` (operator's stack only;
never set on testers'). Adds `/admin/stacks/` route and renders the top nav in
red on every page. The route is the **only** code path that reads cross-stack
state from inside `findajob.web` — invariant: read-only, no POST handlers, all
SQLite handles open with `mode=ro` URI. See `findajob.admin.{stack_discovery,
stack_health,jsonl_tail}` and `docs/setup/install-docker.md` "Operator mode"
subsection.

### Per-column filter framework

Declarative framework at `findajob.web.filters`. Each board tab declares a `tuple[ColumnSpec, ...]` in `findajob.web.filters.registry`; framework parses URL params, builds parameterized SQL clauses, and renders header inputs via shared partials.

URL contract — flat, type-suffixed param names: `?col=sub` (TEXT), `?col_min=&col_max=` (SCORE/INTEGER), `?col=a,b,c` (ENUM), `?col_from=&col_to=` (DATE), `?sort=col&desc=1`, `?cols=a,b,c` (visibility).

Adding a new tab: declare ColumnSpec list in `registry.py`, add base WHERE + `_<tab>_query()` in `routes/board.py`, include `_filters.html` + `_table_header.html` in the template.

---

## Critical Architecture Rules

### Web is the Write Surface
Every STATUS and REJECT_REASON transition runs through a POST handler in
`findajob.web.routes.board_actions` that calls straight into
`findajob.actions`. The Google Sheet is a one-way synced view (DB → Sheet);
`sync_sheet.py` never reads from Sheets. Do not add new transition logic to
`watchdog.py` or to any Sheet-reading path — every new action is a new web
handler + a new `findajob.actions` helper.

Some transitions also spawn detached generator subprocesses:
- `POST /board/jobs/{fp}/prep` and `/regenerate` → `scripts/prep_application.py` (briefing, tailored resume, cover, recruiter critique, outreach drafts)
- `POST /board/jobs/{fp}/interview` → `scripts/interview_prep.py` (interview prep artifact). Always (re)launches on each click — re-clicking is the regenerate mechanism after a recruiter sends panel info; a sentinel file `.interview_prep_in_progress` in the prep folder guards against concurrent runs.
- `POST /ingest/speculative` and `POST /speculative/regenerate/{id}` → `scripts/run_speculative_research.py` (briefing + role-synth pipeline). Async — status page polls `/speculative/status/{id}/poll` every 5s until `status='ready_for_review'`. Full route surface in `findajob.web.routes.speculative` (POST `/ingest/speculative`, GET `/speculative/status/{id}` + `/poll`, GET `/speculative/review/{id}`, POST `/speculative/{approve,regenerate,trash}/{id}`).
- `POST /board/jobs/{fp}/apply` is synthetic-aware: reads `jobs.synthetic` and writes `audit_log.changed_by='outreach_button'` for synthetic rows (label flips to "Sent Outreach" on the dashboard); otherwise the existing `'user'` value. No separate route — single endpoint, server-derived signal.

### Path Resolution
All binary paths (AICHAT, PANDOC) come from `findajob.paths` (`src/findajob/paths.py`), which reads `config/paths.env`.
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

### Synthetic Jobs Convention (Speculative Cold-Outreach)

Some `jobs` rows are *synthetic* — produced by the speculative ingest path (`/ingest/` "Speculative" tab) for cold-outreach to companies not currently posting a matching opening. Pre-approval state in `speculative_requests`; on approve, `findajob.speculative.approver` writes the `jobs` row.

**Canonical signal:** `jobs.synthetic=1` + `source='web_speculative'` + `[SPEC] ` title prefix (literal in title for universal render coverage; render-time badge in `_job_row.html`).

**Invariants — assume code enforces these; do not duplicate logic that breaks them:**
- Synthetic rejections never feed the scorer (`handle_rejection` skips `feedback_log`; `_build_feedback_block` excludes `synthetic=1` rows).
- Synthetic rows reuse the `applied` stage; `/board/jobs/{fp}/apply` writes `audit_log.changed_by='outreach_button'` for them.
- `prep_application.py` and `find_contacts.py` prepend `<<SPECULATIVE_MODE>>` to cover-letter / outreach prompts when `jobs.synthetic=1`; both role files branch on it.
- `prep_application.py` reuses `companies/{folder}/briefing.md` (set via `jobs.speculative_briefing_folder`) and skips `company_researcher` + `briefing_writer`.
- `watchdog.fail_stuck_speculative()` fails any `speculative_requests` stuck in `researching` > 10 min.

**Folder layout:** `companies/{Company}_SPECULATIVE_{YYYY-MM-DD}_{HHMMSS}/briefing.md`; per-role prep folders use the regular convention.

Full spec: `docs/superpowers/specs/2026-04-28-speculative-ingest-131-design.md`.

### Abbreviation Clarifications
Any internally-branded teams, programs, or org names with ambiguous abbreviations must be
spelled out explicitly in role prompts and CLAUDE.local.md. LLMs will misinterpret
abbreviations if context is not given. See CLAUDE.local.md for this installation's specifics.

### Company Discovery is a Parallel Signal
`config/roles/company_discoverer.md` runs weekly via supercronic and after onboarding completion. It emits `candidate_context/discovered_companies.md` + `.json` (gitignored), read by the scorer and Greenhouse-slug derivation as INPUTS, not floors. The static `## Target Companies / Organizations` section in profile.md remains as a strategic-preference signal — orthogonal to the competency-fit signal the discoverer produces. Do not delete the static list to "consolidate"; they serve different purposes.

### Output Folder Format
`{Company}_{AbbrevTitle}_{YYYY-MM-DD}_{HHMMSS}` — title abbreviated to first 3 words, underscored.
The HHMMSS suffix is required to prevent same-day overwrites.
`abbrev_title()` is defined in `prep_application.py` and `rename_folders.py`.

Speculative briefings use a parallel convention:
`{Company}_SPECULATIVE_{YYYY-MM-DD}_{HHMMSS}/briefing.md` — see
`findajob.speculative.storage.speculative_folder_name`. The literal
`SPECULATIVE` token replaces the abbreviated title since there's no
specific posting at submission time.

### JD at Prep Time
`prep_application.py` reads JD from the database. Never re-curls the URL at prep time.

### company_match() Blank String Guard
`connections.csv` may have blank-company rows. `'' in 'anything'` is True in Python.
Every `company_match()` function must guard: `if not s or not c: return False`

### Title/Company Cleaning
API title and company fields contain appended metadata (location, salary, recency flags).
`clean_title()` and `clean_company()` must be applied at every ingest path before storing.

### Two-Tier Dedup
Ingest runs two tiers. **Tier 1** is the strict `fingerprint(title, company, location)` hash;
**Tier 2** is `loose_fingerprint(title, company)`, checked only when the incoming row OR any
existing same-(company, title) row has a coarse location (empty, country-only, or bare
"Remote"). This dedupes cross-source syndication (Greenhouse "US" vs LinkedIn "Barstow, TX")
while keeping genuinely distinct-city reqs (site managers in different cities) as separate
rows. All location comparisons route through `normalize_location()`, which strips
`(On-site)`/`(Remote)`/`(Hybrid)` suffixes and trailing country codes. Both `scripts/triage.py`
and `scripts/ingest_form.py` use the centralized helpers from `findajob.cleaning` — do not
reintroduce drifted local `_normalize`/`fingerprint` copies.

### LinkedIn JD Fetch
Direct curl to LinkedIn always returns auth wall. Always use RapidAPI `/v2/linkedin/get?id=`.
This applies to both `linkedin_jobsapi` and `gmail_linkedin` sources.

### LinkedIn Query Format
`jsearch_queries.txt`: 3-4 word natural phrases only. Keyword-heavy strings (5+ words)
return zero LinkedIn results. Validate each query manually before committing.

### Google Sheet Architecture

The Sheet is a read-only one-way mirror of DB state for mobile/glance use; `sync_sheet.py` never reads from it. All transitions go through `/board/*` POST handlers.

**Dashboard** — pre-application queue (A–N), filter: `(score>=7 AND stage IN (scored,manual_review))` OR `stage IN (prep_in_progress, materials_drafted)`. Row columns:
`STATUS(dropdown) | REJECT_REASON(dropdown) | fingerprint(hidden) | fit_score | probability_score | relevance_score | title(hyperlink) | company | location | remote | contacts | comp | notes | date`

**Applied** — post-application queue (A–N), filter: `stage IN (applied, interview, offer)`.
`STATUS(dropdown) | REJECT_REASON(dropdown) | fingerprint(hidden) | title(hyperlink) | company(viewer hyperlink) | applied_date | days_since_applied(formula) | stage | user_notes | known_contacts | location | remote | comp | ai_notes`
- `STATUS` options (col A): `Interviewing` / `Offer` / `Not Selected` / `Withdrew`
- `days_since_applied` = live `=IF(F2="","",TODAY()-F2)` formula — no re-sync needed
- Row color by priority: Offer→gold, Interviewing→purple, >=21d→gray (silent = likely ghosted), 14–20d→red, 7–13d→yellow, 0–6d→green
- `user_notes` (col I) is free-text; edited via the web `/board/applied` notes input, one-way synced to Sheet on the next `sync_sheet.py` pass
- `applied_date` sourced from `audit_log` where `new_value='applied'` (first transition)

**Review** — manual review triage (A–H), filter: `stage=manual_review`:
`STATUS(dropdown:Promote) | REJECT_REASON(dropdown) | fingerprint(hidden) | title(hyperlink) | company | score_flag_reason | source | date`

**Waitlist** — deferred jobs (A–K), filter: `stage=waitlisted`:
`STATUS(dropdown:Reactivate) | REJECT_REASON(dropdown) | fingerprint(hidden) | title(hyperlink) | company | relevance_score | location | remote | ai_notes | date | blocking_app`
- `blocking_app` = computed at sync time: title + stage of active application at same company

**Write surface — `findajob.web.routes.board_actions`:**

Every transition is a POST handler that calls straight into `findajob.actions`
(handle_rejection, handle_not_selected, handle_waitlist, handle_reactivate,
promote_to_scored, notify_waitlist_resurface, reset_prep_to_scored) and
responds in the same request. No poll cycle, no Sheet readback.

| Action | Endpoint | Where it lives |
|---|---|---|
| Flag for Prep | `POST /board/jobs/{fp}/prep` | Dashboard dropdown |
| Regenerate | `POST /board/jobs/{fp}/regenerate` | Dashboard dropdown |
| Applied | `POST /board/jobs/{fp}/apply` | Dashboard dropdown |
| Waitlist | `POST /board/jobs/{fp}/waitlist` | Dashboard dropdown |
| Reject (w/ reason) | `POST /board/jobs/{fp}/reject` | Dashboard / Review / Waitlist reject cell |
| Interviewing | `POST /board/jobs/{fp}/interview` | Applied dropdown |
| Offer | `POST /board/jobs/{fp}/offer` | Applied dropdown |
| Withdrew | `POST /board/jobs/{fp}/withdraw` | Applied dropdown |
| Not Selected (w/ reason) | `POST /board/jobs/{fp}/not-selected` | Applied dropdown + reject cell |
| Promote | `POST /board/jobs/{fp}/promote` | Review button |
| Reactivate | `POST /board/jobs/{fp}/reactivate` | Waitlist button |
| Edit user_notes | `POST /board/jobs/{fp}/notes` | Applied notes input (800ms debounce) |

**REJECT_REASON dropdown**: 11 options (includes "Low Fit Score"). Behavior depends on STATUS:
- If STATUS = `Not Selected`: company rejection → `stage=not_selected`, NO `feedback_log`, folder stays in `_applied/` with `NOT_SELECTED_` marker file
- Otherwise: user rejection → `stage=rejected`, writes `feedback_log`, moves folder to `_rejected/`

**Stage `waitlisted`:** Set by `POST /board/jobs/{fp}/waitlist`. Folder moves to `companies/_waitlisted/`. Not a rejection — does not write to feedback_log or contaminate scorer feedback loop. When an active application at the same company is rejected/withdrawn, ntfy notification surfaces waitlisted jobs.

**Stage `not_selected`:** Set by `POST /board/jobs/{fp}/not-selected`. Only valid for post-application stages (`applied`, `interview`, `offer`); 409 otherwise. Folder stays in `companies/_applied/` with a `NOT_SELECTED_{reason}_{date}.txt` marker file. Does NOT write to `feedback_log` — company rejections must not contaminate the scorer's feedback loop. `notify_waitlist_resurface()` still fires. Appears on the Rejected Applications tab alongside user rejections.

**Stage `prep_in_progress`:** Set by `POST /board/jobs/{fp}/prep` immediately before launching `prep_application.py` as a subprocess. Prevents duplicate prep runs (handler idempotency guard + 3-job concurrency cap). Cleared to `materials_drafted` on success. `scripts/watchdog.py` rolls any job stuck > 60 min back to `scored` so the operator can re-flag.

**Health checks** (`notify.py health-check`): warns if manual_review backlog > 100, a source silently stopped producing jobs, or any target-company job scored 3–6 in last 7 days (potential mis-scores).

### Gmail Integration

Gmail ingestion uses IMAP + app password, configured per-stack at `/config/gmail/`. Transparency contract spelled out in `docs/superpowers/specs/2026-04-30-330-design.md` §4 and codified as executable assertions in `tests/test_transparency_invariants.py` — failures there mean the disclosure banner is lying.

---

## Project Board — Single Source of Truth

All work is tracked on the GitHub Project board at https://github.com/users/brockamer/projects/1. **Not on the board = not on the roadmap.** No markdown tracking files, no TODO lists.

Canonical conventions live in [`docs/project-board.md`](docs/project-board.md). Read it before any work that creates, moves, or reprioritizes issues. That doc covers columns, Priority field, labels, triage checklist, and `gh project` CLI IDs.

Core rules (enforced — see the doc for detail):
- Creating an issue is **two steps**: `gh issue create` then `gh project item-add 1 --owner brockamer --url <url>`. New issues do not auto-add.
- Every open issue on the board must have **Priority** (High/Medium/Low) set.
- Speculative far-horizon ideas get the `big-idea` label and Priority: Low — keeps them on the board without cluttering the active roadmap.
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

## Release Management

Docker image releases follow [`docs/release-process.md`](docs/release-process.md). Claude owns orchestration (dogfood gate, CHANGELOG drafting, tag cut, post-tag verification, rollback); the user reviews and approves the proposed cut. The dogfood gate is a binary 48h window on `:latest` — six observable signals must all be clean before any `v*.*.*` tag is pushed. PRs containing schema/config/crontab/mount/compose-down changes get the `migration-required` label at PR-open time so that release notes surface them for external users.

---

## Commit Flow

This is a solo repo. Default to committing directly to `main`. Use feature branches + PRs only when the change needs the review/CI/release-notes scaffolding.

| Change type | Flow |
|-------------|------|
| Docs, board conventions, plan/spec files, jared skill tweaks, comment edits | Commit to `main` |
| Code touching pipeline behavior (scoring, fetchers, sheet sync, DB schema, LLM roles) | Feature branch → PR → merge |
| Anything qualifying for `migration-required` (schema, config, compose, crontab, mounts) | PR — release-notes workflow depends on it |

Rationale: PRs exist to gate risky changes and to give the `migration-required` → release-notes pipeline something to attach to. A board-chore or docs-tweak PR is overhead without those benefits, and unmerged PRs cause drift (forgotten branches, misleading "merged in #N" references).

When in doubt — does this change affect what users see when they pull `:latest`? If yes, PR. If no, commit to main.

---

## Working Style

- Read file contents before proposing changes. Never assume files match prior discussion.
- Diagnose root cause before fixing. No shotgun solutions.
- Use paths from `findajob.paths`. Platform-aware. No placeholders in commands.
- Never confuse `aichat` with `aichat-ng` — different binaries.
- Preserve the scheduler-driven daily run in all changes.
- Working features first, polish later.

@CLAUDE.local.md
