# findajob — CLAUDE.md

Read by Claude Code at the start of every session. Authoritative context for this codebase.
Personal identifiers (name, targets, API topic, form URLs) live in `CLAUDE.local.md` (gitignored).

---

## Self-Governance — Check Before Every Command

Before writing any command, path, binary call, or file location:

- [ ] All binary paths come from `findajob.paths` — `PANDOC`, `BASE`. Never hardcode.
- [ ] For subprocess calls to other pipeline scripts, use `sys.executable` (never a hardcoded Python path).
- [ ] LLM calls go through `findajob.llm.openrouter.complete()`. Never re-introduce a subprocess transport.
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
- [ ] **Operator topology** — hostnames, deployment paths (`/opt/stacks/...`), backup destinations (NAS / FTP / cloud-bucket specifics), secrets-file locations, port numbers tied to a specific stack, cron-window specifics, the operator's domain, and consumer-grade infra brand names (hypervisor / NAS / VPN mesh products) — see `.git/hooks/pre-commit` for the canonical pattern list. Setup docs use placeholders like `<deployment-host>`, `<operator-handle>`, `<operator-domain>`.

If personal content must exist in the pipeline (e.g., name enforcement in a role prompt),
move it to a **gitignored** file such as `candidate_context/profile.md`, `CLAUDE.local.md`, or
`config/` (credentials), and have the tracked file reference it instead.

