# Getting your API keys

findajob's pipeline calls three external services. To run your own stack
end-to-end, you provide your own keys for each on the onboarding page —
they live only in your stack's `data/.env` and are never shared with anyone
else's deployment.

This guide walks you through getting each key. As of April 2026 all three
have a free tier sufficient for personal job-search use; none require a
credit card to get started.

| Key | What findajob uses it for | Cost on free tier | Required? |
|---|---|---|---|
| **OpenRouter** | All LLM calls (scoring, briefings, resume tailoring, cover letters, outreach drafts, in-app interview) | Pay-as-you-go from $0; no monthly minimum. ~$0.50/day triage-only; $1.50–3.00 per fully-prepped job (Claude Opus dominates that bill). | **Yes** — pipeline cannot score or generate materials without it |
| **RapidAPI feed** (jobs-api14, JSearch, or jobs-api14 Indeed) | LinkedIn + Indeed job search ingestion | BASIC plan: 150–250 requests/month free | Optional — Gmail LinkedIn-alert ingestion still works without it |

You can leave RapidAPI blank during onboarding and add it later by
re-running onboarding with `?mode=rerun`. OpenRouter is the only hard
requirement.

---

## OpenRouter

OpenRouter is a single API gateway for many LLM providers (Anthropic,
Google, DeepSeek, Perplexity, etc.). findajob uses it because the
pipeline mixes multiple model families and OpenRouter lets you pay one
bill instead of holding direct accounts with five vendors.

### Steps

1. Go to <https://openrouter.ai>.
2. Click **Sign In** (top right). Sign up with Google, GitHub, or email.
3. Add credit to your account: **Settings** → **Credits** → **Buy
   credits**. $20–$30 is a comfortable starter; the pipeline burns roughly
   $1.50–$3.00 per job that gets a full briefing + tailored resume +
   cover letter + recruiter critique + outreach drafts (Claude Opus
   dominates that bill).
4. Create an API key: **Settings** → **Keys** → **Create Key**. Give it
   a name like "findajob" so you can find it later. Copy the key — it
   starts with `sk-or-v1-` and is shown to you only once.
5. Paste it into findajob's onboarding page in the **OpenRouter API key**
   field.

### What findajob does with it

findajob calls 10 different OpenRouter models for different roles:

- Scoring → DeepSeek v3.2
- Cover letters, briefings, recruiter critique → Claude Opus 4.7
- Resume change review, network analysis → Gemini 3 Flash
- Company research, fit analysis → Perplexity Sonar Reasoning Pro
- In-app interview chat → Claude Sonnet 4.6

You can see the full role-to-model mapping in the project's `CLAUDE.md`
under "Pipeline Context Table."

### Cost expectations

| Activity | Approximate cost |
|---|---|
| Score one job (no JD fetch) | < $0.001 |
| Score one job (with JD + fit analysis) | $0.01–$0.05 |
| Full prep package (briefing + tailored resume + cover letter + recruiter critique + outreach drafts) | $1.50–$3.00 (Claude Opus dominates) |
| In-app onboarding interview, end to end | ~$1 |
| Weekly company-discovery cron | ~$0.10 |

Daily triage with no full-prep activity typically stays under $0.50/day.

OpenRouter shows you a live spend breakdown at <https://openrouter.ai/activity>.

---

## RapidAPI feed — optional

