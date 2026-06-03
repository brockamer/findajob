# Troubleshooting

When something's wrong, read the logs first, check the health check second, then work through the symptom index.

If you're just getting started, see [`getting-started/README.md`](getting-started/README.md). If you're looking for the daily workflow, see [`usage.md`](usage.md).

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

Lists every event type ever emitted. Common ones: `pipeline_complete`, `pipeline_started`, `jobs_fetched`, `scoring_complete`, `manual_job_ingested`, `prep_started`, `prep_complete`, `prep_failed`, `prep_subprocess_failed`, `prep_validation_failed`, `prep_failed_reset`, `watchdog_run`.

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

- **RapidAPI (`jobsapi`)**: key missing, quota exhausted, `config/jsearch_queries.txt` empty/malformed, or the key's account isn't subscribed to the API. A `jobsapi_403` event with `body_excerpt` containing `"not subscribed"` means the RapidAPI account that owns the key has no active subscription on the API listing. Fix: log into <https://rapidapi.com>, open the API listing (e.g. <https://rapidapi.com/Pat92/api/jobs-api14>), click **Subscribe to Test** → **BASIC** (free).
- **Gmail**: IMAP login failing → the Google app password was revoked or your 2FA settings changed. Generate a new app password and re-save it at `/config/gmail/` (see [`getting-started/gmail.md`](getting-started/gmail.md)).
- **Greenhouse**: `config/feed_urls.txt` slug 404s when the company removes a careers page — prune dead slugs.

### "Jobs are scoring 0 or not scoring at all"

First, smoke-test the OpenRouter wrapper from inside the container:

```bash
docker compose exec scheduler python3 -c "from findajob.llm.openrouter import complete; print(complete(role='job_scorer', prompt='hi', max_tokens=8).text)"
```

**HTTP 401 / 402 / no balance?** Either the OpenRouter API key is missing/expired or the account has no balance. Check `data/.env` for `OPENROUTER_API_KEY=`.

**Response works but jobs score null?** Health check will report `INFO: N jobs scored None`. Common cause: LLM timeout — the scoring loop falls back to `None` when the LLM errors. Retry by rerunning triage.

**Score 5/6 for jobs without a JD is normal** — Stage 2 of the prefilter deterministically scores 5 when no JD is present, because there's nothing for the LLM to score. Not a bug.

### "Prep is stuck in 'Prep in Progress'"

Prep normally finishes in 3–5 minutes. If a job stays in `prep_in_progress` beyond 60 minutes, the watchdog rolls it back to `scored` automatically.

```bash
jq -c 'select(.event == "watchdog_run")' state/logs/pipeline.jsonl | tail -5
```

**Cause of the stuck prep** is usually in `pipeline.jsonl` — look for a `prep_failed`, `prep_validation_failed`, or `prep_subprocess_failed` event near the `prep_started` entry:

```bash
jq -c 'select(.event | test("prep"))' state/logs/pipeline.jsonl | tail -10
```

Common causes: Anthropic API rate limit, Perplexity rate limit, pandoc conversion failure on the `.docx` step. Pandoc / `find_contacts.py` subprocess failures roll the stage back to `scored` immediately and emit `prep_subprocess_failed` (#495); the prep folder is preserved with a `.failed_subprocess` sentinel containing the cmd, returncode, and stderr tail for inspection.

### "The `/ingest/` web form isn't saving jobs"

Web form at `http://<your-host>:${FINDAJOB_MATERIALS_PORT}/ingest/` is the primary manual-ingest path.

```bash
jq -c 'select(.event | test("ingest"))' state/logs/pipeline.jsonl | tail -5
```

Look for `manual_job_ingested` (success) or `ingest_skipped` (duplicate fingerprint). Common causes of silent failure: `pipeline.db` is read-only (fix mount permissions), the URL is unparsable (malformed or blocked), the DB path in the container doesn't match the bind mount.

### "Gmail isn't ingesting job alerts"

Gmail uses IMAP with a Google app password (configured at `/config/gmail/`). Ingestion fails if the app password is revoked or your Google 2FA settings change — there is no OAuth token to expire.

```bash
jq -c 'select(.event | test("gmail"))' state/logs/pipeline.jsonl | tail -5
```

Look for `gmail_fetched` dropping to zero or error events. Fix it by generating a new Google app password and re-saving it at `/config/gmail/` — see [`getting-started/gmail.md`](getting-started/gmail.md) for the 2FA + app-password procedure.

### "Onboarding finalize fails with 402 PaymentRequired"

The onboarding interview's **Finalize** button triggers a 1-token verification call against your OpenRouter key. If the account has zero credit, that call returns 402 and the route surfaces HTTP 402 with a recovery message.

**No config files are written on a 402.** The smoke check runs before the commit step, so the interview session stays in its pre-finalize state — your captured blocks are still on the session row, and a second click runs from a clean slate.

**Recovery flow:**

1. Add prepaid credit at <https://openrouter.ai/credits>.
2. Return to the same `/onboarding/interview/{session_id}` URL (the session row is untouched).
3. Click **Finalize** again. The verification call succeeds and the config files commit atomically.

If you instead see a 400 with "OpenRouter rejected the key when we tried to verify it," that's 401 (bad key) or 429 (throttled) — fix via **Change keys** on `/onboarding/` rather than a credit top-up.

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
| **WARN: N null-score jobs in manual_review (scorer failure — check OpenRouter / pipeline.jsonl)** | Jobs were shunted to review because scoring returned null | Smoke-test the OpenRouter wrapper (above); inspect `pipeline.jsonl` for `score_failed` events |
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

### Reading a single value from `data/.env`

Use the helper instead of bash-sourcing the file:

```bash
docker compose exec scheduler /app/scripts/read_env_value.py --key NTFY_TOPIC
```

`bash -c 'set -a; . data/.env; set +a; printf %s "$KEY"'` looks tempting but silently fails on values containing shell metacharacters — a path like `/srv/example/state` is treated as a command and exits `Permission denied` while the surrounding script still appears to "succeed". The helper parses values literally and exits non-zero on missing key or missing file.

</details>
