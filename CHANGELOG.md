# Changelog

All notable changes to findajob are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Until the pipeline stabilizes, 0.x releases are considered unstable. Breaking
changes may land in minor version bumps; patch releases are bugfix-only.

## [Unreleased]

### Added

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

[Unreleased]: https://github.com/brockamer/findajob/compare/v0.1.4...HEAD
[0.1.4]: https://github.com/brockamer/findajob/releases/tag/v0.1.4
[0.1.3]: https://github.com/brockamer/findajob/releases/tag/v0.1.3
[0.1.2]: https://github.com/brockamer/findajob/releases/tag/v0.1.2
[0.1.1]: https://github.com/brockamer/findajob/releases/tag/v0.1.1
[0.1.0]: https://github.com/brockamer/findajob/releases/tag/v0.1.0