> **Already onboarded?** The onboarding interview's source-strategy
> briefing (#283) walks through what the paid service is good for and
> when to skip it. If the briefing led you to opt out of a RapidAPI feed
> (sub-phase 3g, no `a` selection), you can leave the key field blank
> here — the pipeline will use your other sources only. Re-run
> onboarding via `/onboarding/?mode=rerun` if you want to revisit the
> source-strategy decision.

findajob supports multiple RapidAPI-flavored job feeds. All feeds share the
canonical `RAPIDAPI_KEY` env var — one RapidAPI account key covers every API
you've subscribed to. The onboarding interview's Section 3h recommends the
right feed for your field from the operator-curated `config/rapidapi_feeds.yaml`
table; the `/onboarding/feed-config/` form walks you through signup and runs a
live connection test.

| Feed | Adapter name (in `active_sources.txt`) | Env var | What it searches | Free tier |
|---|---|---|---|---|
| **jobs-api14** | `jobs-api14` | `RAPIDAPI_KEY` (canonical) or `JOBS_API14_KEY` (legacy fallback, #414) | LinkedIn — broad coverage, LinkedIn-heavy | 150 req/month (BASIC); 20,000 req/month (PRO) |
| **jobs-api14 (Indeed)** | `jobs-api14-indeed` | `RAPIDAPI_KEY` (canonical) or `JOBS_API14_KEY` (legacy fallback, #414) | Indeed — broad US coverage, inline JD | shares per-account quota with jobs-api14 |
| **jobs-api14 (Bing)** | `jobs-api14-bing` | `RAPIDAPI_KEY` (canonical) or `JOBS_API14_KEY` (legacy fallback, #414) | Bing — 18 jobs/page, inline JD | shares per-account quota with jobs-api14 |
| **JSearch** | `jsearch` | `RAPIDAPI_KEY` (canonical) or `JSEARCH_API_KEY` (legacy fallback, #414) | LinkedIn + Indeed + Glassdoor + ZipRecruiter | 200 req/month (BASIC); 20,000 req/month (PRO) |

> **Adapter names matter.** The values in the "Adapter name" column above are what you must put in `config/active_sources.txt` — one per line. Stacks without `config/active_sources.txt` default to `jobs-api14` (LinkedIn-only); the file is written by the onboarding picker for new stacks.

> **Indeed is opt-in.** To activate it, add `jobs-api14-indeed` to `config/active_sources.txt`. The onboarding picker handles this for new stacks; existing stacks need to add the line manually. Example `config/active_sources.txt` with both feeds:
> ```
> jobs-api14
> jobs-api14-indeed
> ```

> **PRO/ULTRA/MEGA tiers:** Indeed, Bing, and LinkedIn share the same per-account quota — upgrading your RapidAPI plan raises the limit for all three feeds together (PRO: 20,000 req/month shared).

> **Note (Indeed title allowlist):** The `jobs-api14-indeed` adapter applies a hardcoded title allowlist tuned for engineering / operations / program-management / hardware / data-center families. Non-engineering candidates may see sparse Indeed pulls until a follow-up issue lifts this to a config file. See `_TITLE_ALLOW_PATTERN` in `src/findajob/fetchers/adapters/jobs_api14_indeed.py` for the full regex.

> **Note (Bing — opt-in, no allowlist initially):** The `jobs-api14-bing` adapter (#422) is **default-off** — add `jobs-api14-bing` to `config/active_sources.txt` to enable it. Unlike Indeed, Bing ships with no title-allowlist post-filter; AC #4 of #422 calls for an empirical decision after one triage-day measurement (tracked in #601). Until that follow-up lands, all titles flow through. 18 jobs per page (vs Indeed's 20 / LinkedIn's 10) on the same shared quota.

#### Pagination tuning (PRO-tier)

Both jobs-api14 and JSearch let you trade quota for yield via per-stack env vars (#414 PR2 / PR3). Each additional page is one billed RapidAPI request (per-call billing — empirically confirmed on the operator stack for both APIs).

**`JobsApi14Adapter`** (LinkedIn endpoint, opaque-token pagination via `meta.nextToken`) — set `JOBS_API14_MAX_PAGES=N`:

| Tier | Recommended | Monthly cost (5 queries × N pages × 30 days) |
|---|---|---|
| BASIC (150 req/mo) | leave at 1 | 150 / 150 = 100% — no headroom |
| PRO (20,000 req/mo) | 3–5 | 450 / 20,000 = 2.25% (N=3); 750 / 20,000 = 3.75% (N=5) |

Clamped to `[1, 20]`.

**`JSearchAdapter`** (server-side pagination via the API's own `num_pages` param — single HTTP request per query, billed as N units) — set `JSEARCH_NUM_PAGES=N`:

| Tier | Recommended | Monthly cost (5 queries × N pages × 30 days) | Yield ratio |
|---|---|---|---|
| BASIC (200 req/mo) | leave at 1 | 150 / 200 = 75% | ~10 jobs/query |
| PRO (10,000 req/mo) | 3 | 450 / 10,000 = 4.5% | ~27 jobs/query (~2.7x) |

Clamped to `[1, 10]` (half of jobs-api14's ceiling because JSearch's PRO quota is half).

Restart the stack after editing `data/.env`. Live-test in `/onboarding/feed-config/` stays single-page for both adapters regardless of these settings — it's a connectivity check, not a yield benchmark.

If you skip the RapidAPI key, findajob will:

- still ingest jobs from Greenhouse / Ashby / Lever ATS feeds (configured
  in `config/feed_urls.txt`)
- still ingest jobs from Gmail LinkedIn / Indeed alerts (configured in
  `/config/gmail/`)
- skip the LinkedIn / Indeed direct search path entirely (no errors, no
  silent failures — the pipeline simply runs with one fewer source)

You can add a key later by visiting `/onboarding/?mode=rerun` and
running the picker again.

### Sign up for jobs-api14

1. Go to <https://rapidapi.com> and click **Sign Up** (top right).
2. Visit the jobs-api14 listing: <https://rapidapi.com/Pat92/api/jobs-api14>.
3. Click **Subscribe to Test** → choose the **BASIC** plan (150
   requests/month, free, no credit card).
4. After subscribing, click the **Endpoints** tab. Your API key appears
   in the right-hand pane as the `X-RapidAPI-Key` header value.
5. The onboarding `/onboarding/feed-config/` form accepts the key and
   runs a live connection test before writing it to `data/.env` as
   `RAPIDAPI_KEY` — the canonical name covers every RapidAPI feed (#414).

### Sign up for JSearch

1. Go to <https://rapidapi.com> and click **Sign Up** (top right).
2. Visit the JSearch listing: <https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch>.
3. Click **Subscribe to Test** → choose the **BASIC** plan (200
   requests/month, free, no credit card).
4. After subscribing, your API key appears in the right-hand pane as the
   `X-RapidAPI-Key` header value.
5. The onboarding `/onboarding/feed-config/` form accepts the key and
   writes it to `data/.env` as `RAPIDAPI_KEY` — the canonical name covers every RapidAPI feed (#414).

### Quota guidance

Daily triage uses one request per query in `config/jsearch_queries.txt`,
so keep the query count modest (3–4 queries) if you want headroom for
re-runs and the occasional spec ingest. If you need more capacity, both
feeds offer paid tiers with higher quotas — pricing is on their
respective RapidAPI listing pages.

---

## Replacing a key later

To rotate any key:

1. Generate the new key at the provider (using the steps above).
2. Visit `/onboarding/?mode=rerun` on your findajob instance.
3. Paste the new key. The injector backs up the existing `data/.env`
   under `.backups/{UTC-stamp}/` and writes the new value in place.

The pipeline picks up the new key on its next scheduled run; no
restart needed for keys read at request time. RAG embeddings use the
key fresh on each rebuild.

If the operator manages your stack and you want them to rotate a key
on your behalf, they can edit `data/.env` directly via SSH —
documented in `docs/getting-started/install-docker.md` under "Operating an
existing stack."

---

## Sources

- OpenRouter — <https://openrouter.ai/docs/api-reference/authentication>
- RapidAPI keys overview — <https://docs.rapidapi.com/docs/keys-and-key-rotation>
- jobs-api14 listing — <https://rapidapi.com/Pat92/api/jobs-api14>
- JSearch listing — <https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch>
