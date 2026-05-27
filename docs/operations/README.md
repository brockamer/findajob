# Operations

> **New to findajob?** Start at [`../usage.md`](../usage.md). This page is the operator reference for running the stack by hand — triage, sync, prep, notifications — from a shell.

Day-to-day operation of the pipeline. The `ghcr.io/brockamer/findajob` image runs supercronic + uvicorn co-process inside one container. Setup: [`install-docker.md`](install-docker.md). All pipeline commands below are shown in their Docker form (`docker compose exec scheduler …`).

For cloud deployment on Fly.io as an alternative to the host compose stack — one Fly app per tenant, image runs unchanged — see [`fly-deploy.md`](fly-deploy.md).

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

Preferred: use the `/ingest/` web form at `http://<your-host>:${FINDAJOB_MATERIALS_PORT}/ingest/` to paste a URL + JD.

CLI fallback (same underlying code path as the web form):
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
1. Edit `config/roles/{role_name}.md` — this file is **baked into the image** at `/app/config/roles/`, NOT bind-mounted. Edit the file in your repo clone, rebuild the image, and `docker compose pull` to deploy.
2. The OpenRouter wrapper reads the role file (frontmatter `model:`, `temperature:`, `max_tokens:`) fresh on every invocation — no restart.

### Change a role's model
1. Edit the `model:` line in the role's frontmatter (e.g. `config/roles/job_scorer.md`).
2. Rebuild the image and `docker compose pull` to deploy. Each role pins its own model — there's no global default to override.

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

The container publishes the full web UI (board, ingest, materials viewer, config editor) on `FINDAJOB_MATERIALS_PORT` (default `8090`). Access at `http://<host>:<port>/` on your LAN or via reverse proxy (see [`internet-exposure.md`](internet-exposure.md)).

```bash
curl http://localhost:8090/healthz    # expect: ok
```

The materials sub-view (`/materials/`) renders prep-folder contents grouped by stage (staged, applied, waitlisted, rejected), Markdown inline, `.docx` as download.

---

## Log Rotation

`logs/pipeline.jsonl` rotates in-process at 5 MB. The rotator lives in `findajob.audit` (the same module that writes the file) so the behavior is identical inside and outside Docker without per-host `logrotate` setup.

On rotation, the current file is gzipped to `pipeline.jsonl.1.gz`; older backups shift up (`.1.gz` → `.2.gz`, `.2.gz` → `.3.gz`, …). The ring keeps the 6 most recent backups, so disk usage per stack is bounded at roughly 5 MB current file + 6 × ≪5 MB gzipped backups (a few MB total in practice). At the end of every rotation, any backup older than 90 days is also swept regardless of slot — on low-activity stacks where rotation happens only every few months, this prevents indefinitely-stale gzipped history from accumulating.

Two readers know about rotation:
- The log tail utility (`findajob.jsonl_tail`) reads only the trailing 1 MB of the *current* file; rotated history is intentionally not surfaced there.
- The staging green-check (`findajob.staging.green`) reads the current file plus `pipeline.jsonl.1.gz` so the 26h `pipeline_complete` predicate survives a rotation that lands between green-check runs.

If you specifically need a long-running off-host log history, ship `pipeline.jsonl` to syslog / Loki / Datadog from the host — the in-container rotation is intentionally short-window operational visibility, not durable archival. The durable transition trail lives in the `audit_log` SQLite table, not in `pipeline.jsonl`.

---

## Stack operations

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

# Recreate after editing data/.env or compose.yaml
# (config/ files are hot-reloaded — no restart needed)
docker compose up -d --force-recreate

# Pull a new image and recreate the container
docker compose pull && docker compose up -d

