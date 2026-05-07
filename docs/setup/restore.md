# Restore from backup

A backup that has never been restored is not a backup. This page documents the
procedure for restoring a findajob stack from a backup tarball — what's in the
tarball, how to drop it in place, and how to verify the restored stack behaves
identically to the source.

This procedure is generic. It works against any tarball produced by a backup
mechanism that captures `/opt/stacks/findajob-<handle>/state/` with the
constraints below. If you're using the operator's per-stack backup mechanism
(the one that ships nightly tarballs to a sibling host), the layout matches by
design.

When to exercise this:

- **At least once after setting up your backup mechanism.** Otherwise you are
  collecting tar files of unknown utility.
- **Before any release that touches schema, onboarding, mounts, or the
  entrypoint.** Listed as a smoke gate in [`../release-process.md`](../release-process.md).
- **Annually**, even if no migration-touching releases have shipped.

---

## What a backup tarball must contain

Restore expects a tarball whose top-level directory is `state/`, with the
following layout reflecting the bind-mount structure of a running stack:

    state/
      data/
        pipeline.db                  # SQLite — the source of truth
        .env                         # API keys + per-stack env (chmod 600)
        .onboarding-complete         # sentinel — restore must include this
        connections.csv              # optional, LinkedIn export
      config/
        # All the per-stack YAML/TXT/JSON config (gitignored upstream;
        # operator-curated locally). Includes profile-derived configs:
        # prefilter_rules.yaml, excluded_employers.yaml,
        # in_domain_patterns.yaml, jsearch_queries.txt,
        # rapidapi_feeds.yaml, active_sources.txt, gmail.json, etc.
      candidate_context/
        profile.md                   # operator-authored, hours of work
        master_resume.md
        voice_samples/               # cover-letter voice calibration
        discovered_companies.{md,json}  # generated; can be re-run
        linkedin-alerts.md           # interview-emitted, optional
      companies/                     # generated artifacts; structurally rebuildable
        _applied/                    # but operationally precious (audit trail)
        _waitlisted/
        _rejected/
        # plus per-prep folders Company_AbbrevTitle_YYYY-MM-DD_HHMMSS/
      logs/
        pipeline.jsonl               # rolling event log

A correct backup tarball **excludes** the following (transient or
reproducible — backing them up wastes space without adding restore value):

- `companies/.stale/` — moved-aside duplicates
- `data/pipeline.db-shm` and `data/pipeline.db-wal` — write-ahead log sidecars;
  irrelevant after a clean SQLite `.backup` dump
- `*.bak` files

The SQLite database **must** be dumped via `sqlite3 .backup` rather than file-
copy; a raw file copy of `pipeline.db` while the stack is running risks
WAL inconsistency and a corrupted restore.

---

## Prerequisites

- A Docker host with `docker` and `docker compose` installed.
- A backup tarball from a prior stack instance.
- The `compose.yaml` for the stack you're restoring into (typically copied
  from `ops/compose.yaml.example` and adjusted for the target handle and ports).
- The same image tag the source stack was running, or a known-compatible newer
  tag. Cross-major-version restores are not supported (schema migrations may
  not run cleanly against backed-up state from an incompatible version).

---

## Procedure

The restore lands the tarball under `/opt/stacks/findajob-<handle>/state/` on
the target host, then brings the stack up. `<handle>` can be any stable
identifier — it does not have to match the source stack's handle, since the
handle is just the per-stack directory name.

1. **Stop the target stack if one is running.**

       cd /opt/stacks/findajob-<handle>
       docker compose down

   If the target directory does not yet exist (fresh restore, new handle),
   create it first:

       sudo mkdir -p /opt/stacks/findajob-<handle>
       sudo chown $USER:$USER /opt/stacks/findajob-<handle>
       cd /opt/stacks/findajob-<handle>

2. **Place `compose.yaml` and `stack.env`** if not already present. Use
   `ops/compose.yaml.example` and `ops/stack.env.example` as templates and
   set the handle, port, and any per-stack env vars to match the target.

3. **Wipe any pre-existing `state/` to avoid mixed-tarball drift.**

       sudo rm -rf state/

   If the existing `state/` has anything you want to preserve (recent
   pipeline.db, recent companies/), capture it to a holding tarball first.

4. **Extract the backup tarball.** The tarball's top-level directory must be
   `state/`; this preserves the expected bind-mount layout.

       sudo tar -xzf /path/to/<your-tarball>.tar.gz

