# Getting your API keys

findajob's onboarding asks you to supply **two API keys** for external
services. Your keys live only in your stack's `data/.env` and are never
shared with anyone else's deployment.

| Key | What findajob uses it for | Cost | Required? |
|---|---|---|---|
| **OpenRouter** | All LLM calls (scoring, briefings, resume tailoring, cover letters, outreach drafts, in-app interview) | Pay-as-you-go. No subscription, no monthly minimum; you pay only for what you use. See [`cost.md`](cost.md) for monthly expectations by usage profile. | **Yes** — pipeline cannot score or generate materials without it |
| **RapidAPI** | LinkedIn / Indeed / Bing job-search ingestion | Free BASIC tier on every supported feed (150–250 requests/month); paid tiers raise the quota | Optional — pipeline still ingests from Greenhouse / Ashby / Lever / Gmail alerts without it |

You can leave RapidAPI blank during onboarding and add it later by
re-running onboarding via `/onboarding/?mode=rerun`. OpenRouter is the
only hard requirement.

> **Onboarding also asks for two other credentials** — a Gmail app
> password (for job-alert ingestion + ATS rejection detection) and
> your LinkedIn `Connections.csv` (for network-based outreach drafting).
> Both are optional. See [`gmail.md`](gmail.md) and the LinkedIn step
> in [the getting-started README](README.md).

---

## OpenRouter

OpenRouter is a single API gateway for many LLM providers (Anthropic,
Google, DeepSeek, Perplexity, etc.). findajob uses it because the
pipeline mixes multiple model families and OpenRouter lets you pay one
bill instead of holding direct accounts with five vendors.

### Steps

1. Go to <https://openrouter.ai>.
2. Sign up using Google, GitHub, or MetaMask (the sign-in / sign-up
   controls are top right).
3. Add credit to your account at <https://openrouter.ai/credits>.
   **$10 minimum** to cover the onboarding interview ($3–6);
   **$20–$30** is a comfortable starter for a typical first month
   after that. See [`cost.md`](cost.md) for monthly expectations
   broken down by usage profile.
4. Create an API key at <https://openrouter.ai/settings/keys>. Give it
   a name like "findajob" so you can find it later. Copy the key — it
   starts with `sk-or-v1-` and is shown to you only once. (If you lose
   it, just create a new one at the same URL — keys are free to mint.)
5. Paste it into findajob's onboarding page in the **OpenRouter API key**
   field.

### What findajob does with it