# Stop the stack
docker compose down
```

The scheduler inside the container is **supercronic**. Schedules are declared in `ops/scheduled-jobs.yaml` and rendered to `/app/crontab` by `scripts/render_crontab.py` at entrypoint. Per-job env overrides are documented in CLAUDE.md (`FINDAJOB_<JOB>_SCHEDULE` / `FINDAJOB_<JOB>_ENABLED`).

---

## Notifications

ntfy push notifications sent by `scripts/notify.py`. The topic is read from `NTFY_TOPIC` in `data/.env`. For initial setup (registering a topic, installing the phone app), see [`../getting-started/notifications.md`](../getting-started/notifications.md).

### `daily-stats` — Morning summary
**Schedule:** 06:15 daily (15 min after triage's completion window).

Queue depth, jobs added in the last 24h, in-progress applications (prepped/applied/interviewing), last successful triage timestamp.

### `health-check` — Pipeline health
**Schedule:** 07:00 daily.

Whether triage completed in the last 25h (looks for `pipeline_complete` event in logs), error events from `pipeline.jsonl` in the last 25h, count of `manual_review` jobs (potential scoring failures), last completion timestamp.

The 7h offset gives triage (which can take 30–60 min) comfortable headroom. Firing earlier would race the run.

### `apply-reminder` — Daily nudge
**Schedule:** 06:00 daily.

Rotating motivational quip + a reminder to submit one application today. Quips are mildly sarcastic tech-industry humor; edit `scripts/notify.py` to customize them.

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

## Scripts reference

All scripts live in `scripts/`. Diag scripts live in `scripts/diag/` and are run manually only. All scripts import `BASE` and `PANDOC` from `findajob.paths` (`src/findajob/paths.py`). Never hardcode binary paths in scripts — add overrides to `config/paths.env` instead.

Each entry below carries a **Manual run** line in the Docker form (`docker compose exec scheduler …`).

### Core pipeline scripts

#### `triage.py`
**Run by:** scheduler (daily 00:00 PT). No arguments.
**Manual run:** `docker compose exec scheduler python3 scripts/triage.py`

Fetches jobs from all sources, deduplicates, enriches with JD text, then scores with LLM in parallel (6 concurrent threads), writes to SQLite.

**Sources:**
- LinkedIn / Indeed via RapidAPI jobs-api14 + JSearch (per `config/active_sources.txt`).
- Gmail IMAP (LinkedIn job alerts, Indeed digests, recruiter messages — config at `/config/gmail/`).
- Greenhouse / Lever / Ashby JSON APIs (slugs / URLs in `config/feed_urls.txt`).

**Key events logged:** `triage_started`, `job_ingested`, `job_deduplicated`, `job_scored`, `pipeline_complete`.

#### `scripts/prep_application.py` (entry-point shim)
*Entry-point shim; implementation in `src/findajob/prep/orchestrator.py`.*

**Run by:** `POST /board/jobs/{fp}/prep` or `/regenerate` (detached subprocess); also callable manually. Args: `company title url job_id`.
**Manual run:** `docker compose exec scheduler python3 scripts/prep_application.py "Acme" "Engineer" "https://..." "<job_id>"`

Generates a full application package for one job. LLM calls run sequentially.

**Outputs (in `companies/{Company}_{AbbrevTitle}_{date}_{time}/`):**
- `tailored_resume_DRAFT.md` + `.docx`
- `tailored_resume_CHANGES.md`
- `cover_letter_DRAFT.md` + `.docx`
- `company_briefing.md` + `.docx`
- `outreach_*.txt` (one per matching contact, if any)
- `job_description.txt`
- `REVIEW_CHECKLIST.md`

After completion: updates DB to `stage=materials_drafted`, sends ntfy notification.

#### `watchdog.py`
**Run by:** scheduler (every 10 min). No arguments.
**Manual run:** `docker compose exec scheduler python3 scripts/watchdog.py`

Resets any job stuck in `stage='prep_in_progress'` for more than 60 minutes back to `scored`. Calls `findajob.actions.reset_prep_to_scored()` which writes an `audit_log` row and emits `prep_failed_reset`. Emits a `watchdog_run` summary event at the end of each run.

#### `notify.py`
**Run by:** scheduler (8 subcommands; see [Notifications](#notifications) above for the per-subcommand schedule and content).
**Manual run:** `docker compose exec scheduler python3 scripts/notify.py <subcommand>`

#### `scripts/find_contacts.py` (entry-point shim)
*Entry-point shim; implementation in `src/findajob/find_contacts.py`.*

**Run by:** `scripts/prep_application.py` (step 5). Args: `company jd_text_excerpt outdir`.
**Manual run:** `docker compose exec scheduler python3 scripts/find_contacts.py "Acme" "<jd-excerpt>" companies/<folder>`

Reads `data/connections.csv`, finds LinkedIn connections at the target company, generates personalized outreach drafts via the OpenRouter wrapper.

**Output:** `{outdir}/outreach_{FirstName}_{LastName}.txt` for each match.

**Key guard:** `company_match()` always checks `if not s or not c: return False` — blank company strings would otherwise match everything.

#### `manual_prep.py`
**Run by:** manually (when you have a job outside the pipeline). Args: optional path to a job file (default: `manual_job.txt`).
**Manual run:** `docker compose exec scheduler python3 scripts/manual_prep.py [path/to/job.txt]`

File format:
```
company: CompanyName
title: Job Title
url: https://...
---
Full JD text below this line
```

Inserts the job into DB and calls `scripts/prep_application.py` immediately.

#### `rescore_all.py`
**Run by:** manually (after model or prompt changes). No arguments.
**Manual run:** `docker compose exec scheduler python3 scripts/rescore_all.py`

Re-runs the scorer on all jobs that have JD text.

#### `rename_folders.py`
**Run by:** manually. No arguments.
**Manual run:** `docker compose exec scheduler python3 scripts/rename_folders.py`

Renames `companies/` folders from old format (`{Company}_{date}_{time}`) to new format (`{Company}_{AbbrevTitle}_{date}_{time}`). Looks up DB for title, updates `prep_folder_path` in DB. Safe to re-run.

#### `init_db.py`
**Run by:** once on new install. No arguments.
**Manual run:** `docker compose exec scheduler python3 scripts/init_db.py`

Creates `data/pipeline.db` with all tables. Safe to re-run — uses `CREATE TABLE IF NOT EXISTS`.

### Diag scripts (`scripts/diag/`)

Run manually for debugging. Not part of normal pipeline operation.

#### `debug_contacts.py`
Shows contact matching diagnostics for a batch of jobs. Useful for debugging false positive/negative company-name matches.
**Manual run:** `docker compose exec scheduler python3 scripts/diag/debug_contacts.py`
