# Linux Setup (Pop!_OS / Ubuntu) — native install

> **Recommended path for new installs is Docker, not native.** See [install-docker.md](install-docker.md).
> This guide remains for users running findajob directly on a Linux host without containers.

This guide covers a fresh install on a Debian-based Linux system. Tested on Pop!_OS 22.04 LTS.

---

## 1. System Dependencies

```bash
sudo apt update && sudo apt install -y \
  python3 python3-pip \
  pandoc \
  curl \
  git \
  build-essential  # needed for Rust/aichat-ng build
```

Verify:
```bash
python3 --version    # should be 3.11+
pandoc --version
```

---

## 2. Install aichat-ng (prebuilt from blob42/aichat-ng)

The Docker image and this native install both use the same prebuilt musl binary
from the `blob42/aichat-ng` fork. No Rust toolchain required.

```bash
AICHAT_NG_VERSION=v0.31.0
AICHAT_NG_ARCH=x86_64-unknown-linux-musl
AICHAT_NG_SHA256=8e1f5a9cf09ae651168f2a425de20b2f6e8702072d47a7052c6229fa366aa57b

curl -fsSL -o /tmp/aichat-ng.tar.gz \
    "https://github.com/blob42/aichat-ng/releases/download/${AICHAT_NG_VERSION}/aichat-ng-${AICHAT_NG_VERSION}-${AICHAT_NG_ARCH}.tar.gz"
echo "${AICHAT_NG_SHA256}  /tmp/aichat-ng.tar.gz" | sha256sum -c -
tar -xzf /tmp/aichat-ng.tar.gz -C /tmp
sudo install -m 0755 /tmp/aichat-ng /usr/local/bin/aichat-ng
rm -f /tmp/aichat-ng.tar.gz /tmp/aichat-ng
/usr/local/bin/aichat-ng --version
```

