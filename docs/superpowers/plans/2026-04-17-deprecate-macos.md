# Deprecate macOS Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Purge all macOS/OS X/launchd/Homebrew/Apple-Silicon references from tracked files, making Linux (LXC + future Docker container) the single supported target.

**Architecture:** Pure doc/config refactor — no behavior change. Linux code paths already exist and are exercised in production; this PR removes the dual-platform scaffolding (parity tables, alternative paths, launchd commands) that has accumulated around them.

**Tech Stack:** Markdown docs, YAML/shell config, minor Python comment edits.

---

## Scope Summary

- **19 tracked files** contain macOS references (mapped via `git ls-files | xargs grep ...`).
- **1 file deleted** outright: `docs/setup/install-macos.md`.
- **2 files preserved as historical record**: `docs/superpowers/plans/2026-04-14-lxc-migration.md` and `docs/superpowers/specs/2026-04-15-doc-overhaul-design.md` — completed plans/specs are snapshots of past work and should not be rewritten.
- **No code-path `darwin` / `sys.platform` checks** exist — confirmed via grep. Purge is purely textual.
- **No `mac.brockbot.com` / `brockbot` references** exist in tracked files — confirmed via grep.

## Acceptance Criteria

1. `git ls-files | xargs grep -l -iE 'macos|mac os|os x|darwin|homebrew|/opt/homebrew|launchd|launchctl|launchagents|brockbot|cloud.?mac|pbcopy|pbpaste|osascript|m4 mac|mac mini|apple silicon'` returns only the two allowlisted historical files under `docs/superpowers/`.
2. `pytest` passes (no behavioral change expected).
3. `ruff check .` and `ruff format --check .` pass.
4. `mypy src/findajob` passes.
5. Pipeline continues to run on the LXC with no regression (triage / poller timers fire as scheduled).

---

## Task 1: Delete `docs/setup/install-macos.md`

**Files:**
- Delete: `docs/setup/install-macos.md`

- [ ] **Step 1: Delete the file**

```bash
git rm docs/setup/install-macos.md
```

- [ ] **Step 2: Verify gone**

```bash
ls docs/setup/install-macos.md 2>&1
# Expected: "No such file or directory"
```

---

## Task 2: Update `CLAUDE.md`

**File:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Remove dual-platform self-governance lines**

Current lines 15, 20, 95:
```
- [ ] aichat-ng config dir: macOS = `~/Library/Application Support/aichat_ng/`; Linux = `~/.config/aichat_ng/`
- [ ] macOS sed: `sed -i '' ...`; Linux sed: `sed -i ...` (no empty string)
| Scheduler | macOS: launchd agents; Linux: systemd user services (see docs/setup/install-linux.md) |
```

Edit to:
```
- [ ] aichat-ng config dir: `~/.config/aichat_ng/`
```

Delete the `sed -i ''` self-governance line entirely (no dual-platform caveat needed).

Change the Pipeline Context Table scheduler row to:
```
| Scheduler | systemd user services (see docs/setup/install-linux.md) |
```

---

## Task 3: Update `CLAUDE.local.md.example`

**File:**
- Modify: `CLAUDE.local.md.example`

- [ ] **Step 1: Replace the platform-conditional aichat-ng config block (lines 33–37)**

Current:
```markdown
## aichat-ng Config Location

[Uncomment the one that applies:]
# macOS: ~/Library/Application Support/aichat_ng/
# Linux: ~/.config/aichat_ng/
```

Replace with:
```markdown
## aichat-ng Config Location

`~/.config/aichat_ng/`
```

---

## Task 4: Update `README.md`

**File:**
- Modify: `README.md`

- [ ] **Step 1: Strip launchd from the scheduler tagline**

Line 5 currently:
```
Runs daily via a scheduler (launchd on macOS, systemd on Linux). No cloud infrastructure. No subscription. Costs ~$0.50–2/day in API usage depending on job volume.
```

Change to:
```
Runs daily via systemd user timers. No cloud infrastructure. No subscription. Costs ~$0.50–2/day in API usage depending on job volume.
```

- [ ] **Step 2: Fix scheduler table row**

Line 31 currently:
```
| Scheduler | launchd (macOS) / systemd (Linux) | Native, no extra daemons |
```

Change to:
```
| Scheduler | systemd user timers | Native, no extra daemons |
```

