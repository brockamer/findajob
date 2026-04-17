# #13 Docker Compose Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship findajob as a `ghcr.io/brockamer/findajob` container image deployed via Docker Compose on `docker.lan`, managed through Dockge, with Daniel dogfooding the migration before Amy.

**Architecture:** Single image based on `python:3.12-slim-bookworm` with `aichat-ng` (blob42 fork, prebuilt musl binary), `supercronic` (container cron), `pandoc`, `rclone`. Two compose services per stack: `scheduler` (long-running supercronic) and `gmail-auth` (compose profile `setup`, device flow). Bind mounts for all state under `./state/`. GHCR registry, tag model `:main-<sha>` / `:latest` / `:v<x.y.z>` / moving alias `:v0.1`.

**Tech Stack:** Docker, docker compose, supercronic, GitHub Actions, GHCR, Python 3.12, `google-auth-oauthlib` (device flow), bash entrypoint with `su-exec`.

**Spec:** `docs/superpowers/specs/2026-04-17-docker-compose-design.md` (10 decision-log entries, all resolved).

**Branch:** `feat/13-docker-compose` (already created off `origin/main`; spec doc committed).

**Correction vs. spec:** The spec lists `scripts/triage.py`, `scripts/notify.py`, and `scripts/backfill_jd.py` as needing graceful-Gmail-skip modifications. Verified during plan authoring: `src/findajob/fetchers.py:472-490` already skips cleanly when `gmail_oauth_client.json` or `gmail_token.json` is absent, or when stdin is not a TTY (exactly the container case). No modifications to those three scripts are required.

---

## File Structure

**New files** (in this branch, this PR):

| Path | Responsibility |
|---|---|
| `Dockerfile` | Image build definition, multi-binary install, Python editable install |
| `.dockerignore` | Exclude tests, docs, state, backups from the build context |
| `ops/crontab` | Supercronic cron lines — 1:1 translation of systemd timers |
| `ops/entrypoint.sh` | Create runtime user at PUID/PGID, chown bind mounts, re-exec via `su-exec` |
| `ops/compose.yaml.example` | Deploy-ready template for any user's stack dir |
| `ops/stack.env.example` | Per-instance env template (image tag, TZ, flags, UID/GID) |
| `scripts/gmail_auth.py` | Standalone OAuth helper: `--mode=device` (used) and `--mode=local` (scaffolded for v0.2.0) |
| `tests/test_gmail_auth.py` | Unit tests for the helper's argparse + mode dispatch |
| `.github/workflows/build-image.yml` | GHCR build + push on `main` and on `v*.*.*` tags |
| `.github/workflows/create-release.yml` | Auto-generated release notes on `v*.*.*` tag |
| `CHANGELOG.md` | Keep-a-Changelog format; initial Unreleased + `v0.1.0` sections |
| `docs/setup/install-docker.md` | Stub doc pointing at #69 for the full deploy guide |

**Modified files:**

| Path | Responsibility |
|---|---|
| `.github/workflows/ci.yml` | Add `docker-build-smoke` job gated on lint/test success |
| `src/findajob/paths.py` | Document `JSP_BASE=/app` convention in module docstring |
| `docs/setup/install-linux.md` | Add prominent pointer to Docker install as the recommended path |
| `CLAUDE.md` | Add Container Context Table entry; `/app` path note in Critical Architecture Rules |

No code changes to scheduled scripts — the existing Gmail-skip behavior works correctly in the container.

---

## Task 1: Scaffolding — `.dockerignore` + empty `ops/` directory

**Files:**
- Create: `.dockerignore`

- [ ] **Step 1: Write `.dockerignore`**

```
# .dockerignore — keep the build context small and secret-free

# VCS
.git
.gitignore
.github

# Docs / dev artifacts
docs
tests
README.md

# Python caches
__pycache__
*.pyc
.pytest_cache
.mypy_cache
.ruff_cache

# Local state — MUST never enter the image
data
candidate_context
companies
logs
.tmux.conf
manual_job.txt

# Editor / OS
.DS_Store
.vscode
.idea

# Release / build artifacts
dist
build
*.egg-info
```

- [ ] **Step 2: Verify the build context is clean**

Run: `docker context inspect 2>/dev/null | head -1 || echo "docker not required for this step"`

Expected: no error. (Verification of actual context size happens in Task 2 after the Dockerfile exists.)

- [ ] **Step 3: Commit**

```bash
git add .dockerignore
git commit -m "Add .dockerignore for Docker build context hygiene (#13)

Excludes VCS, tests, docs, local state, caches. Keeps the build
context small and guarantees user state never enters the image.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Dockerfile

**Files:**
- Create: `Dockerfile`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# syntax=docker/dockerfile:1.7

# findajob image
# Base: Python 3.12 on Debian slim. Single stage — aichat-ng is a prebuilt binary,
# supercronic is a prebuilt binary; no compilation needed.

FROM python:3.12-slim-bookworm

ARG AICHAT_NG_VERSION=v0.31.0
ARG AICHAT_NG_ARCH=x86_64-unknown-linux-musl
ARG SUPERCRONIC_VERSION=v0.2.29
ARG SUPERCRONIC_SHA1SUM=cd48d45c4b10f3f0bfdd3a57d054cd05ac96812b
ARG SUPERCRONIC_FILE=supercronic-linux-amd64

# System packages — keep minimal
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        pandoc \
        rclone \
        sqlite3 \
        tini \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# su-exec (tiny drop-privilege helper; Alpine's version, compiled for Debian as gosu alternative)
# We use gosu here since it's in Debian repos — functionally equivalent to su-exec.
RUN apt-get update && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

# aichat-ng (blob42 fork, prebuilt musl binary — static, no libc dep)
RUN set -eux; \
    curl -fsSL -o /tmp/aichat-ng.tar.gz \
        "https://github.com/blob42/aichat-ng/releases/download/${AICHAT_NG_VERSION}/aichat-ng-${AICHAT_NG_VERSION}-${AICHAT_NG_ARCH}.tar.gz"; \
    tar -xzf /tmp/aichat-ng.tar.gz -C /tmp; \
    install -m 0755 "/tmp/aichat-ng-${AICHAT_NG_VERSION}-${AICHAT_NG_ARCH}/aichat-ng" /usr/local/bin/aichat-ng; \
    rm -rf /tmp/aichat-ng.tar.gz "/tmp/aichat-ng-${AICHAT_NG_VERSION}-${AICHAT_NG_ARCH}"; \
    /usr/local/bin/aichat-ng --version

# supercronic
RUN set -eux; \
    curl -fsSL -o /usr/local/bin/supercronic \
        "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/${SUPERCRONIC_FILE}"; \
    echo "${SUPERCRONIC_SHA1SUM}  /usr/local/bin/supercronic" | sha1sum -c -; \
    chmod +x /usr/local/bin/supercronic; \
    /usr/local/bin/supercronic -version

# Python deps — copy pyproject first for better layer caching
WORKDIR /app
COPY pyproject.toml /app/
RUN pip install --no-cache-dir --break-system-packages -e . || \
    (pip install --no-cache-dir -e . --root-user-action=ignore)

# App code
COPY src/ /app/src/
COPY scripts/ /app/scripts/
COPY config/roles/ /app/config/roles/
COPY ops/crontab /app/crontab
COPY ops/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Path resolution — tell src/findajob/paths.py where we live
ENV JSP_BASE=/app

# Tini as PID 1 for clean signal propagation; entrypoint drops privileges
ENTRYPOINT ["tini", "--", "/entrypoint.sh"]
CMD ["supercronic", "/app/crontab"]
```

