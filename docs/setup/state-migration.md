# State Migration

> **Note:** This guide covers **native → native** migration (moving a systemd
> install between Linux hosts). For Docker deploys, migration is simpler:
> `rsync` the stack directory's `state/` folder to the new host and bring
> the stack up there. See [`install-docker.md`](install-docker.md).
>

How to move a running findajob pipeline from one machine to another without losing data.

This guide assumes:
- **Source machine**: existing running pipeline (Linux host)
- **Target machine**: new Linux host
- **Strategy**: parallel bring-up — keep source running until target is validated

Do NOT decommission the source until you have confirmed a full triage cycle completes cleanly on the target.

---

## What State Exists

| Item | Location | How to migrate |
|---|---|---|
| Job database | `data/pipeline.db` | Copy file directly (SQLite is portable) |
| API keys | `data/.env` | Copy file, `chmod 600` |
| Gmail integration config | `config/gmail.json` | Copy file |
| Gmail integration state  | `config/gmail_state.json` | Copy file |
| Form response sheet ID | `config/form_responses_sheet_id.txt` | Copy file (only if you still drain Form stragglers via `ingest_form.py`) |
| Candidate profile | `candidate_context/profile.md` | Copy file |
| Master resume | `candidate_context/master_resume.md` | Copy file |
| Target companies | `config/target_companies.md` | Copy file |
| Search queries | `config/jsearch_queries.txt` | Copy file |
| Greenhouse feed slugs | `config/feed_urls.txt` | Copy file |
| LinkedIn connections | `data/connections.csv` | Copy file |
| Binary path config | `config/paths.env` | Create new on target if paths differ |
| Voice samples | `candidate_context/voice_samples/*.txt` | Copy directory |
| RAG index | `rags/` or aichat-ng data dir | Rebuild on target (run `--rag rebuild`) |
| Company prep folders | `companies/` | Optional — large, rsync or copy manually |
| aichat-ng config | `~/.config/aichat_ng/config.yaml` | Create new on target |
| Personal CLAUDE context | `CLAUDE.local.md` | Copy |

**Do NOT copy:**
- `logs/` — not needed, starts fresh
- `data/pipeline.db-journal`, `data/pipeline.db-wal` — stale WAL files
- `__pycache__/` — Python bytecode, rebuilds automatically

---

## Step-by-Step Migration

### Step 1: Prepare the Target Machine

Complete the [Linux install guide](install-linux.md) through Step 10 (systemd setup).

Do not start the scheduler yet — keep it stopped until migration is complete.

### Step 2: Transfer State Files

From the **source machine**, run:

```bash
# Replace TARGET_HOST and TARGET_PATH with your actual values
TARGET=user@TARGET_HOST
DEST=~/findajob

rsync -av \
  data/pipeline.db \
  data/.env \
  data/connections.csv \
  config/gmail.json \
  config/gmail_state.json \
  config/jsearch_queries.txt \
  config/feed_urls.txt \
  config/target_companies.md \
  candidate_context/ \
  ${TARGET}:${DEST}/
```

Also copy your personal CLAUDE context:
```bash
rsync -av CLAUDE.local.md ${TARGET}:${DEST}/
```

**On the target**, verify permissions:
```bash
chmod 600 ~/findajob/data/.env
chmod 600 ~/findajob/config/gmail.json
chmod 600 ~/findajob/config/gmail_state.json
```

### Step 3: Create Target-Side Config

On the **target machine**, create `config/paths.env`:
```bash
# Linux defaults — adjust if your install is non-standard
AICHAT_NG=/usr/local/bin/aichat-ng
PANDOC=/usr/bin/pandoc
```

Create the aichat-ng config:
```bash
mkdir -p ~/.config/aichat_ng
# Create ~/.config/aichat_ng/config.yaml — see configure.md for the template
```

### Step 4: Verify Database Integrity

```bash
sqlite3 ~/findajob/data/pipeline.db "PRAGMA integrity_check;"
# Expected: ok

sqlite3 ~/findajob/data/pipeline.db "SELECT count(*) FROM jobs;"
# Should match your source machine count
```

### Step 5: Validate aichat-ng

