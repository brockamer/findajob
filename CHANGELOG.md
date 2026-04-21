# Changelog

All notable changes to findajob are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Until the pipeline stabilizes, 0.x releases are considered unstable. Breaking
changes may land in minor version bumps; patch releases are bugfix-only.

## [Unreleased]

### Added
- Web materials viewer (`#59`): local FastAPI service serves prep-folder contents
  on `http://docker.lan:<port>/`. Replaces Google Drive folder browsing.

### Removed
- rclone integration and Google Drive sync (`#29`, `#59`). `FINDAJOB_JOBSYNC_*`
  env vars deleted; `state/rclone/` bind mount no longer used; `rclone` removed
  from the container image (~50 MB smaller).

### Migration required

Operators on prior versions who had `FINDAJOB_JOBSYNC_ENABLED=true` must
perform a one-time stack update — see `docs/setup/state-migration.md` for
the exact commands. Testers on fresh installs are unaffected.

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

[Unreleased]: https://github.com/brockamer/findajob/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/brockamer/findajob/releases/tag/v0.1.0
