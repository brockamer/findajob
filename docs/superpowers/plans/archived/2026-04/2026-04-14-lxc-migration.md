---
**Archived 2026-04-19. self-titled COMPLETE (2026-04-14); LXC migration shipped.**
---

# LXC Migration Implementation Plan — COMPLETE (2026-04-14)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the findajob pipeline from the laptop ("addy") to a Proxmox LXC container for reliability and 24/7 uptime.

**Architecture:** Ubuntu 24.04 unprivileged LXC with nesting (for systemd). Same repo path, same systemd timers, same user workflow. State transferred via rsync, validated layer-by-layer.

**Tech Stack:** Proxmox VE 8.4, Ubuntu 24.04 LXC, systemd user services, Python 3.12+, aichat-ng (Rust), Claude Code (apt)

**Spec:** `docs/superpowers/specs/2026-04-14-lxc-migration-design.md`

> **Substitution:** Replace `MYUSER` with your actual username and `LAPTOP` with your laptop hostname throughout this plan.

---

## Pre-flight: Record Laptop Baseline

Before touching anything, capture the numbers you'll validate against on the LXC.

- [ ] **Step 1: Record job count on laptop**

Run on laptop:
```bash
sqlite3 ~/Code/findajob/data/pipeline.db "SELECT count(*) FROM jobs;"
```
Write down the number. You'll compare this on the LXC in Task 5.

- [ ] **Step 2: Record timer list on laptop**

Run on laptop:
```bash
systemctl --user list-timers | grep findajob
```
Screenshot or copy the output. You'll compare this on the LXC in Task 6.

- [ ] **Step 3: Stop all timers on laptop**

This prevents writes to the DB during transfer.

Run on laptop:
```bash
systemctl --user stop findajob-{triage,poller,form-ingest,notify-stats,notify-health,notify-apply,notify-issues,notify-feedback,jobsync,rag-rebuild}.timer
```

Verify they're stopped:
```bash
systemctl --user list-timers | grep findajob
```
Expected: no output (all timers stopped).

---

## Task 1: Create the LXC on Proxmox

**Where:** Proxmox web UI or CLI on the Proxmox host.

- [ ] **Step 1: Download the Ubuntu 24.04 template (if not cached)**

Run on the Proxmox host shell:
```bash
pveam update
pveam download local ubuntu-24.04-standard_24.04-2_amd64.tar.zst
```

If the exact filename differs, list available templates:
```bash
pveam available --section system | grep ubuntu-24.04
```

- [ ] **Step 2: Create the LXC container**

Run on the Proxmox host shell. Pick an unused CTID (e.g., 110 — check `pct list` first):
```bash
pct list
```

Then create:
```bash
pct create 110 local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst \
  --hostname findajob \
  --cores 2 \
  --memory 2048 \
  --swap 512 \
  --rootfs local-lvm:20 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --unprivileged 1 \
  --features nesting=1 \
  --onboot 1 \
  --start 1
```

Adjust `--net0 bridge=vmbr0` to match your LAN bridge name if different.
Adjust the template filename if the downloaded name differs.

- [ ] **Step 3: Verify the container is running**

```bash
pct list
```
Expected: CTID 110, status `running`, hostname `findajob`.

- [ ] **Step 4: Verify hostname resolves from Chromebook**

From your Chromebook:
```bash
ping findajob
```

If it doesn't resolve, check your DNS/DHCP server picked up the new host. You may need to wait a minute or force a DHCP lease renewal inside the LXC:
```bash
pct exec 110 -- dhclient -r && pct exec 110 -- dhclient
```

---

## Task 2: Set Up User Account and SSH

**Where:** Proxmox host shell (via `pct exec`) and Chromebook.

- [ ] **Step 1: Enter the LXC shell**

```bash
pct exec 110 -- bash
```

- [ ] **Step 2: Create user account**

Inside the LXC:
```bash
adduser MYUSER
usermod -aG sudo MYUSER
```

Set a password when prompted (needed for sudo). You'll use SSH keys for login.

- [ ] **Step 3: Set up SSH key auth**

