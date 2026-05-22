# Docker Install

> **New here?** Start at [`README.md`](README.md) — it sequences prerequisites → install → configure in order.

This is the install + operations guide for external users running findajob from the prebuilt `ghcr.io/brockamer/findajob` image via Docker Compose. Claude's release orchestration runbook lives separately at [`docs/maintainers/release-process.md`](../maintainers/release-process.md).

## Who this is for

- You have a Docker host reachable on a LAN or VPN.
- You have [Dockge](https://github.com/louislam/dockge) installed (or docker compose CLI access).
- You want to run findajob from the prebuilt image at `ghcr.io/brockamer/findajob` rather than building from source.

## Supported platforms

findajob is supported on **Linux Docker hosts** (any distro running native Docker Engine on a native filesystem — ext4, xfs, btrfs, zfs, overlay, tmpfs). The pipeline is a long-running batch process that writes to SQLite in WAL mode; this requires the bind-mount layer to honor POSIX file-locking semantics correctly.

**Docker Desktop on macOS and Windows is not supported.** Both ship bind mounts through gRPC-FUSE / VirtioFS / 9p, which have known incompatibilities with SQLite WAL mode and can corrupt `pipeline.db` mid-write (#625). The container's entrypoint emits a stderr warning at startup when it detects a non-native filesystem under `state/data/`, but it will still try to run — corruption is observed in practice but does not always reproduce immediately.

If you want to run findajob on a Mac or Windows machine, host it inside a **Linux VM** (any hypervisor) and install Docker Engine inside the VM. The bind mount then sits on the VM's native ext4 (or equivalent) and works correctly.

## Prerequisites on the Docker host

- Docker Engine 24+ and Docker Compose v2
- (Optional) A Gmail account, if you want to ingest LinkedIn and other job-alert emails. See [gmail.md](gmail.md) for the post-deploy walkthrough.

## Prerequisites for your Claude Code helper (for the admin)

See [configure.md](configure.md). API keys and personal config end up in `state/data/.env` (mode 0600).

## 1. Create the stack directory

Pick any directory for your stack — Docker Compose's bind mounts are relative to wherever `compose.yaml` lives, so the location is your choice:

- Linux server: `/opt/stacks/findajob-<you>/` is the conventional system-path layout (will need `sudo`).
- Anywhere under your home directory works too — pick what fits your other Docker stacks.

> If you're on macOS or Windows, your Docker host needs to be a Linux VM, not Docker Desktop — see [Supported platforms](#supported-platforms) above. Then choose a stack directory *inside the VM*.

```bash
# Replace <stack-dir> with your chosen path, e.g. /opt/stacks/findajob-<you> or ~/docker/findajob-<you>
mkdir -p <stack-dir>/state/{data,config,candidate_context,companies,logs,.backups}
```

If you placed the stack in a system path like `/opt/stacks/`, prefix the `mkdir` with `sudo` and follow up with `sudo chown -R $(id -u):$(id -g) <stack-dir>/` so your user (rather than root) owns the bind-mount targets. Skip both for paths under your home directory.

Replace `<you>` with a short user tag.

## 2. Drop in the compose template and env

```bash
cd <stack-dir>/
curl -fsSL -o compose.yaml https://raw.githubusercontent.com/brockamer/findajob/main/ops/compose.yaml.example
curl -fsSL -o .env https://raw.githubusercontent.com/brockamer/findajob/main/ops/stack.env.example
```

Edit `.env` to taste — at minimum set `FINDAJOB_TZ` and `FINDAJOB_MATERIALS_PORT`. `FINDAJOB_IMAGE_TAG` defaults to `latest`, which is what every stack runs.

## 3. Populate `state/`

There are two `.env` files in a findajob deployment with different roles
— don't conflate them:

- **`./.env`** (next to `compose.yaml`, populated in step 2 above) —
  read by Docker Compose itself for `${VAR}` interpolation in
  `compose.yaml`. Holds image tag, port, timezone, basic-auth credentials.
- **`./state/data/.env`** — bind-mounted into the container as the
  runtime `env_file`. Holds API keys (`OPENROUTER_API_KEY`,
  `RAPIDAPI_KEY`), `NTFY_TOPIC`, and similar runtime secrets.

**`state/data/.env` MUST exist (even with placeholder values) before
`docker compose up -d`.** Docker Compose's `env_file:` directive errors
out on missing files and the container refuses to start. The first-run
onboarding interview overwrites the placeholder API keys with your real
values via the web UI; you don't hand-edit this file unless you're
rotating keys later (see "Rotating an API key" further down).

```bash
# Seed state/data/.env from the documented template:
curl -fsSL -o state/data/.env https://raw.githubusercontent.com/brockamer/findajob/main/data/.env.example
chmod 600 state/data/.env
```

Other files under `state/`:

- `state/config/*.yaml|.txt|.json` — personal config files. See [configure.md](configure.md) for each file's purpose.
- `state/candidate_context/profile.md` + `master_resume.md` — your candidate profile. See [`candidate_context/profile.md.example`](https://github.com/brockamer/findajob/blob/main/candidate_context/profile.md.example).

> **First-time deployers can stop here.** The remaining `state/` files
> (`profile.md`, `master_resume.md`, `target_companies.md`,
> `prefilter_rules.yaml`, etc.) are produced by the first-run onboarding
> interview in step 7 below — you don't write them by hand.

### HTTP Basic Auth (required for internet-exposed instances)

If your stack is reachable from the public internet (any non-VPN deployment),
add these to `state/data/.env` to gate the entire web UI behind HTTP Basic
Auth (#327):

```
FINDAJOB_AUTH_USER=<your username>
FINDAJOB_AUTH_PASS=<a strong password>
```

the perimeter VPN-only / LAN-only instances can skip this — the perimeter is the gate.
See [`../operations/internet-exposure.md`](../operations/internet-exposure.md) for the full threat model.

### What the entrypoint does automatically

The container image's entrypoint handles these on every start — you do not
run any of these commands manually:

- Creates `state/data/pipeline.db` with the full schema if it's missing.
  Idempotent: no-op on populated DBs (#116, #117).
- Seeds tracked config files (`roles/`, `scoring_schema.json`,
  `model_pricing.yaml`, `reference.docx`, `strip-bookmarks.lua`) into
  `state/config/` on every start — these are always overwritten so image
  updates propagate on `docker compose up`. Your personal config files
  (`jsearch_queries.txt`, `feed_urls.txt`, etc.) are left alone because
  they don't exist in the bundled set.

Fill in your personal config files above and run `docker compose up -d` —
no manual schema init.

### Materials viewer port

Set `FINDAJOB_MATERIALS_PORT` in your stack `.env` to a free host port (default `8090`).
Each stack on the same host must use a unique port number.

```
FINDAJOB_MATERIALS_PORT=8090
```

The container publishes the viewer at `http://<docker-host>:<port>/`. On a LAN or the perimeter VPN
VPN this is reachable from any device. The viewer is read-only — it displays prep-folder
contents grouped by stage (staged, applied, waitlisted, rejected), renders Markdown inline,
and offers `.docx` files for download.

The viewer has a top nav linking all feature groups. `/` is a pipeline-at-a-glance landing
page with stage counts; `/materials/` is the prep-folder index (previously served at `/`).

```bash
# Quick smoke test after first deploy
curl http://<deployment-host>:8090/healthz    # expect: ok
```

The viewer also serves six board pages under `/board/`: Dashboard, Applied,
Review, Waitlist, Rejected, Archive. The Archive page covers every job in
the DB (10k+) with infinite-scroll pagination, per-column sort, and a
live text filter.

## 4. Configure Gmail integration (optional)

If you want findajob to ingest LinkedIn (and other) job-alert emails from your Gmail, follow [`gmail.md`](gmail.md) after the stack is up. The pipeline runs cleanly without Gmail integration — Greenhouse / Ashby / Lever direct fetches and RapidAPI LinkedIn search cover most ingestion volume.

## 5. Deploy

Via Dockge: click **Deploy**. Via CLI: `docker compose up -d`.

## 6. Verify the stack is reachable

```bash
docker compose logs -f scheduler
# You should see supercronic print its crontab and wait.

curl http://<docker-host>:<FINDAJOB_MATERIALS_PORT>/healthz
# Expected: ok
```

If `/healthz` returns `ok`, the container is up. The pipeline isn't
producing notifications yet — that needs step 7.

## 7. First-run onboarding

The first time you open the web UI — even at the bare URL — you'll be
redirected to `/onboarding/`. This page now uses a two-step layout:

**Step 1 — API keys.** Provide your own keys before either interview
path enables. You'll need:

| Key | Required? | What it funds | Free tier? |
|---|---|---|---|
| **OpenRouter** | Yes | All pipeline LLM calls + the in-app interview itself | Pay-as-you-go from $0; ~$1–2 per fully-prepped job |
| **RapidAPI feed** (jobs-api14 or JSearch — onboarding picker chooses) | Optional | LinkedIn + Indeed search ingestion | 150–200 requests/month BASIC (no credit card) |

Skipping RapidAPI means LinkedIn + Indeed search is inactive, but
Greenhouse / Ashby / Lever feeds and Gmail alert ingestion still work.
Full sign-up walk-throughs:
[`docs/getting-started/api-keys.md`](api-keys.md) — also reachable in-app at
`/docs/getting-started/api-keys`.

**Step 2 — Run the interview.** Click "Start interview." findajob opens a
chat surface where you have a structured 60–90 minute conversation with
Claude Sonnet 4.6, billed against your own OpenRouter key. The session
is server-side persistent — close the tab any time and the index page
surfaces a "Resume your interview" affordance. When the LLM finishes
emitting your config blocks (it does this in the chat itself; the
parser extracts them automatically), a green Finalize button appears
below the chat. Click it.

Cost runs ~$3-6 per onboarding even with prompt caching enabled. The
system prompt is cached at OpenRouter so subsequent turns are billed
at ~10% of the system tokens, but voice-samples emission and the
cumulative chat history dominate the bill in long interviews.

The injector validates the emission, runs a 1-token smoke check against
OpenRouter to re-verify the key, atomically writes the config files
(`findajob.config_loader` reads Tier 1 directly from `target_companies.md`
at runtime — no derived file post-#211), and runs initial company
discovery. Errors are surfaced verbatim — fix and resubmit.

**Step 3 — Gmail configuration (optional).** After Finalize, you're routed
to `/onboarding/gmail-config/{session_id}/` to wire up IMAP + a Google
app-password if you want findajob to ingest LinkedIn (and other ATS)
job-alert emails. Skippable — you can configure later at `/config/gmail/`.

**Step 4 — LinkedIn Connections.csv upload (optional, #571).** The Gmail
gate routes to `/onboarding/connections/{session_id}/` where you upload
your LinkedIn connections export so the outreach drafter can name real
contacts at target companies. Skippable; can be uploaded later at
`/onboarding/connections/`. The connections gate writes
`data/.onboarding-complete` (the sentinel that lets `onboarding_guard`
stop redirecting), then lands you on the dashboard.

After the connections gate, the next scheduled triage run (00:00 in your
configured `TZ`) ingests its first batch of jobs.

## 8. Send a test notification

```bash
docker compose exec scheduler python3 /app/scripts/notify.py health-check
# Sanity check: ntfy notification should land on your phone.
```

This requires `NTFY_TOPIC` in `state/data/.env`, which the onboarding
injector populates from your interview emission. Skip this step if you
ran it before step 7 — it would silently no-op.

## Driving the pipeline

Once the scheduler is running, your daily workflow happens at `/board/*`
in the web UI. Open
`http://<host>:<FINDAJOB_MATERIALS_PORT>/board/dashboard` in a browser.
The Dashboard tab lists high-scoring jobs; click **Flag for Prep** on the
ones you want materials for. When prep completes, switch the status to
**Applied** to move the job to the Applied tab, then track it through
**Interviewing / Offer / Withdrew / Not Selected**. Review and Waitlist
tabs handle triage and deferred jobs respectively. Every click writes to
the DB in the same request — no polling delay.

`scripts/watchdog.py` runs every 10 min and resets any job stuck in
`prep_in_progress` for more than 60 min back to `scored` so you can re-flag it.

## Tag pinning strategy

`FINDAJOB_IMAGE_TAG` in your `.env` controls which image Docker Compose pulls. The default is `latest`, which is what every stack runs.

| Value | Mutability | Use when |
|---|---|---|
| `latest` | moving (advances on every `main` push) | **Default.** Every stack tracks this; releases roll to all stacks together. |
| `vX.Y.Z` (immutable) | immutable | You specifically want to freeze a stack on a known image (e.g., during an active job-hunt push where you can't afford surprises). |
| `main-<sha>` | immutable (one per `main` commit) | Precise pinning or bisecting when diagnosing a regression. |

Switching between tags is a one-line `.env` edit followed by `docker compose pull && docker compose up -d`.

## Multi-tenant hosts: staggering scheduled jobs

If you run multiple findajob stacks on the same Docker host (one per tester / family member / friend), the daily `triage` cron in every stack defaults to `00:00` in the container's TZ. Same-TZ stacks fire `triage.py` at the *exact same instant*, simultaneously hitting RapidAPI / Gmail / OpenRouter — risking quota exhaustion and host CPU spikes.

The image reads its supercronic schedule from `ops/scheduled-jobs.yaml` (baked into the image). Per-job env-var overrides in your stack's `.env` let you stagger schedules without forking the YAML:

| Override | Effect |
|---|---|
| `FINDAJOB_<JOB>_SCHEDULE="<cron>"` | Replace the schedule for one job (e.g., shift triage to 00:30 PT) |
| `FINDAJOB_<JOB>_ENABLED="false"` | Disable a single job for this stack |

`<JOB>` is the upper-cased YAML key with `-` → `_`. So `triage` reads `FINDAJOB_TRIAGE_SCHEDULE` / `FINDAJOB_TRIAGE_ENABLED`; `notify-apply` reads `FINDAJOB_NOTIFY_APPLY_SCHEDULE` / `FINDAJOB_NOTIFY_APPLY_ENABLED`.

Example for two same-TZ stacks sharing a host (LA-TZ):

```env
# stack-a/.env — keeps default
# (no override needed)

# stack-b/.env — shifts triage by 30 min
FINDAJOB_TRIAGE_SCHEDULE=30 0 * * *
```

Do the same for any other job that does heavyweight network or LLM work (`discover` is the other big one). Lightweight jobs (`watchdog`, `notify-*`) don't need staggering — they're DB reads + ntfy pushes.

The full job list lives at `ops/scheduled-jobs.yaml` in the repo. To inspect what your running container actually scheduled: `docker exec <container> cat /app/crontab` (the rendered output).

### Operator mode (multi-tenant stack health dashboard) — #333

If you run multiple findajob stacks side-by-side (e.g. yourself + several
beta testers on the same `<deployment-host>`), the operator stack can run with
operator mode enabled to surface a cross-stack health dashboard at
`/admin/stacks/`. The dashboard shows last-triage time, stage distribution,
stuck-prep count, and last-failure timestamp for every stack at
`/opt/stacks/findajob-*/`.

Operator mode is operator-only — testers' stacks must NOT enable it. It is
gated by a single env flag and a read-only mount.

**On operator's stack only**, edit `compose.yaml`:

```yaml
services:
  scheduler:
    environment:
      FINDAJOB_OPERATOR_MODE: "1"
      # Optional: float operator's own row to the top of the dashboard.
      # Value must match the operator's stack handle (the trailing component
      # of /opt/stacks/findajob-{handle}). When unset, rows render in pure
      # alphabetical order. The handle is read from the env so tracked code
      # stays free of operator-specific identifiers.
      FINDAJOB_OPERATOR_HANDLE: "${YOUR_HANDLE}"
    volumes:
      - /opt/stacks:/opt/stacks:ro
```

Apply with `docker compose up -d`. The route is loaded conditionally — when
the flag is unset, `/admin/stacks/` returns 404 and no cross-stack mount is
required.

**Visual cue:** when operator mode is enabled, the top nav bar renders red
on every page (not just `/admin/stacks/`). This is intentional — it keeps
you aware that you're in the operator surface.

**Auth:** the dashboard inherits `FINDAJOB_AUTH_USER` / `FINDAJOB_AUTH_PASS`
Basic Auth (the same credentials that protect `/board/`). No new credential
to manage.

**Read-only invariant:** the dashboard cannot modify any tester state. All
SQLite reads use `mode=ro` URI; `/opt/stacks` is mounted read-only.

## Operating an existing stack

### Rotating an API key

To replace the OpenRouter or RapidAPI feed key (`RAPIDAPI_KEY` — canonical; or legacy per-adapter vars `JOBS_API14_KEY` / `JSEARCH_API_KEY`, #414) on an already-onboarded stack, you have two options:

**Option A — Web UI (recommended):**

1. Visit `/onboarding/?mode=rerun` in a browser.
2. Use Step 1's "Change keys" affordance to clear and re-enter the values.
3. Click **Save keys**. The new values smoke-check against the provider
   and overwrite the stack's `data/.env` atomically.

The injector backs up the existing `data/.env` to `.backups/{UTC-stamp}/`
before overwriting.

**Option B — SSH (operator-side):**

Useful when the tester is unavailable and you need to rotate on their
behalf, or when the web UI is unreachable. Edit `data/.env` server-side
and bounce the stack:

```bash
ssh <deployment-host>
cd /opt/stacks/findajob-<handle>/
sudo sed -i 's|^OPENROUTER_API_KEY=.*|OPENROUTER_API_KEY=sk-or-v1-NEW...|' state/data/.env
sudo docker compose up -d --force-recreate
```

Per the project memory `feedback_never_print_secrets`, never `cat` or
echo the `.env` to your terminal — copy + edit server-side only.
Repeat for `RAPIDAPI_KEY` as needed. Stacks with legacy
per-adapter vars (`JOBS_API14_KEY` / `JSEARCH_API_KEY`) can rotate those
the same way — both still work as fallback (#414).

### API-key env vars

`RAPIDAPI_KEY` is the canonical RapidAPI key var. Legacy per-adapter names
(`JOBS_API14_KEY`, `JSEARCH_API_KEY`) are still accepted as fallback (#414);
renaming to `RAPIDAPI_KEY` is optional.

### Active sources

`config/active_sources.txt` controls which adapters run on each pipeline
cycle. Manage it from `/settings/active-sources/` — the page reads
`REGISTERED_ADAPTERS` directly so newly-registered adapters appear
automatically.

---

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

The "Action required" section is driven by PRs labeled `migration-required` (see [`docs/maintainers/release-process.md`](../maintainers/release-process.md) for the criteria). If a release has no such PRs in its range, the section won't appear.

### Optional tuning: RapidAPI multi-page

Operators on PRO tier can raise per-query page counts via env vars in `data/.env`:

- `JOBS_API14_MAX_PAGES=3` (or up to 5) — `JobsApi14Adapter` multi-page LinkedIn fetch (#414 PR2)
- `JSEARCH_NUM_PAGES=3` — `JSearchAdapter` server-side pagination width (#414 PR3)

Each additional page is one billed RapidAPI request; both default to 1 (pre-#414 behavior). See [`api-keys.md` → Pagination tuning](api-keys.md#pagination-tuning-pro-tier) for the cost math.

## Rolling back locally

If a pull broke your stack and you need to get back to a working state immediately:

1. Edit `.env` to pin to a prior immutable tag, e.g.,
   ```
   FINDAJOB_IMAGE_TAG=vX.Y.Z
   ```
   Available tags are listed at https://github.com/brockamer/findajob/releases.
2. Re-deploy:
   ```bash
   docker compose pull
   docker compose up -d
   ```
3. Report the regression via a GitHub issue so the shared `:latest` tag can be rolled back globally (the upstream maintainer's call — see [release-process.md Rollback section](../maintainers/release-process.md#rollback)).

A local rollback via `.env` pin doesn't affect other users on `:latest`.

## Troubleshooting

- Container fails to start: `docker compose logs scheduler` usually points at the issue.
- Supercronic prints "schedule invalid": a crontab syntax error. Check `ops/scheduled-jobs.yaml` for the canonical schedule, or `docker exec <container> cat /app/crontab` for the rendered version after env-var overrides.
- Container restart-loops with "render_crontab: FATAL": malformed `ops/scheduled-jobs.yaml`, missing required field, or an unrecognized `FINDAJOB_<JOB>_ENABLED` value (must be `true`/`false`/`1`/`0`/`yes`/`no`). Logs name the offending job.
- Gmail ingestion silently disabled: revisit configuration at /config/gmail/ — see [gmail.md](gmail.md).
- For anything else, open an issue at https://github.com/brockamer/findajob/issues.
