# findajob — CLAUDE.md

Read by Claude Code at the start of every session. Authoritative context for this codebase.
Personal identifiers (name, targets, API topic, form URLs) live in `CLAUDE.local.md` (gitignored).

---

## Self-Governance — Check Before Every Command

Before writing any command, path, binary call, or file location:

- [ ] All binary paths come from `findajob.paths` — `PANDOC`, `BASE`. Never hardcode.
- [ ] For subprocess calls to other pipeline scripts, use `sys.executable` (never a hardcoded Python path).
- [ ] LLM calls go through `findajob.llm.openrouter.complete()`. Never re-introduce a subprocess transport.

**If uncertain about any value: say so. Do not guess.**

---

## PII and Domain-Neutrality

The repo is public. Tracked files must not contain personal identifiers (real names, emails, API keys) or content that locks the pipeline to one career field. The actual enforcement layer is `.git/hooks/pre-commit` — see `docs/operations/config-reference.md` for setup. The hook is not tracked; each clone installs its own and extends `PATTERNS` when new identifiers appear.

Two categories the hook can't fully catch — be deliberate about these:

- **Operator topology** — hostnames, deployment paths (`/opt/stacks/...`), backup destinations, consumer infra brand names (hypervisor / NAS / VPN mesh products), per-stack port numbers, the operator's domain. Setup docs use placeholders: `<deployment-host>`, `<operator-handle>`, `<operator-domain>`.
- **Field-locked content** — hardcoded company lists, single-field title patterns, industry vocabulary in role prompts. Belong in gitignored config (`config/target_companies.md`, `config/prefilter_rules.yaml`) or referenced from the candidate profile, not enumerated in tracked files. Tracking doc: [`docs/maintainers/generalization.md`](docs/maintainers/generalization.md).

