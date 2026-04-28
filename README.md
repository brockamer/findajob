# findajob

Self-hosted infrastructure for a sane job search.

The modern job search grinds people down — hundreds of listings per day, most irrelevant; the same cover letter rewritten at midnight; no memory of which companies went silent weeks ago; no signal about whether the rejections mean "wrong skill," "wrong level," or "wrong field."

Burnout is the default. findajob absorbs the triage, the tailoring, and the tracking so your attention goes to the few applications actually worth sending. It's a pre-1.0 personal project — used daily by one operator and one beta tester, not a polished product yet.

LinkedIn, Indeed, Greenhouse, and Gmail flow in; a local LLM filters out the noise; a web UI lets you triage, prep, and track. Runs as a Docker container on any Linux host. ~$0.50–2/day in API usage.

---

## What it does

The pipeline narrows the funnel at every step where a human would otherwise waste attention — LLM triage on the way in, human triage on the way to prep, prep only for jobs worth applying to. Thirty days on the operator's instance looks like this:

| Stage (30 days) | Count | Pass rate |
|---|---:|---:|
| Listings ingested | **12,565** | — |
| Scored ≥7 (surfaced to operator) | 388 | 3.1% of ingested |
| Prepped (resume + cover letter + briefing) | 157 | 40% of surfaced |
| Applications sent | **58** | 37% of prepped |
| Interviews (lifetime) | 5 | 9% of applied |

```
Pass rate at each step:
Surfaced   ▓░░░░░░░░░░░░░░░░░░░░░░░░   3.1%   ← LLM triage does the heaviest cut
Prepped    ▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░   40%
Applied    ▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░   37%
Interview  ▓▓░░░░░░░░░░░░░░░░░░░░░░░   9%
```

12,565 listings narrowed to 58 applications — triage cuts the noise so attention goes to the few worth sending. The reject-with-reason flow (448 rejected with feedback, 40 waitlisted in the same 30 days) feeds back into the scorer so its cuts keep improving. Prep is LLM-assisted but user-gated: you never apply to a job the system chose for you.


---

## Is this for you?

- **If your search feels like 11pm cover letters, spreadsheet sprawl, and bot-rejection silence** — this is built for exactly that.
- **If you want polished consumer SaaS** — not yet. It's self-hosted, rough at the edges, opinionated, and used daily by the operator.
- **If you're technical and want to read the code** — see [docs/architecture.md](docs/architecture.md).

---

## Roadmap

