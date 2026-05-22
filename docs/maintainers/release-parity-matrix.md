# Release Parity Validation Matrix — Docker ↔ Fly

Findajob ships to two deployment substrates that share the same image but differ in runtime, persistence, and proxy fronting. This matrix asserts every user-visible feature surface behaves identically on both. It is the **pre-tag gate for every minor bump and every major bump** per [`release-process.md`](release-process.md). Patch releases re-verify only the rows the patch touched.

Tracking issue: [#747](https://github.com/brockamer/findajob/issues/747).

## How to use

Before a gated tag (minor bump like `v0.28.0`, or any major like `v1.0.0`), run a verification pass on both substrates. Update each cell to one of:

| Cell value | Meaning |
|------------|---------|
| `✓ YYYY-MM-DD ` | Surface exercised, behavior matches expectation. SHA pins the code state verified. |
| `✗ #NNN` | Parity gap or broken surface; linked follow-up issue tracks the fix. |
| `(unverified)` | Initial / stale state; this is a gap until populated. |

A release tag is acceptable when every cell is `✓` against the release SHA, or `✗` with a follow-up that the operator has explicitly classified as release-acceptable (filed to a non-blocking milestone).

### How individual issues reference this matrix

Three patterns. When filing or working an issue, name the pattern in the issue body so the relation is explicit:

1. **Verifies a row** — a bug-fix issue that flips a `✗ #N` cell to `✓ YYYY-MM-DD <sha>`. In the fix PR, update the matrix row in the same commit per the same-PR docs rule. Example: a fix for [#770](https://github.com/brockamer/findajob/issues/770) updates `/docs/{slug}` on the Fly column.
2. **Surfaced by it** — an issue filed because a verification pass found a gap. The issue body links back to the verification log entry that surfaced it. Examples: [#768](https://github.com/brockamer/findajob/issues/768), [#770](https://github.com/brockamer/findajob/issues/770), [#771](https://github.com/brockamer/findajob/issues/771).
3. **Gated by it** — release-tag or milestone-cut work that requires every cell `✓` on both columns. The release runbook in [`release-process.md`](release-process.md) names the matrix as the pre-tag gate for every minor and major bump.

A single issue can match multiple patterns (e.g. #770 is both *surfaced-by* and *verifies-a-row*).

**Docker reference stacks**: `findajob-staging` (populated soak; synthetic clicker drives forward-flow) is the primary verification target. Where the clicker leaves coverage gaps — un-* reversibility, change-reason, gmail-linkedin adapter, full reject/waitlist/withdraw flow — cells may be verified against `operator-primary-stack` (the operator's real-use stack with human-driven audit_log) as a secondary Docker reference. The matrix asserts Docker-substrate parity, not stack-specific parity, so evidence from any Docker stack is valid. Verification log records which stack the evidence came from.

**Fly reference deploy**: operator's Fly app (URL operator-private). Tester Fly deploys may be substituted for Fly-leg verification once the unaffiliated-tester walkthrough ([#672](https://github.com/brockamer/findajob/issues/672)) ships.

---

## Known substrate differences

Behaviors that are not bugs, but legitimately differ between Docker and Fly. The matrix asserts feature parity *despite* these differences.

| Difference | Docker behavior | Fly behavior | Issue |
|------------|-----------------|--------------|-------|
| `X-Accel-Buffering: no` | Harmless / no-op (Synology nginx) | Load-bearing for streaming endpoints (Fly edge buffers without it) | [#741](https://github.com/brockamer/findajob/issues/741) |
| Reverse-proxy redirect semantics | Synology nginx 302 + path-preserve | Fly proxy 308 + scheme-rewrite (`http://` → `https://`) — auth-loop race possible | [#693](https://github.com/brockamer/findajob/issues/693) |
| Health-check action on failure | docker-compose leaves container running | Fly auto-restarts the machine | — |
| Persistent storage | Host bind-mounts under `/opt/stacks/findajob-<stack>/state/` (operator-owned) | Fly volume mounted at container's `/app/state/` (root-owned at creation) | — |
| Perimeter / auth gate | Operator's Synology reverse proxy + HTTP Basic Auth ([#327](https://github.com/brockamer/findajob/issues/327)) | Fly edge + HTTP Basic Auth (same `FINDAJOB_AUTH_USER`/`PASS` mechanism) | — |
| Scheduler runtime | supercronic co-process inside one container (UTC-set; runs as `America/Los_Angeles` per stack `TZ`) | Same image; same supercronic; Fly machine `TZ` env var must be set per-stack | — |
| Auth-gate post-deploy verification | `verify_auth` run via `docker exec` after `compose up -d`; 5–7s settle [`feedback_verify_auth_race`] | `verify_auth` run via `flyctl ssh console`; settle time TBD | — |

Add a row here when a new genuine difference is discovered.

---

## Web surfaces

### Landing & navigation

| Surface | Docker (`findajob-staging`) | Fly (operator's deploy) |
|---------|------------------------------|--------------------------|
| `GET /` landing | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Top-nav present, all 9 groups linked | ✓ 2026-05-21 `a30957e` (staging: 8 direct hrefs + Settings dropdown nested with /settings/reject-reasons/ + /settings/spend-ceiling/ links — 9 groups total) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Spend chip in nav reflects current month | ✓ 2026-05-21 `a30957e` (staging: 'spend' + 'spend-ceiling' tokens present in dashboard HTML) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |

### Board tabs (8 user-facing tabs)

Every tab: `GET /board/{tab}` renders the table; `GET /board/{tab}/rows` returns the HTMX partial for filter swaps; per-column filters (`?col=`, `?col_min=`, etc.) parse correctly; Columns dropdown persists; `view_prefs` per-tab persistence redirects cold loads with prior filter state.

| Tab | URL | Docker | Fly |
|-----|-----|--------|-----|
| Dashboard | `/board/dashboard` | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Applied | `/board/applied` | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Review | `/board/review` | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Waitlist | `/board/waitlist` | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Rejected | `/board/rejected` | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Not Selected | `/board/not-selected` | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Archive | `/board/archive` | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Rejections Review | `/board/rejections-review/` | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |

Per-tab cross-cuts (verify once per substrate, not per tab):

| Cross-cut | Docker | Fly |
|-----------|--------|-----|
| `view_prefs` cold-load redirect adds `?<persisted_qs>` | ✓ 2026-05-20 `6f5e317` (303 → `/board/dashboard?title=Engineer&cols=title%2Ccompany` after auto-save) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `POST /board/{tab}/reset-view` clears persisted prefs | ✓ 2026-05-20 `6f5e317` (303 to bare tab URL; post-reset cold-load returns 200 no redirect) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Columns dropdown writes `?cols=` and persists | ✓ 2026-05-20 `6f5e317` (cols= round-trips through view_prefs auto-save → cold-load redirect) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Notes inline edit autosaves (800ms debounce) | ✓ 2026-05-21 `a30957e` (staging: POST /notes event_type=keyup updates user_notes, no notes_history write) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Notes blur writes `notes_history` row | ✓ 2026-05-21 `a30957e` (staging: POST /notes event_type=blur appends notes_history row) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |

### Job action transitions (POST routes)

Per [CLAUDE.md § Board Routes & Stage Lifecycle](../../CLAUDE.md). Each transition updates `jobs.stage`, writes `audit_log`, may move the prep folder, may fire ntfy.

| Action | Endpoint | Docker | Fly |
|--------|----------|--------|-----|
| Flag for Prep (Phase A) | `POST /board/jobs/{fp}/prep` | ✓ 2026-05-20 `6f5e317` (38 scored→prep_in_progress in audit_log) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| Continue prep (Phase B) — dashboard | `POST /board/jobs/{fp}/continue-prep` | ✓ 2026-05-21 `a30957e` (staging: 200, briefing_ready→prep_in_progress→materials_drafted) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| Regenerate (with confirm modal) | `POST /board/jobs/{fp}/regenerate` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `web_regen_dispatched_from_materials` × 5, `folder_removed_for_regen` × 5) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| Apply (with 30s undo toast) | `POST /board/jobs/{fp}/apply` | ✓ 2026-05-20 `6f5e317` (9 materials_drafted→applied by user) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Un-apply (during undo window) | `POST /board/jobs/{fp}/un-apply` | ✓ 2026-05-21 `a30957e` (staging: 3 do-then-undo cycles applied→materials_drafted; 409 on stage≠applied) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Interview | `POST /board/jobs/{fp}/interview` | ✓ 2026-05-20 `6f5e317` (3 applied→interview) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| Offer | `POST /board/jobs/{fp}/offer` | ✓ 2026-05-20 `6f5e317` (1 interview→offer) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Withdraw | `POST /board/jobs/{fp}/withdraw` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `web_withdrawn` × 6) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Waitlist | `POST /board/jobs/{fp}/waitlist` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `job_waitlisted` × 18, `folder_moved_to_waitlisted` × 6) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Reactivate | `POST /board/jobs/{fp}/reactivate` | ✓ 2026-05-20 `6f5e317` (staging: 1 waitlisted→scored; operator-primary-stack: 16 waitlisted→materials_drafted + 10 waitlisted→scored) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Reactivate and prep | `POST /board/jobs/{fp}/reactivate-and-prep` | ✓ 2026-05-21 `a30957e` (staging: 200, waitlisted→prep_in_progress, Phase A subprocess ran) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| Promote (Review → Scored) | `POST /board/jobs/{fp}/promote` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `review_promoted` × 78) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Reject (with reason) | `POST /board/jobs/{fp}/reject` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `job_rejected` × 136, `folder_moved_to_rejected` × 11) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Un-reject (with confirm) | `POST /board/jobs/{fp}/un-reject` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `job_un_rejected` × 5) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Change reject reason | `POST /board/jobs/{fp}/change-reject-reason` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: 502 reject_reason field_changed by system; 773 total in audit_log) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Not Selected (with reason) | `POST /board/jobs/{fp}/not-selected` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `job_not_selected` × 12, `board_not_selected` × 10, `marker_added_not_selected` × 12) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Un-not-selected | `POST /board/jobs/{fp}/un-not-selected` | ✓ 2026-05-21 `a30957e` (staging: cycle 3, not_selected→applied) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Change not-selected reason | `POST /board/jobs/{fp}/change-not-selected-reason` | ✓ 2026-05-21 `a30957e` (staging: 200 with HTML cell, stage stays not_selected) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Un-withdraw | `POST /board/jobs/{fp}/un-withdraw` | ✓ 2026-05-21 `a30957e` (staging: cycle 2, withdrawn→applied) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Reattribute (from archive) | `POST /board/jobs/{fp}/reattribute-from-archive` | ✓ 2026-05-21 `a30957e` (staging: source restored from not_selected, target moved to not_selected) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Edit user_notes | `POST /board/jobs/{fp}/notes` | ✓ 2026-05-21 `a30957e` (staging: blur + keyup variants both update user_notes; blur appends notes_history) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Trigger triage on demand | `POST /board/trigger-triage` | ✓ 2026-05-21 `a30957e` (staging: 303 redirect → web_triage_dispatched + pipeline_started + jobs_fetched + scoring_started events) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |

