# Changelog

All notable changes to findajob are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Until the pipeline stabilizes, 0.x releases are considered unstable. Breaking
changes may land in minor version bumps; patch releases are bugfix-only.

## [Unreleased]

### Added
- `sync_sheet.py` now hyperlinks the company cell on Dashboard, Applied, Waitlist, and Rejected Applications tabs into the materials viewer when a new `FINDAJOB_MATERIALS_BASE_URL` env var is set (e.g., `http://docker.lan:8090`). Stages without folders and unset env var render as plain text (no 404s). Stale "Drive hyperlink" annotations removed from `CLAUDE.md`, `docs/google-sheets.md`, and `setup_sheets.py` (#130).
- Web viewer now has a top nav and landing page. Materials folder index moved from `/` to `/materials/`; deep links `/materials/{fingerprint}` and `/materials/{fingerprint}/{filename}` unchanged. Placeholder pages for board, ingest, tools, config, docs fill in as features land (#60).
- `/board/dashboard`, `/board/applied`, `/board/review`, `/board/waitlist`, `/board/archive` render the same content as the corresponding Google Sheet tabs, reading directly from the database. Archive covers all jobs (10k+) with HTMX infinite-scroll pagination, obsoleting Sheet1's archival filter. Per-tab text filter via HTMX, URL-param sort, Sheet-matching conditional formatting (Applied row-age buckets, Offer gold, Interviewing purple, known-contacts amber), and Applied's company cell hyperlinks into the materials viewer when `FINDAJOB_MATERIALS_BASE_URL` is set. `sync_sheet.py` continues to update Sheets in parallel during the 14b → 14c → 14d migration (#60).

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

[Unreleased]: https://github.com/brockamer/findajob/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/brockamer/findajob/releases/tag/v0.1.2
[0.1.1]: https://github.com/brockamer/findajob/releases/tag/v0.1.1
[0.1.0]: https://github.com/brockamer/findajob/releases/tag/v0.1.0
