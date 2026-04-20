---
**Archived 2026-04-19. design spec for the completed LXC migration plan.**
---

# Migration: Laptop → Proxmox LXC

**Date:** 2026-04-14
**Status:** Approved
**Motivation:** Laptop (Pop!_OS, "addy") has hardware issues. Move the findajob pipeline to a Proxmox LXC for reliability, snapshotting, and 24/7 uptime.

---

## Decision: LXC over VM or Docker

**Chosen:** Ubuntu 24.04 LXC on Proxmox (unprivileged, nesting enabled).

**Why not VM:** Heavier resource footprint (~1GB kernel overhead) for no benefit — this is a headless Python pipeline, not a desktop workload.

**Why not Docker:** The pipeline uses 10 systemd user timers. Docker doesn't run systemd. Rearchitecting the scheduler is significant work with zero job-search benefit. Containerization is a valid future goal but is deferred.

---

## 1. LXC Container Specifications

| Parameter | Value |
|-----------|-------|
| Template | Ubuntu 24.04 |
| Hostname | `findajob` |
| CPU cores | 2 |
| RAM | 2048 MB |
| Swap | 512 MB |
| Disk | 20 GB (local-lvm) |
| Network | DHCP on LAN bridge, hostname resolution |
| Unprivileged | Yes |
| Nesting | Enabled (`features: nesting=1`) |
| Start on boot | Yes |
| DNS | Inherit from Proxmox host |

**Sizing rationale:**
- Triage runs are CPU-bound for ~30-40 min/day, idle otherwise. 2 cores is sufficient.
- Peak RAM during triage (multiple aichat-ng subprocesses): ~800MB. 2GB gives headroom.
- Repo + DB + companies folders + system packages: ~2GB. 20GB leaves 18GB free.
- Nesting=1 is required for systemd to work inside an unprivileged LXC.

---

## 2. System Setup Inside the LXC

### 2.1 APT packages

```
python3 python3-pip pandoc rclone curl git build-essential
mosh tmux mc btop
nodejs npm
```

### 2.2 Claude Code

Install via apt from the Anthropic repository (recommended method).

### 2.3 Rust + aichat-ng

- Install rustup for the pipeline user (not root).
- Clone aichat repo, `cargo build --release`, install binary to `/usr/local/bin/aichat-ng`.

### 2.4 Python packages

```bash
pip3 install --break-system-packages \
  google-api-python-client google-auth-httplib2 google-auth-oauthlib \
  requests jsonschema beautifulsoup4
pip3 install --break-system-packages -e .
```

### 2.5 User account

- Create a user account with sudo.
- SSH key auth (Chromebook public key).
- `loginctl enable-linger <user>` — required for systemd user timers to run when not logged in.

---

## 3. State Migration from Laptop

### 3.1 What moves

| Category | Files | Size |
|----------|-------|------|
| Git repo | `~/Code/findajob/` (clone fresh, then overlay gitignored files) | ~5 MB |
| Database | `data/pipeline.db` | ~46 MB |
| Credentials | `data/.env`, `config/gsheets_creds.json`, `config/gmail_oauth_client.json`, `config/gmail_token.json` | <1 MB |
| Config (gitignored) | `config/sheet_id.txt`, `config/ntfy_topic.txt`, `config/form_responses_sheet_id.txt`, `config/jsearch_queries.txt`, `config/feed_urls.txt`, `config/target_companies.md`, `config/scoring_schema.json` | <1 MB |
| Candidate context | `candidate_context/profile.md`, `candidate_context/master_resume.md`, `candidate_context/voice_samples/` | ~60 KB |
| Company folders | `companies/` | ~10 MB |
| Systemd units | `~/.config/systemd/user/findajob-*` | <1 MB |
| aichat-ng config | `~/.config/aichat_ng/config.yaml`, `~/.config/aichat_ng/models-override.yaml` | ~55 KB |
| CLAUDE files | `CLAUDE.local.md` | <1 KB |

### 3.2 What does NOT move (rebuilt on target)

