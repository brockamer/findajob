# Plan — Web Frontend 14c: STATUS + REJECT_REASON Write Workflows

**Spec:** `docs/superpowers/specs/2026-04-22-web-frontend-14c-design.md`
**Issue:** [#61](https://github.com/brockamer/findajob/issues/61)
**Plan date:** 2026-04-22

---

## 1. Goal + scope

**In scope.** Turn the read-only board pages from #60 into the operator's primary write surface for every current Sheets-driven transition. Add 11 POST handlers to `src/findajob/web/routes/board_actions.py`; extract the transition logic from `scripts/poll_flags.py` into `src/findajob/actions.py`; shrink `poll_flags.py` to a stale-prep watchdog renamed `scripts/watchdog.py`; stop reading Sheets in `sync_sheet.py`; drop `Ghosted` as a distinct status; add Playwright E2E coverage.

Ship in two PRs under #61:

- **PR-A — Web writes with Sheets safety net.** Every POST endpoint is live; `poll_flags.py` still reads Sheets in parallel so a bug in the web layer does not strand the operator.
- **PR-B — One-way pivot.** Delete `poll_flags.py`; add `watchdog.py`; strip Sheets-read paths from `sync_sheet.py`; drop `Ghosted`; add Playwright. Label `migration-required` (crontab changes per CLAUDE.md release-management criteria).

**Out of scope.** Retiring `sync_sheet.py` entirely (that's 14d / #14). Deleting the Google Sheet workspace (operator-initiated). Adding authentication or CSRF (deferred; see spec §D7). Anything visible only to external testers (generalization work tracked in #20).

---

## 2. Tasks

Tasks 1–10 are PR-A. Tasks 11–19 are PR-B. Execute in order; each task ends with a green commit.

### Task 1 — Extract `findajob/actions.py` from `poll_flags.py`

**Files**
- Create `src/findajob/actions.py`
- Modify `scripts/poll_flags.py` (import from new module)
- Create `tests/test_actions.py`
- Modify `tests/test_poll_flags.py` (delete tests that now live in `test_actions.py`)

**Steps**
- [ ] Move `handle_rejection`, `handle_not_selected`, `handle_waitlist`, `handle_reactivate`, `notify_waitlist_resurface` verbatim from `poll_flags.py` to `src/findajob/actions.py`. No signature changes. No behavior changes.
- [ ] Move `reset_prep_to_scored` from `src/findajob/utils.py` to `src/findajob/actions.py`. Update every import site (`poll_flags.py`, any tests).
- [ ] Add `promote_to_scored(conn, job, reason="Promoted from web UI")` — extract the inline Promote logic from `poll_flags.py` lines 417–431.
- [ ] `poll_flags.py` imports from `findajob.actions` for all five functions. Behavior unchanged.
- [ ] Write unit tests in `tests/test_actions.py` for every function: assert DB state, folder moves, marker files, `feedback_log` writes-or-not, `audit_log` rows. Use `tmp_path` for folders; in-memory SQLite for DB.
- [ ] Move any existing tests covering these functions out of `tests/test_poll_flags.py` into `tests/test_actions.py`.

**Verification**
- `uv run pytest tests/test_actions.py -v` → all new tests pass.
- `uv run pytest tests/test_poll_flags.py tests/test_waitlist.py -v` → still green (same logic, new module path).
- `uv run ruff check src/findajob/actions.py scripts/poll_flags.py` → clean.
- `uv run mypy src/findajob/actions.py` → clean.

**Commit message**
```
refactor(actions): extract transition logic from poll_flags.py

Prep for 14c (#61). poll_flags.py imports from findajob.actions; behavior
unchanged. handle_rejection / handle_not_selected / handle_waitlist /
handle_reactivate / notify_waitlist_resurface / reset_prep_to_scored /
promote_to_scored now live in one module. Web POST handlers in PR-A
will call into this module directly.
```

---

### Task 2 — POST handler scaffolding + `/prep` endpoint

**Files**
- Create `src/findajob/web/routes/board_actions.py`
- Modify `src/findajob/web/routes/__init__.py` (include new router)
- Modify `src/findajob/web/templates/board/dashboard.html` (Flag for Prep button)
- Modify `src/findajob/web/templates/_job_row.html` (button slot for Prep)
- Create `tests/test_board_actions.py`

**Steps**
- [ ] Create `board_actions.py` with:
  ```python
  from fastapi import APIRouter, Depends, HTTPException, Request
  from fastapi.responses import HTMLResponse
  # imports for actions, folder_resolver, db
  router = APIRouter()

  @router.post("/board/jobs/{fingerprint}/prep", response_class=HTMLResponse)
  def prep(fingerprint: str, request: Request, db = Depends(get_db)):
      ...
  ```
- [ ] Handler steps: fp→job lookup (404 if missing); idempotency guard (no-op if `stage in ('prep_in_progress', 'materials_drafted')`); set `stage='prep_in_progress'`; `subprocess.Popen([sys.executable, '.../prep_application.py', ...], start_new_session=True)`; render updated `<tr>` for dashboard tab.
- [ ] Register router in `src/findajob/web/routes/__init__.py`.
- [ ] In `_job_row.html`: render a Flag for Prep `<button>` with `hx-post="/board/jobs/{{ row.fingerprint }}/prep"`, `hx-target="closest tr"`, `hx-swap="outerHTML"` when `tab == 'dashboard'` and `row.stage in ('scored', 'manual_review')`.
- [ ] Test: happy path, 404 on unknown fp, idempotency (double POST no-op), subprocess launch mocked with `monkeypatch.setattr(subprocess, "Popen", ...)`.

**Verification**
- `uv run pytest tests/test_board_actions.py::test_prep -v` → passes.
- `uv run pytest tests/ -v` → full suite green.
- Manual: `uv run uvicorn findajob.web.app:default_app --reload --port 8090` on a scratch DB; click Flag for Prep on a scored row; verify 200 response + `<tr>` swap + stage change in DB.

**Commit message**
```
feat(web): POST /board/jobs/{fp}/prep + Flag for Prep button

First POST handler for #61 PR-A. Web UI can now dispatch prep without
the operator editing the Google Sheet. Calls prep_application.py via
Popen with start_new_session=True; returns immediately and re-renders
the dashboard row. Sheets-based flagging still works (poll_flags.py
unchanged in this commit).
```

---

### Task 3 — Simple stage-transition handlers: apply, interview, offer, withdraw

**Files**
- Modify `src/findajob/web/routes/board_actions.py`
- Modify `src/findajob/web/templates/_job_row.html` (STATUS dropdown for Applied tab)
- Create `src/findajob/web/templates/board/_status_cell.html`
- Modify `tests/test_board_actions.py`

**Steps**
- [ ] Add endpoints:
  - `POST /board/jobs/{fp}/apply` (Dashboard → stage=applied, folder move to `_applied/`, `folders_moved` log, re-render Dashboard row which will be empty next sync — return 200 with empty body since HTMX removes on empty)
  - `POST /board/jobs/{fp}/interview` (Applied → stage=interview)
  - `POST /board/jobs/{fp}/offer` (Applied → stage=offer)
  - `POST /board/jobs/{fp}/withdraw` (Applied → stage=withdrawn + `notify_waitlist_resurface`)
- [ ] `_status_cell.html` macro — takes `row` and `tab`, renders the correct dropdown (Dashboard: `Flag for Prep | Regenerate | Waitlist | Applied`; Applied: `Interviewing | Offer | Withdrew | Not Selected`). Dropdown change = HTMX POST with `hx-post`, `hx-target="closest tr"`, `hx-swap="outerHTML"`.
- [ ] `_job_row.html` calls `_status_cell.html` for the STATUS column when `tab` is dashboard or applied.
- [ ] Tests per endpoint: happy path + idempotency + fingerprint-not-found.

**Verification**
- `uv run pytest tests/test_board_actions.py -v -k "apply or interview or offer or withdraw"` → passes.
- Manual: on scratch DB, use UI to mark an applied-stage job Interviewing; confirm DB stage change.

**Commit message**
```
feat(web): stage-transition POST handlers (apply/interview/offer/withdraw)

Four endpoints for #61 PR-A. Each re-reads stage before writing to stay
idempotent. withdraw fires notify_waitlist_resurface like poll_flags.py
does today. Applied dropdown is now interactive; Dashboard's Applied
option moves folder to companies/_applied/.
```

---

### Task 4 — Waitlist, Reactivate, Promote handlers

**Files**
- Modify `src/findajob/web/routes/board_actions.py`
- Modify `src/findajob/web/templates/_job_row.html`
- Modify `tests/test_board_actions.py`

**Steps**
- [ ] `POST /board/jobs/{fp}/waitlist` (Dashboard → calls `actions.handle_waitlist`).
- [ ] `POST /board/jobs/{fp}/reactivate` (Waitlist → calls `actions.handle_reactivate`).
- [ ] `POST /board/jobs/{fp}/promote` (Review → calls `actions.promote_to_scored`).
- [ ] `_status_cell.html` renders Reactivate button on Waitlist tab; Promote button on Review tab.
- [ ] Tests: happy path + wrong-stage 409 (promote on non-manual_review job; reactivate on non-waitlisted).

**Verification**
- `uv run pytest tests/test_board_actions.py -v -k "waitlist or reactivate or promote"` → passes.

**Commit message**
```
feat(web): waitlist/reactivate/promote POST handlers

Covers the remaining non-REJECT_REASON actions from #61 PR-A matrix.
Waitlist + Reactivate reuse actions.handle_waitlist / handle_reactivate
for folder moves. Promote sets score=7 and stage=scored.
```

---

### Task 5 — Reject + Not Selected handlers (with `reason` form field)

**Files**
- Modify `src/findajob/web/routes/board_actions.py`
- Create `src/findajob/web/templates/board/_reject_cell.html`
- Modify `src/findajob/web/templates/_job_row.html`
- Modify `tests/test_board_actions.py`

**Steps**
- [ ] `POST /board/jobs/{fp}/reject` accepts `reason` form field. Calls `actions.handle_rejection`. Valid from Dashboard, Applied, Review, Waitlist tabs. Fires `notify_waitlist_resurface`.
- [ ] `POST /board/jobs/{fp}/not-selected` accepts `reason` form field. Valid only when `stage in ('applied', 'interview', 'offer')`; 409 otherwise. Calls `actions.handle_not_selected`. Fires `notify_waitlist_resurface`.
- [ ] `_reject_cell.html` — `<select>` with the same 11 reject-reason options as Sheet, plus a "— no reason —" placeholder. `hx-post` to `/reject` on the Dashboard/Review/Waitlist tabs; `hx-post` to `/not-selected` when `tab == 'applied'` AND the STATUS cell shows `Not Selected` (handled by an Alpine.js directive in the template, since this is cross-cell logic).
- [ ] Tests: happy path for both endpoints; verify `feedback_log` is written on reject but NOT on not-selected; verify marker files created.

**Verification**
- `uv run pytest tests/test_board_actions.py -v -k "reject or not_selected"` → passes.
- Manual: reject an applied-stage job; verify folder in `_rejected/` and `feedback_log` row exists.

**Commit message**
```
feat(web): reject + not-selected POST handlers

Completes the 11-action matrix for #61 PR-A. Reject writes feedback_log
and moves folder to _rejected/; Not Selected drops a marker in _applied/
without writing feedback_log. notify_waitlist_resurface fires on both.
```

---

### Task 6 — Regenerate handler + concurrency cap for Prep

**Files**
- Modify `src/findajob/web/routes/board_actions.py`
- Modify `tests/test_board_actions.py`

**Steps**
- [ ] `POST /board/jobs/{fp}/regenerate`: guard (no-op if `stage='prep_in_progress'`); `shutil.rmtree` the prep folder if it exists; set `stage='prep_in_progress'`, clear `prep_folder_path` and `gdrive_folder_url`; Popen prep subprocess. Same Popen pattern as Task 2.
- [ ] Add to the `/prep` and `/regenerate` handlers:
  ```python
  in_flight = db.execute(
      "SELECT COUNT(*) FROM jobs WHERE stage='prep_in_progress'"
  ).fetchone()[0]
  if in_flight >= 3:
      return HTMLResponse("Prep queue full (3 in flight). Try again in a few minutes.", status_code=429)
  ```
- [ ] Test: 4th prep returns 429 with DB unchanged; regenerate on `stage='prep_in_progress'` returns 200 with no-op.

**Verification**
- `uv run pytest tests/test_board_actions.py -v -k "regenerate or cap or in_flight"` → passes.

**Commit message**
```
feat(web): regenerate handler + 3-job prep concurrency cap

Matches poll_flags.py's MAX_CONCURRENT_PREPS=3 in the web layer. 4th
POST to /prep or /regenerate returns 429 without touching the DB.
Regenerate deletes the prep folder and re-dispatches prep, matching
the existing poll_flags flow.
```

---

### Task 7 — `user_notes` POST handler (Applied tab)

**Files**
- Modify `src/findajob/web/routes/board_actions.py`
- Create `src/findajob/web/templates/board/_notes_cell.html`
- Modify `src/findajob/web/templates/_job_row.html` (use macro for Applied notes cell)
- Modify `tests/test_board_actions.py`

**Steps**
- [ ] `POST /board/jobs/{fp}/notes` accepts `notes` form field. `UPDATE jobs SET user_notes=?, updated_at=datetime('now') WHERE fingerprint=?`. No audit log entry (notes are free-text, already versioned elsewhere). Returns the re-rendered `<td>`.
- [ ] `_notes_cell.html` — `<input type="text">` bound with `hx-post="/board/jobs/{fp}/notes"`, `hx-trigger="blur, keyup changed delay:800ms"`, `hx-swap="outerHTML"`.
- [ ] `_job_row.html` uses `_notes_cell.html` on the Applied tab.
- [ ] Test: happy path (POST sets DB); empty note clears DB column.

**Verification**
- `uv run pytest tests/test_board_actions.py -v -k "notes" ` → passes.

**Commit message**
```
feat(web): POST /board/jobs/{fp}/notes for Applied user_notes

Replaces the Sheet column-I write-back that sync_sheet.py does today.
HTMX debounces at 800ms so every keystroke doesn't hit the DB. The
Sheet still shows notes (sync_sheet.py continues writing them) but
the web UI is now where they're edited.
```

---

### Task 8 — PR-A verification gate

**Files** — none (verification only).

**Steps**
- [ ] Full test suite: `uv run pytest tests/ -v` → all green.
- [ ] `uv run ruff check .` → clean.
- [ ] `uv run mypy src/findajob/` → clean.
- [ ] Local smoke test: `uv run uvicorn findajob.web.app:default_app --reload --port 8090` on a scratch DB copy. Walk every endpoint in the D4 matrix from the spec, one click per endpoint. Verify DB state after each.
- [ ] Confirm `poll_flags.py` still runs without error (crontab still dispatches it). Edit the Sheet for a test row, wait 10 min, confirm DB reflects the Sheet edit (safety net verified).
- [ ] Take screenshots of Dashboard + Applied + Review + Waitlist tabs with the new controls. Attach to PR description.

**Commit message** — none; gate task.

---

### Task 9 — Open PR-A

**Files** — none (git only).

**Steps**
- [ ] `git fetch && git checkout -b feat/61-14c-pra origin/main`
- [ ] Cherry-pick / move commits from the working branch to `feat/61-14c-pra`.
- [ ] `git push -u origin feat/61-14c-pra`
- [ ] `gh pr create --title "feat(web): 14c PR-A — STATUS + REJECT_REASON web write handlers (#61)" --body "..."`
- [ ] PR body must include: summary of new endpoints, screenshots, confirmation that `poll_flags.py` keeps reading Sheets.
- [ ] Add `enhancement` label. Do NOT add `migration-required` (PR-A is compatible — both write surfaces work).
- [ ] Add a Session note comment on #61 summarizing PR-A ship and what's left for PR-B.

**Verification**
- `gh pr view` shows checks passing.
- Board: #61 remains `In Progress`.

**Commit message** — none; PR task.

---

### Task 10 — Merge PR-A + in-production smoke check

**Files** — none.

**Steps**
- [ ] Wait for CI green + review (solo repo: self-review against spec).
- [ ] Merge with squash.
- [ ] Pull on `docker.lan`: in the operator's stack dir under `/opt/stacks/`, `docker compose pull && docker compose up -d`.
- [ ] Hit `http://docker.lan:<FINDAJOB_MATERIALS_PORT>/board/dashboard` in a real browser.
- [ ] Perform one real action on a real job (Flag for Prep on a live scored row). Confirm prep completes end-to-end.
- [ ] Leave the Sheet workspace untouched for 24h — verify operator can drive the full workflow from the web UI without touching the Sheet.
- [ ] If no regressions, proceed to Task 11 (PR-B).

**Verification** — observational.

**Commit message** — none.

---

### Task 11 — Create `scripts/watchdog.py`

**Files**
- Create `scripts/watchdog.py`
- Create `tests/test_watchdog.py`

**Steps**
- [ ] `scripts/watchdog.py`: single `main()` that resets `stage='prep_in_progress'` rows older than 60 min to `scored` via `actions.reset_prep_to_scored(conn, job_id, reason="watchdog_stale_reset")`. See spec §D2 for the skeleton.
- [ ] Add `log_event("watchdog_run", stale_reset=count)` at end of run.
- [ ] `tests/test_watchdog.py`: fixture with one stale row (`stage_updated` 2h ago) and one fresh row (5 min ago). Run `main()`. Assert only the stale row transitions to `scored`.

**Verification**
- `uv run pytest tests/test_watchdog.py -v` → passes.

**Commit message**
```
feat(watchdog): scripts/watchdog.py — stale-prep cleanup only

Skeleton for poll_flags.py's replacement. Single responsibility: reset
jobs stuck in prep_in_progress > 60 min. No Sheets reads, no
subprocess dispatch. Next commits swap the cron entry and delete
poll_flags.py.
```

---

### Task 12 — Swap `ops/crontab` + flip `prep_application.py` defaults

**Files**
- Modify `ops/crontab`
- Modify `scripts/prep_application.py`

**Steps**
- [ ] Replace the `*/10 * * * * python poll_flags.py` line with `*/10 * * * * python watchdog.py`.
- [ ] In `prep_application.py`: remove the `--no-sync` handling (`if "--no-sync" not in sys.argv`). Prep always runs `sync_sheet.py` at the end. Keep the subprocess launch as-is.
- [ ] Grep the repo for any caller passing `--no-sync` — should be poll_flags.py only, which is deleted next task. Delete the flag reference in the docstring too.

**Verification**
- `uv run pytest tests/test_prep_pipeline.py -v` → green.
- `grep -rn "no-sync\|no_sync" scripts/ src/` → empty.

**Commit message**
```
chore(ops): crontab poll_flags -> watchdog; drop --no-sync from prep

Part of #61 PR-B. The container now runs watchdog.py every 10 min
instead of poll_flags.py. prep_application.py always invokes
sync_sheet.py at the end — no callers pass --no-sync anymore.

migration-required: crontab entry changed. Operators pulling :latest
get the swap automatically at container restart.
```

---

### Task 13 — Strip Sheets-read paths from `sync_sheet.py`

**Files**
- Modify `scripts/sync_sheet.py`
- Modify `tests/test_sync_sheet.py`

**Steps**
- [ ] Delete the `values().get()` + `pending_statuses` / `pending_rejects` / `pending_notes` block in `sync_dashboard()` (~lines 278–296).
- [ ] Delete the equivalent block in `sync_applied()` (~lines 593–624), including the notes-writeback loop.
- [ ] Delete the legacy Sheet1 preservation block (~line 432–459 and ~line 488–525 — verify by `grep -n "pending_statuses" scripts/sync_sheet.py`).
- [ ] Delete constants `VALID_STATUSES` (dashboard) and `APPLIED_VALID_STATUSES`.
- [ ] STATUS column derivation in each `sync_*` becomes purely DB-driven: derive from `stage` with the existing `Ready to Apply` / `Prep in Progress` / `Interviewing` / `Offer` mappings. Delete the `Ghosted` branch (see Task 15).
- [ ] Update `tests/test_sync_sheet.py`: remove fixtures for pending statuses; add a test that asserts `sync_sheet.py` does NOT call `values().get()` on the four tabs.

**Verification**
- `uv run pytest tests/test_sync_sheet.py -v` → green.
- `grep -c "values().get" scripts/sync_sheet.py` → only the ones that belong (if any remain — the Rejected Applications tab may still read to preserve — verify against the spec's "four read paths" table).

**Commit message**
```
fix(sync): stop reading user edits back from Sheets

Part of #61 PR-B. Acceptance criterion 3: "Sheet edits by the user are
ignored by the pipeline." sync_sheet.py now reads nothing from Sheets;
it only writes DB state. Deleted pending_statuses / pending_rejects /
pending_notes preservation logic — obsolete now that the web UI is
the write surface.
```

---

### Task 14 — Drop `Ghosted` from status options

**Files**
- Modify `src/findajob/web/templates/board/_status_cell.html`
- Modify `scripts/sync_sheet.py`
- Modify `docs/architecture.md`
- Modify `CLAUDE.md`

**Steps**
- [ ] Remove `Ghosted` from the Applied-tab STATUS dropdown options in `_status_cell.html`.
- [ ] Remove the `Ghosted` derivation branch from `sync_sheet.py::sync_applied` (so the Sheet's Applied tab STATUS column no longer emits `Ghosted`; rows >=21d still go gray via the existing row-age rule).
- [ ] Update `docs/architecture.md` Applied-tab column-description: drop `Ghosted` from STATUS options. Note row-age gray coloring replaces it.
- [ ] Update `CLAUDE.md` STATUS-options table similarly.

**Verification**
- `grep -c Ghosted scripts/sync_sheet.py src/findajob/web/templates/` → 0 in both.

**Commit message**
```
refactor: drop Ghosted status (row-age gray coloring subsumes it)

Ghosted was a Sheet-only visual flag preserved across syncs via
pending_statuses. With the web UI as the write surface there's no
carrier for the flag. The existing 21-day gray-row rule already
communicates "this one's quiet"; operators who want to act flip to
Not Selected.
```

---

### Task 15 — Delete `poll_flags.py`

**Files**
- Delete `scripts/poll_flags.py`
- Delete `tests/test_poll_flags.py`

**Steps**
- [ ] `git rm scripts/poll_flags.py tests/test_poll_flags.py`.
- [ ] Grep for any remaining references: `grep -rn "poll_flags" docs/ scripts/ src/ tests/ ops/`. Docs references get updated in Task 18; any code reference is a bug — fix before committing.

**Verification**
- `uv run pytest tests/ -v` → green (no missing-module errors).

**Commit message**
```
chore: delete poll_flags.py — superseded by web handlers + watchdog

Final step of the #61 pivot. All transition logic lives in
findajob.actions (called from web POST handlers); stale-prep cleanup
lives in scripts/watchdog.py. poll_flags.py's Sheets-read paths have
no purpose now. test_poll_flags.py moves to test_actions.py +
test_watchdog.py.
```

---

## PR-D — Playwright E2E coverage (Task 16)

Deferred from PR-B 2026-04-22 — scope-discipline call. PR-B ships without
browser-level tests; `test_board_actions.py` covers every handler at the
HTTP layer and the plan's CI job was already marked non-blocking, so
landing E2E later loses nothing. Label: `enhancement`.

### Task 16 — Playwright E2E suite

**Files**
- Modify `pyproject.toml` (optional-dependencies `dev` gains `playwright`, `pytest-playwright`)
- Create `tests/e2e/__init__.py`
- Create `tests/e2e/conftest.py`
- Create `tests/e2e/test_dashboard_write.py`
- Create `tests/e2e/test_applied_write.py`
- Create `tests/e2e/test_review_write.py`
- Create `tests/e2e/test_waitlist_write.py`
- Modify `.github/workflows/ci.yml` (new `pytest-e2e` job)

**Steps**
- [ ] `uv add --dev playwright pytest-playwright`.
- [ ] `uv run playwright install chromium` (locally and in CI step).
- [ ] `tests/e2e/conftest.py`: fixture spinning up FastAPI app on random port with a temp DB seeded from `tests/fixtures/pipeline.db` (or a built-from-schema fresh DB).
- [ ] One test per tab, walking the canonical happy-path workflow (see spec §D9 for the exact scenarios).
- [ ] CI job: separate from the main `pytest` job. `pip install -e .[dev]`, `playwright install chromium --with-deps`, `pytest tests/e2e/ -v`. Allow failure (mark as non-blocking for 14c) — E2E flakiness shouldn't block ship.

**Verification**
- `uv run pytest tests/e2e/ -v` → all tests pass locally.
- CI run on PR shows the e2e job executing and (ideally) green.

**Commit message**
```
test(e2e): Playwright coverage for #61 write workflows

14b deferred browser E2E to 14c explicitly. One test per board tab
walks the canonical happy-path workflow. CI runs the suite as a
separate non-blocking job so developer feedback stays fast on
ordinary pytest runs.
```

---

### Task 17 — Tidy `utils.py` after `reset_prep_to_scored` move

**Files**
- Modify `src/findajob/utils.py`

**Steps**
- [ ] Verify `reset_prep_to_scored` is no longer in `utils.py` (moved in Task 1). Delete any orphan imports or test references.
- [ ] Audit `utils.py` for anything else that only `poll_flags.py` used; leave if generic, delete if dead code.

**Verification**
- `grep -rn "from findajob.utils import reset_prep_to_scored" .` → empty.
- `uv run pytest tests/test_utils.py -v` → green.

**Commit message**
```
refactor(utils): drop reset_prep_to_scored import (moved to actions.py)

No behavior change. Cleanup after the Task 1 extraction.
```

---

### Task 18 — Documentation sweep

**Files**
- Modify `CHANGELOG.md`
- Modify `docs/architecture.md`
- Modify `CLAUDE.md`
- Modify `docs/setup/install-docker.md`
- Modify `docs/roadmap.md` (if 14 arc is tracked there — verify first)
- Modify `scripts/sync_sheet.py` (module docstring)
- Modify `scripts/prep_application.py` (module docstring)
- Modify `scripts/watchdog.py` (module docstring)

**Steps**
- [ ] `CHANGELOG.md` [Unreleased]:
  - PR-A line: "Web UI adds interactive STATUS and REJECT_REASON controls on every board tab. Google Sheet edits still work in parallel during PR-A."
  - PR-B line (flag `migration-required`): "poll_flags.py is removed; the web UI is the sole write surface. sync_sheet.py no longer reads user edits back from Sheets (one-way DB → Sheet). Crontab entry moves from poll_flags.py to watchdog.py — container pull + restart picks this up automatically."
- [ ] `docs/architecture.md`:
  - Prep Workflow diagram: replace `poll_flags.py (runs every 10 min) reads Dashboard!A2:C10000 ...` with `POST /board/jobs/{fp}/prep → prep_application.py launched via Popen`.
  - Google Sheet Layout section: add banner at top noting Sheets are now a one-way synced view, not a write surface.
  - STATUS dropdown tables: drop `Ghosted` row; update REJECT_REASON description (web UI drives it now).
- [ ] `CLAUDE.md`:
  - Pipeline Context Table: add `Scheduler` row showing watchdog.py every 10 min; remove `poll_flags.py` references.
  - "Google Sheet Architecture" subsection: rewrite to describe Sheets as a read-only synced view; add a pointer to `/board/*` as the primary UI.
  - "Critical Architecture Rules": add "Web handlers are the write surface" rule.
- [ ] `docs/setup/install-docker.md`: add a paragraph describing the board-page write controls in the "Using the pipeline" section.
- [ ] `docs/roadmap.md`: check off 14c (if tracked there).
- [ ] Module docstrings: `sync_sheet.py` drop "reads user edits back"; `prep_application.py` drop `--no-sync` reference; `watchdog.py` describe single responsibility.

**Verification**
- `grep -rn "poll_flags" docs/` → empty except in archived specs / plans / CHANGELOG historical entries.
- `grep -c Ghosted docs/` → only in archived specs.

**Commit message**
```
docs(14c): sweep architecture, CLAUDE, install-docker, CHANGELOG

Matches the doc surfaces enumerated in the 14c spec's Documentation
Impact section. Sheets are now described as a one-way synced view.
Web UI is the primary write surface. Ghosted is gone from all live
docs (remains in archived 14b spec, as expected).
```

---

### Task 19 — PR-B verification gate + open PR

**Files** — none (verification + PR).

**Steps**
- [ ] `uv run pytest tests/ -v` → green (incl. `test_actions.py`, `test_watchdog.py`, `test_board_actions.py`, `test_sync_sheet.py`).
- [ ] `uv run pytest tests/e2e/ -v` → green (or documented flakes).
- [ ] `uv run ruff check .` → clean.
- [ ] `uv run mypy src/findajob/` → clean.
- [ ] Manual E2E (from the spec's §End-to-end verification before PR-B merges):
  1. Pull on `docker.lan`, restart container.
  2. Walk every action in the D4 matrix against at least one job.
  3. Confirm `sync_sheet.py` still writes Sheets.
  4. Make a Sheet edit, wait one watchdog cycle, confirm DB unchanged (acceptance criterion 3).
- [ ] Branch from `origin/main`: `git fetch && git checkout -b feat/61-14c-prb origin/main`.
- [ ] `gh pr create --label "enhancement,migration-required" --title "feat(14c): pivot to web writes + drop poll_flags.py (#61 PR-B)" --body "..."`.
- [ ] PR body: summary, deleted-files list, crontab-change note, Sheet-edits-now-ignored note, screenshots.
- [ ] On merge: monitor the next watchdog run; confirm `watchdog_run` event in `logs/pipeline.jsonl`.
- [ ] On #61: Session note summarizing both PRs shipped. Close #61; verify the board auto-moves it to Done.
- [ ] Add a task note or follow-up issue to spec's "Open questions / risks" items that remain (Sheet drift during PR-A is resolved once PR-B ships; CSRF stays deferred).
- [ ] Banner the spec with `> **ARCHIVED** — Issue #61 shipped YYYY-MM-DD. See the closed issue and merged PRs for the canonical record.` (matches 14b pattern).

**Commit message** — none; gate task.

---

## PR-C — UX polish (Tasks 20–22)

Ships as a separate follow-on PR after PR-B merges — keeps PR-B focused on the
Sheets-retirement pivot. Label: `enhancement`, no `migration-required`.

### Task 20 — Company cell links to materials folder when it exists

**Context.** Added 2026-04-22 after operator feedback during PR-B execution: the Sheet's `materials_company_cell` wraps the company name in a hyperlink to the materials viewer when `prep_folder_path` exists and is on disk. The web UI currently renders company as plain text.

**Files**
- Modify `src/findajob/web/templates/_job_row.html` (or the per-tab templates if cells are specialized)
- Possibly modify `src/findajob/web/routes/board.py` to pass `MATERIALS_BASE_URL` / folder existence into the row context

**Steps**
- [ ] Identify the materials-viewer URL pattern the web UI already serves (`/materials/...`) and decide whether to link there or to the existing `MATERIALS_BASE_URL`-based external URL.
- [ ] In `_job_row.html`, render company as `<a href="...">{{ row.company }}</a>` when the row has a live prep folder; plain text otherwise.
- [ ] Apply to Dashboard, Applied, Waitlist (all tabs that currently show company for jobs that may have folders).
- [ ] Manual check in browser on dev server.

**Verification** — visual; no unit test.

**Commit message**
```
feat(web): company cell links to materials folder when present

Mirrors the Sheet's materials_company_cell behavior. Operator feedback
during 14c PR-B — the web UI was plain text where the Sheet offered a
one-click drill-in to resume/cover drafts.
```

---

### Task 21 — STATUS + REJECT_REASON as the first two columns

**Context.** Added 2026-04-22 after operator feedback. In the Sheet, STATUS is col A and REJECT_REASON is col B. The web UI currently puts these later in the row, which breaks muscle memory. Move them to cols 1–2 on Dashboard, Applied, Review, Waitlist.

**Files**
- Modify `src/findajob/web/routes/board.py` (the `_COLS` lists for each tab)
- Modify `src/findajob/web/templates/_job_row.html` (column ordering)
- Modify `src/findajob/web/templates/board/_status_cell.html`, `_reject_cell.html` (if they hardcode column position)

**Steps**
- [ ] Pull STATUS and REJECT_REASON to the front of each tab's `_COLS` list.
- [ ] Update `_job_row.html` rendering to match the new order.
- [ ] Update any per-tab header templates.
- [ ] Verify existing HTMX targets (`closest tr`) still work (they should — row-level targets aren't column-position-sensitive).

**Verification**
- `uv run pytest tests/test_board_actions.py -v` → green (row re-render assertions match).
- Manual: open each tab in browser; confirm col 1 = STATUS, col 2 = REJECT_REASON.

**Commit message**
```
feat(web): STATUS + REJECT_REASON as cols 1–2 on every board tab

Matches the Sheet's column layout; preserves operator muscle memory
after the Sheets-to-web pivot. Dashboard, Applied, Review, Waitlist
all now lead with the two action controls.
```

---

### Task 22 — Board table fits standard browser width; no left-margin gutter

**Context.** Added 2026-04-22 after operator feedback. Board rows overflow a standard browser window (~1440px), forcing horizontal scroll. Content below the nav bar starts ~2 inches inset from the left edge — wasted real estate.

**Files**
- Modify `src/findajob/web/templates/_nav.html` or the layout template (container/padding classes)
- Modify `src/findajob/web/static/app.css` (design tokens, column-width rules)
- Possibly modify per-tab templates if columns need truncation/hiding at narrow widths

**Steps**
- [ ] Remove or reduce the left gutter below the nav bar. Likely a Tailwind container class (`max-w-*`, `mx-auto`, `px-*`) — either drop to full-bleed (`w-full`) or keep container but remove `mx-auto` inset.
- [ ] Audit column widths: give STATUS + REJECT_REASON enough room for the dropdowns; compress ancillary columns (location, remote, source) with truncation + tooltip if needed.
- [ ] Consider hiding low-priority columns below ~1280px (responsive `hidden lg:table-cell` pattern).
- [ ] Verify on Chromebook-typical 1366×768 and a standard 1440×900.

**Verification** — visual on dev server. Take before/after screenshots.

**Commit message**
```
fix(web): board rows fit standard browser width; drop left gutter

Operator feedback — rows overflowed viewport and content started ~2in
right of the left edge. Full-bleed layout below the nav, column-width
audit, responsive hiding for ancillary columns.
```

---

## 3. Documentation Impact

Every doc surface the plan touches. (Enumerated in §2 per task — consolidated here for review.)

- **`CHANGELOG.md`** — two [Unreleased] entries (PR-A + PR-B). PR-B flagged `migration-required`. Covered in Task 18.
- **`docs/architecture.md`** — Prep Workflow diagram rewritten; Google Sheet Layout section rebannered; STATUS dropdown tables updated; Ghosted row removed. Task 18.
- **`CLAUDE.md`** — Pipeline Context Table updated; "Google Sheet Architecture" subsection rewritten; "Critical Architecture Rules" gains a write-surface rule; Ghosted references removed. Task 18.
- **`docs/setup/install-docker.md`** — new paragraph in the "Using the pipeline" section describing the board-page write controls. Task 18.
- **`docs/roadmap.md`** — 14c checkbox ticked (if that's where the arc is tracked). Task 18.
- **`docs/superpowers/specs/2026-04-22-web-frontend-14c-design.md`** — post-ship banner as ARCHIVED. Task 19.
- **Module docstrings** — `scripts/sync_sheet.py`, `scripts/prep_application.py`, `scripts/watchdog.py`. Task 18.
- **`docs/project-board.md`** — no change expected (plan follows existing conventions); if plan execution reveals a new board pattern, update it in the same PR per CLAUDE.md.

No README updates expected — the README describes the operator-facing pipeline at a level that doesn't mention the Sheet-vs-web write surface. Verify during Task 18 and update if needed.

---

## 4. Verification gate (whole-feature)

Distinct from per-task verification — this is the gate for "14c is done."

- [ ] All four issue acceptance criteria verified:
  1. Every STATUS + REJECT_REASON action in the D4 matrix works end-to-end in the web UI.
  2. `grep -c "values().get" scripts/poll_flags.py` → file doesn't exist (deleted).
  3. Manual: edit a cell in the Google Sheet, wait one watchdog cycle, confirm DB unchanged.
  4. Walk the prep → applied → not_selected → rejected transition on a real job; verify folder moves and `feedback_log` behavior.
- [ ] `uv run pytest tests/ -v` — all green. (E2E suite deferred to PR-D.)
- [ ] Container restart on `docker.lan` without manual migration steps (operator just pulls + restarts).
- [ ] Operator's next-day driven workflow: open `/board/dashboard`, complete one full apply cycle (Flag for Prep → prep completes → Applied → row moves to `/board/applied`).
- [ ] No `poll_flags_*` or `pending_statuses` mentions in the live codebase (`grep -rn "poll_flags\|pending_statuses" src/ scripts/ tests/ ops/` → empty).
- [ ] `apply_gate` DB queries (audit_log-based) still work — the operator's daily apply-gate check unaffected.

---

## 5. Self-review checklist

### Spec coverage map

Every section of the spec maps to at least one task:

| Spec section | Task(s) |
|---|---|
| D1 — Prep dispatch model (subprocess from web) | 2, 6, 12 |
| D2 — watchdog.py replaces poll_flags.py | 11, 12, 15 |
| D3 — Ghosted dropped | 14 |
| D4 — 11-action endpoint matrix | 2, 3, 4, 5, 6, 7 |
| D5 — Concurrency cap | 6 |
| D6 — Idempotency via DB-read-before-write | 2, 3, 4, 5, 6 (guarded in every handler) |
| D7 — Auth deferred, single-operator assumption | (no task; documented in spec and Task 18 docs sweep) |
| D8 — `user_notes` POST endpoint | 7 |
| D9 — Playwright E2E | 16 |
| Four Sheets-read paths disappear | 13, 15 |
| Handler matrix invariants | 1 (extraction) + 2–7 (reuse) |
| PR boundary | 9 (PR-A), 19 (PR-B) |
| Testing strategy | per-task unit tests; 16 for E2E |
| Documentation Impact | 18 |
| Error handling | 2–7 (each handler implements the patterns) |

### Placeholder scan

- [ ] No `TBD`, `TODO`, `FIXME` left in the plan.
- [ ] All `...` in code snippets are intentional (illustrative, not unfilled).
- [ ] No "see spec for details" that isn't grounded in a specific section reference.

### Type/contract consistency

- [ ] Every handler's signature matches the FastAPI dependency pattern in `routes/materials.py::file_serve` (db via `Depends(get_db)`, request via `Request`).
- [ ] Every call into `findajob.actions` uses the `(conn, job, ...)` signature Task 1 defines.
- [ ] `reset_prep_to_scored` — current `utils.py` signature is `(conn, job_id, reason)`. Verify this matches the post-move `actions.py` signature before Task 11 (which calls into it).
- [ ] STATUS-column derivation in `sync_sheet.py` (Task 13) does NOT emit `Ghosted` — confirmed in Task 14.
- [ ] PR-B's `migration-required` label is added in Task 19 — confirmed against CLAUDE.md's release-management criteria (crontab change triggers the label).
