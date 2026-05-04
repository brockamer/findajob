# Operations

> **New to findajob?** Start at [`usage.md`](usage.md). This page is the operator reference for running the stack by hand — triage, sync, prep, notifications — from a shell.

Day-to-day operation of the pipeline. Two run modes are supported:

- **Docker (canonical).** The `ghcr.io/brockamer/findajob` image runs supercronic + uvicorn co-process inside one container. Setup: [`setup/install-docker.md`](setup/install-docker.md). All pipeline commands below are shown in their Docker form (`docker compose exec scheduler …`).
- **Native (fallback).** A direct clone running on systemd timers. Setup: [`setup/install-linux.md`](setup/install-linux.md). To run any Docker command natively, drop the `docker compose exec scheduler ` prefix and run from the repo root.

Where the two modes genuinely diverge (process inspection, log paths, restart procedure, env edits), this doc has [parallel sections](#docker-operations-compose) below.

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

Commands below are in their Docker form. For native installs, drop the
`docker compose exec scheduler ` prefix and run from the repo root.

### Run triage manually (test or catch-up)
```bash
docker compose exec scheduler python3 scripts/triage.py
```

### Prep a specific job manually
```bash
docker compose exec scheduler python3 scripts/prep_application.py "Company Name" "Job Title" "https://url" "job-db-id"
```

Or use `manual_prep.py` with a text file:
```bash
# Create manual_job.txt:
# company: CompanyName
# title: Job Title
# url: https://...
# ---
# Full JD text here
docker compose exec scheduler python3 scripts/manual_prep.py
```

### Inject a job manually

Preferred: use the `/ingest/` web form at `http://<your-host>:${FINDAJOB_MATERIALS_PORT}/ingest/` to paste a URL + JD. The old Google Form + `ingest_form.py` path is retired (#62); the script remains in the image only to drain Form stragglers from pre-v0.2.0 installs.

CLI fallback (same underlying code path as the web form):
```bash
docker compose exec scheduler python3 scripts/manual_prep.py /path/to/job.txt
```

### Fire notifications manually
```bash
docker compose exec scheduler python3 scripts/notify.py daily-stats
docker compose exec scheduler python3 scripts/notify.py health-check
docker compose exec scheduler python3 scripts/notify.py apply-reminder
docker compose exec scheduler python3 scripts/notify.py issues-ping
docker compose exec scheduler python3 scripts/notify.py feedback-review
```

### Re-score all jobs with new scorer
```bash
docker compose exec scheduler python3 scripts/rescore_all.py
```
Use after changing the `job_scorer` role or switching models.

---

## Monitoring

`logs/pipeline.jsonl` lives at `/app/logs/pipeline.jsonl` inside the container,
which is bind-mounted from `./state/logs/` on the host. SQLite lives at
`/app/data/pipeline.db` (host: `./state/data/pipeline.db`).

### Check recent pipeline events
```bash
docker compose exec scheduler tail -f logs/pipeline.jsonl | python3 -c "import sys,json; [print(json.loads(l)) for l in sys.stdin]"
```

### Check last triage completion
```bash
docker compose exec scheduler bash -c 'grep "pipeline_complete" logs/pipeline.jsonl | tail -3 | python3 -c "import sys,json; [print(json.loads(l)[\"ts\"], json.loads(l).get(\"new_jobs\",0), \"new jobs\") for l in sys.stdin]"'
```

### Check for errors in last 24h
```bash
docker compose exec scheduler python3 scripts/notify.py health-check
```

Or directly against the JSONL log:
```bash
docker compose exec scheduler bash -c 'grep -i "\"event\":\".*error\\|exception\\|failed\"" logs/pipeline.jsonl | tail -10'
```

### DB stats
```bash
docker compose exec scheduler sqlite3 data/pipeline.db \
  "SELECT stage, count(*) AS n FROM jobs GROUP BY stage ORDER BY n DESC;"
```

### Check scoring breakdown
```bash
docker compose exec scheduler sqlite3 data/pipeline.db \
  "SELECT score_status, count(*) FROM jobs GROUP BY score_status;"
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
1. Edit `config/roles/{role_name}.md` — under Docker, this file is **baked into the image** at `/app/config/roles/`, NOT bind-mounted. Edit the file in your repo clone, rebuild the image, and `docker compose pull` to deploy. (Native installs edit in place.)
2. In scheduled mode, aichat-ng reads the role file fresh on every invocation — no restart.

### Change the default model
1. Edit `state/aichat_ng/config.yaml` (Docker — bind-mounted from host) or `~/.config/aichat_ng/config.yaml` (native).
2. Change the `model:` line at the top.
3. Role-specific model overrides in `state/aichat_ng/models-override.yaml` take precedence.

### Export feedback log for analysis
Free-text columns can shred under naive separator dumps; use `python3 -c` with `csv.QUOTE_ALL` rather than `sqlite3 -separator`.
```bash
docker compose exec scheduler python3 -c '
import csv, sqlite3, sys
conn = sqlite3.connect("data/pipeline.db")
rows = conn.execute("SELECT title, company, relevance_score, reject_reason, logged_at FROM feedback_log ORDER BY logged_at DESC").fetchall()
w = csv.writer(sys.stdout, quoting=csv.QUOTE_ALL)
w.writerow(["title","company","relevance_score","reject_reason","logged_at"])
w.writerows(rows)
' > /tmp/feedback_export.csv
```

### Rename company folders to new format
```bash
docker compose exec scheduler python3 scripts/rename_folders.py
```
Safe to re-run — skips already-renamed folders. Historical migration script for old `{Company}_{date}_{time}` folders predating the title-disambiguation suffix.

---

## Web UI + Materials Viewer

The container publishes the full web UI (board, ingest, materials viewer, config editor) on `FINDAJOB_MATERIALS_PORT` (default `8090`). Access at `http://<host>:<port>/` on your LAN or via reverse proxy (see [`setup/internet-exposure.md`](setup/internet-exposure.md)).

```bash
curl http://localhost:8090/healthz    # expect: ok
```

The materials sub-view (`/materials/`) renders prep-folder contents grouped by stage (staged, applied, waitlisted, rejected), Markdown inline, `.docx` as download.

---

## Log Rotation

`logs/pipeline.jsonl` grows without bound. Rotation #8 is open; until that lands, rotate manually or set up `logrotate` on the host targeting the bind-mounted log directory.

**Docker** (logrotate runs on the host against the bind-mounted directory):
```
/opt/stacks/findajob-{handle}/state/logs/*.jsonl {
    weekly
    rotate 4
    compress
    missingok
    notifempty
}
```

**Native** (logrotate against the repo's `logs/` dir):
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

## Docker Operations (Compose)

Operate the stack from the host as the user that owns `/opt/stacks/findajob-{handle}/`.

```bash
# Stack status
docker compose ps

# Tail container logs (supercronic + uvicorn merged on stdout)
docker compose logs -f scheduler

# Drop into a shell inside the container
docker compose exec scheduler bash

# Force a one-shot run of a scheduled job (does not touch supercronic)
docker compose exec scheduler python3 scripts/triage.py

# Restart after editing data/.env, config/, or compose.yaml
docker compose restart scheduler

# Pull a new image and recreate the container
docker compose pull && docker compose up -d

# Stop the stack
docker compose down
```

The scheduler inside the container is **supercronic**, not systemd. Schedules are declared in `ops/scheduled-jobs.yaml` and rendered to `/app/crontab` by `scripts/render_crontab.py` at entrypoint. Per-job env overrides are documented in CLAUDE.md (`FINDAJOB_<JOB>_SCHEDULE` / `FINDAJOB_<JOB>_ENABLED`).

---

## Native Operations (systemd)

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

