# Expanding job sources via LinkedIn alerts

The pipeline's `gmail_linkedin` ingestion path is consistently the highest-yielding automated source by hit-rate. It also costs nothing in code or quota — adding more saved-search alerts on LinkedIn directly multiplies your input volume.

This guide walks through creating LinkedIn saved-search alerts that feed the pipeline, tuning them for signal-to-noise, and a cross-field bundle of example searches you can adapt.

For the broader source-strategy picture — including the paid RapidAPI feeds, career-page polling, and manual ingest — see [`usage.md`](../usage.md#job-sources).

---

## How the path works

1. You create a saved-search alert on LinkedIn (Job Search → set filters → "Save search" → choose alert frequency).
2. LinkedIn emails new matches to your Gmail address on the schedule you picked.
3. The pipeline reads those emails via IMAP (configured at `/config/gmail/`), parses the embedded job rows, and submits each LinkedIn URL to the `gmail_linkedin` fetcher.
4. The fetcher resolves the job ID, calls the RapidAPI LinkedIn endpoint to retrieve the full posting (title / company / location / JD), and queues it for scoring.

The first step is the only one you re-touch as you tune. Steps 2–4 are automatic once Gmail integration is configured (see [`getting-started/gmail.md`](../getting-started/gmail.md)).

---

## Setting up a saved-search alert

1. Open [linkedin.com/jobs](https://www.linkedin.com/jobs/) and run a search: enter keywords, set a location, set filters (date posted, experience level, on-site / remote, etc.).
2. After results load, look for the **Set alert** toggle near the top of the results — turn it on.
3. Click **Manage alerts** (or visit [linkedin.com/jobs/job-alerts/](https://www.linkedin.com/jobs/job-alerts/)).
4. For the alert you just created, set:
   - **Frequency:** *Daily* for keywords likely to surface a small number of matches; *Weekly* if a search is very broad and would otherwise spam you.
   - **Email:** the same Gmail address the pipeline is configured to read (set in `/config/gmail/`).
   - **Notification method:** Email (in-app notifications don't reach the pipeline).

LinkedIn caps you at ~20 saved searches; in practice 8–12 is the right working set.

---

## Tuning for signal vs noise

### Frequency
- **Daily** — for any search where you expect 0–10 matches per day. Most field-targeted searches.
- **Weekly** — for very broad fallback searches (e.g. "remote engineering" without seniority filter) you keep around as a safety net.

### Keyword breadth
LinkedIn's keyword field is OR-permissive when you use multiple terms but AND-strict when you put them in quotes. A few patterns:
- **Single canonical title:** `"data center operations manager"` — narrow, high precision.
- **Multi-title alternation:** `(operations manager OR ops lead OR site lead) data center` — broader but still on-domain.
- **Skill anchor:** `npi hardware` — pulls roles where these terms appear anywhere, not just title.

The pipeline's scorer + prefilter will reject obviously-off-domain hits, so it's safer to over-recall (broader query, more raw input) than to under-recall (narrow query, you miss real matches).

### Geo splits
A single search across "United States" tends to flatten regional signal. A common pattern is to run **the same keywords in 3–4 separate searches** with different location filters:
- Your primary metro (e.g. *Los Angeles, CA*)
- A second metro you'd accept (*San Francisco Bay Area*, *New York City*, etc.)
- *Remote — United States*
- *Hybrid* in your primary metro

This costs you saved-search slots but gives you a much cleaner per-region read.

### Seniority bands
LinkedIn's *Experience level* filter is coarse. A useful split for senior candidates:
- One alert filtered to **Director / VP / Executive** roles
- One alert filtered to **Senior level** (sometimes labeled "Mid-Senior")
- *Skip* Entry / Associate filters unless you're early-career

Under-targeting on seniority is the single biggest source of "looks relevant but actually wrong level" noise — worth the extra alert.

---

## Example bundle (cross-field)

These are starting templates. Pick the field closest to yours, replace the keywords with your own, duplicate per geo, and tune frequency.

### Engineering / data center / hardware ops
| Keywords | Geo | Frequency |
|---|---|---|
| `"data center operations manager"` | LA Metro | Daily |
| `"data center operations manager"` | Remote — US | Daily |
| `(npi OR "new product introduction") hardware` | SF Bay Area | Daily |
| `"infrastructure operations" director` | LA Metro | Weekly |

### Healthcare
| Keywords | Geo | Frequency |
|---|---|---|
| `"clinical operations manager"` | NYC Metro | Daily |
| `"nurse manager" OR "nursing manager"` | Your metro | Daily |
| `"healthcare program manager"` | Remote — US | Weekly |

### Social work / homelessness services
| Keywords | Geo | Frequency |
|---|---|---|
| `("director of homeless services" OR "homelessness")` | Your metro | Daily |
| `"continuum of care"` | Remote — US | Weekly |
| `"systems social work"` | Your metro | Daily |

### Education / curriculum
| Keywords | Geo | Frequency |
|---|---|---|
| `"instructional designer"` | Remote — US | Daily |
| `"curriculum specialist" K-12` | Your metro | Daily |
| `"director of teaching and learning"` | Your metro | Weekly |

### Skilled trades / facilities
| Keywords | Geo | Frequency |
|---|---|---|
| `"hvac service manager"` | Your metro | Daily |
| `"facilities manager"` commercial | Your metro | Daily |
| `master electrician supervisor` | Your metro | Weekly |

The ratio of "hits" to "noise" varies by field. A reasonable starting target is 80% on-field with the prefilter handling the rest. If a search is producing >50% noise, narrow the keywords or add an exclusion phrase.

---

## Verifying the path end-to-end

After you've added a few alerts:

1. Wait for LinkedIn's first email (Daily alerts arrive between 6am and 9am local time).
2. The next pipeline triage pass picks them up — the morning ntfy summary should show jobs with `source=gmail_linkedin`.
3. Spot-check the Dashboard for jobs whose company / title looks like it came from a search you just added.

If a search runs for several days without producing any jobs in the Dashboard:
- Verify the alert is still active in [LinkedIn → Job Alerts](https://www.linkedin.com/jobs/job-alerts/).
- Confirm the email is reaching the configured Gmail address (check the *All Mail* label).
- Check the pipeline's IMAP fetch state at `/config/gmail/` for auth failures.

---

## Out of scope

- **Programmatic alert creation.** LinkedIn's UI is the only sane path today; no API surface for managing saved searches.
- **Other email-alert sources** (Indeed, Glassdoor, Otta). They work technically — the same Gmail-ingest path will pick them up — but parsing reliability varies and is tracked separately.

See also: [`getting-started/gmail.md`](../getting-started/gmail.md) for IMAP / Gmail configuration; [`usage.md`](../usage.md#gmail-ingest-alerts) for the source-strategy overview.