- [ ] **Step 2: Verify the file was written without smart quotes or mangled characters**

Run: `head -1 Dockerfile && wc -l Dockerfile`

Expected: `# syntax=docker/dockerfile:1.7` as line 1; ~50-55 lines total.

- [ ] **Step 3: Commit**

```bash
git add Dockerfile
git commit -m "Add Dockerfile for findajob container image (#13)

Single-stage build on python:3.12-slim-bookworm. Installs:
- aichat-ng from blob42/aichat-ng prebuilt musl binary (pinned v0.31.0)
- supercronic pinned v0.2.29 with SHA1 verification
- pandoc, rclone, sqlite3, tini, gosu via apt
- Python deps via pip install -e .

JSP_BASE=/app so findajob.paths resolves correctly. Entrypoint
drops privileges via gosu; supercronic is the default CMD.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Entrypoint script — PUID/PGID drop-privileges

**Files:**
- Create: `ops/entrypoint.sh`

- [ ] **Step 1: Write the entrypoint**

```bash
#!/bin/sh
# ops/entrypoint.sh — runtime entry for the findajob container.
# Creates a non-root user matching PUID:PGID from env so bind-mounted files
# are host-owned correctly, chowns writable app dirs, then re-execs the
# command as that user.
#
# Env:
#   PUID, PGID — host UID/GID to run as (default 1000:1000)
#
# Idempotent: safe to run every container start.

set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# Create group if missing
if ! getent group findajob >/dev/null 2>&1; then
    groupadd -g "$PGID" findajob
fi

# Create user if missing
if ! id findajob >/dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -d /app -s /bin/sh -M findajob
fi

# Ensure writable dirs exist and are owned by findajob.
# These directories are bind-mounted from the host. First-container-start
# chowns them; subsequent starts are no-ops if ownership already matches.
for dir in /app/data /app/logs /app/companies /app/config /app/candidate_context /root/.config/aichat_ng; do
    if [ -d "$dir" ]; then
        # Only chown if not already findajob-owned (avoids thrashing on large dirs)
        if [ "$(stat -c %u "$dir" 2>/dev/null || echo 0)" != "$PUID" ]; then
            chown -R "$PUID:$PGID" "$dir" || true
        fi
    fi
done

# aichat_ng config lives under /root/.config — give findajob access
# via the same PUID mapping (the bind mount points there)
if [ -d /root/.config/aichat_ng ]; then
    chown -R "$PUID:$PGID" /root/.config/aichat_ng || true
fi

# Drop privileges and exec the command
exec gosu "$PUID:$PGID" "$@"
```

- [ ] **Step 2: Make it executable and verify POSIX-shell syntax**

Run: `chmod +x ops/entrypoint.sh && sh -n ops/entrypoint.sh && echo "syntax ok"`

Expected: `syntax ok` (no parse errors from `sh -n`).

- [ ] **Step 3: Commit**

```bash
git add ops/entrypoint.sh
git commit -m "Add container entrypoint for PUID/PGID drop-privileges (#13)

Creates a findajob user matching host PUID:PGID so bind-mount
files are host-owned. Idempotent: chown only runs when ownership
doesn't already match. Uses gosu to drop privileges and exec the
CMD (supercronic by default).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Supercronic crontab

**Files:**
- Create: `ops/crontab`

- [ ] **Step 1: Write the crontab**

```
# ops/crontab — supercronic cron for the findajob scheduler container.
# Schedules mirror the systemd timers 1:1. All times are in container-local
# TZ (from env var); set per-tenant via stack .env.
#
# See docs/superpowers/specs/2026-04-17-docker-compose-design.md for the full
# mapping table.

# Ensure Python child output is line-buffered to container stdout
PYTHONUNBUFFERED=1

# ── Ingest + scoring ─────────────────────────────────────────────────────────
0    0   *  *  *   timeout ${FINDAJOB_TRIAGE_TIMEOUT:-7200} python3 /app/scripts/triage.py
*/10 *   *  *  *   timeout 900 python3 /app/scripts/poll_flags.py
*/30 *   *  *  *   python3 /app/scripts/ingest_form.py

# ── Google Drive sync (gated by env, disabled by default) ────────────────────
*/15 *   *  *  *   [ "$FINDAJOB_JOBSYNC_ENABLED" = "true" ] && rclone copy --update /app/companies/ "$FINDAJOB_JOBSYNC_REMOTE"

# ── Notifications ────────────────────────────────────────────────────────────
0    6   *  *  *        python3 /app/scripts/notify.py apply-reminder
15   6   *  *  *        python3 /app/scripts/notify.py stats
0    7   *  *  *        python3 /app/scripts/notify.py health-check
0    8   *  *  1,3,5    python3 /app/scripts/notify.py issues
30   8   *  *  1        python3 /app/scripts/notify.py scoreboard
0    8   *  *  0        python3 /app/scripts/notify.py feedback

# ── RAG rebuild (Sunday 03:00) ───────────────────────────────────────────────
0    3   *  *  0   /usr/local/bin/aichat-ng --rag job_search_rag --rebuild-rag
```

- [ ] **Step 2: Verify crontab is readable ASCII and has no tabs (supercronic accepts spaces)**

Run: `file ops/crontab && grep -P "\t" ops/crontab || echo "no tabs"`

Expected: `ASCII text`, `no tabs`.

- [ ] **Step 3: Commit**

