---
**Archived 2026-04-19. refs #18, #21 — both closed; doc overhaul work shipped.**
---

# Documentation Overhaul — Design Spec

**Issue:** #18
**Date:** 2026-04-15
**Scope:** Fix all stale references, add missing features, rewrite obsolete sections across 8 doc files.

---

## Change Manifest

Each fix below is a discrete, verifiable edit. Grouped by file. Source of truth for each change is the running code (checked against actual imports, DB schema, sync_sheet.py headers, setup_sheets.py dropdowns, poll_flags.py logic, bootstrap.sh service templates, and notify.py dispatch table).

### 1. `docs/architecture.md`

| Line(s) | Current | Correct | Source |
|---------|---------|---------|--------|
| 91 | `company_researcher (Perplexity sonar-pro)` | `company_researcher (Perplexity sonar-reasoning-pro)` | prep_application.py:147 |
| 101 | `rclone bisync: companies/ → Google Drive` | `rclone copy: companies/ → Google Drive` | bootstrap.sh:397, refactor_recommendations.md:37 |
| 125-126 | Stage list missing `prep_in_progress`, `waitlisted` | Add both to the stage enum comment | sync_sheet.py:136,164; DB `SELECT DISTINCT stage` |
| 184-201 | Dashboard A-L, D=relevance_score, E=title | Dashboard A-N, D=fit_score, E=probability_score, F=relevance_score, G=title | sync_sheet.py DASH_HEADERS (lines 67-82) |
| 184 | `Dashboard — Actionable Queue (A–L)` | `Dashboard — Actionable Queue (A–N)` | 14 columns in DASH_HEADERS |
| 202-203 | STATUS lifecycle missing Prep in Progress, Regenerate, Waitlist | Add all 9 STATUS values | setup_sheets.py STATUS_OPTIONS (lines 74-83) |
| 205 | `folder move to _done/` | `folder move to _rejected/` | poll_flags.py:129 |
| 205 | `immediate rclone bisync` | `immediate rclone sync` | poll_flags.py:142-144 uses move+copy |
| 217 | `rclone bisync (not unidirectional)` rationale row | Rewrite: `rclone copy --update (push-only)` with correct rationale | refactor_recommendations.md:37 |
| NEW | No Review tab section | Add Review tab (A-H): STATUS(Promote), REJECT_REASON, fingerprint, title, company, score_flag_reason, source, date | sync_sheet.py REVIEW_HEADERS (lines 300-309) |
| NEW | No Waitlist tab section | Add Waitlist tab (A-K): STATUS(Reactivate), REJECT_REASON, fingerprint, title, company, relevance_score, location, remote_status, ai_notes, date, blocking_app | sync_sheet.py WAITLIST_HEADERS (lines 323-335) |
| NEW | No Rejected Applications tab section | Add Rejected Applications tab (A-H): title, company, reject_reason, applied_date, rejected_date, fit_score, probability_score, ai_notes | sync_sheet.py REJECTED_APPS_HEADERS (lines 470-479) |

### 2. `docs/google-sheets.md`

| Line(s) | Current | Correct | Source |
|---------|---------|---------|--------|
| 44-58 | Dashboard A-L, D=relevance_score, E=title | Dashboard A-N: STATUS, REJECT_REASON, fingerprint(hidden), fit_score, probability_score, relevance_score, title(hyperlink), company, location, remote_status, known_contacts, comp_estimate, ai_notes, date_found | sync_sheet.py DASH_HEADERS |
| 62-74 | STATUS dropdown: 6 values | 9 values: Flag for Prep, Prep in Progress, Regenerate, Ready to Apply, Waitlist, Applied, Interviewing, Offer, Withdrew | setup_sheets.py STATUS_OPTIONS |
| 88 | `moved to companies/_done/` | `moved to companies/_rejected/` | poll_flags.py:129 |
| 89 | `rclone bisync fires immediately` | `rclone sync fires immediately` | poll_flags.py:142-144 |
| NEW | No Review tab section | Add Review tab section with columns, filter, and Promote workflow | sync_sheet.py:298-400, poll_flags.py:389-445 |
| NEW | No Waitlist tab section | Add Waitlist tab section with columns, filter, Reactivate/reject workflow, blocking_app | sync_sheet.py:402-465, poll_flags.py:354-358,447-504 |
| NEW | No Rejected Applications tab | Add section: post-apply rejections, A-H columns | sync_sheet.py:469-533 |