Live status of every issue and milestone is on the **[project board](https://github.com/users/brockamer/projects/1)** — issues move through Backlog → Up Next → In Progress → Done as work happens. The summary below is a snapshot.

| Milestone | What it means | Status | Target |
|---|---|---|---|
| **General Availability** | A second non-technical user runs their own instance end-to-end. Config layer fully externalized, user docs written, onboarding flow exists. | 35 closed / 13 open | 2026-05-31 |
| **v1.1 — Cost + Credentials Hardening** | You see per-job and per-week LLM spend in-app, and no plaintext API key lives on disk. | 0 closed / 7 open | 2026-07-30 |
| **v1.2 — Tuning Loop + Stats** | The pipeline recommends scorer tunes from your behavior, and `/stats/*` dashboards show precision, outcome, recall, and cost trends over time. | 0 closed / 19 open | 2026-09-29 |
| **v1.3 — Ops Hardening** | Fresh-install smoke is CI-gated, log rotation works, DB migrates cleanly across versions. | 1 closed / 12 open | 2026-10-30 |
| **v1.4 — Funnel + Triage UX** | Every candidate row in the daily triage loop is actionable in one click, with prior-application context inline. | 7 closed / 13 open | 2026-08-30 |

*Counts above are approximate snapshots — for the live state, follow the [project board](https://github.com/users/brockamer/projects/1).*

---

## How it works

**1. Daily triage** (00:00, scheduler-driven) — fetches 100–500 listings from RapidAPI (LinkedIn), direct ATS feeds (Greenhouse, Ashby, Lever), and Gmail job alerts (LinkedIn + Indeed); cleans + deduplicates; enriches with JD text; scores each against your `profile.md` using an LLM. Results land in SQLite.

**2. Dashboard triage** — the web UI shows every scored job that cleared the threshold, with relevance/fit/probability scores, known contacts, and AI notes. You flag the ones worth prepping.

![Dashboard](docs/screenshots/dashboard.png)

**3. Prep** (on-flag) — launches `prep_application.py`, which generates a folder per job containing a tailored resume, cover letter, company briefing, and network-outreach drafts. Uses Claude Opus for writing, Perplexity for company research.

**4. Apply + track** — you submit the application, mark the job *Applied*. The Applied tab color-codes by days-since-submission so you can see at a glance which applications have gone silent too long.

![Applied](docs/screenshots/applied.png)

**5. Reject with reason** — jobs that don't work out get rejected with a reason (*Skills Mismatch*, *Too Senior*, *Comp Too Low*, *Geography/Onsite*, etc.). Those reasons feed back into the next day's scorer as negative examples.

**6. Learn** — stats dashboards make the funnel and the rejection mix legible, so you can tell whether the scorer is drifting or whether a particular reason is spiking — a signal to tune the profile or retarget.

![Funnel](docs/screenshots/funnel.png)

![Feedback](docs/screenshots/feedback.png)

*Screenshots are from a fresh-install demo database seeded with fictional jobs across data center operations, social work, and K–12 education. No real employer or candidate data.*

---

## What you get out of it

- **No more switching between Linear, Notion, Gmail, and three browser tabs.** Dashboard, Applied, Waitlist, Review, Rejected, Archive are all filtered views of the same SQLite table. Sort, filter, density toggles are URL query params — any view is bookmarkable and shareable.
- **Your tailored resumes and cover letters stay yours.** Generated folders sit on your Docker host as plain `.docx` and `.md` files; the web UI renders Markdown inline and serves the docs as downloads. Nothing is locked behind a vendor login.
- **When you reject a job, you tell it why — and tomorrow's scorer remembers.** Every rejection is a labeled training example. Every manual-review flag points at the part of your profile the LLM found ambiguous, so you know exactly where to tune.
- **Built by a data center ops candidate; designed to work for a social worker, teacher, accountant, or trades professional too.** Same pipeline, same setup — only `profile.md` changes. See [`docs/GENERALIZATION.md`](docs/GENERALIZATION.md) for the state of the field-agnostic work.
- **Your data stays local.** SQLite on your Docker host. The only outbound calls are to the LLM providers you've configured; the repo contains zero personal data.

---

## Stack

| Component | Choice |
|---|---|
| Scoring | DeepSeek v3.2 via OpenRouter (through [aichat-ng](https://github.com/blob42/aichat-ng)) |
| Resume + cover letter + outreach | Claude Opus / Sonnet 4.6 |
| Company research | Perplexity Sonar Pro |
| Embeddings (REPL RAG over your own writing) | Gemini Embedding |
| Storage | SQLite |
| Job sources | RapidAPI jobs-api14, Greenhouse JSON, Gmail OAuth2 |
| Web UI | FastAPI + HTMX + Tailwind + Chart.js |
| Push notifications | [ntfy.sh](https://ntfy.sh) |
| Scheduler | supercronic (in-container) |

---

## Quick start

The pipeline ships as `ghcr.io/brockamer/findajob` pulled via Docker Compose.

```bash
# On your Docker host
sudo mkdir -p /opt/stacks/findajob-<you>/state/{data,config,candidate_context,companies,logs,aichat_ng}
sudo chown -R $(id -u):$(id -g) /opt/stacks/findajob-<you>/
cd /opt/stacks/findajob-<you>

curl -fsSL -o compose.yaml https://raw.githubusercontent.com/brockamer/findajob/main/ops/compose.yaml.example
curl -fsSL -o .env         https://raw.githubusercontent.com/brockamer/findajob/main/ops/stack.env.example

# Populate state/ with API keys, personal config, candidate profile
# (templates + walkthrough in the install guide)
docker compose up -d
```

Full walkthrough → [`docs/setup/install-docker.md`](docs/setup/install-docker.md) (or start at [`docs/setup/README.md`](docs/setup/README.md) for the guided sequence). Native-host install remains as a legacy fallback → [`docs/setup/install-linux.md`](docs/setup/install-linux.md).

---

## Documentation

Start here:

- **[Setup](docs/setup/README.md)** — guided sequence for getting your stack running
- **[Daily workflow](docs/usage.md)** — what to do each day, tab by tab in the web UI
- **[Troubleshooting](docs/troubleshooting.md)** — symptom index, log reading, health alerts
- **[Architecture](docs/architecture.md)** — system design, data flow, component map (for operators who want to read the code)

<details>
<summary>All documentation (click to expand)</summary>

| Doc | Contents |
|---|---|
| [docs/setup/README.md](docs/setup/README.md) | Setup — start here |
| [docs/usage.md](docs/usage.md) | Daily workflow: web UI tab by tab |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Symptom index + log reading + health alerts |
| [docs/architecture.md](docs/architecture.md) | System design, data flow, component map |
| [docs/setup/prerequisites.md](docs/setup/prerequisites.md) | API keys, accounts, tools you need |
| [docs/setup/install-docker.md](docs/setup/install-docker.md) | Docker Compose setup (recommended) |
| [docs/setup/install-linux.md](docs/setup/install-linux.md) | Legacy native install (Ubuntu + systemd) |
| [docs/setup/configure.md](docs/setup/configure.md) | Profile, resume, search queries, API keys |
| [docs/setup/state-migration.md](docs/setup/state-migration.md) | Moving an existing pipeline to a new host |
| [docs/operations.md](docs/operations.md) | Operator reference: manual commands, monitoring |
| [docs/notifications.md](docs/notifications.md) | ntfy.sh setup and notification schedule |
| [docs/GENERALIZATION.md](docs/GENERALIZATION.md) | Making the pipeline work for non-tech fields |
| [docs/claude-code.md](docs/claude-code.md) | Using Claude Code as a pipeline operator |

</details>

---

## What it costs to run

Real-world per-day usage on the operator's instance, ~10k jobs/month scored:

| Item | Typical day |
|---|---|
| Scoring (DeepSeek via OpenRouter) | $0.10–0.30 |
| Company research (Perplexity Sonar Pro) | $0.10–0.20 per prepped job |
| Prep writing (Claude Opus) | $1.50–3.00 per prepped job |
| Embeddings rebuild (Gemini) | ~$0.01/week |

Total: ~$0.50/day when triaging only; ~$5–15 on days you prep a few applications.

---

## Privacy

The repository contains no personal data. All candidate content (resume, profile, writing samples, search queries, API keys) lives in gitignored paths populated from `.example` templates. See [`docs/claude-code.md`](docs/claude-code.md) for how to keep personal context out of Claude Code sessions touching this repo.

---

## Stay in touch / contribute

- **[Project board](https://github.com/users/brockamer/projects/1)** — what's being worked on, what's blocked, what's on the roadmap. The single source of truth for active work.
- **[Issues](https://github.com/brockamer/findajob/issues)** — file a bug, request a feature, or browse the open ones. New issues land in the board's Backlog and get triaged with a Priority field.
- **In-app feedback widget** — if you're running an instance, the floating "Feedback" button on every page files a GitHub issue directly from the web UI (configure with a fine-grained PAT per `docs/setup/configure.md`).
- **[Discussions](https://github.com/brockamer/findajob/discussions)** — for "how do I..." or "have you considered..." threads that aren't bug reports yet.

This is a personal project, but contributions are welcome. The code is opinionated, the docs are written for an external reader trying it for the first time, and the pre-commit hook will block any PII you accidentally try to commit.

---

## License

MIT.
