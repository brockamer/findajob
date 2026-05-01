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
| **RapidAPI (jobs-api14)** | LinkedIn + Indeed job search ingestion | BASIC plan: 150 requests/month free | Optional — Gmail LinkedIn-alert ingestion still works without it |
| **Google AI Studio (Gemini)** | Embeddings for the optional RAG index over your candidate context (REPL-only feature) | Free tier on Gemini embeddings; no billing setup needed | Optional — only used by the REPL workflow; pipeline is fully functional without it |

You can leave RapidAPI and Google blank during onboarding and add them
later by re-running onboarding with `?mode=rerun`. OpenRouter is the only
hard requirement.

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

## RapidAPI (jobs-api14) — optional

RapidAPI is the gateway findajob uses to query LinkedIn and Indeed
listings programmatically. The free BASIC tier gives 150 requests per
month, which is enough for a daily search across a small set of queries.

If you skip this key, findajob will:

- still ingest jobs from Greenhouse / Ashby / Lever ATS feeds (configured
  in `config/feed_urls.txt`)
- still ingest jobs from Gmail LinkedIn / Indeed alerts (configured in
  `/config/gmail/`)
- skip the LinkedIn / Indeed direct search path entirely (no errors, no
  silent failures — the pipeline simply runs with one fewer source)

You can add the key later by visiting `/onboarding/?mode=rerun` and
filling the field.

### Steps

1. Go to <https://rapidapi.com> and click **Sign Up** (top right). You
   can use Google, GitHub, or email.
2. Visit the jobs-api14 listing: <https://rapidapi.com/Pat92/api/jobs-api14>.
3. Click **Subscribe to Test** → choose the **BASIC** plan (150
   requests/month, free, no credit card).
4. After subscribing, click the **Endpoints** tab. Your API key is shown
   in the right-hand pane in the `X-RapidAPI-Key` header. Copy that
   value.
5. Paste it into findajob's onboarding page in the **RapidAPI key**
   field.

### Quota guidance

150 requests/month works out to about 5 per day. findajob's daily triage
uses one request per query in `config/jsearch_queries.txt`, so keep the
query count modest (3–4 queries) if you want headroom for re-runs and
the occasional spec ingest.

If you need more capacity, jobs-api14's PRO plan adds a meaningful
quota bump for a small monthly fee — pricing is shown on the same page.

---

## Google AI Studio (Gemini) — optional

Google AI Studio gives free Gemini API access. findajob uses Gemini's
text-embedding model to build a local search index over your candidate
context (master resume + voice samples + optional company research).
This index is consumed only by the REPL workflow when you want to
chat with your own materials interactively — the daily pipeline does not
use it.

If you skip this key, findajob's daily pipeline runs identically. You
just won't be able to use the REPL `aichat-ng -r` workflow against your
indexed materials.

### Steps

1. Go to <https://aistudio.google.com>.
2. Sign in with any standard Gmail account. **No Google Cloud account is
   required, and no billing setup is required for the free tier.**
3. Accept the Terms of Service if prompted. Google AI Studio
   automatically provisions a default project for new users.
4. Click **Get API key** in the left sidebar (or visit
   <https://aistudio.google.com/app/apikey>).
5. Click **Create API key**. Choose the default project. Copy the key
   that's generated.
6. Paste it into findajob's onboarding page in the **Google API key**
   field.

### Free-tier rate limits

As of April 2026, Google's Gemini free tier provides:

- 5–15 requests per minute (per model)
- 100–1,000 requests per day (per model)
- A universal cap of 250,000 tokens per minute across all models

findajob's RAG rebuild runs once weekly (Sunday 03:00 PT) and embeds
your candidate context plus any indexed company research. A typical
candidate context fits well under the daily quota in a single rebuild
pass.

> **Note (April 2026 free-tier change):** Google removed Gemini Pro
> models from the free tier on April 1, 2026. Gemini 2.5 Flash and
> Flash-Lite remain free. findajob uses the embedding model
> (`gemini-embedding-001`), which is on the free tier — pipeline
> behavior is unaffected by the Pro-model change.

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
documented in `docs/setup/install-docker.md` under "Operating an
existing stack."

---

## Sources

- OpenRouter — <https://openrouter.ai/docs/api-reference/authentication>
- RapidAPI keys overview — <https://docs.rapidapi.com/docs/keys-and-key-rotation>
- jobs-api14 listing — <https://rapidapi.com/Pat92/api/jobs-api14>
- Google AI Studio API keys — <https://ai.google.dev/gemini-api/docs/api-key>
- Gemini API billing — <https://ai.google.dev/gemini-api/docs/billing>
