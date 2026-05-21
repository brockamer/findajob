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
| `GET /` landing | ✓ 2026-05-20 `6f5e317` | (unverified) |
| Top-nav present, all 9 groups linked | (unverified — needs DOM check) | (unverified) |
| Spend chip in nav reflects current month | (unverified — needs DOM check) | (unverified) |

### Board tabs (8 user-facing tabs)

Every tab: `GET /board/{tab}` renders the table; `GET /board/{tab}/rows` returns the HTMX partial for filter swaps; per-column filters (`?col=`, `?col_min=`, etc.) parse correctly; Columns dropdown persists; `view_prefs` per-tab persistence redirects cold loads with prior filter state.

| Tab | URL | Docker | Fly |
|-----|-----|--------|-----|
| Dashboard | `/board/dashboard` | ✓ 2026-05-20 `6f5e317` | (unverified) |
| Applied | `/board/applied` | ✓ 2026-05-20 `6f5e317` | (unverified) |
| Review | `/board/review` | ✓ 2026-05-20 `6f5e317` | (unverified) |
| Waitlist | `/board/waitlist` | ✓ 2026-05-20 `6f5e317` | (unverified) |
| Rejected | `/board/rejected` | ✓ 2026-05-20 `6f5e317` | (unverified) |
| Not Selected | `/board/not-selected` | ✓ 2026-05-20 `6f5e317` | (unverified) |
| Archive | `/board/archive` | ✓ 2026-05-20 `6f5e317` | (unverified) |
| Rejections Review | `/board/rejections-review/` | ✓ 2026-05-20 `6f5e317` | (unverified) |

Per-tab cross-cuts (verify once per substrate, not per tab):

| Cross-cut | Docker | Fly |
|-----------|--------|-----|
| `view_prefs` cold-load redirect adds `?<persisted_qs>` | ✓ 2026-05-20 `6f5e317` (303 → `/board/dashboard?title=Engineer&cols=title%2Ccompany` after auto-save) | (unverified) |
| `POST /board/{tab}/reset-view` clears persisted prefs | ✓ 2026-05-20 `6f5e317` (303 to bare tab URL; post-reset cold-load returns 200 no redirect) | (unverified) |
| Columns dropdown writes `?cols=` and persists | ✓ 2026-05-20 `6f5e317` (cols= round-trips through view_prefs auto-save → cold-load redirect) | (unverified) |
| Notes inline edit autosaves (800ms debounce) | (unverified — needs DOM-driven keyup event) | (unverified) |
| Notes blur writes `notes_history` row | (unverified — needs DOM-driven blur event) | (unverified) |

### Job action transitions (POST routes)

Per [CLAUDE.md § Board Routes & Stage Lifecycle](../../CLAUDE.md). Each transition updates `jobs.stage`, writes `audit_log`, may move the prep folder, may fire ntfy.