Inside the LXC:
```bash
mkdir -p /home/MYUSER/.ssh
chmod 700 /home/MYUSER/.ssh
```

From your Chromebook (in a separate terminal), copy your public key:
```bash
ssh-copy-id MYUSER@findajob
```

Or manually paste your Chromebook's `~/.ssh/id_ed25519.pub` (or `id_rsa.pub`) into `/home/MYUSER/.ssh/authorized_keys` on the LXC.

- [ ] **Step 4: Verify SSH from Chromebook**

```bash
ssh MYUSER@findajob
```

Expected: logged in without password prompt.

- [ ] **Step 5: Enable linger for systemd user services**

This is critical — without linger, all your timers stop when you disconnect.

Run as root inside the LXC (or via `pct exec`):
```bash
loginctl enable-linger MYUSER
```

Verify:
```bash
ls /var/lib/systemd/linger/
```
Expected: `MYUSER` listed.

Exit root shell. All remaining steps are run as `MYUSER` via SSH.

---

## Task 3: Install System Dependencies

**Where:** SSH into `findajob` as `MYUSER`.

- [ ] **Step 1: Install APT packages**

```bash
sudo apt update && sudo apt install -y \
  python3 python3-pip python3-venv \
  pandoc rclone curl git build-essential sqlite3 \
  mosh tmux mc btop \
  openssh-server
```

- [ ] **Step 2: Install Claude Code from Anthropic apt repo**

```bash
sudo apt update && sudo apt install -y curl gpg

curl -fsSL https://storage.googleapis.com/anthropic-packages/claude-code/claude-code-signing-key.asc | sudo gpg --dearmor -o /usr/share/keyrings/anthropic-keyring.gpg

echo "deb [signed-by=/usr/share/keyrings/anthropic-keyring.gpg] https://storage.googleapis.com/anthropic-packages/claude-code/debian stable main" | sudo tee /etc/apt/sources.list.d/anthropic.list > /dev/null

sudo apt update && sudo apt install -y claude-code
```

Verify:
```bash
claude --version
```