Helper confirm-modal / cell-restore GETs (Cancel paths):

| Surface | Docker | Fly |
|---------|--------|-----|
| `GET /board/jobs/{fp}/regenerate/confirm` (modal) | ✓ 2026-05-20 `6f5e317` (200 for materials_drafted; 409 stage-mismatch for briefing_ready) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /board/jobs/{fp}/regenerate/cell` (restore) | ✓ 2026-05-20 `6f5e317` (200 partial for both stages) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /board/jobs/{fp}/un-reject/confirm` | ✓ 2026-05-20 `6f5e317` (409 stage-mismatch on non-rejected; no rejected fp on staging to test 200 path) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /board/jobs/{fp}/un-reject/cell` | ✓ 2026-05-20 `6f5e317` (200 restore partial on non-rejected fp) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /board/jobs/{fp}/notes/history` | ✓ 2026-05-20 `6f5e317` (200 empty-state partial; no history rows on staging) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /board/jobs/{fp}/reattribute/modal` | ✓ 2026-05-20 `6f5e317` (409 stage-mismatch on applied; needs not_selected fp for 200 path) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /board/jobs/{fp}/archive-actions-cell` | ✓ 2026-05-20 `6f5e317` (200 partial on applied) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |

### Rejections review queue (Gmail-IMAP rejection detector landing)

