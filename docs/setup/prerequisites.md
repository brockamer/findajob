# Prerequisites

Everything you need before running the setup guide.

---

## Required Accounts and API Keys

### 1. Anthropic (Claude)
Used by: `resume_tailor`, `cover_letter_writer`, `briefing_writer`, `outreach_drafter`

- Sign up at https://console.anthropic.com
- Create an API key
- Add to `data/.env` as `ANTHROPIC_API_KEY`
- Models used: `claude-opus-4-6:thinking`, `claude-sonnet-4-6`

### 2. OpenRouter
Used by: `job_scorer` (DeepSeek v3.2)

- Sign up at https://openrouter.ai
- Create an API key
- Add to `data/.env` as `OPENROUTER_API_KEY`
- Model: `deepseek/deepseek-v3.2` — very cheap, accurate for structured JSON

### 3. Google AI (Gemini)
Used by: default aichat-ng model, `resume_change_reviewer`, `network_analyst`, embedding model

- Sign up at https://aistudio.google.com
- Create an API key
- Add to `data/.env` as `GOOGLE_API_KEY`
- Models: `gemini-3-flash-preview`, `gemini-embedding-001`

### 4. Perplexity
Used by: `company_researcher`

- Sign up at https://www.perplexity.ai
- Create an API key
- Add to `data/.env` as `PERPLEXITY_API_KEY`
- Model: `sonar-reasoning-pro` (real-time web access with reasoning)

### 5. RapidAPI — jobs-api14
Used by: LinkedIn and Indeed job search in `triage.py`

- Sign up at https://rapidapi.com
- Subscribe to **jobs-api14** (has a free tier)
- Add API key to `data/.env` as `RAPIDAPI_KEY`

### 6. Google Cloud — Sheets API + Gmail API

**Why:** The pipeline reads/writes Google Sheets and reads Gmail for job emails.

**Steps:**
1. Go to https://console.cloud.google.com
2. Create a new project (e.g. "findajob")
3. Enable **Google Sheets API** and **Gmail API**
4. Create a **Service Account** for Sheets access:
   - IAM → Service Accounts → Create
   - Download JSON key → save as `config/gsheets_creds.json`
   - Share your Google Sheet with the service account email (Editor role)
5. Create **OAuth2 credentials** for Gmail:
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

```bash
pip3 install --break-system-packages \
  google-api-python-client \
  google-auth-httplib2 \
  google-auth-oauthlib \
  requests \
  jsonschema
```

No virtualenv needed. The pipeline uses the system Python directly.

---

## Google Sheet Setup

1. Create a new Google Sheet (blank)
2. Copy the Sheet ID from the URL: `https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit`
3. Save to `config/sheet_id.txt`
4. Share the sheet with your service account email (from `config/gsheets_creds.json`)
5. Run `python3 scripts/setup_sheets.py` to create tabs, headers, and formatting

---

## LinkedIn Connections Export (optional but recommended)

The pipeline matches LinkedIn connections to job companies for network outreach.

1. Go to https://www.linkedin.com/mynetwork/invite-connect/connections/
2. Click "Manage synced and imported contacts" → "Export contacts"
3. Select "Connections" → Export
4. Save as `data/connections.csv`

The pipeline reads this file during prep to find warm contacts at target companies.