- `logs/` — fresh start
- `__pycache__/`, `.mypy_cache/`, `.pytest_cache/`
- RAG index — rebuilt via `aichat-ng --rag job_search_rag --rebuild-rag`
- aichat-ng `roles/` — symlinked to `config/roles/` on target
- DB WAL/journal files

### 3.3 Migration sequence

1. Stop all systemd timers on laptop (prevent writes during transfer).
2. Clone repo on LXC to `~/Code/findajob/`.
3. rsync gitignored state files from laptop.
4. rsync systemd units and aichat-ng config.
5. `chmod 600` on credentials.
6. Symlink aichat-ng roles: `ln -s ~/Code/findajob/config/roles ~/.config/aichat_ng/roles`
7. Rebuild RAG index.
8. Run validation checklist.

**Path consistency:** Use `~/Code/findajob/` on the LXC — same as laptop. Systemd units need zero path edits.

---

## 4. Validation Checklist

Run in order. Each layer gates the next.

### Layer 1: Infrastructure

- [ ] `python3 --version` → 3.12+
- [ ] `aichat-ng --version` → responds
- [ ] `pandoc --version` → responds
- [ ] `rclone version` → responds
- [ ] `sqlite3 data/pipeline.db "PRAGMA integrity_check;"` → `ok`
- [ ] `sqlite3 data/pipeline.db "SELECT count(*) FROM jobs;"` → matches laptop count

### Layer 2: Python environment

- [ ] `pip install -e .` succeeds
- [ ] `pytest` → all tests pass

### Layer 3: API connectivity

- [ ] `echo "test" | aichat-ng -m gemini:gemini-3-flash-preview` → responds
- [ ] `python3 scripts/sync_sheet.py` → prints row counts per tab
- [ ] `python3 scripts/notify.py send-raw "Migration test"` → notification arrives

### Layer 4: Full pipeline dry run

- [ ] `python3 scripts/triage.py` → completes, `pipeline_complete` in `logs/pipeline.jsonl`
- [ ] `python3 scripts/poll_flags.py` → completes without error

### Layer 5: Scheduler

- [ ] Enable all 10 timers
- [ ] `systemctl --user list-timers | grep findajob` → all 10 show next fire times
- [ ] Wait for one poller cycle, confirm via `journalctl --user -u findajob-poller.service`

### Layer 6: Google Drive sync

- [ ] `rclone bisync ~/Code/findajob/companies/ "gdrive:01 PROJECTS/Jobs To Apply For" --resync --max-delete 500`
- [ ] Confirm jobsync timer fires and subsequent syncs work without `--resync`

### Decommission gate

Only stop laptop timers permanently after Layer 5 passes. Keep laptop state intact for 1 week as fallback.

---

## 5. Dev Workflow

**Daily development:** Chromebook → `mosh <user>@findajob` → `tmux` → Claude Code.

**Code location:** `~/Code/findajob/` — identical to laptop. All CLAUDE.md paths work unchanged.

**CI:** Git push to GitHub, CI runs ruff + mypy + pytest (unchanged).

**CLAUDE.local.md:** Update platform table to reflect LXC details. Archive laptop platform table.

---

## 6. Backup Strategy

- Configure a nightly Proxmox vzdump backup job for the `findajob` LXC.
- Retention: 3 daily snapshots.
- Restores the entire LXC (OS + state + config) in minutes.

---

## 7. Gmail OAuth Token Refresh

- Token lasts months. When it expires, triage fails with an auth error.
- Fix: run OAuth flow on any machine with a browser, scp new `config/gmail_token.json` to LXC.
- Frequency: ~2-3 times per year.

---

## 8. Future Work (Deferred)

- **Containerization (Docker/Podman):** Viable once the pipeline is stable on the LXC. Would require replacing systemd timers with cron or an external scheduler. Not urgent.
- **aichat-ng env var migration:** API keys are currently hardcoded in `config.yaml`. Should migrate to `${VAR_NAME}` syntax. Non-blocking.