| Action | Endpoint | Docker | Fly |
|--------|----------|--------|-----|
| Flag for Prep (Phase A) | `POST /board/jobs/{fp}/prep` | ✓ 2026-05-20 `6f5e317` (38 scored→prep_in_progress in audit_log) | (unverified) |
| Continue prep (Phase B) — dashboard | `POST /board/jobs/{fp}/continue-prep` | (unverified — staging clicker exercises materials-page route, not dashboard route) | (unverified) |
| Regenerate (with confirm modal) | `POST /board/jobs/{fp}/regenerate` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `web_regen_dispatched_from_materials` × 5, `folder_removed_for_regen` × 5) | (unverified) |
| Apply (with 30s undo toast) | `POST /board/jobs/{fp}/apply` | ✓ 2026-05-20 `6f5e317` (9 materials_drafted→applied by user) | (unverified) |
| Un-apply (during undo window) | `POST /board/jobs/{fp}/un-apply` | (unverified — no applied→materials_drafted in audit_log on either stack) | (unverified) |
| Interview | `POST /board/jobs/{fp}/interview` | ✓ 2026-05-20 `6f5e317` (3 applied→interview) | (unverified) |
| Offer | `POST /board/jobs/{fp}/offer` | ✓ 2026-05-20 `6f5e317` (1 interview→offer) | (unverified) |
| Withdraw | `POST /board/jobs/{fp}/withdraw` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `web_withdrawn` × 6) | (unverified) |
| Waitlist | `POST /board/jobs/{fp}/waitlist` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `job_waitlisted` × 18, `folder_moved_to_waitlisted` × 6) | (unverified) |
| Reactivate | `POST /board/jobs/{fp}/reactivate` | ✓ 2026-05-20 `6f5e317` (staging: 1 waitlisted→scored; operator-primary-stack: 16 waitlisted→materials_drafted + 10 waitlisted→scored) | (unverified) |
| Reactivate and prep | `POST /board/jobs/{fp}/reactivate-and-prep` | (unverified — no waitlisted→prep_in_progress transitions on either stack) | (unverified) |
| Promote (Review → Scored) | `POST /board/jobs/{fp}/promote` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `review_promoted` × 78) | (unverified) |
| Reject (with reason) | `POST /board/jobs/{fp}/reject` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `job_rejected` × 136, `folder_moved_to_rejected` × 11) | (unverified) |
| Un-reject (with confirm) | `POST /board/jobs/{fp}/un-reject` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `job_un_rejected` × 5) | (unverified) |
| Change reject reason | `POST /board/jobs/{fp}/change-reject-reason` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: 502 reject_reason field_changed by system; 773 total in audit_log) | (unverified) |
| Not Selected (with reason) | `POST /board/jobs/{fp}/not-selected` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `job_not_selected` × 12, `board_not_selected` × 10, `marker_added_not_selected` × 12) | (unverified) |
| Un-not-selected | `POST /board/jobs/{fp}/un-not-selected` | (unverified — no not_selected→applied/interview in audit_log on either stack) | (unverified) |
| Change not-selected reason | `POST /board/jobs/{fp}/change-not-selected-reason` | (unverified — distinguishable from change-reject-reason only by deeper audit) | (unverified) |
| Un-withdraw | `POST /board/jobs/{fp}/un-withdraw` | (unverified — no withdrawn→applied transitions on either stack) | (unverified) |
| Reattribute (from archive) | `POST /board/jobs/{fp}/reattribute-from-archive` | (unverified — no clear audit marker; needs DOM exercise) | (unverified) |
| Edit user_notes | `POST /board/jobs/{fp}/notes` | (unverified — no `notes` audit_log writes seen; needs DOM keyup) | (unverified) |
| Trigger triage on demand | `POST /board/trigger-triage` | (unverified — cron-driven triage covers the underlying code path; manual route not exercised on either stack) | (unverified) |

Helper confirm-modal / cell-restore GETs (Cancel paths):

| Surface | Docker | Fly |
|---------|--------|-----|
| `GET /board/jobs/{fp}/regenerate/confirm` (modal) | ✓ 2026-05-20 `6f5e317` (200 for materials_drafted; 409 stage-mismatch for briefing_ready) | (unverified) |
| `GET /board/jobs/{fp}/regenerate/cell` (restore) | ✓ 2026-05-20 `6f5e317` (200 partial for both stages) | (unverified) |
| `GET /board/jobs/{fp}/un-reject/confirm` | ✓ 2026-05-20 `6f5e317` (409 stage-mismatch on non-rejected; no rejected fp on staging to test 200 path) | (unverified) |
| `GET /board/jobs/{fp}/un-reject/cell` | ✓ 2026-05-20 `6f5e317` (200 restore partial on non-rejected fp) | (unverified) |
| `GET /board/jobs/{fp}/notes/history` | ✓ 2026-05-20 `6f5e317` (200 empty-state partial; no history rows on staging) | (unverified) |
| `GET /board/jobs/{fp}/reattribute/modal` | ✓ 2026-05-20 `6f5e317` (409 stage-mismatch on applied; needs not_selected fp for 200 path) | (unverified) |
| `GET /board/jobs/{fp}/archive-actions-cell` | ✓ 2026-05-20 `6f5e317` (200 partial on applied) | (unverified) |

### Rejections review queue (Gmail-IMAP rejection detector landing)

| Surface | Docker | Fly |
|---------|--------|-----|
| `GET /board/rejections-review/` | ✓ 2026-05-20 `6f5e317` | (unverified) |
| `GET /board/rejections-review/widget` (badge HTMX poll) | ✓ 2026-05-20 `6f5e317` | (unverified) |
| `POST .../{id}/confirm` (apply not_selected) | ✓ 2026-05-20 `6f5e317` (operator-primary-stack audit_log: `changed_by='gmail_rejection_detector'` × 4 with stage and reject_reason writes) | (unverified) |
| `POST .../{id}/dismiss` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `rejection_suggestion_dismissed` × 7) | (unverified) |
| `POST .../{id}/reattribute` (override matched_job_id) | (unverified — no distinct audit marker; gmail_rejection_detector reject_reason × 2 may include reattribute path but not separable) | (unverified) |

