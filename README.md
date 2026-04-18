# findajob

A self-hosted, AI-powered job search pipeline. Fetches leads from LinkedIn, Indeed, Greenhouse, and Gmail, scores them with an LLM, surfaces high-quality matches in a Google Sheet, and on demand generates a full application package: tailored resume, cover letter, company briefing, and network outreach drafts.

Deploys as a Docker container via Compose. Native systemd install remains documented as a fallback. No cloud infrastructure. No subscription. Costs ~$0.50–2/day in API usage depending on job volume.

---

## What It Does

1. **Daily triage** — fetches 100–500 job listings from multiple sources, deduplicates, enriches with JD text, scores with an LLM against your profile, writes results to SQLite and syncs to Google Sheets
2. **Flag → Prep** — you flag a job in the Dashboard; the pipeline generates a tailored resume, cover letter, company briefing, and LinkedIn outreach drafts
3. **Notifications** — push notifications via ntfy.sh: daily stats, health check, apply reminders
4. **Rejection tracking** — reject with a reason in the sheet; the pipeline logs it for pattern analysis and moves the folder to `_rejected/`
5. **Manual injection** — Google Form lets you add any job (found outside the pipeline) and optionally trigger prep immediately

---

## Tech Stack

| Component | Choice | Why |
|---|---|---|
| Job scoring | [aichat-ng](https://github.com/blob42/aichat-ng) + DeepSeek v3 via OpenRouter | Fast, cheap, accurate for structured JSON output |
| Resume / cover letter | Claude Opus 4.6 (thinking mode) | Best writing quality at cost |
| Company research | Perplexity Sonar Pro | Real-time web access |
| Database | SQLite | Zero-config, ACID, queryable |
| Sheet UI | Google Sheets API v4 | Familiar interface, cross-device |
| Job sources | RapidAPI jobs-api14, Gmail OAuth2, Greenhouse JSON API | Broad coverage |
| Notifications | ntfy.sh | Free, cross-platform push |
| File sync | rclone bisync | Google Drive sync for prep folders |
| Scheduler | supercronic (Docker) / systemd (native) | supercronic runs the crontab inside the image; systemd is the fallback on native installs |

---

## Prerequisites

- Python 3.11+
- [aichat-ng](https://github.com/blob42/aichat-ng) (`aichat-ng` binary, not `aichat`)
- pandoc
- rclone (optional — only needed for Google Drive sync)
- API keys: Anthropic, OpenRouter (DeepSeek), Perplexity, Google Gemini, RapidAPI
- Google Cloud project with Sheets API and Gmail API enabled

---

## Quick Start

findajob ships as a Docker image pulled from GHCR and deployed via Docker Compose.

```bash
# On your Docker host — replace <you> with a short tag (brock, amy, etc.)
sudo mkdir -p /opt/stacks/findajob-<you>/state/{data,config,candidate_context,companies,logs,aichat_ng}
sudo chown -R $(id -u):$(id -g) /opt/stacks/findajob-<you>/
cd /opt/stacks/findajob-<you>

curl -fsSL -o compose.yaml https://raw.githubusercontent.com/brockamer/findajob/main/ops/compose.yaml.example
curl -fsSL -o .env         https://raw.githubusercontent.com/brockamer/findajob/main/ops/stack.env.example

# Populate state/ with API keys, personal config, and candidate profile —
# see the install guide for each file's purpose and template.
docker compose up -d
```

Full walkthrough (API keys, Gmail OAuth, Google Sheets, Drive sync) →
[`docs/setup/install-docker.md`](docs/setup/install-docker.md).

Running on a Linux host without Docker is still supported — see
[`docs/setup/install-linux.md`](docs/setup/install-linux.md).

---

## Documentation

| Doc | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | System design, data flow, component map |
| [docs/setup/prerequisites.md](docs/setup/prerequisites.md) | API keys, accounts, tools you need |
| [docs/setup/install-docker.md](docs/setup/install-docker.md) | **Docker Compose setup (recommended)** |
| [docs/setup/install-linux.md](docs/setup/install-linux.md) | Ubuntu + systemd setup (native fallback) |
| [docs/setup/configure.md](docs/setup/configure.md) | Profile, resume, queries, Google Sheets |
| [docs/setup/state-migration.md](docs/setup/state-migration.md) | Moving an existing pipeline to a new machine |
| [docs/operations.md](docs/operations.md) | Day-to-day use, monitoring, common tasks |
| [docs/scripts-reference.md](docs/scripts-reference.md) | Every script documented |
| [docs/google-sheets.md](docs/google-sheets.md) | Sheet layout, Dashboard workflow |
| [docs/notifications.md](docs/notifications.md) | ntfy.sh setup and notification schedule |
| [docs/claude-code.md](docs/claude-code.md) | Using Claude Code as a pipeline operator |

---

## Repository Structure

```
findajob/
├── candidate_context/          # YOUR personal content (all gitignored)
│   ├── profile.md              # your candidate profile
│   ├── master_resume.md        # your master resume
│   ├── voice_samples/          # your writing samples for CL voice calibration
│   └── profile.md.example      # template — copy and fill in
├── config/
│   ├── roles/                  # aichat-ng role prompts (8 roles)
│   ├── scoring_schema.json     # JSON schema for LLM scorer output
│   ├── strip-bookmarks.lua     # pandoc Lua filter
│   ├── reference.docx          # pandoc Word template
│   ├── paths.env               # YOUR binary paths (gitignored)
│   └── *.example               # templates for gitignored files
├── data/
│   ├── pipeline.db             # SQLite (gitignored)
│   └── .env                    # API keys (gitignored)
├── docs/                       # Documentation
├── scripts/
│   ├── triage.py               # daily pipeline
│   ├── prep_application.py     # on-demand prep
│   ├── poll_flags.py           # sheet flag poller
│   ├── sync_sheet.py           # DB → Google Sheets
│   ├── setup_sheets.py         # sheet formatting (run once)
│   ├── notify.py               # ntfy push notifications
│   ├── find_contacts.py        # LinkedIn contact matching
│   ├── ingest_form.py          # Google Form ingestion
│   └── diag/                   # diagnostic scripts (run manually)
├── companies/                  # Generated prep folders (gitignored)
├── logs/                       # Pipeline logs (gitignored)
├── ops/                        # Docker deploy: crontab, compose example, entrypoint
├── Dockerfile                  # findajob image build (published to ghcr.io/brockamer/findajob)
└── CLAUDE.md                   # Claude Code session context
```

---

## Privacy Model

This repository contains no personal data. All files containing personal information (resume, profile, writing samples, search queries, API keys) are gitignored and must be created locally from `.example` templates.

See [docs/claude-code.md](docs/claude-code.md) for how Claude Code integrates with this project and how to keep your personal context out of the public repo.

---

## Cost Estimate

Based on ~200 leads/day:
- Job scoring (DeepSeek v3 via OpenRouter): ~$0.10–0.30/day
- Prep generation per job (Claude Opus 4.6): ~$1.50–3.00 per job flagged
- Company research (Perplexity Sonar Pro): ~$0.10–0.20 per job flagged
- Embedding rebuild (Gemini embedding): ~$0.01/week

---

## License

MIT
