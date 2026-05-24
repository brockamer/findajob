# Restore from backup

The primary restore path is the **in-app restore UI** at `/onboarding/restore/`.
Upload a backup tarball to a factory-clean findajob instance and it restores to
full operation — database, config, candidate profile, prep materials, and logs —
with no interview needed.

The backup tarball is created from `/settings/backup/` on any running instance.

---

## In-app restore (recommended)

1. Deploy a fresh findajob instance (Docker or Fly). Do not run onboarding.
2. Navigate to the onboarding page — the **"Restore from backup"** link is
   visible above Step 1.
3. Upload the `.tar.gz` tarball. The app validates the tarball structure,
   extracts it atomically, fixes permissions, and redirects to the dashboard.
4. Verify the dashboard renders, job counts match the source, and the cron
   schedule is active.

To restore onto an already-onboarded instance: navigate directly to
`/onboarding/restore/`. The app will ask you to confirm the overwrite.

---

## What a backup tarball must contain

The tarball's top-level directory is `state/`. Contents:

    state/
      data/
        pipeline.db                  # via sqlite3 .backup, NOT file-copy
        .env                         # per-stack secrets (mode 0600)
        .onboarding-complete         # sentinel — MUST be in the tarball
        connections.csv              # optional, LinkedIn export
      config/                        # all per-stack YAML/TXT/JSON config
      candidate_context/             # profile.md, master_resume.md, voice_samples/, etc.
      companies/                     # _applied/, _waitlisted/, _rejected/, per-prep folders
      logs/
        pipeline.jsonl               # rolling event log

Exclusions: `companies/.stale/`, `pipeline.db-shm`, `pipeline.db-wal`,
`*.bak`. The SQLite database must be dumped via `sqlite3 .backup` (online
backup API) — a raw file copy of `pipeline.db` while the stack is running
risks WAL inconsistency.

---

## Manual restore (fallback)

Use this only if the web UI is unreachable (e.g., the instance won't boot).

### Docker

    cd /opt/stacks/findajob-<handle>
    docker compose down
    sudo rm -rf state/
    sudo tar -xzf /path/to/<your-tarball>.tar.gz
    sudo chown -R 1000:1000 state/
    sudo chmod 600 state/data/.env
    sudo chmod 600 state/config/gmail.json   # if present
    docker compose pull
    docker compose up -d

### Fly

    fly ssh console --app <app-name>

    # Inside the container:
    cd /app/state
    rm -rf data/ config/ candidate_context/ companies/ logs/
    tar -xzf /tmp/backup.tar.gz --strip-components=1
    chmod 600 data/.env
    chmod 600 config/gmail.json   # if present
    exit

    fly apps restart <app-name>

(Upload the tarball to `/tmp/` inside the Fly machine via `fly ssh sftp shell`
before running the restore commands.)

---

## Verification

After any restore — in-app or manual:

1. Dashboard renders without redirect to `/onboarding/`.
2. Job counts match the source stack.
3. Cron schedule is active (`supercronic` running, crontab rendered).
4. A scored job can be re-scored end-to-end (LLM call succeeds, API keys intact).
5. Health check is silent or shows only expected warnings.