findajob routes 21 role prompts to 6 OpenRouter models (and 2 Anthropic-direct models for the loose-ends onboarding helpers — those don't draw from your OpenRouter balance):

- **Scoring** (`job_scorer`) → DeepSeek v3.2
- **Briefings, cover letters, recruiter critique, resume tailoring, outreach, interview prep, voice processing** → Claude Opus 4.7
- **Onboarding interview, speculative role synthesis** → Claude Sonnet 4.6
- **Network analysis, resume change review** → Gemini 3 Flash
- **Company research, company discovery, fit analysis** → Perplexity Sonar Reasoning Pro
- **Candidate-led speculative briefing** → Perplexity Sonar Deep Research
- **Loose-ends helpers** (onboarding nav) → Claude Haiku 4.5 / Sonnet 4.6 via Anthropic direct

### Cost expectations

See [`cost.md`](cost.md) for a breakdown of monthly spend by usage
profile (hosting-only, triage-only, light user, active user, power
user, sprint mode), plus per-LLM-call estimates and the one-time
onboarding cost.

OpenRouter shows you a live spend breakdown at <https://openrouter.ai/activity>.
You can also cap monthly LLM spend at any dollar amount you choose —
visit `/settings/spend-ceiling/` on your running stack.

---

## RapidAPI

Optional — required only if you want LinkedIn / Indeed / Bing direct
search ingestion (Greenhouse / Ashby / Lever and Gmail alerts work
without it).

RapidAPI is a marketplace API gateway: you create one account, subscribe
to whichever job-search APIs you want, and use a single account-level key
for all of them. findajob supports a curated set of RapidAPI-hosted job
feeds (currently four — listed below), and the onboarding picker
recommends the right feed(s) for your field.

> **Already onboarded?** The onboarding interview's source-strategy
> briefing walks through what RapidAPI is good for and when to skip it.
> If you opted out of a RapidAPI feed during the interview you can leave
> the key field blank — the pipeline uses your other sources only.
> Re-run onboarding via `/onboarding/?mode=rerun` to revisit the
> source-strategy decision.

### How RapidAPI subscriptions work

- **One account, many APIs.** Sign up at <https://rapidapi.com> once.
  Each API on the marketplace has its own subscription with its own
  BASIC / PRO / ULTRA / MEGA pricing tiers.
- **Shared account key.** All of your subscriptions use a single
  account-level key (`X-RapidAPI-Key` header). findajob stores it once
  in `data/.env` as `RAPIDAPI_KEY` and uses it for every feed you've
  subscribed to.
- **Free BASIC tiers.** Every feed findajob supports has a free BASIC
  plan that doesn't require a credit card. The request ceiling varies
  per feed (see table below).
- **Subscriptions are per-API, not per-account.** Subscribing to one
  RapidAPI feed does not subscribe you to the others. The onboarding
  picker tells you which feed(s) to subscribe to.

### Supported feeds

findajob ships with adapters for four RapidAPI feeds. The onboarding
picker at `/onboarding/feed-config/` is the right place to choose which
one(s) to subscribe to — it reads `config/rapidapi_feeds.yaml` (a
curated allowlist) and recommends per-field based on your interview.

| Feed | Adapter name (`active_sources.txt`) | What it searches | Free BASIC tier |
|---|---|---|---|
| **jobs-api14** | `jobs-api14` | LinkedIn — broad coverage, LinkedIn-heavy | 150 req/month |
| **jobs-api14 (Indeed)** | `jobs-api14-indeed` | Indeed — broad US coverage, inline JD | shares quota with jobs-api14 |
| **jobs-api14 (Bing)** | `jobs-api14-bing` | Bing — 18 jobs/page, inline JD | shares quota with jobs-api14 |
| **JSearch** | `jsearch` | LinkedIn + Indeed + Glassdoor + ZipRecruiter | 200 req/month |

The env-var name for all four is `RAPIDAPI_KEY`. Legacy per-adapter
fallbacks (`JOBS_API14_KEY`, `JSEARCH_API_KEY`) still work for stacks
that haven't migrated.

> **Adapter names matter.** The values in the "Adapter name" column go
> in `config/active_sources.txt`, one per line. Stacks without that
> file default to `jobs-api14` (LinkedIn-only); the file is written by
> the onboarding picker for new stacks.

> **Indeed and Bing are opt-in.** To activate either, add the adapter
> name to `config/active_sources.txt`. The onboarding picker handles
> this for new stacks; existing stacks need to add the line manually.

> **PRO/ULTRA/MEGA tiers.** Indeed, Bing, and LinkedIn share the same
> per-account quota — upgrading raises the limit for all three feeds
> together (PRO: 20,000 req/month shared).

> **Note (Bing — no allowlist initially).** Unlike Indeed, Bing ships
> with no title-allowlist post-filter — all titles flow through
> currently. 18 jobs per page (vs Indeed's 20, LinkedIn's 10) on the
> same shared quota.

### What happens during onboarding

The picker at `/onboarding/feed-config/` walks you through sign-up plus
a live connection test:

1. **Recommendation.** Based on your interview answers (field, target
   companies, target geography), the picker recommends one or more feeds
   from the supported set.
2. **Sign up at RapidAPI.** A link button takes you to the recommended
   feed's listing page. Subscribe to the BASIC plan — free, no credit
   card required.
3. **Copy your key.** RapidAPI shows your account key on the listing
   page after you've subscribed.
4. **Paste + test.** Paste the key into the picker's field. The picker
   runs a one-request live test against the feed before writing the key
   to `data/.env`. If the test fails, you get a specific error message
   and the key is not written.

If you'd rather walk this manually (or you skipped onboarding entirely),
the same form is reachable at `/onboarding/feed-config/` on a running
stack.

<details>
<summary>Pagination tuning (PRO-tier only — skip on the free BASIC plan)</summary>

Both jobs-api14 and JSearch let you trade quota for yield via per-stack
env vars. Each additional page is one billed RapidAPI request (per-call
billing — empirically confirmed on the operator stack for both APIs).

**`JobsApi14Adapter`** (LinkedIn endpoint, opaque-token pagination via
`meta.nextToken`) — set `JOBS_API14_MAX_PAGES=N`:

| Tier | Recommended | Monthly cost (5 queries × N pages × 30 days) |
|---|---|---|
| BASIC (150 req/mo) | leave at 1 | 150 / 150 = 100% — no headroom |
| PRO (20,000 req/mo) | 3–5 | 450 / 20,000 = 2.25% (N=3); 750 / 20,000 = 3.75% (N=5) |

Clamped to `[1, 20]`.

**`JSearchAdapter`** (server-side pagination via the API's own
`num_pages` param — single HTTP request per query, billed as N units) —
set `JSEARCH_NUM_PAGES=N`:

| Tier | Recommended | Monthly cost (5 queries × N pages × 30 days) | Yield ratio |
|---|---|---|---|
| BASIC (200 req/mo) | leave at 1 | 150 / 200 = 75% | ~10 jobs/query |
| PRO (10,000 req/mo) | 3 | 450 / 10,000 = 4.5% | ~27 jobs/query (~2.7x) |

Clamped to `[1, 10]` (half of jobs-api14's ceiling because JSearch's PRO
quota is half).

Restart the stack after editing `data/.env`. Live-test in
`/onboarding/feed-config/` stays single-page for both adapters
regardless of these settings — it's a connectivity check, not a yield
benchmark.

</details>

### Skipping RapidAPI entirely

If you skip the RapidAPI key, findajob will:

- still ingest jobs from Greenhouse / Ashby / Lever ATS feeds
  (configured in `config/feed_urls.txt`)
- still ingest jobs from Gmail LinkedIn / Indeed alerts (configured in
  `/config/gmail/`; see [`gmail.md`](gmail.md))
- skip the LinkedIn / Indeed / Bing direct search path entirely (no
  errors, no silent failures — the pipeline simply runs with one fewer
  source)

You can add a key later by visiting `/onboarding/?mode=rerun` and
running the picker again.

### Adding support for a new RapidAPI feed

Need a feed that's not in the supported set? Adding a new adapter is a
development task — each RapidAPI vendor has a different response schema
and pagination mechanism, so each feed needs a custom adapter
implementing the `JobSourceAdapter` Protocol. File an issue at
<https://github.com/brockamer/findajob/issues> or see CLAUDE.md
§"Source Adapters are Pluggable" for the contributor walkthrough.

---

## Replacing a key later

To rotate any key:

1. Generate the new key at the provider (using the steps above).
2. Visit `/onboarding/?mode=rerun` on your findajob instance.
3. Paste the new key. The injector backs up the existing `data/.env`
   under `.backups/{UTC-stamp}/` and writes the new value in place.

The pipeline picks up the new key on its next scheduled run; no
restart needed for keys read at request time.

If you're running your own stack (e.g. self-hosting via docker-compose
or deploying to Fly), you ARE the operator — both options above work
for you. If a separate person manages your stack on your behalf, they
can edit `data/.env` directly on the host:
- **Docker self-host:** SSH into the host and edit `data/.env` — see
  `docs/operations/install-docker.md` under "Operating an existing
  stack."
- **Fly.io:** `fly secrets set OPENROUTER_API_KEY=sk-or-v1-... --app findajob-<your-handle>`
  (Fly redeploys the machine automatically when secrets change).

---

## Other credentials onboarding asks for

Two credentials are collected outside the API-keys form:

- **Gmail app password** — for ingesting LinkedIn / Indeed job-alert
  emails and auto-detecting ATS rejection emails. Optional. The
  onboarding flow lands at `/onboarding/gmail-config/` after the
  interview; see [`gmail.md`](gmail.md) for the 2-Step Verification
  + app-password generation walkthrough.
- **LinkedIn `Connections.csv`** — for network-based outreach
  drafting. Optional. The onboarding flow has a dedicated terminal step
  that walks through exporting from LinkedIn and validating the CSV.
  See the LinkedIn step in [the getting-started README](README.md).

---

## Sources

- OpenRouter — <https://openrouter.ai/docs/api-reference/authentication>
- RapidAPI keys overview — <https://docs.rapidapi.com/docs/keys-and-key-rotation>
- jobs-api14 listing — <https://rapidapi.com/Pat92/api/jobs-api14>
- JSearch listing — <https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch>
