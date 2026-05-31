# Getting your API keys

**You need one account to get started: OpenRouter.** That single account
covers every AI call findajob makes — scoring, resume tailoring, cover
letters, the onboarding interview, all of it. There's no subscription; you
pay only for what you use.

**RapidAPI is optional.** It lets findajob search LinkedIn and Indeed
directly. findajob works without it, just with fewer job sources — it will
still pull from company career portals (Greenhouse, Ashby, Lever) and Gmail
job-alert emails.

| Key | What it does | Cost | Required? |
|---|---|---|---|
| **OpenRouter** | Powers all AI features — scoring, briefings, resume tailoring, cover letters, outreach drafts, the onboarding interview | Pay-as-you-go, no monthly minimum. See [`cost.md`](cost.md) for monthly estimates by usage profile. | **Yes** |
| **RapidAPI** | Searches LinkedIn and Indeed for jobs matching your profile | Free plan available (no credit card). See quota details below. | No — you can skip it and add it later |

> **Onboarding also asks for two optional credentials** — a Gmail app
> password (for job-alert emails + rejection detection) and your LinkedIn
> `Connections.csv` (for personalized outreach). You can skip both.
> See [`gmail.md`](gmail.md) and the LinkedIn step in
> [the getting-started README](README.md).

---

## OpenRouter

OpenRouter is the service that handles every AI call findajob makes. One
account covers everything — you don't need separate accounts for each AI
model findajob uses.

### Steps

1. Go to <https://openrouter.ai>.
2. Sign up using Google, GitHub, or email (sign-in / sign-up controls are
   top right).
3. Add credit at <https://openrouter.ai/credits>.
   **$10 minimum** covers the onboarding interview ($3–6);
   **$20–$30** is a comfortable starter for a typical first month after
   that. See [`cost.md`](cost.md) for monthly estimates by usage profile.
4. Create an API key at <https://openrouter.ai/settings/keys>. Give it a
   name like "findajob" so you can find it later. Copy the key — it starts
   with `sk-or-v1-` and is shown to you only once. (If you lose it, create
   a new one at the same URL — keys are free to mint.)
5. Paste it into findajob's onboarding page in the **OpenRouter API key**
   field.

### What findajob does with it

findajob routes its AI features across several models — each task uses the
model best suited to it:

- **Job scoring** → DeepSeek v3.2
- **Briefings, cover letters, resume tailoring, outreach, interview prep, recruiter critique** → Claude Opus 4.8
- **Onboarding interview, speculative role synthesis** → Claude Sonnet 4.6
- **Network analysis, resume change review** → Gemini 3 Flash
- **Company research, company discovery, fit analysis** → Perplexity Sonar Reasoning Pro
- **Candidate-led speculative briefing** → Perplexity Sonar Deep Research

### Cost expectations

See [`cost.md`](cost.md) for a breakdown of monthly spend by usage
profile (hosting-only, triage-only, light user, active user, power
user, sprint mode), plus per-call estimates and the one-time
onboarding cost.

OpenRouter shows you a live spend breakdown at <https://openrouter.ai/activity>.
You can also cap monthly AI spend at any dollar amount you choose —
visit `/settings/spend-ceiling/` on your running stack.

---

## RapidAPI (optional)

RapidAPI lets findajob search LinkedIn and Indeed directly. It's optional
— findajob works without it, just with fewer job sources.

If you skip it, findajob still pulls from:
- Company career portals (Greenhouse, Ashby, Lever) via direct feeds
- Gmail job-alert emails from LinkedIn and Indeed (see [`gmail.md`](gmail.md))

You can leave the field blank during onboarding and add it later at
`/onboarding/?mode=rerun`.

### How to sign up

1. Go to <https://rapidapi.com> and create a free account (no credit card
   required for the free plan).
