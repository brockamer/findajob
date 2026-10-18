# findajob — agent instructions

Read at session start by Claude Code (through `CLAUDE.md`'s import) and by other coding agents. Authoritative context for this codebase.
Personal identifiers (name, targets, API topic, form URLs) live in `CLAUDE.local.md` (gitignored).

This file holds the rules that apply to every change. Task-specific reference lives in the docs listed under [`## Key File Locations`](#key-file-locations) — read the one that matches the task before editing that area.

---

## Self-Governance — Check Before Every Command

Before writing any command, path, binary call, or file location:

- [ ] All binary paths come from `findajob.paths` — `PANDOC`, `BASE`. Never hardcode.
- [ ] For subprocess calls to other pipeline scripts, use `sys.executable` (never a hardcoded Python path).
- [ ] LLM calls go through `findajob.llm.openrouter.complete()`. Never re-introduce a subprocess transport.

**If uncertain about any value: say so. Do not guess.**

---

## Commands

CI (`.github/workflows/ci.yml`) runs these four; run them all before pushing.

- `uv sync --extra dev` — install findajob (editable) + dev deps. A plain `uv sync` removes the dev deps: they are the optional `dev` extra.
- `uv run ruff check src/ scripts/ tests/` and `uv run ruff format --check src/ scripts/ tests/`
- `uv run mypy src/findajob/`
- `uv run pytest tests/ -v`

---

## PII and Domain-Neutrality

The repo is public. Tracked files must not contain personal identifiers (real names, emails, API keys) or content that locks the pipeline to one career field. The actual enforcement layer is `.git/hooks/pre-commit` — see `docs/operations/config-reference.md` for setup. The hook is not tracked; each clone installs its own and extends `PATTERNS` when new identifiers appear.

Two categories the hook can't fully catch — be deliberate about these:

- **Operator topology** — hostnames, deployment paths (`/opt/stacks/...`), backup destinations, consumer infra brand names (hypervisor / NAS / VPN mesh products), per-stack port numbers, the operator's domain. Setup docs use placeholders: `<deployment-host>`, `<operator-handle>`, `<operator-domain>`.
- **Field-locked content** — hardcoded company lists, single-field title patterns, industry vocabulary in role prompts. Belong in gitignored config (`config/target_companies.md`, `config/prefilter_rules.yaml`) or referenced from the candidate profile, not enumerated in tracked files. Tracking doc: [`docs/maintainers/generalization.md`](docs/maintainers/generalization.md).

Plans, specs, and experiment notes for public features are committed under [`docs/superpowers/`](docs/superpowers/README.md) (#1188). They are public like every other tracked file, so every rule in this section applies to them. Anything about running a specific instance of findajob stays out of this repo — see [`## Project Board, Plans, Releases`](#project-board-plans-releases). Plan-content conventions are in [`docs/superpowers/README.md` § Plan structure](docs/superpowers/README.md#plan-structure).

If you find yourself wanting to put a real name, real employer, real city, or a tech-only example into a tracked file: move it to `CLAUDE.local.md` or a gitignored config and reference it instead.

---

## Pipeline Context

The pipeline is Docker-only: image `ghcr.io/brockamer/findajob`, supercronic + uvicorn co-process inside one container, paths under `/app/...` (override via `JSP_BASE`). All scripts use `findajob.paths.BASE` — never hardcode `/home/...` or `/app/`. All LLM calls go through `findajob.llm.openrouter.complete()`. Per-role models, pipeline plumbing and container path shifts: [`docs/architecture/models-and-paths.md`](docs/architecture/models-and-paths.md).

When authoring scripts or tests:

- Tests must not depend on absolute paths — use tmpdirs or `BASE`-relative paths.

---

## Key File Locations

The full file map lives at [`docs/architecture/file-map.md`](docs/architecture/file-map.md). Update that file when files are added, renamed, or retired.

Reference docs — read the matching one before working in that area:

| Task touches… | Read |
|---|---|
| A role prompt, a model choice, a fetcher, `config/`, container paths | [`docs/architecture/models-and-paths.md`](docs/architecture/models-and-paths.md) |
| Backup, restore, or what state is safe to lose | [`docs/architecture/data-ownership.md`](docs/architecture/data-ownership.md) |
| Web routes, templates, `/settings/`, `/onboarding/`, board filters | [`docs/architecture/web-frontend.md`](docs/architecture/web-frontend.md) |
| A stage transition, a board action, prep launch or reset | [`docs/architecture/board-lifecycle.md`](docs/architecture/board-lifecycle.md) |
| A script in `scripts/` — args, outputs, manual runs | [`docs/architecture/scripts.md`](docs/architecture/scripts.md) |
| System design and data flow (human-oriented overview) | [`docs/architecture.md`](docs/architecture.md) |

### Web Frontend Architecture

Lives at `src/findajob/web/`, one file per URL group in `routes/`. Foundational decisions (design rationale lives in operator-private specs):
- Server-rendered HTML + HTMX (no SPA)
- Grouped URL IA — top-nav = `/`, `/board/`, `/materials/`, `/ingest/`, `/tools/`, `/config/`, `/settings/`, `/docs/`
- Tailwind via CDN + `static/app.css` design tokens
- URL query params for UI state (not cookies/localStorage). The page URL is the only URL the address bar may hold (#1218): never put `hx-push-url` on a filter input.
- Alpine.js added only when ephemeral client state is needed

**Authorization model:** no per-user auth inside findajob — perimeter is the boundary. Default perimeter is VPN-only; internet-exposed instances add HTTP Basic Auth via `FINDAJOB_AUTH_USER` / `FINDAJOB_AUTH_PASS` (see `findajob.web.auth` and [`docs/operations/internet-exposure.md`](docs/operations/internet-exposure.md)). Independently of auth, every `POST`/`PUT`/`PATCH`/`DELETE` passes `findajob.web.middleware.CrossSiteRequestMiddleware`, which rejects requests a browser marks as cross-site (`Sec-Fetch-Site`, then `Origin` vs `Host`); it is always on and sits inside the auth gate. Keep that order when touching `app.py` — `tests/test_web_cross_site.py` pins it. HTMX-only POST routes additionally carry `findajob.web.htmx_guard.require_htmx` (403 without `HX-Request`); **every new unsafe route must be classified** — gate it, or add it to `PLAIN_FORM_ROUTES` in `tests/test_web_htmx_guard.py` when a plain `<form>`, a `fetch` call, or a non-HTMX fallback reaches it. The inventory test fails either way until you do.

---

## Critical Architecture Rules

### Web is the Write Surface
Every STATUS and REJECT_REASON transition runs through a POST handler in
`findajob.web.routes.board_actions` that calls straight into
`findajob.actions`. SQLite is the single source of truth. Do not add
new transition logic to `watchdog.py` or to any out-of-band path — every
new action is a new web handler + a new `findajob.actions` helper.
Which transitions spawn generator subprocesses (prep, interview prep, speculative research) is in [`docs/architecture/board-lifecycle.md`](docs/architecture/board-lifecycle.md).

### Path Resolution
The `PANDOC` binary path comes from `findajob.paths` (`src/findajob/paths.py`), which reads `config/paths.env`.
Never hardcode platform paths in scripts. `BASE` is derived from `__file__` — the repo can live anywhere.
For subprocess calls to other pipeline scripts, always use `sys.executable`, not a hardcoded Python path.
Library code lives in `src/findajob/` (installed editable via `uv sync` — into the project venv for local dev, and into `/app/.venv` in the Docker image via a `uv sync --locked` builder stage — #126, #997). Entry point scripts in `scripts/` import via `from findajob.* import ...`. No `sys.path.insert` hacks.

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
Stage 1: title regex → score 1, no LLM. Location gate (#1217): a US state, country or
non-US region outside `config/target_locations.txt` → score 2, `location_mismatch`, no LLM.
Stage 2: in-domain + no JD → score 5/6, no LLM.
The location gate is a recognizer, not a matcher: it compares states and countries, never
cities, anything it cannot place passes to the scorer, and a target line it cannot read turns
it off. Keep it that way — a wrong Stage-1 reject is never seen by the LLM or the operator.
Never rely on LLM prompt instructions alone for boolean classification tasks.

### Cost Tracking Is Native
Every LLM call goes through `findajob.llm.openrouter.complete()`, which writes `cost_log.cost_usd` from OpenRouter's `response.usage.cost` (authoritative — no heuristic, no calibration, no multiplier). UI surfaces (nav spend chip, dashboard burn-rate widget, Applied cost cell, Materials breakdown, notify-stats projection) sum directly from `cost_log` via `findajob.cost_rollups` helpers. If a new surface needs cost data, add a helper to `cost_rollups.py` so the math stays in one place.

### Rejection-Driven Filter Proposals Are Human-Gated
`analyze_feedback._prefilter_candidates` mines rejection reasons into proposed Stage-1
prefilter regexes; `findajob.filter_proposals` surfaces them in a review queue at
`/board/filter-proposals` (weekly refresh via the notify-feedback cron). Machine-applied
rules live in `prefilter_rules.yaml` `hard_rejects.auto_added` (separate from operator
categories, so revert is surgical). Apply re-scores matching active jobs to 1 (no stage
change, NEVER writes `feedback_log`) so the change is visible immediately, and records
provenance in `config_changes` (`changed_by='auto_tuner'`; revert uses `'auto_tuner_revert'`).
An apply-time danger recompute on the FINAL (possibly operator-edited) pattern forces an
explicit confirm when the rule would hard-reject unactioned score-7+ jobs. Never auto-apply
without the operator click — Stage-1 hard rejects skip the LLM, so a bad rule silently kills
good jobs.

### Synthetic Jobs Convention (Speculative Cold-Outreach)

Some `jobs` rows are *synthetic* — produced by the speculative ingest path for cold-outreach. **Canonical signal:** `jobs.synthetic=1` + `source='web_speculative'` + `[SPEC] ` title prefix.

Two invariants worth restating because they bite if broken — synthetic rejections never feed the scorer (`feedback_log` skips them), and `prep_application.py` reuses the speculative briefing rather than running `company_researcher`. The rest is enforced in code (`findajob.speculative.approver`, `handle_rejection`, `_build_feedback_block`, role-prompt branching on `<<SPECULATIVE_MODE>>`). Full spec in operator-private notes.

### Abbreviation Clarifications
Internally-branded teams, programs, or org names with ambiguous abbreviations must be spelled out in role prompts; LLMs will misinterpret them otherwise. Installation-specific clarifications live in CLAUDE.local.md.

### Company Discovery is a Parallel Signal
`config/roles/company_discoverer.md` runs weekly via supercronic and after onboarding completion. It emits `candidate_context/discovered_companies.md` + `.json` (gitignored), read by the scorer and Greenhouse-slug derivation as INPUTS, not floors. The static `## Target Companies / Organizations` section in profile.md remains as a strategic-preference signal — orthogonal to the competency-fit signal the discoverer produces. Do not delete the static list to "consolidate"; they serve different purposes.

### JD at Prep Time
`prep_application.py` reads JD from the database and never fetches the URL at prep time. A non-synthetic job whose stored JD is under 50 characters fails prep with a `prep_jd_unavailable` event instead. Server-side JD fetches happen at triage only, through `findajob.fetchers.fetch_jd_http` (https, public hosts, redirects re-checked by `findajob.urlcheck`); never shell out to `curl` with a job field.

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

The route table, every stage's folder and notification behavior, and the reject-reason vocabulary are in [`docs/architecture/board-lifecycle.md`](docs/architecture/board-lifecycle.md). The invariants that bite when broken:

- **Scorer feedback is for operator rejections only.** `POST …/reject` → `stage=rejected`, writes `feedback_log`, moves the folder to `_rejected/`. Company rejections (`not_selected`), `waitlisted`, `withdrawn_fallback` and synthetic rows never write `feedback_log`.
- **The prep claim is atomic and first.** Every prep-launching handler starts with `board_actions._claim_prep_slot` — one conditional `UPDATE` that enforces the 3-job concurrency cap — so folder ops and the subprocess launch run only for the winner (#957).
- **Prep failures reset, never strand.** Phase A failure → `scored`; Phase B failure → `briefing_ready` (Phase A is not re-paid). The watchdog is the backstop for hangs > 60 min (#691, #956).
- **A briefing survives a reset.** A stale `briefing_ready` row returns to `scored` with `prep_folder_path` kept, and Flag for Prep resurfaces it through `findajob.actions.resurface_briefing` instead of re-running Phase A (#1222).

### Gmail Integration

Gmail ingestion uses IMAP + app password, configured per-stack at `/config/gmail/`. Transparency contract codified as executable assertions in `tests/test_transparency_invariants.py` — failures there mean the disclosure banner is lying.

The same IMAP integration also drives **rejection detection** (#362): every 30 minutes, `scripts/detect_rejections.py` scans Gmail against `config.rejection_sender_allowlist` (Greenhouse, Ashby, Lever, Workday-style ATS senders) and writes pending rows to `rejection_suggestions` for operator review at `/board/rejections-review/`. Cron entry `detect-rejections` in `ops/scheduled-jobs.yaml`. Operator confirms via the review-queue UI; never auto-flips. The #362 design notes are maintainer-private. Company-name aliases live in `config/company_aliases.yaml` (allowlisted in `/config/`; matcher hot-reloads on every cycle).

### Auth Gate Must Be Verified Post-Deploy

After every `docker compose up -d` on any stack, the basic-auth gate must be verified by running `python -m findajob.web.verify_auth` (image-baked) inside the running container. If the verifier exits non-zero, the stack is taken down until fixed. **No exceptions** — including hotfixes, rollbacks, and one-off restarts.

Exit codes: 2 = `FINDAJOB_AUTH_USER`/`FINDAJOB_AUTH_PASS` empty; 3 = anonymous request didn't get `401 + WWW-Authenticate: Basic`; 4 = authenticated request didn't get `200`; 5 = network failure.

A stack that intentionally has no app-level auth (e.g., behind an internal-mesh perimeter) will fail with exit 2 — that's the signal to either configure auth or document the explicit exception in CLAUDE.local.md.

---

## Implementation Guardrails

PR-vs-main flow, branching, commit conventions, and the `migration-required` label live in [`CONTRIBUTING.md`](CONTRIBUTING.md). Read it before any non-trivial change.

The one rule worth restating here because it bites often: **Same-PR docs rule.** When code touches a documented surface, update the docs in the same PR. Schema → CHANGELOG `### Migration required` entry; new env var → `configure.md`; new state transition → [`docs/architecture/board-lifecycle.md`](docs/architecture/board-lifecycle.md); a changed area in any reference doc listed under [`## Key File Locations`](#key-file-locations) → that doc.

---

## Project Board, Plans, Releases

- **Project board** — GitHub Projects v2 at https://github.com/users/brockamer/projects/1 is the single source of truth. Not on the board = not on the roadmap. Conventions in [`docs/project-board.md`](docs/project-board.md) (also jared's config file — the machine-readable header block is parsed on every board operation). Use the `/jared file` skill instead of manual `gh` calls — issue creation requires both `gh issue create` AND `gh project item-add` (new issues do not auto-add).
- **Instance-specific work is tracked privately** — Work about running specific instances of findajob (the maintainer's own, or instances run for other people) and other maintainer-operational work is tracked in the maintainer's private tracker, not on this board. Public issues stay generic: no host names, instance names, people or business detail. When private work needs a product change, file a generic issue here; the link runs private → public only.
- **Plans, specs, experiments** — generic plans and specs for public features are committed under `docs/superpowers/` (layout in its [README](docs/superpowers/README.md)); anything about the maintainer's own instances stays out of this repo (rule above). Content conventions in its [§ Plan structure](docs/superpowers/README.md#plan-structure). A plan without a **Documentation Impact** section is incomplete — push back rather than execute it.
- **Releases** — Docker image tagged from main; CHANGELOG.md is the release-notes source. PRs with schema / config / crontab / mount / compose changes get `migration-required` at PR-open time.

---

## Working Style

- Use paths from `findajob.paths`. No placeholders in commands.
- Preserve the scheduler-driven daily run in all changes.
- Working features first, polish later.