- [ ] **Step 3: Install Rust (as MYUSER, not root)**

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source ~/.cargo/env
```

Add to shell profile so it persists:
```bash
echo 'source ~/.cargo/env' >> ~/.bashrc
```

- [ ] **Step 4: Build and install aichat-ng**

```bash
git clone https://github.com/sigoden/aichat /tmp/aichat-ng-build
cd /tmp/aichat-ng-build
cargo build --release
sudo cp target/release/aichat /usr/local/bin/aichat-ng
rm -rf /tmp/aichat-ng-build
```

Verify:
```bash
aichat-ng --version
```

- [ ] **Step 5: Verify all binaries**

```bash
python3 --version   # 3.12+
pandoc --version     # any
rclone version       # any
aichat-ng --version  # any
claude --version     # any
mosh --version       # any
```

All six should respond without errors.

---

## Task 4: Transfer State from Laptop

**Where:** Run rsync commands from the LXC (pulling from laptop). Or run from laptop pushing to LXC — whichever direction SSH works. Examples below assume running from the LXC, pulling from laptop.

If SSH from LXC → laptop doesn't work, run these from the laptop instead, replacing `MYUSER@LAPTOP:` with the source and `MYUSER@findajob:` with the destination.

- [ ] **Step 1: Clone the repo on the LXC**

```bash
mkdir -p ~/Code
cd ~/Code
git clone https://github.com/MYUSER/findajob.git
cd findajob
```

- [ ] **Step 2: rsync gitignored state files from laptop**

```bash
rsync -avz MYUSER@LAPTOP:~/Code/findajob/data/pipeline.db ~/Code/findajob/data/
rsync -avz MYUSER@LAPTOP:~/Code/findajob/data/.env ~/Code/findajob/data/
rsync -avz MYUSER@LAPTOP:~/Code/findajob/data/connections.csv ~/Code/findajob/data/
```

- [ ] **Step 3: rsync config files from laptop**

```bash
rsync -avz MYUSER@LAPTOP:~/Code/findajob/config/gsheets_creds.json ~/Code/findajob/config/
rsync -avz MYUSER@LAPTOP:~/Code/findajob/config/gmail_oauth_client.json ~/Code/findajob/config/
rsync -avz MYUSER@LAPTOP:~/Code/findajob/config/gmail_token.json ~/Code/findajob/config/
rsync -avz MYUSER@LAPTOP:~/Code/findajob/config/sheet_id.txt ~/Code/findajob/config/
rsync -avz MYUSER@LAPTOP:~/Code/findajob/config/ntfy_topic.txt ~/Code/findajob/config/
rsync -avz MYUSER@LAPTOP:~/Code/findajob/config/form_responses_sheet_id.txt ~/Code/findajob/config/
rsync -avz MYUSER@LAPTOP:~/Code/findajob/config/jsearch_queries.txt ~/Code/findajob/config/
rsync -avz MYUSER@LAPTOP:~/Code/findajob/config/feed_urls.txt ~/Code/findajob/config/
rsync -avz MYUSER@LAPTOP:~/Code/findajob/config/target_companies.md ~/Code/findajob/config/
rsync -avz MYUSER@LAPTOP:~/Code/findajob/config/scoring_schema.json ~/Code/findajob/config/
```

- [ ] **Step 4: rsync candidate context from laptop**

```bash
rsync -avz MYUSER@LAPTOP:~/Code/findajob/candidate_context/ ~/Code/findajob/candidate_context/
```

- [ ] **Step 5: rsync company folders from laptop**

```bash
rsync -avz MYUSER@LAPTOP:~/Code/findajob/companies/ ~/Code/findajob/companies/
```

- [ ] **Step 6: rsync CLAUDE.local.md**

```bash
rsync -avz MYUSER@LAPTOP:~/Code/findajob/CLAUDE.local.md ~/Code/findajob/
```

- [ ] **Step 7: rsync aichat-ng config from laptop**

```bash
mkdir -p ~/.config/aichat_ng
rsync -avz MYUSER@LAPTOP:~/.config/aichat_ng/config.yaml ~/.config/aichat_ng/
rsync -avz MYUSER@LAPTOP:~/.config/aichat_ng/models-override.yaml ~/.config/aichat_ng/
```

- [ ] **Step 8: Set permissions on credentials**

```bash
chmod 600 ~/Code/findajob/data/.env
chmod 600 ~/Code/findajob/config/gsheets_creds.json
chmod 600 ~/Code/findajob/config/gmail_oauth_client.json
chmod 600 ~/Code/findajob/config/gmail_token.json
```

- [ ] **Step 9: Create logs directory**

```bash
mkdir -p ~/Code/findajob/logs
```

- [ ] **Step 10: Symlink aichat-ng roles**

```bash
ln -s ~/Code/findajob/config/roles ~/.config/aichat_ng/roles
```

Verify:
```bash
ls ~/.config/aichat_ng/roles/
```
Expected: 9 `.md` role files listed.

---

## Task 5: Install Python Environment and Validate Data

**Where:** SSH into `findajob` as `MYUSER`.

- [ ] **Step 1: Install Python dependencies**

```bash
cd ~/Code/findajob
pip3 install --break-system-packages \
  google-api-python-client \
  google-auth-httplib2 \
  google-auth-oauthlib \
  requests \
  jsonschema \
  beautifulsoup4
