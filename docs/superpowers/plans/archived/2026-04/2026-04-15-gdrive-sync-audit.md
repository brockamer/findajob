---
**Archived 2026-04-19. self-titled ALL TASKS COMPLETE; Google Drive sync fixes shipped.**
---

# Google Drive Sync Audit & Fix Plan — COMPLETE

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all Google Drive sync issues so user edits on Drive are never clobbered, folder moves preserve edited content, and archived materials remain accessible for posterity.

**Architecture:** Replace `rclone bisync` with push-only `rclone copy --update` (local→Drive). Folder moves use `rclone move` within Drive (server-side, preserves user edits) instead of copy-from-local + purge. Local is authoritative for *new content*; Drive is authoritative for *edited content*.

**Tech Stack:** rclone, systemd, Python, SQLite, Google Drive

**Design Principles:**
- User edits on Drive are NEVER overwritten except on Regenerate (which is explicitly destructive)
- Folder moves within Drive are server-side (preserves edits, fast, no re-upload)
- Rejection/withdrawal archives materials — never deletes them
- `--update` flag on all pushes: only overwrites if local is newer than Drive

---

## Status: ALL TASKS COMPLETE

- Tasks 1-2: Shipped (architecture: push-only rclone + Drive-side moves)
- Task 3: Complete (zero path1/path2 files remain)
- Task 4: Complete (4 stale Drive copies purged 2026-04-15)
- Task 5: Complete (DB paths already correct; Synology already null; Oracle has gdrive_url)
- Tasks 6-7: Shipped (Rejected Applications Drive links + health checks)

**Remaining minor item:** Two AWS waitlisted jobs share one prep folder (`abbrev_title()` collision + same-batch timestamp). Low severity — can regenerate one if needed.
