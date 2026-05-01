# Setup

This page is a map. Read it top to bottom; each numbered link is a stop on the way to a running pipeline. The full walkthrough is spread across a few files on purpose — each one focuses on a single thing you have to do once.

Allow 45–90 minutes end-to-end. Most of that is waiting for API key approvals.

---

## 1. Prerequisites → [`prerequisites.md`](prerequisites.md)

What you need to have before touching the stack: a Linux host, Docker + Compose, a handful of API keys (LLM providers, RapidAPI for LinkedIn + Indeed, Gmail OAuth if you want job alerts ingested), and an ntfy topic for push notifications. The linked doc walks through each one with the sign-up URL and the minimum plan/quota you'll need.

## 2. Install → [`install-docker.md`](install-docker.md)

Create `/opt/stacks/findajob-<you>/`, drop `compose.yaml`, start the container. The guide explains each mount, each env var, and what happens on first boot. Docker is the supported install path; the [legacy native install](install-linux.md) remains in-repo as a fallback.

## 3. Configure → three paths

After the container is up, open `http://<your-host>:${FINDAJOB_MATERIALS_PORT}/` in a browser. A fresh stack 307s straight into `/onboarding/` — no need to know to navigate via Tools → Onboarding. The page presents Step 1 (collect your three API keys) and then Step 2, where you pick how to onboard:

**In-app interview (default for self-deploy):** Step 1 collects your OpenRouter, RapidAPI, and Google API keys (sign-up walkthrough at [`api-keys.md`](api-keys.md)); Step 2 enables a "Start interview" button that runs the entire interview inside findajob as a chat — no tab-switching, no copy-paste. Server-side persistent: close the tab and reload the page to see a "Resume your interview" affordance. The chat is funded by your own OpenRouter key from Step 1.

**Paste-back (the fallback):** For environments that can't reach OpenRouter directly, or if you'd rather run the interview in claude.ai / ChatGPT / Gemini in another tab and paste the emission. Same Step 1 keys collection; Step 2's "I'll run the interview elsewhere and paste back" section hands you the prompt and a paste box. Same config files written either way.

**Operator-funded fallback (optional, for `findajob-test` and operator-deployed-for-tester scenarios):** When the operator sets `OPENROUTER_OPERATOR_KEY` on the stack, the in-app affordance enables before Step 1 keys are collected — useful for the operator's own dogfood instance or a tester whose stack the operator stood up directly. Self-deploy testers do not need this. See [`configure.md`](configure.md#openrouter_operator_key-operator-funded-fallback-optional) for cost (~$1/onboarding) and operational notes.

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