Plans, specs, and experiment notes under `docs/superpowers/` are gitignored (#430). Stay off the index even for "just this PR." Plan-content conventions are documented in [`## Plan Structure`](#plan-structure) below; the *storage* is operator-private.

If you find yourself wanting to put a real name, real employer, real city, or a tech-only example into a tracked file: move it to `CLAUDE.local.md` or a gitignored config and reference it instead.

---

## Pipeline Context

The pipeline is Docker-only: image `ghcr.io/brockamer/findajob`, supercronic + uvicorn co-process inside one container, paths under `/app/...` (override via `JSP_BASE`). All scripts use `findajob.paths.BASE` — never hardcode `/home/...` or `/app/`. All LLM calls go through `findajob.llm.openrouter.complete()`. Per-role model assignments, plumbing, and container path shifts are in [`## Per-Role Model Assignments`](#per-role-model-assignments) below.

---

## Per-Role Model Assignments

<!-- Absorbed from docs/maintainers/pipeline-context.md, 2026-05-27 -->

Model assignment for every LLM-driven role, plus the canonical paths and conventions the pipeline depends on. Read when working on a specific role, fetcher, or path question.

### Models per role

| Role | Model | Notes |
|------|-------|-------|
| Default | `openrouter:google/gemini-3-flash-preview` | |
| `job_scorer` | `openrouter:deepseek/deepseek-v3.2` | profile.md injected directly; `--rag` NEVER used |
| `resume_tailor` / `cover_letter_writer` | `openrouter:anthropic/claude-opus-4.8` | `max_tokens: 4096` |
| `briefing_writer` | `openrouter:anthropic/claude-opus-4.8` | cascades into `resume_tailor` + `cover_letter_writer` |
| `outreach_drafter` | `openrouter:anthropic/claude-opus-4.8` | profile + voice samples injected directly |
| `recruiter_critic` | `openrouter:anthropic/claude-opus-4.8` | `max_tokens: 1024`; sees company, title, JD, tailored resume, cover; NOT profile/briefing/fit |
| `interview_prep` | `openrouter:anthropic/claude-opus-4.8` | `max_tokens: 4096`; fires on `applied → interview` |
| `company_discoverer` | `openrouter:perplexity/sonar-reasoning-pro` | weekly Sun 02:00; emits `candidate_context/discovered_companies.md` + `.json`; field-agnostic, augments static `## Target Companies` |
| `company_researcher` | `openrouter:perplexity/sonar-reasoning-pro` | |
| `fit_analyst` | `openrouter:perplexity/sonar-reasoning-pro` | appended to company briefing |
| `candidate_led_briefing` | `openrouter:perplexity/sonar-deep-research` | async (1–5 min); drives speculative briefing pass |
| `speculative_roles_synth` | `openrouter:anthropic/claude-sonnet-4.6` | `max_tokens: 4096`; synthesizes 1–5 candidate-tailored role cards |
| `resume_change_reviewer` / `network_analyst` | `openrouter:google/gemini-3-flash-preview` | |

### Pipeline plumbing

| Item | Value |
|------|-------|
| Job ingestion | Pluggable via `JobSourceAdapter` (`src/findajob/fetchers/adapters/`); jobs-api14 + JSearch ship in v0.14; per-stack active list in `config/active_sources.txt`. Greenhouse / Ashby / Lever / Gmail still function-style — migration tracked in #410. v0.15 adds `JobsApi14IndeedAdapter` (Indeed via jobs-api14 with sortType=date + post-filter). RapidAPI credentials consolidated to `RAPIDAPI_KEY` (legacy `JOBS_API14_KEY` / `JSEARCH_API_KEY` work as fallbacks) per #414. |
| Cost tracking | Every LLM call writes `cost_log.cost_usd` from `response.usage.cost` (OpenRouter authoritative; no heuristic, no calibration). `findajob.cost_rollups` helpers (`per_job_cost`, `per_job_breakdown`, `weekly_spend`, `projected_monthly`, `spend_this_month`) sum directly from `cost_log` to back the nav spend chip, dashboard burn-rate widget, Applied cost cell, Materials breakdown, and notify-stats projection. |
| Per-prep cost projection | `findajob.prep.cost_projection.compute_projection` runs at `_run_prep_phase_a` start and emits a `prep_cost_projection` event to `pipeline.jsonl` with `projected_cost_usd` (sum of trailing-30d per-`(role, model)` medians for the 8 prep roles), `expensive_role`, and `ceiling_usd` (1.5x trailing-30d median full-prep cost, scoring excluded). When projection > ceiling, an additional `prep_cost_projection_high` event fires — non-blocking, the operator wanted early warning not a gate. Cold start (no `cost_log` history) emits the event with `None` sentinels and `n_roles_with_history=0`. Catches per-prep cost creep earlier than "operator notices an outlier in the burn-rate widget" (#713). |
| Package manager | `uv sync` installs findajob (editable) + dev deps; `uv run` prefix for pytest/ruff/mypy/uvicorn |
| Path resolution | `src/findajob/paths.py` — reads `config/paths.env`; BASE derived from `__file__` |
| Roles dir | `config/roles/` |
| Master resume | `candidate_context/master_resume.md` |
| Profile | `candidate_context/profile.md` |
| DB | `data/pipeline.db` |
| Pre-filter | `src/findajob/scorer_prefilter.py` — Stage 1 regex hard reject, Stage 2 no-JD default |
| Board writes | `src/findajob/web/routes/board_actions.py` — every STATUS / REJECT_REASON transition is a POST handler calling `findajob.actions`. SQLite is the single source of truth. |
| Watchdog | `scripts/watchdog.py` every 10 min — resets jobs stuck in `prep_in_progress` > 60 min |
| Scheduler | supercronic in-container; schedules declared in `ops/scheduled-jobs.yaml`, rendered to `/app/crontab` by `scripts/render_crontab.py` at entrypoint. Per-job env overrides: `FINDAJOB_<JOB>_SCHEDULE` / `FINDAJOB_<JOB>_ENABLED` (#344). |
| ntfy topic | in `data/.env` as `NTFY_TOPIC`; also in `CLAUDE.local.md` |

### Container path shifts

When the pipeline runs inside the `ghcr.io/brockamer/findajob` image, paths shift:

| Thing | Local clone | Container |
|---|---|---|
| `BASE` (from `findajob.paths`) | Repo clone path | `/app` (set via `JSP_BASE=/app` in compose) |
| `data/pipeline.db` | `<repo>/data/pipeline.db` | `/app/data/pipeline.db` (bind-mounted from `./state/data/`) |
| `config/roles/` | `<repo>/config/roles/` | `/app/config/roles/` (baked into image — NOT from bind mount) |
| Personal config (`config/*.yaml|.txt|.json`) | `<repo>/config/` | `/app/config/` (bind-mounted from `./state/config/`) |
| `candidate_context/` | `<repo>/candidate_context/` | `/app/candidate_context/` (bind-mount) |
| `discovered_companies.{md,json}` | `<repo>/candidate_context/` (gitignored) | `/app/candidate_context/` (generated into bind-mount) |
| `companies/` | `<repo>/companies/` | `/app/companies/` (bind-mount) |
| Onboarding sentinel | `<repo>/data/.onboarding-complete` | `/app/data/.onboarding-complete` (bind-mount) |
| Onboarding backups | `<repo>/.backups/{UTC-stamp}/` | `/app/.backups/` (bind-mount from `./state/.backups/`) |
| Web viewer | `src/findajob/web/` (package) | uvicorn co-process on container port 8090 (mapped to `FINDAJOB_MATERIALS_PORT`) |

### When authoring scripts or tests

- Always use `findajob.paths.BASE` — never hardcode `/home/...` or `/app/`.
- Binary subprocess calls go through `PANDOC` from `findajob.paths`.
- LLM calls go through `findajob.llm.openrouter.complete()`.
- Tests must not depend on absolute paths — use tmpdirs or `BASE`-relative paths.

---

## Data Ownership and Backup Classification

<!-- Absorbed from docs/maintainers/data-ownership.md, 2026-05-27 -->

Audit anchor — classifies persisted state by ownership and recoverability. The data layer is the only thing `docker compose pull` + a fresh interview can't regenerate.

| Path | Source | Backup-critical? | Rebuildable if lost? |
|---|---|---|---|
| `data/pipeline.db` | Pipeline-generated; operator-curated via stage transitions, notes, score corrections | **Yes** | **No** — fetcher results from past dates aren't retrievable; transitions are operator decisions |
| `candidate_context/profile.md`, `master_resume.md`, `voice_samples/` | Operator-authored | **Yes** | **No** — re-interview loses weeks of hand-tuning |
| `candidate_context/discovered_companies.{md,json}` | Pipeline-generated (weekly cron) | No | **Yes** — next Sunday discoverer run reproduces |
| `config/` (operator-curated subset: `target_companies.md`, `prefilter_rules.yaml`, `excluded_employers.yaml`, `ranking_boost_terms.yaml`, `title_signal_keywords.yaml`, `feed_urls.txt`, `jsearch_queries.txt`, `target_locations.txt`, `feedback_weights.yaml`, `gmail.json`, `gsheets_creds.json`, etc.) | Operator-curated (interview-emitted seed + accumulated edits) | **Yes** | **No** — re-interview emits ~half; hand-curation gone |
| `config/gmail_state.json` | Pipeline-generated (IMAP UID checkpoint) | No | **Yes** — re-syncs on next poll |
| `config/roles/`, `config/scoring_schema.json`, `config/model_pricing.yaml`, `config/reference.docx`, `config/strip-bookmarks.lua` | Repo-baked (in image, not bind-mount) | No | **Yes** — `docker compose pull` restores |
| `data/.env` | Operator-curated (API keys, NTFY_TOPIC) | **Yes** | **No** — rotation-grade pain to re-collect |
| `data/.onboarding-complete` | Pipeline-generated sentinel | No | **Yes** — re-emit on next interview |
| `data/timezone` | Operator-picked at onboarding; read at container boot to set `TZ` (#981) | No | **Yes** — re-interview re-emits; effective only after restart |
| `data/connections.csv` | Operator-uploaded (LinkedIn export) | No | **Yes** — re-export from LinkedIn (minutes) |
| `companies/` (active + `_applied/` + `_waitlisted/` + `_rejected/` + `.stale/`) | Pipeline-generated | Selective (skip `.stale/`) | **Partially** — re-runnable per-job, but stale JD URLs no longer reachable |
| `logs/pipeline.jsonl` | Pipeline-generated | No (observability, not state) | **No** — historical observability lost if dropped |
| `logs/{form-ingest,jobsync,poller,triage,notify,rescore_backfill}.log` | Legacy / pipeline-generated | No | **Yes** — mostly stale; safe to drop |

Deep reference: `docs/superpowers/specs/2026-05-03-301-data-model-audit.md` §1 (operator-private).

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

**Authorization model:** no per-user auth inside findajob — perimeter is the boundary. Default perimeter is VPN-only; internet-exposed instances add HTTP Basic Auth via `FINDAJOB_AUTH_USER` / `FINDAJOB_AUTH_PASS` (see `findajob.web.auth` and [`docs/operations/internet-exposure.md`](docs/operations/internet-exposure.md)).

**Top-level URL groups:**

- `/config/` — raw text editor for allowlisted config files (`findajob.web.config_files`).
- `/settings/` — domain-aware config editors with per-page UX (validation, structured rows, HTMX partial-swap). Occupants: `/settings/reject-reasons/` (#490), `/settings/active-sources/` (#603 — checkbox list of `REGISTERED_ADAPTERS` with per-row `is_configured()` badge; writes `config/active_sources.txt` atomically), `/settings/connections/` (#614 — maintenance UI for `data/connections.csv`: last-imported timestamp, row count, refresh/replace, remove with confirm-zone modal mirroring the #700 regenerate-confirm pattern), `/settings/backup/` (#841 — one-click backup tarball download; streams a gzipped tar via `sqlite3 .backup` API + `tarfile`; tarball follows the `state/` contract in `docs/operations/restore.md`; every instance gets its own backup), and `/settings/timezone/` (#988 — IANA zone picker that atomically writes `data/timezone` via `findajob.timeutil.write_timezone_file`; pick takes effect on next restart, mirroring the onboarding pick and the dashboard restart-to-apply banner #981; the dashboard timezone banner links here). The connections page shares `findajob.web.connections_upload` (validator + atomic write) with the onboarding gate at `/onboarding/connections/` (#571) so the two upload paths can't drift; the shared `templates/settings/_linkedin_export_explainer.html` partial keeps the LinkedIn-export procedure copy DRY across both. Saves take effect on the next request without container restart; `findajob.config_loader` loaders are no-cache. The `/board/dashboard` shows a dismissible banner when `active_sources.txt` is absent, pointing at `/settings/active-sources/`. All `/settings/` pages include the shared `templates/settings/_settings_nav.html` sub-nav (#988) — a single declarative `(href, label)` list that cross-links every settings page and bolds the active one via a `request.url.path` prefix match. There is no `/settings/` index route; the sub-nav is the discoverability surface. Adding a settings page = append one pair to that list and include the partial in the new template.
- `/onboarding/` — first-run NUX. Steps: (0) auth credentials (#895 — conditional, only when `FINDAJOB_AUTH_USER`/`FINDAJOB_AUTH_PASS` not set; writes to `data/.env` + `app.state`, middleware activates immediately without restart), (1) API keys (user's own OpenRouter required), (2) chat interview (`onboarding_sessions` table). Sentinel `data/.onboarding-complete` written by the Gmail-config gate; `findajob.web.onboarding_guard` guards the core app surfaces (board, materials, stats, all `/settings/*`, speculative, rejections-review, landing, notifications), redirecting them to `/onboarding/` until it exists; `/onboarding/`, `/docs/`, `/config/`, `/tools/`, and `/ingest/` stay reachable. Alternative path: `/onboarding/restore/` (#841) accepts a backup tarball upload, validates the `state/` contract, extracts atomically with rollback, and redirects to the dashboard — skipping the interview. Confirm-overwrite gate on already-onboarded instances.
- `/docs/` — renders pages from `docs/getting-started/*`, `docs/usage/*`, `docs/operations/*`, `docs/tuning.md`, `docs/troubleshooting.md` inline. Slug allowlist in `findajob.web.routes.docs._PAGES`. Breadcrumb navigation on subpages, sequential "Next step" links on getting-started pages, primary CTA on the index.
- `/tools/` — guided LLM prompts (`findajob.web.tools_registry`) plus direct-edit shortcuts. Each prompt tile loads its body from `config/tool_prompts/{slug}.md` and renders a Copy-prompt button plus an "Open in Claude" anchor pointing at `claude.ai/new?q=<urlencoded prompt>` (anchor is omitted when the encoded prompt exceeds 6 KB). Adding a new tool = append a tile to `TILES` + drop a markdown file in `config/tool_prompts/` — no schema, no migration. Prompts steer their output into gitignored `candidate_context/profile.md` sections (`## Excluded Categories`, `## Title Calibration Notes`, etc.) rather than tracked role files, keeping the surface generalization-safe (#150).

### Per-column filter framework

Declarative framework at `findajob.web.filters`. Each board tab declares a `tuple[ColumnSpec, ...]` in `findajob.web.filters.registry`; framework parses URL params, builds parameterized SQL clauses, and renders header inputs via shared partials.

URL contract — flat, type-suffixed param names: `?col=sub` (TEXT), `?col_min=&col_max=` (SCORE/INTEGER), `?col=a,b,c` (ENUM), `?col_from=&col_to=` (DATE), `?sort=col&desc=1`, `?cols=a,b,c` (visibility).

Adding a new tab: declare ColumnSpec list in `registry.py`, add base WHERE + `_<tab>_query()` in `routes/board.py`, include `_filters.html` + `_table_header.html` in the template.

**Per-tab persistence (#277).** The framework's URL state persists per tab via the `view_prefs` SQLite table (migration 0005). Cascade for what filters/cols apply on load: **URL ?cols= + filter params > persisted `view_prefs` row > `ColumnSpec.default_visible`**.

Mechanism — redirect-on-cold-load + auto-save-on-URL-settle, both in `findajob.web.routes.board`:

- `_maybe_redirect_to_persisted(...)` — page GET with no allowlisted filter state but a persisted row 303-redirects to the same path with `?<persisted_qs>`. Bookmarks/deep-links win because the URL has filter state by the time this check runs.
- `_persist_view(...)` — every page + `/rows` GET auto-saves the serialized parsed filters via `findajob.web.view_prefs.save()`. Density and other unrelated URL params (`?dismiss_*=`) are filtered by construction — `view_prefs.serialize()` rebuilds the querystring from `ParsedFilters` after parsing, not from `request.url.query`. **Default-aware cols (#844):** when `parsed.cols` equals the tab's `default_visible` set (order-ignored), the `cols=` clause is dropped from persistence so a no-op clause doesn't ride the cold-load redirect and render the chip-strip's cols pill on a perceived default view.
- `POST /board/{tab}/reset-view` — explicit clear via the "Reset to defaults" link in `_filters.html` AND the "Clear all" link in `_active_filters.html`. 303s back to the bare tab URL.
- `POST /board/{tab}/reset-filter/{name}` — per-chip clear backing the ✕ buttons on the chip strip in `_active_filters.html` (#844). Reads the persisted state, removes the named key (a column name, the literal `cols`, or the literal `sort`), re-serializes with `default_cols`, and 303-redirects to the path with the remaining filters in the URL (or bare `/board/{tab}` if nothing remains). GET anchors used here landed in `_maybe_redirect_to_persisted`'s cold-load branch when the chip was the last filter and silently snapped back to persisted state; POST + explicit reset avoids the loop class for every filter type.

UI: the "Columns ▾" dropdown in `_filters.html` mirrors the enum-popover Alpine pattern from `_table_header.html` (checkbox per spec column, Apply/Clear/Cancel buttons, hidden `cols` input + `filters.js` handler commits the new `?cols=` via HTMX). Adding a new tab to persistence: append the tab id to `view_prefs.ALLOWED_TABS`, the migration's CHECK constraint, the `_URL_TAB_TO_STORAGE` map (if the URL form uses a hyphen), and the `ensure_view_prefs_table()` test helper.

---

## Critical Architecture Rules

### Web is the Write Surface
Every STATUS and REJECT_REASON transition runs through a POST handler in
`findajob.web.routes.board_actions` that calls straight into
`findajob.actions`. SQLite is the single source of truth. Do not add
new transition logic to `watchdog.py` or to any out-of-band path — every
new action is a new web handler + a new `findajob.actions` helper.

Some transitions also spawn detached generator subprocesses:
- `POST /board/jobs/{fp}/prep` and `/regenerate` → `scripts/prep_application.py` (briefing, tailored resume, cover, recruiter critique, outreach drafts). Default `--phase=all` runs Phase A then Phase B in sequence.
- `POST /board/jobs/{fp}/continue-prep` → `scripts/prep_application.py --phase=b` (the briefing-first gate #691). Promotes a `briefing_ready` row to `prep_in_progress` and runs Phase B only (resume tailor → cover → critique → outreach). Operator-confirmed continuation after Phase A's briefing.
- `POST /board/jobs/{fp}/interview` → `scripts/interview_prep.py` (interview prep artifact). Always (re)launches on each click — re-clicking is the regenerate mechanism after a recruiter sends panel info; a sentinel file `.interview_prep_in_progress` in the prep folder guards against concurrent runs.
- `POST /ingest/speculative` and `POST /speculative/regenerate/{id}` → `scripts/run_speculative_research.py` (briefing + role-synth pipeline). Async — status page polls `/speculative/status/{id}/poll` every 5s until `status='ready_for_review'`. Full route surface in `findajob.web.routes.speculative` (POST `/ingest/speculative`, GET `/speculative/status/{id}` + `/poll`, GET `/speculative/review/{id}`, POST `/speculative/{approve,regenerate,trash}/{id}`).
- `POST /board/jobs/{fp}/apply` is synthetic-aware: reads `jobs.synthetic` and writes `audit_log.changed_by='outreach_button'` for synthetic rows (label flips to "Sent Outreach" on the dashboard); otherwise the existing `'user'` value. No separate route — single endpoint, server-derived signal.

### Path Resolution
The `PANDOC` binary path comes from `findajob.paths` (`src/findajob/paths.py`), which reads `config/paths.env`.
Never hardcode platform paths in scripts. `BASE` is derived from `__file__` — the repo can live anywhere.
For subprocess calls to other pipeline scripts, always use `sys.executable`, not a hardcoded Python path.
Library code lives in `src/findajob/` (installed editable into the project venv via `uv sync` for local dev, `pip install -e .` inside the Docker image — #126). Entry point scripts in `scripts/` import via `from findajob.* import ...`. No `sys.path.insert` hacks.

### Source Adapters are Pluggable
Every RapidAPI-flavored job source implements `JobSourceAdapter`
(`src/findajob/fetchers/adapters/base.py`). Adding a new feed = one new
adapter file + one entry in `REGISTERED_ADAPTERS`. `triage.py` iterates
the registry; no per-source code paths in triage. Adapters share a canonical
`RAPIDAPI_KEY` env var (#414); per-adapter env vars (`JOBS_API14_KEY`,
`JSEARCH_API_KEY`) remain valid as fallbacks. Active adapters are selected via
`config/active_sources.txt` (default: `['jobs-api14']` if missing). The
`JobSourceAdapter` Protocol is source-agnostic
by design — direct fetchers (Workday CXS #248, Gem GraphQL #249) implement
the same contract.

### Hard Rejects are Code
`scorer_prefilter.py` handles hard rejects deterministically before any LLM call.
Stage 1: title regex → score 1, no LLM. Stage 2: in-domain + no JD → score 5/6, no LLM.
Never rely on LLM prompt instructions alone for boolean classification tasks.

### Cost Tracking Is Native
Every LLM call goes through `findajob.llm.openrouter.complete()`, which writes `cost_log.cost_usd` from OpenRouter's `response.usage.cost` (authoritative — no heuristic, no calibration, no multiplier). UI surfaces (nav spend chip, dashboard burn-rate widget, Applied cost cell, Materials breakdown, notify-stats projection) sum directly from `cost_log` via `findajob.cost_rollups` helpers. If a new surface needs cost data, add a helper to `cost_rollups.py` so the math stays in one place.

### Synthetic Jobs Convention (Speculative Cold-Outreach)

Some `jobs` rows are *synthetic* — produced by the speculative ingest path for cold-outreach. **Canonical signal:** `jobs.synthetic=1` + `source='web_speculative'` + `[SPEC] ` title prefix.

Two invariants worth restating because they bite if broken — synthetic rejections never feed the scorer (`feedback_log` skips them), and `prep_application.py` reuses the speculative briefing rather than running `company_researcher`. The rest is enforced in code (`findajob.speculative.approver`, `handle_rejection`, `_build_feedback_block`, role-prompt branching on `<<SPECULATIVE_MODE>>`). Full spec in operator-private notes.

### Abbreviation Clarifications
Internally-branded teams, programs, or org names with ambiguous abbreviations must be spelled out in role prompts; LLMs will misinterpret them otherwise. Installation-specific clarifications live in CLAUDE.local.md.

### Company Discovery is a Parallel Signal
`config/roles/company_discoverer.md` runs weekly via supercronic and after onboarding completion. It emits `candidate_context/discovered_companies.md` + `.json` (gitignored), read by the scorer and Greenhouse-slug derivation as INPUTS, not floors. The static `## Target Companies / Organizations` section in profile.md remains as a strategic-preference signal — orthogonal to the competency-fit signal the discoverer produces. Do not delete the static list to "consolidate"; they serve different purposes.

### JD at Prep Time
`prep_application.py` reads JD from the database. Never re-curls the URL at prep time.

### company_match() Discipline
Two regression-prone rules every `company_match()` implementation must observe:

1. **Blank-string guard.** `connections.csv` may have blank-company rows. `'' in 'anything'` is True in Python — without the guard, every blank-company row false-matches. Required: `if not s or not c: return False`.
2. **Word-boundary matching, not substring containment** (#497). Use `re.search(rf"\b{re.escape(needle)}\b", haystack)` (bidirectional), not `needle in haystack`. Substring `in` matches "Apple" inside "GreenApple" and "AI" inside "AIRBUS"; word boundaries don't.

Ingest (`triage/contacts.py`) and prep (`find_contacts.py`) share the single canonical `company_match()` — ingest delegates to it (#963). Do not reintroduce a separate matcher in the triage path; a divergent copy is what let the substring bug survive there after #497 fixed prep. (The rejection detector's `_company_match()` is intentionally a different, fuzzy algorithm — leave it.)

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
and `src/findajob/ingest.py` (the `/ingest/` web form) use the centralized helpers from
`findajob.cleaning` — do not reintroduce drifted local `_normalize`/`fingerprint` copies.

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
handle_withdraw_as_fallback, mark_as_fallback, promote_from_fallback,
promote_to_scored, notify_waitlist_resurface, reset_prep_to_scored) and
responds in the same request — no poll cycle, no mirror table.

| Action | Endpoint | Where it lives |
|---|---|---|
| Flag for Prep | `POST /board/jobs/{fp}/prep` | Dashboard dropdown. Launches Phase A only (`--phase=a`) — company research, briefing, fit analysis. Operator continues to Phase B from the briefing-first gate at `/materials/{fp}/`, or rejects with a substantive reason. `scripts/prep_application.py` retains `--phase=all` as the CLI default for cron and manual invocations (#691). |
| Continue prep | `POST /board/jobs/{fp}/continue-prep` | HTMX-shaped dashboard endpoint (returns `<tr>` for outerHTML swap). Reserved for future dashboard dropdown affordance. The materials-page UI uses `POST /materials/{fp}/continue-prep` (303-redirect wrapper) instead — same gates, same Phase B subprocess, but page-shape-correct for the server-rendered materials view (#691). |
| Continue prep from materials | `POST /materials/{fp}/continue-prep` | Briefing-first gate panel on `/materials/{fp}/` when stage is `briefing_ready` (#691). 303-redirect wrapper around the dashboard route — same idempotency + spend-ceiling + queue-cap gates, dispatches `prep_application.py --phase=b`. Queue-full / spend-ceiling refusals 303-redirect to `/materials/?continue_prep_error={queue_full,spend_ceiling}`. |
| Reject from materials | `POST /materials/{fp}/reject` | Briefing-first gate reject affordance (#691). 303-redirect wrapper around `handle_rejection` (writes `feedback_log`, moves folder to `_rejected/`, fires `notify_waitlist_resurface`). Idempotent on already-rejected rows. |
| Regenerate | `POST /board/jobs/{fp}/regenerate` | Dashboard dropdown — gated by `GET /board/jobs/{fp}/regenerate/confirm` modal (#700); Cancel restores cell via `GET /board/jobs/{fp}/regenerate/cell` |
| Applied | `POST /board/jobs/{fp}/apply` | Dashboard dropdown. Response carries the `_undo_toast.html` partial as `hx-swap-oob` into `#undo-toast` (#699). |
| Un-apply | `POST /board/jobs/{fp}/un-apply` | Undo button inside the 30s undo toast (#699). 409 once the audit_log row '… → applied' is older than 30 seconds — gate is SQL-side `datetime('now', '-30 seconds')` for clock-drift safety. |
| Waitlist | `POST /board/jobs/{fp}/waitlist` | Dashboard dropdown + Review tab button (#702) |
| Reject (w/ reason) | `POST /board/jobs/{fp}/reject` | Dashboard / Review / Waitlist reject cell |
| Interviewing | `POST /board/jobs/{fp}/interview` | Applied dropdown |
| Re-run interview prep | `POST /materials/{fp}/rerun-interview-prep` | Materials page button (when stage=interview). Same subprocess as `/interview` re-click; per-job concurrency guard via `background_tasks`; spend ceiling gate. 303-redirects to `/materials/{fp}/` (#875). |
| Generate study guide | `POST /materials/{fp}/study-guide` | Materials page button (when stage=interview). On-demand single-artifact generation reusing existing prep artifacts; synchronous (`writeback_sync`) `background_tasks` row keyed `kind='study_guide'`; per-job concurrency guard + spend ceiling gate; non-destructive (timestamped output). 409 off interview stage / missing briefing+interview-prep. Shared launcher `_launch_study_artifact` with flashcards. 303-redirects to `/materials/{fp}#study-materials` (#873). |
| Generate flashcards | `POST /materials/{fp}/flashcards` | Materials page button (when stage=interview). Same launcher as study guide, `kind='flashcards'`; builds the Anki/CSV/JSON deck via `generate_flashcards_for_job`. 303-redirects to `/materials/{fp}#study-materials` (#873). |
| Un-interview | `POST /board/jobs/{fp}/un-interview` | Applied dropdown (when stage=interview). Restores prior stage from audit_log (fallback `applied`). Row stays on Applied tab; OOB stage-change toast. |
| Offer | `POST /board/jobs/{fp}/offer` | Applied dropdown |
| Withdrew | `POST /board/jobs/{fp}/withdraw` | Applied dropdown |
| Withdrew (Fallback) | `POST /board/jobs/{fp}/withdraw-as-fallback` | Applied dropdown. Sets stage=`withdrawn_fallback`, stores reason="Better opportunity", fires `notify_waitlist_resurface`. No folder move (#358). |
| Not Selected (w/ reason) | `POST /board/jobs/{fp}/not-selected` | Applied dropdown + reject cell |
| Promote | `POST /board/jobs/{fp}/promote` | Review button |
| Reactivate | `POST /board/jobs/{fp}/reactivate` | Waitlist button |
| Reactivate and prep | `POST /board/jobs/{fp}/reactivate-and-prep` | Waitlist tab "Flag for Prep" button (#702). Same spend-ceiling + queue-cap gates as `/prep`. Atomically claims the job straight from `waitlisted → prep_in_progress` (the prep claim is the first mutation, #957), relocating the prep folder inline — a single audit row, not the former two-row `waitlisted → intermediate → prep_in_progress` trail. |
| Change reject reason | `POST /board/jobs/{fp}/change-reject-reason` | Rejected tab inline dropdown (#697) |
| Un-not-selected | `POST /board/jobs/{fp}/un-not-selected` | Not Selected tab button (#698) |
| Change not-selected reason | `POST /board/jobs/{fp}/change-not-selected-reason` | Not Selected tab inline dropdown (#698) |
| Mark as Fallback | `POST /board/jobs/{fp}/mark-as-fallback` | Archive actions cell (#358). Converts withdrawn → withdrawn_fallback. No folder move. |
| Promote from Fallback | `POST /board/jobs/{fp}/promote-from-fallback` | Fallback tab button (#358). Restores prior stage from audit_log. Clears reject_reason. |
| Un-withdraw | `POST /board/jobs/{fp}/un-withdraw` | Archive actions cell (#701) |
| Reattribute | `POST /board/jobs/{fp}/reattribute-from-archive` | Archive reattribute modal (#701) |
| Edit user_notes | `POST /board/jobs/{fp}/notes` | Notes input on any tab that surfaces the column (Dashboard / Review / Waitlist / Fallback / Applied). 800ms debounce. Blur writes `notes_history`; keyup only writes `jobs.user_notes`. |
| Confirm rejection email | `POST /board/rejections-review/{id}/confirm` | Rejections-review queue (#362) |
| Dismiss rejection email | `POST /board/rejections-review/{id}/dismiss` | Rejections-review queue (#362) |
| Reattribute rejection email | `POST /board/rejections-review/{id}/reattribute` | Rejections-review queue (#362) |

The rejections-review row is keyed by `rejection_suggestions.id` rather than `jobs.fingerprint` — the suggestion is the source row, the job_id is found via `matched_job_id` (or operator-supplied on reattribute). Confirm/reattribute call `handle_not_selected(..., changed_by='gmail_rejection_detector')` so the audit trail tags the transition.

**REJECT_REASON dropdown**: vocabulary is per-stack configurable via `config/reject_reasons.yaml`; defaults to a field-agnostic list in `findajob.config_loader._DEFAULT_REJECT_REASONS`, hot-reloaded per request. A "title-signal" subset (declared in the same YAML, defaults in `_DEFAULT_TITLE_SIGNAL_REASONS`) feeds `analyze_feedback._prefilter_candidates` so the scorer learns from rejections where the title alone was a tell. Behavior depends on STATUS:
- If STATUS = `Not Selected`: company rejection → `stage=not_selected`, NO `feedback_log`, folder stays in `_applied/` with `NOT_SELECTED_` marker file
- Otherwise: user rejection → `stage=rejected`, writes `feedback_log`, moves folder to `_rejected/`

**Stage `waitlisted`:** Set by `POST /board/jobs/{fp}/waitlist`. Folder moves to `companies/_waitlisted/`. Not a rejection — does not write to feedback_log or contaminate scorer feedback loop. When an active application at the same company is rejected/withdrawn, ntfy notification surfaces waitlisted jobs.

**Stage `withdrawn_fallback`:** Set by `POST /board/jobs/{fp}/withdraw-as-fallback` (from Applied dropdown) or `POST /board/jobs/{fp}/mark-as-fallback` (from Archive, converting existing withdrawn rows). Folder stays in `companies/_applied/` — no folder move. Not a rejection — does not write to `feedback_log`. Withdraw reason stored in `reject_reason` column (stage-disjoint from rejected/not_selected). `notify_waitlist_resurface()` fires on the withdraw-as-fallback route. The Fallback tab (`/board/fallback`) surfaces all `withdrawn_fallback` rows with a Promote button that restores the prior stage via `audit_log` lookup.

**Stage `not_selected`:** Set by `POST /board/jobs/{fp}/not-selected`. Only valid for post-application stages (`applied`, `interview`, `offer`); 409 otherwise. Folder stays in `companies/_applied/` with a `NOT_SELECTED_{reason}_{date}.txt` marker file. Does NOT write to `feedback_log` — company rejections must not contaminate the scorer's feedback loop. `notify_waitlist_resurface()` still fires.

**Stage `prep_in_progress`:** Set by `POST /board/jobs/{fp}/prep` or `POST /board/jobs/{fp}/continue-prep` immediately before launching `prep_application.py` as a subprocess. Prevents duplicate prep runs via a single atomic claim — `findajob.web.routes.board_actions._claim_prep_slot` does one conditional `UPDATE … WHERE stage <claimable> AND (in-flight count) < cap` so a double-click / HTMX retry can win at most one slot, and the 3-job concurrency cap holds under burst (#957). The claim is the first state mutation in every prep-launching handler (`prep`, `regenerate`, `continue_prep`, `reactivate_and_prep`, and the `/materials/` wrappers), so folder ops and the subprocess launch run only for the winner. On success the route-spawned phase clears to its own exit stage: `/prep` (Phase A only) clears to `briefing_ready`; `/continue-prep` (Phase B) clears to `materials_drafted`. `scripts/watchdog.py` rolls any job stuck > 60 min back: `kind='prep'` rows reset to `scored` (via `reset_prep_to_scored`); `kind='prep_phase_b'` rows reset to `briefing_ready` (via `reset_prep_to_briefing_ready`) so the operator can re-try Phase B without re-paying Phase A (#691). A `LLMSpendCeilingExceeded` breach or a fail-closed ceiling-gate `sqlite3.OperationalError` *during* prep is caught in-process by both phase handlers (`_handle_prep_runtime_failure` for Phase A → `scored`; `_handle_phase_b_failure` for Phase B → `briefing_ready`) and resets immediately with a `.failed_prep` sentinel + `prep_failure` ntfy, rather than waiting on the watchdog; the watchdog stays the backstop for genuine hangs (#956).

**Stage `briefing_ready`:** Set by `_run_prep_phase_a` at Phase A completion. The briefing folder is written to disk, `fit_score` + `probability_score` are stored in the DB, and the operator decides via `/materials/{fp}/` whether to continue (POST `/continue-prep`) or reject (POST `/reject` with a substantive reason — `handle_rejection` writes `feedback_log` as usual). Applied is also reachable from the Dashboard dropdown for rows at this stage — operator skips Phase B and applies with what Phase A produced (the briefing folder is moved + snapshotted into `_applied/` by `_move_folder_to_applied` like any other apply transition). `scripts/watchdog.reap_briefing_ready_stale` resets rows older than 48h to `scored` *without* nulling `prep_folder_path`, so a re-flag resurfaces the existing briefing rather than re-paying Phase A.

**Health checks** (`notify.py health-check`): warns if manual_review backlog > 100, a source silently stopped producing jobs, or any target-company job scored 3–6 in last 7 days (potential mis-scores).

### Gmail Integration

Gmail ingestion uses IMAP + app password, configured per-stack at `/config/gmail/`. Transparency contract codified as executable assertions in `tests/test_transparency_invariants.py` — failures there mean the disclosure banner is lying.

The same IMAP integration also drives **rejection detection** (#362): every 30 minutes, `scripts/detect_rejections.py` scans Gmail against `config.rejection_sender_allowlist` (Greenhouse, Ashby, Lever, Workday-style ATS senders) and writes pending rows to `rejection_suggestions` for operator review at `/board/rejections-review/`. Cron entry `detect-rejections` in `ops/scheduled-jobs.yaml`. Operator confirms via the review-queue UI; never auto-flips. Spec: §4.x of `docs/superpowers/specs/2026-05-01-362-rejection-detection-design.md` (operator-private). Company-name aliases live in `config/company_aliases.yaml` (allowlisted in `/config/`; matcher hot-reloads on every cycle).

### Auth Gate Must Be Verified Post-Deploy

After every `docker compose up -d` on any stack, the basic-auth gate must be verified by running `python -m findajob.web.verify_auth` (image-baked) inside the running container. If the verifier exits non-zero, the stack is taken down until fixed. **No exceptions** — including hotfixes, rollbacks, and one-off restarts.

Exit codes: 2 = `FINDAJOB_AUTH_USER`/`FINDAJOB_AUTH_PASS` empty; 3 = anonymous request didn't get `401 + WWW-Authenticate: Basic`; 4 = authenticated request didn't get `200`; 5 = network failure.

A stack that intentionally has no app-level auth (e.g., behind an internal-mesh perimeter) will fail with exit 2 — that's the signal to either configure auth or document the explicit exception in CLAUDE.local.md.

---

## Implementation Guardrails

Code-style patterns, required-tests boundaries, PR-vs-main flow, branching, and the `migration-required` label all live in [`CONTRIBUTING.md`](CONTRIBUTING.md). Read it before any non-trivial change. The rules in CLAUDE.md and CONTRIBUTING.md are the same rules — CONTRIBUTING.md is the canonical version.

The one rule worth restating here because it bites often: **Same-PR docs rule.** When code touches a documented surface, update the docs in the same PR. Schema → CHANGELOG `### Migration required` entry; new env var → `configure.md`; new state transition → the Board Routes table above.

---

## Project Board, Plans, Releases

- **Project board** — GitHub Projects v2 at https://github.com/users/brockamer/projects/1 is the single source of truth. Not on the board = not on the roadmap. Conventions in [`docs/project-board.md`](docs/project-board.md) (also jared's config file — the machine-readable header block is parsed on every board operation). Use the `/jared file` skill instead of manual `gh` calls — issue creation requires both `gh issue create` AND `gh project item-add` (new issues do not auto-add).
- **Plans, specs, experiments** — gitignored under `docs/superpowers/`. Content conventions in [`## Plan Structure`](#plan-structure) below. A plan without a **Documentation Impact** section is incomplete — push back rather than execute it.
- **Releases** — Docker image tagged from main; CHANGELOG.md is the release-notes source. PRs with schema / config / crontab / mount / compose changes get `migration-required` at PR-open time.

---

## Plan Structure

<!-- Absorbed from docs/maintainers/plan-conventions.md, 2026-05-27 -->

Implementation plans live in an operator-private location (`docs/superpowers/plans/` — gitignored; files on disk but not tracked, per #430). They are the bridge between a brainstormed spec and the actual commits. The storage location is operator-private; the content discipline below is unchanged.

### Required sections

Every plan must include the sections below. Skipping one is a smell — push back rather than write a plan that hides scope.

1. **Goal + scope** — One paragraph: what's being built and why. One paragraph: what's intentionally NOT in scope (and links to the issues that cover the deferred work).
2. **Tasks** — Numbered, bite-sized tasks. Each spells out **Files** to create/modify, **Steps** as a checklist, **Verification** commands and their expected outputs, and a **Commit message** body. Prescriptive enough that a fresh subagent can execute the task without re-reading the spec.
3. **Documentation Impact** — **Mandatory, even if the answer is "none."** For each documentation surface the work touches, name the file and the change: `README.md` (install path, tech stack, quick-start), `docs/getting-started/*.md`, `CLAUDE.md` / `CLAUDE.local.md`, `CHANGELOG.md`, the spec doc in `docs/superpowers/specs/` (does this plan amend the original spec?), in-code docstrings. If an item belongs to a follow-up issue, name the issue. If no docs are touched, write "None — no user-visible or developer-facing surface changes." Don't leave it empty.
4. **Verification gate** — The smoke checks, integration tests, or manual validations that must pass before the PR opens. Distinct from per-task verification — this is the whole-feature acceptance gate.
5. **Self-review checklist** — Spec coverage map (every spec section → tasks that implement it), placeholder scan (no `TBD`/`TODO` left), type/contract consistency across files.

**Why Documentation Impact is required:** doc drift is silent. Code reviews catch behavioral bugs but rarely a stale README or a CLAUDE.md table listing the wrong scheduler. Enumerating every surface before implementing turns "doc updates" from an afterthought into part of the task list.

### Storage and naming

`docs/superpowers/plans/YYYY-MM-DD-short-feature-slug.md` — one plan per feature, dated for ordering. A mid-implementation handoff uses the `-CHECKPOINT.md` suffix and is deleted once the next session resumes.

### Relationship to specs

Specs (`docs/superpowers/specs/`) describe **what** and **why** — the design + decision-log artifact from brainstorming. Plans describe **how** — concrete tasks with verifications. A spec without a plan can't be executed; a plan without a spec usually means the design wasn't really thought through. When a plan reveals a flaw in the spec, fix the spec in the same PR (often via a "Decisions made during implementation" subsection appended to the spec). Don't let plan and spec drift.

---

## Working Style

- Use paths from `findajob.paths`. No placeholders in commands.
- Preserve the scheduler-driven daily run in all changes.
- Working features first, polish later.

---

## Scripts Reference

<!-- Absorbed from docs/operations/README.md, 2026-05-30 -->

All scripts live in `scripts/`. Diag scripts live in `scripts/diag/` and are run manually only. All scripts import `BASE` and `PANDOC` from `findajob.paths` (`src/findajob/paths.py`). Never hardcode binary paths in scripts — add overrides to `config/paths.env` instead.

**Docker vs Fly:** Docker: `docker compose exec scheduler <cmd>` · Fly: `fly ssh console --app <app> --command "<cmd>"`

### Core pipeline scripts

#### `triage.py`
**Run by:** scheduler (daily 00:00 PT). No arguments.
**Manual run:** `docker compose exec scheduler python3 scripts/triage.py`

Fetches jobs from all sources, deduplicates, enriches with JD text, then scores with LLM in parallel (6 concurrent threads), writes to SQLite.

**Sources:**
- LinkedIn / Indeed via RapidAPI jobs-api14 + JSearch (per `config/active_sources.txt`).
- Gmail IMAP (LinkedIn job alerts, Indeed digests, recruiter messages — config at `/config/gmail/`).
- Greenhouse / Lever / Ashby JSON APIs (slugs / URLs in `config/feed_urls.txt`).

**Key events logged:** `triage_started`, `job_ingested`, `job_deduplicated`, `job_scored`, `pipeline_complete`.

#### `scripts/prep_application.py` (entry-point shim)
*Entry-point shim; implementation in `src/findajob/prep/orchestrator.py`.*

**Run by:** `POST /board/jobs/{fp}/prep` or `/regenerate` (detached subprocess); also callable manually. Args: `company title url job_id`.
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

After completion: updates DB to `stage=materials_drafted`, sends ntfy notification.

#### `watchdog.py`
**Run by:** scheduler (every 10 min). No arguments.
**Manual run:** `docker compose exec scheduler python3 scripts/watchdog.py`

Resets any job stuck in `stage='prep_in_progress'` for more than 60 minutes back to `scored`. Calls `findajob.actions.reset_prep_to_scored()` which writes an `audit_log` row and emits `prep_failed_reset`. Emits a `watchdog_run` summary event at the end of each run.

#### `notify.py`
**Run by:** scheduler (5 subcommands; see `docs/operations/README.md` → Notifications for the per-subcommand schedule and content).
**Manual run:** `docker compose exec scheduler python3 scripts/notify.py <subcommand>`

#### `scripts/find_contacts.py` (entry-point shim)
*Entry-point shim; implementation in `src/findajob/find_contacts.py`.*

**Run by:** `scripts/prep_application.py` (step 5). Args: `company jd_text_excerpt outdir`.
**Manual run:** `docker compose exec scheduler python3 scripts/find_contacts.py "Acme" "<jd-excerpt>" companies/<folder>`

Reads `data/connections.csv`, finds LinkedIn connections at the target company, generates personalized outreach drafts via the OpenRouter wrapper.

**Output:** `{outdir}/outreach_{FirstName}_{LastName}.txt` for each match.

**Key guard:** `company_match()` always checks `if not s or not c: return False` — blank company strings would otherwise match everything.

#### `manual_prep.py`
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

#### `rescore_all.py`
**Run by:** manually (after model or prompt changes). No arguments.
**Manual run:** `docker compose exec scheduler python3 scripts/rescore_all.py`

Re-runs the scorer on all jobs that have JD text.

#### `rename_folders.py`
**Run by:** manually. No arguments.
**Manual run:** `docker compose exec scheduler python3 scripts/rename_folders.py`

Renames `companies/` folders from old format (`{Company}_{date}_{time}`) to new format (`{Company}_{AbbrevTitle}_{date}_{time}`). Looks up DB for title, updates `prep_folder_path` in DB. Safe to re-run.

#### `init_db.py`
**Run by:** once on new install. No arguments.
**Manual run:** `docker compose exec scheduler python3 scripts/init_db.py`

Creates `data/pipeline.db` with all tables. Safe to re-run — uses `CREATE TABLE IF NOT EXISTS`.

### Diag scripts (`scripts/diag/`)

Run manually for debugging. Not part of normal pipeline operation.

#### `debug_contacts.py`
Shows contact matching diagnostics for a batch of jobs. Useful for debugging false positive/negative company-name matches.
**Manual run:** `docker compose exec scheduler python3 scripts/diag/debug_contacts.py`

@CLAUDE.local.md

