# Changelog

All notable changes to findajob are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Until the pipeline stabilizes, 0.x releases are considered unstable. Breaking
changes may land in minor version bumps; patch releases are bugfix-only.

## [Unreleased]

### Added

- **In-app onboarding interview (#336).** Tester runs the full onboarding interview as a chat surface inside findajob — no tab-switching to claude.ai / ChatGPT, no copy-paste of the emission. Server-side persistent sessions (`onboarding_sessions` SQLite table): close the tab and reload `/onboarding/` to see a "Resume your interview (X minutes ago)" affordance and pick up where you left off. Operator-funded via the new `OPENROUTER_OPERATOR_KEY` env var (~$1 per onboarding for Sonnet 4.6 × ~30k output tokens); the tester's own OpenRouter key is collected at finalize and used for the post-onboarding pipeline as before. Conditionally registered: when `OPENROUTER_OPERATOR_KEY` is unset, the affordance does not render and `/onboarding/` falls back to paste-back-only — the existing paste-back flow is preserved verbatim as the escape hatch for outbound-blocked deployments and operators who prefer it. Mid-interview errors (401 / 402 / 429 / 5xx / network) surface a kind-specific banner inside the chat with a link to the relevant OpenRouter dashboard (keys / credits) so the operator can fix and the tester can retry without losing input. All errors emit structured `onboarding_interview_error` events to `pipeline.jsonl` with `error_kind` and `status_code` fields for triage. New module surface: `findajob.onboarding.{session_store,interview_runner}`, `findajob.web.routes.onboarding_interview`, templates `onboarding/{interview,_turn,_turn_bubble,_turn_error}.html`. End-to-end integration test in `tests/test_onboarding_interview_integration.py` exercises the full flow with mocked `urlopen`. Documentation: `docs/setup/configure.md` describes opt-in + cost; `docs/setup/README.md` describes the three configure paths; `CLAUDE.md` updated with the two-path onboarding architecture.

### Migration required

- **Schema:** new `onboarding_sessions` table. Pulled automatically on next `docker compose pull` + entrypoint `init_db` run; no operator action required.
- **Operator opt-in (optional):** to enable the in-app onboarding interview path on a stack, set `OPENROUTER_OPERATOR_KEY=sk-or-v1-…` in the stack's compose `.env` and restart. When unset, the existing paste-back path keeps working unchanged — no migration is required for stacks that prefer paste-back. See `docs/setup/configure.md` § "OPENROUTER_OPERATOR_KEY" for cost (~$1/onboarding) and operational notes.

## [0.9.2] — 2026-05-01

Patch bump. Three independent web/cron bug fixes surfaced by today's overnight tester triage observation. No migration required — bind mounts, schema, crontab, and compose unchanged. Operators on `:latest` and testers on `:v0.9` both pull and recreate.

### Fixed

- **Applied → Not Selected mobile stall: defensive hardening + visible error surfacing (#361).** Move the two-cell pending-action handoff from `tr.dataset.pendingAction` to `rejectSel.dataset.pendingAction` — mobile Chrome was a likely loser of row-level dataset between cell focus events, and the active select element is the most stable carrier. Wire a global `htmx:responseError` / `htmx:sendError` listener (new `static/htmx_errors.js`) so silent 4xx/5xx responses surface as a transient toast instead of an apparently-dead button. Add `log_event("board_not_selected", ...)` server-side so the next mobile attempt yields evidence in `pipeline.jsonl`. New rendering tests pin the JS contract; root-cause confirmation deferred until the operator's next mobile attempt produces logs.
- **Cron-fired scripts no-op cleanly on not-yet-onboarded / not-configured stacks (#371).** `triage.py`, `sync_sheet.py`, and `discover_companies.py` now check `data/.onboarding-complete` (and, for `sync_sheet`, the optional `config/sheet_id.txt` + `config/gsheets_creds.json`) at the top of `main()`. Missing onboarding → emit `{triage,sync,discovery}_skipped: not_onboarded` and return 0; missing Sheet integration → `sync_skipped: sheet_not_configured`. Was: dave/judy/tango stacks (deployed but never onboarded) emitted `pipeline_crash` on every cron tick, and papa's healthy stack emitted `triage_sync_failed: returncode 1` 3× per day because Sheet integration is optional. New `tests/test_onboarding_guards.py` pins the contract.
- **Sort link breaks after filter change (#340).** Filter inputs use `hx-push-url="true"`, which shifts the address bar to `/board/<tab>/rows` after the first interaction. A subsequent click on a column-header sort link (a plain relative `<a href="?sort=...">`) then resolved against `/rows` and returned a `<tr>`-only fragment that hx-boost dumped into `<body>` — manifesting as "all the rows expand and it doesn't work right." Sort links now use the canonical page path stripped of any trailing `/rows` segment, so the resolution is correct regardless of the URL-bar state. New rendering test pins the contract.

## [0.9.1] — 2026-05-01

Patch bump. Widens the Gmail IMAP cold-start window from 7 to 30 days. Mirrors the v0.8.4 datePosted recall fix: a brand-new tester authorizing fresh on a long-lived inbox needs more than a 7-day window to seed the board with useful signal, especially when the inbox has years of LinkedIn / Indeed / ZipRecruiter alerts. Steady-state UID-incremental fetch is unchanged.

### Fixed

- **Gmail IMAP cold-start window widened from 7 to 30 days (#370).** New module-level constant `_COLDSTART_WINDOW_DAYS = 30` in `src/findajob/gmail_imap.py`. Cold-start path (UIDVALIDITY mismatch — first authorize or server-side mailbox reset) now emits `SEARCH (SINCE <30-days-ago> FROM "<sender>")` per allowlisted sender. Two new tests pin the window length and assert that steady-state SEARCH never includes a `SINCE` clause (regression guard against the wider cold-start window leaking into normal operation). No migration: existing operators who already cold-started under v0.9.0 with 7 days are now in steady-state UID-incremental and unaffected; only fresh authorizations going forward see the wider window.

## [0.9.0] — 2026-05-01

Minor bump. Replaces the Gmail OAuth integration with an IMAP + app-password path, configured per-stack at `/config/gmail/`. Opens the multi-tenant foundations milestone — every tester stack can now wire up its own Gmail ingestion without operator intervention or GCP-project provisioning. Migration required for stacks that were on the OAuth integration.

### Added

- **Gmail integration replaced with IMAP/app-password (#330).** Gmail ingestion now uses an app password + IMAP, configured per-stack at `/config/gmail/`. The previous OAuth integration (`config/gmail_oauth_client.json` + `config/gmail_token.json`, plus the loopback OAuth helper service in `compose.yaml.example`) is removed. New onboarders generate a 16-character app password from their Google Account and paste it in — no GCP project, no consent screen, no OAuth verification. The `/config/gmail/` page carries an audit-link-pinned transparency banner specifying exactly what the integration touches in the user's mailbox; the disclosure claims are codified as executable assertions in `tests/test_transparency_invariants.py`.

### Migration required

- **Gmail integration (#330).** If you were using the OAuth integration: configure the new path at `/config/gmail/` and delete `config/gmail_oauth_client.json` + `config/gmail_token.json` from your bind mount. The pipeline silently no-ops Gmail ingestion until configured. Operators with no Gmail integration: nothing to do.

## [0.8.4] — 2026-05-01

Patch bump. Fixes the empty-board NUX cliff for brand-new tester stacks: jobs-api14's LinkedIn `datePosted='day'` filter yields ~7 jobs/day, and the Dashboard's score≥7 default floor filtered most of those out. Live example surfaced this session: papa's stack on day 1 returned 7 jobs total (max score 4) — board read as broken. The fix widens the recency window for stacks in their first 30 days post-onboarding; steady-state behavior unchanged.

### Fixed

- **LinkedIn `datePosted` widens to `'month'` during the first 30 days post-onboarding (#369).** New `_date_posted_for_install()` in `src/findajob/fetchers.py` checks the mtime of `data/.onboarding-complete` and returns `'month'` when under the 30-day threshold, falling back to `'day'` otherwise. `'month'` was chosen over `'week'` because (a) jobs-api14's LinkedIn endpoint accepts only `any|day|week|month` (no `2weeks` value), (b) the scorer correctly filters the additional volume — validated against papa's first triage which went 7 → 47 jobs ingested, all scored, no garbage in the high-score band. Auto-anchored per stack — no env var, no per-stack config. Logs the chosen value once per fetch as `jobsapi_date_posted` for traceability.

## [0.8.3] — 2026-05-01

Patch bump. Fixes two onboarding bugs that surfaced when the second beta tester (#337 papa wave) tried to paste back his interview emission. The first click silently no-op'd because the body-level `hx-boost` was intercepting the form submit and HTMX was dropping the 400 response. The second click 500'd because `/app/.backups/` wasn't bind-mounted — the install-docker.md mkdir command and `ops/compose.yaml.example` volumes block both omitted it. Both fixes ship together so a fresh install on this tag onboards cleanly end-to-end.

### Fixed

- **Onboarding paste form opts out of `hx-boost` so 400 responses render natively (#364).** `base.html` has body-level `<body hx-boost="true">`, which converts every form submit to AJAX. HTMX, by default, does not swap responses with a non-2xx status — so the 400 returned on validation failure (missing paste blocks, missing OpenRouter key, OpenRouter smoke-check failed) was silently dropped, leaving the page unchanged with no user feedback. Clicking the Inject button looked like nothing happened. Mirrors the existing pattern in `speculative/_status_fragment.html` and `speculative/review.html`. Validated at production scale: papa's stack logged 15+ silent 400s before the patch landed. While here, replaces the single "Your paste is missing: X, Y, Z" error message with three diagnostic shapes (empty paste, no blocks parsed at all, partial paste with N-of-10 + which blocks) and tightens the OpenRouter empty-key + smoke-check messages so the underlying specific error (401/402/429/network) reads cleanly without a generic preamble. Regression test asserts `hx-boost="false"` literally on the form opening tag.
- **`backup_existing` short-circuits when nothing to back up + adds the `.backups` bind mount to template (#365, #366).** First-onboarding click was returning `PermissionError: [Errno 13] '/app/.backups'` because `backup_existing()` unconditionally called `mkdir(parents=True, exist_ok=True)` even on first runs with no existing state to preserve. The mkdir failed because `/app/` is root-owned in the image and `/app/.backups` was not bind-mounted. Two complementary fixes: (a) `ops/compose.yaml.example` adds `./state/.backups:/app/.backups` to the scheduler service volumes; `docs/setup/install-docker.md` step 1 adds `.backups` to the brace expansion. (b) Defense-in-depth: `backup_existing()` now walks the candidate sources first and returns the dest path without mkdir if none are extant — first runs never touch the path at all. The bind mount is only required on `?mode=rerun`, where there's actual state to preserve. Existing regression test renamed and reshaped to assert the new contract.

### Added

- **`/admin/stacks/` multi-tenant operator dashboard (#333, #355, #356, #357).** When `FINDAJOB_OPERATOR_MODE=1` is set on the operator's stack, a new route surfaces last-triage time, stage distribution, stuck-prep count, and last-failure timestamp for every `findajob-*` stack on the host. Top nav bar renders red on every page as an ambient cue that operator mode is active. Tester stacks unaffected — no flag, no route, no visual change. Auth inherits the existing `FINDAJOB_AUTH_USER`/`PASS` Basic Auth. Two follow-up fixes ship in the same release: cross-stack SQLite reads now use `?mode=ro&immutable=1` (foreign-uid reader against another stack's `pipeline.db` requires the immutable hint, not just `mode=ro`, because WAL/shm sidecar perms break the default), and the stuck-prep query reads `stage_updated` instead of an invented `prep_started_at` column.

### Migration required

- **Operator mode (#333) — operator's stack only.** If you want the `/admin/stacks/` dashboard, edit operator's `compose.yaml` to add:
  ```yaml
  services:
    scheduler:
      environment:
        FINDAJOB_OPERATOR_MODE: "1"
        # Optional — match this to your stack handle to float your own
        # row to the top of the dashboard. When unset, rows render
        # alphabetically.
        FINDAJOB_OPERATOR_HANDLE: "<your-handle>"
      volumes:
        - /opt/stacks:/opt/stacks:ro
  ```
  Apply with `docker compose up -d`. Tester stacks: leave both unset.

- **`.backups` bind mount (#365, #366) — every existing stack.** Add `./state/.backups:/app/.backups` to the scheduler service volumes block in your `compose.yaml`, and `mkdir -p ./state/.backups && sudo chown lad:lad ./state/.backups` on the host. Without this, first-run onboarding works (defense-in-depth covers it), but `?mode=rerun` would still 500 on the next attempt. The simplest path is to re-pull the compose template:
  ```bash
  cd /opt/stacks/findajob-<handle>
  curl -fsSL -o compose.yaml https://raw.githubusercontent.com/brockamer/findajob/main/ops/compose.yaml.example
  mkdir -p state/.backups && sudo chown lad:lad state/.backups
  docker compose pull && docker compose up -d
  ```

## [0.8.2] — 2026-04-30

Patch bump. Closes a longstanding orphan-folder leak in the prep-application pipeline by adding a watchdog sweep, complementing the existing `quarantine_stale_prep_folders` (#174) which only fires on the *next* prep attempt for the same job.

### Fixed

- **Watchdog now sweeps orphan prep folders into `companies/.stale/` (#TBD).** Top-level subdirectories of `companies/` that have no `jobs.prep_folder_path` pointing at them AND mtime older than `ORPHAN_FOLDER_MIN_AGE_MIN=120` (2h) are moved to `.stale/` on each watchdog cycle. Caused-by paths covered: (a) `prep_application.py`'s bare-except handler clears `prep_folder_path` from the DB but doesn't `shutil.rmtree` the partial folder; (b) `watchdog.run_watchdog`'s `reset_prep_to_scored` call doesn't have `outdir` info to clean up; (c) container OOM/SIGKILL during prep — process never wrote `prep_folder_path` to DB. The 2h grace prevents sweeping a folder mid-prep. Discovered when two stacks on a multi-tenant host accumulated stale folders from regenerate failures and various process kills; both stacks cleaned up at deploy time. 6 new unit tests cover the happy path, in-flight grace, db-tracked exclusion, underscore/dot exclusion, dst-collision skip, and missing-dir tolerance.

## [0.8.1] — 2026-04-30

Patch bump. Fixes a deployment-time gap in v0.8.0: `ops/compose.yaml.example` did not pass the new `FINDAJOB_<JOB>_SCHEDULE` / `FINDAJOB_<JOB>_ENABLED` env vars from `.env` into the container, so multi-tenant stagger overrides had no effect on existing stacks. Two coupled fixes ship together so a fresh-install operator can stagger schedules without editing compose.yaml by hand.

### Fixed

- **`ops/compose.yaml.example` now passes all 16 `FINDAJOB_<JOB>_*` override vars through to the scheduler service** with default-empty values. Existing operators on v0.8.0 must re-pull `compose.yaml` from `main` (or hand-edit) before per-stack stagger overrides will apply.
- **`scripts/render_crontab.py` treats empty-string env vars as "no override"** — falls through to the YAML default. Required because compose.yaml.example sets all 16 vars to default-empty; without this, a stack with no overrides set would render an empty schedule and break supercronic.

### Migration required

For operators on `:v0.8.0` who want to use per-stack stagger overrides:

1. Re-pull `compose.yaml` from main: `curl -fsSL -o compose.yaml https://raw.githubusercontent.com/brockamer/findajob/main/ops/compose.yaml.example` (preserve any local customizations like watchtower labels).
2. Edit `.env` to set `FINDAJOB_TRIAGE_SCHEDULE=...` etc. for each override.
3. `docker compose pull && docker compose up -d`.
4. Verify: `docker exec <container> cat /app/crontab` shows the overridden schedule.

For operators on `:v0.8.0` not staggering, no migration needed — defaults preserve all behavior.

## [0.8.0] — 2026-04-30

Minor bump. Closes the loop on the company discoverer with operator-facing surfacing (Dashboard widget + ntfy push), and replaces the static `ops/crontab` with a declarative YAML manifest that lets multi-tenant hosts stagger same-TZ stacks without forking the image. Both ship clean — no migration steps required for existing operators; defaults preserve all prior behavior.

### Added

- **Discovered-companies surfacing on the Dashboard + ntfy on weekly run (#288).** Two long-standing gaps in the company discoverer (#284) are addressed in one merge. (a) After each successful run of `findajob.discoverer.runner.run()`, a single ntfy fires with the count + top-5 names — title `findajob: discovered N companies`, body `Lightmatter, Lambda Labs, Hyperbolic, ...` (or `(no novel companies surfaced this run)` when zero). The success ntfy is best-effort and does not replace existing failure ntfys (`discovery: timeout`, `discovery: parse error`); both can fire on a high-cost successful run. (b) A small banner on `/board/dashboard` reads the `discovered_companies.json` sidecar and shows count + last-run date (e.g., "🔍 Discoveries: 10 companies (updated 2026-04-26) — view") with a click-through to the file. Five visual states: fresh, this-week-late, stale (>10d, surfaced as "weekly cron may have skipped"), empty-never-run (informative copy for fresh installs), and empty-zero-hits. New `findajob.web.discoveries.load_discoveries_summary()` helper reads JSON, never markdown — with a defensive None return on missing/malformed files so a bad weekly run cannot break the Dashboard. 12 new tests cover the helper edge cases + the 5 visual states. Sections C (weekly diff) and D (promote-to-target affordance) split out as follow-up issues.

- **Scheduled jobs as data — `ops/scheduled-jobs.yaml` + per-job env overrides (#344).** Replaces the static `ops/crontab` text file with a declarative manifest. At container start, `scripts/render_crontab.py` reads the YAML + env-var overrides and writes `/app/crontab` before `exec supercronic`. Per-job knobs follow a single convention — `FINDAJOB_<JOB>_SCHEDULE` (cron expression replacement) and `FINDAJOB_<JOB>_ENABLED` ("true"/"false") — so multi-tenant hosts can stagger same-TZ stacks without forking the image. All 8 existing scheduled jobs (triage, watchdog, notify-apply / -stats / -health / -issues / -feedback, discover, rag-rebuild) migrate 1:1; the previously-disabled `notify-scoreboard` is now declaratively `enabled: false` instead of a commented-out crontab line. Fail-fast behavior on malformed YAML / unrecognized override values surfaces as a noisy container restart loop rather than a silent fallback. 18 unit tests cover the renderer (env overrides, fail-fast, hyphenated job keys, truthy aliases) and 3 sanity checks against the live YAML. The legacy `ops/crontab` is removed; migration safety was verified pre-merge by diffing the rendered output (no overrides) against the legacy file as a temporary `ops/crontab.legacy` snapshot. **Not migration-required** for operators — defaults preserve existing behavior; opt-in only when staggering is desired.

## [0.7.4] — 2026-04-30

Patch bump. Two material-viewer follow-ups land together: (1) per-doc plain-language descriptions that name the role + employer for submission artifacts and flag internal-prep docs as "for your eyes only," and (2) a robust `.docx` download path that survives reverse-proxy header mangling on internet-exposed instances.

### Changed

- **Per-group descriptions explaining what each artifact is + when to use it (PR #347, commit `151be87`).** Each materials group now carries a plain-language description rendered in the group header. Resume / Cover Letter interpolate the actual `{title}` and `{company}` from the database (e.g., "Your resume tailored to Senior Field Applications Engineer at Supermicro. The .docx is what you submit with the application."). Internal-only docs — Briefing, Resume Changes, Recruiter Critique — explicitly read "for your eyes only" so testers don't accidentally paste them into applications. Per-file rows show a format-specific hint (Outreach `.txt` → "paste into LinkedIn DM or email"; Briefing `.docx` → "printable copy") replacing the generic "best for applications" copy from 0.7.3 that incorrectly appeared on every `.docx` including non-submission artifacts. 6 regression tests lock the discipline (descriptions interpolate; briefing must NOT mention "submit"; internal-prep docs must contain "for your eyes only"; outreach hint names paste destination).

### Fixed

- **`.docx` download now reliably saves the file instead of rendering bytes as text (PRs #348 + #349, commits `764293c` + `3030637`).** On internet-exposed instances behind a Synology reverse proxy, the proxy was stripping or rewriting the `Content-Disposition: attachment` / `Content-Type: application/octet-stream` headers on `.docx` responses, so browsers rendered the binary bytes as on-screen text. Server-side headers had been correct since #152; the proxy was the missing link. The Download control is now a `<button>` (not an `<a download>`) that fetches the file via JS, builds a Blob locally, and triggers a download from a same-origin object URL. The browser's only signal is the synthetic `<a download>` click on a Blob it created itself, so reverse-proxy response headers are bypassed entirely. The first attempt (#348) used an `<a>` with `@click.prevent` and produced a race where both the native navigation and the JS download fired together; #349 replaced the anchor with a button to remove the native default behavior cleanly.

## [0.7.3] — 2026-04-29

Patch bump. Materials folder page redesigned for clarity: prep folders had 11 files in a flat alphabetical list with identical link styling for `.md` (preview) and `.docx` (download), and the variable part of each 60+ char filename (Briefing/Cover/Resume) was buried in the middle. Three coupled improvements address it.

### Changed

- **Grouped folder view by document type, ordered by workflow (commit `70dbb60`).** Files now group into labeled cards — JD → Briefing → Resume → Resume Changes → Cover Letter → Outreach → Recruiter Critique → Review Checklist → Other. Within each group, `.md` sorts before `.docx` so the in-browser preview is the first option presented. New `_group_files()` helper in `routes/materials.py` keys on document-type substrings (e.g., ` Briefing - `) rather than the leading display_name, so the classifier survives display_name changes (#335) across testers. Speculative submission folders' bare `briefing.md` gets its own "Briefing (speculative)" bucket. 17 new unit tests lock the workflow ordering and the Resume-vs-Resume-Changes disambiguation discipline.
- **Distinct View vs. Download affordances on every file card (commit `70dbb60`).** Each card now shows an MD/DOCX/TXT colored badge, a human-readable description ("Markdown — preview in browser, or copy + paste into Google Docs" / "Word document — best for applications; drag to Google Drive to open in Docs"), file size + UTC mtime, and a colored action button — green "View →" for `.md`/`.txt`, blue "↓ Download" for `.docx` and other binaries.
- **Copy-MD button on every `.md` card + Google Docs workflow helper banner (commit `0b0aa00`).** New `?raw=1` query option on the existing `/materials/{fp}/{filename}` route returns the `.md`/`.txt` source bytes as `text/plain`. The Copy-MD button on each `.md` card fetches that endpoint and pipes the response into `navigator.clipboard.writeText()` — what lands on the clipboard is byte-identical to the file on disk, same as if the user selected the source view text and hit Ctrl+C. Pasting into a fresh Google Doc preserves the markdown formatting (headings, lists, bold/italic) via Google Docs' auto-detection. A dismissible helper banner at the top of the folder page explains both workflows: Copy-MD for fast paste-into-Docs, .docx → Google Drive for the resume + cover letter where formatting matters for applications. Uses Alpine.js (already loaded). 3 new tests lock the byte-identical guarantee + the `.docx` fall-through behavior.

### Fixed

- **`.docx` files now reliably trigger Save instead of rendering as binary in the browser (commit `70dbb60`).** Server-side already set `Content-Disposition: attachment`, but some reverse-proxy configurations and browser combinations could render the response inline. The Download button now also carries the HTML5 `download` attribute as belt-and-suspenders, forcing browser-side save regardless of upstream header rewriting.

## [0.7.2] — 2026-04-29

Patch bump. Two non-functional shipments: docs catch-up on the structural reshape that landed mid-day (Decision 18 — milestone date compression to ~30-day window + version-codename convention) and copy improvements to the onboarding interviewer prompt that smooth out four observed friction points before the next tester onboards.

### Changed

- **Onboarding interviewer prompt clarified (commit `efb14fd`).** Four substantive copy improvements: (1) header rule clarified — only markdown / YAML files get the "Generated by …" attribution line; the three plain-text files (`display_name.txt`, `timezone.txt`, `ntfy_topic.txt`) and the optional `voice-samples.md` emit ONLY their value content (a leading comment line breaks parsing). (2) Phase 1 orientation now sets the "next" / "redo" gate language up front so the user isn't surprised by the per-file review pause later. (3) Phase 2 resume upload has an explicit paste-as-fallback path for users without a resume document on hand, with a candid note that a real document produces a noticeably better first run. (4) Phase 1 tab-loss warning ("you lose this tab, you start over — about 90 minutes — so keep it pinned"). (5) Phase 1 captures whether the user is self-hosting or being onboarded by an operator who'll provide the OpenRouter key; gates the Phase 5 signup walkthrough on that answer.

### Docs

- **Decision 18 recorded in `docs/roadmap.md` (commit `0dd3592`).** Second structural reshape on 2026-04-29 compressed all milestone dates into a single ~30-day window (GA 2026-05-12, v0.9 2026-05-18, v1.1 2026-05-22, v1.4 2026-05-25, v1.2 2026-05-27, v1.3 2026-05-29) and recorded the version-codename convention (chronological order is `GA → v0.9 → v1.1 → v1.4 → v1.2 → v1.3` — versions are codenames, not semver). Bundle of metadata moves applied at the same time. Milestone due dates were already updated on GitHub at reshape time; this commit catches the canonical roadmap doc up.

## [0.7.1] — 2026-04-29

Patch bump. One critical bugfix for internet-exposed tester deployments: in-app materials links now use same-origin relative URLs instead of an absolute `http://docker.lan:8090` prefix, so testers reaching their stack via `findajob-{handle}.<operator-domain>` no longer get dead links back into the operator's LAN. One small notify-side fix carried from earlier today (#343).

### Fixed

- **In-app materials links now use same-origin relative URLs (commit `74935d5`).** Two Jinja templates (`_job_row.html`, `_company_history_cell.html`) prefixed company-cell hyperlinks with `FINDAJOB_MATERIALS_BASE_URL`, which defaulted to `http://docker.lan:8090` per stack. For testers reaching their stack at `https://findajob-{handle}.<operator-domain>`, every "click company" link rendered as `http://docker.lan:8090/materials/{fp}` — unreachable from outside the operator's LAN. Materials are served by the same FastAPI app at `/materials/{fp}`, so the link is correctly same-origin relative — works through Wireguard, internet exposure, and reverse proxies identically. The env var (`FINDAJOB_MATERIALS_BASE_URL`) remains correct in `sync_sheet.py` (Sheet hyperlinks render outside FastAPI) and `notify.py` (ntfy notifications render in the user's notification client) — both are external surfaces that need absolute URLs. Vestigial `materials_base_url` reads in `routes/board.py` are harmless and a follow-up cleanup can remove them. Tests previously asserted the buggy absolute-URL form; now assert relative `/materials/{fp}`.

- **Orphan-folder health check now skips dotfile dirs (#343, commit `d85d84e`).** `notify.py orphan-folder-check` was flagging `.stale/` and other dot-prefixed bookkeeping dirs as orphan companies, polluting the daily health-check ntfy. The check now skips any directory whose name starts with `.`.

## [0.7.0] — 2026-04-29

Minor bump. Three substantive shipments unblock the v0.9 multi-tenancy push: per-tester onboarding now collects each tester's own credentials and identity (#328), the web UI can be gated behind opt-in HTTP Basic Auth for internet-exposed deployments (#327), and the speculative-ingest path reuses its deep-research briefing at prep time instead of regenerating it (#320). One critical proxy-headers bugfix (commit `e6de82d`) that any operator running findajob behind an HTTPS reverse proxy needs — without it, FastAPI's auto trailing-slash redirect leaks the request out of HTTPS into bare HTTP. One soft schema migration carried by #320 (idempotent column add — runs automatically on `docker compose pull && up -d`).

### Fixed

- **`uvicorn` now trusts upstream `X-Forwarded-Proto` headers (commit `e6de82d`).** Without `--proxy-headers`, FastAPI's auto trailing-slash redirect (e.g., `GET /materials` → `307 Location: /materials/`) emitted absolute Location URLs with `scheme=http` even when the original request arrived via HTTPS at a TLS-terminating reverse proxy. Browsers followed the redirect to bare HTTP; on Synology DSM (and similar setups without an explicit `:80` vhost for the findajob host), the request fell through to the DSM admin redirect on `:5001`, surfacing as a "redirected to hostname:5001" symptom for any internet-exposed instance. Fix is two flags appended to the entrypoint's uvicorn command: `--proxy-headers --forwarded-allow-ips='*'` (the perimeter is the reverse proxy itself; the container only ever receives traffic from trusted upstream nodes — Wireguard mesh + the operator's reverse-proxy box). Reproduced and verified end-to-end on the operator's `findajob.brockbot.com` instance: pre-fix `Location: http://findajob.brockbot.com/materials/` → DSM bounce to `:5001`; post-fix `Location: https://findajob.brockbot.com/materials/` → clean follow.

- **`read_file_prefix()` now consumes `display_name.txt` first (#328 follow-up).** The structured field that #328 added to onboarding was being WRITTEN but not consumed — `findajob.utils.read_file_prefix()` continued to parse profile.md narrative for `File Prefix:` / `Name:` fields, the exact fragile path the structured field was meant to replace. Resolution order is now: `display_name.txt` (last word) → legacy `File Prefix:` line → legacy `Name:` line → `Candidate` fallback. Sibling-of-profile.md path resolution (not BASE-relative), so tests stay deterministic. Pre-#328 deployments without `display_name.txt` keep working via the legacy paths — no migration needed. 12 new tests in `tests/test_read_file_prefix.py` exercising every branch of the resolution order.

- **Speculative briefing is reused at prep time instead of regenerated by `briefing_writer` (#320, spec drift from #131).** When the operator approves a `[SPEC]` row from the speculative review page and later flags it for prep, `prep_application.py` now reads the deep-research briefing from `companies/{Company}_SPECULATIVE_{date}_{HHMMSS}/briefing.md` (the file Sonar Deep Research wrote at submit time and the operator approved on the review page) instead of regenerating a fresh briefing via `company_researcher` + `briefing_writer` Opus 4.7 calls. Spec called for "one briefing, per-role prep folders" but B4 didn't wire the read side. New `jobs.speculative_briefing_folder TEXT` column populated by `findajob.speculative.approver` at approve time and read by prep — degrades gracefully (falls back to the regular `briefing_writer` flow) when the column is unset, the folder is missing, or briefing.md is empty. Saves $0.10–$1.20 of Opus tokens per prep depending on how many cards from the same submission get prepped, and eliminates drift between approve-time and prep-time briefings. New `speculative_briefing_reused` / `speculative_briefing_missing` events emitted to `pipeline.jsonl` for auditability. Pre-#320 already-approved synthetic rows in the operator's DB will have `speculative_briefing_folder=NULL` and continue to use the old regenerate path on any re-prep — intentional degrade behavior, not a bug; backfill is feasible but out of scope.

### Migration required

- (#320) `jobs.speculative_briefing_folder` column adds via idempotent `ALTER TABLE ADD COLUMN`. Runs automatically on next `docker compose up -d` via `init_db.py`. No operator action required beyond `docker compose pull && up -d`. Existing approved synthetic rows are unaffected — they keep working via the fallback path.

### Added

- **GA onboarding now collects user credentials and identity (#328).** The onboarding interview emits three new required blocks — `display_name.txt` (used by `prep_application.py` to derive a deterministic materials-filename prefix instead of fragile narrative-form parsing), `timezone.txt` (single-line IANA tz; written to `data/timezone` for the operator to reflect in their stack's `compose.yaml` `TZ` env var so supercronic crons fire at the user's wall-clock time), and `ntfy_topic.txt` (parsed and merged into `data/.env` as `NTFY_TOPIC=...` so notifications reach each user's own topic). The user's **OpenRouter API key** is collected via a dedicated form field on `/onboarding/` (kept out of the chat-LLM emission so it never enters claude.ai / ChatGPT logs) and merged into `data/.env`. Before writing the sentinel, the injector smoke-checks the key with a 1-token completion against `google/gemini-3-flash-preview` via OpenRouter — invalid or unreachable key surfaces a friendly error in the UI and the user re-pastes with a correction (files are already committed; next paste overwrites cleanly). RapidAPI and Google embedding keys remain operator-shared (no per-user collection); Google Sheets credentials are not collected at all (Sheet path retiring per #331). New module `findajob.onboarding.openrouter_smoke`; `merge_env_content()` helper preserves all existing `data/.env` keys across the merge (so operator-shared `RAPIDAPI_KEY` / `GOOGLE_API_KEY` survive). 24 net new tests covering the smoke check's failure modes, the env-merge semantics, the new file destinations, and the route's error rendering.

- **Optional HTTP Basic Auth on the findajob web UI for internet-exposed instances (#327).** Per-tester instances reachable at `https://findajob-{tester}.example.com` no longer have to rely on the Wireguard perimeter alone. New FastAPI middleware (`findajob.web.auth.BasicAuthMiddleware`) gates every request to the web UI behind HTTP Basic Auth when `FINDAJOB_AUTH_USER` and `FINDAJOB_AUTH_PASS` env vars are both set; allowlist for `/healthz`, `/static/*`, and `/favicon.ico` so health checks and the auth-prompt page itself still render. Constant-time credential compare via `hmac.compare_digest`. When env vars are unset the middleware is not installed at all — Wireguard-only deployments and local-dev loops are unchanged. Threat model is drive-by scanning of the open internet (TLS terminates upstream, Firewalla/equivalent restricts geography); per-user identity / RBAC remains intentionally out of scope. New pattern doc at `docs/setup/internet-exposure.md` (also reachable at `/docs/setup/internet-exposure` in-app). Roadmap Decision 16 supersedes Decision 3 for the public-exposure case. 14-test canary suite at `tests/test_web_basic_auth.py` — including a check that the gate fires for protected routes — guards against accidental middleware-order regressions in `app.py`.

## [0.6.1] — 2026-04-28

Patch bump. Two small bugfix PRs against board interactions. Bugfix-only — operators pinned to `:v0.6` pick this up automatically on `docker compose pull && up -d`. No migration required.

### Fixed

- **Speculative job titles now link to a JD viewer instead of a dead `speculative://` URL; ENUM/DATE filter Apply buttons now actually filter; `/materials/{fp}` no longer 404s for in-flight speculative rows (#324).** Three small UI defects surfaced during board exploration. (1) `_job_row.html` rendered every title as `<a href="row.url">` regardless of synthetic-ness, so `[SPEC]` rows produced an unclickable `speculative://...` sentinel. The template now branches on the `[SPEC]` prefix and links to a new `GET /jobs/{fp}/jd` route that renders `jobs.raw_jd_text` (the role-card description) as Markdown. (2) ENUM and DATE filter popovers were silently no-op'ing on Apply because `filters.js` fired `htmx.trigger(hidden, 'change')` against hidden inputs that had no `hx-*` attrs — htmx only reacts to elements it has been wired against. The hidden ENUM input and the date `_from` input now mirror the text/number inputs' HTMX contract. (3) `/materials/{fp}` 404'd for synthetic rows that hadn't been flagged for prep yet (no `prep_folder_path` until then). Synthetic rows now 303-redirect to the JD viewer; real-job 404 path is unchanged. Four regression tests added.
- **interview_prep treats orphaned sentinels as stale instead of blocking forever (#325, closes #312).** `scripts/interview_prep.py` wrote `.interview_prep_in_progress` at start and removed it in a `try/finally`. A process killed before cleanup (OOM, kill -9, container restart) left the sentinel in place permanently — every future Interviewing click for that job became a silent no-op until the operator manually deleted the file. The existence-check now reads mtime: anything older than `SENTINEL_STALE_AFTER_SECONDS` (600s, well above observed Opus 4.7 generation time of ~2 min) is treated as orphaned, removed in place, and emits an `interview_prep_sentinel_stale_removed` event to `pipeline.jsonl` for auditability. Fresh sentinels still short-circuit as before. Logic factored into `_sentinel_blocks_run()` for isolated unit testing.

## [0.6.0] — 2026-04-28

Minor bump. The headline shipment is **#131 speculative ingest end-to-end** — a new cold-outreach path for companies that aren't currently posting a matching role. Operator submits a company name, the pipeline runs Perplexity Sonar Deep Research and Claude Sonnet 4.6 to synthesize 1–5 plausible role cards, operator approves on a review page, and `[SPEC]`-prefixed `jobs` rows land on the dashboard ready for prep + cold outreach. Schema migration carried in PR #315 (B1 of 4): new `jobs.synthetic` column + new `speculative_requests` table, both apply automatically on first container restart via `init_db.py`. Two operator-facing UX bugfixes (#319 redirect-after-approve, #314 PII-hook silent-fail defense-in-depth) round out the release.

### Added

- **Speculative ingest end-to-end (#131).** New cold-outreach path for companies that aren't currently posting a matching role. Operator submits a company name through the new **Speculative** tab on `/ingest/`; the pipeline runs Perplexity Sonar Deep Research (`candidate_led_briefing` role) for the briefing, then Claude Sonnet 4.6 (`speculative_roles_synth` role) to synthesize 1–5 plausible role cards aligned to the candidate's master resume. Async UX: form POST returns immediately with a status page that polls every 5s via HTMX until research completes (1–5 min). The review page renders the briefing + role cards with default-checked Keep boxes; **Approve** writes one `[SPEC]`-prefixed `jobs` row per kept card with `synthetic=1` and `source='web_speculative'` (stage `scored`), **Regenerate** re-runs the synth step (preserving briefing for cheap retries), **Trash** drops the submission with no DB rows written. Approved rows can be flagged for prep like real rows; cover letter and outreach drafts auto-detect synthetic mode (via `<<SPECULATIVE_MODE>>` marker injection in `prep_application.py` + `find_contacts.py`) and write cold-outreach framing — opens with explicit acknowledgment that there's no posting, leads with hiring-signal from the briefing, ends with a low-pressure ask. The Dashboard's "Applied" dropdown becomes "Sent Outreach" for synthetic rows (hits the same `/apply` endpoint, but the server reads `jobs.synthetic` and writes `audit_log.changed_by='outreach_button'` for stats — apply-gate query unchanged, so cold outreach counts toward the daily 3/day gate). Synthetic rows are firewalled from scorer training: write-time guard in `handle_rejection` skips `feedback_log` for `synthetic=1`, read-time filter in `_build_feedback_block` LEFT JOINs and excludes synthetic — defense-in-depth so synthesizer hallucinations cannot contaminate the scorer. Watchdog flips `speculative_requests` rows stuck in `status='researching'` >10 min to `failed` (catches silent subprocess deaths so the operator's status page surfaces Retry/Trash instead of polling forever). Schema migration: new `jobs.synthetic` column (default 0) + new `speculative_requests` table with status-enum CHECK constraint. Spec at `docs/superpowers/specs/2026-04-28-speculative-ingest-131-design.md`; closes #131.

### Fixed

- **Speculative review/status forms now follow 303 redirects after Approve/Trash/Regenerate (#319, PR #321).** `base.html` applies `hx-boost="true"` globally, which intercepted the speculative forms' POSTs and swallowed the server's `Location` header. Operator clicked Approve and saw "nothing happen" until manually navigating to `/board/`. Fix is one attribute per form: `hx-boost="false"` on the three speculative forms (review's approve/regen/trash trio + status fragment's retry/trash on `failed`). Browser does normal navigation; status page's HTMX poll is unaffected (it lives on a `<div>`, not a form).

### Security

- **CI-side PII scan + diagnostic line on the local pre-commit hook (#314, PR #322).** During #258 release prep two CHANGELOG additions containing operator's first name committed cleanly despite the local hook's PATTERNS array including `\bBrock\b`. Repro in current env shows the hook fires correctly — most likely a one-time `--no-verify` slip — but the silent-fail mode is real either way. Three layers of defense: (1) new `.github/workflows/pii-scan.yml` runs on every PR, scans the diff against patterns from a GitHub Secret `PII_PATTERNS_REGEX`, fails the check if any pattern matches (matched pattern is logged but the matched line is NOT, to avoid leaking PII to public CI logs); (2) the local hook now prints a one-line stderr diagnostic per run (`pre-commit: PII scan: N patterns × M added lines`), so silent-fail conditions are observable; (3) `docs/setup/configure.md` documents the secret-install recipe. Operator action: set the `PII_PATTERNS_REGEX` GitHub Secret post-deploy per the recipe in the docs.

### Migration required

- (#131) `jobs.synthetic` column adds via idempotent `ALTER TABLE ADD COLUMN` (default 0); `speculative_requests` is a fresh `CREATE TABLE` with a `CHECK` constraint on the status enum. Both run automatically on next `docker compose up -d` via `init_db.py`. No operator action required beyond `docker compose pull && up -d`.

## [0.5.2] — 2026-04-28

Patch bump. Two substantial Added shipments — the new interview-prep artifact triggered on the board's `applied → interview` transition (#258) and the in-app feedback widget that files GitHub issues per submission (#227) — plus the jobs-api14 Indeed slot drop (#274), three quality bugfixes (#308 Ashby timeout, #302 probability_score visibility, #280 Archive horizontal shift), and two documentation passes (README polish, CLAUDE.md scheduler-row drift fix). One soft-migration marker below for the feedback widget's optional GitHub PAT — the pipeline stays up and only the feedback path degrades if the PAT is unset, so existing operators on `:v0.5` can `docker compose pull && up -d` without reading anything if they don't care about the widget.

### Added

- **Interview prep artifact generated on the `applied → interview` board transition (#258).** New role `config/roles/interview_prep.md` (Opus 4.7, max_tokens 4096) and entry-point `scripts/interview_prep.py`. Marking a job as **Interviewing** on the Applied tab spawns the generator as a detached subprocess; it reads the existing prep folder (briefing, tailored resume, cover letter, optional recruiter critique) and emits `{Prefix} Interview Prep - {Company} - {Title} - {timestamp}.md` (+ `.docx`) alongside the other artifacts (`{Prefix}` derived from each operator's `profile.md`). Five sections: lead-with-this opener, 30-second elevator pitch, 3–5 STAR expansions of the briefing's existing question/story map, 3–5 tough Qs with draft answers (incl. career-narrative traps and recruiter-critique gaps), and 5 concrete questions to ask the interviewer. Anti-drift contract in the role prompt: STAR section MUST expand the briefing's `❓ Likely Interview Questions` and `💡 Stories from Your Background` sections rather than re-derive them, preventing the parallel-slop failure mode where two prompts produce overlapping question lists with subtly different framings. Re-clicking "Interviewing" on an already-interview job re-launches the generator (refresh mechanism after a recruiter sends panel info); a sentinel file in the prep folder guards against concurrent runs. Trigger lives in `findajob.web.routes.board_actions.interview` rather than `prep_application.py` — the `applied → interview` rate is ~5%, so generating at apply time would waste ~95% of Opus 4.7 tokens on jobs that never reach an interview. Briefing sections 4–5 stay untouched and continue to serve their apply-time decision-preview role.

- **In-app feedback widget files a GitHub issue per submission (#227).** Floating "Feedback" button in the bottom-right corner of every page opens a small modal with a textarea; submission posts to `/feedback/submit` which calls the GitHub Issues API server-side and labels the issue `feedback` (plus an optional per-stack identifier label). New env vars in `state/data/.env`: `GITHUB_FEEDBACK_PAT` (fine-grained PAT scoped to Issues:read+write on the target repo, required), `FEEDBACK_STACK_LABEL` (optional second label, e.g. `from:operator` / `from:alice-doe`), `FEEDBACK_REPO` (defaults to `brockamer/findajob`). The PAT is held server-side and never reaches the browser. No PII guard, no rate limit — testers see what they're typing and the feedback path is internal to the Wireguard perimeter.

### Changed

- **Dropped the jobs-api14 Indeed slot from daily triage (#274).** Lifetime data showed 0.08% application rate (3 of 3,584 ingested) and 0.77% LLM-precision at score≥7 — vs 4.2% LLM-precision for Greenhouse on the same scorer. Root cause is upstream: jobs-api14's Indeed endpoint accepts no recency, level, or employment-type filter, so its keyword matching returns large volumes of off-target rows ("Patient Engagement *Center* QA Analyst" for `query=data center operations manager`, etc.). The LinkedIn slot in the same fetcher remains — it accepts `datePosted` + `experienceLevels` filters and is producing useful signal. Indeed coverage continues via `gmail_indeed` (LinkedIn / Indeed alert emails parsed from Gmail). Cuts ~$4/month of pure-noise LLM scoring spend and removes ~120 daily off-target rows from triage. The historical 3,584 rows remain in the DB and are still filterable via the `source` column on the board's Archive tab. See the issue thread for the diagnostic + market-survey writeup of replacement candidates (JSearch, Adzuna) — tracked as separate follow-up issues.

### Fixed

- **Ashby fetcher timeout bumped 15s → 30s with single retry on `Timeout` (#308).** The `Crusoe` Ashby slug was timing out on ~25% of daily triage runs (4 of 16 attempts in the last two weeks), all with `read timeout=15`. Other slugs were unaffected. Bumping the per-request timeout to 30s and adding a single retry on `requests.exceptions.Timeout` only (not on permanent errors like 4xx/5xx) lets a one-off slow upstream recover without losing a day of postings. Worst-case daily-triage impact: +30s if every Ashby slug times out twice (extremely unlikely; observed pattern is one slow slug per run, not all of them).

- **probability_score column visible by default + canonical score-column order on Dashboard and Waitlist (#302).** The `probability_score` column had `default_visible=False` in the per-column filter framework's registry (`src/findajob/web/filters/registry.py`), so the operator never saw the briefing-derived probability average on triage views. The column declarations also had `interview_likelihood` after `probability_score`, so even when toggled on via `?cols=`, the rendered order didn't match expectations. Both fixed: `probability_score` is now `default_visible=True`, and the four score columns now render in the canonical order `relevance_score → interview_likelihood → fit_score → probability_score` — putting compositional/relative signals on the left and derived briefing scores on the right. Two parametrized regression tests in `tests/test_filter_score_columns.py` pin both invariants for every tab declaring all four scores.

- **Archive tab no longer shifts horizontally relative to other board tabs (#280).** Root cause: the `html` element didn't reserve the vertical-scrollbar gutter, so tabs whose content overflowed vertically (Archive with thousands of historical jobs) rendered ~15px narrower than tabs that fit on screen (Dashboard with a handful of pre-application rows). Switching between them via hx-boost made the content area "shift right" because the scrollbar appeared on Archive but not on Dashboard. Fix: one CSS rule, `html { scrollbar-gutter: stable; }`, in `static/app.css`. Reserves the gutter on every page regardless of content height; layout is now consistent across all six board tabs and all other routes that extend `base.html`.

### Documentation

- **README polish: roadmap section + project board link + reader-approachability pass.** Added an "Is this for you?" expectation-setting block, a "Roadmap" section summarizing the five active milestones with live link to the [project board](https://github.com/users/brockamer/projects/1), reframed "What you get out of it" bullets around felt relief instead of features, collapsed the 12-row docs index behind `<details>` with 4 starter links surfaced above, and added a "Stay in touch / contribute" footer pointing at the project board, issues, in-app feedback widget, and discussions. Tightened the lead so "burnout is the default" lands harder; added explicit "pre-1.0 personal project" framing.

- **CLAUDE.md scheduler-row drift fix.** The Pipeline Context Table's Scheduler row said "systemd user services" but the live deployment uses supercronic in the container (correctly described in the Container Context table 30 lines below). Reconciled.

### Migration required

- Set `GITHUB_FEEDBACK_PAT` in each stack's `state/data/.env` before the next `docker compose up -d`. Without it, the widget renders but submissions return a 503 with a friendly "feedback isn't configured on this stack" message — i.e. the app stays up; only the feedback path is degraded. Generate a fine-grained PAT at github.com/settings/personal-access-tokens/new with `Issues: read+write` scoped to the target repo only. Optionally set `FEEDBACK_STACK_LABEL=from:<stack>` per stack so triage can tell operator-stack and tester-stack reports apart.

## [0.5.1] — 2026-04-26

Patch bump. Two bug fixes that surfaced after v0.5.0 deployed: the discoverer's atomic-replace writer was creating files at mode 0o600 (the `tempfile.mkstemp` default), which made them unreadable by the FastAPI process when written by a different user, and the scorer's JSON parser was failing on prose-prefixed LLM responses (~5–10/triage cycle on the operator stack). Rolling `docker compose pull && up -d` picks both up.

### Fixed

- **Discoverer outputs now mode 0o644; /config/ shows readable errors instead of silently 500ing (#289).** Two-part defense-in-depth fix: (1) `findajob.discoverer.writer.commit_atomically` now `os.chmod(0o644)` after staging both the markdown and JSON outputs — `tempfile.mkstemp` defaulted to 0o600, so files written by a non-FastAPI user (e.g., manual `docker exec` as root) were unreadable when the web server tried to render them in the editor; (2) `GET /config/files/{path}` now catches `PermissionError` and `OSError` on the read path and renders an inline red error block in the editor template, naming the failure mode and pointing at chown/chmod as the host-side fix — previously the exception 500ed silently into HTMX with no visual indication anything was wrong.
- **Scorer recovers from prose-prefixed and fenced LLM JSON output (#278).** Symptom on the operator stack: 43 `score_validation_failed` events in pipeline.jsonl over ~36h, all with identical parser error `JSON parse: Expecting value: line 3 column 21 (char 50)` — the scorer model (DeepSeek v3.2) sometimes prepended prose to its JSON or wrapped it in markdown fences embedded in prose, and the previous fence-stripper only handled the "entire response is wrapped in ```...```" shape. New `findajob.utils.extract_json_payload` helper handles four shapes: whole-response fence (with or without language tag), fenced JSON block embedded in prose, bare JSON after prose, and the no-JSON fallthrough. Both `validate_llm_json` and `_normalize_llm_output` route through it. Defense-in-depth: `score_validation_failed` events now carry `raw_excerpt` (first 500 chars of the LLM response) so any remaining failure modes are diagnosable from pipeline.jsonl without redeploying a debug branch.

## [0.5.0] — 2026-04-26

Minor bump. Three additive board+pipeline shipments and one Archive-tab bug fix. Adds a per-column filter framework on every board tab (#273) that bookmarks any view via URL query params, introduces dynamic competency-driven company discovery (#284) as a new weekly Sunday-02:00 cron job, and unsticks the Archive-tab Promote button on score-6 stage='scored' rows (#282). One `migration-required` marker below — the new scheduled job is image-baked, but operators should know it will start firing.

### Added

- **Per-column filter+sort framework on every board tab (#273).** Replaces
  the single `?q=` text input with type-aware filters: TEXT (substring),
  SCORE / INTEGER (min/max range), ENUM (multi-select via comma-separated
  values), DATE (from/to range). Sort changes preserve filter state and
  vice versa. All state lives in URL query params (`hx-push-url`), so any
  view is bookmarkable + shareable. A 🔗 Copy-link button on every tab
  writes the current URL to the clipboard. Column visibility supports
  explicit override via `?cols=a,b,c`. The framework lives in
  `findajob.web.filters` as a declarative `ColumnSpec` registry; new
  board tabs declare their column specs and the filter UI + SQL composer
  apply automatically. Per-tab default-visible columns retuned: Dashboard
  surfaces AI notes + Likelihood by default and hides Probability + Stage
  (filterable via `?stage=...` for score-5/6 triage); Waitlist gains
  Likelihood for parity with Dashboard's scoring trio.
- **Surfaceable score-5/6 jobs on the Dashboard.** Visit
  `/board/dashboard?relevance_score_min=5&stage=scored,manual_review` to
  see jobs the prior 7+ default hid. The 7+ happy path is unchanged on
  cold load. Followed by #277 (Columns dropdown UI + per-tab pref
  persistence) and #276 (scorer-side IC-vs-manager noise reduction).
- **Dynamic company discovery (#284).** New `company_discoverer` role
  (`openrouter:perplexity/sonar-reasoning-pro`, ~$3-5/run) runs weekly on
  Sunday 02:00 and after onboarding completion. Emits
  `candidate_context/discovered_companies.md` (human-readable, gitignored)
  + `.json` sidecar (machine-readable consumer contract for #285 scorer
  rewire and #283 Greenhouse-slug derivation). Augments — does not
  replace — the static `## Target Companies / Organizations` profile
  section: the static list now carries strategic preference, the
  discovered set carries competency-fit (orthogonal signals). Field-
  agnostic by design; same prompt produces sensibly different outputs for
  operators in different fields. Cost soft-guardrail: ntfy warning when
  any single run reports >$10 (configurable via
  `DISCOVERY_COST_THRESHOLD_USD`).

### Fixed

- **Archive-tab Promote 409 on score-6 rows (#282).** The `/board/jobs/{fp}/promote`
  handler accepted only `stage='manual_review'`, but the Archive-tab template
  renders the Promote button on `stage='scored'` rows (operator's bump-score-6-to-7
  path). Clicks failed silently with HTMX swallowing a 409 "Job is not in
  manual_review" response. Handler now accepts both `manual_review` and `scored`
  stages; `promote_to_scored` already produces the correct end state in either
  case (stage='scored', relevance_score=7). Test coverage added for the
  Archive-tab path. Verified end-to-end on the operator stack 2026-04-26.

### Removed

- **`?q=` text-search URL param on board tabs.** Superseded by the
  per-column TEXT filters under `?title=...&company=...`. Bookmarks using
  the old `?q=foo` will silently drop the filter — the bookmark scheme
  was internal to one feature.

### Migration required

- **No user action required for the new weekly cron job (#284).** A new
  supercronic line at Sunday 02:00 (container time, UTC) runs the
  `company_discoverer` role and writes
  `candidate_context/discovered_companies.{md,json}` into your bind-mounted
  `state/candidate_context/`. The schedule is image-baked, so `docker compose
  pull && up -d` is sufficient — but operators should know the new artifact
  will start appearing weekly and that each run consumes ~$3–5 of OpenRouter
  Perplexity-Sonar budget. Set `DISCOVERY_COST_THRESHOLD_USD` in
  `state/data/.env` to override the default >$10 ntfy soft-guardrail.

## [0.4.0] — 2026-04-24

Minor bump. Closes the #250 OpenRouter Phase 2 cutover loop with three follow-ons (#254, #251, #261), promotes `briefing_writer` to Opus 4.7 to complete the prep-quality cascade (#264), neutralizes the `job_scorer` prompt so it derives reject categories and in-domain titles from the candidate profile rather than enumerating a tech-specific list (#65), adds a recruiter critic step + voice-samples calibration to the prep flow (#257), and wires voice-sample collection + auto-clean into the onboarding interview (#262). Three `migration-required` markers below — the #250 trio still applies to anyone bumping past v0.3.x for the first time, plus a new one for the scorer prompt change.

### Removed

- **Direct-Anthropic and direct-Perplexity aichat-ng client blocks (#251).** `ops/aichat-ng/config.yaml.example` no longer seeds `type: claude` or `type: openai-compatible name: perplexity` clients. Both providers are reached through OpenRouter as of the #250 Phase 2 cutover, so the inline blocks were inert. Same cleanup applied to the legacy native-install paths in `scripts/bootstrap.sh`, `docs/setup/install-linux.md`, and `docs/setup/configure.md`.
- **`ANTHROPIC_API_KEY` and `PERPLEXITY_API_KEY` env vars (#261).** `data/.env.example` no longer asks for them; `ops/entrypoint.sh` no longer substitutes them when seeding `state/aichat_ng/config.yaml`; `docs/setup/prerequisites.md` retired the Anthropic and Perplexity sections (Anthropic models now route via `openrouter:anthropic/...`, Perplexity via `openrouter:perplexity/sonar-reasoning-pro`). Existing stacks: the variables are simply unused — leaving them in `state/data/.env` is harmless. Closes the #250 cutover loop.

### Added

- **Onboarding interview now collects voice samples + auto-cleans (#262).** The interview prompt at `config/roles/onboarding_interviewer.md` adds a new Phase 3f that asks the user to paste 3,000–8,000 words of their own long-form prose (blog posts, essays, long emails) for cover-letter / outreach voice calibration. The interview emits an optional eighth file `voice-samples.md`; the paste-back injector runs the body through `findajob.onboarding.voice_processor.process_voice_samples`, which (1) deterministically strips markdown structure (headers, images, link syntax, bold/italic, blockquotes, code fences, footnote markers, HTML tags, tables, frontmatter) without altering prose, then (2) calls Opus 4.7 to generalize personal identifiers the user may not have thought to scrub (specific dates, named third parties, exact geographic specifiers, named institutions) while preserving voice. The cleaned-and-generalized text lands at `candidate_context/voice_samples/voice-samples.md`. Voice samples are **optional** — absence yields no error and falls back to resume-based voice calibration. LLM redaction failure degrades to cleaned-only with a flag the caller can surface. Closes the onboarding gap left by #257.
- **Recruiter critic step (#257).** New `recruiter_critic` role on Opus 4.7 runs after the cover letter and produces a `{Prefix} Critique - {Company} - {Title} - {timestamp}.md` artifact in each prep folder. Sees only what an actual recruiter sees (company, title, JD, tailored resume, cover letter — no profile, briefing, or fit analysis) so the critique simulates a 30-second outside read rather than a self-review. Tells the candidate what looks generic, what looks weak, and what is missing, in ≤150 words.
- **Voice samples wired into cover letters and outreach (#257).** `findajob.utils.load_voice_samples()` reads `.md` and `.txt` files from `candidate_context/voice_samples/` (excluding `README*`), concatenates with double-newline separators, caps at 32K chars, and the result is injected into `cover_letter_writer` and `outreach_drafter` prompts as a `VOICE SAMPLES:` section. Both role prompts include explicit "use for STYLE only — sentence rhythm, word choice, register; do NOT adopt the topical content or subject matter" guard rails. Empty / missing samples directory yields an empty section and no behavior change. Operators who want voice calibration drop long-form prose into the directory.

### Changed

- **`job_scorer` prompt neutralized — derives rejects + in-domain from profile (#65).** `config/roles/job_scorer.md` no longer enumerates ~12 tech-specific reject categories or hardcodes data-center / NPI in-domain title vocabulary. The prompt now reads the candidate's profile to determine exclusions (under any of `## Excluded Categories`, `## Deal-Breakers`, `## What I Am NOT`, `## Not Open To`, or `## Reduce score for`), in-domain titles (under target-role / core-competency sections), cross-industry framing (`## Core Competency (Cross-Industry)` etc.), and token-level calibration (new optional `## Title Calibration Notes`). Profile exclusions explicitly take priority over the Tier 1 floor. Generic load-bearing rules preserved: title-deterministic hard reject → score 1, score_status=scored, never manual_review for known-out-of-domain titles; Tier 1 + in-domain → 6 floor; ambiguous-title + absent-JD + no-company-signal as the only valid manual_review trigger. Operator-specific IC-vs-ops engineer calibration moved into the operator's profile under `## Title Calibration Notes`. The deterministic prefilter (`scorer_prefilter.py` + `prefilter_rules.yaml`) remains the comprehensive regex backstop. Required for the pipeline to score correctly for candidates outside data center / NPI work (e.g., Alice Doe's social-work stack).
- **briefing_writer upgraded to Opus 4.7 + max_tokens bumped 4096 → 8192 (#264).** `briefing_writer` was set to Sonnet 4.6 in the #250 OpenRouter Phase 2 cutover; that briefing cascades into `resume_tailor` and `cover_letter_writer`, both already on Opus 4.7. Promoting the briefing to the same tier closes the quality cascade — every downstream artifact now reads from a top-tier briefing rather than a Sonnet-tier one. The output cap was raised at the same time because Sonnet 4.6 was already saturating ~70% of the 4096 cap (2,851 tokens observed); Opus tends to write longer prose, so 4096 risked mid-section truncation that would trip the `Overall Recommendation:` validator and force a retry. 8192 eliminates the failure mode without changing typical output cost (Opus only generates what the briefing actually needs). Per-prep cost delta: ~$0.10 → ~$0.50 (~$2/day at 5 preps/day on the operator stack). No migration required.
- **outreach_drafter rewritten and upgraded to Opus 4.7 (#257).** Prompt expanded from ~13 lines to a structured spec covering tone & register, structure & density, honesty & framing, vocabulary, calibrate-to-contact rules, anti-fabrication, and per-format specs for LinkedIn DM and email. Bans common AI tells (em dashes, performative enthusiasm, corporate filler vocabulary, restating the contact's own title, formal closing platitudes) explicitly. Sonnet 4.6 to Opus 4.7 because complex multi-rule prompts on inputs that get sent to real humans benefit from stronger instruction-following.
- **Truncation slices removed from prep prompts (#257).** Seven `JD[:N]` / `full_briefing[:N]` slices in `scripts/prep_application.py` and `scripts/find_contacts.py` have been removed. Slices dated to smaller-context-window models; current routes (Opus 4.7 / Sonnet 4.6 200K, Gemini 3 Flash 1M, Perplexity sonar 128K) all have ample headroom. Briefings now reach `resume_tailor` and `cover_letter_writer` un-truncated; full JDs reach researcher, briefing, fit, change_reviewer, and outreach. The `JD_MAX_CHARS = 16000` cap in `findajob.utils` is unchanged — that is a defensive cap on the curl-fallback JD-load path, not an in-prompt slice.
- **OpenRouter Phase 2 cutover (#250).** Ten of eleven pipeline roles now route via OpenRouter as a single gateway: `resume_tailor` and `cover_letter_writer` upgraded to **Opus 4.7** (same pricing as 4.6 per OR catalog, small-to-moderate quality edge on real-pipeline prompts per Phase 1 verdict #22); `briefing_writer` and `outreach_drafter` to `openrouter:anthropic/claude-sonnet-4.6`; `company_researcher` and `fit_analyst` to `openrouter:perplexity/sonar-reasoning-pro` (OR's Perplexity path returns structured URL citations, direct path strips them); `resume_change_reviewer`, `network_analyst`, and the default model to `openrouter:google/gemini-3-flash-preview`. Embedding (`gemini-embed:gemini-embedding-001`) stays on the direct Google client — OR has zero embedding endpoints. `job_scorer` unchanged (already on OR).

### Fixed

- **Perplexity model_override hardcodes dropped from `prep_application.py` (#254).** Two `model_override="perplexity:sonar-reasoning-pro"` arguments at `prep_application.py:233,263` were overriding the role-front-matter routes set in #250 at runtime, silently re-routing `company_researcher` and `fit_analyst` back to the direct Perplexity client and losing the structured `url_citation` annotations that were the main Phase 1 rationale for the OpenRouter switch. The two-line removal lets the role front-matter (now `openrouter:perplexity/sonar-reasoning-pro`) actually govern these calls.
- **`.gitignore` voice-samples pattern tightened (#257 sub-bullet).** `candidate_context/voice_samples/*.txt` only caught `.txt` files — operator-supplied `.md` voice samples were exposed to git. Pattern is now `candidate_context/voice_samples/*` with negation `!candidate_context/voice_samples/README.md`. Run `git rm --cached` on any voice-sample `.md` files that were accidentally tracked under the previous rule.

### Migration required

- **Edit `state/aichat_ng/config.yaml` on each deployed stack** to change the top-level `model:` line from `gemini:gemini-3-flash-preview` to `openrouter:google/gemini-3-flash-preview`. The image's `ops/aichat-ng/config.yaml.example` template seeds this file only on first install; existing installs keep their pre-upgrade default otherwise. (#250)
- **Diff `state/aichat_ng/models-override.yaml` against `ops/aichat-ng/models-override.yaml` in this release** and append the two new openrouter catalog entries if absent: `anthropic/claude-opus-4.7` and `google/gemini-3-flash-preview`. Without these, the role files will reference models aichat-ng does not know about. (#250)
- **Ensure `OPENROUTER_API_KEY` is set** in `state/data/.env` (or equivalent). Ten of eleven roles now depend on it. (#250)
- **Ensure your `profile.md` has an exclusions section.** The neutralized `job_scorer` prompt (#65) reads the candidate profile to derive hard-reject categories. The prompt fuzzy-matches several heading names (`## Excluded Categories`, `## Deal-Breakers`, `## What I Am NOT`, `## Not Open To`, or `## Reduce score for` under `## Flags for Scorer`). Without one of these, the LLM-side title-deterministic hard reject silently degrades to "make a call from the JD" — the deterministic regex prefilter (`prefilter_rules.yaml`) is unchanged and will still catch the obvious cases, but you'll see a higher rate of out-of-domain roles passing through to mid-fit scores. See `candidate_context/profile.md.example` for canonical guidance with both tech and non-tech examples. Operator stack and Alice Doe's stack already have equivalent sections from prior onboarding — no immediate action required, but new operators must include one.

## [0.3.3] — 2026-04-24

Patch bump. Three additive `/board/*` UI improvements surfaced during the 2026-04-24 structural review (waitlist scores, Archive score-6 browse + promote, dashboard/waitlist company application history), plus a regression fix for Greenhouse fetcher URL parsing that was silently dropping Tier 1 additions using the newer `job-boards.*` subdomain. No migration required — rolling `docker compose pull && up -d` picks it up cleanly.

### Added

- **Fit and probability scores on `/board/waitlist` (#241).** Waitlist rows now render `fit_score` and `probability_score` alongside `relevance_score`; NULL values show as em-dash. Applies to the shared `_job_row.html` partial so every tab using it benefits. Closes #237.
- **`/board/archive` score filter + Promote-from-archive (#242).** Archive view accepts `?min_score=N&max_score=M` (bounds optional, inclusive); header carries "Score 6" and "Score 7+" quick-filter presets plus a Clear link. Rows in `stage='scored'` gain a Promote button backing the existing `/board/jobs/{fp}/promote` handler. HTMX infinite-scroll sentinel carries the filter params so pagination stays consistent. Surfaces the ~2–3/day score-6 supply that Dashboard's >=7 filter was hiding without flooding the triage queue. Closes #238.
- **Company application history cell on `/board/dashboard` and `/board/waitlist` rows (#244).** Each row now shows "N pending" + "N not selected" counts of prior applications to the same company, with a green flag for any offer and a yellow flag for `not_selected` within 90 days. Company matching normalizes on the first token so multi-word employer names with optional suffixes collapse to the same key (e.g. `Acme` and `Acme, Inc.`). Operator-side `rejected` jobs are excluded (noise, not signal); a row's own fingerprint is excluded from its own history. HTMX row-swap path in `board_actions.py` passes the cell through so post-action re-renders keep the context. Closes #234.

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

[Unreleased]: https://github.com/brockamer/findajob/compare/v0.8.3...HEAD
[0.8.3]: https://github.com/brockamer/findajob/releases/tag/v0.8.3
[0.8.2]: https://github.com/brockamer/findajob/releases/tag/v0.8.2
[0.8.1]: https://github.com/brockamer/findajob/releases/tag/v0.8.1
[0.8.0]: https://github.com/brockamer/findajob/releases/tag/v0.8.0
[0.7.4]: https://github.com/brockamer/findajob/releases/tag/v0.7.4
[0.7.3]: https://github.com/brockamer/findajob/releases/tag/v0.7.3
[0.7.2]: https://github.com/brockamer/findajob/releases/tag/v0.7.2
[0.7.1]: https://github.com/brockamer/findajob/releases/tag/v0.7.1
[0.7.0]: https://github.com/brockamer/findajob/releases/tag/v0.7.0
[0.6.1]: https://github.com/brockamer/findajob/releases/tag/v0.6.1
[0.6.0]: https://github.com/brockamer/findajob/releases/tag/v0.6.0
[0.5.2]: https://github.com/brockamer/findajob/releases/tag/v0.5.2
[0.5.1]: https://github.com/brockamer/findajob/releases/tag/v0.5.1
[0.5.0]: https://github.com/brockamer/findajob/releases/tag/v0.5.0
[0.4.0]: https://github.com/brockamer/findajob/releases/tag/v0.4.0
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
