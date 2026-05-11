# Deploying findajob to Fly.io

A cloud alternative to running the compose stack on a host you own. One Fly app per tenant. The image runs unchanged — Fly executes the same `ENTRYPOINT` as Docker Compose, supercronic stays PID 1, uvicorn is a child process, SQLite lives on a Fly Volume (POSIX block device, not network filesystem).

## What this gives you

- One Fly app per tenant — strict per-tenant isolation matches the per-stack key isolation invariant (#339).
- Always-on `findajob-<handle>.fly.dev` URL, HTTPS terminated by Fly.
- HTTP Basic Auth via the same `FINDAJOB_AUTH_USER` / `FINDAJOB_AUTH_PASS` env vars as the compose stack.
- Six Fly Volumes mirroring the host bind-mount layout (`data`, `logs`, `companies`, `config`, `candidate_context`, `.backups`).
- Image upgrades are one-line edits to `ops/fly.toml` + `fly deploy`.
- Roughly $3–5/month per tenant on the default `shared-cpu-1x` 1GB machine + 8GB of volumes (see [Cost guide](#cost-guide) below).

## Threat model

Same as compose: shared-secret HTTP Basic Auth is *not* identity. It defends against drive-by scanning and casual probing. It does not defend against a determined attacker who learns the credential. See [`internet-exposure.md`](internet-exposure.md) for the full discussion — this runbook adopts that model verbatim. Fly adds TLS termination upstream of the gate; the gate itself is unchanged.

## Prerequisites

- [flyctl](https://fly.io/docs/flyctl/install/) installed and on `PATH`.
- `fly auth login` completed against your Fly account.
- This repo checked out locally (you'll edit `ops/fly.toml`).
- Credentials in hand before you start: OpenRouter API key, RapidAPI key, ntfy topic, and a basic-auth username + password you'll generate (≥24 chars; `openssl rand -base64 32`).

## First deploy

Three commands from the repo root:

    cp ops/fly.toml.example ops/fly.toml
    $EDITOR ops/fly.toml                    # set `app = "findajob-<handle>"`
    bash ops/fly-deploy.sh

The script is idempotent: re-runs detect existing apps, volumes, and secrets and skip them. On a clean run it:

1. Creates the Fly app if it doesn't exist.
2. Creates the six volumes if they don't exist (`findajob_data`, `findajob_logs`, `findajob_companies`, `findajob_config`, `findajob_context`, `findajob_backups`).
3. Prompts only for secrets not already set (`OPENROUTER_API_KEY`, `RAPIDAPI_KEY`, `NTFY_TOPIC`, `FINDAJOB_AUTH_USER`, `FINDAJOB_AUTH_PASS`, `FINDAJOB_WEB_URL` — defaults to `https://<app>.fly.dev`).
4. Runs `fly deploy --config ops/fly.toml`.
5. Verifies the auth gate by running `python -m findajob.web.verify_auth` inside the running machine via `fly ssh console --command`. Non-zero exit means the deploy is up but unverified — the script prints `fly logs / status / ssh console` debug commands and exits.

Verify in a browser: `https://findajob-<handle>.fly.dev/` should prompt for basic auth. After login the dashboard renders.

## Image upgrades

Edit the pinned tag in `ops/fly.toml`:

    [build]
      image = "ghcr.io/brockamer/findajob:vNEW.TAG"

then redeploy and re-verify:

    fly deploy --config ops/fly.toml
    fly ssh console --app findajob-<handle> --command "python -m findajob.web.verify_auth"

The same hard rule from CLAUDE.md applies: a non-zero `verify_auth` exit means the stack is unverified. Take it down with `fly machines stop` (or `fly apps destroy` if abandoning) until fixed.

## Secret rotation

Stage a new value, then deploy to apply it:

    fly secrets set --stage --app findajob-<handle> FINDAJOB_AUTH_PASS=<new-value>
    fly deploy --config ops/fly.toml
    fly ssh console --app findajob-<handle> --command "python -m findajob.web.verify_auth"

Notify the tester out-of-band. Same caveat as compose: anyone holding the credential can edit pipeline config at `/config/`.

## Inspecting and operating

Translation from the compose forms used elsewhere in this directory:

| docker compose                                                   | fly                                                                                  |
|------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| `docker compose logs -f scheduler`                               | `fly logs --app findajob-<handle>`                                                   |
| `docker compose exec scheduler bash`                             | `fly ssh console --app findajob-<handle>`                                            |
| `docker compose exec scheduler python3 scripts/triage.py`        | `fly ssh console --app findajob-<handle> --command "python3 scripts/triage.py"`      |
| `docker compose exec scheduler sqlite3 data/pipeline.db`         | `fly ssh console --app findajob-<handle> --command "sqlite3 /app/data/pipeline.db"`  |
| `docker compose ps`                                              | `fly status --app findajob-<handle>`                                                 |
| `docker compose restart scheduler`                               | `fly machines restart <machine-id> --app findajob-<handle>`                          |
| `docker compose pull && docker compose up -d`                    | edit image tag in `ops/fly.toml`, then `fly deploy --config ops/fly.toml`            |
| `docker compose down`                                            | `fly machines stop <machine-id> --app findajob-<handle>`                             |

Get the machine ID with `fly machines list --app findajob-<handle>`.

## Resizing volumes

Volumes start at 1 GB (3 GB for `findajob_companies`). Extend a single volume:

    fly volumes list --app findajob-<handle>
    fly volumes extend <volume-id> --size 5      # new size in GB, must be larger

The volume stays attached; no downtime. Shrinking is not supported — Fly's path for that is "snapshot, destroy, recreate smaller, restore."

## Cost guide

Rough monthly cost per tenant on the defaults in `fly.toml.example`:

| Item                                  | Rate (approx.)         | Default sizing            | Monthly |
|---------------------------------------|------------------------|---------------------------|---------|
| `shared-cpu-1x` 1 GB machine, always-on | ~$3.19/mo at full month | 1 machine                 | ~$3.19  |
| Volumes                               | $0.15/GB-month         | 8 GB total                | ~$1.20  |
| Bandwidth                             | Free tier covers low-egress traffic | Operator + tester only | ~$0 |
| **Total**                             |                        |                           | **~$3–5** |

Fly's current pricing is at <https://fly.io/docs/about/pricing/> — verify before forecasting more than a handful of tenants. **Volume snapshots are billed separately starting January 2026**; if you take snapshots (see [Backup](#backup) below), check the pricing page for the current rate.

## Backup

SQLite + role artifacts under `companies/` are the data layer. Snapshot the volumes that matter:

    fly volumes snapshots create <volume-id>           # one snapshot per volume
    fly volumes snapshots list   <volume-id>

Snapshots are durable, off-machine, and restorable to a new volume with `fly volumes create --snapshot-id <snap>`. For a full tenant backup, snapshot all six volumes; in practice `findajob_data`, `findajob_companies`, and `findajob_config` are the ones whose loss can't be replayed from a fresh interview + `docker compose pull`. See [`../maintainers/data-ownership.md`](../maintainers/data-ownership.md) for the per-path classification.

## Rollback

If a deploy goes bad:

    fly releases --app findajob-<handle>                # find the prior version
    # Edit ops/fly.toml — set image to the previous good tag
    fly deploy --config ops/fly.toml
    fly ssh console --app findajob-<handle> --command "python -m findajob.web.verify_auth"

Fly's release history keeps the prior image references; the rollback is a re-deploy of the prior tag, not a separate primitive. Schema-breaking releases can't be rolled back this way — check the release's CHANGELOG `### Migration required` block before rolling forward.

## Tearing down a tenant

    fly apps destroy findajob-<handle>

This is **irreversible**. It destroys the app, the machine, and all six volumes (and their snapshots). Take a final snapshot first if you want a recovery option. There is no "soft delete."

## Not in scope

The following are deliberately out of scope for this initial Fly target and are tracked separately:

- **Multi-region.** A single machine in one region is fine for a per-tenant tool. If a tenant ever needs multi-region read replicas, [LiteFS](https://fly.io/docs/litefs/) is the supported path — non-trivial schema-replication work, not a config flip.
- **Tenant-name-as-argument wrapper.** `fly-deploy.sh` currently runs against the single `ops/fly.toml` in the working tree. A future revision can take `<handle>` as an argument and template the file. Deliberate follow-up after the first deploy lands.
- **Custom domains.** Fly issues TLS certs for `<app>.fly.dev` automatically. Custom domains (`findajob-<handle>.<your-domain>`) work via `fly certs create` but aren't wired into the deploy script — add manually if needed.
