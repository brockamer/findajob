# Deploying findajob to Fly.io

> **First-time deployer?** See [`../getting-started/install-fly.md`](../getting-started/install-fly.md) — the average-user runbook. This page is the operator-tier reference: deeper command surface, no hand-holding, assumes you've already deployed once.

A cloud alternative to running the compose stack on a host you own. One Fly app per tenant. The image runs unchanged — Fly executes the same `ENTRYPOINT` as Docker Compose, supercronic stays PID 1, uvicorn is a child process, SQLite lives on a Fly Volume (POSIX block device, not network filesystem).

## What this gives you

- One Fly app per tenant — strict per-tenant isolation matches the per-stack key isolation invariant (#339).
- Always-on `findajob-<handle>.fly.dev` URL, HTTPS terminated by Fly.
- HTTP Basic Auth via the same `FINDAJOB_AUTH_USER` / `FINDAJOB_AUTH_PASS` env vars as the compose stack.
- One Fly Volume mounted at `/app/state/` holds all six state subdirs (`data/`, `logs/`, `companies/`, `config/`, `candidate_context/`, `.backups/`). The image's entrypoint materializes the subdirs on first boot; `JSP_BASE=/app/state` routes all pipeline I/O underneath. Fly Machines support exactly one volume per machine, so the compose-stack six-bind-mount layout is folded into a single mount here.
- Image upgrades are one-line edits to `ops/fly.toml` + `fly deploy`.
- Roughly $3–5/month per tenant on the default `shared-cpu-1x` 1GB machine + 8GB of volume (see [Cost guide](#cost-guide) below).

## Threat model

Same as compose: shared-secret HTTP Basic Auth is *not* identity. It defends against drive-by scanning and casual probing. It does not defend against a determined attacker who learns the credential. See [`internet-exposure.md`](internet-exposure.md) for the full discussion — this runbook adopts that model verbatim. Fly adds TLS termination upstream of the gate; the gate itself is unchanged.

## Prerequisites

- [flyctl](https://fly.io/docs/flyctl/install/) installed and on `PATH`.
- `fly auth login` completed against your Fly account.
- **Billing enabled on your Fly organization.** Trial orgs reject `fly deploy` with HTTP 422 "This functionality is disabled for trial organizations" until a credit card is on file. Add one at `https://fly.io/dashboard/<your-org-slug>/billing` before running the deploy script.
- This repo checked out locally (you'll edit `ops/fly.toml`).
- Credentials in hand before you start: OpenRouter API key, RapidAPI key, ntfy topic. The basic-auth username + password are now optional at deploy time (#895) — they can be set during the in-app onboarding flow instead, with a one-time setup token from `fly logs` gating the form against drive-by squat. To pre-set them via Fly secrets, generate a strong password (`openssl rand -base64 32`) and have it ready.

## First deploy

Three commands from the repo root:

    cp ops/fly.toml.example ops/fly.toml
    $EDITOR ops/fly.toml                    # set `app = "findajob-<handle>"`
    bash ops/fly-deploy.sh

The script is idempotent: re-runs detect existing apps, volumes, and secrets and skip them. On a clean run it:

1. Creates the Fly app if it doesn't exist.
2. Creates the single `findajob_state` volume (8 GB default) if it doesn't exist.
3. Prompts only for secrets not already set (`OPENROUTER_API_KEY`, `RAPIDAPI_KEY`, `NTFY_TOPIC`, `FINDAJOB_AUTH_USER`, `FINDAJOB_AUTH_PASS`, `FINDAJOB_WEB_URL` — defaults to `https://<app>.fly.dev`). Auth credentials can be skipped here and set during onboarding instead (#895).
4. Runs `fly deploy --config ops/fly.toml`. On first boot inside the machine, `ops/entrypoint.sh` materializes the six state subdirs under `/app/state/` and `init_db.py` creates `pipeline.db`.
5. Verifies the auth gate by running `python -m findajob.web.verify_auth` inside the running machine via `fly ssh console --command`. Non-zero exit means the deploy is up but unverified — the script prints `fly logs / status / ssh console` debug commands and exits. If auth credentials were skipped, the verifier exits 2 — set them during onboarding to make the gate active and re-run the verifier afterward.

Verify in a browser: `https://findajob-<handle>.fly.dev/` should prompt for basic auth (if pre-set) or land on the onboarding page with a "Set your login password" step that requires the `FINDAJOB_SETUP_TOKEN` from `fly logs`. After login or password setup, the dashboard renders.

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

Notify the user out-of-band. Same caveat as compose: anyone holding the credential can edit pipeline config at `/config/`.

## Inspecting and operating

Translation from the compose forms used elsewhere in this directory:

| docker compose                                                   | fly                                                                                  |
|------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| `docker compose logs -f scheduler`                               | `fly logs --app findajob-<handle>`                                                   |
| `docker compose exec scheduler bash`                             | `fly ssh console --app findajob-<handle>`                                            |
| `docker compose exec scheduler python3 scripts/triage.py`        | `fly ssh console --app findajob-<handle> --command "python3 scripts/triage.py"`      |
| `docker compose exec scheduler sqlite3 data/pipeline.db`         | `fly ssh console --app findajob-<handle> --command "sqlite3 /app/state/data/pipeline.db"`  |
| `docker compose ps`                                              | `fly status --app findajob-<handle>`                                                 |
| `docker compose restart scheduler`                               | `fly machines restart <machine-id> --app findajob-<handle>`                          |
| `docker compose pull && docker compose up -d`                    | edit image tag in `ops/fly.toml`, then `fly deploy --config ops/fly.toml`            |
| `docker compose down`                                            | `fly machines stop <machine-id> --app findajob-<handle>`                             |

Get the machine ID with `fly machines list --app findajob-<handle>`.

## Resizing the volume

The `findajob_state` volume starts at 8 GB. Extend it in place:

    fly volumes list --app findajob-<handle>
    fly volumes extend <volume-id> --size 16     # new size in GB, must be larger

The volume stays attached; no downtime. Shrinking is not supported — Fly's path for that is "snapshot, destroy, recreate smaller, restore." Companies/prep artifacts (`companies/_inbox/`, `companies/_applied/`, etc.) are the directory most likely to grow over time; if you're approaching the cap, this is the knob.

## Cost guide

Rough monthly cost per tenant on the defaults in `fly.toml.example`:

| Item                                  | Rate (approx.)         | Default sizing            | Monthly |
|---------------------------------------|------------------------|---------------------------|---------|
| `shared-cpu-1x` 1 GB machine, always-on | ~$3.19/mo at full month | 1 machine                 | ~$3.19  |
| Volume                                | $0.15/GB-month         | 8 GB                      | ~$1.20  |
| Bandwidth                             | Free tier covers low-egress traffic | Single-user traffic    | ~$0 |
| **Total**                             |                        |                           | **~$3–5** |

Fly's current pricing is at <https://fly.io/docs/about/pricing/> — verify before forecasting more than a handful of tenants. **Volume snapshots are billed separately starting January 2026**; if you take snapshots (see [Backup](#backup) below), check the pricing page for the current rate.

## Backup

SQLite + role artifacts under `companies/` are the data layer. Because all state lives on a single volume, one snapshot captures the full tenant:

    fly volumes list --app findajob-<handle>
    fly volumes snapshots create <volume-id>
    fly volumes snapshots list   <volume-id>

Snapshots are durable, off-machine, and restorable to a new volume with `fly volumes create --snapshot-id <snap>`. See [`CLAUDE.md` § Data Ownership and Backup Classification](../../CLAUDE.md#data-ownership-and-backup-classification) for the per-path classification of what's rebuildable vs. backup-critical inside that single volume.

## Rollback

If a deploy goes bad:

    fly releases --app findajob-<handle>                # find the prior version
    # Edit ops/fly.toml — set image to the previous good tag
    fly deploy --config ops/fly.toml
    fly ssh console --app findajob-<handle> --command "python -m findajob.web.verify_auth"

Fly's release history keeps the prior image references; the rollback is a re-deploy of the prior tag, not a separate primitive. Schema-breaking releases can't be rolled back this way — check the release's CHANGELOG `### Migration required` block before rolling forward.

## Tearing down a tenant

    fly apps destroy findajob-<handle>

This is **irreversible**. It destroys the app, the machine, and the `findajob_state` volume (and its snapshots). Take a final snapshot first if you want a recovery option. There is no "soft delete."

## Not in scope

The following are deliberately out of scope for this initial Fly target and are tracked separately:

- **Multi-region.** A single machine in one region is fine for a per-tenant tool. If a tenant ever needs multi-region read replicas, [LiteFS](https://fly.io/docs/litefs/) is the supported path — non-trivial schema-replication work, not a config flip.
- **Tenant-name-as-argument wrapper.** `fly-deploy.sh` currently runs against the single `ops/fly.toml` in the working tree. A future revision can take `<handle>` as an argument and template the file. Deliberate follow-up after the first deploy lands.
- **Custom domains.** Fly issues TLS certs for `<app>.fly.dev` automatically. Custom domains (`findajob-<handle>.<your-domain>`) work via `fly certs create` but aren't wired into the deploy script — add manually if needed.
