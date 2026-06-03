# Getting started

This page is a map. Read it top to bottom; each numbered link is a stop on the way to a running pipeline. The full walkthrough is spread across a few files on purpose — each one focuses on a single thing you have to do once.

Allow ~2 hours end-to-end: ~20 minutes setup (install + API key signups), a 60–90 minute in-app onboarding interview, then ~15 minutes for the first triage to complete.

---

## 1. Install — pick a path

Three docs cover the same Fly or Docker install at different paces. Both Fly options reach the same place; pick the runbook style that matches your comfort level.

- **[`start-here-fly.md`](start-here-fly.md)** — **start here if you're not comfortable with the command line.** Step-by-step Fly.io install paced for first-timers, with screenshots at every UI decision point and "what to do if this didn't work" branches inline. Recommended for most non-engineers. ~$3–5/mo Fly hosting + LLM API spend.
- **[`install-fly.md`](install-fly.md)** — denser, reference-style runbook for the same browser-based Fly "Launch an App" install (no terminal). ~20 minutes from Fly sign-in to the first onboarding screen. Same ~$3–5/mo cost. One Fly app per person; no server to operate.
- **[`install-docker.md`](../operations/install-docker.md)** — have a Linux server? Self-host with docker-compose. Free if you already have the box; more knobs, more responsibility.

Prerequisites for each path are listed at the top of its respective runbook. Both Fly paths need a Fly.io account with billing enabled; Docker needs a Linux host running Docker 24+ with Compose v2. All three need an OpenRouter API key (and optionally a RapidAPI key + ntfy topic) — picked up inside the runbook.

## 2. Configure → in-app interview (or manual)

After the container is up, open `http://<your-host>:${FINDAJOB_MATERIALS_PORT}/` in a browser. A fresh stack 307s straight into `/onboarding/` — no need to know to navigate via Tools → Onboarding.

The page presents three steps, plus a Gmail-config gate on the way to the dashboard:

**Step 1 — API keys.** Collects your OpenRouter (required) and RapidAPI (optional, for LinkedIn / Indeed search) keys. The sign-up walkthrough is at [`api-keys.md`](api-keys.md). Keys live only in your stack's `data/.env`; findajob never sees them server-side.

**Step 2 — Run the interview.** Once Step 1 is saved, a "Start interview" button enables. Clicking it opens a chat surface inside findajob where you have a structured 60–90 minute conversation with an LLM (Claude Sonnet 4.6, billed against your own OpenRouter key). Server-side persistent: close the tab anytime and the index page surfaces a "Resume your interview" affordance. When the LLM finishes emitting your config blocks, a green Finalize button appears — click it and findajob writes your files, runs initial company discovery, and hands off to the Gmail-config gate.

**Gmail-config gate (optional).** Configure IMAP credentials so findajob can ingest LinkedIn / Indeed / etc. job-alert emails directly, plus auto-detect ATS rejection emails (#362). Save + run "Test connection" to advance, or Skip if you don't want Gmail ingestion — it's always opt-out. See [`gmail.md`](gmail.md) for the 2FA + app-password walkthrough.

**Step 3 — Upload LinkedIn connections.** The terminal step. Upload your `Connections.csv` from a LinkedIn data export — findajob uses it to find people in your network at companies a job was posted by, and drafts outreach. Skippable; the explainer on the page walks through the export procedure. Headers are validated strictly against the canonical LinkedIn shape, so if your export has a `Notes:` preamble at the top, delete those lines before uploading (the error message reminds you). On upload or Skip, you land on the dashboard.

Cost runs ~$3-6 per onboarding even with prompt caching enabled (the system prompt is cached server-side at OpenRouter so subsequent turns are billed at ~10% of the system tokens, but voice-samples emission and the cumulative chat history dominate the bill in long interviews).

**Manual:** Skip the interview and edit the config files by hand. See [`config-reference.md`](../operations/config-reference.md) for the file-by-file walkthrough — which fields matter most, which have sensible defaults, and which you can safely leave blank.

Once onboarding is done, the web UI unlocks `/board/`, `/materials/`, `/stats/`, and `/config/`. The in-browser editor at `/config/` is how you edit these same files later without shelling in — it's the primary surface for ongoing tweaks.

## 3. Verify

Run the health check against your running stack.

**If you deployed to Fly.io:**

```bash
fly ssh console --app findajob-<your-handle> --command "python3 /app/scripts/notify.py health-check"
```

**If you deployed via Docker Compose:**

```bash
docker compose exec scheduler /app/scripts/notify.py health-check
```

**Expected:** no output (silent = healthy), or a list of `WARN` / `ERROR` lines pointing at what's not wired yet. Each alert is documented in [`../troubleshooting.md`](../troubleshooting.md). A freshly-started container with no triage run yet will fire `WARN: pipeline_complete not seen in last 25h` — that's normal; it clears after the first scheduled triage at 00:00 in the stack's configured `TZ` (default `America/New_York` on Fly per [`install-fly.md` §6](install-fly.md#6-verify-and-wait-for-first-triage); editable via the `TZ` env var on Docker).

## 4. Gmail job-alert ingestion (optional) → [`gmail.md`](gmail.md)

If you want LinkedIn (and other) job-alert emails ingested automatically,
set up the Gmail IMAP integration. The guide walks through generating a
Google app password and wiring it into `/config/gmail/`. The pipeline runs
without it — Greenhouse / Ashby / Lever and RapidAPI LinkedIn search still
cover most ingestion volume.

## 5. Restore (if you have backups) → [`restore.md`](../operations/restore.md)

If you have a backup mechanism in place (sibling-host tarballs, S3 sync, or
similar), you also need a documented restore procedure — and you need to have
exercised it at least once. The guide walks through the layout a backup tarball
must capture, the step-by-step restore on a fresh stack, and the verification
gate that confirms the restored stack is operationally identical to the source.
Re-run the exercise on every release that touches schema, onboarding, mounts,
or the entrypoint.

## 6. What's next

- [`../usage.md`](../usage.md) — the daily workflow: web UI tab by tab.
- `/config/` in the web UI — edit `profile.md`, `prefilter_rules.yaml`, `jsearch_queries.txt`, and the role prompts without touching disk.
- Tuning (writing an effective `profile.md`, prefilter calibration, scoring feedback) — tracked in [issue #219](https://github.com/brockamer/findajob/issues/219); the guide ships after the scorer-prompt and excluded-employers work land.
