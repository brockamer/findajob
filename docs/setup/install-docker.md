# Docker Install

> **New here?** Start at [`README.md`](README.md) — it sequences prerequisites → install → configure in order.

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
sudo mkdir -p /opt/stacks/findajob-<you>/state/{data,config,candidate_context,companies,logs,aichat_ng,.backups}
sudo chown -R $(id -u):$(id -g) /opt/stacks/findajob-<you>/
```

Replace `<you>` with a short user tag.

## 2. Drop in the compose template and env

```bash
cd /opt/stacks/findajob-<you>/
curl -fsSL -o compose.yaml https://raw.githubusercontent.com/brockamer/findajob/main/ops/compose.yaml.example
curl -fsSL -o .env https://raw.githubusercontent.com/brockamer/findajob/main/ops/stack.env.example
```

Edit `.env` to taste — at minimum set `FINDAJOB_TZ`, `FINDAJOB_MATERIALS_PORT`, and (if dogfooding) `FINDAJOB_IMAGE_TAG=latest`.

## 3. Populate `state/`

- `state/data/.env` — API keys (chmod 600). Template: [repo's `data/.env.example`](https://github.com/brockamer/findajob/blob/main/data/.env.example)
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

Wireguard-only / LAN-only instances can skip this — the perimeter is the gate.
See [`internet-exposure.md`](internet-exposure.md) for the full threat model.

### What the entrypoint does automatically

As of `:v0.1.1`, the container image's entrypoint handles these on every
start — you do not run any of these commands manually:

- Creates `state/data/pipeline.db` with the full schema if it's missing.
  Idempotent: no-op on populated DBs (#116, #117).
- Seeds `state/aichat_ng/config.yaml` from a sanitized template **only if
  absent**. Your customizations (added clients, custom models, REPL prefs)
  persist across image pulls (#118).
- Seeds `state/aichat_ng/models-override.yaml` only if absent (#106).
- Creates the `state/aichat_ng/roles` symlink pointing at the image's
  bundled `/app/config/roles/` **only if absent** — so you can override
  with your own roles dir (#118).
- Seeds tracked config files (`roles/`, `scoring_schema.json`,
  `model_pricing.yaml`, `reference.docx`, `strip-bookmarks.lua`) into
  `state/config/` on every start — these are always overwritten so image
  updates propagate on `docker compose up`. Your personal config files
  (`sheet_id.txt`, `jsearch_queries.txt`, etc.) are left alone because
  they don't exist in the bundled set.

Fill in your personal config files above and run `docker compose up -d` —
no manual schema init, no handcrafted aichat-ng config, no symlink setup.

### Materials viewer port

Set `FINDAJOB_MATERIALS_PORT` in your stack `.env` to a free host port (default `8090`).
Each stack on the same host must use a unique port number.

```
FINDAJOB_MATERIALS_PORT=8090
```

The container publishes the viewer at `http://<docker-host>:<port>/`. On a LAN or Wireguard
VPN this is reachable from any device. The viewer is read-only — it displays prep-folder
contents grouped by stage (staged, applied, waitlisted, rejected), renders Markdown inline,
and offers `.docx` files for download.

The viewer has a top nav linking all feature groups. `/` is a pipeline-at-a-glance landing
page with stage counts; `/materials/` is the prep-folder index (previously served at `/`).

```bash
# Quick smoke test after first deploy
curl http://docker.lan:8090/healthz    # expect: ok
```

The viewer also serves six board pages under `/board/`: Dashboard, Applied,
Review, Waitlist, Rejected, Archive. These mirror the Google Sheet tabs,
reading the same database. `sync_sheet.py` keeps updating Sheets in
parallel — use whichever view you prefer. The Archive page covers every
job in the DB (10k+) with infinite-scroll pagination, per-column sort,
and a live text filter.

### Materials viewer base URL (for Sheet hyperlinks)

