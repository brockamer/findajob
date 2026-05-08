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
`docs/maintainers/plan-conventions.md` for what every plan must contain (the *content* discipline
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
file. If you're refactoring an old hardcoded section, add a note to `docs/maintainers/generalization.md`.

### See also
- `docs/maintainers/generalization.md` — tracks every remaining piece of domain-locked content and the plan to neutralize it
- `docs/getting-started/configure.md` — how to configure the pre-commit hook and set up personal config files

---

## Pipeline Context

Per-role model assignments, container path shifts, and pipeline plumbing reference: [`docs/maintainers/pipeline-context.md`](docs/maintainers/pipeline-context.md). Read it when working on a specific role, fetcher, or path question.

The pipeline is Docker-only: image `ghcr.io/brockamer/findajob`, supercronic + uvicorn co-process inside one container, paths under `/app/...` (override via `JSP_BASE`). All scripts use `findajob.paths.BASE` — never hardcode `/home/...` or `/app/`. All LLM calls go through `findajob.llm.openrouter.complete()`.

---

## Data Ownership

Per-path classification (source, backup-critical, rebuildable) lives in [`docs/maintainers/data-ownership.md`](docs/maintainers/data-ownership.md). The data layer is the only thing `docker compose pull` + a fresh interview can't regenerate.

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

**`/config/`** — in-browser editor for editable pipeline config; allowlist in `findajob.web.config_files`. No per-user authorization inside findajob — perimeter is the boundary. Default perimeter is a VPN-only mesh; internet-exposed instances additionally require HTTP Basic Auth via `FINDAJOB_AUTH_USER` / `FINDAJOB_AUTH_PASS` (see `findajob.web.auth` and `docs/operations/internet-exposure.md`).

**`/settings/`** — domain-aware config editors with rich UX. First occupant: `/settings/reject-reasons/` (#490) — editable rows + per-row `title-signal` checkbox for `config/reject_reasons.yaml`. Distinguished from `/config/` (raw text editor for any allowlisted file): `/settings/` has per-page UX tailored to the config it edits (validation, structured rows, HTMX partial-swap save flow). Saves take effect on the next request without container restart — `findajob.config_loader.load_reject_reasons` is no-cache and `ColumnSpec.enum_values` accepts a callable so dropdown + filter chip values both refresh per request. Future similar editors (e.g., `prefilter_rules.yaml`, `in_domain_patterns.yaml`) live here.

**`/onboarding/`** — first-run NUX. Two-step structure:

- **Step 1 — API keys.** Tester provides own OpenRouter (required) + RapidAPI (optional, `RAPIDAPI_KEY` per #414). Collected via `POST /onboarding/keys`; live OpenRouter smoke check at collection. Help: `docs/getting-started/api-keys.md`.
- **Step 2 — Run the interview.** In-app chat surface, server-side persistent (`onboarding_sessions` table). Funded by the tester's own OpenRouter key — no operator-funded fallback. ~$3–6 per onboarding for Sonnet 4.6 with prompt caching; voice-samples emission dominates the cost in long interviews.

Module surface: `findajob.onboarding.{parser,injector,voice_processor,openrouter_smoke,session_store,interview_runner,key_validation}`. Routes: `findajob.web.routes.onboarding_interview`. The `findajob.web.onboarding_guard` dependency redirects `/`, `/board/*`, `/materials/*`, `/stats/*` to `/onboarding/` while `data/.onboarding-complete` is missing. Re-trigger via `/onboarding/?mode=rerun`. The injector writes ~10 canonical files atomically with backups under `.backups/{UTC-stamp}/`; the sentinel itself is written only by the Gmail-config gate (`/onboarding/gmail-config/{sid}/finish` or `/skip`) per #407.

**Per-stack key isolation invariant (#339):** every tester stack's `data/.env` carries only that tester's credentials. `migrate_schema()` in `session_store.py` runs idempotently at app startup.

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
Every LLM call goes through `findajob.llm.openrouter.complete()`, which writes `cost_log.cost_usd` from OpenRouter's `response.usage.cost` (authoritative — no heuristic, no calibration, no multiplier). UI surfaces (nav spend chip, dashboard burn-rate widget, Applied cost cell, Materials breakdown, notify-stats projection) sum directly from `cost_log` via `findajob.cost_rollups` helpers. If a new surface needs cost data, add a helper to `cost_rollups.py` so the math stays in one place.

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

After every `docker compose up -d` on any stack, the basic-auth gate must be verified by running `python -m findajob.web.verify_auth` (image-baked) inside the running container. If the verifier exits non-zero, the stack is taken down until fixed. **No exceptions** — including hotfixes, rollbacks, and one-off restarts.

Exit codes: 2 = `FINDAJOB_AUTH_USER`/`FINDAJOB_AUTH_PASS` empty; 3 = anonymous request didn't get `401 + WWW-Authenticate: Basic`; 4 = authenticated request didn't get `200`; 5 = network failure.

A stack that intentionally has no app-level auth (e.g., behind an internal-mesh perimeter) will fail with exit 2 — that's the signal to either configure auth or document the explicit exception in CLAUDE.local.md. Applies to every stack, operator-only or tester.

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

Canonical conventions live in [`docs/maintainers/project-board.md`](docs/maintainers/project-board.md). Read it before any work that creates, moves, or reprioritizes issues. That doc covers columns, Priority field, labels, triage checklist, and `gh project` CLI IDs.

Core rules (enforced — see the doc for detail):
- Creating an issue is **two steps**: `gh issue create` then `gh project item-add 1 --owner brockamer --url <url>`. New issues do not auto-add. The `/jared file` skill atomizes this — prefer it over manual `gh` calls.
- Every open issue on the board must have **Priority** (High/Medium/Low) set.
- Speculative far-horizon ideas get the `big-idea` label and Priority: Low — keeps them on the board without cluttering the active roadmap.
- `priority: high/med/low` labels are legacy — the **Priority field** is canonical. Reconcile mismatches.
- In Progress should hold 1–3 items max. If more, focus is scattered.
- Status transitions: Backlog → Up Next → In Progress → Done. Closing an issue auto-moves to Done; verify after closing.
- Re-sync board state before changing it — other sessions may have updated it.

**When board usage evolves** (new column, new label, new workflow, new convention): update `docs/maintainers/project-board.md` in the same change. The doc describes how the board actually works, not how it used to work. Behavior drifting ahead of docs is the main failure mode.

---

## Plan Conventions

Implementation plans live in an operator-private location (`docs/superpowers/plans/` is gitignored — files exist on disk for session use, but are not tracked). Conventions for plan *content* are documented in [`docs/maintainers/plan-conventions.md`](docs/maintainers/plan-conventions.md).

**Hard requirements for every plan:**
- Numbered tasks with files, steps, verification commands, commit messages
- A **Documentation Impact** section enumerating every doc surface that needs to change (README, docs/getting-started/*, CLAUDE.md, CHANGELOG.md, spec doc, docstrings). If none, say "None" — never omit the section
- A whole-feature verification gate distinct from per-task checks
- A self-review checklist mapping every spec section to its implementing task(s)

A plan without Documentation Impact is incomplete — push back rather than execute it.

---

## Release Management

Docker image releases follow [`docs/maintainers/release-process.md`](docs/maintainers/release-process.md). Claude owns orchestration (dogfood gate, CHANGELOG drafting, tag cut, post-tag verification, rollback); the user reviews and approves the proposed cut. The dogfood gate is a binary 48h window on `:latest` — six observable signals must all be clean before any `v*.*.*` tag is pushed. PRs containing schema/config/crontab/mount/compose-down changes get the `migration-required` label at PR-open time so that release notes surface them for external users.

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

