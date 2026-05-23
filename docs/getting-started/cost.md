# Cost expectations

Before signing up, you should know — with reasonable confidence — what running findajob will cost you per month. Two providers bill you directly: **Fly.io** for hosting, **OpenRouter** for LLM calls. There's no findajob bill, no middleman, no usage-based markup. You can cap the LLM spend at any dollar amount you choose.

This page answers four questions:

1. [What does Fly hosting cost?](#fly-hosting)
2. [What does the LLM spend look like?](#llm-spend)
3. [How do I cap the LLM spend?](#capping-the-spend)
4. [What makes the LLM spend go up?](#what-drives-cost-up)

And a fifth, for the curious: [Where do these numbers come from?](#where-the-numbers-come-from)

---

## TL;DR

| Scenario | Monthly cost |
|---|---|
| Hosting only, no LLM work | ~$4 (Fly only) |
| Triage-only (scores incoming jobs, no preps) | ~$13–25 (Fly + $9–21 LLM) |
| Light user — 1 prep per week | ~$17–30 |
| Active user — 3 preps per week | ~$25–40 |
| Power user — 1 prep per day + interview prep | ~$45–80 |
| Sprint mode — 3–5 preps per day | ~$130–180 |

Onboarding is a one-time $3–6 LLM cost, separate from monthly.

These are real-data ranges from one production instance over a 10-day window in May 2026. Different patterns (different ingest volume, different role mix, heavy speculative use) will sit outside these ranges. See [Where the numbers come from](#where-the-numbers-come-from) for methodology, caveats, and sample-size honesty.

---

## Fly hosting

Roughly **$3–5 per month** on the defaults that `ops/fly.toml.example` ships with:

| Item | Default sizing | Rate | Monthly |
|---|---|---|---|
| `shared-cpu-1x` 1 GB machine, always-on | 1 machine | ~$3.19/mo | ~$3.19 |
| Volume | 8 GB | $0.15 / GB-month | ~$1.20 |
| Bandwidth | low-egress per-tenant | free tier covers | ~$0 |
| **Total** | | | **~$3–5** |

The machine has to stay always-on because supercronic runs the daily triage cron and the web UI must answer requests. `auto_stop_machines = "off"` in `fly.toml.example` is deliberate.

Verify current Fly pricing at <https://fly.io/docs/about/pricing/>. Volume snapshots are billed separately starting January 2026 — if you take snapshots for backup, check that line item too.

---

## LLM spend

LLM cost depends almost entirely on **how much you use findajob to prep applications**. Daily triage (the LLM scoring of every incoming job) is cheap because scoring is volume-driven but each score is $0.001–$0.003. Prep operations (writing a tailored resume, a cover, a briefing) are more expensive — $0.20–$0.30 each — but only happen when you flag a job for prep.

### Per-operation costs

| Operation | Typical cost / call | When it fires |
|---|---|---|
| `score` (triage scoring) | $0.0015–$0.003 | Every job ingested, automatic |
| `briefing_writer` | $0.20–$0.30 | Once per prep |
| `resume_tailor` | $0.25–$0.30 | Once per prep |
| `cover_letter_writer` | $0.25–$0.30 | Once per prep |
| `fit_analyst` | $0.05–$0.10 | Once per prep |
| `recruiter_critic` | $0.03–$0.05 | Once per prep |
| `outreach_drafter` | $0.04–$0.08 | Once per prep, only if connections.csv uploaded |
| `company_researcher` | $0.02–$0.05 | Once per company (cached across preps at that company) |
| `resume_change_reviewer` | <$0.01 | Once per prep |
| `interview_prep` | $0.20–$0.40 | When you flag a job as Interviewing |
| `candidate_led_briefing` | $0.80–$1.20 | Once per speculative cold-outreach request |
| `speculative_roles_synth` | $0.05–$0.15 | Once per speculative cold-outreach request |

### Typical totals

**One full prep run** (briefing + resume + cover + fit + critic + outreach + reviewer + company-research-if-uncached) sums to **~$0.80–$1.20**.

**Daily triage cost** depends on how many jobs are ingested. A typical day pulls 200–400 jobs through the scorer:

- Low (200 jobs scored, no preps): ~$0.30–$0.50/day → $9–$15/month
- Active (300 jobs scored, no preps): ~$0.50–$0.70/day → $15–$21/month

### Monthly scenarios

| Pattern | Triage cost | Prep cost | Monthly LLM |
|---|---|---|---|
| Triage-only | $9–$21 | $0 | $9–$21 |
| Triage + 1 prep / week | $9–$21 | ~$4 | $13–$25 |
| Triage + 3 preps / week | $9–$21 | ~$12 | $21–$33 |
| Triage + daily prep + 2 interview preps / month | $9–$21 | ~$30 + ~$0.6 | $40–$52 |

**Onboarding** is a one-time charge of **$3–$6** for the in-app interview. It runs once when you first deploy. Re-running onboarding (rare) would charge again.

---

## Capping the spend

findajob has a built-in **monthly spend ceiling** you can configure in the web UI at `/settings/spend-ceiling/`. The dashboard nudges you to set one on first visit if it's unset.

When the running monthly OpenRouter total crosses your configured cap:

- **Triage continues to ingest; scoring halts.** New jobs keep landing in your database from every configured source — ingest itself makes no LLM calls. But every score call fails until the cap is raised or the month resets. Unscored jobs sit at `enriched` stage and re-enter the scoring queue automatically on the next triage after LLM calls resume. No data loss, but no scored shortlist either until then.
- **New prep, interview-prep, and speculative-ingest requests are refused** with an HTTP 402 PaymentRequired response. The "Flag for Prep" button shows a friendly error pointing you at `/settings/spend-ceiling/` to adjust the cap or wait until the next calendar month.
- **An in-flight prep that crosses the cap mid-run is aborted.** A full prep is a sequence of 6+ LLM calls; if the cap is crossed between calls, the next call raises and the prep stops. The job's stage stays at `prep_in_progress` until the next watchdog cycle (≤60 min) rolls it back to `scored` — within that window, clicking "Flag for Prep" again will be refused by the handler's idempotency guard. Once the watchdog reaps, you can re-flag the job after raising the cap or when the next month rolls over. Partial artifacts (whatever was generated before the cap was hit) remain on disk in the prep folder under `companies/` and are cleaned up automatically the next time you flag the same job for prep.
- The top-nav cost chip turns amber at ≥90% of cap, rose at ≥100%.

Reset is automatic at the start of each calendar month in your stack's local timezone — no action required.

The cap acts on the *next* LLM call: brief overshoots are possible (a call already in flight when the threshold is crossed completes and gets billed). Set the cap with a small buffer below your true budget if you want headroom for those overshoots.

### Setting a reasonable cap

The Settings page shows a suggested ceiling based on your expected weekly prep cadence. The recommendation uses a per-prep cost biased above the observed median so the cap covers roughly the upper-quartile of prep runs plus a small buffer for non-prep operations (interview prep, candidate-led briefing). The values it suggests align with the [Monthly scenarios](#monthly-scenarios) table:

- 1 prep / week → ~$22
- 3 preps / week → ~$32
- 1 prep / day → ~$69
- Heavier than that → either set a ceiling near the upper bound of your scenario row, or disable the cap and watch the cost chip in the top nav

You can change the cap any time; resetting it doesn't lose state. If you'd rather not have a hard cap at all, the cost chip in the top nav shows running month-to-date spend regardless.

---

## What drives cost up

Three things move LLM spend most:

1. **Manual preps.** Every "Flag for Prep" click costs ~$1. Cheap individually, accumulates with volume. The dominant lever on monthly spend.
2. **Triage cadence and ingest volume.** Default triage runs once daily at 00:00 stack-time. Adding more frequent triage, more search queries, or more job sources scales scoring cost proportionally — but each individual score is so cheap that going from 200 to 500 jobs/day only adds ~$3/month.
3. **Speculative cold-outreach.** Each speculative ingest runs `candidate_led_briefing` (~$1) plus a `speculative_roles_synth` step. Used sparingly is fine; used as the primary workflow gets expensive.

Cost-reducing levers:

- **Tune your prefilter.** Jobs that match `prefilter_rules.yaml` deny patterns are rejected before scoring — no LLM call. A well-tuned prefilter can cut triage cost in half. Edit it in the web UI at `/config/files/prefilter_rules.yaml`.
- **Adjust scoring model.** Default is a small/cheap model. You won't drop below $0.0015/score without losing quality, but you can verify your model choice on the `/stats/` page.
- **Skip preps you won't apply to.** The flagged-for-prep queue isn't a bookmark list. Each prep is ~$1.

---

## Where the numbers come from

Every dollar in the ranges above is grounded in `cost_log` — findajob's per-call accounting table, which writes `cost_log.cost_usd` directly from OpenRouter's `response.usage.cost` field on every LLM call. No heuristic, no markup, no calibration: this is what OpenRouter actually charged.

**Source data:** a 10-contiguous-day window in early May 2026 from one production findajob instance. Total in the window: ~2,750 calls across 12 distinct operations, ~40 full prep runs. The averages and ranges in the tables above are computed from that window. The lowest-cost day in the window had ~280 scores but only $0.62 of LLM spend — kept in the range floor but not the central estimate. The cause wasn't investigated; treat it as natural variance, not a target.

**Caveats:**

- **n = 1 instance.** All numbers come from one running stack. A user with a different job-search shape — different ingest volume, different role mix, heavy speculative use, different scoring model — will see different totals.
- **The window was prep-active.** Roughly 4 prep runs/day across the 10-day window puts the data closer to the "sprint mode" row of the TL;DR table than the "active user" row. The per-operation `$/call` rates are properties of the prompt + model combination and travel cleanly to any instance; the monthly scenarios in the table above extrapolate per-pattern, not from the window's full monthly run-rate.
- **OpenRouter model swaps shift costs.** When a model deprecates or a new one drops in, prompt cache hits and per-token rates can move materially. Re-check the cost chip and `/stats/` after major model migrations.
- **Prompt caching is on by default.** OpenRouter's prompt cache discounts repeated system-prompt tokens at ~10% of base rate. Without caching, the numbers above would be 2–3× higher. Don't disable caching unless you have a specific reason.

For the operator-tier numbers (per-tenant Fly cost across multiple instances, snapshot pricing, etc.), see [`../operations/fly-deploy.md#cost-guide`](../operations/fly-deploy.md#cost-guide).

---

## See also

- [Capping the spend](#capping-the-spend) — set a monthly limit
- [`install-fly.md`](install-fly.md) — Fly install runbook (links back to this doc)
- [`install-docker.md`](install-docker.md) — self-host install (same LLM cost structure)
- `/stats/` in your running stack — real-time cost data for your own instance
