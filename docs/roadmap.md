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
4. **Timeline — 3–5 weeks full arc.** Contingent on: scope holds, apply-gate stays met daily, no surprise complexity in the `poll_flags.py` pivot (the cross-cutting sub-phase of Phase 5).
5. **Phase 1 parallelizes** via `superpowers:dispatching-parallel-agents` + ralph-loop (mechanical config-externalization work) + `frontend-design` (Phase 5 viewer).
6. **Registry + image-tagging:** GHCR. `:main-<sha>` on merge, `:latest` floating, `:v<x.y.z>` on git tag, plus moving alias `:v0.1` repointed on each patch. Testers pin to the minor alias, auto-accept patches; operator dogfoods `:latest`.
7. **Release ownership:** Claude orchestrates releases (tagging, CHANGELOG, notes, migration markers, dogfood verification); user reviews and approves. See [`release-process.md`](release-process.md).
8. **Phase reorder — Phase 3 before Phase 4.** Shipping a beta tester without materials access was a fake win. The web materials viewer was promoted to Phase 3 so the tester's first pull has working materials access with zero rclone on her side. Operator keeps rclone through the Docker migration (env-var gated) — deleted when Phase 3 lands.
9. **aichat-ng build strategy:** pull prebuilt musl binary from `blob42/aichat-ng` GitHub Releases pinned to a tag; no Rust toolchain in image. Fallback to source build is a one-line change if the fork goes stale.
10. **Scheduler inside container:** `supercronic` running a crontab that mirrors the old systemd timers 1:1. Timezone set per instance via `TZ=America/Los_Angeles`. On-demand scripts via `docker compose exec`.
11. **Onboarding interview — Phase 4 prerequisite.** Hand-curating a second tester's candidate config is untenable; the interview is critical path to Phase 4.
12. **v0.1.0 dogfood gate discipline:** during the 48h observation on `:latest`, any merge to `main` restarts the clock. Phase 3 + Phase 4 work happens on unmerged branches.
13. **User-facing documentation — split planned.** The umbrella scope is too broad for one issue. Plan: three focused issues, each paired with the shipping work that makes the doc concrete — (a) day-1 Dashboard usage guide paired with Phase 4 tester deploy, (b) tuning guide paired with first feedback-calibration cycle, (c) troubleshooting guide paired with first real tester failure.

### Scope out (explicit)

- Public web access / app-level auth (follow-up if demand materializes).
- Separate rclone-replacement project.
- Manual RAG source document editing.
- Alternative LLM provider exploration.

---

## Future milestones

- [Reliable Materials](https://github.com/brockamer/findajob/milestone/1) — no due date set; narrative stays on the milestone page until it becomes active.
- [Expanded Coverage](https://github.com/brockamer/findajob/milestone/2) — no due date set.

When one of these activates, add a `## Active milestone: <name>` section with the same shape (goal, acceptance, phase arc, decisions, scope-out). Move the outgoing milestone's section to `## Shipped milestones` below — or archive to a dated file if this page grows long.

---

## Conventions

- **One active milestone at a time** gets a full `## Active milestone` section.
- **Phases are a narrative layer on top of issues.** An issue can belong to exactly one phase; a phase is a group of issues that ship together or in close sequence.
- **Issue numbers are not embedded in the phase arc** — they drift. Look up execution units via the milestone page.
- **Decision log is append-only.** Numbers are stable references.
- **Close the meta-issue** that originally held this narrative with a pointer here. The meta-issue was scaffolding for the doc, not a work item.