- [ ] **Step 3: Remove install-macos.md link from docs table**

Line 87 currently:
```
| [docs/setup/install-macos.md](docs/setup/install-macos.md) | macOS + Homebrew + launchd setup |
```

Delete that row entirely.

---

## Task 5: Simplify `config/paths.env.example`

**File:**
- Modify: `config/paths.env.example`

- [ ] **Step 1: Replace entire file with Linux-only template**

Replace full contents with:
```
# config/paths.env — binary path overrides
# Copy this file to config/paths.env and fill in the correct paths.
# This file is gitignored. Do not commit your actual paths.env.
#
# Linux defaults are already baked into src/findajob/paths.py.
# You only need this file if your binaries are in non-standard locations.

# Standard Linux (apt / system install):
# AICHAT_NG=/usr/local/bin/aichat-ng
# PANDOC=/usr/bin/pandoc
# RCLONE=/usr/bin/rclone
```

---

## Task 6: Clean `src/findajob/paths.py`

**File:**
- Modify: `src/findajob/paths.py`

- [ ] **Step 1: Drop macOS comments (lines 8 and 37)**

Line 8 currently:
```
Defaults are Linux-appropriate; macOS users set overrides in that file.
```

Change to:
```
Override defaults via config/paths.env if your binaries live elsewhere.
```

Lines 36–37 currently:
```python
# Binary paths — defaults are Linux-appropriate.
# macOS and other users: set these in config/paths.env (see config/paths.env.example).
```

Change to:
```python
# Binary paths — defaults are Linux-appropriate.
# Override via config/paths.env if your install is non-standard.
```

---

## Task 7: Rewrite `docs/setup/state-migration.md` as Linux-to-Linux

**File:**
- Modify: `docs/setup/state-migration.md`

- [ ] **Step 1: Rewrite intro (lines 5–10)**

Current:
```
This guide assumes:
- **Source machine**: existing running pipeline (e.g., macOS)
- **Target machine**: new machine (e.g., Pop!_OS Linux laptop)
- **Strategy**: parallel bring-up — keep source running until target is validated
```

Change to:
```
This guide assumes:
- **Source machine**: existing running pipeline (Linux host)
- **Target machine**: new Linux host
- **Strategy**: parallel bring-up — keep source running until target is validated
```

- [ ] **Step 2: Simplify Step 3 header (line 90)**

Change `### Step 3: Create Platform-Specific Config` → `### Step 3: Create Target-Side Config`.

- [ ] **Step 3: Replace Step 10 launchd commands (lines 196–203)**

Current:
```bash
# Unload all launchd agents (macOS source)
launchctl unload ~/Library/LaunchAgents/com.findajob.*.plist 2>/dev/null
# Or for the old naming scheme:
launchctl unload ~/Library/LaunchAgents/com.OWNER.jobpipeline.*.plist 2>/dev/null
```

Change to:
```bash
# Stop and disable all findajob timers on the source host
systemctl --user stop 'findajob-*.timer'
systemctl --user disable 'findajob-*.timer'
```

- [ ] **Step 4: Remove stale `aichat-ng config | Platform-specific (see below)` table row**

Line 35 currently:
```
| aichat-ng config | Platform-specific (see below) | Create new for target platform |
```

Change to:
```
| aichat-ng config | `~/.config/aichat_ng/config.yaml` | Create new on target |
```

- [ ] **Step 5: Remove "and update platform section" from CLAUDE.local.md row**

Line 36 currently:
```
| Personal CLAUDE context | `CLAUDE.local.md` | Copy and update platform section |
```

Change to:
```
| Personal CLAUDE context | `CLAUDE.local.md` | Copy |
```

- [ ] **Step 6: Remove "Update CLAUDE.local.md on the target to reflect Linux platform paths" line**

Line 106 — delete entirely.

---

## Task 8: Update `docs/setup/configure.md`

**File:**
- Modify: `docs/setup/configure.md`

- [ ] **Step 1: Remove macOS path from aichat-ng config location (line 125)**

Current:
```
- macOS: `~/Library/Application Support/aichat_ng/config.yaml`
```

Delete that line entirely (adjacent Linux line remains).

- [ ] **Step 2: Fix pre-commit-hook label reference (line 217)**

Current:
```
- Launchd/systemd label prefixes
```

