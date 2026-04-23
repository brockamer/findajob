# Google Sheets

> **One-way synced view as of #61 PR-B.** The Google Sheet mirrors DB state
> but is no longer read by the pipeline. The primary human interface is the
> web UI at `/board/*` — see `docs/architecture.md` for the handler matrix.
> Edits made directly in the Sheet are ignored and overwritten on the next
> `sync_sheet.py` cycle. The Sheet stays useful for phone-glance views and
> for sharing read-only status with collaborators.

SQLite is the source of truth.

---

## Tabs

### Sheet1 — Full Archive

All jobs that passed deduplication. Reference view only — not interactive (except the APPLY_FLAG checkbox, which is a legacy field). Use the Dashboard for workflow actions.

Columns A–N:
| Col | Field |
|---|---|
| A | fingerprint (hidden) |
| B | APPLY_FLAG (checkbox) |
| C | relevance_score |
| D | title |
| E | company |
| F | location |
| G | remote_status |
| H | stage |
| I | known_contacts |
| J | comp_estimate |
| K | ai_notes |
| L | date_found |
| M | source |
| N | url |

Rejected rows are greyed out (conditional formatting on `stage="rejected"`).

---

### Dashboard — Pre-Application Queue

Jobs the user can still act on before applying. Updated by `sync_sheet.py` (after triage and after every prep).
`board_actions` (web handler) reads this tab every 10 minutes and acts on STATUS and REJECT_REASON.

**Filter:** `(relevance_score >= 7 AND stage IN (scored, manual_review))` OR `stage IN (prep_in_progress, materials_drafted)`.

Once the user marks STATUS=Applied, the poller sets `stage=applied` and the job leaves the Dashboard for the Applied tab.

**Sort:** `materials_drafted` jobs first (most actionable), then by score descending.

Columns A–N:
| Col | Field | Who writes it |
|---|---|---|
| A | STATUS | You (via dropdown) |
| B | REJECT_REASON | You (via dropdown) |
| C | fingerprint (hidden) | System |
| D | fit_score | System |
| E | probability_score | System |
| F | relevance_score | System |
| G | title (hyperlink) | System |
| H | company | System (plain text on Sheet1; hyperlinks into the materials viewer on Dashboard/Applied/Waitlist/Rejected Applications when `FINDAJOB_MATERIALS_BASE_URL` is set) |
| I | location | System |
| J | remote_status | System |
| K | known_contacts | System |
| L | comp_estimate | System |
| M | ai_notes | System |
| N | date_found | System |

---

## STATUS Dropdown (col A)

The STATUS dropdown drives the entire application workflow.

| Value | Set by | What happens |
|---|---|---|
| *(empty)* | System (default) | No action |
| `Flag for Prep` | You | `board_actions` (web handler) triggers `prep_application.py` within 10 min |
| `Prep in Progress` | System | Set when prep is actively running (prevents duplicate triggers) |
| `Regenerate` | You | Deletes existing prep folder and re-runs prep from scratch |
| `Ready to Apply` | System | Set automatically when `stage=materials_drafted` (prep done) |
| `Waitlist` | You | Defers the job — folder moves to `_waitlisted/`, appears on Waitlist tab |
| `Applied` | You | `board_actions` (web handler) updates DB `stage=applied`, moves folder to `_applied/` |
| `Interviewing` | You | `board_actions` (web handler) updates DB `stage=interview` |
| `Offer` | You | `board_actions` (web handler) updates DB `stage=offer` |
| `Not Selected` | You | Company rejected — `stage=not_selected`, folder stays in `_applied/`, no feedback_log |
| `Withdrew` | You | `board_actions` (web handler) updates DB `stage=withdrawn` |

**Important:** `Ready to Apply` and `Prep in Progress` are system-set. Setting them manually has no effect on the DB — the poller ignores them.

---

## REJECT_REASON Dropdown (col B)

Behavior depends on the STATUS column:

