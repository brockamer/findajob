# Daily Usage

findajob runs overnight. You wake up, triage, prep a handful of applications, submit, and mark what you sent. Everything happens in a web browser.

This page walks through each `/board/` tab in the order you'll use them. If you're setting up for the first time, read [`setup/README.md`](setup/README.md) first.

---

## The daily loop

A normal morning is five steps, usually under 30 minutes:

1. **Check ntfy.** Overnight triage sent a push notification with the scored-jobs count and the number needing manual review.
2. **Open the Dashboard** (`/board/dashboard`). Scan the jobs sorted by score. Each row has three scores, a contacts count, and a set of AI notes.
3. **Flag prep-worthy jobs.** Choose *Flag for Prep* from the STATUS dropdown. Prep runs in the background; ntfy pings you when materials are ready.
4. **Review materials** (`/materials/<folder>`). A tailored resume, cover letter, company briefing, and network-outreach drafts land in a folder per job. Read them in the browser; download the resume as `.docx` when you're ready to submit.
5. **Submit applications, mark them Applied.** STATUS → *Applied* moves the job to the Applied tab with a days-since-applied timer running.

Rejections come later — from the Applied tab when a company comes back "no", or from the Dashboard when you decide a listing isn't worth prepping. Each rejection has a reason, and reasons feed back into tomorrow's scoring.

---

## The Dashboard (`/board/dashboard`)

The Dashboard shows every scored job worth your attention — usually `score >= 7` plus anything you've flagged manually. You'll spend more time here than anywhere else.

### Column meaning, in plain English