2. Visit the [jobs-api14 listing](https://rapidapi.com/Pat92/api/jobs-api14)
   and click **Subscribe to Test**. Pick the **BASIC** plan (150 free
   requests/month, no card required).
3. On the same page, find your **X-RapidAPI-Key** in the right-hand code
   sample panel. Copy that value.
4. Paste it into findajob's onboarding page in the **RapidAPI key** field.
   The onboarding picker runs a one-request live test before saving the key.

One RapidAPI account and one key covers every job-search feed findajob
supports — you don't need separate keys per feed.

### Supported feeds

findajob ships with four RapidAPI feed options. The onboarding picker
recommends one or more based on your interview answers:

| Feed | What it searches | Free plan quota |
|---|---|---|
| **jobs-api14** (LinkedIn) | LinkedIn — broad coverage | 150 req/month |
| **jobs-api14** (Indeed) | Indeed — broad US coverage, includes full job text | Shares quota with jobs-api14 |
| **jobs-api14** (Bing) | Bing job listings | Shares quota with jobs-api14 |
| **JSearch** | LinkedIn + Indeed + Glassdoor + ZipRecruiter | 200 req/month |

> **Activating feeds.** The onboarding picker writes your chosen feeds to
> `config/active_sources.txt` automatically. To change feeds later, visit
> `/settings/active-sources/` or re-run onboarding at `/onboarding/?mode=rerun`.

> **PRO/ULTRA/MEGA tiers.** LinkedIn, Indeed, and Bing share the same
> per-account quota — upgrading raises the limit for all three feeds
> together (PRO: 20,000 req/month shared for jobs-api14; 10,000/month for
> JSearch).

### Adding support for a new feed

Need a feed that's not in the supported set? Each RapidAPI vendor has a
different response format, so each feed needs a custom adapter. File an
issue at <https://github.com/brockamer/findajob/issues> or see
`CLAUDE.md` § "Source Adapters are Pluggable" for the contributor
walkthrough.

<details>
<summary>Advanced</summary>

### Legacy per-adapter env var fallbacks

Older deployments may have set per-adapter keys instead of the single
canonical key. These still work as fallbacks:

| Adapter | Canonical key | Legacy fallback |
|---|---|---|
| jobs-api14 (LinkedIn / Indeed / Bing) | `RAPIDAPI_KEY` | `JOBS_API14_KEY` |
| JSearch | `RAPIDAPI_KEY` | `JSEARCH_API_KEY` |

New onboardings write only `RAPIDAPI_KEY`. If you're on an older
deployment that has `JOBS_API14_KEY` or `JSEARCH_API_KEY` in `data/.env`,
those will continue to work — no migration required.

### Pagination tuning (PRO tier only — skip on the free BASIC plan)

Both jobs-api14 and JSearch let you trade quota for yield. Each
additional page uses one additional billed RapidAPI request.

**`JobsApi14Adapter`** (LinkedIn, opaque-token pagination) — set
`JOBS_API14_MAX_PAGES=N` in `data/.env`:

| Tier | Recommended | Monthly cost (5 queries × N pages × 30 days) |
|---|---|---|
| BASIC (150 req/mo) | leave at 1 | 150 / 150 = 100% — no headroom |
| PRO (20,000 req/mo) | 3–5 | 450 / 20,000 = 2.25% (N=3); 750 / 20,000 = 3.75% (N=5) |

Clamped to `[1, 20]`.

**`JSearchAdapter`** (server-side pagination, billed as N units per
query) — set `JSEARCH_NUM_PAGES=N` in `data/.env`:

| Tier | Recommended | Monthly cost (5 queries × N pages × 30 days) | Yield |
|---|---|---|---|
| BASIC (200 req/mo) | leave at 1 | 150 / 200 = 75% | ~10 jobs/query |
| PRO (10,000 req/mo) | 3 | 450 / 10,000 = 4.5% | ~27 jobs/query (~2.7x) |

Clamped to `[1, 10]`.

Restart the stack after editing `data/.env`. The live-test in
`/onboarding/feed-config/` always runs single-page regardless of these
settings — it's a connectivity check, not a yield benchmark.

</details>

---

## Replacing a key later

To rotate any key:

1. Generate a new key at the provider (using the steps above).
2. Visit `/onboarding/?mode=rerun` on your findajob instance.
3. Paste the new key. The injector backs up the existing `data/.env`
   under `.backups/{UTC-stamp}/` and writes the new value in place.

The pipeline picks up the new key on its next scheduled run; no
restart needed.

If you manage your own stack (self-hosted or on Fly), you can also edit
`data/.env` directly:
- **Docker self-host:** SSH into the host and edit `data/.env` — see
  `docs/operations/install-docker.md` under "Operating an existing stack."
- **Fly.io:** `fly secrets set OPENROUTER_API_KEY=sk-or-v1-... --app findajob-<your-handle>`
  (Fly redeploys automatically when secrets change).

---

## Other credentials onboarding asks for

Two credentials are collected outside the API-keys form:

- **Gmail app password** — for ingesting job-alert emails and
  auto-detecting rejection emails from employers. Optional. See
  [`gmail.md`](gmail.md) for the setup walkthrough.
- **LinkedIn `Connections.csv`** — for network-based outreach drafting.
  Optional. See the LinkedIn step in
  [the getting-started README](README.md).

---

## Sources

- OpenRouter — <https://openrouter.ai/docs/api-reference/authentication>
- RapidAPI keys overview — <https://docs.rapidapi.com/docs/keys-and-key-rotation>
- jobs-api14 listing — <https://rapidapi.com/Pat92/api/jobs-api14>
- JSearch listing — <https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch>
