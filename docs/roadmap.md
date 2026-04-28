# Project roadmap

Canonical phase-narrative for findajob. Captures *why* the work is ordered the way it is — the board captures *what* is being worked on right now.

See [`project-board.md`](project-board.md) for the full division of labor between this doc and the board. Short version: phase ordering, cross-issue decisions, and milestone-level acceptance live here. Issue state, per-issue acceptance, and dependencies live on the board.

If a roadmap fact drifts between this doc and an issue, *this doc wins*.

---

## Active milestone: General Availability

[Milestone 4 · due 2026-05-31](https://github.com/brockamer/findajob/milestone/4) — open issues assigned to the milestone are the execution units; find them via the milestone page.

**Goal.** A second person (Alice Doe, the first external tester) runs the pipeline independently in a different field. Config layer fully externalized, user docs written, onboarding flow exists.

**Milestone-level acceptance** (all must hold to close):

1. External tester is deriving daily utility — not merely testing.
2. Her data survives code updates with zero admin action on her part.
3. Web frontend has retired Sheets reads (Dashboard/Applied/Review/Waitlist), writes (STATUS/REJECT_REASON), manual JD ingest, and Google Drive materials viewing.
4. Apply-gate stayed met on average during the arc (daily applies ≥ 1/day averaged across any 7-day window).

### Phase arc

```
Phase 1 (shipped)          Config externalization + frontend evaluation
Phase 2 (shipped)          Docker migration + release management
Phase 2.5                  v0.1.0 tag cut (48h dogfood gate)
Phase 3                    Web materials viewer (retires rclone + Drive)
Phase 4                    First-tester deployment (needs onboarding interview)
Phase 5                    Remaining web-frontend sub-phases:
                             read-only views → STATUS/REJECT workflows →
                             manual JD ingest → stats/trends
Phase 6 (parallel w/ 5)    User-facing documentation (split per area)
```

Phase ordering is deliberate — see Decisions 8 (Phase 3 before Phase 4) and 11 (onboarding interview critical path to Phase 4).

### Decisions

Append-only. Numbers are stable references. Amend an entry in place only for factual corrections (renamed file, etc.); supersede with a new entry otherwise.

1. **rclone replacement — tabled.** Accepted pending the Phase 3 materials viewer, which retires the rclone layer entirely. No tactical hardening pass in the interim.
2. **Beta-tester deployment workflow — image-pull cycle.** Bind-mounted `state/` subtree for data, image pulls for code. Operator's Claude Code session (on his laptop, SSHed into `docker.lan`) runs `docker compose pull && docker compose up -d` for each stack. Tester data never at risk from updates.
3. **Auth — Wireguard perimeter only.** No session auth in the app, no TLS code, no public exposure. See [`deployment-model.md`](deployment-model.md) for topology.
4. **Timeline — 3–5 weeks full arc.** Contingent on: scope holds, apply-gate stays met daily, no surprise complexity in the board-write pivot (14c / #61 — `poll_flags.py` replaced by web handlers + `watchdog.py` 2026-04-22).
5. **Phase 1 parallelizes** via `superpowers:dispatching-parallel-agents` + ralph-loop (mechanical config-externalization work) + `frontend-design` (Phase 5 viewer).
6. **Registry + image-tagging:** GHCR. `:main-<sha>` on merge, `:latest` floating, `:v<x.y.z>` on git tag, plus moving alias `:v0.1` repointed on each patch. Testers pin to the minor alias, auto-accept patches; operator dogfoods `:latest`.
7. **Release ownership:** Claude orchestrates releases (tagging, CHANGELOG, notes, migration markers, dogfood verification); user reviews and approves. See [`release-process.md`](release-process.md).
8. **Phase reorder — Phase 3 before Phase 4.** Shipping a beta tester without materials access was a fake win. The web materials viewer was promoted to Phase 3 so the tester's first pull has working materials access with zero rclone on her side. Operator keeps rclone through the Docker migration (env-var gated) — deleted when Phase 3 lands.
9. **aichat-ng build strategy:** pull prebuilt musl binary from `blob42/aichat-ng` GitHub Releases pinned to a tag; no Rust toolchain in image. Fallback to source build is a one-line change if the fork goes stale.
10. **Scheduler inside container:** `supercronic` running a crontab that mirrors the old systemd timers 1:1. Timezone set per instance via `TZ=America/Los_Angeles`. On-demand scripts via `docker compose exec`.
11. **Onboarding interview — Phase 4 prerequisite.** Hand-curating a second tester's candidate config is untenable; the interview is critical path to Phase 4.
12. **v0.1.0 dogfood gate discipline:** during the 48h observation on `:latest`, any merge to `main` restarts the clock. Phase 3 + Phase 4 work happens on unmerged branches.
13. **User-facing documentation — split planned.** The umbrella scope is too broad for one issue. Plan: three focused issues, each paired with the shipping work that makes the doc concrete — (a) day-1 Dashboard usage guide paired with Phase 4 tester deploy, (b) tuning guide paired with first feedback-calibration cycle, (c) troubleshooting guide paired with first real tester failure.
14. **Post-GA milestone split (2026-04-24).** Post-GA Hardening grab-bag replaced with four dated release milestones (v1.1 / v1.2 / v1.3 / v1.4 — see "Post-GA horizon" below). Driven by a structural review: the single grab-bag milestone couldn't pass the one-sentence-deliverable test, and the Roadmap view showed no visible arc past GA. v1.4 (Funnel + Triage UX) precedes v1.2/v1.3 because active-operator triage friction is the binding constraint on the daily job-search loop.
15. **Umbrella-epic convention (2026-04-24).** GitHub's native sub-issue field is the canonical parent-child relationship for grouping related work (example: #228 tuning loop → #229/#230/#231; #239 credentials hygiene → #67/#225; #240 cost observability → #48/#87). Epics are `enhancement`-labeled, Medium priority, and live on the board like any other issue — not labels, not milestones. Milestones are release boundaries; epics are thematic groupings that may span milestones.
16. **Optional HTTP Basic Auth for internet-exposed instances (2026-04-28, supersedes #3 for the public-exposure case).** A FastAPI middleware (`findajob.web.auth`) gates the entire web UI behind shared-secret HTTP Basic Auth when `FINDAJOB_AUTH_USER` / `FINDAJOB_AUTH_PASS` env vars are both set, with `/healthz`, `/static/*`, and `/favicon.ico` allowlisted. When env vars are unset the middleware is a no-op — Wireguard-only deployments are unchanged. Intended for per-tester instances at `findajob-{tester}.example.com`; defends against drive-by scanning, not against a determined adversary. Real per-user identity / RBAC remains out of scope (still a future change). See `docs/setup/internet-exposure.md` (#327).

### Scope out (explicit)

- Per-user identity / RBAC inside findajob — Decision 16 added shared-secret auth, not identity.
- Separate rclone-replacement project.
- Manual RAG source document editing.
- Alternative LLM provider exploration.

---

## Shipped and retired milestones

- **Reliable Materials** (m1) — shipped; all acceptance criteria closed.
- **Expanded Coverage** (m2) — retired 2026-04-18; scope pivoted into the generalization work (Alice Doe beta) now tracked under General Availability.
- **Feedback Loop v2** (m3) — closed 2026-04-18; initial scope shipped.

## Post-GA horizon

Post-GA Hardening was a single undated grab-bag milestone through 2026-04-24; a structural review that day split it into four dated release milestones so the Roadmap view renders past GA. Deliverable sentences are authoritative — if a proposed issue doesn't fit exactly one, it belongs in NO_MILESTONE (big-idea) or in a new milestone, not wedged into an existing one.

- **[v1.1 — Cost + Credentials Hardening](https://github.com/brockamer/findajob/milestone/6)** (due 2026-07-31) — "The user sees per-job and per-week LLM spend in-app, and no plaintext API key lives on disk." Anchored by umbrella epics #239 (credentials hygiene) and #240 (cost observability).
- **[v1.4 — Funnel + Triage UX](https://github.com/brockamer/findajob/milestone/9)** (due 2026-08-31) — "The operator's daily triage loop (Dashboard, Waitlist, Archive, manual JD ingest) makes every candidate row actionable in one click, with prior-application context inline." Scheduled ahead of v1.2/v1.3 because funnel/UX friction is the active-operator pain.
- **[v1.2 — Tuning Loop + Stats](https://github.com/brockamer/findajob/milestone/7)** (due 2026-09-30) — "The pipeline recommends scorer tunes from user-behavior metrics, and /stats/* dashboards show precision, outcome, recall, and cost trends over time." Anchored by epic #228 (data-driven tuning loop) with C.0/C.1/C.2 children.
- **[v1.3 — Ops Hardening](https://github.com/brockamer/findajob/milestone/8)** (due 2026-10-31) — "Fresh-install smoke is CI-gated, pipeline.jsonl rotates, DB schema migrates cleanly, and folder/DB drift is detectable on demand." Can ship in parallel with v1.2; date is outside-in.

When a new strategic milestone activates (e.g., v1.1 after GA closes), add a `## Active milestone: <name>` section with the same shape (goal, acceptance, phase arc, decisions, scope-out). Move the outgoing milestone's section up to `## Shipped and retired milestones`.

---

## Far-future wishlist

Not on the roadmap. These become worth pursuing only after the operator has job offers in hand and the pipeline has demonstrably gotten someone hired. Captured here so they don't float around as issues.

**Open-source launch**
Public-repo polish: domain-neutral README top-to-bottom, external user install guide requiring no operator intervention, `GENERALIZATION.md` complete, config externalization audit finished, CI green for external contributors, CONTRIBUTING.md, CODE_OF_CONDUCT.md, license review.

**Multi-tester scaling**
Per-user API keys, per-user GCP project isolation, separate admin Claude sessions per instance, operator automation for new-tester provisioning. First concrete item: #71 (multi-tenancy discipline on docker.lan).

**Community feedback loop**
Issue templates for external users, triage discipline when external reports arrive, release cadence that doesn't break downstream users.

**Optional: hosted variant**
Only if demand materializes from the open-source launch. Not in scope until then.

_(Content migrated from issue #88, closed 2026-04-21.)_

---

## Conventions

- **One active milestone at a time** gets a full `## Active milestone` section.
- **Phases are a narrative layer on top of issues.** An issue can belong to exactly one phase; a phase is a group of issues that ship together or in close sequence.
- **Issue numbers are not embedded in the phase arc** — they drift. Look up execution units via the milestone page.
- **Decision log is append-only.** Numbers are stable references.
- **Close the meta-issue** that originally held this narrative with a pointer here. The meta-issue was scaffolding for the doc, not a work item.
