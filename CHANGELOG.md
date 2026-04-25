# Changelog

All notable changes to findajob are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Until the pipeline stabilizes, 0.x releases are considered unstable. Breaking
changes may land in minor version bumps; patch releases are bugfix-only.

## [Unreleased]

### Added

- **Recruiter critic step (#257).** New `recruiter_critic` role on Opus 4.7 runs after the cover letter and produces a `{Prefix} Critique - {Company} - {Title} - {timestamp}.md` artifact in each prep folder. Sees only what an actual recruiter sees (company, title, JD, tailored resume, cover letter — no profile, briefing, or fit analysis) so the critique simulates a 30-second outside read rather than a self-review. Tells the candidate what looks generic, what looks weak, and what is missing, in ≤150 words.
- **Voice samples wired into cover letters and outreach (#257).** `findajob.utils.load_voice_samples()` reads `.md` and `.txt` files from `candidate_context/voice_samples/` (excluding `README*`), concatenates with double-newline separators, caps at 32K chars, and the result is injected into `cover_letter_writer` and `outreach_drafter` prompts as a `VOICE SAMPLES:` section. Both role prompts include explicit "use for STYLE only — sentence rhythm, word choice, register; do NOT adopt the topical content or subject matter" guard rails. Empty / missing samples directory yields an empty section and no behavior change. Operators who want voice calibration drop long-form prose into the directory.

### Changed

- **outreach_drafter rewritten and upgraded to Opus 4.7 (#257).** Prompt expanded from ~13 lines to a structured spec covering tone & register, structure & density, honesty & framing, vocabulary, calibrate-to-contact rules, anti-fabrication, and per-format specs for LinkedIn DM and email. Bans common AI tells (em dashes, performative enthusiasm, corporate filler vocabulary, restating the contact's own title, formal closing platitudes) explicitly. Sonnet 4.6 to Opus 4.7 because complex multi-rule prompts on inputs that get sent to real humans benefit from stronger instruction-following.
- **Truncation slices removed from prep prompts (#257).** Seven `JD[:N]` / `full_briefing[:N]` slices in `scripts/prep_application.py` and `scripts/find_contacts.py` have been removed. Slices dated to smaller-context-window models; current routes (Opus 4.7 / Sonnet 4.6 200K, Gemini 3 Flash 1M, Perplexity sonar 128K) all have ample headroom. Briefings now reach `resume_tailor` and `cover_letter_writer` un-truncated; full JDs reach researcher, briefing, fit, change_reviewer, and outreach. The `JD_MAX_CHARS = 16000` cap in `findajob.utils` is unchanged — that is a defensive cap on the curl-fallback JD-load path, not an in-prompt slice.
- **OpenRouter Phase 2 cutover (#250).** Ten of eleven pipeline roles now route via OpenRouter as a single gateway: `resume_tailor` and `cover_letter_writer` upgraded to **Opus 4.7** (same pricing as 4.6 per OR catalog, small-to-moderate quality edge on real-pipeline prompts per Phase 1 verdict #22); `briefing_writer` and `outreach_drafter` to `openrouter:anthropic/claude-sonnet-4.6`; `company_researcher` and `fit_analyst` to `openrouter:perplexity/sonar-reasoning-pro` (OR's Perplexity path returns structured URL citations, direct path strips them); `resume_change_reviewer`, `network_analyst`, and the default model to `openrouter:google/gemini-3-flash-preview`. Embedding (`gemini-embed:gemini-embedding-001`) stays on the direct Google client — OR has zero embedding endpoints. `job_scorer` unchanged (already on OR).

### Migration required

- **Edit `state/aichat_ng/config.yaml` on each deployed stack** to change the top-level `model:` line from `gemini:gemini-3-flash-preview` to `openrouter:google/gemini-3-flash-preview`. The image's `ops/aichat-ng/config.yaml.example` template seeds this file only on first install; existing installs keep their pre-upgrade default otherwise.
- **Diff `state/aichat_ng/models-override.yaml` against `ops/aichat-ng/models-override.yaml` in this release** and append the two new openrouter catalog entries if absent: `anthropic/claude-opus-4.7` and `google/gemini-3-flash-preview`. Without these, the role files will reference models aichat-ng does not know about.
- **Ensure `OPENROUTER_API_KEY` is set** in `state/data/.env` (or equivalent). Ten of eleven roles now depend on it.

## [0.3.3] — 2026-04-24

Patch bump. Three additive `/board/*` UI improvements surfaced during the 2026-04-24 structural review (waitlist scores, Archive score-6 browse + promote, dashboard/waitlist company application history), plus a regression fix for Greenhouse fetcher URL parsing that was silently dropping Tier 1 additions using the newer `job-boards.*` subdomain. No migration required — rolling `docker compose pull && up -d` picks it up cleanly.

### Added

- **Fit and probability scores on `/board/waitlist` (#241).** Waitlist rows now render `fit_score` and `probability_score` alongside `relevance_score`; NULL values show as em-dash. Applies to the shared `_job_row.html` partial so every tab using it benefits. Closes #237.
- **`/board/archive` score filter + Promote-from-archive (#242).** Archive view accepts `?min_score=N&max_score=M` (bounds optional, inclusive); header carries "Score 6" and "Score 7+" quick-filter presets plus a Clear link. Rows in `stage='scored'` gain a Promote button backing the existing `/board/jobs/{fp}/promote` handler. HTMX infinite-scroll sentinel carries the filter params so pagination stays consistent. Surfaces the ~2–3/day score-6 supply that Dashboard's >=7 filter was hiding without flooding the triage queue. Closes #238.
- **Company application history cell on `/board/dashboard` and `/board/waitlist` rows (#244).** Each row now shows "N pending" + "N not selected" counts of prior applications to the same company, with a green flag for any offer and a yellow flag for `not_selected` within 90 days. Company matching normalizes on the first token so "Meta" and "Meta Platforms" collapse together. Operator-side `rejected` jobs are excluded (noise, not signal); a row's own fingerprint is excluded from its own history. HTMX row-swap path in `board_actions.py` passes the cell through so post-action re-renders keep the context. Closes #234.

### Fixed

- **Greenhouse fetcher now recognizes `job-boards.greenhouse.io` URLs and bare-slug entries (#245).** Adding URLs using Greenhouse's newer `job-boards.*` subdomain (or bare-slug shapes with no trailing `/jobs.rss`) to `config/feed_urls.txt` now ingests; the previous regex required `boards(.eu)?.greenhouse.io/{slug}/` with a trailing path segment and silently dropped anything else. Existing entries continue to parse unchanged. Discovered while widening Tier 1 coverage (xAI, Nscale, Astera Labs — all served under `job-boards.*`). Closes #199.

## [0.3.2] — 2026-04-24

Patch bump. Hotfix for a v0.3.1 regression: the shipped image didn't include the `docs/` tree, so every `/docs/` slug link 404'd post-deploy. Rolling `docker compose pull && up -d` fixes it.

### Fixed

- **`/docs/` slug routes now actually resolve in the shipped image (#224 follow-up, #235).** v0.3.1 shipped the `/docs/` viewer but the Dockerfile didn't `COPY docs/` into `/app/docs/` (and `.dockerignore` excluded the tree from the build context), so the route served a 200 index but every slug link 404'd post-deploy. Fixed by baking `docs/` into the image at build time (narrowing the `.dockerignore` exclude to `docs/superpowers` — internal plan/spec tree only). Added a `/docs/` + slug assertion to `scripts/test_container_integration.sh` so the regression can't recur silently — the v0.3.1 pre-tag smoke would have caught this if it had covered the route.

## [0.3.1] — 2026-04-24

Patch bump. Two bugfixes for reliability issues surfaced during Alice's morning triage (#222, #223), plus the last-mile `/docs/` viewer that makes user-facing guides reachable from inside the web UI (#224). No migration required — rolling `docker compose pull && up -d` picks it up cleanly.

### Added

- **`/docs/` renders user guides inline in the web UI (#224).** The `/docs/` top-nav slot now serves `docs/usage.md`, `docs/troubleshooting.md`, and `docs/setup/README.md` (plus the setup sub-pages it links to: prerequisites, install-docker, install-linux, configure, state-migration) as HTML inside the app shell. `.md` cross-links between guides are rewritten to `/docs/<slug>` at render time; external links get `target="_blank" rel="noopener noreferrer"`; heading `#anchor` fragments resolve (Python-Markdown `toc` extension auto-generates IDs). Markdown source on disk under `docs/` is unchanged — GitHub rendering still works. The shared Markdown helper moved from `routes/materials.py` into `findajob.web.markdown` so both viewers share one implementation. Finishes the last mile of #11.

### Fixed

- **Pre-#148 stacks now auto-backfill `config/companies_of_interest.txt` at container start (#222).** Stacks whose `config/target_companies.md` was written before the #148 onboarding injector learned to derive a companions list were left with `config/companies_of_interest.txt` missing — `config_loader` silently disabled the `sync_sheet` archival exception and the `notify.py health-check` mis-score probe, and logged a `UserWarning` on every import. A new `scripts/seed_companies_of_interest.py` now runs from `ops/entrypoint.sh` on every start: when `target_companies.md` exists and the derived file is missing, it derives and writes; when both exist, it's a no-op (user edits preserved); when no `## Tier 1` section is present, it logs `companies_of_interest_derive_skip` at info level instead of raising a warning.
- **RapidAPI LinkedIn JD 429 burst during morning triage (#223).** `fetch_linkedin_job_data()` now mirrors the 429 handling already in `fetch_jobsapi_jobs()`: respects `Retry-After`, sleeps (clamped 10s–60s), retries once, and adds a 0.2s per-call throttle to keep the bursty opening of morning triage below the plan's per-minute cap. Per-hit `linkedin_get_error: 429` spam is replaced by a single end-of-triage `linkedin_rate_limited` summary event with `count` and `total_wait`. Observed cause: Alice's 214-job triage fired 9 LinkedIn JD GETs in a 27s window on 2026-04-24, all logged as `linkedin_get_error` and scoring without enriched JD (Stage 2 prefilter default of 5–6).

## [0.3.0] — 2026-04-23

Minor bump. Adds the first-run onboarding NUX at `/onboarding/` (#148) — fresh stacks are now guided end-to-end through an LLM interview that writes the seven canonical config files atomically, with existing destinations backed up. Retires Sheet1 writes (#136) — `/board/archive` has been the archival surface since v0.1.3, and Sheet1 was dead weight. Extends the `/config/` editor allowlist (#149) to cover the two files the onboarding flow newly produces. Two `migration-required` markers below.

### Removed

- **Sheet1 writes (#136).** `sync_sheet.py` no longer writes to the `Sheet1` tab on the Google Sheet; the `notify.py health-check` drops the "Sheet1 > N rows" warning; `scripts/init_sheet.py` deleted (existed only to write Sheet1 headers); `scripts/setup_sheets.py` no longer formats Sheet1; `build_row()` loses its `use_status` parameter (only dashboard callers remain, all derive status). The web `/board/archive` view has been the archival surface since #60 (v0.1.3) and is strictly more useful than Sheet1's filtered subset.

### Added

- **Onboarding NUX at `/onboarding/` — fresh stacks are guided end-to-end.** First-run stacks (no `{base_root}/data/.onboarding-complete` sentinel) redirect from `/board/*`, `/materials/*`, and `/stats/*` to a new `/onboarding/` landing page that walks the user through running the interview (`config/roles/onboarding_interviewer.md`) in their chosen LLM (Claude / ChatGPT / Gemini) and pasting the emission back. The paste-back handler parses the seven `<<<FILE: name>>>`-delimited blocks, backs up any existing destinations to `{base_root}/.backups/{UTC-stamp}/`, atomically writes the seven canonical config files (profile, master resume, target companies, sector reference, search queries, prefilter rules, in-domain patterns) plus a derived `config/companies_of_interest.txt` from the Tier 1 section of `target_companies.md`, and writes the sentinel that clears the redirect. Re-triggerable from `/tools/` via `/onboarding/?mode=rerun`. Closes #148; unblocks #11 (user-facing setup docs).
- **`/config/` editor allowlist extended for post-injection tuning.** `config/target_companies.md` and `config/business_sector_employers_reference.md` (both injected by the onboarding flow) are now editable via `/config/`. `config/companies_of_interest.txt` stays off the allowlist — it's derived at injection time and editing it directly would drift from `target_companies.md` (#148).

### Migration required

- **Operators with existing stacks:** after pulling this release, either (a) run the onboarding interview once from `/tools/ → Run onboarding interview`, or (b) touch the sentinel file manually: `docker compose exec scheduler python -c "from findajob.onboarding import mark_complete; from pathlib import Path; mark_complete(Path('/app'))"`. Without one of these, the first request to `/board/` will 307-redirect to `/onboarding/` until the sentinel exists. This is a one-time action per stack (#148).
- **Stale `Sheet1` tab on existing spreadsheets (#136).** The pipeline no longer writes to or reads from `Sheet1`, but the tab itself is not programmatically deleted. Right-click the `Sheet1` tab in the Sheets UI → Delete once you've confirmed you don't need its contents. The web `/board/archive` view has the same data (and more) with pagination, sort, and filter.

## [0.2.0] — 2026-04-23

Minor bump. Web UI becomes the primary write surface for the board, replacing Google Sheet edits and the Google Form JD-ingest loop. Adds a `/config/` in-browser editor, the first two `/stats/` dashboards, and the `/board/*` read/write views. Introduces two-tier dedup and a new nullable `loose_fingerprint` column on `jobs` — **operators upgrading from v0.1.4 must run `scripts/migrate_add_loose_fingerprint.py` once after pulling** (see Migration required below). Several prep/sync reliability fixes surfaced during the #61 PR-B smoke are also included.

### Added

- **`/config/` in-browser editor — edit pipeline config files without SSH.** New top-nav page `/config/` lists the editable config files by category (candidate context, search config, role prompts) and opens each in a plain `<textarea>` with a save button. An allowlist module (`src/findajob/web/config_files.py`) enumerates the editable paths (`candidate_context/profile.md`, `candidate_context/master_resume.md`, `config/prefilter_rules.yaml`, `config/in_domain_patterns.yaml`, `config/jsearch_queries.txt`, `config/feed_urls.txt`, `config/roles/*.md`) — every other path returns 403. Writes are atomic (tmpfile + `os.replace`). Missing files render as an empty textarea and are created on save, so the editor works on a fresh stack before the onboarding flow (#148) has run. `/tools/` bumped from placeholder to a real page linking to the editor. Closes #149; unblocks the tuning section of #11 (user-facing docs).
- **`/stats/feedback` dashboard — rejection-reason trends.** Second tab in the 14e stats group surfaces user-side rejections logged to `feedback_log`: a this-week (trailing 7 days) per-reason summary and a 28-day daily multi-line chart covering all 11 canonical reject reasons plus any legacy free-text entries. The weekly `notify.py feedback-review` ntfy push now includes a `Trends:` link to the dashboard; base URL is configurable via the new `FINDAJOB_WEB_URL` env var (default `http://docker.lan:8090`). Data source deviates from the 14e spec's AC #2 — spec named a `feedback_stats` jsonl event that was never emitted by #55, and the `feedback_log` SQLite table already carries the same data with the canonical naïve-UTC timestamp format. Closes #193, reduces #56.
- **Web stats dashboards kicked off with `/stats/funnel`.** New top-nav group `/stats/` introduces daily stage-transition counts over the last 30 days, backed by a Chart.js line chart (CDN-pinned) and a data table. The full sub-tab taxonomy (Funnel, Feedback, Scoring, Rejections, Throughput, Effectiveness) is visible from day one; the five deferred dashboards render as disabled placeholders with follow-up issues #193–#197 already filed against the 14e spec (`docs/superpowers/specs/2026-04-24-web-frontend-14e-stats.md`). Closes #63 for the vertical slice; retires #31 (Pipeline Funnel Scoreboard) and #112 (restore notify.py scoreboard) — the textual scoreboard is superseded by the live web views. As a drive-by, fixes the top-nav "Board" link so it highlights on every `/board/*` page, not just `/board/dashboard` (nav portion of #138).
- **Web UI is now the primary write surface for the board.** Every STATUS and REJECT_REASON action that previously required editing the Google Sheet — Flag for Prep, Applied, Interviewing, Offer, Withdrew, Not Selected, Waitlist, Reactivate, Promote, Regenerate, Reject — now has a POST handler at `/board/jobs/{fingerprint}/{action}`, wired up to Alpine-flavored HTMX dropdowns on each board tab. The Applied tab's `user_notes` column edits through `POST /board/jobs/{fingerprint}/notes` with an 800ms debounce (#61 PR-A).
- **Manual JD ingest moved from Google Form to web UI.** The new `/ingest/` page replaces the Google Form + `scripts/ingest_form.py` polling loop — paste company / title / URL / full JD text and the row lands on the Dashboard at `stage=scored`, `relevance_score=8`, `source='web_manual'`. The full-JD-text field is required, which covers JS-rendered SPAs and auth-walled postings that scrape poorly (absorbs #79); `prep_application.py` uses the pasted JD directly and skips URL refetch. A "Generate prep folder immediately" checkbox dispatches `prep_application.py` subject to the same 3-job concurrency cap as the Dashboard's Flag-for-Prep button. The form template includes a disabled Speculative-mode tab linking to #131 for the follow-up cold-outreach flow (#62).

### Changed

- **Google Sheet is now one-way (DB → Sheet).** `sync_sheet.py` no longer reads user edits back from any tab; the four `values().get()` calls (Dashboard, Applied, Review, Waitlist) are deleted, along with the `pending_statuses` / `pending_rejects` / `pending_notes` preservation logic. The Sheet remains available as a read-only synced view; operators drive the pipeline from `/board/*` in the web UI (#61 PR-B).
- **`poll_flags.py` removed; replaced by `scripts/watchdog.py`.** The 10-minute cron's only remaining responsibility is to roll stuck `prep_in_progress` jobs back to `scored` after 60 min. Every transition handler (handle_rejection, handle_not_selected, handle_waitlist, handle_reactivate, promote_to_scored, notify_waitlist_resurface, reset_prep_to_scored) now lives in `src/findajob/actions.py` and is called from `findajob.web.routes.board_actions` (#61 PR-B).
- **Applied tab drops the `Ghosted` status option.** With the Sheet no longer preserving user-only flags across syncs, the existing 21-day row-age gray-coloring rule replaces it. Operators who want to act on a quiet row flip to `Not Selected` (#61 PR-B).
- **`scripts/ingest_form.py` timer retired.** The `*/30` crontab entry is commented out; the script is kept in place as a manual-run fallback for draining any leftover Google Form responses until the Form itself is decommissioned. New submissions should use the `/ingest/` web form (#62).

### Migration required

- Crontab entry changes from `scripts/poll_flags.py` to `scripts/watchdog.py`. Operators pulling `:latest` pick up the swap automatically at container restart — no manual action needed. Sheet edits made during the pull window (if any) are ignored; operators should use the web UI for any queued transitions.
- `*/30 ingest_form.py` entry in `ops/crontab` is commented out; operators pulling `:latest` stop seeing the 30-min Google Form poll at container restart. The `/ingest/` web form replaces it. No state migration required — existing `manual_form`-source rows keep their semantics, new rows land as `web_manual` (#62).
- `jobs` gains a nullable `loose_fingerprint TEXT` column and `idx_jobs_loose_fingerprint` index for Tier 2 dedup (#182). Fresh deploys get the column from `scripts/init_db.py` on first container start. Existing stacks must run `python3 scripts/migrate_add_loose_fingerprint.py` once after pulling `:latest` — the script is idempotent, backfills existing rows by recomputing `loose_fingerprint(title, company)`, and preserves all other state.

### Fixed

- Dedup cluster (#182). Three related bugs surfaced during #61 PR-B smoke test. **Bug A** — `clean_title()` now strips NBSP (U+00A0) and collapses all whitespace runs, so titles differing only in leading/trailing/internal whitespace produce the same fingerprint. Applied at ingest in the Greenhouse, Ashby, and Lever fetchers (previously only the JSearch/Indeed path). **Bug B** — `normalize_location()` strips LinkedIn's `(On-site)` / `(Remote)` / `(Hybrid)` suffixes and trailing `", United States"` / `", US"` / `", UK"` / `", Canada"` before fingerprinting, so re-ingesting the same URL with a volatile location suffix no longer mints a fresh row. **Bug C** — introduced two-tier dedup. Tier 1 remains `hash(title, company, location)`; Tier 2 is a new `loose_fingerprint(title, company)` lookup that fires only when incoming OR any existing same-(company,title) row has a coarse location (empty, country-only, or bare "Remote"). LinkedIn syndication of a Greenhouse posting (e.g. Greenhouse "US" vs LinkedIn "Barstow, TX") now dedupes; distinct-city reqs (site managers in different cities) keep producing distinct strict fingerprints and never reach Tier 2. `scripts/ingest_form.py` now shares the centralized `findajob.cleaning` helpers instead of its drifted local copies.
- `sync_sheet.py` now verifies each tab's `values().update()` response against the expected row count. The Sheets API can return HTTP 200 with `updatedRows` far below the request size — observed 2026-04-22 where both tenants' syncs logged `sync_complete applied=31 waitlist=36` etc. but the actual sheets had 0 rows on most tabs. The old code trusted `len(sheet_rows) - 1` for its `sync_complete` counts; the new `_assert_full_write()` raises `RuntimeError` on mismatch (propagating to `triage.py`'s `triage_sync_failed` event from #145) and emits a `sync_partial_write` event with the server-reported counts for post-mortem. All six tabs (Sheet1, Dashboard, Review, Waitlist, Applied, Rejected Applications) are covered (#171).
- `prep_application.py` failure paths (missing candidate files, empty-output validation, unhandled exception) now share a `reset_prep_to_scored()` helper that writes an `audit_log` entry and emits a `prep_failed_reset` event before rolling stage back to `scored`. Without the audit entry the 60-min stale-prep reset couldn't distinguish real hangs from silent error-path resets, and a transient upstream outage (e.g. today's 15-min Anthropic/Gemini auth blip) could loop forever with only the forward half of each transition visible in the audit trail. `poll_flags.py`'s deferred-over-concurrency-cap reset uses the same helper (#172).
- `prep_application.py` now quarantines any prior prep folders for the same `{company, title}` that aren't tracked in DB, before creating its own folder. Each prep run mints a new `{date}_{HHMMSS}` suffix, but only the latest is stored in `jobs.prep_folder_path` — Regenerate clicks and prep races otherwise leave older folders orphaned on disk (observed 2026-04-22: 4 folders for one UN/P4 job in ~50 min). The new `quarantine_stale_prep_folders()` helper moves matches into `companies/.stale/` rather than `rmtree`-ing so a racing prep's files are recoverable, and emits a `stale_prep_folders_quarantined` event for post-mortem (#174).
- `triage.py` now captures the exit code of the `sync_sheet.py` subprocess and emits a `triage_sync_failed` event with the return code when sync crashes non-zero. Previously `check=False` swallowed the failure, leaving only a `sync_complete not seen in 25h` warning as the eventual signal. The new event is picked up by `notify.py health-check`'s generic error matcher immediately (#145).

## [0.1.4] — 2026-04-22

Bugfix patch: reliability and diagnostics fixes surfaced during the generalization beta (Alice Doe / #20). Fixes silent triage crashes, stuck prep cycles, a bad Gmail OAuth client type, and several web and sheet inconsistencies. Operators with a "TVs and Limited Input devices" OAuth client must rotate to a Desktop-type client before pulling (see Migration required below).

### Fixed

- Entrypoint now asserts `aichat-ng config.yaml` is readable by the runtime user before supercronic starts, exiting with a clear diagnostic if `HOME: /app` is absent from `compose.yaml`. Guards the silent failure that stranded all scoring on the Alice Doe stack (#161, #166).
- CI smoke tests now pass `-e HOME=/app` to all `docker run` commands, matching the production compose.yaml requirement exposed by the health check above (#166).
- `poll_flags.py` now resets any job stuck in `prep_in_progress` for >60 minutes back to `scored` at the start of each poll cycle. Recovers from container restarts that kill the prep subprocess before it can reset its own stage (#163, #168).
- `triage.py` now wraps the `main()` call in a top-level `try/except` that logs a `pipeline_crash` event with the full traceback before re-raising. Previously a crash after `jobs_fetched` would leave no diagnostic trace in `pipeline.jsonl` (#162, #167).
- `triage.py` now retries up to 50 `manual_review` rows with `relevance_score=NULL` per triage cycle. `notify.py health-check` subcommand split from `notify.py health` for finer-grained alerting (#147).
- `poll_flags.py` now issues a single `sync_sheet.py` call after all prep subprocesses complete, eliminating a race condition that caused sheet drift on multi-job prep batches (#143).
- Web dashboard filter corrected to use `relevance_score` (the triage score) instead of `fit_score` (the prep-time score); previously the dashboard showed no jobs (#142).
- `.docx` downloads from the materials viewer now force `application/octet-stream`, fixing browser-rendered garbage (#152).
- Markdown viewer now has prose typography via Tailwind's typography plugin; code blocks, blockquotes, and headings render correctly (#157).
- `cover_letter_writer` role prompt no longer contains an operator-specific example; replaced with a generic placeholder (#156, #159).
- `ops/entrypoint.sh` API-key injection now uses `eval` for portable indirect-variable expansion and defaults unset keys to empty, fixing `set -eu` failures when containers are started with some keys absent (#154, #155).
- `gmail_auth.py` drops the device-flow code path entirely. Google's device authorization grant excludes Gmail scopes; the only working flow is the loopback (`InstalledAppFlow`) with an SSH tunnel (#115, #144).
- Fresh-install smoke script now requires service-account credentials and asserts `sync_sheet.py` writes to the sheet, preventing the silent empty run that let v0.1.0 fresh-install bugs go undetected (#129, #146).

### Migration required

**Operators with a "TVs and Limited Input devices" OAuth 2.0 client must rotate to a Desktop-type client before pulling this release.** Google's device authorization grant excludes Gmail scopes; the old client type cannot authorize Gmail in any flow (#144).

1. In Google Cloud Console, create a new OAuth 2.0 client → type: **Desktop app**
2. Download new JSON → overwrite `state/config/gmail_oauth_client.json`
3. Re-run Gmail auth using the SSH tunnel (see [`docs/setup/install-docker.md`](docs/setup/install-docker.md))

Operators already on a Desktop-type client (including all fresh installs from v0.1.1+) are unaffected.

## [0.1.3] — 2026-04-21

Bugfix patch: fixes a container ownership race that left `pipeline.jsonl` root-owned after a `docker exec`-as-root, causing `PermissionError` crash-loops in supercronic. Also ships the web board (five tabs), company-cell hyperlinks in the sheet, and the materials viewer top-nav refactor that were queued behind v0.1.2. No operator action needed on pull.

### Added
- `sync_sheet.py` now hyperlinks the company cell on Dashboard, Applied, Waitlist, and Rejected Applications tabs into the materials viewer when a new `FINDAJOB_MATERIALS_BASE_URL` env var is set (e.g., `http://docker.lan:8090`). Stages without folders and unset env var render as plain text (no 404s). Stale "Drive hyperlink" annotations removed from `CLAUDE.md`, `docs/google-sheets.md`, and `setup_sheets.py` (#130).
- Web viewer now has a top nav and landing page. Materials folder index moved from `/` to `/materials/`; deep links `/materials/{fingerprint}` and `/materials/{fingerprint}/{filename}` unchanged. Placeholder pages for board, ingest, tools, config, docs fill in as features land (#60).
- `/board/dashboard`, `/board/applied`, `/board/review`, `/board/waitlist`, `/board/archive` render the same content as the corresponding Google Sheet tabs, reading directly from the database. Archive covers all jobs (10k+) with HTMX infinite-scroll pagination, obsoleting Sheet1's archival filter. Per-tab text filter via HTMX, URL-param sort, Sheet-matching conditional formatting (Applied row-age buckets, Offer gold, Interviewing purple, known-contacts amber), and Applied's company cell hyperlinks into the materials viewer when `FINDAJOB_MATERIALS_BASE_URL` is set. `sync_sheet.py` continues to update Sheets in parallel during the 14b → 14c → 14d migration (#60).

### Fixed
- Container entrypoint now reconciles file and subdirectory ownership inside each writable dir (data, logs, companies, config, candidate\_context, aichat config), not just the top-level inode. A root-owned file created by `docker exec` (default user: root) is now corrected on the next container restart, preventing `PermissionError` crash-loops in supercronic jobs (#140).

### Migration required

Operators whose deployed `compose.yaml` was copied from `ops/compose.yaml.example` on v0.1.2 or earlier need two edits to opt into the company-cell hyperlinks (#133). Fresh installs from the current template are unaffected.

1. Add to the stack `.env`:
   ```
   FINDAJOB_MATERIALS_BASE_URL=http://<your-docker-host>:<FINDAJOB_MATERIALS_PORT>
   ```
2. Add under `environment:` in the deployed `compose.yaml` (`scheduler` service):
   ```yaml
   FINDAJOB_MATERIALS_BASE_URL: ${FINDAJOB_MATERIALS_BASE_URL:-}
   ```

Without both edits, the env var never reaches the container and company cells render as plain text. Full steps in [`docs/setup/state-migration.md`](docs/setup/state-migration.md).

## [0.1.2] — 2026-04-21

Retires the Google Drive / rclone folder-browsing surface in favor of a local FastAPI web viewer served per stack. The container image loses the `rclone` apt package (~50 MB smaller), gains a uvicorn co-process, and publishes a new `FINDAJOB_MATERIALS_PORT` — each stack picks its own. Operators with `FINDAJOB_JOBSYNC_ENABLED=true` on v0.1.x need a one-time stack update; fresh-install testers are unaffected.

### Added
- Web materials viewer on `http://docker.lan:<port>/` serves prep-folder contents — markdown rendered inline, `.docx` downloads, index grouped by lifecycle stage (In flight / Applied / Waitlisted / Rejected). Replaces Google Drive folder browsing (#125, closes #59, closes #29).

### Removed
- rclone integration and Google Drive sync plumbing. `FINDAJOB_JOBSYNC_*` env vars deleted; `state/rclone/` bind mount no longer used; `rclone` removed from the container image; `poll_flags.py` / `prep_application.py` / `notify.py` rclone call sites deleted; `scripts/bootstrap.sh` no longer installs rclone (#125).

### Fixed
- Viewer index route queried `score` and `applied_date` columns that don't exist on the production `jobs` schema; both replaced with `fit_score` (REAL) and `stage_updated` (TEXT). Test fixtures rewritten against the real schema so future drift surfaces in CI rather than prod (#127).

### Migration required

Operator stacks that had `FINDAJOB_JOBSYNC_ENABLED=true` on v0.1.x must stop the stack, remove the `state/rclone/` bind mount, add a `FINDAJOB_MATERIALS_PORT` to `.env`, add a `ports:` block to `compose.yaml`, and drop the `FINDAJOB_JOBSYNC_ENABLED` env line before pulling. Exact steps in `docs/setup/state-migration.md`. Fresh-install testers are unaffected.

## [0.1.1] — 2026-04-20

Fresh-install fixes uncovered during the first external tester's deployment (#20 / #82). `v0.1.0` had only been validated against the operator's legacy stack; empty bind mounts hit four untested code paths. No migration required — all fixes are entrypoint-driven and idempotent. Existing operator stacks pull `:v0.1` and keep working; fresh deploys now reach a populated Dashboard without operator intervention beyond API keys + per-tester config.

### Changed

- Pre-tag smoke check is now a fresh-install end-to-end test (empty bind mounts → documented install procedure → assert `scored > 0` and schema fold + aichat seed landed), not a 24h operator-stack observation window (#119). `docs/release-process.md` rewritten accordingly; the 48h dogfood gate is permanently retired for `v0.1.x`. Smoke is run locally on a docker-equipped host before each tag cut; CI wiring is deferred to a follow-up (#124).

### Fixed

- Entrypoint now runs `init_db.py` on every container start so fresh deploys don't crash on first triage's `SELECT FROM jobs` (#116).
- `init_db.py` now carries `cost_log.input_tokens`, `cost_log.output_tokens`, `cost_log.cost_usd`, and `jobs.user_notes` columns that previously lived only in one-shot migration scripts (#117). Fresh deploys no longer crash mid-scoring or on Applied-tab user-notes sync.
- Entrypoint now seeds `aichat-ng config.yaml` from a sanitized template and creates the `roles` symlink on first container start (#118). Fresh deploys no longer fail every scoring subprocess with "Failed to load config.yaml."
- `scripts/test_container_integration.sh` was stubbing config "enough to bring the scheduler up without erroring on import" and thus missed the four fresh-install bugs that shipped in `v0.1.0`. Rewritten to exercise the full install → triage → `pipeline_complete` cycle with fictional fixtures ("Casey Example") (#119).

## [0.1.0] — 2026-04-20

First containerized release. Ships the pipeline as a Docker image pulled
from GHCR and deployed via Docker Compose on a shared Docker host.

### Added
- `Dockerfile` building `python:3.12-slim-bookworm` with pinned `aichat-ng`
  (`blob42/aichat-ng` v0.31.0 prebuilt musl binary) and `supercronic`
  v0.2.29 (#13)
- `ops/crontab` — supercronic schedule translating all systemd timers 1:1 (#13)
- `ops/entrypoint.sh` — PUID/PGID-aware drop-privileges entrypoint via gosu (#13)
- `ops/compose.yaml.example` + `ops/stack.env.example` — deploy templates (#13)
- `scripts/gmail_auth.py` — standalone OAuth helper with device flow (#13)
- GitHub Actions workflows:
  - `build-image.yml` — push to GHCR on `main` and on `v*.*.*` tags (#13)
  - `create-release.yml` — auto-generated release notes on tag push (#13)
  - `docker-build-smoke` job in `ci.yml` — image smoke tests on every push (#13)
- `docs/release-process.md` — Claude-facing release orchestration runbook: dogfood gate (now suspended, see Changed), CHANGELOG workflow, tag cut mechanics, post-tag verification, rollback (#69)
- `docs/setup/install-docker.md` — full external-user Docker install + operations guide replacing the stub (#13, #69)
- `migration-required` GitHub label for PRs needing post-pull manual steps; auto-surfaced by `create-release.yml` in "Action required" section of release notes (#69)
- `CLAUDE.md` "Release Management" subsection pointing future sessions at the runbook (#69)
- `ops/aichat-ng/models-override.yaml` bundled into image at `/opt/findajob/bundled-aichat/`; entrypoint seeds it into `$HOME/.config/aichat_ng/` on first start when no catalog is present. Fresh installs get a known-good model catalog with `require_max_tokens: true` on Anthropic models so `claude:*` roles work out of the box (#106)

### Fixed
- `claude:*` roles (resume_tailor, cover_letter_writer, briefing_writer, outreach_drafter) failing silently when `models-override.yaml` was stale or missing required Anthropic flags — image now ships a bundled baseline catalog (#106)
- Fresh Docker installs hitting silent scoring outage from day one: aichat-ng config was mounted at `/root/.config/aichat_ng` (unreadable under non-root PUID) and `HOME` was unset in the container environment. `ops/compose.yaml.example` now mounts `./state/aichat_ng` at `/app/.config/aichat_ng`, adds `HOME: /app` to both services' env, and adds a new `./state/rclone:/app/.config/rclone` mount so jobsync state persists across container recreation. `ops/entrypoint.sh` chown loop de-duped (hardcoded `/root/.config/aichat_ng` removed; now redundant with `$AICHAT_CFG_DIR`). `ops/stack.env.example` documents `FINDAJOB_JOBSYNC_REMOTE` with an example value. `docs/setup/install-docker.md` has a "Migrating from an older image" section for existing instances (#100)
- Pre-tag smoke check command in `docs/release-process.md` was grepping `docker compose logs` for `pipeline_complete`, but `log_event()` writes only to `logs/pipeline.jsonl` — so the check could never succeed. Replaced with an `awk` read against the bind-mounted jsonl file (#111)

### Changed
- Deployment target: Linux host running Docker. Native systemd install remains documented as a fallback but Docker Compose is the recommended path (#13)
- `ops/crontab` scoreboard line commented out: `notify.py scoreboard` depends on the `gh` CLI which is not in the image, producing a weekly Monday 08:30 PT traceback on every stack. Restoration tracked in #112 (REST API rewrite + env gate). No user-visible feature loss — the scoreboard updates a maintainer-only pinned issue on the operator's project repo (#111)
- Release process: dogfood gate suspended until the first external tester is deployed on a pinned `:vX.Y` tag. Pre-tag requirement drops to a 24h smoke check (no tracebacks, at least one `pipeline_complete`). Full 48h six-signal gate preserved in file history for reactivation later
- Maintainer platform migrated from Proxmox LXC (`findajob.lan`) to Docker host (`docker.lan`). All release-process runbook SSH commands now target `docker.lan`

### Deprecated
- systemd user services for the pipeline scheduler — replaced by supercronic
  inside the container. Existing systemd units stay archived on the maintainer's
  LXC during the observation window (#13)

### Notes
- Release management process is documented in `docs/release-process.md` and
  followed for this cut (#69)
- Documentation cleanup — removing `sigoden/aichat` references in favor of
  `blob42/aichat-ng` — is tracked in #70

[Unreleased]: https://github.com/brockamer/findajob/compare/v0.3.3...HEAD
[0.3.3]: https://github.com/brockamer/findajob/releases/tag/v0.3.3
[0.3.2]: https://github.com/brockamer/findajob/releases/tag/v0.3.2
[0.3.1]: https://github.com/brockamer/findajob/releases/tag/v0.3.1
[0.3.0]: https://github.com/brockamer/findajob/releases/tag/v0.3.0
[0.2.0]: https://github.com/brockamer/findajob/releases/tag/v0.2.0
[0.1.4]: https://github.com/brockamer/findajob/releases/tag/v0.1.4
[0.1.3]: https://github.com/brockamer/findajob/releases/tag/v0.1.3
[0.1.2]: https://github.com/brockamer/findajob/releases/tag/v0.1.2
[0.1.1]: https://github.com/brockamer/findajob/releases/tag/v0.1.1
[0.1.0]: https://github.com/brockamer/findajob/releases/tag/v0.1.0
