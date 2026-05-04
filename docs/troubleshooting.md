# Troubleshooting

When something's wrong, read the logs first, check the health check second, then work through the symptom index.

If you're just getting started, see [`setup/README.md`](setup/README.md). If you're looking for the daily workflow, see [`usage.md`](usage.md).

---

## Reading the logs first

### Live pipeline activity

```bash
docker compose logs scheduler --tail 200 -f
```

Shows everything the container has done recently: triage runs, scoring, watchdog wake-ups, prep subprocesses, ntfy pings.

### Structured event log

One JSON object per line at `state/logs/pipeline.jsonl`. Every major pipeline action writes a structured event. From the host:

```bash
jq -r '.event' state/logs/pipeline.jsonl | sort -u
```

Lists every event type ever emitted. Common ones: `pipeline_complete`, `pipeline_started`, `jobs_fetched`, `scoring_complete`, `manual_job_ingested`, `prep_started`, `prep_complete`, `prep_failed`, `watchdog_run`.

To find events of one type, last 25 hours:

```bash
jq -c 'select(.event == "pipeline_complete")' state/logs/pipeline.jsonl | tail -5
```

### Health check

Runs inside the container:

```bash
docker compose exec scheduler /app/scripts/notify.py health-check
```

Silent output means healthy. Any line of output names a thing that's off. See §"Health check alert reference" below for what each alert means.

---

## Symptom index

### "No new jobs appearing on the Dashboard"

Triage runs at 00:00 local time and writes a `pipeline_complete` event on success.

```bash
jq -c 'select(.event == "pipeline_complete")' state/logs/pipeline.jsonl | tail -3
```

**No recent events?** Scheduler isn't firing — check `docker compose logs scheduler` for supercronic output, verify the compose service is up.

**Events present but no new jobs?** A source is silent. The health check's "silent feed failure" alert tells you which one. Causes by source:

- **RapidAPI (`jobsapi`)**: key missing, quota exhausted, or `config/jsearch_queries.txt` empty/malformed.
- **Gmail**: OAuth token expired → re-authenticate inside the container: `docker compose exec scheduler /app/scripts/gmail_auth.py`. OAuth client must be "Desktop app" type, not TV/limited-input.
- **Greenhouse**: `config/feed_urls.txt` slug 404s when the company removes a careers page — prune dead slugs.

### "Jobs are scoring 0 or not scoring at all"

First, smoke-test aichat-ng from inside the container:

```bash
docker compose exec scheduler aichat-ng -- hi
```

**No response or error?** Either the OpenRouter API key is missing/expired, the account has no balance, or the aichat-ng config is wrong. Check `state/aichat_ng/config.yaml` inside the bind-mount.

**Response works but jobs score null?** Health check will report `INFO: N jobs scored None`. Common cause: LLM timeout — the scoring loop falls back to `None` when the LLM errors. Retry by rerunning triage.

**Score 5/6 for jobs without a JD is normal** — Stage 2 of the prefilter deterministically scores 5 when no JD is present, because there's nothing for the LLM to score. Not a bug.

### "Prep is stuck in 'Prep in Progress'"

Prep normally finishes in 3–5 minutes. If a job stays in `prep_in_progress` beyond 60 minutes, the watchdog rolls it back to `scored` automatically.

```bash
jq -c 'select(.event == "watchdog_run")' state/logs/pipeline.jsonl | tail -5
```

**Cause of the stuck prep** is usually in `pipeline.jsonl` — look for a `prep_failed` or `prep_validation_failed` event near the `prep_started` entry:

```bash
jq -c 'select(.event | test("prep"))' state/logs/pipeline.jsonl | tail -10
```

Common causes: Anthropic API rate limit, Perplexity rate limit, pandoc conversion failure on the `.docx` step.

### "The `/ingest/` web form isn't saving jobs"

