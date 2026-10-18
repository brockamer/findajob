# Board routes and stage lifecycle

Reference for coding agents and maintainers, linked from [`AGENTS.md`](../../AGENTS.md) § Board Routes & Stage Lifecycle. The invariants (web is the write surface, which transitions write `feedback_log`, the atomic prep claim) stay in `AGENTS.md`. **Same-PR docs rule:** a new or changed state transition updates this file in the same PR.

Every transition is a POST handler in `findajob.web.routes.board_actions`
that calls straight into `findajob.actions` (handle_rejection,
handle_not_selected, handle_waitlist, handle_reactivate,
handle_withdraw_as_fallback, mark_as_fallback, promote_from_fallback,
promote_to_scored, notify_waitlist_resurface, reset_prep_to_scored) and
responds in the same request — no poll cycle, no mirror table.

## Generator subprocesses

Some transitions also spawn detached generator subprocesses:
- `POST /board/jobs/{fp}/prep` and `/regenerate` → `scripts/prep_application.py` (briefing, tailored resume, cover, recruiter critique, outreach drafts). Default `--phase=all` runs Phase A then Phase B in sequence.
- `POST /board/jobs/{fp}/continue-prep` → `scripts/prep_application.py --phase=b` (the briefing-first gate #691). Promotes a `briefing_ready` row to `prep_in_progress` and runs Phase B only (resume tailor → cover → critique → outreach). Operator-confirmed continuation after Phase A's briefing.
- `POST /board/jobs/{fp}/interview` → `scripts/interview_prep.py` (interview prep artifact). Always (re)launches on each click — re-clicking is the regenerate mechanism after a recruiter sends panel info; a sentinel file `.interview_prep_in_progress` in the prep folder guards against concurrent runs.
- `POST /ingest/speculative` and `POST /speculative/regenerate/{id}` → `scripts/run_speculative_research.py` (briefing + role-synth pipeline). Async — status page polls `/speculative/status/{id}/poll` every 5s until `status='ready_for_review'`. Full route surface in `findajob.web.routes.speculative` (POST `/ingest/speculative`, GET `/speculative/status/{id}` + `/poll`, GET `/speculative/review/{id}`, POST `/speculative/{approve,regenerate,trash}/{id}`).
- `POST /board/jobs/{fp}/apply` is synthetic-aware: reads `jobs.synthetic` and writes `audit_log.changed_by='outreach_button'` for synthetic rows (label flips to "Sent Outreach" on the dashboard); otherwise the existing `'user'` value. No separate route — single endpoint, server-derived signal.

## Routes

| Action | Endpoint | Where it lives |
|---|---|---|
| Flag for Prep | `POST /board/jobs/{fp}/prep` | Dashboard dropdown. Launches Phase A only (`--phase=a`) — company research, briefing, fit analysis. Operator continues to Phase B from the briefing-first gate at `/materials/{fp}/`, or rejects with a substantive reason. `scripts/prep_application.py` retains `--phase=all` as the CLI default for cron and manual invocations (#691). On a `scored` row whose `prep_folder_path` still holds a briefing, it launches nothing: `findajob.actions.resurface_briefing` moves the row straight back to `briefing_ready`, before the spend-ceiling gate because it spends nothing (#1222). |
| Continue prep | `POST /board/jobs/{fp}/continue-prep` | HTMX-shaped dashboard endpoint (returns `<tr>` for outerHTML swap). Reserved for future dashboard dropdown affordance. The materials-page UI uses `POST /materials/{fp}/continue-prep` (303-redirect wrapper) instead — same gates, same Phase B subprocess, but page-shape-correct for the server-rendered materials view (#691). |
| Continue prep from materials | `POST /materials/{fp}/continue-prep` | Briefing-first gate panel on `/materials/{fp}/` when stage is `briefing_ready`, or `scored` with a briefing in its folder — such a row is resurfaced to `briefing_ready` first (its own audit row), then claimed (#691, #1222). 303-redirect wrapper around the dashboard route — same idempotency + spend-ceiling + queue-cap gates, dispatches `prep_application.py --phase=b`. Queue-full / spend-ceiling refusals 303-redirect to `/materials/?continue_prep_error={queue_full,spend_ceiling}`. |
| Reject from materials | `POST /materials/{fp}/reject` | Briefing-first gate reject affordance (#691). 303-redirect wrapper around `handle_rejection` (writes `feedback_log`, moves folder to `_rejected/`, fires `notify_waitlist_resurface`). Idempotent on already-rejected rows. |
| Regenerate | `POST /board/jobs/{fp}/regenerate` | Dashboard dropdown — gated by `GET /board/jobs/{fp}/regenerate/confirm` modal (#700); Cancel restores cell via `GET /board/jobs/{fp}/regenerate/cell` |
| Applied | `POST /board/jobs/{fp}/apply` | Dashboard dropdown. Response carries the `_undo_toast.html` partial as `hx-swap-oob` into `#undo-toast` (#699). |
| Un-apply | `POST /board/jobs/{fp}/un-apply` | Undo button inside the 30s undo toast (#699). 409 once the audit_log row '… → applied' is older than 30 seconds — gate is SQL-side `datetime('now', '-30 seconds')` for clock-drift safety. |
| Waitlist | `POST /board/jobs/{fp}/waitlist` | Dashboard dropdown + Review tab button (#702) |
| Reject (w/ reason) | `POST /board/jobs/{fp}/reject` | Dashboard / Review / Waitlist reject cell |
| Interviewing | `POST /board/jobs/{fp}/interview` | Applied dropdown |
| Re-run interview prep | `POST /materials/{fp}/rerun-interview-prep` | Materials page button (when stage=interview). Same subprocess as `/interview` re-click; per-job concurrency guard via `background_tasks`; spend ceiling gate. 303-redirects to `/materials/{fp}/` (#875). |
| Generate study guide | `POST /materials/{fp}/study-guide` | Materials page button (when stage=interview). On-demand single-artifact generation reusing existing prep artifacts; synchronous (`writeback_sync`) `background_tasks` row keyed `kind='study_guide'`; per-job concurrency guard + spend ceiling gate; non-destructive (timestamped output). 409 off interview stage; missing briefing/interview-prep redirects 303 to `?study_materials_error=missing_artifacts` (#1029). Shared launcher `_launch_study_artifact` with flashcards. 303-redirects to `/materials/{fp}#study-materials` (#873). |
| Generate flashcards | `POST /materials/{fp}/flashcards` | Materials page button (when stage=interview). Same launcher as study guide, `kind='flashcards'`; builds the Anki/CSV/JSON deck via `generate_flashcards_for_job`. 303-redirects to `/materials/{fp}#study-materials` (#873). |
| Un-interview | `POST /board/jobs/{fp}/un-interview` | Applied dropdown (when stage=interview). Restores prior stage from audit_log (fallback `applied`). Row stays on Applied tab; OOB stage-change toast. |
| Offer | `POST /board/jobs/{fp}/offer` | Applied dropdown |
| Withdrew | `POST /board/jobs/{fp}/withdraw` | Applied dropdown |
| Withdrew (Fallback) | `POST /board/jobs/{fp}/withdraw-as-fallback` | Applied dropdown. Sets stage=`withdrawn_fallback`, stores reason="Better opportunity", fires `notify_waitlist_resurface`. No folder move (#358). |
| Not Selected (w/ reason) | `POST /board/jobs/{fp}/not-selected` | Applied dropdown + reject cell |
| Promote | `POST /board/jobs/{fp}/promote` | Review button |
| Reactivate | `POST /board/jobs/{fp}/reactivate` | Waitlist button |
| Reactivate and prep | `POST /board/jobs/{fp}/reactivate-and-prep` | Waitlist tab "Flag for Prep" button (#702). Same spend-ceiling + queue-cap gates as `/prep`. Atomically claims the job straight from `waitlisted → prep_in_progress` (the prep claim is the first mutation, #957), relocating the prep folder inline — a single audit row. |
| Change reject reason | `POST /board/jobs/{fp}/change-reject-reason` | Rejected tab inline dropdown (#697) |
| Un-not-selected | `POST /board/jobs/{fp}/un-not-selected` | Not Selected tab button (#698) |
| Change not-selected reason | `POST /board/jobs/{fp}/change-not-selected-reason` | Not Selected tab inline dropdown (#698) |
| Mark as Fallback | `POST /board/jobs/{fp}/mark-as-fallback` | Archive actions cell (#358). Converts withdrawn → withdrawn_fallback. No folder move. |
| Promote from Fallback | `POST /board/jobs/{fp}/promote-from-fallback` | Fallback tab button (#358). Restores prior stage from audit_log. Clears reject_reason. |
| Un-withdraw | `POST /board/jobs/{fp}/un-withdraw` | Archive actions cell (#701) |
| Reattribute | `POST /board/jobs/{fp}/reattribute-from-archive` | Archive reattribute modal (#701) |
| Edit user_notes | `POST /board/jobs/{fp}/notes` | Notes input on any tab that surfaces the column (Dashboard / Review / Waitlist / Fallback / Applied). 800ms debounce. Blur writes `notes_history`; keyup only writes `jobs.user_notes`. |
| Confirm rejection email | `POST /board/rejections-review/{id}/confirm` | Rejections-review queue (#362) |
| Dismiss rejection email | `POST /board/rejections-review/{id}/dismiss` | Rejections-review queue (#362) |
| Reattribute rejection email | `POST /board/rejections-review/{id}/reattribute` | Rejections-review queue (#362) |
| Apply filter proposal | `POST /board/filter-proposals/{id}/apply` | Filter-proposals queue; danger-recompute confirm gate; writes hard_rejects.auto_added + re-scores matches |
| Skip filter proposal | `POST /board/filter-proposals/{id}/skip` | Filter-proposals queue |
| Revert filter proposal | `POST /board/filter-proposals/{id}/revert` | Filter-proposals queue "Applied rules" section |

The rejections-review row is keyed by `rejection_suggestions.id` rather than `jobs.fingerprint` — the suggestion is the source row, the job_id is found via `matched_job_id` (or operator-supplied on reattribute). Confirm/reattribute call `handle_not_selected(..., changed_by='gmail_rejection_detector')` so the audit trail tags the transition.

## Reject reasons

**REJECT_REASON dropdown**: vocabulary is per-stack configurable via `config/reject_reasons.yaml`; defaults to a field-agnostic list in `findajob.config_loader._DEFAULT_REJECT_REASONS`, hot-reloaded per request. A "title-signal" subset (declared in the same YAML, defaults in `_DEFAULT_TITLE_SIGNAL_REASONS`) feeds `analyze_feedback._prefilter_candidates` so the scorer learns from rejections where the title alone was a tell. Behavior depends on STATUS:
- If STATUS = `Not Selected`: company rejection → `stage=not_selected`, NO `feedback_log`, folder stays in `_applied/` with `NOT_SELECTED_` marker file
- Otherwise: user rejection → `stage=rejected`, writes `feedback_log`, moves folder to `_rejected/`

## Stages

**Stage `waitlisted`:** Set by `POST /board/jobs/{fp}/waitlist`. Folder moves to `companies/_waitlisted/`. Not a rejection — does not write to feedback_log or contaminate scorer feedback loop. When an active application at the same company is rejected/withdrawn, ntfy notification surfaces waitlisted jobs.

**Stage `withdrawn_fallback`:** Set by `POST /board/jobs/{fp}/withdraw-as-fallback` (from Applied dropdown) or `POST /board/jobs/{fp}/mark-as-fallback` (from Archive, converting existing withdrawn rows). Folder stays in `companies/_applied/` — no folder move. Not a rejection — does not write to `feedback_log`. Withdraw reason stored in `reject_reason` column (stage-disjoint from rejected/not_selected). `notify_waitlist_resurface()` fires on the withdraw-as-fallback route. The Fallback tab (`/board/fallback`) surfaces all `withdrawn_fallback` rows with a Promote button that restores the prior stage via `audit_log` lookup.

**Stage `not_selected`:** Set by `POST /board/jobs/{fp}/not-selected`. Only valid for post-application stages (`applied`, `interview`, `offer`); 409 otherwise. Folder stays in `companies/_applied/` with a `NOT_SELECTED_{reason}_{date}.txt` marker file. Does NOT write to `feedback_log` — company rejections must not contaminate the scorer's feedback loop. `notify_waitlist_resurface()` still fires.

**Stage `prep_in_progress`:** Set by `POST /board/jobs/{fp}/prep` or `POST /board/jobs/{fp}/continue-prep` immediately before launching `prep_application.py` as a subprocess. Prevents duplicate prep runs via a single atomic claim — `findajob.web.routes.board_actions._claim_prep_slot` does one conditional `UPDATE … WHERE stage <claimable> AND (in-flight count) < cap` so a double-click / HTMX retry can win at most one slot, and the 3-job concurrency cap holds under burst (#957). The claim is the first state mutation in every prep-launching handler (`prep`, `regenerate`, `continue_prep`, `reactivate_and_prep`, and the `/materials/` wrappers), so folder ops and the subprocess launch run only for the winner. On success the route-spawned phase clears to its own exit stage: `/prep` (Phase A only) clears to `briefing_ready`; `/continue-prep` (Phase B) clears to `materials_drafted`. `scripts/watchdog.py` rolls any job stuck > 60 min back: `kind='prep'` rows reset to `scored` (via `reset_prep_to_scored`); `kind='prep_phase_b'` rows reset to `briefing_ready` (via `reset_prep_to_briefing_ready`) so the operator can re-try Phase B without re-paying Phase A (#691). A `LLMSpendCeilingExceeded` breach or a fail-closed ceiling-gate `sqlite3.OperationalError` *during* prep is caught in-process by both phase handlers (`_handle_prep_runtime_failure` for Phase A → `scored`; `_handle_phase_b_failure` for Phase B → `briefing_ready`) and resets immediately with a `.failed_prep` sentinel + `prep_failure` ntfy, rather than waiting on the watchdog; the watchdog stays the backstop for genuine hangs (#956).

**Stage `briefing_ready`:** Set by `_run_prep_phase_a` at Phase A completion. The briefing folder is written to disk, `fit_score` + `probability_score` are stored in the DB, and the operator decides via `/materials/{fp}/` whether to continue (POST `/continue-prep`) or reject (POST `/reject` with a substantive reason — `handle_rejection` writes `feedback_log` as usual). Applied is also reachable from the Dashboard dropdown for rows at this stage — operator skips Phase B and applies with what Phase A produced (the briefing folder is moved + snapshotted into `_applied/` by `_move_folder_to_applied` like any other apply transition). `scripts/watchdog.reap_briefing_ready_stale` resets rows older than the decision window (`FINDAJOB_BRIEFING_READY_WINDOW_DAYS`, default 14 days, `0` = never; #1222) to `scored` *without* nulling `prep_folder_path`. Such a row is not a bare scored row: the dashboard status cell links its briefing as "📋 Briefing ready (N days)" (age from the briefing file's mtime via `findajob.prep.briefing.briefing_age_days`, because the reset rewrites `stage_updated`), and `/materials/{fp}/` shows the gate. Flag for Prep or Continue prep calls `findajob.actions.resurface_briefing` (guarded `scored → briefing_ready`, one audit row, `briefing_resurfaced` event) instead of re-running Phase A. `_run_prep_phase_a` runs the same check from `prep_in_progress` before it creates a folder, so launch paths that keep the folder (`reactivate-and-prep`, a manual CLI run) resume too; `/regenerate` clears the path first, so it still runs Phase A. Because the path stays tracked, neither the orphan sweep nor the prep-start quarantine moves the folder. The remaining path that can orphan a briefing — the Phase B catch-all that nulls `prep_folder_path` — is #1142.

## Health checks

`notify.py health-check` warns if manual_review backlog > 100, a source silently stopped producing jobs, or any target-company job scored 3–6 in last 7 days (potential mis-scores).