5. **Fix file ownership** so the container user can write to the bind mount.
   The container runs as uid:gid `1000:1000`; bind-mount paths must match.

       sudo chown -R 1000:1000 state/

6. **Restrict secrets-file mode.**

       sudo chmod 600 state/data/.env
       sudo chmod 600 state/config/gmail.json   # if present

7. **Pull the image and start the stack.**

       docker compose pull
       docker compose up -d

8. **Tail the entrypoint logs once** to confirm initial boot completed cleanly
   (crontab rendered, healthcheck endpoint live):

       docker compose logs --tail=80 scheduler

   You should see `crontab rendered ok` and `uvicorn started on 0.0.0.0:8090`.
   No `migrate_schema()` errors, no `KeyError` on env vars.

---

## Verification gate

Run all of the checks below. Any failure means the restore is incomplete; do
not declare the restore successful and do not promote the restored stack to
production traffic.

1. **Healthcheck endpoint responds.**

       curl -fsS http://<deployment-host>:<port>/healthz

   Expected output: `ok`

2. **Onboarding sentinel is intact** — a restored stack must NOT 307 to
   `/onboarding/`. The sentinel at `state/data/.onboarding-complete` is what
   gates this; if the tarball was missing that file, the stack will trigger
   onboarding on first request and overwrite the restored configs.

       curl -sI http://<deployment-host>:<port>/board/dashboard | head -1

   Expected: `HTTP/1.1 200 OK` (NOT a 307 redirect to `/onboarding/`).

3. **Job counts match the source stack.** If you have a record of the source
   stack's job count immediately before backup (e.g. from a recent
   `notify-stats` run), confirm the dashboard shows the same total:

       docker compose exec scheduler sqlite3 /app/data/pipeline.db \
         "SELECT COUNT(*) FROM jobs"

4. **supercronic is running and the schedule rendered.**

       docker compose exec scheduler ps -ef | grep -E '[s]upercronic'
       docker compose exec scheduler cat /app/crontab | head -10

   Expected: a `supercronic` process running, and a non-empty crontab listing
   the same schedules as `ops/scheduled-jobs.yaml`.

5. **Scoring runs end-to-end against the restored DB.** Pick any
   `stage='scored'` job from the restored DB and re-score it; if the LLM call
   succeeds and the score is recomputed without error, the restored config
   (profile, scorer role, API keys) is intact.

       docker compose exec scheduler sqlite3 /app/data/pipeline.db \
         "SELECT fingerprint FROM jobs WHERE stage='scored' LIMIT 1"
       # Use the printed fingerprint:
       docker compose exec scheduler /app/scripts/triage.py --rescore <fingerprint>

   Expected: the rescore prints a fresh `relevance_score` / `fit_score` /
   `probability_score` triple, and `audit_log` has a corresponding row.

6. **Health check is silent or shows only expected WARN.**

       docker compose exec scheduler /app/scripts/notify.py health-check

   A freshly-restored stack with no triage run since restore will fire
   `WARN: pipeline_complete not seen in last 25h` — that clears after the
   first scheduled triage. Anything else is a real signal worth investigating.

---

## When verification passes

The restored stack is operationally identical to the source. You can either:

- Promote it (route traffic to it; decommission the old stack), or
- Tear it down (`docker compose down -v` plus `sudo rm -rf state/`) — the
  exercise was the goal; the source stack is unaffected.

Either way, capture the date you exercised the procedure. The release process
expects a recent (≤ 1 release cycle) restore exercise as a smoke gate; see
[`../release-process.md`](../release-process.md).

---

## When verification fails

Investigate before retrying. Common failure modes:

- **`/onboarding/` redirect on a restored stack** — sentinel was missing from
  the tarball. The backup mechanism must include `state/data/.onboarding-complete`.
- **`KeyError: 'OPENROUTER_API_KEY'` (or similar) in entrypoint logs** —
  `state/data/.env` was missing or unreadable. Re-extract; re-chmod 600.
- **Permission errors writing to `state/`** — chown step missed; the container
  user (1000:1000) cannot write into a root-owned bind mount.
- **Schema migration errors on startup** — image tag is older than the version
  that wrote the backed-up database. Pull a tag at least as new as the source.
- **Scoring fails with `LLM call failed`** — API keys in `.env` are valid but
  the OpenRouter / RapidAPI account is in a different state than at backup
  time (key rotated, credits exhausted, etc.). Not a restore failure;
  resolve at the API provider.