| Column | What it tells you |
|---|---|
| **relevance_score** (1–10) | Does this job match the kind of work you want? |
| **fit_score** (1–10) | How well does the role map to what's on your resume? |
| **probability_score** (1–10) | Are you likely to clear the resume screen? |
| **contacts** | How many people in your LinkedIn connections list work at this company |
| **comp** | Published compensation range (empty if the posting doesn't list one) |
| **remote** | Onsite / hybrid / remote / unclear |
| **notes** | One-sentence AI-generated comment on anything unusual about the listing |

A job scoring 9 / 9 / 9 is unusual — most good jobs score 7–8 on two of the three. The *probability* score is the most pessimistic on purpose; it's answering "would this resume realistically make it past the screen," not "would you be good at this."

### STATUS dropdown

| Option | What happens |
|---|---|
| **Flag for Prep** | Starts `prep_application.py` in the background. Stage → `prep_in_progress`. You'll get an ntfy ping when it finishes (~3–5 min). |
| **Regenerate** | Re-runs prep with fresh output (profile changed, model flaked, first pass was off). |
| **Applied** | You already applied through another channel; skip prep and jump straight to the Applied tab. |
| **Waitlist** | Defer. The job stays in the DB but moves off the Dashboard. Not a rejection — see *Waitlist* below. |
| **Reject** | Remove with a reason. Reasons feed the next day's scorer as negative examples. |

### REJECT_REASON — the 11 options

The reject dropdown has eleven preset reasons so they can be counted and charted over time. Pick the one that matches why you're passing:

1. **Too Senior** — role is a level above what you're after.
2. **Too Junior** — role is a level below.
3. **Skills Mismatch** — title matches, but the actual stack or domain doesn't.
4. **Too TPM-Heavy** — role drifted from hands-on into pure program management (or whatever the equivalent drift is for your field — this label is being generalized in #65).
5. **Geography/Onsite** — unworkable location.
6. **Company Not a Fit** — ethical, cultural, or trajectory objection.
7. **Comp Too Low** — published band is below your floor.
8. **Low Fit Score** — scorer rated it low and you agree; you're acknowledging the cut.
9. **Stale/Closed** — posting is dead.
10. **Already Applied** — duplicate of one you've already sent.
11. **Other** — free-form; pair with a note.

If the reason you want isn't here, use *Other* and put the detail in the notes column.

---

## What happens when you Flag for Prep

Prep is the heaviest LLM step — it uses Claude Opus to write a tailored resume and cover letter, Perplexity Sonar Pro for company research, and a few Sonnet roles for the outreach drafts. It costs roughly $1.50–$3.00 per job.

**Stage progression:**

```
scored → prep_in_progress → materials_drafted → applied
                         ↓
                       (failure/timeout → scored, retry via watchdog after 60 min)
```

**Output:** a folder under `companies/{Company}_{AbbrevTitle}_{YYYY-MM-DD}_{HHMMSS}/` containing:

| File | What it is |
|---|---|
| `resume.md` | Tailored resume (your master resume reshaped for this posting) |
| `resume.docx` | Same, converted to Word for submission |
| `cover_letter.md` | Cover letter, voice-calibrated against your writing samples |
| `company_briefing.md` | Perplexity-sourced notes on the company — recent news, culture, known fit issues |
| `network_outreach_*.md` | Drafts for messaging LinkedIn connections at the company |
| `job_description.md` | JD snapshot from ingest (so you don't re-fetch the URL) |

**Viewing materials:** the web UI at `/materials/<folder>/` renders everything inline — Markdown is styled, the JD is linked, `.docx` is offered as a download. You don't need to `scp` or sync anything.

---

## The Review tab (`/board/review`)

Jobs land here when the scorer's output couldn't be confidently validated — the LLM said "needs human review," or returned a low-confidence fit/probability split, or the JD was thin. `stage = manual_review`.

Use the tab to:

- **Promote** → move the job to `scored` so it appears on the Dashboard.
- **Reject** → same rejection flow as the Dashboard; pick a reason.

A healthy pipeline has a small (under 20) steady state on Review. If the queue grows past 100, the health check fires a warning — tune the profile or adjust the scoring threshold.

---

## The Applied tab (`/board/applied`)

Everything you've applied to, ordered by how long ago you applied.

### Post-application STATUS options

| Option | What it means |
|---|---|
| **Interviewing** | Got a reply, scheduled something. Row turns purple. |
| **Offer** | Got the offer. Row turns gold. |
| **Not Selected** | Company passed. Use the REJECT_REASON dropdown to record why (ghosted → "Other" with a note, formal rejection → pick the closest reason). The job stays on Applied — rejections from companies don't feed the scorer the way your own reject-with-reason calls do. |
| **Withdrew** | You pulled out. |

### Row color coding (silent = likely ghosted)

| Days since applied | Color | Meaning |
|---|---|---|
| 0–6 | Green | Fresh; normal to not hear back yet. |
| 7–13 | Yellow | First week over; most movement happens in this window. |
| 14–20 | Red | Lagging; consider a follow-up note. |
| 21+ | Gray | Likely ghosted. Safe to move on. |
| (any) | Purple | Interviewing — overrides days-since color. |
| (any) | Gold | Offer — overrides everything else. |

### `days_since_applied` is live

The column is a spreadsheet-formula field — it updates every time the Sheet refreshes, no re-sync required. On the web page it renders from the DB directly so it's always current.

### `user_notes` — free text

The notes field saves on 800 ms debounce. Type, pause, it's written. Useful for logging follow-up dates, interview feedback, or "Jess gave me a referral link 2026-05-02."

---

## The Waitlist tab (`/board/waitlist`)

Waitlisted jobs are jobs you didn't want to reject — maybe the role is good but you're interviewing somewhere else, maybe the comp wasn't disclosed and you want to see if a similar listing surfaces, maybe the company is a "yes but only if another option falls through."

**Waitlist is not rejection.** It does *not* write to the feedback log. The scorer never sees it.

From the tab:

- **Reactivate** → back to `scored`, appears on the Dashboard again.
- **Reject** → standard reject flow; pick a reason.

**Waitlist resurface:** when an active application at the same company ends in rejection or withdrawal, ntfy fires a notification pointing back at the waitlisted job. ("You waitlisted *Acme — Ops Lead*. Your *Acme — Site Manager* application was just rejected. Reconsider?")

---

## The Archive tab (`/board/archive`)

Every job the pipeline has ever ingested, in one paginated, filterable, sortable table. Filters for score, stage, company, date range, source. This is the backstop — if you can't find a job anywhere else, it's here.

---

## The Rejected tab (`/board/rejected`)

Every rejection, including rejections *from* you (stage = `rejected`) and rejections *from companies* (stage = `not_selected`). Browseable and filterable by reason, date, company. Useful for catching patterns — if `Skills Mismatch` is spiking, the profile's wrong; if `Geography/Onsite` is spiking, the search queries are.

---

## The Materials viewer (`/materials/`)

Three ways to get here:

- Click the company name on any Dashboard / Applied / Waitlist row — the cell is a link to the materials folder.
- Click a materials-folder name directly in the URL bar.
- `/materials/` root → index of every folder ever created.

Each folder renders its Markdown files inline and offers `.docx` downloads. The JD is linked back to the original posting URL. All served locally — no Drive sync, no rclone.

---

<details>
<summary><strong>For advanced users: stage names, POST handlers, and Sheet mirror</strong></summary>

**Canonical stage names in the DB** (`jobs.stage` column):

| Stage | Meaning |
|---|---|
| `scored` | Triaged, LLM-scored, appears on Dashboard |
| `manual_review` | Scorer flagged as uncertain; appears on Review tab |
| `prep_in_progress` | `prep_application.py` running; watchdog clears stuck jobs after 60 min |
| `materials_drafted` | Prep completed; materials folder exists; Dashboard STATUS shows *Ready to Apply* |
| `applied` | You submitted; appears on Applied |
| `interview` / `offer` | Post-application progress; appears on Applied |
| `rejected` | User rejection with reason; writes to `feedback_log`; folder moves to `companies/_rejected/` |
| `not_selected` | Company rejection; does NOT write to `feedback_log` (don't poison the scorer); folder stays in `companies/_applied/` with a `NOT_SELECTED_*.txt` marker |
| `waitlisted` | Deferred; folder moves to `companies/_waitlisted/`; scorer never sees it |

**Every STATUS dropdown action is a POST handler.** `findajob.web.routes.board_actions` contains one handler per transition; each calls straight into `findajob.actions` and responds in the same request. The handlers are:

- `/board/jobs/{fp}/prep` — Flag for Prep
- `/board/jobs/{fp}/regenerate` — Regenerate
- `/board/jobs/{fp}/apply` — Applied
- `/board/jobs/{fp}/waitlist` — Waitlist
- `/board/jobs/{fp}/reject` — Reject (with REJECT_REASON)
- `/board/jobs/{fp}/interview` / `/offer` / `/withdraw` — post-application
- `/board/jobs/{fp}/not-selected` — Not Selected (with REJECT_REASON)
- `/board/jobs/{fp}/promote` — Review → scored
- `/board/jobs/{fp}/reactivate` — Waitlist → scored
- `/board/jobs/{fp}/notes` — user_notes save

No poll cycle. The Google Sheet (Dashboard, Applied, Review, Waitlist, Rejected Applications tabs) is a one-way synced mirror via `sync_sheet.py` every 15 min — a glance-at-your-phone view, not a write surface.

**Scheduler timing:**

| Timer | Cadence | What it does |
|---|---|---|
| triage | 00:00 daily | Fetch + clean + score; writes `pipeline_complete` event |
| watchdog | every 10 min | Resets jobs stuck in `prep_in_progress` > 60 min |
| sync_sheet | every 15 min | One-way DB → Google Sheet mirror |
| notify-apply | 06:00 daily | "Time to triage" ntfy |
| notify-stats | 06:15 daily | Morning funnel summary |
| notify-health | 07:00 daily | Health-check alerts via ntfy |

Running inside the container as supercronic jobs; `docker compose logs scheduler` to see them fire.

</details>