```

- [ ] **Step 2: Install findajob package in editable mode**

```bash
pip3 install --break-system-packages -e .
```

- [ ] **Step 3: Run tests**

```bash
pytest
```

Expected: all tests pass (318+). If any fail, diagnose before proceeding.

- [ ] **Step 4: Validate database integrity**

```bash
sqlite3 data/pipeline.db "PRAGMA integrity_check;"
```
Expected: `ok`

```bash
sqlite3 data/pipeline.db "SELECT count(*) FROM jobs;"
```
Expected: matches the number you recorded in Pre-flight Step 1.

- [ ] **Step 5: Delete stale WAL/journal files if present**

```bash
rm -f data/pipeline.db-journal data/pipeline.db-wal
```

---

## Task 6: Validate API Connectivity

**Where:** SSH into `findajob` as `MYUSER`.

- [ ] **Step 1: Source environment variables**

aichat-ng needs API keys in the environment:
```bash
set -a; source ~/Code/findajob/data/.env; set +a
```

Add this to your `.bashrc` so it's always loaded:
```bash
echo 'set -a; source ~/Code/findajob/data/.env; set +a' >> ~/.bashrc
```

- [ ] **Step 2: Test aichat-ng (Gemini)**

```bash
echo "test" | aichat-ng -m gemini:gemini-3-flash-preview
```
Expected: an LLM response (any text back means the API key works).

- [ ] **Step 3: Test Google Sheets connectivity**

```bash
cd ~/Code/findajob
python3 scripts/sync_sheet.py
```
Expected: prints row counts per tab (Sheet1, Dashboard, Review, Waitlist, Active).

- [ ] **Step 4: Test ntfy notifications**

```bash
python3 scripts/notify.py send-raw "LXC migration test"
```
Expected: notification arrives on your phone.

- [ ] **Step 5: Rebuild RAG index**

```bash
aichat-ng --rag job_search_rag --rebuild-rag
```
Expected: completes in 5-10 minutes without errors.

---

## Task 7: Full Pipeline Dry Run

**Where:** SSH into `findajob` as `MYUSER`.

- [ ] **Step 1: Run triage**

```bash
cd ~/Code/findajob
python3 scripts/triage.py 2>&1 | tee /tmp/triage_validation.log
```

This takes 30-60 minutes. Watch for errors. When complete:

```bash
grep -i "error\|traceback\|exception" /tmp/triage_validation.log
```
Expected: no output (no errors).

```bash
tail -5 logs/pipeline.jsonl
```
Expected: `pipeline_complete` event in the last few lines.

- [ ] **Step 2: Run poll_flags**

```bash
python3 scripts/poll_flags.py
```
Expected: completes without error. Output shows tabs read and any flags processed.

---

## Task 8: Install and Enable Systemd Timers

**Where:** SSH into `findajob` as `MYUSER`.

- [ ] **Step 1: Run bootstrap.sh to generate systemd units**

The bootstrap script generates all 10 service/timer pairs with correct paths for the current user and repo location:

```bash
cd ~/Code/findajob
bash scripts/bootstrap.sh --systemd
```

Expected: all unit files written, timers enabled.

- [ ] **Step 2: Start all timers**

```bash
systemctl --user start findajob-{triage,poller,form-ingest,notify-stats,notify-health,notify-apply,notify-issues,notify-feedback,jobsync,rag-rebuild}.timer
```

- [ ] **Step 3: Verify all timers are active**

```bash
systemctl --user list-timers | grep findajob
```

Expected: 10 timers listed, each showing a `NEXT` fire time. Compare against the laptop output from Pre-flight Step 2.

- [ ] **Step 4: Wait for one poller cycle and verify**

Wait ~30 minutes for the poller timer to fire, then:

```bash
journalctl --user -u findajob-poller.service --since "30 min ago"
```

Expected: log output showing the poller ran successfully.

---

## Task 9: Initialize Google Drive Bisync

**Where:** SSH into `findajob` as `MYUSER`.

- [ ] **Step 1: Configure rclone remote for Google Drive**

If rclone isn't already configured with a `gdrive` remote:

```bash
rclone config
```

Choose `New remote` → name it `gdrive` → type `Google Drive` → follow the OAuth flow.

Note: this requires a browser for OAuth. If headless, use `rclone authorize` on a machine with a browser and paste the token back. See `rclone config` docs.

If you already have an rclone config on the laptop:
```bash
rsync -avz MYUSER@LAPTOP:~/.config/rclone/rclone.conf ~/.config/rclone/rclone.conf
```

- [ ] **Step 2: Run initial bisync with --resync**

```bash
rclone bisync ~/Code/findajob/companies/ "gdrive:01 PROJECTS/Jobs To Apply For" --resync --max-delete 500
```

Expected: completes without errors. Local wins any conflicts on this initial sync.

- [ ] **Step 3: Verify the jobsync timer fires**

The timer runs every 15 minutes. Wait for it, then:

```bash
journalctl --user -u findajob-jobsync.service --since "15 min ago"
```

Expected: bisync ran successfully without `--resync`.

---

## Task 10: Set Up Mosh Workflow from Chromebook

**Where:** Chromebook.

- [ ] **Step 1: Verify mosh works**

```bash
mosh MYUSER@findajob
```

Expected: connected. The mosh session survives network changes and sleep/wake on the Chromebook.

- [ ] **Step 2: Set up tmux on the LXC**

Inside the mosh session:
```bash
tmux new -s work
```

This is your persistent dev session. Detach with `Ctrl-b d`, reattach with `tmux attach -t work`.

- [ ] **Step 3: Test Claude Code inside tmux**

Inside the tmux session:
```bash
cd ~/Code/findajob
claude
```

Expected: Claude Code starts and loads CLAUDE.md context.

---

## Task 11: Configure Proxmox Backup

**Where:** Proxmox web UI.

- [ ] **Step 1: Create a backup job**

In the Proxmox UI:
1. Go to Datacenter → Backup
2. Click Add
3. Storage: select your backup storage (e.g., `local`, or a NAS mount)
4. Schedule: daily
5. Selection mode: Include → select CTID 110 (findajob)
6. Retention: keep-last = 3
7. Compression: ZSTD
8. Mode: Snapshot

- [ ] **Step 2: Run a test backup**

Click "Run now" on the backup job, or from the Proxmox host shell:
```bash
vzdump 110 --compress zstd --storage local
```

Expected: backup completes. Verify it appears under the LXC's Backup tab in the UI.

---

## Task 12: Update CLAUDE.local.md for LXC

**Where:** SSH into `findajob` as `MYUSER`, or via Claude Code.

- [ ] **Step 1: Update the platform table in CLAUDE.local.md**

Replace the "Pop!_OS Linux laptop" platform table with:

```markdown
## Platform (Proxmox LXC "findajob" — active machine)