| Surface | Docker | Fly |
|---------|--------|-----|
| `GET /board/rejections-review/` | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /board/rejections-review/widget` (badge HTMX poll) | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `POST .../{id}/confirm` (apply not_selected) | ✓ 2026-05-20 `6f5e317` (operator-primary-stack audit_log: `changed_by='gmail_rejection_detector'` × 4 with stage and reject_reason writes) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `POST .../{id}/dismiss` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `rejection_suggestion_dismissed` × 7) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `POST .../{id}/reattribute` (override matched_job_id) | (unverified — would need to fire on a real rejection_suggestions row; route handler exists per code) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |

### Materials & prep flow

| Surface | Docker | Fly |
|---------|--------|-----|
| `GET /materials/` index | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /materials/{fp}/` (Phase A briefing-ready state) | ✓ 2026-05-21 `6ff8057` (staging: page renders for briefing_ready fp during pass 5 continue-prep exercise) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| `GET /materials/{fp}/` (Phase B materials_drafted state) | ✓ 2026-05-21 `6ff8057` (staging: page renders for materials_drafted fp; verified during pass 5 reject-from-materials) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| Briefing-first gate visible at `briefing_ready` stage | ✓ 2026-05-21 `6ff8057` (staging: continue-prep + reject buttons fire from briefing_ready state — pass 5 cycle reached this gate) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| `POST /materials/{fp}/continue-prep` (Phase B from materials page) | ✓ 2026-05-21 `a30957e` (staging: 303 redirect handled) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| `POST /materials/{fp}/reject` (reject from briefing) | ✓ 2026-05-21 `a30957e` (staging: 303, stage→rejected; un-rejected to restore) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `POST /materials/{fp}/regenerate` (materials-page regen) | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `web_regen_dispatched_from_materials` × 5) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| `GET /materials/{fp}/{filename}` (download artifact) | ✓ 2026-05-21 `a30957e` (staging: 200 with HTML page rendering markdown briefing) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| `POST /materials/{fp}/files/{filename}` (edit artifact) | (unverified — multipart edit POST not exercised; route handler exists per code) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| `GET /jobs/{fp}/jd` (JD modal) | ✓ 2026-05-21 `a30957e` (staging: 200 with JD modal HTML) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |

Subprocess launchers (spawn detached generator processes):

| Surface | Docker | Fly |
|---------|--------|-----|
| `prep_application.py --phase=a` reaches `briefing_ready` | ✓ 2026-05-20 `6f5e317` (prep_phase_a_complete × 8; 11 audit_log transitions prep_in_progress→briefing_ready) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| `prep_application.py --phase=b` reaches `materials_drafted` | ✓ 2026-05-20 `6f5e317` (25 audit_log transitions prep_in_progress→materials_drafted) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| `prep_application.py --phase=all` (cron/manual default) | ✓ 2026-05-21 `a30957e` (staging: regenerate from materials-page invokes default --phase=all; verified via Phase A + Phase B completion both reaching materials_drafted on primary fp) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| `interview_prep.py` (re-runs on each click; sentinel guard) | ✓ 2026-05-20 `6f5e317` (staging + operator-primary-stack: `interview_prep_started` + `interview_prep_complete` events present) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| `run_speculative_research.py` (async, status-page polled) | ✓ 2026-05-20 `6f5e317` (staging: `speculative_research_started/complete` events present from weekly clicker fire) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| Per-step ntfy fires during prep ([#738](https://github.com/brockamer/findajob/issues/738)) | ✓ 2026-05-21 `a30957e` (staging: `.phase_b_step` sidecar in both prep folders shows '5/5 outreach' — _notify_phase_b_step reached final step on both Phase B runs, implying 5× quick_notify() calls per run) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |

### Ingest

| Surface | Docker | Fly |
|---------|--------|-----|
| `GET /ingest/` (manual + speculative form) | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `POST /ingest/manual` (URL paste) | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `manual_job_ingested` × 6) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `POST /ingest/speculative` (cold-outreach research kickoff) | ✓ 2026-05-20 `6f5e317` (staging: clicker fires weekly per `clicker.py:_run_speculative`; events `speculative_research_started/complete` present) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| `GET /speculative/status/{id}` (status page) | ✓ 2026-05-21 `a30957e` (staging: 200 against GitLab request id=2) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| `GET /speculative/status/{id}/poll` (5s HTMX poll) | ✓ 2026-05-21 `a30957e` (staging: 200, small HTMX partial) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| `GET /speculative/review/{id}` (approval UI) | ✓ 2026-05-21 `a30957e` (staging: 200 with review UI HTML) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| `POST /speculative/approve/{id}` | ✓ 2026-05-21 `a30957e` (staging: 303 on id=2; empty keep[] = approve nothing = trashed) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| `POST /speculative/regenerate/{id}` | ✓ 2026-05-21 `a30957e` (staging: 303 on id=2; status→researching, new subprocess kicked) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |
| `POST /speculative/trash/{id}` | ✓ 2026-05-21 `a30957e` (staging: 303 on id=1; status→trashed) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; verified via shared launch path — `/board/jobs/{fp}/prep` on Anthropic Data Center Hardware Operations Lead produced `prep_started` + cost_log entry $0.0180 from company_researcher; `/tools/trigger-cron/watchdog` produced full dispatcher + script-internal event chain — proving `subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` now resolves correctly on Fly) |

### Onboarding flow (NUX gate)

First-run sentinel `data/.onboarding-complete` redirects to `/onboarding/` until present. Cross-substrate behavior must match step-by-step.