### 3. `docs/scripts-reference.md`

| Line(s) | Current | Correct | Source |
|---------|---------|---------|--------|
| 5 | `import ... from scripts/paths.py` | `import ... from findajob.paths` | Every script's imports |
| 6 | `Never hardcode binary paths ... add overrides to config/paths.env` | Keep this sentence, just fix the import reference | — |
| 42 | `triggers rclone bisync` | `pushes folder via rclone copy` | prep_application.py:366-368 |
| 50-53 | poll_flags reads `Dashboard!A2:C10000` only | Reads Dashboard + Review + Waitlist tabs | poll_flags.py:229,394,452 |
| 53 | `moves prep folder to _done/` | `moves prep folder to _rejected/` | poll_flags.py:129 |
| 53 | `fires rclone bisync` | `fires rclone sync` | poll_flags.py:142-144 |
| 63-65 | Dashboard filter missing `prep_in_progress` | Add `prep_in_progress` to the filter stages | sync_sheet.py:235 |
| 88-98 | notify.py: 5 subcommands | 7 subcommands: add `send-raw` and `ci-check` | notify.py COMMANDS dict (lines 550-557) |

### 4. `docs/operations.md`

| Line(s) | Current | Correct | Source |
|---------|---------|---------|--------|
| 15 | `moves to _done/` | `moves to _rejected/` | poll_flags.py:129 |
| 157-170 | Google Drive Sync section: all `rclone bisync` | Rewrite for `rclone copy --update` | bootstrap.sh:397, refactor_recommendations.md:37 |

### 5. `docs/setup/install-linux.md`

| Line(s) | Current | Correct | Source |
|---------|---------|---------|--------|
| 57-62 | pip install missing `beautifulsoup4` | Add `beautifulsoup4` | pyproject.toml:12 |
| 94 | `defaults in scripts/paths.py` | `defaults in src/findajob/paths.py` | actual file location |
| 200 | `python3 scripts/bootstrap.py --systemd` | `bash scripts/bootstrap.sh --systemd` | scripts/bootstrap.sh:2 |
| 253-271 | Entire bisync initialization section | Rewrite for `rclone copy --update` architecture: no bisync state, no `--resync`, simpler model | bootstrap.sh:387-397 |

### 6. `docs/setup/install-macos.md`

| Line(s) | Current | Correct | Source |
|---------|---------|---------|--------|
| 51-57 | pip install missing `beautifulsoup4` | Add `beautifulsoup4` | pyproject.toml:12 |
| 200 | `rclone bisync` in scheduler table | `rclone copy --update` | bootstrap.sh:397 |

### 7. `docs/notifications.md`

| Line(s) | Current | Correct | Source |
|---------|---------|---------|--------|
| NEW | Missing `send-raw` subcommand | Add section: `send-raw <title> <body>` — sends arbitrary notification | notify.py:503-506 |
| NEW | Missing `ci-check` subcommand | Add section: `ci-check` — checks latest CI run, alerts on failure | notify.py:510-546 |
| 89-95 | Schedule table missing ci-check | Add ci-check to schedule table (or note it's on-demand only) | notify.py COMMANDS |

### 8. `docs/setup/configure.md`

| Line(s) | Current | Correct | Source |
|---------|---------|---------|--------|
| 101 | `defaults in scripts/paths.py` | `defaults in src/findajob/paths.py` | actual file location |

### 9. `docs/setup/prerequisites.md`

| Line(s) | Current | Correct | Source |
|---------|---------|---------|--------|
| 39 | `Model: sonar-pro` | `Model: sonar-reasoning-pro` | prep_application.py:147 |

---

## Implementation Approach

Single branch, single commit per logical group (HIGH fixes, then MEDIUM, then LOW). Alternatively, one commit for all — the changes are all doc-only and internally consistent.

Verification: after all edits, grep the entire `docs/` directory for known stale terms (`_done/`, `bisync`, `scripts/paths.py`, `sonar-pro`) to confirm zero remaining references.

## Out of Scope

- `bootstrap.sh` `_done` → `_rejected/_applied/_waitlisted` — tracked as #21
- `company_researcher.md` role frontmatter model — code change, not doc
- `rename_folders.py` `_done` references — legacy migration code
- `data/.env.example` `sonar-pro` comment — low-traffic, file is gitignored template