`sync_sheet.py` hyperlinks the company cell on Dashboard / Applied / Waitlist / Rejected Applications tabs into the viewer, but only when `FINDAJOB_MATERIALS_BASE_URL` is set in the stack `.env` **and** the deployed `compose.yaml` passes it into the container. Unset → cells render as plain text, no crash.

```
FINDAJOB_MATERIALS_BASE_URL=http://docker.lan:8090
```

Match the hostname and port to what the user's browser can reach (LAN hostname or VPN hostname + `FINDAJOB_MATERIALS_PORT`).

**If you deployed from `ops/compose.yaml.example` on v0.1.2 or earlier**, the env var isn't forwarded yet — the template was updated after that release. Add this line under `environment:` in the `scheduler` service:

```yaml
FINDAJOB_MATERIALS_BASE_URL: ${FINDAJOB_MATERIALS_BASE_URL:-}
```

Then `docker compose up -d` to restart with the new env. Full migration writeup in [`state-migration.md`](state-migration.md).

## 4. Initial auth: Gmail (optional)

Gmail ingestion uses a loopback OAuth flow that requires an SSH tunnel — Google's device flow (`google.com/device`) does not support Gmail scopes. If you skip this step, Gmail ingestion is automatically disabled and the pipeline falls back to Greenhouse/Ashby/Lever feeds and RapidAPI.

### Prerequisites