### Materials & prep flow

| Surface | Docker | Fly |
|---------|--------|-----|
| `GET /materials/` index | ✓ 2026-05-20 `6f5e317` | (unverified) |
| `GET /materials/{fp}/` (Phase A briefing-ready state) | (unverified) | (unverified) |
| `GET /materials/{fp}/` (Phase B materials_drafted state) | (unverified) | (unverified) |
| Briefing-first gate visible at `briefing_ready` stage | (unverified) | (unverified) |
| `POST /materials/{fp}/continue-prep` (Phase B from materials page) | (unverified) | (unverified) |
| `POST /materials/{fp}/reject` (reject from briefing) | (unverified) | (unverified) |
| `POST /materials/{fp}/regenerate` (materials-page regen) | (unverified) | (unverified) |
| `GET /materials/{fp}/{filename}` (download artifact) | (unverified) | (unverified) |
| `POST /materials/{fp}/files/{filename}` (edit artifact) | (unverified) | (unverified) |
| `GET /jobs/{fp}/jd` (JD modal) | (unverified) | (unverified) |

Subprocess launchers (spawn detached generator processes):

| Surface | Docker | Fly |
|---------|--------|-----|
| `prep_application.py --phase=a` reaches `briefing_ready` | ✓ 2026-05-20 `6f5e317` (prep_phase_a_complete × 8; 11 audit_log transitions prep_in_progress→briefing_ready) | (unverified) |
| `prep_application.py --phase=b` reaches `materials_drafted` | ✓ 2026-05-20 `6f5e317` (25 audit_log transitions prep_in_progress→materials_drafted) | (unverified) |
| `prep_application.py --phase=all` (cron/manual default) | (unverified — staging clicker uses split phases) | (unverified) |
| `interview_prep.py` (re-runs on each click; sentinel guard) | ✓ 2026-05-20 `6f5e317` (staging + operator-primary-stack: `interview_prep_started` + `interview_prep_complete` events present) | (unverified) |
| `run_speculative_research.py` (async, status-page polled) | ✓ 2026-05-20 `6f5e317` (staging: `speculative_research_started/complete` events present from weekly clicker fire) | (unverified) |
| Per-step ntfy fires during prep ([#738](https://github.com/brockamer/findajob/issues/738)) | (unverified — needs ntfy topic capture during prep run) | (unverified) |

### Ingest

| Surface | Docker | Fly |
|---------|--------|-----|
| `GET /ingest/` (manual + speculative form) | ✓ 2026-05-20 `6f5e317` | (unverified) |
| `POST /ingest/manual` (URL paste) | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `manual_job_ingested` × 6) | (unverified) |
| `POST /ingest/speculative` (cold-outreach research kickoff) | ✓ 2026-05-20 `6f5e317` (staging: clicker fires weekly per `clicker.py:_run_speculative`; events `speculative_research_started/complete` present) | (unverified) |
| `GET /speculative/status/{id}` (status page) | (unverified — needs an active request_id) | (unverified) |
| `GET /speculative/status/{id}/poll` (5s HTMX poll) | (unverified — needs an active request_id) | (unverified) |
| `GET /speculative/review/{id}` (approval UI) | (unverified — needs a ready_for_review row) | (unverified) |
| `POST /speculative/approve/{id}` | (unverified — needs operator action; not in clicker scope) | (unverified) |
| `POST /speculative/regenerate/{id}` | (unverified — needs operator action; not in clicker scope) | (unverified) |
| `POST /speculative/trash/{id}` | (unverified — needs operator action; not in clicker scope) | (unverified) |

### Onboarding flow (NUX gate)

First-run sentinel `data/.onboarding-complete` redirects to `/onboarding/` until present. Cross-substrate behavior must match step-by-step.

| Step | Surface | Docker | Fly |
|------|---------|--------|-----|
| Step 1 — API keys page | `GET /onboarding/` | (unverified) | (unverified) |
| Step 1 — save own keys | `POST /onboarding/keys` | (unverified) | (unverified) |
| Step 1 — use detected env vars | `POST /onboarding/keys/use-detected` | (unverified) | (unverified) |
| Step 2 — interview page | `GET /onboarding/interview/{sid}` | (unverified) | (unverified) |
| Step 2 — start interview | `POST /onboarding/interview/start` | (unverified) | (unverified) |
| Step 2 — turn (non-stream) | `POST /onboarding/interview/turn` | (unverified) | (unverified) |
| Step 2 — turn (streaming, [#740](https://github.com/brockamer/findajob/issues/740)) | `POST /onboarding/interview/turn-stream` | (unverified) | (unverified) |
| Step 2 — finalize | `POST /onboarding/interview/{sid}/finalize` | (unverified) | (unverified) |
| Step 3 — connections page | `GET /onboarding/connections/{sid}/` | (unverified) | (unverified) |
| Step 3 — connections upload | `POST /onboarding/connections/{sid}/upload` | (unverified) | (unverified) |
| Step 3 — skip connections | `POST /onboarding/connections/{sid}/skip` | (unverified) | (unverified) |
| Step 4 — spend ceiling page | `GET /onboarding/spend-ceiling/{sid}/` | (unverified) | (unverified) |
| Step 4 — save spend ceiling | `POST /onboarding/spend-ceiling/{sid}/` | (unverified) | (unverified) |
| Step 4 — finish | `GET /onboarding/spend-ceiling/{sid}/finish` | (unverified) | (unverified) |
| Step 5 — Gmail config page | `GET /onboarding/gmail-config/{sid}/` | (unverified) | (unverified) |
| Step 5 — finish Gmail | `POST /onboarding/gmail-config/{sid}/finish` | (unverified) | (unverified) |
| Step 5 — skip Gmail | `POST /onboarding/gmail-config/{sid}/skip` | (unverified) | (unverified) |
| Step 6 — feed config page | `GET /onboarding/feed-config/{sid}` | (unverified) | (unverified) |
| Step 6 — save feed config | `POST /onboarding/feed-config/{sid}` | (unverified) | (unverified) |
| Step 6 — finish (writes sentinel) | `POST /onboarding/feed-config/{sid}/finish` | (unverified) | (unverified) |

### Settings (domain-aware editors)

| Surface | Docker | Fly |
|---------|--------|-----|
| `GET /settings/reject-reasons/` ([#490](https://github.com/brockamer/findajob/issues/490)) | ✓ 2026-05-20 `6f5e317` | (unverified) |
| `POST /settings/reject-reasons/` | (unverified — POST not exercised) | (unverified) |
| `GET /settings/active-sources/` ([#603](https://github.com/brockamer/findajob/issues/603)) | ✓ 2026-05-20 `6f5e317` | (unverified) |
| `POST /settings/active-sources/` | (unverified — POST not exercised) | (unverified) |
| Per-adapter `is_configured()` badge correct on `/settings/active-sources/` | (unverified — needs DOM check) | (unverified) |
| `GET /settings/connections/` ([#614](https://github.com/brockamer/findajob/issues/614)) | ✓ 2026-05-20 `6f5e317` | (unverified) |
| `POST /settings/connections/upload` (atomic replace) | (unverified — POST not exercised) | (unverified) |
| Connections remove confirm-zone modal | (unverified — needs DOM check) | (unverified) |
| `GET /settings/spend-ceiling/` ([#671](https://github.com/brockamer/findajob/issues/671)) | ✓ 2026-05-20 `6f5e317` | (unverified) |
| `POST /settings/spend-ceiling/` | (unverified — POST not exercised) | (unverified) |
| `GET /settings/excluded-employers/` ([#729](https://github.com/brockamer/findajob/issues/729)) | ✓ 2026-05-20 `6f5e317` | (unverified) |
| `POST /settings/excluded-employers/` | (unverified — POST not exercised) | (unverified) |

### Config editor (raw text)

| Surface | Docker | Fly |
|---------|--------|-----|
| `GET /config/` index | ✓ 2026-05-20 `6f5e317` | (unverified) |
| `GET /config/files/{relpath}` (allowlisted file load) | (unverified — needs per-file loop) | (unverified) |
| `POST /config/files/{relpath}` (atomic save) | (unverified — POST not exercised) | (unverified) |
| `GET /config/gmail/` | ✓ 2026-05-20 `6f5e317` | (unverified) |
| `POST /config/gmail/save` | (unverified — POST not exercised) | (unverified) |
| `POST /config/gmail/test` (IMAP smoke; auto-runs on save per [#690](https://github.com/brockamer/findajob/issues/690)) | ✓ 2026-05-20 `6f5e317` (staging POST returns 200 with config card; unconfigured-stack message rendered correctly) | (unverified) |
| `POST /config/gmail/disconnect` | (unverified — POST not exercised) | (unverified) |

### Notifications surfaces

| Surface | Docker | Fly |
|---------|--------|-----|
| `GET /notifications/` index | ✓ 2026-05-20 `6f5e317` | (unverified) |
| `GET /notifications/badge` (HTMX nav poll) | ✓ 2026-05-20 `6f5e317` | (unverified) |
| `POST /notifications/{id}/read` | ✓ 2026-05-20 `6f5e317` (staging: 303 redirect on POST `/notifications/37/read`; idempotent on already-read row) | (unverified) |
| `POST /notifications/mark-all-read` | ✓ 2026-05-20 `6f5e317` (staging: 303 redirect, post-call unread=0) | (unverified) |

### Stats, docs, tools, health

| Surface | Docker | Fly |
|---------|--------|-----|
| `GET /stats/` redirect | (unverified — redirect not exercised) | (unverified) |
| `GET /stats/funnel` | ✓ 2026-05-20 `6f5e317` | (unverified) |
| `GET /stats/feedback` | ✓ 2026-05-20 `6f5e317` | (unverified) |
| `GET /docs/` (renders `docs/usage.md` etc.) | ✓ 2026-05-20 `6f5e317` | (unverified) |
| `GET /docs/{slug}` (allowlisted: see `_PAGES` in `routes/docs.py`) | ✓ 2026-05-20 `6f5e317` (16/16 slugs return 200) | (unverified) |
| `GET /tools/` (LLM-prompt tile gallery) | ✓ 2026-05-20 `6f5e317` | (unverified) |
| `GET /healthz` (container liveness probe) | ✓ 2026-05-20 `6f5e317` | (unverified) |
| `POST /feedback/submit` (anonymous feedback form) | ✓ 2026-05-20 `6f5e317` (staging: 503 with graceful "not configured" message; pydantic 422 on missing `text` field — route + validation working) | (unverified) |

---

## Backend services

### Scheduled jobs (supercronic, container `TZ=America/Los_Angeles`)

| Job | Cadence (PT) | Docker | Fly |
|-----|--------------|--------|-----|
| `triage` | 00:00 daily | ✓ 2026-05-20 `6f5e317` (2 cycles in last 500 events) | (unverified) |
| `watchdog` | every 10 min | ✓ 2026-05-20 `6f5e317` (278 watchdog_run events) | (unverified) |
| `notify-apply` | 06:00 daily | (unverified — needs event tail before tag) | (unverified) |
| `notify-stats` | 06:15 daily | (unverified — needs event tail before tag) | (unverified) |
| `notify-health` | 07:00 daily | (unverified — needs event tail before tag) | (unverified) |
| `notify-issues` | Mon/Wed/Fri 08:00 | (unverified — needs event tail before tag) | (unverified) |
| `notify-feedback` | Sunday 08:00 | (unverified — needs event tail before tag) | (unverified) |
| `discover` (company_discoverer) | Sunday 02:00 | (unverified — runs weekly) | (unverified) |
| `detect-rejections` | every 30 min | ✓ 2026-05-20 `6f5e317` (93 rejection_scan_* events) | (unverified) |
| Staging clicker (operator-only; `FINDAJOB_STAGING_*_ENABLED=true`) | — | n/a | n/a |

`notify-scoreboard` (Monday 08:30) is disabled in tracked `scheduled-jobs.yaml` per [#112](https://github.com/brockamer/findajob/issues/112); not part of parity.

### Source adapters (`REGISTERED_ADAPTERS`)

Each adapter declared in `src/findajob/fetchers/adapters/__init__.py`. Selection via `config/active_sources.txt`. Per-adapter `is_configured()` returns deterministic boolean — surfaced on `/settings/active-sources/`.

| Adapter | Class | Docker | Fly |
|---------|-------|--------|-----|
| jobs-api14 (RapidAPI) | `JobsApi14Adapter` | ✓ 2026-05-20 `6f5e317` (jobsapi_date_posted × 2) | (unverified) |
| jobs-api14-indeed (RapidAPI) | `JobsApi14IndeedAdapter` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `jobsapi_indeed_fetched` × 266) | (unverified) |
| jobs-api14-bing (RapidAPI, opt-in) | `JobsApi14BingAdapter` | (not active on any operator-managed stack — alice/papa/judy/dave/tango/clean/staging/operator-primary; verify by selecting in a fresh stack's `active_sources.txt`) | (unverified) |
| jsearch (LinkedIn via RapidAPI) | `JSearchAdapter` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `jsearch_fetched` × 265) | (unverified) |
| greenhouse (ATS direct) | `GreenhouseAdapter` | ✓ 2026-05-20 `6f5e317` (greenhouse_fetch × 14) | (unverified) |
| ashby (ATS direct) | `AshbyAdapter` | ✓ 2026-05-20 `6f5e317` (ashby_fetch × 10) | (unverified) |
| lever (ATS direct) | `LeverAdapter` | ✓ 2026-05-20 `6f5e317` (lever_fetch_skip × 14 — adapter reached) | (unverified) |
| workday-cxs (ATS direct) | `WorkdayCXSAdapter` | (not active on any operator-managed stack — alice/papa/judy/dave/tango/clean/staging/operator-primary; verify by selecting in a fresh stack's `active_sources.txt`) | (unverified) |
| gmail-linkedin (LinkedIn alerts via IMAP) | `GmailLinkedInAdapter` | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `gmail_messages_found` × 23, `gmail.json` present + `gmail` in active_sources.txt) | (unverified) |

### External integrations

| Integration | Docker | Fly |
|-------------|--------|-----|
| ntfy push (`NTFY_TOPIC` env var) | ✓ 2026-05-20 `6f5e317` (`notifications.ntfy.send()` returned row id 37 with `delivery_status='sent'`, also notify-* cron events visible in db) | (unverified) |
| Gmail IMAP ingestion (`gmail_linkedin` adapter) | ✓ 2026-05-20 `6f5e317` (operator-primary-stack: `gmail_messages_found` × 23) | (unverified) |
| Gmail IMAP rejection detection ([#362](https://github.com/brockamer/findajob/issues/362)) — every 30 min | ✓ 2026-05-20 `6f5e317` (rejection_scan_* × 93; staging skips empty) | (unverified) |
| OpenRouter LLM (`findajob.llm.openrouter.complete()`) | ✓ 2026-05-20 `6f5e317` (scoring_complete + fit_analysis events) | (unverified) |
| `cost_log` writes from OpenRouter `response.usage.cost` | ✓ 2026-05-20 `6f5e317` (prep_cost_projection × 7 implies cost_log writes) | (unverified) |
| Per-call spend-ceiling gate ([#671](https://github.com/brockamer/findajob/issues/671)) | (unverified — needs cap-breach scenario, separate verification) | (unverified) |

### Persistence & operational

| Concern | Docker | Fly |
|---------|--------|-----|
| Schema migrations apply at container start (`apply_pending`) | ✓ 2026-05-20 `6f5e317` (staging recreate clean, no migration errors) | (unverified) |
| SQLite WAL sidecars writable by `lad`/app user | ✓ 2026-05-20 `6f5e317` (in-container writes succeed post-recreate) | (unverified) |
| Companies folder writes (`prep_folder_path`) atomic with DB updates ([#709](https://github.com/brockamer/findajob/issues/709)) | (unverified — needs prep-run inspection) | (unverified) |
| `verify_auth` post-deploy exits 0 | ✓ 2026-05-20 `6f5e317` (exit 0 confirmed after recreate) | (unverified) |
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
  - `POST /feedback/submit` with empty body → pydantic 422; with `text=...` → 503 graceful "not configured" error. Both validation and unconfigured-stack handling verified.

Pass-4 raises Docker-leg ✓ count to ~80. Remaining gaps are the un-*/reverse-flow POSTs that need either DOM exercise, route-fire with careful rollback, or operator-managed exercise (speculative approve/regenerate/trash, all `/onboarding/*` POSTs, `/trigger-triage`).

Remaining gaps on the Docker leg: roughly 25 POST routes the clicker doesn't exercise; subprocess launchers for `interview_prep.py` and `run_speculative_research.py`; per-step ntfy fires during prep; spend-ceiling cap-breach scenario; and per-tester verification (adapters not active on staging). The "un-*" reversibility paths and the rejected-job affordances need either a manual exercise pass, a clicker extension, or a Playwright-driven DOM pass.
