# Getting started

This page is a map. Read it top to bottom; each numbered link is a stop on the way to a running pipeline. The full walkthrough is spread across a few files on purpose — each one focuses on a single thing you have to do once.

Allow 45–90 minutes end-to-end. Most of that is waiting for API key approvals.

---

## 1. Prerequisites → [`prerequisites.md`](prerequisites.md)

What you need to have before touching the stack: a Linux host, Docker + Compose, a handful of API keys (LLM providers, RapidAPI for LinkedIn + Indeed, Gmail OAuth if you want job alerts ingested), and an ntfy topic for push notifications. The linked doc walks through each one with the sign-up URL and the minimum plan/quota you'll need.

## 2. Install → [`install-docker.md`](install-docker.md)

Create `/opt/stacks/findajob-<you>/`, drop `compose.yaml`, start the container. The guide explains each mount, each env var, and what happens on first boot. Docker is the only supported install path.

## 3. Configure → in-app interview (or manual)

After the container is up, open `http://<your-host>:${FINDAJOB_MATERIALS_PORT}/` in a browser. A fresh stack 307s straight into `/onboarding/` — no need to know to navigate via Tools → Onboarding.

The page presents two steps:

**Step 1 — API keys.** Collects your OpenRouter (required), RapidAPI (optional, for LinkedIn / Indeed search), and Google (optional, for the RAG embeddings) keys. The sign-up walkthrough is at [`api-keys.md`](api-keys.md). Keys live only in your stack's `data/.env`; findajob never sees them server-side.

**Step 2 — Run the interview.** Once Step 1 is saved, a "Start interview" button enables. Clicking it opens a chat surface inside findajob where you have a structured 60–90 minute conversation with an LLM (Claude Sonnet 4.6, billed against your own OpenRouter key). Server-side persistent: close the tab anytime and the index page surfaces a "Resume your interview" affordance. When the LLM finishes emitting your config blocks, a green Finalize button appears — click it, and findajob writes your files, runs initial company discovery, and lands you on the dashboard. No copy-paste step.

Cost runs ~$3-6 per onboarding even with prompt caching enabled (the system prompt is cached server-side at OpenRouter so subsequent turns are billed at ~10% of the system tokens, but voice-samples emission and the cumulative chat history dominate the bill in long interviews).

**Manual:** Skip the interview and edit the config files by hand. See [`configure.md`](configure.md) for the file-by-file walkthrough — which fields matter most, which have sensible defaults, and which you can safely leave blank.

Once onboarding is done, the web UI unlocks `/board/`, `/materials/`, `/stats/`, and `/config/`. The in-browser editor at `/config/` is how you edit these same files later without shelling in — it's the primary surface for ongoing tweaks.

## 4. Verify

Run the health check from inside the container:

```bash
docker compose exec scheduler /app/scripts/notify.py health-check
```

**Expected:** no output (silent = healthy), or a list of `WARN` / `ERROR` lines pointing at what's not wired yet. Each alert is documented in [`../troubleshooting.md`](../troubleshooting.md). A freshly-started container with no triage run yet will fire `WARN: pipeline_complete not seen in last 25h` — that's normal; it clears after the first scheduled triage at 00:00 local time.

## 5. Gmail job-alert ingestion (optional) → [`gmail.md`](gmail.md)

If you want LinkedIn (and other) job-alert emails ingested automatically,
set up the Gmail IMAP integration. The guide walks through generating a
Google app password and wiring it into `/config/gmail/`. The pipeline runs
without it — Greenhouse / Ashby / Lever and RapidAPI LinkedIn search still
cover most ingestion volume.

## 6. Restore from backup → [`restore.md`](restore.md)

If you have a backup mechanism in place (sibling-host tarballs, S3 sync, or
similar), you also need a documented restore procedure — and you need to have
exercised it at least once. The guide walks through the layout a backup tarball
must capture, the step-by-step restore on a fresh stack, and the verification
gate that confirms the restored stack is operationally identical to the source.
Re-run the exercise on every release that touches schema, onboarding, mounts,
or the entrypoint.

## 7. What's next

- [`../usage.md`](../usage.md) — the daily workflow: web UI tab by tab.
- `/config/` in the web UI — edit `profile.md`, `prefilter_rules.yaml`, `jsearch_queries.txt`, and the role prompts without touching disk.
- Tuning (writing an effective `profile.md`, prefilter calibration, scoring feedback) — tracked in [issue #219](https://github.com/brockamer/findajob/issues/219); the guide ships after the scorer-prompt and excluded-employers work land.