- In [Google Cloud Console](https://console.cloud.google.com/), create an OAuth 2.0 client of type **Desktop app** (not "TVs and Limited Input devices" — that type rejects Gmail scopes). Download the JSON and save it as `state/config/gmail_oauth_client.json`.

### One-time token flow

**Step 1 — open an SSH tunnel** (keep this terminal open):

```bash
ssh -L 8080:localhost:8080 <your-docker-host>
```

**Step 2 — in another terminal, run the auth container**:

```bash
docker compose --profile setup run --rm -p 8080:8080 gmail-auth
```

**Step 3** — copy the URL printed by the container, open it in your browser, sign in, and grant Gmail.readonly access. The container exits automatically when consent is granted.

Token is saved to `state/config/gmail_token.json` (chmod 600). Close the SSH tunnel once the container exits.

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

## 7. First-run onboarding interview

The first time you open the web UI you'll be redirected to `/onboarding/`
(#148). This page hosts the paste-back step that turns an LLM-conducted
interview into the ten config files findajob needs to run your pipeline.

1. Open the `/onboarding/` page in a browser. It will display a button labelled
   "Copy Onboarding Prompt" and instructions.
2. Click the button to copy the interviewer prompt, paste it into your
   preferred LLM (Claude.ai with Opus 4.7 + Extended Thinking is the
   recommended default; ChatGPT and Gemini also work — see the prompt for
   per-platform upload instructions). Attach your resume + any
   performance reviews / 360s / writing samples you have.
3. Run the interview end-to-end (~90 min). When done, copy the entire
   emitted block of text the LLM gives you.
4. Back on `/onboarding/`, paste the block into the main text box.
5. Paste your **OpenRouter API key** (sign up at https://openrouter.ai and
   prepay $10–$20 — covers a long time of use) into the dedicated key
   field. Each user funds their own OpenRouter usage.
6. Click submit. The injector validates the emission, runs a 1-token
   smoke check against OpenRouter to verify the key, atomically writes
   the ten config files plus a derived `companies_of_interest.txt`, and
   marks onboarding complete. Any errors are surfaced verbatim — fix and
   resubmit.

After paste-back lands, the next scheduled triage run (00:00 in your
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

Once the scheduler is running, your daily workflow happens in two places:

1. **`/board/*` in the web UI** — the primary interface. Open
   `http://<host>:<FINDAJOB_MATERIALS_PORT>/board/dashboard` in a browser.
   The Dashboard tab lists high-scoring jobs; click **Flag for Prep** on
   the ones you want materials for. When prep completes, switch the status
   to **Applied** to move the job to the Applied tab, then track it through
   **Interviewing / Offer / Withdrew / Not Selected**. Review and Waitlist
   tabs handle triage and deferred jobs respectively. Every click writes
   to the DB in the same request — no polling delay.

2. **The Google Sheet** — a read-only synced view. Useful for phone-glance
   status checks or sharing a read-only link. Edits made directly in the
   Sheet are **ignored by the pipeline** and overwritten on the next
   `sync_sheet.py` run; always drive state changes from the web UI.

`scripts/watchdog.py` runs every 10 min and resets any job stuck in
`prep_in_progress` for more than 60 min back to `scored` so you can re-flag it.

## Tag pinning strategy

`FINDAJOB_IMAGE_TAG` in your `.env` controls which image Docker Compose pulls. Pick based on how much change tolerance you want.

| Value | Mutability | Recommended for |
|---|---|---|
| `v0.1` | moving (auto-advances to latest `v0.1.x` patch) | **Default.** Most users. Auto-accepts bugfixes; breaking changes require an explicit `.env` edit. |
| `v0.1.0` | immutable | Pin exactly when you need a known-good version and can't afford surprises (e.g., during an active job-hunt push). |
| `latest` | moving (tip of `main`) | Dogfood track. The upstream maintainer runs this to exercise releases before tagging. May break. |
| `main-<sha>` | immutable (one tag per commit on `main`) | Precise pinning or bisecting when diagnosing a regression. |

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
beta testers on the same `docker.lan`), the operator stack can run with
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

## Migrating from an older image: aichat-ng mount path fix

If your stack was deployed before the aichat-ng mount-path fix, your `compose.yaml` still mounts `./state/aichat_ng` to `/root/.config/aichat_ng`. The container now runs as a non-root user (PUID), so `/root` is unreadable and all scoring calls fail silently.

Apply these changes once, per instance:

1. **Stop the stack.**
   ```bash
   cd /opt/stacks/findajob-<you>/
   docker compose down
   ```

2. **Edit `compose.yaml`** (or re-pull `ops/compose.yaml.example` if you haven't customized it). Two changes to the `scheduler` service:

   - Under `environment:`, add `HOME: /app`.
   - Change the aichat-ng volume from `./state/aichat_ng:/root/.config/aichat_ng` to `./state/aichat_ng:/app/.config/aichat_ng`.

   Apply the same `HOME: /app` change to the `gmail-auth` service.

3. **Fix ownership of `state/aichat_ng/`** in case it was populated under the old path:
   ```bash
   sudo chown -R $(id -u):$(id -g) state/aichat_ng
   ```

4. **Pull and bring the stack back up.**
   ```bash
   docker compose pull
   docker compose up -d
   docker compose logs -f scheduler  # Ctrl-C once you see supercronic's schedule dump
   ```

Verify with a scoring smoke test:
```bash
docker compose exec scheduler aichat-ng -m claude:claude-sonnet-4-6 -- 'reply "ok"'
```
Expected output: `ok`. If aichat-ng errors with "no such file or directory" or returns nothing, the config is still in the old location — re-check the mount path.

For instructions on migrating from rclone/Drive to the materials viewer, see [`docs/setup/state-migration.md`](state-migration.md).

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
- Supercronic prints "schedule invalid": a crontab syntax error. Check `ops/scheduled-jobs.yaml` for the canonical schedule, or `docker exec <container> cat /app/crontab` for the rendered version after env-var overrides.
- Container restart-loops with "render_crontab: FATAL": malformed `ops/scheduled-jobs.yaml`, missing required field, or an unrecognized `FINDAJOB_<JOB>_ENABLED` value (must be `true`/`false`/`1`/`0`/`yes`/`no`). Logs name the offending job.
- Gmail ingestion silently disabled: re-run the token flow from step 4 (`ssh -L 8080:localhost:8080 <host>` + `docker compose --profile setup run --rm -p 8080:8080 gmail-auth`).
- For anything else, open an issue at https://github.com/brockamer/findajob/issues.