| Item | Value |
|------|-------|
| Python | `/usr/bin/python3` |
| pip | `pip3 install --break-system-packages` |
| aichat-ng | `/usr/local/bin/aichat-ng` |
| pandoc | `/usr/bin/pandoc` |
| rclone | `/usr/bin/rclone` |
| aichat-ng config | `~/.config/aichat_ng/config.yaml` |
| Pipeline root | `~/Code/findajob/` |
| sed | `sed -i ...` (Linux — no empty string) |
| Scheduler | systemd user services (`~/.config/systemd/user/`) |
| Host | Proxmox LXC, Ubuntu 24.04, 2 vCPU, 2GB RAM |
```

- [ ] **Step 2: Archive the Mac Mini platform table**

Either remove it or wrap it in a comment/collapsed section if you want to keep it for reference.

- [ ] **Step 3: Commit**

```bash
cd ~/Code/findajob
git add CLAUDE.local.md
git commit -m "Update CLAUDE.local.md for Proxmox LXC migration"
```

---

## Task 13: Decommission Laptop (After 1 Week)

**Do NOT execute this task immediately.** Wait at least 1 week with the LXC running successfully before decommissioning the laptop.

**Gate criteria — all must be true:**
- [ ] At least 7 consecutive daily triage runs completed without error on the LXC
- [ ] Google Sheet sync working correctly from LXC (spot-check Dashboard)
- [ ] At least 3 ntfy notifications received from the LXC
- [ ] At least 1 `prep_application.py` run completed on the LXC (flag a job on Dashboard)
- [ ] Google Drive bisync running without errors for 7 days
- [ ] No errors in `journalctl --user -u findajob-triage.service --since "7 days ago"`

Once all gates pass:

- [ ] **Step 1: Permanently disable laptop timers**

Run on laptop:
```bash
systemctl --user disable findajob-{triage,poller,form-ingest,notify-stats,notify-health,notify-apply,notify-issues,notify-feedback,jobsync,rag-rebuild}.timer
```

- [ ] **Step 2: Keep laptop DB as cold backup**

Don't delete `~/Code/findajob/data/pipeline.db` on the laptop. It's a free cold backup of the pre-migration state.