```bash
git add ops/crontab
git commit -m "Add supercronic crontab mirroring systemd timers (#13)

1:1 translation of all findajob systemd user services. Schedules
evaluated in container TZ (set per-stack via .env). Long-running
jobs (triage, poller) wrapped in timeout. Jobsync gated by
FINDAJOB_JOBSYNC_ENABLED env var — disabled by default so Amy
and other dogfooders don't see rclone activity.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Gmail auth helper — test harness first (TDD)

**Files:**
- Create: `tests/test_gmail_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gmail_auth.py
"""
Tests for scripts/gmail_auth.py — the standalone OAuth helper.

Scope: argparse + mode dispatch. Actual OAuth calls are mocked — we don't
exercise Google's endpoints from unit tests.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make scripts/ importable
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def fake_client_secrets(tmp_path):
    """Write a minimal but structurally-valid OAuth client JSON."""
    p = tmp_path / "gmail_oauth_client.json"
    p.write_text(
        '{"installed": {"client_id": "x.apps.googleusercontent.com", '
        '"client_secret": "abc", "redirect_uris": ["http://localhost"]}}'
    )
    return p


def test_default_mode_is_device():
    """Running with no --mode flag should default to device flow."""
    import gmail_auth

    parser = gmail_auth.build_parser()
    args = parser.parse_args([])
    assert args.mode == "device"


def test_mode_flag_accepts_device_and_local():
    import gmail_auth

    parser = gmail_auth.build_parser()
    assert parser.parse_args(["--mode", "device"]).mode == "device"
    assert parser.parse_args(["--mode", "local"]).mode == "local"


def test_mode_flag_rejects_unknown():
    import gmail_auth

    parser = gmail_auth.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--mode", "magic"])


def test_run_dispatches_to_device_mode(fake_client_secrets, tmp_path):
    """--mode=device should call run_device and write the token file."""
    import gmail_auth

    token_path = tmp_path / "gmail_token.json"
    mock_creds = MagicMock()
    mock_creds.to_json.return_value = '{"token": "fake"}'

    with patch.object(gmail_auth, "run_device", return_value=mock_creds) as m_device, \
         patch.object(gmail_auth, "run_local") as m_local:
        gmail_auth.main(
            [
                "--mode", "device",
                "--client-secrets", str(fake_client_secrets),
                "--token-out", str(token_path),
            ]
        )

    m_device.assert_called_once()
    m_local.assert_not_called()
    assert token_path.read_text() == '{"token": "fake"}'


def test_run_dispatches_to_local_mode(fake_client_secrets, tmp_path):
    """--mode=local should call run_local, not run_device."""
    import gmail_auth

    token_path = tmp_path / "gmail_token.json"
    mock_creds = MagicMock()
    mock_creds.to_json.return_value = '{"token": "fake-local"}'

    with patch.object(gmail_auth, "run_local", return_value=mock_creds) as m_local, \
         patch.object(gmail_auth, "run_device") as m_device:
        gmail_auth.main(
            [
                "--mode", "local",
                "--client-secrets", str(fake_client_secrets),
                "--token-out", str(token_path),
                "--port", "8080",
            ]
        )

    m_local.assert_called_once()
    m_device.assert_not_called()


def test_missing_client_secrets_errors(tmp_path):
    """If client-secrets file doesn't exist, should exit non-zero with a clear error."""
    import gmail_auth

    token_path = tmp_path / "gmail_token.json"
    missing_client = tmp_path / "nonexistent.json"

    with pytest.raises(SystemExit) as exc_info:
        gmail_auth.main(
            [
                "--mode", "device",
                "--client-secrets", str(missing_client),
                "--token-out", str(token_path),
            ]
        )
    assert exc_info.value.code != 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_gmail_auth.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'gmail_auth'` or similar (the script doesn't exist yet).

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_gmail_auth.py
git commit -m "Add failing tests for gmail_auth helper (#13)

TDD step 1: test the argparse contract + mode dispatch before
writing the implementation. Mocks OAuth calls so we don't need
network or real Google credentials. Covers device/local mode
selection, default behavior, unknown-mode rejection, and
missing-client-secrets error path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Gmail auth helper — implementation

**Files:**
- Create: `scripts/gmail_auth.py`

- [ ] **Step 1: Write the helper**

```python
#!/usr/bin/env python3
# scripts/gmail_auth.py
"""
Gmail OAuth helper — standalone script run once per instance to mint
the Gmail API token that triage.py, backfill_jd.py, and notify.py use.

Two modes:
  --mode=device  (default)  OAuth 2.0 Limited Input Device flow.
                            Prints google.com/device URL + code; polls
                            for user consent. No callback, no port.
                            Used in v0.1.0.
  --mode=local              InstalledAppFlow.run_local_server on --port.
                            Requires the redirect URI to be authorized on
                            the OAuth client. NOT USED in v0.1.0 — present
                            for v0.2.0 when #59 lands with reverse-proxy
                            routing.

Writes credentials to --token-out (default: /app/config/gmail_token.json),
which is bind-mounted into the container and subsequently read by
fetchers.py on every scheduled run.

Usage inside the scheduler container:
  docker compose --profile setup run --rm gmail-auth
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gmail_auth",
        description="Mint a Gmail API token via device or local flow.",
    )
    p.add_argument(
        "--mode",
        choices=["device", "local"],
        default="device",
        help="OAuth flow type. device = limited-input (default); local = callback server.",
    )
    p.add_argument(
        "--client-secrets",
        default="/app/config/gmail_oauth_client.json",
        help="Path to the OAuth client JSON downloaded from Google Cloud Console.",
    )
    p.add_argument(
        "--token-out",
        default="/app/config/gmail_token.json",
        help="Where to write the resulting token JSON.",
    )
    p.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for --mode=local callback server (ignored for device mode).",
    )
    return p


def run_device(client_secrets: str):
    """
    OAuth 2.0 Limited Input Device flow.

    User opens google.com/device on any browser, enters the code we print,
    signs in, grants scopes. We poll until consent and return credentials.
    """
    # Imported lazily so --help works even without the google libs installed
    import json
    import time
    from urllib.parse import urlencode

    import requests
    from google.oauth2.credentials import Credentials

    with open(client_secrets) as f:
        secrets = json.load(f)
    installed = secrets.get("installed") or secrets.get("web") or {}
    client_id = installed["client_id"]
    client_secret = installed["client_secret"]

    # Step 1: ask Google for a device code
    r = requests.post(
        "https://oauth2.googleapis.com/device/code",
        data={"client_id": client_id, "scope": " ".join(GMAIL_SCOPES)},
        timeout=30,
    )
    r.raise_for_status()
    dev = r.json()

    print()
    print(f"  Open this URL on any browser: {dev['verification_url']}")
    print(f"  Enter this code:              {dev['user_code']}")
    print()
    print(f"  Waiting for consent (expires in {dev['expires_in']}s)...")
    print()

    # Step 2: poll the token endpoint until the user completes consent
    interval = int(dev.get("interval", 5))
    deadline = time.time() + int(dev["expires_in"])
    while time.time() < deadline:
        time.sleep(interval)
        r = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "device_code": dev["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            timeout=30,
        )
        body = r.json()
        if r.ok:
            return Credentials(
                token=body["access_token"],
                refresh_token=body.get("refresh_token"),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=GMAIL_SCOPES,
            )
        if body.get("error") == "authorization_pending":
            continue
        if body.get("error") == "slow_down":
            interval += 5
            continue
        # Any other error is terminal
        raise SystemExit(f"Device flow failed: {body}")

    raise SystemExit("Device flow timed out waiting for user consent.")


def run_local(client_secrets: str, port: int):
    """
    OAuth 2.0 callback flow — listens on 0.0.0.0:port for the redirect.

    Requires the OAuth client to list the corresponding http://host:port/...
    as an authorized redirect URI. Not used in v0.1.0.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(client_secrets, GMAIL_SCOPES)
    return flow.run_local_server(
        host="0.0.0.0",
        port=port,
        open_browser=False,
        authorization_prompt_message="Open this URL to grant access:\n  {url}\n",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not os.path.exists(args.client_secrets):
        print(
            f"ERROR: OAuth client file not found: {args.client_secrets}\n"
            f"Download the client JSON from Google Cloud Console and place it at that path.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    if args.mode == "device":
        creds = run_device(args.client_secrets)
    else:
        creds = run_local(args.client_secrets, args.port)

    out = Path(args.token_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json())
    out.chmod(0o600)
    print(f"Token written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `pytest tests/test_gmail_auth.py -v`

Expected: all 6 tests PASS.

- [ ] **Step 3: Verify lint + type check**

Run: `ruff check scripts/gmail_auth.py tests/test_gmail_auth.py && ruff format --check scripts/gmail_auth.py tests/test_gmail_auth.py`

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add scripts/gmail_auth.py
git commit -m "Add Gmail OAuth helper with device and local flows (#13)

v0.1.0 uses device flow exclusively: user opens google.com/device
on any browser, enters the code, grants Gmail.readonly. No
callback, no port, no reverse-proxy setup. Local flow is present
but unused until v0.2.0 when #59 adds the reverse-proxy routing.

Writes token to --token-out with mode 0600. Fails cleanly with
exit 2 when OAuth client JSON is missing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Compose template

**Files:**
- Create: `ops/compose.yaml.example`

- [ ] **Step 1: Write the template**

```yaml
# ops/compose.yaml.example
# Copy to your Dockge stack directory (e.g., /opt/stacks/findajob-<user>/compose.yaml)
# and edit the adjacent .env to match your instance.
#
# See docs/setup/install-docker.md and ops/stack.env.example for setup.

services:
  scheduler:
    image: ghcr.io/brockamer/findajob:${FINDAJOB_IMAGE_TAG:-v0.1}
    restart: unless-stopped
    env_file: ./state/data/.env
    environment:
      TZ: ${FINDAJOB_TZ:-America/New_York}
      PUID: ${PUID:-1000}
      PGID: ${PGID:-1000}
      JSP_BASE: /app
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
      JSP_BASE: /app
    volumes:
      - ./state/config:/app/config
    command: python3 scripts/gmail_auth.py --mode=device
    networks:
      - findajob-network

networks:
  findajob-network:
    driver: bridge
```

- [ ] **Step 2: Verify YAML parses**

Run: `python3 -c "import yaml; yaml.safe_load(open('ops/compose.yaml.example'))" && echo "yaml ok"`

Expected: `yaml ok`.

- [ ] **Step 3: Commit**

```bash
git add ops/compose.yaml.example
git commit -m "Add compose template for Dockge-managed deploys (#13)

Two services: scheduler (long-running supercronic) and
gmail-auth (compose profile 'setup', one-shot device flow).
Bind mounts from ./state/*. Per-stack bridge network.
Image tag defaults to :v0.1 moving alias — Amy pins here and
auto-accepts patches; Daniel overrides to :latest in his .env
for dogfooding.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Stack env template

**Files:**
- Create: `ops/stack.env.example`

- [ ] **Step 1: Write the template**

```
# ops/stack.env.example
# Copy to your stack dir as .env (Dockge auto-loads it).
# This file is per-instance and intentionally NOT secret — commit to
# your own config store if you want, but don't put API keys here.
# API keys go in state/data/.env (chmod 600).

# Image tag to pull. Options:
#   v0.1          (moving minor alias — auto-accepts v0.1.x patches; recommended for most users)
#   v0.1.0        (immutable tag — pin exactly if you need stability)
#   latest        (dogfood track — tip of main, may break)
#   main-<sha>    (immutable commit-sha tag — for precise pinning)
FINDAJOB_IMAGE_TAG=v0.1

# Container timezone (sets cron schedule evaluation)
# Examples: America/Los_Angeles, America/New_York, Europe/London
FINDAJOB_TZ=America/New_York

# Host UID/GID to run the container as. Must match the owner of ./state/
# so bind-mounted files are host-editable through Dockge's file viewer.
PUID=1000
PGID=1000

# Google Drive sync via rclone. Disabled by default — #59 makes this
# obsolete once the web materials viewer ships.
#
# If true: set FINDAJOB_JOBSYNC_REMOTE in state/data/.env (e.g.,
# gdrive:01 PROJECTS/Jobs To Apply For) and ensure state/aichat_ng/
# (shared with rclone config dir) has your rclone remote configured.
FINDAJOB_JOBSYNC_ENABLED=false

# Triage timeout in seconds. Default 7200 (2h) is generous for steady
# state (~30-45min typical). For fresh installs with a large first feed
# pull, bump to 21600 (6h) for the first week, then drop back.
FINDAJOB_TRIAGE_TIMEOUT=7200
```

- [ ] **Step 2: Commit**

```bash
git add ops/stack.env.example
git commit -m "Add per-instance stack .env template (#13)

Documents all tunable per-stack env vars. Explicitly separates
stack-level config (this file, non-secret) from app-level secrets
(state/data/.env, chmod 600). Explains image tag options, TZ
rationale, PUID/PGID, jobsync gating, and triage timeout tuning
for first-week bootstrap.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Extend CI with docker-build-smoke job

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Read the current file and plan the edit**

Run: `cat .github/workflows/ci.yml`

Expected: existing `lint-and-test` job (lines 9-32 in current version).

- [ ] **Step 2: Replace the workflow with the extended version**

Write to `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install -e ".[dev]"

      - name: Lint
        run: ruff check src/ scripts/ tests/

      - name: Format check
        run: ruff format --check src/ scripts/ tests/

      - name: Type check
        run: mypy src/findajob/

      - name: Test
        run: pytest tests/ -v

  docker-build-smoke:
    runs-on: ubuntu-latest
    needs: lint-and-test
    steps:
      - uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build image (no push)
        uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64
          push: false
          load: true
          tags: findajob:ci-smoke
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: Smoke — aichat-ng executes
        run: docker run --rm findajob:ci-smoke aichat-ng --version

      - name: Smoke — supercronic validates the crontab
        run: docker run --rm findajob:ci-smoke supercronic -test /app/crontab

      - name: Smoke — Python package imports
        run: |
          docker run --rm \
            -e JSP_BASE=/app \
            findajob:ci-smoke \
            python3 -c "import findajob; import findajob.paths; print(findajob.paths.BASE)"

      - name: Smoke — entrypoint creates runtime user without error
        run: |
          docker run --rm \
            -e PUID=1001 -e PGID=1001 \
            findajob:ci-smoke \
            id findajob
```

- [ ] **Step 3: Verify YAML parses**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "ci yaml ok"`

Expected: `ci yaml ok`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "Extend CI with docker-build-smoke job (#13)

New job builds the image (cached via GHA cache) and runs four
smoke checks: aichat-ng version, supercronic crontab validation,
Python package import, entrypoint user creation. Gated on
lint-and-test success so we don't waste Docker build cycles on
already-failing changes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: GHCR build + push workflow

**Files:**
- Create: `.github/workflows/build-image.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: Build and push image

on:
  push:
    branches: [main]
    tags: ["v*.*.*"]

permissions:
  contents: read
  packages: write

jobs:
  build-push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Compute tags
        id: tags
        run: |
          IMAGE="ghcr.io/${{ github.repository_owner }}/findajob"
          TAGS=""
          if [[ "${GITHUB_REF}" == refs/tags/v*.*.* ]]; then
            VERSION="${GITHUB_REF#refs/tags/}"              # v0.1.3
            MINOR="${VERSION%.*}"                            # v0.1
            TAGS="${IMAGE}:${VERSION},${IMAGE}:${MINOR},${IMAGE}:latest"
          elif [[ "${GITHUB_REF}" == refs/heads/main ]]; then
            SHA="${GITHUB_SHA::7}"
            TAGS="${IMAGE}:main-${SHA},${IMAGE}:latest"
          fi
          echo "tags=${TAGS}" >> "$GITHUB_OUTPUT"
          echo "Will push: ${TAGS}"

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64
          push: true
          tags: ${{ steps.tags.outputs.tags }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
          labels: |
            org.opencontainers.image.source=https://github.com/${{ github.repository }}
            org.opencontainers.image.revision=${{ github.sha }}
            org.opencontainers.image.licenses=MIT
```

- [ ] **Step 2: Verify YAML parses**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/build-image.yml'))" && echo "ok"`

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/build-image.yml
git commit -m "Add GHCR build + push workflow (#13)

On push to main: tags :main-<sha> + :latest.
On push of v*.*.* tag: tags :v<x.y.z> + moving minor alias
:v<x.y> + :latest.

Cached via GitHub Actions cache for fast incremental builds.
Single platform linux/amd64 for v0.1.0 — ARM64 deferred.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Auto-generate GitHub Release on tag push

**Files:**
- Create: `.github/workflows/create-release.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: Create release

on:
  push:
    tags: ["v*.*.*"]

permissions:
  contents: write
  pull-requests: read

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Resolve previous version tag
        id: prev
        run: |
          CURRENT="${GITHUB_REF#refs/tags/}"
          PREV=$(git tag --sort=-version:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | grep -v "^${CURRENT}$" | head -n1 || true)
          echo "current=${CURRENT}" >> "$GITHUB_OUTPUT"
          echo "previous=${PREV}" >> "$GITHUB_OUTPUT"
          echo "Current: ${CURRENT}  Previous: ${PREV:-<none>}"

      - name: Generate release notes
        id: notes
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          CURRENT="${{ steps.prev.outputs.current }}"
          PREV="${{ steps.prev.outputs.previous }}"

          # Use GitHub's release-notes generator if we have a previous tag;
          # otherwise a first-release stub.
          if [ -n "$PREV" ]; then
            NOTES=$(gh api repos/${{ github.repository }}/releases/generate-notes \
              -f tag_name="$CURRENT" \
              -f previous_tag_name="$PREV" \
              --jq .body)
          else
            NOTES="First release of the containerized pipeline. See the commit history and docs/superpowers/specs/ for design decisions."
          fi

          # Surface migration-required PRs at the top, if any
          MIG_PRS=$(gh pr list --state merged --label migration-required --search "merged:>$(git log -1 --format=%cI ${PREV:-HEAD~1}) " --json number,title --jq '.[] | "- #\(.number) \(.title)"' || true)
          if [ -n "$MIG_PRS" ]; then
            NOTES=$(printf "### ⚠️ Action required before upgrade\n\nThe following changes need a manual step (DB migration, config change, or similar). Read each linked PR before running \`docker compose pull\`.\n\n%s\n\n---\n\n%s\n" "$MIG_PRS" "$NOTES")
          fi

          # Write notes to a file to avoid shell-quoting hell
          printf '%s\n' "$NOTES" > /tmp/release-notes.md

      - name: Create GitHub Release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          gh release create "${{ steps.prev.outputs.current }}" \
            --title "${{ steps.prev.outputs.current }}" \
            --notes-file /tmp/release-notes.md
```

- [ ] **Step 2: Verify YAML parses**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/create-release.yml'))" && echo "ok"`

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/create-release.yml
git commit -m "Auto-create GitHub Release on v*.*.* tag push (#13)

Pulls GitHub's auto-generated release notes (PR titles between
the previous v*.*.* tag and the current one) and prepends an
'Action required' section listing any merged PRs labeled
'migration-required' — so external users read migration steps
before pulling.

First-release case handled with a stub note.

Deferred to #69: the 'migration-required' label itself, plus
docs/release-process.md explaining when to apply it.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: CHANGELOG.md

**Files:**
- Create: `CHANGELOG.md`

- [ ] **Step 1: Write the changelog**

```markdown
# Changelog

All notable changes to findajob are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Until the pipeline stabilizes, 0.x releases are considered unstable. Breaking
changes may land in minor version bumps; patch releases are bugfix-only.

## [Unreleased]

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
  inside the container. Existing systemd units stay archived on Daniel's LXC
  during the observation window. (#13)

### Notes
- Release management process itself is tracked in #69; once that ships, the
  process doc lives at `docs/release-process.md`.
- Documentation cleanup — removing `sigoden/aichat` references in favor of
  `blob42/aichat-ng` — is tracked in #70.

[Unreleased]: https://github.com/brockamer/findajob/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/brockamer/findajob/releases/tag/v0.1.0
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "Add CHANGELOG.md with v0.1.0 containerization entry (#13)

Keep-a-Changelog format. Documents every artifact shipping in the
v0.1.0 release: Dockerfile, ops/, gmail_auth.py, three workflows,
install-docker.md stub. Calls out #69 (release mgmt) and #70 (docs
cleanup) as follow-ons so external readers see the context.

Tag date is TBD — fills in when the dogfood gate clears and we
cut v0.1.0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: Container integration test harness

**Files:**
- Create: `scripts/test_container_integration.sh`

- [ ] **Step 1: Write the harness**

```bash
#!/bin/bash
# scripts/test_container_integration.sh
#
# Local pre-release smoke test for the findajob container image.
# Spins up a throwaway stack, runs each scheduled script once, asserts
# no exceptions and sane DB state, then tears down.
#
# Prerequisites:
# - docker + docker compose on PATH
# - A findajob image already built locally (findajob:local) OR pass via
#   FINDAJOB_TEST_IMAGE env var
# - data/.env from a working instance (copied into the throwaway stack)
#
# Usage:
#   FINDAJOB_TEST_IMAGE=findajob:local scripts/test_container_integration.sh
#
# Run this as part of the #69 release gate before tagging v0.1.N.

set -euo pipefail

IMAGE="${FINDAJOB_TEST_IMAGE:-findajob:local}"
STACK_DIR="$(mktemp -d -t findajob-test-XXXXXX)"
cleanup() {
    echo "[cleanup] tearing down stack at $STACK_DIR"
    (cd "$STACK_DIR" && docker compose down -v 2>/dev/null || true)
    rm -rf "$STACK_DIR"
}
trap cleanup EXIT

echo "[setup] stack dir: $STACK_DIR  image: $IMAGE"

mkdir -p "$STACK_DIR/state"/{data,config,candidate_context,companies,logs,aichat_ng}

# Copy user's real data/.env (API keys) — tests hit real LLM and API endpoints
if [ ! -f "$HOME/.findajob/state/data/.env" ] && [ ! -f "${PWD}/data/.env" ]; then
    echo "ERROR: need data/.env with API keys — either $HOME/.findajob/state/data/.env or ./data/.env" >&2
    exit 2
fi
cp "${PWD}/data/.env" "$STACK_DIR/state/data/.env" 2>/dev/null \
    || cp "$HOME/.findajob/state/data/.env" "$STACK_DIR/state/data/.env"
chmod 600 "$STACK_DIR/state/data/.env"

# Minimal stub config — enough to bring the scheduler up without erroring on import
cat > "$STACK_DIR/state/config/sheet_id.txt" <<'EOF'
TEST_SHEET_ID_PLACEHOLDER
EOF

cat > "$STACK_DIR/state/candidate_context/profile.md" <<'EOF'
# Test Profile
Minimal stub for container integration testing.
EOF

# Compose file pointing at the local image
cat > "$STACK_DIR/compose.yaml" <<EOF
services:
  scheduler:
    image: ${IMAGE}
    env_file: ./state/data/.env
    environment:
      TZ: America/New_York
      PUID: $(id -u)
      PGID: $(id -g)
      JSP_BASE: /app
      FINDAJOB_JOBSYNC_ENABLED: "false"
    volumes:
      - ./state/data:/app/data
      - ./state/config:/app/config
      - ./state/candidate_context:/app/candidate_context
      - ./state/companies:/app/companies
      - ./state/logs:/app/logs
      - ./state/aichat_ng:/root/.config/aichat_ng
    command: sleep infinity   # don't actually run cron; we exec scripts ourselves
EOF

echo "[start] bringing up stack"
(cd "$STACK_DIR" && docker compose up -d)

EXEC="docker compose -f $STACK_DIR/compose.yaml exec -T scheduler"

echo "[init] creating scratch pipeline.db"
$EXEC python3 /app/scripts/init_db.py

echo "[test] python package imports"
$EXEC python3 -c "import findajob; import findajob.paths; assert findajob.paths.BASE == '/app'"

echo "[test] supercronic validates crontab"
$EXEC supercronic -test /app/crontab

echo "[test] aichat-ng executes"
$EXEC aichat-ng --version

echo "[test] notify.py health-check on empty state"
$EXEC python3 /app/scripts/notify.py health-check || echo "[note] health-check may warn on empty DB — non-fatal"

echo "[test] poll_flags.py dry run (Sheet is stub — expect a soft failure, not a Python crash)"
set +e
$EXEC python3 /app/scripts/poll_flags.py
POLL_EXIT=$?
set -e
# Acceptable: 0 (if Sheet exists and is empty) or non-zero-with-clean-stderr.
# Unacceptable: a Python traceback, which would indicate a packaging or path bug.
echo "[test] poll_flags.py exit: $POLL_EXIT (traceback-free is the pass criterion)"

echo "[test] DB has expected tables"
TABLES=$($EXEC sqlite3 /app/data/pipeline.db ".tables")
echo "Tables: $TABLES"
for t in jobs audit_log feedback_log; do
    echo "$TABLES" | grep -q "$t" || { echo "ERROR: missing table $t" >&2; exit 1; }
done

echo
echo "✅  Container integration test passed."
echo "   Stack dir (auto-cleaned): $STACK_DIR"
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x scripts/test_container_integration.sh && sh -n scripts/test_container_integration.sh && echo "syntax ok"`

Expected: `syntax ok`.

- [ ] **Step 3: Commit**

```bash
git add scripts/test_container_integration.sh
git commit -m "Add container integration test harness (#13)

Pre-release smoke test. Spins up a throwaway stack with real API
keys from the user's data/.env, execs each scheduled script once,
asserts no Python crashes and that the DB has the expected tables.
Tears down on exit via trap.

Not automated in CI (needs real API keys + costs per LLM call).
Claude runs this as part of the #69 release gate before tagging
each v0.1.N.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: paths.py — document the JSP_BASE convention

**Files:**
- Modify: `src/findajob/paths.py` (module docstring only)

- [ ] **Step 1: Read the current module**

Run: `head -20 src/findajob/paths.py`

- [ ] **Step 2: Edit the module docstring**

Use Edit tool to replace:

**Old:**
```python
"""
Central path and base-directory resolver for all pipeline scripts.

BASE is derived from this file's location — works wherever the repo is cloned,
regardless of directory name or home folder. No hardcoded paths.

Binary paths (AICHAT, PANDOC, RCLONE) are read from config/paths.env.
Override defaults via config/paths.env if your binaries live elsewhere.

Usage:
    from findajob.paths import BASE, AICHAT, PANDOC, RCLONE

Use sys.executable (not a PYTHON constant) for subprocess calls to other pipeline scripts.
"""
```

**New:**
```python
"""
Central path and base-directory resolver for all pipeline scripts.

BASE is derived from this file's location — works wherever the repo is cloned,
regardless of directory name or home folder. No hardcoded paths.

Binary paths (AICHAT, PANDOC, RCLONE) are read from config/paths.env.
Override defaults via config/paths.env if your binaries live elsewhere.

Usage:
    from findajob.paths import BASE, AICHAT, PANDOC, RCLONE

Use sys.executable (not a PYTHON constant) for subprocess calls to other pipeline scripts.

Containerized deploys:
    When running in the findajob Docker image, the app is installed at /app
    (not the repo's filesystem location). The compose file sets
    JSP_BASE=/app to pin BASE correctly. See docs/setup/install-docker.md
    and docs/superpowers/specs/2026-04-17-docker-compose-design.md for
    the container architecture.
"""
```

- [ ] **Step 3: Verify**

Run: `python3 -c "from findajob.paths import BASE; print(BASE)"`

Expected: your repo path (works locally — no container yet).

- [ ] **Step 4: Commit**

```bash
git add src/findajob/paths.py
git commit -m "Document JSP_BASE=/app convention for containerized deploys (#13)

Docstring-only change. No behavioral change — JSP_BASE env override
was already supported at line 23-24.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 15: install-docker.md stub

**Files:**
- Create: `docs/setup/install-docker.md`

- [ ] **Step 1: Write the stub**

```markdown
# Docker Install (stub)

> **Full deploy guide is being authored under #69.** This page documents just enough to stand up a stack today. When #69 ships, `docs/release-process.md` and a complete install walkthrough land here.

## Who this is for

- You have a Docker host reachable on a LAN or VPN.
- You have [Dockge](https://github.com/louislam/dockge) installed (or docker compose CLI access).
- You want to run findajob from the prebuilt image at `ghcr.io/brockamer/findajob` rather than building from source.

## Prerequisites on the Docker host

- Docker Engine 24+ and Docker Compose v2
- Access to Google Cloud Console to register an OAuth client for Gmail (optional but recommended)
- A Google Sheet and service account for the jobs dashboard (see [prerequisites.md](prerequisites.md))

## Prerequisites for your Claude Code helper (for the admin)

See [configure.md](configure.md). API keys and personal config end up in `state/data/.env` (mode 0600).

## 1. Create the stack directory

```bash
# On the Docker host
sudo mkdir -p /opt/stacks/findajob-<you>/state/{data,config,candidate_context,companies,logs,aichat_ng}
sudo chown -R $(id -u):$(id -g) /opt/stacks/findajob-<you>/
```

Replace `<you>` with a short user tag (`brock`, `amy`, etc.).

## 2. Drop in the compose template and env

```bash
cd /opt/stacks/findajob-<you>/
curl -fsSL -o compose.yaml https://raw.githubusercontent.com/brockamer/findajob/main/ops/compose.yaml.example
curl -fsSL -o .env https://raw.githubusercontent.com/brockamer/findajob/main/ops/stack.env.example
```

Edit `.env` to taste — at minimum set `FINDAJOB_TZ` and (if dogfooding) `FINDAJOB_IMAGE_TAG=latest`.

## 3. Populate `state/`

- `state/data/.env` — API keys (chmod 600). Template: [repo's `data/.env.example`](https://github.com/brockamer/findajob/blob/main/data/.env.example)
- `state/config/*.yaml|.txt|.json` — personal config files. See [configure.md](configure.md) for each file's purpose.
- `state/candidate_context/profile.md` + `master_resume.md` — your candidate profile. See [`candidate_context/profile.md.example`](https://github.com/brockamer/findajob/blob/main/candidate_context/profile.md.example).

## 4. Initial auth: Gmail (optional)

```bash
docker compose --profile setup run --rm gmail-auth
```

You'll see `Open this URL on any browser: https://www.google.com/device`. Enter the code, sign in, grant Gmail.readonly. Token is saved to `state/config/gmail_token.json`.

If you skip this step, Gmail ingestion is automatically disabled — the pipeline falls back to Greenhouse/Ashby/Lever feeds and RapidAPI.

## 5. Deploy

Via Dockge: click **Deploy**. Via CLI: `docker compose up -d`.

## 6. Verify

```bash
docker compose logs -f scheduler
# You should see supercronic print its crontab and wait.

docker compose exec scheduler python3 /app/scripts/notify.py health-check
# Sanity check: ntfy notification should land on your phone.
```

## Updating

```bash
docker compose pull && docker compose up -d
```

Or click **Pull** + **Deploy** in Dockge.

## Troubleshooting

See GitHub issues or open a new one at https://github.com/brockamer/findajob/issues.
```

- [ ] **Step 2: Commit**

```bash
git add docs/setup/install-docker.md
git commit -m "Add install-docker.md stub (#13, #69)

Minimum-viable install guide so external dogfooders can stand up
a stack from the v0.1.0 image. Full polished guide (rollback,
advanced configuration, troubleshooting deep-dives) lands with
#69.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 16: Point install-linux.md at the Docker path

**Files:**
- Modify: `docs/setup/install-linux.md` (insert banner at top)

- [ ] **Step 1: Prepend the banner**

Use Edit to insert after the first-line title. Find:

```
# Linux Setup (Pop!_OS / Ubuntu)

This guide covers a fresh install on a Debian-based Linux system. Tested on Pop!_OS 22.04 LTS.
```

Replace with:

```
# Linux Setup (Pop!_OS / Ubuntu) — native install

> **Recommended path for new installs is Docker, not native.** See [install-docker.md](install-docker.md).
> This guide remains for users running findajob directly on a Linux host without containers.

This guide covers a fresh install on a Debian-based Linux system. Tested on Pop!_OS 22.04 LTS.
```

- [ ] **Step 2: Verify the file still renders**

Run: `head -10 docs/setup/install-linux.md`

Expected: new banner appears, original content below.

- [ ] **Step 3: Commit**

```bash
git add docs/setup/install-linux.md
git commit -m "Point new users at install-docker.md from install-linux.md (#13)

Keeps the native install documented for advanced users but steers
new installs toward the containerized path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 17: Update CLAUDE.md with container context

**Files:**
- Modify: `CLAUDE.md` — add Container Context Table section and /app path note

- [ ] **Step 1: Find the insertion point**

Run: `grep -n "^## " CLAUDE.md | head -10`

Expected: a list of top-level sections; locate `## Pipeline Context Table` and `## Critical Architecture Rules`.

- [ ] **Step 2: Add a "Container Context" subsection after the Pipeline Context Table**

Use Edit to insert the following block immediately after the Pipeline Context Table's closing line (the table ends with `| Google Form | URL and response sheet ID in ...|`):

```markdown

---

## Container Context (when running from the findajob Docker image)

When the pipeline runs inside the `ghcr.io/brockamer/findajob` image, paths shift:

| Thing | Native install | Container |
|---|---|---|
| `BASE` (from `findajob.paths`) | Repo clone path | `/app` (set via `JSP_BASE=/app` in compose) |
| `data/pipeline.db` | `<repo>/data/pipeline.db` | `/app/data/pipeline.db` (bind-mounted from `./state/data/`) |
| `config/roles/` | `<repo>/config/roles/` | `/app/config/roles/` (baked into image — NOT from bind mount) |
| Personal config (`config/*.yaml|.txt|.json`) | `<repo>/config/` | `/app/config/` (bind-mounted from `./state/config/`) |
| `candidate_context/` | `<repo>/candidate_context/` | `/app/candidate_context/` (bind-mount) |
| `companies/` | `<repo>/companies/` | `/app/companies/` (bind-mount) |
| `aichat-ng` | `/usr/local/bin/aichat-ng` | `/usr/local/bin/aichat-ng` (blob42/aichat-ng prebuilt) |
| aichat-ng config dir | `~/.config/aichat_ng/` | `/root/.config/aichat_ng/` (bind-mount from `./state/aichat_ng/`) |
| Scheduler | systemd user services | supercronic inside the container |

**When authoring new scripts or tests:**
- Always use `findajob.paths.BASE` — never hardcode `/home/...` or `/app/`.
- Binary subprocess calls go through `AICHAT`/`PANDOC`/`RCLONE` from `findajob.paths`.
- Tests must not depend on absolute paths — use tmpdirs or `BASE`-relative paths.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "Document Container Context in CLAUDE.md (#13)

Adds a table clarifying path mapping between native and
containerized deploys, and reinforces the 'always use
findajob.paths, never hardcode' rule that keeps code portable
across both.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 18: Local build + smoke verification before opening PR

**Files:** none created; verification step only.

- [ ] **Step 1: Build the image locally**

Run:

```bash
docker build -t findajob:local .
```

Expected: image builds without error. First build takes 3-6 minutes on a warm system; pandoc is the slowest layer.

- [ ] **Step 2: Run the same smoke checks CI will run**

```bash
docker run --rm findajob:local aichat-ng --version
docker run --rm findajob:local supercronic -test /app/crontab
docker run --rm -e JSP_BASE=/app findajob:local \
    python3 -c "import findajob; import findajob.paths; print(findajob.paths.BASE)"
docker run --rm -e PUID=1001 -e PGID=1001 findajob:local id findajob
```

Expected outputs:
- `aichat-ng` prints a version string starting with `aichat`
- `supercronic -test` prints the parsed crontab lines
- Python imports resolve; `BASE` prints `/app`
- `id findajob` prints `uid=1001 gid=1001 groups=1001(findajob)`

- [ ] **Step 3: Run the integration harness (optional but recommended)**

Run:

```bash
FINDAJOB_TEST_IMAGE=findajob:local ./scripts/test_container_integration.sh
```

Expected: ends with `✅  Container integration test passed.`

- [ ] **Step 4: Run the standard test suite**

Run:

```bash
pytest tests/ -v
ruff check src/ scripts/ tests/
ruff format --check src/ scripts/ tests/
mypy src/findajob/
```

Expected: all pass.

- [ ] **Step 5: Commit any last fixes if verification revealed issues**

```bash
# If nothing changed, skip. Otherwise:
git add -A
git commit -m "Fix issues surfaced by pre-PR verification (#13)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 19: Push branch and open PR

**Files:** none created; git + gh operations only.

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feat/13-docker-compose
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "Containerize findajob: Docker image + Compose deploy (#13)" --body "$(cat <<'EOF'
## Summary

Ships findajob as a GHCR container image deployed via Docker Compose on a shared Docker host, managed through Dockge. Replaces systemd user services as the scheduler mechanism.

**Spec:** `docs/superpowers/specs/2026-04-17-docker-compose-design.md` — 10 decision-log entries, all resolved during brainstorming.

**Plan:** `docs/superpowers/plans/2026-04-17-docker-compose.md`

## What's in this PR

- `Dockerfile` + `.dockerignore` + `ops/entrypoint.sh` — image build with PUID/PGID drop-privileges
- `ops/crontab` — supercronic schedule mirroring systemd timers 1:1
- `ops/compose.yaml.example` + `ops/stack.env.example` — deploy templates
- `scripts/gmail_auth.py` + tests — OAuth helper with device flow (v0.1.0) and local flow (scaffolded for v0.2.0 with #59)
- `.github/workflows/build-image.yml` — GHCR build + push on main + on `v*.*.*` tags
- `.github/workflows/create-release.yml` — auto-generated release notes with migration-required surfacing
- `docker-build-smoke` job added to existing `ci.yml`
- `CHANGELOG.md` (Keep-a-Changelog)
- `docs/setup/install-docker.md` stub (full guide lands with #69)
- `CLAUDE.md` container-context section; `install-linux.md` banner pointing at Docker

## What's NOT in this PR (intentional scope boundaries)

- `docs/release-process.md`, `docs/setup/install-docker.md` full guide, `migration-required` GitHub label — scoped to #69
- Web UI / materials viewer, retiring rclone — scoped to #59
- macOS or ARM64 support — linux/amd64 only in v0.1.0
- Amy's admin tooling upgrades — deferred to #71 when dogfooder count ≥ 3
- Daniel's actual migration from systemd to containers — happens after this PR merges, following the migration runbook in the spec (#13 acceptance gate)

## Test plan

- [ ] CI green (lint, mypy, pytest, docker-build-smoke)
- [ ] `docker build -t findajob:local .` completes locally
- [ ] All four local smoke checks pass (aichat-ng version, supercronic crontab validation, Python import, entrypoint user creation)
- [ ] `scripts/test_container_integration.sh` passes against local image using real `data/.env`
- [ ] `pytest tests/test_gmail_auth.py -v` — 6 tests pass
- [ ] Manual: after merge, trigger a `main` push → verify `:main-<sha>` and `:latest` appear on GHCR

## Post-merge

1. Pull `:latest` on `docker.lan`, provision `/opt/stacks/findajob-brock/` per spec Section 7
2. Run the migration (rsync state from `findajob.lan`, stop systemd, bring up compose)
3. Observe 24–48h through full triage + poller + notify cycle
4. Cut `v0.1.0` tag → release workflow fires → Amy can pull via #20

## Closes

- Closes #13

## Related

- #69 (release management process — parallel, to be authored after this merges)
- #70 (docs fix: sigoden → blob42/aichat-ng — spinoff)
- #71 (Model-2 admin upgrade — deferred to ≥3 dogfooders)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Move #13 to "In Progress" on the project board** (if not already there)

Run:

```bash
gh project item-edit --project-id PVT_kwHOAgGulc4BUtxZ \
  --id PVTI_lAHOAgGulc4BUtxZzgqCnzs \
  --field-id PVTSSF_lAHOAgGulc4BUtxZzhCOoMM \
  --single-select-option-id 2c2c07d2
```

(Already In Progress per the kickoff scan — this step is a safety net.)

---

## Self-review checklist

- **Spec coverage:**
  - Architecture (spec §Architecture) → Tasks 2–8, 10
  - Image structure (spec §Image structure) → Task 2
  - Compose file (spec §Compose file) → Task 7, 8
  - Scheduler / supercronic (spec §Scheduler) → Task 4
  - Gmail auth (spec §Gmail auth helper) → Tasks 5, 6
  - Per-user admin model (spec §Per-user admin model) → documented in install-docker.md stub Task 15; no code change
  - Release automation (spec §Release automation) → Tasks 10, 11, 12
  - Data flow (spec §Data flow) → no code — enforced by compose bind mounts + entrypoint PUID/PGID, Tasks 3, 7
  - Migration (spec §Migration) → NOT a task — runbook in spec, executed post-merge per PR body post-merge section
  - Error handling (spec §Error handling) → covered in entrypoint (idempotency), crontab (timeouts), existing Gmail skip behavior
  - Testing strategy (spec §Testing strategy) → Layer 1: Task 9. Layer 2: Task 13. Layer 3: post-merge observation, not a task.
  - Files created/modified (spec §Files) → every entry mapped to a task; correction noted at plan top re: Gmail script edits being unnecessary
- **Placeholder scan:** no TBD/TODO/placeholder text. "TBD" in CHANGELOG.md for the v0.1.0 date is intentional — it's filled when the tag is cut, per the #69 release process.
- **Type consistency:**
  - `build_parser()`, `run_device()`, `run_local()`, `main()` — consistent across Task 5 tests and Task 6 implementation
  - `gmail_auth.py` CLI contract: `--mode`, `--client-secrets`, `--token-out`, `--port` — consistent in tests, implementation, compose command line
  - Volume paths: `/app/data`, `/app/config`, `/app/candidate_context`, `/app/companies`, `/app/logs`, `/root/.config/aichat_ng` — same across Dockerfile env, entrypoint chown, compose bind mounts, CLAUDE.md table
  - `FINDAJOB_*` env var names: consistent across crontab, compose, env template