For a different architecture (arm64, etc.) or newer upstream version, check
[blob42/aichat-ng releases](https://github.com/blob42/aichat-ng/releases)
and update `AICHAT_NG_VERSION`, `AICHAT_NG_ARCH`, and `AICHAT_NG_SHA256`.

---

## 3. Clone the Repository

```bash
git clone https://github.com/yourname/findajob ~/findajob
cd ~/findajob
```

---

## 4. Install Python Dependencies

```bash
pip3 install --break-system-packages \
  google-api-python-client \
  google-auth-httplib2 \
  google-auth-oauthlib \
  requests \
  jsonschema \
  beautifulsoup4
```

---

## 5. Create Personal Config Files

```bash
cd ~/findajob
cp candidate_context/profile.md.example candidate_context/profile.md
cp config/jsearch_queries.txt.example config/jsearch_queries.txt
cp config/feed_urls.txt.example config/feed_urls.txt
cp config/target_companies.md.example config/target_companies.md
cp CLAUDE.local.md.example CLAUDE.local.md
cp data/.env.example data/.env && chmod 600 data/.env
```

Edit each file. See [configure.md](configure.md) for what to put in each.

---

## 6. Create Binary Path Config

Linux typically installs binaries in standard locations. Verify yours:

```bash
which python3    # likely /usr/bin/python3
which pandoc     # likely /usr/bin/pandoc
which aichat-ng  # likely /usr/local/bin/aichat-ng (installed in step 2)
```

If all paths match the defaults in `src/findajob/paths.py`, you don't need `config/paths.env`.
If any path differs:

```bash
cp config/paths.env.example config/paths.env
# Edit config/paths.env with the correct paths
```

---

## 7. Configure aichat-ng

Create the config directory:
```bash
mkdir -p ~/.config/aichat_ng/roles
```

Create `~/.config/aichat_ng/config.yaml`:
```yaml
# See docs/setup/configure.md for full config template
model: gemini:gemini-3-flash-preview

clients:
  - type: gemini
    api_key: ${GOOGLE_API_KEY}

  - type: claude
    api_key: ${ANTHROPIC_API_KEY}

  - type: openrouter
    api_key: ${OPENROUTER_API_KEY}

  - type: perplexity
    api_key: ${PERPLEXITY_API_KEY}

  - type: gemini
    name: gemini-embed
    api_key: ${GOOGLE_API_KEY}
    models:
      - name: gemini-embedding-001
        max_input_tokens: 2048

roles_dir: ~/findajob/config/roles
```

**Important:** API keys should come from environment variables (the `${VAR}` syntax), NOT be pasted directly into config.yaml. Source your `.env` before running aichat-ng or add to your shell profile:

```bash
# Add to ~/.bashrc or ~/.zshrc
set -a; source ~/findajob/data/.env; set +a
```

---

## 8. Set Up the Database

```bash
cd ~/findajob
python3 scripts/init_db.py
```

Verify:
```bash
sqlite3 data/pipeline.db ".tables"
# Expected: audit_log  feedback_log  jobs
```

---

## 9. Set Up Google Sheets

Copy your credential files (from [prerequisites.md](prerequisites.md)):
```bash
# Copy from your existing machine or download fresh:
cp /path/to/gsheets_creds.json config/gsheets_creds.json
echo "YOUR_SHEET_ID" > config/sheet_id.txt
```

Run the sheet formatter:
```bash
python3 scripts/setup_sheets.py
```

---

## 10. Set Up Gmail OAuth

```bash
cp /path/to/gmail_oauth_client.json config/gmail_oauth_client.json
```

First triage run will trigger browser-based OAuth consent and cache the token at `config/gmail_token.json`. If running headless, see [operations.md](../operations.md) for the headless OAuth flow.

---

## 11. Set Up systemd Scheduler

Create user service units for all scheduled jobs.

First, ensure the systemd user directory exists:
```bash
mkdir -p ~/.config/systemd/user
```

Run the systemd setup script (generates and loads all units):
```bash
bash scripts/bootstrap.sh --systemd
```

Or manually create service units. Example for the daily triage:

```ini
# ~/.config/systemd/user/findajob-triage.service
[Unit]
Description=findajob daily triage pipeline
After=network-online.target

[Service]
Type=oneshot
# TimeoutStartSec: max time the triage can run before systemd sends SIGTERM.
# A typical run takes 30-40 minutes depending on how many new jobs are scored
# and how many LLM calls hit. 3600 (1 hour) gives comfortable headroom.
# Keep this >= the longest legitimate run you have observed. The SIGTERM handler
# in triage.py logs a 'pipeline_terminated' event, so hitting the timeout will
# surface in notify.py health-check.
TimeoutStartSec=3600
ExecStart=/usr/bin/python3 /home/USERNAME/findajob/scripts/triage.py
WorkingDirectory=/home/USERNAME/findajob
EnvironmentFile=/home/USERNAME/findajob/data/.env
StandardOutput=append:/home/USERNAME/findajob/logs/triage.log
StandardError=append:/home/USERNAME/findajob/logs/triage.log
```

```ini
# ~/.config/systemd/user/findajob-triage.timer
[Unit]
Description=Run findajob triage daily at 7 AM

[Timer]
OnCalendar=*-*-* 07:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable and start:
```bash
systemctl --user enable findajob-triage.timer
systemctl --user start findajob-triage.timer
systemctl --user enable --now findajob-triage.timer
```

See [bootstrap.sh](../../scripts/bootstrap.sh) for all service unit definitions.

### Materials Viewer

Prep folders are served locally via a FastAPI web viewer running on `localhost:8080`. No external cloud sync or Google Drive dependencies. Markdown is rendered inline; `.docx` files are offered as downloads. This replaces the prior rclone-based Drive sync approach.

---

## 12. Verify the Install

```bash
# Test aichat-ng can reach the API
echo "Hello" | /usr/local/bin/aichat-ng -m gemini:gemini-3-flash-preview -S "Say hi back"

# Test DB
sqlite3 ~/findajob/data/pipeline.db "SELECT count(*) FROM jobs;"

# Test Sheet connection
python3 scripts/sync_sheet.py

# Run a single triage cycle (may take 30–60 min)
python3 scripts/triage.py
```

---

## Common Issues

**`aichat-ng: command not found`**
Verify it's at `/usr/local/bin/aichat-ng`. If installed elsewhere, update `config/paths.env`.

**`ModuleNotFoundError: google.oauth2`**
Re-run the pip install command from step 4.

**`FileNotFoundError: config/sheet_id.txt`**
You forgot to create this file. Run: `echo "YOUR_SHEET_ID" > config/sheet_id.txt`

**systemd timer not running**
Check: `systemctl --user status findajob-triage.timer`
Logs: `journalctl --user -u findajob-triage.service -f`

**Gmail OAuth browser won't open (headless machine)**
Run triage.py from a machine with a browser first to generate the token, then copy `config/gmail_token.json` to the headless machine.