Change to:
```
- systemd unit label prefixes
```

---

## Task 9: Update `docs/setup/prerequisites.md`

**File:**
- Modify: `docs/setup/prerequisites.md`

- [ ] **Step 1: Fix Python install hint (line 89)**

Current:
```
| Python | 3.11+ | System or Homebrew/apt |
```

Change to:
```
| Python | 3.11+ | System package manager (apt) |
```

- [ ] **Step 2: Remove macOS aichat-ng path (line 114)**

Current:
```
- macOS: `~/Library/Application Support/aichat_ng/config.yaml`
```

Delete the line entirely.

---

## Task 10: Update `docs/setup/pre-commit-hook.example.sh`

**File:**
- Modify: `docs/setup/pre-commit-hook.example.sh`

- [ ] **Step 1: Replace launchd mentions in comments (lines 29 and 48)**

Line 29 currently:
```
#   - Launchd/systemd label prefixes that include your name
```

Change to:
```
#   - systemd unit label prefixes that include your name
```

Line 48 currently:
```
    # Launchd/systemd labels
```

Change to:
```
    # systemd unit labels
```

---

## Task 11: Update `docs/operations.md`

**File:**
- Modify: `docs/operations.md`

- [ ] **Step 1: Fix aichat-ng config path reference (line 138)**

Current:
```
1. Edit `~/Library/Application Support/aichat_ng/config.yaml` (macOS) or `~/.config/aichat_ng/config.yaml` (Linux)
```

Change to:
```
1. Edit `~/.config/aichat_ng/config.yaml`
```

- [ ] **Step 2: Fix log-rotation line (line 174)**

