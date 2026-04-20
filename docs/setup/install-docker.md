# Docker Install

This is the install + operations guide for external users running findajob from the prebuilt `ghcr.io/brockamer/findajob` image via Docker Compose. Claude's release orchestration runbook lives separately at [`docs/release-process.md`](../release-process.md).

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
sudo mkdir -p /opt/stacks/findajob-<you>/state/{data,config,candidate_context,companies,logs,aichat_ng,rclone}
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

## Tag pinning strategy

`FINDAJOB_IMAGE_TAG` in your `.env` controls which image Docker Compose pulls. Pick based on how much change tolerance you want.

| Value | Mutability | Recommended for |
|---|---|---|
| `v0.1` | moving (auto-advances to latest `v0.1.x` patch) | **Default.** Most users. Auto-accepts bugfixes; breaking changes require an explicit `.env` edit. |
| `v0.1.0` | immutable | Pin exactly when you need a known-good version and can't afford surprises (e.g., during an active job-hunt push). |
| `latest` | moving (tip of `main`) | Dogfood track. The upstream maintainer runs this to exercise releases before tagging. May break. |
| `main-<sha>` | immutable (one tag per commit on `main`) | Precise pinning or bisecting when diagnosing a regression. |

Switching between tags is a one-line `.env` edit followed by `docker compose pull && docker compose up -d`.

## Updating

Before running `docker compose pull && docker compose up -d`:

1. Check the [latest GitHub Release](https://github.com/brockamer/findajob/releases/latest) for an "⚠️ Action required before upgrade" section at the top of the notes.
2. If the section is present, follow each linked PR's migration notes before pulling.
3. If the section is absent, a straight pull-and-up is safe:
   ```bash
   cd /opt/stacks/findajob-<you>/
   docker compose pull
   docker compose up -d
   ```
   Or click **Pull** + **Deploy** in Dockge.

The "Action required" section is driven by PRs labeled `migration-required` (see [`docs/release-process.md`](../release-process.md) for the criteria). If a release has no such PRs in its range, the section won't appear.

## Migrating from an older image: aichat-ng / rclone mount paths

If your stack was deployed before the aichat-ng mount-path fix, your `compose.yaml` still mounts `./state/aichat_ng` to `/root/.config/aichat_ng`. The container now runs as a non-root user (PUID), so `/root` is unreadable and all scoring calls fail silently. You also need a new `rclone` mount and a `HOME=/app` env var.

Apply these changes once, per instance:

1. **Stop the stack.**
   ```bash
   cd /opt/stacks/findajob-<you>/
   docker compose down
   ```

2. **Create the new `state/rclone/` bind-mount directory.**
   ```bash
   mkdir -p state/rclone
   sudo chown $(id -u):$(id -g) state/rclone
   ```

3. **Edit `compose.yaml`** (or re-pull `ops/compose.yaml.example` if you haven't customized it). Three changes to the `scheduler` service:

   - Under `environment:`, add `HOME: /app`.
   - Change the aichat-ng volume from `./state/aichat_ng:/root/.config/aichat_ng` to `./state/aichat_ng:/app/.config/aichat_ng`.
   - Add a new volume: `./state/rclone:/app/.config/rclone`.

   Apply the same `HOME: /app` change to the `gmail-auth` service.

4. **Fix ownership of `state/aichat_ng/`** in case it was populated under the old path:
   ```bash
   sudo chown -R $(id -u):$(id -g) state/aichat_ng
   ```

5. **Pull and bring the stack back up.**
   ```bash
   docker compose pull
   docker compose up -d
   docker compose logs -f scheduler  # Ctrl-C once you see supercronic's schedule dump
   ```

6. **(If using jobsync)** Re-run `docker compose exec scheduler rclone config` so the rclone remote lands in `/app/.config/rclone/rclone.conf` (the new persistent location).

Verify with a scoring smoke test:
```bash
docker compose exec scheduler aichat-ng -m claude:claude-sonnet-4-6 -- 'reply "ok"'
```
Expected output: `ok`. If aichat-ng errors with "no such file or directory" or returns nothing, the config is still in the old location — re-check the mount path.

## Rolling back locally

If a pull broke your stack and you need to get back to a working state immediately:

1. Edit `.env` to pin to a prior immutable tag, e.g.,
   ```
   FINDAJOB_IMAGE_TAG=v0.1.0
   ```
2. Re-deploy:
   ```bash
   docker compose pull
   docker compose up -d
   ```
3. Report the regression via a GitHub issue so the shared `:v0.1` alias can be rolled back globally (the upstream maintainer's call — see [release-process.md Rollback section](../release-process.md#rollback)).

A local rollback via `.env` pin doesn't affect other users on `:v0.1`.

## Troubleshooting

- Container fails to start: `docker compose logs scheduler` usually points at the issue.
- Supercronic prints "schedule invalid": a crontab syntax error. Check `ops/crontab` for recent changes.
- Gmail ingestion silently disabled: re-run `docker compose --profile setup run --rm gmail-auth` to refresh the token.
- For anything else, open an issue at https://github.com/brockamer/findajob/issues.
