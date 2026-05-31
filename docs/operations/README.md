# Operations

> **New to findajob?** Start at [`../usage.md`](../usage.md). This page is the operator reference for running the stack by hand — triage, sync, prep, notifications — from a shell.

Day-to-day operation of the pipeline. The `ghcr.io/brockamer/findajob` image runs supercronic + uvicorn co-process inside one container. Setup: [`install-docker.md`](install-docker.md). For cloud deployment on Fly.io — one Fly app per tenant, image runs unchanged — see [`fly-deploy.md`](fly-deploy.md).

**Command forms:**
- Docker: `docker compose exec scheduler python3 scripts/<script>.py`
- Fly: `fly ssh console --app <app> --command "python3 scripts/<script>.py"`

---

## Daily Workflow

The pipeline is mostly autonomous. Your job is:

1. **Morning** — check the ntfy notification and open the Dashboard
2. **Review** — look at new jobs with score ≥ 7
3. **Action** — set STATUS in the Dashboard:
   - `Flag for Prep` → generates application materials (~5–10 min)
   - `REJECT_REASON` (any value) → rejects, logs, moves to `_rejected/`
4. **Apply** — when prep finishes, review the briefing and drafted materials on the job's Materials page, then submit
5. **Track** — set STATUS to `Applied` / `Interviewing` / `Offer` / `Withdrew` as appropriate

---

## Manual Commands

### Run triage
```bash
docker compose exec scheduler python3 scripts/triage.py
```

### Prep a specific job
```bash
docker compose exec scheduler python3 scripts/prep_application.py "Company Name" "Job Title" "https://url" "job-db-id"
```

### Inject a job manually

Preferred: use the `/ingest/` web form at `http://<your-host>:${FINDAJOB_MATERIALS_PORT}/ingest/` to paste a URL + JD.

CLI fallback (job file format: `company:`, `title:`, `url:`, `---`, then JD text):
```bash
docker compose exec scheduler python3 scripts/manual_prep.py /path/to/job.txt
```

### Fire notifications manually
```bash
docker compose exec scheduler python3 scripts/notify.py daily-stats
docker compose exec scheduler python3 scripts/notify.py health-check
docker compose exec scheduler python3 scripts/notify.py apply-reminder
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
bind-mounted from `./state/logs/` on the host. SQLite at `/app/data/pipeline.db`
(host: `./state/data/pipeline.db`).

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
docker compose exec scheduler bash -c 'grep -i "\"event\":\".*error\|exception\|failed\"" logs/pipeline.jsonl | tail -10'
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
1. Edit `config/roles/{role_name}.md` — this file is **baked into the image** at `/app/config/roles/`, NOT bind-mounted. Edit the file in your repo clone, rebuild the image, and deploy to pick up the change.
2. The OpenRouter wrapper reads the role file (frontmatter `model:`, `temperature:`, `max_tokens:`) fresh on every invocation — no restart.

### Change a role's model
1. Edit the `model:` line in the role's frontmatter (e.g. `config/roles/job_scorer.md`).
2. Rebuild and deploy. Each role pins its own model — there's no global default to override.

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
Safe to re-run — skips already-renamed folders.

---

## Web UI + Materials Viewer

The container publishes the full web UI (board, ingest, materials viewer, config editor) on `FINDAJOB_MATERIALS_PORT` (default `8090`). Access at `http://<host>:<port>/` on your LAN or via reverse proxy (see [`internet-exposure.md`](internet-exposure.md)).

```bash
curl http://localhost:8090/healthz    # expect: ok
```

The materials sub-view (`/materials/`) renders prep-folder contents grouped by stage (staged, applied, waitlisted, rejected), Markdown inline, `.docx` as download.

---

## Log Rotation

`logs/pipeline.jsonl` rotates in-process at 5 MB (gzipped to `pipeline.jsonl.1.gz`; ring keeps 6 backups; entries older than 90 days pruned). The durable transition trail lives in the `audit_log` SQLite table, not in `pipeline.jsonl`. To ship long-running log history off-host, forward `pipeline.jsonl` to syslog / Loki / Datadog from the host.

---

