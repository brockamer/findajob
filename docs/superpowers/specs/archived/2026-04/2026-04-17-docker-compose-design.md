---
**Archived 2026-04-20. shipped — findajob runs as a Docker Compose stack on docker.lan (#13 closed).**
---

# #13 Docker Compose — Design

**Date:** 2026-04-17
**Issues:** #13 (this spec), #69 (release management, parallel), #70 (docs cleanup, spinoff), #71 (Model-2 admin, deferred)
**Roadmap:** #58 Phase 2
**Status:** Design approved 2026-04-17; ready for implementation plan.

---

## Goal

Ship findajob as a container image pulled from GHCR and deployed via Docker Compose on the shared Docker host at `docker.lan`, managed through the Dockge web UI. Replace systemd user services as the scheduler mechanism. Daniel migrates his live pipeline to the containerized deploy as part of this work (dogfood before Amy). Amy (#20) and future dogfooders pull the same image to their own stack directories with zero code changes.

Success means:
- `docker compose pull && docker compose up -d` (or Dockge's Pull + Deploy buttons) is the entire update workflow for any user
- User state (DB, candidate content, prep output, logs, personal config) is in a bind-mounted host directory; image updates never touch it
- Daniel's migration is rollback-able at any step up to and including the first 48h of observation
- The image is multi-tenant: a second (or third) stack directory on the same Docker host runs an independent instance with no collisions

## Non-goals

- Retiring rclone / Google Drive sync (scoped to #59 / Phase 3 of the reordered roadmap)
- Web UI / materials viewer (scoped to #59)
- App-level auth, public internet exposure, multi-region deploy
- Multi-platform images (v0.1.0 is `linux/amd64` only)
- Amy running her own admin Claude session (deferred to #71 trigger condition)

## Architecture

Single image, deployed as a compose stack with two services:

- `scheduler` — long-running. Runs `supercronic` with a crontab mirroring today's systemd timers 1:1. All scheduled and on-demand entry points execute in this container.
- `gmail-auth` — short-lived, compose profile `setup`. Runs the Google OAuth device flow once per token lifetime. Excluded from default `up`.

No web service in v0.1.0 — added as a third compose service in v0.2.0 when #59 lands.

```
┌──────────────────── Docker host: docker.lan ────────────────────────┐
│                                                                       │
│  /opt/stacks/findajob-brock/   ← Dockge-managed stack (Daniel)       │
│    ├── compose.yaml                                                   │
│    ├── .env                     per-instance: image tag, TZ, flags    │
│    └── state/                   bind-mounted into container           │
│         ├── data/               pipeline.db, data/.env (secrets)      │
│         ├── config/             personal config, OAuth creds          │
│         ├── candidate_context/  profile.md, master_resume.md          │
│         ├── companies/          prep output folders                    │
│         ├── logs/                                                      │
│         └── aichat_ng/          aichat-ng config dir                   │
│                                                                       │
│  /opt/stacks/findajob-amy/     ← same shape, independent stack        │
│    └── state/...                                                       │
│                                                                       │
│         ┌────────── compose.yaml ──────────┐                          │
│         │ image: ghcr.io/brockamer/findajob:${FINDAJOB_IMAGE_TAG}    │
│         │ services: scheduler, gmail-auth                             │
│         │ bind mounts from ./state/                                   │
│         │ per-stack bridge network                                    │
│         └───────────────────────────────────┘                         │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
         ↑                                             ↑
   GHCR pull                                  (future v0.2.0) Synology
   (ghcr.io/brockamer/findajob:v0.1)          reverse proxy at
                                               <user>-findajob.brockbot.com
```

### Image structure

- Base: `python:3.12-slim-bookworm`
- Binaries:
  - `aichat-ng` — prebuilt musl binary from `blob42/aichat-ng` GitHub Releases, pinned to a tag (initial: `v0.31.0`)
  - `supercronic` — prebuilt from `aptible/supercronic` releases, pinned
  - `pandoc`, `rclone`, `sqlite3`, `tini`, `su-exec` — apt packages
- Python deps: `pip install -e .` from `pyproject.toml` plus the explicit list from `docs/setup/install-linux.md` step 4
- Copied from repo: `src/findajob/`, `scripts/`, `config/roles/`, `ops/crontab`, `ops/entrypoint.sh`
- NOT in image: `data/`, `candidate_context/`, `companies/`, `logs/`, `aichat_ng/` config, personal `config/*.yaml|*.txt|*.json` files — all come from bind mount
- Entry: `ENTRYPOINT ["tini", "--", "/entrypoint.sh"]`, `CMD ["supercronic", "/app/crontab"]`
- Target size: ~550 MB compressed (pandoc dominates; acceptable for one-time pull)
- User model: `entrypoint.sh` creates a runtime `findajob` user matching PUID/PGID from env, chowns `/app/data /app/logs /app/companies /app/aichat_ng`, re-execs the command under that UID via `su-exec`

### Compose file

Repo template lives at `ops/compose.yaml.example`. Users copy it to their stack dir and commit changes through Dockge.

```yaml
services:
  scheduler:
    image: ghcr.io/brockamer/findajob:${FINDAJOB_IMAGE_TAG:-v0.1}
    restart: unless-stopped
    env_file: ./state/data/.env
    environment:
      TZ: ${FINDAJOB_TZ:-America/New_York}
      PUID: ${PUID:-1000}
      PGID: ${PGID:-1000}
      FINDAJOB_JOBSYNC_ENABLED: ${FINDAJOB_JOBSYNC_ENABLED:-false}
      FINDAJOB_TRIAGE_TIMEOUT: ${FINDAJOB_TRIAGE_TIMEOUT:-7200}
    volumes:
      - ./state/data:/app/data
      - ./state/config:/app/config
      - ./state/candidate_context:/app/candidate_context
      - ./state/companies:/app/companies
      - ./state/logs:/app/logs
      - ./state/aichat_ng:/root/.config/aichat_ng
    networks:
      - findajob-network

  gmail-auth:
    image: ghcr.io/brockamer/findajob:${FINDAJOB_IMAGE_TAG:-v0.1}
    profiles: [setup]
    env_file: ./state/data/.env
    environment:
      TZ: ${FINDAJOB_TZ:-America/New_York}
      PUID: ${PUID:-1000}
      PGID: ${PGID:-1000}
    volumes:
      - ./state/config:/app/config
    command: python3 scripts/gmail_auth.py --mode=device
    networks:
      - findajob-network

networks:
  findajob-network:
    driver: bridge
```

Per-stack `.env`:

```
# /opt/stacks/findajob-brock/.env
FINDAJOB_IMAGE_TAG=latest
FINDAJOB_TZ=America/Los_Angeles
FINDAJOB_JOBSYNC_ENABLED=true
PUID=1000
PGID=1000

# /opt/stacks/findajob-amy/.env
FINDAJOB_IMAGE_TAG=v0.1
FINDAJOB_TZ=America/New_York
FINDAJOB_JOBSYNC_ENABLED=false
FINDAJOB_TRIAGE_TIMEOUT=21600    # 6h during first-week bootstrap
PUID=1000
PGID=1000
```

Two env layers, intentional:
1. **Stack `.env`** (Dockge editable) — image tag, TZ, flags, UID/GID. Per-instance, safe to view. Variables: `FINDAJOB_IMAGE_TAG`, `FINDAJOB_TZ`, `FINDAJOB_JOBSYNC_ENABLED`, `FINDAJOB_TRIAGE_TIMEOUT`, `PUID`, `PGID`.
2. **`state/data/.env`** (app-level, chmod 600) — API keys and secrets. Referenced via `env_file:`, unchanged semantics from current systemd. Adds `FINDAJOB_JOBSYNC_REMOTE` (rclone remote URL; only read when `FINDAJOB_JOBSYNC_ENABLED=true`).

### Scheduler (supercronic)

New file `ops/crontab` in the repo:

```cron
PYTHONUNBUFFERED=1

# Ingest + scoring
0    0   *  *  *   timeout ${FINDAJOB_TRIAGE_TIMEOUT:-7200} python3 /app/scripts/triage.py
*/10 *   *  *  *   timeout 900 python3 /app/scripts/poll_flags.py
*/30 *   *  *  *   python3 /app/scripts/ingest_form.py

# Drive sync (gated)
*/15 *   *  *  *   [ "$FINDAJOB_JOBSYNC_ENABLED" = "true" ] && rclone copy --update /app/companies/ "${FINDAJOB_JOBSYNC_REMOTE}"

# Notifications
0    6   *  *  *       python3 /app/scripts/notify.py apply-reminder
15   6   *  *  *       python3 /app/scripts/notify.py stats
0    7   *  *  *       python3 /app/scripts/notify.py health-check
0    8   *  *  1,3,5   python3 /app/scripts/notify.py issues
30   8   *  *  1       python3 /app/scripts/notify.py scoreboard
0    8   *  *  0       python3 /app/scripts/notify.py feedback

# RAG rebuild
0    3   *  *  0   /usr/local/bin/aichat-ng --rag job_search_rag --rebuild-rag
```

Supercronic runs as the image's `CMD`, evaluating schedules in the container's `TZ`. Logs to stdout; Docker collects. Missed triggers across restarts are not caught up (unlike systemd `Persistent=true`) — mitigated by `restart: unless-stopped` plus a worst-case one-tick miss on the poller.

Long-running jobs are wrapped in `timeout`:
- `triage.py`: `FINDAJOB_TRIAGE_TIMEOUT` env (default 7200s / 2h; bumpable per-instance for first-run bootstrap)
- `poll_flags.py`: 900s

On-demand scripts are run via `docker compose exec scheduler python3 scripts/<name>.py <args>` — same mental model as today's direct invocation, just through compose.

### Gmail auth helper

New file `scripts/gmail_auth.py` — ~50 lines wrapping `google-auth-oauthlib`.

Two modes:
- `--mode=device` — OAuth 2.0 Limited Input Device flow. Prints `google.com/device` + code, polls for consent. **v0.1.0 default and only mode used.**
- `--mode=local` — `InstalledAppFlow.run_local_server` with `open_browser=False`. Listens on a port for callback. **Present in the script but not enabled until v0.2.0 when #59 provides the reverse-proxy routing for it.**

Writes token to `/app/config/gmail_token.json` (bind-mounted, chmod 600). Existing scripts (`triage.py`, `notify.py`, `backfill_jd.py`) check for token file presence and skip Gmail work gracefully when absent — no hard failure. This makes Gmail opt-in: the credential file IS the enable flag.

One-time Google Cloud setup (Daniel's task, documented):
1. Create OAuth client of type "TVs and Limited Input devices" _(historically incorrect — this type rejects Gmail scopes with `invalid_scope`; use Desktop app. Fixed in #115.)_
2. Add dogfooders as test users (100-user limit is far beyond need)
3. Download client JSON to each user's `state/config/gmail_oauth_client.json`

### Per-user admin model (v0.1.0)

**Model 1:** Daniel is Amy's remote admin. Amy uses Dockge for pull/deploy/logs/edit-text-config, which covers routine operations. For anything harder, she contacts Daniel, whose Claude Code session (on his machine, SSH'd into `docker.lan`) drives the action. No per-user admin LXC, no per-user Claude session. Trade: scales poorly past 2–3 external users; deferred upgrade captured in #71.

### Release automation

Shipped with #13 (minimum needed to tag v0.1.0):

- `.github/workflows/build-image.yml`:
  - On push to main: build + tag `:main-<short-sha>` + `:latest`, push to GHCR
  - On tag `v*.*.*`: build + tag `v<x.y.z>` + moving alias `v<x.y>` + `:latest`, push
- `.github/workflows/create-release.yml`:
  - On tag `v*.*.*`: generate release notes between previous `v*.*.*` and this one; surface `migration-required`-labeled PRs in an "Action required" section at the top; publish GitHub Release
- `CHANGELOG.md` at repo root (Keep a Changelog format; initial Unreleased + v0.1.0 entry)

Deferred to #69:
- `docs/release-process.md`, `docs/setup/install-docker.md`
- `migration-required` label creation (one-shot `gh label create`)
- Dogfood-gate verification playbook

GHCR image visibility: **public** (findajob is open-source-headed; public image pulls have no auth friction for external dogfooders).

## Data flow

- Container writes: `data/pipeline.db`, `companies/<prep-folder>/`, `logs/pipeline.jsonl`, `config/gmail_token.json` refresh
- Host writes (via Dockge's file viewer or direct edit): `candidate_context/profile.md`, `config/prefilter_rules.yaml`, `config/target_companies.md`, etc. — these are bind-mount paths that both host and container see simultaneously
- No DB locking concerns between host and container: the container is the only process writing the DB; host edits are text-only
- Bind-mount UID/GID: container's `findajob` user created with host user's PUID/PGID → new files are host-owned correctly, Dockge file viewer can edit them

## Migration (Daniel's live cutover)

Summary — full detail in the implementation plan.

1. Pre-flight on current `findajob.lan` LXC: tag repo state, backup tarball, record DB row count
2. Provision `/opt/stacks/findajob-brock/` on `docker.lan`: compose.yaml, .env, empty state/ subdirs, `docker compose pull` to verify registry
3. Initial rsync of data/, config/, candidate_context/, companies/, logs/, ~/.config/aichat_ng/
4. Stop systemd timers on old LXC (disable but don't delete)
5. Delta rsync pass to capture last-minute drift
6. `docker compose up -d` (via Dockge Deploy)
7. Parity checks: DB row count, supercronic crontab load, health-check ntfy, manual `triage.py` exec
8. Observe for 24–48h through a full triage + poller + notify cycle
9. Only after successful observation: archive old systemd units, cut `v0.1.0` tag
10. Rollback plan: `down` the compose stack, re-enable systemd timers on the old LXC; state on the old LXC was never modified during cutover because step 4 only stopped timers

Amy's provisioning (separate runbook after Daniel): same stack template but empty state; Daniel drops her personal config in; first triage run unbounded (manual) with `FINDAJOB_TRIAGE_TIMEOUT=21600` set during the first week.

## Error handling

- Container crash → `restart: unless-stopped` brings it back; worst case one missed poller tick
- Hung script → `timeout` wrapper kills it; health-check catches stuck-stage jobs on the next run
- Bad image push (CI regression) → `:latest` advances; Daniel notices within the dogfood window; rollback is pin stack to prior `:main-<sha>` and cut a fix PR
- Bad release tag → re-tag the prior `v<x.y>` alias to a safe patch (Claude's #69 rollback playbook)
- Missing Gmail token → scripts skip Gmail, log warning, continue
- Missing jobsync remote config → `FINDAJOB_JOBSYNC_ENABLED=true` with no remote URL fails loudly on first cron tick; user sees it in Dockge logs

## Testing strategy

Three layers:

**Layer 1 — CI smoke test** (every push):
- Extend `.github/workflows/ci.yml` with `docker-build-smoke` job
- Build image; run `docker run --rm` to verify: package import works, `supercronic -test /app/crontab` passes, `aichat-ng --version` returns
- Does not run pipeline scripts (needs secrets)

**Layer 2 — integration harness** (manual, pre-release):
- `scripts/test_container_integration.sh` spins up a throwaway `/tmp/findajob-test-stack/` with scratch DB, minimal config, real API keys from Daniel's `.env`
- Execs each scheduled script once; asserts no exceptions, supercronic loaded crontab, DB state sane
- Tears down and cleans up
- Claude runs this as part of the #69 release gate before tagging

**Layer 3 — live observation** (post-migration):
- Daniel's stack on `:latest` running 24–48h through a full cycle before tag cut
- Amy's stack on `:v0.1` alias observed for 1 week after first deploy

Explicit non-test items:
- End-to-end LLM triage in CI (cost + duration)
- Multi-stack coexistence (verified visually during Amy deploy)
- Rollback automation (rare enough that manual is fine)

Regressions to specifically watch during observation:
- Path assumptions — `JSP_BASE` set correctly to `/app`
- Binary subprocess paths using `AICHAT`/`PANDOC` from `findajob.paths`
- Timezone: first morning's 06:00 apply-reminder fires at local, not UTC
- File ownership — new files host-editable (Dockge viewer)
- Gmail token refresh writes succeed (bind-mount write permission)

## Files created/modified

New:
- `Dockerfile`
- `ops/crontab`
- `ops/entrypoint.sh`
- `ops/compose.yaml.example`
- `ops/stack.env.example`
- `scripts/gmail_auth.py`
- `scripts/test_container_integration.sh`
- `.github/workflows/build-image.yml`
- `.github/workflows/create-release.yml`
- `CHANGELOG.md`
- `.dockerignore`

Modified:
- `.github/workflows/ci.yml` — add `docker-build-smoke` job
- `scripts/triage.py`, `scripts/notify.py`, `scripts/backfill_jd.py` — graceful skip when `gmail_token.json` absent
- `src/findajob/paths.py` — document `JSP_BASE=/app` convention for containerized deploys
- `docs/setup/install-docker.md` — new file covering container deploy (stub in this PR; fleshed out in #69)
- `docs/setup/install-linux.md` — add note that containerized install is now the recommended path; keep native install as fallback
- `CLAUDE.md` — add Container Context Table entry; update Critical Architecture Rules to mention `/app` convention

Already-present deps (confirmed in pyproject.toml; no modification needed):
- `google-auth-oauthlib>=1.3.1` is already an explicit dependency.

Possibly modified (grep sweep):
- Any script hardcoding `/home/brockamer/Code/findajob` — should already be none thanks to `paths.py`, but verify

## Open questions

None at design time. All resolved during brainstorming 2026-04-17.

## Decision log (referenced from #58 decisions 6–10)

1. Registry + tag model B: `:main-<sha>`, `:latest`, `:v<x.y.z>`, moving minor alias `:v0.1`
2. Release management owned by Claude (per `feedback_release_management.md` memory; #69)
3. Reorder: #59 before #20, so Amy lands on a stack with web-based materials access
4. aichat-ng from `blob42/aichat-ng` prebuilt musl binary, pinned to tag
5. Scheduler: supercronic in the same container as the scripts; no separate container; no Docker socket mount
6. Volumes: bind mounts only; no named Docker volumes
7. Admin model: Model 1 (Daniel as Amy's remote admin) for v0.1.0; Model 2 deferred to #71 when dogfooder count ≥ 3
8. Gmail: device flow only in v0.1.0; local callback flow in code but unused until v0.2.0's reverse-proxy routing
9. Dockge-managed stacks under `/opt/stacks/findajob-<user>/`; per-stack bridge networks; Synology reverse proxy terminates TLS at `<user>.brockbot.com`
10. TZ per tenant via stack `.env`, not a repo-wide default

---

## Decisions made during implementation

Captured post-PR-#72 per `docs/plan-conventions.md`. These deltas do not
invalidate the spec; they correct small details that shook out during build.

1. **aichat-ng tarball layout.** The spec assumed the `blob42/aichat-ng` v0.31.0
   release tarball wrapped the binary in `aichat-ng-${VERSION}-${ARCH}/`. The
   tarball is actually flat (`tar -tzf` lists only `aichat-ng`). Dockerfile
   extracts to `/tmp` and installs from there. Fixed in commit `e3b0e04`.

2. **supercronic version probe.** supercronic v0.2.29 has no `-version` flag.
   Build-time healthcheck replaced with `-test` against a no-op crontab, which
   proves both binary executability and crontab-parsing capability. SHA1
   verification is preserved. Fixed in commit `a4586bc`.

3. **gmail_auth.py test count.** Spec called for 6 tests; shipped 8. The two
   extra tests cover 0600 token-file mode enforcement and device-polling loop
   coverage, added after in-PR code review flagged the gaps.

4. **ops/crontab notify.py subcommand mismatches.** The shipped crontab used
   three notify.py subcommand names (`stats`, `issues`, `feedback`) that the
   dispatcher does not accept — the correct names are `daily-stats`,
   `issues-ping`, `feedback-review`. Each invocation fired at its scheduled
   time, printed the usage line, and exited 1 — silently disabling three
   notifications on every Docker deploy. Fixed in PR #75 (issue #74) with a
   pytest regression guard that AST-parses `notify.COMMANDS` and cross-checks
   every `notify.py <subcmd>` invocation in `ops/crontab`.
