# findajob

**Self-hosted job search infrastructure: AI triages thousands of listings down to a handful, generates tailored materials for the ones worth applying to, and learns from every rejection.**

The modern job search grinds people down — hundreds of listings per day, most irrelevant; the same cover letter rewritten at midnight; black-hole rejections that tell you nothing about whether you targeted wrong, wrote wrong, or got unlucky. findajob absorbs the triage, the tailoring, and the tracking so your attention goes to the few applications actually worth sending.

Built and operated daily; pre-1.0 means active development.

![Dashboard](docs/screenshots/dashboard.png)

*Fictional demo data spanning data-center ops, social services, and K-12 education — the same pipeline works for every field, only your candidate profile changes.*

[![Deploy to Fly.io](https://img.shields.io/badge/Deploy%20to%20Fly.io-8B5CF6?style=for-the-badge&logo=flydotio&logoColor=white)](docs/getting-started/start-here-fly.md)

*No terminal needed — about 20 minutes, start to finish.*

---

## What it does

- **Cuts the noise so you can focus.** Every morning it pulls hundreds of fresh listings, scores each one against your background, and surfaces only the handful worth your attention. Most job tools track what you applied to; this one finds the few worth applying to.
- **Writes the application for you to finish.** One click produces a per-job folder: a tailored resume, a cover letter, a researched briefing on the company, and outreach drafts that name real contacts from your network.
- **Learns from every rejection.** Each job that doesn't pan out gets tagged with a reason — *Skills Mismatch*, *Too Senior*, *Comp Too Low* — and those reasons train the next day's scoring. No other job tool closes that loop.
- **Works for any field.** Built by a data-center-ops candidate, but it works just as well for a social worker, teacher, accountant, software engineer, or trades professional. Only your profile changes.

Thirty days on the operator's own instance:

```
Listings ingested                8,393   ── 30-day window
Worth a look (AI-scored)           187      2.2%
Entered the prep pipeline           93      50% of those
Applications submitted              59      63% of prepped
Interviews from those 59 apps       13      22%
```

8,393 listings narrowed to 59 applications — and 13 interviews — in a month. Interview rates are unpredictable; what the system does is shrink the application volume you need to get them.

---

## What you'll need

- **An OpenRouter account** *(required)* — funds every AI call findajob makes. Pay-as-you-go, starting from $0. **Add at least $10 of credit before you begin** (that covers the onboarding interview); **$20–30 covers a typical first month.**
- **A RapidAPI account** *(optional)* — adds LinkedIn and Indeed listings to the mix. Free tier, no credit card. Skip it and findajob still pulls from company career pages and your Gmail job alerts — but most people want LinkedIn too.
- **A Gmail account** *(optional)* — lets findajob read your LinkedIn job alerts and notice rejection emails automatically.

Sign-up walkthroughs for both keys: [`docs/getting-started/api-keys.md`](docs/getting-started/api-keys.md). You paste them into the app once during setup.

---

## What it costs

Real per-call rates from the operator's instance (last 30 days):

| Item | Typical |
|---|---|
| Daily scoring (~100 listings) | $0.10–$0.40 |
| Per fully-prepped job | ~$1.10 |
| Per interview-prep run | ~$0.30 |

**Most people: ~$20–50/month in AI spend**, plus ~$5/month if you host on Fly.io. Full breakdown across usage levels: [`docs/getting-started/cost.md`](docs/getting-started/cost.md).

---

## Quick start

Two ways to run findajob — pick based on whether you want to operate a server:

- **New to this? Host it on Fly.io** *(recommended)* — runs under your own Fly account, no terminal required, about $5/month. Sign up, launch the app, add your keys: live in roughly 20 minutes. **→ [Start here](docs/getting-started/start-here-fly.md)**
- **Have a Linux server? Self-host with Docker** — runs on a box you operate, with no hosting cost beyond the machine. You handle backups, TLS, and updates. **→ [Docker install guide](docs/operations/install-docker.md)**

Both paths run the same image and reach the same dashboard. Once it's live, a one-time onboarding interview — a roughly hour-long chat inside the app about your background and target role — teaches findajob who you are. The next morning's triage then delivers your first scored shortlist.

---

## Documentation

- **[Getting started](docs/getting-started/README.md)** — sequenced setup guide
- **[Daily workflow](docs/usage.md)** — what to do each day, tab by tab
- **[Troubleshooting](docs/troubleshooting.md)** — symptom index and log reading

---

## For contributors

findajob is a real, daily-driven system, and the codebase is a worked example of how a multi-stage LLM pipeline holds together in production.

- **[Architecture](docs/architecture.md)** — system design, the prep pipeline's stage-by-stage data flow, and the per-stage model choices
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — dev setup, commit conventions, and the architectural invariants the code enforces
- **[Project board](https://github.com/users/brockamer/projects/1)** — the single source of truth for active work
- New here? Browse [`good first issue`](https://github.com/brockamer/findajob/labels/good%20first%20issue).

---

## Privacy

The repo contains zero personal data. All candidate content — resume, profile, writing samples, API keys — lives in gitignored paths populated from `.example` templates, and a pre-commit hook blocks PII you accidentally try to commit. Your materials stay in your own stack's storage; the only outbound calls are to the AI providers you configure.

- **[Issues](https://github.com/brockamer/findajob/issues)** — file a bug, request a feature
- **[Discussions](https://github.com/brockamer/findajob/discussions)** — "how do I…" or "have you considered…"
- **Security** — please don't file public issues for security bugs; see [`SECURITY.md`](SECURITY.md)

---

## License

[MIT](LICENSE).
