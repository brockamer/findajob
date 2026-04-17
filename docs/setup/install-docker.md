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