Web form at `http://<your-host>:${FINDAJOB_MATERIALS_PORT}/ingest/` is the primary manual-ingest path (replaced the Google Form in #62).

```bash
jq -c 'select(.event | test("ingest"))' state/logs/pipeline.jsonl | tail -5
```

Look for `manual_job_ingested` (success) or `ingest_skipped` (duplicate fingerprint). Common causes of silent failure: `pipeline.db` is read-only (fix mount permissions), the URL is unparsable (malformed or blocked), the DB path in the container doesn't match the bind mount.

### "Gmail isn't ingesting job alerts"

Gmail uses OAuth2 with a Desktop-app client. Tokens expire silently after long idle periods or if scopes change.

```bash
jq -c 'select(.event | test("gmail"))' state/logs/pipeline.jsonl | tail -5
```

Look for `gmail_fetched` dropping to zero or error events. Re-authenticate with:

```bash
docker compose exec scheduler /app/scripts/gmail_auth.py
```

Follow the device-flow prompts. OAuth client type must be **Desktop** — TV / limited-input clients have a different scope set and Google rejects job-alert scopes on those.

### "The container won't start"

```bash
docker compose logs scheduler
```

Typical failures on first boot:

- **Mount path mismatch**: `compose.yaml` references a `state/` subdirectory that doesn't exist. Create the missing dir and restart.
- **Permission denied writing to mount**: `chown -R` the `state/` tree to the UID the compose file uses (defaults to your shell UID).
- **Image not pulled**: `docker compose pull` then `docker compose up -d`.
- **Port collision**: `${FINDAJOB_MATERIALS_PORT}` already in use — change in `.env` and restart.

---

## Health check alert reference

`notify.py health-check` fires one line per issue. Paraphrased alerts and what they mean:

| Alert | What triggered it | Typical fix |
|---|---|---|
| **ERROR: triage was terminated (SIGTERM)** | Nightly triage was killed mid-run, usually by supercronic timeout | Investigate the job taking too long; raise the container-level timeout |
| **WARN: pipeline_complete not seen in last 25h** | Triage didn't complete overnight | Check scheduler logs; verify supercronic is running |
| **WARN: watchdog_run not seen in last 25h** | The 10-min watchdog cron never fired | Same as above |
| **ERRORS: N error events in log** | Any event with `error` / `exception` / `failed` fired | Read the alert for the first three; grep `pipeline.jsonl` for more |
| **INFO: N jobs scored None (likely LLM timeout)** | Scoring LLM errored mid-batch | Usually transient; re-triage if it persists |
| **WARN: N source(s) returned 0 jobs despite producing jobs in last 7d** | A feed silently broke | Check the named source — API key, quota, config file |
| **WARN: low memory — N MB available** | Container is memory-starved | Increase host RAM, or reduce parallelism in config |
| **WARN: high swap usage — N/M MB used** | Swap over 50% utilized | Same — investigate memory pressure |
| **WARN: N null-score jobs in manual_review (scorer failure — check aichat-ng)** | Jobs were shunted to review because scoring returned null | Smoke-test aichat-ng; check OpenRouter |
| **WARN: N real-flag jobs in manual_review backlog** | Queue growing past threshold | Triage the Review tab; tune profile if scorer is flagging too much |
| **WARN: N target-company jobs scored 3–6 in last N days (potential mis-scores)** | Scorer rated Tier 1 company jobs low | Review each; if you disagree, add to profile's Tier 1 list and rescore |

---

<details>
<summary><strong>For advanced users: audit_log, manual re-triage</strong></summary>

### Reading `audit_log` in `pipeline.db`

Every stage transition writes to `audit_log`. The table is useful for reconstructing what happened to a specific job.

```bash
docker compose exec scheduler sqlite3 /app/data/pipeline.db \
  "SELECT timestamp, job_id, old_value, new_value, actor FROM audit_log ORDER BY timestamp DESC LIMIT 20"
```

For one job by fingerprint:

```bash
docker compose exec scheduler sqlite3 /app/data/pipeline.db \
  "SELECT a.timestamp, a.old_value, a.new_value, a.actor
     FROM audit_log a JOIN jobs j ON a.job_id = j.id
    WHERE j.fingerprint = '<fp>'
    ORDER BY a.timestamp"
```

### Manually re-triaging

```bash
docker compose exec scheduler /app/scripts/triage.py
```

Fetches from every source, dedupes, scores, writes. Takes 5–15 minutes depending on how many new listings surfaced. Add `--dry-run` if the script supports it in your version (check `--help`).

### Rescoring with a changed profile

After editing `profile.md`, rescore existing rows:

```bash
docker compose exec scheduler /app/scripts/rescore_all.py
```

Only rescores jobs still in `scored` or `manual_review` — won't touch jobs you've already actioned on.

</details>
