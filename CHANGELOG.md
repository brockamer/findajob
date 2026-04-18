# Changelog

All notable changes to findajob are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Until the pipeline stabilizes, 0.x releases are considered unstable. Breaking
changes may land in minor version bumps; patch releases are bugfix-only.

## [Unreleased]

### Added
- `docs/release-process.md` — Claude-facing release orchestration runbook: 48h dogfood gate, CHANGELOG workflow, tag cut mechanics, post-tag verification, rollback (#69)
- `docs/setup/install-docker.md` — full external-user Docker install + operations guide replacing the stub (#69)
- `migration-required` GitHub label for PRs needing post-pull manual steps; auto-surfaced by `create-release.yml` in "Action required" section of release notes (#69)
- `CLAUDE.md` "Release Management" subsection pointing future sessions at the runbook (#69)

## [0.1.0] — TBD

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
- `docs/setup/install-docker.md` — install guide stub (full guide in #69) (#13)

### Changed
- Deployment target: Linux host running Docker. Native systemd install remains
  documented as a fallback but Docker Compose is the recommended path. (#13)

### Deprecated
- systemd user services for the pipeline scheduler — replaced by supercronic
  inside the container. Existing systemd units stay archived on the maintainer's
  LXC during the observation window. (#13)

### Notes
- Release management process is documented in `docs/release-process.md` and
  followed for this cut (#69).
- Documentation cleanup — removing `sigoden/aichat` references in favor of
  `blob42/aichat-ng` — is tracked in #70.

[Unreleased]: https://github.com/brockamer/findajob/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/brockamer/findajob/releases/tag/v0.1.0
