# Operations

> **Docker users:** This document describes the native-install workflow.
> Prefix pipeline commands with `docker compose exec scheduler` to run them
> inside a Compose stack (e.g., `docker compose exec scheduler python3 scripts/triage.py`).
> Docker-specific rewrite tracked in #76.
>

Day-to-day use of the pipeline.

---

## Daily Workflow

The pipeline is mostly autonomous. Your job is:

1. **Morning** — check the ntfy notification and open the Dashboard
2. **Review** — look at new jobs with score ≥ 7
3. **Action** — set STATUS in the Dashboard:
   - `Flag for Prep` → generates application materials (~5–10 min)
   - `REJECT_REASON` (any value) → rejects, logs, moves to `_rejected/`
4. **Apply** — when prep is done, STATUS auto-changes to `Ready to Apply`; you review materials and submit
5. **Track** — set STATUS to `Applied` / `Interviewing` / `Offer` / `Withdrew` as appropriate

---

## Manual Commands

### Run triage manually (test or catch-up)
```bash
python3 scripts/triage.py
```

### Sync sheet immediately
```bash
python3 scripts/sync_sheet.py
```

### Prep a specific job manually (without flagging via sheet)
```bash
python3 scripts/prep_application.py "Company Name" "Job Title" "https://url" "job-db-id"
```

Or use `manual_prep.py` with a text file:
```bash
# Create manual_job.txt:
# company: CompanyName
# title: Job Title
# url: https://...
# ---
# Full JD text here
python3 scripts/manual_prep.py
```

### Inject a job manually (bypasses Google Form)
```bash
python3 scripts/manual_prep.py /path/to/job.txt
```

### Fire notifications manually
```bash
python3 scripts/notify.py daily-stats
python3 scripts/notify.py health-check
python3 scripts/notify.py apply-reminder
python3 scripts/notify.py issues-ping
python3 scripts/notify.py feedback-review
```

### Re-score all jobs with new scorer
```bash
python3 scripts/rescore_all.py
```
Use after changing the `job_scorer` role or switching models.

### Rebuild Google Sheet formatting
```bash
python3 scripts/setup_sheets.py
```
Safe to re-run — idempotent.

---

## Monitoring

### Check recent pipeline events
```bash
tail -f logs/pipeline.jsonl | python3 -c "import sys,json; [print(json.loads(l)) for l in sys.stdin]"
```

### Check last triage completion
```bash
grep "pipeline_complete" logs/pipeline.jsonl | tail -3 | python3 -c "import sys,json; [print(json.loads(l)['ts'], json.loads(l).get('new_jobs',0), 'new jobs') for l in sys.stdin]"
```

### Check for errors in last 24h
```bash
python3 scripts/notify.py health-check
```

Or directly:
```bash
grep -i '"event":".*error\|exception\|failed"' logs/pipeline.jsonl | tail -10
```

### DB stats
```bash
sqlite3 data/pipeline.db "
SELECT stage, count(*) as n FROM jobs GROUP BY stage ORDER BY n DESC;
"
```

### Check scoring breakdown
```bash
sqlite3 data/pipeline.db "
SELECT score_status, count(*) FROM jobs GROUP BY score_status;
"
```

---

## Common Tasks

### Add a new Greenhouse company
1. Find their Greenhouse slug (e.g., from `https://boards.greenhouse.io/newcompany`)
2. Verify: `curl -s "https://boards-api.greenhouse.io/v1/boards/newcompany/jobs" | python3 -m json.tool | head -20`
3. Add slug to `config/feed_urls.txt`
4. Triage picks it up next morning

### Add a new LinkedIn search query
1. Test the query manually in LinkedIn Jobs
2. If it returns results, add to `config/jsearch_queries.txt`
3. Keep it 3–4 words. Test before adding.

### Update your profile
1. Edit `candidate_context/profile.md`
2. No restart needed — profile is read fresh on every triage + every prep

### Update a role prompt
1. Edit `config/roles/{role_name}.md`
2. Reload aichat-ng if it's running interactively: `/reload`
3. In scheduled mode, aichat-ng reads the file fresh on every invocation

### Change the default model
1. Edit `~/.config/aichat_ng/config.yaml`
2. Change the `model:` line at the top
3. Role-specific model overrides in the role `.md` frontmatter take precedence

### Export feedback log for analysis
```bash
sqlite3 -csv data/pipeline.db \
  "SELECT title, company, relevance_score, reject_reason, logged_at FROM feedback_log ORDER BY logged_at DESC;" \
  > /tmp/feedback_export.csv
```

### Rename company folders to new format
```bash
python3 scripts/rename_folders.py
```
Safe to re-run — skips already-renamed folders.

---

## Materials Viewer

The container publishes a read-only web viewer on `FINDAJOB_MATERIALS_PORT` (default `8090`).
Access it at `http://<docker-host>:<port>/` on your LAN or via Wireguard.

The viewer displays prep-folder contents grouped by stage (staged, applied, waitlisted,
rejected), renders Markdown inline, and offers `.docx` files for download.

```bash
# Quick check
curl http://docker.lan:8090/healthz    # expect: ok
```

---

## Log Rotation

`logs/pipeline.jsonl` grows without bound. Rotate manually or set up `logrotate` targeting `logs/*.jsonl` with weekly rotation, 4 copies kept.

Example `logrotate` entry (`/etc/logrotate.d/findajob`):
```
/home/USERNAME/findajob/logs/*.jsonl {
    weekly
    rotate 4
    compress
    missingok
    notifempty
}
```

---

## Systemd Operations (Linux)

```bash
# Check all timer status
systemctl --user list-timers | grep findajob

# Check last run of a specific service
systemctl --user status findajob-triage.service

# View logs
journalctl --user -u findajob-triage.service --since "24 hours ago"

# Force a manual run now
systemctl --user start findajob-triage.service

# Disable a timer temporarily
systemctl --user stop findajob-triage.timer
systemctl --user start findajob-triage.timer  # re-enable
```