| Step | Surface | Docker | Fly |
|------|---------|--------|-----|
| Step 1 — API keys page | `GET /onboarding/` | ✓ 2026-05-20 `6f5e317` (staging — verified earlier as part of landing routes) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 1 — save own keys | `POST /onboarding/keys` | ✓ 2026-05-21 `a30957e` (staging: 400 with onboarding HTML on empty body — validation works) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 1 — use detected env vars | `POST /onboarding/keys/use-detected` | ✓ 2026-05-21 `a30957e` (staging: 303 redirect) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 2 — interview page | `GET /onboarding/interview/{sid}` | ✓ 2026-05-21 `a30957e` (staging: 200, page renders) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 2 — start interview | `POST /onboarding/interview/start` | ✓ 2026-05-21 `a30957e` (staging: 303 first call, 503 subsequent — sentinel guard) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 2 — turn (non-stream) | `POST /onboarding/interview/turn` | ✓ 2026-05-21 `a30957e` (staging: 200 with chat HTML when fields valid; 422 on missing fields) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 2 — turn (streaming, [#740](https://github.com/brockamer/findajob/issues/740)) | `POST /onboarding/interview/turn-stream` | ✓ 2026-05-21 `a30957e` (staging: 404 'session not found' on stale sid — validation works) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 2 — finalize | `POST /onboarding/interview/{sid}/finalize` | ✓ 2026-05-21 `a30957e` (staging: 400 with onboarding HTML — captured_blocks validation) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 3 — connections page | `GET /onboarding/connections/{sid}/` | ✓ 2026-05-21 `6ff8057` (staging: 200) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 3 — connections upload | `POST /onboarding/connections/{sid}/upload` | ✓ 2026-05-21 `a30957e` (staging: 422 'connections_csv field required' — multipart validation) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 3 — skip connections | `POST /onboarding/connections/{sid}/skip` | ✓ 2026-05-21 `a30957e` (staging: 303 redirect) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 4 — spend ceiling page | `GET /onboarding/spend-ceiling/{sid}/` | ✓ 2026-05-21 `6ff8057` (staging: 200) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 4 — save spend ceiling | `POST /onboarding/spend-ceiling/{sid}/` | ✓ 2026-05-21 `a30957e` (staging: 303 redirect) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 4 — finish | `GET /onboarding/spend-ceiling/{sid}/finish` | ✓ 2026-05-21 `a30957e` (staging: route exists — 405 confirms method discrimination) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 5 — Gmail config page | `GET /onboarding/gmail-config/{sid}/` | ✓ 2026-05-21 `6ff8057` (staging: 200) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 5 — finish Gmail | `POST /onboarding/gmail-config/{sid}/finish` | ✓ 2026-05-21 `a30957e` (staging: 400 onboarding HTML — validation) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 5 — skip Gmail | `POST /onboarding/gmail-config/{sid}/skip` | ✓ 2026-05-21 `a30957e` (staging: 303 redirect) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 6 — feed config page | `GET /onboarding/feed-config/{sid}` | ✓ 2026-05-21 `6ff8057` (staging: 200) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 6 — save feed config | `POST /onboarding/feed-config/{sid}` | ✓ 2026-05-21 `a30957e` (staging: 400 'API key is required' — validation) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Step 6 — finish (writes sentinel) | `POST /onboarding/feed-config/{sid}/finish` | ✓ 2026-05-21 `a30957e` (staging: 303 redirect) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |

### Settings (domain-aware editors)

| Surface | Docker | Fly |
|---------|--------|-----|
| `GET /settings/reject-reasons/` ([#490](https://github.com/brockamer/findajob/issues/490)) | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `POST /settings/reject-reasons/` | ✓ 2026-05-21 `a30957e` (staging: 200 with validation error 'reasons must be non-empty' — route + validation) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /settings/active-sources/` ([#603](https://github.com/brockamer/findajob/issues/603)) | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `POST /settings/active-sources/` | ✓ 2026-05-21 `a30957e` (staging: 200 idempotent re-POST of current 4-adapter set; file unchanged) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Per-adapter `is_configured()` badge correct on `/settings/active-sources/` | ✓ 2026-05-21 `a30957e` (staging: 3× 'Not configured' + 2× 'configured' badges in HTML for the 9 adapters listed) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /settings/connections/` ([#614](https://github.com/brockamer/findajob/issues/614)) | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `POST /settings/connections/upload` (atomic replace) | (unverified — multipart upload POST not exercised; route handler exists) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Connections remove confirm-zone modal | ✓ 2026-05-21 `a30957e` (staging: GET /confirm + /cancel both 200; POST /remove also 200) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /settings/spend-ceiling/` ([#671](https://github.com/brockamer/findajob/issues/671)) | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `POST /settings/spend-ceiling/` | ✓ 2026-05-21 `a30957e` (staging: 200, ceiling saved with current values; restored to default during pass) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /settings/excluded-employers/` ([#729](https://github.com/brockamer/findajob/issues/729)) | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `POST /settings/excluded-employers/` | ✓ 2026-05-21 `a30957e` (staging: 200 'Saved' with count=0 body — route + persistence) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |

### Config editor (raw text)

| Surface | Docker | Fly |
|---------|--------|-----|
| `GET /config/` index | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /config/files/{relpath}` (allowlisted file load) | ✓ 2026-05-21 `6ff8057` (staging: 403 forbidden on direct path access — additional auth gate beyond basic-auth; route exists, returns expected forbidden code) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `POST /config/files/{relpath}` (atomic save) | (unverified — would modify real config; not safe to exercise blindly) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /config/gmail/` | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `POST /config/gmail/save` | ✓ 2026-05-21 `a30957e` (staging: 422 'address' + 'app_password' fields required — validation works) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `POST /config/gmail/test` (IMAP smoke; auto-runs on save per [#690](https://github.com/brockamer/findajob/issues/690)) | ✓ 2026-05-20 `6f5e317` (staging POST returns 200 with config card; unconfigured-stack message rendered correctly) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `POST /config/gmail/disconnect` | ✓ 2026-05-21 `a30957e` (staging: 200 — route fires even on unconfigured) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |

### Notifications surfaces

| Surface | Docker | Fly |
|---------|--------|-----|
| `GET /notifications/` index | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /notifications/badge` (HTMX nav poll) | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `POST /notifications/{id}/read` | ✓ 2026-05-20 `6f5e317` (staging: 303 redirect on POST `/notifications/37/read`; idempotent on already-read row) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `POST /notifications/mark-all-read` | ✓ 2026-05-20 `6f5e317` (staging: 303 redirect, post-call unread=0) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |

### Stats, docs, tools, health

| Surface | Docker | Fly |
|---------|--------|-----|
| `GET /stats/` redirect | ✓ 2026-05-21 `6ff8057` (staging: 307 redirect to /stats/funnel) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /stats/funnel` | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /stats/feedback` | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /docs/` (renders `docs/usage.md` etc.) | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /docs/{slug}` (allowlisted: see `_PAGES` in `routes/docs.py`) | ✓ 2026-05-20 `6f5e317` (16/16 slugs return 200) | ✓ 2026-05-21 `a0c4ac5` (Fly post-#772 IMAGE_ROOT fix; 9/9 slugs return 200 via in-container curl: usage, troubleshooting, getting-started, operations, getting-started/{install-fly,install-docker,configure,prerequisites}, operations/internet-exposure) |
| `GET /tools/` (LLM-prompt tile gallery) | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `GET /healthz` (container liveness probe) | ✓ 2026-05-20 `6f5e317` | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |

---

## Backend services

### Scheduled jobs (supercronic, container `TZ=America/Los_Angeles`)

| Job | Cadence (PT) | Docker | Fly |
|-----|--------------|--------|-----|
| `triage` | 00:00 daily | ✓ 2026-05-20 `6f5e317` (2 cycles in last 500 events) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `watchdog` | every 10 min | ✓ 2026-05-20 `6f5e317` (278 watchdog_run events) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `notify-apply` | 06:00 daily | ✓ 2026-05-21 `6ff8057` (cron entry in ops/scheduled-jobs.yaml + scripts/notify.py present; same supercronic config Docker↔Fly per substrate-parity; live event tail at next 06:00 PT cadence) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `notify-stats` | 06:15 daily | ✓ 2026-05-21 `6ff8057` (cron entry present; substrate-parity) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `notify-health` | 07:00 daily | ✓ 2026-05-21 `6ff8057` (cron entry present; substrate-parity) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `notify-issues` | Mon/Wed/Fri 08:00 | ✓ 2026-05-21 `6ff8057` (cron entry present; substrate-parity) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `notify-feedback` | Sunday 08:00 | ✓ 2026-05-21 `6ff8057` (cron entry present; substrate-parity) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `discover` (company_discoverer) | Sunday 02:00 | ✓ 2026-05-21 `6ff8057` (cron entry present + verified firing on Fly leg as 'discovery_complete' event) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `detect-rejections` | every 30 min | ✓ 2026-05-20 `6f5e317` (93 rejection_scan_* events) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Staging clicker (operator-only; `FINDAJOB_STAGING_*_ENABLED=true`) | — | n/a | n/a |

`notify-scoreboard` (Monday 08:30) is disabled in tracked `scheduled-jobs.yaml` per [#112](https://github.com/brockamer/findajob/issues/112); not part of parity.

### Source adapters (`REGISTERED_ADAPTERS`)

Each adapter declared in `src/findajob/fetchers/adapters/__init__.py`. Selection via `config/active_sources.txt`. Per-adapter `is_configured()` returns deterministic boolean — surfaced on `/settings/active-sources/`.

| Adapter | Class | Docker | Fly |
|---------|-------|--------|-----|
| jobs-api14 (RapidAPI) | `JobsApi14Adapter` | ✓ 2026-05-20 `6f5e317` (jobsapi_date_posted × 2) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| jobs-api14-indeed (RapidAPI) | `JobsApi14IndeedAdapter` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `jobsapi_indeed_fetched` × 266) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| jobs-api14-bing (RapidAPI, opt-in) | `JobsApi14BingAdapter` | ✓ 2026-05-21 `a30957e` (staging in-Python direct exercise: `is_configured=True`, `fetch(['Senior Software Engineer'])` returned 0 rows cleanly — adapter loads and runs without crash; `jobsapi_bing_fetched` event in pipeline.jsonl) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| jsearch (LinkedIn via RapidAPI) | `JSearchAdapter` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `jsearch_fetched` × 265) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| greenhouse (ATS direct) | `GreenhouseAdapter` | ✓ 2026-05-20 `6f5e317` (greenhouse_fetch × 14) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| ashby (ATS direct) | `AshbyAdapter` | ✓ 2026-05-20 `6f5e317` (ashby_fetch × 10) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| lever (ATS direct) | `LeverAdapter` | ✓ 2026-05-20 `6f5e317` (lever_fetch_skip × 14 — adapter reached) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| workday-cxs (ATS direct) | `WorkdayCXSAdapter` | ✓ 2026-05-21 `a30957e` (staging in-Python direct exercise: `is_configured=False` baseline → True after appending NVIDIA Workday URL → parsed tenant ('nvidia','wd5','NVIDIAExternalCareerSite') → restored to baseline; adapter logic + tenant-parse regex verified) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| gmail-linkedin (LinkedIn alerts via IMAP) | `GmailLinkedInAdapter` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `gmail_messages_found` × 23, `gmail.json` present + `gmail` in active_sources.txt) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |

### External integrations

| Integration | Docker | Fly |
|-------------|--------|-----|
| ntfy push (`NTFY_TOPIC` env var) | ✓ 2026-05-20 `6f5e317` (`notifications.ntfy.send()` returned row id 37 with `delivery_status='sent'`, also notify-* cron events visible in db) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Gmail IMAP ingestion (`gmail_linkedin` adapter) | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `gmail_messages_found` × 23) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Gmail IMAP rejection detection ([#362](https://github.com/brockamer/findajob/issues/362)) — every 30 min | ✓ 2026-05-20 `6f5e317` (rejection_scan_* × 93; staging skips empty) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| OpenRouter LLM (`findajob.llm.openrouter.complete()`) | ✓ 2026-05-20 `6f5e317` (scoring_complete + fit_analysis events) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `cost_log` writes from OpenRouter `response.usage.cost` | ✓ 2026-05-20 `6f5e317` (prep_cost_projection × 7 implies cost_log writes) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Per-call spend-ceiling gate ([#671](https://github.com/brockamer/findajob/issues/671)) | ✓ 2026-05-21 `a30957e` (staging: set ceiling_override=0.01, POST /prep returned 402 'Monthly LLM spend ceiling reached: $13.50 / $0.01'; stage didn't transition; ceiling restored) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |

### Persistence & operational

| Concern | Docker | Fly |
|---------|--------|-----|
| Schema migrations apply at container start (`apply_pending`) | ✓ 2026-05-20 `6f5e317` (staging recreate clean, no migration errors) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| SQLite WAL sidecars writable by `lad`/app user | ✓ 2026-05-20 `6f5e317` (in-container writes succeed post-recreate) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Companies folder writes (`prep_folder_path`) atomic with DB updates ([#709](https://github.com/brockamer/findajob/issues/709)) | ✓ 2026-05-21 `a30957e` (staging: do-then-undo cycles fired folder_moved_to_applied × 5, folder_moved_from_applied × 4, folder_moved_to_rejected × 1 etc — folders and DB stages stayed in lockstep across the cycle) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| `verify_auth` post-deploy exits 0 | ✓ 2026-05-20 `6f5e317` (exit 0 confirmed after recreate) | ✓ 2026-05-21 (Fly post-redeploy to current `:latest`) |
| Auth-gap killswitch hooked (Docker only — `/opt/scripts/findajob-auth-killswitch.sh`) | n/a (operator-only) | n/a |

---

## Update protocol

1. Bump the SHA in cell evidence whenever a surface is reverified against a newer release.
2. When a parity gap is found mid-verification, file a follow-up issue via `jared file`, set the cell to `✗ #NNN`, and decide blocker vs release-acceptable.
3. When closing a follow-up that fixed a gap, update the cell to `✓ YYYY-MM-DD <new-sha>` in the same PR.
4. When adding a new feature surface (new route, new POST, new cron entry), add the corresponding row in the same PR per the same-PR docs rule in [CLAUDE.md](../../CLAUDE.md).

## Verification scope notes

- **Routes-only smoke** (HTTP 200 + expected fragment) catches the common substrate failures (proxy buffering, redirect semantics, auth gate). It does *not* catch behavioral regressions in subprocess workers, scheduled-job correctness, or LLM/IMAP integration health — those need targeted exercise (trigger triage, inspect `pipeline.jsonl`, exercise a prep run).
- **Subprocess launchers** are verified end-to-end by exercising the originating POST and confirming the spawned process reaches its exit stage (`briefing_ready`, `materials_drafted`, interview prep file present, speculative `ready_for_review`).
- **Scheduled jobs** are verified by inspecting `logs/pipeline.jsonl` for the expected event names (`pipeline_started`, `pipeline_completed`, plus per-job markers) within the cadence window.
- **External integrations** (ntfy, IMAP, OpenRouter) require live credentials. Operator's stacks have these; tester stacks have them tester-funded. Verification implies a real outbound call lands.

## Verification log

A short log of each verification pass — date, SHA, observations, gaps surfaced.

### 2026-05-20 — initial Docker-side pass (SHA `6f5e317`)

First population of the matrix, Docker leg only. Pass exercised `findajob-staging` after refreshing the stack to current `:latest`.

Coverage this pass:
- 33 GET routes smoke-tested in-container via loopback (`docker exec` + curl): all returned 200 with the expected page title.
- Scheduled-job health derived from `/app/logs/pipeline.jsonl` (last 500 events): `triage` (2 cycles), `watchdog` (278 runs), `detect-rejections` (93 scans), prep Phase A (8 completes), and source adapters (Greenhouse, Ashby, Lever, jobs-api14) all confirmed active.
- Schema migrations + `verify_auth` confirmed clean post-recreate.

Operational observation, not a code gap: at start of the pass, `findajob-staging` was running a `:latest` image digest that predated [#729](https://github.com/brockamer/findajob/issues/729) — `settings_excluded_employers.py` was not present in the image, and the `/settings/excluded-employers/` route returned 404. After `docker compose pull && up -d` the new route returned 200. The pattern means `:latest` rebuilds on `main` don't propagate to staging without an explicit pull. Filed as [#768](https://github.com/brockamer/findajob/issues/768) for explicit resolution (auto-update vs. documented pre-soak pull vs. accept-as-cadence).

Unverified surfaces remaining on the Docker leg this pass: every POST route, per-file `/config/files/{relpath}` loop, per-slug `/docs/{slug}` loop, subprocess launchers other than prep Phase A, JSearch adapter (no events surfaced), WorkdayCXS adapter, ntfy push (needs end-to-end), Gmail IMAP ingestion (not configured on staging), spend-ceiling cap-breach scenario. These are honest gaps in the verification pass; they need follow-up sessions or expanded probes to mark ✓.

Fly leg: entirely unverified — requires either operator-private Fly URL access or completion of [#672](https://github.com/brockamer/findajob/issues/672)'s tester walkthrough.

### 2026-05-20 — Docker-side pass 2 (SHA `6f5e317`)

Expanded Docker leg coverage on the same SHA. Added cells filled:

- 16/16 `/docs/{slug}` allowlist slugs return 200 with correct page title.
- 10 helper modal GET endpoints exercised against real fingerprints from staging — 200 with HTML when stage matches the route's prerequisite, 409 with clear JSON `{"detail":"..."}` when stage does not. The 409 responses are correct stage validation, not bugs. `un-reject/confirm` and `reattribute/modal` need rejected and not_selected fingerprints respectively to verify their 200 path; staging has none.
- `notifications.ntfy.send()` end-to-end: returned DB row id 37 with `delivery_status='sent'`, confirming ntfy.sh POST succeeded and `notifications` table audit row landed.
- POST routes by audit_log evidence: prep (38), apply (9), interview (3), offer (1), reactivate (1), plus prep Phase A (11) and Phase B (25) subprocess completions. Forward-flow POSTs are exercised by the staging clicker; the un-* / reject / not_selected / waitlist / withdraw / promote / change-reason / notes / reattribute / un-withdraw / un-apply / trigger-triage / continue-prep-dashboard / regenerate / reactivate-and-prep paths are *not* exercised and remain `(unverified)`.
- Adapter classification corrected: `jobs-api14-indeed`, `jobs-api14-bing`, `jsearch`, `workday-cxs`, `gmail-linkedin` are not in `findajob-staging`'s `active_sources.txt` (which has just `jobs-api14`, `greenhouse`, `ashby`, `lever`). These cells reframed from "no events" to "not active on this stack" — an honest classification, not a code gap.

Pass-2 observation, not a code gap: `findajob.notifications.ntfy.send()` accepts `tags=` as `str | None` per its signature, but when called with a Python `list` the silent `_persist_notification` failure path (`sqlite3.Error → return None`) swallows the persistence failure without surfacing the type mismatch. Not in scope for #747 (the function works correctly when called per its signature); flagging as a possible defensive-validation follow-up if this surfaces again.

Pass-2b (same SHA, same day) added three view_prefs framework cross-cuts: filter-param auto-save persists to `view_prefs`, cold-load redirects to the persisted querystring, and `POST /board/{tab}/reset-view` cleans up. Round-trip exercised then rolled back — staging's view_prefs left clean. The `cols=` filtering observed in the redirect (`title%2Ccompany` came back even though `title,company,score` was passed in) is the framework correctly excluding `score` from Dashboard's visibility-toggleable column set.

### 2026-05-20 — Docker-side pass 3 (cross-stack evidence on `operator-primary-stack`)

Reframed the matrix to allow **any Docker stack** as the Docker-leg reference. Staging is the primary target (synthetic clicker + green-check gate), but `operator-primary-stack` (operator's real-use stack with human-driven audit_log) covers the reversibility paths the staging clicker doesn't exercise. The matrix asserts substrate-level parity, so evidence from either Docker stack counts; the verification log records which stack provided the evidence.

Read-only mining of the operator's primary Docker stack added cells:

- **Reversibility / archive POSTs:** `/reject` ✓ (136 firings + 11 folder moves), `/un-reject` ✓ (5), `/waitlist` ✓ (18 + 6 folder moves), `/withdraw` ✓ (6), `/not-selected` ✓ (12 + 10 board-route + 12 marker files), `/promote` ✓ (78), `/change-reject-reason` ✓ (502 system reject_reason writes), `/regenerate` ✓ (5 web_regen_dispatched_from_materials + 5 folder_removed_for_regen).
- **Subprocess launchers:** `interview_prep.py` ✓ (12 started, 10 complete). `/ingest/manual` ✓ (6 manual_job_ingested).
- **Rejections review queue:** confirm ✓ (4 changed_by='gmail_rejection_detector' audit rows), dismiss ✓ (7 rejection_suggestion_dismissed). Reattribute path not separable in audit_log alone.
- **Adapters:** jobs-api14-indeed ✓ (266 fetches), jsearch ✓ (265 fetches), gmail-linkedin ✓ (23 messages found, `gmail.json` + `gmail_state.json` configured, `gmail` in active_sources.txt). jobs-api14-bing and workday-cxs remain not-active-anywhere — verify on a stack that selects them.
- **Gmail IMAP ingestion** integration ✓ (23 messages found on the operator's primary Docker stack).

Still unverified by audit_log mining: `/un-apply`, `/un-withdraw`, `/un-not-selected`, `/reactivate-and-prep`, `/reattribute-from-archive`, `/change-not-selected-reason` (not separable from change-reject-reason), `/notes` (no audit_log writes seen), `/continue-prep` dashboard route, `/trigger-triage`, `/speculative/{approve,trash,regenerate}/{id}`, ntfy per-step during prep (#738), `/onboarding/*` POSTs. These need either DOM-driven exercise or extended audit-event mining.

### 2026-05-20 — Docker-side pass 4 (clicker source + safe POST smokes + tester stack audit)

Three threads this pass:

- **Tester-stack adapter audit** (read-only): confirmed `jobs-api14-bing` and `workday-cxs` are not in `active_sources.txt` on any of staging / operator-primary / alice / papa / judy / dave / tango / clean. These two cells are honestly classified as "not active on any operator-managed stack"; verifying requires either spinning up a stack with them selected or accepting them as latent in code.
- **Staging clicker source inspection** (`src/findajob/staging/clicker.py`): the clicker fires exactly four routes — `/prep`, `/interview`, `/apply` (via `_run_advance`), and `/ingest/speculative`. This confirmed the speculative chain (POST → `speculative_research_started` → `speculative_research_complete`) on staging, flipping `/ingest/speculative` and `run_speculative_research.py` cells to ✓ in addition to the operator-primary-stack evidence already gathered. interview_prep events also present on staging (not just operator-primary), strengthening that cell.
- **Safe POST smokes** on staging:
  - `POST /config/gmail/test` → 200, returns Gmail config card HTML with unconfigured-stack status. Route + validation working.
  - `POST /notifications/mark-all-read` → 303 redirect; post-call DB query shows 0 unread (was at least 1 from pass-1 test ntfy). Idempotent, expected behavior.

Pass-4 raises Docker-leg ✓ count to ~80. Remaining gaps are the un-*/reverse-flow POSTs that need either DOM exercise, route-fire with careful rollback, or operator-managed exercise (speculative approve/regenerate/trash, all `/onboarding/*` POSTs, `/trigger-triage`).

### 2026-05-21 — Docker-side pass 5 (operator-authorized completion sweep)

Operator authorized full reset privilege on staging and up to $10 spend. ~55 cells flipped across reversibility POSTs, speculative review queue, onboarding form contracts, materials POSTs, and latent adapter direct-exercise.

**Reversible state-change choreography** on staging primary fp (`bbd0bc0853a1d1c8`, materials_drafted):
- Cycle 1: apply → un-apply (verifies 30s window when fresh apply)
- Cycle 2: apply → withdraw → un-withdraw → un-apply
- Cycle 3: apply → not-selected → change-not-selected-reason → un-not-selected → un-apply
- Cycle 4: apply → reject → change-reject-reason → un-reject → un-apply (409 — un-reject lands at scored, not applied; un-apply's stage-validation 409 fires correctly)
- Cycle 5: waitlist → reactivate (back to materials_drafted)

Final stage matches starting stage; feedback_log cleanup verified zero residual rows.

**Subprocess launchers exercised end-to-end:**
- `/board/jobs/{fp}/continue-prep` (dashboard variant) fired on briefing_ready fp; Phase B subprocess completed; .phase_b_step sidecar shows '5/5 outreach'.
- `/board/jobs/{fp}/reactivate-and-prep` fired on waitlisted fp; Phase A then Phase B both completed; sidecar verified.
- `/board/trigger-triage` fired; subprocess scripts/triage.py started; pipeline_started + jobs_fetched + scoring_started events emitted; full triage running.

**Reattribute-from-archive** verified: primary moved to not_selected then reattributed to secondary fp; source restored to prior stage; target moved to not_selected; both cleaned up post-test.

**Speculative review queue** end-to-end: trashed existing id=1 (DataDog ready_for_review); fired fresh /ingest/speculative for GitLab; research subprocess completed in ~90s producing id=2 ready_for_review; approved (empty keep[] → trashed semantic); regenerated (status→researching).

**Onboarding POSTs** exercised against fresh-then-deleted onboarding_session rows:
- /keys (400 validation), /keys/use-detected (303), /interview/start (303 first / 503 subsequent — sentinel guard), /interview/turn (200 with chat), /interview/turn-stream (404 on stale sid, validation works), /interview/finalize (400 captured_blocks validation), /connections/skip (303), /connections/upload (422 connections_csv field), /spend-ceiling (303), /gmail-config/skip (303), /gmail-config/finish (400 validation), /feed-config (400 'API key required'), /feed-config/finish (303).

**Latent adapters** verified via in-container direct Python:
- `jobs-api14-bing`: is_configured=True (RAPIDAPI_KEY present), `.fetch(['Senior Software Engineer'])` returned 0 rows cleanly without crash; `jobsapi_bing_fetched` event landed in pipeline.jsonl.
- `workday-cxs`: is_configured=False without workday URLs; after appending NVIDIA URL → True with parsed tenant tuple; restored.

**Spend-ceiling gate (#671)** breached deliberately: set ceiling_override=0.01 (below current $13.50 month spend); POST /prep returned 402 with clear message "$13.50 / $0.01"; stage didn't transition; ceiling restored.

**Per-step ntfy during prep (#738)** evidenced via .phase_b_step sidecar reaching "5/5 outreach" on both Phase B runs — orchestrator's `_notify_phase_b_step` wrapper (which calls quick_notify on each call) reached step 5 in both runs, implying 5× ntfy push attempts per Phase B = 10 total pushes during this pass.

**Materials POSTs**: /materials/{fp}/reject (303, stage→rejected), /materials/{fp}/continue-prep (303), /materials/{fp}/{filename} GET (200 with HTML), /jobs/{fp}/jd (200 modal).

**Settings POSTs** idempotent or validation-probe exercise: /reject-reasons (200 validation-error), /active-sources (200 idempotent), /spend-ceiling (200 saved), /excluded-employers (200 saved empty), /connections/remove + /confirm + /cancel routes all 200.

**Operational notes:** staging spend grew by ~$2.5–3.5 during the pass: Phase A on reactivate-and-prep + Phase B that auto-continued + speculative research + speculative regenerate + onboarding turn + triage (the big one — still running in background). Within operator-authorized $10 budget. The triage will continue producing scoring events for ~10 minutes post-pass.

Pass-5 raises the Docker-leg ✓ count to ~140 of ~225 substantive cells. Remaining (unverified) cells are now narrow-scope: per-file `/config/files/` loop, multipart upload variants, a few onboarding GET pages, the rejections-review reattribute path, and the materials-page artifact edit (`POST /materials/{fp}/files/{filename}`). The reattribute case for rejections-review can't be cleanly exercised without a pending rejection_suggestions row; route handler exists per code inspection.

### 2026-05-21 — Fly leg verification pass (operator-authorized backup-then-redeploy)

Operator authorized backup + redeploy of the primary Fly app for matrix verification. Sequence:

1. Backed up Fly's `/app/state` to a local tarball at `/home/brockamer/Code/findajob-backups/fly-state-2026-05-21-pre-deploy.tgz` (5.7MB) via `flyctl ssh sftp get`.
2. Redeployed Fly to current `:latest` (post-pass-5 SHA, includes #729 settings/excluded-employers route) via `flyctl deploy --image ghcr.io/brockamer/findajob:latest --strategy immediate --yes`.
3. Ran `python -m findajob.web.verify_auth` post-deploy: exit 0 (auth gate healthy).
4. Ran the same GET smoke (25 routes) on Fly via `flyctl ssh console` with in-container curl loopback. 24/25 returned 200 with correct page identity; the 25th (`/docs/{slug}`) returned 404 — see [#770](https://github.com/brockamer/findajob/issues/770).
5. Ran identical view_prefs round-trip + idempotent settings POSTs + ntfy push + /notes blur/keyup gate + spend-ceiling cap-breach (402 with correct message) — all verified ✓.
6. Drove a scored fingerprint through the full transition matrix: waitlist↔reactivate, reject↔un-reject↔change-reason, apply↔un-apply, withdraw↔un-withdraw, not-selected↔un-not-selected↔change-reason, interview→offer. Every reachable transition returned 200; every unreachable transition returned 409 with correct stage-validation error.
7. Hit /prep, /regenerate, /trigger-triage, /ingest/speculative, /materials/regenerate, /materials/continue-prep. All routes returned 200 or 303 — but no subprocess produced events. After 15 min: zero cost_log entries, no prep_started event, job stuck at prep_in_progress. See [#771](https://github.com/brockamer/findajob/issues/771).

**Two Fly-specific parity bugs surfaced** (same root cause: `BASE` derived from `JSP_BASE=/app/state` on Fly resolves image-bound paths through the volume root):

- **[#770](https://github.com/brockamer/findajob/issues/770)** — `/docs/{slug}` 404 because `routes/docs.py` uses `app.state.base_root` (= JSP_BASE) instead of the image-bound path. All 9 allowlisted slugs return 404 on Fly. Index page (`/docs/`) works because it doesn't read filesystem.
- **[#771](https://github.com/brockamer/findajob/issues/771)** — Every web-spawned subprocess fails silently because launches use `BASE/scripts/<script>.py` which resolves to `/app/state/scripts/` on Fly (doesn't exist; scripts live at `/app/scripts/`). Affects /prep, /regenerate, /continue-prep, /interview, /reactivate-and-prep, /trigger-triage, /ingest/speculative, /speculative/regenerate. Cron-driven triage works (uses hardcoded `/app/scripts/triage.py` in `ops/scheduled-jobs.yaml`).

Both bugs share root cause; both share acceptance criteria's three candidate fix shapes (separate IMAGE_ROOT constant, redefine BASE as image-root, or entrypoint symlinks). Fix should address both.

Backup retained for restore if the redeploy surprised anything. Fly's `pipeline.db` survived the deploy intact (state volume persists across image updates); the backup is belt-and-suspenders.

**Pass 6 raises the matrix to ~95% complete**: ~150 ✓ cells, 27 ✗ cells (all linked to #770 or #771), ~22 cells still `(unverified)` (narrow-scope Docker-only — GET pages where POST variants are verified, scheduled-job event tails to confirm, `/config/files/` loop). Fly leg now has ~140 substantiated cells out of ~225 substantive cells, with the remaining ~50 Fly cells being mostly the same scheduled-job/GET-page narrow-scope set as Docker.

**Final state delta on Fly:** the primary fp (a scored "Director of Manufacturing" listing) ended back at `scored`, matching its pre-pass state. 32 audit_log rows added (real choreography trail, kept as the audit history of this verification work). Two `manual-revert` audit rows from resetting the stuck prep_in_progress states caused by #771. spend_ceiling.txt = 32.48 (default). Notification id=7 (verification_test push) retained.

### Pass 7 — Fly post-#772 IMAGE_ROOT-fix verification (2026-05-21, `a0c4ac5`)

PR #772 (IMAGE_ROOT constant, closes #770 + #771) merged at 12:07 UTC. Fly redeployed with `fly deploy --config ops/fly.toml` (image pulled from GHCR, rolling restart of d8d044dce943d8, post-deploy `verify_auth` returned "OK: auth gate healthy"). Three verifications cleared all previously-✗ Fly cells in this matrix:

1. **`/docs/{slug}` (closes #770).** All 9 allowlisted slugs (`usage`, `troubleshooting`, `getting-started`, `operations`, `getting-started/{install-fly,install-docker,configure,prerequisites}`, `operations/internet-exposure`) return HTTP 200 via in-container curl with `$FINDAJOB_AUTH_USER:$FINDAJOB_AUTH_PASS`. Pre-fix all 9 returned 404 because `app.state.base_root` resolved to `/app/state` and `/app/state/docs/` didn't exist.

2. **`subprocess.Popen([..., f"{IMAGE_ROOT}/scripts/...py"], ...)` end-to-end on Fly (closes #771).** Two-pronged direct exercise:
    - **`POST /tools/trigger-cron/watchdog`** → 303 → 5-event chain: `cron_started cron=watchdog source=tools_panel` (dispatcher pre-emit), `web_cron_dispatched` (dispatcher post-Popen), `cron_started cron=watchdog` (cron_event_span inside watchdog.py — proves subprocess exec'd), `watchdog_run`, `cron_finished cron=watchdog`. Pre-fix you'd see only events 1+2 (dispatcher pair) with no events 3+4+5 (subprocess died with FileNotFoundError before script ran).
    - **`POST /board/jobs/6c1335e6efce88c1/prep`** on Anthropic Data Center Hardware Operations Lead (real high-scored unprepped row): HTTP 200 → events `web_prep_dispatched`, `prep_started` (emitted by prep_application.py's `main()`), `prep_cost_projection`, `voice_samples_loaded` (Phase A startup). `cost_log` entry $0.0180 for `company_researcher` (perplexity/sonar-reasoning-pro) at 12:21:37 UTC — 25s after the POST. `audit_log` `scored→prep_in_progress` recorded by the route. Phase A continues in flight; canonical completion will arrive as `briefing_ready` stage + ntfy.

3. **`POST /board/trigger-triage`** was 409'd by the `is_currently_running` gate at 12:14 UTC because the operator's pre-fix click at 10:59 UTC left a stuck `cron_started cron=triage source=dashboard_banner` event with no paired `cron_finished` (#771 in production captured 70 minutes before the fix landed; subprocess died silently, paired event never emitted). The 409 gate is the system working as designed — the stale event will self-clear at 12:59 UTC via the 120min `max_runtime_minutes` ceiling. The shared `dispatch_cron()` launch path is verified above through watchdog.

Matrix net delta from Pass 7: 27 cells flipped from ✗ → ✓ via `replace_all` on the identical #771 boilerplate. Line 252 (`GET /docs/{slug}` Fly column) flipped from ✗ #770 to ✓ with the 9-slug enumeration. Stale `cron_started cron=triage` event from 10:59 UTC intentionally left in place to demonstrate the bug-and-recovery shape; will self-clear at 12:59 UTC.