### Plans, specs, experiments — operator-private
Implementation plans, design specs, and experiment notes live under `docs/superpowers/`
which is **gitignored** (#430). Files stay on disk for local session use; they are not
tracked. Never re-add them to the index, even temporarily for "just this PR." They are
session-execution diaries, not pedagogical artifacts — outsiders aren't the audience and
the operator-topology leak surface is too large to police line-by-line. See
`docs/plan-conventions.md` for what every plan must contain (the *content* discipline
remains; only the *storage location* changed).

### Never hardcode field-specific content in tracked files
- [ ] Company lists (Meta, Google, OpenAI, etc.) — belong in `config/target_companies.md` or `config/tier1.txt` (gitignored)
- [ ] Job title patterns specific to one field (software engineer, data center operations, nurse, teacher) — belong in `config/prefilter_rules.yaml` or similar (gitignored)
- [ ] Industry vocabulary in role prompts ("NPI", "IC6", "Tier 1 company") — rewrite to reference the candidate profile
- [ ] Hard-reject categories enumerated in `config/roles/job_scorer.md` — should reference profile categories, not enumerate tech/healthcare/finance/etc. inline
- [ ] Example files (`*.example`) should show **multiple fields** or use abstract placeholders, not just tech examples

### Pre-commit hook
A local pre-commit hook at `.git/hooks/pre-commit` blocks commits containing the user's
personal identifiers. The hook is **not tracked** — each clone must install its own. See
`docs/getting-started/configure.md` for setup. When adding new personal identifiers (new ntfy topic,
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
- `docs/getting-started/configure.md` — how to configure the pre-commit hook and set up personal config files

---

## Pipeline Context Table

| Item | Value |
|------|-------|
| Default model | `openrouter:google/gemini-3-flash-preview` |
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
| `speculative_roles_synth` | `openrouter:anthropic/claude-sonnet-4.6`, `max_tokens: 4096` — synthesizes 1–5 candidate-tailored role cards from the briefing. |
| Job ingestion | Pluggable via `JobSourceAdapter` (`src/findajob/fetchers/adapters/`); jobs-api14 + JSearch ship in v0.14; per-stack active list in `config/active_sources.txt`. Greenhouse / Ashby / Lever / Gmail still function-style — migration tracked in #410. v0.15 adds `JobsApi14IndeedAdapter` (Indeed via jobs-api14 with sortType=date + post-filter, restoring pre-#408 coverage) and consolidates RapidAPI credentials to a shared `RAPIDAPI_KEY` env var (legacy `JOBS_API14_KEY` / `JSEARCH_API_KEY` work as fallbacks) (#414). |
| Cost tracking | Every LLM call writes `cost_log.cost_usd` from `response.usage.cost` (OpenRouter authoritative; no heuristic, no calibration). `findajob.cost_rollups` helpers (`per_job_cost`, `per_job_breakdown`, `weekly_spend`, `projected_monthly`, `spend_this_month`) sum directly from `cost_log` to back the nav spend chip, dashboard burn-rate widget, Applied cost cell, Materials breakdown, and notify-stats projection. |
| Package manager | `uv sync` for dev deps; `uv run` prefix for pytest/ruff/mypy/uvicorn |
| Path resolution | `src/findajob/paths.py` — reads `config/paths.env`; BASE derived from `__file__` |
| Roles dir | `config/roles/` |
| Master resume | `candidate_context/master_resume.md` |
| Profile | `candidate_context/profile.md` |
| DB | `data/pipeline.db` |
| Pre-filter | `src/findajob/scorer_prefilter.py` — Stage 1 regex hard reject, Stage 2 no-JD default |
| Board writes | `src/findajob/web/routes/board_actions.py` — every STATUS / REJECT_REASON transition is a POST handler calling `findajob.actions`. SQLite is the single source of truth. |
| Watchdog | `scripts/watchdog.py` every 10 min — resets jobs stuck in `prep_in_progress` > 60 min. |
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
| Scheduler | systemd user services | supercronic inside the container |
| Web viewer | `src/findajob/web/` (package) | uvicorn co-process on container port 8090 (mapped to `FINDAJOB_MATERIALS_PORT`) |

**When authoring new scripts or tests:**
- Always use `findajob.paths.BASE` — never hardcode `/home/...` or `/app/`.
- Binary subprocess calls go through `PANDOC` from `findajob.paths`.
- LLM calls go through `findajob.llm.openrouter.complete()`.
- Tests must not depend on absolute paths — use tmpdirs or `BASE`-relative paths.

---

## Data Ownership

Audit anchor — classifies persisted state by ownership and recoverability. When backup work lands (#426), update the "Backup-critical?" column to reflect what's actually included in the nightly tarball. Deep reference: `docs/superpowers/specs/2026-05-03-301-data-model-audit.md` §1.

| Path | Source | Backup-critical? | Rebuildable if lost? |
|---|---|---|---|
| `data/pipeline.db` | Pipeline-generated; operator-curated via stage transitions, notes, score corrections | **Yes** | **No** — fetcher results from past dates aren't retrievable; transitions are operator decisions |
| `candidate_context/profile.md`, `master_resume.md`, `voice_samples/` | Operator-authored | **Yes** | **No** — re-interview loses weeks of hand-tuning |
| `candidate_context/discovered_companies.{md,json}` | Pipeline-generated (weekly cron) | No | **Yes** — next Sunday discoverer run reproduces |
| `config/` (operator-curated subset: `target_companies.md`, `prefilter_rules.yaml`, `excluded_employers.yaml`, `feed_urls.txt`, `jsearch_queries.txt`, `target_locations.txt`, `feedback_weights.yaml`, `gmail.json`, `gsheets_creds.json`, etc.) | Operator-curated (interview-emitted seed + accumulated edits) | **Yes** | **No** — re-interview emits ~half; hand-curation gone |
| `config/gmail_state.json` | Pipeline-generated (IMAP UID checkpoint) | No | **Yes** — re-syncs on next poll |
| `config/roles/`, `config/scoring_schema.json`, `config/model_pricing.yaml`, `config/reference.docx`, `config/strip-bookmarks.lua` | Repo-baked (in image, not bind-mount) | No | **Yes** — `docker compose pull` restores |
| `data/.env` | Operator-curated (API keys, NTFY_TOPIC) | **Yes** | **No** — rotation-grade pain to re-collect |
| `data/.onboarding-complete` | Pipeline-generated sentinel | No | **Yes** — re-emit on next interview |
| `data/connections.csv` | Operator-uploaded (LinkedIn export) | No | **Yes** — re-export from LinkedIn (minutes) |
| `companies/` (active + `_applied/` + `_waitlisted/` + `_rejected/` + `.stale/`) | Pipeline-generated | Selective (skip `.stale/`) | **Partially** — re-runnable per-job, but stale JD URLs no longer reachable |
| `logs/pipeline.jsonl` | Pipeline-generated | No (observability, not state) | **No** — historical observability lost if dropped |
| `logs/{form-ingest,jobsync,poller,triage,notify,ci-check,rescore_backfill}.log` | Legacy / pipeline-generated | No | **Yes** — mostly stale; safe to drop |

The data layer is the only thing `docker compose pull` + a fresh interview can't regenerate.

---

## Key File Locations

The full file map lives at [`docs/architecture/file-map.md`](docs/architecture/file-map.md). Update that file when files are added, renamed, or retired.

### Web Frontend Architecture

Lives at `src/findajob/web/`. One file per URL group in `routes/` (e.g. `routes/materials.py`, `routes/board.py`, `routes/landing.py`). Shared partials (`_nav.html`, `_job_row.html`) live in `templates/`.

Foundational decisions (design rationale lives in operator-private specs):
- Server-rendered HTML + HTMX (no SPA)
- Grouped URL IA — top-nav = `/`, `/board/`, `/materials/`, `/ingest/`, `/stats/`, `/tools/`, `/config/`, `/settings/`, `/docs/`
- Tailwind via CDN + `static/app.css` design tokens
- URL query params for UI state (not cookies/localStorage)
- Alpine.js added only when ephemeral client state is needed

**`/config/`** — in-browser editor for editable pipeline config; allowlist in `findajob.web.config_files`. No per-user authorization inside findajob — perimeter is the boundary. Default perimeter is a VPN-only mesh; internet-exposed instances additionally require HTTP Basic Auth via `FINDAJOB_AUTH_USER` / `FINDAJOB_AUTH_PASS` (see `findajob.web.auth` and `docs/getting-started/internet-exposure.md`).

**`/settings/`** — domain-aware config editors with rich UX. First occupant: `/settings/reject-reasons/` (#490) — editable rows + per-row `title-signal` checkbox for `config/reject_reasons.yaml`. Distinguished from `/config/` (raw text editor for any allowlisted file): `/settings/` has per-page UX tailored to the config it edits (validation, structured rows, HTMX partial-swap save flow). Saves take effect on the next request without container restart — `findajob.config_loader.load_reject_reasons` is no-cache and `ColumnSpec.enum_values` accepts a callable so dropdown + filter chip values both refresh per request. Future similar editors (e.g., `prefilter_rules.yaml`, `in_domain_patterns.yaml`) live here.

**`/onboarding/`** — first-run NUX. Two-step structure:

- **Step 1 — API keys.** Tester provides own OpenRouter (required) + RapidAPI account key (optional; one account-level key authorizes every API the user has subscribed to under that RapidAPI account, collected uniformly as `RAPIDAPI_KEY` per #414; the Step 2 Section 3h picker selects which adapter is active, not which credential is collected). Collected via `POST /onboarding/keys`; persisted into the credentials-only row in `onboarding_sessions` (UPDATE-not-INSERT on retry). Format validators in `findajob.onboarding.key_validation`; OpenRouter live smoke check at collection. Linked help: `docs/getting-started/api-keys.md`.
- **Step 2 — Run the interview.** Disabled until Step 1 succeeds. Tester runs the entire interview as a chat inside findajob's UI. Server-side persistent — close the tab and reload to resume. Routes live in `findajob.web.routes.onboarding_interview`; runtime-gate per request via `_resolved_chat_key`. Chat is funded by the tester's own OpenRouter key collected in Step 1 — the pipeline (triage, scoring, prep) and the in-app interview both run on that key. There is no operator-funded fallback (the `OPENROUTER_OPERATOR_KEY` env var that briefly existed in v0.11.0 was reverted in v0.11.1 / #401 — operator clarified that path was never supposed to be supported). ~$3-6 per onboarding for Sonnet 4.6 with prompt caching (system-prompt `cache_control` breakpoint; voice-samples emission is the dominant cost driver in long interviews).

The earlier paste-back path (run the interview in another LLM, paste the emission back into a textarea on /onboarding/) was retired 2026-05-02 — it created a phantom OpenRouter input on the finalize form that broke the smoke check when Step 1 had already collected a key, and it doubled the prompt-rewrite surface area whenever the role changed. The in-app flow is the single supported path.

The interview shares: parser (`<<<FILE: name>>>` block protocol — emission delimited blocks are extracted from the cumulative assistant transcript on every turn), injector (atomic file writes + backup + per-stack `data/.env` merge for collected keys), and the `findajob.web.onboarding_guard` dependency that redirects `/`, `/board/*`, `/materials/*`, `/stats/*` to `/onboarding/` when `data/.onboarding-complete` is missing. Re-triggerable via `/onboarding/?mode=rerun`. The injector atomically writes ~10 canonical files under `candidate_context/`/`config/`/`data/`, merges OpenRouter + RapidAPI account (`RAPIDAPI_KEY`, #414) keys into `data/.env` (blank-not-written semantic — `findajob.fetchers` uses `os.environ.get(K, "")` truthiness for skip-vs-call routing), backs up existing destinations to `.backups/{UTC-stamp}/`, optionally processes pasted `voice-samples.md` (markdown-strip + PII-generalization), and verifies the OpenRouter key with a 1-token live call. The injector itself **never writes the sentinel** (#407) — every onboarding flow ends at the Gmail-config gate (`/onboarding/gmail-config/{sid}/`), whose `/finish` (verified IMAP) or `/skip` endpoint is the single sentinel-write site. See `findajob.onboarding.{parser,injector,voice_processor,openrouter_smoke,session_store,interview_runner,key_validation}` for boundaries.

**Per-stack key isolation invariant (#339):** every tester stack's `data/.env` carries only that tester's collected credentials; no operator-key leakage. The schema migration (`migrate_schema()` in `session_store.py`) runs idempotently at app startup. Existing tester stacks with the sentinel already present skip the new collection flow — migration applies only to net-new onboardings.

**`/docs/`** — renders `docs/usage.md`, `docs/troubleshooting.md`, `docs/getting-started/*` inline in the web UI. Slug allowlist in `findajob.web.routes.docs`; rendering via `findajob.web.markdown.render_markdown()` (handles `.md` cross-link rewriting + `target="_blank"` on external links).

**Operator mode** — gated by `FINDAJOB_OPERATOR_MODE=1` (operator's stack only;
never set on testers'). Adds `/admin/stacks/` route and renders the top nav in
red on every page. The route is the **only** code path that reads cross-stack
state from inside `findajob.web` — invariant: read-only, no POST handlers, all
SQLite handles open with `mode=ro` URI. See `findajob.admin.{stack_discovery,
stack_health,jsonl_tail}` and `docs/getting-started/install-docker.md` "Operator mode"
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
`findajob.actions`. SQLite is the single source of truth. Do not add
new transition logic to `watchdog.py` or to any out-of-band path — every
new action is a new web handler + a new `findajob.actions` helper.

Some transitions also spawn detached generator subprocesses:
- `POST /board/jobs/{fp}/prep` and `/regenerate` → `scripts/prep_application.py` (briefing, tailored resume, cover, recruiter critique, outreach drafts)
- `POST /board/jobs/{fp}/interview` → `scripts/interview_prep.py` (interview prep artifact). Always (re)launches on each click — re-clicking is the regenerate mechanism after a recruiter sends panel info; a sentinel file `.interview_prep_in_progress` in the prep folder guards against concurrent runs.
- `POST /ingest/speculative` and `POST /speculative/regenerate/{id}` → `scripts/run_speculative_research.py` (briefing + role-synth pipeline). Async — status page polls `/speculative/status/{id}/poll` every 5s until `status='ready_for_review'`. Full route surface in `findajob.web.routes.speculative` (POST `/ingest/speculative`, GET `/speculative/status/{id}` + `/poll`, GET `/speculative/review/{id}`, POST `/speculative/{approve,regenerate,trash}/{id}`).
- `POST /board/jobs/{fp}/apply` is synthetic-aware: reads `jobs.synthetic` and writes `audit_log.changed_by='outreach_button'` for synthetic rows (label flips to "Sent Outreach" on the dashboard); otherwise the existing `'user'` value. No separate route — single endpoint, server-derived signal.

### Path Resolution
The `PANDOC` binary path comes from `findajob.paths` (`src/findajob/paths.py`), which reads `config/paths.env`.
Never hardcode platform paths in scripts. `BASE` is derived from `__file__` — the repo can live anywhere.
For subprocess calls to other pipeline scripts, always use `sys.executable`, not a hardcoded Python path.
Library code lives in `src/findajob/` (installed editable into the project venv via `uv sync` for local dev, `pip install -e .` inside the Docker image — #126). Entry point scripts in `scripts/` import via `from findajob.* import ...`. No `sys.path.insert` hacks.

### RAG Policy

RAG was retired in v0.19.0 (#267, #455). The pipeline never consumed embeddings in production code; operator REPL queries are off-pipeline.

### Source Adapters are Pluggable
Every RapidAPI-flavored job source implements `JobSourceAdapter`
(`src/findajob/fetchers/adapters/base.py`). Adding a new feed = one new
adapter file + one entry in `REGISTERED_ADAPTERS`. `triage.py` iterates
the registry; no per-source code paths in triage. Adapters share a canonical
`RAPIDAPI_KEY` env var (#414); per-adapter env vars (`JOBS_API14_KEY`,
`JSEARCH_API_KEY`) remain valid as fallbacks for legacy stacks. Stacks pick
which adapters to run via `config/active_sources.txt` (default: `['jobs-api14']`
if missing). The `JobSourceAdapter` Protocol is source-agnostic
by design — direct fetchers (Workday CXS #248, Gem GraphQL #249) implement
the same contract.

### Hard Rejects are Code
`scorer_prefilter.py` handles hard rejects deterministically before any LLM call.
Stage 1: title regex → score 1, no LLM. Stage 2: in-domain + no JD → score 5/6, no LLM.
Never rely on LLM prompt instructions alone for boolean classification tasks.

### Cost Tracking Is Native
Every LLM call goes through `findajob.llm.openrouter.complete()`, which writes `cost_log.cost_usd` from OpenRouter's `response.usage.cost` (authoritative). Every cost number rendered in the UI (nav spend chip, dashboard burn-rate widget, Applied cost cell, Materials breakdown, notify-stats projection) sums directly from `cost_log` via `findajob.cost_rollups` helpers — no heuristic, no calibration, no multiplier. If a new surface needs cost data, add a helper to `cost_rollups.py` so the math stays in one place.

The earlier calibration stack (`cost_calibration` table, `poll_openrouter_credits` cron, multiplier-application across the rollup helpers) was retired in v0.20.0 (#472) once Phase 1+2 of the OpenRouter native migration (#469 epic) had ported every production call site to the wrapper.

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

Full spec lives in operator-private notes (`docs/superpowers/`, gitignored).

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

### Board Routes & Stage Lifecycle

Every transition is a POST handler in `findajob.web.routes.board_actions`
that calls straight into `findajob.actions` (handle_rejection,
handle_not_selected, handle_waitlist, handle_reactivate,
promote_to_scored, notify_waitlist_resurface, reset_prep_to_scored) and
responds in the same request — no poll cycle, no mirror table.

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

**Stage `not_selected`:** Set by `POST /board/jobs/{fp}/not-selected`. Only valid for post-application stages (`applied`, `interview`, `offer`); 409 otherwise. Folder stays in `companies/_applied/` with a `NOT_SELECTED_{reason}_{date}.txt` marker file. Does NOT write to `feedback_log` — company rejections must not contaminate the scorer's feedback loop. `notify_waitlist_resurface()` still fires.

**Stage `prep_in_progress`:** Set by `POST /board/jobs/{fp}/prep` immediately before launching `prep_application.py` as a subprocess. Prevents duplicate prep runs (handler idempotency guard + 3-job concurrency cap). Cleared to `materials_drafted` on success. `scripts/watchdog.py` rolls any job stuck > 60 min back to `scored` so the operator can re-flag.

**Health checks** (`notify.py health-check`): warns if manual_review backlog > 100, a source silently stopped producing jobs, or any target-company job scored 3–6 in last 7 days (potential mis-scores).

### Gmail Integration

Gmail ingestion uses IMAP + app password, configured per-stack at `/config/gmail/`. Transparency contract codified as executable assertions in `tests/test_transparency_invariants.py` — failures there mean the disclosure banner is lying.

### Auth Gate Must Be Verified Post-Deploy

After every `docker compose up -d` on any findajob stack, the basic-auth gate must be auto-verified. If it isn't healthy, the stack is auto-shutdown until fixed. **No exceptions** — including hotfixes, rollbacks, and one-off restarts.

The verifier is `findajob.web.verify_auth` (image-baked). Run from inside the running container:

```bash
docker exec <stack>-scheduler-1 python -m findajob.web.verify_auth
```

It checks three things and exits non-zero on any failure:
1. `FINDAJOB_AUTH_USER` and `FINDAJOB_AUTH_PASS` are non-empty in the runtime env (exit 2)
2. An anonymous request to a protected route returns `401` with a `WWW-Authenticate: Basic` header (exit 3)
3. An authenticated request with the configured creds returns `200` (exit 4)
   (Network-level failures are exit 5.)

On any non-zero exit:

```bash
cd /opt/stacks/<stack> && docker compose down
```

The operator (or Claude on the operator's behalf) must fix before bringing back up. Why this is a hard rule: in 2026-05-07, `findajob-test` was found internet-exposed without auth because no one verified after a previous recompose. This rule makes that class of incident detectable in the same operational pass that caused it, instead of relying on accidental discovery.

This applies to every stack, including operator-only ones. A stack that doesn't have basic auth configured (e.g., a future stack reachable only via an internal mesh perimeter that explicitly chooses no app-level auth) is expected to fail with exit 2 — that's the signal to either configure auth or document the explicit allowlist exception in CLAUDE.local.md.

---

## Implementation Guardrails

Discipline layer that complements the architectural invariants above. Apply before every change.

- **Architectural invariants** must not be touched casually — see "Critical Architecture Rules" above. SQLite-as-SoT, Web-as-Write-Surface, centralized LLM transport, `JobSourceAdapter` Protocol.
- **Patterns new code must follow**: `findajob.llm.openrouter.complete` for every LLM call; `findajob.actions` for every state transition; route-matrix tests for new POST handlers; `findajob.utils.log_event` / `write_audit` for events. No `logging.getLogger`. No mocking of `sqlite3.connect` in tests (use real SQLite — tmpfile or `:memory:`). No prompt-string snapshots; assert structural properties.
- **Patterns to retreat from on every pass-through**: bare `sqlite3.connect`, additions to `utils.py`, business logic in `scripts/*.py`, `.in_progress` sentinel files, inline `ALTER TABLE` in `init_db.py`. Don't sweep — clean up only when you're already in the file for another reason.
- **Soft-cap file sizes**: ~300 LOC for `src/findajob/` modules; ≤50 LOC for `scripts/` shims (entry-points only); ~400 LOC for route modules; ~500 LOC for tests. Hard signals at ~1.5×. CLAUDE.md itself caps near 500.
- **Same-PR docs rule**: when code touches a documented surface, update the docs in the same PR. Schema → CHANGELOG `### Migration required` entry; new env var → `configure.md`; new state transition → the Board Routes table above. No "docs follow-up" deferrals.
- **Tests required when**: new POST handler in `routes/`, new `actions` helper, schema change, new adapter registered in `REGISTERED_ADAPTERS`, change to `complete()` or `cost_rollups`, change to dedup/cleaning helpers, or change crossing a known-repeat-bug boundary (cross-stack SQLite immutable URI; audit_log timestamp formats; jobs.id JOIN dependencies; blank-string `company_match` guards). Otherwise encouraged but not gated.
- **Split a refactor across PRs when**: it crosses a `migration-required` boundary, exceeds ~500 LOC of behavior change, mixes cleanup with behavior change, or risks a partial-state outage. Otherwise keep it one PR.

The full PR + maintainer checklists, deprecation table, dependency-add criteria, and error-handling/logging conventions will be promoted into `CONTRIBUTING.md` as part of Open-Source Launch Readiness (Epic #377). This section is the durable abridged form until then.

---

## Project Board — Single Source of Truth

All work is tracked on the GitHub Project board at https://github.com/users/brockamer/projects/1. **Not on the board = not on the roadmap.** No markdown tracking files, no TODO lists.

Canonical conventions live in [`docs/project-board.md`](docs/project-board.md). Read it before any work that creates, moves, or reprioritizes issues. That doc covers columns, Priority field, labels, triage checklist, and `gh project` CLI IDs.

Core rules (enforced — see the doc for detail):
- Creating an issue is **two steps**: `gh issue create` then `gh project item-add 1 --owner brockamer --url <url>`. New issues do not auto-add. The `/jared file` skill atomizes this — prefer it over manual `gh` calls.
- Every open issue on the board must have **Priority** (High/Medium/Low) set.
- Speculative far-horizon ideas get the `big-idea` label and Priority: Low — keeps them on the board without cluttering the active roadmap.
- `priority: high/med/low` labels are legacy — the **Priority field** is canonical. Reconcile mismatches.
- In Progress should hold 1–3 items max. If more, focus is scattered.
- Status transitions: Backlog → Up Next → In Progress → Done. Closing an issue auto-moves to Done; verify after closing.
- Re-sync board state before changing it — other sessions may have updated it.

**When board usage evolves** (new column, new label, new workflow, new convention): update `docs/project-board.md` in the same change. The doc describes how the board actually works, not how it used to work. Behavior drifting ahead of docs is the main failure mode.

---

## Plan Conventions

Implementation plans live in an operator-private location (`docs/superpowers/plans/` is gitignored — files exist on disk for session use, but are not tracked). Conventions for plan *content* are documented in [`docs/plan-conventions.md`](docs/plan-conventions.md).

**Hard requirements for every plan:**
- Numbered tasks with files, steps, verification commands, commit messages
- A **Documentation Impact** section enumerating every doc surface that needs to change (README, docs/getting-started/*, CLAUDE.md, CHANGELOG.md, spec doc, docstrings). If none, say "None" — never omit the section
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
| Code touching pipeline behavior (scoring, fetchers, DB schema, LLM roles) | Feature branch → PR → merge |
| Anything qualifying for `migration-required` (schema, config, compose, crontab, mounts) | PR — release-notes workflow depends on it |

Rationale: PRs exist to gate risky changes and to give the `migration-required` → release-notes pipeline something to attach to. A board-chore or docs-tweak PR is overhead without those benefits, and unmerged PRs cause drift (forgotten branches, misleading "merged in #N" references).

When in doubt — does this change affect what users see when they pull `:latest`? If yes, PR. If no, commit to main.

---

## Working Style

- Read file contents before proposing changes. Never assume files match prior discussion.
- Diagnose root cause before fixing. No shotgun solutions.
- Use paths from `findajob.paths`. Platform-aware. No placeholders in commands.
- Preserve the scheduler-driven daily run in all changes.
- Working features first, polish later.

@CLAUDE.local.md

