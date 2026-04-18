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
| Google Sheets credentials | `config/gsheets_creds.json` | Copy file |
| Sheet ID | `config/sheet_id.txt` | Copy file |
| Gmail OAuth credentials | `config/gmail_oauth_client.json` | Copy file |
| Gmail token cache | `config/gmail_token.json` | Copy file (or re-authorize) |
| Form response sheet ID | `config/form_responses_sheet_id.txt` | Copy file |
| Candidate profile | `candidate_context/profile.md` | Copy file |
| Master resume | `candidate_context/master_resume.md` | Copy file |
| Target companies | `config/target_companies.md` | Copy file |
| Search queries | `config/jsearch_queries.txt` | Copy file |
| Greenhouse feed slugs | `config/feed_urls.txt` | Copy file |
| LinkedIn connections | `data/connections.csv` | Copy file |
| Binary path config | `config/paths.env` | Create new on target if paths differ |
| Voice samples | `candidate_context/voice_samples/*.txt` | Copy directory |
| RAG index | `rags/` or aichat-ng data dir | Rebuild on target (run `--rag rebuild`) |
| Company prep folders | `companies/` | Optional — large, can sync via Google Drive |
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
  config/gsheets_creds.json \
  config/sheet_id.txt \
  config/gmail_oauth_client.json \
  config/gmail_token.json \
  config/form_responses_sheet_id.txt \
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
chmod 600 ~/findajob/config/gsheets_creds.json
chmod 600 ~/findajob/config/gmail_oauth_client.json
```

### Step 3: Create Target-Side Config

On the **target machine**, create `config/paths.env`:
```bash
# Linux defaults — adjust if your install is non-standard
AICHAT_NG=/usr/local/bin/aichat-ng
PANDOC=/usr/bin/pandoc
RCLONE=/usr/bin/rclone
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

### Step 5: Rebuild the RAG Index

The RAG index is not portable (it contains binary embeddings tied to the build). Rebuild it:

```bash
# This takes a few minutes
/usr/local/bin/aichat-ng --rag job_search_rag --rebuild-rag
```

### Step 6: Validate Google Sheets Connection

```bash
python3 scripts/sync_sheet.py
# Should print: "Sheet1: N jobs  Dashboard: M jobs ..."
```

### Step 7: Validate aichat-ng

```bash
echo "Test" | /usr/local/bin/aichat-ng -m gemini:gemini-3-flash-preview -S "Reply with: OK"
# Should return: OK
```

### Step 8: Run a Validation Triage

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

### Step 9: Enable the Scheduler on the Target

Once the validation triage passes:

```bash
# Enable all systemd timers
systemctl --user enable --now findajob-triage.timer
systemctl --user enable --now findajob-poller.timer
systemctl --user enable --now findajob-form-ingest.timer
systemctl --user enable --now findajob-notify-stats.timer
systemctl --user enable --now findajob-notify-health.timer
systemctl --user enable --now findajob-notify-issues.timer
systemctl --user enable --now findajob-notify-apply.timer
systemctl --user enable --now findajob-notify-feedback.timer
systemctl --user enable --now findajob-rag-rebuild.timer
systemctl --user enable --now findajob-jobsync.timer
```

Verify all timers are loaded and show next trigger times:
```bash
systemctl --user list-timers | grep findajob
```

### Step 10: Decommission the Source

Only after:
- [ ] At least one full triage cycle completed on target without errors
- [ ] Google Sheet synced correctly from target
- [ ] At least one ntfy notification fired from target
- [ ] prep_application.py tested on target (flag one job manually)

Then on the **source machine**:

```bash
# Stop and disable all findajob timers on the source host
systemctl --user stop 'findajob-*.timer'
systemctl --user disable 'findajob-*.timer'
```

---

## Google Sheets: Same Sheet or New?

**Recommendation: keep the same sheet.** The existing sheet has your historical data and current queue. A new sheet would require re-importing everything.

The target machine uses the same `config/gsheets_creds.json` and `config/sheet_id.txt`. It writes to the same Google Sheet. This is fine — only one machine runs at a time after step 10.

If you want a fresh sheet (e.g., to experiment on the target without affecting your active queue), create a new Google Sheet, update `config/sheet_id.txt`, and run `scripts/setup_sheets.py` to format it.

---

## Troubleshooting

**`ModuleNotFoundError` during triage**
Pip install was missed. Re-run the pip install from the install guide.

**Gmail OAuth fails on headless machine**
Run triage once on a machine with a browser to generate `config/gmail_token.json`, then transfer that file. The token is valid for months.

**`sqlite3.OperationalError: disk I/O error`**
Usually means a stale WAL file from an interrupted transaction. Remove `data/pipeline.db-wal` and `data/pipeline.db-journal` (only when the pipeline is not running).

**Dedup mismatch (same jobs appear twice)**
The fingerprint is based on `SHA-256(title+company+url)[:16]`. If your source machine's DB has jobs that the target will also fetch, the dedup will correctly skip them on first run. No action needed.
