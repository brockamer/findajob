# Tester Migration: docker → Fly.io

This is the runbook for moving a tester's accumulated findajob state from
the operator-administered docker stack on `<deployment-host>` to that
tester's own freshly-provisioned Fly.io app. Per
[`roadmap.md`](../roadmap.md) Decision 26 — once the Fly.io public
launch ships and self-service deploy is verified by `#672`, testers
self-host on their own Fly accounts and the per-tester docker stack is
sunset (#819 covers the stop/archive procedure).

The migration unit is a single tarball produced by
`findajob.migrate.export` from the source stack's bind-mount root
(`/opt/stacks/findajob-<handle>/state/`) and consumed by
`findajob.migrate.import-fly` against a freshly-deployed Fly app. The
tarball carries the SQLite database (jobs, audit_log, feedback_log,
cost_log, …), the `companies/` per-job folder tree, and the
`candidate_context/` persona profile + role artifacts. Secrets are
**not** in the tarball; the operator hands them off separately via
`fly secrets import` (see step 4 below).

## Prerequisites

- `fly` CLI installed locally and authenticated as the operator (`fly
  auth whoami`).
- Tester's Fly app already provisioned via `ops/fly-deploy.sh` against
  the tester's `ops/fly.toml`. The app should be reachable via `fly
  status --app findajob-<handle>` and the basic-auth gate verified
  (`fly ssh console --app findajob-<handle> --command "python -m
  findajob.web.verify_auth"`).
- Source stack on `<deployment-host>` accessible via `ssh
  <deployment-host>` with sudo.
- ~5-10 minutes of operator-side downtime on the source stack.

## Procedure

### 1. Stop the source stack

```
ssh <deployment-host> 'cd /opt/stacks/findajob-<handle> && sudo docker compose stop'
```

The source stack **must** be stopped before export. Otherwise the
SQLite WAL sidecar may not truncate to empty after
`PRAGMA wal_checkpoint(TRUNCATE)`, and the exporter will refuse to run
with a `DirtyWalError`.

### 2. Export the state

The exporter runs inside the source stack's container (which has the
`findajob.migrate` module installed) against the bind-mount root.
`--user 1000:1000` is critical — `--entrypoint python` bypasses
`entrypoint.sh`'s `gosu findajob` drop, so without it the container
runs as root and the tarball lands root-owned in the lad-owned bind
mount (the next step's transfer will then fail).

```
ssh <deployment-host> 'cd /opt/stacks/findajob-<handle> && sudo docker compose run --rm --user 1000:1000 --entrypoint python scheduler -m findajob.migrate export --state-dir /app/state --tarball /app/state/.export.tar.gz --stack-tag findajob-<handle>'
```

This produces `/opt/stacks/findajob-<handle>/state/.export.tar.gz`
owned `lad:lad` (uid/gid 1000) on the deployment host. The exporter
embeds a `manifest.json` at the top of the tarball with per-table row
counts, the SHA-256 of `pipeline.db`, and file-count / total-size
figures for `companies/` and `candidate_context/`. Skipped on purpose:

- `data/.env` (credentials — handled separately via `fly secrets
  import` in step 4; findajob's runtime reads only from env vars, so
  a copy of `.env` on the Fly volume would be dormant + a secrets-
  at-rest hazard).
- `aichat_ng/` (regenerable LLM chat state — historical interview
  chat replay does **not** survive migration).
- `logs/` (rebuildable from pipeline events; inflates the tarball).

Pull the tarball back to the operator's box. The tarball is lad-owned
and not readable by the SSH user, so a plain `scp` fails — use
`ssh sudo cat | local-redirect` instead:

```
ssh <deployment-host> 'sudo cat /opt/stacks/findajob-<handle>/state/.export.tar.gz' > /tmp/findajob-<handle>-migration.tar.gz
```

Verify size and that `manifest.json` is present at the top:

```
tar -tzf /tmp/findajob-<handle>-migration.tar.gz | head -5
```

(Expected first member: `manifest.json`.)

Clean up the deployment-host copy once the transfer is verified:

```
ssh <deployment-host> 'sudo rm /opt/stacks/findajob-<handle>/state/.export.tar.gz'
```

### 3. Restart the source stack

Get the source stack back up immediately — total downtime should be
under 10 minutes:

```
ssh <deployment-host> 'cd /opt/stacks/findajob-<handle> && sudo docker compose start'
```

Verify the auth gate inside the running container (per CLAUDE.md's
post-deploy verification rule):

```
ssh <deployment-host> 'sudo docker exec -u 1000 findajob-<handle>-scheduler-1 python -m findajob.web.verify_auth'
```

### 4. Hand off secrets to the Fly app

`data/.env` carries the tester's OpenRouter, RapidAPI, and Gmail IMAP
credentials. Per the per-stack key isolation invariant (#339), these
must move to Fly as the tester's own Fly secrets — never shared across
stacks, never auto-imported by the migration tool.

```
ssh <deployment-host> 'sudo cat /opt/stacks/findajob-<handle>/state/data/.env' | fly secrets import --app findajob-<handle>
```

(The `sudo cat` is required because `state/data/.env` is owned `lad:lad`
on the operator's host.)

### 5. Import into the Fly app

```
python -m findajob.migrate import-fly --tarball /tmp/findajob-<handle>-migration.tar.gz --app findajob-<handle>
```

The importer:

1. **Pre-flight checks** that `/app/state/manifest.json` does not
   already exist on the target volume. If it does, the import aborts
   (`TargetNotEmptyError`) — a prior migration already ran. Pass
   `--force` to clobber an existing migration intentionally.
2. **sftp uploads** the tarball to `/tmp/<basename>` on the Fly
   machine.
3. **ssh extracts** the tarball into `/app/state` via `tar -xzf`.
4. **Cleans up** the uploaded `/tmp` tarball.
5. **Verifies** the import by running `python -m findajob.migrate
   verify --state-dir /app/state` inside the Fly container, which
   re-computes per-table row counts, the `pipeline.db` SHA-256, and
   file counts, then compares against the manifest's claims.

On success, the importer prints a JSON `ImportResult` with `"ok":
true` and the observed numbers. On failure, the `failures` array
itemizes which invariant broke (row count mismatch, sha256 mismatch,
file count off, companies size beyond 1% tolerance, candidate_context
empty).

### 6. Post-import smoke

Restart the Fly machine so the imported state takes effect, then
re-verify the auth gate:

```
fly machine restart --app findajob-<handle>
fly ssh console --app findajob-<handle> --command "python -m findajob.web.verify_auth"
```

Browse to `https://findajob-<handle>.fly.dev/` with the tester's
basic-auth credentials. The dashboard should show the tester's
familiar job board (not the post-onboarding empty state) with their
applied / rejected / waitlisted history intact.

## Sanity check expectations

After a successful migration:

- **DB row counts:** exact match on `jobs`, `audit_log`,
  `feedback_log`, `cost_log` between source manifest and Fly volume.
- **`pipeline.db` SHA-256:** byte-identical between source manifest
  and Fly volume — proves the database round-tripped without
  corruption.
- **`companies/` file count:** exact match.
- **`companies/` total size:** within 1% of source (filesystem block
  size can shift slightly between ext4 on the deployment host and
  Fly's ext4 volume; 1% absorbs that without hiding real loss).
- **`candidate_context/` file count:** exact match, and never 0.

The verifier emits any mismatch as a single line in the `failures`
array. If anything is non-empty, the migration is not done — investigate
before sunsetting the source stack.

## Rollback

If the migration verifies cleanly but the tester reports something
missing in their Fly instance, the operator's options are:

- **Re-import with `--force`** from the same tarball after deleting
  `/app/state/manifest.json` on Fly. Useful if the tester's first
  attempt to use the app modified state in a way the operator wants
  to revert.
- **Roll back to the source docker stack.** The source stack on
  `<deployment-host>` is untouched by the migration — `docker compose
  start` brings it back to exactly the state it was in pre-export. The
  Fly app can be left in place or destroyed (`fly apps destroy
  findajob-<handle> --yes`) and re-attempted later.

## Cross-references

- **Issue:** [`#816`](https://github.com/brockamer/findajob/issues/816)
- **Source:** [`src/findajob/migrate/`](../../src/findajob/migrate/)
- **Stack-stop gating:** [`#819`](https://github.com/brockamer/findajob/issues/819)
  blocks on a successful migration; do not stop a tester's source
  docker stack until the post-migration smoke is clean.
- **Roadmap:** [`docs/roadmap.md`](../roadmap.md) Decision 26
- **Cold-cutover playbook (umbrella):**
  [`#749`](https://github.com/brockamer/findajob/issues/749)
