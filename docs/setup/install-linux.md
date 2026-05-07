# Linux Setup (Pop!_OS / Ubuntu) — native install

> **Recommended path for new installs is Docker, not native.** See [install-docker.md](install-docker.md).
> This guide remains for users running findajob directly on a Linux host without containers.

This guide covers a fresh install on a Debian-based Linux system. Tested on Pop!_OS 22.04 LTS.

---

## 1. System Dependencies

```bash
sudo apt update && sudo apt install -y \
  pandoc \
  curl \
  git

# Install uv — manages Python + the venv for findajob (#126).
curl -LsSf https://astral.sh/uv/install.sh | sh
exec $SHELL  # reload shell to pick up `uv` on PATH
```

Verify:
```bash
uv --version         # 0.4+
pandoc --version
```

`uv` provisions Python 3.12+ on demand for the project's venv; you don't
need a system-wide Python install. (The Docker image uses pip on a
Python 3.12 base — different concern, internal to the image.)

---

## 2. Clone the Repository

```bash
git clone https://github.com/yourname/findajob ~/findajob
cd ~/findajob
```

---

## 3. Install Python Dependencies

`uv` reads `pyproject.toml` and installs the project + every transitive
dependency into a project-local venv at `.venv/`:

```bash
uv sync
```

Subsequent commands (pytest, ruff, mypy, uvicorn, scripts/*) prefix with
`uv run` so they execute against the venv:

```bash
uv run pytest
uv run python scripts/triage.py
```

You don't need to `source .venv/bin/activate` — `uv run` handles it. If
you prefer the activated-shell flow, `source .venv/bin/activate` works
the conventional way.

---

## 4. Create Personal Config Files

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

## 5. Create Binary Path Config

Linux typically installs binaries in standard locations. Verify yours:

```bash
which python3    # likely /usr/bin/python3
which pandoc     # likely /usr/bin/pandoc
```

If all paths match the defaults in `src/findajob/paths.py`, you don't need `config/paths.env`.
If any path differs:

```bash
cp config/paths.env.example config/paths.env
# Edit config/paths.env with the correct paths
```

---

## 6. Set Up the Database

```bash
cd ~/findajob
uv run python scripts/init_db.py
```

Verify:
```bash
sqlite3 data/pipeline.db ".tables"
# Expected: audit_log  feedback_log  jobs
```

---

## 9. Configure Gmail integration (optional)

If you want findajob to ingest LinkedIn (and other) job-alert emails from your Gmail, follow [`gmail.md`](gmail.md) after the stack is up. The pipeline runs cleanly without Gmail integration — Greenhouse / Ashby / Lever direct fetches and RapidAPI LinkedIn search cover most ingestion volume.

---

## 10. Set Up systemd Scheduler

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

## Verify the Install

```bash
# Test the OpenRouter wrapper can reach the API
uv run python -c "from findajob.llm.openrouter import complete; print(complete(role='job_scorer', prompt='hi', max_tokens=8).text)"

# Test DB
sqlite3 ~/findajob/data/pipeline.db "SELECT count(*) FROM jobs;"

# Run a single triage cycle (may take 30–60 min)
uv run python scripts/triage.py
```

---

## Common Issues

**systemd timer not running**
Check: `systemctl --user status findajob-triage.timer`
Logs: `journalctl --user -u findajob-triage.service -f`