**If STATUS = `Not Selected` (company rejection):**
1. `board_actions` (web handler) (within 10 min) detects the value
2. DB updated: `stage=not_selected`, `reject_reason=<value>`
3. No `feedback_log` write (company rejections don't contaminate the scorer)
4. Folder stays in `companies/_applied/` with a `NOT_SELECTED_{reason}_{date}.txt` marker file
5. Waitlisted jobs at the same company are surfaced via ntfy notification

**Otherwise (user rejection):**
1. `board_actions` (web handler) (within 10 min) detects the value
2. DB updated: `stage=rejected`, `reject_reason=<value>`
3. Row written to `feedback_log` table (for pattern analysis)
4. If a prep folder exists for this job: it is moved to `companies/_rejected/`
5. Job disappears from Dashboard on next sync

"Not Selected" is checked before generic rejection in the poll cycle to prevent routing errors.

**Tip:** You can reject and prep at the same time by setting both — rejection wins. If you change your mind after rejecting, you'd need to manually update the DB.

---

## Applied Tab — Post-Application Management

The management view for jobs you've submitted and are waiting to hear back on. Every row here represents an application in flight.

**Filter:** `stage IN (applied, interview, offer)`.

**Sort:** by stage — `offer` first, then `interview`, then `applied`; within each stage, most recently updated first.

Columns A–N:
| Col | Field | Who writes it |
|---|---|---|
| A | STATUS | You (dropdown) |
| B | REJECT_REASON | You (dropdown — same 11 options as Dashboard) |
| C | fingerprint (hidden) | System |
| D | title (hyperlink to JD) | System |
| E | company (hyperlinks into materials viewer when `FINDAJOB_MATERIALS_BASE_URL` is set) | System |
| F | applied_date | System (from `audit_log` first `applied` transition) |
| G | days_since_applied | Live `=IF(F2="","",TODAY()-F2)` formula |
| H | stage | System (`applied` / `interview` / `offer`) |
| I | user_notes | You (free text — syncs back to `jobs.user_notes`) |
| J | known_contacts | System |
| K | location | System |
| L | remote_status | System |
| M | comp_estimate | System |
| N | ai_notes | System (read-only — scorer output) |

### STATUS Dropdown

| Value | What happens |
|---|---|
| `Interviewing` | `board_actions` (web handler) sets `stage=interview`, row stays on Applied tab |
| `Offer` | `board_actions` (web handler) sets `stage=offer`, row stays on Applied tab |
| `Ghosted` | Visual-only — stage stays `applied`, row stays on tab, whole row turns gray |
| `Not Selected` | Company rejection — `stage=not_selected`, marker file in `_applied/`, row moves to Rejected Applications tab |
| `Withdrew` | `stage=withdrawn`, row leaves Applied tab |

`Ghosted` is for jobs where a recruiter has gone dark — the row stays so you can follow up. Flip to `Not Selected` when you give up.

### Row Color Priority

First matching rule wins:
1. `STATUS = Offer` → gold (best news)
2. `STATUS = Interviewing` → purple
3. `STATUS = Ghosted` **or** `days_since_applied ≥ 21` → gray
4. `days_since_applied 14–20` → red (getting stale)
5. `days_since_applied 7–13` → yellow
6. `days_since_applied 0–6` → green (fresh)

### User Notes Writeback

`sync_sheet.py` reads the Applied tab before clearing it. If the user changed `user_notes` (col I) since the last sync, the new value is written to `jobs.user_notes` before the tab is re-rendered. No separate migration or action needed — just edit and it persists.

---

## Review Tab — Manual Review Triage

Jobs that the scorer flagged for human review (null scores, schema validation failures, edge cases).

**Filter:** `stage = manual_review`

Columns A–H:
| Col | Field | Who writes it |
|---|---|---|
| A | STATUS | You (dropdown: `Promote`) |
| B | REJECT_REASON | You (dropdown — same options as Dashboard) |
| C | fingerprint (hidden) | System |
| D | title (hyperlink) | System |
| E | company | System |
| F | score_flag_reason | System — why the scorer flagged this job |
| G | source | System |
| H | date_found | System |

**Promote:** `board_actions` (web handler) sets `score=7, stage=scored` → job appears on Dashboard.
**REJECT_REASON:** same rejection workflow as Dashboard.

---

## Waitlist Tab — Deferred Jobs

Jobs you want to keep but aren't ready to pursue yet. Not a rejection — does not write to `feedback_log` or contaminate the scorer feedback loop.

**Filter:** `stage = waitlisted`

Columns A–K:
| Col | Field | Who writes it |
|---|---|---|
| A | STATUS | You (dropdown: `Reactivate`) |
| B | REJECT_REASON | You (dropdown — same options as Dashboard) |
| C | fingerprint (hidden) | System |
| D | title (hyperlink) | System |
| E | company | System |
| F | relevance_score | System |
| G | location | System |
| H | remote_status | System |
| I | ai_notes | System |
| J | date_found | System |
| K | blocking_app | System — active application at same company (title + stage) |

**Reactivate:** restores to `scored` (no folder) or `materials_drafted` (has folder), moves folder back from `_waitlisted/`.
**REJECT_REASON:** rejects the job from the waitlist (same workflow as Dashboard).
**blocking_app:** computed at sync time. When an active application at the same company is rejected or withdrawn, `notify.py` sends a notification to surface the waitlisted job.

---

## Rejected Applications Tab

Read-only reference view of jobs that were rejected after reaching the `applied` stage. Useful for tracking company-side rejections.

Columns A–H:
| Col | Field |
|---|---|
| A | title (hyperlink) |
| B | company (hyperlinks into materials viewer when `FINDAJOB_MATERIALS_BASE_URL` is set) |
| C | reject_reason |
| D | applied_date |
| E | rejected_date |
| F | fit_score |
| G | probability_score |
| H | ai_notes |

---

## Color Coding

**Dashboard** (pre-application statuses only):
| Column | Color trigger | Color |
|---|---|---|
| STATUS col A | `Flag for Prep` | Entire row turns light blue |
| STATUS col A | `Prep in Progress` | Entire row turns warm yellow |
| STATUS col A | `Regenerate` | Entire row turns warm orange |
| STATUS col A | `Ready to Apply` | Entire row turns teal |
| STATUS col A | `Waitlist` | Entire row turns warm amber |
| STATUS col A | `Applied` | Entire row turns green (brief — poller removes row within 10 min) |
| REJECT_REASON col B | Any value | Cell gets a distinct color per reason |
| remote_status col J | `Remote` | Red background |
| remote_status col J | `Hybrid` | Yellow background |
| remote_status col J | `On-site`/`Onsite` | Green background |
| known_contacts col K | Non-empty | Amber background |

**Applied tab** (row-color priority — first match wins):
| Trigger | Color |
|---|---|
| `STATUS = Offer` | Gold |
| `STATUS = Interviewing` | Purple |
| `STATUS = Ghosted` OR `days_since_applied ≥ 21` | Gray |
| `days_since_applied 14–20` | Red |
| `days_since_applied 7–13` | Yellow |
| `days_since_applied 0–6` | Green |

**Sheet1:**
| Column | Trigger | Color |
|---|---|---|
| stage col H | `rejected` | Entire row grey with grey text |

---

## Formatting Maintenance

If the sheet loses formatting (tabs reorganized, conditional formatting deleted, etc.):

```bash
python3 scripts/setup_sheets.py
```

This re-applies all formatting. It is idempotent — safe to run at any time. It will:
1. Delete existing row banding and re-add it
2. Replace all conditional formatting rules (not add duplicates)
3. Set all dropdown validations
4. Hide/unhide the correct columns
5. Set column widths

---

## Google Sheet Architecture Notes

**Why SQLite instead of Sheets as database?**
Google Sheets has race conditions when multiple concurrent writes happen. The API has rate limits that cause failures under load. SQLite is ACID, fast, and queryable with SQL. The sheet is purely for display and user input.

**Why read fingerprint (col C) instead of row index?**
The sheet is re-written from scratch on each sync. Row positions are unstable. The fingerprint identifies a job uniquely across both the sheet and the DB.

**Why does the poller run every 10 min instead of instantly?**
systemd timer granularity. 10 min is the default cadence; if you need faster response, run `python3 scripts/board_actions (web handler)` manually immediately after flagging.

**Where does `board_actions` (web handler) read from?**
Four tabs per cycle: `Dashboard!A2:C10000`, `Applied!A2:C10000`, `Review!A2:C10000`, `Waitlist!A2:C10000`. All use the same col A=STATUS, B=REJECT_REASON, C=fingerprint layout so one loop handles them.