Current:
```
`logs/pipeline.jsonl` grows without bound. Rotate manually or set up `logrotate` (Linux) / `newsyslog` (macOS) targeting `logs/*.jsonl` with weekly rotation, 4 copies kept.
```

Change to:
```
`logs/pipeline.jsonl` grows without bound. Rotate manually or set up `logrotate` targeting `logs/*.jsonl` with weekly rotation, 4 copies kept.
```

- [ ] **Step 3: Delete `## launchd Operations (macOS)` section (lines 209–224)**

Delete from the `## launchd Operations (macOS)` heading through the closing triple-backtick on line 224. Document ends after the Systemd Operations block.

---

## Task 12: Update `docs/notifications.md`

**File:**
- Modify: `docs/notifications.md`

- [ ] **Step 1: Remove macOS LaunchAgents path (line 119)**

Current:
```
- macOS: `~/Library/LaunchAgents/com.findajob.notify-*.plist`
```

Delete the line entirely.

- [ ] **Step 2: Collapse scheduler-alternative language (line 145)**

Current:
```
3. Add a new scheduler entry (launchd plist or systemd unit)
```

Change to:
```
3. Add a new systemd timer + service unit
```

---

## Task 13: Update `docs/claude-code.md`

**File:**
- Modify: `docs/claude-code.md`

- [ ] **Step 1: Fix infra-changes bullet (line 42)**

Current:
```
- **Infrastructure changes** — modifying scheduler configs, adding new launchd/systemd agents
```

Change to:
```
- **Infrastructure changes** — modifying scheduler configs, adding new systemd units
```

- [ ] **Step 2: Rename schedule section header (line 81)**

Current:
```
## Launchd/Systemd Schedule
```

Change to:
```
## Systemd Schedule
```

---

## Task 14: Update `docs/refactor_recommendations.md`

**File:**
- Modify: `docs/refactor_recommendations.md`

- [ ] **Step 1: Fix narrative mention of launchd (line 8)**

Current:
```
... RAG for candidate context was the right call. The launchd-driven daily run is solid. ...
```

Change to:
```
... RAG for candidate context was the right call. The systemd-driven daily run is solid. ...
```

- [ ] **Step 2: Fix log-rotation narrative (line 56)**

Current:
```
`pipeline.jsonl` is already 1.6 MB after a few weeks. No rotation. `launchd_poller_stderr.log` had 25 KB of the same error repeated. Add `newsyslog` entries or a weekly rotation cron.
```

Change to:
```
`pipeline.jsonl` is already 1.6 MB after a few weeks. No rotation. The poller stderr log had 25 KB of the same error repeated. Add a `logrotate` entry or a weekly rotation cron.
```

---

## Task 15: Update script comments

**Files:**
- Modify: `scripts/rescore_all.py`
- Modify: `scripts/ingest_form.py`

- [ ] **Step 1: Fix `scripts/rescore_all.py:6`**

Current:
```
Run manually — not a launchd agent.
```

Change to:
```
Run manually — not a scheduled job.
```

- [ ] **Step 2: Fix `scripts/ingest_form.py:22`**

Current:
```
Run manually or add to the poller launchd agent.
```

Change to:
```
Run manually or add to the poller systemd unit.
```

---

## Task 16: Update `.gitignore`

**File:**
- Modify: `.gitignore`

- [ ] **Step 1: Remove defensive `setup_launchd.sh` rule (line 39)**

The `scripts/setup_launchd.sh` script no longer exists and won't be re-created. The defensive ignore is no longer meaningful.

Delete line 39 (`scripts/setup_launchd.sh`).

---

## Task 17: Verify and commit

- [ ] **Step 1: Run the acceptance grep**

```bash
git ls-files | xargs grep -l -iE 'macos|mac os|os x|darwin|homebrew|/opt/homebrew|launchd|launchctl|launchagents|brockbot|cloud.?mac|pbcopy|pbpaste|osascript|m4 mac|mac mini|apple silicon' 2>/dev/null | grep -v '^docs/superpowers/'
```

Expected output: **empty** (only the two allowlisted historical files remain, and they are filtered out).

- [ ] **Step 2: Run test suite**

```bash
pytest -q
```

Expected: all tests pass, no new failures.

- [ ] **Step 3: Run linters**

```bash
ruff check .
ruff format --check .
mypy src/findajob
```

Expected: all pass (no code changes, just comment edits).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
Deprecate macOS support: single Linux target (#51)

Purge all macOS / OS X / launchd / Homebrew / Apple-Silicon references
from tracked files. Linux (LXC now, containerized later per #13) is the
sole supported target.

- Delete docs/setup/install-macos.md
- Collapse dual-platform callouts in CLAUDE.md, README.md, setup/*.md,
  operations.md, notifications.md, claude-code.md
- Simplify config/paths.env.example to Linux-only
- Drop macOS narrative from src/findajob/paths.py comments
- Rewrite state-migration.md as Linux-to-Linux
- Remove launchd Operations section from operations.md
- Fix script comments in rescore_all.py, ingest_form.py

No behavior change — tests + lint + mypy unchanged. Historical specs
under docs/superpowers/ preserved as archival record.

Closes #51.
EOF
)"
```

- [ ] **Step 5: Push and open PR**

```bash
git push -u origin feat/51-deprecate-macos
gh pr create --title "Deprecate macOS support: single Linux target (#51)" --body "$(cat <<'EOF'
## Summary

Single-target Linux purge per roadmap #58 Phase 1. Removes all macOS / OS X / launchd / Homebrew / Apple-Silicon references from tracked files. No behavior change.

## What changed

- **Deleted:** \`docs/setup/install-macos.md\` (obsolete — Mac host retired)
- **Collapsed dual-platform callouts** across CLAUDE.md, CLAUDE.local.md.example, README.md, docs/setup/*, docs/operations.md, docs/notifications.md, docs/claude-code.md, docs/refactor_recommendations.md
- **Simplified** \`config/paths.env.example\` to Linux-only
- **Cleaned** \`src/findajob/paths.py\` comments
- **Rewrote** state-migration.md for Linux-to-Linux moves
- **Removed** launchd Operations section from operations.md
- **Fixed** script header comments in rescore_all.py, ingest_form.py

## What was preserved

Two historical design docs under \`docs/superpowers/\` (the 2026-04-14 LXC migration plan and the 2026-04-15 doc-overhaul spec) retain their original macOS mentions as archival record of completed work.

## Verification

\`\`\`
git ls-files | xargs grep -l -iE 'macos|mac os|os x|darwin|homebrew|/opt/homebrew|launchd|launchctl|launchagents|brockbot|cloud.?mac|pbcopy|pbpaste|osascript|m4 mac|mac mini|apple silicon' | grep -v '^docs/superpowers/'
# empty
\`\`\`

Plus \`pytest\`, \`ruff\`, \`mypy\` all green.

Closes #51.
EOF
)"
```