## Stack operations

Operate the stack from the host as the user that owns the stack directory.

```bash
# Stack status
docker compose ps

# Tail container logs (supercronic + uvicorn merged on stdout)
docker compose logs -f scheduler

# Drop into a shell inside the container
docker compose exec scheduler bash
# Fly equivalent:
fly ssh console --app <app>

# Force a one-shot run of a scheduled job (does not touch supercronic)
docker compose exec scheduler python3 scripts/triage.py
# Fly equivalent:
fly ssh console --app <app> --command "python3 scripts/triage.py"

# Recreate after editing data/.env or compose.yaml
# (config/ files are hot-reloaded — no restart needed)
docker compose up -d --force-recreate

# Pull a new image and recreate the container
docker compose pull && docker compose up -d
# Fly equivalent:
fly deploy --config ops/fly.toml

# Verify auth gate after every deploy (exit non-zero = take the stack down)
docker compose exec scheduler python -m findajob.web.verify_auth
# Fly equivalent:
fly ssh console --app <app> --command "python -m findajob.web.verify_auth"

# Stop the stack
docker compose down
```

The scheduler inside the container is **supercronic**. Schedules are declared in `ops/scheduled-jobs.yaml` and rendered to `/app/crontab` by `scripts/render_crontab.py` at entrypoint. Per-job env overrides: `FINDAJOB_<JOB>_SCHEDULE` / `FINDAJOB_<JOB>_ENABLED`.

---

## Notifications

ntfy push notifications sent by `scripts/notify.py`. The topic is read from `NTFY_TOPIC` in `data/.env`. For initial setup (registering a topic, installing the phone app), see [`../getting-started/notifications.md`](../getting-started/notifications.md).

### `daily-stats` — Morning summary
**Schedule:** 06:15 daily (15 min after triage's completion window).

Queue depth, jobs added in the last 24h, in-progress applications (prepped/applied/interviewing), last successful triage timestamp.

### `health-check` — Pipeline health
**Schedule:** 07:00 daily.

Whether triage completed in the last 25h (looks for `pipeline_complete` event in logs), error events from `pipeline.jsonl` in the last 25h, count of `manual_review` jobs (potential scoring failures), last completion timestamp.

### `apply-reminder` — Daily nudge
**Schedule:** 06:00 daily.

Rotating motivational quip + a reminder to submit one application today.

### `feedback-review` — Rejection-pattern alert
**Schedule:** Sunday 08:00.

Fires only when `feedback_log` has ≥ 10 entries. Prompts you to review rejection patterns and adjust scoring or profile.

To review manually:
```bash
docker compose exec scheduler python3 -c '
import csv, sqlite3, sys
conn = sqlite3.connect("data/pipeline.db")
rows = conn.execute(
    "SELECT reject_reason, count(*) AS n FROM feedback_log GROUP BY reject_reason ORDER BY n DESC"
).fetchall()
w = csv.writer(sys.stdout, quoting=csv.QUOTE_ALL)
w.writerow(["reject_reason", "n"]); w.writerows(rows)
'
```

### `send-raw` — Arbitrary notification
**Manual only.**

```bash
docker compose exec scheduler python3 scripts/notify.py send-raw "My Title" "My message body"
```

Useful for testing ntfy connectivity or sending one-off alerts from other scripts.

### Schedule summary

| Notification | Schedule |
|---|---|
| `apply-reminder` | 06:00 daily |
| `daily-stats` | 06:15 daily |
| `health-check` | 07:00 daily |
| `feedback-review` | Sunday 08:00 |
| `send-raw` | Manual only |

### Customizing

Notification modules live in `src/findajob/notifications/`. To add a new notification:

1. Add a module in `src/findajob/notifications/` (follow the pattern of existing ones).
2. Register the subcommand in `src/findajob/notifications/cli.py`.
3. Add the kind to `NOTIFICATION_KINDS` in `src/findajob/notifications/ntfy.py`.
4. Add a new entry to `ops/scheduled-jobs.yaml` if scheduled.

---

For a full reference of all pipeline scripts and their arguments, see `## Scripts Reference` in `CLAUDE.md`.
