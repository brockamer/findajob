# Setup

This page is a map. Read it top to bottom; each numbered link is a stop on the way to a running pipeline. The full walkthrough is spread across a few files on purpose — each one focuses on a single thing you have to do once.

Allow 45–90 minutes end-to-end. Most of that is waiting for API key approvals.

---

## 1. Prerequisites → [`prerequisites.md`](prerequisites.md)

What you need to have before touching the stack: a Linux host, Docker + Compose, a handful of API keys (LLM providers, RapidAPI for LinkedIn + Indeed, Gmail OAuth if you want job alerts ingested), and an ntfy topic for push notifications. The linked doc walks through each one with the sign-up URL and the minimum plan/quota you'll need.

## 2. Install → [`install-docker.md`](install-docker.md)

Create `/opt/stacks/findajob-<you>/`, drop `compose.yaml`, start the container. The guide explains each mount, each env var, and what happens on first boot. Docker is the supported install path; the [legacy native install](install-linux.md) remains in-repo as a fallback.

## 3. Configure → three paths

After the container is up, open `http://<your-host>:${FINDAJOB_MATERIALS_PORT}/onboarding/` in a browser. There are now two interview paths and a manual escape hatch:

**In-app interview (operator opt-in, easiest for testers):** When the operator sets `OPENROUTER_OPERATOR_KEY` on the stack, `/onboarding/` shows a "Run interview here" button. Click it and the entire interview happens inside findajob as a chat — no tab-switching, no copy-paste. Server-side persistent: close the tab and reload the page to see a "Resume your interview" affordance and pick up where you left off. findajob bills the operator's OpenRouter key (~$1 per onboarding for Sonnet 4.6); your own key is collected at the end and used for the post-onboarding pipeline. See [`configure.md`](configure.md#openrouter_operator_key-in-app-onboarding-interview-optional) for opt-in details.

**Paste-back (the original path, always available):** You paste the interview prompt into ChatGPT / Claude / Gemini in another tab, answer its questions in conversation, then paste the structured output back. Works for outbound-blocked deployments and for operators who haven't opted into the in-app path. The "I already ran the interview elsewhere" section on `/onboarding/` collapses this option when the in-app path is enabled.

Both interview paths produce the same emission protocol and write the same config files (profile, resume, prefilter rules, search queries, and more), back up anything they replace, and clear the onboarding sentinel on success.

**Manual:** Edit the config files by hand. See [`configure.md`](configure.md) for the file-by-file walkthrough — which fields matter most, which have sensible defaults, and which you can safely leave blank.

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

## 6. What's next

- [`../usage.md`](../usage.md) — the daily workflow: web UI tab by tab.
- `/config/` in the web UI — edit `profile.md`, `prefilter_rules.yaml`, `jsearch_queries.txt`, and the role prompts without touching disk.
- Tuning (writing an effective `profile.md`, prefilter calibration, scoring feedback) — tracked in [issue #219](https://github.com/brockamer/findajob/issues/219); the guide ships after the scorer-prompt and excluded-employers work land.
