# Pipeline Context

Reference table for Claude Code sessions and contributors. Lists the model assignment for every LLM-driven role plus the canonical paths and conventions the pipeline depends on.

## Models per role

| Role | Model | Notes |
|------|-------|-------|
| Default | `openrouter:google/gemini-3-flash-preview` | |
| `job_scorer` | `openrouter:deepseek/deepseek-v3.2` | profile.md injected directly; `--rag` NEVER used |
| `resume_tailor` / `cover_letter_writer` | `openrouter:anthropic/claude-opus-4.7` | `max_tokens: 4096` |
| `briefing_writer` | `openrouter:anthropic/claude-opus-4.7` | cascades into `resume_tailor` + `cover_letter_writer` |
| `outreach_drafter` | `openrouter:anthropic/claude-opus-4.7` | profile + voice samples injected directly |
| `recruiter_critic` | `openrouter:anthropic/claude-opus-4.7` | `max_tokens: 1024`; sees company, title, JD, tailored resume, cover; NOT profile/briefing/fit |
| `interview_prep` | `openrouter:anthropic/claude-opus-4.7` | `max_tokens: 4096`; fires on `applied → interview` |
| `company_discoverer` | `openrouter:perplexity/sonar-reasoning-pro` | weekly Sun 02:00; emits `candidate_context/discovered_companies.md` + `.json`; field-agnostic, augments static `## Target Companies` |
| `company_researcher` | `openrouter:perplexity/sonar-reasoning-pro` | |
| `fit_analyst` | `openrouter:perplexity/sonar-reasoning-pro` | appended to company briefing |
| `candidate_led_briefing` | `openrouter:perplexity/sonar-deep-research` | async (1–5 min); drives speculative briefing pass |
| `speculative_roles_synth` | `openrouter:anthropic/claude-sonnet-4.6` | `max_tokens: 4096`; synthesizes 1–5 candidate-tailored role cards |
| `resume_change_reviewer` / `network_analyst` | `openrouter:google/gemini-3-flash-preview` | |

## Pipeline plumbing

| Item | Value |
|------|-------|
| Job ingestion | Pluggable via `JobSourceAdapter` (`src/findajob/fetchers/adapters/`); jobs-api14 + JSearch ship in v0.14; per-stack active list in `config/active_sources.txt`. Greenhouse / Ashby / Lever / Gmail still function-style — migration tracked in #410. v0.15 adds `JobsApi14IndeedAdapter` (Indeed via jobs-api14 with sortType=date + post-filter). RapidAPI credentials consolidated to `RAPIDAPI_KEY` (legacy `JOBS_API14_KEY` / `JSEARCH_API_KEY` work as fallbacks) per #414. |
| Cost tracking | Every LLM call writes `cost_log.cost_usd` from `response.usage.cost` (OpenRouter authoritative; no heuristic, no calibration). `findajob.cost_rollups` helpers (`per_job_cost`, `per_job_breakdown`, `weekly_spend`, `projected_monthly`, `spend_this_month`) sum directly from `cost_log` to back the nav spend chip, dashboard burn-rate widget, Applied cost cell, Materials breakdown, and notify-stats projection. |
| Per-prep cost projection | `findajob.prep.cost_projection.compute_projection` runs at `_run_prep_phase_a` start and emits a `prep_cost_projection` event to `pipeline.jsonl` with `projected_cost_usd` (sum of trailing-30d per-`(role, model)` medians for the 8 prep roles), `expensive_role`, and `ceiling_usd` (1.5x trailing-30d median full-prep cost, scoring excluded). When projection > ceiling, an additional `prep_cost_projection_high` event fires — non-blocking, the operator wanted early warning not a gate. Cold start (no `cost_log` history) emits the event with `None` sentinels and `n_roles_with_history=0`. Catches per-prep cost creep earlier than "operator notices an outlier in the burn-rate widget" (#713). |
| Package manager | `uv sync` for dev deps; `uv run` prefix for pytest/ruff/mypy/uvicorn |
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

## Container path shifts

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

## When authoring scripts or tests

- Always use `findajob.paths.BASE` — never hardcode `/home/...` or `/app/`.
- Binary subprocess calls go through `PANDOC` from `findajob.paths`.
- LLM calls go through `findajob.llm.openrouter.complete()`.
- Tests must not depend on absolute paths — use tmpdirs or `BASE`-relative paths.
