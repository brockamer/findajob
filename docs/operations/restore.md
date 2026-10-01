# Restore from backup

The primary restore path is the **in-app restore UI** at `/onboarding/restore/`.
Upload a backup tarball to a factory-clean findajob instance and it restores to
full operation — database, config, candidate profile, prep materials, and logs —
with no interview needed.

The backup tarball is created from `/settings/backup/` on any running instance.
It carries no credentials: each instance keeps its own API keys and sign-in
(see [Credentials are not in a backup](#credentials-are-not-in-a-backup)).

---

## In-app restore (recommended)

1. Deploy a fresh findajob instance (Docker or Fly). Do not start the
   onboarding interview.
2. Navigate to the onboarding page — the **"Restore from backup"** link is
   visible above Step 1.
3. Upload the `.tar.gz` tarball. The app validates the tarball structure,
   extracts it atomically, fixes permissions, and redirects to the dashboard.
4. On a new instance, set its credentials (next section).
5. Verify the dashboard renders, job counts match the source, and the cron
   schedule is active.

To restore onto an already-onboarded instance: navigate directly to
`/onboarding/restore/`. The app will ask you to confirm the overwrite. The
instance keeps its own API keys and sign-in.

---

## Credentials are not in a backup

Anyone who can sign in to an instance can download its backup, so the tarball
leaves out every file that holds a credential:

- `data/.env` — API keys, `NTFY_TOPIC`, and the sign-in pair set during onboarding
- `config/gmail.json` — the Gmail app password
- `config/gmail_token.json`, `config/gsheets_creds.json`, `config/ntfy_topic.txt`
  — no longer written, but an older instance can still have them

Onboarding also stores the API keys you enter in `pipeline.db`. The backup's
copy of the database has them cleared; the running instance's database is not
changed.

Restore never removes these files from the target instance. When the tarball
does not contain one, the target's own copy stays in place. Restore also clears
any API keys stored in the restored database.

What to do after a restore:

- **Same instance, or one that is already set up:** nothing. Its keys and
  sign-in are unchanged.
- **New instance:** give it its own credentials.
  - Sign-in: set it at `/onboarding/` (the setup token is in the container
    logs), or with `FINDAJOB_AUTH_USER` / `FINDAJOB_AUTH_PASS`.
  - API keys: on Docker, add them to `state/data/.env` on the host and restart
    the stack (see [Rotating an API key](install-docker.md#rotating-an-api-key),
    option B). On Fly, set them with `fly secrets set`.
  - Gmail ingestion: re-enter the app password at `/config/gmail/`.

  Never copy one instance's `data/.env` to an instance run for someone else —
  see [one set of API keys per instance](install-docker.md#multi-tenant-hosts-one-set-of-api-keys-per-instance).
- **Moving your own instance to a new host:** to keep the same keys, copy
  `state/data/.env` and `state/config/gmail.json` between hosts yourself, over a
  channel you would trust with a password. They do not travel in the backup.

A backup made before credentials were left out still contains `data/.env` and
`config/gmail.json`, and its `pipeline.db` holds the API keys entered during
onboarding. Restoring one replaces the target's credential files with its
copies. Treat such a file like a password export.

---

## What a backup tarball must contain

The tarball's top-level directory is `state/`. Contents:

    state/
      data/
        pipeline.db                  # via sqlite3 .backup, NOT file-copy
        .onboarding-complete         # sentinel — MUST be in the tarball
        connections.csv              # optional, LinkedIn export
      config/                        # per-stack YAML/TXT/JSON config, minus credential files
      candidate_context/             # profile.md, master_resume.md, voice_samples/, etc.
      companies/                     # _applied/, _waitlisted/, _rejected/, per-prep folders
      logs/
        pipeline.jsonl               # rolling event log

Exclusions: the credential files listed above, `companies/.stale/`,
`pipeline.db-shm`, `pipeline.db-wal`, `*.bak`, `*.tmp`. The SQLite database must be
dumped via `sqlite3 .backup` (online backup API) — a raw file copy of
`pipeline.db` while the stack is running risks WAL inconsistency.

---

## Manual restore (fallback)

Use this only if the web UI is unreachable (e.g., the instance won't boot).

### Docker

    cd /opt/stacks/findajob-<handle>
    docker compose down
    sudo mv state/ state.pre-restore/
    sudo tar -xzf /path/to/<your-tarball>.tar.gz
    # Keep this instance's credentials; -n skips any the tarball already has.
    # Newer coreutils print a "behavior of -n is non-portable" warning; it is harmless.
    sudo cp -pn state.pre-restore/data/.env state/data/
    sudo cp -pn state.pre-restore/config/gmail.json state/config/   # if present
    sudo chown -R 1000:1000 state/
    sudo chmod 600 state/data/.env
    sudo chmod 600 state/config/gmail.json   # if present
    docker compose pull
    docker compose up -d

Delete `state.pre-restore/` once the verification below passes.

### Fly

    fly ssh console --app <app-name>

    # Inside the container:
    cd /app/state
    # Keep this instance's credentials. data/.env exists on Fly when the
    # sign-in was set during onboarding rather than as a Fly secret.
    mkdir -p /tmp/keep
    cp -p data/.env /tmp/keep/          # if present
    cp -p config/gmail.json /tmp/keep/  # if present
    rm -rf data/ config/ candidate_context/ companies/ logs/
    tar -xzf /tmp/backup.tar.gz --strip-components=1
    cp -pn /tmp/keep/.env data/         # if present
    cp -pn /tmp/keep/gmail.json config/ # if present
    chmod 600 data/.env config/gmail.json   # whichever are present
    rm -rf /tmp/keep
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
4. A scored job can be re-scored end-to-end (LLM call succeeds). On a new
   instance this passes only after its API keys are set — see
   [Credentials are not in a backup](#credentials-are-not-in-a-backup).
5. Health check is silent or shows only expected warnings.