```bash
echo "Test" | /usr/local/bin/aichat-ng -m gemini:gemini-3-flash-preview -S "Reply with: OK"
# Should return: OK
```

### Step 6: Run a Validation Triage

This is the critical test. Run one full triage cycle and verify it completes without errors:

```bash
python3 scripts/triage.py 2>&1 | tee /tmp/triage_validation.log
```

Check the log for errors:
```bash
grep -i "error\|traceback\|exception" /tmp/triage_validation.log
```

Check the event log for a successful completion:
```bash
grep "pipeline_complete\|triage_complete" ~/findajob/logs/pipeline.jsonl | tail -3
```

Check that new jobs appeared in the DB:
```bash
sqlite3 ~/findajob/data/pipeline.db \
  "SELECT count(*) FROM jobs WHERE created_at > datetime('now', '-2 hours');"
```

### Step 8: Enable the Scheduler on the Target

Once the validation triage passes:

```bash
# Enable all systemd timers
systemctl --user enable --now findajob-triage.timer
systemctl --user enable --now findajob-poller.timer
# findajob-form-ingest.timer retired in #62 — the /ingest/ web UI replaces it.
systemctl --user enable --now findajob-notify-stats.timer
systemctl --user enable --now findajob-notify-health.timer
systemctl --user enable --now findajob-notify-issues.timer
systemctl --user enable --now findajob-notify-apply.timer
systemctl --user enable --now findajob-notify-feedback.timer
```

Verify all timers are loaded and show next trigger times:
```bash
systemctl --user list-timers | grep findajob
```

### Step 9: Decommission the Source

Only after:
- [ ] At least one full triage cycle completed on target without errors
- [ ] At least one ntfy notification fired from target
- [ ] prep_application.py tested on target (flag one job manually)

Then on the **source machine**:

```bash
# Stop and disable all findajob timers on the source host
systemctl --user stop 'findajob-*.timer'
systemctl --user disable 'findajob-*.timer'
```

---

## Troubleshooting

**`ModuleNotFoundError` during triage**
Run `uv sync` from the repo root to (re)install the project venv per
[install-linux.md](install-linux.md). For Docker installs, this means
the image build is incomplete — pull a fresh image and re-up the stack.

**Gmail integration not working after migration**
Re-configure at `/config/gmail/` — the new IMAP/app-password integration is per-stack and the simplest path forward is reconfiguring rather than copying state. See [`gmail.md`](gmail.md).

**`sqlite3.OperationalError: disk I/O error`**
Usually means a stale WAL file from an interrupted transaction. Remove `data/pipeline.db-wal` and `data/pipeline.db-journal` (only when the pipeline is not running).

**Dedup mismatch (same jobs appear twice)**
The fingerprint is based on `SHA-256(title+company+url)[:16]`. If your source machine's DB has jobs that the target will also fetch, the dedup will correctly skip them on first run. No action needed.

---

## Migrating from rclone/Drive to the materials viewer

Applies to operator stacks that were running with `FINDAJOB_JOBSYNC_ENABLED=true`
on v0.1.x. Testers on fresh installs can skip this — they never had rclone enabled.

### Steps

1. Stop the stack:
   ```bash
   docker compose down
   ```

2. Remove the now-unused bind mount:
   ```bash
   rm -rf state/rclone
   ```

3. Edit `.env` to add a port for the materials viewer:
   ```
   FINDAJOB_MATERIALS_PORT=8090   # or next free port if 8090 is taken
   ```

4. Edit `compose.yaml`:
   - Remove:  `- ./state/rclone:/app/.config/rclone`
   - Remove env var: `FINDAJOB_JOBSYNC_ENABLED`
   - Add a ports block under the `scheduler` service:
   ```yaml
   ports:
     - "${FINDAJOB_MATERIALS_PORT}:8090"
   ```

5. Pull and start:
   ```bash
   docker compose pull
   docker compose up -d
   ```

6. Verify:
   ```bash
   curl http://<deployment-host>:8090/healthz    # expect: ok
   ```
   Then open `http://<deployment-host>:8090/` in a browser to browse materials.

### Existing Drive folders

Nothing automated. Drive folders that rclone synced remain at
drive.google.com. Delete them manually if desired — findajob will
never look at them again.

