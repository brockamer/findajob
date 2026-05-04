# Prerequisites

Everything you need before running the setup guide.

---

## Required Accounts and API Keys

### 1. OpenRouter
Used by: 10 of 11 pipeline roles — `job_scorer`, `resume_tailor`, `cover_letter_writer`, `briefing_writer`, `outreach_drafter`, `company_researcher`, `fit_analyst`, `recruiter_critic`, `resume_change_reviewer`, `network_analyst`, plus the default model.

- Sign up at https://openrouter.ai
- Create an API key
- Add to `data/.env` as `OPENROUTER_API_KEY`
- Models routed: `anthropic/claude-opus-4.7`, `anthropic/claude-sonnet-4.6`, `google/gemini-3-flash-preview`, `deepseek/deepseek-v3.2`, `perplexity/sonar-reasoning-pro`

### 2. Google AI (Gemini)
Used by: embedding model only (`gemini-embedding-001`) for RAG indexing of `candidate_context/`. Direct-Gemini chat models were retired from the pipeline in v0.4.0 — Gemini chat now routes through OpenRouter as well.

- Sign up at https://aistudio.google.com
- Create an API key
- Add to `data/.env` as `GOOGLE_API_KEY`
- Model: `gemini-embedding-001` (embedding endpoint; no OpenRouter equivalent)

### 3. RapidAPI feed (jobs-api14 or JSearch)
Used by: LinkedIn and Indeed job search in `triage.py` (via pluggable adapter)

- Sign up at https://rapidapi.com
- The onboarding interview's Section 3h recommends the right feed for your field
- Subscribe to your chosen feed (both have a free tier)
- Add API key to `data/.env` as `RAPIDAPI_KEY` (canonical, covers all RapidAPI feeds) — legacy per-adapter vars `JOBS_API14_KEY` / `JSEARCH_API_KEY` still work as fallback (#414)
- See `docs/setup/api-keys.md` for per-feed sign-up walkthroughs

### 4. Google Cloud — Sheets API + Gmail API

**Why:** The pipeline reads Gmail for job-alert emails (optional integration).

**Steps:**
1. Go to https://console.cloud.google.com
2. Create a new project (e.g. "findajob")
3. Enable **Gmail API**
4. Create **OAuth2 credentials** for Gmail:
   - Credentials → Create OAuth 2.0 Client ID → Desktop App
   - Download JSON → save as `config/gmail_oauth_client.json`
   - First run of triage.py will open a browser for OAuth consent
   - Token is cached in `config/gmail_token.json`

### 7. ntfy.sh (notifications)
Used by: `notify.py`

- No account required for basic use
- Go to https://ntfy.sh and pick a topic name (e.g. `yourname-jobsearch`)
- Download the ntfy app on your phone and subscribe to your topic
- Add topic to `data/.env` as `NTFY_TOPIC`
- Optional: self-host ntfy for privacy

---

## Required Tools

| Tool | Minimum version | Install |
|---|---|---|
| Python | 3.11+ | System package manager (apt) |
| aichat-ng | latest | See below |
| pandoc | 3.x | `apt install pandoc` |

### Installing aichat-ng

> **Docker users can skip this subsection** — `aichat-ng` is baked into the
> `ghcr.io/brockamer/findajob` image.

**Important:** This pipeline uses `aichat-ng` (the `blob42/aichat-ng` fork),
not the original `aichat`. They are different binaries.

```bash
# Check if already installed
/usr/local/bin/aichat-ng --version

# Install from the blob42 prebuilt release tarball (no Rust toolchain needed)
AICHAT_NG_VERSION=v0.31.0
AICHAT_NG_ARCH=x86_64-unknown-linux-musl
AICHAT_NG_SHA256=8e1f5a9cf09ae651168f2a425de20b2f6e8702072d47a7052c6229fa366aa57b

curl -fsSL -o /tmp/aichat-ng.tar.gz \
    "https://github.com/blob42/aichat-ng/releases/download/${AICHAT_NG_VERSION}/aichat-ng-${AICHAT_NG_VERSION}-${AICHAT_NG_ARCH}.tar.gz"
echo "${AICHAT_NG_SHA256}  /tmp/aichat-ng.tar.gz" | sha256sum -c -
tar -xzf /tmp/aichat-ng.tar.gz -C /tmp
sudo install -m 0755 /tmp/aichat-ng /usr/local/bin/aichat-ng
rm -f /tmp/aichat-ng.tar.gz /tmp/aichat-ng
```

For a different architecture or newer upstream version, check the
[blob42/aichat-ng releases](https://github.com/blob42/aichat-ng/releases)
page and update the three variables.

### Configuring aichat-ng

aichat-ng config lives at `~/.config/aichat_ng/config.yaml` (native install)
or `state/aichat_ng/config.yaml` (Docker install, mounted into the container).

See [configure.md](configure.md) for the full config template.

---

## Python Dependencies

The project uses [`uv`](https://docs.astral.sh/uv/) to manage Python and
dependencies (#126). Install once per machine:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
exec $SHELL
```

Then from the cloned repo:

```bash
uv sync
```

This provisions Python 3.12+ if absent, creates `.venv/`, and installs
the project + dev dependencies declared in `pyproject.toml`. Subsequent
commands use `uv run` (e.g., `uv run pytest`, `uv run python scripts/triage.py`).
No system-level pip install required.

> **Why uv:** the project requires Python 3.12+. `uv` provisions a
> compatible interpreter on hosts that ship with older Python (e.g.,
> Ubuntu 22.04 → 3.10), avoiding `--break-system-packages` and PEP-668
> conflicts. The Docker image pip-installs against a 3.12 base internally
> — different concern, image-only.

---

## LinkedIn Connections Export (optional but recommended)

The pipeline matches LinkedIn connections to job companies for network outreach.

1. Go to https://www.linkedin.com/mynetwork/invite-connect/connections/
2. Click "Manage synced and imported contacts" → "Export contacts"
3. Select "Connections" → Export
4. Save as `data/connections.csv`

The pipeline reads this file during prep to find warm contacts at target companies.
